"""Tipos de mob definidos em ``mobs.json`` (lidos pelo servidor e pelo cliente).

Cada entrada descreve um mob: modelo, tamanho, vida, velocidades (em blocos por
segundo), alcances (em blocos), ataque, defesa com escudo, nomes dos clipes de
animação e caixas de colisão. Ver o README ("Mobs") para cada campo.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional

PROJECT_ROOT = Path(__file__).resolve().parent
MOBS_JSON = PROJECT_ROOT / "mobs.json"
MODELS_DIR = PROJECT_ROOT / "assets" / "models"

_TYPES: Dict[str, dict] = {}
_MTIME = None


def load(path=None) -> Dict[str, dict]:
    global _TYPES, _MTIME
    p = Path(path) if path else MOBS_JSON
    import game_config        # online: o mobs.json do servidor (ver game_config.py)
    data = game_config.read_json(p, {})
    _TYPES = {str(k): v for k, v in data.items() if isinstance(v, dict)}
    try:
        _MTIME = p.stat().st_mtime
    except OSError:
        _MTIME = None
    return _TYPES


def all_types() -> Dict[str, dict]:
    if not _TYPES:
        try:
            load()
        except (OSError, ValueError) as exc:
            print(f"[mobs] mobs.json ilegível: {exc}")
    return _TYPES


def get(tipo) -> Optional[dict]:
    return all_types().get(str(tipo))


def model_path(tipo) -> Optional[Path]:
    cfg = get(tipo)
    if not cfg or not cfg.get("model"):
        return None
    ref = Path(str(cfg["model"]))
    for candidate in (ref, MODELS_DIR / ref, PROJECT_ROOT / ref):
        if candidate.is_file():
            return candidate.resolve()
    return None


def world_scale(tipo, block_size: float) -> float:
    """Unidades do mundo por bloco do modelo."""
    cfg = get(tipo) or {}
    return float(cfg.get("scale", 1.0)) * float(block_size)


def display_name(tipo) -> str:
    cfg = get(tipo) or {}
    return str(cfg.get("nome") or tipo)


__all__ = ["load", "all_types", "get", "model_path", "world_scale", "display_name"]
