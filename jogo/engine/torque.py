"""Torque (rotação), no espírito do mod Create do Minecraft.

Peças (``block_properties.torque`` em ``blocks.json``):

* ``eixo`` — barra de transmissão. ``"eixo": "y"`` (em pé) ou ``"frente"``
  (deitado, na direção da frente do bloco). Passa a rotação às duas pontas.
* ``manivela`` — fonte manual. ``"base": "baixo"`` (posta em cima de algo) ou
  ``"tras"`` (presa na face de trás). Enquanto o jogador a gira (segurar o
  botão direito; com Shift gira ao contrário) dá ``rpm`` com até
  ``capacidade`` de força (SU).
* ``encaixe`` — o encaixe do parafuso de Arquimedes (caixa de engrenagens no
  topo da cadeia): recebe rotação pelos lados e por cima e passa-a ao
  parafuso (``engine/screw.py``). Gasta ``impacto`` SU por RPM, mais
  ``impacto_por_parafuso`` por cada parafuso da cadeia.

Regras (como no Create):

* Duas peças ligam-se quando estão encostadas e ambas têm encaixe nessa face
  (o mesmo eixo). A rotação é um vetor: um eixo leva-a igual de ponta a ponta.
* **Força**: a soma das capacidades das fontes a girar tem de pagar a soma
  ``impacto x RPM`` das peças da rede. Se não paga, a rede **trava**
  (``sobrecarga``) e nada gira.
* Duas fontes a girar a mesma rede em sentidos contrários também travam
  (``conflito``).
* Peças de torque seguram-se no que estão ligadas (não caem): ver ``held``.
* **Água a descer**: um encaixe cujo parafuso tem água a descer (muita água
  na saída, ou a saída tapada/cheia com água nas bolsas; ver
  ``ScrewSystem.water_drive``) é uma **fonte**: a água gira o parafuso para
  baixo e a rede gira com ela (eixos, manivelas). Uma manivela a ser girada
  manda mais do que a água.

Sem Ursina: testes com um ``ChunkRoom`` falso.
"""

from __future__ import annotations

from collections import deque
from typing import Callable, Dict, List, Optional, Tuple

Cell = Tuple[int, int, int]
Vec = Tuple[float, float, float]

# Para onde aponta a frente de um bloco (a frente fica voltada para quem o pôs).
FRONT = {"N": (0, 0, -1), "S": (0, 0, 1), "L": (1, 0, 0), "O": (-1, 0, 0)}
UP = (0, 1, 0)
DOWN = (0, -1, 0)

DEFAULTS = {
    "eixo": {"impacto": 0.0},
    "manivela": {"rpm": 32.0, "capacidade": 256.0, "impacto": 0.0},
    "encaixe": {"impacto": 2.0, "impacto_por_parafuso": 1.0, "relacao": 2.0},
}
HOLD = 0.45                  # s que uma manivela continua a girar depois do último sinal
# água a descer pelo parafuso: rpm no encaixe e força (SU) por parafuso da cadeia
AGUA = {"rpm": 16.0, "capacidade_por_parafuso": 48.0}


def _neg(v):
    return (-v[0], -v[1], -v[2])


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _scale(v, k):
    return (v[0] * k, v[1] * k, v[2] * k)


def yaw_local(orientation: str, v: Vec) -> Vec:
    """Vetor local do modelo (+X direita, +Z para trás) no mundo, pela orientação."""
    ang = {"N": 0, "L": 270, "S": 180, "O": 90}.get(orientation or "N", 0)
    import math
    t = math.radians(ang)
    c, s = round(math.cos(t)), round(math.sin(t))
    x, y, z = v
    return (x * c + z * s, y, -x * s + z * c)


def facing_for(normal) -> Optional[str]:
    """Orientação cuja frente aponta para ``normal`` (horizontal)."""
    n = tuple(int(v) for v in normal)
    for o, f in FRONT.items():
        if f == n:
            return o
    return None


def spec_of(block_type) -> Optional[dict]:
    if block_type is None:
        return None
    props = (getattr(block_type, "raw", None) or {}).get("block_properties") or {}
    t = props.get("torque")
    if not isinstance(t, dict) or t.get("tipo") not in DEFAULTS:
        return None
    return {**DEFAULTS[t["tipo"]], **t}


class Part:
    __slots__ = ("cell", "kind", "spec", "orientation", "connectors", "axis", "n_out", "right")

    def __init__(self, cell, kind, spec, orientation):
        self.cell = cell
        self.kind = kind
        self.spec = spec
        self.orientation = orientation if orientation in FRONT else "N"
        o = self.orientation
        self.n_out = None
        self.right = None
        if kind == "eixo":
            d = UP if spec.get("eixo", "y") == "y" else FRONT[o]
            self.connectors = [d, _neg(d)]
            # eixo do modelo no mundo (ver ScrewView/TorqueView: rotação do clipe)
            self.axis = UP if d == UP else _neg(FRONT[o])
        elif kind == "manivela":
            base = DOWN if spec.get("base", "baixo") == "baixo" else _neg(FRONT[o])
            self.connectors = [base]
            self.n_out = _neg(base)
            self.axis = UP if base == DOWN else _neg(FRONT[o])
        else:                                           # encaixe
            r = yaw_local(o, (1, 0, 0))
            self.right = r
            self.connectors = [r, _neg(r), UP]
            self.axis = _neg(r)          # eixo X do modelo no mundo (o modelo é espelhado em X)


def connector_dirs(block_type, orientation) -> List[Vec]:
    """Faces onde uma peça de torque se liga (para a física: segura-se ali)."""
    spec = spec_of(block_type)
    if spec is None:
        return []
    return list(Part((0, 0, 0), spec["tipo"], spec, orientation).connectors)


class Network:
    __slots__ = ("cells", "omega", "rpm", "demand", "capacity", "state", "fonte")

    def __init__(self):
        self.cells: List[Cell] = []
        self.omega: Dict[Cell, Vec] = {}      # vetor rotação (rpm) de cada peça
        self.rpm = 0.0
        self.demand = 0.0
        self.capacity = 0.0
        self.state = "parada"                 # parada | girando | sobrecarga | conflito
        self.fonte = None                     # "manivela" | "agua" (quem a gira)


class TorqueSystem:
    def __init__(self, chunk_room, type_of: Optional[Callable[[str], object]] = None,
                 chain_length: Optional[Callable[[Cell], int]] = None):
        self.cr = chunk_room
        if type_of is None:
            from blocks import get_block_type as type_of       # noqa: N813
        self.type_of = type_of
        self.chain_length = chain_length or (lambda cell: 0)
        self.parts: Dict[Cell, Part] = {}
        self.turning: Dict[Cell, Tuple[float, int]] = {}    # manivela -> (segundos, sentido)
        self.phase: Dict[Cell, float] = {}                  # voltas do clipe de cada peça
        self.networks: List[Network] = []
        self.net_of: Dict[Cell, Network] = {}
        self.version = 0
        self._dirty = True
        # encaixe -> a água desce por esse parafuso? (ScrewSystem.water_drive)
        self.water_source: Optional[Callable[[Cell], bool]] = None
        self._scan()
        self._hook()

    # ------------------------------------------------------------ peças
    def _orientation(self, cell):
        try:
            o = self.cr.orientation_at(cell)
        except Exception:
            o = getattr(self.cr, "block_orientations", {}).get(cell)
        return o if o in FRONT else "N"

    def _classify(self, cell, tipo):
        self.parts.pop(cell, None)
        spec = spec_of(self.type_of(tipo)) if tipo else None
        if spec is not None:
            self.parts[cell] = Part(cell, spec["tipo"], spec, self._orientation(cell))

    def _scan(self):
        for cell, tipo in list(getattr(self.cr, "block_map", {}).items()):
            self._classify(tuple(cell), tipo)
        self._changed()

    def _changed(self):
        self._dirty = True
        self.version += 1

    def _hook(self):
        cr = self.cr
        orig_set, orig_remove = cr.set_block, cr.remove_block

        def set_block(bpos, tipo, orientation=None):
            out = orig_set(bpos, tipo, orientation=orientation)
            bpos = tuple(bpos)
            if bpos in cr.block_map:
                had = bpos in self.parts
                self._classify(bpos, cr.block_map.get(bpos))
                if had or bpos in self.parts or self._touches(bpos):
                    self._changed()
            return out

        def remove_block(bpos):
            bpos = tuple(bpos)
            out = orig_remove(bpos)
            if bpos not in cr.block_map and (self.parts.pop(bpos, None) is not None or self._touches(bpos)):
                self.turning.pop(bpos, None)
                self.phase.pop(bpos, None)
                self._changed()
            return out

        cr.set_block = set_block
        cr.remove_block = remove_block

    def _touches(self, cell):
        x, y, z = cell
        for d in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)):
            if (x + d[0], y + d[1], z + d[2]) in self.parts:
                return True
        return False

    def mark_dirty(self):
        """Algo fora do torque mudou (ex.: o tamanho de uma cadeia de parafusos)."""
        self._changed()

    def linked(self, a: Part, d: Vec) -> Optional[Part]:
        """A peça ligada a ``a`` pela face ``d``, se as duas encaixam ali."""
        if d not in a.connectors:
            return None
        b = self.parts.get(_add(a.cell, d))
        if b is None or _neg(d) not in b.connectors:
            return None
        return b

    # ------------------------------------------------------------ manivela
    def turn(self, crank_cell, direction: int = 1, seconds: float = HOLD) -> bool:
        p = self.parts.get(tuple(crank_cell))
        if p is None or p.kind != "manivela":
            return False
        self.turning[p.cell] = (max(seconds, self.turning.get(p.cell, (0, 0))[0]),
                                1 if direction >= 0 else -1)
        self._dirty = True
        return True

    # ------------------------------------------------------------ redes
    def _impact(self, p: Part) -> float:
        imp = float(p.spec.get("impacto", 0.0))
        if p.kind == "encaixe":
            imp += float(p.spec.get("impacto_por_parafuso", 0.0)) * self.chain_length(p.cell)
        return imp

    def solve(self) -> None:
        """Refaz as redes: quem gira, a que velocidade, e se há força."""
        self._dirty = False
        self.networks, self.net_of = [], {}
        seen = set()
        for start in self.parts:
            if start in seen:
                continue
            net = Network()
            q = deque([start])
            seen.add(start)
            while q:
                c = q.popleft()
                net.cells.append(c)
                self.net_of[c] = net
                p = self.parts[c]
                for d in p.connectors:
                    b = self.linked(p, d)
                    if b is not None and b.cell not in seen:
                        seen.add(b.cell)
                        q.append(b.cell)
            self.networks.append(net)
            self._spin(net)

    def _propagate(self, c0: Cell, w0: Vec, rpm: float):
        """Espalha o vetor de rotação pela rede a partir de ``c0``."""
        omega = {c0: w0}
        q = deque([c0])
        conflict = False
        while q and not conflict:
            c = q.popleft()
            p = self.parts[c]
            w = omega[c]
            for d in p.connectors:
                b = self.linked(p, d)
                if b is None:
                    continue
                if p.kind == "encaixe":
                    # caixa de engrenagens: o mesmo sentido do parafuso em todas as faces
                    sig = self._encaixe_sign(p, w)
                    w_b = _scale(self._ref(p, d), sig * rpm)
                else:
                    w_b = w
                if b.cell in omega:
                    if b.kind == "encaixe":
                        bad = self._encaixe_sign(b, omega[b.cell]) != self._encaixe_sign(b, w_b)
                    else:
                        bad = _dot(omega[b.cell], w_b) <= 0
                    if bad:
                        conflict = True
                        break
                    continue
                omega[b.cell] = w_b
                q.append(b.cell)
        return omega, conflict

    def _spin(self, net: Network) -> None:
        net.fonte = None
        sources = [(c,) + self.turning[c] for c in net.cells
                   if c in self.turning and self.turning[c][0] > 0]
        if sources:
            # manivelas a ser giradas (mandam mais do que a água)
            rpm = max(float(self.parts[c].spec["rpm"]) for c, _t, _d in sources)
            c0, _t, d0 = sources[0]
            p0 = self.parts[c0]
            omega, conflict = self._propagate(c0, _scale(p0.n_out, -d0 * rpm), rpm)  # botão direito: horário visto de frente
            for c, _t, d in sources[1:]:
                want = _scale(self.parts[c].n_out, -d * rpm)
                if c in omega and _dot(omega[c], want) <= 0:
                    conflict = True
            net.capacity = sum(float(self.parts[c].spec["capacidade"]) for c, _t, _d in sources)
            net.fonte = "manivela"
        else:
            # água a descer por um parafuso: o encaixe gira para baixo (-1)
            ws = self.water_source
            water = [c for c in net.cells if self.parts[c].kind == "encaixe" and ws is not None and ws(c)]
            if not water:
                net.state = "parada"
                return
            rpm = float(AGUA["rpm"])
            c0 = water[0]
            omega, conflict = self._propagate(c0, _scale(self.parts[c0].right, -rpm), rpm)
            for c in water[1:]:
                if c in omega and self._encaixe_sign(self.parts[c], omega[c]) != -1:
                    conflict = True
            net.capacity = sum(float(AGUA["capacidade_por_parafuso"]) * max(1, self.chain_length(c))
                               for c in water)
            net.fonte = "agua"
        net.demand = sum(self._impact(self.parts[c]) for c in net.cells) * rpm
        if conflict:
            net.state = "conflito"
            return
        if net.demand > net.capacity + 1e-6:
            net.state = "sobrecarga"
            return
        net.state = "girando"
        net.rpm = rpm
        net.omega = omega

    @staticmethod
    def _ref(p: Part, face: Vec) -> Vec:
        """Sentido de referência do encaixe em cada face (sobe a água quando o
        vetor de rotação aponta para aqui)."""
        if face == UP:
            return DOWN
        return p.right

    def _encaixe_sign(self, p: Part, w: Vec) -> int:
        """+1 sobe a água, -1 desce, pelo vetor de rotação que entrou."""
        for face in p.connectors:
            ref = self._ref(p, face)
            k = _dot(w, ref)
            if abs(k) > 1e-9:
                return 1 if k > 0 else -1
        return 0

    # ------------------------------------------------------------ consultas
    def omega_of(self, cell) -> Vec:
        net = self.net_of.get(tuple(cell))
        if net is None or net.state != "girando":
            return (0.0, 0.0, 0.0)
        return net.omega.get(tuple(cell), (0.0, 0.0, 0.0))

    def screw_speed(self, encaixe_cell) -> float:
        """Voltas por segundo do parafuso ligado a este encaixe (sinal: + sobe)."""
        p = self.parts.get(tuple(encaixe_cell))
        if p is None or p.kind != "encaixe":
            return 0.0
        w = self.omega_of(p.cell)
        sig = self._encaixe_sign(p, w)
        rpm = _dot(w, w) ** 0.5
        return sig * rpm / 60.0 * float(p.spec.get("relacao", 1.0))

    def network_at(self, cell) -> Optional[Network]:
        if self._dirty:
            self.solve()
        return self.net_of.get(tuple(cell))

    # ------------------------------------------------------------ tempo
    def update(self, dt: float) -> None:
        ended = False
        for c in list(self.turning):
            t, d = self.turning[c]
            t -= dt
            if t <= 0:
                del self.turning[c]
                ended = True
            else:
                self.turning[c] = (t, d)
        if ended or self._dirty:
            self.solve()
        for net in self.networks:
            if net.state != "girando":
                continue
            for c in net.cells:
                p = self.parts[c]
                w = net.omega.get(c)
                if w is None:
                    continue
                if p.kind == "encaixe":
                    # todas as pontas do encaixe na mesma fase (ver Part.axis)
                    w = _scale(p.right, self._encaixe_sign(p, w) * net.rpm)
                self.phase[c] = self.phase.get(c, 0.0) + _dot(w, p.axis) / 60.0 * dt


CARGA_MAX = 250               # peso que o parafuso de baixo de uma cadeia aguenta


def _screw_info(chunk_room, cell, type_of):
    """(papel, orientação, props) de um parafuso/encaixe; (None, None, None) se não for."""
    tipo = chunk_room.block_map.get(cell)
    bt = type_of(tipo) if tipo else None
    if bt is None:
        return None, None, None
    props = (getattr(bt, "raw", None) or {}).get("block_properties") or {}
    papel = (props.get("parafuso") or {}).get("papel")
    if papel not in ("parafuso", "encaixe"):
        return None, None, None
    try:
        o = chunk_room.orientation_at(cell)
    except Exception:
        o = getattr(chunk_room, "block_orientations", {}).get(cell)
    return papel, (o if o in FRONT else "N"), props


def screw_chain(chunk_room, cell, type_of=None) -> List[Cell]:
    """Os blocos da cadeia de parafusos de ``cell`` (de baixo para cima, com
    o encaixe no fim, se houver). Vazio se ``cell`` não é parafuso/encaixe."""
    if type_of is None:
        from blocks import get_block_type as type_of           # noqa: N813
    cell = tuple(cell)
    papel, o, _p = _screw_info(chunk_room, cell, type_of)
    if papel is None:
        return []
    b = _neg(FRONT[o])                            # a cadeia sobe para trás
    st = (b[0], 1, b[2])
    down = (-st[0], -1, -st[2])
    # desce até ao parafuso de baixo
    bottom = cell
    if papel == "encaixe":
        prev = _add(cell, down)
        pp, po, _ = _screw_info(chunk_room, prev, type_of)
        if pp != "parafuso" or po != o:
            return [cell]
        bottom = prev
    for _ in range(512):
        prev = _add(bottom, down)
        pp, po, _ = _screw_info(chunk_room, prev, type_of)
        if pp != "parafuso" or po != o:
            break
        bottom = prev
    out = [bottom]
    for _ in range(512):
        nxt = _add(out[-1], st)
        pp, po, _ = _screw_info(chunk_room, nxt, type_of)
        if po != o or pp is None:
            break
        out.append(nxt)
        if pp == "encaixe":
            break
    return out


def chain_load(chunk_room, chain, type_of=None) -> Tuple[float, float]:
    """(peso da cadeia, carga máxima do parafuso de baixo)."""
    if type_of is None:
        from blocks import get_block_type as type_of           # noqa: N813
    total = 0.0
    for c in chain:
        _pp, _o, props = _screw_info(chunk_room, c, type_of)
        try:
            total += float((props or {}).get("peso", 0) or 0)
        except (TypeError, ValueError):
            pass
    _pp, _o, props = _screw_info(chunk_room, chain[0], type_of) if chain else (None, None, None)
    try:
        cap = float(((props or {}).get("parafuso") or {}).get("carga_max", CARGA_MAX))
    except (TypeError, ValueError):
        cap = CARGA_MAX
    return total, cap


def held(chunk_room, owner, type_of=None, group=()) -> bool:
    """Peça de torque/parafuso que se segura no que está ligado (não cai):
    eixos e manivelas nas faces de encaixe; parafusos e o encaixe na cadeia
    (o peso todo vai para o parafuso de baixo, que tem de estar apoiado; se
    a cadeia pesar mais do que ``carga_max`` dele, cai tudo)."""
    if type_of is None:
        from blocks import get_block_type as type_of           # noqa: N813
    tipo = chunk_room.block_map.get(owner)
    bt = type_of(tipo) if tipo else None
    if bt is None:
        return False
    try:
        o = chunk_room.orientation_at(owner)
    except Exception:
        o = None
    o = o if o in FRONT else "N"
    props = (getattr(bt, "raw", None) or {}).get("block_properties") or {}
    papel = (props.get("parafuso") or {}).get("papel")
    if papel in ("parafuso", "encaixe"):
        chain = screw_chain(chunk_room, owner, type_of)
        if len(chain) >= 2:
            bottom = chain[0]
            if bottom == owner:
                return False                      # o de baixo apoia-se como um bloco normal
            below = chunk_room.owner_at((bottom[0], bottom[1] - 1, bottom[2]))
            if below is None or below in chain:
                return False
            total, cap = chain_load(chunk_room, chain, type_of)
            if total <= cap:
                return True
        if papel == "parafuso":
            return False
    dirs = list(connector_dirs(bt, o))
    for d in dirs:
        other = chunk_room.owner_at(_add(owner, d))
        if other is not None and other not in group and other != owner:
            return True
    return False
