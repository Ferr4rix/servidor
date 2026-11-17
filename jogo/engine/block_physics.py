"""Física de blocos: estruturas, impacto (mover/quebrar) e gravidade na grade.

Conceitos
---------
* **Bloco avulso**: um bloco sem ligações. Dois blocos encostados continuam a
  ser dois blocos avulsos.
* **Estrutura**: blocos ligados entre si (pares de vizinhos). Árvores são
  estruturas naturais (geradas pela seed); construções do jogador viram
  estrutura com o item **cola** (cada uso liga o bloco clicado aos vizinhos).
* **Impacto**: um golpe dá ``dano`` a um bloco. Se ele faz parte de uma
  estrutura, o dano é **dividido igualmente** pelos blocos da mesma estrutura
  dentro do 3x3x3 à volta do bloco atingido.
* **Unidades físicas** (ver ``physics_units.py``; 1 bloco = 1 m). Cada bloco
  tem a **massa** = ``peso`` (kg) em ``block_properties`` e, em ``physics``::

      "physics": {"tenacidade": 2000, "estabilidade": 3528, "compressao": 11760}

  - **mover** (golpe ou encontrão): a energia do golpe (J) tem de pagar o
    arrasto de 1 m: atrito x m x g (``"atrito"``, 0,6 por omissão; ou
    ``"empurrar"`` em J). Um bloco avulso é **empurrado** 1 bloco; numa
    estrutura, a estrutura inteira **tomba**: gira 90° sobre a aresta da base,
    para o lado do golpe, e fica deitada (uma árvore cai como tronco deitado).
    Agachado e encostado, o jogador também empurra sem golpe (``push``);
  - ``"on_topple": "vanish"``: o bloco some quando a sua estrutura tomba (as
    folhas das árvores);
  - **quebrar**: ``tenacidade`` (J), somando os golpes (ver ``hit``);
  - **desabamento**: ``compressao`` (N que aguenta em cima antes de esmagar) e
    ``estabilidade`` (N: pilha solta tomba acima de 75%; a cola aguenta um
    momento de até isto em N.m).
  Internamente "move" e "break" ficam em pontos de dano (J / 100), para
  comparar com o dano dos golpes.
* **Gravidade** (na grade): um bloco avulso sem nada por baixo cai até apoiar.
  Uma estrutura só cai se **nenhum** dos seus blocos estiver apoiado em algo
  de fora dela, e cai inteira, como um corpo só. O chão do mundo (y < 0) apoia.

A posição muda logo no mundo lógico; o que se vê a cair/deslizar são cópias
temporárias (``_Motion``) que somem quando o chunk já mostra o bloco no lugar
novo. A vida dos blocos fica só em memória.
"""

from __future__ import annotations

import math
import time as _time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

Cell = Tuple[int, int, int]

from physics_units import (G, J_PER_HP, REF_BLOCK_N, DEFAULT_MASS_KG,  # noqa: E402
                           push_force_n)

DEFAULT_PHYSICS = {"move": push_force_n(DEFAULT_MASS_KG) / J_PER_HP, "break": 20.0,
                   "weight": DEFAULT_MASS_KG * G, "mass": DEFAULT_MASS_KG,
                   "stack": 5.0 * REF_BLOCK_N, "crush": 20.0 * REF_BLOCK_N}
# Resistências efetivas sob carga (peso por cima, em N): mover = move × (1 +
# LOAD_FACTOR × carga / REF_BLOCK_N) (mais estável), quebrar = break / (o mesmo)
# (mais frágil). REF_BLOCK_N = peso de 1 bloco de terra (588 N). Ver resist().
LOAD_FACTOR = 0.5
# Balanceamento (mais difícil mexer no mundo). Os blocos pesam 4x menos que
# antes (blocks.json: terra 15 kg; o peso na mochila é o real), por isso o
# "arrastar" (atrito x m x g) também ficou 4x menor; estes fatores dão a
# resistência a golpes e empurrões, sem depender disso:
#  * BREAK_FACTOR: o "quebrar" (tenacidade) de todos os blocos x isto. Terra
#    20 -> 140 (23 socos de 6,1; 13 golpes da espada do herói), tronco 210,
#    pedra 560, folhas 28. Vale também para o fogo (dano por parte do
#    quebrar) e para os golpes de mobs/explosões.
#  * HIT_MOVE_FACTOR: um GOLPE precisa de isto x a força de arrastar para
#    arrancar/empurrar um bloco (ou tombar uma estrutura): terra 30,9, tronco
#    25,8, pedra 12,9, baú 15,5 (3,5x a versão anterior).
#  * PUSH_MOVE_FACTOR: empurrar agachado, encostado (sem golpe): terra 5,3
#    (1,5x a versão anterior; ainda dá com a força base a andar).
BREAK_FACTOR = 7.0
HIT_MOVE_FACTOR = 35.0
PUSH_MOVE_FACTOR = 6.0
# Desabamento natural (sem golpe): ver ``check_stability``.
#  * vertical: um bloco esmaga quando o peso apoiado nele (N) passa de
#    ``compressao`` x CRUSH_FACTOR;
#  * horizontal: uma ligação de cola que segura blocos "no ar" rompe quando o
#    momento (peso em N x distância ao apoio em m) passa de ``estabilidade`` x
#    GLUE_HOLD do mais fraco dos dois blocos. Terra (3528 N) aguenta uma viga
#    de 3 blocos (588 x (1+2+3) = 3528); a 4.ª já a faz cair.
CRUSH_FACTOR = 1.0
GLUE_HOLD = 1.0
#  * pilha solta (sem cola): tomba quando o peso em cima de um bloco passa de
#    ``estabilidade`` x STACK_FACTOR. Terra (3528 N): aguenta 4 em cima (5
#    empilhados); com o 6.º a pilha tomba. Tronco (4704 N, 50 kg): 8 empilhados.
STACK_FACTOR = 0.75
#  * torre colada: a cola deixa-a rígida, mas uma torre fina demais tomba.
#    Altura máxima = (altura de uma pilha solta do material) x GLUED_STACK_BONUS
#    x (largura da base). Terra: 1x1 colada aguenta 10 de altura, base 2x2
#    aguenta 20, 3x3 aguenta 30 (a sala tem ~16).
GLUED_STACK_BONUS = 2.0
MAX_COLUMN = 64               # blocos contados por cima de um bloco
TOPPLE_MAX_LIFT = 12          # quanto pode subir para não cair dentro de blocos
NEIGHBORS6 = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))
MAX_STRUCTURE = 4096          # limite da busca de uma estrutura
GRAVITY = 30.0                # unidades do mundo / s² (só a animação)
PUSH_SECONDS = 0.18


@dataclass
class _Motion:
    """Blocos a caminho do destino: já saíram do mundo e voltam ao chegar."""
    blocks: List[Tuple[Cell, str, Optional[str]]]   # (destino, tipo, orientação)
    offset: Tuple[float, float, float]              # deslocamento total (mundo)
    duration: float
    fall: bool
    reserved: Set[Cell]
    proxies: list = field(default_factory=list)
    elapsed: float = 0.0
    landed: bool = False
    linger: float = 0.0


DAMAGE_MEMORY = 10.0     # s que um bloco guarda o dano dos golpes


class BlockPhysics:
    def __init__(self, chunk_room, *, on_break: Optional[Callable[[str], None]] = None):
        self.cr = chunk_room
        self.room = chunk_room.room
        self.on_break = on_break                 # recebe o tipo do bloco quebrado
        # Ganchos opcionais (ex.: baús levam o conteúdo junto / soltam-no):
        #   on_moved(origem, destino)  -> bloco mudou de célula pela física
        #   on_destroyed(owner, tipo)  -> bloco saiu do mundo (quebrou/esmagou)
        self.on_moved: Optional[Callable] = None
        # Dano por desabamento (engine/impacts.py): o main.py liga um
        # ImpactSystem aqui; blocos a cair/tombar ferem quem atingirem.
        self.impacts = None
        self.on_destroyed: Optional[Callable] = None
        #   on_void(owner, tipo, destino|None) -> caiu no void (destino = onde
        #   reapareceu um bloco inquebrável; None = sumiu)
        self.on_void: Optional[Callable] = None
        # Blocos "pendura" (maçãs) que se soltaram: já não se seguram em cima.
        self.loose: Set[Cell] = set()
        # Dano acumulado por âncora: (total, instante do último golpe). Golpes
        # seguidos somam; sem levar golpe por DAMAGE_MEMORY s, o bloco "sara".
        self.damage: Dict[Cell, Tuple[float, float]] = {}
        self.links: Dict[Cell, Set[Cell]] = {}
        for a, b in self.room.effective_links():
            self.links.setdefault(a, set()).add(b)
            self.links.setdefault(b, set()).add(a)
        # Ligações que a seed não recria (vão para rooms.json). Mantidas aos
        # poucos, para gravar sem percorrer as ~100 mil ligações das árvores.
        self.extra_links: Set[Tuple[Cell, Cell]] = set(self.room.stored_links)
        # Cola passada numa face sem bloco encostado: (célula, direção). O
        # próximo bloco posto encostado a essa face fica colado.
        self.glue_faces: Set[Tuple[Cell, Tuple[int, int, int]]] = set(
            getattr(self.room, "glue_faces", set()))
        self.fader = None
        self.motions: List[_Motion] = []
        self.topples: List[dict] = []    # estruturas a tombar (corpo rígido)
        self.reserved: Set[Cell] = set()
        self.fx = None                   # partículas (criadas no 1º uso, precisam do Ursina)
        self._debug = None              # pontos amarelos nas ligações (F4)
        self._debug_timer = 0.0
        self._labels: list = []         # etiquetas de resistência (F4)
        self._labels_timer = 0.0
        # --- física no servidor ---------------------------------------------
        # remote=True (cliente online): esta física NÃO mexe no mundo; o
        # servidor decide e manda os resultados. Ela só anima o que chega
        # (play_remote) e responde a leituras (inspecionar, peso...).
        self.remote = False
        # on_anim(tipo, dados): o servidor avisa os clientes de quedas e
        # tombos para eles animarem ("motion" / "topple").
        self.on_anim: Optional[Callable] = None
        # on_burst(célula, tipo, n, velocidade): partículas (servidor -> clientes)
        self.on_burst: Optional[Callable] = None
        self.remote_anims: list = []
        self.drops_pending: list = []       # blocos de "ao_quebrar" a assentar
        # Blocos de "baixa interação" (pedra, "baixa_interacao" em blocks.json):
        # ficam fixos até levarem ``despertar`` de dano; aí "acordam" (física
        # normal) e acordam também os do mesmo tipo num raio (sem cascata).
        self.awake: Set[Cell] = set()
        self._low_cache: Dict[str, Optional[dict]] = {}
        self._just_woken: List[Cell] = []
        self._prewarm()

    # ---------------------------------------------------------------- baixa interação
    def _low_spec(self, tipo) -> Optional[dict]:
        if not tipo:
            return None
        hit = self._low_cache.get(tipo, False)
        if hit is False:
            from blocks import get_block_type
            bt = get_block_type(tipo)
            spec = (bt.raw.get("block_properties") or {}).get("baixa_interacao") if bt else None
            hit = self._low_cache[tipo] = spec if isinstance(spec, dict) else None
        return hit

    def dormant(self, owner) -> bool:
        """Pedra ainda "adormecida": não cai, não é empurrada, não quebra."""
        return (owner not in self.awake
                and self._low_spec(self.cr.block_map.get(owner)) is not None)

    def _wake(self, owner, spec) -> None:
        """Acorda ``owner`` e cria o campo: todos os blocos com
        ``baixa_interacao`` a ``raio`` casas também acordam (só estes: os que
        acordam pelo campo não criam outro, por isso não há cascata)."""
        self.awake.add(owner)
        r = float(spec.get("raio", 3))
        ri = int(math.ceil(r))
        x0, y0, z0 = owner
        woken = []
        for dx in range(-ri, ri + 1):
            for dy in range(-ri, ri + 1):
                for dz in range(-ri, ri + 1):
                    if dx * dx + dy * dy + dz * dz > r * r:
                        continue
                    c = (x0 + dx, y0 + dy, z0 + dz)
                    if c == owner or c in self.awake:
                        continue
                    if self._low_spec(self.cr.block_map.get(c)) is not None:
                        self.awake.add(c)
                        woken.append(c)
        if woken:
            self._burst_at(owner, self.cr.block_map.get(owner), count=20, speed=6.0)
        self._just_woken += woken + [owner]

    def _take_woken(self) -> List[Cell]:
        out, self._just_woken = self._just_woken, []
        return out

    def _prewarm(self):
        """Monta já as formas das cópias (tronco em pé/deitado, folha...), para o
        primeiro golpe não travar a montar malhas."""
        try:
            from blocks import all_block_types
            from engine.physics_fx import _template
        except Exception:
            return
        try:
            for name, bt in all_block_types().items():
                if bt.format == "produced":
                    continue
                orients = [None]
                if bt.format in ("cylindrical", "cylinder"):
                    orients += ["deitado_l_o", "deitado_n_s"]
                for o in orients:
                    _template(name, o)
        except Exception as exc:
            print(f"[física] pré-carregamento das formas: {exc}")

    # ---------------------------------------------------------------- dados
    def physics_of(self, tipo) -> Dict[str, float]:
        """Física do bloco em unidades de jogo derivadas das físicas:

        * ``mass`` (kg) = ``peso``; ``weight`` (N) = massa x g;
        * ``move`` (pontos) = atrito x m x g x 1 m / 100 (ou ``"empurrar"`` J / 100);
        * ``break`` (pontos) = ``tenacidade`` J / 100;
        * ``stack`` (N) = ``estabilidade``; ``crush`` (N) = ``compressao``.

        Os ``move``/``break`` antigos ainda são aceites (convertidos)."""
        from blocks import get_block_type
        bt = get_block_type(tipo) if tipo else None
        props = (bt.raw.get("block_properties", {}) or {}) if bt else {}
        raw = props.get("physics") if bt else None
        out = dict(DEFAULT_PHYSICS)
        out["on_topple"] = ""
        out["structural"] = True
        try:
            mass = max(0.0, float(props.get("peso", DEFAULT_MASS_KG)))
        except (TypeError, ValueError):
            mass = DEFAULT_MASS_KG
        out["mass"] = mass
        out["weight"] = mass * G
        out["move"] = push_force_n(mass) / J_PER_HP
        if isinstance(raw, dict):
            def num(key):
                try:
                    v = raw.get(key)
                    return None if v is None else max(0.0, float(v))
                except (TypeError, ValueError):
                    return None
            friction = num("atrito")
            push_j = num("empurrar")
            if push_j is not None:
                out["move"] = push_j / J_PER_HP
            elif friction is not None:
                out["move"] = push_force_n(mass, friction) / J_PER_HP
            tough = num("tenacidade")
            legacy_break, legacy_move = num("break"), num("move")
            if tough is not None:
                out["break"] = tough / J_PER_HP
            elif legacy_break is not None:
                out["break"] = legacy_break
            stack = num("estabilidade")
            if stack is not None:
                out["stack"] = stack
            elif legacy_move is not None:
                out["stack"] = legacy_move * REF_BLOCK_N
            crush = num("compressao")
            out["crush"] = crush if crush is not None else out["break"] * REF_BLOCK_N
            out["on_topple"] = str(raw.get("on_topple") or "").strip().casefold()
            # "inquebravel": true -> nunca quebra (golpes, peso, quedas), mas
            # continua a poder ser empurrado e pego (ex.: minoculus).
            if raw.get("inquebravel"):
                out["break"] = float("inf")
                out["crush"] = float("inf")
        # "structural": false -> um golpe neste bloco só o afeta a ele, nunca
        # a estrutura (folhas: bater numa folha não derruba a árvore).
        # Sem a chave: blocos que somem ao tombar (folhas) não são estruturais.
        structural = raw.get("structural") if isinstance(raw, dict) else None
        if structural is None:
            out["structural"] = out["on_topple"] != "vanish"
        else:
            out["structural"] = bool(structural)
        out["break"] = out["break"] * BREAK_FACTOR       # inf continua inf
        return out

    def _has_entity_health(self, tipo) -> bool:
        """Blocos com ``health`` (ex.: stand) usam a vida de entidade, não esta."""
        from blocks import get_block_type
        bt = get_block_type(tipo) if tipo else None
        props = (bt.raw.get("block_properties", {}) or {}) if bt else {}
        return bool(props.get("health"))

    def footprint(self, owner) -> List[Cell]:
        tipo = self.cr.block_map.get(owner)
        return self.cr.cells_for_block(owner, tipo) if tipo else [owner]

    def occupied(self, cell) -> bool:
        return cell in self.cr.cells or cell in self.reserved

    # ---------------------------------------------------------------- carga
    def column_load(self, owner) -> float:
        """Peso dos blocos empilhados logo por cima (até haver um vão)."""
        cells = self.footprint(owner)
        top = max(c[1] for c in cells)
        load, seen = 0.0, {owner}
        for (x, _y, z) in {(c[0], 0, c[2]) for c in cells}:
            y = top + 1
            while y - top <= MAX_COLUMN:
                above = self.cr.owner_at((x, y, z))
                if above is None:
                    break
                if above not in seen:
                    seen.add(above)
                    load += self.physics_of(self.cr.block_map.get(above))["weight"]
                y += 1
        return load

    def load_of(self, owner, group=None) -> float:
        """Carga sobre o bloco.

        Numa estrutura colada, o peso empilhado é repartido por igual (média)
        e, além disso, cada bloco soma o peso dos blocos que segura "no ar"
        pela cola (ver ``_hang_loads``)."""
        group = group if group is not None else self.structure(owner)
        if len(group) <= 1:
            return self.column_load(owner)
        return self._layer_loads(group).get(owner, 0.0) + self._hang_loads(group).get(owner, 0.0)

    def _layer_loads(self, group) -> Dict[Cell, float]:
        """Carga de cada bloco colado = peso por cima da sua camada / área dela.

        Como tensão = força / área: os blocos colados da mesma altura dividem
        entre si o peso que está por cima deles. Uma coluna 1x1 em cima de uma
        base colada 2x2 pesa 1/4 em cada bloco da base; numa 3x3, 1/9.
        """
        members = [o for o in group if o in self.cr.block_map]
        by_y: Dict[int, List[Cell]] = {}
        for o in members:
            by_y.setdefault(o[1], []).append(o)
        out: Dict[Cell, float] = {}
        for y, layer in by_y.items():
            total = sum(self.column_load(o) for o in layer)
            share = total / len(layer)
            for o in layer:
                out[o] = share
        return out

    def _avg_column_load(self, group) -> float:
        members = [o for o in group if o in self.cr.block_map]
        return sum(self.column_load(o) for o in members) / max(1, len(members))

    def _rests(self, owner) -> bool:
        """Tem alguma coisa (qualquer bloco, ou o fundo do mundo) logo por baixo?"""
        for (x, y, z) in self.footprint(owner):
            if y - 1 < 0:
                return True
            below = self.cr.owner_at((x, y - 1, z))
            if below is not None and below != owner:
                return True
        return False

    def _grounded(self, members) -> Set[Cell]:
        """Membros apoiados de verdade: com o fundo do mundo, um bloco de fora
        da estrutura ou um membro já apoiado logo por baixo (de baixo para
        cima). Um bloco em cima de um bloco pendurado também está pendurado."""
        member_set = set(members)
        grounded: Set[Cell] = set()
        for o in sorted(members, key=lambda c: c[1]):
            for (x, y, z) in self.footprint(o):
                if y - 1 < 0:
                    grounded.add(o)
                    break
                below = self.cr.owner_at((x, y - 1, z))
                if below is None or below == o:
                    continue
                if below not in member_set or below in grounded:
                    grounded.add(o)
                    break
        return grounded

    def _hang_loads(self, group) -> Dict[Cell, float]:
        """Peso extra de cada bloco por segurar blocos "erguidos" pela cola.

        Um bloco sem nada por baixo, colado de lado, pendura-se no vizinho
        colado mais próximo que esteja apoiado. O peso dele (e do que estiver
        empilhado em cima) soma-se a cada bloco do caminho até esse apoio:
        numa viga de 3 blocos para fora de uma parede, o bloco da parede
        carrega os 3, o primeiro da viga carrega os 2 de fora, etc.
        """
        members = [o for o in group if o in self.cr.block_map]
        resting = self._grounded(members)
        hanging = [o for o in members if o not in resting]
        out: Dict[Cell, float] = {}
        if not hanging or not resting:
            return out
        parent: Dict[Cell, Cell] = {}
        queue = deque(resting)
        seen = set(resting)
        while queue:
            cur = queue.popleft()
            for other in self.links.get(cur, ()):
                if other in seen or other not in self.cr.block_map or other in resting:
                    continue
                seen.add(other)
                parent[other] = cur
                queue.append(other)
        for h in hanging:
            if h not in parent:
                continue                           # pendurado em nada: vai cair
            weight = (self.physics_of(self.cr.block_map[h])["weight"]
                      + self.column_load(h))
            cur = parent[h]
            while True:
                out[cur] = out.get(cur, 0.0) + weight
                if cur not in parent:
                    break
                cur = parent[cur]
        return out

    @staticmethod
    def resist(phys, load) -> Tuple[float, float]:
        """(mover, quebrar) efetivos sob ``load`` de peso em cima, como no mundo real:

        * **mover** sobe com o peso: quanto mais carregado, mais o bloco fica
          preso ao chão (atrito). A base de uma pilha é a parte mais estável:
          ``move x (1 + LOAD_FACTOR x carga)``;
        * **quebrar** desce com o peso: o material já está comprimido, por isso
          parte com menos: ``break / (1 + LOAD_FACTOR x carga)``. A base é a mais
          frágil, e se ela parte o que está em cima desaba.
        """
        k = 1.0 + LOAD_FACTOR * max(0.0, load) / REF_BLOCK_N
        return phys["move"] * k, phys["break"] / k

    def effective(self, owner, group=None) -> Dict[str, float]:
        """Resistências efetivas agora (ver ``resist``)."""
        tipo = self.cr.block_map.get(owner)
        phys = self.physics_of(tipo)
        # O chão natural é terreno compactado: o peso por cima (as outras
        # camadas do chão) não o deixa mais frágil nem mais preso.
        load = 0.0 if self.is_ground(owner) else self.load_of(owner, group)
        move, brk = self.resist(phys, load)
        return {"move": move, "break": brk,
                "load": load, "base_move": phys["move"], "base_break": phys["break"]}


    def is_tree(self, owner) -> bool:
        """Faz parte de uma árvore ainda em pé (ligações naturais da seed)?"""
        natural = self.room.natural_links
        return any(self._pair(owner, other) in natural for other in self.links.get(owner, ()))

    # ---------------------------------------------------------------- ligações
    @staticmethod
    def _pair(a, b):
        return (a, b) if a <= b else (b, a)

    def _link(self, a, b):
        self._links_version = getattr(self, "_links_version", 0) + 1
        self.links.setdefault(a, set()).add(b)
        self.links.setdefault(b, set()).add(a)
        pair = self._pair(a, b)
        if pair not in self.room.natural_links:
            self.extra_links.add(pair)

    def _drop_glue_faces(self, owner) -> bool:
        """A cola das faces de um bloco que sai do lugar perde-se."""
        if not self.glue_faces:
            return False
        cells = set(self.footprint(owner)) | {owner}
        gone = {f for f in self.glue_faces if f[0] in cells}
        if gone:
            self.glue_faces -= gone
            save = getattr(self.room, "save_glue_faces", None)
            if save is not None:
                save(self.glue_faces)
        return bool(gone)

    def _unlink_all(self, owner):
        self._drop_glue_faces(owner)
        if owner in self.links:
            self._links_version = getattr(self, "_links_version", 0) + 1
        for other in self.links.pop(owner, set()):
            self.extra_links.discard(self._pair(owner, other))
            peers = self.links.get(other)
            if peers is not None:
                peers.discard(owner)
                if not peers:
                    self.links.pop(other, None)

    def _save_links(self):
        self.room.save_stored_links(self.extra_links)

    def is_attached(self, cell) -> bool:
        """O bloco está preso a uma estrutura (colado ou árvore em pé)?"""
        owner = self.cr.owner_at(cell)
        return bool(owner is not None and self.links.get(owner))

    def is_ground(self, owner) -> bool:
        """Chão natural da sala (camada y=0 gerada, nunca mexida): a cola não liga a ele.
        Pedra adormecida (baixa interação) conta como chão; acordada, nunca."""
        if owner in self.awake:
            return False
        if self._low_spec(self.cr.block_map.get(owner)) is not None:
            return True
        check = getattr(self.room, "is_natural_ground", None)
        if check is not None:
            return check(owner)
        return owner[1] == 0 and not self.room.is_modified(owner)

    def structure(self, owner) -> Set[Cell]:
        """Blocos ligados (direta ou indiretamente) a ``owner``, incluindo ele."""
        seen = {owner}
        queue = deque([owner])
        while queue and len(seen) < MAX_STRUCTURE:
            for other in self.links.get(queue.popleft(), ()):
                if other not in seen and other in self.cr.block_map:
                    seen.add(other)
                    queue.append(other)
        return seen

    def glue(self, cell, normal=None) -> int:
        """Cola na face ``normal`` do bloco de ``cell`` (o lado clicado).

        Se já há um bloco encostado nessa face, os dois ficam ligados. Se a
        face está livre, ela fica com cola: o próximo bloco posto ali fica
        colado (e pode ficar "erguido" no ar, preso só por esse lado).
        Devolve 1 se pôs cola, 0 se não havia o que fazer.
        """
        if self.remote:
            return 0          # online: quem decide é o servidor
        owner = self.cr.owner_at(cell)
        if owner is None:
            return 0
        if normal is None:                       # compatibilidade: todos os lados
            added = sum(self.glue(cell, d) for d in NEIGHBORS6)
            return 1 if added else 0
        d = tuple(int(round(v)) for v in normal)
        if sum(abs(v) for v in d) != 1:
            return 0
        target = (cell[0] + d[0], cell[1] + d[1], cell[2] + d[2])
        other = self.cr.owner_at(target)
        if other is not None and other != owner:
            if other in self.links.get(owner, ()):
                return 0                              # já colados
            self._link(owner, other)
            self._save_links()
        else:
            face = (tuple(cell), d)
            if face in self.glue_faces:
                return 0
            self.glue_faces.add(face)
            self.room.save_glue_faces(self.glue_faces)
        self.flash_face(cell, d)
        return 1

    def _attach_glued_faces(self, owner) -> bool:
        """Um bloco novo encostado a faces com cola fica colado a elas."""
        if not self.glue_faces:
            return False
        joined = False
        for c in self.footprint(owner):
            for d in NEIGHBORS6:
                n = (c[0] - d[0], c[1] - d[1], c[2] - d[2])
                face = (n, d)
                if face not in self.glue_faces:
                    continue
                other = self.cr.owner_at(n)
                self.glue_faces.discard(face)
                if other is not None and other != owner:
                    self._link(owner, other)
                    joined = True
        self.room.save_glue_faces(self.glue_faces)
        if joined:
            self._save_links()
        return joined

    def flash_face(self, cell, direction, seconds: float = 2.0) -> None:
        """A face que recebeu cola fica branca por ``seconds`` e depois some."""
        try:
            from ursina import Entity, color
            size = self.cr.size
            p = self.cr.block_to_world(cell)
            dx, dy, dz = direction
            off = size / 2 + 0.02
            rot = (90 if dy else 0, 90 if dx else 0, 0)    # o quad nasce virado para z
            e = Entity(model="quad", color=color.white, double_sided=True,
                       position=(p.x + dx * off, p.y + dy * off, p.z + dz * off),
                       rotation=rot, scale=size * 0.98, unlit=True,
                       add_to_scene_entities=False)
            e.ignore = True
            fader = self._fader()
            if fader is not None:
                fader.add([e], duration=0.35, delay=seconds)
            else:
                from ursina import destroy
                destroy(e, delay=seconds)
        except Exception as exc:
            print(f"[física] não foi possível marcar a cola: {exc}")

    # ---------------------------------------------------------------- impacto
    def hit(self, cell, damage: float, direction: Tuple[int, int, int]) -> Optional[dict]:
        """Um golpe no bloco de ``cell``: nada, move ou quebra, decidido na hora.

        Mover compara só este golpe com a resistência efetiva (base reduzida
        pela carga, com a cola a repartir o peso pela estrutura). Quebrar usa o
        dano **acumulado**: golpes no mesmo bloco somam enquanto não passar
        ``DAMAGE_MEMORY`` s sem levar golpe (dá para partir madeira à mão).

        * golpe < mover          -> nada (mas o dano fica acumulado);
        * mover <= golpe         -> o bloco é arrancado 1 casa (uma árvore em
          pé tomba inteira) e leva o dano acumulado junto;
        * acumulado >= quebrar   -> o bloco quebra.
        """
        if self.remote:
            return None          # online: quem decide é o servidor
        owner = self.cr.owner_at(cell)
        if owner is None or damage <= 0 or owner in self.reserved:
            return None
        tipo = self.cr.block_map.get(owner)
        if tipo is None or self._has_entity_health(tipo):
            return None
        low = self._low_spec(tipo)
        if low is not None and owner not in self.awake:
            # Baixa interação: até ``despertar`` de dano acumulado nada acontece.
            now = _time.monotonic()
            prev = self.damage.get(owner)
            total = damage + (prev[0] if prev is not None and now - prev[1] <= DAMAGE_MEMORY else 0.0)
            need = float(low.get("despertar", 50))
            if total < need:
                self.damage[owner] = (total, now)
                self._burst_at(owner, tipo, count=4, speed=3.0)
                return {"block": tipo, "move_needed": need, "break_needed": need,
                        "load": 0.0, "result": "nada", "moved": None, "toppled": None,
                        "broken": [], "accumulated": total, "dormente": True}
            self._wake(owner, low)
        group = self.structure(owner)
        # Folha (ou outro bloco não estrutural) presa a uma árvore: o golpe é
        # só dela, com a resistência dela sozinha (sem a carga da árvore).
        alone = len(group) > 1 and (
            not self.physics_of(tipo)["structural"]
            # colada ao chão natural: a estrutura não sai inteira do lugar
            or any(self.is_ground(o) for o in group if o in self.cr.block_map))
        eff = dict(self.effective(owner, {owner} if alone else group))
        eff["move"] *= HIT_MOVE_FACTOR       # golpe: mais difícil arrancar que encostar e empurrar
        self._burst_at(owner, tipo, count=6, speed=4.0)       # lascas do golpe
        now = _time.monotonic()
        prev = self.damage.get(owner)
        total = damage
        if prev is not None and now - prev[1] <= DAMAGE_MEMORY:
            total += prev[0]
        self.damage[owner] = (total, now)
        summary = {"block": tipo, "move_needed": eff["move"], "break_needed": eff["break"],
                   "load": eff["load"], "result": "nada", "moved": None,
                   "toppled": None, "broken": [], "accumulated": total}
        vacated: List[Cell] = []
        if total >= eff["break"]:
            rest = set(group) - {owner} if len(group) > 1 and not alone else set()
            vacated += self._break(owner)
            summary["broken"].append(tipo)
            summary["result"] = "quebrou"
            # Partiu a base de uma árvore/estrutura: o resto, sem apoio, não
            # cai a direito: tomba para o lado do golpe (como cortar um tronco).
            rest = {o for o in rest if o in self.cr.block_map}
            if rest:
                for part in self._split_groups(rest):
                    if not self._supported(part):
                        result = self._topple(part, direction, keep_links=False)
                        if result is not None:
                            summary["toppled"] = result
        else:
            result = None
            if len(group) > 1 and not alone and damage >= eff["move"]:
                # Árvore ou construção colada: mexe-se a estrutura inteira,
                # como um corpo só (alta tomba, baixa/larga desliza).
                result = self._move_structure(group, direction)
                if result is not None:
                    summary["toppled"] = result if result["kind"] == "tombou" else None
                    summary["moved"] = result.get("moved")
                    summary["result"] = result["kind"]
            if result is None:
                # Empurrar: o bloco leva à frente a fila de blocos soltos que
                # estiver no caminho, e a força precisa é a soma de todos.
                row, need = self._push_row(owner, direction, eff["move"], HIT_MOVE_FACTOR)
                summary["move_needed"] = need
                if row is None:
                    if damage >= eff["move"]:
                        summary["result"] = "preso"   # algo fixo no caminho
                elif damage >= need:
                    summary["moved"] = self._push_many(row, direction)
                    summary["result"] = "moveu"
                    summary["pushed"] = len(row)
                    vacated += [c for o in row for c in self.footprint(o)]
        if summary["result"] in ("quebrou", "moveu", "tombou", "deslizou"):
            self._save_links()
        # as pedras acordadas pelo campo que ficaram sem apoio caem agora
        self.settle(vacated + self._take_woken())
        return summary

    # ---------------------------------------------------------------- empurrar
    def push_need(self, cell, direction):
        """Empurrar sem golpe (agachado, encostado ao bloco): (fila, força em
        N) para arrastar 1 m o bloco de ``cell`` e os soltos à frente dele.
        Fila None: não sai (colado, chão, pedra adormecida, algo fixo à frente)."""
        owner = self.cr.owner_at(cell)
        if owner is None or owner in self.reserved:
            return None, 0.0
        tipo = self.cr.block_map.get(owner)
        if (tipo is None or self._has_entity_health(tipo) or self.links.get(owner)
                or (self._low_spec(tipo) is not None and owner not in self.awake)):
            return None, 0.0
        eff = self.effective(owner, {owner})
        row, need = self._push_row(owner, direction, eff["move"] * PUSH_MOVE_FACTOR, PUSH_MOVE_FACTOR)
        return row, need * J_PER_HP        # J para arrastar 1 m = N

    def push(self, cell, direction, force_n: float) -> Optional[dict]:
        """Empurrar sem golpe: se ``force_n`` paga o atrito da fila, ela anda
        1 casa (não guarda dano: não parte nada)."""
        if self.remote:
            return None          # online: quem decide é o servidor
        row, need = self.push_need(cell, direction)
        out = {"result": "preso", "need": need, "moved": None}
        if row is None:
            return out
        if float(force_n) + 1e-6 < need:
            out["result"] = "fraco"
            return out
        out["moved"] = self._push_many(row, direction)
        out["result"] = "moveu"
        out["pushed"] = len(row)
        self._save_links()
        self.settle([c for o in row for c in self.footprint(o)] + self._take_woken())
        return out

    def burn(self, owner, fraction: float) -> None:
        """Fogo (engine/fire.py): soma ``fraction`` do "quebrar" do bloco ao
        dano guardado (como um golpe, mas sem o partir nem o mover: quem o
        consome é o fogo). Mantém a memória do dano viva enquanto arde, por
        isso um bloco a arder fica cada vez mais fácil de partir."""
        tipo = self.cr.block_map.get(owner)
        if tipo is None or fraction <= 0:
            return
        try:
            brk = float(self.physics_of(tipo).get("break", 0.0))
        except Exception:
            return
        if brk <= 0:
            return
        now = _time.monotonic()
        prev = self.damage.get(owner)
        total = prev[0] if prev is not None and now - prev[1] <= DAMAGE_MEMORY else 0.0
        total = min(brk * 0.98, total + brk * float(fraction))   # partir é com um golpe
        self.damage[owner] = (total, now)

    def stored_damage(self, owner):
        """(dano guardado, segundos até sarar) do bloco; (0, 0) sem dano."""
        entry = self.damage.get(owner)
        if not entry:
            return 0.0, 0.0
        left = DAMAGE_MEMORY - (_time.monotonic() - entry[1])
        if left <= 0:
            return 0.0, 0.0
        return float(entry[0]), left

    def footprint_cells(self, owner, tipo):
        return self.cr.cells_for_block(owner, tipo)

    # ---------------------------------------------------------------- tombar
    @staticmethod
    def _rotate_cell(cell, axis, sign, pivot_u, base_y):
        """Gira uma célula 90° sobre a aresta (pivot_u, base_y), para o lado ``sign``."""
        # Igual ao giro da animação: o centro do bloco roda 90° em torno da
        # aresta de baixo da base, do lado da queda; a base vai para a casa
        # seguinte, deitada.
        x, y, z = cell
        u = x if axis == "x" else z
        new_u = pivot_u + sign * (1 + y - base_y)
        new_y = base_y + sign * (pivot_u - u)
        return (new_u, new_y, z) if axis == "x" else (x, new_y, new_u)

    @staticmethod
    def _rotate_orientation(tipo, orientation, axis):
        """Troncos: em pé viram deitados no eixo da queda, e vice-versa."""
        from blocks import get_block_type
        bt = get_block_type(tipo)
        if bt is None or bt.format not in ("cylindrical", "cylinder"):
            return orientation
        lying = "deitado_l_o" if axis == "x" else "deitado_n_s"
        current = orientation or "cima"
        if current == "cima":
            return lying
        if current == lying:
            return "cima"
        return current          # deitado no outro eixo: continua igual

    def _move_structure(self, group: Set[Cell], direction) -> Optional[dict]:
        """Golpe que passou de "mover" numa estrutura (árvore ou colada).

        Como um corpo rígido: se é mais alta do que comprida na direção do
        golpe, tomba (gira 90° sobre a aresta da base); senão desliza 1 casa.
        Uma árvore tomba e os troncos ficam soltos; uma construção colada
        perde a cola ao tombar ou deslizar (chega como blocos soltos). None se não há espaço.
        """
        dx, _dy, dz = direction
        if not (dx or dz):
            return None
        members = [o for o in group if o in self.cr.block_map]
        tree = any(self.is_tree(o) for o in members)
        solid = [o for o in members
                 if self.physics_of(self.cr.block_map[o])["on_topple"] != "vanish"] \
            or members
        cells = [c for o in solid for c in self.footprint(o)]
        height = max(c[1] for c in cells) - min(c[1] for c in cells) + 1
        us = [c[0] if dx else c[2] for c in cells]
        length = max(us) - min(us) + 1
        if tree or height > length:
            # A cola não sobrevive ao tombo: chega ao chão como blocos soltos.
            result = self._topple(group, direction, keep_links=False)
            if result is not None:
                result["kind"] = "tombou"
                return result
        return self._slide(members, direction)

    def _slide(self, members: List[Cell], direction) -> Optional[dict]:
        """A estrutura inteira desliza 1 casa na direção do golpe (e perde a cola)."""
        dx, _dy, dz = direction
        delta = (1 if dx > 0 else -1, 0, 0) if dx else (0, 0, 1 if dz > 0 else -1)
        own = {c for o in members for c in self.footprint(o)}
        for (x, y, z) in own:
            c = (x + delta[0], y, z + delta[2])
            if not self.cr.in_bounds(c) or (self.occupied(c) and c not in own):
                return None
        self._move_group(sorted(members), delta, fall=False)
        return {"kind": "deslizou", "blocks": len(members), "moved": delta}

    def _topple(self, group: Set[Cell], direction, keep_links: bool = False) -> Optional[dict]:
        """A estrutura inteira gira 90° para o lado do golpe e fica deitada."""
        dx, _dy, dz = direction
        if not (dx or dz):
            return None
        axis, sign = ("x", 1 if dx > 0 else -1) if dx else ("z", 1 if dz > 0 else -1)
        members = [o for o in group if o in self.cr.block_map]
        # Só blocos 1x1x1 rodam bem na grade; produced maiores bloqueiam.
        for o in members:
            if len(self.footprint(o)) != 1:
                return None
        vanish = {o for o in members
                  if self.physics_of(self.cr.block_map[o])["on_topple"] == "vanish"}
        solid = [o for o in members if o not in vanish]
        if not solid:
            return None
        base_y = min(o[1] for o in solid)
        us = [o[0] if axis == "x" else o[2] for o in solid]
        pivot_u = max(us) if sign > 0 else min(us)
        targets = {o: self._rotate_cell(o, axis, sign, pivot_u, base_y) for o in solid}
        moving = set(members)

        def blocked(lift):
            for t in targets.values():
                c = (t[0], t[1] + lift, t[2])
                if not self.cr.in_bounds(c):
                    return True
                if self.occupied(c) and self.cr.owner_at(c) not in moving:
                    return True
            return False

        lift = 0
        while blocked(lift):
            lift += 1
            if lift > TOPPLE_MAX_LIFT:
                return None

        # Ligações entre os blocos que rodam, já nas posições novas.
        new_links = []
        for o in solid:
            for other in self.links.get(o, ()):
                if other in targets and o < other:
                    a, b = targets[o], targets[other]
                    new_links.append(((a[0], a[1] + lift, a[2]), (b[0], b[1] + lift, b[2])))
        blocks, sources = [], []
        for o in solid:
            tipo = self.cr.block_map[o]
            t = targets[o]
            dest = (t[0], t[1] + lift, t[2])
            orient = self._rotate_orientation(tipo, self.cr.orientation_at(o), axis)
            blocks.append((dest, tipo, orient, o))
            hp = self.damage.pop(o, None)
            if hp is not None:
                self.damage[dest] = hp
            self._notify("on_moved", o, dest)
        # Cópias de todos (folhas incluídas: caem junto e desfazem-se no chão).
        size = self.cr.size
        proxies_solid, proxies_vanish = [], []
        for dest, tipo, orient, o in blocks:
            proxies_solid.append((self._proxy(o, tipo, self.cr.orientation_at(o)),
                                  dest, tipo, orient))
        for o in vanish:
            tipo = self.cr.block_map[o]
            proxies_vanish.append((self._proxy(o, tipo, self.cr.orientation_at(o)), tipo))
        vacated = []
        for o in members:
            vacated += self.footprint(o)
            self._unlink_all(o)
            if o in vanish:
                self.damage.pop(o, None)
                self._notify("on_destroyed", o, self.cr.block_map.get(o))
            self.cr.remove_block(o)
        # A cópia está por cima do bloco original até o chunk ser refeito
        # (no ritmo normal do jogo, sem travar).
        # Os troncos de uma árvore derrubada ficam soltos (avulsos): podem ser
        # pegos com Q, empurrados ou partidos um a um. Uma construção colada
        # perde a cola também (keep_links=False em _move_structure).
        if not keep_links:
            new_links = []
        reserved = {d for d, _t, _o, _s in blocks}
        self.reserved |= reserved

        # Eixo: aresta de baixo da base, do lado da queda (coordenadas Ursina).
        edge_u = (pivot_u + 0.5 * sign) * size
        pivot_y = base_y * size
        pivot = (edge_u, pivot_y, 0.0) if axis == "x" else (0.0, pivot_y, edge_u)
        fall_dir = (sign, 0, 0) if axis == "x" else (0, 0, sign)
        height = (max(o[1] for o in members) - base_y + 1) * size
        body = None
        try:
            from engine.physics_fx import ToppleBody
            body = ToppleBody([e for e, *_ in proxies_solid] + [e for e, _t in proxies_vanish],
                              pivot, (0, 1, 0), fall_dir, height, lift * size)
        except Exception as exc:
            print(f"[física] animação de tombar indisponível: {exc}")
        topple = {"body": body, "solid": proxies_solid,
                  "vanish": proxies_vanish, "reserved": reserved,
                  "links": new_links}
        if self.on_anim is not None:
            self.on_anim("topple", {
                # 5º valor: orientação ANTES do tombo (a cópia começa assim e
                # o tombo roda-a; o 4º é a orientação no chão, já deitada).
                "solid": [[list(o), list(dest), tipo, orient, self.cr.orientation_at(o)]
                          for dest, tipo, orient, o in blocks],
                "vanish": [[list(o), t] for o, (_e, t) in zip(vanish, proxies_vanish)],
                "pivot": list(pivot), "fall_dir": list(fall_dir), "height": height,
                "lift": lift * size})
        # Sem gráficos (servidor): pousa quando a animação dos jogos acabar.
        from engine.topple_math import topple_duration
        topple["timer"] = topple_duration(height)
        self.topples.append(topple)
        if body is not None:
            self._launch([e for e, *_ in proxies_solid] + [e for e, _t in proxies_vanish],
                         [t for _e, _d, t, _o in proxies_solid] + [t for _e, t in proxies_vanish],
                         topple)
        self._save_links()
        self.settle(vacated)
        return {"blocks": len(solid), "vanished": len(vanish), "axis": axis,
                "sign": sign, "lift": lift}

    # ---------------------------------------------------------------- impacto
    def _launch(self, proxies, tipos, group) -> None:
        """Regista as cópias em movimento como projéteis de gravidade."""
        if self.impacts is None:
            return
        for e, tipo in zip(proxies, tipos):
            if e is None or not tipo:
                continue
            mass = self.physics_of(tipo)["mass"]
            self.impacts.launch(e, mass, "gravidade", group=group, source=tipo)

    def _release(self, proxies) -> None:
        if self.impacts is None:
            return
        for e in proxies:
            if e is not None:
                self.impacts.release(e)

    def _land_topple(self, topple) -> None:
        """Bateu no chão: as folhas desfazem-se, os troncos entram na grade."""
        from ursina import destroy
        self._release([e for e, *_ in topple["solid"]] + [e for e, _t in topple["vanish"]])
        body = topple.get("body")
        if body is not None:
            body.release()
        fx = self._particles()
        for e, tipo in topple["vanish"]:
            if fx is not None:
                try:
                    from engine.physics_fx import block_color
                    p = e.world_position
                    fx.burst((p.x, p.y, p.z), block_color(tipo), count=4, speed=3.0,
                             spread=0.8, size=0.32, life=1.1)
                except Exception:
                    pass
        fader = self._fader()
        leaves = [e for e, _t in topple["vanish"]]
        if fader is not None:
            fader.add(leaves, duration=0.3)          # desfazem-se com as partículas
        else:
            for e in leaves:
                destroy(e)
        self.reserved -= topple["reserved"]
        placed = []
        for e, dest, tipo, orient in topple["solid"]:
            if any(c in self.cr.cells for c in self.cr.cells_for_block(dest, tipo)):
                print(f"[física] destino ocupado em {dest}; bloco {tipo} perdido")
            else:
                self.cr.set_block(dest, tipo, orientation=orient)
                placed.append(dest)
        placed_set = set(placed)
        linked = False
        for a, b in topple.get("links", ()):
            if a in placed_set and b in placed_set:
                self._link(a, b)
                linked = True
        if linked:
            self._save_links()
        # Se ficou sem apoio (ex.: tombou para fora de uma beirada), cai já,
        # continuando o movimento a partir da cópia, sem "fantasma" parado.
        self.settle(placed)
        self._hand_off([(e, dest, tipo) for e, dest, tipo, _o in topple["solid"]])
        if fx is not None and placed:
            from engine.physics_fx import block_color
            for d in placed[::2]:
                p = self.cr.block_to_world(d)
                fx.burst((p.x, p.y - self.cr.size * 0.45, p.z), (0.45, 0.36, 0.25),
                         count=3, speed=2.5, spread=0.9, size=0.25, life=0.6)

    def _hand_off(self, items) -> None:
        """Depois de pousar: cada cópia (entidade, destino, tipo) dá lugar ao bloco.

        Se o bloco ficou mesmo ali, a cópia espera o chunk mostrá-lo e
        esmaece. Se a gravidade já o levou (há uma cópia nova a cair a partir
        do mesmo ponto), a cópia velha some na hora, para não ficar um
        fantasma parado no meio do caminho.
        """
        from ursina import destroy
        fader = self._fader()
        stay, stay_cells = [], []
        models, model_cells = [], []
        for e, dest, tipo in items:
            if e is None:
                continue
            if self.cr.block_map.get(dest) == tipo and dest not in self.reserved:
                if getattr(e, "_produced_copy", False):
                    models.append(e)
                    model_cells.append(dest)
                else:
                    stay.append(e)
                    stay_cells.append(dest)
            else:
                destroy(e)
        if fader is not None:
            if stay:
                fader.add(stay, cells=stay_cells, chunk_room=self.cr)
            if models:
                # Modelo igual ao que vai aparecer: troca no mesmo frame em que
                # o chunk o mostra (esmaecer faria os dois brigarem na tela).
                fader.add(models, cells=model_cells, chunk_room=self.cr, duration=0.0)
        else:
            for e in stay + models:
                destroy(e)

    # ---------------------------------------------------------------- avisos    # ---------------------------------------------------------------- avisos
    def on_placed(self, owner) -> None:
        """Um bloco foi colocado: cola às faces com cola encostadas e, se ficou
        no ar sem nada que o segure, cai."""
        if self.remote:
            return None          # online: quem decide é o servidor
        self.awake.discard(owner)          # pedra posta: adormecida
        self._attach_glued_faces(owner)
        self.settle([owner])

    def clean_outside_links(self) -> int:
        """Desfaz as ligações de blocos fora do mapa feitas pela antiga cola
        automática (só uma vez por sala) e deixa-os cair."""
        if self.remote:
            return 0          # online: quem decide é o servidor
        in_map = getattr(self.cr, "in_map", None)
        if in_map is None:
            return 0
        loose = [o for o in list(self.links)
                 if o in self.cr.block_map and not all(in_map(c) for c in self.footprint(o))]
        for o in loose:
            self._unlink_all(o)
        if loose:
            self._save_links()
            self.settle(loose)
        return len(loose)

    def on_removed(self, owner, cells) -> None:
        """Um bloco saiu do mundo (ex.: Q): solta as ligações e faz cair o resto."""
        if self.remote:
            return None          # online: quem decide é o servidor
        had_links = owner in self.links
        self.awake.discard(owner)
        self._unlink_all(owner)
        self.damage.pop(owner, None)
        if had_links:
            self._save_links()
        self.settle(cells)

    def _notify(self, name, *args) -> None:
        hook = getattr(self, name, None)
        if hook is None:
            return
        try:
            hook(*args)
        except Exception as exc:
            print(f"[física] {name}: {exc}")

    def _break(self, owner) -> List[Cell]:
        tipo = self.cr.block_map.get(owner)
        cells = self.footprint(owner)
        self._notify("on_destroyed", owner, tipo)
        self._burst_at(owner, tipo, count=14, speed=5.0)
        self.awake.discard(owner)
        self._unlink_all(owner)
        self.damage.pop(owner, None)
        self.cr.remove_block(owner)
        self._drop_on_break(owner, tipo)
        if self.on_break and tipo:
            try:
                self.on_break(tipo)
            except Exception as exc:
                print(f"[física] {exc}")
        return cells

    def _drop_on_break(self, owner, tipo) -> None:
        """``"ao_quebrar": {"bloco": "maca", "chance": 0.2}`` em blocks.json: ao
        quebrar, com essa chance, aparece esse bloco no lugar (e cai)."""
        import random
        from blocks import get_block_type
        bt = get_block_type(tipo) if tipo else None
        rule = (bt.raw.get("block_properties") or {}).get("ao_quebrar") if bt else None
        if not isinstance(rule, dict) or not rule.get("bloco"):
            return
        if random.random() >= float(rule.get("chance", 1.0)):
            return
        if self.cr.owner_at(owner) is None and get_block_type(rule["bloco"]) is not None:
            self.cr.set_block(owner, rule["bloco"], orientation=None)
            self.drops_pending.append(owner)

    PUSH_ROW_MAX = 32

    def _push_row(self, owner, direction, first_move, scale=1.0) -> Tuple[Optional[List[Cell]], float]:
        """Fila de blocos que um empurrão em ``owner`` teria de mover, e a força.

        Anda na direção do golpe enquanto houver blocos encostados; a fila
        acaba na primeira célula livre. Força precisa = soma do "mover" efetivo
        de cada bloco da fila (com o peso que cada um tem em cima): para mover
        2 terras é preciso o dobro da força de 1. Devolve (None, força) se no
        caminho houver algo que não sai do lugar (chão natural, bloco colado,
        modelo maior que 1 bloco, a borda da sala) ou se a fila for longa demais.
        """
        dx, _dy, dz = direction
        if not (dx or dz):
            return None, first_move
        if len(self.footprint(owner)) != 1 or self.is_ground(owner):
            return None, first_move                  # o chão natural não sai do lugar
        row, need = [owner], first_move
        x, y, z = owner
        while True:
            x, z = x + dx, z + dz
            cell = (x, y, z)
            if not self.cr.in_bounds(cell):
                return None, need
            other = self.cr.owner_at(cell)
            if other is None:
                if cell in self.reserved:
                    return None, need
                return row, need
            tipo = self.cr.block_map.get(other)
            if (self.links.get(other) or self.is_ground(other)
                    or len(self.footprint(other)) != 1
                    or self._has_entity_health(tipo)
                    or len(row) >= self.PUSH_ROW_MAX):
                return None, need
            need += self.resist(self.physics_of(tipo), self.column_load(other))[0] * scale
            row.append(other)

    def _push_many(self, row, direction) -> Cell:
        """Move a fila inteira 1 casa (soltam-se da cola, se tinham)."""
        dx, _dy, dz = direction
        for o in row:
            self._unlink_all(o)
            self.damage.pop(o, None)
        self._move_group(list(row), (dx, 0, dz), fall=False)
        first = row[0]
        return (first[0] + dx, first[1], first[2] + dz)

    def _push(self, owner, direction) -> Optional[Cell]:
        dx, dy, dz = direction
        if not (dx or dy or dz):
            return None
        tipo = self.cr.block_map[owner]
        dest = (owner[0] + dx, owner[1] + dy, owner[2] + dz)
        old_cells = set(self.footprint(owner))
        new_cells = self.cr.cells_for_block(dest, tipo)
        for c in new_cells:
            if not self.cr.in_bounds(c) or (self.occupied(c) and c not in old_cells):
                return None
        self._unlink_all(owner)                      # solta-se da estrutura
        hp = self.damage.pop(owner, None)
        self._move_group([owner], (dx, dy, dz), fall=False)
        if hp is not None:
            self.damage[dest] = hp
        return dest

    # ---------------------------------------------------------------- gravidade
    def _hangs(self, owner) -> bool:
        """Bloco que se segura pendurado no de cima (ex.: maçã na folha)."""
        if owner in self.loose:
            return False
        from blocks import get_block_type
        bt = get_block_type(self.cr.block_map.get(owner) or "")
        return bool(bt is not None and (bt.raw.get("block_properties") or {}).get("pendura"))

    def release(self, owner) -> None:
        """Solta um bloco pendurado (a maçã cai da árvore)."""
        if self.remote:
            return None          # online: quem decide é o servidor
        self.loose.add(owner)
        self.settle([owner])
        self.loose.discard(owner)

    def _fixed(self, owner) -> bool:
        """Bloco que flutua e não cai (pedra dura: segura o mapa)."""
        from blocks import get_block_type
        bt = get_block_type(self.cr.block_map.get(owner) or "")
        return bool(bt is not None and (bt.raw.get("block_properties") or {}).get("fixo"))

    def _supported(self, group: Set[Cell]) -> bool:
        for o in group:
            if self._fixed(o) or self.is_ground(o):    # chão gerado (ex.: pisos da dungeon)
                return True
        # torque e parafusos seguram-se no que estão ligados (eixo na caixa,
        # manivela no eixo, parafuso no de baixo): engine/torque.py
        from engine.torque import held
        for o in group:
            try:
                if held(self.cr, o, group=group):
                    return True
            except Exception:
                pass
        for o in group:
            if self._hangs(o):
                x, y, z = o
                above = self.cr.owner_at((x, y + 1, z))
                if above is not None and above not in group:
                    return True
        cells = {c for o in group for c in self.footprint(o)}
        for (x, y, z) in cells:
            below = (x, y - 1, z)
            if y - 1 <= self.cr.VOID_Y:
                continue                     # por baixo é o void: não apoia
            if below in cells:
                continue
            if below in self.reserved:
                return True
            other = self.cr.owner_at(below)
            if other is not None and other not in group:
                return True
        return False

    # ---------------------------------------------------------------- void
    def _on_void_floor(self, group) -> bool:
        """Alguma célula do grupo está logo acima do void (camada VOID_Y)?"""
        void = self.cr.VOID_Y
        return any(c[1] - 1 <= void for o in group for c in self.footprint(o))

    def _falls_into_void(self, group: Set[Cell]) -> bool:
        """Nada por baixo de nenhuma célula até ao void?"""
        cells = {c for o in group for c in self.footprint(o)}
        void = self.cr.VOID_Y
        for (x, y, z) in cells:
            for yy in range(y - 1, void, -1):
                c = (x, yy, z)
                if c in cells:
                    break
                if self.occupied(c) or c in self.reserved:
                    return False
        return True

    def _to_void(self, group: Set[Cell]) -> None:
        """Caiu no void: quebra e some. Blocos inquebráveis reaparecem num
        sítio aleatório do mapa (por cima do chão)."""
        for o in sorted(group, key=lambda c: c[1]):
            tipo = self.cr.block_map.get(o)
            if tipo is None:
                continue
            orient = self.cr.orientation_at(o)
            self._burst_at(o, tipo, count=10, speed=3.0)
            self._unlink_all(o)
            self.damage.pop(o, None)
            if self.physics_of(tipo)["break"] == float("inf"):
                self.cr.remove_block(o)
                dest = self._random_surface_cell(tipo)
                if dest is not None:
                    self.cr.set_block(dest, tipo, orientation=orient)
                    self._notify("on_moved", o, dest)
                    self._notify("on_void", o, tipo, dest)
                    continue
                self.cr.set_block(o, tipo, orientation=orient)    # sem sítio: fica
                continue
            self._notify("on_void", o, tipo, None)
            self.cr.remove_block(o)

    def _random_surface_cell(self, tipo, tries=60):
        import random
        w = self.room
        for _ in range(tries):
            x = random.randrange(0, max(1, w.size_x))
            z = random.randrange(0, max(1, w.size_z))
            top = None
            for y in range(self.cr.max_y, self.cr.VOID_Y, -1):
                if self.cr.owner_at((x, y, z)) is not None:
                    top = y
                    break
            if top is None:
                continue
            dest = (x, top + 1, z)
            if all(self.cr.in_bounds(c) and self.cr.owner_at(c) is None
                   for c in self.cr.cells_for_block(dest, tipo)):
                return dest
        return None

    def _drop_distance(self, group: Set[Cell]) -> int:
        cells = {c for o in group for c in self.footprint(o)}
        k = 0
        while True:
            n = k + 1
            for (x, y, z) in cells:
                c = (x, y - n, z)
                if y - n <= self.cr.VOID_Y or (self.occupied(c) and c not in cells):
                    return k
            k = n
            if k > 512:
                return k

    def settle(self, cells: Iterable[Cell]) -> None:
        """Faz cair o que ficou sem apoio perto das células ``cells``."""
        if self.remote:
            return None          # online: quem decide é o servidor
        candidates: Set[Cell] = set()
        for (x, y, z) in cells:
            for dx in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for dy in (-1, 0, 1, 2):
                        o = self.cr.owner_at((x + dx, y + dy, z + dz))
                        if o is not None:
                            candidates.add(o)
        # parafusos de Arquimedes: a cadeia inteira (o peso vai todo para o de baixo)
        try:
            from engine.torque import screw_chain
            for o in list(candidates):
                candidates.update(screw_chain(self.cr, o))
        except Exception:
            pass
        done: Set[Cell] = set()
        for owner in sorted(candidates, key=lambda c: c[1]):
            if owner in done or owner not in self.cr.block_map:
                continue
            group = self.structure(owner)
            done |= group
            if self._supported(group):
                continue
            k = self._drop_distance(group)
            if k <= 0:
                if self._on_void_floor(group):
                    self._to_void(group)         # já está em cima do void: some
                continue
            for o in group:
                hp = self.damage.pop(o, None)
                if hp is not None:
                    self.damage[(o[0], o[1] - k, o[2])] = hp
            self._move_group(sorted(group), (0, -k, 0), fall=True)
        self.check_stability(candidates)

    # ---------------------------------------------------------------- queda
    FALL_G = G           # gravidade em blocos/s² = m/s² (para a velocidade ao pousar)

    def _after_fall(self, landed, k) -> None:
        """Depois de cair ``k`` casas: o que caiu bate no que está por baixo
        (dano = 1/2 m v² x transferência / 100, acumula como um golpe; uma maçã a cair
        quebra a maçã de baixo) e blocos "ao_cair" mudam de tipo (a maçã
        pendurada que caiu vira maçã no chão)."""
        from blocks import get_block_type
        from engine.impacts import impact_damage
        speed = math.sqrt(2.0 * self.FALL_G * max(1, k))
        landed_set = {d for d, _t in landed}
        hits = {}
        for new, tipo in landed:
            if tipo is None or self.cr.block_map.get(new) != tipo:
                continue
            mass = self.physics_of(tipo)["mass"]
            for (x, y, z) in self.footprint(new):
                below = self.cr.owner_at((x, y - 1, z))
                if below is None or below in landed_set:
                    continue
                dmg = impact_damage(mass, speed)
                if dmg > hits.get(below, 0.0):
                    hits[below] = dmg
        for new, _t in landed:
            tipo = self.cr.block_map.get(new)
            bt = get_block_type(tipo) if tipo else None
            after = (bt.raw.get("block_properties") or {}).get("ao_cair") if bt else None
            if after and not self._hangs(new):
                orient = self.cr.orientation_at(new)
                self.cr.remove_block(new)
                self.cr.set_block(new, after, orientation=orient)
        for below, dmg in hits.items():
            self._fall_impact(below, dmg)

    def _fall_impact(self, owner, dmg) -> None:
        """Dano de uma queda no bloco de baixo: só acumula/quebra (não empurra)."""
        tipo = self.cr.block_map.get(owner)
        # (pedra adormecida conta como chão: uma queda não a acorda, para as
        # pedras que caem pelo campo não acordarem outras em cascata)
        if tipo is None or dmg <= 0 or self._has_entity_health(tipo) or self.is_ground(owner):
            return
        eff = self.effective(owner, {owner})
        now = _time.monotonic()
        prev = self.damage.get(owner)
        total = dmg + (prev[0] if prev is not None and now - prev[1] <= DAMAGE_MEMORY else 0.0)
        self.damage[owner] = (total, now)
        if total >= eff["break"]:
            cells = self._break(owner)
            self.settle(cells)

    # ---------------------------------------------------------------- desabamento
    def check_stability(self, owners: Iterable[Cell]) -> None:
        """Desabamento por excesso de peso, sem precisar de golpe.

        Para cada estrutura perto do que mudou:
          * **horizontal**: blocos sem nada por baixo, presos de lado pela
            cola, penduram-se no apoio mais próximo. Cada ligação do caminho
            aguenta um momento (peso x distância) até ``estabilidade`` x GLUE_HOLD do
            mais fraco dos dois blocos; se passar, a cola rompe ali e o que
            estava pendurado cai.
          * **vertical**: o peso que um bloco segura (empilhado em cima e
            pendurado nele) não pode passar de ``compressao`` x CRUSH_FACTOR; se
            passar, o bloco esmaga (some em lascas) e o de cima cai.
        Árvores em pé ficam de fora (as folhas penduram-se no tronco).
        """
        if self.remote:
            return None          # online: quem decide é o servidor
        if getattr(self, "_checking", False):
            return
        self._checking = True
        try:
            changed = True
            rounds = 0
            pending = {o for o in owners if o in self.cr.block_map}
            while changed and pending and rounds < 8:
                rounds += 1
                changed = False
                # inclui a coluna por baixo de cada bloco (é quem segura o peso)
                todo = set()
                for o in pending:
                    x, y, z = o
                    for dy in range(0, -MAX_COLUMN, -1):
                        below = self.cr.owner_at((x, y + dy, z))
                        if below is None:
                            break
                        todo.add(below)
                pending = set()
                seen: Set[Cell] = set()
                next_round: Set[Cell] = set()
                for owner in sorted(todo, key=lambda c: c[1]):
                    if owner in seen or owner not in self.cr.block_map:
                        continue
                    group = self.structure(owner)
                    seen |= group
                    if any(self.is_tree(o) for o in group):
                        continue
                    if len(group) == 1 and self._topple_loose_column(owner):
                        changed = True
                        continue
                    if len(group) > 1 and self._topple_slender_tower(group):
                        changed = True
                        continue
                    pending |= {o for o in group if o in self.cr.block_map}
                    cut = self._failing_joints(group) if len(group) > 1 else []
                    if cut:
                        for a, b in cut:
                            self._unlink_pair(a, b)
                        self._save_links()
                        print(f"[física] cola rompeu com o peso em {len(cut)} ligação(ões)")
                        self.settle([c for pair in cut for c in pair])
                        changed = True
                        continue
                    crushed = self._crushed(group)
                    if crushed:
                        cells = []
                        for o in crushed:
                            cells += self.footprint(o)
                            self._crush(o)
                        print(f"[física] {len(crushed)} bloco(s) esmagado(s) pelo peso")
                        self.settle(cells)
                        changed = True
        finally:
            self._checking = False

    def _loose_single(self, owner) -> bool:
        """Bloco solto (sem cola), de uma célula, que não é chão natural."""
        return (owner is not None and owner in self.cr.block_map
                and not self.links.get(owner) and not self.is_ground(owner)
                and len(self.footprint(owner)) == 1
                and not self._has_entity_health(self.cr.block_map[owner]))

    def _loose_run(self, owner) -> List[Cell]:
        """A pilha solta (de baixo para cima) que passa por ``owner``."""
        x, y, z = owner
        while self._loose_single(self.cr.owner_at((x, y - 1, z))):
            y -= 1
        run = []
        while self._loose_single(self.cr.owner_at((x, y, z))):
            run.append((x, y, z))
            y += 1
        return run

    def _topple_loose_column(self, owner) -> bool:
        """Pilha sem cola alta demais tomba (como uma torre de blocos).

        O bloco mais baixo cujo peso em cima passa de ``estabilidade`` x STACK_FACTOR
        cede: dali para cima, a pilha gira e cai deitada para um lado livre.
        """
        if not self._loose_single(owner):
            return False
        run = self._loose_run(owner)
        if len(run) < 2 or not self._rests(run[0]):
            return False
        for i, o in enumerate(run[:-1]):
            phys = self.physics_of(self.cr.block_map[o])
            if self.column_load(o) > phys["stack"] * STACK_FACTOR + 1e-9:
                segment = set(run[i:])
                dirs = [(1, 0, 0), (-1, 0, 0), (0, 0, 1), (0, 0, -1)]
                import random
                random.shuffle(dirs)
                for d in dirs:
                    if self._topple(segment, d) is not None:
                        print(f"[física] pilha de {len(segment)} bloco(s) tombou com o peso")
                        return True
                # Sem espaço para tombar (entre paredes): o bloco cede e a
                # pilha desce.
                self._crush(o)
                print("[física] pilha sem espaço para tombar: o bloco de baixo cedeu")
                self.settle([o])
                return True
        return False

    def _stack_limit(self, tipo) -> int:
        """Quantos blocos deste material ficam empilhados soltos sem tombar."""
        phys = self.physics_of(tipo)
        w = max(phys["weight"], 1e-6)
        return int(phys["stack"] * STACK_FACTOR / w + 1e-9) + 1

    def _topple_slender_tower(self, group) -> bool:
        """Estrutura colada alta e fina demais para a base: tomba inteira.

        Altura máxima = pilha solta do material x GLUED_STACK_BONUS x largura
        da base (o lado menor da base, em blocos). Base 2x2 aguenta o dobro da
        altura de uma coluna 1x1; 3x3, o triplo. Estruturas presas ao chão
        natural pela cola ficam de fora (estão ancoradas).
        """
        members = [o for o in group if o in self.cr.block_map]
        if any(self.is_ground(o) for o in members):
            return False
        cells = [c for o in members for c in self.footprint(o)]
        y0 = min(c[1] for c in cells)
        height = max(c[1] for c in cells) - y0 + 1
        if height < 3:
            return False
        base = [c for c in cells if c[1] == y0]
        width = min(len({c[0] for c in base}), len({c[2] for c in base}))
        tipos = [self.cr.block_map[o] for o in members]
        limit = min(self._stack_limit(t) for t in set(tipos))
        max_height = limit * GLUED_STACK_BONUS * max(1, width)
        if height <= max_height:
            return False
        import random
        dirs = [(1, 0, 0), (-1, 0, 0), (0, 0, 1), (0, 0, -1)]
        random.shuffle(dirs)
        for d in dirs:
            if self._topple(set(members), d) is not None:
                print(f"[física] torre colada de {height} de altura (base {width}) tombou")
                return True
        return False

    def _split_groups(self, owners) -> List[Set[Cell]]:
        """Separa ``owners`` nas estruturas (ligadas) que sobraram."""
        left = set(owners)
        parts = []
        while left:
            start = left.pop()
            part = self.structure(start) & (left | {start})
            left -= part
            parts.append(part)
        return parts

    def _unlink_pair(self, a, b) -> None:
        self._links_version = getattr(self, "_links_version", 0) + 1
        for x, y in ((a, b), (b, a)):
            peers = self.links.get(x)
            if peers is not None:
                peers.discard(y)
                if not peers:
                    self.links.pop(x, None)
        self.extra_links.discard(self._pair(a, b))

    def _failing_joints(self, group) -> List[Tuple[Cell, Cell]]:
        """Ligações de cola cujo momento (peso x distância) passa do limite."""
        members = [o for o in group if o in self.cr.block_map]
        resting = self._grounded(members)
        if not resting or len(resting) == len(members):
            return []
        parent: Dict[Cell, Cell] = {}
        depth: Dict[Cell, int] = {o: 0 for o in resting}
        queue = deque(resting)
        while queue:
            cur = queue.popleft()
            for other in self.links.get(cur, ()):
                if other in depth or other not in self.cr.block_map:
                    continue
                depth[other] = depth[cur] + 1
                parent[other] = cur
                queue.append(other)
        moment: Dict[Tuple[Cell, Cell], float] = {}
        for h in members:
            if h in resting or h not in parent:
                continue
            w = self.physics_of(self.cr.block_map[h])["weight"] + self.column_load(h)
            cur = h
            while cur in parent:
                p = parent[cur]
                joint = (cur, p)
                arm = abs(h[0] - p[0]) + abs(h[2] - p[2])     # distância na horizontal
                moment[joint] = moment.get(joint, 0.0) + w * arm
                cur = p
        failing = []
        for (child, par), m in moment.items():
            cap = GLUE_HOLD * min(self.physics_of(self.cr.block_map[child])["stack"],
                                  self.physics_of(self.cr.block_map[par])["stack"])
            if m > cap + 1e-9:
                failing.append((depth[par], child, par))
        if not failing:
            return []
        # rompe só a ligação mais perto do apoio de cada ramo (o resto cai junto)
        failing.sort()
        cut, dropped = [], set()
        for _d, child, par in failing:
            if par in dropped:
                continue
            cut.append((child, par))
            # tudo o que pendia por esta ligação já vai cair
            stack = [child]
            while stack:
                n = stack.pop()
                if n in dropped:
                    continue
                dropped.add(n)
                stack += [c for c, p in parent.items() if p == n]
        return cut

    def _crushed(self, group) -> List[Cell]:
        """Blocos com mais peso em cima do que o material aguenta."""
        hang = self._hang_loads(group) if len(group) > 1 else {}
        layer = self._layer_loads(group) if len(group) > 1 else {}
        out = []
        for o in group:
            if o not in self.cr.block_map or self.is_ground(o):
                continue
            phys = self.physics_of(self.cr.block_map[o])
            load = (layer.get(o, 0.0) if len(group) > 1 else self.column_load(o)) \
                + hang.get(o, 0.0)
            if load > phys["crush"] * CRUSH_FACTOR + 1e-9:
                out.append(o)
        return out

    def _crush(self, owner) -> None:
        tipo = self.cr.block_map.get(owner)
        self._notify("on_destroyed", owner, tipo)
        self._burst_at(owner, tipo, count=12, speed=4.5)
        self.awake.discard(owner)
        self._unlink_all(owner)
        self.damage.pop(owner, None)
        self.cr.remove_block(owner)

    # ---------------------------------------------------------------- movimento
    def _move_group(self, owners: List[Cell], delta, fall: bool) -> None:
        """Tira os blocos do mundo, anima, e põe-nos no destino ao chegar."""
        dx, dy, dz = delta
        moved = set(owners)
        blocks, vacated = [], []
        old_links = []
        for o in owners:
            for other in self.links.get(o, ()):
                if other in moved and o < other:
                    old_links.append((o, other))
        for o in owners:
            tipo = self.cr.block_map.get(o)
            orient = self.cr.orientation_at(o)
            vacated += self.footprint(o)
            blocks.append(((o[0] + dx, o[1] + dy, o[2] + dz), tipo, orient, o))
        proxies = [self._proxy(src, tipo, orient) for _d, tipo, orient, src in blocks]
        for dest, _t, _or, src in blocks:
            self._notify("on_moved", src, dest)
        woke = {src: dest for dest, _t, _or, src in blocks if src in self.awake}
        for src, dest in woke.items():
            self.awake.discard(src)
        for dest in woke.values():
            self.awake.add(dest)
        for o in owners:
            self._unlink_all(o)
            self.cr.remove_block(o)
        # A cola não sobrevive à física: o grupo move-se junto, mas chega ao
        # destino como blocos soltos (as ligações antigas já foram desfeitas).
        reserved = {c for dest, tipo, _o, _src in blocks
                    for c in self.cr.cells_for_block(dest, tipo)}
        self.reserved |= reserved
        size = self.cr.size
        offset = (dx * size, dy * size, dz * size)
        if fall:
            duration = math.sqrt(2 * abs(dy * size) / GRAVITY)
        else:
            duration = PUSH_SECONDS
        motion = _Motion([(d, t, o) for d, t, o, _s in blocks], offset,
                         max(duration, 0.05), fall, reserved)
        motion.proxies = proxies
        if self.on_anim is not None:
            self.on_anim("motion", {
                "blocks": [[list(src), list(dest), tipo, orient] for dest, tipo, orient, src in blocks],
                "delta": [dx, dy, dz], "duration": max(duration, 0.05), "fall": bool(fall)})
        for e in proxies:
            if e is not None:
                e._start = e.position
        if fall:
            self._launch(proxies, [t for _d, t, _o, _s in blocks], motion)
        self.motions.append(motion)
        if old_links:
            self._save_links()
        # o que estava em cima do grupo pode ter ficado sem apoio
        self.settle(vacated)

    # ---------------------------------------------------------------- visual
    def _particles(self):
        if self.fx is None:
            try:
                from engine.physics_fx import Particles
                self.fx = Particles()
            except Exception:
                self.fx = False
        return self.fx or None

    def _burst_at(self, owner, tipo, count=6, speed=4.0):
        if self.on_burst is not None:
            try:
                self.on_burst(owner, tipo, count, speed)
            except Exception:
                pass
        fx = self._particles()
        if fx is None or not tipo:
            return
        try:
            from engine.physics_fx import block_color
            p = self.cr.block_to_world(owner)
            fx.burst((p.x, p.y, p.z), block_color(tipo), count=count, speed=speed)
        except Exception:
            pass

    def _proxy(self, owner, tipo, orientation):
        """Cópia com a forma do bloco no lugar dele (ou None sem Ursina)."""
        try:
            from engine.physics_fx import make_proxy
        except Exception:
            return None
        try:
            cells = self.cr.cells_for_block(owner, tipo)
            high = 1 + max(c[1] for c in cells) - owner[1]
            p = self.cr.block_to_world(owner)
            # Modelos produced (baú, maçã, balde...): a cópia é um modelo novo,
            # montado como o do chunk (mesma textura e encaixe), e o original
            # some já. Copiar o modelo vivo falhava (cubo sem textura) quando o
            # chunk ainda não o tinha criado, p.ex. a maçã que nasce e cai logo.
            build = getattr(self.cr, "build_produced_copy", None)
            if build is not None:
                proxy = build(tipo, orientation, (p.x, p.y, p.z))
                if proxy is not None:
                    live = self.cr.produced_entities.pop(owner, None)
                    if live is not None:
                        try:
                            live.hide()
                        except Exception:
                            pass
                    return proxy
            return make_proxy(tipo, orientation, (p.x, p.y, p.z), self.cr.size, high)
        except Exception as exc:
            print(f"[física] cópia de {tipo}: {exc}")
            return None

    def _fader(self):
        if self.fader is None:
            try:
                from engine.physics_fx import Fader
                self.fader = Fader()
            except Exception:
                self.fader = False
        return self.fader or None

    def update(self, dt: float) -> None:
        if self.drops_pending and not self.remote:
            cells, self.drops_pending = self.drops_pending, []
            self.settle([c for c in cells if c in self.cr.block_map])
        fx = self._particles()
        if fx is not None:
            fx.update(dt)
        fader = self._fader()
        if fader is not None:
            try:
                fader.update(dt)
            except Exception as exc:
                print(f"[física] {exc}")
                self.fader = False
        landed_topples, landed_motions = [], []
        for topple in tuple(self.topples):
            body = topple["body"]
            if body is None:
                # sem gráficos (servidor): pousa depois do tempo da animação
                topple["timer"] = topple.get("timer", 0.0) - dt
                if topple["timer"] <= 0:
                    landed_topples.append(topple)
            elif body.update(dt):
                landed_topples.append(topple)
        self._update_remote_anims(dt)
        for motion in tuple(self.motions):
            if not motion.landed:
                motion.elapsed = min(motion.duration, motion.elapsed + dt)
                f = motion.elapsed / motion.duration
                f = f * f if motion.fall else 1 - (1 - f) * (1 - f)
                for e in motion.proxies:
                    if e is None:
                        continue
                    s = e._start
                    e.position = (s.x + motion.offset[0] * f, s.y + motion.offset[1] * f,
                                  s.z + motion.offset[2] * f)
                if motion.elapsed >= motion.duration:
                    landed_motions.append(motion)
            else:
                self._finish(motion)
        # Impactos medidos com as posições deste frame, antes de pousar (o
        # último trecho da queda é o que mais fere).
        if self.impacts is not None:
            try:
                self.impacts.update(dt)
            except Exception as exc:
                print(f"[física] impacto: {exc}")
        for topple in landed_topples:
            if topple in self.topples:
                self.topples.remove(topple)
                self._land_topple(topple)
        for motion in landed_motions:
            self._land(motion)

    HEADLESS_TOPPLE_SECONDS = 1.1

    # ---------------------------------------------------------------- animações do servidor
    def play_remote(self, kind, data) -> None:
        """Cliente online: anima uma queda/tombo que o servidor decidiu. Os
        blocos de verdade chegam à parte (mensagens de bloco)."""
        try:
            if kind == "motion":
                size = self.cr.size
                d = data.get("delta") or [0, 0, 0]
                items = []
                for src, _dest, tipo, orient in data.get("blocks", []):
                    e = self._proxy(tuple(src), tipo, orient)
                    if e is not None:
                        e._start = e.position
                        items.append((e, tipo))
                anim = {"kind": "motion", "proxies": [e for e, _t in items], "elapsed": 0.0,
                        "duration": float(data.get("duration", 0.3)),
                        "offset": (d[0] * size, d[1] * size, d[2] * size),
                        "fall": bool(data.get("fall"))}
                if anim["fall"] and items:
                    self._launch(anim["proxies"], [t for _e, t in items], anim)   # fere quem estiver por baixo
                self.remote_anims.append(anim)
            elif kind == "topple":
                proxies = []
                for entry in data.get("solid", []):
                    src, tipo = tuple(entry[0]), entry[2]
                    start = entry[4] if len(entry) > 4 else self.cr.orientation_at(src)
                    proxies.append(self._proxy(src, tipo, start))
                for src, tipo in data.get("vanish", []):
                    proxies.append(self._proxy(tuple(src), tipo, None))
                proxies = [e for e in proxies if e is not None]
                body = None
                try:
                    from engine.physics_fx import ToppleBody
                    body = ToppleBody(proxies, tuple(data["pivot"]), (0, 1, 0),
                                      tuple(data["fall_dir"]), float(data["height"]),
                                      float(data.get("lift", 0.0)))
                except Exception as exc:
                    print(f"[física] tombo remoto: {exc}")
                self.remote_anims.append({"kind": "topple", "proxies": proxies, "body": body,
                                          "elapsed": 0.0, "duration": 4.0})
        except Exception as exc:
            print(f"[física] animação do servidor: {exc}")

    def _update_remote_anims(self, dt) -> None:
        if not self.remote_anims:
            return
        done = []
        for a in self.remote_anims:
            a["elapsed"] += dt
            if a["kind"] == "motion":
                f = min(1.0, a["elapsed"] / max(a["duration"], 1e-3))
                f = f * f if a["fall"] else 1 - (1 - f) * (1 - f)
                for e in a["proxies"]:
                    s = e._start
                    e.position = (s.x + a["offset"][0] * f, s.y + a["offset"][1] * f,
                                  s.z + a["offset"][2] * f)
                if a["elapsed"] >= a["duration"] + 0.15:     # o bloco real já chegou
                    done.append(a)
            else:
                body = a.get("body")
                finished = body is None or body.update(dt)
                if finished or a["elapsed"] >= a["duration"]:
                    if body is not None:
                        try:
                            body.release()
                        except Exception:
                            pass
                    done.append(a)
        for a in done:
            self.remote_anims.remove(a)
            self._release(a["proxies"])
            try:
                from ursina import destroy
                for e in a["proxies"]:
                    destroy(e)
            except Exception:
                pass

    def _land(self, motion: _Motion) -> None:
        motion.landed = True
        self._release(motion.proxies)
        self.reserved -= motion.reserved
        for dest, tipo, orient in motion.blocks:
            if tipo is None:
                continue
            if any(c in self.cr.cells for c in self.cr.cells_for_block(dest, tipo)):
                print(f"[física] destino ocupado em {dest}; bloco {tipo} perdido")
                continue
            self.cr.set_block(dest, tipo, orientation=orient)
        motion.linger = 0.0
        if motion.fall:
            k = max(1, int(round(abs(motion.offset[1]) / self.cr.size)))
            landed = [(d, t) for d, t, _o in motion.blocks]
            on_void = {d for d, _t in landed if d in self.cr.block_map
                       and self._on_void_floor({d}) and not self._supported({d})}
            if on_void:
                self._to_void(on_void)           # chegou ao void: quebra e some
            self._after_fall([(d, t) for d, t in landed if d not in on_void], k)
        self.settle([d for d, _t, _o in motion.blocks])
        self._hand_off([(e, d, t) for e, (d, t, _o) in zip(motion.proxies, motion.blocks)])
        motion.proxies = []

    # ---------------------------------------------------------------- depuração
    def describe(self, cell) -> str:
        return _describe(self, cell)

    def show_links(self, show: bool, center, dt: float, radius: int = 12) -> None:
        """Com o Inspecionar aberto, um ponto amarelo no meio de cada ligação."""
        if not show:
            if self._debug is not None:
                self._debug.enabled = False
            return
        self._debug_timer -= dt
        if self._debug is not None and self._debug.enabled and self._debug_timer > 0:
            return
        self._debug_timer = 0.5
        size = self.cr.size
        cx, cz = round(center[0] / size), round(center[2] / size)
        key = (cx, cz, getattr(self, "_links_version", 0), len(self.glue_faces))
        if self._debug is not None and getattr(self, "_debug_key", None) == key:
            self._debug.enabled = True          # nada mudou: reaproveita os pontos
            return
        self._debug_key = key
        try:
            from ursina import Entity, Mesh
            from color_utils import rgb255
            points = []
            links = self.links
            height = getattr(self.room, "size_y", 32)
            # Só as células à volta (antes percorria as ~80 mil ligações).
            found = [(cell, links[cell])
                     for x in range(cx - radius, cx + radius + 1)
                     for z in range(cz - radius, cz + radius + 1)
                     for y in range(height)
                     for cell in ((x, y, z),) if cell in links]
            for a, peers in found:
                pa = self.cr.block_to_world(a)
                for b in peers:
                    if a < b:
                        pb = self.cr.block_to_world(b)
                        points.append(((pa.x + pb.x) / 2, (pa.y + pb.y) / 2,
                                       (pa.z + pb.z) / 2))
            # Faces com cola à espera de um bloco: ponto no meio da face.
            half = self.cr.size / 2
            for cell, d in self.glue_faces:
                if abs(cell[0] - cx) > radius or abs(cell[2] - cz) > radius:
                    continue
                pc = self.cr.block_to_world(cell)
                points.append((pc.x + d[0] * half, pc.y + d[1] * half, pc.z + d[2] * half))
            if self._debug is not None:
                from ursina import destroy
                destroy(self._debug)
                self._debug = None
            if points:
                self._debug = Entity(model=Mesh(vertices=points, mode="point", thickness=8),
                                     color=rgb255(255, 230, 0), unlit=True)
                self._debug.set_depth_test(False)
                self._debug.set_bin("fixed", 50)
        except Exception as exc:
            print(f"[física] não foi possível mostrar as ligações: {exc}")
            self._debug_timer = 1e9

    # Etiquetas de resistência por cima dos blocos (F4) -----------------------
    LABEL_RADIUS = 5           # blocos à volta do jogador (horizontal)
    LABEL_HEIGHT = 8           # blocos acima/abaixo dos pés
    LABEL_MAX = 260

    def resistance_labels(self, center, radius=None):
        """[(âncora, texto)] dos blocos visíveis perto de ``center`` (coords Ursina).

        Visível = com pelo menos uma face livre. A carga de cada estrutura é
        calculada uma vez (é a mesma para todos os blocos colados).
        """
        radius = self.LABEL_RADIUS if radius is None else radius
        size = self.cr.size
        cx, cy, cz = round(center[0] / size), math.floor(center[1] / size), round(center[2] / size)
        key = (cx, cy, cz, radius, getattr(self.cr, "world_version", 0),
               getattr(self, "_links_version", 0))
        cached = getattr(self, "_labels_cache", None)
        if cached is not None and cached[0] == key:
            return cached[1]                 # nada mudou por perto
        out = self._resistance_labels(cx, cy, cz, radius)
        self._labels_cache = (key, out)
        return out

    def _resistance_labels(self, cx, cy, cz, radius):
        group_load: Dict[Cell, float] = {}
        out = []
        seen = set()
        for x in range(cx - radius, cx + radius + 1):
            for z in range(cz - radius, cz + radius + 1):
                for y in range(max(0, cy - self.LABEL_HEIGHT), cy + self.LABEL_HEIGHT + 1):
                    owner = self.cr.owner_at((x, y, z))
                    if owner is None or owner in seen:
                        continue
                    seen.add(owner)
                    if not any(c[1] + dy >= 0
                               and not self.occupied((c[0] + dx, c[1] + dy, c[2] + dz))
                               for c in self.footprint(owner)
                               for dx, dy, dz in NEIGHBORS6):
                        continue                       # enterrado: não se vê
                    tipo = self.cr.block_map.get(owner)
                    if self._has_entity_health(tipo):
                        continue
                    if owner not in group_load:
                        group = self.structure(owner)
                        if len(group) > 1:
                            layer = self._layer_loads(group)
                            hang = self._hang_loads(group)
                            for o in group:
                                group_load[o] = layer.get(o, 0.0) + hang.get(o, 0.0)
                        else:
                            group_load[owner] = self.column_load(owner)
                    phys = self.physics_of(tipo)
                    load = (group_load[owner] if phys["structural"]
                            else self.column_load(owner))   # folha: só ela
                    if self.is_ground(owner):
                        load = 0.0                          # chão compactado
                    move, top = self.resist(phys, load)
                    move *= HIT_MOVE_FACTOR                 # o que um golpe precisa
                    # só os números: em cima mover (amarelo), em baixo quebrar (laranja)
                    out.append((owner, f"<yellow>{move:.1f}\n<orange>{top:.1f}"))
                    if len(out) >= self.LABEL_MAX:
                        return out
        return out

    def show_values(self, show: bool, center, eye, dt: float) -> None:
        """Com o Inspecionar aberto, a resistência de cada bloco por perto.

        A etiqueta fica na face virada para a câmera (``eye``) e olha para ela.
        """
        if not show:
            for t in self._labels:
                t.enabled = False
            return
        self._labels_timer -= dt
        if self._labels_timer > 0 and self._labels and any(t.enabled for t in self._labels):
            return
        self._labels_timer = 0.5
        try:
            from ursina import Text, color, scene
            items = self.resistance_labels(center)
            while len(self._labels) < len(items):
                t = Text(parent=scene, text="", billboard=True, origin=(0, 0),
                         scale=9, color=color.white)
                self._labels.append(t)
            size = self.cr.size
            for t, (owner, text) in zip(self._labels, items):
                cells = self.footprint(owner)
                p = self.cr.block_to_world(owner)
                high = (max(c[1] for c in cells) - owner[1]) * size / 2
                c = (p.x, p.y + high, p.z)
                d = (eye[0] - c[0], eye[1] - c[1], eye[2] - c[2])
                n = math.sqrt(d[0] ** 2 + d[1] ** 2 + d[2] ** 2) or 1.0
                push = size * 0.62                    # sai da face, para a câmera
                t.position = (c[0] + d[0] / n * push, c[1] + d[1] / n * push,
                              c[2] + d[2] / n * push)
                if t.text != text:
                    t.text = text
                t.enabled = True
            for t in self._labels[len(items):]:
                t.enabled = False
        except Exception as exc:
            print(f"[física] etiquetas de resistência indisponíveis: {exc}")
            self._labels_timer = 1e9

    def _finish(self, motion: _Motion) -> None:
        self._release(motion.proxies)
        try:
            from ursina import destroy
            for e in motion.proxies:
                if e is not None:
                    destroy(e)
        except Exception:
            pass
        self.motions.remove(motion)


def inspect_block(physics, cell, damage=None, direction=None) -> dict:
    """O que o Inspecionar mostra do bloco mirado (sem mexer em nada).

    Devolve textos: ``bloco``, ``mover``, ``quebrar``, ``carga`` e ``golpe``
    (o que o golpe atual do jogador faria agora: nada, empurra a fila,
    tomba/desliza a estrutura ou quebra)."""
    out = {"bloco": "-", "mover": "-", "quebrar": "-", "carga": "-", "golpe": "-",
           "guardado": "-"}
    owner = physics.cr.owner_at(cell) if cell is not None else None
    if owner is None:
        return out
    tipo = physics.cr.block_map.get(owner, "?")
    if physics._has_entity_health(tipo):
        out["bloco"] = f"{tipo} (alvo com vida: leva dano, não se move)"
        return out
    group = physics.structure(owner)
    alone = len(group) > 1 and (
        not physics.physics_of(tipo)["structural"]
        or any(physics.is_ground(o) for o in group if o in physics.cr.block_map))
    eff = dict(physics.effective(owner, {owner} if alone else group))
    eff["move"] *= HIT_MOVE_FACTOR           # o que um golpe precisa (ver hit)
    if physics.is_ground(owner):
        kind = "chão natural"
    elif len(group) <= 1:
        kind = "solto"
    elif physics.is_tree(owner):
        kind = f"árvore de {len(group)} blocos"
    else:
        kind = f"colado ({len(group)} blocos)"
    phys = physics.physics_of(tipo)
    out["bloco"] = f"{tipo} · {kind} · {phys['mass']:g} kg"
    J = J_PER_HP
    out["mover"] = (f"{eff['move']:.1f}  ({eff['move'] * J:.0f} J = arrastar 1 m; "
                    f"base {eff['base_move'] * J:.0f} J)")
    out["quebrar"] = ("inquebrável" if eff["break"] == float("inf")
                      else f"{eff['break']:.1f}  ({eff['break'] * J:.0f} J; "
                           f"tenacidade {eff['base_break'] * J:.0f} J)")
    stored, left = physics.stored_damage(owner)
    out["guardado"] = ("nenhum" if stored <= 0 else
                       f"{stored:.1f}  (inquebrável)" if eff["break"] == float("inf") else
                       f"{stored:.1f} / {eff['break']:.1f}  (some em {left:.0f} s)")
    load = eff["load"]
    out["carga"] = ("nenhum" if load <= 1e-9 else
                    f"{load:.0f} N = {load / G:.0f} kg em cima  (mais estável, mais frágil)")
    if damage is None:
        return out
    dmg = float(damage)
    if dmg + stored >= eff["break"]:
        verdict = "QUEBRA" + (" (com o dano guardado)" if stored > 0 and dmg < eff["break"] else "")
    else:
        verdict = None
        if len(group) > 1 and not alone and dmg >= eff["move"]:
            verdict = "mexe a estrutura inteira (tomba ou desliza)"
        if verdict is None:
            row, need = (physics._push_row(owner, direction, eff["move"], HIT_MOVE_FACTOR)
                         if direction is not None else (None, eff["move"]))
            if row is None:
                verdict = "nada (não há para onde empurrar)"
            elif dmg >= need:
                verdict = (f"EMPURRA {len(row)} bloco(s)" +
                           (f" (fila precisa {need:.1f})" if len(row) > 1 else ""))
            elif need >= eff["break"]:
                verdict = f"nada: fila de {len(row)} precisa {need:.1f} (nunca move)"
            else:
                verdict = f"nada (empurrar precisa {need:.1f})"
    out["golpe"] = f"{dmg:g} -> {verdict}"
    return out


def _describe(physics, cell):
    """Texto do bloco mirado para o Inspecionar."""
    owner = physics.cr.owner_at(cell) if cell is not None else None
    if owner is None:
        return "-"
    tipo = physics.cr.block_map.get(owner, "?")
    if physics._has_entity_health(tipo):
        return f"{tipo} (entidade)"
    group = physics.structure(owner)
    alone = len(group) > 1 and not physics.physics_of(tipo)["structural"]
    eff = physics.effective(owner, {owner} if alone else group)
    if len(group) <= 1:
        kind = "avulso"
    elif physics.is_tree(owner):
        kind = f"árvore ({len(group)})"
    else:
        kind = f"colado ({len(group)})"
    return (f"{tipo} · mover {eff['move']:.1f} · quebrar {eff['break']:.1f}"
            f" (carga {eff['load']:.1f}) · {kind}")


def hit_direction(player_pos, cell, size) -> Tuple[int, int, int]:
    """Direção do empurrão: do jogador para o bloco, no eixo horizontal dominante."""
    dx = cell[0] * size - player_pos[0]
    dz = cell[2] * size - player_pos[2]
    if abs(dx) >= abs(dz):
        return (1 if dx > 0 else -1, 0, 0) if abs(dx) > 1e-6 else (0, 0, 0)
    return (0, 0, 1 if dz > 0 else -1)


__all__ = ["BlockPhysics", "hit_direction", "DEFAULT_PHYSICS"]
