"""Configurações do jogo (itens, receitas, blocos, mobs, combate).

Online, o servidor manda as configurações ao entrar (``GET /api/config``) e o
jogo usa SÓ essas, na memória: mexer nos ``.json`` da pasta do jogo não muda
nada (nem itens, nem receitas, nem dano). Offline (``servidor.json`` com
``"modo": "local"``) os ficheiros da pasta continuam a valer, como antes.

Os módulos que leem configurações chamam ``read_json(caminho)`` em vez de
abrir o ficheiro, e ``locked(caminho)`` diz se é para ignorar mudanças no
disco (recarregar a quente só offline).
"""

from __future__ import annotations

import copy
import json
import os
from typing import Any, Dict, Optional

_BUNDLE: Dict[str, Any] = {}
_VERSION: Optional[str] = None
TEST_MODE = False                # o servidor aceita F6/F7/F8?


def _name(path) -> str:
    return os.path.basename(str(path))


def active() -> bool:
    return bool(_BUNDLE)


def version() -> Optional[str]:
    return _VERSION


def locked(path) -> bool:
    """True se este ficheiro vem do servidor (mudanças no disco não contam)."""
    return _name(path) in _BUNDLE


def read_json(path, default=None):
    """O conteúdo de ``path``: o do servidor se houver, senão o do disco.
    Sem nenhum dos dois devolve ``default`` (ou levanta o erro do disco)."""
    name = _name(path)
    if name in _BUNDLE:
        return copy.deepcopy(_BUNDLE[name])
    try:
        with open(path, "r", encoding="utf-8") as fp:
            return json.load(fp)
    except FileNotFoundError:
        if default is not None:
            return default
        raise


def install(bundle: dict) -> None:
    """Usa as configurações do servidor e recarrega quem já tinha lido."""
    global _BUNDLE, _VERSION, TEST_MODE
    files = (bundle or {}).get("files") or {}
    if not isinstance(files, dict) or not files:
        raise ValueError("o servidor não mandou as configurações do jogo")
    _BUNDLE = {str(k): v for k, v in files.items()}
    _VERSION = bundle.get("versao")
    TEST_MODE = bool(bundle.get("testes"))
    _reload_all()
    print(f"[config] configurações do servidor ({_VERSION}): {', '.join(sorted(_BUNDLE))}")


def update_balancing(bundle: dict) -> None:
    """Balanceamento novo a meio do jogo (painel dos moderadores): só os
    números dos itens mudam na hora; o resto vale ao entrar de novo."""
    global _VERSION
    files = (bundle or {}).get("files") or {}
    if "item_balancing.json" not in files:
        return
    _BUNDLE["item_balancing.json"] = files["item_balancing.json"]
    _VERSION = bundle.get("versao")
    import item_balancing
    item_balancing.load_balancing()


def _reload_all() -> None:
    try:
        import items
        items.load_registry()
        items._WEIGHT_CACHE.clear()
    except Exception as exc:
        print(f"[config] itens: {exc}")
    try:
        import item_balancing
        item_balancing.load_balancing()
    except Exception as exc:
        print(f"[config] balanceamento: {exc}")
    try:
        import blocks
        blocks.load_registry()
    except Exception as exc:
        print(f"[config] blocos: {exc}")
    try:
        import mob_types
        mob_types.load()
    except Exception as exc:
        print(f"[config] mobs: {exc}")
    try:
        import transmutation
        transmutation._CACHE = (None, [])
    except Exception:
        pass
    try:
        from player_advanced import combat
        combat._cache.update(mtime=None, cfg=None)
    except Exception:
        pass
    try:
        from backend import mobs as local_mobs
        local_mobs._BLOCKS = None
    except Exception:
        pass


__all__ = ["install", "read_json", "locked", "active", "version", "TEST_MODE"]
