"""Copia da pasta ../cliente o código do jogo que o servidor também usa.

A física dos blocos, a água, o fogo, o capim e o "livro" dos jogadores correm
no servidor com o MESMO código Python do jogo (pasta ``jogo/``). Sempre que
mudar um destes ficheiros no cliente, rode isto antes de
``docker compose up --build``:

    python sincronizar_do_cliente.py           (copia e diz o que mudou)
    python sincronizar_do_cliente.py --ver     (só diz o que está diferente)

As configurações do jogo (itens, blocos, mobs, receitas, combate, economia)
NÃO vêm daqui: vivem só em ``config/`` (o servidor manda-as aos jogos).
"""
from __future__ import annotations

import filecmp
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CLIENTE = HERE.parent / "cliente"
JOGO = HERE / "jogo"

FILES = [
    "CRUD_room.py", "color_utils.py", "physics_units.py", "game_config.py",
    "items.py", "item_balancing.py", "item_durability.py", "transmutation.py", "mob_types.py",
    "engine/__init__.py", "engine/block_physics.py", "engine/impacts.py", "engine/grass.py",
    "engine/apples.py", "engine/topple_math.py", "engine/torque.py", "engine/fire.py",
    "engine/corpses.py", "engine/tufts.py",
]
DIRS = ["blocks", "backend"]
MODELS = "assets/models/mobs"          # caixas de colisão dos mobs (só .gltf)


def wanted():
    out = list(FILES)
    for d in DIRS:
        out += [p.relative_to(CLIENTE).as_posix() for p in sorted((CLIENTE / d).rglob("*.py"))
                if "__pycache__" not in p.parts]
    out += [p.relative_to(CLIENTE).as_posix() for p in sorted((CLIENTE / MODELS).rglob("*.gltf"))]
    return out


def main() -> int:
    if not CLIENTE.is_dir():
        print(f"Não achei a pasta do cliente em {CLIENTE}")
        return 1
    only_look = "--ver" in sys.argv
    changed = 0
    for rel in wanted():
        src, dst = CLIENTE / rel, JOGO / rel
        if not src.is_file():
            print(f"  falta no cliente: {rel}")
            continue
        if dst.is_file() and filecmp.cmp(src, dst, shallow=False):
            continue
        changed += 1
        print(f"  {'diferente' if only_look else 'copiado'}: {rel}")
        if not only_look:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    print("Nada mudou." if not changed else
          (f"{changed} ficheiro(s) diferentes." if only_look else
           f"{changed} ficheiro(s) copiados. Agora: docker compose up --build"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
