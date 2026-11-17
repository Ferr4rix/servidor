"""Atributos de balanceamento dos itens, lidos de ``item_balancing.json``.

``itens.json`` diz como o item aparece (ícone, modelo, mão); este ficheiro diz
quanto ele vale em jogo. Os itens ficam agrupados por categoria, e cada item
guarda os atributos que quiser::

    {
      "armaduras": {
        "elmo 1": {"resistencia": 5, "peso": 2.5}
      },
      "armas": {
        "espada 1": {"dano": 7, "peso": 3.0}
      },
      "outros": {}
    }

As categorias servem apenas para organizar: a busca é feita pelo ID do item
(o mesmo de ``itens.json``), sem diferenciar maiúsculas. Um atributo ausente
vale 0. O ficheiro é relido sozinho quando é guardado (``reload_if_changed``).

Os atributos são números; os de texto (ex.: ``"estilo": "2h"`` das armas, ver
combate.json) ficam à parte e leem-se com ``get_text``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Iterable, Optional


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_JSON_PATH = PROJECT_ROOT / "item_balancing.json"

_STATS: Dict[str, Dict[str, float]] = {}      # id em casefold -> atributos
_TEXTS: Dict[str, Dict[str, str]] = {}        # id em casefold -> atributos de texto
TEXT_ATTRS = ("estilo",)                      # atributos que são texto, não número
_CATEGORY: Dict[str, str] = {}                # id em casefold -> categoria
_LOADED = False
_SOURCE: Optional[Path] = None
_SOURCE_MTIME = 0.0


def _number(value) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None


def load_balancing(path: Optional[str | Path] = None) -> None:
    """Lê ``item_balancing.json`` e substitui os atributos atuais."""
    global _STATS, _TEXTS, _CATEGORY, _LOADED, _SOURCE, _SOURCE_MTIME
    json_path = Path(path) if path is not None else DEFAULT_JSON_PATH
    if not json_path.is_absolute():
        json_path = (PROJECT_ROOT / json_path).resolve()
    import game_config        # online: o do servidor (ver game_config.py)
    data = game_config.read_json(json_path, {})
    if not isinstance(data, dict):
        raise ValueError(f"A raiz de {json_path} deve ser um objeto JSON")

    stats: Dict[str, Dict[str, float]] = {}
    texts: Dict[str, Dict[str, str]] = {}
    categories: Dict[str, str] = {}
    for category, items in data.items():
        if not isinstance(items, dict):
            raise ValueError(f"A categoria {category!r} deve ser um objeto JSON")
        for item_id, attrs in items.items():
            if not isinstance(attrs, dict):
                raise ValueError(f"Os atributos de {item_id!r} devem ser um objeto JSON")
            clean = {}
            words = {}
            for name, value in attrs.items():
                if str(name).strip() in TEXT_ATTRS:
                    words[str(name).strip()] = str(value).strip()
                    continue
                number = _number(value)
                if number is None:
                    print(f"[balanceamento] {item_id}.{name} não é número: {value!r}")
                    continue
                clean[str(name).strip()] = number
            key = str(item_id).strip().casefold()
            if key in stats:
                print(f"[balanceamento] {item_id!r} aparece mais de uma vez; vale a última")
            stats[key] = clean
            texts[key] = words
            categories[key] = str(category)

    _STATS, _TEXTS, _CATEGORY = stats, texts, categories
    _LOADED = True
    _SOURCE = json_path
    try:
        _SOURCE_MTIME = json_path.stat().st_mtime
    except OSError:
        _SOURCE_MTIME = 0.0
    print(f"[balanceamento] {len(_STATS)} itens carregados")


def ensure_loaded() -> None:
    """Carrega na primeira utilização; sem ficheiro, todos os atributos valem 0."""
    global _LOADED
    if _LOADED:
        return
    try:
        load_balancing()
    except (OSError, ValueError) as exc:
        print(f"[balanceamento] não foi possível ler {DEFAULT_JSON_PATH.name}: {exc}")
        _LOADED = True


def reload_if_changed() -> bool:
    """Relê o ficheiro se foi guardado desde a última leitura.

    Com o JSON inválido (por exemplo, a meio de uma edição) mantém os valores
    anteriores e avisa no console.
    """
    global _SOURCE_MTIME
    import game_config
    if not _LOADED or _SOURCE is None or game_config.locked(_SOURCE):
        return False
    try:
        mtime = _SOURCE.stat().st_mtime
    except OSError:
        return False
    if mtime == _SOURCE_MTIME:
        return False
    try:
        load_balancing(_SOURCE)
    except (OSError, ValueError) as exc:
        _SOURCE_MTIME = mtime
        print(f"[balanceamento] {_SOURCE.name} inválido, mantendo a versão anterior: {exc}")
        return False
    return True


def get_stats(item_id: Optional[str]) -> Dict[str, float]:
    """Todos os atributos do item (cópia); ``{}`` se ele não estiver no ficheiro."""
    ensure_loaded()
    if not item_id:
        return {}
    return dict(_STATS.get(str(item_id).strip().casefold(), {}))


def get_text(item_id: Optional[str], name: str, default: str = "") -> str:
    """Um atributo de texto do item, ex.: ``get_text("montante", "estilo")`` -> "2h"."""
    ensure_loaded()
    if not item_id:
        return default
    return _TEXTS.get(str(item_id).strip().casefold(), {}).get(name, default)


def get_stat(item_id: Optional[str], name: str, default: float = 0.0) -> float:
    """Um atributo do item, ex.: ``get_stat("elmo 1", "resistencia")`` -> 5.0."""
    return get_stats(item_id).get(name, default)


def category_of(item_id: Optional[str]) -> Optional[str]:
    ensure_loaded()
    if not item_id:
        return None
    return _CATEGORY.get(str(item_id).strip().casefold())


def sum_stats(item_ids: Iterable[Optional[str]]) -> Dict[str, float]:
    """Soma atributo a atributo, ex.: as quatro peças de armadura vestidas."""
    total: Dict[str, float] = {}
    for item_id in item_ids:
        for name, value in get_stats(item_id).items():
            total[name] = total.get(name, 0.0) + value
    return total


__all__ = [
    "DEFAULT_JSON_PATH",
    "load_balancing",
    "ensure_loaded",
    "reload_if_changed",
    "get_stats",
    "get_text",
    "get_stat",
    "category_of",
    "sum_stats",
]
