"""Fogo: combustão dos blocos (a mesma lógica no servidor e no jogo sozinho).

O fogo não é um bloco à parte: é um **estado dentro de um bloco** (como a
cola), que só existe em blocos com ``"combustivel"`` nas propriedades::

    "combustivel": {"segundos": 60, "pega": 0.05, "altura": 3}

* ``segundos``: quanto tempo o bloco arde até ser consumido (some, sem dar
  item; um baú perde o que tinha dentro).
* ``pega``: chance por segundo (0-1) de pegar fogo quando há chamas ao lado
  (folhas pegam depressa, troncos devagar).
* ``altura``: altura máxima das chamas em blocos (no máximo 3; sem ela,
  ``1 + segundos/30``).

Ciclo de um fogo: as chamas **crescem** (de ~0,6 bloco até à altura máxima
no primeiro quarto do tempo), mantêm-se e depois **diminuem** à medida que o
bloco é consumido, até ele sumir. As chamas sobem pelas células de cima (até
3 blocos de altura) e passam o fogo aos blocos inflamáveis à volta do bloco
e das chamas. **Água** ao lado do bloco, em cima dele ou nas chamas apaga-o
(o bloco fica, com o que ainda não ardeu).

**Fogo no ar**: o fogo também arde **em cima de qualquer bloco** (pedra,
terra...), numa célula de ar apoiada num bloco: dura pouco (``AIR_MIN`` a
``AIR_MAX`` s), não consome nada e pode acender blocos que ardem ao lado.
Os blocos a arder lançam fogo no ar à volta (o fogo "anda" pelo chão); o
isqueiro num bloco que não arde acende o ar em cima dele. Fogo no ar não
cria mais fogo no ar (não se espalha sem fim por um campo de pedra).

**Dano**: enquanto arde, o bloco vai levando dano (``damage_block``, no
mapa de dano da física, como os golpes): a meio do fogo já ~75% do que o
parte. Um bloco a arder fica cada vez mais fácil de partir.

**Mover**: um bloco a arder que cai, tomba ou é empurrado leva o fogo com
ele (``moved``). No ar ele não está no mundo: o fogo espera (não arde, não
some) até ele assentar no destino, até ``FLIGHT_WAIT`` s.

Estado: ``fires`` = {célula do bloco: [segundos a arder, duração]}; vai para
o campo ``fogo`` da sala ({"x,y,z": [t, dur]}), gravado e mandado a todos.
Quem simula (o worker do servidor, ou o jogo sem servidor) chama ``update``;
os outros só ``apply_state`` + ``advance`` (para as chamas andarem entre
dois estados do servidor).
"""

from __future__ import annotations

import random
from typing import Callable, Dict, List, Optional, Tuple

Cell = Tuple[int, int, int]

MAX_HEIGHT = 3.0          # chamas de no máximo 3 blocos
SPREAD_EVERY = 1.0        # s entre tentativas de espalhar
WATER_EVERY = 0.25        # s entre verificações de água
SYNC_EVERY = 3.0          # s entre estados mandados (fora mudanças de fogos)
PUSH_GAP = 0.5            # mudanças juntam-se: no máximo um estado a cada 0,5 s
FLIGHT_WAIT = 12.0        # s que um fogo espera pelo bloco a voar/cair (física)
BURN_DAMAGE = 1.5         # parte do "quebrar" que o fogo tira por duração (limite 98%)
SPREAD_BOOST = 2.0        # x a "pega" do blocks.json (o fogo espalha mais)
AIR = "__ar__"            # tipo de um fogo no ar
AIR_MIN, AIR_MAX = 2.5, 6.0   # s que dura um fogo no ar
AIR_HEIGHT = 0.9          # altura máxima das chamas de um fogo no ar
AIR_SPAWN = 0.35          # chance/s de um bloco a arder acender o ar ao lado (x força)
AIR_MAX_COUNT = 160       # fogos no ar ao mesmo tempo, no máximo
_SIDES = ((1, 0, 0), (-1, 0, 0), (0, 0, 1), (0, 0, -1))
_SIX = _SIDES + ((0, 1, 0), (0, -1, 0))
# vizinhos em diagonal (arestas): o fogo também salta para eles, com metade da chance
_EDGES = tuple((dx, dy, dz) for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1)
               if abs(dx) + abs(dy) + abs(dz) == 2)
_RING = ((1, 0, 0), (-1, 0, 0), (0, 0, 1), (0, 0, -1), (1, 0, 1), (1, 0, -1), (-1, 0, 1), (-1, 0, -1))


def _key(c: Cell) -> str:
    return f"{c[0]},{c[1]},{c[2]}"


def _unkey(text) -> Cell:
    return tuple(int(v) for v in str(text).split(","))


class FireSystem:
    def __init__(self, chunk_room, type_of: Optional[Callable] = None,
                 water_level: Optional[Callable[[Cell], int]] = None,
                 remove_block: Optional[Callable[[Cell], None]] = None,
                 save: Optional[Callable[[dict], None]] = None, seed: int = 0,
                 damage_block: Optional[Callable[[Cell, float], None]] = None):
        self.cr = chunk_room
        self.type_of = type_of
        self.water_level = water_level          # célula -> nível da água (0 = seca)
        self.remove_block = remove_block        # quem simula: consumir um bloco
        self.save = save                        # quem simula: gravar/mandar o estado
        self.damage_block = damage_block        # quem simula: (bloco, parte do "quebrar") a somar
        self._away: Dict[Cell, float] = {}      # fogo -> s à espera do bloco (a voar)
        self.fires: Dict[Cell, List[float]] = {}
        self._tipo: Dict[Cell, str] = {}
        self._cfg_cache: Dict[str, Optional[dict]] = {}
        self._rng = random.Random(seed)
        self._spread_t = 0.0
        self._water_t = 0.0
        self._sync_t = 0.0
        self._pending = False                   # mudou e ainda não foi mandado
        self._push_t = PUSH_GAP
        self.version = 0

    # ------------------------------------------------------------ configuração
    def config(self, tipo) -> Optional[dict]:
        """{"segundos", "pega", "altura"} se o bloco arde, senão None."""
        if not tipo:
            return None
        if tipo not in self._cfg_cache:
            got = None
            try:
                type_of = self.type_of
                if type_of is None:
                    from blocks import get_block_type as type_of        # noqa: N813
                bt = type_of(tipo)
                props = (getattr(bt, "raw", None) or {}).get("block_properties") or {}
                c = props.get("combustivel")
                if isinstance(c, dict):
                    secs = max(1.0, float(c.get("segundos", 30)))
                    got = {"segundos": secs,
                           "pega": max(0.0, min(1.0, float(c.get("pega", 0.05)))),
                           "altura": max(0.5, min(MAX_HEIGHT, float(c.get("altura", 1 + secs / 30))))}
            except Exception:
                got = None
            self._cfg_cache[tipo] = got
        return self._cfg_cache[tipo]

    def flammable(self, cell) -> bool:
        owner = self.cr.owner_at(tuple(cell))
        return owner is not None and self.config(self.cr.block_map.get(owner)) is not None

    def burning(self, cell) -> bool:
        cell = tuple(cell)
        if cell in self.fires:
            return True
        owner = self.cr.owner_at(cell)
        return owner is not None and owner in self.fires

    def is_air(self, cell) -> bool:
        return self._tipo.get(tuple(cell)) == AIR

    def supported(self, cell) -> bool:
        """Célula de ar com um bloco por baixo (onde o fogo no ar pode arder)."""
        x, y, z = cell
        return self.cr.owner_at((x, y, z)) is None and self.cr.owner_at((x, y - 1, z)) is not None

    # ------------------------------------------------------------ altura das chamas
    def height(self, owner) -> float:
        """Altura das chamas (blocos): crescem, mantêm-se e diminuem com o bloco."""
        f = self.fires.get(owner)
        if f is None:
            return 0.0
        t, dur = f
        if self._tipo.get(owner) == AIR:
            p = max(0.0, min(1.0, t / max(0.01, dur)))
            if p < 0.15:
                return 0.3 + (AIR_HEIGHT - 0.3) * p / 0.15
            if p < 0.55:
                return AIR_HEIGHT
            return AIR_HEIGHT - (AIR_HEIGHT - 0.2) * (p - 0.55) / 0.45
        cfg = self.config(self._tipo.get(owner) or self.cr.block_map.get(owner)) or {}
        top = float(cfg.get("altura", 1.0))
        p = max(0.0, min(1.0, t / max(0.01, dur)))
        if p < 0.25:
            return 0.6 + (top - 0.6) * p / 0.25
        if p < 0.6:
            return top
        return top - (top - 0.4) * (p - 0.6) / 0.4

    def flame_cells(self, owner) -> List[Cell]:
        """O bloco e as células de cima por onde as chamas sobem."""
        h = self.height(owner)
        n = max(1, int(-(-h // 1)))               # ceil
        return [(owner[0], owner[1] + k, owner[2]) for k in range(n)]

    # ------------------------------------------------------------ acender / apagar
    def light(self, cell) -> bool:
        """Isqueiro: acende o bloco (se arder) ou o ar em cima de um bloco."""
        cell = tuple(cell)
        if self.flammable(cell):
            return self.ignite(cell)
        return self.ignite_air(cell)

    def ignite_air(self, cell) -> bool:
        """Fogo curto numa célula de ar apoiada num bloco."""
        cell = tuple(cell)
        if cell in self.fires or not self.supported(cell) or self._wet(cell, 1):
            return False
        if sum(1 for t in self._tipo.values() if t == AIR) >= AIR_MAX_COUNT:
            return False
        self.fires[cell] = [0.0, round(self._rng.uniform(AIR_MIN, AIR_MAX), 2)]
        self._tipo[cell] = AIR
        self._changed()
        return True

    def ignite(self, cell) -> bool:
        """Acende o bloco de ``cell`` (isqueiro, ou chamas ao lado)."""
        owner = self.cr.owner_at(tuple(cell))
        if owner is None or owner in self.fires:
            return False
        tipo = self.cr.block_map.get(owner)
        cfg = self.config(tipo)
        if cfg is None or self._wet(owner, 1):
            return False
        self.fires[owner] = [0.0, float(cfg["segundos"])]
        self._tipo[owner] = tipo
        self._changed()
        return True

    def extinguish(self, owner) -> bool:
        if self.fires.pop(owner, None) is None:
            return False
        self._tipo.pop(owner, None)
        self._changed()
        return True

    def moved(self, src, dest) -> None:
        """O bloco a arder caiu/mudou de sítio: o fogo vai com ele. (A física
        avisa antes de o bloco chegar: até lá o fogo espera, ver update.)"""
        src, dest = tuple(src), tuple(dest)
        f = self.fires.pop(src, None)
        if f is not None:
            self._tipo[dest] = self._tipo.pop(src, None)
            self._away.pop(src, None)
            self.fires[dest] = f
            self._changed()

    def _wet(self, owner, flames) -> bool:
        wl = self.water_level
        if wl is None:
            return False
        x, y, z = owner
        cells = [owner] + [(x + dx, y + dy, z + dz) for dx, dy, dz in _SIX]
        cells += [(x, y + k, z) for k in range(1, int(flames))]
        return any(wl(c) > 0 for c in cells)

    # ------------------------------------------------------------ simulação
    def update(self, dt: float) -> None:
        """Quem simula: o tempo passa, a água apaga, o fogo espalha-se e
        consome os blocos."""
        self._push_t += dt
        if not self.fires:
            if self._pending and self._push_t >= PUSH_GAP:
                self._push()
            return
        gone = []
        for owner, f in list(self.fires.items()):
            if self._tipo.get(owner) == AIR:
                # puseram um bloco ali ou tiraram o de baixo: apaga
                if not self.supported(owner):
                    gone.append(owner)
                    continue
                f[0] += dt
                if f[0] >= f[1]:
                    gone.append(owner)
                continue
            tipo = self.cr.block_map.get(owner)
            if tipo is None:
                # a voar (a física já disse para onde vai) ou tirado: espera
                waited = self._away.get(owner, 0.0) + dt
                self._away[owner] = waited
                if waited > FLIGHT_WAIT:
                    gone.append(owner)
                continue
            self._away.pop(owner, None)
            if tipo != self._tipo.get(owner):
                gone.append(owner)                # outro bloco no lugar
                continue
            f[0] += dt
            if self.damage_block is not None:
                try:
                    self.damage_block(owner, BURN_DAMAGE * dt / max(0.01, f[1]))
                except Exception as exc:
                    print(f"[fogo] dano: {exc}")
            if f[0] >= f[1]:
                gone.append(owner)
                if self.remove_block is not None:
                    self.remove_block(owner)        # consumido
        for owner in gone:
            self.fires.pop(owner, None)
            self._tipo.pop(owner, None)
            self._away.pop(owner, None)
        if gone:
            self._changed()
        self._water_t += dt
        if self._water_t >= WATER_EVERY:
            self._water_t = 0.0
            for owner in list(self.fires):
                if (owner in self.cr.block_map or self._tipo.get(owner) == AIR) \
                        and self._wet(owner, len(self.flame_cells(owner))):
                    self.extinguish(owner)
        self._spread_t += dt
        if self._spread_t >= SPREAD_EVERY:
            step, self._spread_t = self._spread_t, 0.0
            self._spread(step)
        self._sync_t += dt
        if self._sync_t >= SYNC_EVERY or (self._pending and self._push_t >= PUSH_GAP):
            self._push()

    def _spread(self, step: float) -> None:
        new, air = [], []
        n_air = sum(1 for t in self._tipo.values() if t == AIR)
        for owner in list(self.fires):
            is_air = self._tipo.get(owner) == AIR
            if not is_air and owner not in self.cr.block_map:
                continue                          # a voar: não espalha
            if is_air:
                strength = 1.0
            else:
                top = self.config(self._tipo.get(owner)) or {}
                strength = self.height(owner) / max(0.5, float(top.get("altura", 1.0)))
            x, y, z = owner
            near = {}                             # célula -> peso da chance
            for dx, dy, dz in _SIX:
                near[(x + dx, y + dy, z + dz)] = 1.0
            if not is_air:
                for dx, dy, dz in _EDGES:
                    near.setdefault((x + dx, y + dy, z + dz), 0.5)
                for c in self.flame_cells(owner)[1:]:       # as chamas que sobem
                    near[c] = 1.0
                    for dx, _dy, dz in _SIDES:
                        near.setdefault((c[0] + dx, c[1], c[2] + dz), 1.0)
                    near.setdefault((c[0], c[1] + 1, c[2]), 1.0)
            for c, w in near.items():
                o = self.cr.owner_at(c)
                if o is None or o in self.fires or o in new:
                    continue
                cfg = self.config(self.cr.block_map.get(o))
                if cfg is None:
                    continue
                if self._rng.random() < cfg["pega"] * SPREAD_BOOST * step * strength * w:
                    new.append(o)
            if is_air or n_air + len(air) >= AIR_MAX_COUNT:
                continue
            # fogo no ar à volta (no chão ao lado, em cima e um degrau abaixo)
            column = set(self.flame_cells(owner))
            for dy in (-1, 0, 1):
                for dx, _z, dz in _RING:
                    c = (x + dx, y + dy, z + dz)
                    if c in self.fires or c in air or c in column or not self.supported(c):
                        continue
                    if self._rng.random() < AIR_SPAWN * step * strength / (1 + abs(dy)):
                        air.append(c)
        for o in new:
            self.ignite(o)
        for c in air:
            self.ignite_air(c)

    # ------------------------------------------------------------ estado
    def state(self) -> dict:
        out = {}
        for c, f in self.fires.items():
            v = [round(f[0], 2), round(f[1], 2)]
            if self._tipo.get(c) == AIR:
                v.append(1)                       # fogo no ar
            out[_key(c)] = v
        return out

    def apply_state(self, data) -> None:
        """Estado vindo do servidor (ou gravado): troca o que havia."""
        fires = {}
        tipos = {}
        for k, v in (data or {}).items():
            try:
                c = _unkey(k)
                fires[c] = [float(v[0]), float(v[1])]
                is_air = len(v) > 2 and bool(v[2])
            except (TypeError, ValueError, IndexError):
                continue
            tipos[c] = AIR if is_air else self.cr.block_map.get(c)
        self.fires, self._tipo = fires, tipos
        self.version += 1

    def advance(self, dt: float) -> None:
        """Quem não simula: as chamas andam até ao próximo estado do servidor."""
        for f in self.fires.values():
            f[0] = min(f[0] + dt, f[1])

    def _changed(self) -> None:
        self.version += 1
        self._pending = True                    # vai no próximo update (juntando mudanças)

    def _push(self) -> None:
        self._pending = False
        self._sync_t = 0.0
        self._push_t = 0.0
        if self.save is not None:
            try:
                self.save(self.state())
            except Exception as exc:
                print(f"[fogo] não gravei: {exc}")


__all__ = ["FireSystem", "MAX_HEIGHT", "AIR"]
