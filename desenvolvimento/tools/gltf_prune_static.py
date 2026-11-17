"""Tira das animações de um .gltf os canais que não mexem em nada.

    python tools/gltf_prune_static.py modelo.gltf [outro.gltf ...]

O Blockbench guarda keyframes para quase todos os ossos em cada animação,
mesmo os que ficam parados (posição 0, escala 1). Cada canal custa tempo de
CPU por frame no jogo (o Python amostra e aplica cada um), por isso um canal
que fica sempre igual à pose de repouso é apagado (o resultado é o mesmo).
"""

import base64
import json
import struct
import sys
from pathlib import Path

EPS = 1e-4


def prune(path):
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    buf = bytearray(base64.b64decode(doc["buffers"][0]["uri"].split(",", 1)[1]))

    def read(i):
        a = doc["accessors"][i]
        v = doc["bufferViews"][a["bufferView"]]
        n = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}[a["type"]]
        off = v.get("byteOffset", 0) + a.get("byteOffset", 0)
        flat = struct.unpack_from("<" + "f" * (a["count"] * n), buf, off)
        return [flat[k * n:(k + 1) * n] for k in range(a["count"])]

    def rest(node, path_):
        n = doc["nodes"][node]
        return {"translation": n.get("translation", [0, 0, 0]),
                "rotation": n.get("rotation", [0, 0, 0, 1]),
                "scale": n.get("scale", [1, 1, 1])}[path_]

    def same(a, b, path_):
        if path_ == "rotation":                      # q e -q são a mesma rotação
            d = abs(sum(x * y for x, y in zip(a, b)))
            return d > 1 - 1e-6
        return max(abs(x - y) for x, y in zip(a, b)) < EPS

    before = after = 0
    for anim in doc.get("animations", []):
        keep_s, keep_c = [], []
        for ch in anim["channels"]:
            before += 1
            s = anim["samplers"][ch["sampler"]]
            vals = read(s["output"])
            node, path_ = ch["target"]["node"], ch["target"]["path"]
            if all(same(v, rest(node, path_), path_) for v in vals):
                continue                          # parado na pose de repouso
            # (canais constantes noutra pose ficam como estão: os tempos são
            # partilhados entre canais do mesmo osso e não se podem encurtar)
            keep_s.append(s)
            keep_c.append({"sampler": len(keep_s) - 1, "target": ch["target"]})
            after += 1
        anim["samplers"], anim["channels"] = keep_s, keep_c
    Path(path).write_text(json.dumps(doc), encoding="utf-8")
    return before, after


if __name__ == "__main__":
    for p in sys.argv[1:]:
        b, a = prune(p)
        print(f"{p}: {b} -> {a} canais")
