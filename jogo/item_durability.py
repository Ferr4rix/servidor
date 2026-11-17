"""Durabilidade e "espaço" dos itens (status de cada unidade).

* **Durabilidade** (armas e armaduras): quanto dano o item aguenta. O máximo
  vem de ``"durabilidade"`` em ``item_balancing.json``; o atual fica nos dados
  da pilha (``stack.data["durabilidade"]``; sem a chave = novo, no máximo).
  - armadura: perde o dano que o jogador leva (repartido pelas peças
    vestidas, conforme a resistência de cada uma);
  - arma: perde o dano que dá, e o dano de um golpe que outro jogador acerte
    em cheio nela (as armas têm caixa de colisão própria).
  Chegando a 0 o item quebra (some).
* **Espaço** (baús e baldes): quanto o item leva dentro.
  - baú: ``data["ocupados"]`` slots usados (de ``slots`` em blocks.json);
  - balde de água: ``data["agua"]`` níveis (1 até a capacidade do balde,
    ``"capacidade"`` em ``itens.json``, 4 = meio bloco; sem a chave = cheio).
"""

from __future__ import annotations

from typing import Optional

KEY = "durabilidade"
WATER_KEY = "agua"
WATER_FULL = 8          # níveis numa célula de água cheia (um bloco)
BUCKET_FULL = 4         # capacidade de um balde (meio bloco); ver bucket_capacity
BUCKET_MIN = 4          # nível mínimo da água para o balde a conseguir apanhar


def bucket_cfg(item_id) -> dict:
    try:
        from items import get_item_type
        item = get_item_type(item_id)
        cfg = item.raw.get("balde") if item is not None else None
        return cfg if isinstance(cfg, dict) else {}
    except Exception:
        return {}


def bucket_capacity(item_id=None) -> int:
    """Níveis que o balde leva (``"capacidade"`` em itens.json; 4 = meio bloco)."""
    try:
        return max(1, int(bucket_cfg(item_id).get("capacidade", BUCKET_FULL)))
    except (TypeError, ValueError):
        return BUCKET_FULL


def bucket_minimum(item_id=None) -> int:
    """Nível mínimo da água para o balde a apanhar (``"minimo"``; água rasa não dá)."""
    try:
        return max(1, int(bucket_cfg(item_id).get("minimo", BUCKET_MIN)))
    except (TypeError, ValueError):
        return BUCKET_MIN


def max_durability(item_id) -> float:
    """Durabilidade máxima (0 = o item não gasta)."""
    if not item_id:
        return 0.0
    import item_balancing
    try:
        return max(0.0, float(item_balancing.get_stat(item_id, KEY, 0.0)))
    except (TypeError, ValueError):
        return 0.0


def durability(stack) -> Optional[float]:
    """Durabilidade atual da unidade (None se o item não gasta)."""
    if stack is None:
        return None
    top = max_durability(stack.item_id)
    if top <= 0:
        return None
    try:
        cur = float((stack.data or {}).get(KEY, top))
    except (TypeError, ValueError):
        cur = top
    return max(0.0, min(top, cur))


def wear(stack, amount) -> bool:
    """Gasta ``amount`` de durabilidade. Devolve True se o item quebrou."""
    cur = durability(stack)
    if cur is None or amount <= 0:
        return False
    cur = max(0.0, cur - float(amount))
    stack.data = dict(stack.data or {}, **{KEY: round(cur, 2)})
    return cur <= 0.0


def water_level(stack) -> int:
    """Níveis de água no balde (0 no balde vazio)."""
    if stack is None:
        return 0
    cfg = bucket_cfg(stack.item_id)
    if cfg.get("cheio"):                 # balde vazio
        return 0
    cap = bucket_capacity(stack.item_id)
    try:
        return max(0, min(cap, int((stack.data or {}).get(WATER_KEY, cap))))
    except (TypeError, ValueError):
        return cap


def fmt(v: float) -> str:
    v = float(v)
    return str(int(round(v))) if abs(v - round(v)) < 0.05 else f"{v:.1f}"


__all__ = ["KEY", "WATER_KEY", "WATER_FULL", "BUCKET_FULL", "BUCKET_MIN", "bucket_capacity",
           "bucket_minimum", "bucket_cfg", "max_durability", "durability", "wear",
           "water_level", "fmt"]
