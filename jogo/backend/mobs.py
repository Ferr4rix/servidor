"""Servidor dos mobs: a ÚNICA autoridade sobre onde estão e o que fazem.

O cliente nunca decide a posição, a vida ou o comportamento de um mob. Ele só:

* conta ao servidor onde o jogador está (``sync``) e recebe de volta o estado
  dos mobs (posição, rumo, vida, animação) e os eventos (ex.: "a espada do mob
  acertou-te, 3 de dano");
* pede para golpear um mob (``attack``); o servidor confere a distância, vê se
  o escudo bloqueou, tira a vida e responde com o resultado.

O servidor tem o seu próprio relógio (``time.monotonic``) e avança a simulação
em passos fixos (``TICK``). Hoje corre dentro do processo do jogo
(``LocalBackend``); num servidor de verdade, esta mesma classe correria lá e o
cliente falaria com ela pela rede, com as mesmas mensagens.

Todos os mobs ficam registados na sala (``rooms.json`` -> ``"mobs"``): tipo,
posição, rumo e vida. Ao reabrir o jogo, continuam onde estavam.

Unidades: posições em unidades do mundo (como o cliente; 1 bloco = 2); as
velocidades e alcances do ``mobs.json`` estão em blocos.
"""

from __future__ import annotations

import math
import random
import time
from typing import Callable, Dict, List, Optional

import numpy as np

import mob_types
from engine.mob_rig import MobBoxes, Rig, aabb_box, boxes_intersect, segment_hits_box

TICK = 0.05                 # 20 passos por segundo
GRAVITY = 30.0              # unidades do mundo / s²
SAVE_EVERY = 5.0            # segundos entre gravações na sala
PLAYER_TIMEOUT = 5.0        # jogador sem notícias há mais que isto: ignorado
MAX_CATCHUP = 0.5           # no máximo meio segundo de simulação por chamada
HIT_REACH = 7.0             # blocos: golpe de mais longe que isto é recusado
# Caixas de colisão do jogador: cabeça (o quarto de cima da altura) e corpo.
# Um golpe que pega na cabeça faz HEAD_MULT vezes o dano.
HEAD_FRACTION = 0.25
HEAD_MULT = 1.5


_BLOCKS = None


def _block_defs() -> dict:
    """blocks.json lido direto (o servidor não depende do Ursina)."""
    global _BLOCKS
    if _BLOCKS is None:
        import json
        from pathlib import Path
        try:
            import game_config
            _BLOCKS = game_config.read_json(Path(__file__).resolve().parent.parent / "blocks.json")
        except (OSError, ValueError):
            _BLOCKS = {}
    return _BLOCKS


class _World:
    """O que o servidor sabe do terreno: que células são sólidas."""

    def __init__(self, room, block_size: float):
        self.room = room
        self.size = float(block_size)
        self._types = {}

    def solid(self, cell) -> bool:
        x, y, z = cell
        if y < 0:
            return True
        sx = getattr(self.room, "size_x", None)
        sz = getattr(self.room, "size_z", None)
        if sx is not None and sz is not None and not (0 <= x < sx and 0 <= z < sz):
            return True                      # borda da sala: parede
        tipo = self.room.get_block(x, y, z)
        if tipo is None:
            return False
        bt = self._types.get(tipo)
        if bt is None:
            bt = self._types[tipo] = bool(_block_defs().get(tipo, {}).get("solid", True))
        return bt

    def cell_of(self, x, y, z):
        s = self.size
        return (math.floor((x + s / 2) / s), math.floor(y / s), math.floor((z + s / 2) / s))

    @property
    def height(self) -> int:
        return int(getattr(self.room, "size_y", 64))


class _Mob:
    __slots__ = ("id", "tipo", "x", "y", "z", "yaw", "health", "vy", "state",
                 "state_t", "anim", "anim_start", "target", "home", "goal",
                 "cooldown", "block_cd", "shield", "shield_t", "swing_hit",
                 "think", "dead")

    def __init__(self, mob_id, tipo, pos, yaw=0.0, health=None):
        cfg = mob_types.get(tipo) or {}
        self.id = mob_id
        self.tipo = tipo
        self.x, self.y, self.z = (float(v) for v in pos)
        self.yaw = float(yaw)
        self.health = float(cfg.get("health", 20) if health is None else health)
        self.vy = 0.0
        self.state = "idle"
        self.state_t = 0.0
        self.anim = "idle"
        self.anim_start = 0.0
        self.target = None
        self.home = (self.x, self.y, self.z)
        self.goal = None
        self.cooldown = 0.0
        self.block_cd = 0.0
        self.shield = float((cfg.get("block") or {}).get("shield_health", 0))
        self.shield_t = 0.0
        self.swing_hit = False
        self.think = random.uniform(0.5, 2.0)
        self.dead = False


class MobServer:
    """Simulação autoritativa dos mobs de UMA sala."""

    def __init__(self, room_id: str, world_room, block_size: float,
                 store: Callable[[str, dict], None], stored: Optional[list] = None):
        self.room_id = room_id
        self.world = _World(world_room, block_size)
        self.size = float(block_size)
        self._store = store
        self.mobs: Dict[str, _Mob] = {}
        self.players: Dict[str, dict] = {}
        self.events: Dict[str, List[dict]] = {}
        self.clock = 0.0                      # tempo de simulação (s)
        self._last_real = None
        self._acc = 0.0
        self._since_save = 0.0
        self._dirty = False
        self._next_id = 1
        self._spawn_t: Dict[str, float] = {}
        self._boxes: Dict[str, MobBoxes] = {}
        for entry in stored or []:
            try:
                m = _Mob(str(entry["id"]), str(entry["tipo"]), entry["pos"],
                         entry.get("yaw", 0.0), entry.get("health"))
            except (KeyError, TypeError, ValueError):
                continue
            if mob_types.get(m.tipo) is None:
                continue
            self.mobs[m.id] = m
            try:
                self._next_id = max(self._next_id, int(m.id.split("-")[-1]) + 1)
            except ValueError:
                pass

    # ------------------------------------------------------------ utilidades
    def cfg(self, m: _Mob) -> dict:
        return mob_types.get(m.tipo) or {}

    def boxes_for(self, tipo) -> Optional[MobBoxes]:
        if tipo not in self._boxes:
            path = mob_types.model_path(tipo)
            if path is None:
                self._boxes[tipo] = None
            else:
                cfg = mob_types.get(tipo) or {}
                self._boxes[tipo] = MobBoxes(Rig.load(path), cfg.get("hitboxes", {}),
                                             mob_types.world_scale(tipo, self.size))
        return self._boxes[tipo]

    def mob_boxes(self, m: _Mob, only=None):
        mb = self.boxes_for(m.tipo)
        if mb is None:
            return {}
        clip, t = self._clip_of(m)
        return mb.boxes((m.x, m.y, m.z), m.yaw, clip, t, only=only)

    def _clip_of(self, m: _Mob):
        anims = self.cfg(m).get("animations", {})
        return anims.get(m.anim, m.anim), self.clock - m.anim_start

    def _clip_length(self, m: _Mob, key) -> float:
        mb = self.boxes_for(m.tipo)
        clip = self.cfg(m).get("animations", {}).get(key, key)
        if mb is None or clip not in mb.rig.clips:
            return 1.0
        return mb.rig.clips[clip]["duration"]

    def _set_state(self, m: _Mob, state, anim=None):
        if m.state != state:
            m.state = state
            m.state_t = 0.0
        anim = anim or state
        if m.anim != anim:
            m.anim = anim
            m.anim_start = self.clock

    def _event(self, player_id, ev):
        self.events.setdefault(player_id, []).append(ev)

    def _broadcast(self, ev):
        for pid in self.players:
            self._event(pid, ev)

    # ------------------------------------------------------------ API
    def sync(self, player_id: str, state: dict) -> dict:
        """O cliente conta onde está o jogador; recebe o estado dos mobs."""
        now = time.monotonic()
        p = dict(state or {})
        p["seen"] = now
        prev = self.players.get(player_id)
        p["swing_new"] = bool(prev and p.get("swing", 0) != prev.get("swing", 0))
        self.players[player_id] = p
        self._advance(now)
        events = self.events.pop(player_id, [])
        return {"time": self.clock, "mobs": [self._public(m) for m in self.mobs.values()],
                "events": events}

    def attack(self, player_id: str, mob_id: str, damage: float, origin, kind="golpe") -> dict:
        """Pedido de golpe num mob. O servidor decide o resultado."""
        self._advance(time.monotonic())
        m = self.mobs.get(str(mob_id))
        if m is None or m.dead:
            return {"result": "invalido", "motivo": "mob não existe"}
        try:
            ox, oy, oz = (float(v) for v in origin)
            damage = max(0.0, float(damage))
        except (TypeError, ValueError):
            return {"result": "invalido", "motivo": "pedido malformado"}
        dist = math.dist((ox, oz), (m.x, m.z)) / self.size
        bash = kind == "empurrao"             # empurrão com escudo (igual ao servidor)
        if kind in ("golpe", "empurrao") and dist > HIT_REACH:
            return {"result": "invalido", "motivo": "longe demais"}
        cfg = self.cfg(m)
        block = cfg.get("block") or {}
        m.target = player_id                  # quem bate vira o alvo
        if bash and self._shield_up(m):       # abre a defesa, não gasta o escudo
            self._set_state(m, "block_break")
            self._dirty = True
            return {"result": "defesa_aberta", "damage": 0, "mob": self._public(m)}
        # Escudo levantado e entre o atacante e o corpo: bloqueia.
        if kind == "golpe" and self._shield_up(m):
            boxes = self.mob_boxes(m, only=(block.get("hitbox", "escudo"), "corpo"))
            shield, body = boxes.get(block.get("hitbox", "escudo")), boxes.get("corpo")
            if shield is not None and body is not None and \
                    segment_hits_box((ox, oy, oz), body[0], shield):
                m.shield -= damage
                self._dirty = True
                if m.shield <= 0:
                    m.shield = 0.0
                    m.shield_t = 0.0
                    self._set_state(m, "block_break")
                    return {"result": "escudo_quebrou", "damage": damage, "mob": self._public(m)}
                return {"result": "bloqueou", "damage": damage, "mob": self._public(m)}
        if m.state == "block_break":
            damage *= float(block.get("broken_damage_mult", 1.0))
        m.health -= damage
        self._dirty = True
        if m.health <= 0:
            m.dead = True
            self.mobs.pop(m.id, None)
            self._broadcast({"type": "mob_died", "mob": m.id, "tipo": m.tipo,
                             "pos": [m.x, m.y, m.z]})
            return {"result": "morreu", "damage": damage, "mob": self._public(m)}
        # Recuo: meio bloco para longe do atacante (o empurrão: bloco e meio).
        dx, dz = m.x - ox, m.z - oz
        n = math.hypot(dx, dz) or 1.0
        push = 1.5 if bash else 0.5
        self._move(m, dx / n * self.size * push, dz / n * self.size * push)
        return {"result": "acertou", "damage": damage, "mob": self._public(m)}

    def spawn(self, tipo: str, pos=None) -> dict:
        if mob_types.get(tipo) is None:
            return {"result": "invalido", "motivo": f"tipo {tipo!r} não existe"}
        if pos is None:
            pos = self._spawn_point(tipo)
            if pos is None:
                return {"result": "invalido", "motivo": "sem lugar livre"}
        m = _Mob(f"mob-{self._next_id}", tipo, pos, random.uniform(0, 360))
        self._next_id += 1
        m.anim_start = self.clock
        self.mobs[m.id] = m
        self._dirty = True
        return {"result": "ok", "mob": self._public(m)}

    def save(self) -> None:
        data = [{"id": m.id, "tipo": m.tipo, "pos": [round(m.x, 3), round(m.y, 3), round(m.z, 3)],
                 "yaw": round(m.yaw, 1), "health": round(m.health, 2)}
                for m in self.mobs.values()]
        try:
            self._store(self.room_id, {"mobs": data})
        except Exception as exc:
            print(f"[mobs] não foi possível gravar: {exc}")
        self._dirty = False

    def _public(self, m: _Mob) -> dict:
        cfg = self.cfg(m)
        return {"id": m.id, "tipo": m.tipo, "pos": [m.x, m.y, m.z], "yaw": m.yaw,
                "health": max(0.0, m.health), "max_health": float(cfg.get("health", 20)),
                "state": m.state, "anim": m.anim, "anim_time": self.clock - m.anim_start,
                "shield": m.shield,
                "shield_max": float((cfg.get("block") or {}).get("shield_health", 0))}

    # ------------------------------------------------------------ simulação
    def _advance(self, now: float) -> None:
        if self._last_real is None:
            self._last_real = now
            return
        self._acc += min(MAX_CATCHUP, max(0.0, now - self._last_real))
        self._last_real = now
        while self._acc >= TICK:
            self._acc -= TICK
            self._step(TICK)

    def _active_players(self):
        now = time.monotonic()
        return {pid: p for pid, p in self.players.items()
                if now - p.get("seen", 0) <= PLAYER_TIMEOUT and not p.get("dead")}

    def _step(self, dt: float) -> None:
        players = self._active_players()
        if not players:
            return                          # ninguém por perto: mundo parado
        self.clock += dt
        for m in tuple(self.mobs.values()):
            self._think(m, players, dt)
            self._gravity(m, dt)
        self._separate()
        self._spawner(players, dt)
        self._since_save += dt
        if self._since_save >= SAVE_EVERY:
            self._since_save = 0.0
            if self._dirty:
                self.save()

    # -- comportamento --------------------------------------------------------
    def _think(self, m: _Mob, players: dict, dt: float) -> None:
        cfg = self.cfg(m)
        bs = self.size
        m.state_t += dt
        m.cooldown = max(0.0, m.cooldown - dt)
        m.block_cd = max(0.0, m.block_cd - dt)
        block = cfg.get("block") or {}
        if block and m.shield <= 0:
            m.shield_t += dt
            if m.shield_t >= float(block.get("regen_after", 8)):
                m.shield = float(block.get("shield_health", 0))
                self._dirty = True

        # alvo: o jogador mais perto dentro do alcance de deteção
        target, tdist = None, 1e9
        for pid, p in players.items():
            pos = p.get("pos") or (0, 0, 0)
            d = math.dist((pos[0], pos[2]), (m.x, m.z)) / bs
            limit = float(cfg.get("lose_range" if pid == m.target else "detect_range", 12))
            if d <= limit and abs(pos[1] - m.y) / bs < 6 and d < tdist:
                target, tdist = pid, d
        m.target = target
        p = players.get(target) if target else None

        # Estados que se completam sozinhos (animações "presas").
        if m.state == "attack":
            self._attack_tick(m, p, cfg)
            return
        if m.state == "block_start":
            if p is not None:
                self._face(m, p["pos"], cfg, dt)
            if m.state_t >= self._clip_length(m, "block_start"):
                self._set_state(m, "block")
            return
        if m.state == "block":
            if p is not None:
                self._face(m, p["pos"], cfg, dt)
            if m.state_t >= float(block.get("hold", 2.0)):
                self._set_state(m, "block_end")
            return
        if m.state == "block_end":
            if m.state_t >= self._clip_length(m, "block_end") * 0.6:
                m.block_cd = float(block.get("cooldown", 3.0))
                self._set_state(m, "idle")
            return
        if m.state == "block_break":
            if m.state_t >= self._clip_length(m, "block_break"):
                m.block_cd = float(block.get("cooldown", 3.0))
                self._set_state(m, "idle")
            return

        if p is None:
            self._wander(m, cfg, dt)
            return

        ppos = p["pos"]
        # Defesa: o jogador golpeou por perto -> chance de levantar o escudo.
        if (block and p.get("swing_new") and m.shield > 0 and m.block_cd <= 0
                and tdist <= float(block.get("range", 3))
                and random.random() < float(block.get("chance", 0.4))):
            p["swing_new"] = False
            self._set_state(m, "block_start")
            return
        attack = cfg.get("attack") or {}
        if tdist <= float(cfg.get("attack_range", 1.6)):
            self._face(m, ppos, cfg, dt)
            if attack and m.cooldown <= 0 and self._facing(m, ppos) < 35:
                m.swing_hit = False
                self._set_state(m, "attack")
            else:
                self._set_state(m, "idle")
            return
        # Persegue: corre de longe, anda de perto.
        run = tdist > float(cfg.get("run_range", 5))
        speed = float(cfg.get("run_speed" if run else "walk_speed", 2)) * bs
        self._face(m, ppos, cfg, dt)
        self._set_state(m, "chase", "run" if run else "walk")
        self._walk_towards(m, ppos, speed, dt,
                           stop_at=float(cfg.get("attack_range", 1.6)) * 0.85 * bs)

    def _attack_tick(self, m: _Mob, p: Optional[dict], cfg: dict) -> None:
        attack = cfg.get("attack") or {}
        length = self._clip_length(m, "attack")
        w0, w1 = attack.get("window", [0.0, length])
        if p is not None and not m.swing_hit and w0 <= m.state_t <= w1:
            sword = self.mob_boxes(m, only=(attack.get("hitbox", "espada"),))
            box = sword.get(attack.get("hitbox", "espada"))
            if box is not None:
                for part, pbox in self._player_boxes(p):     # cabeça primeiro
                    if not boxes_intersect(box, pbox):
                        continue
                    m.swing_hit = True
                    damage = float(attack.get("damage", 2))
                    if part == "cabeca":
                        damage *= HEAD_MULT
                    self._event(m.target, {"type": "player_hit", "mob": m.id, "tipo": m.tipo,
                                           "damage": damage, "part": part,
                                           "kind": "espada", "from": [m.x, m.y, m.z]})
                    break
        if m.state_t >= length:
            m.cooldown = float(attack.get("cooldown", 1.0))
            self._set_state(m, "idle")

    def _player_boxes(self, p: dict):
        """[("cabeca", caixa), ("corpo", caixa)] do jogador."""
        x, y, z = p.get("pos") or (0, 0, 0)
        hw = float(p.get("half_width", 0.45))
        h = float(p.get("height", 3.0))
        neck = y + h * (1.0 - HEAD_FRACTION)
        return [("cabeca", aabb_box((x - hw, neck, z - hw), (x + hw, y + h, z + hw))),
                ("corpo", aabb_box((x - hw, y, z - hw), (x + hw, neck, z + hw)))]

    def _shield_up(self, m: _Mob) -> bool:
        block = self.cfg(m).get("block") or {}
        if m.shield <= 0:
            return False
        if m.state == "block":
            return True
        return m.state == "block_start" and m.state_t >= float(block.get("raised_after", 0.5))

    def _wander(self, m: _Mob, cfg: dict, dt: float) -> None:
        bs = self.size
        if m.goal is None:
            m.think -= dt
            self._set_state(m, "idle")
            if m.think > 0:
                return
            r = float(cfg.get("wander_radius", 6)) * bs
            a = random.uniform(0, 2 * math.pi)
            d = random.uniform(0.3, 1.0) * r
            m.goal = (m.home[0] + math.cos(a) * d, m.home[2] + math.sin(a) * d)
            m.think = random.uniform(2.0, 5.0)
        gx, gz = m.goal
        if math.hypot(gx - m.x, gz - m.z) < bs * 0.3 or m.state_t > 8.0:
            m.goal = None
            self._set_state(m, "idle")
            return
        self._face(m, (gx, m.y, gz), cfg, dt)
        self._set_state(m, "wander", "walk")
        if not self._walk_towards(m, (gx, m.y, gz),
                                  float(cfg.get("walk_speed", 2)) * bs * 0.7, dt):
            m.goal = None

    def _face(self, m: _Mob, pos, cfg, dt) -> None:
        want = math.degrees(math.atan2(pos[0] - m.x, pos[2] - m.z))
        diff = (want - m.yaw + 180.0) % 360.0 - 180.0
        step = float(cfg.get("turn_speed", 300)) * dt
        m.yaw = (m.yaw + max(-step, min(step, diff))) % 360.0

    def _facing(self, m: _Mob, pos) -> float:
        want = math.degrees(math.atan2(pos[0] - m.x, pos[2] - m.z))
        return abs((want - m.yaw + 180.0) % 360.0 - 180.0)

    # -- movimento ------------------------------------------------------------
    def _radius(self, m: _Mob) -> float:
        mb = self.boxes_for(m.tipo)
        if mb is not None and "corpo" in mb.defs:
            _b, lo, hi = mb.defs["corpo"]
            return float(max(hi[0] - lo[0], hi[2] - lo[2]) / 2 * mb.scale) * 0.8
        return self.size * 0.35

    def _height(self, m: _Mob) -> float:
        mb = self.boxes_for(m.tipo)
        if mb is not None and "corpo" in mb.defs:
            _b, lo, hi = mb.defs["corpo"]
            return float(hi[1] * mb.scale)
        return self.size * 2

    def _blocked(self, x, y, z, r, h) -> bool:
        w = self.world
        s = self.size
        y0, y1 = y + 0.05, y + h - 0.05
        cx0, _cy, cz0 = w.cell_of(x - r, y0, z - r)
        cx1, _cy, cz1 = w.cell_of(x + r, y0, z + r)
        by0, by1 = math.floor(y0 / s), math.floor(y1 / s)
        for cx in range(cx0, cx1 + 1):
            for cz in range(cz0, cz1 + 1):
                for cy in range(by0, by1 + 1):
                    if w.solid((cx, cy, cz)):
                        return True
        return False

    def _move(self, m: _Mob, dx, dz) -> bool:
        r, h = self._radius(m), self._height(m)
        moved = False
        for ddx, ddz in ((dx, dz), (dx, 0.0), (0.0, dz)):
            if ddx == 0 and ddz == 0:
                continue
            nx, nz = m.x + ddx, m.z + ddz
            if not self._blocked(nx, m.y, nz, r, h):
                m.x, m.z = nx, nz
                moved = True
                break
            # degrau de um bloco: sobe se houver espaço em cima
            up = m.y + self.size
            if m.vy == 0 and not self._blocked(nx, up, nz, r, h) and \
                    not self._blocked(m.x, up, m.z, r, h):
                m.x, m.y, m.z = nx, up, nz
                moved = True
                break
        if moved:
            self._dirty = True
        return moved

    def _walk_towards(self, m: _Mob, pos, speed, dt, stop_at=0.0) -> bool:
        dx, dz = pos[0] - m.x, pos[2] - m.z
        d = math.hypot(dx, dz)
        if d <= stop_at or d < 1e-6:
            return True
        step = min(speed * dt, d - stop_at)
        return self._move(m, dx / d * step, dz / d * step)

    def _ground(self, m: _Mob) -> float:
        """Altura do chão debaixo do mob (topo da célula sólida mais alta)."""
        s = self.size
        r = self._radius(m)
        best = -1e9
        for ox, oz in ((0, 0), (-r, -r), (r, -r), (-r, r), (r, r)):
            cx, cy, cz = self.world.cell_of(m.x + ox, m.y - 0.01, m.z + oz)
            for y in range(cy, cy - 8, -1):
                if self.world.solid((cx, y, cz)):
                    best = max(best, (y + 1) * s)
                    break
        return best

    def _gravity(self, m: _Mob, dt: float) -> None:
        ground = self._ground(m)
        if m.y > ground + 1e-3 or m.vy > 0:
            m.vy -= GRAVITY * dt
            m.y += m.vy * dt
            if m.y <= ground:
                m.y, m.vy = ground, 0.0
            self._dirty = True
        else:
            m.y, m.vy = max(m.y, ground), 0.0
        if m.y < -50:                      # caiu do mundo
            m.dead = True
            self.mobs.pop(m.id, None)
            self._broadcast({"type": "mob_died", "mob": m.id, "tipo": m.tipo,
                             "pos": [m.x, m.y, m.z]})

    def _separate(self) -> None:
        mobs = list(self.mobs.values())
        for i, a in enumerate(mobs):
            ra = self._radius(a)
            for b in mobs[i + 1:]:
                rb = self._radius(b)
                dx, dz = b.x - a.x, b.z - a.z
                d = math.hypot(dx, dz)
                if d < ra + rb and abs(a.y - b.y) < self.size:
                    push = (ra + rb - d) / 2
                    nx, nz = (dx / d, dz / d) if d > 1e-6 else (1.0, 0.0)
                    self._move(a, -nx * push, -nz * push)
                    self._move(b, nx * push, nz * push)

    # -- aparecimento ---------------------------------------------------------
    def _spawner(self, players: dict, dt: float) -> None:
        for tipo, cfg in mob_types.all_types().items():
            spawn = (cfg.get("spawn") or {}).get("privada") or {}   # modo local = mundo privado
            if not spawn:
                continue
            t = self._spawn_t.get(tipo, float(spawn.get("recarga", spawn.get("interval", 15))) * 0.2) - dt
            self._spawn_t[tipo] = t
            if t > 0:
                continue
            self._spawn_t[tipo] = float(spawn.get("recarga", spawn.get("interval", 15)))
            alive = sum(1 for m in self.mobs.values() if m.tipo == tipo)
            if alive < int(spawn.get("max", 0)):
                self.spawn(tipo)

    def _spawn_point(self, tipo):
        cfg = mob_types.get(tipo) or {}
        spawn = (cfg.get("spawn") or {}).get("privada") or {}
        players = list(self._active_players().values())
        if not players:
            return None
        s = self.size
        for _ in range(20):
            p = random.choice(players)["pos"]
            a = random.uniform(0, 2 * math.pi)
            d = random.uniform(float(spawn.get("min_distance", 10)),
                               float(spawn.get("max_distance", 20))) * s
            x, z = p[0] + math.cos(a) * d, p[2] + math.sin(a) * d
            cx, _cy, cz = self.world.cell_of(x, 0, z)
            for cy in range(min(self.world.height - 3, int(p[1] / s) + 6), -1, -1):
                if self.world.solid((cx, cy, cz)):
                    if not self.world.solid((cx, cy + 1, cz)) and \
                            not self.world.solid((cx, cy + 2, cz)):
                        return (cx * s, (cy + 1) * s, cz * s)
                    break
        return None


__all__ = ["MobServer", "TICK"]
