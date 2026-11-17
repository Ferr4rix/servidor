"""Bloco arredondado: cubo com arestas e cantos arredondados.

A malha é gerada face a face. Cada vértice da grade é "puxado" para
dentro de uma esfera de raio `raio` cujo centro é o ponto mais próximo
da caixa interna (definida pelos limites `lims_min`/`lims_max`).

Otimização: o miolo de cada face é plano, então ele é um único segmento;
só as faixas de arredondamento (largura `raio`, junto às bordas que
realmente arredondam) são subdivididas em `resolucao` segmentos. Isso dá
bem menos triângulos que uma grade uniforme e curvas mais suaves.

Quando um bloco vizinho encosta numa face, essa face é omitida e,
além disso, o limite correspondente é estendido até `metade`, de
modo que as arestas adjacentes continuem arredondando corretamente
mesmo encostadas em outro bloco.
"""

import numpy as np
from ursina import Mesh


def _coords_eixo(lim_min, lim_max, metade, resolucao):
    """Coordenadas ao longo de um eixo da face.

    Faixa arredondada = [-metade, lim_min] e [lim_max, metade] (só existe se
    o limite não foi estendido até a borda). O miolo é um segmento só.
    """
    coords = []
    if lim_min > -metade + 1e-9:
        faixa = lim_min + metade
        coords += [-metade + faixa * k / resolucao for k in range(resolucao + 1)]
    else:
        coords.append(-metade)
    if lim_max < metade - 1e-9:
        faixa = metade - lim_max
        coords += [lim_max + faixa * k / resolucao for k in range(resolucao + 1)]
    else:
        coords.append(metade)
    return coords


def create_model(tamanho=2.0, raio=0.25, resolucao=4, vizinhos=None):
    """Cria a malha de um bloco arredondado.

    Parâmetros
    ----------
    tamanho : float
        Comprimento da aresta do bloco cúbico.
    raio : float
        Raio do arredondamento (aplicado em arestas e cantos).
    resolucao : int
        Segmentos em cada faixa arredondada de borda (maior = mais suave).
    vizinhos : dict | None
        Quais faces estão ocultas por blocos adjacentes.
        Chaves aceitas: 'mais_x', 'menos_x', 'mais_y', 'menos_y',
        'mais_z', 'menos_z'. Ausentes/False significam face visível.
    """
    if vizinhos is None:
        vizinhos = {}

    metade = tamanho / 2.0
    limite = metade - raio

    tem_mais_x = bool(vizinhos.get('mais_x', False))
    tem_menos_x = bool(vizinhos.get('menos_x', False))
    tem_mais_y = bool(vizinhos.get('mais_y', False))
    tem_menos_y = bool(vizinhos.get('menos_y', False))
    tem_mais_z = bool(vizinhos.get('mais_z', False))
    tem_menos_z = bool(vizinhos.get('menos_z', False))

    # Limites da caixa interna: quanto mais faces ocultas, mais a caixa
    # se expande até a borda real (assim o arredondamento some naquele lado).
    lims_min = [
        -metade if tem_menos_x else -limite,
        -metade if tem_menos_y else -limite,
        -metade if tem_menos_z else -limite,
    ]
    lims_max = [
        metade if tem_mais_x else limite,
        metade if tem_mais_y else limite,
        metade if tem_mais_z else limite,
    ]

    vertices = []
    triangulos = []
    uvs = []

    faces = [
        (0,  1, tem_mais_x),
        (0, -1, tem_menos_x),
        (1,  1, tem_mais_y),
        (1, -1, tem_menos_y),
        (2,  1, tem_mais_z),
        (2, -1, tem_menos_z),
    ]

    for eixo, sinal, oculta in faces:
        if oculta:
            continue

        inicio = len(vertices)
        outros = [k for k in range(3) if k != eixo]
        coords_u = _coords_eixo(lims_min[outros[0]], lims_max[outros[0]], metade, resolucao)
        coords_v = _coords_eixo(lims_min[outros[1]], lims_max[outros[1]], metade, resolucao)

        for u in coords_u:
            for v in coords_v:
                p = [0.0, 0.0, 0.0]
                p[eixo] = sinal * metade
                p[outros[0]] = u
                p[outros[1]] = v

                p = np.array(p, dtype=float)

                centro = np.array([
                    np.clip(p[0], lims_min[0], lims_max[0]),
                    np.clip(p[1], lims_min[1], lims_max[1]),
                    np.clip(p[2], lims_min[2], lims_max[2]),
                ])

                delta = p - centro
                distancia = np.linalg.norm(delta)

                if distancia > 1e-8:
                    p = centro + delta / distancia * raio

                vertices.append(tuple(p))
                # UV pela posição plana original (igual à versão anterior)
                uvs.append(((u + metade) / tamanho, (v + metade) / tamanho))

        largura = len(coords_v)

        for i in range(len(coords_u) - 1):
            for j in range(len(coords_v) - 1):
                a = inicio + i * largura + j
                b = a + largura
                c = b + 1
                d = a + 1

                triangulos.extend([
                    (a, b, d),
                    (b, c, d),
                ])

    # Fallback defensivo (todas as faces ocultas / sem geometria).
    if not vertices:
        vertices = [(0, 0, 0), (0, 0, 0), (0, 0, 0)]
        triangulos = [(0, 1, 2)]
        uvs = [(0, 0), (0, 0), (0, 0)]

    malha = Mesh(vertices=vertices, triangles=triangulos, uvs=uvs,
                 mode="triangle", static=True)
    malha.generate()
    return malha