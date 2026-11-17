"""Bloco cilíndrico com meshes separadas para laterais e tampas."""

import math
from ursina import Mesh, color


def _build_sides(resolution, radius, height):
    """Laterais do cilindro. UV: u dá uma volta, v cobre a altura toda."""
    half = height / 2.0
    v, uv, n, c, t = [], [], [], [], []

    for i in range(resolution + 1):
        a = i / resolution * math.tau
        cx, cz = math.cos(a), math.sin(a)
        u = i / resolution
        v.append((cx * radius, -half, cz * radius)); uv.append((u, 0.0))
        n.append((cx, 0.0, cz));                    c.append(color.white)
        v.append((cx * radius,  half, cz * radius)); uv.append((u, 1.0))
        n.append((cx, 0.0, cz));                    c.append(color.white)

    # Winding invertido (ordem trocada) para bater com a convenção do Ursina:
    #   antes:  (a, b, cc) / (b, d, cc)  →  normais apontavam para DENTRO
    #   agora:  (a, cc, b) / (b, cc, d)  →  normais apontam para FORA
    for i in range(resolution):
        a, b, cc, d = i * 2, i * 2 + 1, i * 2 + 2, i * 2 + 3
        t.append((a, cc, b))
        t.append((b, cc, d))

    return Mesh(vertices=v, triangles=t, uvs=uv,
                normals=n, colors=c, mode='triangle')


def _build_cap(resolution, radius, y, ny, flip):
    """Tampa (disco). UV mapeia o disco inteiro em (0..1)x(0..1)."""
    v, uv, n, c, t = [], [], [], [], []

    v.append((0.0, y, 0.0)); uv.append((0.5, 0.5))
    n.append((0.0, ny, 0.0)); c.append(color.white)
    ring_start = len(v)

    for i in range(resolution):
        a = i / resolution * math.tau
        cx, cz = math.cos(a), math.sin(a)
        v.append((cx * radius, y, cz * radius))
        uv.append((0.5 + cx * 0.5, 0.5 + cz * 0.5))
        n.append((0.0, ny, 0.0)); c.append(color.white)

    for i in range(resolution):
        a0 = 0
        b = ring_start + i
        cc = ring_start + ((i + 1) % resolution)
        t.append((a0, cc, b) if flip else (a0, b, cc))

    return Mesh(vertices=v, triangles=t, uvs=uv,
                normals=n, colors=c, mode='triangle')


# --- API pública ---
def create_model(resolution=16, radius=0.5, height=1.0):
    """Compatível com o factory antigo (`create_block_model`)."""
    return _build_sides(resolution, radius, height)


def create_side_mesh(resolution=16, radius=0.5, height=1.0):
    return _build_sides(resolution, radius, height)


def create_top_cap_mesh(resolution=16, radius=0.5, height=1.0):
    return _build_cap(resolution, radius, height / 2.0, 1.0, False)


def create_bottom_cap_mesh(resolution=16, radius=0.5, height=1.0):
    return _build_cap(resolution, radius, -height / 2.0, -1.0, True)