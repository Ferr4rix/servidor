"""Tempo de um tombo (árvore/estrutura a cair), sem gráficos.

O ``ToppleBody`` (engine/physics_fx.py) anima o tombo como uma barra a cair
sobre a ponta, com quiques. O servidor não tem gráficos mas precisa de saber
quanto tempo a animação dura, para pôr os blocos no destino só quando a
animação dos jogos acaba. Os dois usam estas constantes e esta conta.
"""

from __future__ import annotations

import math

TOPPLE_G = 30.0              # gravidade da animação (unidades do mundo / s²)
TOPPLE_KICK = 0.9            # rad/s iniciais do golpe
TOPPLE_START = 0.03          # rad: inclinação inicial
BOUNCE = 0.28                # fração da velocidade que volta no quique
BOUNCE_MIN = 0.35            # rad/s abaixo disto deixa de quicar
STEP = 0.004


def topple_duration(height: float) -> float:
    """Segundos até o tombo de uma estrutura com ``height`` (mundo) parar."""
    k = 3.0 * TOPPLE_G / (2.0 * max(float(height), 1.0))
    theta, omega, bounces, t = TOPPLE_START, TOPPLE_KICK, 0, 0.0
    while t < 30.0:
        omega += k * math.sin(theta) * STEP
        theta += omega * STEP
        t += STEP
        if theta >= math.pi / 2:
            theta = math.pi / 2
            if omega > BOUNCE_MIN and bounces < 3:
                omega = -omega * BOUNCE
                bounces += 1
            else:
                break
    return t
