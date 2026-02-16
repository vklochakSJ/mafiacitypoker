import asyncio
import json
import random
import itertools
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional, Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse


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
    # Wheel A-2-3-4-5
    if n == 5 and v == [2, 3, 4, 5, 14]:
        return 5
    if max(v) - min(v) == n - 1:
        return max(v)
    return None


# Strict category ranks per size (higher = better).
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

    # n == 5 (standard)
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

def find_pairs_trips_quads(hand: List[Tuple[str, str]]) -> Dict[str, List[List[Tuple[str, str]]]]:
    by_rank: Dict[str, List[Tuple[str, str]]] = {}
    for c in hand:
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


def find_straights_5(hand: List[Tuple[str, str]]) -> List[List[Tuple[str, str]]]:
    by_rank: Dict[int, List[Tuple[str, str]]] = {}
    for r, s in hand:
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


def find_flushes_5plus(hand: List[Tuple[str, str]]) -> List[List[Tuple[str, str]]]:
    by_suit: Dict[str, List[Tuple[str, str]]] = {}
    for c in hand:
        by_suit.setdefault(c[1], []).append(c)
    out: List[List[Tuple[str, str]]] = []
    for _, cards in by_suit.items():
        if len(cards) >= 5:
            combos = list(itertools.combinations(cards, 5))[:25]
            out.extend([list(x) for x in combos])
    return out


# ------------------ Room / Players ------------------

@dataclass
class Player:
    pid: str
    name: str
    hand: List[Tuple[str, str]] = field(default_factory=list)
    archive: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class Room:
    room_id: str
    players: Dict[str, Player] = field(default_factory=dict)
    sockets: Dict[str, WebSocket] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def can_join(self) -> bool:
        return len(self.players) < 6


ROOMS: Dict[str, Room] = {}


def get_room(room_id: str) -> Room:
    if room_id not in ROOMS:
        ROOMS[room_id] = Room(room_id=room_id)
    return ROOMS[room_id]


def random_unique_cards(n: int) -> List[Tuple[str, str]]:
    """
    За ОДНУ роздачу (один натиск deal/deal_all) — без дублів карт.
    Дублі між різними роздачами все одно можливі (поведінка "кілька колод").
    """
    if n < 0:
        raise ValueError("N має бути ≥ 0")
    if n > 52:
        raise ValueError("За одну роздачу максимум 52 унікальні карти")

    deck = [(r, s) for r in RANKS for s in SUITS]  # 52 unique cards
    batch = random.sample(deck, n)

    # guard (на випадок помилок у майбутньому)
    if len(set(batch)) != len(batch):
        raise RuntimeError("ПОМИЛКА: дубль у межах однієї роздачі (цього не має статись)")

    return batch


def room_snapshot(room: Room) -> Dict[str, Any]:
    plist = []
    for p in room.players.values():
        plist.append({
            "pid": p.pid,
            "name": p.name,
            "hand": [card_str(c) for c in p.hand],
            "archive": p.archive,
        })
    return {"room": room.room_id, "players": plist}


async def broadcast(room: Room) -> None:
    snap = room_snapshot(room)
    msg = {"type": "state", "state": snap}
    dead: List[str] = []
    for pid, ws in room.sockets.items():
        try:
            await ws.send_text(json.dumps(msg, ensure_ascii=False))
        except Exception:
            dead.append(pid)
    for pid in dead:
        room.sockets.pop(pid, None)


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
            await ws.send_text(json.dumps(
                {"type": "error", "message": "Перше повідомлення має бути типу join"},
                ensure_ascii=False
            ))
            return

        room_id = (msg.get("room") or "default").strip()
        name = (msg.get("name") or "Гравець").strip()[:20]
        pid = (msg.get("pid") or "").strip() or f"p{random.randint(100000, 999999)}"

        room = get_room(room_id)

        async with room.lock:
            if pid not in room.players:
                if not room.can_join():
                    await ws.send_text(json.dumps(
                        {"type": "error", "message": "Кімната заповнена (макс. 6 гравців)"},
                        ensure_ascii=False
                    ))
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

                    player.hand.extend(random_unique_cards(n))

                elif t == "deal_all":
                    n = int(msg.get("n", 0))

                    if n < 0:
                        await ws.send_text(json.dumps({"type": "error", "message": "N має бути ≥ 0"}, ensure_ascii=False))
                        continue
                    if n > 52:
                        await ws.send_text(json.dumps({"type": "error", "message": "За одну роздачу максимум 52 унікальні карти"}, ensure_ascii=False))
                        continue

                    for p in room.players.values():
                        p.hand.extend(random_unique_cards(n))

                elif t == "add_manual":
                    ctxt = msg.get("card", "")
                    c = parse_card_text(ctxt)
                    if c is None:
                        await ws.send_text(json.dumps(
                            {"type": "error", "message": f"Невірна карта: {ctxt}"},
                            ensure_ascii=False
                        ))
                        continue
                    player.hand.append(c)

                elif t == "clear_hand":
                    player.hand.clear()

                elif t == "eval_selected":
                    idxs = msg.get("idxs", [])
                    if not isinstance(idxs, list):
                        await ws.send_text(json.dumps({"type": "error", "message": "idxs має бути списком"}, ensure_ascii=False))
                        continue
                    idxs = [int(i) for i in idxs]
                    if not (2 <= len(idxs) <= 5):
                        await ws.send_text(json.dumps({"type": "error", "message": "Виберіть 2–5 карт"}, ensure_ascii=False))
                        continue
                    if any(i < 0 or i >= len(player.hand) for i in idxs):
                        await ws.send_text(json.dumps({"type": "error", "message": "Невірний індекс карти"}, ensure_ascii=False))
                        continue

                    cards = [player.hand[i] for i in idxs]
                    cat, tb, label = eval_strict(cards)

                    await ws.send_text(json.dumps({
                        "type": "eval_result",
                        "cards": [card_str(c) for c in cards],
                        "label": label,
                        "cat": cat,
                        "tb": tb,
                    }, ensure_ascii=False))
                    continue

                elif t == "archive_selected":
                    idxs = msg.get("idxs", [])
                    if not isinstance(idxs, list):
                        await ws.send_text(json.dumps({"type": "error", "message": "idxs має бути списком"}, ensure_ascii=False))
                        continue
                    idxs = [int(i) for i in idxs]
                    if not (2 <= len(idxs) <= 5):
                        await ws.send_text(json.dumps({"type": "error", "message": "Виберіть 2–5 карт"}, ensure_ascii=False))
                        continue
                    if any(i < 0 or i >= len(player.hand) for i in idxs):
                        await ws.send_text(json.dumps({"type": "error", "message": "Невірний індекс карти"}, ensure_ascii=False))
                        continue

                    cards = [player.hand[i] for i in idxs]
                    cat, tb, label = eval_strict(cards)

                    for i in sorted(idxs, reverse=True):
                        del player.hand[i]

                    player.archive.append({
                        "cards": [card_str(c) for c in cards],
                        "label": label,
                        "cat": cat,
                        "tb": list(tb),
                    })

                elif t == "hints":
                    pairs_trips_quads = find_pairs_trips_quads(player.hand)
                    straights = find_straights_5(player.hand)
                    flushes = find_flushes_5plus(player.hand)

                    await ws.send_text(json.dumps({
                        "type": "hints_result",
                        "count": len(player.hand),
                        "pairs": [[card_str(c) for c in x] for x in pairs_trips_quads["pairs"][:30]],
                        "trips": [[card_str(c) for c in x] for x in pairs_trips_quads["trips"][:30]],
                        "quads": [[card_str(c) for c in x] for x in pairs_trips_quads["quads"][:30]],
                        "straights5": [[card_str(c) for c in x] for x in straights[:30]],
                        "flushes5": [[card_str(c) for c in x] for x in flushes[:30]],
                    }, ensure_ascii=False))
                    continue

                elif t == "leave":
                    break

                else:
                    await ws.send_text(json.dumps(
                        {"type": "error", "message": f"Невідомий тип повідомлення: {t}"},
                        ensure_ascii=False
                    ))
                    continue

                await broadcast(room)

    except WebSocketDisconnect:
        pass
    finally:
        if room is not None and pid is not None:
            async with room.lock:
                room.sockets.pop(pid, None)
                await broadcast(room)
