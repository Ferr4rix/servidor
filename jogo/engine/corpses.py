"""Cadáveres: ao morrer, o jogador deixa o corpo com **todos os itens**.

Cada cadáver é um "baú" que não é bloco: fica deitado onde o jogador morreu e
qualquer um pode abri-lo (botão direito) e tirar os itens. Some sozinho:

* **10 min** (``LIFE_UNTOUCHED``) se ninguém tirar nada;
* **1 min** (``LIFE_AFTER_TAKE``) depois de tirarem o primeiro item.

Quando some, o que ainda tiver dentro perde-se.

Estado (campo ``corpos`` da sala, gravado e mandado a todos)::

    {"<id>": {"pos": [x, y, z], "yaw": graus, "nome": "...", "pid": "...",
              "slots": [[id, n, dados?] | null, ...],
              "criado": epoch, "expira": epoch, "mexido": bool}}

Quem simula (o worker do servidor, ou o jogo sem servidor) cria os
cadáveres, muda o conteúdo e chama ``update`` (some quando expira). O
conteúdo entra e sai do livro do jogador no servidor (economy.transfer),
como num baú.
"""

from __future__ import annotations

import random
import time
from typing import Callable, Dict, Optional

LIFE_UNTOUCHED = 600.0      # s sem ninguém tirar nada
LIFE_AFTER_TAKE = 60.0      # s depois de tirarem o primeiro item
MAX_SLOTS = 40              # inventário (32) + equipamento (5), com folga
MIN_SLOTS = 8
REACH_BLOCKS = 6.0          # só se mexe num cadáver a até isto (blocos)


def count_items(entries) -> int:
    n = 0
    for e in entries or []:
        if isinstance(e, (list, tuple)) and len(e) >= 2:
            try:
                n += max(0, int(e[1]))
            except (TypeError, ValueError):
                pass
    return n


class Corpses:
    def __init__(self, save: Optional[Callable[[dict], None]] = None,
                 clock: Callable[[], float] = time.time):
        self.save = save
        self.clock = clock
        self.state: Dict[str, dict] = {}
        self.version = 0
        self._t = 0.0

    # ------------------------------------------------------------ consultas
    def get(self, cid) -> Optional[dict]:
        return self.state.get(str(cid))

    # ------------------------------------------------------------ mudar
    def create(self, pos, yaw, nome, pid, entries) -> Optional[str]:
        entries = list(entries or [])[:MAX_SLOTS]
        if not any(entries):
            return None                       # morreu sem nada: sem cadáver
        size = max(MIN_SLOTS, len(entries))
        size = ((size + 7) // 8) * 8          # filas de 8
        entries += [None] * (min(MAX_SLOTS, size) - len(entries))
        now = self.clock()
        cid = "c%x%04x" % (int(now * 1000), random.randint(0, 0xFFFF))
        self.state[cid] = {
            "pos": [round(float(v), 2) for v in pos], "yaw": round(float(yaw or 0.0), 1),
            "nome": str(nome or "")[:32], "pid": str(pid or ""),
            "slots": entries, "criado": round(now, 1),
            "expira": round(now + LIFE_UNTOUCHED, 1), "mexido": False,
        }
        self._changed()
        return cid

    def set_slots(self, cid, entries, taken: bool) -> None:
        c = self.get(cid)
        if c is None:
            return
        c["slots"] = list(entries)
        if taken and not c.get("mexido"):
            c["mexido"] = True
            c["expira"] = round(min(float(c["expira"]), self.clock() + LIFE_AFTER_TAKE), 1)
        self._changed()

    def remove(self, cid) -> None:
        if self.state.pop(str(cid), None) is not None:
            self._changed()

    def update(self, dt: float = 1.0) -> None:
        """Some os que expiraram (verifica a cada segundo)."""
        self._t += dt
        if self._t < 1.0 or not self.state:
            return
        self._t = 0.0
        now = self.clock()
        gone = [k for k, c in self.state.items() if float(c.get("expira", 0)) <= now]
        for k in gone:
            del self.state[k]
        if gone:
            self._changed()

    # ------------------------------------------------------------ estado
    def apply_state(self, data) -> None:
        self.state = {str(k): dict(v) for k, v in (data or {}).items() if isinstance(v, dict)}
        self.version += 1

    def _changed(self) -> None:
        self.version += 1
        if self.save is not None:
            try:
                self.save(dict(self.state))
            except Exception as exc:
                print(f"[cadáver] não gravei: {exc}")


__all__ = ["Corpses", "count_items", "LIFE_UNTOUCHED", "LIFE_AFTER_TAKE", "MAX_SLOTS",
           "REACH_BLOCKS"]
