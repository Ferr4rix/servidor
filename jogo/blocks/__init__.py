"""Pacote de blocos.

Aqui mora o **registry de tipos de bloco** carregado de
`blocks.json`. O JSON é a fonte de verdade: cada chave vira
um `BlockType` com textura(s), cor, flags e formato geométrico.

Uso típico:

    from blocks import load_registry, get_block_type

    load_registry()
    bt = get_block_type("wood")
    if bt.format == "cylindrical":
        ...
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from ursina import color as ursina_color

from color_utils import rgb255


# ------------------------------------------------------------------ paths
_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_HERE)                # ...
DEFAULT_JSON_PATH = os.path.join(_PROJECT_ROOT, "blocks.json")


# ------------------------------------------------------------------ modelo
@dataclass
class BlockType:
    name: str
    texture: Optional[str] = None                 # categoria simple
    texture_sides: Optional[str] = None           # textura adicional
    texture_surfaces: Optional[str] = None        # textura adicional (topo/base)
    texture_top: Optional[str] = None             # só o topo (ex.: grama)
    texture_bottom: Optional[str] = None          # só a base
    color_hex: str = "#ffffff"
    solid: bool = True
    transparent: bool = False
    block_format: str = "default"                 # default | cylindrical | rounded
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def format(self) -> str:
        return self.block_format

    @property
    def render(self) -> str:
        """Como o bloco é desenhado no mundo (independente da física/``format``).

        ``block_properties.render`` em blocks.json escolhe:
          * ``cube``     — cubo opaco com faces escondidas entre vizinhos (leve);
          * ``cutout``   — cubo com textura recortada (pixels transparentes),
                           como as folhas do Minecraft;
          * ``rounded``  — cubo arredondado (pesado: ~9x mais vértices);
          * ``cylinder`` — cilindro de 16 lados (pesado: não esconde faces).
        Sem ``render``: rounded -> rounded, cylindrical -> cylinder, o resto cube.
        Um tronco ``cylindrical`` com ``render: cube`` continua a ter orientação
        (em pé / deitado): a casca fica nos lados e os anéis nas pontas.
        """
        cached = self.__dict__.get("_render")
        if cached is not None:
            return cached
        props = self.raw.get("block_properties") or {}
        wanted = str(props.get("render") or "").strip().lower()
        if wanted not in ("cube", "cutout", "rounded", "cylinder"):
            wanted = {"rounded": "rounded", "cylindrical": "cylinder",
                      "cylinder": "cylinder", "produced": "produced"}.get(
                          self.block_format, "cube")
        if self.block_format == "produced":
            wanted = "produced"
        self.__dict__["_render"] = wanted
        return wanted

    def tint(self):
        """Cor do JSON -> `ursina.color` (útil p/ UI/inventário)."""
        h = self.color_hex.lstrip("#")
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        try:
            r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
        except (ValueError, IndexError):
            return ursina_color.white
        return rgb255(r, g, b)

    def side_texture(self) -> Optional[str]:
        return self.texture_sides or self.texture

    def surface_texture(self) -> Optional[str]:
        return self.texture_surfaces or self.texture

    def top_texture(self) -> Optional[str]:
        return self.texture_top or self.surface_texture()

    def bottom_texture(self) -> Optional[str]:
        return self.texture_bottom or self.surface_texture()

    @property
    def multi_textured(self) -> bool:
        """Cubo com texturas diferentes no topo/lados/base (ex.: grama)."""
        return bool(self.texture_top or self.texture_bottom or self.texture_sides)

    @property
    def collectable(self) -> bool:
        """O jogador pode pegar com Q? (``"coletavel": false`` no JSON = não)."""
        return bool((self.raw.get("block_properties") or {}).get("coletavel", True))


# ------------------------------------------------------------------ estado
_REGISTRY: Dict[str, BlockType] = {}


# ------------------------------------------------------------------ parsers
def _parse_entry(name: str, data: Dict[str, Any]) -> BlockType:
    props = data.get("block_properties") or {}
    if isinstance(props, dict):
        fmt = props.get("format", "default")
        extra = dict(props)
    else:
        fmt = str(props)
        extra = {}

    return BlockType(
        name=name,
        texture=data.get("texture"),
        texture_sides=data.get("texture-sides"),
        texture_surfaces=data.get("texture-surfaces"),
        texture_top=data.get("texture-top"),
        texture_bottom=data.get("texture-bottom"),
        color_hex=data.get("color", "#ffffff"),
        solid=bool(data.get("solid", True)),
        transparent=bool(data.get("transparent", False)),
        block_format=(str(fmt).strip().lower() or "default"),
        raw={**data, "block_properties": extra},
    )


# ------------------------------------------------------------------ API
def load_registry(path: Optional[str] = None) -> Dict[str, BlockType]:
    """Lê o JSON e (re)popula o registry. Devolve o dicionário."""
    global _REGISTRY
    path = path or DEFAULT_JSON_PATH
    import game_config        # online: o blocks.json do servidor (ver game_config.py)
    data = game_config.read_json(path, {})

    _REGISTRY = {name: _parse_entry(name, entry) for name, entry in data.items()}
    print(f"[blocos] {len(_REGISTRY)} tipos carregados")
    return _REGISTRY


# Nomes antigos -> nomes atuais (saves de rooms.json antigos continuam a abrir).
LEGACY_BLOCK_IDS = {"old-stool": "banco de madeira", "mini sol": "berco solar"}


def canonical_block_id(name):
    """Nome atual de um bloco (traduz nomes antigos, ex.: ``old-stool``)."""
    return LEGACY_BLOCK_IDS.get(name, name) if isinstance(name, str) else name


def get_block_type(name: str) -> Optional[BlockType]:
    return _REGISTRY.get(name) or _REGISTRY.get(canonical_block_id(name))


def all_block_types() -> Dict[str, BlockType]:
    return dict(_REGISTRY)


def ensure_loaded() -> None:
    if not _REGISTRY:
        load_registry()


# ------------------------------------------------------------------ exports
__all__ = [
    "BlockType",
    "canonical_block_id",
    "LEGACY_BLOCK_IDS",
    "load_registry",
    "get_block_type",
    "all_block_types",
    "ensure_loaded",
    "DEFAULT_JSON_PATH",
]
