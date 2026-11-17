"""Gera os modelos do parafuso de Arquimedes e das peças de torque.

    python tools/torque_modelos.py

Estilo do jogo: tudo feito de **cubos** (como no Blockbench) com texturas de
**16x16 px por bloco** (1 pixel de textura = 1 px do modelo). A água usa a
textura ``assets/textures/agua.png`` do próprio jogo.

Para cada peça escreve o ``.gltf`` (assets/models/produced_blocks), o
``.bbmodel`` (assets/models/source, abre no Blockbench) e o ícone
(assets/icons):

  parafuso_arquimedes  calha de tábuas com cintas de ferro, eixo redondo e hélice contínua de madeira
  encaixe_parafuso     caixa de engrenagens (pontas de eixo nos lados e em cima, bica atrás)
  eixo / eixo_deitado  barra de transmissão
  manivela / manivela_lado

Convenções: coordenadas do Blockbench, 16 px = 1 bloco, bloco de x/z -8..8 e
y 0..16; a frente é o norte (-Z), que o jogo vira para quem pôs o bloco
(``rotation_offset`` [0, 180, 0]). Animação ``girar``: 1 volta em 1 s (o jogo
põe a pose pela fase do torque, ver engine/screw_view.py).

Parafuso: a 45°, da aresta de baixo da frente à aresta de cima de trás. Tudo
fica **acima da diagonal** do bloco (o fundo da calha assenta nela): não
entra no bloco de baixo/de trás (a escada de apoio). Os pedaços que passam
das faces (a junta, no ar por cima do degrau) são nós à parte (``*_baixo`` /
``*_cima``) que só aparecem com um vizinho ligado desse lado.
"""

from __future__ import annotations

import base64
import io
import json
import math
import random
import sys
import uuid as _uuid
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from caminhos import CLIENTE as ROOT, FONTES  # noqa: E402


# ---------------------------------------------------------------- medidas do parafuso (px)
L = 16 * math.sqrt(2)          # eixo dentro de um bloco (aresta a aresta)
H = L / 2
TURNS = 2                      # voltas da hélice por bloco
PITCH = L / TURNS
K = PITCH / (2 * math.pi)      # s = K * theta
AXIS_Z = -5.6                  # o eixo fica 5,6 px acima da diagonal (z local: + = para baixo)
SHAFT = 1.1                    # raio do eixo (tronco de 8 lados)
BLADE_R = 4.3                  # raio da hélice (0,3 px acima do fundo: sem z-fighting)
FLIGHT_T = 0.5                 # meia espessura da hélice (1 px: chapa fina demais cintilava)
SEGS_PER_TURN = 12             # segmentos da hélice por volta (lisa, sem degraus)
FLOOR = (-1.0, 0.0)            # fundo da calha: assenta na diagonal (z = 0)
WALL_IN, WALL_OUT = 5.0, 6.0   # paredes (x)
WALL_TOP = -9.0
HOOP_T = 1.0                   # espessura dos arcos de ferro (1 px: mais fino cintilava)
HOOP_TOP = AXIS_Z - BLADE_R - 0.3 - HOOP_T   # arcos de ferro: 0,3 px acima da hélice
C45 = math.cos(math.radians(45))

# ---------------------------------------------------------------- texturas (atlas 64x64, ladrilhos 16x16)
TILES = {
    "tabua": (0, 0), "tabua_escura": (1, 0), "ferro": (2, 0), "agua": (3, 0),
    "eixo": (0, 1), "eixo_ponta": (1, 1), "caixa": (2, 1), "caixa_eixo": (3, 1),
    "punho": (0, 2), "base_ferro": (1, 2), "pa": (2, 2), "bica": (3, 2),
    "isq_corpo": (0, 3), "isq_metal": (1, 3), "isq_roda": (2, 3),
}
AVG = {}                       # cor média de cada ladrilho (ícones / prévias)


def _px(img, tile, x, y, rgb, a=255):
    tx, ty = TILES[tile]
    img.putpixel((tx * 16 + x, ty * 16 + y), tuple(int(max(0, min(255, c))) for c in rgb) + (a,))


PAD_CELL, PAD = 32, 8          # atlas do glTF: cada ladrilho 16x16 numa célula 32x32
PAD_SIZE = 4 * PAD_CELL        # com 8 px de margem que repete a borda (mipmaps sem sangrar)


def padded_atlas_png(png: bytes) -> bytes:
    """O atlas 64x64 com margens: cada ladrilho no meio de uma célula 32x32,
    e a margem copia a borda do ladrilho. Assim a textura pode ter mipmaps
    (como os blocos do jogo) sem as cores dos ladrilhos vizinhos sangrarem;
    sem mipmaps, de longe, cada pixel do ecrã apanhava um texel ao acaso e
    o modelo ficava cheio de granulado a piscar."""
    from PIL import Image
    src = Image.open(io.BytesIO(png)).convert("RGBA")
    out = Image.new("RGBA", (PAD_SIZE, PAD_SIZE), (0, 0, 0, 0))
    for tx in range(4):
        for ty in range(4):
            tile = src.crop((tx * 16, ty * 16, tx * 16 + 16, ty * 16 + 16))
            ox, oy = tx * PAD_CELL + PAD, ty * PAD_CELL + PAD
            for y in range(-PAD, 16 + PAD):
                for x in range(-PAD, 16 + PAD):
                    out.putpixel((ox + x, oy + y), tile.getpixel((min(15, max(0, x)), min(15, max(0, y)))))
    buf = io.BytesIO()
    out.save(buf, "PNG")
    return buf.getvalue()


def padded_uv(t, mat):
    """UV do atlas 64x64 (px) -> UV normalizado do atlas com margens."""
    tx, ty = TILES[mat]
    u = min(16.0, max(0.0, t[0] - tx * 16))
    v = min(16.0, max(0.0, t[1] - ty * 16))
    return ((tx * PAD_CELL + PAD + u) / PAD_SIZE, (ty * PAD_CELL + PAD + v) / PAD_SIZE)


def atlas_png() -> bytes:
    from PIL import Image
    rnd = random.Random(5)
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))

    def planks(tile, base, seam, light, grain):
        for y in range(16):
            for x in range(16):
                c = base
                if y % 4 == 3:
                    c = seam                                 # junta entre tábuas
                elif (x + (y // 4) * 5) % 16 == 0 and y % 4 != 3:
                    c = seam                                 # topo da tábua
                elif rnd.random() < 0.18:
                    c = grain
                elif y % 4 == 0 and rnd.random() < 0.5:
                    c = light
                j = rnd.randint(-5, 5)
                _px(img, tile, x, y, (c[0] + j, c[1] + j, c[2] + j))

    planks("tabua", (176, 134, 80), (104, 74, 40), (198, 156, 98), (150, 110, 62))
    planks("tabua_escura", (122, 86, 50), (70, 48, 26), (142, 102, 62), (100, 70, 40))
    # hélice: chapa de ferro (aço escovado ao longo da espiral (u), borda de
    # fora mais escura e gasta, rebites junto ao eixo)
    for y in range(16):
        for x in range(16):
            c = (150, 156, 164)
            if y >= 14:
                c = (96, 100, 108)                   # borda de fora
            elif y == 13:
                c = (176, 182, 190)                  # brilho na dobra da borda
            elif y <= 1:
                c = (110, 114, 122)                  # junto ao eixo, à sombra
            elif y == 3 and x % 4 == 1:
                c = (196, 200, 206)                  # rebites
            elif (y * 7 + x // 5) % 6 == 0:
                c = (138, 144, 152)                  # riscos do escovado
            elif rnd.random() < 0.06:
                c = (128, 110, 96)                   # um pouco de ferrugem
            j = rnd.randint(-3, 3)
            _px(img, "pa", x, y, (c[0] + j, c[1] + j, c[2] + j))
    planks("bica", (122, 86, 50), (70, 48, 26), (142, 102, 62), (100, 70, 40))
    # ferro: chapa com borda e rebites
    for tile, base in (("ferro", (164, 168, 174)), ("base_ferro", (110, 114, 122))):
        for y in range(16):
            for x in range(16):
                c = base
                if x in (0, 15) or y in (0, 15):
                    c = tuple(v - 40 for v in base)
                elif x in (1, 14) or y in (1, 14):
                    c = tuple(v + 18 for v in base)
                if (x, y) in ((3, 3), (12, 3), (3, 12), (12, 12)):
                    c = tuple(v + 45 for v in base)
                j = rnd.randint(-4, 4)
                _px(img, tile, x, y, (c[0] + j, c[1] + j, c[2] + j))
    # eixo: madeira descascada com veio ao comprido (u)
    for y in range(16):
        for x in range(16):
            c = (168, 122, 70)
            if y in (0, 15):
                c = (112, 78, 42)
            elif rnd.random() < 0.25:
                c = (142, 100, 56)
            if x in (3, 12):
                c = tuple(v - 25 for v in c)                 # cintas
            j = rnd.randint(-5, 5)
            _px(img, "eixo", x, y, (c[0] + j, c[1] + j, c[2] + j))
    # ponta do eixo: anéis
    for y in range(16):
        for x in range(16):
            d = max(abs(x - 7.5), abs(y - 7.5))
            c = (186, 146, 92) if int(d) % 3 else (140, 100, 58)
            if d > 6.5:
                c = (112, 78, 42)
            _px(img, "eixo_ponta", x, y, c)
    # eixo: a textura de lado do bloco "wood" do próprio jogo (wood_sides.png),
    # com o veio ao comprido do eixo (u); as pontas com os anéis (wood_top_bot.png)
    for tile, fname, turn in (("eixo", "wood_sides.png", True), ("eixo_ponta", "wood_top_bot.png", False)):
        try:
            tex = Image.open(ROOT / "assets" / "textures" / fname).convert("RGB").resize((16, 16), Image.NEAREST)
        except OSError:
            continue                      # sem a textura: fica a desenhada acima
        if turn:
            tex = tex.transpose(Image.Transpose.ROTATE_90)   # veio vertical -> ao comprido (u)
        for y in range(16):
            for x in range(16):
                _px(img, tile, x, y, tex.getpixel((x, y)))
    # caixa (encaixe): moldura escura com pregos e tábuas claras por dentro
    for tile in ("caixa", "caixa_eixo"):
        for y in range(16):
            for x in range(16):
                if x < 2 or x > 13 or y < 2 or y > 13:
                    c = (92, 62, 36) if (x + y) % 5 else (80, 54, 30)
                    if (x, y) in ((1, 1), (14, 1), (1, 14), (14, 14)):
                        c = (190, 190, 196)                  # pregos
                else:
                    c = (172, 130, 78) if (y - 2) % 4 != 3 else (118, 84, 46)
                    if rnd.random() < 0.15:
                        c = (150, 110, 62)
                if tile == "caixa_eixo" and 4 <= x <= 11 and 4 <= y <= 11:
                    c = (150, 154, 160) if 5 <= x <= 10 and 5 <= y <= 10 else (96, 100, 106)
                    if 6 <= x <= 9 and 6 <= y <= 9:
                        c = (40, 34, 30)                     # furo do eixo
                j = rnd.randint(-4, 4)
                _px(img, tile, x, y, (c[0] + j, c[1] + j, c[2] + j))
    # punho
    for y in range(16):
        for x in range(16):
            c = (86, 56, 32) if (x + y) % 4 else (70, 44, 24)
            _px(img, "punho", x, y, c)
    # água: a do jogo
    try:
        agua = Image.open(ROOT / "assets" / "textures" / "agua.png").convert("RGBA").resize((16, 16), Image.NEAREST)
        for y in range(16):
            for x in range(16):
                r, g, b, _a = agua.getpixel((x, y))
                _px(img, "agua", x, y, (r, g, b), 200)
    except OSError:
        for y in range(16):
            for x in range(16):
                _px(img, "agua", x, y, (60, 110, 200), 200)
    # ---- Tudo a partir da madeira do JOGO (wood_sides / wood_top_bot), não
    # das tábuas do Minecraft; o ferro e a pá em metal limpo, sem pintas.
    def game(fname):
        try:
            return Image.open(ROOT / "assets" / "textures" / fname).convert("RGB").resize((16, 16), Image.NEAREST)
        except OSError:
            return None

    wood = game("wood_sides.png")
    if wood is not None:
        along = wood.transpose(Image.Transpose.ROTATE_90)        # veio ao comprido (u)
        dim = lambda c, k: tuple(max(0, min(255, int(v * k))) for v in c)
        for y in range(16):
            for x in range(16):
                c = along.getpixel((x, y))
                _px(img, "tabua", x, y, dim(c, 1.12))           # calha (um pouco mais clara)
                _px(img, "bica", x, y, dim(c, 1.12))
                _px(img, "tabua_escura", x, y, dim(c, 0.78))    # braço da manivela
                _px(img, "punho", x, y, dim(wood.getpixel((x, y)), 0.9))
                # caixa do encaixe: a madeira com uma moldura escura de 1 px
                cv = wood.getpixel((x, y))
                edge = x in (0, 15) or y in (0, 15)
                cc = dim(cv, 0.6) if edge else dim(cv, 1.05)
                _px(img, "caixa", x, y, cc)
                _px(img, "caixa_eixo", x, y, cc)
        # caixa_eixo: placa de ferro com o furo do eixo no meio
        for y in range(4, 12):
            for x in range(4, 12):
                c = (150, 154, 160)
                if x in (4, 11) or y in (4, 11):
                    c = (112, 116, 122)
                if 6 <= x <= 9 and 6 <= y <= 9:
                    c = (36, 30, 26)
                _px(img, "caixa_eixo", x, y, c)

    def metal(tile, base, light, dark):
        for y in range(16):
            for x in range(16):
                c = base
                if y == 0 or x == 0:
                    c = light                                     # chanfro com luz
                elif y == 15 or x == 15:
                    c = dark
                _px(img, tile, x, y, c)

    metal("ferro", (146, 150, 156), (176, 180, 186), (104, 108, 114))
    metal("base_ferro", (110, 114, 120), (140, 144, 150), (80, 84, 90))
    # pá: chapa de aço lisa; v = do eixo (0) para a borda (15); só um degradê
    # suave e a borda de fora mais escura (sem pintas: cintilavam)
    for y in range(16):
        for x in range(16):
            k = y / 15
            c = (int(128 + 34 * k), int(133 + 34 * k), int(140 + 34 * k))
            if y >= 14:
                c = (100, 104, 110)
            elif y == 13:
                c = (178, 182, 188)
            _px(img, "pa", x, y, c)

    # isqueiro: corpo vermelho com chanfro; tampa de aço; roda estriada
    for y in range(16):
        for x in range(16):
            c = (176, 40, 34)
            if x == 0 or y == 0:
                c = (212, 70, 58)
            elif x == 15 or y == 15:
                c = (120, 24, 22)
            _px(img, "isq_corpo", x, y, c)
            m = (170, 174, 180)
            if x == 0 or y == 0:
                m = (210, 214, 220)
            elif x == 15 or y == 15:
                m = (110, 114, 120)
            elif 5 <= x <= 10 and 5 <= y <= 10 and (x + y) % 2 == 0:
                m = (60, 62, 66)                  # furos da chaminé
            _px(img, "isq_metal", x, y, m)
            _px(img, "isq_roda", x, y, (90, 92, 98) if x % 2 == 0 else (150, 152, 158))
    for name, (tx, ty) in TILES.items():
        px = [img.getpixel((tx * 16 + x, ty * 16 + y))[:3] for x in range(16) for y in range(16)]
        AVG[name] = tuple(int(sum(p[i] for p in px) / len(px)) for i in range(3))
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


# ---------------------------------------------------------------- malhas com UV
_SOLIDS = {}     # id do sólido -> material das tampas dos cortes
_next_solid = [0]


def _newell(P):
    n = np.zeros(3)
    for i in range(len(P)):
        a, b = P[i], P[(i + 1) % len(P)]
        n += np.array([(a[1] - b[1]) * (a[2] + b[2]), (a[2] - b[2]) * (a[0] + b[0]),
                       (a[0] - b[0]) * (a[1] + b[1])])
    return n


class Mesh:
    """Polígonos: (pontos Nx3, uv Nx2 em px do atlas, material, sólido).

    Tudo é feito de **sólidos fechados** com as faces viradas para fora: o
    jogo só desenha a frente das faces (backface culling), por isso uma face
    virada para dentro, ou um corte aberto, deixava ver através do modelo.
    Cada polígono é orientado ao ser posto (para fora do centro do sólido) e
    os cortes (``split_mesh``) fecham cada sólido com uma tampa."""

    def __init__(self):
        self.polys = []
        self.sid = None
        self.center = None

    def solid(self, center, cap=None):
        """Começa um sólido novo (centro: para orientar as faces para fora)."""
        _next_solid[0] += 1
        self.sid = _next_solid[0]
        self.center = np.array(center, float)
        _SOLIDS[self.sid] = cap
        return self.sid

    def add(self, pts, uvs, mat):
        P, U = np.array(pts, float), np.array(uvs, float)
        if self.center is not None:
            n = _newell(P)
            if n @ (P.mean(0) - self.center) < 0:
                P, U = P[::-1].copy(), U[::-1].copy()
        if _SOLIDS.get(self.sid, "x") is None:
            _SOLIDS[self.sid] = mat                     # tampas com o material da 1.ª face
        self.polys.append((P, U, mat, self.sid))

    def extend(self, other):
        self.polys += other.polys
        return self

    def transformed(self, fn):
        m = Mesh()
        m.polys = [(np.array([fn(p) for p in P]), U.copy(), mat, sid) for P, U, mat, sid in self.polys]
        return m


_FACES = {   # face: (cantos (i,j,k) em ordem), eixo u, eixo v
    "-z": (((0, 0, 0), (0, 1, 0), (1, 1, 0), (1, 0, 0)), 0, 1),
    "+z": (((0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)), 0, 1),
    "-x": (((0, 0, 0), (0, 0, 1), (0, 1, 1), (0, 1, 0)), 2, 1),
    "+x": (((1, 0, 0), (1, 1, 0), (1, 1, 1), (1, 0, 1)), 2, 1),
    "-y": (((0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1)), 0, 2),
    "+y": (((0, 1, 0), (0, 1, 1), (1, 1, 1), (1, 1, 0)), 0, 2),
}


def box(m, lo, hi, mat, long_u=True, faces=None):
    """Cubo com UV de 1 px de textura por px. ``mat``: material de todas as
    faces, ou dict {face: material} (as outras com "_" ou a primeira).
    ``long_u``: o veio (u) segue o lado mais comprido da face."""
    # Sólido fechado: as 6 faces sempre. (``faces`` era para tirar faces
    # encostadas e evitar z-fighting; com backface culling, duas faces no
    # mesmo plano viradas para lados opostos nunca lutam, e uma face a menos
    # era um buraco visto de algum lado.)
    faces = None
    lo, hi = np.array(lo, float), np.array(hi, float)
    size = hi - lo
    m.solid((lo + hi) / 2, cap=(mat.get("_") if isinstance(mat, dict) else mat))
    for face, (corners, ua, va) in _FACES.items():
        if faces is not None and face not in faces:
            continue
        fm = mat.get(face, mat.get("_")) if isinstance(mat, dict) else mat
        if fm is None:
            continue
        a, b = ua, va
        if long_u and size[b] > size[a]:
            a, b = b, a
        wa, wb = size[a], size[b]
        k = min(1.0, 16.0 / max(wa, wb, 1e-6))
        offa = max(0.0, min(16 - wa * k, lo[a] % 16)) if wa * k < 16 else 0.0
        offb = max(0.0, min(16 - wb * k, lo[b] % 16)) if wb * k < 16 else 0.0
        tx, ty = TILES[fm]
        pts, uvs = [], []
        for c in corners:
            p = lo + np.array(c) * size
            pts.append(p)
            uvs.append((tx * 16 + offa + (p[a] - lo[a]) * k, ty * 16 + offb + (p[b] - lo[b]) * k))
        m.add(pts, uvs, fm)


def tri_prism_x(m, x0, x1, tri, mat):
    """Prisma de secção triangular (pontos (y, z)) ao longo de x."""
    tx, ty = TILES[mat]
    ys = [p[0] for p in tri]
    zs = [p[1] for p in tri]
    m.solid(((x0 + x1) / 2, sum(ys) / 3, sum(zs) / 3), cap=mat)
    uv = lambda y, z: (tx * 16 + (y - min(ys)) % 16, ty * 16 + (z - min(zs)) % 16)
    m.add([(x0, y, z) for y, z in reversed(tri)], [uv(y, z) for y, z in reversed(tri)], mat)
    m.add([(x1, y, z) for y, z in tri], [uv(y, z) for y, z in tri], mat)
    for i in range(3):
        (ya, za), (yb, zb) = tri[i], tri[(i + 1) % 3]
        ln = math.hypot(yb - ya, zb - za)
        w = x1 - x0
        k = min(1.0, 16 / max(ln, w))
        m.add([(x0, ya, za), (x0, yb, zb), (x1, yb, zb), (x1, ya, za)],
              [(tx * 16, ty * 16), (tx * 16 + ln * k, ty * 16), (tx * 16 + ln * k, ty * 16 + w * k),
               (tx * 16, ty * 16 + w * k)], mat)


# ---------------------------------------------------------------- cortes (com UV)
def split(P, U, n, d, eps=1e-7):
    """Corta um polígono pelo plano n·p = d: (parte <= d, parte > d).

    Pontos em cima do plano vão para os dois lados (antes iam só para um, e
    uma face que só encostava ao plano por uma aresta perdia pontos e
    desaparecia: ficava um buraco). Uma face deitada no próprio plano vai
    para o lado de que é a fronteira (pela direção da normal)."""
    f = P @ n - d
    f = np.where(np.abs(f) < eps, 0.0, f)
    if np.all(f == 0):
        side = (_newell(P) @ n) > 0
        return ((P, U), None) if side else (None, (P, U))
    a, au, b, bu = [], [], [], []
    for i in range(len(P)):
        j = (i + 1) % len(P)
        p, q, fp, fq = P[i], P[j], f[i], f[j]
        if fp <= 0:
            a.append(p)
            au.append(U[i])
        if fp >= 0:
            b.append(p)
            bu.append(U[i])
        if (fp < 0 < fq) or (fq < 0 < fp):
            t = fp / (fp - fq)
            x, xu = p + (q - p) * t, U[i] + (U[j] - U[i]) * t
            a.append(x)
            au.append(xu)
            b.append(x)
            bu.append(xu)
    A = (np.array(a), np.array(au)) if len(a) >= 3 else None
    B = (np.array(b), np.array(bu)) if len(b) >= 3 else None
    return A, B


def _cap(points, n, side_normal, mat):
    """Tampa (polígono convexo) dos pontos de corte de um sólido no plano."""
    uniq = []
    for p in points:
        if all(np.linalg.norm(p - q) > 1e-5 for q in uniq):
            uniq.append(p)
    if len(uniq) < 3:
        return None
    c = np.mean(uniq, 0)
    e1 = np.cross(n, (1, 0, 0) if abs(n[0]) < 0.9 else (0, 1, 0))
    e1 /= np.linalg.norm(e1)
    e2 = np.cross(n, e1)
    uniq.sort(key=lambda p: math.atan2((p - c) @ e2, (p - c) @ e1))
    P = np.array(uniq)
    if np.linalg.norm(_newell(P)) < 1e-6:
        return None
    if _newell(P) @ side_normal < 0:
        P = P[::-1]
    a, b = P @ e1, P @ e2
    ext = max(a.max() - a.min(), b.max() - b.min(), 1e-6)
    k = min(1.0, 16.0 / ext)
    tx, ty = TILES[mat]
    U = np.array([(tx * 16 + (ai - a.min()) * k, ty * 16 + (bi - b.min()) * k) for ai, bi in zip(a, b)])
    return P, U


def split_mesh(mesh, n, d, caps=None):
    """Corta pelo plano n·p = d: (lado n·p <= d, lado n·p > d).

    ``caps``: "lo", "hi" ou "both" fecha cada sólido cortado desse lado com
    uma tampa (o material das tampas do sólido); None, sem tampas. As tampas
    só servem onde o corte fica à vista: numa junta (o vizinho continua a
    peça) a aresta da tampa encostada à superfície de fora fazia uma linha."""
    n = np.array(n, float)
    lo, hi = Mesh(), Mesh()
    cuts = {}
    sides = {}                     # sólido -> (tem parte < d, tem parte > d)
    for P, U, mat, sid in mesh.polys:
        f = P @ n - d
        neg, pos = sides.get(sid, (False, False))
        sides[sid] = (neg or bool((f < -1e-6).any()), pos or bool((f > 1e-6).any()))
        a, b = split(P, U, n, d)
        # (pedaços sem área, achatados numa aresta da célula, saem)
        if a is not None and np.linalg.norm(_newell(a[0])) < 1e-6:
            a = None
        if b is not None and np.linalg.norm(_newell(b[0])) < 1e-6:
            b = None
        if a is not None:
            lo.polys.append((a[0], a[1], mat, sid))
        if b is not None:
            hi.polys.append((b[0], b[1], mat, sid))
        for part in (a, b):
            if part is not None:
                f = part[0] @ n - d
                cuts.setdefault(sid, []).extend(part[0][np.abs(f) < 1e-6])
    for sid, pts in cuts.items():
        if sides.get(sid) != (True, True):
            continue               # só encosta no plano: não foi cortado
        mat = _SOLIDS.get(sid) or "tabua"
        cap = _cap(pts, n, n, mat) if caps else None
        if cap is not None:
            if caps in ("lo", "both"):
                lo.polys.append((cap[0], cap[1], mat, sid))
            if caps in ("hi", "both"):
                hi.polys.append((cap[0][::-1].copy(), cap[1][::-1].copy(), mat, sid))
    return lo, hi


def to_block(p):        # local do parafuso (eixo = Y; z+ = para baixo) -> bloco
    x, y, z = p
    return np.array([x, 8 + y * C45 - z * C45, y * C45 + z * C45])


def to_local(p):
    x, y, z = p
    y -= 8
    return np.array([x, y * C45 + z * C45, -y * C45 + z * C45])


CUBE = [((0, 0, 1), 8.0), ((0, 1, 0), 16.0), ((0, 0, -1), 8.0), ((0, -1, 0), 0.0),
        ((1, 0, 0), 8.0), ((-1, 0, 0), 8.0)]


def cube_split(mesh_local):
    """Corta pela célula, no local: (dentro, fora na ponta de baixo, fora na
    de cima, tampas de baixo, tampas de cima).

    As tampas fecham o que fica dentro onde foi cortado; vão em peças à
    parte porque só se vêem **sem** vizinho desse lado (com vizinho, a peça
    de junta continua a superfície; a tampa ficava lá dentro com a aresta
    encostada à superfície de fora, a fazer uma linha)."""
    m = mesh_local.transformed(to_block)
    inside, low, high = m, Mesh(), Mesh()
    for n, d in CUBE:
        keep, out = split_mesh(inside, n, d, caps="lo")
        inside = keep
        for P, U, mat, sid in out.polys:
            s = np.mean([to_local(p)[1] for p in P])
            (high if s > 0 else low).polys.append((P, U, mat, sid))
    mid, cap_low, cap_high = Mesh(), Mesh(), Mesh()
    for poly in inside.polys:
        P = poly[0]
        on = any(np.all(np.abs(P @ np.array(n, float) - d) < 1e-5) for n, d in CUBE)
        if on:
            s = np.mean([to_local(p)[1] for p in P])
            (cap_high if s > 0 else cap_low).polys.append(poly)
        else:
            mid.polys.append(poly)
    back = lambda mm: mm.transformed(to_local)
    return back(mid), back(low), back(high), back(cap_low), back(cap_high)


def axial_split(mesh, cut):
    low, rest = split_mesh(mesh, (0, 1, 0), -cut)
    mid, high = split_mesh(rest, (0, 1, 0), cut)
    return low, mid, high


def rot_about_axis(mesh, ang):
    """Gira à volta do eixo do parafuso (Y local, em x=0, z=AXIS_Z)."""
    c, s = math.cos(ang), math.sin(ang)
    return mesh.transformed(lambda p: np.array([p[0] * c - (p[2] - AXIS_Z) * s, p[1],
                                                p[0] * s + (p[2] - AXIS_Z) * c + AXIS_Z]))


def inside_cube(mesh_local, tol=0.02):
    for P, _U, _m, _s in mesh_local.polys:
        for p in P:
            b = to_block(p)
            if b[0] < -8 - tol or b[0] > 8 + tol or b[1] < -tol or b[1] > 16 + tol or b[2] < -8 - tol or b[2] > 8 + tol:
                return False
    return True


# ---------------------------------------------------------------- peças do parafuso
def trough(m, s0, s1):
    """Calha de tábuas: fundo na diagonal, paredes, cintas de ferro.

    É uma peça só em U, **sem faces por dentro**: as paredes não têm face
    de baixo (assentam no fundo), o fundo só tem face de cima entre as
    paredes, e não há tampas nas pontas (a calha continua no bloco vizinho
    ou é cortada pela célula). Faces no mesmo plano umas das outras
    piscavam (z-fighting), sobretudo vistas pelas pontas cortadas."""
    # fundo: por baixo e dos lados de fora; por cima só entre as paredes
    box(m, (-WALL_OUT, s0, FLOOR[0]), (WALL_OUT, s1, FLOOR[1]), "tabua")
    # paredes: em cima, por fora e por dentro
    box(m, (-WALL_OUT, s0, WALL_TOP), (-WALL_IN, s1, FLOOR[0]), "tabua", faces=("-z", "-x", "+x"))
    box(m, (WALL_IN, s0, WALL_TOP), (WALL_OUT, s1, FLOOR[0]), "tabua", faces=("-z", "-x", "+x"))
    for sc in (-H / 2, H / 2):                       # arcos de ferro por fora das paredes
        if s0 < sc < s1:
            # O arco passa POR CIMA da hélice (que sobe até AXIS_Z - BLADE_R),
            # sem a atravessar. Sem faces no mesmo plano: os lados sobem até
            # ao topo do arco; por baixo da parede não têm face de dentro.
            top = HOOP_TOP
            for sx in (1, -1):
                xo0, xo1 = sorted((sx * WALL_OUT, sx * (WALL_OUT + HOOP_T)))
                outer = "+x" if sx > 0 else "-x"
                inner = "-x" if sx > 0 else "+x"
                # junto à parede: só por fora, frente/trás e por baixo
                box(m, (xo0, sc - 1, WALL_TOP), (xo1, sc + 1, FLOOR[1]), "ferro",
                    faces=(outer, "-y", "+y", "+z"))
                # acima da parede (até ao topo do arco): por fora, por dentro, frente/trás
                xi0, xi1 = sorted((sx * WALL_IN, sx * (WALL_OUT + HOOP_T)))
                box(m, (xi0, sc - 1, top + HOOP_T), (xi1, sc + 1, WALL_TOP), "ferro",
                    faces=(outer, inner, "-y", "+y"))
            # travessa de cima (só por cima, por baixo, frente/trás; os topos
            # das pontas ficam nas faces de fora dos lados)
            box(m, (-WALL_OUT - HOOP_T, sc - 1, top), (WALL_OUT + HOOP_T, sc + 1, top + HOOP_T), "ferro",
                faces=("-z", "+z", "-y", "+y", "-x", "+x"))


def _uv(tile, u, v):
    tx, ty = TILES[tile]
    return (tx * 16 + u, ty * 16 + v)


def rotor(s0, s1):
    """Eixo redondo (tronco de 8 lados) e uma hélice contínua de chapa de ferro,
    como a de um parafuso de Arquimedes de verdade: uma fita em espiral à
    volta do eixo, com espessura e borda, sem degraus."""
    m = Mesh()

    def at(r, t, ds=0.0):        # ponto da hélice no ângulo t, raio r
        return (r * math.cos(t), K * t + ds, r * math.sin(t) + AXIS_Z)

    # eixo: 8 faces ao comprido, veio ao longo do eixo (um sólido: os cortes
    # fecham-no com os anéis da madeira)
    n = 8
    m.solid((0.0, (s0 + s1) / 2, AXIS_Z), cap="eixo_ponta")
    for k in range(n):
        a0, a1 = 2 * math.pi * k / n + math.pi / n, 2 * math.pi * (k + 1) / n + math.pi / n
        p = [(SHAFT * math.cos(a), sv, SHAFT * math.sin(a) + AXIS_Z) for a, sv in
             ((a0, s0), (a0, s1), (a1, s1), (a1, s0))]
        v0, v1 = 2 * k % 16, 2 * k % 16 + 2
        m.add(p, [_uv("eixo", 0, v0), _uv("eixo", 16, v0), _uv("eixo", 16, v1), _uv("eixo", 0, v1)], "eixo")
    # hélice: segmentos de 1/12 de volta, partidos também nos cantos do eixo
    # (8 lados): assim a borda de dentro de cada segmento fica deitada
    # numa face do eixo, sem entrar nele (antes entrava até 0,13 px e, nos
    # cortes da célula, a tampa da pá e a do eixo lutavam: z-fighting)
    step = 2 * math.pi / SEGS_PER_TURN
    t_a, t_b = s0 / K - step, s1 / K + step
    apo = SHAFT * math.cos(math.pi / n)              # apótema do eixo
    corner = math.pi / 4

    def r_in(t):                                    # raio da face do eixo no ângulo t
        return apo / math.cos(t - round(t / corner) * corner)

    cuts = set()
    k0, k1 = math.floor(t_a / step), math.ceil(t_b / step)
    cuts.update(k * step for k in range(k0, k1 + 1))
    c0, c1 = math.floor((t_a - math.pi / 8) / corner), math.ceil((t_b - math.pi / 8) / corner)
    cuts.update(math.pi / 8 + k * corner for k in range(c0, c1 + 1))
    ts = sorted(t for t in cuts if k0 * step - 1e-9 <= t <= k1 * step + 1e-9)
    clean = [ts[0]]
    for t in ts[1:]:
        if t - clean[-1] > 1e-6:
            clean.append(t)
    for ta, tb in zip(clean, clean[1:]):
        i = math.floor((ta + 1e-9) / step)
        u0 = (i % 4) * 4.0 + (ta - i * step) / step * 4.0
        u1 = u0 + (tb - ta) / step * 4.0
        ra, rb = r_in(ta + 1e-9), r_in(tb - 1e-9)     # a mesma face do eixo nas duas pontas
        # cada segmento é um sólido fechado (chapa com espessura)
        corners = [at(r, t, ds) for r, t in ((ra, ta), (BLADE_R, ta), (rb, tb), (BLADE_R, tb))
                   for ds in (-FLIGHT_T, FLIGHT_T)]
        m.solid(np.mean(np.array(corners), 0), cap="pa")
        for ds in (FLIGHT_T, -FLIGHT_T):            # frente e verso
            q = [at(ra, ta, ds), at(BLADE_R, ta, ds), at(BLADE_R, tb, ds), at(rb, tb, ds)]
            uv = [_uv("pa", u0, 0), _uv("pa", u0, 15.9), _uv("pa", u1, 15.9), _uv("pa", u1, 0)]
            m.add(q, uv, "pa")
        # borda de fora (mais escura) e a de dentro (deitada na face do eixo)
        for r0, r1 in ((BLADE_R, BLADE_R), (ra, rb)):
            q = [at(r0, ta, -FLIGHT_T), at(r0, ta, FLIGHT_T), at(r1, tb, FLIGHT_T), at(r1, tb, -FLIGHT_T)]
            m.add(q, [_uv("pa", u0, 14), _uv("pa", u0, 15.9), _uv("pa", u1, 15.9), _uv("pa", u1, 14)], "pa")
        # pontas do segmento (encostam às do vizinho, viradas ao contrário)
        for t, r in ((ta, ra), (tb, rb)):
            q = [at(r, t, -FLIGHT_T), at(BLADE_R, t, -FLIGHT_T), at(BLADE_R, t, FLIGHT_T), at(r, t, FLIGHT_T)]
            m.add(q, [_uv("pa", 0, 0), _uv("pa", 0, 15.9), _uv("pa", 1, 15.9), _uv("pa", 1, 0)], "pa")
    # corta nas pontas pedidas (as fatias são cortadas outra vez pela célula)
    return m


def pocket(m, s_b, depth=3.0, w=WALL_IN - 0.4, nx=6, nz=3):
    """Bolsa de água encostada à pá (que passa no fundo em s_b).

    A água fica **entre** a pá e a sua superfície (horizontal no mundo; no
    bloco a 45°: s - z constante), sem atravessar nada: a face de trás segue
    a superfície da pá (uma hélice, por isso torta), fica abaixo do eixo e
    afastada do fundo e das paredes. Feita numa grelha x por z."""
    zf = FLOOR[0] - 0.3                  # acima do fundo (não fica no mesmo plano)
    s0 = s_b + FLIGHT_T + 0.6            # o topo da água fica rente à pá em x = 0
    zt = zf - depth                      # abaixo do eixo (AXIS_Z + SHAFT)

    def s_top(z):                        # superfície da água
        return s0 + depth - (zf - z)

    def s_back(x, z):                    # a pá (lado de cima dela), mais uma folga
        t = math.atan2(z - AXIS_Z, x)
        return min(s_b + K * (t - math.pi / 2) + FLIGHT_T + 0.6, s_top(z))

    xs = np.linspace(-w, w, nx + 1)
    zs = np.linspace(zt, zf, nz + 1)
    m.solid((0.0, s0 + depth / 3, zf - depth / 3), cap="agua")
    tx, ty = TILES["agua"]
    uv = lambda x, z: (tx * 16 + (x + w) / (2 * w) * 16, ty * 16 + (z - zt) / (zf - zt) * 16)
    for i in range(nx):
        for j in range(nz):
            x0, x1, z0, z1 = xs[i], xs[i + 1], zs[j], zs[j + 1]
            q = [(x0, z0), (x1, z0), (x1, z1), (x0, z1)]
            back = [(x, s_back(x, z), z) for x, z in q]
            front = [(x, s_top(z), z) for x, z in q]
            if all(abs(b[1] - f[1]) < 1e-3 for b, f in zip(back, front)):
                continue                 # sem água aqui (a pá já está acima da superfície)
            m.add(back, [uv(x, z) for x, z in q], "agua")
            m.add(front, [uv(x, z) for x, z in q], "agua")
    # fundo e lados (fecham o volume)
    for i in range(nx):
        x0, x1 = xs[i], xs[i + 1]
        m.add([(x0, s_back(x0, zf), zf), (x1, s_back(x1, zf), zf), (x1, s_top(zf), zf), (x0, s_top(zf), zf)],
              [uv(x0, zf), uv(x1, zf), uv(x1, zt), uv(x0, zt)], "agua")
    for x in (-w, w):
        for j in range(nz):
            z0, z1 = zs[j], zs[j + 1]
            m.add([(x, s_back(x, z0), z0), (x, s_top(z0), z0), (x, s_top(z1), z1), (x, s_back(x, z1), z1)],
                  [uv(x, z0), uv(x, zt), uv(x, zt), uv(x, z1)], "agua")


def model_parafuso():
    carc = Mesh()
    trough(carc, -H, H)
    c_mid, c_low, c_high, c_tlow, c_thigh = cube_split(carc)
    rot = rotor(-H - 1, H + 1)
    rot = split_mesh(split_mesh(rot, (0, 1, 0), H)[0], (0, 1, 0), -H)[1]     # de junta a junta
    # o rotor é cortado pela célula, como a calha: sozinho fica um bloco
    # inteiro (as pontas que saem da célula são as peças da junta)
    r_mid, r_low, r_high, r_tlow, r_thigh = cube_split(rot)
    cut = round(float(max(abs(to_local(to_block(p))[1]) for P, _U, _m, _s in r_mid.polys for p in P)), 2)
    agua = []
    s_b = K * math.pi / 2                          # onde a pá passa no fundo (aponta para +z)
    for n in (-1, 0):
        b = Mesh()
        pocket(b, s_b + n * PITCH)
        agua.append(b)
    return [
        {"name": "Carcaca", "incl": True, "pivot": (0, 0, 0), "anim": None,
         "pieces": {"Carcaca_meio": c_mid, "Carcaca_baixo": c_low, "Carcaca_cima": c_high,
                    "Carcaca_tampa_baixo": c_tlow, "Carcaca_tampa_cima": c_thigh}},
        {"name": "Rotor", "incl": True, "pivot": (0, 0, AXIS_Z), "anim": ("rot", "y"),
         "pieces": {"Rotor_meio": r_mid, "Rotor_baixo": r_low, "Rotor_cima": r_high,
                    "Rotor_tampa_baixo": r_tlow, "Rotor_tampa_cima": r_thigh}},
        {"name": "Agua", "incl": True, "pivot": (0, 0, 0), "anim": ("sobe", PITCH),
         "pieces": {"Bolsa_0": agua[0], "Bolsa_1": agua[1]}},
    ], {"rotor_meio": cut}


# ---------------------------------------------------------------- encaixe
CASE = (-7.0, 7.0, 0.0, 15.0, -7.0, 7.0)


def model_encaixe():
    x0, x1, y0, y1, z0, z1 = CASE
    caixa = Mesh()
    box(caixa, (x0, y0, z0), (x1, y1, z1),
        {"_": "caixa", "+x": "caixa_eixo", "-x": "caixa_eixo", "+y": "caixa_eixo", "-z": "caixa_eixo"},
        long_u=False)
    # bica atrás, em baixo (calha curta de tábuas, sai 2 px para a célula de trás)
    box(caixa, (-2.5, 1.0, z1), (2.5, 1.8, 10.0), "bica", faces=("-x", "+x", "-y", "+y", "+z"))
    box(caixa, (-2.5, 1.8, z1), (-1.7, 3.8, 10.0), "bica", faces=("-x", "+x", "+y", "+z"))
    box(caixa, (1.7, 1.8, z1), (2.5, 3.8, 10.0), "bica", faces=("-x", "+x", "+y", "+z"))
    # pontas de eixo (encaixes) que giram: lados e cima
    lados, topo = Mesh(), Mesh()
    pont = {"_": "eixo", "+x": "eixo_ponta", "-x": "eixo_ponta", "+y": "eixo_ponta"}
    box(lados, (x1, 6.0, -2.0), (8.0, 10.0, 2.0), pont, faces=("+x", "-y", "+y", "-z", "+z"))
    box(lados, (-8.0, 6.0, -2.0), (x0, 10.0, 2.0), pont, faces=("-x", "-y", "+y", "-z", "+z"))
    box(topo, (-2.0, y1, -2.0), (2.0, 16.0, 2.0), pont, faces=("-x", "+x", "+y", "-z", "+z"))
    # a calha e o eixo que chegam do parafuso: só o que fica à frente da caixa
    cal = Mesh()
    trough(cal, -H, 0.0)
    cal = cal.transformed(to_block)
    cal = split_mesh(cal, (0, 0, 1), z0)[0].transformed(to_local)
    eix = rotor(-H - 1, 1.0)
    # cortado exatamente na junta (s = -H): a hélice gerada passa um pouco
    # das pontas e sobrepunha-se à do parafuso de baixo (z-fighting)
    eix = split_mesh(split_mesh(eix, (0, 1, 0), 0.0)[0], (0, 1, 0), -H)[1]
    eix_b = eix.transformed(to_block)
    eix = split_mesh(eix_b, (0, 0, 1), z0)[0].transformed(to_local)
    return [
        {"name": "Calha", "incl": True, "pivot": (0, 0, 0), "anim": None, "pieces": {"Calha_baixo": cal}},
        {"name": "Eixo_parafuso", "incl": True, "pivot": (0, 0, AXIS_Z), "anim": ("rot", "y"),
         "pieces": {"Eixo_parafuso_baixo": eix}},
        {"name": "Caixa", "incl": False, "pivot": (0, 8, 0), "anim": None, "pieces": {"Caixa": caixa}},
        {"name": "Pontas_lados", "incl": False, "pivot": (0, 8, 0), "anim": ("rot", "x"),
         "pieces": {"Pontas_lados_malha": lados}},
        {"name": "Pontas_topo", "incl": False, "pivot": (0, 8, 0), "anim": ("rot", "y"),
         "pieces": {"Pontas_topo_malha": topo}},
    ], {}


# ---------------------------------------------------------------- eixo e manivela
def _shaft(axis):
    m = Mesh()
    if axis == "y":
        box(m, (-2, 0, -2), (2, 16, 2), {"_": "eixo", "+y": "eixo_ponta", "-y": "eixo_ponta"})
    else:
        box(m, (-2, 6, -8), (2, 10, 8), {"_": "eixo", "+z": "eixo_ponta", "-z": "eixo_ponta"})
    return m


def model_eixo():
    return [{"name": "Eixo", "incl": False, "pivot": (0, 8, 0), "anim": ("rot", "y"),
             "pieces": {"Eixo_malha": _shaft("y")}}], {}


def model_eixo_deitado():
    return [{"name": "Eixo", "incl": False, "pivot": (0, 8, 0), "anim": ("rot", "z"),
             "pieces": {"Eixo_malha": _shaft("z")}}], {}


def _crank():
    base, roda = Mesh(), Mesh()
    box(base, (-4, 0, -4), (4, 1, 4), "base_ferro")
    box(base, (-1.5, 1, -1.5), (1.5, 3, 1.5), "ferro", faces=("-x", "+x", "-z", "+z"))
    box(roda, (-2, 3, -2), (2, 5, 2), {"_": "eixo", "+y": "eixo_ponta", "-y": "eixo_ponta"})
    # (braço, punho e pomo sem faces no mesmo plano umas das outras: piscavam)
    box(roda, (2, 3.5, -0.8), (5, 4.8, 0.8), "tabua_escura", faces=("-y", "+y", "-z", "+z"))
    box(roda, (5, 3.5, -1), (7, 10.5, 1), "punho", faces=("-x", "+x", "-y", "-z", "+z"))
    box(roda, (4.5, 10.5, -1.5), (7.5, 12, 1.5), "punho", faces=("-x", "+x", "+y", "-z", "+z", "-y"))
    return base, roda


def model_manivela():
    base, roda = _crank()
    return [{"name": "Base", "incl": False, "pivot": (0, 0, 0), "anim": None, "pieces": {"Base_malha": base}},
            {"name": "Manivela", "incl": False, "pivot": (0, 8, 0), "anim": ("rot", "y"),
             "pieces": {"Manivela_malha": roda}}], {}


def model_manivela_lado():
    # a de cima deitada: a base fica na face de trás (+Z), o punho para a frente
    f = lambda p: np.array([p[0], 8 + p[2], 8 - p[1]])
    base, roda = _crank()
    return [{"name": "Base", "incl": False, "pivot": (0, 0, 0), "anim": None,
             "pieces": {"Base_malha": base.transformed(f)}},
            {"name": "Manivela", "incl": False, "pivot": (0, 8, 0), "anim": ("rot", "z"),
             "pieces": {"Manivela_malha": roda.transformed(f)}}], {}


def model_isqueiro():
    """Isqueiro (item na mão): corpo vermelho, tampa/chaminé de aço e a roda."""
    m = Mesh()
    box(m, (-3, 0, -1.5), (3, 9, 1.5), "isq_corpo")
    box(m, (-3, 9, -1.5), (3, 11, 1.5), "isq_metal")
    box(m, (-2.5, 11, -1.2), (1.0, 14, 1.2), "isq_metal")      # chaminé
    box(m, (1.4, 11.4, -0.9), (3.0, 13.4, 0.9), "isq_roda")     # roda de pedra
    return [{"name": "Isqueiro", "incl": False, "pivot": (0, 0, 0), "anim": None,
             "pieces": {"Isqueiro_malha": m}}], {}


MODELS = {
    "parafuso_arquimedes": model_parafuso,
    "encaixe_parafuso": model_encaixe,
    "eixo": model_eixo,
    "eixo_deitado": model_eixo_deitado,
    "manivela": model_manivela,
    "manivela_lado": model_manivela_lado,
    "isqueiro": model_isqueiro,
}
CLIP, CLIP_LEN = "girar", 1.0
AXES = {"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}


# ---------------------------------------------------------------- triângulos
def triangles(mesh):
    out = []
    for P, U, mat, _sid in mesh.polys:
        if len(P) < 3:
            continue
        n = np.zeros(3)
        for i in range(len(P)):
            a, b = P[i], P[(i + 1) % len(P)]
            n += np.array([(a[1] - b[1]) * (a[2] + b[2]), (a[2] - b[2]) * (a[0] + b[0]),
                           (a[0] - b[0]) * (a[1] + b[1])])
        ln = np.linalg.norm(n)
        if ln < 1e-9:
            continue
        n /= ln
        for i in range(1, len(P) - 1):
            if np.linalg.norm(np.cross(P[i] - P[0], P[i + 1] - P[0])) < 2e-4:
                continue           # lasca sem área (de um corte): fora
            out.append(((P[0], P[i], P[i + 1]), n, (U[0], U[i], U[i + 1]), mat))
    return out


# ---------------------------------------------------------------- glTF
def _quat(axis, ang):
    s = math.sin(ang / 2)
    return [axis[0] * s, axis[1] * s, axis[2] * s, math.cos(ang / 2)]


def write_gltf(groups, path, png):
    nodes, meshes, accessors, views = [], [], [], []
    blob = bytearray()

    def add_view(data, target=None):
        while len(blob) % 4:
            blob.append(0)
        off = len(blob)
        blob.extend(data)
        v = {"buffer": 0, "byteOffset": off, "byteLength": len(data)}
        if target:
            v["target"] = target
        views.append(v)
        return len(views) - 1

    def add_acc(arr, ctype, typ, target=None, minmax=False):
        arr = np.asarray(arr, dtype=np.float32 if ctype == 5126 else np.uint32)
        a = {"bufferView": add_view(arr.tobytes(), target), "componentType": ctype,
             "count": int(arr.shape[0]), "type": typ}
        if minmax:
            flat = arr.reshape(arr.shape[0], -1)
            a["min"] = [float(v) for v in flat.min(0)]
            a["max"] = [float(v) for v in flat.max(0)]
        accessors.append(a)
        return len(accessors) - 1

    def mesh_of(mesh, pivot):
        tris = triangles(mesh)
        prims = []
        for mat_idx, water in ((0, False), (1, True)):
            sel = [t for t in tris if (t[3] == "agua") == water]
            if not sel:
                continue
            pos, nor, uv = [], [], []
            for pts, n, uvs, mat in sel:
                for p, t in zip(pts, uvs):
                    pos.append((p - np.array(pivot)) / 16.0)
                    nor.append(n)
                    uv.append(padded_uv(t, mat))
            pos = np.array(pos)
            prims.append({"attributes": {
                "POSITION": add_acc(pos, 5126, "VEC3", 34962, True),
                "NORMAL": add_acc(np.array(nor), 5126, "VEC3", 34962),
                "TEXCOORD_0": add_acc(np.array(uv), 5126, "VEC2", 34962)},
                "indices": add_acc(np.arange(len(pos)), 5125, "SCALAR", 34963), "material": mat_idx})
        if not prims:
            return None
        meshes.append({"primitives": prims})
        return len(meshes) - 1

    nodes.append({"name": "raiz", "children": []})
    incl_idx = None
    if any(g["incl"] for g in groups):
        nodes.append({"name": "Inclinacao", "translation": [0, 0.5, 0],
                      "rotation": _quat((1, 0, 0), math.radians(45)), "children": []})
        incl_idx = len(nodes) - 1
        nodes[0]["children"].append(incl_idx)
    animated = []
    for g in groups:
        pv = np.array(g["pivot"], float)
        node = {"name": g["name"], "translation": list(pv / 16.0), "children": []}
        nodes.append(node)
        gi = len(nodes) - 1
        nodes[incl_idx if g["incl"] else 0]["children"].append(gi)
        for pname, mesh in g["pieces"].items():
            mi = mesh_of(mesh, pv)
            if mi is not None:
                nodes.append({"name": pname, "mesh": mi})
                node["children"].append(len(nodes) - 1)
        if g["anim"]:
            animated.append((gi, g["anim"], pv))
    times = np.linspace(0, CLIP_LEN, 61)
    t_acc = add_acc(times.reshape(-1, 1), 5126, "SCALAR", minmax=True)
    channels, samplers = [], []
    for ni, (kind, arg), pv in animated:
        if kind == "sobe":
            base_t = pv / 16.0
            vals = [[base_t[0], base_t[1] + arg / 16.0 * t / CLIP_LEN, base_t[2]] for t in times]
            out = add_acc(np.array(vals), 5126, "VEC3")
            path_ = "translation"
        else:
            vals = [_quat(AXES[arg], a) for a in times / CLIP_LEN * 2 * math.pi]
            out = add_acc(np.array(vals), 5126, "VEC4")
            path_ = "rotation"
        samplers.append({"input": t_acc, "output": out, "interpolation": "LINEAR"})
        channels.append({"sampler": len(samplers) - 1, "target": {"node": ni, "path": path_}})
    img_view = add_view(padded_atlas_png(png))
    doc = {
        "asset": {"version": "2.0", "generator": "jogo tools/torque_modelos.py"},
        "scene": 0, "scenes": [{"nodes": [0]}], "nodes": nodes, "meshes": meshes,
        "materials": [
            {"name": "madeira", "pbrMetallicRoughness": {"baseColorTexture": {"index": 0},
                                                         "metallicFactor": 0, "roughnessFactor": 1},
             "alphaMode": "MASK", "alphaCutoff": 0.05, "doubleSided": False},
            {"name": "agua", "pbrMetallicRoughness": {"baseColorTexture": {"index": 0},
                                                      "metallicFactor": 0, "roughnessFactor": 0.2},
             "alphaMode": "BLEND", "doubleSided": False}],
        "textures": [{"source": 0, "sampler": 0}],
        "samplers": [{"magFilter": 9728, "minFilter": 9987, "wrapS": 33071, "wrapT": 33071}],
        "images": [{"bufferView": img_view, "mimeType": "image/png", "name": "atlas"}],
        "animations": [{"name": CLIP, "channels": channels, "samplers": samplers}],
        "accessors": accessors, "bufferViews": views,
        "buffers": [{"byteLength": len(blob),
                     "uri": "data:application/octet-stream;base64," + base64.b64encode(bytes(blob)).decode()}],
    }
    path.write_text(json.dumps(doc), encoding="utf-8")


# ---------------------------------------------------------------- Blockbench
def write_bbmodel(groups, path, png, name):
    U_ = lambda: str(_uuid.uuid4())
    elements, bgroups, outliner, anim = [], [], [], {}
    incl = None
    if any(g["incl"] for g in groups):
        incl = {"uuid": U_(), "isOpen": True, "children": []}
        bgroups.append({"name": "Inclinacao", "origin": [0, 8, 0], "rotation": [45, 0, 0], "uuid": incl["uuid"],
                        "export": True, "visibility": True, "autouv": 0, "color": 0, "isOpen": True})
        outliner.append(incl)
    for g in groups:
        pv = np.array(g["pivot"], float)
        off = np.array([0, 8, 0]) if g["incl"] else np.zeros(3)
        gu = U_()
        bgroups.append({"name": g["name"], "origin": [float(v) for v in pv + off], "rotation": [0, 0, 0],
                        "uuid": gu, "export": True, "visibility": True, "autouv": 0, "color": 1, "isOpen": True})
        node = {"uuid": gu, "isOpen": True, "children": []}
        (incl["children"] if g["incl"] else outliner).append(node)
        for pname, mesh in g["pieces"].items():
            if not mesh.polys:
                continue
            verts, faces, vid = {}, {}, {}
            for P, Uv, mat, _sid in mesh.polys:
                keys = []
                for p in P:
                    q = tuple(round(float(c), 4) for c in (p + off))
                    if q not in vid:
                        vid[q] = f"v{len(vid):04x}"
                        verts[vid[q]] = list(q)
                    keys.append(vid[q])
                if len(set(keys)) < 3 or len(keys) > 4:
                    # o Blockbench só aceita tri/quad: polígonos maiores em leque
                    for i in range(1, len(keys) - 1):
                        tri = [keys[0], keys[i], keys[i + 1]]
                        if len(set(tri)) == 3:
                            faces[f"f{len(faces):04x}"] = {
                                "uv": {k: [round(float(t[0]) / 4, 3), round(float(t[1]) / 4, 3)]
                                       for k, t in zip(tri, (Uv[0], Uv[i], Uv[i + 1]))},
                                "vertices": tri, "texture": 0}
                    continue
                faces[f"f{len(faces):04x}"] = {"uv": {k: [round(float(t[0]) / 4, 3), round(float(t[1]) / 4, 3)]
                                                      for k, t in zip(keys, Uv)},
                                               "vertices": keys, "texture": 0}
            eu = U_()
            elements.append({"name": pname, "type": "mesh", "uuid": eu, "origin": [0, 0, 0],
                             "rotation": [0, 0, 0], "export": True, "visibility": True, "locked": False,
                             "render_order": "default", "allow_mirror_modeling": True,
                             "vertices": verts, "faces": faces, "color": 0})
            node["children"].append(eu)
        if g["anim"]:
            kind, arg = g["anim"]
            kfs = []
            for t in (0, 0.25, 0.5, 0.75, 1.0):
                if kind == "rot":
                    dp = {"x": "0", "y": "0", "z": "0"}
                    dp[arg] = str(round(360 * t, 3))
                    ch = "rotation"
                else:
                    dp = {"x": "0", "y": str(round(arg * t, 4)), "z": "0"}
                    ch = "position"
                kfs.append({"channel": ch, "data_points": [dp], "uuid": U_(), "time": t,
                            "color": -1, "interpolation": "linear"})
            anim[gu] = {"name": g["name"], "type": "bone", "keyframes": kfs}
    doc = {
        "meta": {"format_version": "4.10", "model_format": "free", "box_uv": False},
        "name": name, "model_identifier": "", "visible_box": [1, 1, 0],
        "variable_placeholders": "", "variable_placeholder_buttons": [],
        "timeline_setups": [], "unhandled_root_fields": {},
        "resolution": {"width": 16, "height": 16},
        "elements": elements, "groups": bgroups, "outliner": outliner,
        "textures": [{"path": "", "name": "atlas.png", "folder": "", "namespace": "", "id": "0",
                      "width": 64, "height": 64, "uv_width": 16, "uv_height": 16,
                      "particle": False, "render_mode": "default", "render_sides": "auto",
                      "frame_time": 1, "frame_order_type": "loop", "frame_order": "",
                      "frame_interpolate": False, "visible": True, "internal": True, "saved": True,
                      "uuid": U_(), "source": "data:image/png;base64," + base64.b64encode(png).decode()}],
        "animations": [{"uuid": U_(), "name": CLIP, "loop": "loop", "override": False,
                        "length": CLIP_LEN, "snapping": 24, "selected": False,
                        "anim_time_update": "", "blend_weight": "", "start_delay": "", "loop_delay": "",
                        "animators": anim}],
    }
    path.write_text(json.dumps(doc, indent=1), encoding="utf-8")


# ---------------------------------------------------------------- vista (ícones / prévias)
def alone(name):
    """Peças visíveis num bloco sozinho (e na mão): sem juntas, com tampas, sem água."""
    if "_tampa_" in name:
        return True
    return not (name.endswith("_baixo") or name.endswith("_cima"))


def world_tris(groups, show=alone,
               water=False, phase=0.0):
    """Triângulos no espaço do bloco (px), com a pose da fase."""
    out = []
    for g in groups:
        for pname, mesh in g["pieces"].items():
            if not show(pname):
                continue
            if pname.startswith("Bolsa") and not water:
                continue
            m = mesh
            if g["anim"] and phase:
                kind, arg = g["anim"]
                a = phase * 2 * math.pi
                pv = np.array(g["pivot"], float)
                if kind == "sobe":
                    m = m.transformed(lambda q: q + np.array([0, arg * phase, 0]))
                elif g["incl"]:
                    m = rot_about_axis(m, a)
            if g["incl"]:
                m = m.transformed(to_block)
            out += [(pts, n, uvs, mat) for pts, n, uvs, mat in triangles(m)]
    return out


def write_icon(groups, path):
    from PIL import Image, ImageDraw
    tris = world_tris(groups)
    yaw, pitch = math.radians(-35), math.radians(28)
    Ry = np.array([[math.cos(yaw), 0, math.sin(yaw)], [0, 1, 0], [-math.sin(yaw), 0, math.cos(yaw)]])
    Rx = np.array([[1, 0, 0], [0, math.cos(pitch), -math.sin(pitch)], [0, math.sin(pitch), math.cos(pitch)]])
    R = Rx @ Ry
    light = np.array([0.4, 0.8, -0.45])
    light /= np.linalg.norm(light)
    S = 4
    img = Image.new("RGBA", (64 * S, 64 * S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    proj = []
    for (a, b, c), n, _uv, mat in tris:
        P = [R @ (p - np.array([0, 8, 0])) for p in (a, b, c)]
        shade = 0.55 + 0.45 * abs(float((R @ n) @ (R @ light)))
        col = tuple(int(min(255, v * shade)) for v in AVG[mat]) + (255,)
        proj.append((np.mean([p[2] for p in P]), [(32 * S + p[0] * 3.0 * S, 32 * S - p[1] * 3.0 * S) for p in P], col))
    for _z, pts, col in sorted(proj, key=lambda t: -t[0]):
        d.polygon(pts, fill=col)
    img.resize((64, 64), Image.LANCZOS).save(path)


def main():
    png = atlas_png()
    out_models = ROOT / "assets" / "models" / "produced_blocks"
    out_src = FONTES / "assets" / "models" / "source"
    out_icons = ROOT / "assets" / "icons"
    out_tools = ROOT / "assets" / "models" / "ferramentas"
    for name, fn in MODELS.items():
        groups, info = fn()
        if name == "isqueiro":                      # item (ferramenta), não bloco
            (out_tools / "isqueiro").mkdir(parents=True, exist_ok=True)
            write_gltf(groups, out_tools / "isqueiro" / "isqueiro.gltf", png)
            write_icon(groups, out_icons / "isqueiro.png")
            continue
        write_gltf(groups, out_models / f"{name}.gltf", png)
        write_bbmodel(groups, out_src / f"{name}.bbmodel", png, name)
        if name in ("parafuso_arquimedes", "encaixe_parafuso", "eixo", "manivela"):
            write_icon(groups, out_icons / f"{name}.png")
        pts = np.vstack([np.array(t[0]) for t in world_tris(groups)])
        extra = f"  ({', '.join(f'{k}={v:.2f}' for k, v in info.items())})" if info else ""
        print(f"{name:20s} sozinho: x {pts[:,0].min():6.2f}..{pts[:,0].max():6.2f}  "
              f"y {pts[:,1].min():6.2f}..{pts[:,1].max():6.2f}  z {pts[:,2].min():6.2f}..{pts[:,2].max():6.2f}{extra}")


if __name__ == "__main__":
    main()
