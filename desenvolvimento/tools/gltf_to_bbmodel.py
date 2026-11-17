"""Converte um .gltf exportado pelo Blockbench (cubos + animações) de volta num
.bbmodel que o Blockbench abre e edita.

    python tools/gltf_to_bbmodel.py assets/models/player/player.gltf saida.bbmodel

* Nós com filhos = grupos (ossos), com o pivô na mesma posição.
* Cada malha de 24 vértices = um cubo (from/to em px); as "Layer" (camada de
  fora da pele) viram o cubo de dentro com ``inflate``.
* UV de cada face lida dos vértices (inclui as faces espelhadas).
* Animações: as rotações (quaterniões) voltam a Euler ZYX em graus e as
  posições a px, como keyframes lineares. O Blockbench soma os keyframes
  direto à rotação/posição do osso (three.js), sem trocar sinais (só troca ao
  exportar para Bedrock), por isso os valores passam como estão.
* A textura embutida vai junto.
"""

from __future__ import annotations

import base64
import json
import math
import struct
import sys
import uuid
from pathlib import Path

PX = 16.0
LOOPING = {"walk", "run", "idle", "hold_block", "crouch", "fall"}

# Cantos de cada face, igual ao tools/bbmodel_to_gltf.py: (u0,v0) (u1,v0) (u0,v1) (u1,v1)
_FACES = {
    "east":  ((1, 1, 1), (1, 1, 0), (1, 0, 1), (1, 0, 0)),
    "west":  ((0, 1, 0), (0, 1, 1), (0, 0, 0), (0, 0, 1)),
    "up":    ((0, 1, 0), (1, 1, 0), (0, 1, 1), (1, 1, 1)),
    "down":  ((0, 0, 1), (1, 0, 1), (0, 0, 0), (1, 0, 0)),
    "south": ((0, 1, 1), (1, 1, 1), (0, 0, 1), (1, 0, 1)),
    "north": ((1, 1, 0), (0, 1, 0), (1, 0, 0), (0, 0, 0)),
}


def _uuid():
    return str(uuid.uuid4())


def _r(v, n=4):
    v = round(float(v), n)
    return 0.0 if v == 0 else v


class Gltf:
    def __init__(self, path):
        self.path = Path(path)
        self.g = json.loads(self.path.read_text(encoding="utf-8"))
        self._bufs = {}

    def buffer(self, i):
        if i not in self._bufs:
            uri = self.g["buffers"][i]["uri"]
            self._bufs[i] = (base64.b64decode(uri.split(",", 1)[1]) if uri.startswith("data:")
                             else (self.path.parent / uri).read_bytes())
        return self._bufs[i]

    def accessor(self, i):
        a = self.g["accessors"][i]
        bv = self.g["bufferViews"][a["bufferView"]]
        data = self.buffer(bv["buffer"])
        n = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}[a["type"]]
        fmt = {5126: "f", 5125: "I", 5123: "H", 5121: "B"}[a["componentType"]]
        size = struct.calcsize(fmt)
        stride = bv.get("byteStride") or size * n
        off = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
        out = []
        for k in range(a["count"]):
            vals = struct.unpack_from("<" + fmt * n, data, off + k * stride)
            out.append(vals if n > 1 else vals[0])
        return out


def quat_to_euler_zyx(q):
    """Quaternião (x,y,z,w) -> Euler (graus) com R = Rz·Ry·Rx (three.js 'ZYX')."""
    x, y, z, w = q
    r00 = 1 - 2 * (y * y + z * z)
    r10 = 2 * (x * y + z * w)
    r20 = 2 * (x * z - y * w)
    r21 = 2 * (y * z + x * w)
    r22 = 1 - 2 * (x * x + y * y)
    b = math.asin(max(-1.0, min(1.0, -r20)))
    if abs(r20) < 0.999999:
        a = math.atan2(r21, r22)
        c = math.atan2(r10, r00)
    else:                                   # gimbal: põe tudo em X
        r12 = 2 * (y * z - x * w)
        r11 = 1 - 2 * (x * x + z * z)
        a = math.atan2(-r12, r11)
        c = 0.0
    return [math.degrees(a), math.degrees(b), math.degrees(c)]


def convert(src, dst):
    G = Gltf(src)
    g = G.g
    nodes = g["nodes"]
    parent = {}
    for i, n in enumerate(nodes):
        for c in n.get("children", []):
            parent[c] = i

    def world_t(i):
        t = [0.0, 0.0, 0.0]
        while i is not None:
            tr = nodes[i].get("translation", [0, 0, 0])
            t = [t[k] + tr[k] for k in range(3)]
            i = parent.get(i)
        return t

    # textura
    img = g["images"][0]
    tex_src = img.get("uri", "")
    tw = th = 16
    if tex_src.startswith("data:"):
        try:
            from PIL import Image
            import io
            im = Image.open(io.BytesIO(base64.b64decode(tex_src.split(",", 1)[1])))
            tw, th = im.size
        except Exception:
            tw = th = 64

    group_uuid = {}
    groups = []
    elements = []
    elem_uuid = {}

    def make_cube(i):
        n = nodes[i]
        prim = g["meshes"][n["mesh"]]["primitives"][0]
        pos = G.accessor(prim["attributes"]["POSITION"])
        uvs = G.accessor(prim["attributes"]["TEXCOORD_0"])
        nrm = G.accessor(prim["attributes"]["NORMAL"]) if "NORMAL" in prim["attributes"] else None
        base = world_t(i)
        pts = [[(p[k] + base[k]) * PX for k in range(3)] for p in pos]
        lo = [min(p[k] for p in pts) for k in range(3)]
        hi = [max(p[k] for p in pts) for k in range(3)]
        faces = {}
        for fname, corners in _FACES.items():
            want = [[hi[k] if c[k] else lo[k] for k in range(3)] for c in corners]
            # vértices desta face: os que estão no plano dela
            axis = next(k for k in range(3) if all(c[k] == corners[0][k] for c in corners))
            plane = hi[axis] if corners[0][axis] else lo[axis]
            sign = 1 if corners[0][axis] else -1
            idx = [j for j, p in enumerate(pts) if abs(p[axis] - plane) < 1e-3
                   and (nrm is None or nrm[j][axis] * sign > 0.5)]
            found = []
            for w in want:
                best = None
                for j in idx:
                    d = sum((pts[j][k] - w[k]) ** 2 for k in range(3))
                    if best is None or d < best[0]:
                        best = (d, j)
                found.append(uvs[best[1]] if best else (0, 0))
            u0, v0 = found[0][0] * tw, found[0][1] * th
            u1, v1 = found[3][0] * tw, found[3][1] * th
            faces[fname] = {"uv": [_r(u0), _r(v0), _r(u1), _r(v1)], "texture": 0}
        return {"name": n.get("name") or "cubo", "box_uv": False, "rescale": False,
                "locked": False, "render_order": "default", "allow_mirror_modeling": True,
                "from": [_r(v) for v in lo], "to": [_r(v) for v in hi], "autouv": 0,
                "color": 0, "origin": [_r(v * PX) for v in world_t(parent.get(i))] if parent.get(i) is not None else [0, 0, 0],
                "inflate": 0, "faces": faces, "type": "cube", "uuid": _uuid()}

    def walk(i):
        n = nodes[i]
        if "mesh" in n and not n.get("children"):
            el = make_cube(i)
            elements.append(el)
            elem_uuid[i] = el["uuid"]
            return el["uuid"]
        u = _uuid()
        group_uuid[i] = u
        origin = [_r(v * PX) for v in world_t(i)]
        children = [walk(c) for c in n.get("children", [])]
        grp = {"name": n.get("name") or "grupo", "origin": origin, "color": 0,
               "rotation": [0, 0, 0], "uuid": u, "export": True, "mirror_uv": False,
               "isOpen": False, "locked": False, "visibility": True, "autouv": 0}
        groups.append(grp)
        return dict(grp, children=children)

    roots = [i for i in range(len(nodes)) if i not in parent]
    scene = g.get("scenes", [{}])[g.get("scene", 0)].get("nodes") or roots
    outliner = [walk(i) for i in scene]

    # "Layer": cubo de dentro + inflate (a pele de fora do Minecraft)
    by_name = {}
    for el in elements:
        by_name.setdefault(el["name"], []).append(el)
    for el in elements:
        if not el["name"].endswith(" Layer"):
            continue
        inner = (by_name.get(el["name"][:-6]) or [None])[0]
        if inner is None:
            continue
        d = [inner["from"][k] - el["from"][k] for k in range(3)] + \
            [el["to"][k] - inner["to"][k] for k in range(3)]
        if max(d) - min(d) < 1e-3 and d[0] > 0:
            el["inflate"] = _r(d[0])
            el["from"], el["to"] = list(inner["from"]), list(inner["to"])

    # animações
    animations = []
    for a in g.get("animations", []):
        animators = {}
        length = 0.0
        for ch in a["channels"]:
            ni = ch["target"]["node"]
            path = ch["target"]["path"]
            if ni not in group_uuid or path not in ("rotation", "translation", "scale"):
                continue
            s = a["samplers"][ch["sampler"]]
            times = G.accessor(s["input"])
            vals = G.accessor(s["output"])
            if s.get("interpolation") == "CUBICSPLINE":
                vals = vals[1::3]
            length = max(length, max(times) if times else 0)
            rest = nodes[ni].get("translation", [0, 0, 0])
            an = animators.setdefault(group_uuid[ni], {"name": nodes[ni].get("name"),
                                                         "type": "bone", "keyframes": []})
            for t, v in zip(times, vals):
                if path == "rotation":
                    x, y, z = quat_to_euler_zyx(v)
                    dp = {"x": _r(x, 3), "y": _r(y, 3), "z": _r(z, 3)}
                    channel = "rotation"
                elif path == "translation":
                    dp = {"x": _r((v[0] - rest[0]) * PX, 3), "y": _r((v[1] - rest[1]) * PX, 3),
                          "z": _r((v[2] - rest[2]) * PX, 3)}
                    channel = "position"
                else:
                    dp = {"x": _r(v[0], 3), "y": _r(v[1], 3), "z": _r(v[2], 3)}
                    channel = "scale"
                an["keyframes"].append({"channel": channel, "data_points": [dp],
                                        "uuid": _uuid(), "time": _r(t, 4), "color": -1,
                                        "interpolation": "linear"})
        name = a.get("name") or f"anim{len(animations)}"
        animations.append({"uuid": _uuid(), "name": name,
                           "loop": "loop" if name in LOOPING else "once",
                           "override": False, "length": _r(length, 4), "snapping": 24,
                           "selected": False, "anim_time_update": "", "blend_weight": "",
                           "start_delay": "", "loop_delay": "", "animators": animators})

    model = {
        "meta": {"format_version": "4.10", "model_format": "free", "box_uv": False},
        "name": Path(dst).stem, "model_identifier": "", "visible_box": [1, 1, 0],
        "variable_placeholders": "", "variable_placeholder_buttons": [],
        "timeline_setups": [], "unhandled_root_fields": {},
        "resolution": {"width": tw, "height": th},
        "elements": elements, "groups": groups, "outliner": outliner,
        "textures": [{"name": g["textures"][0].get("name", "textura") if g.get("textures") else "textura",
                      "path": "", "folder": "", "namespace": "", "id": "0",
                      "width": tw, "height": th, "uv_width": tw, "uv_height": th,
                      "particle": False, "render_mode": "default", "render_sides": "auto",
                      "wrap_mode": "limited", "visible": True, "internal": True, "saved": False,
                      "uuid": _uuid(), "source": tex_src}],
        "animations": animations,
    }
    Path(dst).write_text(json.dumps(model, indent=1, ensure_ascii=False), encoding="utf-8")
    return len(groups), len(elements), [a["name"] for a in animations]


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    ng, ne, anims = convert(sys.argv[1], sys.argv[2])
    print(f"ok: {ng} grupos, {ne} cubos, animações: {', '.join(anims)}")
