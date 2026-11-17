"""Gera a flor lunar (assets/models/produced_blocks/flor_lunar.gltf + ícone).

Flor pequena que brilha de noite (bloco "flor lunar", ``"luz"`` em
blocks.json): caules verde-escuros e botões redondos azul-pálido / branco,
16x16 em pixel art, nos mesmos 3 planos cruzados do capim
(tools/gerar_capim.py).
Uso: python tools/gerar_flor_lunar.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gerar_capim as base  # noqa: E402

ROOT = base.ROOT
STEM = [(34, 72, 40), (44, 90, 50), (56, 108, 58)]
GLOW = [(150, 210, 235), (190, 235, 250), (235, 252, 255)]


def textura():
    im = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    px = im.load()
    # (x, altura do caule, desvio da flor)
    stems = [(3, 6, -1), (7, 10, 0), (11, 8, 1), (13, 4, 0), (5, 3, 0)]
    for bx, h, lean in stems:
        for i in range(h):
            x = bx + (lean if i > h * 0.6 else 0)
            px[x, 15 - i] = STEM[min(2, i * 3 // max(1, h))] + (255,)
        # botão 3x3 com o centro mais claro (se o caule for alto)
        if h >= 6:
            cx, cy = bx + lean, 15 - h
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    if abs(dx) + abs(dy) == 2:
                        continue
                    x, y = cx + dx, cy + dy
                    if 0 <= x < 16 and 0 <= y < 16:
                        px[x, y] = (GLOW[2] if dx == dy == 0 else GLOW[1 if dy < 0 else 0]) + (255,)
        else:
            px[bx, 15 - h] = GLOW[1] + (255,)
    # folhinhas na base
    for x, y in ((2, 15), (4, 14), (8, 15), (9, 14), (12, 15), (14, 14)):
        px[x, y] = STEM[1] + (255,)
    return im


if __name__ == "__main__":
    base.HEIGHT = 0.6
    im = textura()
    g = base.gltf(im)
    g["asset"]["generator"] = "tools/gerar_flor_lunar.py"
    g["nodes"][0]["name"] = "flor_lunar"
    out = ROOT / "assets" / "models" / "produced_blocks" / "flor_lunar.gltf"
    out.write_text(json.dumps(g, separators=(",", ":")))
    im.resize((64, 64), Image.NEAREST).save(ROOT / "assets" / "icons" / "flor_lunar.png")
    print("flor_lunar.gltf + icons/flor_lunar.png")
