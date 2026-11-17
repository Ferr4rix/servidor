"""Geração de árvores. Devolve blocos, não toca na sala.

Contrato: `tree_blocks(trunk_x, trunk_z, base_y)` devolve
`{(x, y, z): block_type}` — o CRUD_room decide onde plantar, valida
limites e mistura com o resto das decorações.
"""
from __future__ import annotations


TRUNK_HEIGHT = 5      # blocos de tronco
BASE_Y = 4            # base do tronco: pedra dura em y=0, terra em y=1..3


def tree_blocks(trunk_x: int, trunk_z: int, base_y: int = BASE_Y) -> dict:
    """Blocos de uma árvore com tronco em (trunk_x, base_y, trunk_z).

    Layout vertical (y cresce para cima), com base_y = 1:
        base_y    .. base_y+3 : tronco (4 blocos de wood)
        base_y+2              : folhagem 3x3 cheia (envolve o tronco)
        base_y+3              : folhagem 5x5 sem cantos
        base_y+4              : folhagem 3x3 cheia
        base_y+5              : folhagem 3x3 sem cantos
        base_y+6              : 1 bloco de topo
    """
    blocks: dict[tuple[int, int, int], str] = {}

    def put(pos, btype):
        # A primeira escrita ganha — evita folhas a sobreporem-se ao tronco.
        blocks.setdefault(pos, btype)

    # ----- tronco ------------------------------------------------------
    for i in range(TRUNK_HEIGHT):
        put((trunk_x, base_y + i, trunk_z), "wood")

    leaf_y0 = base_y + 3

    # ----- camada 0: 3x3 a rodear o tronco -----------------------------
    for dx in (-1, 0, 1):
        for dz in (-1, 0, 1):
            put((trunk_x + dx, leaf_y0, trunk_z + dz), "leaves")

    # ----- camada 1: 5x5 sem cantos ------------------------------------
    for dx in range(-2, 3):
        for dz in range(-2, 3):
            if abs(dx) == 2 and abs(dz) == 2:
                continue
            put((trunk_x + dx, leaf_y0 + 1, trunk_z + dz), "leaves")

    # ----- camada 2: 3x3 cheia -----------------------------------------
    for dx in (-1, 0, 1):
        for dz in (-1, 0, 1):
            put((trunk_x + dx, leaf_y0 + 2, trunk_z + dz), "leaves")

    # ----- camada 3: 3x3 sem cantos ------------------------------------
    for dx in (-1, 0, 1):
        for dz in (-1, 0, 1):
            if abs(dx) == 1 and abs(dz) == 1:
                continue
            put((trunk_x + dx, leaf_y0 + 3, trunk_z + dz), "leaves")

    # ----- camada 4: topo, 1 bloco -------------------------------------
    put((trunk_x, leaf_y0 + 4, trunk_z), "leaves")

    return blocks