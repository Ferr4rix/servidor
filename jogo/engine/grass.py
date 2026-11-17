"""Grama que cresce sozinha sobre a terra.

A grama não é um bloco "de verdade" para o jogador: é a terra com grama por
cima. Pegá-la com ``Q`` dá **terra** (``"drop": "terra"`` em ``blocks.json``).

* Terra **exposta** (nada na célula de cima) durante ``GROW_SECONDS``
  (±30%, para não crescer tudo de uma vez) vira ``grama``.
* Um cubo opaco posto em cima da grama tapa-a: ela volta a ser terra.

Funciona com os ganchos de ``ChunkRoom.set_block`` / ``remove_block``, por isso
apanha tudo: blocos colocados, quebrados, empurrados pela física e os que
chegam de outros jogadores. A troca grava na sala como qualquer bloco (e vai
para o servidor); vários clientes a fazê-la ao mesmo tempo dão o mesmo
resultado.
"""

from __future__ import annotations

import random
import time

DIRT = "terra"
GRASS = "grama"
GROW_SECONDS = 60.0         # tempo exposta até nascer grama
CHECK_EVERY = 1.0           # s entre verificações


class GrassGrowth:
    def __init__(self, chunk_room):
        self.cr = chunk_room
        self.pending = {}           # célula da terra -> instante em que vira grama
        self._busy = False          # a própria troca não conta como evento
        self._next_check = 0.0
        self._rng = random.Random()
        self._hook()
        self._scan_all()

    # ------------------------------------------------------------ ganchos
    def _hook(self):
        cr = self.cr
        orig_set, orig_remove = cr.set_block, cr.remove_block

        def set_block(bpos, tipo, orientation=None):
            out = orig_set(bpos, tipo, orientation=orientation)
            if not self._busy:
                self._placed(tuple(bpos), tipo)
            return out

        def remove_block(bpos):
            bpos = tuple(bpos)
            tipo = cr.block_map.get(bpos)
            cells = cr.cells_for_block(bpos, tipo) if tipo else [bpos]
            out = orig_remove(bpos)
            if out and not self._busy:
                for cell in cells:
                    self._uncovered(cell)
            return out

        cr.set_block = set_block
        cr.remove_block = remove_block

    def _scan_all(self):
        """Terra já exposta ao entrar na sala começa a contar agora."""
        cr = self.cr
        for cell, tipo in list(cr.block_map.items()):
            if tipo == DIRT and self._exposed(cell):
                self._schedule(cell)

    # ------------------------------------------------------------ regras
    def _exposed(self, cell):
        x, y, z = cell
        return self.cr.owner_at((x, y + 1, z)) is None

    def _schedule(self, cell):
        if cell not in self.pending:
            self.pending[cell] = time.monotonic() + GROW_SECONDS * self._rng.uniform(0.7, 1.3)

    def _placed(self, cell, tipo):
        x, y, z = cell
        if tipo == DIRT and self._exposed(cell):
            self._schedule(cell)
        below = (x, y - 1, z)
        self.pending.pop(below, None)
        if self.cr.block_map.get(below) == GRASS and self.cr._is_opaque_cube(tipo):
            self._swap(below, DIRT)             # tapada: volta a ser terra

    def _uncovered(self, cell):
        x, y, z = cell
        below = (x, y - 1, z)
        if self.cr.block_map.get(below) == DIRT and self._exposed(below):
            self._schedule(below)

    def _swap(self, cell, tipo):
        self._busy = True
        try:
            self.cr.set_block(cell, tipo, orientation=None)
        finally:
            self._busy = False

    # ------------------------------------------------------------ laço
    def update(self):
        now = time.monotonic()
        if now < self._next_check or not self.pending:
            return
        self._next_check = now + CHECK_EVERY
        for cell, when in list(self.pending.items()):
            if when > now:
                continue
            self.pending.pop(cell, None)
            if self.cr.block_map.get(cell) == DIRT and self._exposed(cell):
                self._swap(cell, GRASS)


__all__ = ["GrassGrowth", "GROW_SECONDS"]
