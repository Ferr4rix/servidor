"""Gera o capim (assets/models/produced_blocks/capim.gltf) e o ícone.

Parte do primeiro capim (savana, ``assets/models/fontes/savannah-grass.bbmodel``,
convertido por ``tools/bbmodel_para_gltf.py`` — folhas em planos, leve para
desenhar) e **repinta a textura** com os verdes da grama do jogo
(grama_topo.png): cada pixel fica com o verde do mesmo brilho, do escuro (pé)
ao claro (pontas). Fica com a forma do primeiro, mas com as cores do mundo.
Uso: python tools/gerar_capim.py
"""
from __future__ import annotations

import base64
import io
import json
import subprocess
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from caminhos import CLIENTE as ROOT, FONTES, TOOLS  # noqa: E402
SRC = FONTES / "assets/models/fontes/savannah-grass.bbmodel"
OUT = ROOT / "assets/models/produced_blocks/capim.gltf"
PAL = [(52, 96, 30), (66, 118, 36), (82, 140, 44), (94, 157, 52), (106, 170, 60),
       (118, 182, 70), (140, 200, 86)]


def recolor(im):
    im = im.convert("RGBA")
    px = im.load()
    lum = [(0.3 * r + 0.59 * g + 0.11 * b) for r, g, b, a in im.getdata() if a > 127]
    lo, hi = (min(lum), max(lum)) if lum else (0, 255)
    for y in range(im.height):
        for x in range(im.width):
            r, g, b, a = px[x, y]
            if a <= 127:
                continue
            t = ((0.3 * r + 0.59 * g + 0.11 * b) - lo) / max(1.0, hi - lo)
            c = PAL[min(len(PAL) - 1, int(t * len(PAL)))]
            px[x, y] = c + (255,)
    return im


if __name__ == "__main__":
    subprocess.run([sys.executable, str(TOOLS / "bbmodel_para_gltf.py"), str(SRC), str(OUT),
                    "--escala-h", "0.3333", "--escala-v", "0.62"], check=True)
    g = json.loads(OUT.read_text())
    head, data = g["images"][0]["uri"].split(",", 1)
    im = recolor(Image.open(io.BytesIO(base64.b64decode(data))))
    b = io.BytesIO()
    im.save(b, "PNG")
    g["images"][0]["uri"] = head + "," + base64.b64encode(b.getvalue()).decode()
    OUT.write_text(json.dumps(g, separators=(",", ":")))
    # ícone: o meio da textura (as folhas), em 64x64
    bbox = im.getbbox() or (0, 0, im.width, im.height)
    crop = im.crop(bbox)
    side = max(crop.size)
    icon = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    icon.paste(crop, ((side - crop.width) // 2, side - crop.height))
    icon.resize((64, 64), Image.NEAREST).save(ROOT / "assets/icons/capim.png")
    print("capim.gltf (forma do 1º, cores da grama) + ícone")
