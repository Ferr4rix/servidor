"""Conversões de cor usadas pelo jogo (Ursina trabalha com valores 0–1)."""

from ursina import color


def rgb255(r, g, b, a=255):
    """Cria uma cor Ursina a partir de componentes inteiros no intervalo 0–255."""
    values = [max(0.0, min(1.0, float(component) / 255.0))
              for component in (r, g, b, a)]
    return color.rgba(*values)
