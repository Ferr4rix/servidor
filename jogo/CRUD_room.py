"""CRUD da sala.

- O estado inicial é **sempre** gerado proceduralmente a partir da seed
  (determinístico: mesma seed = mesma sala).
- As alterações dos jogadores ficam em `modifications` e são persistidas pelo
  **backend** (hoje `rooms.json`, no futuro o servidor). Esta classe não toca
  em ficheiros nem em rede.
- Chaves em coordenadas **inteiras de bloco** `(bx, by, bz)`:
    * `modifications["3,0,4"] = "wood"`  → coloca (formato antigo)
    * `modifications["3,0,4"] = {"type": "wood", "orientation": "cima"}`
      → coloca com orientação
    * `modifications["3,0,4"] = None`    → remove

Uso:
    from backend import LocalBackend
    from CRUD_room import Room

    room = Room(LocalBackend(), "room_a84f2c91")
    room.get_block(3, 0, 4)                # -> "terra"
    room.set_block(3, 0, 4, "wood")
    room.remove_block(3, 0, 4)
    for (bx, by, bz), tipo in room.iter_blocks():
        ...
"""

from __future__ import annotations

import math
import random
from typing import Dict, Iterator, List, Optional, Tuple

from backend import Backend
from blocks import canonical_block_id
from blocks.structure_blocks import tree_blocks

BlockKey = Tuple[int, int, int]

# Camadas do chão: pedra dura em y=0 (base indestrutível do mapa) e 3 camadas
# de terra por cima (y=1..3). As árvores começam por cima da terra.
BEDROCK_Y = 0
GROUND_LAYERS = 3
GROUND_Y = BEDROCK_Y + GROUND_LAYERS          # camada de cima da terra
TREE_BASE_Y = GROUND_Y + 1
# Versão do layout das camadas -> altura da superfície (topo da terra) nela.
# Ao abrir uma sala de versão antiga, tudo o que foi mexido sobe a diferença
# (uma vez só): v1 terra em y=0, v2 pedra+1 terra (topo y=1), v3 pedra+3 terras.
ROOM_LAYERS = 3
SURFACE_BY_LAYERS = {1: 0, 2: 1, 3: GROUND_Y}


def cell_hash(seed: int, x: int, y: int, z: int) -> float:
    """Número 0-1 fixo para (semente, x, y, z); igual ao cellHash do worldgen.js."""
    M = 0xFFFFFFFF
    h = (seed * 0x9E3779B1 + x * 0x85EBCA77) & M
    h = (h + y * 0xC2B2AE3D) & M
    h = (h + z * 0x27D4EB2F) & M
    h ^= h >> 15
    h = (h * 0x2C1B3C6D) & M
    h ^= h >> 12
    h = (h * 0x297A2D39) & M
    h ^= h >> 15
    return h / 4294967296.0

# ---------------------------------------------------------------- lagos
# Igual ao lakesOf/lakeDepth/lakeCells do worldgen.js (contas inteiras e o
# mesmo cell_hash): o jogo e o servidor escavam os mesmos lagos.
LAKE_SALT = 900


def lakes_of(seed, size_x, size_z, spec, praca_raio=0, geracao=None):
    if not isinstance(spec, dict) or geracao == "caixa":
        return []
    seed = int(seed)
    want = max(0, int(spec.get("quantidade", 0) or 0))
    r = spec.get("raio") if isinstance(spec.get("raio"), list) else [3, 7]
    rmin = max(2, int(r[0]))
    rmax = max(rmin, int(r[1]))
    pr = int(praca_raio or 0)
    out = []
    i = 0
    while i < want * 8 and len(out) < want:
        h = lambda k, i=i: cell_hash(seed + LAKE_SALT + k, i, 0, 0)    # noqa: E731
        rx = rmin + int(math.floor(h(1) * (rmax - rmin + 1)))
        rz = rmin + int(math.floor(h(2) * (rmax - rmin + 1)))
        mx, mz = 3 + rx, 3 + rz
        i += 1
        if size_x - 2 * mx <= 0 or size_z - 2 * mz <= 0:
            continue
        cx = mx + int(math.floor(h(3) * (size_x - 2 * mx)))
        cz = mz + int(math.floor(h(4) * (size_z - 2 * mz)))
        big = max(rx, rz)
        dcx, dcz = cx - size_x // 2, cz - size_z // 2
        if pr > 0 and dcx * dcx + dcz * dcz < (pr + big + 4) ** 2:
            continue
        if any((l[0] - cx) ** 2 + (l[1] - cz) ** 2 < (max(l[2], l[3]) + big + 3) ** 2 for l in out):
            continue
        out.append((cx, cz, rx, rz))
    return out


def lake_depth(lakes, x, z, grow=0) -> int:
    for (cx, cz, rx, rz) in lakes:
        rx, rz = rx + grow, rz + grow
        dx, dz = x - cx, z - cz
        num = dx * dx * rz * rz + dz * dz * rx * rx
        lim = rx * rx * rz * rz
        if num < lim:
            return 1 if grow else (2 if num * 100 < 45 * lim else 1)
    return 0


def lake_cells(lakes) -> dict:
    """{(x, z): profundidade} de todas as células dos lagos."""
    cells = {}
    for lake in lakes:
        cx, cz, rx, rz = lake
        for x in range(cx - rx, cx + rx + 1):
            for z in range(cz - rz, cz + rz + 1):
                d = lake_depth([lake], x, z)
                if d:
                    cells[(x, z)] = d
    return cells



def _maze_carve(seed, n, i, j, level):
    if i == n - 1 and j == n - 1:
        return 0
    if i == n - 1:
        return 2                                   # sul
    if j == n - 1:
        return 1                                   # leste
    return 1 if cell_hash(seed + 10 + level, i, 0, j) < 0.5 else 2


def maze_wall(seed, size_x, size_z, lab, x, z, level) -> bool:
    """Coluna (x, z) do andar ``level`` é parede do labirinto? Igual ao
    ``mazeWall`` do server/src/world/worldgen.js."""
    c = max(3, int(lab.get("celula") or 14))
    n = (size_x - 4) // c
    rx, rz = x - 2, z - 2
    if rx <= 0 or rz <= 0 or rx >= n * c or rz >= n * c:
        return False
    on_x, on_z = rx % c == 0, rz % c == 0
    if not on_x and not on_z:
        return False
    cl = int(lab.get("clareira") or 0)
    if level == 0 and cl > 0 and abs(x - size_x // 2) <= cl and abs(z - size_z // 2) <= cl:
        return False
    if on_x and on_z:
        return True                                # pilar
    extra = float(lab.get("aberto_extra") or 0)
    salt = seed + 20 + level
    if on_x:
        i, j = rx // c - 1, rz // c
        return not (_maze_carve(seed, n, i, j, level) == 1 or cell_hash(salt, i, 1, j) < extra)
    i, j = rx // c, rz // c - 1
    return not (_maze_carve(seed, n, i, j, level) == 2 or cell_hash(salt, i, 2, j) < extra)


def layer_base(cam) -> int:
    return int(cam.get("inicio", 0) or 0) if isinstance(cam, dict) else 0


def floors_of(size_y, cam) -> list:
    """y (células) dos pisos da caixa (= floorsOf do worldgen.js)."""
    cam = cam if isinstance(cam, dict) else {}
    ys = {int(v) for v in (cam.get("tambem") or [])}
    step = int(cam.get("a_cada") or 0)
    if step > 0:
        ys |= {y for y in range(layer_base(cam), int(size_y) - 1, step) if y > 0}
    return sorted(ys) if ys else [0]


STAIR_SECTORS = [
    "666777888",
    "666678888",
    "556678899",
    "555###999",
    "555###000",
    "444###000",
    "443321100",
    "333321111",
    "333222111",
]
STAIR_SIZE = 9


def _stair_sector(lx, lz):
    if lx < 0 or lz < 0 or lx >= STAIR_SIZE or lz >= STAIR_SIZE:
        return None
    ch = STAIR_SECTORS[lz][lx]
    return -1 if ch == "#" else int(ch)


def dungeon_stairs(seed, size_x, size_z, size_y, cam, lab, spec) -> list:
    """Escadas em caracol largas da dungeon (= dungeonStairs do worldgen.js:
    mesmo sorteio pelo cell_hash). Devolve [(x, z, f, H, p)] por célula do
    chão: p = altura do degrau, -1 pilar, -2 pilar com luz no topo."""
    if not (isinstance(spec, dict) and isinstance(cam, dict) and isinstance(lab, dict)):
        return []
    step = int(cam.get("a_cada") or 0)
    if step <= 0:
        return []
    c = max(3, int(lab.get("celula") or 14))
    n = (size_x - 4) // c
    S = STAIR_SIZE
    off = 1 + (c - 1 - S) // 2
    cl = int(lab.get("clareira") or 0) + 4
    cx, cz = size_x // 2, size_z // 2
    pa = spec.get("por_andar", 3)
    lo, hi = (int(pa[0]), int(pa[1])) if isinstance(pa, list) else (int(pa or 3), int(pa or 3))
    floors = floors_of(size_y, cam)
    base = layer_base(cam)
    cells = [(lx, lz, _stair_sector(lx, lz)) for lz in range(S) for lx in range(S)]
    out = []
    prev = set()
    for fi in range(len(floors) - 1):
        f, H = floors[fi], floors[fi + 1] - floors[fi]
        if H > 10 or H < 4 or S + 2 > c - 1:
            prev = set()
            continue
        L = (f + 1 - base) // step
        count = lo + int(math.floor(cell_hash(seed + 49, L, 7, 0) * (hi - lo + 1)))

        def height(sct, H=H):
            return sct * (H - 1) // 9
        used = set()
        t = 0
        while t < 400 and len(used) < count:
            i = int(math.floor(cell_hash(seed + 50, L, t, 1) * n))
            j = int(math.floor(cell_hash(seed + 51, L, t, 2) * n))
            key = (i, j)
            bx, bz = 2 + i * c, 2 + j * c
            ok = key not in used and key not in prev and not (
                fi == 0 and abs(bx + c / 2 - cx) <= cl and abs(bz + c / 2 - cz) <= cl)
            if ok:
                used.add(key)
                rot = int(math.floor(cell_hash(seed + 52, L, t, 3) * 4))
                mirror = cell_hash(seed + 53, L, t, 4) < 0.5
                for (lx, lz, sct) in cells:
                    if sct >= 0:
                        p = height(sct)
                    else:
                        ring, low = 0, True
                        for dz in (-1, 0, 1):
                            for dx in (-1, 0, 1):
                                q = _stair_sector(lx + dx, lz + dz)
                                if q is not None and q >= 0:
                                    ring += 1
                                    if height(q) > H - 6:
                                        low = False
                        p = -2 if ring > 0 and low else -1
                    wx, wz = lx, (S - 1 - lz) if mirror else lz
                    for _ in range(rot):
                        wx, wz = S - 1 - wz, wx
                    out.append((bx + off + wx, bz + off + wz, f, H, p))
            t += 1
        prev = used
    return out


class Room:
    # ---------------------------------------------------------- ciclo de vida
    def __init__(self, backend: Backend, room_id: str):
        self.backend = backend
        self.room_id = room_id

        self.seed: int = 0
        self.size_x: int = 40
        self.size_y: int = 20
        self.size_z: int = 40

        # Em memória, os valores são tipos/null; orientações ficam num índice
        # separado. No JSON, blocos orientados usam {type, orientation}.
        self.modifications: Dict[str, Optional[str]] = {}
        self.orientations: Dict[str, str] = {}
        self._base: Dict[BlockKey, str] = {}
        # Ligações de estrutura (pares de blocos vizinhos, ver engine/block_physics.py):
        # naturais = árvores geradas pela seed; guardadas = cola e estruturas
        # que se moveram (vão para rooms.json em "links").
        self.natural_links: set = set()
        self.stored_links: set = set()
        self.glue_faces: set = set()
        # Conteúdo dos baús: {(x,y,z): [[item, quantidade] ou None, ...]}.
        self.containers: dict = {}
        self.soul_banks: dict = {}        # minoculus: célula -> almas guardadas
        self.fire_state: dict = {}        # fogo: "x,y,z" -> [segundos a arder, duração] (engine/fire.py)
        self.corpses: dict = {}           # cadáveres: id -> {pos, nome, slots, expira...} (engine/corpses.py)

        self.load()

    # ---------------------------------------------------------- persistência
    def load(self):
        """Pede a sala ao backend (levanta `RoomNotFoundError` se não existir)."""
        data = self.backend.load_room(self.room_id)
        self._migrate_layers(data)

        self.seed = int(data.get("seed", 0))
        self.size_x = int(data.get("size_x", 40))
        self.size_y = int(data.get("size_y", 20))
        self.size_z = int(data.get("size_z", 40))
        # Sala pública: praça sem árvores à volta do centro (o minoculus).
        self.praca_raio = int(data.get("praca_raio", 0) or 0)
        # Lagos ("lagos": {quantidade, raio}): escavados no chão (ver lakes_of);
        # a água é posta pelo servidor uma vez (rooms.js, "lagos_agua").
        self.lagos = data.get("lagos") if isinstance(data.get("lagos"), dict) else None
        self.lagos_agua = bool(data.get("lagos_agua"))
        # Plantas já na criação (sala pública): {"capim": 0.008, ...}
        self.plantas = data.get("plantas") if isinstance(data.get("plantas"), dict) else None
        # Geração: None = chão de terra/grama com árvores; "caixa" (dungeon) =
        # só uma caixa oca de ``material`` do tamanho da sala (igual ao
        # server/src/world/worldgen.js).
        self.geracao = data.get("geracao") or None
        self.material = canonical_block_id(data.get("material") or "pedra dura")
        self.revestimento = data.get("revestimento") or None
        self.camadas = data.get("camadas") or None
        self.labirinto = data.get("labirinto") or None
        self.escadas = data.get("escadas") or None
        self.auto_cola_limpa = bool(data.get("auto_cola_limpa"))
        # Céu e luz ambiente próprios da sala (o Santuário é todo branco):
        # "ceu": "#rrggbb" (sem: preto); "ambiente": 0-1 (sem: lighting.AMBIENT)
        self.ceu = data.get("ceu") or None
        try:
            self.ambiente = float(data["ambiente"]) if data.get("ambiente") is not None else None
        except (TypeError, ValueError):
            self.ambiente = None

        raw_mods = data.get("modifications") or {}
        self.modifications = {}
        self.orientations = {}
        for k, v in raw_mods.items():
            key = self._sanitize_key(k)
            if key is None:
                print(f"[room] modificação inválida ignorada: {k!r}")
                continue
            if isinstance(v, dict):
                # Formato extensível: o tipo continua explícito e orientation
                # é opcional, para acomodar futura metadata de blocos.
                block_type = v.get("type")
                if not isinstance(block_type, str) or not block_type:
                    print(f"[room] bloco inválido ignorado em {key}: falta type")
                    continue
                self.modifications[key] = canonical_block_id(block_type)
                orientation = v.get("orientation")
                if isinstance(orientation, str) and orientation:
                    self.orientations[key] = orientation
            else:
                # Compatibilidade com os valores históricos: string coloca,
                # null remove o bloco procedural.
                self.modifications[key] = canonical_block_id(v)

        self._rebuild_base()
        self._parse_world_fields(data)

    def _parse_world_fields(self, data, keys=None):
        """Lê ligações, cola, baús e almas (todos, ou só ``keys``)."""
        if keys is None or "links" in keys:
            self.stored_links = set()
            for text in data.get("links") or []:
                link = self.parse_link(text)
                if link is not None:
                    self.stored_links.add(link)
        if keys is None or "glue_faces" in keys:
            # Faces com cola ainda sem bloco encostado: "x,y,z|dx,dy,dz".
            self.glue_faces = set()
            for text in data.get("glue_faces") or []:
                try:
                    left, right = str(text).split("|")
                    cell = tuple(int(v) for v in self._unkey(left.strip()))
                    direction = tuple(int(v) for v in self._unkey(right.strip()))
                except (ValueError, TypeError):
                    print(f"[room] face com cola inválida ignorada: {text!r}")
                    continue
                self.glue_faces.add((cell, direction))
        if keys is None or "containers" in keys:
            # Baús: "x,y,z" -> lista de slots ([item, quantidade] ou null).
            self.containers = {}
            for text, slots in (data.get("containers") or {}).items():
                try:
                    cell = tuple(int(v) for v in self._unkey(str(text)))
                    clean = []
                    for entry in slots or []:
                        if entry and int(entry[1]) > 0:
                            ref = entry[0]
                            item = [ref if isinstance(ref, int) and not isinstance(ref, bool)
                                    else str(ref), int(entry[1])]
                            if len(entry) > 2 and isinstance(entry[2], dict) and entry[2]:
                                item.append(dict(entry[2]))      # dados (ex.: baú dentro do baú)
                            clean.append(item)
                        else:
                            clean.append(None)
                except (ValueError, TypeError, IndexError):
                    print(f"[room] baú inválido ignorado: {text!r}")
                    continue
                self.containers[cell] = clean
        if keys is None or "soul_banks" in keys:
            # Almas guardadas em minoculus: "x,y,z" -> quantidade.
            self.soul_banks = {}
            for text, amount in (data.get("soul_banks") or {}).items():
                try:
                    cell = tuple(int(v) for v in self._unkey(str(text)))
                    amount = int(amount)
                except (ValueError, TypeError):
                    continue
                if amount > 0:
                    self.soul_banks[cell] = amount
        if keys is None or "fogo" in keys:
            fogo = data.get("fogo")
            self.fire_state = dict(fogo) if isinstance(fogo, dict) else {}
        if keys is None or "corpos" in keys:
            corpos = data.get("corpos")
            self.corpses = dict(corpos) if isinstance(corpos, dict) else {}

    def apply_remote_fields(self, fields):
        """Campos mudados pelo servidor/outros jogadores (online): só na memória
        (o servidor já gravou)."""
        keys = [k for k in ("links", "glue_faces", "containers", "soul_banks", "fogo", "corpos")
                if k in fields]
        self._parse_world_fields(fields, keys)
        if "auto_cola_limpa" in fields:
            self.auto_cola_limpa = bool(fields["auto_cola_limpa"])
        return keys

    # ---------------------------------------------------------- ligações
    @staticmethod
    def make_link(a, b):
        """Par ordenado de blocos ligados."""
        a, b = tuple(int(v) for v in a), tuple(int(v) for v in b)
        return (a, b) if a <= b else (b, a)

    @classmethod
    def parse_link(cls, text):
        try:
            left, right = str(text).split("|")
            return cls.make_link(cls._unkey(left.strip()), cls._unkey(right.strip()))
        except (ValueError, TypeError):
            print(f"[room] ligação inválida ignorada: {text!r}")
            return None

    def is_modified(self, cell) -> bool:
        return self._key(*cell) in self.modifications

    def effective_links(self) -> set:
        """Ligações válidas agora: árvores intactas + as guardadas."""
        natural = {link for link in self.natural_links
                   if not self.is_modified(link[0]) and not self.is_modified(link[1])}
        return natural | set(self.stored_links)

    def save_stored_links(self, stored) -> None:
        """Grava diretamente as ligações que não vêm da seed (sem verificar as naturais)."""
        stored = set(stored)
        if stored == self.stored_links:
            return
        self.stored_links = stored
        try:
            self.backend.set_room_links(
                self.room_id,
                [f"{self._key(*a)}|{self._key(*b)}" for a, b in stored])
        except NotImplementedError:
            print("[room] este backend não grava ligações de estrutura")

    def save_fire(self, state) -> None:
        """Grava (e, online, manda a todos) o estado do fogo (engine/fire.py)."""
        state = dict(state or {})
        self.fire_state = state
        try:
            self.backend.migrate_room(self.room_id, {"fogo": state})
        except NotImplementedError:
            pass

    def save_corpses(self, state) -> None:
        """Grava (e, online, manda a todos) os cadáveres (engine/corpses.py)."""
        state = dict(state or {})
        self.corpses = state
        try:
            self.backend.migrate_room(self.room_id, {"corpos": state})
        except NotImplementedError:
            pass

    def save_glue_faces(self, faces) -> None:
        """Grava as faces com cola que ainda esperam um bloco encostado."""
        faces = set(faces)
        if faces == getattr(self, "glue_faces", set()):
            return
        self.glue_faces = faces
        try:
            self.backend.migrate_room(
                self.room_id,
                {"glue_faces": sorted(f"{self._key(*c)}|{self._key(*d)}"
                                      for c, d in faces)})
        except NotImplementedError:
            print("[room] este backend não grava faces com cola")

    # ---------------------------------------------------------- baús
    def container_slots(self, cell, size: int) -> list:
        """Cópia dos slots do baú em ``cell`` ([item, qtd] ou None), com ``size`` slots."""
        slots = [list(e) if e else None for e in self.containers.get(tuple(cell), [])]
        if len(slots) < size:
            slots += [None] * (size - len(slots))
        return slots

    def set_container(self, cell, slots) -> None:
        """Grava os slots de um baú (lista vazia/só None apaga a entrada)."""
        cell = tuple(cell)
        clean = []
        for e in slots:
            if e and int(e[1]) > 0:
                ref = e[0] if isinstance(e[0], int) and not isinstance(e[0], bool) else str(e[0])
                item = [ref, int(e[1])]          # id numérico do item (ou nome antigo)
                if len(e) > 2 and isinstance(e[2], dict) and e[2]:
                    item.append(dict(e[2]))
                clean.append(item)
            else:
                clean.append(None)
        if any(clean):
            if self.containers.get(cell) == clean:
                return
            self.containers[cell] = clean
        elif self.containers.pop(cell, None) is None:
            return
        self._save_containers()

    def container_is_empty(self, cell) -> bool:
        return not any(self.containers.get(tuple(cell)) or [])

    def move_container(self, src, dest) -> None:
        """O bloco do baú mudou de célula (empurrado, caiu, tombou)."""
        slots = self.containers.pop(tuple(src), None)
        if slots is None:
            return
        self.containers[tuple(dest)] = slots
        self._save_containers()

    def pop_container(self, cell) -> list:
        """Tira e devolve o conteúdo de um baú que saiu do mundo."""
        slots = self.containers.pop(tuple(cell), None)
        if slots is not None:
            self._save_containers()
        return [e for e in (slots or []) if e]

    # ---------------------------------------------------------- almas (minoculus)
    def soul_bank(self, cell) -> int:
        return int(self.soul_banks.get(tuple(cell), 0))

    def set_soul_bank(self, cell, amount) -> None:
        cell, amount = tuple(cell), max(0, int(amount))
        if amount:
            if self.soul_banks.get(cell) == amount:
                return
            self.soul_banks[cell] = amount
        elif self.soul_banks.pop(cell, None) is None:
            return
        self._save_soul_banks()

    def move_soul_bank(self, src, dest) -> None:
        amount = self.soul_banks.pop(tuple(src), None)
        if amount is None:
            return
        self.soul_banks[tuple(dest)] = amount
        self._save_soul_banks()

    def pop_soul_bank(self, cell) -> int:
        amount = self.soul_banks.pop(tuple(cell), None)
        if amount is not None:
            self._save_soul_banks()
        return int(amount or 0)

    def _save_soul_banks(self) -> None:
        try:
            self.backend.migrate_room(
                self.room_id, {"soul_banks": {self._key(*c): n
                                              for c, n in sorted(self.soul_banks.items())}})
        except NotImplementedError:
            print("[room] este backend não grava almas")

    def _save_containers(self) -> None:
        try:
            self.backend.migrate_room(
                self.room_id,
                {"containers": {self._key(*c): slots
                                for c, slots in sorted(self.containers.items())}})
        except NotImplementedError:
            print("[room] este backend não grava baús")

    def save_links(self, links) -> None:
        """Grava as ligações que a seed não recria sozinha."""
        stored = set()
        for link in links:
            natural = (link in self.natural_links and not self.is_modified(link[0])
                       and not self.is_modified(link[1]))
            if not natural:
                stored.add(link)
        if stored == self.stored_links:
            return
        self.stored_links = stored
        try:
            self.backend.set_room_links(
                self.room_id,
                [f"{self._key(*a)}|{self._key(*b)}" for a, b in stored])
        except NotImplementedError:
            print("[room] este backend não grava ligações de estrutura")

    # ---------------------------------------------------------- geração base
    def _rebuild_base(self):
        self._base.clear()
        self.natural_links = set()
        if getattr(self, "geracao", None) == "caixa":
            self._generate_box()
            return
        if getattr(self, "geracao", None) == "branca":
            self._generate_white()
            return
        self._generate_ground()
        self._generate_trees()
        self._generate_plants()

    def _generate_box(self):
        """Dungeon: caixa oca (chão, teto e 4 paredes de 1 bloco) e, dentro,
        o revestimento das paredes e os pisos a cada ``camadas.a_cada``
        (com fragmentos sorteados por ``cell_hash``, igual ao worldgen.js)."""
        sx, sy, sz = self.size_x, self.size_y, self.size_z
        mat = self.material
        base = self._base
        for x in range(sx):
            for z in range(sz):
                base[(x, 0, z)] = mat
                base[(x, sy - 1, z)] = mat
        for y in range(1, sy - 1):
            for x in range(sx):
                base[(x, y, 0)] = mat
                base[(x, y, sz - 1)] = mat
            for z in range(1, sz - 1):
                base[(0, y, z)] = mat
                base[(sx - 1, y, z)] = mat
        seed = int(self.seed)

        def filler(spec, salt):
            bloco = canonical_block_id(spec.get("bloco") or "pedra")
            frag = spec.get("fragmento")
            frag = canonical_block_id(frag) if frag else None
            chance = float(spec.get("chance") or 0)

            def put(cell):
                if frag and cell_hash(seed + salt, *cell) < chance:
                    base[cell] = frag
                else:
                    base[cell] = bloco
            return put

        rev = self.revestimento
        if isinstance(rev, dict):
            put = filler(rev, 1)
            for y in range(1, sy - 1):
                for x in range(1, sx - 1):
                    put((x, y, 1))
                    put((x, y, sz - 2))
                for z in range(2, sz - 2):
                    put((1, y, z))
                    put((sx - 2, y, z))
        cam = self.camadas if isinstance(self.camadas, dict) else {}
        step = int(cam.get("a_cada") or 0)
        inner = 2 if isinstance(rev, dict) else 1
        layer_ys = {y for y in floors_of(sy, cam) if 0 < y < sy - 1} if (step > 0 or cam.get("tambem")) else set()
        if layer_ys:
            put = filler(cam, 2)
            for y in sorted(layer_ys):
                for x in range(inner, sx - inner):
                    for z in range(inner, sz - inner):
                        put((x, y, z))
        lab = self.labirinto
        if isinstance(lab, dict) and step > 0:
            put = filler(lab, 3)
            # colunas de parede (só nas linhas da grelha) de cada andar
            lines = [(x, z) for x in range(inner, sx - inner) for z in range(inner, sz - inner)
                     if (x - 2) % max(3, int(lab.get("celula") or 14)) == 0
                     or (z - 2) % max(3, int(lab.get("celula") or 14)) == 0]
            for y in range(2, sy - 1):
                if y in layer_ys:
                    continue
                level = (y - layer_base(cam)) // step
                for (x, z) in lines:
                    if maze_wall(seed, sx, sz, lab, x, z, level):
                        put((x, y, z))
        # escadas (por cima de tudo, como o stairAt do worldgen.js)
        spec = self.escadas
        if isinstance(spec, dict):
            bloco = canonical_block_id(spec.get("bloco") or "pedra")
            luz = spec.get("luz")
            luz = canonical_block_id(luz) if luz else None
            for (x, z, f, H, p) in dungeon_stairs(seed, sx, sz, sy, cam, lab, spec):
                for k in range(1, H):
                    cell = (x, f + k, z)
                    if p == -2:
                        base[cell] = luz if (k == H - 1 and luz) else bloco
                    elif p == -1 or k <= p:
                        base[cell] = bloco
                    else:
                        base.pop(cell, None)
                if p >= H - 4:
                    base.pop((x, f + H, z), None)       # buraco no piso de cima

    def _in_bounds(self, x: int, y: int, z: int) -> bool:
        return 0 <= x < self.size_x and 0 <= y < self.size_y and 0 <= z < self.size_z

    def _generate_white(self):
        """Santuário do anjo: chão liso de ``material`` (pedra branca) de y=0 a
        GROUND_Y, sem árvores nem lagos (= worldgen.js, geração "branca")."""
        self.lakes, self.lake_cells = [], {}
        mat = self.material
        for x in range(self.size_x):
            for z in range(self.size_z):
                for y in range(BEDROCK_Y, GROUND_Y + 1):
                    self._base[(x, y, z)] = mat

    def _generate_ground(self):
        """Pedra dura por baixo de tudo, terra no meio e grama em cima; nos
        lagos a terra/grama de cima fica escavada (é onde vai a água)."""
        self.lakes = lakes_of(self.seed, self.size_x, self.size_z, getattr(self, "lagos", None),
                              getattr(self, "praca_raio", 0), getattr(self, "geracao", None))
        self.lake_cells = lake_cells(self.lakes) if self.lakes else {}
        for x in range(self.size_x):
            for z in range(self.size_z):
                self._base[(x, BEDROCK_Y, z)] = "pedra dura"
                top = GROUND_Y - self.lake_cells.get((x, z), 0)
                for y in range(BEDROCK_Y + 1, min(GROUND_Y, top + 1)):
                    self._base[(x, y, z)] = "terra"
                if top == GROUND_Y:
                    self._base[(x, GROUND_Y, z)] = "grama"

    def lake_water(self):
        """[((x, y, z), 8)] da água dos lagos cheios."""
        out = []
        for (x, z), d in self.lake_cells.items():
            for y in range(GROUND_Y - d + 1, GROUND_Y + 1):
                out.append(((x, y, z), 8))
        return out

    def is_natural_ground(self, cell) -> bool:
        """Chão gerado (pedra dura ou terra da camada de base) nunca mexido."""
        if getattr(self, "geracao", None) == "branca":
            return cell[1] <= GROUND_Y and cell in self._base and not self.is_modified(cell)
        if getattr(self, "geracao", None) == "caixa":
            # dungeon: tudo o que foi gerado (paredes, revestimento, pisos a
            # cada 50) e ninguém mexeu fica fixo, como o chão: não cai
            return cell in self._base and not self.is_modified(cell)
        return (cell[1] <= GROUND_Y and cell in self._base
                and self._base[cell] in ("terra", "grama", "pedra dura")
                and not self.is_modified(cell))

    def _migrate_layers(self, data) -> None:
        """Salas do layout antigo (terra em y=0): sobe 1 bloco o que foi mexido."""
        try:
            layers = int(data.get("layers", 1))
        except (TypeError, ValueError):
            layers = 1
        if layers >= ROOM_LAYERS:
            return
        shift = SURFACE_BY_LAYERS[ROOM_LAYERS] - SURFACE_BY_LAYERS.get(layers, 0)
        size_y = int(data.get("size_y", 20))
        moved = {}
        for key, value in (data.get("modifications") or {}).items():
            clean = self._sanitize_key(key)
            if clean is None:
                continue
            x, y, z = self._unkey(clean)
            if y + shift < size_y:
                moved[self._key(x, y + shift, z)] = value
        links = []
        for text in data.get("links") or []:
            try:
                left, right = str(text).split("|")
                a, b = self._unkey(left.strip()), self._unkey(right.strip())
                links.append(f"{self._key(a[0], a[1] + shift, a[2])}|"
                             f"{self._key(b[0], b[1] + shift, b[2])}")
            except (ValueError, TypeError):
                continue
        data["modifications"] = moved
        data["links"] = links
        data["layers"] = ROOM_LAYERS
        print(f"[room] {self.room_id}: camadas do chão atualizadas (v{layers} -> "
              f"v{ROOM_LAYERS}); {len(moved)} alterações subiram {shift} bloco(s)")
        try:
            self.backend.migrate_room(self.room_id, {
                "modifications": moved, "links": links, "layers": ROOM_LAYERS})
        except NotImplementedError:
            print("[room] este backend não grava a migração das camadas")

    PLANT_SALT = 700

    def _generate_plants(self):
        """Plantas já na criação (``"plantas"``: {tipo: chance por bloco de
        grama}); igual ao plantAt do worldgen.js."""
        spec = getattr(self, "plantas", None)
        if not isinstance(spec, dict) or not spec:
            return
        sx, sz = self.size_x, self.size_z
        pr = int(getattr(self, "praca_raio", 0) or 0)
        cx, cz = sx // 2, sz // 2
        y = GROUND_Y + 1
        items = [(str(t), float(p or 0)) for t, p in spec.items()]
        lakes = getattr(self, "lake_cells", {}) or {}
        for x in range(1, sx - 1):
            for z in range(1, sz - 1):
                if pr > 0 and (x - cx) ** 2 + (z - cz) ** 2 <= (pr + 2) ** 2:
                    continue
                if (x, z) in lakes or (x, y, z) in self._base:
                    continue
                h = cell_hash(int(self.seed or 0) + self.PLANT_SALT, x, y, z)
                acc = 0.0
                for tipo, p in items:
                    acc += p
                    if h < acc:
                        self._base[(x, y, z)] = tipo
                        break

    def _generate_trees(self):
        """Sorteia posições e delega o desenho da árvore a `tree_blocks`."""
        rng = random.Random(self.seed)
        tentativas = max(1, (self.size_x * self.size_z) // 60)
        colocadas: List[Tuple[int, int]] = []
        min_dist2 = 5 * 5

        for _ in range(tentativas):
            bx = rng.randrange(2, self.size_x - 2)
            bz = rng.randrange(2, self.size_z - 2)
            if any((bx - ax) ** 2 + (bz - az) ** 2 < min_dist2
                   for ax, az in colocadas):
                continue
            pr = getattr(self, "praca_raio", 0)
            if pr > 0 and (bx - self.size_x // 2) ** 2 + (bz - self.size_z // 2) ** 2 < pr * pr:
                continue
            lakes = getattr(self, "lakes", None)
            if lakes and lake_depth(lakes, bx, bz, 3):
                continue                      # nem dentro nem à beira de um lago
            self._plant_tree(bx, bz)
            colocadas.append((bx, bz))

    def _plant_tree(self, bx: int, bz: int):
        """Aplica no `_base` os blocos devolvidos por `tree_blocks`.

        O `tree.py` só descreve a árvore; aqui filtramos o que estiver
        dentro dos limites da sala e usamos `setdefault` para não
        sobrescrever o chão já gerado.
        """
        planted = set()
        for (x, y, z), tipo in tree_blocks(bx, bz, base_y=TREE_BASE_Y).items():
            if self._in_bounds(x, y, z) and (x, y, z) not in self._base:
                self._base[(x, y, z)] = tipo
                planted.add((x, y, z))
        # A árvore é uma estrutura natural: cada bloco liga-se aos vizinhos
        # (faces) da mesma árvore. Não usa cola.
        for (x, y, z) in planted:
            for dx, dy, dz in ((1, 0, 0), (0, 1, 0), (0, 0, 1)):
                other = (x + dx, y + dy, z + dz)
                if other in planted:
                    self.natural_links.add(self.make_link((x, y, z), other))

    # ---------------------------------------------------------- helpers
    @staticmethod
    def _key(bx: int, by: int, bz: int) -> str:
        return f"{int(bx)},{int(by)},{int(bz)}"

    @staticmethod
    def _unkey(s: str) -> BlockKey:
        x, y, z = (int(p) for p in s.split(","))
        return x, y, z

    @staticmethod
    def _sanitize_key(s) -> Optional[str]:
        """Normaliza '  3, 1,4 ' -> '3,1,4'. None se inválida."""
        if not isinstance(s, str):
            return None
        parts = [p.strip() for p in s.split(",")]
        if len(parts) != 3:
            return None
        try:
            x, y, z = (int(p) for p in parts)
        except ValueError:
            return None
        return f"{x},{y},{z}"

    # ---------------------------------------------------------- CRUD
    def get_block(self, bx: int, by: int, bz: int) -> Optional[str]:
        k = self._key(bx, by, bz)
        if k in self.modifications:
            return self.modifications[k]
        return self._base.get((int(bx), int(by), int(bz)))

    def set_block(self, bx: int, by: int, bz: int, tipo: Optional[str],
                  orientation: Optional[str] = None):
        key = self._key(bx, by, bz)
        self.modifications[key] = tipo
        if tipo is not None and isinstance(orientation, str) and orientation:
            self.orientations[key] = orientation
            stored_value = {"type": tipo, "orientation": orientation}
        else:
            self.orientations.pop(key, None)
            stored_value = tipo
        self.backend.set_room_block(self.room_id, key, stored_value)

    def remove_block(self, bx: int, by: int, bz: int):
        self.set_block(bx, by, bz, None)

    def reset_modifications(self):
        self.modifications.clear()
        self.orientations.clear()
        self.stored_links = set()
        self.backend.reset_room_modifications(self.room_id)

    # ---------------------------------------------------------- leitura em massa
    def iter_blocks(self) -> Iterator[Tuple[BlockKey, str]]:
        effective: Dict[BlockKey, Optional[str]] = dict(self._base)
        for k, v in self.modifications.items():
            effective[self._unkey(k)] = v

        for pos, tipo in effective.items():
            if tipo is not None:
                yield pos, tipo

    def iter_orientations(self) -> Iterator[Tuple[BlockKey, str]]:
        """Devolve as orientações persistidas por âncora de bloco."""
        for key, orientation in self.orientations.items():
            yield self._unkey(key), orientation

    def get_orientation(self, bx: int, by: int, bz: int) -> Optional[str]:
        return self.orientations.get(self._key(bx, by, bz))

    def dump_base(self) -> Dict[BlockKey, str]:
        return dict(self._base)
