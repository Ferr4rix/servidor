"""Converte o .bbmodel de uma arma/escudo num .gltf pronto para a mão do jogador
(e, opcionalmente, gera o ícone 64x64 do inventário).

    python tools/arma_para_jogo.py arma.bbmodel saida.gltf [--pega 0.1]
        [--ponta 1,1,0] [--escudo] [--frente 0,0,1] [--icone icone.png]

O que faz:

1. texturas animadas (várias imagens empilhadas, ex.: 64x384 = 6 quadros)
   ficam só com o 1º quadro;
2. converte com ``tools/bbmodel_to_gltf.py`` (cubos, malhas, rotações);
3. **endireita** a arma: o eixo mais comprido do modelo (PCA da superfície;
   muitos modelos "java" vêm desenhados na diagonal) passa a ser +Y, com a
   ponta/cabeça para cima (``--ponta``: direção aproximada da ponta no modelo
   original; padrão +Y), a largura em +Z e a espessura em +X;
4. põe a **pega** na origem: o ponto do eixo a ``--pega`` (fração do
   comprimento, a partir do cabo) e no meio do cabo. É a origem que fica na
   mão do jogador (``held_item.build_on_bone``);
5. escudo (``--escudo``): a espessura vai para X com a face da frente
   (``--frente``: direção da face decorada no modelo original; padrão +Z) para
   -X, o cima para +Y, e o centro na origem — o que ``itens.json`` →
   ``"escudo": {"face": "-x", "cima": "z"}`` espera (Y do glTF = Z do Panda).
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import struct
import sys
import tempfile
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from bbmodel_to_gltf import convert  # noqa: E402


# ------------------------------------------------------------------ texturas
def _first_frame(src: str) -> str:
    """data URI de uma textura animada (quadros empilhados) -> só o 1º quadro."""
    from PIL import Image
    head, raw = src.split(",", 1)
    im = Image.open(io.BytesIO(base64.b64decode(raw)))
    w, h = im.size
    if h > w and h % w == 0:
        im = im.crop((0, 0, w, w))
        out = io.BytesIO()
        im.save(out, format="PNG")
        return "data:image/png;base64," + base64.b64encode(out.getvalue()).decode()
    return src


def _prepare(bbmodel: Path) -> Path:
    d = json.loads(bbmodel.read_text(encoding="utf-8"))
    for t in d.get("textures", []):
        src = str(t.get("source") or "")
        w, h = int(t.get("width") or 0), int(t.get("height") or 0)
        if src.startswith("data:") and w and h > w and h % w == 0:
            t["source"] = _first_frame(src)
            t["height"] = w
            print(f"  textura animada {t.get('name')!r}: {w}x{h} -> 1º quadro {w}x{w}")
    tmp = Path(tempfile.mkdtemp()) / bbmodel.name
    tmp.write_text(json.dumps(d), encoding="utf-8")
    return tmp


# ------------------------------------------------------------------ glTF
class _Doc:
    def __init__(self, path: Path):
        self.d = json.loads(path.read_text(encoding="utf-8"))
        self.buf = base64.b64decode(self.d["buffers"][0]["uri"].split(",", 1)[1])
        self.parent = {}
        for i, n in enumerate(self.d["nodes"]):
            for c in n.get("children", []):
                self.parent[c] = i

    def acc(self, i):
        a = self.d["accessors"][i]
        bv = self.d["bufferViews"][a["bufferView"]]
        n = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}[a["type"]]
        ch = {5126: "f", 5125: "I", 5123: "H", 5121: "B"}[a.get("componentType", 5126)]
        off = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
        return np.array(struct.unpack_from("<" + ch * n * a["count"], self.buf, off)).reshape(a["count"], n)

    def world(self, i):
        n = self.d["nodes"][i]
        t = np.array(n.get("translation", [0, 0, 0]), float)
        x, y, z, w = n.get("rotation", [0, 0, 0, 1])
        R = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                      [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                      [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
        M = np.eye(4)
        M[:3, :3] = R * np.array(n.get("scale", [1, 1, 1]), float)
        M[:3, 3] = t
        return self.world(self.parent[i]) @ M if i in self.parent else M

    def triangles(self):
        out = []
        for i, n in enumerate(self.d["nodes"]):
            if "mesh" not in n:
                continue
            M = self.world(i)
            for pr in self.d["meshes"][n["mesh"]]["primitives"]:
                P = self.acc(pr["attributes"]["POSITION"])
                P = (M[:3, :3] @ P.T).T + M[:3, 3]
                idx = self.acc(pr["indices"])[:, 0].astype(int).reshape(-1, 3)
                out.append(P[idx])
        return np.concatenate(out) if out else np.zeros((0, 3, 3))


def _surface_samples(tris, n=20000, seed=7):
    """Pontos espalhados pela superfície (por área): PCA e pega sem viés."""
    a = np.linalg.norm(np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0]), axis=1) / 2
    if a.sum() <= 0:
        return tris.reshape(-1, 3)
    rng = np.random.default_rng(seed)
    k = rng.choice(len(tris), size=n, p=a / a.sum())
    u, v = rng.random(n), rng.random(n)
    flip = u + v > 1
    u[flip], v[flip] = 1 - u[flip], 1 - v[flip]
    t = tris[k]
    return t[:, 0] + u[:, None] * (t[:, 1] - t[:, 0]) + v[:, None] * (t[:, 2] - t[:, 0])


def _quat(R):
    """Matriz de rotação -> quaternião glTF [x, y, z, w]."""
    m = R
    tr = m[0, 0] + m[1, 1] + m[2, 2]
    if tr > 0:
        s = np.sqrt(tr + 1.0) * 2
        w, x, y, z = 0.25 * s, (m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s, (m[1, 0] - m[0, 1]) / s
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = np.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2
        w, x, y, z = (m[2, 1] - m[1, 2]) / s, 0.25 * s, (m[0, 1] + m[1, 0]) / s, (m[0, 2] + m[2, 0]) / s
    elif m[1, 1] > m[2, 2]:
        s = np.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2
        w, x, y, z = (m[0, 2] - m[2, 0]) / s, (m[0, 1] + m[1, 0]) / s, 0.25 * s, (m[1, 2] + m[2, 1]) / s
    else:
        s = np.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2
        w, x, y, z = (m[1, 0] - m[0, 1]) / s, (m[0, 2] + m[2, 0]) / s, (m[1, 2] + m[2, 1]) / s, 0.25 * s
    q = np.array([x, y, z, w])
    return (q / np.linalg.norm(q)).tolist()


def align(gltf: Path, *, grip=0.1, tip=(0, 1, 0), shield=False, front=(0, 0, 1)) -> dict:
    """Endireita o glTF (ver o topo) e grava por cima. Devolve medidas."""
    doc = _Doc(gltf)
    pts = _surface_samples(doc.triangles())
    c = pts.mean(0)
    w, V = np.linalg.eigh(np.cov((pts - c).T))
    V = V[:, ::-1]                                    # do maior para o menor
    if shield:
        thick = V[:, 2]
        if thick @ np.asarray(front, float) < 0:
            thick = -thick
        up = V[:, 0] if abs(V[:, 0][1]) >= abs(V[:, 1][1]) else V[:, 1]
        if up[1] < 0:
            up = -up
        up = up - thick * (up @ thick)
        up /= np.linalg.norm(up)
        # linhas = para onde vai cada eixo novo: -X = frente, +Y = cima
        side = np.cross(up, -thick)
        R = np.array([-thick, up, side]).T            # colunas: eixos novos no antigo
        R = R.T                                        # antigo -> novo
        if np.linalg.det(R) < 0:
            R[2] = -R[2]
        lo, hi = (pts @ R.T).min(0), (pts @ R.T).max(0)
        center = (lo + hi) / 2
        T = -center
        info = {"tamanho": (hi - lo).round(3).tolist()}
    else:
        a1 = V[:, 0]
        if a1 @ np.asarray(tip, float) < 0:
            a1 = -a1
        a2 = V[:, 1]
        a3 = np.cross(a1, a2)                          # (a3, a1, a2) direita
        R = np.array([a3, a1, a2])                     # antigo -> novo: X=a3, Y=a1, Z=a2
        q = pts @ R.T
        lo, hi = q.min(0), q.max(0)
        y = lo[1] + float(grip) * (hi[1] - lo[1])
        slab = q[np.abs(q[:, 1] - y) <= max(0.03 * (hi[1] - lo[1]), 0.02)]
        if len(slab) < 10:
            slab = q
        mid = (slab.min(0) + slab.max(0)) / 2
        T = -np.array([mid[0], y, mid[2]])
        info = {"comprimento": round(float(hi[1] - lo[1]), 3), "pega": float(grip)}
    roots = list(doc.d["scenes"][doc.d.get("scene", 0)]["nodes"])
    doc.d["nodes"].append({"name": "arma", "rotation": _quat(R), "translation": [0.0, 0.0, 0.0],
                           "children": roots})
    # a rotação acontece primeiro (no nó "arma"); a translação vem num nó por cima
    doc.d["nodes"].append({"name": "pega", "translation": T.tolist(),
                           "children": [len(doc.d["nodes"]) - 1]})
    doc.d["scenes"][doc.d.get("scene", 0)]["nodes"] = [len(doc.d["nodes"]) - 1]
    gltf.write_text(json.dumps(doc.d), encoding="utf-8")
    return info


# ------------------------------------------------------------------ ícone
def icon(gltf: Path, out: Path, size=64, shield=False):
    """Ícone com a arma na diagonal (ponta em cima à direita), fundo transparente."""
    from PIL import Image
    doc = _Doc(gltf)
    imgs = []
    for im in doc.d.get("images", []):
        raw = base64.b64decode(im["uri"].split(",", 1)[1])
        imgs.append(np.array(Image.open(io.BytesIO(raw)).convert("RGBA")))
    mats, texs = doc.d.get("materials", []), doc.d.get("textures", [])
    tris = []
    for i, n in enumerate(doc.d["nodes"]):
        if "mesh" not in n:
            continue
        M = doc.world(i)
        for pr in doc.d["meshes"][n["mesh"]]["primitives"]:
            P = (M[:3, :3] @ doc.acc(pr["attributes"]["POSITION"]).T).T + M[:3, 3]
            UV = doc.acc(pr["attributes"]["TEXCOORD_0"])
            idx = doc.acc(pr["indices"])[:, 0].astype(int).reshape(-1, 3)
            mat = mats[pr.get("material", 0)] if mats else {}
            ti = mat.get("pbrMetallicRoughness", {}).get("baseColorTexture", {}).get("index", 0)
            img = imgs[texs[ti]["source"]] if texs else None
            tris += [(P[t], UV[t], img) for t in idx]
    import math

    def rot(axis, deg):
        a = math.radians(deg)
        c, s = math.cos(a), math.sin(a)
        return {"x": np.array([[1, 0, 0], [0, c, -s], [0, s, c]]),
                "y": np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]]),
                "z": np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])}[axis]
    if shield:   # de frente (a face -X para a câmera), um pouco de lado
        R = rot("y", -70) @ rot("x", 8)
    else:        # lâmina na diagonal, largura de frente para a câmera
        R = rot("z", -45) @ rot("y", 90) @ rot("x", 10)
    q_all = np.vstack([t[0] for t in tris]) @ R.T
    lo, hi = q_all.min(0), q_all.max(0)
    span = max(hi[0] - lo[0], hi[1] - lo[1]) * 1.08
    cx, cy = (lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2
    S = size * 4                                       # super-amostra e reduz
    img_out = np.zeros((S, S, 4), np.uint8)
    zb = np.full((S, S), -1e9)
    L = np.array([0.3, 0.7, 0.6]); L /= np.linalg.norm(L)
    for P, UV, tex in tris:
        Q = P @ R.T
        sx = (Q[:, 0] - cx) / span * S + S / 2
        sy = S / 2 - (Q[:, 1] - cy) / span * S
        nrm = np.cross(Q[1] - Q[0], Q[2] - Q[0])
        nn = np.linalg.norm(nrm)
        if nn < 1e-12:
            continue
        shade = 0.6 + 0.4 * abs((nrm / nn) @ L)
        x0, x1 = int(max(0, np.floor(sx.min()))), int(min(S - 1, np.ceil(sx.max())))
        y0, y1 = int(max(0, np.floor(sy.min()))), int(min(S - 1, np.ceil(sy.max())))
        if x1 < x0 or y1 < y0:
            continue
        xs, ys = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
        d = (sx[1] - sx[0]) * (sy[2] - sy[0]) - (sx[2] - sx[0]) * (sy[1] - sy[0])
        if abs(d) < 1e-9:
            continue
        a = ((sx[1] - xs) * (sy[2] - ys) - (sx[2] - xs) * (sy[1] - ys)) / d
        b = ((sx[2] - xs) * (sy[0] - ys) - (sx[0] - xs) * (sy[2] - ys)) / d
        cc = 1 - a - b
        m = (a >= -1e-4) & (b >= -1e-4) & (cc >= -1e-4)
        if not m.any():
            continue
        z = a * Q[0, 2] + b * Q[1, 2] + cc * Q[2, 2]
        u = a * UV[0, 0] + b * UV[1, 0] + cc * UV[2, 0]
        v = a * UV[0, 1] + b * UV[1, 1] + cc * UV[2, 1]
        yy, xx = np.nonzero(m)
        for py, px in zip(yy, xx):
            Y, X = py + y0, px + x0
            if z[py, px] <= zb[Y, X]:
                continue
            if tex is None:
                col = np.array([200, 200, 200, 255], float)
            else:
                h, w_ = tex.shape[:2]
                col = tex[min(h - 1, max(0, int(v[py, px] * h))), min(w_ - 1, max(0, int(u[py, px] * w_)))].astype(float)
            if col[3] < 13:
                continue
            zb[Y, X] = z[py, px]
            img_out[Y, X, :3] = np.clip(col[:3] * shade, 0, 255)
            img_out[Y, X, 3] = 255
    Image.fromarray(img_out).resize((size, size), Image.LANCZOS).save(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("bbmodel")
    ap.add_argument("saida")
    ap.add_argument("--pega", type=float, default=0.1)
    ap.add_argument("--ponta", default="0,1,0")
    ap.add_argument("--escudo", action="store_true")
    ap.add_argument("--frente", default="0,0,1")
    ap.add_argument("--icone")
    a = ap.parse_args()
    src = _prepare(Path(a.bbmodel))
    out = Path(a.saida)
    out.parent.mkdir(parents=True, exist_ok=True)
    convert(src, out)
    info = align(out, grip=a.pega, tip=[float(v) for v in a.ponta.split(",")],
                 shield=a.escudo, front=[float(v) for v in a.frente.split(",")])
    print(f"  {out.name}: {info}")
    if a.icone:
        icon(out, Path(a.icone), shield=a.escudo)
        print(f"  ícone: {a.icone}")


if __name__ == "__main__":
    main()
