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


function setSaveInfoFromState(st){
  const v = st && (st.last_saved_ms || st.lastSavedMs || st.last_saved || 0);
  const elSave = document.getElementById("saveInfo");
  if (!elSave) return;
  if (!v) { elSave.textContent = "Saved: —"; return; }
  const d = new Date(v);
  elSave.textContent = "Saved: " + d.toLocaleTimeString();
}

/* ------------------ saves (snapshots) ------------------ */
function requestSavesList(){
  if (!ws || ws.readyState !== 1) return;
  send({type:"saves_list"});
}
function renderSavesList(items){
  const box = el("savesList");
  if (!box) return;
  box.innerHTML = "";
  if (!items || !items.length){
    const div = document.createElement("div");
    div.className = "small muted";
    div.textContent = "(збережень поки нема)";
    box.appendChild(div);
    return;
  }
  for (const it of items){
    const row = document.createElement("div");
    row.className = "saveRow";

    const meta = document.createElement("div");
    meta.className = "saveMeta";

    const name = document.createElement("div");
    name.className = "saveName";
    name.textContent = it.name || it.save_name || it.id || "—";

    const sub = document.createElement("div");
    sub.className = "saveSub";
    const ts = it.ts_ms || it.created_ms || it.created_at_ms || 0;
    sub.textContent = ts ? ("#" + (it.id ?? "") + " • " + new Date(ts).toLocaleString()) : ("#" + (it.id ?? ""));

    meta.appendChild(name);
    meta.appendChild(sub);

    const actions = document.createElement("div");
    actions.className = "saveActions";

    const loadBtn = document.createElement("button");
    loadBtn.className = "secondary";
    loadBtn.textContent = "Завантажити";
    loadBtn.onclick = () => send({type:"load_save", id: it.id});

    const delBtn = document.createElement("button");
    delBtn.className = "danger";
    delBtn.textContent = "Видалити";
    delBtn.onclick = () => {
      if (!confirm("Видалити збереження?")) return;
      send({type:"delete_save", id: it.id});
      // refresh after a moment
      setTimeout(requestSavesList, 200);
    };

    actions.appendChild(loadBtn);
    actions.appendChild(delBtn);

    row.appendChild(meta);
    row.appendChild(actions);
    box.appendChild(row);
  }
}

/* ------------------ hints rendering (wide blocks) ------------------ */
function renderHintsBlocks(msg){
  const root = el("hints");
  if (!root) return;
  root.innerHTML = "";

  const makeBlock = (title, items) => {
    const b = document.createElement("div");
    b.className = "hintBlock";

    const h = document.createElement("div");
    h.className = "hintTitle";
    h.textContent = title;

    const list = document.createElement("div");
    list.className = "hintItems";

    if (!items || !items.length){
      const e = document.createElement("div");
      e.className = "hintEmpty";
      e.textContent = "(нема)";
      list.appendChild(e);
    } else {
      for (const t of items){
        const chip = document.createElement("span");
        chip.className = "hintChip";
        chip.textContent = t;
        list.appendChild(chip);
      }
    }
    b.appendChild(h);
    b.appendChild(list);
    return b;
  };

  // Order / titles match old UI
  root.appendChild(makeBlock("Пари:", msg.pairs));
  root.appendChild(makeBlock("Трійки:", msg.trips));
  root.appendChild(makeBlock("Каре:", msg.quads));
  root.appendChild(makeBlock("Стріти (5):", msg.straights5));
  root.appendChild(makeBlock("Флеші (5):", msg.flushes5));

  // Optional: show count
  const info = document.createElement("div");
  info.className = "small muted";
  info.textContent = "Карт у руці: " + (msg.hand_count ?? "—");
  root.prepend(info);
}

function setPidFromSeat() {
  const seat = el("seat").value;
  myPid = seat;
  sessionStorage.setItem(PID_KEY, myPid);
  el("pid").textContent = myPid;
}

function setOnline(on) {
  el("connectBtn").disabled = on;
  el("disconnectBtn").disabled = !on;
  el("seat").disabled = on;
  el("room").disabled = on;

  el("dealMeBtn").disabled = !on;
  el("dealAllBtn").disabled = !on;
  if (el("saveBtn")) el("saveBtn").disabled = !on;
  if (el("refreshSavesBtn")) el("refreshSavesBtn").disabled = !on;
  if (el("unknownBtn")) el("unknownBtn").disabled = !on;
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

  el("newTabBtn").disabled = on;
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
  if (!state || !state.last_round){
    box.innerHTML = `<div class="small">(ще немає завершених раундів)</div>`;
    return;
  }
  renderRoundWithAllPlays(box, state.last_round);
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

    const tables = r.tables || [];
    if (!tables.length){
      sec.appendChild(Object.assign(document.createElement("div"), {className:"small", textContent:"(ніхто не грав)"}));
      box.appendChild(sec);
      continue;
    }

    for (const t of tables){
      const w = t.winner;
      const wText = w ? `${w.name}: ${w.cards.join(" ")} (${w.label}) #${w.placed_seq}` : "(нема)";

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
  el("roundStatus").textContent = text;
}

/* ------------------ main render ------------------ */
function render(){
  if (!state) return;
  setSaveInfoFromState(state);

  const used = myUsedCardIds();

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

  setPidFromSeat();

  const room = el("room").value.trim() || "default";
  const seat = el("seat").value;

  ws = new WebSocket(wsUrl());
  el("status").textContent = "підключення…";

  ws.onopen = () => send({type:"join", room, name: seat, pid: myPid});

  ws.onmessage = (ev)=>{
    const msg = JSON.parse(ev.data);

    if (msg.type === "joined"){
      el("status").textContent = `онлайн (кімната: ${msg.room})`;
      setOnline(true);
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
      renderHintsBlocks({
        pairs: msg.pairs || [],
        trips: msg.trips || [],
        quads: msg.quads || [],
        straights5: msg.straights5 || [],
        flushes5: msg.flushes5 || [],
        hand_count: msg.count
      });
      return;
    }
    
    if (msg.type === "saves_list"){
      // expected: msg.items (array)
      renderSavesList(msg.items || msg.saves || []);
      return;
    }
    if (msg.type === "save_done"){
      toast("Збережено ✅");
      requestSavesList();
      return;
    }
    if (msg.type === "save_deleted"){
      toast("Видалено 🗑");
      requestSavesList();
      return;
    }
    if (msg.type === "save_loaded"){
      toast("Завантажено ✅");
      // request fresh state + hints
      send({type:"state"});
      requestHintsSoon();
      requestSavesList();
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
  const used = myUsedCardIds();
  return Array.from(selectedCardIds).filter(id => !used.has(id));
}

el("connectBtn").onclick = connect;
el("disconnectBtn").onclick = disconnect;

el("newTabBtn").onclick = ()=>{
  window.open(window.location.href, "_blank");
};

el("seat").onchange = ()=>{
  if (!ws) setPidFromSeat();
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

/* ------------------ saves UI ------------------ */
if (el("saveBtn")){
  el("saveBtn").onclick = ()=>{
    const name = (el("saveName")?.value || "").trim();
    send({type:"save_current", name});
    setTimeout(requestSavesList, 200);
  };
}
if (el("refreshSavesBtn")){
  el("refreshSavesBtn").onclick = ()=> requestSavesList();
}

/* ------------------ unknown card ------------------ */
if (el("unknownBtn")){
  el("unknownBtn").onclick = ()=> send({type:"add_unknown"});
}

/* ------------------ init ------------------ */
setOnline(false);
setupSuitPickers();
setPidFromSeat();
