"""Economia do servidor: o que cada jogador TEM de verdade.

O jogo (cliente) mostra e arruma o inventário, mas quem sabe o que o jogador
possui é o servidor. Cada jogador tem um *livro* (``Ledger``):

* ``items``: quantos tem de cada item que empilha (terra, pedra, maçã...);
* ``units``: as unidades dos itens que não empilham (armas, armaduras, baús,
  pedra de transmutação...), cada uma com a durabilidade (``d``) e, se for um
  baú com conteúdo guardado no servidor, o id desse conteúdo (``c``);
* ``souls``: as almas (as que leva + as guardadas em minoculus no inventário).

O livro só cresce com o que o servidor vê acontecer: tirar um bloco do mundo,
o baú/minoculus que quebrou, o que um mob largou, almas de um mob morto,
tirar coisas de um baú do mundo, transmutação (com as receitas do servidor),
itens iniciais e, no modo de testes, F6/F8. E só diminui pelo que o servidor
vê sair: pôr um bloco, cola, guardar num baú/minoculus, transmutação.

O inventário que o jogo manda (``check``) é conferido com o livro: o que tiver
A MAIS é cortado (o jogo recebe a correção); o que tiver a menos (comeu, a
arma gastou, morreu...) é aceite e o livro passa a ser esse.

Os itens do inventário gravado são os mesmos do jogo (``propertie_inventory``):
``{"slots": [{"id": 9, "count": 64, "data": {...}} | null, ...], "armor": {...}}``.
Conteúdo de baús: ``[[id, quantidade, dados?] | null, ...]``.
"""

from __future__ import annotations

import copy
from collections import Counter
from typing import Dict, List, Optional, Tuple

from items import (canonical_item_id, ensure_loaded as ensure_items, get_item_type, item_from_ref,
                   item_id_for_block, item_ref, max_stack_for)

SLOT_COUNT = 32
HOTBAR_START = 24
ARMOR_SLOTS = ("head", "chest", "legs", "feet", "offhand")
DUR_KEY = "durabilidade"
EPS = 1e-6

# Balde vazio e balde com água são o mesmo balde (a água não vale nada).
FAMILY = {"balde_agua": "balde"}

# --- almas -> pontos (igual a player_advanced/souls.py, sem importar o jogo) ---
TIER_SIZE = 100
BASE_COST = 10
STATS = ("vida", "forca", "velocidade", "estamina", "attack_speed")


def points_for(souls: float) -> int:
    souls = max(0.0, float(souls))
    total, tier = 0.0, 1
    while souls > 0:
        part = min(souls, TIER_SIZE)
        total += part / (BASE_COST * tier)
        souls -= part
        tier += 1
    return int(total + 1e-9)


# ---------------------------------------------------------------- itens
def _known(name) -> bool:
    if not name:
        return False
    if get_item_type(name) is not None:
        return True
    try:
        from blocks import get_block_type
        return get_block_type(str(name)) is not None
    except Exception:
        return False


def item_name(ref) -> Optional[str]:
    """Nome do item a partir do que veio gravado (id numérico ou nome)."""
    ensure_items()
    name = item_from_ref(ref)
    if not name:
        return None
    name = canonical_item_id(name)
    t = get_item_type(name)
    if t is not None:
        name = t.name
    return name if _known(name) else None


def family(name) -> str:
    return FAMILY.get(name, name)


def max_dur(name) -> float:
    try:
        import item_balancing
        return max(0.0, float(item_balancing.get_stat(name, DUR_KEY, 0.0)))
    except Exception:
        return 0.0


def is_unit(fam) -> bool:
    """Itens que não empilham (cada unidade tem dados próprios)."""
    if fam == "balde":
        return False
    return max_stack_for(fam) == 1 or max_dur(fam) > 0


def _unit_from(name, data) -> dict:
    data = data if isinstance(data, dict) else {}
    top = max_dur(name)
    d = None
    if top > 0:
        try:
            d = max(0.0, min(top, float(data.get(DUR_KEY, top))))
        except (TypeError, ValueError):
            d = top
    c = data.get("container")
    return {"d": d, "c": str(c) if isinstance(c, str) and c else None}


def _strip_container(data):
    if not isinstance(data, dict):
        return data
    out = {k: v for k, v in data.items() if k not in ("container", "ocupados", "peso_conteudo")}
    return out or None


# ---------------------------------------------------------------- livro
class Ledger:
    def __init__(self):
        self.items: Counter = Counter()
        self.units: Dict[str, List[dict]] = {}
        self.souls = 0
        self.layout: Optional[dict] = None        # último inventário aceite
        self.almas: dict = {"total": 0, "pontos": []}
        self.dirty = False

    # ---- gravar / ler -------------------------------------------------------
    def dump(self) -> dict:
        return {"items": {k: int(v) for k, v in self.items.items() if v > 0},
                "units": {k: [dict(u) for u in v] for k, v in self.units.items() if v},
                "souls": int(self.souls)}

    @classmethod
    def load(cls, raw) -> "Ledger":
        led = cls()
        raw = raw if isinstance(raw, dict) else {}
        for k, v in (raw.get("items") or {}).items():
            try:
                if int(v) > 0:
                    led.items[str(k)] = int(v)
            except (TypeError, ValueError):
                pass
        for k, v in (raw.get("units") or {}).items():
            if isinstance(v, list):
                led.units[str(k)] = [{"d": u.get("d"), "c": u.get("c")} for u in v if isinstance(u, dict)]
        try:
            led.souls = max(0, int(raw.get("souls", 0)))
        except (TypeError, ValueError):
            led.souls = 0
        return led

    # ---- contar -----------------------------------------------------------------
    def count(self, fam) -> int:
        return len(self.units.get(fam, ())) if is_unit(fam) else int(self.items.get(fam, 0))

    def has_cid(self, cid) -> bool:
        return any(u.get("c") == cid for us in self.units.values() for u in us)

    # ---- mudar -----------------------------------------------------------------------
    def add(self, name, count=1, data=None):
        fam = family(name)
        count = int(count)
        if count <= 0:
            return
        if is_unit(fam):
            lst = self.units.setdefault(fam, [])
            for _ in range(count):
                lst.append(_unit_from(name, data))
        else:
            self.items[fam] += count
        if isinstance(data, dict):
            try:
                self.souls += max(0, int(data.get("almas", 0) or 0)) * count
            except (TypeError, ValueError):
                pass
        self.dirty = True

    def take(self, name, count=1, unit=None) -> int:
        """Tira ``count`` (unidades: a que melhor bate com ``unit``). Devolve
        quantos tirou."""
        fam = family(name)
        if is_unit(fam):
            lst = self.units.get(fam) or []
            got = 0
            for _ in range(int(count)):
                i = _best_unit(lst, unit)
                if i is None:
                    break
                lst.pop(i)
                got += 1
            if got:
                self.dirty = True
            return got
        have = int(self.items.get(fam, 0))
        got = min(have, int(count))
        if got:
            self.items[fam] = have - got
            if self.items[fam] <= 0:
                del self.items[fam]
            self.dirty = True
        return got

    def take_unit(self, name, unit) -> Optional[dict]:
        """Tira e devolve a unidade que melhor bate com ``unit`` (ou None)."""
        lst = self.units.get(family(name)) or []
        i = _best_unit(lst, unit)
        if i is None:
            return None
        self.dirty = True
        return lst.pop(i)

    def add_souls(self, n):
        n = int(n)
        if n:
            self.souls = max(0, self.souls + n)
            self.dirty = True

    def take_souls(self, n) -> int:
        got = max(0, min(int(n), self.souls))
        if got:
            self.souls -= got
            self.dirty = True
        return got


def ledger_entries(led) -> list:
    """Tudo o que o livro tem, como conteúdo de baú ([[id, n, dados?], ...]):
    pilhas até ao máximo de cada item; cada unidade com a durabilidade e o
    conteúdo (baú na mão). Para o cadáver (morrer deixa tudo)."""
    out = []
    for fam, n in sorted(led.items.items()):
        n = int(n)
        cap = max(1, max_stack_for(fam))
        while n > 0:
            k = min(cap, n)
            out.append([item_ref(fam), k])
            n -= k
    for fam, lst in sorted(led.units.items()):
        for u in lst:
            data = {}
            if u.get("d") is not None:
                data[DUR_KEY] = round(float(u["d"]), 2)
            if u.get("c"):
                data["container"] = u["c"]
            out.append([item_ref(fam), 1] + ([data] if data else []))
    return out


def wear_unit(led, name, amount) -> bool:
    """Gasta ``amount`` de durabilidade da unidade de ``name`` mais gasta que
    ainda a tem (isqueiro: 1 por uso); a 0 a unidade some. True se gastou."""
    lst = led.units.get(family(name)) or []
    if not lst:
        return False
    top = max_dur(name)
    i = _best_unit(lst, {"d": amount})
    if i is None:
        return False
    d = lst[i].get("d")
    d = top if d is None else float(d)
    d -= float(amount)
    if d <= EPS:
        lst.pop(i)
    else:
        lst[i]["d"] = round(d, 3)
    led.dirty = True
    return True


def _best_unit(lst, want) -> Optional[int]:
    """Índice da unidade que melhor bate com ``want`` ({"d", "c"}): o mesmo
    conteúdo (c); senão a de durabilidade mais baixa que ainda chega; senão a
    mais alta. Unidades com conteúdo só saem se ``want`` tiver o mesmo c."""
    if not lst:
        return None
    want = want or {}
    c = want.get("c")
    if c:
        for i, u in enumerate(lst):
            if u.get("c") == c:
                return i
    free = [i for i, u in enumerate(lst) if not u.get("c")] or list(range(len(lst)))
    d = want.get("d")
    if d is None:
        return free[0]
    ok = [i for i in free if (lst[i].get("d") or 0) + EPS >= d]
    if ok:
        return min(ok, key=lambda i: lst[i].get("d") or 0)
    return max(free, key=lambda i: lst[i].get("d") or 0)


# ---------------------------------------------------------------- inventário gravado
def _inv_entries(inv) -> List[Tuple[object, object, dict]]:
    """[(onde, chave, entrada)] na ordem de corte: o que sobra sai primeiro do
    fim da mochila; a armadura vestida por último."""
    out = []
    inv = inv if isinstance(inv, dict) else {}
    slots = inv.get("slots") if isinstance(inv.get("slots"), list) else []
    order = list(range(0, HOTBAR_START)) + list(range(HOTBAR_START, SLOT_COUNT))
    for i in reversed(order):
        if i < len(slots) and isinstance(slots[i], dict):
            out.append((slots, i, slots[i]))
    armor = inv.get("armor") if isinstance(inv.get("armor"), dict) else {}
    for k in ARMOR_SLOTS:
        if isinstance(armor.get(k), dict):
            out.append((armor, k, armor[k]))
    return out


def _entry_count(e) -> int:
    try:
        return max(0, int(e.get("count", 1)))
    except (TypeError, ValueError):
        return 0


def _remove(where, key):
    where[key] = None


def _clean_inventory(inv) -> dict:
    """Formato certo: 32 slots + armadura (itens desconhecidos saem)."""
    inv = copy.deepcopy(inv) if isinstance(inv, dict) else {}
    slots = inv.get("slots") if isinstance(inv.get("slots"), list) else []
    slots = (slots + [None] * SLOT_COUNT)[:SLOT_COUNT]
    clean = []
    for e in slots:
        if isinstance(e, dict) and item_name(e.get("id", e.get("item"))) and _entry_count(e) > 0:
            name = item_name(e.get("id", e.get("item")))
            out = {"id": item_ref(name), "count": _entry_count(e)}
            if isinstance(e.get("data"), dict) and e["data"]:
                out["data"] = dict(e["data"])
            clean.append(out)
        else:
            clean.append(None)
    armor = inv.get("armor") if isinstance(inv.get("armor"), dict) else {}
    carmor = {}
    for k in ARMOR_SLOTS:
        e = armor.get(k)
        name = item_name(e.get("id", e.get("item"))) if isinstance(e, dict) else None
        if name:
            out = {"id": item_ref(name), "count": 1}
            if isinstance(e.get("data"), dict) and e["data"]:
                out["data"] = dict(e["data"])
            carmor[k] = out
        else:
            carmor[k] = None
    try:
        sel = int(inv.get("selected", 0))
    except (TypeError, ValueError):
        sel = 0
    return {"slots": clean, "selected": sel, "armor": carmor}


def _clean_almas(almas) -> dict:
    almas = almas if isinstance(almas, dict) else {}
    try:
        total = max(0, int(almas.get("total", 0)))
    except (TypeError, ValueError):
        total = 0
    pontos = [p for p in (almas.get("pontos") or []) if p in STATS]
    return {"total": total, "pontos": pontos}


def _item_souls(entries) -> List[Tuple[dict, int]]:
    out = []
    for _w, _k, e in entries:
        data = e.get("data")
        if isinstance(data, dict):
            try:
                n = int(data.get("almas", 0) or 0)
            except (TypeError, ValueError):
                n = 0
            if n > 0:
                out.append((e, n))
    return out


def ledger_from_inventory(inv, almas) -> Ledger:
    """Livro a partir de um inventário (contas antigas: confia no que estava gravado)."""
    led = Ledger()
    inv = _clean_inventory(inv)
    almas = _clean_almas(almas)
    for _w, _k, e in _inv_entries(inv):
        name = item_name(e.get("id"))
        n = _entry_count(e)
        if is_unit(family(name)):
            n = 1
        data = e.get("data") if isinstance(e.get("data"), dict) else None
        led.add(name, n, {k: v for k, v in (data or {}).items() if k != "almas"} or None)
    led.souls = almas["total"] + sum(n for _e, n in _item_souls(_inv_entries(inv)))
    led.layout = inv
    led.almas = almas
    led.dirty = True
    return led


def check_inventory(led: Ledger, inv, almas) -> Tuple[dict, dict, bool]:
    """Confere o inventário que o jogo mandou com o livro. Devolve
    (inventário corrigido, almas corrigidas, mudou?) e o livro passa a ser
    o inventário corrigido (o que falta foi gasto/perdido no jogo)."""
    raw_inv = inv
    inv = _clean_inventory(inv)
    almas = _clean_almas(almas)
    changed = _clean_inventory(raw_inv) != inv     # (sempre igual; deixa claro)
    entries = _inv_entries(inv)

    # --- itens que empilham: corta o que passar do livro ----------------------
    groups: Dict[str, list] = {}
    for w, k, e in entries:
        fam = family(item_name(e.get("id")))
        groups.setdefault(fam, []).append((w, k, e))
    new_items: Counter = Counter()
    new_units: Dict[str, List[dict]] = {}
    for fam, group in groups.items():
        if not is_unit(fam):
            allowed = int(led.items.get(fam, 0))
            total = sum(_entry_count(e) for _w, _k, e in group)
            excess = total - allowed
            for w, k, e in group:              # já vem do fim para o começo
                if excess <= 0:
                    break
                n = _entry_count(e)
                cut = min(n, excess)
                excess -= cut
                changed = True
                if cut >= n:
                    _remove(w, k)
                else:
                    e["count"] = n - cut
            new_items[fam] = min(total, allowed)
            continue
        # --- unidades -------------------------------------------------------------
        avail = [dict(u) for u in led.units.get(fam, [])]
        units = []
        for w, k, e in group:
            if _entry_count(e) != 1:
                e["count"] = 1
                changed = True
            units.append((w, k, e))
        matched = []
        rest = []
        for w, k, e in units:                      # 1) o mesmo conteúdo (baús)
            data = e.get("data") if isinstance(e.get("data"), dict) else {}
            c = data.get("container")
            hit = None
            if c:
                for i, u in enumerate(avail):
                    if u.get("c") == c:
                        hit = avail.pop(i)
                        break
                if hit is None:
                    e["data"] = _strip_container(data)   # conteúdo que não é dele
                    if not e["data"]:
                        e.pop("data", None)
                    changed = True
            if hit is not None:
                matched.append((w, k, e, hit))
            else:
                rest.append((w, k, e))
        name_of = {}

        def dur_of(e):
            name = name_of.setdefault(id(e), item_name(e.get("id")))
            return _unit_from(name, e.get("data")).get("d") or 0.0

        rest.sort(key=lambda t: -dur_of(t[2]))
        avail.sort(key=lambda u: (bool(u.get("c")), -(u.get("d") or 0.0)))
        for i, (w, k, e) in enumerate(rest):
            if i < len(avail):
                matched.append((w, k, e, avail[i]))
            else:
                _remove(w, k)                       # unidade a mais: sai
                changed = True
        out_units = []
        for w, k, e, u in matched:
            name = item_name(e.get("id"))
            cur = _unit_from(name, e.get("data"))
            if cur["d"] is not None and u.get("d") is not None and cur["d"] > u["d"] + EPS:
                e["data"] = dict(e.get("data") or {}, **{DUR_KEY: round(float(u["d"]), 2)})
                cur["d"] = float(u["d"])
                changed = True
            out_units.append(cur)
        new_units[fam] = out_units

    # --- almas: as que leva + as dos minoculus no inventário ---------------------
    entries = _inv_entries(inv)
    pool = almas["total"] + sum(n for _e, n in _item_souls(entries))
    excess = pool - led.souls
    if excess > 0:
        changed = True
        cut = min(almas["total"], excess)
        almas["total"] -= cut
        excess -= cut
        for e, n in _item_souls(entries):
            if excess <= 0:
                break
            c2 = min(n, excess)
            excess -= c2
            data = dict(e["data"])
            if n - c2 > 0:
                data["almas"] = n - c2
            else:
                data.pop("almas", None)
            if data:
                e["data"] = data
            else:
                e.pop("data", None)
        pool = led.souls
    keep = points_for(almas["total"])
    if len(almas["pontos"]) > keep:
        almas["pontos"] = almas["pontos"][:keep]
        changed = True

    led.items = Counter({k: v for k, v in new_items.items() if v > 0})
    led.units = {k: v for k, v in new_units.items() if v}
    led.souls = max(0, int(pool))
    led.layout = inv
    led.almas = almas
    led.dirty = True
    return inv, almas, changed


_ORDER = list(range(HOTBAR_START, SLOT_COUNT)) + list(range(0, HOTBAR_START))


def _put(slots, name, count, data=None) -> int:
    """Põe ``count`` de ``name`` nos slots (junta às pilhas, depois os livres,
    a barra primeiro). Devolve o que não coube."""
    cap = max_stack_for(name)
    if data is None:
        for i in _ORDER:
            e = slots[i]
            if count <= 0:
                return 0
            if e is not None and item_name(e.get("id")) == name and not e.get("data"):
                room = cap - _entry_count(e)
                if room > 0:
                    n = min(room, count)
                    e["count"] = _entry_count(e) + n
                    count -= n
    for i in _ORDER:
        if count <= 0:
            return 0
        if slots[i] is None:
            n = min(cap, count)
            slots[i] = {"id": item_ref(name), "count": n, **({"data": data} if data else {})}
            count -= n
    return count


def give(led: Ledger, name, count) -> None:
    """Dá um item (itens iniciais): entra no livro e no inventário com o nome
    certo (ex.: balde de água, que no livro conta como balde)."""
    led.add(name, count)
    inv = _clean_inventory(led.layout)
    _put(inv["slots"], name, int(count))
    led.layout = inv


def reconcile(led: Ledger) -> dict:
    """Inventário para entregar ao entrar: o último aceite + o que o livro
    tem a mais (ganho depois da última gravação) nos slots livres."""
    inv = _clean_inventory(led.layout)
    almas = _clean_almas(led.almas)
    have_items: Counter = Counter()
    have_units: Dict[str, List[dict]] = {}
    for _w, _k, e in _inv_entries(inv):
        name = item_name(e.get("id"))
        fam = family(name)
        if is_unit(fam):
            have_units.setdefault(fam, []).append(_unit_from(name, e.get("data")))
        else:
            have_items[fam] += _entry_count(e)
    slots = inv["slots"]

    def put(name, count, data=None):
        return _put(slots, name, count, data)

    for fam, n in led.items.items():
        extra = int(n) - int(have_items.get(fam, 0))
        if extra > 0:
            put(fam, extra)
    for fam, lst in led.units.items():
        have = list(have_units.get(fam, []))
        for u in lst:
            j = _best_unit(have, u) if have else None
            if j is not None and (not u.get("c") or have[j].get("c") == u.get("c")):
                have.pop(j)
                continue
            data = {}
            top = max_dur(fam)
            if u.get("d") is not None and top > 0 and u["d"] < top - EPS:
                data[DUR_KEY] = round(float(u["d"]), 2)
            if u.get("c"):
                data["container"] = u["c"]
            put(fam, 1, data or None)
    pool = almas["total"] + sum(n for _e, n in _item_souls(_inv_entries(inv)))
    if led.souls > pool:
        almas["total"] += led.souls - pool
    led.layout = inv
    led.almas = almas
    return inv


# ---------------------------------------------------------------- baús
def entries_bag(entries) -> Tuple[Counter, Dict[str, List[dict]], int]:
    """(itens que empilham, unidades, almas) de uma lista de conteúdo de baú."""
    items: Counter = Counter()
    units: Dict[str, List[dict]] = {}
    souls = 0
    for e in entries or []:
        if not isinstance(e, (list, tuple)) or len(e) < 2:
            continue
        name = item_name(e[0])
        if not name:
            continue
        try:
            n = max(0, int(e[1]))
        except (TypeError, ValueError):
            continue
        data = e[2] if len(e) > 2 and isinstance(e[2], dict) else None
        fam = family(name)
        if is_unit(fam):
            units.setdefault(fam, []).extend(_unit_from(name, data) for _ in range(min(n, 1)))
        else:
            items[fam] += n
        if data:
            try:
                souls += max(0, int(data.get("almas", 0) or 0))
            except (TypeError, ValueError):
                pass
    return items, units, souls


def clean_entries(entries, size) -> list:
    out = []
    for e in list(entries or [])[:size]:
        name = item_name(e[0]) if isinstance(e, (list, tuple)) and len(e) >= 2 else None
        try:
            n = int(e[1]) if name else 0
        except (TypeError, ValueError):
            n = 0
        if not name or n <= 0:
            out.append(None)
            continue
        n = min(n, max_stack_for(name))
        if is_unit(family(name)):
            n = 1
        item = [item_ref(name), n]
        if len(e) > 2 and isinstance(e[2], dict) and e[2]:
            item.append(dict(e[2]))
        out.append(item)
    return out


def transfer(led: Ledger, old_entries, new_entries, size, self_cid=None) -> Tuple[list, bool]:
    """O jogador mexeu num baú: ``old`` é o que o servidor tem, ``new`` o que
    o jogo diz que ficou. O que entrou no baú sai do livro (só o que o jogador
    tem); o que saiu do baú entra no livro. Devolve (conteúdo final, mudou?)."""
    new = clean_entries(new_entries, size)
    old = clean_entries(old_entries, max(size, len(old_entries or [])))
    changed = len(new) != len(list(new_entries or [])[:size]) or any(
        (a is None) != (b is None) for a, b in zip(new, list(new_entries or [])[:size]))
    o_items, o_units, _ = entries_bag(old)
    # --- unidades: casar as que ficaram (mesmo conteúdo / durabilidade) --------
    left_old: Dict[str, List[dict]] = {k: list(v) for k, v in o_units.items()}
    deposits = []                                   # (índice em new, nome, unidade)
    for i, e in enumerate(new):
        if e is None:
            continue
        name = item_name(e[0])
        fam = family(name)
        if not is_unit(fam):
            continue
        data = e[2] if len(e) > 2 else None
        u = _unit_from(name, data)
        if self_cid and u.get("c") == self_cid:     # um baú dentro dele mesmo
            new[i] = None
            changed = True
            continue
        lst = left_old.get(fam, [])
        j = next((k for k, v in enumerate(lst)
                  if v.get("c") == u.get("c") and abs((v.get("d") or 0) - (u.get("d") or 0)) < 0.01),
                 None)
        if j is not None:
            lst.pop(j)
        else:
            deposits.append((i, name, u))
    # retiradas de unidades: entram no livro
    for fam, lst in left_old.items():
        for u in lst:
            led.units.setdefault(fam, []).append(dict(u))
            led.dirty = True
    for i, name, u in deposits:
        got = led.take_unit(name, u)
        if got is None:
            new[i] = None                           # não tinha: não entra no baú
            changed = True
            continue
        data = dict(new[i][2]) if len(new[i]) > 2 else {}
        if u.get("c") and got.get("c") != u.get("c"):
            data = _strip_container(data) or {}
            changed = True
        if got.get("c"):
            data["container"] = got["c"]
        if u.get("d") is not None and got.get("d") is not None and u["d"] > got["d"] + EPS:
            data[DUR_KEY] = round(float(got["d"]), 2)
            changed = True
        new[i] = [new[i][0], 1] + ([data] if data else [])
    # --- itens que empilham ---------------------------------------------------------
    n_items, _nu, _ = entries_bag(new)
    for fam in set(o_items) | set(n_items):
        delta = int(n_items.get(fam, 0)) - int(o_items.get(fam, 0))
        if delta < 0:
            led.items[fam] += -delta
            led.dirty = True
        elif delta > 0:
            got = led.take(fam, delta)
            short = delta - got
            if short > 0:
                changed = True
                for i in range(len(new) - 1, -1, -1):
                    e = new[i]
                    if short <= 0:
                        break
                    if e is None or family(item_name(e[0])) != fam:
                        continue
                    cut = min(int(e[1]), short)
                    short -= cut
                    if cut >= int(e[1]):
                        new[i] = None
                    else:
                        new[i] = [e[0], int(e[1]) - cut] + list(e[2:])
    # --- almas dentro de itens (minoculus guardado num baú) -------------------------
    _a, _b, o_souls = entries_bag(old)
    _c, _d, n_souls = entries_bag(new)
    if n_souls > o_souls:
        need = n_souls - o_souls
        got = led.take_souls(need)
        if got < need:
            changed = True
            short = need - got
            for i in range(len(new) - 1, -1, -1):
                e = new[i]
                if short <= 0:
                    break
                if e is None or len(e) < 3 or not int(e[2].get("almas", 0) or 0):
                    continue
                n = int(e[2]["almas"])
                cut = min(n, short)
                short -= cut
                data = dict(e[2])
                if n - cut > 0:
                    data["almas"] = n - cut
                else:
                    data.pop("almas", None)
                new[i] = [e[0], e[1]] + ([data] if data else [])
    elif o_souls > n_souls:
        led.add_souls(o_souls - n_souls)
    return new, changed


# ---------------------------------------------------------------- perfil de combate
# O que o jogador consegue fazer com o que TEM (livro + pontos de almas): o
# Node usa isto para limitar o dano que o jogo diz que deu e a velocidade.
PUNCH_FACTOR = 1.0
BONUS_PER_POINT = {"vida": 1.0, "forca": 60.0, "velocidade": 0.5, "estamina": 10.0,
                   "attack_speed": 0.14}
BASE_ATTACK_SPEED = 2.0
MAX_ATTACK_RATE = 3.0
SPEED_SAFETY_CAP = 8.0        # player.py: correr acelera até 8x a base
FLY_SPEED_MULT = 1.6
FASTEST_SWING = 0.45 * 0.5    # o golpe mais curto (soco, combo cedo do dual), s a 1x
_ECO_CACHE = {"mtime": None, "data": {}}


def _economia() -> dict:
    import json
    import os
    import items as _items
    path = os.path.join(os.path.dirname(os.path.abspath(_items.__file__)), "economia.json")
    try:
        m = os.path.getmtime(path)
        if _ECO_CACHE["mtime"] != m:
            with open(path, encoding="utf-8") as fp:
                _ECO_CACHE.update(mtime=m, data=json.load(fp))
    except (OSError, ValueError):
        pass
    return _ECO_CACHE["data"]


def _combat_mult() -> float:
    """O maior multiplicador de um golpe (finalizador x carregado/contra/correndo)."""
    try:
        import game_config
        import os
        cfg = game_config.read_json(os.path.join(os.path.dirname(os.path.abspath(
            __import__("items").__file__)), "combate.json"), {})
    except Exception:
        cfg = {}
    fin = max([float(e.get("finalizador", 1.0)) for e in (cfg.get("estilos") or {}).values()] or [1.5])
    extra = [float((cfg.get("correndo") or {}).get("dano", 1.25)),
             float((cfg.get("contra_ataque") or {}).get("dano", 1.5))]
    extra += [float((e.get("carregado") or {}).get("dano", 1.0))
              for e in (cfg.get("estilos") or {}).values()]
    return max(1.0, fin) * max([1.0] + extra)


def _factor(name):
    import item_balancing
    st = item_balancing.get_stats(name) or {}
    if "fator" in st:
        return max(0.0, float(st["fator"]))
    if st.get("dano"):
        from physics_units import BASE_STRENGTH_N, strike_damage
        return PUNCH_FACTOR + float(st["dano"]) / max(strike_damage(BASE_STRENGTH_N), 1e-6)
    return None


def profile(led: Ledger) -> dict:
    from items import item_weight
    from physics_units import kinetic_energy, weapon_damage
    import item_balancing
    base = {"vida_max": 10, "forca": 600, "velocidade": 10, "attack_speed": 2,
            **{k: v for k, v in (_economia().get("status_base") or {}).items()}}
    pts = Counter((led.almas or {}).get("pontos") or [])

    def num(k):
        try:
            return float(str(base.get(k)).replace(",", "."))
        except (TypeError, ValueError):
            return 0.0
    forca = num("forca") + BONUS_PER_POINT["forca"] * pts["forca"]
    vida = num("vida_max") + BONUS_PER_POINT["vida"] * pts["vida"]
    vel = num("velocidade") + BONUS_PER_POINT["velocidade"] * pts["velocidade"]
    rate = max(0.05, (num("attack_speed") + BONUS_PER_POINT["attack_speed"] * pts["attack_speed"])
               / BASE_ATTACK_SPEED)
    carried = 0.0
    names = set(led.items)
    for fam, n in led.items.items():
        carried += item_weight(fam) * n
    for fam, units in led.units.items():
        carried += item_weight(fam) * len(units)
        if units:
            names.add(fam)
    mult = _combat_mult() * 1.1                     # folga para arredondamentos

    def hit(f, wname=None):
        spd = 1.0
        if wname:
            try:
                spd = max(0.05, float(item_balancing.get_stat(wname, "velocidade", 1.0)))
            except (TypeError, ValueError):
                spd = 1.0
        r = min(MAX_ATTACK_RATE, spd * rate)
        return {"dano": round(weapon_damage(forca, f, carried, vida) * mult, 2),
                "intervalo": round(FASTEST_SWING / r * 0.8, 3)}

    armas = {}
    voo = False
    for name in names:
        f = _factor(name)
        if f is not None and f > 0:
            armas[name] = hit(f, name)
        t = get_item_type(name)
        if t is not None and t.raw.get("voo"):
            voo = True
    vmax = vel * SPEED_SAFETY_CAP * (FLY_SPEED_MULT if voo else 1.0) * 1.15
    # encontrão no máximo: energia do corpo a toda a velocidade (2 unidades = 1 m)
    impacto = kinetic_energy(40.0 + carried, vmax / 2.0) * 0.08 / 100.0
    return {"forca": forca, "vida_max": vida, "velocidade": vel, "ritmo": rate,
            "soco": hit(PUNCH_FACTOR), "armas": armas, "voo": voo,
            "vmax": round(vmax, 2), "impacto": round(min(200.0, max(50.0, impacto)), 2),
            "carga": round(carried, 1)}


__all__ = ["profile", "give", "Ledger", "check_inventory", "reconcile", "ledger_from_inventory", "transfer",
           "clean_entries", "entries_bag", "item_name", "family", "is_unit", "points_for",
           "item_id_for_block"]
