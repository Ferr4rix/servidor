"""Back-end local: ``rooms.json`` e ``players.json`` na raiz do projeto.

Serve de simulação do servidor. As gravações são atómicas (ficheiro
temporário + ``os.replace``), por isso uma falha a meio não corrompe o JSON.
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from .base import (
    AuthError, Backend, BackendError, PlayerSession, RoomNotFoundError,
)
from .passwords import hash_password, is_hashed, verify_password

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ROOMS_JSON = PROJECT_ROOT / "rooms.json"
PLAYERS_JSON = PROJECT_ROOT / "players.json"

# Campos que só o back-end controla: nunca vão para o cliente nem são
# sobrescritos por ``save_player``.
_PRIVATE_FIELDS = ("usuario", "senha_hash")


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as fp:
            data = json.load(fp)
    except FileNotFoundError as exc:
        raise BackendError(f"Ficheiro não encontrado: {path.name}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise BackendError(f"Não foi possível ler {path.name}: {exc}") from exc
    if not isinstance(data, dict):
        raise BackendError(f"A raiz de {path.name} deve ser um objeto JSON")
    return data


def _write_json_atomic(path: Path, data: Dict[str, Any], indent: int) -> None:
    _write_text_atomic(path, json.dumps(data, indent=indent, ensure_ascii=False))


def _write_text_atomic(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            fp.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise



# Mundos dos jogadores: 40x40 blocos. Sala pública: 200x200 com um minoculus.
PLAYER_WORLD_SIZE = 40
PUBLIC_ROOM = {"id": "room_publica", "nome": "Praça pública", "seed": 2026,
               "size_x": 200, "size_y": 20, "size_z": 200, "praca_raio": 6,
               "lagos": {"quantidade": 7, "raio": [4, 9]}}

class LocalBackend(Backend):
    def __init__(self, rooms_path: Optional[Path] = None,
                 players_path: Optional[Path] = None):
        self.rooms_path = Path(rooms_path or ROOMS_JSON)
        self.players_path = Path(players_path or PLAYERS_JSON)
        self._rooms: Optional[Dict[str, Any]] = None   # cache (pode ser grande)
        # Gravação de rooms.json em segundo plano, agrupada: cada alteração
        # só marca a sala como "suja"; o worker espera ROOMS_WRITE_DELAY
        # segundos (juntando tudo o que mudou nesse tempo) e grava uma vez.
        self._rooms_lock = threading.RLock()      # protege self._rooms
        self._rooms_file_lock = threading.Lock()  # uma escrita de cada vez
        self._rooms_dirty = False
        self._rooms_wake = threading.Event()
        self._rooms_write_thread: Optional[threading.Thread] = None
        self._rooms_write_error: Optional[Exception] = None
        # Servidor de mobs de cada sala (a simulação corre aqui, não no cliente).
        self._mob_servers: Dict[str, Any] = {}
        self._room_worlds: Dict[str, Any] = {}

    # ------------------------------------------------------------ jogadores
    def login(self, usuario: str, senha: str) -> PlayerSession:
        usuario = (usuario or "").strip()
        if not usuario or not senha:
            raise AuthError("Preencha usuário e senha.")

        players = _read_json(self.players_path)
        for player_id, record in players.items():
            if not isinstance(record, dict) or record.get("usuario") != usuario:
                continue
            stored = record.get("senha_hash", "")
            if not verify_password(senha, stored):
                break
            if not is_hashed(stored):
                # Senha ainda em texto puro: passa a guardar só o hash.
                record["senha_hash"] = hash_password(senha)
                _write_json_atomic(self.players_path, players, indent=4)
            public = {k: v for k, v in record.items() if k not in _PRIVATE_FIELDS}
            return PlayerSession(
                player_id=str(player_id),
                data={"usuario": usuario, **copy.deepcopy(public)},
            )
        raise AuthError("Usuário ou senha inválidos.")

    def register(self, usuario: str, senha: str, nome: str = "") -> PlayerSession:
        """Cria um jogador em players.json (com um primeiro mundo) e entra."""
        import secrets
        usuario = (usuario or "").strip()
        if not usuario or len(senha or "") < 4:
            raise AuthError("Escolha um usuário e uma senha com 4+ caracteres.")
        players = _read_json(self.players_path)
        if any(isinstance(r, dict) and r.get("usuario") == usuario for r in players.values()):
            raise AuthError("Esse usuário já existe.")
        player_id = "#" + secrets.token_hex(4)
        while player_id in players:
            player_id = "#" + secrets.token_hex(4)
        players[player_id] = {"usuario": usuario, "senha_hash": hash_password(senha),
                              "nome": (nome or usuario).strip()[:24],
                              "link_room_properties": []}
        _write_json_atomic(self.players_path, players, indent=4)
        session = PlayerSession(player_id=player_id,
                                data={"usuario": usuario, "nome": players[player_id]["nome"],
                                      "link_room_properties": []})
        self.create_world(session, "Mundo 1")
        return session

    def save_player(self, session: PlayerSession) -> None:
        players = _read_json(self.players_path)
        stored = players.get(session.player_id)
        if not isinstance(stored, dict):
            raise BackendError(f"Jogador {session.player_id!r} não existe.")
        merged = {k: stored[k] for k in _PRIVATE_FIELDS if k in stored}
        merged.update({k: v for k, v in session.data.items()
                       if k not in _PRIVATE_FIELDS})
        players[session.player_id] = merged
        _write_json_atomic(self.players_path, players, indent=4)

    # ---------------------------------------------------------------- salas
    def _all_rooms(self) -> Dict[str, Any]:
        if self._rooms is None:
            self._rooms = _read_json(self.rooms_path)
        return self._rooms

    # Segundos entre uma alteração e a gravação (as que chegam nesse
    # intervalo vão juntas). Ao sair, flush_room_writes grava na hora.
    ROOMS_WRITE_DELAY = 2.0

    def _write_rooms_worker(self) -> None:
        """Grava rooms.json fora do frame do jogo, no máximo 1x por intervalo.

        Antes, cada bloco alterado fazia uma cópia profunda de todas as salas
        no frame do jogo (uma árvore a tombar = dezenas de cópias) e o JSON
        era escrito com indentação, que obriga o Python a usar o codificador
        lento (e prende o jogo enquanto escreve). Agora: um texto compacto
        (codificador em C) por intervalo; o arquivo indentado só ao sair.
        """
        while True:
            self._rooms_wake.wait()
            self._rooms_wake.clear()
            time.sleep(self.ROOMS_WRITE_DELAY)
            self._rooms_wake.clear()
            try:
                self._write_rooms_now(pretty=False)
            except Exception as exc:
                self._rooms_write_error = exc
            else:
                self._rooms_write_error = None

    def _write_rooms_now(self, pretty: bool) -> None:
        with self._rooms_file_lock:
            with self._rooms_lock:
                if not self._rooms_dirty and not pretty:
                    return
                if pretty:
                    text = json.dumps(self._all_rooms(), indent=2,
                                      ensure_ascii=False)
                else:
                    text = json.dumps(self._all_rooms(), ensure_ascii=False,
                                      separators=(",", ":"))
                self._rooms_dirty = False
            _write_text_atomic(self.rooms_path, text)

    def _schedule_rooms_write(self) -> None:
        with self._rooms_lock:
            self._rooms_dirty = True
        if self._rooms_write_thread is None:
            self._rooms_write_thread = threading.Thread(
                target=self._write_rooms_worker,
                name="rooms-json-writer",
                daemon=True,
            )
            self._rooms_write_thread.start()
        self._rooms_wake.set()

    def flush_room_writes(self) -> None:
        """Grava já o estado final (indentado, legível) e comunica falhas."""
        if self._rooms_write_thread is None:
            return
        try:
            self._write_rooms_now(pretty=True)
        except Exception as exc:
            raise BackendError(f"Não foi possível salvar rooms.json: {exc}") from exc
        if self._rooms_write_error is not None:
            self._rooms_write_error = None

    def _room(self, room_id: str) -> Dict[str, Any]:
        room = self._all_rooms().get(room_id)
        if not isinstance(room, dict):
            raise RoomNotFoundError(f"Sala {room_id!r} não existe em {self.rooms_path.name}.")
        return room

    def load_room(self, room_id: str) -> Dict[str, Any]:
        with self._rooms_lock:
            return copy.deepcopy(self._room(room_id))

    def set_room_block(self, room_id: str, key: str, tipo: Any) -> None:
        with self._rooms_lock:
            room = self._room(room_id)
            room.setdefault("modifications", {})[key] = tipo
        self._schedule_rooms_write()

    # ------------------------------------------------------------ água
    def load_water(self, room_id: str):
        """Água gravada da sala: ({"x,y,z": nível}, {"x,y,z": [a, b]})."""
        with self._rooms_lock:
            try:
                room = self._room(room_id)
            except RoomNotFoundError:
                return {}, {}
            return dict(room.get("agua") or {}), dict(room.get("agua_parafusos") or {})

    def save_water(self, room_id: str, cells, screws) -> None:
        """Grava a água da sala (e a das bolsas dos parafusos) em rooms.json."""
        with self._rooms_lock:
            try:
                room = self._room(room_id)
            except RoomNotFoundError:
                return
            room["agua"] = {str(k): int(v) for k, v in cells.items() if int(v) > 0}
            room["agua_parafusos"] = {str(k): list(v) for k, v in screws.items() if any(v)}
        self._schedule_rooms_write()

    # ------------------------------------------------------------ baús portáteis
    @property
    def _containers_path(self):
        return self.rooms_path.parent / "containers.json"

    def load_item_container(self, cid):
        data = _read_json(self._containers_path) if self._containers_path.exists() else {}
        return copy.deepcopy(data.get(str(cid)) or [])

    def save_item_container(self, cid, slots, size=24):
        data = _read_json(self._containers_path) if self._containers_path.exists() else {}
        data[str(cid)] = slots
        _write_json_atomic(self._containers_path, data, indent=2)

    def delete_item_container(self, cid):
        if not self._containers_path.exists():
            return
        data = _read_json(self._containers_path)
        if data.pop(str(cid), None) is not None:
            _write_json_atomic(self._containers_path, data, indent=2)

    # ------------------------------------------------------------ mundos
    def _ensure_public_room(self, rooms) -> None:
        """Sala pública 200x200 com um minoculus no meio (como no servidor)."""
        if PUBLIC_ROOM["id"] in rooms:
            return
        from CRUD_room import GROUND_Y, ROOM_LAYERS
        p = PUBLIC_ROOM
        cx, cz = p["size_x"] // 2, p["size_z"] // 2
        rooms[p["id"]] = {
            "nome": p["nome"], "seed": p["seed"], "size_x": p["size_x"], "size_y": p["size_y"],
            "size_z": p["size_z"], "layers": ROOM_LAYERS, "praca_raio": p["praca_raio"],
            "lagos": p.get("lagos"), "publica": True, "owner": None,
            "modifications": {f"{cx},{GROUND_Y + 1},{cz}": "minoculus-grump"},
        }
        self._schedule_rooms_write()

    def rename_world(self, session, room_id, nome):
        nome = " ".join(str(nome or "").split())[:32]
        if not nome:
            raise BackendError("nome vazio")
        with self._rooms_lock:
            room = self._all_rooms().get(room_id)
            if not isinstance(room, dict):
                raise BackendError("esse mundo não existe")
            if room.get("publica") or room.get("owner") != session.player_id:
                raise BackendError("só o dono pode mudar o nome do mundo")
            room["nome"] = nome
        self._schedule_rooms_write()
        return nome

    def list_worlds(self, session):
        """A sala pública, os mundos do jogador e depois os dos outros."""
        mine = list(session.room_ids)
        players = _read_json(self.players_path)
        out = []
        with self._rooms_lock:
            rooms = self._all_rooms()
            self._ensure_public_room(rooms)
            for room_id, room in rooms.items():
                if not isinstance(room, dict):
                    continue
                if room.get("publica"):
                    out.append({"id": room_id, "nome": str(room.get("nome") or "Sala pública"),
                                "dono": "todos", "meu": False, "publica": True,
                                "acesso": room_id in mine, "online": 0})
                    continue
                owner_id = room.get("owner")
                if owner_id not in players:
                    owner_id = next((pid for pid, p in players.items()
                                     if isinstance(p, dict)
                                     and room_id in (p.get("link_room_properties") or [])),
                                    None)
                owner = players.get(owner_id) or {}
                dono = owner.get("nome") or owner.get("usuario") or "?"
                out.append({"id": room_id,
                            "nome": str(room.get("nome") or f"Mundo de {dono}"),
                            "dono": dono,
                            "meu": owner_id == session.player_id,
                            "acesso": room_id in mine, "online": 0})
        out.sort(key=lambda w: (not w.get("publica"), not w["meu"], w["nome"]))
        return out

    def join_world(self, session, room_id):
        with self._rooms_lock:
            if room_id not in self._all_rooms():
                raise BackendError("esse mundo não existe")
        links = session.data.setdefault("link_room_properties", [])
        if room_id not in links:
            links.append(room_id)
            self.save_player(session)

    def create_world(self, session, nome):
        import random
        import secrets
        with self._rooms_lock:
            rooms = self._all_rooms()
            template = next((r for r in rooms.values() if isinstance(r, dict)), {})
            room_id = f"room_{secrets.token_hex(4)}"
            while room_id in rooms:
                room_id = f"room_{secrets.token_hex(4)}"
            from CRUD_room import ROOM_LAYERS
            rooms[room_id] = {
                "nome": str(nome or "Novo mundo")[:32],
                "seed": random.randint(1, 99999),
                "size_x": PLAYER_WORLD_SIZE,
                "size_y": int(template.get("size_y", 20)),
                "size_z": PLAYER_WORLD_SIZE,
                "layers": ROOM_LAYERS,
                "modifications": {},
                "owner": session.player_id,
            }
        links = session.data.setdefault("link_room_properties", [])
        if room_id not in links:
            links.append(room_id)
        self.save_player(session)
        self._schedule_rooms_write()
        return room_id

    def release_room(self, room_id):
        server = self._mob_servers.pop(room_id, None)
        if server is not None:
            server.save()
        self._room_worlds.pop(room_id, None)

    # ------------------------------------------------------------ mobs
    BLOCK_SIZE = 2.0

    def attach_room_world(self, room_id: str, room) -> None:
        self._room_worlds[room_id] = room

    def _mob_server(self, room_id: str):
        server = self._mob_servers.get(room_id)
        if server is None:
            from .mobs import MobServer
            world = self._room_worlds.get(room_id)
            if world is None:
                from CRUD_room import Room        # servidor com a sua cópia da sala
                world = Room(self, room_id)
            with self._rooms_lock:
                stored = copy.deepcopy(self._room(room_id).get("mobs") or [])
            server = MobServer(room_id, world, self.BLOCK_SIZE, self.migrate_room, stored)
            self._mob_servers[room_id] = server
        return server

    def mob_sync(self, room_id, player_id, state):
        return self._mob_server(room_id).sync(str(player_id), state)

    def mob_attack(self, room_id, player_id, mob_id, damage, origin, kind="golpe"):
        return self._mob_server(room_id).attack(str(player_id), mob_id, damage, origin, kind)

    def mob_spawn(self, room_id, tipo, position=None):
        return self._mob_server(room_id).spawn(tipo, position)

    def save_mobs(self) -> None:
        for server in self._mob_servers.values():
            server.save()

    def migrate_room(self, room_id: str, fields) -> None:
        with self._rooms_lock:
            room = self._room(room_id)
            for key, value in fields.items():
                if key == "links" and not value:
                    room.pop("links", None)
                else:
                    room[key] = copy.deepcopy(value)
        self._schedule_rooms_write()

    def set_room_links(self, room_id: str, links) -> None:
        with self._rooms_lock:
            room = self._room(room_id)
            if links:
                room["links"] = sorted(links)
            else:
                room.pop("links", None)
        self._schedule_rooms_write()

    def reset_room_modifications(self, room_id: str) -> None:
        with self._rooms_lock:
            room = self._room(room_id)
            room["modifications"] = {}
            room.pop("links", None)
            room.pop("containers", None)
            room.pop("soul_banks", None)
        self._schedule_rooms_write()
