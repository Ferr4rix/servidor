"""Onde ficam as coisas, para as ferramentas de desenvolvimento (v0.6).

    jogoV0.6/cliente/                              <- CLIENTE (o jogo)
    jogoV0.6/servidor/desenvolvimento/modelos_fonte <- FONTES (.bbmodel)
    jogoV0.6/servidor/desenvolvimento/tools         <- TOOLS (esta pasta)
"""
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
DEV = TOOLS.parent
SERVIDOR = DEV.parent
CLIENTE = SERVIDOR.parent / "cliente"
FONTES = DEV / "modelos_fonte"
