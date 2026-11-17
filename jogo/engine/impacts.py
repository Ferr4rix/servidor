"""Dano de impacto: tudo o que voa ou cai e bate em alguém.

    energia = 1/2 x massa (kg) x velocidade² (m/s; 1 bloco = 1 m)
    dano = energia x IMPACT_TRANSFER (25%: o resto vai para o chão, ricochete...)
           / 100 J por ponto de vida  x IMPACT_FACTOR

(ver physics_units.py). A velocidade conta ao quadrado: uma queda alta fere
muito mais do que uma baixa.

A velocidade é medida pelo próprio sistema, frame a frame, a partir da posição
do objeto no mundo. Por isso serve para qualquer "projétil":

* blocos a cair ou estruturas a tombar (a física de blocos regista-os, tipo
  ``"gravidade"``);
* no futuro, flechas, pedras atiradas etc.: basta ``launch(entidade, peso,
  "flecha")`` enquanto ela voa e ``release(entidade)`` quando parar. Quem a
  move é o código da arma; aqui só se mede e se aplica o dano.

Alvos (``add_target``): o jogador e os blocos com vida (stand...). Cada alvo
dá a sua caixa no mundo e recebe ``on_hit(dano, projétil)``.

Um mesmo grupo (ex.: todos os blocos de uma árvore a tombar, ou de uma pilha a
cair junta) acerta cada alvo no máximo uma vez: o primeiro bloco do grupo que
toca no alvo é o que conta.
"""

from __future__ import annotations

import math
from typing import Callable, Dict, List, Optional, Tuple

Box = Tuple[Tuple[float, float, float], Tuple[float, float, float]]

# Multiplicador geral do dano de impacto (1 = a física, sem ajuste).
IMPACT_FACTOR = 1.0
# Abaixo desta velocidade (blocos/s) o impacto não fere (encostar não dói).
MIN_SPEED = 3.0
# Folga das caixas (unidades do mundo). Na vertical conta o toque (bloco a
# pousar em cima de alguém); nos lados é preciso entrar um pouco (um bloco a
# cair na coluna ao lado não fere quem está encostado a ela).
CONTACT_Y = 0.05
CONTACT_SIDE = -0.05


def impact_damage(mass: float, speed: float, factor: float = IMPACT_FACTOR) -> float:
    """Dano (pontos) de um impacto: 1/2 m v² transferido / 100 J, x fator.
    ``mass`` em kg, ``speed`` em blocos/s (= m/s)."""
    if mass <= 0 or speed < MIN_SPEED:
        return 0.0
    from physics_units import impact_energy, to_hp
    return to_hp(impact_energy(mass, speed)) * float(factor)


class Projectile:
    __slots__ = ("entity", "weight", "kind", "half", "group", "last", "speed",
                 "velocity", "source")

    def __init__(self, entity, weight, kind, half, group, source):
        self.entity = entity
        self.weight = float(weight)
        self.kind = kind
        self.half = float(half)          # meia aresta da caixa (mundo)
        self.group = group               # objeto partilhado pelo grupo
        self.source = source             # ex.: tipo do bloco
        self.last = None                 # última posição medida
        self.speed = 0.0                 # blocos/s
        self.velocity = (0.0, 0.0, 0.0)  # unidades do mundo/s

    def position(self):
        p = self.entity.world_position
        return (float(p[0]), float(p[1]), float(p[2]))

    def box(self) -> Box:
        x, y, z = self.position()
        h = self.half
        return (x - h, y - h, z - h), (x + h, y + h, z + h)


class _Target:
    __slots__ = ("name", "box", "on_hit")

    def __init__(self, name, box, on_hit):
        self.name = name
        self.box = box
        self.on_hit = on_hit


def _overlap(a: Box, b: Box) -> bool:
    (a0, a1), (b0, b1) = a, b
    for i, pad in ((0, CONTACT_SIDE), (1, CONTACT_Y), (2, CONTACT_SIDE)):
        if not (a0[i] - pad < b1[i] and b0[i] - pad < a1[i]):
            return False
    return True


class ImpactSystem:
    def __init__(self, block_size: float = 2.0, factor: float = IMPACT_FACTOR):
        self.block_size = float(block_size)
        self.factor = float(factor)
        self.projectiles: Dict[int, Projectile] = {}
        self.targets: List[_Target] = []
        self._hits: Dict[int, set] = {}      # id(grupo) -> nomes de alvos já atingidos
        self._groups: Dict[int, object] = {}

    # ------------------------------------------------------------ registo
    def launch(self, entity, weight: float, kind: str = "projetil", *,
               half: Optional[float] = None, group=None, source=None) -> Optional[Projectile]:
        """Começa a medir ``entity`` (precisa de ``world_position``)."""
        if entity is None:
            return None
        p = Projectile(entity, weight, kind,
                       self.block_size / 2 if half is None else half,
                       group if group is not None else entity, source)
        self.projectiles[id(entity)] = p
        self._groups[id(p.group)] = p.group
        return p

    def release(self, entity) -> None:
        """O objeto parou (pousou, foi destruído): deixa de ferir."""
        p = self.projectiles.pop(id(entity), None)
        if p is None:
            return
        gid = id(p.group)
        if not any(id(q.group) == gid for q in self.projectiles.values()):
            self._hits.pop(gid, None)
            self._groups.pop(gid, None)

    def add_target(self, name: str, box: Callable[[], Optional[Box]],
                   on_hit: Callable[[float, Projectile], None]) -> None:
        """``box()`` -> caixa do alvo no mundo (ou None); ``on_hit(dano, projétil)``."""
        self.targets.append(_Target(name, box, on_hit))

    def add_target_list(self, names_and_boxes: Callable[[], List[Tuple[str, Box]]],
                        on_hit: Callable[[str, float, Projectile], None]) -> None:
        """Vários alvos de uma vez (ex.: blocos com vida por perto)."""
        self.targets.append(_Target(None, names_and_boxes, on_hit))

    # ------------------------------------------------------------ frame
    def update(self, dt: float) -> None:
        if not self.projectiles or dt <= 0:
            return
        boxes = []
        for t in self.targets:
            try:
                if t.name is None:
                    for name, box in t.box() or ():
                        boxes.append((name, box, t))
                else:
                    box = t.box()
                    if box is not None:
                        boxes.append((t.name, box, t))
            except Exception as exc:
                print(f"[impacto] alvo {t.name}: {exc}")
        dead = []
        for key, p in self.projectiles.items():
            try:
                pos = p.position()
            except Exception:
                dead.append(key)              # entidade destruída
                continue
            if p.last is not None:
                v = tuple((pos[i] - p.last[i]) / dt for i in range(3))
                p.velocity = v
                p.speed = math.sqrt(sum(c * c for c in v)) / self.block_size
            p.last = pos
            if p.speed < MIN_SPEED or not boxes:
                continue
            hit_set = self._hits.setdefault(id(p.group), set())
            pbox = p.box()
            for name, box, target in boxes:
                if name in hit_set or not _overlap(pbox, box):
                    continue
                damage = impact_damage(p.weight, p.speed, self.factor)
                if damage <= 0:
                    continue
                hit_set.add(name)
                try:
                    if target.name is None:
                        target.on_hit(name, damage, p)
                    else:
                        target.on_hit(damage, p)
                except Exception as exc:
                    print(f"[impacto] {exc}")
        for key in dead:
            p = self.projectiles.get(key)
            if p is not None:
                self.release(p.entity)


__all__ = ["ImpactSystem", "Projectile", "impact_damage", "IMPACT_FACTOR", "MIN_SPEED"]
