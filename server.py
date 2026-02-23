import asyncio
import json
import random
import itertools
import time
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional, Any, Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse


# ------------------ Tables ------------------

TABLES = [
    "МІДТАУН","ФЕЛБЛОК","ГАРЛЕМ","РІВЕРСАЙД","ОКСМІР","ІНДЕЙЛ","ХАРБОР","ХІЛЛФОРД","БРАЙТОН","ЯРВІК",
    "ХАЙТС","ГРЕЙРОК","НОРТБРІДЖ","БЕЙСАЙД","КРОСБІ","ІСТ-ТАУН","САУЗБРІДЖ","ГРІНВЕЙ","ТОРВІК","ДАУНТАУН",
    "БРУКЛІН","ЛІБЕРТІ","ЕШПАРК","СОХО","ВЕСТ-САЙД","АЙРОНХІЛЛ","САУЗГЕЙТ","ФЕЙРМОНТ","ХАЙЛЕНД","ТВІНС",
]


# ------------------ Cards / Evaluation ------------------

RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "T", "J", "Q", "K", "A"]
SUITS = ["♣", "♦", "♥", "♠"]
RANK_VALUE = {r: i for i, r in enumerate(RANKS, start=2)}


def card_str(card: Tuple[str, str]) -> str:
    return f"{card[0]}{card[1]}"


def straight_high(values: List[int]) -> Optional[int]:
    n = len(values)
    v = sorted(set(values))
    if len(v) != n:
        return None
    if n == 5 and v == [2, 3, 4, 5, 14]:
        return 5
    if max(v) - min(v) == n - 1:
        return max(v)
    return None


CAT_2 = {"HIGH": 1, "PAIR": 2}
CAT_3 = {"HIGH": 1, "PAIR": 2, "TRIPS": 3, "FLUSH": 4, "STRAIGHT": 5}
CAT_4 = {"HIGH": 1, "PAIR": 2, "TWO_PAIR": 3, "TRIPS": 4, "FLUSH": 5, "STRAIGHT": 6, "QUADS": 7}
CAT_5 = {
    "HIGH": 1,
    "PAIR": 2,
    "TWO_PAIR": 3,
    "TRIPS": 4,
    "STRAIGHT": 5,
    "FLUSH": 6,
    "FULL_HOUSE": 7,
    "QUADS": 8,
    "STRAIGHT_FLUSH": 9,
}


def eval_strict(cards: List[Tuple[str, str]]) -> Tuple[int, Tuple[int, ...], str]:
    n = len(cards)
    if not (2 <= n <= 5):
        raise ValueError("Потрібно 2–5 карт")

    ranks = [r for r, _ in cards]
    suits = [s for _, s in cards]

    cnt: Dict[str, int] = {}
    for r in ranks:
        cnt[r] = cnt.get(r, 0) + 1
    items = sorted(cnt.items(), key=lambda kv: (kv[1], RANK_VALUE[kv[0]]), reverse=True)

    is_flush = len(set(suits)) == 1
    values = [RANK_VALUE[r] for r in ranks]
    sh = straight_high(values)

    if n == 2:
        if items[0][1] == 2:
            pair = RANK_VALUE[items[0][0]]
            return CAT_2["PAIR"], (pair,), "Пара"
        return CAT_2["HIGH"], tuple(sorted(values, reverse=True)), "Старші карти"

    if n == 3:
        if items[0][1] == 3:
            trip = RANK_VALUE[items[0][0]]
            return CAT_3["TRIPS"], (trip,), "Трійка"
        if items[0][1] == 2:
            pair = RANK_VALUE[items[0][0]]
            kicker = max(RANK_VALUE[r] for r, c in items if c == 1)
            return CAT_3["PAIR"], (pair, kicker), "Пара"
        if sh is not None:
            return CAT_3["STRAIGHT"], (sh,), "Стріт (3)"
        if is_flush:
            return CAT_3["FLUSH"], tuple(sorted(values, reverse=True)), "Флеш (3)"
        return CAT_3["HIGH"], tuple(sorted(values, reverse=True)), "Старші карти"

    if n == 4:
        if items[0][1] == 4:
            quad = RANK_VALUE[items[0][0]]
            return CAT_4["QUADS"], (quad,), "Каре"
        if items[0][1] == 3:
            trip = RANK_VALUE[items[0][0]]
            kicker = max(RANK_VALUE[r] for r, c in items if c == 1)
            return CAT_4["TRIPS"], (trip, kicker), "Трійка"
        if items[0][1] == 2 and items[1][1] == 2:
            p1, p2 = RANK_VALUE[items[0][0]], RANK_VALUE[items[1][0]]
            hi, lo = max(p1, p2), min(p1, p2)
            kicker = max(RANK_VALUE[r] for r, c in items if c == 1)
            return CAT_4["TWO_PAIR"], (hi, lo, kicker), "Дві пари"
        if items[0][1] == 2:
            pair = RANK_VALUE[items[0][0]]
            kickers = sorted((RANK_VALUE[r] for r, c in items if c == 1), reverse=True)
            return CAT_4["PAIR"], (pair, *kickers), "Пара"
        if sh is not None:
            return CAT_4["STRAIGHT"], (sh,), "Стріт (4)"
        if is_flush:
            return CAT_4["FLUSH"], tuple(sorted(values, reverse=True)), "Флеш (4)"
        return CAT_4["HIGH"], tuple(sorted(values, reverse=True)), "Старші карти"

    # n == 5
    if sh is not None and is_flush:
        return CAT_5["STRAIGHT_FLUSH"], (sh,), "Стріт-флеш"
    if items[0][1] == 4:
        quad = RANK_VALUE[items[0][0]]
        kicker = max(RANK_VALUE[r] for r, c in items if c == 1)
        return CAT_5["QUADS"], (quad, kicker), "Каре"
    if items[0][1] == 3 and items[1][1] == 2:
        trip = RANK_VALUE[items[0][0]]
        pair = RANK_VALUE[items[1][0]]
        return CAT_5["FULL_HOUSE"], (trip, pair), "Фул-хаус"
    if is_flush:
        return CAT_5["FLUSH"], tuple(sorted(values, reverse=True)), "Флеш"
    if sh is not None:
        return CAT_5["STRAIGHT"], (sh,), "Стріт"
    if items[0][1] == 3:
        trip = RANK_VALUE[items[0][0]]
        kickers = sorted((RANK_VALUE[r] for r, c in items if c == 1), reverse=True)
        return CAT_5["TRIPS"], (trip, *kickers), "Трійка"
    if items[0][1] == 2 and items[1][1] == 2:
        p1, p2 = RANK_VALUE[items[0][0]], RANK_VALUE[items[1][0]]
        hi, lo = max(p1, p2), min(p1, p2)
        kicker = max(RANK_VALUE[r] for r, c in items if c == 1)
        return CAT_5["TWO_PAIR"], (hi, lo, kicker), "Дві пари"
    if items[0][1] == 2:
        pair = RANK_VALUE[items[0][0]]
        kickers = sorted((RANK_VALUE[r] for r, c in items if c == 1), reverse=True)
        return CAT_5["PAIR"], (pair, *kickers), "Пара"
    return CAT_5["HIGH"], tuple(sorted(values, reverse=True)), "Старша карта"


# ------------------ Hints ------------------

def find_pairs_trips_quads(hand_cards: List[Tuple[str, str]]) -> Dict[str, List[List[Tuple[str, str]]]]:
    by_rank: Dict[str, List[Tuple[str, str]]] = {}
    for c in hand_cards:
        by_rank.setdefault(c[0], []).append(c)
    out = {"pairs": [], "trips": [], "quads": []}
    for _, cards in by_rank.items():
        if len(cards) >= 2:
            out["pairs"].extend([list(x) for x in itertools.combinations(cards, 2)])
        if len(cards) >= 3:
            out["trips"].extend([list(x) for x in itertools.combinations(cards, 3)])
        if len(cards) >= 4:
            out["quads"].extend([list(x) for x in itertools.combinations(cards, 4)])
    return out


def find_straights_5(hand_cards: List[Tuple[str, str]]) -> List[List[Tuple[str, str]]]:
    by_rank: Dict[int, List[Tuple[str, str]]] = {}
    for r, s in hand_cards:
        by_rank.setdefault(RANK_VALUE[r], []).append((r, s))
    straights: List[List[Tuple[str, str]]] = []

    def pick(seq_vals: List[int]) -> List[Tuple[str, str]]:
        return [by_rank[v][0] for v in seq_vals]

    for start in range(2, 11):
        seq = list(range(start, start + 5))
        if all(v in by_rank for v in seq):
            straights.append(pick(seq))

    wheel = [14, 2, 3, 4, 5]
    if all(v in by_rank for v in wheel):
        straights.append(pick(wheel))

    return straights


def find_flushes_5plus(hand_cards: List[Tuple[str, str]]) -> List[List[Tuple[str, str]]]:
    by_suit: Dict[str, List[Tuple[str, str]]] = {}
    for c in hand_cards:
        by_suit.setdefault(c[1], []).append(c)
    out: List[List[Tuple[str, str]]] = []
    for _, cards in by_suit.items():
        if len(cards) >= 5:
            combos = list(itertools.combinations(cards, 5))[:25]
            out.extend([list(x) for x in combos])
    return out


# ------------------ Room / Players / Card instances ------------------

@dataclass
class CardInst:
    id: int
    rank: str
    suit: str

    def as_tuple(self) -> Tuple[str, str]:
        return (self.rank, self.suit)

    def as_text(self) -> str:
        return f"{self.rank}{self.suit}"

    def to_json(self) -> Dict[str, Any]:
        return {"id": self.id, "c": self.as_text()}


@dataclass
class Player:
    pid: str
    name: str
    hand: List[CardInst] = field(default_factory=list)
    archive: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class Play:
    pid: str
    name: str
    table: str
    card_ids: List[int]
    cards_text: List[str]
    cat: int
    tb: Tuple[int, ...]
    label: str
    placed_ms: int
    placed_seq: int

    def strength_key(self) -> Tuple[int, Tuple[int, ...]]:
        return (self.cat, self.tb)

    def to_public(self) -> Dict[str, Any]:
        return {
            "pid": self.pid,
            "name": self.name,
            "table": self.table,
            "cards": self.cards_text,
            "cat": self.cat,
            "tb": list(self.tb),
            "label": self.label,
            "placed_ms": self.placed_ms,
            "placed_seq": self.placed_seq,
        }


@dataclass
class Room:
    room_id: str
    players: Dict[str, Player] = field(default_factory=dict)
    sockets: Dict[str, WebSocket] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    pending: Dict[str, List[Play]] = field(default_factory=lambda: {t: [] for t in TABLES})
    battle_history: List[Dict[str, Any]] = field(default_factory=list)
    round_no: int = 0

    ready_pids: Set[str] = field(default_factory=set)
    play_seq: int = 0

    next_card_id: int = 1

    def can_join(self) -> bool:
        return len(self.players) < 6

    def new_card(self, rank: str, suit: str) -> CardInst:
        cid = self.next_card_id
        self.next_card_id += 1
        return CardInst(id=cid, rank=rank, suit=suit)


ROOMS: Dict[str, Room] = {}


def get_room(room_id: str) -> Room:
    if room_id not in ROOMS:
        ROOMS[room_id] = Room(room_id=room_id)
    return ROOMS[room_id]


def random_unique_cards(n: int) -> List[Tuple[str, str]]:
    if n < 0:
        raise ValueError("N має бути ≥ 0")
    if n > 52:
        raise ValueError("За одну роздачу максимум 52 унікальні карти")
    deck = [(r, s) for r in RANKS for s in SUITS]
    batch = random.sample(deck, n)
    if len(set(batch)) != len(batch):
        raise RuntimeError("ПОМИЛКА: дубль у межах однієї роздачі")
    return batch


def parse_card_text(t: str) -> Optional[Tuple[str, str]]:
    t = (t or "").strip()
    if not t:
        return None
    suit_map = {
        "c": "♣", "♣": "♣",
        "d": "♦", "♦": "♦",
        "h": "♥", "♥": "♥",
        "s": "♠", "♠": "♠",
    }
    s = t[-1].lower()
    if s not in suit_map:
        return None
    suit = suit_map[s]

    rank_part = t[:-1].strip()
    rank = "T" if rank_part == "10" else rank_part.upper()
    if rank not in RANKS:
        return None
    return (rank, suit)


def involved_pids(room: Room) -> Set[str]:
    s: Set[str] = set()
    for t in TABLES:
        for p in room.pending.get(t, []):
            s.add(p.pid)
    return s


def used_card_ids_in_round(room: Room, pid: str) -> Set[int]:
    used: Set[int] = set()
    for t in TABLES:
        for play in room.pending.get(t, []):
            if play.pid == pid:
                used.update(play.card_ids)
    return used


def room_snapshot_for(room: Room, viewer_pid: str) -> Dict[str, Any]:
    plist = []
    for p in room.players.values():
        plist.append({
            "pid": p.pid,
            "name": p.name,
            "hand": [c.to_json() for c in p.hand],  # ✅ now objects {id,c}
            "archive": p.archive,
        })

    my_pending: Dict[str, List[Dict[str, Any]]] = {t: [] for t in TABLES}
    for t in TABLES:
        for play in room.pending.get(t, []):
            if play.pid == viewer_pid:
                my_pending[t].append(play.to_public())

    inv = involved_pids(room)
    last_round = room.battle_history[-1] if room.battle_history else None

    return {
        "room": room.room_id,
        "tables": TABLES,
        "round_no": room.round_no,
        "players": plist,

        "my_pending": my_pending,
        "last_round": last_round,
        "battle_history": room.battle_history[-20:],

        "involved_count": len(inv),
        "ready_count": len(inv.intersection(room.ready_pids)),
        "you_ready": (viewer_pid in room.ready_pids),
    }


async def broadcast(room: Room) -> None:
    dead: List[str] = []
    for pid, ws in room.sockets.items():
        try:
            snap = room_snapshot_for(room, pid)
            await ws.send_text(json.dumps({"type": "state", "state": snap}, ensure_ascii=False))
        except Exception:
            dead.append(pid)
    for pid in dead:
        room.sockets.pop(pid, None)


def resolve_round(room: Room) -> Dict[str, Any]:
    room.round_no += 1
    round_id = room.round_no

    tables_out: List[Dict[str, Any]] = []

    # collect all played card ids per pid -> remove after round end
    remove_by_pid: Dict[str, Set[int]] = {}

    for t in TABLES:
        plays = room.pending.get(t, [])
        if not plays:
            continue

        best_strength = max(p.strength_key() for p in plays)
        best_plays = [p for p in plays if p.strength_key() == best_strength]

        # tie-break: earlier placed wins
        winner = min(best_plays, key=lambda p: p.placed_seq)

        tables_out.append({
            "table": t,
            "plays": [p.to_public() for p in plays],
            "winner": winner.to_public(),
        })

        for p in plays:
            remove_by_pid.setdefault(p.pid, set()).update(p.card_ids)

    # ✅ remove cards from hands ONLY NOW (after round end)
    for pid, ids in remove_by_pid.items():
        if pid not in room.players:
            continue
        pl = room.players[pid]
        pl.hand = [c for c in pl.hand if c.id not in ids]

    round_rec = {"round": round_id, "tables": tables_out}
    room.battle_history.append(round_rec)

    room.pending = {t: [] for t in TABLES}
    room.ready_pids.clear()
    return round_rec


def maybe_finish_round(room: Room) -> Optional[Dict[str, Any]]:
    inv = involved_pids(room)
    if not inv:
        return None
    if inv.issubset(room.ready_pids):
        return resolve_round(room)
    return None


# ------------------ FastAPI ------------------

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def root():
    return FileResponse("static/index.html")


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()

    pid: Optional[str] = None
    room: Optional[Room] = None

    try:
        raw = await ws.receive_text()
        msg = json.loads(raw)

        if msg.get("type") != "join":
            await ws.send_text(json.dumps({"type": "error", "message": "Перше повідомлення має бути типу join"}, ensure_ascii=False))
            return

        room_id = (msg.get("room") or "default").strip()
        name = (msg.get("name") or "Гравець").strip()[:20]
        pid = (msg.get("pid") or "").strip() or f"p{random.randint(100000, 999999)}"

        room = get_room(room_id)

        async with room.lock:
            if pid not in room.players:
                if not room.can_join():
                    await ws.send_text(json.dumps({"type": "error", "message": "Кімната заповнена (макс. 6 гравців)"}, ensure_ascii=False))
                    return
                room.players[pid] = Player(pid=pid, name=name)
            else:
                room.players[pid].name = name

            room.sockets[pid] = ws

        await ws.send_text(json.dumps({"type": "joined", "pid": pid, "room": room_id}, ensure_ascii=False))

        async with room.lock:
            await broadcast(room)

        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)
            t = msg.get("type")

            async with room.lock:
                if pid not in room.players:
                    continue
                player = room.players[pid]

                if t == "deal":
                    n = int(msg.get("n", 0))
                    if n < 0:
                        await ws.send_text(json.dumps({"type": "error", "message": "N має бути ≥ 0"}, ensure_ascii=False))
                        continue
                    if n > 52:
                        await ws.send_text(json.dumps({"type": "error", "message": "За одну роздачу максимум 52 унікальні карти"}, ensure_ascii=False))
                        continue
                    for r, s in random_unique_cards(n):
                        player.hand.append(room.new_card(r, s))

                elif t == "deal_all":
                    n = int(msg.get("n", 0))
                    if n < 0:
                        await ws.send_text(json.dumps({"type": "error", "message": "N має бути ≥ 0"}, ensure_ascii=False))
                        continue
                    if n > 52:
                        await ws.send_text(json.dumps({"type": "error", "message": "За одну роздачу максимум 52 унікальні карти"}, ensure_ascii=False))
                        continue
                    for p in room.players.values():
                        for r, s in random_unique_cards(n):
                            p.hand.append(room.new_card(r, s))

                elif t == "add_manual":
                    ctxt = msg.get("card", "")
                    parsed = parse_card_text(ctxt)
                    if parsed is None:
                        await ws.send_text(json.dumps({"type": "error", "message": f"Невірна карта: {ctxt}"}, ensure_ascii=False))
                        continue
                    r, s = parsed
                    player.hand.append(room.new_card(r, s))

                elif t == "clear_hand":
                    player.hand.clear()

                elif t == "eval_selected":
                    card_ids = msg.get("card_ids", [])
                    if not isinstance(card_ids, list):
                        await ws.send_text(json.dumps({"type": "error", "message": "card_ids має бути списком"}, ensure_ascii=False))
                        continue
                    card_ids = [int(x) for x in card_ids]
                    if not (2 <= len(card_ids) <= 5):
                        await ws.send_text(json.dumps({"type": "error", "message": "Виберіть 2–5 карт"}, ensure_ascii=False))
                        continue

                    lookup = {c.id: c for c in player.hand}
                    if any(cid not in lookup for cid in card_ids):
                        await ws.send_text(json.dumps({"type": "error", "message": "Деяких карт уже немає в руці"}, ensure_ascii=False))
                        continue

                    cards = [lookup[cid].as_tuple() for cid in card_ids]
                    cat, tb, label = eval_strict(cards)

                    await ws.send_text(json.dumps({
                        "type": "eval_result",
                        "cards": [card_str(c) for c in cards],
                        "label": label,
                        "cat": cat,
                        "tb": tb,
                    }, ensure_ascii=False))
                    continue

                elif t == "play_selected":
                    table = (msg.get("table") or "").strip()
                    if table not in TABLES:
                        await ws.send_text(json.dumps({"type": "error", "message": "Невірний стіл"}, ensure_ascii=False))
                        continue

                    card_ids = msg.get("card_ids", [])
                    if not isinstance(card_ids, list):
                        await ws.send_text(json.dumps({"type": "error", "message": "card_ids має бути списком"}, ensure_ascii=False))
                        continue
                    card_ids = [int(x) for x in card_ids]
                    if not (2 <= len(card_ids) <= 5):
                        await ws.send_text(json.dumps({"type": "error", "message": "Виберіть 2–5 карт"}, ensure_ascii=False))
                        continue

                    lookup = {c.id: c for c in player.hand}
                    if any(cid not in lookup for cid in card_ids):
                        await ws.send_text(json.dumps({"type": "error", "message": "Деяких карт уже немає в руці"}, ensure_ascii=False))
                        continue

                    # ✅ don’t allow reuse of same card in multiple plays in same round
                    used = used_card_ids_in_round(room, player.pid)
                    if any(cid in used for cid in card_ids):
                        await ws.send_text(json.dumps({"type": "error", "message": "Не можна використати ту саму карту двічі в одному раунді"}, ensure_ascii=False))
                        continue

                    cards_tuples = [lookup[cid].as_tuple() for cid in card_ids]
                    cat, tb, label = eval_strict(cards_tuples)

                    cards_text = [lookup[cid].as_text() for cid in card_ids]

                    # if player changes plays, they are no longer ready
                    room.ready_pids.discard(player.pid)

                    room.play_seq += 1
                    now_ms = int(time.time() * 1000)

                    room.pending[table].append(Play(
                        pid=player.pid,
                        name=player.name,
                        table=table,
                        card_ids=card_ids,
                        cards_text=cards_text,
                        cat=cat,
                        tb=tb,
                        label=label,
                        placed_ms=now_ms,
                        placed_seq=room.play_seq,
                    ))

                elif t == "end_round_vote":
                    room.ready_pids.add(player.pid)
                    rr = maybe_finish_round(room)
                    if rr is not None:
                        for _pid, _ws in list(room.sockets.items()):
                            try:
                                await _ws.send_text(json.dumps({"type": "round_result", "round": rr}, ensure_ascii=False))
                            except Exception:
                                pass

                elif t == "end_round_force":
                    rr = resolve_round(room)
                    for _pid, _ws in list(room.sockets.items()):
                        try:
                            await _ws.send_text(json.dumps({"type": "round_result", "round": rr}, ensure_ascii=False))
                        except Exception:
                            pass

                elif t == "hints":
                    # hints for current hand (cards still present until round ends)
                    hand_cards = [c.as_tuple() for c in player.hand]
                    pq = find_pairs_trips_quads(hand_cards)
                    straights = find_straights_5(hand_cards)
                    flushes = find_flushes_5plus(hand_cards)

                    await ws.send_text(json.dumps({
                        "type": "hints_result",
                        "count": len(player.hand),
                        "pairs": [[card_str(c) for c in x] for x in pq["pairs"][:30]],
                        "trips": [[card_str(c) for c in x] for x in pq["trips"][:30]],
                        "quads": [[card_str(c) for c in x] for x in pq["quads"][:30]],
                        "straights5": [[card_str(c) for c in x] for x in straights[:30]],
                        "flushes5": [[card_str(c) for c in x] for x in flushes[:30]],
                    }, ensure_ascii=False))
                    continue

                elif t == "leave":
                    break

                else:
                    await ws.send_text(json.dumps({"type": "error", "message": f"Невідомий тип повідомлення: {t}"}, ensure_ascii=False))
                    continue

                await broadcast(room)

    except WebSocketDisconnect:
        pass
    finally:
        if room is not None and pid is not None:
            async with room.lock:
                room.sockets.pop(pid, None)
                await broadcast(room)
