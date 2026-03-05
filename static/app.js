let ws = null;

const PID_KEY = "poker_pid_tab";
let myPid = sessionStorage.getItem(PID_KEY) || "";
let state = null;

let selectedCardIds = new Set();

const el = (id) => document.getElementById(id);

const toast = (msg) => {
  const t = el("toast");
  if (!t) return;
  t.textContent = msg;
  setTimeout(() => { t.textContent = ""; }, 3500);
};

function renderHints(msg){
  const hintsDiv = el("hints");
  if (!hintsDiv) return;

  // важливо: щоб CSS-колонки працювали
  hintsDiv.classList.add("hintsGrid");

  hintsDiv.innerHTML = "";

  const createBlock = (title, arr)=>{
    const block = document.createElement("div");
    block.className = "hintBlock";

    const h = document.createElement("div");
    h.className = "hintTitle";
    h.textContent = title;
    block.appendChild(h);

    if (!arr || !arr.length){
      const empty = document.createElement("div");
      empty.className = "small muted";
      empty.textContent = "(нема)";
      block.appendChild(empty);
    } else {
      arr.slice(0, 30).forEach(x=>{
        const line = document.createElement("div");
        line.className = "small";
        line.textContent = x.join(" ");
        block.appendChild(line);
      });
    }

    hintsDiv.appendChild(block);
  };

  // порядок як домовлялись
  createBlock("Двійки (пари)", msg.pairs || []);
  createBlock("Трійки", msg.trips || []);
  createBlock("Каре", msg.quads || []);
  createBlock("Флеші", msg.flushes5 || []);
  createBlock("Стріти", msg.straights5 || []);
  createBlock("Стріт-флеші", msg.straight_flushes5 || []);
  createBlock("Роял-флеші", msg.royal_flushes || []);
}

function setPidFromSeat() {
  const seatEl = el("seat");
  if (!seatEl) return;
  const seat = seatEl.value;
  myPid = seat;
  sessionStorage.setItem(PID_KEY, myPid);
  const pidEl = el("pid");
  if (pidEl) pidEl.textContent = myPid;
}

function setOnline(on) {
  if (el("connectBtn")) el("connectBtn").disabled = on;
  if (el("disconnectBtn")) el("disconnectBtn").disabled = !on;
  if (el("seat")) el("seat").disabled = on;
  if (el("room")) el("room").disabled = on;

  if (el("dealMeBtn")) el("dealMeBtn").disabled = !on;
  if (el("dealAllBtn")) el("dealAllBtn").disabled = !on;
  if (el("clearHandBtn")) el("clearHandBtn").disabled = !on;
  if (el("removeSelectedBtn")) el("removeSelectedBtn").disabled = !on;
  if (el("evalBtn")) el("evalBtn").disabled = !on;

  if (el("playBtn")) el("playBtn").disabled = !on;
  if (el("endRoundBtn")) el("endRoundBtn").disabled = !on;
  if (el("forceEndRoundBtn")) el("forceEndRoundBtn").disabled = !on;
  if (el("tableSelect")) el("tableSelect").disabled = !on;

  if (el("pickC")) el("pickC").disabled = !on;
  if (el("pickD")) el("pickD").disabled = !on;
  if (el("pickH")) el("pickH").disabled = !on;
  if (el("pickS")) el("pickS").disabled = !on;

  // optional buttons (may not exist in some layouts)
  const _addUnknownBtn = el("unknownBtn") || el("addUnknownBtn") || el("addEmptyBtn") || el("addBlankBtn");
  if (_addUnknownBtn) _addUnknownBtn.disabled = !on;

  // ✅ Знімки: активуємо кнопки якщо вони є
  if (el("saveBtn")) el("saveBtn").disabled = !on;
  if (el("refreshSavesBtn")) el("refreshSavesBtn").disabled = !on;

  if (el("newTabBtn")) el("newTabBtn").disabled = on;
}

function wsUrl() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}/ws`;
}

function send(obj) {
  if (!ws || ws.readyState !== 1) {
    toast("Немає з’єднання з сервером");
    return false;
  }
  ws.send(JSON.stringify(obj));
  return true;
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
  if (!selectEl) return;
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

  if (pickC) pickC.onchange = () => onPick("♣", pickC);
  if (pickD) pickD.onchange = () => onPick("♦", pickD);
  if (pickH) pickH.onchange = () => onPick("♥", pickH);
  if (pickS) pickS.onchange = () => onPick("♠", pickS);
}

/* ------------------ tables select ------------------ */
function setupTablesSelect(){
  const sel = el("tableSelect");
  if (!sel) return;

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

/* ✅ used ids in my pending => locked */
function myUsedCardIds(){
  const used = new Set();
  const mp = state?.my_pending || {};
  const tables = state?.tables || [];
  for (const t of tables){
    for (const p of (mp[t] || [])){
      for (const cid of (p.card_ids || [])){
        used.add(cid);
      }
    }
  }
  return used;
}

/* ------------------ SAVES (snapshots) ------------------ */

function getSaveName(){
  // підтримуємо різні можливі id в HTML
  const inp = el("saveName") || el("saveNameInput") || el("saveTitle") || el("saveTitleInput");
  return (inp && typeof inp.value === "string") ? inp.value.trim() : "";
}

function requestSavesList(){
  send({ type: "saves_list" });
}

function renderSavesList(items){
  const list = el("savesList");
  if (!list) return;

  list.innerHTML = "";

  if (!items || !items.length){
    const d = document.createElement("div");
    d.className = "small muted";
    d.textContent = "(збережень немає)";
    list.appendChild(d);
    return;
  }

  for (const s of items){
    const row = document.createElement("div");
    row.className = "item";

    const title = document.createElement("div");
    title.innerHTML = `<b>${s.name || "(без назви)"}</b> <span class="small muted">${s.created_at || ""}</span>`;
    row.appendChild(title);

    const btns = document.createElement("div");
    btns.style.display = "flex";
    btns.style.gap = "8px";
    btns.style.marginTop = "8px";

    const loadBtn = document.createElement("button");
    loadBtn.className = "secondary";
    loadBtn.textContent = "Завантажити";
    loadBtn.onclick = () => send({ type: "load_save", save_id: s.id });
    btns.appendChild(loadBtn);

    const delBtn = document.createElement("button");
    delBtn.className = "danger";
    delBtn.textContent = "Видалити";
    delBtn.onclick = () => {
      if (!confirm("Видалити це збереження?")) return;
      send({ type: "delete_save", save_id: s.id });
    };
    btns.appendChild(delBtn);

    row.appendChild(btns);
    list.appendChild(row);
  }
}

/* ------------------ render blocks ------------------ */

function renderOpponents(){
  const box = el("opponentHands");
  if (!box) return;

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
  if (!box) return;

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

function renderRoundWithAllPlays(container, roundObj){
  container.innerHTML = "";
  if (!roundObj){
    container.innerHTML = `<div class="small">(немає даних)</div>`;
    return;
  }

  const title = document.createElement("div");
  title.innerHTML = `<b>Раунд #${roundObj.round}</b>`;
  container.appendChild(title);

  const tables = roundObj.tables || [];
  if (!tables.length){
    container.appendChild(Object.assign(document.createElement("div"), {className:"small", textContent:"(у цьому раунді ніхто не грав)"}));
    return;
  }

  for (const t of tables){
    const sec = document.createElement("div");
    sec.className = "item";

    const w = t.winner;
    const winnerText = w ? `${w.name}: ${w.cards.join(" ")} (${w.label}) #${w.placed_seq}` : "(нема)";

    sec.innerHTML = `<div><b>${t.table}</b></div>
                     <div class="small">Переможець: ${winnerText}</div>
                     <div class="small">Усі комбінації:</div>`;

    const plays = (t.plays || []).slice().sort((a,b)=>(a.placed_seq||0)-(b.placed_seq||0));
    for (const p of plays){
      const line = document.createElement("div");
      line.className = "small";
      line.textContent = `• #${p.placed_seq} ${p.name}: ${p.cards.join(" ")} — ${p.label}`;
      sec.appendChild(line);
    }

    container.appendChild(sec);
  }
}

function renderLastRound(){
  const box = el("lastRound");
  if (!box) return;

  if (!state || !state.last_round){
    box.innerHTML = `<div class="small">(ще немає завершених раундів)</div>`;
    return;
  }
  renderRoundWithAllPlays(box, state.last_round);
}

function renderHistory(){
  const box = el("history");
  if (!box) return;

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

    const tables = r.tables || [];
    if (!tables.length){
      sec.appendChild(Object.assign(document.createElement("div"), {className:"small", textContent:"(ніхто не грав)"}));
      box.appendChild(sec);
      continue;
    }

    for (const t of tables){
      const w = t.winner;
      const wText = w ? `${w.name}: ${w.cards.join(" ")} (${w.label}) #${w.placed_seq}` : "(нема)`;

      const head = document.createElement("div");
      head.className = "small";
      head.innerHTML = `<b>${t.table}</b> — переможець: ${wText}`;
      sec.appendChild(head);

      const plays = (t.plays || []).slice().sort((a,b)=>(a.placed_seq||0)-(b.placed_seq||0));
      for (const p of plays){
        const line = document.createElement("div");
        line.className = "small";
        line.textContent = `• #${p.placed_seq} ${p.name}: ${p.cards.join(" ")} — ${p.label}`;
        sec.appendChild(line);
      }
      const spacer = document.createElement("div");
      spacer.className = "small";
      spacer.textContent = "";
      sec.appendChild(spacer);
    }

    box.appendChild(sec);
  }
}

function renderRoundStatus(){
  const active = state?.active_count ?? 0;
  const ready = state?.ready_count ?? 0;
  const youReady = !!state?.you_ready;

  let text = "";
  if (active === 0) {
    text = "Немає активних гравців.";
  } else {
    text = `Готові: ${ready}/${active}` + (youReady ? " (ви підтвердили)" : "");
  }
  const rs = el("roundStatus");
  if (rs) rs.textContent = text;
}

/* ------------------ main render ------------------ */
function render(){
  if (!state) return;

  const used = myUsedCardIds();

  const playersDiv = el("players");
  if (playersDiv){
    playersDiv.innerHTML = "";
    state.players.forEach(p=>{
      const d = document.createElement("div");
      const you = p.pid === myPid ? " (ви)" : "";
      d.className = "small";
      d.textContent = `• ${p.name}${you} — карт у руці: ${(p.hand||[]).length}`;
      playersDiv.appendChild(d);
    });
  }

  setupTablesSelect();

  const me = meFromState();
  const handDiv = el("hand");
  if (handDiv){
    handDiv.innerHTML = "";

    if (me){
      const myHand = me.hand || [];
      const myIds = new Set(myHand.map(x => x.id));
      selectedCardIds = new Set([...selectedCardIds].filter(id => myIds.has(id)));

      const groups = groupHand(myHand);
      for (const g of groups){
        const idsFree = g.ids.filter(id => !used.has(id));
        const idsLocked = g.ids.filter(id => used.has(id));
        const selCount = g.ids.filter(id => selectedCardIds.has(id)).length;

        const b = document.createElement("div");
        const isAllLocked = idsFree.length === 0;

        b.className =
          "cardbtn"
          + (isRedSuit(g.card) ? " red" : "")
          + (selCount>0 ? " selected" : "")
          + (isAllLocked ? " locked" : "");

        b.textContent = g.card;

        if (g.ids.length > 1){
          const badge = document.createElement("div");
          badge.className = "badge";
          badge.textContent = String(g.ids.length);
          b.appendChild(badge);
        }

        b.onclick = ()=>{
          if (idsFree.length === 0) return;

          for (const id of idsLocked) selectedCardIds.delete(id);

          const selectedHereFree = idsFree.filter(id => selectedCardIds.has(id));
          if (selectedHereFree.length < idsFree.length){
            const next = idsFree.find(id => !selectedCardIds.has(id));
            if (next !== undefined) selectedCardIds.add(next);
          } else {
            idsFree.forEach(id => selectedCardIds.delete(id));
          }
          render();
        };

        handDiv.appendChild(b);
      }
    }
  }

  renderOpponents();
  renderMyPending();
  renderLastRound();
  renderHistory();
  renderRoundStatus();

  const pidEl = el("pid");
  if (pidEl) pidEl.textContent = myPid || "(нема)";
}

/* ------------------ connect ------------------ */
function connect(){
  if (ws) return;

  setPidFromSeat();

  const room = (el("room")?.value || "").trim() || "default";
  const seat = el("seat")?.value || "";

  ws = new WebSocket(wsUrl());
  if (el("status")) el("status").textContent = "підключення…";

  ws.onopen = () => send({type:"join", room, name: seat, pid: myPid});

  ws.onmessage = (ev)=>{
    const msg = JSON.parse(ev.data);

    if (msg.type === "joined"){
      if (el("status")) el("status").textContent = `онлайн (кімната: ${msg.room})`;
      setOnline(true);

      // при підключенні одразу тягнемо hints + список збережень
      requestHintsSoon();
      requestSavesList();
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
      renderHints(msg);
      return;
    }

    // ✅ список збережень може приходити під різними type
    if (msg.type === "saves_list" || msg.type === "saves_list_result" || msg.type === "saves"){
      const items = msg.items || msg.saves || msg.list || [];
      renderSavesList(items);
      return;
    }

    // ✅ після save/delete/load можемо оновити список
    if (msg.type === "save_created" || msg.type === "save_deleted" || msg.type === "save_loaded"){
      requestSavesList();
      toast("OK");
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
    if (el("status")) el("status").textContent = "офлайн";
    setOnline(false);
  };

  ws.onerror = ()=> toast("Помилка WebSocket");
}

function disconnect(){
  if (!ws) return;
  try { send({type:"leave"}); } catch {}
  ws.close();
  ws = null;
  if (el("status")) el("status").textContent = "офлайн";
  setOnline(false);
}

/* ------------------ actions ------------------ */
function selectedIdList(){
  const used = myUsedCardIds();
  return Array.from(selectedCardIds).filter(id => !used.has(id));
}

if (el("connectBtn")) el("connectBtn").onclick = connect;
if (el("disconnectBtn")) el("disconnectBtn").onclick = disconnect;

// optional: add unknown/blank card
const addUnknownBtn = el("unknownBtn") || el("addUnknownBtn") || el("addEmptyBtn") || el("addBlankBtn");
if (addUnknownBtn) {
  addUnknownBtn.onclick = () => send({ type: "add_unknown" });
}

if (el("newTabBtn")) {
  el("newTabBtn").onclick = ()=>{
    window.open(window.location.href, "_blank");
  };
}

if (el("seat")) {
  el("seat").onchange = ()=>{
    if (!ws) setPidFromSeat();
  };
}

if (el("dealMeBtn")) {
  el("dealMeBtn").onclick = ()=>{
    const n = parseInt(el("dealN")?.value, 10) || 0;
    send({type:"deal", n});
  };
}

if (el("dealAllBtn")) {
  el("dealAllBtn").onclick = ()=>{
    const n = parseInt(el("dealN")?.value, 10) || 0;
    send({type:"deal_all", n});
  };
}

if (el("clearHandBtn")) {
  el("clearHandBtn").onclick = ()=> {
    selectedCardIds.clear();
    send({type:"clear_hand"});
  };
}

if (el("removeSelectedBtn")) {
  el("removeSelectedBtn").onclick = ()=>{
    const ids = selectedIdList();
    if (!ids.length){
      toast("Спочатку виберіть карти для видалення");
      return;
    }
    send({ type:"remove_selected", card_ids: ids });
    selectedCardIds.clear();
  };
}

if (el("evalBtn")) {
  el("evalBtn").onclick = ()=>{
    const ids = selectedIdList();
    send({type:"eval_selected", card_ids: ids});
  };
}

if (el("playBtn")) {
  el("playBtn").onclick = ()=>{
    const ids = selectedIdList();
    const table = el("tableSelect")?.value;
    if (!table){
      toast("Оберіть стіл");
      return;
    }
    send({type:"play_selected", card_ids: ids, table});
    selectedCardIds.clear();
  };
}

if (el("endRoundBtn")) {
  el("endRoundBtn").onclick = ()=>{
    send({ type: "end_round_vote" });
  };
}

if (el("forceEndRoundBtn")) {
  el("forceEndRoundBtn").onclick = ()=>{
    send({ type: "end_round_force" });
  };
}

// ✅ Знімки: кнопки + логіка
if (el("saveBtn")) {
  el("saveBtn").onclick = ()=>{
    const name = getSaveName();
    send({ type: "save_current", name });
    // після збереження сервер може сам прислати список, але надійніше — оновити
    setTimeout(requestSavesList, 250);
  };
}

if (el("refreshSavesBtn")) {
  el("refreshSavesBtn").onclick = ()=> requestSavesList();
}

/* ------------------ init ------------------ */
setOnline(false);
setupSuitPickers();
setPidFromSeat();
