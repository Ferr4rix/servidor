"""Converte um ``.bbmodel`` do Blockbench (cubos + animações) num ``.gltf``.

    python tools/bbmodel_to_gltf.py entrada.bbmodel saida.gltf

Feito para os mobs: o resultado é igual ao export glTF do próprio Blockbench
(o mesmo que o jogo já lê para o jogador e os blocos ``produced``):

* cada grupo (osso) vira um nó, com a malha dos cubos dele e os filhos;
* 1 unidade = 1 bloco = 16 px; a frente do modelo (norte do Blockbench, -Z)
  fica virada para +Z no jogo, como o jogador;
* as texturas vão embutidas (uma por material) e com ``alphaMode`` MASK;
* as animações são amostradas a 30 quadros/s (o Blockbench usa catmull-rom,
  que o glTF não tem) e gravadas com interpolação LINEAR. O prefixo
  ``animation.<nome>.`` dos clipes cai (``animation.x.walk`` -> ``walk``).

Cada nó guarda em ``extras.bounds`` a caixa (mínimo, máximo) dos cubos dele, no
espaço do próprio nó: é daí que saem as caixas de colisão (corpo, espada,
escudo) em ``engine/mob_rig.py``.
"""

from __future__ import annotations

import base64
import json
import math
import re
import struct
import sys
from pathlib import Path

FPS = 30.0
PX = 16.0
import os as _os
NEGATE_XY = _os.environ.get("BB_NEGATE_XY", "0") == "1"

# Ordem dos vértices de cada face e o UV de cada um (como o BoxGeometry do
# three.js que o Blockbench usa): (x, y, z) escolhe min (0) ou max (1).
_FACES = {
    "east":  ((1, 1, 1), (1, 1, 0), (1, 0, 1), (1, 0, 0)),
    "west":  ((0, 1, 0), (0, 1, 1), (0, 0, 0), (0, 0, 1)),
    "up":    ((0, 1, 0), (1, 1, 0), (0, 1, 1), (1, 1, 1)),
    "down":  ((0, 0, 1), (1, 0, 1), (0, 0, 0), (1, 0, 0)),
    "south": ((0, 1, 1), (1, 1, 1), (0, 0, 1), (1, 0, 1)),
    "north": ((1, 1, 0), (0, 1, 0), (1, 0, 0), (0, 0, 0)),
}
_NORMALS = {"east": (1, 0, 0), "west": (-1, 0, 0), "up": (0, 1, 0),
            "down": (0, -1, 0), "south": (0, 0, 1), "north": (0, 0, -1)}


# ---------------------------------------------------------------- matemática
def _rot_x(a):
    c, s = math.cos(a), math.sin(a)
    return [[1, 0, 0], [0, c, -s], [0, s, c]]


def _rot_y(a):
    c, s = math.cos(a), math.sin(a)
    return [[c, 0, s], [0, 1, 0], [-s, 0, c]]


def _rot_z(a):
    c, s = math.cos(a), math.sin(a)
    return [[c, -s, 0], [s, c, 0], [0, 0, 1]]


def _mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def _apply(m, v):
    return [sum(m[i][k] * v[k] for k in range(3)) for i in range(3)]


def euler_zyx(deg):
    """Matriz da rotação Euler do Blockbench (ordem ZYX do three.js)."""
    x, y, z = (math.radians(float(v)) for v in deg)
    return _mul(_rot_z(z), _mul(_rot_y(y), _rot_x(x)))


def _qmul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz)


def quat_zyx(deg):
    """Quaternião [x, y, z, w] da rotação Euler ZYX (em graus)."""
    x, y, z = (math.radians(float(v)) / 2 for v in deg)
    qx = (math.sin(x), 0.0, 0.0, math.cos(x))
    qy = (0.0, math.sin(y), 0.0, math.cos(y))
    qz = (0.0, 0.0, math.sin(z), math.cos(z))
    return _qmul(qz, _qmul(qy, qx))


# ---------------------------------------------------------------- animação
def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        # Expressões molang não são suportadas: valem 0 (e avisa).
        print(f"[bbmodel] expressão não suportada {v!r}: usando 0")
        return 0.0


def _catmull(p0, p1, p2, p3, t):
    t2, t3 = t * t, t * t * t
    return 0.5 * ((2 * p1) + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2
                  + (-p0 + 3 * p1 - 3 * p2 + p3) * t3)


def sample_channel(keyframes, t):
    """Valor (x, y, z) de um canal no instante ``t`` (como o Blockbench)."""
    kfs = sorted(keyframes, key=lambda k: float(k["time"]))
    vals = []
    for k in kfs:
        dp = (k.get("data_points") or [{}])[0]
        vals.append([_num(dp.get(c, 0)) for c in "xyz"])
    times = [float(k["time"]) for k in kfs]
    if t <= times[0]:
        return vals[0]
    if t >= times[-1]:
        return vals[-1]
    i = max(j for j in range(len(times)) if times[j] <= t)
    a, b = kfs[i], kfs[i + 1]
    f = (t - times[i]) / max(times[i + 1] - times[i], 1e-9)
    ia, ib = a.get("interpolation", "linear"), b.get("interpolation", "linear")
    if ia == "step":
        return vals[i]
    if "catmullrom" in (ia, ib):
        p0 = vals[i - 1] if i > 0 else vals[i]
        p3 = vals[i + 2] if i + 2 < len(vals) else vals[i + 1]
        return [_catmull(p0[c], vals[i][c], vals[i + 1][c], p3[c], f) for c in range(3)]
    return [vals[i][c] + (vals[i + 1][c] - vals[i][c]) * f for c in range(3)]


# ---------------------------------------------------------------- glTF
class _Buffer:
    def __init__(self):
        self.data = bytearray()
        self.views = []
        self.accessors = []

    def add(self, values, ctype, kind, target=None, minmax=False):
        fmt = {"f": "<f", "I": "<I", "H": "<H"}[ctype]
        while len(self.data) % 4:
            self.data.append(0)
        offset = len(self.data)
        flat = []
        for v in values:
            flat.extend(v if isinstance(v, (list, tuple)) else [v])
        for v in flat:
            self.data += struct.pack(fmt, v)
        view = {"buffer": 0, "byteOffset": offset, "byteLength": len(self.data) - offset}
        if target:
            view["target"] = target
        self.views.append(view)
        acc = {"bufferView": len(self.views) - 1,
               "componentType": {"f": 5126, "I": 5125, "H": 5123}[ctype],
               "count": len(values), "type": kind}
        if minmax:
            width = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}[kind]
            cols = [[(v if isinstance(v, (list, tuple)) else [v])[i] for v in values]
                    for i in range(width)]
            acc["min"] = [min(c) for c in cols]
            acc["max"] = [max(c) for c in cols]
        self.accessors.append(acc)
        return len(self.accessors) - 1


def _sorted_quad(verts, pts):
    """Quads de malha do Blockbench não vêm em ordem de contorno: ordena-os
    pelo ângulo à volta do centro, no plano da face."""
    if len(verts) != 4:
        return verts
    c = [sum(p[i] for p in pts) / 4 for i in range(3)]
    a = [pts[1][i] - pts[0][i] for i in range(3)]
    b = [pts[2][i] - pts[0][i] for i in range(3)]
    n = [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]
    ax = max(range(3), key=lambda i: abs(n[i]))
    u, v = [i for i in range(3) if i != ax]
    order = sorted(range(4), key=lambda k: math.atan2(pts[k][v] - c[v], pts[k][u] - c[u]))
    return [verts[k] for k in order]


def _add_mesh_element(e, textures, origin, prims, lo, hi):
    """Elemento ``mesh`` (vértices livres) do Blockbench: triângulos e quads."""
    eo = [float(v) for v in e.get("origin", [0, 0, 0])]
    rm = euler_zyx(e.get("rotation", [0, 0, 0]))
    verts = e.get("vertices") or {}
    world = {}
    for key, p in verts.items():
        v = _apply(rm, [float(x) for x in p])
        world[key] = [(v[i] + eo[i] - origin[i]) / PX for i in range(3)]
    for face in (e.get("faces") or {}).values():
        if face.get("texture") is None:
            continue
        keys = [k for k in face.get("vertices", []) if k in world]
        if len(keys) < 3:
            continue
        keys = _sorted_quad(keys, [world[k] for k in keys])
        tex = int(face["texture"])
        tw = float(textures[tex].get("uv_width") or textures[tex].get("width") or 16)
        th = float(textures[tex].get("uv_height") or textures[tex].get("height") or 16)
        pts = [world[k] for k in keys]
        a = [pts[1][i] - pts[0][i] for i in range(3)]
        b = [pts[2][i] - pts[0][i] for i in range(3)]
        n = [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]
        ln = math.sqrt(sum(x * x for x in n)) or 1.0
        n = [x / ln for x in n]
        p = prims.setdefault(tex, [[], [], [], []])
        base = len(p[0])
        uv = face.get("uv") or {}
        for k, pt in zip(keys, pts):
            p[0].append(pt)
            p[1].append(n)
            u = uv.get(k, [0, 0])
            p[2].append((float(u[0]) / tw, float(u[1]) / th))
            for i in range(3):
                lo[i] = min(lo[i], pt[i])
                hi[i] = max(hi[i], pt[i])
        for t in range(1, len(keys) - 1):
            p[3] += [base, base + t, base + t + 1]


def convert(bbmodel_path, out_path, fps=FPS):
    d = json.loads(Path(bbmodel_path).read_text(encoding="utf-8"))
    groups = {g["uuid"]: g for g in d.get("groups", [])}
    elements = {e["uuid"]: e for e in d.get("elements", [])}
    textures = d.get("textures", [])

    # --- hierarquia ---------------------------------------------------------
    order = []                     # (uuid do grupo, uuid do pai)
    members = {}                   # grupo -> [elementos]

    ROOT = "__raiz__"

    def walk(items, parent):
        for item in items:
            if isinstance(item, str):
                if item in elements:
                    # Elementos soltos (sem grupo) vão para um grupo raiz.
                    if parent is None:
                        if ROOT not in groups:
                            groups[ROOT] = {"uuid": ROOT, "name": "raiz", "origin": [0, 0, 0]}
                            order.insert(0, (ROOT, None))
                        members.setdefault(ROOT, []).append(elements[item])
                    else:
                        members.setdefault(parent, []).append(elements[item])
                continue
            uuid = item.get("uuid")
            if uuid not in groups:
                groups[uuid] = item
            order.append((uuid, parent))
            walk(item.get("children", []), uuid)

    walk(d.get("outliner", []), None)
    if not order:
        raise ValueError("o modelo não tem grupos (ossos)")

    buf = _Buffer()
    nodes, meshes = [], []
    node_of = {}
    for uuid, parent in order:
        g = groups[uuid]
        origin = [float(v) for v in g.get("origin", [0, 0, 0])]
        p_origin = [float(v) for v in groups[parent].get("origin", [0, 0, 0])] \
            if parent else [0.0, 0.0, 0.0]
        node = {"name": g.get("name") or f"grupo{len(nodes)}",
                "translation": [(origin[i] - p_origin[i]) / PX for i in range(3)]}
        rot = g.get("rotation", [0, 0, 0])
        if any(float(v) for v in rot):
            node["rotation"] = list(quat_zyx(rot))
        node_of[uuid] = len(nodes)
        nodes.append(node)
        if parent:
            nodes[node_of[parent]].setdefault("children", []).append(node_of[uuid])

        # --- malha dos cubos do grupo (no espaço do grupo) ------------------
        prims = {}                 # textura -> [pos, nrm, uv, idx]
        lo, hi = [1e9] * 3, [-1e9] * 3
        for e in members.get(uuid, []):
            if e.get("visibility") is False or e.get("export") is False:
                continue
            if e.get("type") == "mesh":
                _add_mesh_element(e, textures, origin, prims, lo, hi)
                continue
            if e.get("type", "cube") != "cube":
                continue
            inf = float(e.get("inflate", 0) or 0)
            a = [float(v) - inf for v in e["from"]]
            b = [float(v) + inf for v in e["to"]]
            eo = [float(v) for v in e.get("origin", [0, 0, 0])]
            rm = euler_zyx(e.get("rotation", [0, 0, 0]))
            for fname, corners in _FACES.items():
                face = (e.get("faces") or {}).get(fname)
                if not face or face.get("texture") is None:
                    continue
                tex = int(face["texture"])
                tw = float(textures[tex].get("uv_width") or textures[tex].get("width") or 16)
                th = float(textures[tex].get("uv_height") or textures[tex].get("height") or 16)
                u0, v0, u1, v1 = (float(v) for v in face.get("uv", [0, 0, 0, 0]))
                uvs = [(u0, v0), (u1, v0), (u0, v1), (u1, v1)]
                r = int(face.get("rotation", 0) or 0) // 90 % 4
                if r:
                    # gira os cantos do UV (0 1 / 2 3) em passos de 90°
                    ring = [0, 1, 3, 2]
                    vals = [uvs[i] for i in ring]
                    vals = vals[-r:] + vals[:-r]
                    for i, v in zip(ring, vals):
                        uvs[i] = v
                p = prims.setdefault(tex, [[], [], [], []])
                base = len(p[0])
                n = _apply(rm, _NORMALS[fname])
                for k, c in enumerate(corners):
                    v = [b[i] if c[i] else a[i] for i in range(3)]
                    v = [v[i] - eo[i] for i in range(3)]
                    v = _apply(rm, v)
                    v = [(v[i] + eo[i] - origin[i]) / PX for i in range(3)]
                    p[0].append(v)
                    p[1].append(n)
                    p[2].append((uvs[k][0] / tw, uvs[k][1] / th))
                    for i in range(3):
                        lo[i] = min(lo[i], v[i])
                        hi[i] = max(hi[i], v[i])
                p[3] += [base, base + 2, base + 1, base + 2, base + 3, base + 1]
        if prims:
            primitives = []
            for tex, (pos, nrm, uv, idx) in sorted(prims.items()):
                primitives.append({
                    "attributes": {
                        "POSITION": buf.add(pos, "f", "VEC3", 34962, minmax=True),
                        "NORMAL": buf.add(nrm, "f", "VEC3", 34962),
                        "TEXCOORD_0": buf.add(uv, "f", "VEC2", 34962),
                    },
                    "indices": buf.add(idx, "I", "SCALAR", 34963),
                    "material": tex,
                })
            meshes.append({"name": node["name"], "primitives": primitives})
            node["mesh"] = len(meshes) - 1
            node["extras"] = {"bounds": [lo, hi]}

    # --- texturas -----------------------------------------------------------
    images, materials, gl_textures = [], [], []
    for i, t in enumerate(textures):
        src = t.get("source") or ""
        if not src.startswith("data:"):
            for layer in t.get("layers") or []:
                if str(layer.get("data_url", "")).startswith("data:"):
                    src = layer["data_url"]
                    break
        if not src.startswith("data:"):
            raise ValueError(f"textura {t.get('name')!r} não está embutida no .bbmodel")
        images.append({"uri": src, "name": t.get("name", f"textura{i}")})
        gl_textures.append({"source": i, "sampler": 0})
        materials.append({"name": t.get("name", f"material{i}"),
                          "pbrMetallicRoughness": {"baseColorTexture": {"index": i},
                                                   "metallicFactor": 0,
                                                   "roughnessFactor": 1},
                          "alphaMode": "MASK", "alphaCutoff": 0.05,
                          "doubleSided": True})

    # --- animações ----------------------------------------------------------
    animations = []
    for anim in d.get("animations", []):
        name = re.sub(r"^animation\.[^.]+\.", "", str(anim.get("name", "")))
        length = float(anim.get("length") or 0)
        if length <= 0:
            continue
        steps = max(1, int(math.ceil(length * fps)))
        times = sorted({round(i / fps, 6) for i in range(steps)} | {round(length, 6)})
        samplers, channels = [], []
        for uuid, animator in (anim.get("animators") or {}).items():
            kfs = animator.get("keyframes") or []
            if uuid not in node_of or not kfs:
                continue
            ni = node_of[uuid]
            g = groups[uuid]
            by_channel = {}
            for k in kfs:
                by_channel.setdefault(k.get("channel"), []).append(k)
            t_in = buf.add(times, "f", "SCALAR", minmax=True)
            if "rotation" in by_channel:
                base = [float(v) for v in g.get("rotation", [0, 0, 0])]
                quats = []
                for t in times:
                    x, y, z = sample_channel(by_channel["rotation"], t)
                    sx, sy = (-1, -1) if NEGATE_XY else (1, 1)
                    quats.append(list(quat_zyx((base[0] + sx * x, base[1] + sy * y, base[2] + z))))
                samplers.append({"input": t_in, "output": buf.add(quats, "f", "VEC4"),
                                 "interpolation": "LINEAR"})
                channels.append({"sampler": len(samplers) - 1,
                                 "target": {"node": ni, "path": "rotation"}})
            if "position" in by_channel:
                base = nodes[ni]["translation"]
                pos = []
                for t in times:
                    x, y, z = sample_channel(by_channel["position"], t)
                    px_sign = -1 if NEGATE_XY else 1
                    pos.append([base[0] + px_sign * x / PX, base[1] + y / PX, base[2] + z / PX])
                samplers.append({"input": t_in, "output": buf.add(pos, "f", "VEC3"),
                                 "interpolation": "LINEAR"})
                channels.append({"sampler": len(samplers) - 1,
                                 "target": {"node": ni, "path": "translation"}})
            if "scale" in by_channel:
                sc = [sample_channel(by_channel["scale"], t) for t in times]
                samplers.append({"input": t_in, "output": buf.add(sc, "f", "VEC3"),
                                 "interpolation": "LINEAR"})
                channels.append({"sampler": len(samplers) - 1,
                                 "target": {"node": ni, "path": "scale"}})
        if channels:
            animations.append({"name": name, "samplers": samplers, "channels": channels,
                               "extras": {"loop": anim.get("loop", "once"),
                                          "length": length}})

    roots = [node_of[u] for u, p in order if p is None]
    doc = {
        "asset": {"version": "2.0", "generator": "jogo bbmodel_to_gltf"},
        "scene": 0,
        "scenes": [{"nodes": roots}],
        "nodes": nodes,
        "meshes": meshes,
        "materials": materials,
        "textures": gl_textures,
        "images": images,
        "samplers": [{"magFilter": 9728, "minFilter": 9728,
                      "wrapS": 33071, "wrapT": 33071}],
        "animations": animations,
        "accessors": buf.accessors,
        "bufferViews": buf.views,
        "buffers": [{"byteLength": len(buf.data),
                     "uri": "data:application/octet-stream;base64,"
                            + base64.b64encode(bytes(buf.data)).decode("ascii")}],
    }
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps(doc), encoding="utf-8")
    return {"nodes": len(nodes), "meshes": len(meshes),
            "animations": [a["name"] for a in animations]}


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    info = convert(sys.argv[1], sys.argv[2])
    print(f"ok: {info['nodes']} nós, {info['meshes']} malhas, "
          f"clipes: {', '.join(info['animations'])}")
