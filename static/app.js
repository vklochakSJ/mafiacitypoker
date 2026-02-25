let ws = null;

const PID_KEY = "poker_pid_tab";
let myPid = sessionStorage.getItem(PID_KEY) || "";
let state = null;

let selectedCardIds = new Set();

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
  el("removeSelectedBtn").disabled = !on;
  el("evalBtn").disabled = !on;

  el("playBtn").disabled = !on;
  el("endRoundBtn").disabled = !on;
  el("forceEndRoundBtn").disabled = !on;
  el("tableSelect").disabled = !on;

  el("pickC").disabled = !on;
  el("pickD").disabled = !on;
  el("pickH").disabled = !on;
  el("pickS").disabled = !on;

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

/* --------------- sorting + grouping (by text, keep ids) --------------- */

const RANK_VALUE = {"2":2,"3":3,"4":4,"5":5,"6":6,"7":7,"8":8,"9":9,"T":10,"J":11,"Q":12,"K":13,"A":14};
const SUIT_ORDER = {"♣":1,"♦":2,"♥":3,"♠":4};

function rankOf(cardTxt){ return RANK_VALUE[cardTxt[0]] || 0; }
function suitOf(cardTxt){ return cardTxt[cardTxt.length - 1]; }

function sortCardTexts(arr){
  return (arr || []).slice().sort((a,b)=>{
    const ra = rankOf(a), rb = rankOf(b);
    if (ra !== rb) return ra - rb;
    const sa = SUIT_ORDER[suitOf(a)] || 0, sb = SUIT_ORDER[suitOf(b)] || 0;
    if (sa !== sb) return sa - sb;
    return a.localeCompare(b);
  });
}

// handObjs: [{id, c}, ...]
function groupHand(handObjs){
  const byText = new Map(); // c -> [ids]
  for (const obj of (handObjs || [])){
    if (!byText.has(obj.c)) byText.set(obj.c, []);
    byText.get(obj.c).push(obj.id);
  }
  const sortedTexts = sortCardTexts([...byText.keys()]);
  return sortedTexts.map(c => ({ card: c, ids: byText.get(c) || [] }));
}

/* ------------------ auto hints ------------------ */

let hintsTimer = null;
function requestHintsSoon(){
  if (!ws || ws.readyState !== 1) return;
  if (hintsTimer) clearTimeout(hintsTimer);
  hintsTimer = setTimeout(()=> send({type:"hints"}), 120);
}

/* ------------------ suit dropdowns ------------------ */

const RANKS_DESC = ["A","K","Q","J","T","9","8","7","6","5","4","3","2"];
function fillSuitSelect(selectEl){
  selectEl.innerHTML = "";
  const opt0 = document.createElement("option");
  opt0.value = ""; opt0.textContent = "—";
  selectEl.appendChild(opt0);
  for (const r of RANKS_DESC){
    const o = document.createElement("option");
    o.value = r;
    o.textContent = r === "T" ? "10" : r;
    selectEl.appendChild(o);
  }
}
function setupSuitPickers(){
  const pickC = el("pickC"), pickD = el("pickD"), pickH = el("pickH"), pickS = el("pickS");
  [pickC,pickD,pickH,pickS].forEach(fillSuitSelect);

  const onPick = (suit, picker) => {
    const r = picker.value;
    if (!r) return;
    send({ type:"add_manual", card: `${r}${suit}` });
    picker.value = "";
  };
  pickC.onchange = () => onPick("♣", pickC);
  pickD.onchange = () => onPick("♦", pickD);
  pickH.onchange = () => onPick("♥", pickH);
  pickS.onchange = () => onPick("♠", pickS);
}

/* ------------------ tables select ------------------ */

function setupTablesSelect(){
  const sel = el("tableSelect");
  sel.innerHTML = "";
  const opt0 = document.createElement("option");
  opt0.value = ""; opt0.textContent = "— оберіть стіл —";
  sel.appendChild(opt0);

  const tables = (state && state.tables) ? state.tables : [];
  for (const t of tables){
    const o = document.createElement("option");
    o.value = t; o.textContent = t;
    sel.appendChild(o);
  }
}

/* ------------------ render blocks ------------------ */

function renderOpponents(){
  const box = el("opponentHands");
  box.innerHTML = "";
  if (!state) return;

  const opps = state.players.filter(p => p.pid !== myPid);
  if (!opps.length){
    box.innerHTML = `<div class="small">(поки що немає опонентів)</div>`;
    return;
  }

  for (const p of opps){
    const wrap = document.createElement("div");
    wrap.className = "opponent";

    const title = document.createElement("div");
    title.className = "opponent-name";
    title.textContent = p.name;

    const cards = document.createElement("div");
    cards.className = "opponent-cards";

    const groups = groupHand(p.hand || []);
    for (const g of groups){
      const cb = document.createElement("div");
      cb.className = "cardbtn" + (isRedSuit(g.card) ? " red" : "");
      cb.textContent = g.card;

      if (g.ids.length > 1){
        const badge = document.createElement("div");
        badge.className = "badge";
        badge.textContent = String(g.ids.length);
        cb.appendChild(badge);
      }
      cards.appendChild(cb);
    }

    wrap.appendChild(title);
    wrap.appendChild(cards);
    box.appendChild(wrap);
  }
}

function renderMyPending(){
  const box = el("myPending");
  box.innerHTML = "";
  if (!state) return;

  const mp = state.my_pending || {};
  const tables = state.tables || [];
  let any = false;

  for (const t of tables){
    const arr = mp[t] || [];
    if (!arr.length) continue;
    any = true;

    const wrap = document.createElement("div");
    wrap.className = "item";
    wrap.innerHTML = `<div><b>${t}</b></div>`;
    for (const p of arr){
      const line = document.createElement("div");
      line.className = "small";
      const when = (p.placed_seq !== undefined) ? `#${p.placed_seq}` : "";
      line.textContent = `${p.cards.join(" ")} — ${p.label} ${when}`;
      wrap.appendChild(line);
    }
    box.appendChild(wrap);
  }

  if (!any){
    box.innerHTML = `<div class="small">(ви ще нічого не поклали на столи в цьому раунді)</div>`;
  }
}

function renderLastRound(){
  const box = el("lastRound");
  box.innerHTML = "";
  if (!state || !state.last_round){
    box.innerHTML = `<div class="small">(ще немає завершених раундів)</div>`;
    return;
  }

  const r = state.last_round;
  const title = document.createElement("div");
  title.innerHTML = `<b>Раунд #${r.round}</b>`;
  box.appendChild(title);

  if (!r.tables || !r.tables.length){
    box.appendChild(Object.assign(document.createElement("div"), {className:"small", textContent:"(у цьому раунді ніхто не грав на жодному столі)"}));
    return;
  }

  for (const t of r.tables){
    const sec = document.createElement("div");
    sec.className = "item";

    const w = t.winner;
    const winnerText = w ? `${w.name}: ${w.cards.join(" ")} (${w.label})` : "(нема)";

    sec.innerHTML = `<div><b>${t.table}</b></div>
                     <div class="small">Переможець: ${winnerText}</div>
                     <div class="small">Зіграно комбінацій: ${(t.plays||[]).length}</div>`;
    box.appendChild(sec);
  }
}

function renderHistory(){
  const box = el("history");
  box.innerHTML = "";
  if (!state || !state.battle_history || !state.battle_history.length){
    box.innerHTML = `<div class="small">(історія порожня)</div>`;
    return;
  }

  const hist = state.battle_history.slice().reverse();
  for (const r of hist){
    const sec = document.createElement("div");
    sec.className = "item";
    sec.innerHTML = `<div><b>Раунд #${r.round}</b></div>`;
    if (!r.tables || !r.tables.length){
      sec.appendChild(Object.assign(document.createElement("div"), {className:"small", textContent:"(ніхто не грав)"}));
    } else {
      for (const t of r.tables){
        const w = t.winner;
        const wName = w ? w.name : "(нема)";
        const line = document.createElement("div");
        line.className = "small";
        line.textContent = `${t.table}: переможець — ${wName}; зіграно: ${(t.plays||[]).length}`;
        sec.appendChild(line);
      }
    }
    box.appendChild(sec);
  }
}

function renderRoundStatus(){
  const inv = state?.involved_count ?? 0;
  const ready = state?.ready_count ?? 0;
  const youReady = !!state?.you_ready;

  let text = "";
  if (inv === 0) {
    text = "У цьому раунді ще немає зіграних комбінацій (ніхто не задіяний).";
  } else {
    text = `Готові: ${ready}/${inv}` + (youReady ? " (ви підтвердили)" : "");
  }
  el("roundStatus").textContent = text;
}

/* ------------------ main render ------------------ */

function render(){
  if (!state) return;

  const playersDiv = el("players");
  playersDiv.innerHTML = "";
  state.players.forEach(p=>{
    const d = document.createElement("div");
    const you = p.pid === myPid ? " (ви)" : "";
    d.className = "small";
    d.textContent = `• ${p.name}${you} — карт у руці: ${(p.hand||[]).length}`;
    playersDiv.appendChild(d);
  });

  setupTablesSelect();

  const me = meFromState();
  const handDiv = el("hand");
  handDiv.innerHTML = "";

  if (me){
    const myHand = me.hand || [];
    const myIds = new Set(myHand.map(x => x.id));
    selectedCardIds = new Set([...selectedCardIds].filter(id => myIds.has(id)));

    const groups = groupHand(myHand);
    for (const g of groups){
      const selCount = g.ids.filter(id => selectedCardIds.has(id)).length;

      const b = document.createElement("div");
      b.className = "cardbtn"
        + (isRedSuit(g.card) ? " red" : "")
        + (selCount>0 ? " selected" : "");
      b.textContent = g.card;

      if (g.ids.length > 1){
        const badge = document.createElement("div");
        badge.className = "badge";
        badge.textContent = String(g.ids.length);
        b.appendChild(badge);
      }
      if (selCount > 0){
        const badge2 = document.createElement("div");
        badge2.className = "badge2";
        badge2.textContent = g.ids.length > 1 ? `${selCount}/${g.ids.length}` : `${selCount}`;
        b.appendChild(badge2);
      }

      b.onclick = ()=>{
        const selectedHere = g.ids.filter(id => selectedCardIds.has(id));
        if (selectedHere.length < g.ids.length){
          const next = g.ids.find(id => !selectedCardIds.has(id));
          if (next !== undefined) selectedCardIds.add(next);
        } else {
          g.ids.forEach(id => selectedCardIds.delete(id));
        }
        render();
      };

      handDiv.appendChild(b);
    }
  }

  renderOpponents();
  renderMyPending();
  renderLastRound();
  renderHistory();
  renderRoundStatus();

  el("pid").textContent = myPid || "(нема)";
}

/* ------------------ connect ------------------ */

function connect(){
  if (ws) return;
  ensurePid();

  const room = el("room").value.trim() || "default";
  const name = el("name").value.trim() || "Гравець";

  ws = new WebSocket(wsUrl());
  el("status").textContent = "підключення…";

  ws.onopen = () => send({type:"join", room, name, pid: myPid});

  ws.onmessage = (ev)=>{
    const msg = JSON.parse(ev.data);

    if (msg.type === "joined"){
      el("status").textContent = `онлайн (кімната: ${msg.room})`;
      setOnline(true);
      requestHintsSoon();
      return;
    }
    if (msg.type === "state"){
      state = msg.state;
      render();
      requestHintsSoon();
      return;
    }
    if (msg.type === "eval_result"){
      toast(`Оцінка: ${msg.cards.join(" ")} → ${msg.label}`);
      return;
    }
    if (msg.type === "hints_result"){
      const lines = [];
      lines.push(`Карт у руці: ${msg.count}\n`);
      const block = (title, arr)=>{
        lines.push(title);
        if (!arr.length) lines.push("  (нема)");
        else arr.slice(0,30).forEach(x => lines.push("  " + x.join(" ")));
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
    if (msg.type === "round_result"){
      toast(`Раунд #${msg.round.round} завершено`);
      return;
    }
    if (msg.type === "error"){
      toast("Помилка: " + msg.message);
      return;
    }
  };

  ws.onclose = ()=>{
    ws = null;
    el("status").textContent = "офлайн";
    setOnline(false);
  };

  ws.onerror = ()=> toast("Помилка WebSocket");
}

function disconnect(){
  if (!ws) return;
  try { send({type:"leave"}); } catch {}
  ws.close();
  ws = null;
  el("status").textContent = "офлайн";
  setOnline(false);
}

/* ------------------ actions ------------------ */

function selectedIdList(){
  return Array.from(selectedCardIds);
}

el("connectBtn").onclick = connect;
el("disconnectBtn").onclick = disconnect;

el("newIdBtn").onclick = ()=>{
  if (ws) return;
  myPid = genPid();
  sessionStorage.setItem(PID_KEY, myPid);
  el("pid").textContent = myPid;
  toast("Створено нового гравця для цієї вкладки.");
};

el("dealMeBtn").onclick = ()=>{
  const n = parseInt(el("dealN").value, 10) || 0;
  send({type:"deal", n});
};

el("dealAllBtn").onclick = ()=>{
  const n = parseInt(el("dealN").value, 10) || 0;
  send({type:"deal_all", n});
};

el("clearHandBtn").onclick = ()=> {
  selectedCardIds.clear();
  send({type:"clear_hand"});
};

el("removeSelectedBtn").onclick = ()=>{
  const ids = selectedIdList();
  if (!ids.length){
    toast("Спочатку виберіть карти для видалення");
    return;
  }
  send({ type:"remove_selected", card_ids: ids });
  selectedCardIds.clear();
};

el("evalBtn").onclick = ()=>{
  const ids = selectedIdList();
  send({type:"eval_selected", card_ids: ids});
};

el("playBtn").onclick = ()=>{
  const ids = selectedIdList();
  const table = el("tableSelect").value;
  if (!table){
    toast("Оберіть стіл");
    return;
  }
  send({type:"play_selected", card_ids: ids, table});
  selectedCardIds.clear();
};

el("endRoundBtn").onclick = ()=>{
  send({ type: "end_round_vote" });
};

el("forceEndRoundBtn").onclick = ()=>{
  send({ type: "end_round_force" });
};

/* ------------------ init ------------------ */

setOnline(false);
ensurePid();
setupSuitPickers();
