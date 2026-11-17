"""Mini sol: um .bbmodel bem simples (Blockbench) e o .gltf do jogo.

Núcleo de 4x4x4 px amarelo, uma "casca" de 6 px laranja-clara meio
afundada (o brilho) e 8 raios curtinhos nas diagonais de cima/baixo e 4 nos
lados. Textura 16x16 com 4 cores. Escreve:
  assets/models/fontes/mini_sol.bbmodel     (abre no Blockbench)
  assets/models/produced_blocks/mini_sol.gltf (via tools/bbmodel_para_gltf.py)
  assets/icons/mini_sol.png
O modelo fica a flutuar a meio da célula (nó com translação +0.3).
Uso: python tools/gerar_mini_sol.py
"""
from __future__ import annotations

import base64
import io
import json
import subprocess
import sys
import uuid
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from caminhos import CLIENTE as ROOT, FONTES, TOOLS  # noqa: E402

# cor -> canto (u, v) de um quadrado 4x4 na textura 16x16
COLORS = {"nucleo": ((255, 236, 120), (0, 0)), "brilho": ((255, 200, 70), (4, 0)),
          "raio": ((255, 160, 40), (8, 0)), "centro": ((255, 252, 220), (12, 0))}


def texture():
    im = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    for col, (u, v) in COLORS.values():
        for x in range(u, u + 4):
            for y in range(v, v + 4):
                im.putpixel((x, y), col + (255,))
    b = io.BytesIO()
    im.save(b, "PNG")
    return im, "data:image/png;base64," + base64.b64encode(b.getvalue()).decode()


def cube_mesh(name, lo, hi, color):
    """Elemento "mesh" do Blockbench com a forma de uma caixa (px)."""
    u, v = COLORS[color][1]
    uv = [u + 1, v + 1, u + 3, v + 3]
    vs = {}
    for i, (x, y, z) in enumerate([(a, b, c) for a in (lo[0], hi[0]) for b in (lo[1], hi[1]) for c in (lo[2], hi[2])]):
        vs[f"v{i}"] = [x, y, z]
    # índices: bit2=x, bit1=y, bit0=z
    faces_ids = {"e": [4, 6, 7, 5], "w": [1, 3, 2, 0], "u": [2, 3, 7, 6],
                 "d": [1, 0, 4, 5], "s": [5, 7, 3, 1], "n": [0, 2, 6, 4]}
    faces = {}
    for k, ids in faces_ids.items():
        names = [f"v{i}" for i in ids]
        faces[k] = {"vertices": names, "texture": 0,
                    "uv": {names[0]: [uv[0], uv[3]], names[1]: [uv[0], uv[1]],
                           names[2]: [uv[2], uv[1]], names[3]: [uv[2], uv[3]]}}
    return {"name": name, "type": "mesh", "uuid": str(uuid.uuid4()), "origin": [0, 0, 0],
            "rotation": [0, 0, 0], "export": True, "vertices": vs, "faces": faces}


def elements():
    el = [cube_mesh("nucleo", (-2, -2, -2), (2, 2, 2), "nucleo"),
          cube_mesh("centro", (-1, -1, -2.05), (1, 1, 2.05), "centro"),
          cube_mesh("brilho", (-2.6, -1.4, -1.4), (2.6, 1.4, 1.4), "brilho"),
          cube_mesh("brilho2", (-1.4, -2.6, -1.4), (1.4, 2.6, 1.4), "brilho"),
          cube_mesh("brilho3", (-1.4, -1.4, -2.6), (1.4, 1.4, 2.6), "brilho")]
    rays = [((3, 0, 0), (4.5, 0.5, 0.5)), ((-4.5, 0, 0), (-3, 0.5, 0.5)),
            ((0, 0, 3), (0.5, 0.5, 4.5)), ((0, 0, -4.5), (0.5, 0.5, -3)),
            ((0, 3, 0), (0.5, 4.5, 0.5)), ((0, -4.5, 0), (0.5, -3, 0.5))]
    for i, (a, b) in enumerate(rays):
        lo = [min(a[k], b[k]) - (0.25 if a[k] == 0 and b[k] == 0.5 else 0) for k in range(3)]
        hi = [max(a[k], b[k]) - (0.25 if a[k] == 0 and b[k] == 0.5 else 0) for k in range(3)]
        el.append(cube_mesh(f"raio_{i}", lo, hi, "raio"))
    d = 2.6
    for i, (sx, sy, sz) in enumerate([(1, 1, 1), (-1, 1, 1), (1, 1, -1), (-1, 1, -1),
                                      (1, -1, 1), (-1, -1, 1), (1, -1, -1), (-1, -1, -1)]):
        c = (sx * d, sy * d, sz * d)
        el.append(cube_mesh(f"faisca_{i}", [c[k] - 0.4 for k in range(3)], [c[k] + 0.4 for k in range(3)], "raio"))
    return el


if __name__ == "__main__":
    im, src = texture()
    bb = {"meta": {"format_version": "4.10", "model_format": "free", "box_uv": False},
          "name": "mini_sol", "resolution": {"width": 16, "height": 16},
          "elements": elements(), "outliner": [],
          "textures": [{"name": "mini_sol.png", "id": "0", "uv_width": 16, "uv_height": 16,
                        "source": src, "uuid": str(uuid.uuid4())}]}
    bb["outliner"] = [e["uuid"] for e in bb["elements"]]
    fonte = FONTES / "assets/models/fontes/mini_sol.bbmodel"
    fonte.parent.mkdir(parents=True, exist_ok=True)
    fonte.write_text(json.dumps(bb, indent=1))
    out = ROOT / "assets/models/produced_blocks/mini_sol.gltf"
    subprocess.run([sys.executable, str(TOOLS / "bbmodel_para_gltf.py"), str(fonte), str(out)], check=True)
    g = json.loads(out.read_text())
    g["nodes"][0]["translation"] = [0, 0.3, 0]          # flutua a meio da célula
    out.write_text(json.dumps(g, separators=(",", ":")))
    icon = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    for x in range(16):
        for y in range(16):
            d2 = (x - 7.5) ** 2 + (y - 7.5) ** 2
            if d2 < 9: icon.putpixel((x, y), COLORS["centro"][0] + (255,))
            elif d2 < 20: icon.putpixel((x, y), COLORS["nucleo"][0] + (255,))
            elif d2 < 30 and (x in (7, 8) or y in (7, 8) or abs(x - y) < 1 or abs(x + y - 15) < 1):
                icon.putpixel((x, y), COLORS["raio"][0] + (255,))
    icon.resize((64, 64), Image.NEAREST).save(ROOT / "assets/icons/mini_sol.png")
    print("mini_sol.bbmodel + mini_sol.gltf + ícone")
