"""Back-end online: servidor Node (Express + ENet) com MongoDB Atlas.

Duas ligações ao mesmo servidor:

* **HTTP (Express)** — login, criar conta, lista/criação de mundos, ler a sala
  (semente + alterações) e gravar o jogador e campos da sala (baús, cola,
  ligações). As escritas vão por uma fila em segundo plano, para nunca
  travar o jogo à espera da rede.
* **ENet (UDP)** — tempo real: o estado do jogador 20x por segundo, blocos
  colocados/removidos, golpes em mobs; recebe os outros jogadores, os mobs
  (simulados no servidor), os blocos mudados pelos outros e os eventos.

O endereço vem de ``servidor.json`` (ver ``backend/__init__.py``).
"""

from __future__ import annotations

import json
import queue
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from .base import AuthError, Backend, BackendError, PlayerSession, RoomNotFoundError

HTTP_TIMEOUT = 15.0
CONNECT_TIMEOUT_MS = 5000
# Sinal de vida: o servidor considera a conta online enquanto o jogo fala com
# ele (uma conta só pode estar aberta num lugar). Ver server/src/presence.js.
HEARTBEAT_SECONDS = 10.0
LOGOUT_TIMEOUT = 3.0


class RemoteBackend(Backend):
    def __init__(self, http_url: str = "http://localhost:3000",
                 enet_host: str = "localhost", enet_port: int = 7777):
        self.http_url = http_url.rstrip("/")
        self.enet_host = enet_host
        self.enet_port = int(enet_port)
        self.token: Optional[str] = None
        self.session: Optional[PlayerSession] = None
        # ENet
        self._enet = None
        self._host = None
        self._peer = None
        self._room: Optional[str] = None
        self._connected = False
        self._welcomed = False
        self._mobs: List[dict] = []
        self._mob_map: Dict[str, dict] = {}    # id -> último estado (os snaps são parciais)
        self._players: List[dict] = []
        self._events: List[dict] = []
        self._blocks: List[tuple] = []
        # Física no servidor: o que ele decidiu e manda para cá.
        self._fields: List[dict] = []
        self._anims: List[list] = []
        self._fx: List[list] = []
        self._server_events: List[dict] = []
        # Água: o servidor só a guarda em memória e indica quem a simula.
        self._water: List[tuple] = []
        self._screw: list = []
        self.water_host: Optional[str] = None
        self._req = 0
        self.last_error: Optional[str] = None
        # Escritas HTTP em segundo plano
        self._writes: "queue.Queue" = queue.Queue()
        self._writer = threading.Thread(target=self._write_worker, daemon=True)
        self._writer.start()
        self._applying_remote = False
        # Conteúdo dos baús portáteis que eu gravei (o servidor grava pela
        # fila do tempo real; ler logo a seguir por HTTP podia vir antigo).
        self._icont_local: Dict[str, list] = {}
        # Presença: sinal de vida em segundo plano; se o servidor disser que a
        # sessão já não vale, o jogo volta ao login com esta mensagem.
        self.session_lost: Optional[str] = None
        self._heartbeat = threading.Thread(target=self._heartbeat_worker, daemon=True)
        self._heartbeat.start()

    # ================================================================ HTTP
    def _http(self, method: str, path: str, body: Any = None, auth: bool = True):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.http_url + path, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if auth and self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                raw = resp.read()
                return json.loads(raw.decode("utf-8")) if raw else {}
        except urllib.error.HTTPError as exc:
            try:
                msg = json.loads(exc.read().decode("utf-8")).get("erro") or str(exc)
            except Exception:
                msg = str(exc)
            if exc.code in (400, 401, 409) and path.endswith(("/login", "/register")):
                raise AuthError(msg) from None
            if exc.code == 404:
                raise RoomNotFoundError(msg) from None
            raise BackendError(msg) from None
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise BackendError(f"sem ligação a {self.http_url} ({exc})") from None

    def _write_worker(self):
        while True:
            item = self._writes.get()
            try:
                if item is None:
                    return
                method, path, body = item
                for attempt in range(3):
                    try:
                        self._http(method, path, body)
                        break
                    except BackendError as exc:
                        self.last_error = str(exc)
                        if attempt == 2:
                            print(f"[online] escrita perdida {method} {path}: {exc}")
                        time.sleep(1.0 + attempt)
            finally:
                self._writes.task_done()

    def _post_status(self, path: str, token: str, timeout: float) -> int:
        """POST sem corpo com ``token``; devolve o código HTTP (0 sem rede)."""
        req = urllib.request.Request(self.http_url + path, data=b"{}", method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return int(resp.status)
        except urllib.error.HTTPError as exc:
            return int(exc.code)
        except (urllib.error.URLError, OSError, TimeoutError):
            return 0

    def _heartbeat_worker(self):
        while True:
            time.sleep(HEARTBEAT_SECONDS)
            token = self.token
            if not token:
                continue
            code = self._post_status("/api/presence", token, HTTP_TIMEOUT)
            if code == 401 and self.token == token:
                self.token = None
                self.session_lost = ("Sua sessão terminou (a conta entrou em outro lugar "
                                     "ou expirou). Entre de novo.")

    def logout(self) -> None:
        """Sair da conta: a conta fica livre na hora no servidor (outro login
        passa a ser possível). Espera um pouco pelas escritas pendentes."""
        token = self.token
        if not token:
            return
        deadline = time.time() + 5.0
        while self._writes.unfinished_tasks and time.time() < deadline:
            time.sleep(0.05)
        self.token = None
        self.session = None
        self._post_status("/api/logout", token, LOGOUT_TIMEOUT)

    def _write(self, method, path, body):
        self._writes.put((method, path, body))

    def flush_room_writes(self) -> None:
        """Espera as escritas pendentes (ao sair / trocar de mundo)."""
        self._writes.join()

    # ------------------------------------------------------------ contas
    def _session_from(self, out) -> PlayerSession:
        self.token = out.get("token")
        self.session = PlayerSession(player_id=str(out["player_id"]),
                                     data=dict(out.get("data") or {}), token=self.token)
        # Itens, receitas, blocos, mobs e combate: os do servidor (os .json
        # da pasta do jogo não contam online). Ver game_config.py.
        import game_config
        try:
            game_config.install(self._http("GET", "/api/config"))
        except (BackendError, ValueError) as exc:
            raise BackendError(f"não foi possível obter as configurações do servidor: {exc}") from None
        return self.session

    def login(self, usuario: str, senha: str) -> PlayerSession:
        return self._session_from(self._http("POST", "/api/login",
                                             {"usuario": usuario, "senha": senha}, auth=False))

    def register(self, usuario: str, senha: str, nome: str = "") -> PlayerSession:
        return self._session_from(self._http("POST", "/api/register",
                                             {"usuario": usuario, "senha": senha,
                                              "nome": nome or usuario}, auth=False))

    def save_player(self, session: PlayerSession) -> None:
        # Inventário e almas vão pelo tempo real, na ordem dos pedidos de
        # blocos (o servidor confere com o que o jogador ganhou de verdade).
        if self._peer is not None and self._welcomed:
            self._send({"t": "inv", "inv": session.data.get("propertie_inventory"),
                        "almas": session.data.get("almas")})
        # O resto (posição, vida): HTTP. O servidor só aceita estes campos.
        data = {k: session.data[k] for k in ("last_position", "spawn_point", "properties_status")
                if k in session.data}
        self._write("PUT", "/api/players/me", {"data": data})

    # ------------------------------------------------------------ mundos
    def list_worlds(self, session):
        return self._http("GET", "/api/worlds")

    def create_world(self, session, nome):
        out = self._http("POST", "/api/worlds", {"nome": nome})
        room_id = out["id"]
        links = session.data.setdefault("link_room_properties", [])
        if room_id not in links:
            links.append(room_id)
        return room_id

    def rename_world(self, session, room_id, nome):
        return self._http("PATCH", f"/api/worlds/{room_id}", {"nome": nome}).get("nome", nome)

    def join_world(self, session, room_id):
        out = self._http("POST", f"/api/worlds/{room_id}/join", {}) or {}
        if out.get("temporario"):
            return          # dungeon: passe só para esta viagem, não fica na lista
        links = session.data.setdefault("link_room_properties", [])
        if room_id not in links:
            links.append(room_id)

    # ------------------------------------------------------------ baús portáteis
    def load_item_container(self, cid):
        if cid in self._icont_local:
            return [list(e) if e else None for e in self._icont_local[cid]]
        self._writes.join()      # o que eu mesmo gravei tem de chegar antes
        return list(self._http("GET", f"/api/containers/{cid}").get("slots") or [])

    def save_item_container(self, cid, slots, size=24):
        # conferido com o livro do jogador (só entra no baú o que ele tem)
        self._icont_local[cid] = [list(e) if e else None for e in slots]
        self._send({"t": "icont", "cid": cid, "entries": slots, "size": int(size)})

    def delete_item_container(self, cid):
        pass                  # quem apaga é o servidor (ao pôr o baú no mundo)

    # ------------------------------------------------------------ salas
    def load_room(self, room_id: str) -> Dict[str, Any]:
        return self._http("GET", f"/api/rooms/{room_id}")

    def set_room_block(self, room_id: str, key: str, tipo: Any) -> None:
        if self._applying_remote:
            return                       # veio de outro jogador: já está no servidor
        return               # online os blocos mudam por send_place/send_remove (pagos)

    def migrate_room(self, room_id: str, fields) -> None:
        # Online, os campos da sala (baús, almas, ligações, cola) são do
        # servidor: baús e almas vão por send_chest/send_bank.
        return

    def set_room_links(self, room_id: str, links) -> None:
        return                # a física do servidor grava as ligações

    def reset_room_modifications(self, room_id: str) -> None:
        self._write("POST", f"/api/rooms/{room_id}/reset", {})

    # ================================================================ ENet
    def join_room(self, room_id: str) -> None:
        """Liga ao servidor de tempo real e entra na sala."""
        try:
            import enet
        except ImportError as exc:
            raise BackendError(
                "biblioteca ENet (pyenet) não instalada: veja o README (Python 3.12 + "
                "pip install -r requirements.txt)") from exc
        self._enet = enet
        self.leave_room()
        self._host = enet.Host(None, 1, 2, 0, 0)
        addr = enet.Address(self.enet_host.encode("utf-8"), self.enet_port)
        self._peer = self._host.connect(addr, 2)
        self._room = room_id
        self._welcomed = False
        self._mobs, self._players, self._events, self._blocks = [], [], [], []
        self._mob_map = {}
        self._water, self.water_host = [], None
        self._screw = []
        deadline = time.monotonic() + CONNECT_TIMEOUT_MS / 1000.0
        while time.monotonic() < deadline:
            event = self._host.service(50)
            if event.type == enet.EVENT_TYPE_CONNECT:
                self._connected = True
                self._send({"t": "hello", "token": self.token, "room": room_id})
            elif event.type == enet.EVENT_TYPE_RECEIVE:
                self._handle(event.packet.data)
                if self._welcomed:
                    print(f"[online] tempo real ligado ({self.enet_host}:{self.enet_port})")
                    return
                if self.last_error:
                    raise BackendError(self.last_error)
            elif event.type == enet.EVENT_TYPE_DISCONNECT:
                break
        raise BackendError(f"servidor de tempo real não respondeu em "
                           f"{self.enet_host}:{self.enet_port} (UDP)")

    def leave_room(self) -> None:
        if self._peer is not None:
            try:
                self._send({"t": "bye"})
                self._host.flush()
                self._peer.disconnect_later()
                for _ in range(10):
                    if self._host.service(20).type == self._enet.EVENT_TYPE_DISCONNECT:
                        break
            except Exception:
                pass
        self._peer = None
        self._host = None
        self._connected = False
        self._welcomed = False

    def release_room(self, room_id: str) -> None:
        self.leave_room()
        self.flush_room_writes()

    def _send(self, obj) -> None:
        if self._peer is None:
            return
        data = json.dumps(obj, separators=(",", ":")).encode("utf-8")
        try:
            self._peer.send(0, self._enet.Packet(data, self._enet.PACKET_FLAG_RELIABLE))
        except Exception as exc:
            self.last_error = str(exc)

    def pump(self) -> None:
        """Lê tudo o que chegou (chamar todos os frames)."""
        if self._host is None:
            return
        enet = self._enet
        try:
            for _ in range(200):
                event = self._host.service(0)
                if event.type == enet.EVENT_TYPE_NONE:
                    break
                if event.type == enet.EVENT_TYPE_RECEIVE:
                    self._handle(event.packet.data)
                elif event.type == enet.EVENT_TYPE_DISCONNECT:
                    self._connected = False
                    self._welcomed = False
                    self.last_error = "ligação ao servidor perdida"
                    print("[online] ligação de tempo real perdida")
                    break
            self._host.flush()
        except Exception as exc:
            self.last_error = str(exc)

    @staticmethod
    def _unpack_mob(row) -> dict:
        """[id, tipo, x, y, z, yaw, vida, clipe, tempo, extra?] -> dict (como antes)."""
        if isinstance(row, dict):
            return row
        d = {"id": row[0], "tipo": row[1], "pos": [row[2], row[3], row[4]], "yaw": row[5],
             "health": row[6], "anim": row[7], "anim_time": row[8],
             "hidden": [], "parts": {}, "dying": False, "aura": False}
        if len(row) > 9 and isinstance(row[9], dict):
            d.update(row[9])
        try:
            import mob_types
            d["max_health"] = float((mob_types.get(d["tipo"]) or {}).get("health", 20))
        except Exception:
            d["max_health"] = max(1.0, float(d["health"]))
        return d

    def _apply_mobs(self, rows, full) -> None:
        """Os snaps só trazem os mobs perto (20x/s); "full" traz todos (4x/s)
        e apaga os que já não existem."""
        if full is None:                       # servidor antigo: lista inteira sempre
            full = True
        seen = set()
        for row in rows:
            d = self._unpack_mob(row)
            self._mob_map[d["id"]] = d
            seen.add(d["id"])
        if full:
            for mid in [m for m in self._mob_map if m not in seen]:
                del self._mob_map[mid]
        self._mobs = list(self._mob_map.values())

    def _handle(self, raw: bytes) -> None:
        try:
            msg = json.loads(bytes(raw).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return
        t = msg.get("t")
        if t == "welcome":
            self._welcomed = True
            self.last_error = None
        elif t == "error":
            self.last_error = str(msg.get("msg"))
            print(f"[online] servidor: {self.last_error}")
        elif t == "ev":
            self._events.extend(msg.get("events") or [])
        elif t == "snap":
            if msg.get("host") is not None:
                self.water_host = str(msg.get("host"))
            self._apply_mobs(msg.get("mobs") or [], msg.get("full"))
            self._projectiles = msg.get("projectiles") or []
            self._players = msg.get("players") or []
            self._events.extend(msg.get("events") or [])
        elif t == "block":
            self._blocks.append((msg.get("key"), msg.get("value"), msg.get("by")))
        elif t == "blocks":
            for k, v in msg.get("changes") or []:
                self._blocks.append((k, v, "servidor"))
        elif t == "water":
            self._water.append(("changes", msg.get("changes") or []))
        elif t == "water_state":
            self._water.append(("state", msg.get("cells") or []))
        elif t == "crank":
            self._screw.append(("crank", (msg.get("cell"), msg.get("dir", 1))))
        elif t == "screw":
            self._screw.append(("pockets", msg.get("s") or []))
        elif t == "screw_state":
            # o estado inteiro (ao entrar; pode vir gravado da base de dados):
            # vale também para quem simula
            self._screw.append(("pockets_state", msg.get("s") or []))
        elif t == "fields":
            self._fields.append(msg.get("fields") or {})
        elif t == "anim":
            self._anims.extend(msg.get("items") or [])
        elif t == "fx":
            self._fx.extend(msg.get("items") or [])
        elif t in ("hit_result", "destroyed", "reject", "inv_fix", "icont_fix", "chest_fix", "corpse_fix",
                   "pos_fix", "config", "kicked"):
            if t == "icont_fix" and msg.get("cid"):
                if msg.get("entries") is None:
                    self._icont_local.pop(msg["cid"], None)
                else:
                    self._icont_local[msg["cid"]] = msg["entries"]
            self._server_events.append(msg)
        elif t == "attack_result":
            ev = dict(msg)
            ev["type"] = "attack_result"
            self._events.append(ev)
        elif t == "pvp_hit":
            self._events.append({"type": "player_hit", "kind": "pvp",
                                 "tipo": msg.get("nome") or msg.get("from"),
                                 "damage": msg.get("damage", 0), "part": msg.get("part", "corpo"),
                                 "from": msg.get("pos")})
        elif t == "pvp_ok":
            self._events.append({"type": "pvp_ok", "target": msg.get("target"),
                                 "damage": msg.get("damage", 0), "part": msg.get("part", "corpo")})
        elif t == "player_left":
            pid = msg.get("id")
            self._players = [p for p in self._players if p.get("id") != pid]

    # ------------------------------------------------------------ jogo
    @property
    def online(self) -> bool:
        return self._welcomed

    def remote_players(self) -> List[dict]:
        return list(self._players)

    def poll_blocks(self) -> List[tuple]:
        out, self._blocks = self._blocks, []
        return out

    # ------------------------------------------------------------ física no servidor
    @property
    def server_physics(self) -> bool:
        """Online, a física dos blocos é do servidor: o jogo só pede."""
        return True

    def send_hit(self, cell, damage, direction):
        self._send({"t": "hit", "cell": list(cell), "damage": float(damage),
                    "dir": [int(v) for v in direction]})

    def send_push(self, cell, direction, force):
        """Empurrar agachado: o servidor move a fila se ``force`` (N) chegar."""
        self._send({"t": "push", "cell": list(cell), "dir": [int(v) for v in direction],
                    "force": float(force)})

    def send_place(self, cell, tipo, orientation=None, data=None):
        msg = {"t": "place", "cell": list(cell), "tipo": tipo, "orientation": orientation}
        if data:
            msg["data"] = data          # baú com conteúdo / minoculus com almas
        self._send(msg)

    def send_remove(self, cell, cid=None):
        msg = {"t": "remove", "cell": list(cell)}
        if cid:
            msg["cid"] = cid            # baú com itens: o conteúdo fica no servidor com este id
        self._send(msg)

    def refresh_config(self):
        """Os moderadores mudaram o balanceamento: busca as configurações de novo."""
        import game_config
        game_config.update_balancing(self._http("GET", "/api/config"))

    # ------------------------------------------------------------ economia
    @property
    def server_economy(self) -> bool:
        return True

    def send_chest(self, cell, entries):
        self._send({"t": "chest", "cell": [int(v) for v in cell], "entries": entries})

    def send_bank(self, cell, delta):
        self._send({"t": "bank", "cell": [int(v) for v in cell], "delta": int(delta)})

    def send_transmute(self, idx, item, stone_d=None):
        self._send({"t": "transmute", "idx": int(idx), "item": item,
                    "stone_d": stone_d if stone_d is not None else -1})

    def send_test(self, kind):
        self._send({"t": "test", "kind": kind})

    def send_glue(self, cell, normal):
        self._send({"t": "glue", "cell": list(cell),
                    "normal": [int(v) for v in normal] if normal is not None else None})

    # ------------------------------------------------------------ água
    WATER_BATCH = 2000

    def send_water(self, changes) -> None:
        """Mudanças de água daqui ([chave, nível]...): o servidor passa aos outros."""
        for i in range(0, len(changes), self.WATER_BATCH):
            self._send({"t": "water", "changes": changes[i:i + self.WATER_BATCH]})

    # ------------------------------------------------------------ parafusos de Arquimedes
    def send_bucket_fill(self, cell) -> None:
        """Um balde vazio do mundo encheu com água que caiu nele (quem simula)."""
        self._send({"t": "bucket_fill", "cell": [int(v) for v in cell]})

    def send_death(self, pos, yaw=0.0) -> None:
        """Morri: o servidor passa todos os meus itens para um cadáver aqui."""
        self._send({"t": "death", "pos": [float(v) for v in pos], "yaw": float(yaw or 0.0)})

    def send_corpse(self, cid, entries) -> None:
        """Fechei um cadáver: o que ficou nele (o servidor confere, como num baú)."""
        self._send({"t": "corpse", "id": str(cid), "entries": entries})

    def send_ignite(self, cell) -> None:
        """Isqueiro: acender o bloco ``cell``."""
        self._send({"t": "ignite", "cell": [int(v) for v in cell]})

    def send_buy(self, item) -> None:
        """Comprar no minoculus (preço em almas, ver blocks.json "portal.loja")."""
        self._send({"t": "buy", "item": str(item)})

    def send_crank(self, cell, direction=1) -> None:
        self._send({"t": "crank", "cell": [int(v) for v in cell], "dir": 1 if direction >= 0 else -1})

    def send_screw(self, rows) -> None:
        for i in range(0, len(rows), 1000):
            self._send({"t": "screw", "s": rows[i:i + 1000]})

    def poll_screw(self):
        """[("crank", célula) | ("pockets", linhas)] pela ordem de chegada."""
        out, self._screw = self._screw, []
        return out

    def poll_water(self):
        """[("state", células) | ("changes", mudanças)] pela ordem de chegada."""
        out, self._water = self._water, []
        return out

    def poll_physics(self):
        """(campos, animações, partículas, eventos para mim) desde a última vez."""
        out = (self._fields, self._anims, self._fx, self._server_events)
        self._fields, self._anims, self._fx, self._server_events = [], [], [], []
        return out

    def mob_sync(self, room_id, player_id, state):
        self._send(dict(state, t="state"))
        self.pump()
        events, self._events = self._events, []
        return {"mobs": self._mobs, "events": events,
                "projectiles": getattr(self, "_projectiles", [])}

    def mob_attack(self, room_id, player_id, mob_id, damage, origin, kind="golpe"):
        self._req += 1
        self._send({"t": "attack", "req": self._req, "mob": mob_id, "damage": float(damage),
                    "origin": list(origin), "kind": kind})
        return {"result": "pendente", "req": self._req}

    def projectile_deflect(self, proj_id, direction):
        """A espada bateu num projétil de mob: o servidor rebate-o."""
        self._send({"t": "deflect", "proj": str(proj_id), "dir": [float(v) for v in direction]})

    def pvp_attack(self, target_id, damage, part, origin):
        """Golpe noutro jogador: o servidor confere e avisa a vítima."""
        self._send({"t": "pvp", "target": target_id, "damage": float(damage),
                    "part": part, "origin": list(origin)})

    def mob_spawn(self, room_id, tipo, position=None):
        return {"result": "invalido", "motivo": "no modo online o servidor decide"}


__all__ = ["RemoteBackend"]
