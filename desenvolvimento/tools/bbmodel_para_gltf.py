"""Converte um .bbmodel (Blockbench, formato "free" com malhas) num .gltf
para os blocos "produced" do jogo, com a textura embutida.

    python tools/bbmodel_para_gltf.py modelo.bbmodel saida.gltf [--escala-h 0.333] [--escala-v 0.7]

* Unidades do Blockbench: 16 = 1 bloco. Cada elemento tem ``origin`` e
  ``rotation`` (graus; aplica-se Z, depois Y, depois X, como o Blockbench); os
  vértices são relativos ao ``origin``.
* ``--escala-h`` / ``--escala-v``: encolhe na horizontal / vertical para caber
  numa célula (o capim de savana original ocupa 3x3 blocos).
* Material: textura com transparência (alphaMode MASK) e duas faces
  (doubleSided), filtro "nearest" (pixel nítido).
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import struct
from pathlib import Path


def _rot(v, deg):
    x, y, z = v
    rx, ry, rz = (math.radians(a) for a in deg)
    # Z, depois Y, depois X (matriz Rx·Ry·Rz, como o Blockbench)
    x, y = x * math.cos(rz) - y * math.sin(rz), x * math.sin(rz) + y * math.cos(rz)
    x, z = x * math.cos(ry) + z * math.sin(ry), -x * math.sin(ry) + z * math.cos(ry)
    y, z = y * math.cos(rx) - z * math.sin(rx), y * math.sin(rx) + z * math.cos(rx)
    return (x, y, z)


def convert(src, dst, sh=1.0, sv=1.0):
    bb = json.loads(Path(src).read_text(encoding="utf-8"))
    tex = bb["textures"][0]
    tw = float(tex.get("uv_width") or bb["resolution"]["width"])
    th = float(tex.get("uv_height") or bb["resolution"]["height"])
    png = base64.b64decode(tex["source"].split(",", 1)[1])
    pos, uv, idx = [], [], []
    for el in bb["elements"]:
        if el.get("type") != "mesh" or not el.get("export", True):
            continue
        o = el.get("origin", [0, 0, 0])
        r = el.get("rotation", [0, 0, 0])
        verts = el["vertices"]
        for face in el["faces"].values():
            ids = face["vertices"]
            base = len(pos)
            for vid in ids:
                x, y, z = _rot(verts[vid], r)
                pos.append(((x + o[0]) / 16.0 * sh, (y + o[1]) / 16.0 * sv, (z + o[2]) / 16.0 * sh))
                u, v = face["uv"][vid]
                uv.append((u / tw, v / th))
            for k in range(1, len(ids) - 1):
                idx += [base, base + k, base + k + 1]
    # base do modelo no chão (y mínimo = 0) e centrado em x/z
    ys = [p[1] for p in pos]
    xs = [p[0] for p in pos]
    zs = [p[2] for p in pos]
    cx, cz, y0 = (min(xs) + max(xs)) / 2, (min(zs) + max(zs)) / 2, min(ys)
    pos = [(p[0] - cx, p[1] - y0, p[2] - cz) for p in pos]
    pb = b"".join(struct.pack("<3f", *p) for p in pos)
    ub = b"".join(struct.pack("<2f", *t) for t in uv)
    ib = b"".join(struct.pack("<I", i) for i in idx)
    buf = pb + ub + ib
    lo = [min(p[i] for p in pos) for i in range(3)]
    hi = [max(p[i] for p in pos) for i in range(3)]
    gltf = {
        "asset": {"version": "2.0", "generator": "tools/bbmodel_para_gltf.py"},
        "scene": 0, "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": bb.get("name", "modelo")}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "TEXCOORD_0": 1},
                                    "indices": 2, "material": 0}]}],
        "materials": [{"name": "textura", "pbrMetallicRoughness": {
            "baseColorTexture": {"index": 0}, "metallicFactor": 0, "roughnessFactor": 1},
            "alphaMode": "MASK", "alphaCutoff": 0.5, "doubleSided": True}],
        "textures": [{"source": 0, "sampler": 0}],
        "samplers": [{"magFilter": 9728, "minFilter": 9728, "wrapS": 33071, "wrapT": 33071}],
        "images": [{"uri": "data:image/png;base64," + base64.b64encode(png).decode()}],
        "buffers": [{"byteLength": len(buf),
                     "uri": "data:application/octet-stream;base64," + base64.b64encode(buf).decode()}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(pb), "target": 34962},
            {"buffer": 0, "byteOffset": len(pb), "byteLength": len(ub), "target": 34962},
            {"buffer": 0, "byteOffset": len(pb) + len(ub), "byteLength": len(ib), "target": 34963}],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": len(pos), "type": "VEC3", "min": lo, "max": hi},
            {"bufferView": 1, "componentType": 5126, "count": len(uv), "type": "VEC2"},
            {"bufferView": 2, "componentType": 5125, "count": len(idx), "type": "SCALAR"}],
    }
    Path(dst).write_text(json.dumps(gltf), encoding="utf-8")
    return lo, hi, len(idx) // 3


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("src"); ap.add_argument("dst")
    ap.add_argument("--escala-h", type=float, default=1.0)
    ap.add_argument("--escala-v", type=float, default=1.0)
    a = ap.parse_args()
    print(convert(a.src, a.dst, a.escala_h, a.escala_v))
