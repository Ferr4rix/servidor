"""Ajuda para gerar modelos "de cubinhos" em glTF (capim, fada...).

Cada parte é uma lista de caixas (min, max, cor); as cores vão para uma
textura-paleta 16x16 embutida (1 texel por cor, filtro "nearest"), por isso
o shader do jogo (que multiplica pela textura) desenha-as tal e qual.
Partes viram nós (com pai, posição) e podem ter animações (TRS por nó).
"""
from __future__ import annotations

import base64
import io
import json
import struct

from PIL import Image

_FACES = (  # (eixo, sinal, cantos em ordem anti-horária vistos de fora)
    ((1, 0, 0), ((1, 0, 0), (1, 1, 0), (1, 1, 1), (1, 0, 1))),
    ((-1, 0, 0), ((0, 0, 1), (0, 1, 1), (0, 1, 0), (0, 0, 0))),
    ((0, 1, 0), ((0, 1, 0), (0, 1, 1), (1, 1, 1), (1, 1, 0))),
    ((0, -1, 0), ((0, 0, 1), (0, 0, 0), (1, 0, 0), (1, 0, 1))),
    ((0, 0, 1), ((1, 0, 1), (1, 1, 1), (0, 1, 1), (0, 0, 1))),
    ((0, 0, -1), ((0, 0, 0), (0, 1, 0), (1, 1, 0), (1, 0, 0))),
)


class Builder:
    def __init__(self):
        self.colors = []
        self.nodes = []          # dicts glTF
        self.meshes = []
        self.data = bytearray()
        self.views = []
        self.accessors = []
        self.animations = []

    def _color(self, rgb):
        rgb = tuple(int(c) for c in rgb[:3])
        if rgb not in self.colors:
            self.colors.append(rgb)
        i = self.colors.index(rgb)
        return ((i % 16) + 0.5) / 16, ((i // 16) + 0.5) / 16

    def _acc(self, flat, count, kind, comp=5126, fmt="f", target=None, mm=None):
        while len(self.data) % 4:
            self.data.append(0)
        off = len(self.data)
        self.data.extend(struct.pack(f"<{len(flat)}{fmt}", *flat))
        v = {"buffer": 0, "byteOffset": off, "byteLength": len(flat) * 4}
        if target:
            v["target"] = target
        self.views.append(v)
        a = {"bufferView": len(self.views) - 1, "componentType": comp, "count": count, "type": kind}
        if mm:
            a["min"], a["max"] = mm
        self.accessors.append(a)
        return len(self.accessors) - 1

    def part(self, name, boxes, parent=None, translation=(0, 0, 0)):
        """boxes: [((x0,y0,z0),(x1,y1,z1),(r,g,b))] relativas ao nó."""
        pos, uv, idx = [], [], []
        for lo, hi, rgb in boxes:
            u = self._color(rgb)
            for _n, corners in _FACES:
                b = len(pos)
                for cx, cy, cz in corners:
                    pos.append((hi[0] if cx else lo[0], hi[1] if cy else lo[1], hi[2] if cz else lo[2]))
                    uv.append(u)
                idx += [b, b + 1, b + 2, b, b + 2, b + 3]
        node = {"name": name, "translation": list(translation)}
        if pos:
            pa = self._acc([c for p in pos for c in p], len(pos), "VEC3", target=34962,
                           mm=([min(p[i] for p in pos) for i in range(3)],
                               [max(p[i] for p in pos) for i in range(3)]))
            ua = self._acc([c for t in uv for c in t], len(uv), "VEC2", target=34962)
            ia = self._acc(idx, len(idx), "SCALAR", comp=5125, fmt="I", target=34963)
            self.meshes.append({"primitives": [{"attributes": {"POSITION": pa, "TEXCOORD_0": ua},
                                                "indices": ia, "material": 0}]})
            node["mesh"] = len(self.meshes) - 1
        self.nodes.append(node)
        i = len(self.nodes) - 1
        if parent is not None:
            self.nodes[parent].setdefault("children", []).append(i)
        return i

    def animation(self, name, times, tracks):
        """tracks: [(nó, "rotation"|"translation"|"scale", [valores por tempo])]"""
        ti = self._acc(list(times), len(times), "SCALAR", mm=([min(times)], [max(times)]))
        samplers, channels = [], []
        for node, path, vals in tracks:
            kind = "VEC4" if path == "rotation" else "VEC3"
            oa = self._acc([c for v in vals for c in v], len(vals), kind)
            samplers.append({"input": ti, "output": oa, "interpolation": "LINEAR"})
            channels.append({"sampler": len(samplers) - 1, "target": {"node": node, "path": path}})
        self.animations.append({"name": name, "samplers": samplers, "channels": channels})

    def gltf(self, generator):
        im = Image.new("RGBA", (16, 16), (255, 255, 255, 255))
        for i, c in enumerate(self.colors):
            im.putpixel((i % 16, i // 16), c + (255,))
        png = io.BytesIO()
        im.save(png, "PNG")
        roots = [i for i in range(len(self.nodes))
                 if not any(i in n.get("children", []) for n in self.nodes)]
        g = {"asset": {"version": "2.0", "generator": generator}, "scene": 0,
             "scenes": [{"nodes": roots}], "nodes": self.nodes, "meshes": self.meshes,
             "materials": [{"name": "paleta", "pbrMetallicRoughness": {
                 "baseColorTexture": {"index": 0}, "metallicFactor": 0, "roughnessFactor": 1},
                 "doubleSided": True}],
             "textures": [{"source": 0, "sampler": 0}],
             "samplers": [{"magFilter": 9728, "minFilter": 9728, "wrapS": 33071, "wrapT": 33071}],
             "images": [{"uri": "data:image/png;base64," + base64.b64encode(png.getvalue()).decode()}],
             "buffers": [{"byteLength": len(self.data),
                          "uri": "data:application/octet-stream;base64," + base64.b64encode(bytes(self.data)).decode()}],
             "bufferViews": self.views, "accessors": self.accessors}
        if self.animations:
            g["animations"] = self.animations
        return json.dumps(g, separators=(",", ":"))
