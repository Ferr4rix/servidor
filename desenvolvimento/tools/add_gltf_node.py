"""Copia ossos (nós sem malha) de um .gltf para outro, pelo nome do pai.

    python tools/add_gltf_node.py origem.gltf destino.gltf "Right Item" ["Left Item" ...]

Serve para pôr no player.gltf (exportado pelo Blockbench) um osso novo feito
no player.bbmodel (ex.: "Right Item", onde a arma fica presa), sem mexer no
resto do modelo. O nó novo fica com a posição/rotação que tem na origem e é
pendurado no osso do destino com o mesmo nome do pai dele na origem. Um nó com
o mesmo nome no destino é atualizado (não duplica).

Depois copie as animações com tools/copy_gltf_animation.py: os canais do osso
novo passam a ter onde cair.
"""

import json
import sys
from pathlib import Path


def _bones(doc):
    """nome -> índice do nó-osso (o que tem filhos, no export do Blockbench)."""
    out = {}
    for i, n in enumerate(doc["nodes"]):
        name = n.get("name")
        if name is None:
            continue
        if n.get("children") or name not in out:
            out[name] = i
    return out


def _parents(doc):
    out = {}
    for i, n in enumerate(doc["nodes"]):
        for c in n.get("children", []):
            out[c] = i
    return out


def add(src_path, dst_path, names):
    src = json.loads(Path(src_path).read_text(encoding="utf-8"))
    dst = json.loads(Path(dst_path).read_text(encoding="utf-8"))
    sb, sp = _bones(src), _parents(src)
    done = []
    for name in names:
        if name not in sb:
            print(f"  {name!r} não existe na origem")
            continue
        node = src["nodes"][sb[name]]
        parent_name = src["nodes"][sp[sb[name]]].get("name") if sb[name] in sp else None
        db = _bones(dst)
        if parent_name not in db:
            print(f"  pai {parent_name!r} de {name!r} não existe no destino")
            continue
        new = {"name": name}
        for key in ("translation", "rotation", "scale"):
            if key in node:
                new[key] = list(node[key])
        if name in db and "mesh" not in dst["nodes"][db[name]]:
            keep = dst["nodes"][db[name]].get("children")
            dst["nodes"][db[name]] = dict(new, **({"children": keep} if keep else {}))
        else:
            dst["nodes"].append(new)
            parent = dst["nodes"][db[parent_name]]
            parent.setdefault("children", []).append(len(dst["nodes"]) - 1)
        done.append(name)
    Path(dst_path).write_text(json.dumps(dst), encoding="utf-8")
    return done


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)
    print("ossos:", add(sys.argv[1], sys.argv[2], sys.argv[3:]))
