"""Capim (bloco "capim"): nasce sozinho em cima da grama.

Quem simula (o worker do servidor, ou o jogo sozinho) chama ``update``: a
cada ``CHECK_EVERY`` s tenta ``tries`` sítios ao acaso na sala; num sítio
cujo bloco mais alto é **grama** com a célula de cima **vazia e seca**, nasce
um capim (com chance ``GROW_CHANCE``). Há um máximo por sala (1 capim por
``AREA_PER_TUFT`` células de chão), por isso não cobre tudo; quando os mobs o
comem (ou o jogador o parte), volta a crescer aos poucos.

O capim não é sólido (anda-se por ele), parte-se com qualquer golpe e não dá
item. Os mobs da sala pública comem-no (server/src/mobs/mobServer.js).
"""

from __future__ import annotations

import random
import sys
import time

TUFT = "capim"
GRASS = "grama"
CHECK_EVERY = 3.0
AREA_PER_TUFT = 50          # 200x200 -> até 800 capins (cada um é um modelo: pesa)
TRIES_PER_AREA = 1000       # tentativas por verificação: área / isto (mín. 2)
GROW_CHANCE = 0.5


class TuftGrowth:
    """Planta que nasce sozinha na grama. Por omissão é o capim; a flor lunar
    (luz noturna da sala pública) usa a mesma lógica com outros números.
    ``burst``: na 1a verificação, se faltar muito, tenta ``burst`` vezes mais
    (enche a sala logo ao abrir, em vez de levar horas)."""

    def __init__(self, chunk_room, water_level=None, seed=None, block=None,
                 area_per=None, chance=None, burst=1):
        self.cr = chunk_room
        self.block = block or TUFT
        self.chance = GROW_CHANCE if chance is None else chance
        self.burst = max(1, int(burst))
        self.water_level = water_level
        self.rng = random.Random(seed)
        room = getattr(chunk_room, "room", chunk_room)
        sx = int(getattr(room, "size_x", 0) or 0)
        sz = int(getattr(room, "size_z", 0) or 0)
        self.size = (sx, int(getattr(room, "size_y", 20) or 20), sz)
        self.max = max(1, (sx * sz) // (area_per or AREA_PER_TUFT)) if sx and sz else 0
        self.tries = max(2, (sx * sz) // TRIES_PER_AREA)
        self._next = time.monotonic() + CHECK_EVERY

    def count(self):
        return sum(1 for t in self.cr.block_map.values() if t == self.block)

    def _top(self, x, z):
        """Célula do bloco mais alto em (x, z), ou None."""
        for y in range(self.size[1] - 1, -1, -1):
            if self.cr.owner_at((x, y, z)) is not None:
                return (x, y, z)
        return None

    def update(self):
        now = time.monotonic()
        if now < self._next or not self.max:
            return
        self._next = now + CHECK_EVERY
        room_left = self.max - self.count()
        if room_left < 0 and not getattr(self, "_trimmed", False):
            # sala com mais do que o máximo de agora (o máximo baixou): tira
            # as que sobram, ao acaso, uma vez ao abrir
            self._trimmed = True
            extra = [c for c, t in self.cr.block_map.items() if t == self.block]
            self.rng.shuffle(extra)
            for c in extra[: -room_left]:
                try:
                    self.cr.remove_block(c)
                except Exception:
                    pass
            print(f"[plantas] {self.block}: {-room_left} tirados (máx {self.max})", file=sys.stderr, flush=True)
            return
        if room_left <= 0:
            self.burst = 1
            return
        sx, _sy, sz = self.size
        tries = self.tries * self.burst
        first = self.burst > 1
        self.burst = 1
        for _ in range(tries):
            x, z = self.rng.randrange(sx), self.rng.randrange(sz)
            top = self._top(x, z)
            if top is None or self.cr.block_map.get(top) != GRASS:
                continue
            above = (x, top[1] + 1, z)
            if self.cr.owner_at(above) is not None:
                continue
            if self.water_level is not None and (self.water_level(above) or 0) > 0:
                continue
            if self.rng.random() < self.chance:
                self.cr.set_block(above, self.block, orientation=None)
                room_left -= 1
                if room_left <= 0:
                    break
        if first:
            print(f"[plantas] {self.block}: {self.count()} na sala (máx {self.max})", file=sys.stderr, flush=True)


# Flor lunar: brilha de noite (``"luz"`` em blocks.json). Só na sala pública,
# espalhada pela grama: ~1 por 220 células de chão (200x200 -> ~180).
GLOW = "flor lunar"
GLOW_AREA = 220
GLOW_CHANCE = 0.08
GLOW_BURST = 1            # (já não enche de uma vez: as primeiras vêm da criação da sala)


def glow_flowers(chunk_room, water_level=None, seed=None):
    return TuftGrowth(chunk_room, water_level, seed, block=GLOW, area_per=GLOW_AREA,
                      chance=GLOW_CHANCE)


# Mini sóis: flutuam sobre a grama da sala pública e soltam fadas
# (mobs.json "fada", spawn.publica.de_bloco). 200x200 -> 16.
SUN = "berco solar"
SUN_AREA = 2500


def mini_suns(chunk_room, water_level=None, seed=None):
    return TuftGrowth(chunk_room, water_level, seed, block=SUN, area_per=SUN_AREA,
                      chance=0.02)


__all__ = ["TuftGrowth", "TUFT", "GLOW", "glow_flowers", "SUN", "mini_suns"]
