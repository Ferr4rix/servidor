"""Ursina "de mentira" para o servidor (sem gráficos).

A física dos blocos (engine/block_physics.py) e as definições de blocos
importam algumas coisas do Ursina (cores, Vec3, destroy). No servidor só é
preciso que esses nomes existam; tudo o que desenha é ignorado (as partes
gráficas da física já tratam a falta delas: ``from ursina import Entity``
dá ImportError e a física segue sem cópias/partículas).
"""


class Vec3(tuple):
    def __new__(cls, x=0.0, y=0.0, z=0.0):
        return tuple.__new__(cls, (float(x), float(y), float(z)))

    x = property(lambda self: self[0])
    y = property(lambda self: self[1])
    z = property(lambda self: self[2])

    def __add__(self, o):
        return Vec3(self[0] + o[0], self[1] + o[1], self[2] + o[2])

    def __sub__(self, o):
        return Vec3(self[0] - o[0], self[1] - o[1], self[2] - o[2])


class _Color(tuple):
    pass


class _ColorModule:
    def rgba(self, r, g, b, a=1.0):
        return _Color((r, g, b, a))

    def rgb(self, r, g, b):
        return _Color((r, g, b, 1.0))

    def __getattr__(self, name):
        return _Color((1.0, 1.0, 1.0, 1.0))


color = _ColorModule()


def destroy(*_args, **_kwargs):
    return None
