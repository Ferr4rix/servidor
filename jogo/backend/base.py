"""Contrato entre o cliente do jogo e o armazenamento (back-end).

O jogo só fala com um objeto ``Backend``. Hoje a implementação é
``LocalBackend`` (ficheiros JSON); amanhã basta criar um ``HttpBackend`` com
os mesmos métodos e trocá-lo em ``main.py`` — o resto do cliente não muda.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


class BackendError(Exception):
    """Falha ao falar com o back-end (ficheiro em falta, servidor offline...)."""


class AuthError(BackendError):
    """Usuário ou senha inválidos."""


class RoomNotFoundError(BackendError):
    """A sala pedida não existe no back-end."""


@dataclass
class PlayerSession:
    """Jogador autenticado. ``data`` é o registo dele, sem a senha."""

    player_id: str
    data: Dict[str, Any] = field(default_factory=dict)
    token: Optional[str] = None   # reservado para o back-end HTTP

    @property
    def home_room(self) -> Optional[str]:
        """A sala própria do jogador (o servidor manda no login)."""
        home = self.data.get("sala_propria")
        if home:
            return str(home)
        ids = self.room_ids
        return str(ids[0]) if ids else None

    @property
    def last_room(self) -> Optional[str]:
        """A sala onde estava ao sair (gravada pelo servidor ao entrar)."""
        last = self.data.get("ultima_sala")
        return str(last) if last else None

    @property
    def usuario(self) -> str:
        return str(self.data.get("usuario", ""))

    @property
    def name(self) -> str:
        return str(self.data.get("nome") or self.usuario or self.player_id)

    @property
    def room_ids(self) -> List[str]:
        return [str(r) for r in self.data.get("link_room_properties") or []]


class Backend(ABC):
    @abstractmethod
    def login(self, usuario: str, senha: str) -> PlayerSession:
        """Valida as credenciais ou levanta ``AuthError``."""

    @abstractmethod
    def load_room(self, room_id: str) -> Dict[str, Any]:
        """Devolve ``seed``, ``size_x/y/z`` e ``modifications`` da sala."""

    @abstractmethod
    def set_room_block(self, room_id: str, key: str, tipo: Any) -> None:
        """Grava um bloco (``key`` = ``"x,y,z"``); ``None`` remove."""

    def migrate_room(self, room_id: str, fields: Dict[str, Any]) -> None:
        """Substitui campos da sala (usado ao migrar o layout das camadas)."""
        raise NotImplementedError

    # ---- economia no servidor (online): o servidor decide o que o jogador tem
    @property
    def server_economy(self) -> bool:
        """True online: inventário, baús e almas são conferidos pelo servidor."""
        return False

    def send_chest(self, cell, entries) -> None:
        """Fechou um baú do mundo com ``entries`` dentro."""

    def send_bank(self, cell, delta) -> None:
        """Guardou (+) / tirou (-) almas do minoculus em ``cell``."""

    def send_transmute(self, idx, item, stone_d=None) -> None:
        """Transmutou a receita ``idx`` (``item``)."""

    def send_test(self, kind) -> None:
        """Teclas de teste (F6/F7/F8)."""

    def set_room_links(self, room_id: str, links: List[str]) -> None:
        """Grava as ligações de estrutura da sala (``"x,y,z|x,y,z"``).

        Ligações feitas com cola ou árvores que caíram; as árvores intactas
        são geradas pela seed e não precisam ser gravadas.
        """
        raise NotImplementedError

    # ---------------------------------------------------------- baús portáteis
    # Um baú no inventário (ou na mão de alguém) guarda os itens num
    # "contentor" com id próprio, ex.: "cnt-1a2b3c4d". Qualquer jogador pode
    # abri-lo (ex.: o baú na mão de outro jogador).
    def load_item_container(self, cid: str) -> List[Any]:
        """Slots do contentor: ``[[item, quantidade, dados?] ou None, ...]``."""
        raise NotImplementedError

    def save_item_container(self, cid: str, slots: List[Any], size: int = 24) -> None:
        raise NotImplementedError

    def delete_item_container(self, cid: str) -> None:
        raise NotImplementedError

    # ---------------------------------------------------------- mundos
    def list_worlds(self, session: PlayerSession) -> List[Dict[str, Any]]:
        """Mundos (salas) a que o jogador tem acesso: ``[{"id", "nome"}]``."""
        return [{"id": r, "nome": r} for r in session.room_ids]

    def create_world(self, session: PlayerSession, nome: str) -> str:
        """Cria um mundo novo (semente aleatória), dá acesso ao jogador e
        devolve o id. O servidor decide a semente e o tamanho."""
        raise NotImplementedError

    def rename_world(self, session: PlayerSession, room_id: str, nome: str) -> str:
        """Muda o nome de um mundo (só o dono). Devolve o nome gravado."""
        raise NotImplementedError

    def join_world(self, session: PlayerSession, room_id: str) -> None:
        """Dá ao jogador acesso ao mundo de outro jogador (para se encontrarem)."""
        raise NotImplementedError

    def release_room(self, room_id: str) -> None:
        """O jogador saiu da sala (ex.: viajou por um portal)."""

    # ---------------------------------------------------------- mobs
    # Os mobs são do servidor: posição, vida e comportamento só mudam lá. O
    # cliente conta onde o jogador está e pede golpes; recebe o estado.
    def mob_sync(self, room_id: str, player_id: str, state: Dict[str, Any]) -> Dict[str, Any]:
        """Envia o estado do jogador (``pos``, ``height``, ``half_width``,
        ``swing``...) e devolve ``{"time", "mobs": [...], "events": [...]}``."""
        raise NotImplementedError

    def mob_attack(self, room_id: str, player_id: str, mob_id: str, damage: float,
                   origin, kind: str = "golpe") -> Dict[str, Any]:
        """Pede para golpear um mob; o servidor responde com o resultado
        (``acertou``, ``bloqueou``, ``escudo_quebrou``, ``morreu`` ou ``invalido``)."""
        raise NotImplementedError

    def mob_spawn(self, room_id: str, tipo: str, position=None) -> Dict[str, Any]:
        """Pede ao servidor para criar um mob (testes / comandos de admin)."""
        raise NotImplementedError

    def attach_room_world(self, room_id: str, room) -> None:
        """Só no back-end local: partilha o terreno já carregado com o servidor
        de mobs (um servidor de verdade tem a sua própria cópia da sala)."""

    @abstractmethod
    def reset_room_modifications(self, room_id: str) -> None:
        """Apaga todas as alterações dos jogadores na sala."""

    @abstractmethod
    def save_player(self, session: PlayerSession) -> None:
        """Persiste ``session.data`` (inventário, posição, status...)."""
