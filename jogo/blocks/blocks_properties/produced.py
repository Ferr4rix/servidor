"""Helpers para blocos ``produced`` apoiados em modelos glTF ou OBJ.

A animação é opcional e declarada em ``block_properties.animation``. Os
estímulos que disparam ações são declarados separadamente em
``block_properties.events`` e tratados pelo pacote ``events``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

SUPPORTED_MODEL_SUFFIXES = (".gltf", ".glb", ".obj")
ANIMATION_MODES = frozenset({"event", "continuous", "none"})


def size_in_cells(properties: dict) -> Tuple[int, int, int]:
    """Devolve dimensões positivas em células: (largura, altura, profundidade)."""
    raw = properties.get("size", (1, 1, 1))
    if not isinstance(raw, (list, tuple)) or len(raw) != 3:
        raise ValueError("block_properties.size deve conter [largura, altura, profundidade]")
    result = []
    for value in raw:
        number = int(value)
        if number <= 0 or float(value) != number:
            raise ValueError("cada dimensão de block_properties.size deve ser um inteiro positivo")
        result.append(number)
    return tuple(result)


def vector3_values(value, default=(0.0, 0.0, 0.0), name="vetor") -> Tuple[float, float, float]:
    """Converte uma lista/tupla JSON com três componentes em floats."""
    if value is None:
        value = default
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f"{name} deve conter três valores")
    try:
        return tuple(float(component) for component in value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} deve conter números") from exc


def animation_config(properties: dict) -> dict:
    """Normaliza o modo e os nomes de clipes, sem acoplar a um gatilho.

    ``event`` reproduz um clipe uma vez quando algum evento declarado em
    ``block_properties.events`` executa a ação correspondente. ``close_clip``
    é opcional e alterna com ``clip``. ``continuous`` mantém o clipe em loop;
    ``none`` não aplica clipes.
    """
    raw = properties.get("animation", {"mode": "none"})
    if isinstance(raw, str):
        raw = {"mode": raw}
    if not isinstance(raw, dict):
        raise ValueError("block_properties.animation deve ser uma string ou um objeto")

    mode = str(raw.get("mode", "none")).strip().lower()
    if mode not in ANIMATION_MODES:
        choices = ", ".join(sorted(ANIMATION_MODES))
        raise ValueError(f"modo de animação inválido {mode!r}; use: {choices}")

    clip = str(raw.get("clip") or "").strip()
    close_clip = str(raw.get("close_clip") or "").strip() or None
    if mode in {"event", "continuous"} and not clip:
        raise ValueError(f"o modo {mode!r} exige block_properties.animation.clip")
    if mode != "event" and close_clip:
        raise ValueError("close_clip só pode ser usado no modo 'event'")
    if close_clip == clip:
        raise ValueError("clip e close_clip devem ter nomes diferentes")

    return {"mode": mode, "clip": clip or None, "close_clip": close_clip}


def resolve_model_path(model_ref, project_root) -> Optional[Path]:
    """Resolve modelos (.gltf, .glb, .obj) em ``assets/models/``, ``assets/`` ou na raiz.

    Se o caminho indicado não existir, procura um ficheiro com o mesmo nome em
    qualquer subpasta de ``assets/models/`` (ex.: ``"wooden-chest.gltf"`` acha
    ``assets/models/produced_blocks/wooden-chest.gltf``). Assim mover modelos
    para outra pasta não quebra o jogo; se houver dois com o mesmo nome, vale
    o primeiro em ordem alfabética e o log avisa.
    """
    if not isinstance(model_ref, str) or not model_ref.strip():
        return None
    ref = Path(model_ref.strip()).expanduser()
    root = Path(project_root).resolve()
    if ref.suffix and ref.suffix.lower() not in SUPPORTED_MODEL_SUFFIXES:
        return None

    candidates = [ref] if ref.is_absolute() else [
        root / "assets" / "models" / ref,
        root / "assets" / ref,
        root / ref,
    ]
    expanded = []
    for candidate in candidates:
        expanded.append(candidate)
        if not candidate.suffix:
            expanded.extend(candidate.with_suffix(ext) for ext in SUPPORTED_MODEL_SUFFIXES)
    for candidate in expanded:
        try:
            resolved = candidate.resolve()
            if resolved.suffix.lower() in SUPPORTED_MODEL_SUFFIXES and resolved.is_file():
                return resolved
        except OSError:
            continue
    if ref.is_absolute():
        return None
    return _search_by_name(ref, root / "assets" / "models")


_SEARCH_WARNED = set()


def _search_by_name(ref: Path, models_dir: Path) -> Optional[Path]:
    names = ([ref.name] if ref.suffix else
             [ref.name + ext for ext in SUPPORTED_MODEL_SUFFIXES])
    wanted = {name.casefold() for name in names}
    try:
        found = sorted(p for p in models_dir.rglob("*")
                       if p.is_file() and p.name.casefold() in wanted)
    except OSError:
        return None
    if not found:
        return None
    key = str(ref)
    if key not in _SEARCH_WARNED:
        _SEARCH_WARNED.add(key)
        chosen = found[0].relative_to(models_dir).as_posix()
        extra = f" ({len(found)} ficheiros com esse nome)" if len(found) > 1 else ""
        print(f"[modelo] {key!r} encontrado em assets/models/{chosen}{extra}")
    return found[0].resolve()
