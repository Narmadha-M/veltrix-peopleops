(() => {
const $ = s => document.querySelector(s);
const sv = (d, w = 16, sw = 2) => `<svg width="${w}" height="${w}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${sw}" stroke-linecap="round" stroke-linejoin="round">${d}</svg>`;
const ICON = {
  doc: sv('<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6"/>', 13),
  tool: sv('<path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.6 2.6-2.4-.6-.6-2.4z"/>', 15),
  copy: sv('<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/>'),
  check: sv('<path d="M20 6 9 17l-5-5"/>', 16, 2.2),
  trash: sv('<path d="M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14"/>', 15),
  mark: sv('<path d="M12 3v4M12 17v4M3 12h4M17 12h4M5.6 5.6l2.8 2.8M15.6 15.6l2.8 2.8M5.6 18.4l2.8-2.8M15.6 8.4l2.8-2.8"/>', 16, 2.2),
  cal: sv('<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>', 18),
  heart: sv('<path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1-1.1a5.5 5.5 0 0 0-7.8 7.8l1 1.1L12 21l7.8-7.5 1-1.1a5.5 5.5 0 0 0 0-7.8z"/>', 18),
  plane: sv('<path d="M22 2 11 13M22 2l-7 20-4-9-9-4z"/>', 18),
  team: sv('<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8"/>', 18),
  chart: sv('<path d="M3 3v18h18M7 14l4-4 4 4 5-5"/>', 18),
  arrow: sv('<path d="M5 12h14M13 5l7 7-7 7"/>', 18),
  chev: sv('<path d="m6 9 6 6 6-6"/>', 15),
  clock: sv('<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>', 14),
  up: sv('<path d="M7 10v12M15 5.9 14 10h5.8a2 2 0 0 1 2 2.3l-1.4 8a2 2 0 0 1-2 1.7H7V10l4.3-7.6a1.9 1.9 0 0 1 3.4 1.2z"/>', 15),
  down: sv('<path d="M17 14V2M9 18.1 10 14H4.2a2 2 0 0 1-2-2.3l1.4-8a2 2 0 0 1 2-1.7H17v12l-4.3 7.6a1.9 1.9 0 0 1-3.4-1.2z"/>', 15),
  regen: sv('<path d="M21 12a9 9 0 1 1-3-6.7L21 8M21 3v5h-5"/>', 15),
};
const ROUTE = {
  rag: ["From HR documents", sv('<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6"/>', 13)],
  tool: ["From your leave data", sv('<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14c0 1.7 4 3 9 3s9-1.3 9-3V5M3 12c0 1.7 4 3 9 3s9-1.3 9-3"/>', 13)],
  hybrid: ["Documents + leave data", sv('<path d="M12 2 2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/>', 13)],
  clarify: ["Needs a detail", sv('<circle cx="12" cy="12" r="10"/><path d="M9.1 9a3 3 0 0 1 5.8 1c0 2-3 3-3 3M12 17h.01"/>', 13)],
  no_answer: ["Not covered", sv('<circle cx="12" cy="12" r="10"/><path d="M8 12h8"/>', 13)],
  refuse: ["Declined", sv('<circle cx="12" cy="12" r="10"/><path d="m4.9 4.9 14.2 14.2"/>', 13)],
};
const store = {
  get(k, d){ try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch { return d; } },
  set(k, v){ try { localStorage.setItem(k, JSON.stringify(v)); } catch {} },
  del(k){ try { localStorage.removeItem(k); } catch {} },
};
const uid = () => (crypto.randomUUID ? crypto.randomUUID() : Date.now().toString(36) + Math.random().toString(36).slice(2));
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;

// Sign-in is never remembered: every time the page is opened it asks for the email again.
store.del("po.user");
// Old history was saved per employee ID and so was shared by different emails; remove it.
try { Object.keys(localStorage).filter(k => /^po\.convs\.[A-Za-z]\d+$/.test(k)).forEach(k => localStorage.removeItem(k)); } catch {}
let user = null;
let convs = [], current = null, busy = false, statusTimer = null;

/* ---------------- theme ---------------- */
const theme = store.get("po.theme", null);
if (theme) document.documentElement.dataset.theme = theme;
$("#themeBtn").onclick = () => {
  const dark = document.documentElement.dataset.theme
    ? document.documentElement.dataset.theme === "dark"
    : matchMedia("(prefers-color-scheme: dark)").matches;
  document.documentElement.dataset.theme = dark ? "light" : "dark";
  store.set("po.theme", document.documentElement.dataset.theme);
};

/* ---------------- login ---------------- */
const loginLabel = $("#loginBtn").innerHTML;
$("#loginForm").onsubmit = async e => {
  e.preventDefault();
  const email = $("#email").value.trim(), err = $("#loginErr");
  err.textContent = "";
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) { err.textContent = "Please enter a valid email address."; $("#email").focus(); return; }
  $("#loginBtn").disabled = true; $("#loginBtn").textContent = "Signing in…";
  try {
    const r = await fetch("/ui/login", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({email})});
    const body = await r.json();
    if (!r.ok) { err.textContent = body.detail || "Sign-in failed."; return; }
    user = body; user.historyKey = await emailKey(email); enterApp();
  } catch { err.textContent = "Couldn't reach the server. Is it running?"; }
  finally { $("#loginBtn").disabled = false; $("#loginBtn").innerHTML = loginLabel; }
};
$("#email").addEventListener("input", () => { $("#loginErr").textContent = ""; });
$("#logoutBtn").onclick = () => { user = null; convs = []; current = null; $("#app").classList.add("hidden"); $("#login").classList.remove("hidden"); $("#email").value = ""; $("#email").focus(); };

async function loadStatus(){
  const el = $("#aiStatus");
  try {
    const s = await (await fetch("/ui/status")).json();
    el.className = "status " + (s.ai ? "on" : "off");
    el.querySelector(".lbl").textContent = s.ai ? "AI online" : "Keyword mode";
    el.title = s.ai ? `AI answers enabled (${s.provider})` : "No AI key configured: answers are keyword-based passages from the documents";
  } catch { el.className = "status"; el.querySelector(".lbl").textContent = "Offline"; }
}

function enterApp(){
  $("#login").classList.add("hidden"); $("#app").classList.remove("hidden");
  const name = user.name || user.employee_id, first = name.split(" ")[0];
  $("#avatar").textContent = name.split(" ").map(w => w[0]).slice(0, 2).join("").toUpperCase();
  $("#pName").textContent = name;
  $("#pMeta").innerHTML = `<span class="role-pill">${esc({hr:"HR",manager:"Manager",employee:"Employee"}[user.role] || user.role)}</span>${esc(user.employee_id)} · ${esc(user.region)}${user.demo ? " · demo" : ""}`;
  const h = new Date().getHours();
  $("#greet").innerHTML = `${h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening"}, <em>${esc(first)}</em>`;
  renderCards(); loadStatus();
  clearInterval(statusTimer); statusTimer = setInterval(loadStatus, 30000);   // live AI / keyword-mode status
  convs = store.get(convKey(), []);
  convs.length ? openConv(convs[0].id) : newConv();
  $("#q").focus();
}
// Chat history belongs to the email that signed in (not the employee ID: every unknown email signs in as
// the same demo employee). The key is a hash, so the email itself is not stored in the browser.
const convKey = () => "po.convs." + user.historyKey;
async function emailKey(email){
  const norm = email.trim().toLowerCase();
  try {
    const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode("veltrix-history|" + norm));
    return [...new Uint8Array(buf)].slice(0, 16).map(b => b.toString(16).padStart(2, "0")).join("");
  } catch {
    let h = 5381; for (const ch of norm) h = ((h * 33) ^ ch.charCodeAt(0)) >>> 0;
    return "h" + h.toString(16);
  }
}
const saveConvs = () => store.set(convKey(), convs.slice(0, 40));

/* ---------------- suggestions ---------------- */
function renderCards(){
  const c = [
    {i:"heart", t:"Sick leave", s:"How many sick days do I get?"},
    {i:"chart", t:"My balance", s:"What is my leave balance?"},
    {i:"cal", t:"Holidays", s:"Which public holidays are left this year?"},
    {i:"plane", t:"Book time off", s:"Apply annual leave from 14 Dec to 18 Dec"},
  ];
  if (user.role === "manager") c[2] = {i:"team", t:"My team", s:"Who in my team is on leave next week?"};
  if (user.role === "hr") c[2] = {i:"team", t:"Look up an employee", s:"What is the leave balance of E2004?"};
  $("#cards").innerHTML = c.map(x => `<button class="card" type="button" data-q="${esc(x.s)}"><span class="ic">${ICON[x.i]}</span><div><b>${esc(x.t)}</b><span>${esc(x.s)}</span></div><span class="go">${ICON.arrow}</span></button>`).join("");
  $("#cards").querySelectorAll(".card").forEach(b => b.onclick = () => send(b.dataset.q));
}
const FOLLOW = {
  rag: ["What is my leave balance?", "How much annual leave can I carry forward?", "Which public holidays are left this year?"],
  tool: ["Show my leave requests", "How many sick days do I get?", "Which public holidays are left this year?"],
  hybrid: ["Show my leave requests", "Apply annual leave next week"],
  clarify: [], no_answer: ["How many sick days do I get?", "What is my leave balance?"],
  refuse: ["What can you help me with?", "How many sick days do I get?"],
};
function followups(res){
  if (res.pending_confirmation || res.error) return [];
  if (res.route === "clarify" && /what can i do for you/i.test(res.answer)) return ["How many sick days do I get?", "What is my leave balance?", "Which public holidays are left this year?"];
  const norm = s => s.toLowerCase().replace(/[^a-z0-9 ]/g, "").trim();
  const asked = new Set(current.messages.filter(m => m.role === "user").map(m => norm(m.text)));
  return (FOLLOW[res.route] || []).filter(s => !asked.has(norm(s))).slice(0, 3);
}

/* ---------------- conversations ---------------- */
function newConv(){
  current = {id: uid(), session_id: uid(), title: "New chat", messages: [], ts: Date.now()};
  renderThread(); renderList(); closeSidebar(); $("#q").focus();
}
function openConv(id){
  current = convs.find(c => c.id === id) || current; renderThread(); renderList(); closeSidebar();
}
function renderList(){
  const startOfDay = new Date(); startOfDay.setHours(0, 0, 0, 0);
  const groups = [["Today", convs.filter(c => c.ts >= startOfDay)], ["Earlier", convs.filter(c => c.ts < startOfDay)]];
  const item = c => `<li class="conv ${current && c.id === current.id ? "active" : ""}">
    <button class="open" type="button" data-id="${c.id}" title="${esc(c.title)}">${esc(c.title)}</button>
    <button class="icon-btn del" type="button" data-del="${c.id}" aria-label="Delete conversation">${ICON.trash}</button></li>`;
  $("#convList").innerHTML = convs.length
    ? groups.filter(([, l]) => l.length).map(([label, l]) => `<li class="side-label">${label}</li>${l.map(item).join("")}`).join("")
    : `<li class="side-label">Recent</li><li class="empty-list">Your conversations will appear here.</li>`;
  $("#convList").querySelectorAll("[data-id]").forEach(b => b.onclick = () => openConv(b.dataset.id));
  $("#convList").querySelectorAll("[data-del]").forEach(b => b.onclick = () => {
    convs = convs.filter(c => c.id !== b.dataset.del); saveConvs();
    if (current.id === b.dataset.del) convs.length ? openConv(convs[0].id) : newConv(); else renderList();
  });
  $("#convTitle").textContent = current ? current.title : "New chat";
}
function renderThread(){
  $("#thread").innerHTML = "";
  $("#welcome").classList.toggle("hidden", current.messages.length > 0);
  current.messages.forEach((m, i) => m.role === "user" ? addUser(m.text) : addBot(m.res, false, i === current.messages.length - 1));
  scrollDown(false);
}

/* ---------------- rendering ---------------- */
function md(text){
  const lines = esc(text).split(/\n/); let html = "", list = null;
  const inline = s => s.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/`([^`]+)`/g, "<code>$1</code>");
  const close = () => { if (list) { html += `</${list}>`; list = null; } };
  for (const raw of lines) {
    const l = raw.trimEnd(), ul = l.match(/^\s*[-*•]\s+(.*)$/), ol = l.match(/^\s*\d+[.)]\s+(.*)$/);
    if (ul || ol) { const t = ul ? "ul" : "ol"; if (list !== t) { close(); html += `<${t}>`; list = t; } html += `<li>${inline((ul || ol)[1])}</li>`; }
    else if (!l.trim()) close();
    else { close(); html += `<p>${inline(l)}</p>`; }
  }
  close(); return html;
}
function addUser(text){
  $("#welcome").classList.add("hidden");
  const d = document.createElement("div"); d.className = "msg user";
  d.innerHTML = `<div class="bubble">${esc(text)}</div>`; $("#thread").appendChild(d);
}
function botShell(){
  const d = document.createElement("div"); d.className = "msg bot";
  d.innerHTML = `<div class="bot-mark" aria-hidden="true">${ICON.mark}</div><div class="bot-body"></div>`;
  $("#thread").appendChild(d); return d;
}
const STAGES = {
  received: "Reading your message…", understanding: "Understanding your question…",
  checking_leave: "Checking your leave records…", checking_rules: "Checking leave rules and holidays…",
  searching: "Searching HR documents you can access…", writing: "Writing the answer…",
  keyword: "Searching documents by keyword…", saving: "Saving your change…",
};
function addTyping(){
  const d = botShell();
  d.querySelector(".bot-body").innerHTML = `<div class="typing" role="status"><span class="dots"><i></i><i></i><i></i></span><span class="stage">Thinking…</span></div><ol class="steps"></ol>`;
  return d;
}
function setStage(el, ev){
  const label = ev.stage === "found"
    ? `Found ${ev.passages} relevant passage${ev.passages === 1 ? "" : "s"} in ${ev.documents} document${ev.documents === 1 ? "" : "s"}`
    : STAGES[ev.stage];
  if (!label) return;
  const stage = el.querySelector(".stage"), steps = el.querySelector(".steps");
  if (!stage) return;
  if (stage.textContent && stage.textContent !== "Thinking…" && steps) {
    const li = document.createElement("li"); li.innerHTML = `${ICON.check}<span>${esc(stage.textContent.replace(/…$/, ""))}</span>`; steps.appendChild(li);
  }
  stage.textContent = label;
  scrollDown(true);
}
function addBot(res, animate, isLast, el){
  document.querySelectorAll(".followups").forEach(f => f.remove());
  const d = el || botShell(), body = d.querySelector(".bot-body");
  if (res.error) { body.innerHTML = `<div class="md error-note">${esc(res.error)}</div>`; return; }
  const [rLabel, rIcon] = ROUTE[res.route] || [res.route, ""];
  const cites = res.citations || [];
  const sources = cites.length ? `<div class="sources"><div class="sources-label">Sources</div><div class="src-list">${
    cites.map((c, i) => `<button type="button" class="src" data-doc="${esc(c.doc_id)}" title="Open ${esc(c.doc_id)} (${esc(c.locator)})"><span class="n">${i + 1}</span><b>${esc(c.doc_id)}</b><span>${esc(c.locator)}</span></button>`).join("")}</div></div>` : "";
  const calls = res.tool_calls || [];
  const tools = calls.length ? `<details class="tools"><summary>${ICON.tool} Used ${calls.length} tool${calls.length > 1 ? "s" : ""}: ${calls.map(t => esc(t.name)).join(", ")}<span class="chev">${ICON.chev}</span></summary>${
    calls.map(t => `<div class="tool-row"><span class="tool-name">${esc(t.name)}</span><span class="st s-${esc(t.status)}">${esc(t.status.replace("_", " "))}</span><span class="args">${esc(JSON.stringify(t.args))}</span></div>`).join("")}</details>` : "";
  const proposal = res.pending_confirmation;
  const buttons = proposal && isLast ? `<div class="confirm"><button class="btn btn-primary" type="button" data-say="yes">${ICON.check} Confirm</button><button class="btn" type="button" data-say="No, don't do it">Not now</button></div>` : "";
  const answerBlock = proposal
    ? `<div class="proposal"><div class="proposal-head">${ICON.clock} Awaiting your confirmation</div><div class="md"></div>${buttons}</div>`
    : `<div class="md"></div>`;
  body.innerHTML = `${answerBlock}<div class="meta"><span class="route r-${esc(res.route)}">${rIcon}${esc(rLabel)}</span></div>${sources}${tools}
    <div class="actions">
      <button class="icon-btn" type="button" data-copy aria-label="Copy answer" title="Copy">${ICON.copy}</button>
      <button class="icon-btn" type="button" data-rate="up" aria-label="Good answer" title="Good answer">${ICON.up}</button>
      <button class="icon-btn" type="button" data-rate="down" aria-label="Bad answer" title="Bad answer">${ICON.down}</button>
      ${isLast && !res.pending_confirmation ? `<button class="icon-btn" type="button" data-regen aria-label="Regenerate" title="Ask again">${ICON.regen}</button>` : ""}
    </div>`;
  body.querySelectorAll("[data-rate]").forEach(b => b.onclick = () => {
    body.querySelectorAll("[data-rate]").forEach(x => x.classList.remove("on")); b.classList.add("on");
    fetch("/ui/feedback", {method: "POST", headers: {"Content-Type": "application/json", "X-Employee-Id": user.employee_id},
      body: JSON.stringify({request_id: res.request_id, rating: b.dataset.rate})}).catch(() => {});
  });
  const regen = body.querySelector("[data-regen]");
  if (regen) regen.onclick = () => {
    const lastUser = [...current.messages].reverse().find(m => m.role === "user");
    if (lastUser) send(lastUser.text);
  };
  const mdEl = body.querySelector(".md"), meta = [...body.querySelectorAll(".meta,.sources,.tools,.confirm")];
  body.querySelectorAll("[data-say]").forEach(b => b.onclick = () => { body.querySelector(".confirm")?.remove(); send(b.dataset.say); });
  body.querySelector("[data-copy]").onclick = e => { navigator.clipboard?.writeText(res.answer); e.currentTarget.innerHTML = ICON.check; };
  const finish = () => {
    meta.forEach(m => m.style.visibility = "");
    if (!isLast) return;
    const f = followups(res);
    if (f.length) {
      const row = document.createElement("div"); row.className = "followups";
      row.innerHTML = f.map(s => `<button type="button">${esc(s)}</button>`).join("");
      row.querySelectorAll("button").forEach(b => b.onclick = () => send(b.textContent));
      d.after(row);
    }
    scrollDown(true);
  };
  if (!animate || reduced) { mdEl.innerHTML = md(res.answer); finish(); return; }
  meta.forEach(m => m.style.visibility = "hidden");
  const words = res.answer.split(/(\s+)/); let i = 0;
  const step = () => {
    i = Math.min(words.length, i + 3); mdEl.innerHTML = md(words.slice(0, i).join("")); scrollDown(true);
    if (i < words.length) setTimeout(step, 16); else finish();
  };
  step();
}
function scrollDown(smooth){ const s = $("#scroller"); s.scrollTo({top: s.scrollHeight, behavior: smooth && !reduced ? "smooth" : "auto"}); }
$("#scroller").addEventListener("scroll", () => $("#topbar").classList.toggle("scrolled", $("#scroller").scrollTop > 4));

/* ---------------- send ---------------- */
async function send(text){
  text = (text || "").trim(); if (!text || busy) return;
  busy = true; setSendMode();
  document.querySelectorAll(".confirm,.followups").forEach(c => c.remove());
  if (!convs.find(c => c.id === current.id)) { current.title = text.slice(0, 48); convs.unshift(current); }
  current.messages.push({role: "user", text}); current.ts = Date.now(); saveConvs(); renderList();
  addUser(text); const t = addTyping(); scrollDown(true);
  let res;
  controller = new AbortController();
  try {
    // Streaming endpoint: live progress events, then the same JSON body as POST /chat.
    const r = await fetch("/chat/stream", {method:"POST", signal: controller.signal,
      headers:{"Content-Type":"application/json","X-Employee-Id":user.employee_id},
      body: JSON.stringify({session_id: current.session_id, message: text})});
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      if (r.status === 403) { current.session_id = uid(); res = {error: "This conversation belonged to another account, so a new session was started. Please send your message again."}; }
      else if (r.status === 401) { res = {error: "Your account was not recognised. Please sign in again."}; setTimeout(() => $("#logoutBtn").click(), 1500); }
      else res = {error: body.detail || `Request failed (${r.status}).`};
    } else {
      res = await readStream(r, ev => setStage(t, ev));
    }
  } catch (e) {
    res = {error: e.name === "AbortError" ? "Stopped." : "Couldn't reach the assistant. Check that the server is running."};
  }
  controller = null;
  if (!res.error) { current.messages.push({role: "bot", res}); saveConvs(); }
  addBot(res, true, true, t);
  busy = false; setSendMode(); $("#q").focus();
}
async function readStream(r, onProgress){
  const reader = r.body.getReader(), dec = new TextDecoder(); let buf = "", result = null;
  for (;;) {
    const {value, done} = await reader.read(); if (done) break;
    buf += dec.decode(value, {stream: true});
    let i;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const block = buf.slice(0, i); buf = buf.slice(i + 2);
      const ev = (block.match(/^event: (.*)$/m) || [])[1], data = (block.match(/^data: (.*)$/m) || [])[1];
      if (!ev || !data) continue;
      const parsed = JSON.parse(data);
      if (ev === "progress") onProgress(parsed);
      else if (ev === "result") result = parsed;
      else if (ev === "error") result = {error: parsed.detail || "Something went wrong."};
    }
  }
  return result || {error: "The response ended unexpectedly."};
}
let controller = null;
const STOP_ICON = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><rect x="5" y="5" width="14" height="14" rx="3"/></svg>';
const SEND_ICON = $("#sendBtn").innerHTML;
function setSendMode(){
  const b = $("#sendBtn");
  if (busy) { b.innerHTML = STOP_ICON; b.disabled = false; b.setAttribute("aria-label", "Stop"); b.classList.add("stop"); }
  else { b.innerHTML = SEND_ICON; b.setAttribute("aria-label", "Send message"); b.classList.remove("stop"); updateSend(); }
}
const q = $("#q");
const updateSend = () => { $("#sendBtn").disabled = !busy && !q.value.trim(); };
q.addEventListener("input", () => { q.style.height = "auto"; q.style.height = Math.min(q.scrollHeight, 200) + "px"; updateSend(); });
q.addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("#composer").requestSubmit(); } });
$("#composer").onsubmit = e => {
  e.preventDefault();
  if (busy) { controller?.abort(); return; }          // the send button is a Stop button while busy
  const v = q.value; q.value = ""; q.style.height = "auto"; send(v);
};
$("#newChat").onclick = newConv;

/* ---------------- mobile sidebar ---------------- */
const closeSidebar = () => { $("#sidebar").classList.remove("open"); $("#scrim").classList.remove("show"); };
$("#menuBtn").onclick = () => { $("#sidebar").classList.add("open"); $("#scrim").classList.add("show"); };
$("#scrim").onclick = closeSidebar;

if (user) enterApp(); else $("#email").focus();
})();


// Citation chips open the source document in a new tab (via a short-lived signed link, access checked server-side).
document.addEventListener("click", async ev => {
  const chip = ev.target.closest(".src[data-doc]");
  if (!chip || !user) return;
  const tab = window.open("about:blank", "_blank");     // open now so pop-up blockers allow it
  try {
    const r = await fetch("/ui/doc-link", {method: "POST",
      headers: {"Content-Type": "application/json", "X-Employee-Id": user.employee_id},
      body: JSON.stringify({doc_id: chip.dataset.doc})});
    if (!r.ok) throw new Error();
    const {url} = await r.json();
    if (tab) tab.location.href = url; else window.location.href = url;
  } catch {
    if (tab) tab.close();
    alert("This document can't be opened right now.");
  }
});
