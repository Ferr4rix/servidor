"""Acrescenta as animações "comendo" e "bebendo" aos bichos pacíficos.

Os modelos (assets/models/mobs/<nome>/<nome>.gltf) têm todos a mesma
estrutura: "point" (raiz) > "body" (cabeça+corpo, cara virada para -Z) e as
pernas "front_legs"/"back_legs". As duas animações dobram o corpo para a frente
e para baixo (cara no chão / na água) e repetem um gesto:

- comendo (2 s, em ciclo): cabeça baixa, a "mastigar" — 4 bicadas por ciclo,
  pernas da frente um pouco abertas;
- bebendo (2 s, em ciclo): cabeça ainda mais baixa e esticada para a frente,
  "lambidas" lentas (2 por ciclo).

Uso:  python tools/mob_anim_comer_beber.py [nomes...]   (sem nomes: os 5)
Se o modelo já tiver as animações, são substituídas.
"""
from __future__ import annotations

import base64
import json
import math
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from caminhos import CLIENTE as ROOT  # noqa: E402

MOBS = ["bloomlet", "geodo", "melli", "morango", "nimbi"]
FPS = 24


def qx(deg):
    a = math.radians(deg) / 2
    return [math.sin(a), 0.0, 0.0, math.cos(a)]


def node_index(g, name):
    for i, n in enumerate(g["nodes"]):
        if n.get("name") == name:
            return i
    return None


def clips(rest_body_t, rest_fl_t):
    by, bz = rest_body_t[1], rest_body_t[2]
    n = 2 * FPS
    times = [i / FPS for i in range(n + 1)]
    out = {}
    # comendo: pitch -24 +- 7 (4 bicadas), desce 0.04
    rot, tr, leg = [], [], []
    for t in times:
        bob = 0.5 - 0.5 * math.cos(2 * math.pi * 2 * t)          # 0..1, 2 Hz (4 bicadas em 2 s)
        rot.append(qx(-(20 + 12 * bob)))
        tr.append([0.0, by - 0.05 - 0.015 * bob, bz - 0.03])
        leg.append(qx(10))
    out["comendo"] = (times, rot, tr, leg)
    # bebendo: pitch -34 +- 5 (lambidas lentas), mais para a frente
    rot, tr, leg = [], [], []
    for t in times:
        lap = 0.5 - 0.5 * math.cos(2 * math.pi * 1 * t)          # 1 Hz
        rot.append(qx(-(32 + 6 * lap)))
        tr.append([0.0, by - 0.07, bz - 0.06 - 0.02 * lap])
        leg.append(qx(16))
    out["bebendo"] = (times, rot, tr, leg)
    return out


def add(path: Path):
    g = json.loads(path.read_text())
    buf = g["buffers"][0]
    head, b64 = buf["uri"].split(",", 1)
    data = bytearray(base64.b64decode(b64))
    body = node_index(g, "body")
    fl = node_index(g, "front_legs")
    if body is None:
        print(f"{path.name}: sem 'body', saltado")
        return
    g["animations"] = [a for a in g.get("animations", []) if a["name"] not in ("comendo", "bebendo")]

    def acc(values, comps, kind, mm=False):
        while len(data) % 4:
            data.append(0)
        off = len(data)
        flat = [v for row in values for v in (row if comps > 1 else [row])]
        data.extend(struct.pack(f"<{len(flat)}f", *flat))
        g["bufferViews"].append({"buffer": 0, "byteOffset": off, "byteLength": len(flat) * 4})
        a = {"bufferView": len(g["bufferViews"]) - 1, "componentType": 5126,
             "count": len(values), "type": kind}
        if mm:
            a["min"], a["max"] = [min(values)], [max(values)]
        g["accessors"].append(a)
        return len(g["accessors"]) - 1

    rest_b = g["nodes"][body].get("translation", [0, 0, 0])
    rest_f = g["nodes"][fl].get("translation", [0, 0, 0]) if fl is not None else None
    for name, (times, rot, tr, leg) in clips(rest_b, rest_f).items():
        ti = acc(times, 1, "SCALAR", mm=True)
        samplers, channels = [], []
        for node, path_, vals, kind in ((body, "rotation", rot, "VEC4"),
                                        (body, "translation", tr, "VEC3"),
                                        (fl, "rotation", leg, "VEC4")):
            if node is None:
                continue
            samplers.append({"input": ti, "output": acc(vals, len(vals[0]), kind),
                             "interpolation": "LINEAR"})
            channels.append({"sampler": len(samplers) - 1, "target": {"node": node, "path": path_}})
        g["animations"].append({"name": name, "samplers": samplers, "channels": channels})
    buf["byteLength"] = len(data)
    buf["uri"] = head + "," + base64.b64encode(bytes(data)).decode()
    path.write_text(json.dumps(g, separators=(",", ":")))
    print(f"{path.name}: comendo + bebendo")


if __name__ == "__main__":
    for n in sys.argv[1:] or MOBS:
        add(ROOT / "assets" / "models" / "mobs" / n / f"{n}.gltf")
