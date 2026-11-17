"""Gera a fada (assets/models/mobs/fada/fada.gltf): mob pequeno, de cubinhos.

Fada "duende": cabeça grande com orelhas pontudas e cabelo rosa preso num
coque, olhos grandes, vestido violeta com saia em degraus, perninhas, e
**4 asas de borboleta** (2 grandes em cima, 2 pequenas em baixo) com borda
mais escura e uma pinta clara. Um rasto de 3 faíscas atrás.
Nós: point > body > [wing_ul, wing_ur, wing_ll, wing_lr, trail]. Frente = -Z.
Animações: idle (1 s), walk (0.5 s), attack (0.6 s: recua e dá uma
investida para a frente), death (0.8 s: roda e encolhe).
Uso: python tools/gerar_fada.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from voxel_gltf import Builder  # noqa: E402

from caminhos import CLIENTE as ROOT  # noqa: E402
P = 1 / 16
SKIN, HAIR, HAIR2 = (255, 222, 200), (255, 130, 190), (230, 90, 160)
DRESS, DRESS2 = (150, 100, 230), (115, 70, 200)
EYE, EYE2 = (40, 30, 80), (255, 255, 255)
WING, WEDGE, WDOT = (255, 190, 235), (200, 110, 220), (255, 250, 210)
SPARK = (255, 245, 170)


def box(x0, y0, z0, x1, y1, z1, c):
    return ((x0 * P, y0 * P, z0 * P), (x1 * P, y1 * P, z1 * P), c)


def q(axis, deg):
    a = math.radians(deg) / 2
    s = math.sin(a)
    return [s * axis[0], s * axis[1], s * axis[2], math.cos(a)]


def qmul(a, b):
    ax, ay, az, aw = a; bx, by, bz, bw = b
    return [aw * bx + ax * bw + ay * bz - az * by, aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw, aw * bw - ax * bx - ay * by - az * bz]


def wing(side, big):
    """Asa fina (em x, y) com borda e pinta; side=+1 esquerda (+X)."""
    w, h = (6, 5) if big else (4, 3)
    y0 = 0 if big else -h
    out = []
    for i in range(w):
        for j in range(h):
            edge = i == w - 1 or j == (h - 1 if big else 0) or (i == w - 2 and j == (h - 2 if big else 1))
            dot = big and i == w // 2 and j == h // 2
            c = WDOT if dot else (WEDGE if edge else WING)
            # canto arredondado
            if (i == w - 1 and (j == 0 or j == h - 1)):
                continue
            x0 = i if side > 0 else -i - 1
            out.append(box(x0, y0 + j, -0.15, x0 + 1, y0 + j + 1, 0.15, c))
    return out


if __name__ == "__main__":
    b = Builder()
    point = b.part("point", [])
    body = b.part("body", [
        box(-0.6, -2, -0.4, -0.1, 0, 0.4, SKIN), box(0.1, -2, -0.4, 0.6, 0, 0.4, SKIN),   # pernas
        box(-2, 0, -1.5, 2, 1, 1.5, DRESS2), box(-1.5, 1, -1.2, 1.5, 2, 1.2, DRESS),      # saia
        box(-1, 2, -0.8, 1, 4, 0.8, DRESS),                                              # tronco
        box(-1.6, 2.4, -0.4, -1, 3.8, 0.4, SKIN), box(1, 2.4, -0.4, 1.6, 3.8, 0.4, SKIN),  # braços
        box(-2, 4, -2, 2, 8, 2, SKIN),                                                   # cabeça
        box(-2.2, 7, -2.2, 2.2, 8.6, 2.2, HAIR), box(-2.2, 4.5, 0.8, 2.2, 8, 2.2, HAIR),  # cabelo
        box(-2.2, 6, -2.2, -1.4, 7, -1.4, HAIR), box(1.4, 6, -2.2, 2.2, 7, -1.4, HAIR),   # franja
        box(-1, 8.6, 0, 1, 10, 2, HAIR2),                                                # coque
        box(-3, 5.5, -0.5, -2, 6.5, 0.5, SKIN), box(2, 5.5, -0.5, 3, 6.5, 0.5, SKIN),    # orelhas
        box(-3.6, 6.3, -0.3, -2.8, 7.2, 0.3, SKIN), box(2.8, 6.3, -0.3, 3.6, 7.2, 0.3, SKIN),
        box(-1.6, 5, -2.05, -0.4, 6.4, -1.95, EYE), box(0.4, 5, -2.05, 1.6, 6.4, -1.95, EYE),
        box(-1.3, 5.9, -2.1, -0.9, 6.3, -2.0, EYE2), box(0.7, 5.9, -2.1, 1.1, 6.3, -2.0, EYE2),
    ], parent=point, translation=(0, 0.3, 0))
    wul = b.part("wing_ul", wing(+1, True), parent=body, translation=(0.8 * P, 3 * P, 1 * P))
    wur = b.part("wing_ur", wing(-1, True), parent=body, translation=(-0.8 * P, 3 * P, 1 * P))
    wll = b.part("wing_ll", wing(+1, False), parent=body, translation=(0.8 * P, 3 * P, 1 * P))
    wlr = b.part("wing_lr", wing(-1, False), parent=body, translation=(-0.8 * P, 3 * P, 1 * P))
    trail = b.part("trail", [box(-0.5, -0.5, 0, 0.5, 0.5, 1, SPARK), box(-0.4, -1.8, 1.8, 0.4, -1, 2.6, SPARK),
                             box(-0.3, -3, 3.4, 0.3, -2.4, 4, SPARK)], parent=body, translation=(0, -1 * P, 1.5 * P))
    wings = [(wul, 1, 30), (wur, -1, 30), (wll, 1, 18), (wlr, -1, 18)]

    def tracks(ts, length, flaps, bob, tilt, fwd=None):
        out = []
        for node, side, amp in wings:
            vals = []
            for t in ts:
                v = math.sin(2 * math.pi * flaps * t / length)
                vals.append(qmul(q((0, 1, 0), side * (-30 - amp * v)), q((0, 0, 1), side * (12 if amp == 30 else -15))))
            out.append((node, "rotation", vals))
        out.append((body, "translation", [[0, 0.3 + bob * math.sin(2 * math.pi * t / length),
                                           (fwd(t) if fwd else 0)] for t in ts]))
        out.append((body, "rotation", [q((1, 0, 0), -(tilt(t) if callable(tilt) else tilt)) for t in ts]))
        out.append((trail, "scale", [[1, 1, 0.6 + 0.4 * abs(math.sin(2 * math.pi * 3 * t / length))] for t in ts]))
        return out

    def times(length):
        n = int(length * 24)
        return [length * i / n for i in range(n + 1)]

    b.animation("idle", times(1.0), tracks(times(1.0), 1.0, 5, 0.07, 0))
    b.animation("walk", times(0.5), tracks(times(0.5), 0.5, 4, 0.03, 20))
    # ataque: recua (0-0.25 s) e investe (0.25-0.4 s), volta
    def lunge(t):
        if t < 0.25:
            return 0.12 * (t / 0.25)
        if t < 0.4:
            return 0.12 - 0.42 * ((t - 0.25) / 0.15)
        return -0.3 * (1 - (t - 0.4) / 0.2)
    b.animation("attack", times(0.6), tracks(times(0.6), 0.6, 6, 0.0,
                                               lambda t: 10 if t < 0.25 else 35, fwd=lunge))
    ts = [i / 24 * 0.8 for i in range(20)]
    b.animation("death", ts, [(body, "rotation", [q((0, 1, 0), 720 * t / 0.8) for t in ts]),
                              (body, "scale", [[max(0.0, 1 - t / 0.8)] * 3 for t in ts]),
                              (body, "translation", [[0, 0.3 + 0.3 * t, 0] for t in ts])])
    (ROOT / "assets/models/mobs/fada/fada.gltf").write_text(b.gltf("tools/gerar_fada.py"))
    print("fada.gltf")
