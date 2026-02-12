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
  el("addManualBtn").disabled = !on;
  el("clearHandBtn").disabled = !on;
  el("evalBtn").disabled = !on;
  el("archiveBtn").disabled = !on;

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

/* ------------------ Auto hints (debounced) ------------------ */

let hintsTimer = null;

function requestHintsSoon() {
  if (!ws || ws.readyState !== 1) return;
  if (hintsTimer) clearTimeout(hintsTimer);
  hintsTimer = setTimeout(() => {
    send({ type: "hints" });
  }, 120);
}

/* ------------------ Render ------------------ */

function render() {
  if (!state) return;

  // Players list (counts only)
  const playersDiv = el("players");
  playersDiv.innerHTML = "";
  state.players.forEach(p => {
    const d = document.createElement("div");
    const you = p.pid === myPid ? " (you)" : "";
    const handCount = (p.hand || []).length;
    const archCount = (p.archive || []).length;
    d.className = "small";
    d.textContent = `• ${p.name}${you} — hand: ${handCount}, archive: ${archCount}`;
    playersDiv.appendChild(d);
  });

  // My hand
  const me = meFromState();
  const handDiv = el("hand");
  handDiv.innerHTML = "";

  selectedIdxs = new Set([...selectedIdxs].filter(i => me && i >= 0 && i < me.hand.length));

  if (me) {
    me.hand.forEach((c, idx) => {
      const b = document.createElement("div");
      b.className = "cardbtn"
        + (isRedSuit(c) ? " red" : "")
        + (selectedIdxs.has(idx) ? " selected" : "");
      b.textContent = c;
      b.onclick = () => {
        if (selectedIdxs.has(idx)) selectedIdxs.delete(idx);
        else selectedIdxs.add(idx);
        render();
      };
      handDiv.appendChild(b);
    });
  }

  // Opponents hands
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

      (p.hand || []).forEach(cardTxt => {
        const cb = document.createElement("div");
        cb.className = "cardbtn" + (isRedSuit(cardTxt) ? " red" : "");
        cb.textContent = cardTxt;
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

  el("pid").textContent = myPid || "(none)";
}

/* ------------------ Connect ------------------ */

function connect() {
  if (ws) return;

  ensurePid();

  const room = el("room").value.trim() || "default";
  const name = el("name").value.trim() || "Player";

  ws = new WebSocket(wsUrl());
  el("status").textContent = "connecting…";

  ws.onopen = () => {
    send({ type: "join", room, name, pid: myPid });
  };

  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);

    if (msg.type === "joined") {
      el("status").textContent = `online (room: ${msg.room})`;
      setOnline(true);
      // request hints early (state likely comes right after)
      requestHintsSoon();
      return;
    }

    if (msg.type === "state") {
      state = msg.state;
      render();
      // ✅ auto hints whenever state changes (deal/add/archive/clear)
      requestHintsSoon();
      return;
    }

    if (msg.type === "eval_result") {
      toast(`Оцінка: ${msg.cards.join(" ")} -> ${msg.label} | cat=${msg.cat} tb=${JSON.stringify(msg.tb)}`);
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
      toast("Error: " + msg.message);
      return;
    }
  };

  ws.onclose = () => {
    ws = null;
    el("status").textContent = "offline";
    setOnline(false);
  };

  ws.onerror = () => {
    toast("WebSocket error");
  };
}

function disconnect() {
  if (!ws) return;
  try { send({ type: "leave" }); } catch {}
  ws.close();
  ws = null;
  el("status").textContent = "offline";
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
  toast("New identity created for this tab.");
};

el("dealMeBtn").onclick = () => {
  const n = parseInt(el("dealN").value, 10) || 0;
  send({ type: "deal", n });
  // hints will update after state arrives
};

el("dealAllBtn").onclick = () => {
  const n = parseInt(el("dealN").value, 10) || 0;
  send({ type: "deal_all", n });
};

el("addManualBtn").onclick = () => {
  const c = el("manualCard").value.trim();
  if (!c) return;
  send({ type: "add_manual", card: c });
  el("manualCard").value = "";
};

el("clearHandBtn").onclick = () => send({ type: "clear_hand" });

el("evalBtn").onclick = () => {
  const idxs = selectedIdxList();
  send({ type: "eval_selected", idxs });
  // eval doesn't change hand, so no need to refresh hints here
};

el("archiveBtn").onclick = () => {
  const idxs = selectedIdxList();
  send({ type: "archive_selected", idxs });
  selectedIdxs.clear();
  // hints will update after state arrives
};

// init
setOnline(false);
ensurePid();
