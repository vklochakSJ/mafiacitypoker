import asyncio
import json
import os
import random
import itertools
import time
import socket
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional, Any, Set
from urllib.parse import urlparse, urlunparse, ParseResult

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

import psycopg


# ===================== PostgreSQL persistence =====================

DATABASE_URL = (os.getenv("DATABASE_URL", "") or "").strip()

ROOM_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS room_state (
  room_id TEXT PRIMARY KEY,
  state   JSONB NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

UPSERT_SQL = """
INSERT INTO room_state (room_id, state, updated_at)
VALUES (%s, %s::jsonb, now())
ON CONFLICT (room_id)
DO UPDATE SET state = EXCLUDED.state, updated_at = now();
"""

SELECT_SQL = "SELECT state FROM room_state WHERE room_id=%s;"


def _prefer_ipv4_database_url(url: str) -> str:
    if not url:
        return url
    try:
        p = urlparse(url)
        host = p.hostname
        if not host:
            return url

        ipv4 = None
        try:
            infos = socket.getaddrinfo(host, p.port or 5432, family=socket.AF_INET, type=socket.SOCK_STREAM)
            if infos:
                ipv4 = infos[0][4][0]
        except Exception:
            ipv4 = None

        if not ipv4:
            return url

        userinfo = ""
        if p.username:
            userinfo += p.username
            if p.password:
                userinfo += f":{p.password}"
            userinfo += "@"

        port = f":{p.port}" if p.port else ""
        netloc = f"{userinfo}{ipv4}{port}"

        new_p = ParseResult(
            scheme=p.scheme,
            netloc=netloc,
            path=p.path,
            params=p.params,
            query=p.query,
            fragment=p.fragment,
        )
        return urlunparse(new_p)
    except Exception:
        return url


def _db_connect():
    url = _prefer_ipv4_database_url(DATABASE_URL)
    if url and "sslmode=" not in url:
        joiner = "&" if "?" in url else "?"
        url = url + f"{joiner}sslmode=require"
    return psycopg.connect(url, autocommit=True)


def _db_init_sync():
    if not DATABASE_URL:
        print("DB: DATABASE_URL not set -> persistence disabled")
        return
    with _db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(ROOM_TABLE_SQL)
    print("DB: init OK")


def _db_load_room_sync(room_id: str) -> Optional[dict]:
    if not DATABASE_URL:
        return None
    with _db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(SELECT_SQL, (room_id,))
            row = cur.fetchone()
            if not row:
                return None
            state = row[0]
            if isinstance(state, str):
                return json.loads(state)
            return state


def _db_save_room_sync(room_id: str, state: dict) -> None:
    if not DATABASE_URL:
        return
    payload = json.dumps(state, ensure_ascii=False)
    with _db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(UPSERT_SQL, (room_id, payload))


async def db_init():
    if not DATABASE_URL:
        return
    try:
        await asyncio.to_thread(_db_init_sync)
    except Exception as e:
        print(f"DB: init failed -> running WITHOUT persistence. Error: {e}")


async def db_load_room(room_id: str) -> Optional[dict]:
    if not DATABASE_URL:
        return None
    try:
        return await asyncio.to_thread(_db_load_room_sync, room_id)
    except Exception as e:
        print(f"DB: load failed for room={room_id}. Error: {e}")
        return None


async def db_save_room(room_id: str, state: dict) -> None:
    if not DATABASE_URL:
        return
    try:
        await asyncio.to_thread(_db_save_room_sync, room_id, state)
    except Exception as e:
        print(f"DB: save failed for room={room_id}. Error: {e}")


# ===================== Game constants =====================

TABLES = [
    "МІДТАУН","ФЕЛБЛОК","ГАРЛЕМ","РІВЕРСАЙД","ОКСМІР","ІНДЕЙЛ","ХАРБОР","ХІЛЛФОРД","БРАЙТОН","ЯРВІК",
    "ХАЙТС","ГРЕЙРОК","НОРТБРІДЖ","БЕЙСАЙД","КРОСБІ","ІСТ-ТАУН","САУЗБРІДЖ","ГРІНВЕЙ","ТОРВІК","ДАУНТАУН",
    "БРУКЛІН","ЛІБЕРТІ","ЕШПАРК","СОХО","ВЕСТ-САЙД","АЙРОНХІЛЛ","САУЗГЕЙТ","ФЕЙРМОНТ","ХАЙЛЕНД","ТВІНС",
]

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
    "HIGH": 1, "PAIR": 2, "TWO_PAIR": 3, "TRIPS": 4,
    "STRAIGHT": 5, "FLUSH": 6, "FULL_HOUSE": 7, "QUADS": 8,
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


# ===================== Models =====================

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

    @staticmethod
    def from_json(d: Dict[str, Any]) -> "CardInst":
        cid = int(d["id"])
        c = d.get("c") or ""
        rank = c[:-1]
        suit = c[-1:]
        return CardInst(id=cid, rank=rank, suit=suit)


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
            "card_ids": list(self.card_ids),
        }

    @staticmethod
    def from_json(d: Dict[str, Any]) -> "Play":
        return Play(
            pid=d["pid"],
            name=d["name"],
            table=d["table"],
            card_ids=[int(x) for x in d.get("card_ids", [])],
            cards_text=list(d.get("cards", [])),
            cat=int(d["cat"]),
            tb=tuple(int(x) for x in d.get("tb", [])),
            label=d["label"],
            placed_ms=int(d.get("placed_ms", 0)),
            placed_seq=int(d.get("placed_seq", 0)),
        )


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
ROOMS_LOCK = asyncio.Lock()


def parse_card_text(t: str) -> Optional[Tuple[str, str]]:
    t = (t or "").strip()
    if not t:
        return None
    suit_map = {"c":"♣","♣":"♣","d":"♦","♦":"♦","h":"♥","♥":"♥","s":"♠","♠":"♠"}
    s = t[-1].lower()
    if s not in suit_map:
        return None
    suit = suit_map[s]
    rank_part = t[:-1].strip()
    rank = "T" if rank_part == "10" else rank_part.upper()
    if rank not in RANKS:
        return None
    return (rank, suit)


def random_unique_cards(n: int) -> List[Tuple[str, str]]:
    if n < 0:
        raise ValueError("N має бути ≥ 0")
    if n > 52:
        raise ValueError("За одну роздачу максимум 52 унікальні карти")
    deck = [(r, s) for r in RANKS for s in SUITS]
    return random.sample(deck, n)


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


def room_to_state(room: Room) -> Dict[str, Any]:
    players = []
    for p in room.players.values():
        players.append({
            "pid": p.pid,
            "name": p.name,
            "hand": [c.to_json() for c in p.hand],
            "archive": p.archive,
        })
    pending = {t: [pl.to_public() for pl in room.pending.get(t, [])] for t in TABLES}
    return {
        "room_id": room.room_id,
        "round_no": room.round_no,
        "play_seq": room.play_seq,
        "next_card_id": room.next_card_id,
        "ready_pids": list(room.ready_pids),
        "players": players,
        "pending": pending,
        "battle_history": room.battle_history,
    }


def room_from_state(room_id: str, st: Dict[str, Any]) -> Room:
    r = Room(room_id=room_id)
    r.round_no = int(st.get("round_no", 0))
    r.play_seq = int(st.get("play_seq", 0))
    r.next_card_id = int(st.get("next_card_id", 1))
    r.ready_pids = set(st.get("ready_pids", []))
    r.battle_history = list(st.get("battle_history", []))

    for pd in st.get("players", []):
        p = Player(pid=pd["pid"], name=pd.get("name", "Гравець"))
        p.archive = list(pd.get("archive", []))
        p.hand = [CardInst.from_json(x) for x in pd.get("hand", [])]
        r.players[p.pid] = p

    r.pending = {t: [] for t in TABLES}
    pend = st.get("pending", {}) or {}
    for t in TABLES:
        for pl in pend.get(t, []) or []:
            r.pending[t].append(Play.from_json(pl))
    return r


async def get_room(room_id: str) -> Room:
    async with ROOMS_LOCK:
        if room_id in ROOMS:
            return ROOMS[room_id]
        st = await db_load_room(room_id)
        room = room_from_state(room_id, st) if st else Room(room_id=room_id)
        ROOMS[room_id] = room
        return room


async def persist_room(room: Room) -> None:
    await db_save_room(room.room_id, room_to_state(room))


def room_snapshot_for(room: Room, viewer_pid: str) -> Dict[str, Any]:
    plist = []
    for p in room.players.values():
        plist.append({
            "pid": p.pid,
            "name": p.name,
            "hand": [c.to_json() for c in p.hand],
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
    remove_by_pid: Dict[str, Set[int]] = {}

    for t in TABLES:
        plays = room.pending.get(t, [])
        if not plays:
            continue

        best_strength = max((p.cat, p.tb) for p in plays)
        best_plays = [p for p in plays if (p.cat, p.tb) == best_strength]
        winner = min(best_plays, key=lambda p: p.placed_seq)

        tables_out.append({
            "table": t,
            "plays": [p.to_public() for p in plays],
            "winner": winner.to_public(),
        })

        for p in plays:
            remove_by_pid.setdefault(p.pid, set()).update(p.card_ids)

    for pid, ids in remove_by_pid.items():
        if pid in room.players:
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


app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.on_event("startup")
async def _startup():
    await db_init()


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

        room = await get_room(room_id)

        async with room.lock:
            if pid not in room.players:
                if not room.can_join():
                    await ws.send_text(json.dumps({"type": "error", "message": "Кімната заповнена (макс. 6 гравців)"}, ensure_ascii=False))
                    return
                room.players[pid] = Player(pid=pid, name=name)
            else:
                room.players[pid].name = name

            room.sockets[pid] = ws
            await persist_room(room)

        await ws.send_text(json.dumps({"type": "joined", "pid": pid, "room": room_id}, ensure_ascii=False))

        async with room.lock:
            await broadcast(room)

        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)
            t = msg.get("type")

            needs_save = False
            one_shot_round: Optional[dict] = None

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
                    for r_, s_ in random_unique_cards(n):
                        player.hand.append(room.new_card(r_, s_))
                    needs_save = True

                elif t == "deal_all":
                    n = int(msg.get("n", 0))
                    if n < 0:
                        await ws.send_text(json.dumps({"type": "error", "message": "N має бути ≥ 0"}, ensure_ascii=False))
                        continue
                    if n > 52:
                        await ws.send_text(json.dumps({"type": "error", "message": "За одну роздачу максимум 52 унікальні карти"}, ensure_ascii=False))
                        continue
                    for p in room.players.values():
                        for r_, s_ in random_unique_cards(n):
                            p.hand.append(room.new_card(r_, s_))
                    needs_save = True

                elif t == "add_manual":
                    ctxt = msg.get("card", "")
                    parsed = parse_card_text(ctxt)
                    if parsed is None:
                        await ws.send_text(json.dumps({"type": "error", "message": f"Невірна карта: {ctxt}"}, ensure_ascii=False))
                        continue
                    r_, s_ = parsed
                    player.hand.append(room.new_card(r_, s_))
                    needs_save = True

                elif t == "clear_hand":
                    player.hand.clear()
                    needs_save = True

                # ✅ NEW: delete selected cards from hand
                elif t == "remove_selected":
                    card_ids = msg.get("card_ids", [])
                    if not isinstance(card_ids, list):
                        await ws.send_text(json.dumps({"type": "error", "message": "card_ids має бути списком"}, ensure_ascii=False))
                        continue
                    card_ids = [int(x) for x in card_ids]
                    if not card_ids:
                        continue

                    used = used_card_ids_in_round(room, player.pid)
                    if any(cid in used for cid in card_ids):
                        await ws.send_text(json.dumps({
                            "type": "error",
                            "message": "Не можна видаляти карту, яка вже використана у комбінації поточного раунду"
                        }, ensure_ascii=False))
                        continue

                    before = len(player.hand)
                    to_remove = set(card_ids)
                    player.hand = [c for c in player.hand if c.id not in to_remove]
                    after = len(player.hand)

                    if after == before:
                        await ws.send_text(json.dumps({
                            "type": "error",
                            "message": "Вибраних карт уже немає в руці"
                        }, ensure_ascii=False))
                        continue

                    needs_save = True

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

                    used = used_card_ids_in_round(room, player.pid)
                    if any(cid in used for cid in card_ids):
                        await ws.send_text(json.dumps({"type": "error", "message": "Не можна використати ту саму карту двічі в одному раунді"}, ensure_ascii=False))
                        continue

                    cards_tuples = [lookup[cid].as_tuple() for cid in card_ids]
                    cat, tb, label = eval_strict(cards_tuples)
                    cards_text = [lookup[cid].as_text() for cid in card_ids]

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

                    needs_save = True

                elif t == "end_round_vote":
                    room.ready_pids.add(player.pid)
                    rr = maybe_finish_round(room)
                    if rr is not None:
                        one_shot_round = rr
                        needs_save = True

                elif t == "end_round_force":
                    one_shot_round = resolve_round(room)
                    needs_save = True

                elif t == "hints":
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

                if needs_save:
                    await persist_room(room)

                if one_shot_round is not None:
                    for _pid, _ws in list(room.sockets.items()):
                        try:
                            await _ws.send_text(json.dumps({"type": "round_result", "round": one_shot_round}, ensure_ascii=False))
                        except Exception:
                            pass

                await broadcast(room)

    except WebSocketDisconnect:
        pass
    finally:
        if room is not None and pid is not None:
            async with room.lock:
                room.sockets.pop(pid, None)
                await persist_room(room)
                await broadcast(room)
