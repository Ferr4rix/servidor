"""Maçãs das árvores.

* Enquanto uma árvore existe como estrutura (troncos + folhas ligados), nasce
  uma maçã por baixo de uma das folhas e **cai logo no chão**. Cada árvore tem
  o seu tempo, sorteado de cada vez entre ``GROW_MIN`` e ``GROW_MAX`` (em
  média ~1 min), por isso não nascem todas juntas.
* Só nasce onde há **muita luz**: o sítio por baixo da folha tem de ter nível
  ``MIN_LIGHT`` ou mais (a pedra solar dá 30 e perde 1 por casa: tem de
  estar a ~10 casas, sem paredes no meio). No escuro a árvore não dá maçãs.
* A árvore pára de dar maçãs quando já há ``MAX_ON_GROUND`` maçãs no chão à
  volta dela (raio ``AROUND``): assim o mundo não se enche de maçãs.
* Quebrar uma folha tem 20% de chance de soltar uma maçã
  (``"ao_quebrar"`` em blocks.json, tratado na física dos blocos).

Online isto corre no servidor (server/physics/worker.py); no modo local, no
próprio jogo.
"""

from __future__ import annotations

import random
import time

APPLE = "maca"                 # maçã no chão
HANGING = "maca_pendurada"     # (antigo) maçã presa à folha: agora cai logo
LEAVES = "leaves"
WOOD = "wood"
GROW_MIN = 35.0                # s: tempo até a próxima maçã de uma árvore,
GROW_MAX = 95.0                # sorteado entre estes dois (média ~1 min)
GROW_EVERY = (GROW_MIN + GROW_MAX) / 2
CHECK_EVERY = 2.0              # s entre verificações das árvores
TREES_EVERY = 20.0             # s entre releituras das árvores (caro em mundos grandes)
MIN_LIGHT = 20                 # luz (0-30) que o sítio da maçã precisa
MAX_ON_GROUND = 4              # maçãs no chão à volta de uma árvore, no máximo
AROUND = 4                     # raio (células) "à volta da árvore"


class AppleTrees:
    def __init__(self, chunk_room, physics, is_host):
        self.cr = chunk_room
        self.physics = physics
        self.is_host = is_host           # f() -> True se este jogo decide
        self._next_check = time.monotonic() + CHECK_EVERY
        self._tree_next = {}             # árvore (tronco mais baixo) -> hora da próxima maçã
        self._tree_cache = []
        self._trees_at = 0.0
        self._converted = False
        self._rng = random.Random()

    def _delay(self):
        return self._rng.uniform(GROW_MIN, GROW_MAX)

    # ------------------------------------------------------------ árvores
    def _trees(self):
        """(folhas, troncos) de cada estrutura que tem tronco e folhas."""
        seen = set()
        for cell, tipo in list(self.cr.block_map.items()):
            if tipo != WOOD or cell in seen:
                continue
            group = self.physics.structure(cell)
            seen |= group
            leaves = [c for c in group if self.cr.block_map.get(c) == LEAVES]
            if leaves:
                wood = [c for c in group if self.cr.block_map.get(c) == WOOD]
                yield leaves, wood

    def _spots(self, leaves):
        out = []
        for (x, y, z) in leaves:
            below = (x, y - 1, z)
            if (self.cr.block_map.get((x, y, z)) == LEAVES and self.cr.in_bounds(below)
                    and self.cr.owner_at(below) is None):
                out.append(below)
        return out

    def _apples_around(self, wood, apples):
        xs = [c[0] for c in wood]
        zs = [c[2] for c in wood]
        x0, x1, z0, z1 = min(xs) - AROUND, max(xs) + AROUND, min(zs) - AROUND, max(zs) + AROUND
        return sum(1 for (x, _y, z) in apples if x0 <= x <= x1 and z0 <= z <= z1)

    def drop(self, cell):
        """Põe uma maçã em ``cell`` e deixa-a cair."""
        self.cr.set_block(cell, APPLE, orientation=None)
        self.physics.settle([cell])

    # ------------------------------------------------------------ laço
    def update(self):
        if not self._converted:
            self._converted = True
            if self.is_host():
                # maçãs penduradas de versões antigas: caem agora
                for cell, tipo in list(self.cr.block_map.items()):
                    if tipo == HANGING:
                        self.cr.remove_block(cell)
                        self.drop(cell)
        now = time.monotonic()
        if now < self._next_check:
            return
        self._next_check = now + CHECK_EVERY
        if not self.is_host():
            return
        from engine.lighting import light_at, light_sources
        apples = None
        sources = None
        if now >= self._trees_at:
            self._trees_at = now + TREES_EVERY
            self._tree_cache = list(self._trees())
        alive = set()
        for leaves, wood in self._tree_cache:
            key = min(wood)
            alive.add(key)
            due = self._tree_next.get(key)
            if due is None:
                # árvore nova (ou acabou de mexer): começa num ponto sorteado
                self._tree_next[key] = now + self._rng.uniform(0.3, 1.0) * GROW_MAX
                continue
            if now < due:
                continue
            self._tree_next[key] = now + self._delay()
            if apples is None:
                apples = [c for c, t in self.cr.block_map.items() if t == APPLE]
            if self._apples_around(wood, apples) >= MAX_ON_GROUND:
                continue
            if sources is None:
                sources = light_sources(self.cr)
            if not sources:
                continue                              # nada ilumina: nenhuma árvore dá
            spots = [c for c in self._spots(leaves)
                     if light_at(self.cr, c, sources, MIN_LIGHT) >= MIN_LIGHT]
            if spots:
                cell = self._rng.choice(spots)
                self.drop(cell)
                apples.append(cell)
        for key in [k for k in self._tree_next if k not in alive]:
            del self._tree_next[key]


__all__ = ["AppleTrees", "GROW_EVERY", "GROW_MIN", "GROW_MAX", "MIN_LIGHT", "MAX_ON_GROUND"]
