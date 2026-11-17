"""ChunkRoom sem gráficos, para a física no servidor.

Tem só o "estado" da sala (que bloco está em cada célula, âncoras de blocos
grandes, orientações) com a mesma interface que a física usa do ChunkRoom do
jogo (engine/chunk_room.py). Mudar um bloco grava na sala (CRUD_room.Room),
que avisa o servidor (backend do worker).
"""

from __future__ import annotations

from ursina import Vec3

from blocks import get_block_type
from blocks.blocks_properties.produced import size_in_cells


class HeadlessChunkRoom:
    VOID_Y = -20
    BUILD_ABOVE = 64

    def __init__(self, room, size=2.0):
        self.room = room
        self.size = float(size)
        self.block_map = {pos: tipo for pos, tipo in room.iter_blocks()}
        self.block_orientations = dict(room.iter_orientations())
        self.occupancy = {}
        self.cells = set()
        self.produced_entities = {}
        for anchor, tipo in self.block_map.items():
            for cell in self.cells_for_block(anchor, tipo):
                self.occupancy.setdefault(cell, anchor)
                self.cells.add(cell)

    # --- leitura -------------------------------------------------------------
    def block_to_world(self, bpos):
        bx, by, bz = bpos
        return Vec3(bx * self.size, 1 + by * self.size, bz * self.size)

    def owner_at(self, cell):
        if cell in self.block_map:
            return cell
        return self.occupancy.get(cell)

    def type_at(self, cell):
        owner = self.owner_at(cell)
        return self.block_map.get(owner) if owner is not None else None

    def orientation_at(self, cell):
        owner = self.owner_at(cell)
        if owner is None:
            return None
        tipo = self.block_map.get(owner)
        bt = get_block_type(tipo) if tipo else None
        orientation = self.block_orientations.get(owner)
        if bt is not None and bt.format == "produced":
            return orientation if orientation in {"N", "S", "L", "O"} else "N"
        if bt is not None and bt.format in ("cylindrical", "cylinder"):
            return orientation if orientation in {"cima", "deitado_n_s", "deitado_l_o"} else "cima"
        return orientation

    def in_bounds(self, cell):
        return self.VOID_Y < cell[1] < self.room.size_y + self.BUILD_ABOVE

    def in_map(self, cell):
        return 0 <= cell[0] < self.room.size_x and 0 <= cell[2] < self.room.size_z

    @property
    def max_y(self):
        return self.room.size_y + self.BUILD_ABOVE - 1

    def cells_for_block(self, bpos, tipo):
        bt = get_block_type(tipo) if tipo else None
        if bt is not None and bt.format == "produced":
            try:
                sx, sy, sz = size_in_cells(bt.raw.get("block_properties", {}))
            except (TypeError, ValueError):
                sx, sy, sz = 1, 1, 1
        else:
            sx, sy, sz = 1, 1, 1
        return [(bpos[0] + dx, bpos[1] + dy, bpos[2] + dz)
                for dx in range(sx) for dy in range(sy) for dz in range(sz)]

    def _is_opaque_cube(self, tipo):
        bt = get_block_type(tipo) if tipo else None
        return bool(bt is not None and bt.format == "default")

    # --- escrita ---------------------------------------------------------------
    def remove_block(self, bpos):
        bpos = tuple(bpos)
        tipo = self.block_map.get(bpos)
        if tipo is None:
            return False
        self.room.remove_block(*bpos)
        self.block_map.pop(bpos, None)
        self.block_orientations.pop(bpos, None)
        for cell in self.cells_for_block(bpos, tipo):
            if self.occupancy.get(cell) == bpos:
                self.occupancy.pop(cell, None)
            self.cells.discard(cell)
        return True

    def set_block(self, bpos, tipo, orientation=None):
        bpos = tuple(bpos)
        self.room.set_block(*bpos, tipo, orientation=orientation)
        self.block_map[bpos] = tipo
        if orientation is None:
            self.block_orientations.pop(bpos, None)
        else:
            self.block_orientations[bpos] = orientation
        for cell in self.cells_for_block(bpos, tipo):
            self.occupancy[cell] = bpos
            self.cells.add(cell)
        return True
