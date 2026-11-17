"""Copia animações de um .gltf para outro (os ossos casam pelo nome).

    python tools/copy_gltf_animation.py origem.gltf destino.gltf roll [outra ...]

Serve para pôr no player.gltf (exportado pelo Blockbench) uma animação nova
feita no player.bbmodel e convertida com tools/bbmodel_to_gltf.py, sem mexer
no resto do modelo. Uma animação com o mesmo nome no destino é substituída.
"""

import base64
import json
import struct
import sys
from pathlib import Path


def _buf(doc, base):
    uri = doc["buffers"][0]["uri"]
    if uri.startswith("data:"):
        return bytearray(base64.b64decode(uri.split(",", 1)[1]))
    return bytearray((base / uri).read_bytes())


def _bone_index(doc):
    """nome -> índice do nó-osso (o que tem filhos, no export do Blockbench;
    o próprio nó, nos do nosso conversor)."""
    out = {}
    for i, n in enumerate(doc["nodes"]):
        name = n.get("name")
        if name is None:
            continue
        if n.get("children") or name not in out:
            out[name] = i
    return out


def copy(src_path, dst_path, names):
    src = json.loads(Path(src_path).read_text(encoding="utf-8"))
    dst = json.loads(Path(dst_path).read_text(encoding="utf-8"))
    sbuf, dbuf = _buf(src, Path(src_path).parent), _buf(dst, Path(dst_path).parent)
    dst_bones = _bone_index(dst)

    def copy_accessor(i):
        a = dict(src["accessors"][i])
        v = src["bufferViews"][a["bufferView"]]
        data = sbuf[v.get("byteOffset", 0): v.get("byteOffset", 0) + v["byteLength"]]
        while len(dbuf) % 4:
            dbuf.append(0)
        dst["bufferViews"].append({"buffer": 0, "byteOffset": len(dbuf), "byteLength": len(data)})
        dbuf.extend(data)
        a["bufferView"] = len(dst["bufferViews"]) - 1
        dst["accessors"].append(a)
        return len(dst["accessors"]) - 1

    done = []
    for anim in src.get("animations", []):
        if anim.get("name") not in names:
            continue
        samplers, channels = [], []
        for ch in anim["channels"]:
            name = src["nodes"][ch["target"]["node"]].get("name")
            if name not in dst_bones:
                print(f"  (osso {name!r} não existe no destino: ignorado)")
                continue
            s = anim["samplers"][ch["sampler"]]
            samplers.append({"input": copy_accessor(s["input"]),
                             "output": copy_accessor(s["output"]),
                             "interpolation": s.get("interpolation", "LINEAR")})
            channels.append({"sampler": len(samplers) - 1,
                             "target": {"node": dst_bones[name], "path": ch["target"]["path"]}})
        dst["animations"] = [a for a in dst.get("animations", []) if a.get("name") != anim["name"]]
        dst["animations"].append({"name": anim["name"], "samplers": samplers,
                                  "channels": channels, **({"extras": anim["extras"]} if anim.get("extras") else {})})
        done.append(anim["name"])
    dst["buffers"][0] = {"byteLength": len(dbuf),
                         "uri": "data:application/octet-stream;base64," + base64.b64encode(bytes(dbuf)).decode()}
    Path(dst_path).write_text(json.dumps(dst), encoding="utf-8")
    return done


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)
    print("copiadas:", copy(sys.argv[1], sys.argv[2], set(sys.argv[3:])))
