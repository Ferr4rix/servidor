"""Registry dos itens definidos em ``itens.json``.

Os blocos continuam definidos em ``blocks.json``; este módulo gere
itens de inventário com ícones, modelos e a indicação de forma física.

O campo ``hand`` de cada item controla o modelo 3D mostrado entre as mãos do
jogador (ver ``player_advanced/held_item.py``):

    "hand": {
        "model":    null,            # null/"block"/"item" = modelo do próprio item
        "position": [0, 0, 0.12],    # mover  (x direita, y cima, z frente)
        "rotation": [0, 0, 0],       # girar  (graus em x, y, z)
        "scale":    0.4,             # aumentar (número ou [x, y, z])
        "grip":     "both"           # "both" (entre as mãos), "right" ou "left"
    }

``grip`` "right"/"left" prende o item só nessa mão (ex.: espadas): os braços não
fazem a pose ``hold_block`` e o item segue o antebraço em todas as animações.
A origem (0, 0, 0) do modelo fica no punho; ``position``/``rotation`` contam a
partir daí, nos eixos do corpo com o braço caído (x direita, y cima, z frente).

As armas (``armas`` em item_balancing.json) já não usam ``attack_clips`` nem
``attack_blade``: os golpes vêm de combate.json e a arma fica presa ao osso
``Right Item`` do player.gltf, alinhada sozinha (``hand.roll`` gira-a à volta
da lâmina). Os dois campos ainda são lidos, por compatibilidade.

``position`` é relativa ao ponto exatamente entre as duas mãos. ``scale`` 1.0
faz a maior dimensão do modelo medir 1 unidade do modelo do jogador.
O ficheiro é relido sozinho quando é guardado (ver ``reload_if_changed``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_JSON_PATH = PROJECT_ROOT / "itens.json"


DEFAULT_HAND_POSITION = (0.0, 0.0, 0.12)
DEFAULT_HAND_ROTATION = (0.0, 0.0, 0.0)
DEFAULT_HAND_SCALE = (0.4, 0.4, 0.4)
HAND_GRIPS = ("both", "right", "left")


@dataclass(frozen=True)
class ItemType:
    """Metadados de um item carregado de ``itens.json``."""

    name: str
    uid: int = 0          # "id" numérico de itens.json (é o que vai para o banco)
    icon: str = ""
    model: str = ""
    corporeal: str = "none"
    item_type: str = "item"
    block_id: str = ""
    hand_model: str = ""
    hand_position: tuple = DEFAULT_HAND_POSITION
    hand_rotation: tuple = DEFAULT_HAND_ROTATION
    hand_scale: tuple = DEFAULT_HAND_SCALE
    hand_grip: str = "both"
    attack_clips: tuple = ()
    attack_blade: Dict[str, tuple] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def model_path(self) -> Optional[Path]:
        return resolve_asset_path(self.model) if self.model else None

    @property
    def is_block(self) -> bool:
        return self.item_type.casefold() == "block" and bool(self.block_id)

    @property
    def one_handed(self) -> bool:
        """True quando o item fica numa só mão (``hand.grip`` right/left)."""
        return self.hand_grip in ("right", "left")

    @property
    def is_weapon(self) -> bool:
        return self.item_type.casefold() == "weapon"

    @property
    def hand_model_path(self) -> Optional[Path]:
        ref = self.hand_model.strip()
        if not ref or ref.casefold() in {"block", "item"}:
            return None
        return resolve_asset_path(ref)

    @property
    def has_world_form(self) -> bool:
        """False para itens guardados apenas no inventário (ex.: ``none``)."""
        return self.corporeal.strip().casefold() not in {"", "none", "false", "0"}


def resolve_asset_path(value: str | Path) -> Path:
    """Resolve um caminho relativo à raiz do projeto, independentemente do cwd."""
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


_ARMOR_ITEM_TO_SLOT = {
    "elmo 1": "head",
    "peitoral 1": "chest",
    "calça 1": "legs",
    "bota 1": "feet",
    "elmo 2": "head",
    "peitoral 2": "chest",
    "calça 2": "legs",
    "bota 2": "feet",
}
ARMOR_ITEM_TO_SLOT = dict(_ARMOR_ITEM_TO_SLOT)


def armor_slot_for_item(item_id: str) -> Optional[str]:
    """Retorna o slot de equipamento compatível, ou ``None`` se não for armadura."""
    slot = _ARMOR_ITEM_TO_SLOT.get(str(item_id).strip().casefold())
    if slot is not None:
        return slot
    # Itens novos dizem o slot no itens.json: "armor_slot": head|chest|legs|feet
    try:
        item = get_item_type(item_id)
    except Exception:
        item = None
    if item is not None:
        wanted = str(item.raw.get("armor_slot") or "").strip().casefold()
        if wanted in ("head", "chest", "legs", "feet", "offhand"):
            return wanted
        if item.raw.get("escudo"):
            return "offhand"            # escudo: slot "mão esq." (Shift+clique equipa)
    return None


def fits_equipment_slot(item_id: str, slot: str) -> bool:
    """O item pode ir para o slot de equipamento ``slot``? Além da peça certa,
    o slot "offhand" (mão esquerda) aceita uma arma de uma mão (duas armas)."""
    if armor_slot_for_item(item_id) == slot:
        return True
    if slot != "offhand":
        return False
    try:
        import item_balancing
        if item_balancing.category_of(item_id) != "armas":
            return False
        style = item_balancing.get_text(item_id, "estilo", "1h").strip().lower()
        return style == "1h"
    except Exception:
        return False


# Tipos de item que nunca empilham (cada um ocupa um slot).
UNSTACKABLE_TYPES = frozenset({"armor", "weapon"})


def max_stack_for(item_id: str, default: int = 64) -> int:
    """1 para armaduras e armas; ``default`` para o resto."""
    if armor_slot_for_item(item_id):
        return 1
    item = get_item_type(item_id)
    if item is not None and item.item_type.casefold() in UNSTACKABLE_TYPES:
        return 1
    # "max_stack" em itens.json (ex.: balde 16, balde de água 1)
    if item is not None:
        try:
            explicit = int((item.raw or {}).get("max_stack") or 0)
        except (TypeError, ValueError):
            explicit = 0
        if explicit > 0:
            return explicit
    # Blocos que guardam itens (baús) não acumulam: cada um tem o seu conteúdo.
    block_id = item.block_id if (item is not None and item.is_block) else str(item_id)
    try:
        from blocks import get_block_type
        bt = get_block_type(block_id)
        props = (bt.raw.get("block_properties") or {}) if bt is not None else {}
        # ...nem o minoculus (portal): cada um guarda as suas almas.
        if props.get("container") or props.get("portal"):
            return 1
    except Exception:
        pass
    return default


_ITEMS: Dict[str, ItemType] = {}
_ITEMS_BY_CASEFOLD: Dict[str, ItemType] = {}
_LOADED = False
_SOURCE: Optional[Path] = None
_SOURCE_MTIME = 0.0


def _hand_vector(hand: Dict[str, Any], key: str, default: tuple) -> tuple:
    """Lê um vetor de 3 valores; um número solto vale para os três eixos."""
    value = hand.get(key, default)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return (float(value),) * 3
    try:
        vec = tuple(float(v) for v in value)
    except (TypeError, ValueError):
        return tuple(default)
    return vec[:3] if len(vec) >= 3 else tuple(default)


def _parse_blade_tracks(name, raw) -> Dict[str, tuple]:
    """``{"clipe": [[t, [x, y, z] | null], ...]}`` -> ``{"clipe": ((t, dir|None), ...)}``."""
    if not isinstance(raw, dict):
        return {}
    tracks: Dict[str, tuple] = {}
    for clip, keys in raw.items():
        parsed = []
        for key in keys if isinstance(keys, list) else ():
            try:
                t = float(key[0])
                direction = key[1]
                if direction is not None:
                    direction = tuple(float(v) for v in direction)
                    if len(direction) != 3 or not any(direction):
                        raise ValueError("direção tem de ter 3 números, não todos 0")
                parsed.append((t, direction))
            except (TypeError, ValueError, IndexError) as exc:
                print(f"[itens] {name}: attack_blade.{clip} chave inválida {key!r}: {exc}")
        if parsed:
            tracks[str(clip)] = tuple(sorted(parsed, key=lambda k: k[0]))
    return tracks


def load_registry(path: Optional[str | Path] = None) -> Dict[str, ItemType]:
    """Lê ``itens.json`` (ou o caminho indicado) e substitui o registry atual."""
    global _ITEMS, _ITEMS_BY_CASEFOLD, _LOADED, _SOURCE, _SOURCE_MTIME
    json_path = Path(path) if path is not None else DEFAULT_JSON_PATH
    if not json_path.is_absolute():
        json_path = (PROJECT_ROOT / json_path).resolve()
    import game_config        # online: o itens.json do servidor (ver game_config.py)
    data = game_config.read_json(json_path, {})
    if not isinstance(data, dict):
        raise ValueError(f"A raiz de {json_path} deve ser um objeto JSON")

    registry: Dict[str, ItemType] = {}
    seen_ids: Dict[int, str] = {}
    for name, entry in data.items():
        if not isinstance(entry, dict):
            raise ValueError(f"Os dados do item {name!r} devem ser um objeto JSON")
        hand = entry.get("hand") if isinstance(entry.get("hand"), dict) else {}
        grip = str(hand.get("grip") or "both").strip().casefold()
        if grip not in HAND_GRIPS:
            print(f"[itens] {name}: hand.grip {grip!r} inválido; use {HAND_GRIPS}")
            grip = "both"
        clips = entry.get("attack_clips") or ()
        if isinstance(clips, str):
            clips = [clips]
        clips = tuple(str(c).strip() for c in clips if str(c).strip())
        blade = _parse_blade_tracks(name, entry.get("attack_blade"))
        try:
            uid = int(entry.get("id"))
        except (TypeError, ValueError):
            uid = 0
            print(f"[itens] {name}: sem \"id\" numérico em itens.json")
        if uid and uid in seen_ids:
            print(f"[itens] {name}: id {uid} repetido (já é de {seen_ids[uid]!r})")
            uid = 0
        if uid:
            seen_ids[uid] = str(name)

        registry[str(name)] = ItemType(
            name=str(name),
            uid=uid,
            icon=str(entry.get("icon") or ""),
            model=str(entry.get("model") or ""),
            corporeal=str(entry.get("corporeal") or "none"),
            item_type=str(entry.get("type") or "item"),
            block_id=str(entry.get("block") or ""),
            hand_model=str(hand.get("model") or ""),
            hand_position=_hand_vector(hand, "position", DEFAULT_HAND_POSITION),
            hand_rotation=_hand_vector(hand, "rotation", DEFAULT_HAND_ROTATION),
            hand_scale=_hand_vector(hand, "scale", DEFAULT_HAND_SCALE),
            hand_grip=grip,
            attack_clips=clips,
            attack_blade=blade,
            raw=dict(entry),
        )

    _ITEMS = registry
    _ITEMS_BY_CASEFOLD = {name.casefold(): item for name, item in registry.items()}
    _ITEMS_BY_UID.clear()
    _ITEMS_BY_UID.update({item.uid: item for item in registry.values() if item.uid})
    _LOADED = True
    _SOURCE = json_path
    try:
        _SOURCE_MTIME = json_path.stat().st_mtime
    except OSError:
        _SOURCE_MTIME = 0.0
    print(f"[itens] {len(_ITEMS)} tipos carregados")
    return dict(_ITEMS)


def reload_if_changed() -> bool:
    """Relê ``itens.json`` se o ficheiro foi guardado desde a última leitura.

    Devolve True quando o registry foi recarregado. Se o JSON estiver com erro
    (por exemplo, a meio de uma edição) mantém os dados antigos e só avisa uma vez.
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
        load_registry(_SOURCE)
    except (OSError, ValueError) as exc:
        _SOURCE_MTIME = mtime
        print(f"[itens] {_SOURCE.name} inválido, mantendo a versão anterior: {exc}")
        return False
    return True


def ensure_loaded() -> None:
    """Carrega o registry na primeira utilização."""
    if not _LOADED:
        load_registry()


# Nomes antigos -> nomes atuais (inventários salvos antes da mudança).
LEGACY_ITEM_IDS = {"old-stool": "banco de madeira"}


def canonical_item_id(name) -> str:
    """Nome atual de um item (traduz nomes antigos, ex.: ``old-stool``)."""
    key = str(name).strip()
    return LEGACY_ITEM_IDS.get(key, LEGACY_ITEM_IDS.get(key.casefold(), key))


_ITEMS_BY_UID: Dict[int, "ItemType"] = {}


def item_uid(name) -> Optional[int]:
    """Id numérico do item (o que se grava no banco), ou None."""
    item = get_item_type(name)
    return item.uid if item is not None and item.uid else None


def item_from_ref(ref) -> Optional[str]:
    """Nome do item a partir do que veio gravado: id numérico (17 ou "17")
    ou, em registos antigos, o próprio nome."""
    ensure_loaded()
    if isinstance(ref, bool) or ref is None:
        return None
    if isinstance(ref, (int, float)) or (isinstance(ref, str) and ref.strip().isdigit()):
        item = _ITEMS_BY_UID.get(int(ref))
        return item.name if item is not None else None
    return canonical_item_id(ref)


def item_ref(name):
    """O que gravar para um item: o id numérico (ou o nome, se não tiver id)."""
    uid = item_uid(name)
    return uid if uid else str(name)


def get_item_type(name: str) -> Optional[ItemType]:
    ensure_loaded()
    key = canonical_item_id(name)
    return _ITEMS.get(key) or _ITEMS_BY_CASEFOLD.get(key.casefold())


def all_item_types() -> Dict[str, ItemType]:
    ensure_loaded()
    return dict(_ITEMS)


def block_id_for_item(item_id: str) -> Optional[str]:
    """Converte um item do inventário no ID do bloco que ele representa."""
    item = get_item_type(item_id)
    if item is not None and item.is_block:
        return item.block_id
    # Compatibilidade com inventários antigos que guardavam diretamente IDs de bloco.
    try:
        from blocks import get_block_type, ensure_loaded as ensure_blocks_loaded
        ensure_blocks_loaded()
        return str(item_id) if get_block_type(str(item_id)) is not None else None
    except Exception:
        return None


def item_id_for_block(block_id: str) -> Optional[str]:
    """Retorna o item correspondente a um bloco; fallback para saves antigos."""
    ensure_loaded()
    for item in _ITEMS.values():
        if item.is_block and item.block_id == str(block_id):
            return item.name
    return str(block_id) if block_id else None


__all__ = [
    "ItemType",
    "DEFAULT_JSON_PATH",
    "load_registry",
    "reload_if_changed",
    "ensure_loaded",
    "get_item_type",
    "all_item_types",
    "resolve_asset_path",
    "armor_slot_for_item",
    "fits_equipment_slot",
    "max_stack_for",
    "canonical_item_id",
    "LEGACY_ITEM_IDS",
    "UNSTACKABLE_TYPES",
    "ARMOR_ITEM_TO_SLOT",
    "block_id_for_item",
    "item_id_for_block",
]


# ---------------------------------------------------------------- peso
# Blocos têm "peso" em kg (blocks.json -> block_properties.peso). Na
# mochila cada item pesa O MESMO que no mundo (o que o tooltip diz é o que se
# leva e o que se levanta com Q): terra 15 kg. Itens que não são blocos usam o
# "peso" de item_balancing.json. Um baú pesa ainda o que leva dentro
# (``data["peso_conteudo"]``, em kg). O peso deixa o jogador mais lento
# (Player.load_speed_factor) e, acima de 70% da carga, enfraquece os golpes.
ITEM_WEIGHT_DIVISOR = 1.0
DEFAULT_BLOCK_WEIGHT = 40.0
_WEIGHT_CACHE: Dict[str, float] = {}


def block_weight(block_id) -> float:
    """Peso do bloco no mundo, em kg."""
    try:
        from blocks import get_block_type, ensure_loaded as ensure_blocks_loaded
        ensure_blocks_loaded()
        bt = get_block_type(str(block_id))
    except Exception:
        bt = None
    if bt is None:
        return 0.0
    props = bt.raw.get("block_properties") or {}
    try:
        return max(0.0, float(props.get("peso", DEFAULT_BLOCK_WEIGHT)))
    except (TypeError, ValueError):
        return DEFAULT_BLOCK_WEIGHT


def item_weight(item_id, data=None) -> float:
    """Peso de 1 item no inventário, em kg (com o conteúdo, se for um baú)."""
    key = str(item_id)
    base = _WEIGHT_CACHE.get(key)
    if base is None:
        block_id = block_id_for_item(key)
        if block_id:
            base = block_weight(block_id) / ITEM_WEIGHT_DIVISOR
        else:
            try:
                import item_balancing
                base = float(item_balancing.get_stats(key).get("peso", 0.0)) / ITEM_WEIGHT_DIVISOR
            except Exception:
                base = 0.0
        _WEIGHT_CACHE[key] = base
    extra = 0.0
    if isinstance(data, dict):
        try:
            extra = max(0.0, float(data.get("peso_conteudo", 0.0)))
        except (TypeError, ValueError):
            extra = 0.0
    return base + extra


def entries_weight(entries) -> float:
    """Peso (kg de inventário) de uma lista de conteúdo de baú [ref, qtd, dados?]."""
    total = 0.0
    for e in entries or []:
        if not e:
            continue
        name = item_from_ref(e[0])
        if not name:
            continue
        try:
            count = int(e[1])
        except (TypeError, ValueError, IndexError):
            continue
        data = e[2] if len(e) > 2 and isinstance(e[2], dict) else None
        total += item_weight(name, data) * count
    return total
