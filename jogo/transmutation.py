"""Receitas da pedra de transmutação (``receitas.json``).

Cada receita: o item feito, os materiais (id do item -> quantidade), as almas
gastas e quantos itens saem::

    "pedra solar": {"almas": 40, "quantidade": 1, "desgaste": 4}

``desgaste``: quanto a pedra de transmutação gasta da durabilidade dela
(``item_balancing.json``); sem ele, ``default_wear`` (1 a cada 10 almas, mais
meio por material, no mínimo 1). Quando a durabilidade chega a 0, a pedra
quebra.

Os materiais vêm dos slots da janela de transmutação (o jogador põe lá o que
quer gastar); as almas vêm do total do jogador. Chaves que começam por ``_``
são comentários. O ficheiro é relido quando muda.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

PATH = Path(__file__).resolve().parent / "receitas.json"

_CACHE: Tuple[float, List["Recipe"]] = (-1.0, [])


@dataclass
class Recipe:
    item: str
    materials: Dict[str, int] = field(default_factory=dict)
    souls: int = 0
    amount: int = 1
    wear: int = 1                  # desgaste da pedra por transmutação


def default_wear(souls: int, materials: Dict[str, int]) -> int:
    """Desgaste de uma receita sem "desgaste": 1 a cada 10 almas, mais meio
    por material gasto, no mínimo 1."""
    return max(1, int(math.ceil(souls / 10 + 0.5 * sum(materials.values()))))


def load_recipes(path: Optional[Path] = None) -> List[Recipe]:
    global _CACHE
    import game_config        # online: as receitas do servidor (ver game_config.py)
    p = Path(path) if path else PATH
    if game_config.locked(p):
        mtime = "servidor:" + str(game_config.version())
    else:
        try:
            mtime = p.stat().st_mtime
        except OSError:
            return []
    if path is None and _CACHE[0] == mtime:
        return _CACHE[1]
    try:
        data = game_config.read_json(p)
    except (OSError, ValueError) as exc:
        print(f"[transmutação] {p.name} inválido: {exc}")
        return _CACHE[1]
    out = []
    for item, cfg in data.items():
        if str(item).startswith("_") or not isinstance(cfg, dict):
            continue
        mats = {}
        for mid, n in (cfg.get("materiais") or {}).items():
            try:
                if int(n) > 0:
                    mats[str(mid)] = int(n)
            except (TypeError, ValueError):
                continue
        try:
            souls = max(0, int(cfg.get("almas", 0) or 0))
            amount = max(1, int(cfg.get("quantidade", 1) or 1))
            wear = cfg.get("desgaste")
            wear = max(0, int(wear)) if wear is not None else default_wear(souls, mats)
        except (TypeError, ValueError):
            continue
        out.append(Recipe(str(item), mats, souls, amount, wear))
    if path is None:
        _CACHE = (mtime, out)
    return out


def count_materials(stacks) -> Dict[str, int]:
    have: Dict[str, int] = {}
    for st in stacks or ():
        if st is not None:
            have[st.item_id] = have.get(st.item_id, 0) + int(st.count)
    return have


def missing(recipe: Recipe, stacks, souls: int) -> List[Tuple[str, int, int]]:
    """O que falta: [(material ou "almas", preciso, tenho)]; vazio = dá."""
    have = count_materials(stacks)
    out = []
    for mid, need in recipe.materials.items():
        if have.get(mid, 0) < need:
            out.append((mid, need, have.get(mid, 0)))
    if souls < recipe.souls:
        out.append(("almas", recipe.souls, int(souls)))
    return out


def consume(recipe: Recipe, stacks) -> None:
    """Tira os materiais dos slots (a lista é alterada no lugar)."""
    for mid, need in recipe.materials.items():
        for i, st in enumerate(stacks):
            if need <= 0:
                break
            if st is None or st.item_id != mid:
                continue
            take = min(need, st.count)
            st.count -= take
            need -= take
            if st.count <= 0:
                stacks[i] = None


__all__ = ["Recipe", "load_recipes", "count_materials", "missing", "consume", "default_wear", "PATH"]
