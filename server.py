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


# ===================== Fixed players =====================

FIXED_PLAYERS = ["Леви", "Тигри", "Ворони", "Акули", "Змії", "Вовки"]
FIXED_SET = set(FIXED_PLAYERS)


# ===================== PostgreSQL persistence =====================

DATABASE_URL = (os.getenv("DATABASE_URL", "") or "").strip()
ALLOW_NO_DB = (os.getenv("ALLOW_NO_DB", "") or "").strip() in ("1","true","True","yes","YES")
AUTOSAVE_SECONDS = int((os.getenv("AUTOSAVE_SECONDS", "") or "15").strip() or "15")

def _normalize_database_url(url: str) -> str:
    """
    Fix common misconfigurations (e.g. putting sslmode in the path like /postgres&sslmode=require)
    and ensure sslmode=require is present.
    """
    if not url:
        return url
    try:
        p = urlparse(url)
        path = p.path or ""
        query = p.query or ""

        # If someone mistakenly appended query params with "&" to the PATH (no "?"), fix it.
        if "&sslmode=" in path or "&" in path:
            # Split path at first "&" and move the rest into query
            base_path, extra = path.split("&", 1)
            path = base_path
            query = (query + "&" if query else "") + extra

        # Ensure sslmode=require
        if "sslmode=" not in query:
            query = (query + "&" if query else "") + "sslmode=require"

        new_p = ParseResult(
            scheme=p.scheme,
            netloc=p.netloc,
            path=path,
            params=p.params,
            query=query,
            fragment=p.fragment,
        )
        return urlunparse(new_p)
    except Exception:
        # Best effort fallback: append sslmode correctly
        if "sslmode=" in url:
            return url
        joiner = "&" if "?" in url else "?"
        return url + f"{joiner}sslmode=require"

DATABASE_URL = _normalize_database_url(DATABASE_URL)

if not DATABASE_URL and not ALLOW_NO_DB:
    raise RuntimeError("DATABASE_URL is not set. Persistence is required (set ALLOW_NO_DB=1 to run without DB).")

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
    # Normalize URL (fix path/query mistakes) and ensure sslmode=require.
    url = _prefer_ipv4_database_url(_normalize_database_url(DATABASE_URL))
    return psycopg.connect(url, autocommit=True)


def _db_init_sync():
    if not DATABASE_URL:
        if ALLOW_NO_DB:
            print("DB: DATABASE_URL not set -> persistence disabled")
            return
        raise RuntimeError("DB: DATABASE_URL not set (persistence required)")
    with _db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(ROOM_TABLE_SQL)


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
        if ALLOW_NO_DB:
            return
        raise RuntimeError("DB: DATABASE_URL not set (persistence required)")
    # Fail hard if DB cannot be reached; otherwise game would run in RAM and state would be lost.
    await asyncio.to_thread(_db_init_sync)


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


# ===================== Game constants ===========

RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]
SUITS = ["♠", "♥", "♦", "♣"]
RANK_VALUE = {r: i for i, r in enumerate(RANKS)}

TABLES = [f"T{i+1}" for i in range(30)]


# ===================== Card/Play models =========

@dataclass
class CardInst:
    id: int
    rank: str
    suit: str

    def to_json(self) -> dict:
        return {"id": self.id, "rank": self.rank, "suit": self.suit}

    @staticmethod
    def from_json(d: dict) -> "CardInst":
        return CardInst(id=int(d["id"]), rank=d["rank"], suit=d["suit"])


@dataclass
class Player:
    pid: str
    name: str
    hand: List[CardInst] = field(default_factory=list)
    archive: List[dict] = field(default_factory=list)


@dataclass
class Play:
    pid: str
    cards: List[CardInst]
    cat: int
    tb: List[int]
    placed_seq: int
    placed_ms: int

    def to_public(self) -> Dict[str, Any]:
        return {
            "pid": self.pid,
            "cards": [c.to_json() for c in self.cards],
            "cat": self.cat,
            "tb": self.tb,
            "placed_seq": self.placed_seq,
            "placed_ms": self.placed_ms,
        }


# ===================== Room =====================

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
    last_saved_ms: int = 0

    def can_join_new_player(self) -> bool:
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
    suit = None
    for s in SUITS:
        if t.endswith(s):
            suit = s
            t = t[:-1]
            break
    if suit is None:
        return None
    rank = t.strip().upper()
    if rank == "1":
        rank = "A"
    if rank not in RANK_VALUE:
        return None
    return rank, suit


def evaluate_hand(cards: List[CardInst]) -> Tuple[int, List[int]]:
    ranks = sorted([RANK_VALUE[c.rank] for c in cards], reverse=True)
    counts: Dict[int, int] = {}
    for r in ranks:
        counts[r] = counts.get(r, 0) + 1
    by_count = sorted(counts.items(), key=lambda x: (x[1], x[0]), reverse=True)

    is_flush = len(set(c.suit for c in cards)) == 1
    uniq = sorted(set(ranks), reverse=True)
    is_straight = False
    straight_high = None
    if len(uniq) == len(cards):
        if uniq and uniq[0] - uniq[-1] == len(cards) - 1:
            is_straight = True
            straight_high = uniq[0]
        elif uniq == [12, 3, 2, 1, 0] and len(cards) == 5:
            is_straight = True
            straight_high = 3

    if len(cards) == 5:
        if is_straight and is_flush:
            return 8, [straight_high]
        if by_count[0][1] == 4:
            four = by_count[0][0]
            kicker = [r for r in uniq if r != four][0]
            return 7, [four, kicker]
        if by_count[0][1] == 3 and by_count[1][1] == 2:
            return 6, [by_count[0][0], by_count[1][0]]
        if is_flush:
            return 5, ranks
        if is_straight:
            return 4, [straight_high]
        if by_count[0][1] == 3:
            trips = by_count[0][0]
            kickers = [r for r in uniq if r != trips]
            return 3, [trips] + kickers
        if by_count[0][1] == 2 and by_count[1][1] == 2:
            p1, p2 = by_count[0][0], by_count[1][0]
            kicker = [r for r in uniq if r not in (p1, p2)][0]
            return 2, sorted([p1, p2], reverse=True) + [kicker]
        if by_count[0][1] == 2:
            pair = by_count[0][0]
            kickers = [r for r in uniq if r != pair]
            return 1, [pair] + kickers
        return 0, ranks

    if len(cards) == 4:
        if by_count[0][1] == 4:
            return 7, [by_count[0][0]]
        if by_count[0][1] == 3:
            trips = by_count[0][0]
            kicker = [r for r in uniq if r != trips][0]
            return 3, [trips, kicker]
        if by_count[0][1] == 2 and by_count[1][1] == 2:
            return 2, sorted([by_count[0][0], by_count[1][0]], reverse=True)
        if by_count[0][1] == 2:
            pair = by_count[0][0]
            kickers = [r for r in uniq if r != pair]
            return 1, [pair] + kickers
        return 0, ranks

    if len(cards) == 3:
        if by_count[0][1] == 3:
            return 3, [by_count[0][0]]
        if by_count[0][1] == 2:
            pair = by_count[0][0]
            kicker = [r for r in uniq if r != pair][0]
            return 1, [pair, kicker]
        return 0, ranks

    if len(cards) == 2:
        if by_count[0][1] == 2:
            return 1, [by_count[0][0]]
        return 0, ranks

    return 0, ranks


def active_pids(room: Room) -> Set[str]:
    return set(room.sockets.keys())


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
        "last_saved_ms": room.last_saved_ms,
    }


def room_from_state(room_id: str, st: Dict[str, Any]) -> Room:
    r = Room(room_id=room_id)
    r.round_no = int(st.get("round_no", 0))
    r.play_seq = int(st.get("play_seq", 0))
    r.next_card_id = int(st.get("next_card_id", 1))
    r.ready_pids = set(st.get("ready_pids", []))
    r.battle_history = list(st.get("battle_history", []))
    r.last_saved_ms = int(st.get("last_saved_ms", 0))

    for pd in st.get("players", []):
        p = Player(pid=pd["pid"], name=pd.get("name", pd["pid"]))
        p.archive = list(pd.get("archive", []))
        p.hand = [CardInst.from_json(x) for x in pd.get("hand", [])]
        r.players[p.pid] = p

    pend = st.get("pending", {})
    for t in TABLES:
        r.pending[t] = []
        for pl in pend.get(t, []):
            cards = [CardInst.from_json(x) for x in pl.get("cards", [])]
            r.pending[t].append(
                Play(
                    pid=pl["pid"],
                    cards=cards,
                    cat=int(pl["cat"]),
                    tb=list(pl.get("tb", [])),
                    placed_seq=int(pl.get("placed_seq", 0)),
                    placed_ms=int(pl.get("placed_ms", 0)),
                )
            )
    return r


async def get_room(room_id: str) -> Room:
    async with ROOMS_LOCK:
        if room_id in ROOMS:
            return ROOMS[room_id]

        st = await db_load_room(room_id)
        if st:
            room = room_from_state(room_id, st)
        else:
            room = Room(room_id=room_id)

        ROOMS[room_id] = room
        return room


async def persist_room(room: Room) -> None:
    room.last_saved_ms = int(time.time() * 1000)
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

    act = active_pids(room)
    last_round = room.battle_history[-1] if room.battle_history else None

    return {
        "room": room.room_id,
        "tables": TABLES,
        "round_no": room.round_no,
        "players": plist,
        "my_pending": my_pending,
        "last_round": last_round,
        "battle_history": room.battle_history[-20:],

        # ✅ NEW: readiness is based on active players
        "active_count": len(act),
        "ready_count": len(act.intersection(room.ready_pids)),
        "you_ready": (viewer_pid in room.ready_pids),
        "last_saved_ms": room.last_saved_ms,
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
        try:
            del room.sockets[pid]
        except Exception:
            pass


def _pid_name_from_query(pid: str) -> Optional[str]:
    pid = (pid or "").strip()
    if pid in FIXED_SET:
        return pid
    return None


def _ensure_fixed_player(room: Room, pid: str) -> Player:
    if pid in room.players:
        return room.players[pid]
    p = Player(pid=pid, name=pid)
    room.players[pid] = p
    return p


def _deal_cards(room: Room, player: Player, n: int = 8) -> None:
    for _ in range(n):
        rank = random.choice(RANKS)
        suit = random.choice(SUITS)
        player.hand.append(room.new_card(rank, suit))
    player.hand.sort(key=lambda c: (RANK_VALUE[c.rank], c.suit))


def _card_ids(cards: List[CardInst]) -> Set[int]:
    return set(c.id for c in cards)


def _used_card_ids_in_round(room: Room) -> Set[int]:
    used: Set[int] = set()
    for t in TABLES:
        for pl in room.pending.get(t, []):
            used |= _card_ids(pl.cards)
    return used


def _remove_cards_from_hand(player: Player, ids: Set[int]) -> None:
    player.hand = [c for c in player.hand if c.id not in ids]


def _resolve_table_plays(plays: List[Play]) -> Tuple[Optional[Play], List[Play]]:
    if not plays:
        return None, []
    best = plays[0]
    for pl in plays[1:]:
        if (pl.cat, pl.tb) > (best.cat, best.tb):
            best = pl
        elif (pl.cat, pl.tb) == (best.cat, best.tb):
            if pl.placed_seq < best.placed_seq:
                best = pl
    losers = [p for p in plays if p is not best]
    return best, losers


async def resolve_round(room: Room) -> None:
    removed_by_pid: Dict[str, Set[int]] = {}

    round_result = {
        "round_no": room.round_no,
        "tables": [],
        "ts_ms": int(time.time() * 1000),
    }

    for t in TABLES:
        plays = room.pending.get(t, [])
        if not plays:
            continue
        winner, losers = _resolve_table_plays(plays)
        if winner:
            removed_by_pid.setdefault(winner.pid, set()).update(_card_ids(winner.cards))
        for lo in losers:
            removed_by_pid.setdefault(lo.pid, set()).update(_card_ids(lo.cards))

        round_result["tables"].append({
            "table": t,
            "winner": winner.to_public() if winner else None,
            "losers": [x.to_public() for x in losers],
        })

    for pid, ids in removed_by_pid.items():
        if pid in room.players:
            _remove_cards_from_hand(room.players[pid], ids)

    room.pending = {t: [] for t in TABLES}
    room.ready_pids.clear()

    room.battle_history.append(round_result)

    await persist_room(room)


async def maybe_finish_round(room: Room) -> None:
    act = active_pids(room)
    if act and act.issubset(room.ready_pids):
        await resolve_round(room)


app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.on_event("startup")
async def _startup():
    await db_init()
    # Periodically persist all rooms so a Render restart doesn't lose recent moves.
    asyncio.create_task(_autosave_loop())


@app.on_event("shutdown")
async def _shutdown():
    # Best-effort save of all rooms on graceful shutdown.
    try:
        async with ROOMS_LOCK:
            rooms = list(ROOMS.values())
        for r in rooms:
            await persist_room(r)
    except Exception as e:
        print(f"DB: shutdown save failed. Error: {e}")


async def _autosave_loop():
    while True:
        await asyncio.sleep(AUTOSAVE_SECONDS)
        try:
            async with ROOMS_LOCK:
                rooms = list(ROOMS.values())
            for r in rooms:
                await persist_room(r)
        except Exception as e:
            print(f"DB: autosave failed. Error: {e}")



@app.get("/")
async def root():
    return FileResponse("static/index.html")


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()

    room_id = (ws.query_params.get("room", "") or "demo").strip()
    pid = (ws.query_params.get("pid", "") or "").strip()
    pid_name = _pid_name_from_query(pid)
    if not pid_name:
        await ws.send_text(json.dumps({"type": "error", "error": "Invalid player"}, ensure_ascii=False))
        await ws.close()
        return

    room = await get_room(room_id)

    # Reconnect: replace socket
    async with room.lock:
        old = room.sockets.get(pid_name)
        room.sockets[pid_name] = ws
        if old and old is not ws:
            try:
                await old.close()
            except Exception:
                pass

        player = _ensure_fixed_player(room, pid_name)
        if not player.hand:
            _deal_cards(room, player, n=8)

        await persist_room(room)
        await broadcast(room)

    try:
        while True:
            msg_raw = await ws.receive_text()
            try:
                msg = json.loads(msg_raw)
            except Exception:
                continue

            mtype = msg.get("type")

            async with room.lock:
                player = _ensure_fixed_player(room, pid_name)

                if mtype == "state":
                    snap = room_snapshot_for(room, pid_name)
                    await ws.send_text(json.dumps({"type": "state", "state": snap}, ensure_ascii=False))

                elif mtype == "place":
                    table = msg.get("table")
                    cards_in = msg.get("cards", [])
                    if table not in TABLES:
                        await ws.send_text(json.dumps({"type": "error", "error": "Bad table"}, ensure_ascii=False))
                        continue

                    # Build cards
                    chosen: List[CardInst] = []
                    hand_by_id = {c.id: c for c in player.hand}
                    for cd in cards_in:
                        try:
                            cid = int(cd.get("id"))
                        except Exception:
                            cid = None
                        if cid is None or cid not in hand_by_id:
                            chosen = []
                            break
                        chosen.append(hand_by_id[cid])

                    if not (2 <= len(chosen) <= 5):
                        await ws.send_text(json.dumps({"type": "error", "error": "Need 2-5 cards"}, ensure_ascii=False))
                        continue

                    # Prevent reuse within round
                    used = _used_card_ids_in_round(room)
                    if _card_ids(chosen).intersection(used):
                        await ws.send_text(json.dumps({"type": "error", "error": "Some cards already used in this round"}, ensure_ascii=False))
                        continue

                    cat, tb = evaluate_hand(chosen)

                    room.play_seq += 1
                    play = Play(
                        pid=pid_name,
                        cards=chosen,
                        cat=cat,
                        tb=tb,
                        placed_seq=room.play_seq,
                        placed_ms=int(time.time() * 1000),
                    )
                    room.pending[table].append(play)

                    # placing invalidates readiness of this player
                    if pid_name in room.ready_pids:
                        room.ready_pids.remove(pid_name)

                    await persist_room(room)
                    await broadcast(room)

                elif mtype == "remove":
                    table = msg.get("table")
                    placed_seq = msg.get("placed_seq")
                    if table not in TABLES:
                        await ws.send_text(json.dumps({"type": "error", "error": "Bad table"}, ensure_ascii=False))
                        continue
                    try:
                        placed_seq = int(placed_seq)
                    except Exception:
                        await ws.send_text(json.dumps({"type": "error", "error": "Bad placed_seq"}, ensure_ascii=False))
                        continue

                    new_list = []
                    removed = False
                    for pl in room.pending.get(table, []):
                        if pl.pid == pid_name and pl.placed_seq == placed_seq:
                            removed = True
                            continue
                        new_list.append(pl)
                    room.pending[table] = new_list

                    if removed and pid_name in room.ready_pids:
                        room.ready_pids.remove(pid_name)

                    await persist_room(room)
                    await broadcast(room)

                elif mtype == "ready":
                    room.ready_pids.add(pid_name)
                    await persist_room(room)
                    await broadcast(room)
                    await maybe_finish_round(room)
                    await broadcast(room)

                elif mtype == "end_round_force":
                    await resolve_round(room)
                    await broadcast(room)

    except WebSocketDisconnect:
        async with room.lock:
            if room.sockets.get(pid_name) is ws:
                del room.sockets[pid_name]
            await persist_room(room)
            await broadcast(room)

    except Exception:
        async with room.lock:
            if room.sockets.get(pid_name) is ws:
                del room.sockets[pid_name]
            await persist_room(room)
            await broadcast(room)
