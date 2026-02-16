let ws = null;

// per-tab identity (each tab = separate player)
const PID_KEY = "poker_pid_tab";
let myPid = sessionStorage.getItem(PID_KEY) || "";
let state = null;
let selectedIdxs = new Set();

const el = (id) => document.getElementById(id);

const toast = (msg) => {
  el("toast").textContent = msg;
  setTimeout(() => { el("toast").textContent = ""; }, 3500);
};

function genPid() {
  return "p" + Math.random().toString(16).slice(2) + Date.now().toString(16);
}

function ensurePid() {
  if (!myPid) {
    myPid = genPid();
    sessionStorage.setItem(PID_KEY, myPid);
  }
  el("pid").textContent = myPid;
}

function setOnline(on) {
  el("connectBtn").disabled = on;
  el("disconnectBtn").disabled = !on;

  el("dealMeBtn").disabled = !on;
  el("dealAllBtn").disabled = !on;
  el("clearHandBtn").disabled = !on;
  el("evalBtn").disabled = !on;
  el("archiveBtn").disabled = !on;

  // suit dropdowns
  el("pickC").disabled = !on;
  el("pickD").disabled = !on;
  el("pickH").disabled = !on;
  el("pickS").disabled = !on;

  // allow new identity only when offline
  el("newIdBtn").disabled = on;
}

function wsUrl() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}/ws`;
}

function send(obj) {
  if (!ws || ws.readyState !== 1) return;
  ws.send(JSON.stringify(obj));
}

function meFromState() {
  if (!state) return null;
  return state.players.find(p => p.pid === myPid) || null;
}

function isRedSuit(cardTxt) {
  return cardTxt.includes("♥") || cardTxt.includes("♦");
}

/* ------------------ Sorting + Grouping ------------------ */

const RANK_VALUE = {
  "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8, "9": 9,
  "T": 10, "J": 11, "Q": 12, "K": 13, "A": 14
};
const SUIT_ORDER = { "♣": 1, "♦": 2, "♥": 3, "♠": 4 };

function rankOf(cardTxt) {
  const r = cardTxt[0];
  return RANK_VALUE[r] || 0;
}
function suitOf(cardTxt) {
  return cardTxt[cardTxt.length - 1];
}

function sortCards(arr) {
  return (arr || []).slice().sort((a, b) => {
    const ra = rankOf(a), rb = rankOf(b);
    if (ra !== rb) return ra - rb; // ascending by rank
    const sa = SUIT_ORDER[suitOf(a)] || 0;
    const sb = SUIT_ORDER[suitOf(b)] || 0;
    if (sa !== sb) return sa - sb;
    return a.localeCompare(b);
  });
}

function groupHand(hand) {
  const sorted = sortCards(hand);
  const map = new Map(); // cardTxt -> indices in ORIGINAL hand
  for (let i = 0; i < (hand || []).length; i++) {
    const c = hand[i];
    if (!map.has(c)) map.set(c, []);
    map.get(c).push(i);
  }

  const seen = new Set();
  const out = [];
  for (const c of sorted) {
    if (seen.has(c)) continue;
    seen.add(c);
    const idxs = map.get(c) || [];
    out.push({ card: c, count: idxs.length, indices: idxs });
  }
  return out;
}

/* ------------------ Auto hints (debounced) ------------------ */

let hintsTimer = null;

function requestHintsSoon() {
  if (!ws || ws.readyState !== 1) return;
  if (hintsTimer) clearTimeout(hintsTimer);
  hintsTimer = setTimeout(() => {
    send({ type: "hints" });
  }, 120);
}

/* ------------------ Suit dropdowns ------------------ */

const RANKS_DESC = ["A", "K", "Q", "J", "T", "9", "8", "7", "6", "5", "4", "3", "2"];

function fillSuitSelect(selectEl) {
  selectEl.innerHTML = "";
  const opt0 = document.createElement("option");
  opt0.value = "";
  opt0.textContent = "—";
  selectEl.appendChild(opt0);

  for (const r of RANKS_DESC) {
    const o = document.createElement("option");
    o.value = r;
    o.textContent = r === "T" ? "10" : r;
    selectEl.appendChild(o);
  }
}

function setupSuitPickers() {
  const pickC = el("pickC");
  const pickD = el("pickD");
  const pickH = el("pickH");
  const pickS = el("pickS");

  [pickC, pickD, pickH, pickS].forEach(fillSuitSelect);

  const onPick = (suit, picker) => {
    const r = picker.value;
    if (!r) return;
    // send manual add in server-supported format like "A♠"
    send({ type: "add_manual", card: `${r}${suit}` });
    picker.value = "";
  };

  pickC.onchange = () => onPick("♣", pickC);
  pickD.onchange = () => onPick("♦", pickD);
  pickH.onchange = () => onPick("♥", pickH);
  pickS.onchange = () => onPick("♠", pickS);
}

/* ------------------ Render ------------------ */

function render() {
  if (!state) return;

  // Players list (counts only)
  const playersDiv = el("players");
  playersDiv.innerHTML = "";
  state.players.forEach(p => {
    const d = document.createElement("div");
    const you = p.pid === myPid ? " (ви)" : "";
    const handCount = (p.hand || []).length;
    const archCount = (p.archive || []).length;
    d.className = "small";
    d.textContent = `• ${p.name}${you} — карт у руці: ${handCount}, архів: ${archCount}`;
    playersDiv.appendChild(d);
  });

  // My hand (grouped + sorted)
  const me = meFromState();
  const handDiv = el("hand");
  handDiv.innerHTML = "";

  if (me) {
    selectedIdxs = new Set([...selectedIdxs].filter(i => i >= 0 && i < me.hand.length));
    const groups = groupHand(me.hand);

    groups.forEach(g => {
      const b = document.createElement("div");
      const selCount = g.indices.filter(i => selectedIdxs.has(i)).length;

      b.className = "cardbtn"
        + (isRedSuit(g.card) ? " red" : "")
        + (selCount > 0 ? " selected" : "");
      b.textContent = g.card;

      if (g.count > 1) {
        const badge = document.createElement("div");
        badge.className = "badge";
        badge.textContent = String(g.count);
        b.appendChild(badge);
      }

      if (selCount > 0) {
        const badge2 = document.createElement("div");
        badge2.className = "badge2";
        badge2.textContent = g.count > 1 ? `${selCount}/${g.count}` : `${selCount}`;
        b.appendChild(badge2);
      }

      b.onclick = () => {
        const selectedHere = g.indices.filter(i => selectedIdxs.has(i));
        if (selectedHere.length < g.count) {
          const next = g.indices.find(i => !selectedIdxs.has(i));
          if (next !== undefined) selectedIdxs.add(next);
        } else {
          g.indices.forEach(i => selectedIdxs.delete(i));
        }
        render();
      };

      handDiv.appendChild(b);
    });
  }

  // Opponents hands (grouped + sorted)
  const oppDiv = el("opponentHands");
  oppDiv.innerHTML = "";

  state.players
    .filter(p => p.pid !== myPid)
    .forEach(p => {
      const wrap = document.createElement("div");
      wrap.className = "opponent";

      const title = document.createElement("div");
      title.className = "opponent-name";
      title.textContent = p.name;

      const cards = document.createElement("div");
      cards.className = "opponent-cards";

      const groups = groupHand(p.hand || []);
      groups.forEach(g => {
        const cb = document.createElement("div");
        cb.className = "cardbtn" + (isRedSuit(g.card) ? " red" : "");
        cb.textContent = g.card;

        if (g.count > 1) {
          const badge = document.createElement("div");
          badge.className = "badge";
          badge.textContent = String(g.count);
          cb.appendChild(badge);
        }

        cards.appendChild(cb);
      });

      wrap.appendChild(title);
      wrap.appendChild(cards);
      oppDiv.appendChild(wrap);
    });

  // My archive only
  const archDiv = el("archive");
  archDiv.innerHTML = "";
  if (me) {
    (me.archive || []).slice().reverse().forEach((a) => {
      const item = document.createElement("div");
      item.className = "item";
      item.innerHTML = `<div><b>${a.label}</b> (cat=${a.cat}, tb=[${a.tb.join(", ")}])</div>
                        <div>${a.cards.join(" ")}</div>`;
      archDiv.appendChild(item);
    });
  }

  el("pid").textContent = myPid || "(нема)";
}

/* ------------------ Connect ------------------ */

function connect() {
  if (ws) return;

  ensurePid();

  const room = el("room").value.trim() || "default";
  const name = el("name").value.trim() || "Гравець";

  ws = new WebSocket(wsUrl());
  el("status").textContent = "підключення…";

  ws.onopen = () => {
    send({ type: "join", room, name, pid: myPid });
  };

  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);

    if (msg.type === "joined") {
      el("status").textContent = `онлайн (кімната: ${msg.room})`;
      setOnline(true);
      requestHintsSoon();
      return;
    }

    if (msg.type === "state") {
      state = msg.state;
      render();
      requestHintsSoon();
      return;
    }

    if (msg.type === "eval_result") {
      toast(`Оцінка: ${msg.cards.join(" ")} → ${msg.label} | cat=${msg.cat} tb=${JSON.stringify(msg.tb)}`);
      return;
    }

    if (msg.type === "hints_result") {
      const lines = [];
      lines.push(`Карт у руці: ${msg.count}\n`);
      const block = (title, arr) => {
        lines.push(title);
        if (!arr.length) lines.push("  (нема)");
        else arr.slice(0, 30).forEach(x => lines.push("  " + x.join(" ")));
        lines.push("");
      };
      block("Пари:", msg.pairs);
      block("Трійки:", msg.trips);
      block("Каре:", msg.quads);
      block("Стріти (5):", msg.straights5);
      block("Флеші (5):", msg.flushes5);
      el("hints").textContent = lines.join("\n");
      return;
    }

    if (msg.type === "error") {
      toast("Помилка: " + msg.message);
      return;
    }
  };

  ws.onclose = () => {
    ws = null;
    el("status").textContent = "офлайн";
    setOnline(false);
  };

  ws.onerror = () => {
    toast("Помилка WebSocket");
  };
}

function disconnect() {
  if (!ws) return;
  try { send({ type: "leave" }); } catch {}
  ws.close();
  ws = null;
  el("status").textContent = "офлайн";
  setOnline(false);
}

/* ------------------ Actions ------------------ */

function selectedIdxList() {
  return Array.from(selectedIdxs).sort((a, b) => a - b);
}

// UI hooks
el("connectBtn").onclick = connect;
el("disconnectBtn").onclick = disconnect;

el("newIdBtn").onclick = () => {
  if (ws) return;
  myPid = genPid();
  sessionStorage.setItem(PID_KEY, myPid);
  el("pid").textContent = myPid;
  toast("Створено нового гравця для цієї вкладки.");
};

el("dealMeBtn").onclick = () => {
  const n = parseInt(el("dealN").value, 10) || 0;
  send({ type: "deal", n });
};

el("dealAllBtn").onclick = () => {
  const n = parseInt(el("dealN").value, 10) || 0;
  send({ type: "deal_all", n });
};

el("clearHandBtn").onclick = () => send({ type: "clear_hand" });

el("evalBtn").onclick = () => {
  const idxs = selectedIdxList();
  send({ type: "eval_selected", idxs });
};

el("archiveBtn").onclick = () => {
  const idxs = selectedIdxList();
  send({ type: "archive_selected", idxs });
  selectedIdxs.clear();
};

// init
setOnline(false);
ensurePid();
setupSuitPickers();
