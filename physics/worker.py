"""Física dos blocos no servidor.

O servidor Node (server/src/physics/bridge.js) arranca este processo e fala
com ele por linhas JSON (stdin -> pedidos, stdout -> resultados). Aqui corre
a MESMA física do jogo (engine/block_physics.py), sem gráficos, uma por sala
aberta: os jogadores só pedem ("bati", "pus", "tirei", "colei") e o servidor
decide e manda o que mudou a todos.

Pedidos (do Node):
  {"op": "open",   "room", "data": {...sala...}}
  {"op": "close",  "room"}
  {"op": "hit",    "room", "by", "cell": [x,y,z], "damage", "dir": [dx,dy,dz]}
  {"op": "push",   "room", "by", "cell", "dir", "force"}  (agachado: força em N)
  {"op": "place",  "room", "by", "cell", "tipo", "orientation"}
  {"op": "remove", "room", "by", "cell"}
  {"op": "glue",   "room", "by", "cell", "normal"}
  {"op": "set",    "room", "by", "key", "value"}      (bloco direto, legado)
  {"op": "chest",  "room", "by", "cell", "entries"}   (fechou um baú do mundo)
  {"op": "bank",   "room", "by", "cell", "delta"}     (guardou/tirou almas do minoculus)
  Economia (o que cada jogador tem; ver economy.py), sem sala:
  {"op": "eco_load",     "player", "ledger", "inventory", "almas", "grants", "souls", "req"}
  {"op": "eco_check",    "player", "inv", "almas"}    (o inventário que o jogo gravou)
  {"op": "eco_credit",   "player", "items": [[nome, n]], "souls"}   (drops/almas de mobs)
  {"op": "eco_icont",    "player", "cid", "old", "new", "size", "req"}  (baú na mão)
  {"op": "eco_transmute","player", "idx", "item", "stone_d"}
  {"op": "eco_buy",      "player", "item"}                 (loja do minoculus)
  {"op": "ignite",       "room", "by", "cell"}             (isqueiro: acende o bloco, ou o ar em cima de um bloco)
  {"op": "water",        "room", "changes": [[key, nível]...]}  (a água da sala, para o fogo)
  {"op": "bucket_fill",  "room", "cell"}                   (balde vazio do mundo encheu)
  {"op": "eco_test",     "player", "kind"}             (F6/F7/F8, só no modo de testes)
  {"op": "death",        "room", "by", "pos", "yaw", "nome"}   (morreu: tudo vai para o cadáver)
  {"op": "corpse",       "room", "by", "id", "entries", "at"}  (fechou um cadáver: tirou/pôs itens)

Respostas (para o Node):
  {"ev": "changes", "room", "blocks": [[key, valor]...], "fields": {...},
   "anims": [[tipo, dados]...], "fx": [[célula, tipo, n, vel]...]}
  {"ev": "to", "room", "player", "msg": {...}}         (só para um jogador)
  {"ev": "log", "msg"}
  {"ev": "reply", "req", ...}                          (resposta a um pedido com "req")
  {"ev": "eco_save", "player", "ledger", "inventory", "almas"}   (Node grava no jogador)
  {"ev": "eco_to", "player", "msg"}                    (ex.: correção do inventário)
  {"ev": "icont_store", "cid", "entries", "player"} / {"ev": "icont_delete", "cid"}
"""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
GAME = os.environ.get("GAME_DIR") or os.path.abspath(os.path.join(HERE, "..", "jogo"))   # no Docker: /app
sys.path.insert(0, os.path.join(HERE, "shim"))
sys.path.insert(1, GAME)
sys.path.insert(2, HERE)
os.chdir(GAME)                     # blocks.json, itens.json... relativos à pasta do jogo

TICK = 0.05                        # 20x por segundo

_out = sys.stdout
sys.stdout = sys.stderr            # prints da física vão para o log, não para o protocolo


def emit(obj):
    _out.write(json.dumps(obj, separators=(",", ":"), default=_json_default) + "\n")
    _out.flush()


def _json_default(o):
    if isinstance(o, (set, frozenset, tuple)):
        return list(o)
    return str(o)


def log(msg):
    print(f"[física-servidor] {msg}", file=sys.stderr, flush=True)


from CRUD_room import Room                       # noqa: E402
from engine.block_physics import BlockPhysics    # noqa: E402
from engine.grass import GrassGrowth             # noqa: E402
from engine.apples import AppleTrees             # noqa: E402
from headless_room import HeadlessChunkRoom      # noqa: E402
import blocks as _blocks                          # noqa: E402
import economy as eco                             # noqa: E402

_blocks.ensure_loaded()

# O livro de cada jogador (o que ele tem de verdade). Vive aqui (um processo
# só para todas as salas); o Node grava cada mudança no registo do jogador.
LEDGERS = {}


def ledger(pid):
    return LEDGERS.get(str(pid)) if pid else None


def flush_ledgers():
    for pid, led in LEDGERS.items():
        if led.dirty:
            led.dirty = False
            emit({"ev": "eco_save", "player": pid, "ledger": led.dump(),
                  "inventory": led.layout, "almas": led.almas})
            try:
                emit({"ev": "eco_profile", "player": pid, "profile": eco.profile(led)})
            except Exception as exc:
                log(f"perfil de {pid}: {exc}")


def eco_to(pid, msg):
    emit({"ev": "eco_to", "player": pid, "msg": msg})


def _unkey(text):
    return tuple(int(v) for v in str(text).split(","))


class WorkerBackend:
    """O "backend" da sala no worker: guarda o que a sala quer gravar e o
    worker manda ao Node (que grava no diário e avisa os jogadores)."""

    def __init__(self):
        self.data = {}
        self.sims = {}

    def load_room(self, room_id):
        return self.data.pop(room_id)

    def set_room_block(self, room_id, key, value):
        sim = self.sims.get(room_id)
        if sim is not None:
            sim.out_blocks.append([key, value])

    def migrate_room(self, room_id, fields):
        sim = self.sims.get(room_id)
        if sim is not None and not sim.quiet:
            sim.out_fields.update(fields)

    def set_room_links(self, room_id, links):
        self.migrate_room(room_id, {"links": list(links)})

    def reset_room_modifications(self, room_id):
        pass


BACKEND = WorkerBackend()


class RoomSim:
    def __init__(self, room_id, data):
        self.id = room_id
        self.out_blocks, self.out_fields, self.out_anims, self.out_fx = [], {}, [], []
        self.actor = None
        self.quiet = False
        BACKEND.sims[room_id] = self
        BACKEND.data[room_id] = data
        self.room = Room(BACKEND, room_id)
        self.cr = HeadlessChunkRoom(self.room)
        self.physics = BlockPhysics(self.cr)
        p = self.physics
        p.on_moved = self._moved
        p.on_destroyed = self._destroyed
        p.on_void = self._void
        p.on_anim = lambda kind, d: self.out_anims.append([kind, d])
        p.on_burst = lambda cell, tipo, n, v: self.out_fx.append([list(cell), tipo, n, v])
        p._proxy = lambda *_a, **_k: None        # sem cópias gráficas no servidor
        p._particles = lambda: None
        self.grass = GrassGrowth(self.cr)
        # Fogo (engine/fire.py): arde aqui, no servidor; o estado vai no campo
        # "fogo" da sala (gravado e mandado a todos). A água vem do Node ("water").
        from engine.fire import FireSystem
        self.water = {}
        self.fire = FireSystem(self.cr, water_level=lambda c: self.water.get(c, 0),
                               remove_block=self._burnt, save=self.room.save_fire,
                               damage_block=self.physics.burn)
        self.fire.apply_state(getattr(self.room, "fire_state", {}) or {})
        # Cadáveres (engine/corpses.py): campo "corpos" da sala.
        from engine.corpses import Corpses
        self.corpses = Corpses(save=self.room.save_corpses)
        self.corpses.apply_state(getattr(self.room, "corpses", {}) or {})
        # Na sala pública as árvores não fazem maçãs (as folhas quebradas ainda
        # podem largar uma, "ao_quebrar" em blocks.json).
        self.public = bool(data.get("publica")) or room_id == "room_publica"
        self.apples = None if self.public else AppleTrees(self.cr, self.physics, lambda: True)
        # Capim (engine/tufts.py): nasce sozinho na grama; os mobs comem-no ("graze").
        from engine.tufts import TuftGrowth
        self.tufts = TuftGrowth(self.cr, water_level=lambda c: self.water.get(c, 0))
        # Flores lunares (luz fraca de noite), só na sala pública.
        from engine.tufts import glow_flowers
        self.glow = glow_flowers(self.cr, water_level=lambda c: self.water.get(c, 0)) if self.public else None
        # Mini sóis (soltam fadas), também só na sala pública.
        from engine.tufts import mini_suns
        self.suns = mini_suns(self.cr, water_level=lambda c: self.water.get(c, 0)) if self.public else None
        if not bool(data.get("auto_cola_limpa")):
            n = self.physics.clean_outside_links()
            if n:
                log(f"{room_id}: {n} bloco(s) fora do mapa descolados")
            self.out_fields["auto_cola_limpa"] = True
        log(f"{room_id}: aberta ({len(self.cr.block_map)} blocos)")

    # --- ganchos da física ----------------------------------------------------
    def _moved(self, src, dest):
        self.room.move_container(src, dest)
        self.room.move_soul_bank(src, dest)
        fire = getattr(self, "fire", None)
        if fire is not None:
            fire.moved(src, dest)

    def _burnt(self, owner):
        """O fogo consumiu o bloco: some (sem item; o que um baú tinha arde)."""
        tipo = self.cr.block_map.get(owner)
        if tipo is None:
            return
        cells = self.cr.cells_for_block(owner, tipo)
        self.room.pop_container(owner)
        self.room.pop_soul_bank(owner)
        self.cr.remove_block(owner)
        self.physics.on_removed(owner, cells)
        self.out_fx.append([list(owner), tipo, 10, 2.0])

    def _destroyed(self, owner, tipo):
        contents = self.room.pop_container(owner)
        souls = self.room.pop_soul_bank(owner)
        if self.actor and (contents or souls):
            # quem quebrou fica com o conteúdo (baú) e as almas (minoculus)
            led = ledger(self.actor)
            if led is not None:
                for e in eco.clean_entries(contents, len(contents)):
                    if e:
                        name = eco.item_name(e[0])
                        data = e[2] if len(e) > 2 else None
                        led.add(name, int(e[1]), data)
                led.add_souls(int(souls or 0))
            self.to(self.actor, {"t": "destroyed", "tipo": tipo, "contents": contents,
                                 "souls": souls})

    def _void(self, owner, tipo, dest):
        if dest is None:
            self.room.pop_container(owner)        # caiu no void com o que tinha
            self.room.pop_soul_bank(owner)

    def to(self, player, msg):
        emit({"ev": "to", "room": self.id, "player": player, "msg": msg})

    # --- pedidos ----------------------------------------------------------------
    def handle(self, msg):
        op = msg.get("op")
        self.actor = msg.get("by")
        try:
            if op == "hit":
                cell = tuple(int(v) for v in msg["cell"])
                d = tuple(int(v) for v in (msg.get("dir") or (0, 0, 0)))
                summary = self.physics.hit(cell, float(msg.get("damage", 0)), d)
                if self.actor:
                    self.to(self.actor, {"t": "hit_result", "cell": list(cell),
                                         "damage": float(msg.get("damage", 0)),
                                         "summary": summary})
            elif op == "push":
                cell = tuple(int(v) for v in msg["cell"])
                d = tuple(int(v) for v in (msg.get("dir") or (0, 0, 0)))
                if (abs(d[0]) + abs(d[2]) == 1) and d[1] == 0:
                    self.physics.push(cell, d, float(msg.get("force", 0)))
            elif op == "place":
                self._place_paid(tuple(int(v) for v in msg["cell"]), msg.get("tipo"),
                                 msg.get("orientation"), msg.get("data"), msg.get("contents"))
            elif op == "remove":
                self._remove_paid(tuple(int(v) for v in msg["cell"]), msg.get("cid"))
            elif op == "glue":
                led = ledger(self.actor)
                if led is not None and led.count("cola") > 0:
                    if self.physics.glue(tuple(int(v) for v in msg["cell"]),
                                         tuple(int(v) for v in msg["normal"]) if msg.get("normal") else None):
                        led.take("cola", 1)
            elif op == "chest":
                self._chest(tuple(int(v) for v in msg["cell"]), msg.get("entries") or [])
            elif op == "bank":
                self._bank(tuple(int(v) for v in msg["cell"]), msg.get("delta", 0))
            elif op == "set":
                # Bloco direto (sem pagar): só o servidor usa (ex.: ferramentas).
                cell = _unkey(msg["key"])
                value = msg.get("value")
                tipo = value.get("type") if isinstance(value, dict) else value
                orient = value.get("orientation") if isinstance(value, dict) else None
                owner = self.cr.owner_at(cell)
                if owner is not None:
                    cells = self.cr.cells_for_block(owner, self.cr.block_map.get(owner))
                    self.cr.remove_block(owner)
                    self.physics.on_removed(owner, cells)
                if tipo:
                    self._place(cell, tipo, orient)
            elif op == "fields":
                self._apply_fields(msg.get("fields") or {})
            elif op == "graze":
                # um mob comeu o capim desta célula (o Node decide quem come)
                cell = tuple(int(v) for v in msg["cell"])
                if self.cr.block_map.get(cell) == "capim":
                    self.cr.remove_block(cell)
                    self.physics.on_removed(cell, [cell])
                    self.out_fx.append([list(cell), "capim", 6, 1.5])
            elif op == "death":
                self._death(msg)
            elif op == "corpse":
                self._corpse(msg)
            elif op == "bucket_fill":
                self._bucket_fill(tuple(int(v) for v in msg["cell"]))
            elif op == "ignite":
                # isqueiro: o jogador tem de ter um (no livro)
                led = ledger(self.actor)
                if led is not None and led.count("isqueiro") > 0:
                    self.fire.light(tuple(int(v) for v in msg["cell"]))   # bloco que arde, ou o ar em cima
                    eco.wear_unit(led, "isqueiro", 1.0)                  # cada uso gasta 1 (a 0 some)
            elif op == "water":
                for k, v in msg.get("changes") or []:
                    try:
                        c, n = _unkey(k), int(v)
                    except (TypeError, ValueError):
                        continue
                    if n > 0:
                        self.water[c] = n
                    else:
                        self.water.pop(c, None)
        finally:
            self.actor = None

    def _bucket_fill(self, cell):
        """Um recipiente do mundo (balde vazio) encheu com água que caiu nele:
        troca pelo bloco cheio (balde de água), com a mesma orientação. O
        livro não muda: no livro balde e balde de água são o mesmo balde."""
        owner = self.cr.owner_at(cell)
        if owner is None:
            return
        tipo = self.cr.block_map.get(owner)
        rc = self._props(tipo).get("recipiente")
        if not isinstance(rc, dict) or not rc.get("cheio"):
            return
        from blocks import get_block_type
        full = str(rc["cheio"])
        if get_block_type(full) is None:
            return
        orient = self.cr.orientation_at(owner) if hasattr(self.cr, "orientation_at") else None
        self.cr.remove_block(owner)
        self.cr.set_block(owner, full, orientation=orient or None)
        self.physics.on_placed(owner)

    def _reject(self, cell):
        # o jogador pediu algo que já não dá: devolve o estado certo da célula
        owner = self.cr.owner_at(cell)
        if self.actor:
            self.to(self.actor, {"t": "reject", "cell": list(cell)})
        if owner is None:
            self.out_blocks.append([f"{cell[0]},{cell[1]},{cell[2]}", None])

    def _place(self, cell, tipo, orientation):
        from blocks import get_block_type
        if not tipo or get_block_type(tipo) is None:
            self._reject(cell)
            return False
        for c in self.cr.cells_for_block(cell, tipo):
            if c in self.cr.cells or not self.cr.in_bounds(c):
                self._reject(cell)
                return False
        self.cr.set_block(cell, tipo, orientation=orientation or None)
        self.physics.on_placed(cell)
        return True

    # --- blocos pagos com o livro do jogador ---------------------------------------
    @staticmethod
    def _props(tipo):
        from blocks import get_block_type
        bt = get_block_type(tipo) if tipo else None
        return (bt.raw.get("block_properties") or {}) if bt is not None else {}

    def _place_paid(self, cell, tipo, orientation, data, contents):
        """Pôr um bloco gasta o item dele (que o jogador tem de ter). Um baú
        que trazia conteúdo (guardado no servidor) devolve-o ao bloco; um
        minoculus com almas volta a guardá-las no mundo."""
        led = ledger(self.actor)
        # paga com o item do próprio bloco (ex.: grama); variantes sem item
        # (ex.: eixo deitado) pagam com o item do "drop" (o eixo)
        name = None
        for cand in (tipo, self._props(tipo).get("drop")) if tipo else ():
            got = eco.item_name(eco.item_id_for_block(cand)) if cand else None
            if got:
                name = got
                break
        if led is None or not name or led.count(eco.family(name)) <= 0:
            return self._reject(cell)
        data = data if isinstance(data, dict) else {}
        unit = {"d": None, "c": data.get("container") if isinstance(data.get("container"), str) else None}
        if not self._place(cell, tipo, orientation):
            return
        got = led.take_unit(name, unit) if eco.is_unit(eco.family(name)) else None
        if got is None and not eco.is_unit(eco.family(name)):
            led.take(name, 1)
        cid = (got or {}).get("c")
        if cid and cid == unit["c"]:
            entries = [e for e in (contents or []) if e]
            if entries:
                self.room.set_container(cell, contents)
            emit({"ev": "icont_delete", "cid": cid})
        try:
            almas = max(0, int(data.get("almas", 0) or 0))
        except (TypeError, ValueError):
            almas = 0
        if almas and self._props(tipo).get("portal"):
            got_souls = led.take_souls(almas)
            if got_souls:
                self.room.set_soul_bank(cell, got_souls)

    def _remove_paid(self, cell, cid):
        """Tirar um bloco (Q) dá o item dele ao jogador (o "drop" do bloco).
        Baú com itens: o conteúdo vai para o servidor com o id ``cid`` (o
        baú no inventário leva esse id). Minoculus: as almas vão com ele."""
        owner = self.cr.owner_at(cell)
        led = ledger(self.actor)
        if owner is None or led is None:
            return self._reject(cell)
        tipo = self.cr.block_map.get(owner)
        props = self._props(tipo)
        if props.get("collectable", True) is False or self.physics.is_attached(owner):
            return self._reject(cell)
        name = eco.item_name(eco.item_id_for_block(props.get("drop") or tipo))
        if not name:
            return self._reject(cell)
        has_contents = not self.room.container_is_empty(owner)
        if has_contents and not (isinstance(cid, str) and cid.startswith("cnt-")):
            return self._reject(cell)           # o jogo não sabia do conteúdo
        cells = self.cr.cells_for_block(owner, tipo)
        entries = self.room.pop_container(owner) if has_contents else []
        souls = self.room.pop_soul_bank(owner)
        self.cr.remove_block(owner)
        self.physics.on_removed(owner, cells)
        led.add(name, 1, {"container": cid} if has_contents else None)
        if souls:
            led.add_souls(souls)
        if has_contents:
            emit({"ev": "icont_store", "cid": cid, "entries": entries, "player": self.actor})

    def _chest(self, cell, entries):
        """Fechou um baú do mundo: o que entrou sai do livro, o que saiu entra."""
        owner = self.cr.owner_at(cell)
        led = ledger(self.actor)
        tipo = self.cr.block_map.get(owner) if owner is not None else None
        cfg = self._props(tipo).get("container")
        if led is None or not cfg:
            if self.actor:
                self.to(self.actor, {"t": "chest_fix", "cell": list(cell), "entries": None})
            return
        size = int(cfg.get("slots", 24)) if isinstance(cfg, dict) else 24
        old = self.room.container_slots(owner, size)
        new, changed = eco.transfer(led, old, entries, size)
        self.room.set_container(owner, new)
        if changed and self.actor:
            self.to(self.actor, {"t": "chest_fix", "cell": list(owner), "entries": new})

    def _death(self, msg):
        """Morreu: TUDO o que o livro tem vai para um cadáver onde morreu (o
        jogador volta sem itens; as almas ficam com ele)."""
        led = ledger(self.actor)
        if led is None:
            return
        try:
            pos = [float(v) for v in msg.get("pos")][:3]
        except (TypeError, ValueError):
            return
        from engine.corpses import MAX_SLOTS
        entries = eco.ledger_entries(led)[:MAX_SLOTS]
        moved, _ = eco.transfer(led, [], entries, len(entries)) if entries else ([], False)
        # o que não coube (ou não se percebeu) também se perde: volta sem nada
        if led.items or any(led.units.values()):
            led.items.clear()
            led.units = {}
            led.dirty = True
        led.layout = None
        cid = self.corpses.create(pos, msg.get("yaw", 0.0), msg.get("nome"), self.actor, moved)
        print(f"[cadáver] {self.actor} morreu em {self.id}: {eco.entries_bag(moved)[0] if moved else {}} -> {cid}",
              file=sys.stderr)

    def _corpse(self, msg):
        """Fechou um cadáver: o que saiu entra no livro (como num baú). O
        primeiro item tirado deixa-o só mais 1 min."""
        from engine.corpses import REACH_BLOCKS, count_items
        cid = str(msg.get("id") or "")
        c = self.corpses.get(cid)
        led = ledger(self.actor)
        if c is None or led is None:
            if self.actor:
                self.to(self.actor, {"t": "corpse_fix", "id": cid, "entries": None})
            return
        at = msg.get("at")
        try:
            if at is not None:
                d = sum((float(a) - float(b)) ** 2 for a, b in zip(at, c["pos"])) ** 0.5
                if d > REACH_BLOCKS * 2.0 + 2.0:
                    self.to(self.actor, {"t": "corpse_fix", "id": cid, "entries": c["slots"]})
                    return
        except (TypeError, ValueError):
            pass
        old = c["slots"]
        new, changed = eco.transfer(led, old, msg.get("entries") or [], len(old))
        taken = count_items(new) < count_items(old)
        self.corpses.set_slots(cid, new, taken)
        if changed:
            self.to(self.actor, {"t": "corpse_fix", "id": cid, "entries": new})

    def _bank(self, cell, delta):
        """Guardar (+) ou tirar (-) almas do minoculus do mundo."""
        owner = self.cr.owner_at(cell)
        led = ledger(self.actor)
        tipo = self.cr.block_map.get(owner) if owner is not None else None
        if led is None or not self._props(tipo).get("portal"):
            return
        try:
            delta = int(delta)
        except (TypeError, ValueError):
            return
        have = self.room.soul_bank(owner)
        if delta > 0:
            got = led.take_souls(delta)
            self.room.set_soul_bank(owner, have + got)
        elif delta < 0:
            got = min(have, -delta)
            self.room.set_soul_bank(owner, have - got)
            led.add_souls(got)

    def _apply_fields(self, fields):
        """Campos mudados por uma ferramenta do servidor (o jogo já não manda
        baús nem almas: vão por "chest"/"bank", conferidos com o livro)."""
        keep = {k: v for k, v in fields.items() if k in ("containers", "soul_banks")}
        self.room.apply_remote_fields(keep)


    # --- tempo ---------------------------------------------------------------------
    def tick(self, dt):
        self.physics.update(dt)
        self.fire.update(dt)
        self.corpses.update(dt)
        self.grass.update()
        self.tufts.update()
        if self.glow is not None:
            self.glow.update()
        if self.suns is not None:
            self.suns.update()
        if self.apples is not None:
            self.apples.update()

    def flush(self):
        if not (self.out_blocks or self.out_fields or self.out_anims or self.out_fx):
            return
        # Só o estado final de cada célula (pôr e tirar no mesmo tick = tirar).
        last = {}
        for k, v in self.out_blocks:
            last.pop(k, None)
            last[k] = v
        emit({"ev": "changes", "room": self.id, "blocks": [[k, v] for k, v in last.items()],
              "fields": self.out_fields, "anims": self.out_anims, "fx": self.out_fx})
        self.out_blocks, self.out_fields, self.out_anims, self.out_fx = [], {}, [], []


# ---------------------------------------------------------------- economia (sem sala)
def eco_handle(msg):
    op = msg.get("op")
    pid = str(msg.get("player") or "")
    req = msg.get("req")
    if op == "eco_config":
        # balanceamento mudado no painel dos moderadores (vale mais que o ficheiro)
        import game_config
        import item_balancing
        game_config._BUNDLE = {str(k): v for k, v in (msg.get("files") or {}).items()}
        game_config._VERSION = "painel" if game_config._BUNDLE else None
        try:
            item_balancing.load_balancing()
        except Exception as exc:
            log(f"balanceamento do painel: {exc}")
        for led in LEDGERS.values():
            led.dirty = True          # perfis de combate com os valores novos
        return
    if op == "eco_load":
        led = LEDGERS.get(pid)
        if led is None:
            if isinstance(msg.get("ledger"), dict):
                led = eco.Ledger.load(msg["ledger"])
                led.layout = msg.get("inventory")
                led.almas = msg.get("almas") or {"total": 0, "pontos": []}
            else:
                # conta de antes do livro: confia no que estava gravado
                led = eco.ledger_from_inventory(msg.get("inventory"), msg.get("almas"))
            LEDGERS[pid] = led
        for name, n in msg.get("grants") or []:
            name = eco.item_name(name)
            if name:
                eco.give(led, name, int(n))
        led.add_souls(int(msg.get("souls") or 0))
        inv = eco.reconcile(led)
        led.dirty = True
        if req is not None:
            emit({"ev": "reply", "req": req, "inventory": inv, "almas": led.almas})
        return
    led = LEDGERS.get(pid)
    if led is None:
        if req is not None:
            emit({"ev": "reply", "req": req, "erro": "sem livro"})
        return
    if op == "eco_check":
        inv, almas, changed = eco.check_inventory(led, msg.get("inv"), msg.get("almas"))
        if changed:
            log(f"{pid}: inventário corrigido (tinha o que não ganhou)")
            eco_to(pid, {"t": "inv_fix", "inv": inv, "almas": almas})
    elif op == "eco_credit":
        for name, n in msg.get("items") or []:
            name = eco.item_name(name)
            if name:
                led.add(name, int(n))
        led.add_souls(int(msg.get("souls") or 0))
    elif op == "eco_icont":
        cid = str(msg.get("cid") or "")
        if not led.has_cid(cid):
            emit({"ev": "reply", "req": req, "erro": "este baú não é seu"})
            return
        new, changed = eco.transfer(led, msg.get("old") or [], msg.get("new") or [],
                                    int(msg.get("size") or 24), self_cid=cid)
        emit({"ev": "reply", "req": req, "entries": new, "changed": changed})
    elif op == "eco_transmute":
        _transmute(pid, led, msg)
    elif op == "eco_buy":
        _buy(pid, led, msg)
    elif op == "eco_test":
        kind = msg.get("kind")
        if kind == "almas+":
            led.add_souls(10)
        elif kind == "almas-":
            led.take_souls(10)
        elif kind == "balde_agua":
            led.add("balde_agua", 1)


def shop_price(item):
    """Preço (almas) de ``item`` na loja de um portal (blocks.json "portal.loja"), ou None."""
    from blocks import all_block_types
    for bt in all_block_types().values():
        portal = ((bt.raw.get("block_properties") or {}).get("portal") if bt is not None else None)
        loja = portal.get("loja") if isinstance(portal, dict) else None
        if isinstance(loja, dict) and item in loja and not str(item).startswith("_"):
            try:
                return max(0, int(loja[item]))
            except (TypeError, ValueError):
                return None
    return None


def _buy(pid, led, msg):
    """Compra no minoculus: cobra as almas e dá o item (preço do servidor)."""
    item = str(msg.get("item") or "")
    price = shop_price(item)
    name = eco.item_name(item) if item else None
    if price is None or not name:
        return log(f"{pid}: compra desconhecida {item!r}")
    if led.souls < price:
        return log(f"{pid}: compra sem almas ({item})")
    led.take_souls(price)
    led.add(name, 1)
    log(f"{pid}: comprou {item} por {price} almas")


def _transmute(pid, led, msg):
    """Transmutação com as receitas do servidor: gasta materiais, almas e
    desgaste da pedra; dá o item."""
    import transmutation
    recipes = transmutation.load_recipes()
    try:
        idx = int(msg.get("idx", -1))
    except (TypeError, ValueError):
        idx = -1
    recipe = recipes[idx] if 0 <= idx < len(recipes) else None
    if recipe is None or recipe.item != msg.get("item"):
        recipe = next((r for r in recipes if r.item == msg.get("item")), None)
    if recipe is None:
        return log(f"{pid}: transmutação desconhecida {msg.get('item')!r}")
    stone_fam = None
    for fam, units in led.units.items():
        t = eco.get_item_type(fam)
        if units and t is not None and t.raw.get("transmutacao"):
            stone_fam = fam
            break
    if stone_fam is None:
        return log(f"{pid}: transmutação sem pedra")
    for mid, n in recipe.materials.items():
        name = eco.item_name(mid)
        if not name or led.count(eco.family(name)) < n:
            return log(f"{pid}: transmutação sem materiais ({mid})")
    if led.souls < recipe.souls:
        return log(f"{pid}: transmutação sem almas")
    for mid, n in recipe.materials.items():
        led.take(eco.item_name(mid), n)
    led.take_souls(recipe.souls)
    try:
        stone_d = float(msg.get("stone_d"))
    except (TypeError, ValueError):
        stone_d = None
    stone = led.take_unit(stone_fam, {"d": stone_d, "c": None})
    if stone is not None and stone.get("d") is not None and recipe.wear > 0:
        stone["d"] = max(0.0, float(stone["d"]) - recipe.wear)
    if stone is not None and (stone.get("d") is None or stone["d"] > 0):
        led.units.setdefault(stone_fam, []).append(stone)
    led.add(recipe.item, recipe.amount)


def main():
    inbox = queue.Queue()

    def reader():
        for line in sys.stdin:
            line = line.strip()
            if line:
                inbox.put(line)
        inbox.put(None)

    threading.Thread(target=reader, daemon=True).start()
    sims = {}
    last = time.monotonic()
    emit({"ev": "ready"})
    while True:
        deadline = time.monotonic() + TICK
        while True:
            timeout = max(0.0, deadline - time.monotonic())
            try:
                line = inbox.get(timeout=timeout)
            except queue.Empty:
                break
            if line is None:
                return
            try:
                msg = json.loads(line)
                rid = str(msg.get("room"))
                op = msg.get("op")
                if str(op).startswith("eco_"):
                    eco_handle(msg)
                elif op == "open":
                    if rid not in sims:
                        sims[rid] = RoomSim(rid, msg.get("data") or {})
                elif op == "close":
                    sim = sims.pop(rid, None)
                    if sim is not None:
                        sim.flush()
                        BACKEND.sims.pop(rid, None)
                        log(f"{rid}: fechada")
                elif rid in sims:
                    sims[rid].handle(msg)
                    sims[rid].flush()
                elif op in ("place", "remove", "chest", "bank"):
                    # sala fechada: devolve o estado ao jogador
                    if msg.get("by") and msg.get("cell"):
                        emit({"ev": "eco_to", "player": msg["by"],
                              "msg": {"t": "reject", "cell": msg["cell"]}})
                if msg.get("req") is not None and not str(op).startswith("eco_"):
                    emit({"ev": "reply", "req": msg["req"]})
                flush_ledgers()
            except Exception as exc:
                log(f"pedido falhou: {exc}\n{traceback.format_exc()}")
        now = time.monotonic()
        dt, last = now - last, now
        for sim in list(sims.values()):
            try:
                sim.tick(min(dt, 0.25))
                sim.flush()
            except Exception as exc:
                log(f"{sim.id}: {exc}\n{traceback.format_exc()}")
        flush_ledgers()


if __name__ == "__main__":
    main()
