"""Camada de acesso ao back-end.

``make_backend()`` escolhe pelo ``servidor.json`` (na raiz do projeto):

    {"modo": "online", "http": "http://localhost:3000",
     "enet_host": "localhost", "enet_port": 7777}

* ``"online"`` — servidor Node (Express + ENet) com MongoDB Atlas
  (``RemoteBackend``): login, mundos e saves no servidor; multiplayer.
* ``"local"`` — ficheiros ``rooms.json``/``players.json`` (``LocalBackend``),
  um jogador, sem servidor (o modo da v0.4).
"""

import json
from pathlib import Path

from .base import (
    AuthError, Backend, BackendError, PlayerSession, RoomNotFoundError,
)
from .local import LocalBackend
from .passwords import hash_password, verify_password

CONFIG_PATH = Path(__file__).resolve().parent.parent / "servidor.json"


def load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"modo": "local"}


def make_backend() -> Backend:
    cfg = load_config()
    if str(cfg.get("modo", "local")).lower() == "online":
        from .remote import RemoteBackend
        print(f"[online] servidor {cfg.get('http', 'http://localhost:3000')} "
              f"(ENet {cfg.get('enet_host', 'localhost')}:{cfg.get('enet_port', 7777)})")
        return RemoteBackend(cfg.get("http", "http://localhost:3000"),
                             cfg.get("enet_host", "localhost"),
                             int(cfg.get("enet_port", 7777)))
    print("[local] modo local (rooms.json / players.json)")
    return LocalBackend()


__all__ = [
    "AuthError", "Backend", "BackendError", "PlayerSession",
    "RoomNotFoundError", "LocalBackend", "hash_password", "verify_password",
    "make_backend", "load_config",
]
