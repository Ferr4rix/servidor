"""Monta o zip para os jogadores a partir da pasta do cliente.

    python empacotar_cliente.py

Gera ``servidor/desenvolvimento/dist/jogo_v0.6.zip`` com a pasta
``jogoV0.6/cliente`` (pasta "jogo" dentro do zip), sem .venv, logs e caches.
Antes, confere o ``cliente/servidor.json``: tem de estar em "modo": "online"
e apontar para o endereço PÚBLICO do servidor (não localhost).
"""
from __future__ import annotations

import json
import sys
import zipfile

from caminhos import CLIENTE, DEV

VERSAO = "0.6"
OUT = DEV / "dist" / f"jogo_v{VERSAO}.zip"
TOP = "jogo"
SKIP_DIRS = {".venv", "logs", "__pycache__", ".git"}
SKIP_FILES = {".gitignore", "perfil_fps.txt", "players.json", "rooms.json", "containers.json"}


def main() -> int:
    for exe in ("Instalar.exe", "Jogar.exe"):
        if not (CLIENTE / exe).is_file():
            print(f"Falta {exe}: rode antes  python compilar_launchers.py")
            return 1
    cfg = json.loads((CLIENTE / "servidor.json").read_text(encoding="utf-8"))
    if cfg.get("modo") != "online":
        print('cliente/servidor.json não está em "modo": "online"')
        return 1
    if "localhost" in str(cfg.get("http", "")) or cfg.get("enet_host") in ("localhost", "127.0.0.1"):
        print("ATENÇÃO: cliente/servidor.json aponta para localhost — os jogadores não vão conseguir ligar.")
        if "--mesmo-assim" not in sys.argv:
            print("Mude o endereço (ou rode com --mesmo-assim para testar no seu computador).")
            return 1
    OUT.parent.mkdir(exist_ok=True)
    n = 0
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(CLIENTE.rglob("*")):
            rel = path.relative_to(CLIENTE)
            if path.is_dir() or any(p in SKIP_DIRS for p in rel.parts):
                continue
            if path.name in SKIP_FILES or path.suffix in (".pyc", ".log", ".zip"):
                continue
            z.write(path, f"{TOP}/{rel.as_posix()}")
            n += 1
    print(f"{OUT} ({n} ficheiros, {OUT.stat().st_size // (1024 * 1024)} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
