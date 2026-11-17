// The "Tasks" tab: on the left the list of tasks — create, delete with the cross; on the right the whole conversation on
// the open task from different sessions and a button to go to the last session (owner 08.10). Data — the plugin channel
// `tasks` (API 1.2): the extension's process keeps a mirror of its registry in it (mirror.py) — topics and history
// entries, long entries in parts (part/of). The page writes its own requests (create, delete) into the same channel as
// the owner. Markup, the participant's byline and time are modules of the hub's page: the conversation looks the same as
// in a session's feed. They are not part of the plugin API (review 12.10): if they change in the core, the tab goes on
// in a plain view instead of disappearing.
// Language: English by default, Russian when the page language is Russian (hub.lang, plugin API 1.25).
let RU = false;
const tr = (en, ru) => (RU ? ru : en);
const [mdMod, whoMod, domMod] = await Promise.all(["/static/js/markdown.js", "/static/js/who.js", "/static/js/dom.js"]
  .map((u) => import(u).catch(() => ({}))));
const plain = (tag, cls, text) => { const n = document.createElement(tag); n.className = cls; n.textContent = text; return n; };
const md = typeof mdMod.md === "function" ? mdMod.md : (text) => plain("div", "tk-plain", text);
const byline = typeof whoMod.byline === "function" ? whoMod.byline : (name) => plain("b", "byline", name || tr("You", "Вы"));
const stamp = typeof domMod.stamp === "function" ? domMod.stamp
  : (ts) => plain("span", "tk-time", new Date(ts * 1000).toLocaleString(hub?.locale || undefined, { dateStyle: "short", timeStyle: "short" }));

// a core protocol value, not text to translate: how the core signs the plugin process's posts; from the catalog —
// "плагин <account>:tasks"
let AUTHOR = "плагин tasks";
let hub = null;
const el = (...a) => hub.el(...a);

const store = new Map();     // post key (mirror.py: key) → the data of the last post with this key
const closing = new Set();   // tasks the owner deleted and the process has not confirmed yet
let creating = [];           // [{title, at}] — being created: the post went, the task has not come yet
let since = 0, loading = false, loaded = false, failed = "";
let open = null;             // id of the open task
let view = "history";        // what is on the right for the open task: "History" or "Progress" (owner 09.10)
const deleting = new Map();  // id → {title, until, timer} — the cross was pressed: the card stays with a countdown, deleted by the timer
const SKEW = 300;            // s — the browser's and the process's clocks differ (review 12.10), as engine.SKEW
const UNDO_MS = 5000;        // how long one can change one's mind (owner 10.10: deleting "seemed to hang" — the countdown is visible)
let ticker = null;           // updates "Deleting in N s" while there is something to delete
const asking = new Map();    // task id → {at} — the page asked for a summary, the new one has not come yet
const asked = new Map();     // task id → when "Progress" asked for a summary by itself (not to ask over and over)
const ASK_AGAIN = 300000;    // ms — "Progress" asks by itself no more often
const ASK_EVERY = 1800;      // s — on "Progress" no more often (owner 12.10; the process checks itself: summary.EVERY)
const ASK_WAIT = 120000;     // ms — a summary is not awaited longer: "Refresh" is available again
// order and pinning (owner 12.10: "move in order and pin, like on a board") — the owner's taste, kept in this browser:
// the process and the channel do not need them. Pinned ones on top; dragged ones in their order; new and never touched
// ones on top of their group, by the last message
const ORDER_KEY = "tasks.order", PIN_KEY = "tasks.pinned";
const saved = (k) => { try { const v = JSON.parse(localStorage.getItem(k) || "[]"); return Array.isArray(v) ? v : []; } catch { return []; } };
const keep = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode — until a reload */ } };
let order = saved(ORDER_KEY);
const pinned = new Set(saved(PIN_KEY));
let dragging = null;         // id of the task being dragged: while it is dragged, the list is not redrawn
let ui = null;               // the view's nodes: built once, the input field does not lose what is typed between snapshots

const key = (d) => (d.kind === "topic" ? `t:${d.id}` : d.kind === "progress" ? `p:${d.topic}:${d.part}`
  : `e:${d.topic}:${d.id}:${d.part}`);

async function sync() {
  if (loading) return false;
  loading = true;
  try {
    const posts = await hub.feed(since);
    for (const p of posts) {
      since = Math.max(since, Number(p.id.slice(1)));
      let d;
      try { d = JSON.parse(p.text); } catch { continue; }
      if (!d || typeof d !== "object") continue;
      if (p.by === "owner" && d.kind === "topic_close") closing.add(d.id);
      if (p.by === "owner" && d.kind === "topic_reopen") closing.delete(d.id);
      if (p.by === AUTHOR && ["topic", "entry", "progress"].includes(d.kind)) store.set(key(d), d);
    }
    failed = "";
    loaded = true;
    return posts.length > 0;
  } catch (e) {
    failed = tr("Could not load the tasks. Reload the page; if that does not help, check that the tasks extension process is running.",
      "Не удалось загрузить задачи. Обновите страницу; если не поможет — проверьте, что процесс расширения tasks запущен.");
    return true;
  } finally {
    loading = false;
  }
}

function topics() {
  const all = [...store.values()].filter((d) => d.kind === "topic" && d.state === "open" && !closing.has(d.id));
  // created with the button — by the title; with the /new-task command — by time (the process gives the title)
  creating = creating.filter((c) => !all.some((t) => (c.after ? t.created >= c.after - SKEW : t.title === c.title))
    && Date.now() - c.at < 60000);
  const rank = new Map(order.map((id, i) => [id, i]));
  const at = (t) => rank.get(t.id) ?? -1;
  return all.map((t) => ({ ...t, last: lastTs(t.id), pinned: pinned.has(t.id) }))
    .sort((a, b) => (b.pinned - a.pinned) || (at(a) - at(b)) || (b.last - a.last));
}

// move task id to the place of task to (before — in front of it, otherwise after); the order is the whole visible list
function move(id, to, before) {
  if (id === to) return;
  const ids = topics().map((t) => t.id).filter((x) => x !== id);
  const i = ids.indexOf(to);
  if (i < 0) return;
  ids.splice(before ? i : i + 1, 0, id);
  order = ids;
  keep(ORDER_KEY, order);
  paintList();
}

function togglePin(t) {
  if (pinned.has(t.id)) pinned.delete(t.id); else pinned.add(t.id);
  for (const id of [...pinned]) if (!store.has(`t:${id}`)) pinned.delete(id);   // deleted ones are not kept
  keep(PIN_KEY, [...pinned]);
  paintList();
}

function entries(tid) {
  const byId = new Map();
  const rev = store.get(`t:${tid}`)?.rev || 0;   // entries of a previous build of the history (mirror.py: rev) are not shown
  for (const d of store.values()) {
    if (d.kind !== "entry" || d.topic !== tid || (d.rev || 0) !== rev) continue;
    if (!byId.has(d.id)) byId.set(d.id, []);
    byId.get(d.id).push(d);
  }
  const out = [];
  for (const [id, all] of byId) {
    // the entry got shorter — the tail of the previous version (another of) stays in the channel: take the parts of one version (review 12.10)
    const of = all.find((p) => p.part === 0)?.of;
    const parts = all.filter((p) => p.of === of && p.part < of).sort((a, b) => a.part - b.part);
    if (!parts.length || parts.length !== of) continue;   // not all parts have come — do not show it half-assembled
    out.push({ id, who: parts[0].who, session: parts[0].session, ts: parts[0].ts, text: parts.map((p) => p.text).join("") });
  }
  return out.sort((a, b) => a.ts - b.ts || (a.id < b.id ? -1 : 1));
}

// a task's progress (summary.py) — JSON in parts (part/of): assemble and parse; not all parts have come — null
function progress(tid) {
  let parts = [...store.values()].filter((d) => d.kind === "progress" && d.topic === tid).sort((a, b) => a.part - b.part);
  // the progress got shorter — tail parts of the previous one (with another of) stay in the channel: take the parts of one progress
  parts = parts.filter((p) => p.of === parts[0]?.of && p.part < p.of);
  if (!parts.length || parts.length !== parts[0].of) return null;
  try { return JSON.parse(parts.map((p) => p.text).join("")); } catch { return null; }
}

// a summary costs tokens (owner 12.10) — the process makes it only on request: "Progress" is open — if the task has
// something new and 30 minutes have passed, "Refresh" — at once
function summarizing(tid) {
  const a = asking.get(tid);
  if (a && ((progress(tid)?.at || 0) >= a.at - 5 || Date.now() - a.at * 1000 > ASK_WAIT)) asking.delete(tid);
  return asking.has(tid);
}

async function askSummary(t, force) {
  const list = entries(t.id);
  if (!list.length || t.example || summarizing(t.id)) return;   // the process does not summarize the example
  const newest = list[list.length - 1].ts, p = progress(t.id);
  if (!force) {
    const due = !p?.criteria || (newest > p.at && Date.now() / 1000 - p.at >= ASK_EVERY);
    // by itself — at most once per ASK_AGAIN per task: while a turn runs, the last entry changes every few seconds
    if (!due || Date.now() - (asked.get(t.id) || 0) < ASK_AGAIN) return;
    asked.set(t.id, Date.now());
  }
  asking.set(t.id, { at: Date.now() / 1000 });
  let r;
  try { r = await hub.postFeed(JSON.stringify({ kind: "topic_summarize", id: t.id, force })); } catch (e) { r = { error: String(e) }; }
  if (r?.error) asking.delete(t.id);
  if (open === t.id) paintDetail();
}

// the task's Jira key (engine.py: jira_link) — as a link to the task in Jira; it is not needed in the title any more
const bare = (t) => (t.jira && t.title.startsWith(`${t.jira} · `) ? t.title.slice(t.jira.length + 3) : t.title);
function jiraLink(t, cls) {
  const url = hub.snapshot()?.jira?.url;
  if (!t.jira || !url) return null;
  const a = el("a", cls, `${t.jira} ↗`);
  a.href = `${url}/browse/${encodeURIComponent(t.jira)}`;
  a.target = "_blank";
  a.rel = "noopener";
  a.title = tr("Open the task in Jira", "Открыть задачу в Jira");
  return a;
}

function lastTs(tid) {
  let last = 0;
  for (const e of entries(tid)) last = Math.max(last, e.ts);
  const t = store.get(`t:${tid}`);
  return Math.max(last, t?.created || 0);
}

function count(n) {
  if (!RU) return `${n} ${n === 1 ? "message" : "messages"}`;
  const m10 = n % 10, m100 = n % 100;
  const w = m10 === 1 && m100 !== 11 ? "сообщение" : m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14) ? "сообщения" : "сообщений";
  return `${n} ${w}`;
}

// --- left column ----------------------------------------------------------------------------------------------------
function buildForm() {
  const form = el("form", "tk-new");
  const input = el("input", "tk-new-input");
  input.type = "text";
  input.maxLength = 200;
  input.placeholder = tr("New task title", "Название новой задачи");
  input.setAttribute("aria-label", tr("New task title", "Название новой задачи"));
  const btn = el("button", "btn primary", tr("Create", "Создать"));
  btn.type = "submit";
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const title = input.value.trim();
    if (!title) { input.focus(); return; }   // an empty title — back to the field, the button is not disabled without a reason
    btn.disabled = true;
    let r;   // postFeed does not throw — it returns the error in {error} (review 12.10: the field was cleared silently)
    try { r = await hub.postFeed(JSON.stringify({ kind: "topic_create", title })); } catch (e) { r = { error: String(e) }; }
    if (r?.error) ui.formError.textContent = tr("The task was not created. Try again.", "Задача не создалась. Попробуйте ещё раз.");
    else {
      creating.push({ title, at: Date.now() });
      input.value = "";
      ui.formError.textContent = "";
    }
    btn.disabled = false;
    paintList();
  });
  form.append(input, btn);
  return form;
}

function card(t) {
  if (deleting.has(t.id)) return deletingCard(t, deleting.get(t.id));
  // the card is a container; opening the task is its own button, deleting — the next one (a button in a button breaks the keyboard)
  const c = el("div", "tk-card");
  c.setAttribute("aria-current", String(t.id === open));
  const main = el("button", "tk-card-main");
  main.type = "button";
  main.append(el("span", "tk-card-title", bare(t)));
  if (t.summary) main.append(el("span", "tk-card-summary", t.summary));
  const n = t.count ?? entries(t.id).length;   // the channel has the last entries, count — how many there are in total (mirror.py)
  const meta = el("span", "tk-card-meta", n ? `${count(n)} · ` : tr("No messages yet · ", "Сообщений пока нет · "));
  meta.append(stamp(t.last));
  main.append(meta);
  main.addEventListener("click", () => { open = t.id; paint(); });
  // from the keyboard: Alt+↑ / Alt+↓ — move the card
  main.addEventListener("keydown", (e) => {
    if (!e.altKey || !["ArrowUp", "ArrowDown"].includes(e.key)) return;
    e.preventDefault();
    const ids = topics().map((x) => x.id), i = ids.indexOf(t.id), j = i + (e.key === "ArrowUp" ? -1 : 1);
    if (j < 0 || j >= ids.length) return;
    move(t.id, ids[j], e.key === "ArrowUp");
    ui.list.querySelector(`[data-id="${CSS.escape(t.id)}"] .tk-card-main`)?.focus();
  });
  const x = el("button", "tk-x", "×");
  x.type = "button";
  x.title = tr("Delete the task", "Удалить задачу");
  x.setAttribute("aria-label", tr(`Delete the task "${t.title}"`, `Удалить задачу «${t.title}»`));
  x.addEventListener("click", () => remove(t));
  const pin = el("button", "tk-pin");
  pin.type = "button";
  pin.title = t.pinned ? tr("Unpin", "Открепить") : tr("Pin to the top", "Закрепить сверху");
  pin.setAttribute("aria-pressed", String(t.pinned));
  pin.setAttribute("aria-label", t.pinned ? tr(`Unpin the task "${t.title}"`, `Открепить задачу «${t.title}»`)
    : tr(`Pin the task "${t.title}"`, `Закрепить задачу «${t.title}»`));
  const ico = el("span", "tk-pin-ico");
  ico.style.setProperty("--pin", `url("${hub.url("web/pin.svg")}")`);
  pin.append(ico);
  pin.addEventListener("click", () => togglePin(t));
  c.append(main, pin, x);
  c.dataset.id = t.id;
  c.classList.toggle("is-pinned", t.pinned);
  dragCard(c, t);
  const jira = jiraLink(t, "tk-jira tk-card-jira");
  if (jira) { c.classList.add("has-jira"); c.append(jira); }
  return c;
}

// dragging a card with the mouse: a bar shows where it will go
function dragCard(c, t) {
  c.draggable = true;
  const side = (e) => e.clientY < c.getBoundingClientRect().top + c.offsetHeight / 2;
  c.addEventListener("dragstart", (e) => {
    dragging = t.id;
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", t.title);
    requestAnimationFrame(() => c.classList.add("is-dragging"));
  });
  c.addEventListener("dragover", (e) => {
    if (!dragging || dragging === t.id) return;
    e.preventDefault();
    const before = side(e);
    c.classList.toggle("drop-before", before);
    c.classList.toggle("drop-after", !before);
  });
  c.addEventListener("dragleave", () => c.classList.remove("drop-before", "drop-after"));
  c.addEventListener("drop", (e) => {
    e.preventDefault();
    const id = dragging;
    dragging = null;
    if (id) move(id, t.id, side(e));
  });
  c.addEventListener("dragend", () => { dragging = null; paintList(); });
}

// the cross: the card stays in place — "Deleting in N s", a bar and "Undo"; the task is deleted by the timer (the history
// stays with the extension): undo is better than "are you sure?", and the countdown is visible — it does not look hung
function remove(t) {
  if (deleting.has(t.id)) return;
  deleting.set(t.id, { title: t.title, until: Date.now() + UNDO_MS, timer: setTimeout(() => commitRemove(t.id), UNDO_MS) });
  ticker ??= setInterval(() => {
    document.querySelectorAll(".tk-del-left").forEach((n) => { n.textContent = leftText(Number(n.dataset.until)); });
    if (!deleting.size) { clearInterval(ticker); ticker = null; }
  }, 250);
  paintList();
  ui.status.textContent = tr(`The task "${t.title}" will be deleted in ${UNDO_MS / 1000} seconds. To undo — the button in its card`,
    `Задача «${t.title}» удалится через ${UNDO_MS / 1000} секунд. Отменить — кнопка в её карточке`);
}

const leftText = (until) => { const s = Math.max(1, Math.ceil((until - Date.now()) / 1000)); return tr(`Deleting in ${s} s`, `Удалится через ${s} с`); };

function cancelRemove(id) {
  const d = deleting.get(id);
  if (!d) return;
  clearTimeout(d.timer);
  deleting.delete(id);
  paintList();
  ui.status.textContent = tr(`The task "${d.title}" stays`, `Задача «${d.title}» остаётся`);
}

async function commitRemove(id) {
  const d = deleting.get(id);
  if (!d) return;
  deleting.delete(id);
  closing.add(id);
  if (open === id) open = null;
  paint();
  ui.status.textContent = tr(`The task "${d.title}" is deleted`, `Задача «${d.title}» удалена`);
  let r;
  try { r = await hub.postFeed(JSON.stringify({ kind: "topic_close", id })); } catch (e) { r = { error: String(e) }; }
  if (r?.error) { closing.delete(id); ui.status.textContent = tr("The task was not deleted. Try again.", "Задача не удалилась. Попробуйте ещё раз."); paint(); }
}

function deletingCard(t, d) {
  const c = el("div", "tk-card tk-deleting");
  const left = el("span", "tk-del-left", leftText(d.until));
  left.dataset.until = String(d.until);
  const undo = el("button", "btn small", tr("Undo", "Отменить"));
  undo.type = "button";
  undo.setAttribute("aria-label", tr(`Do not delete the task "${t.title}"`, `Не удалять задачу «${t.title}»`));
  undo.addEventListener("click", () => cancelRemove(t.id));
  const row = el("div", "tk-del-row");
  row.append(left, undo);
  const bar = el("span", "tk-del-bar");
  bar.style.animationDelay = `${d.until - Date.now() - UNDO_MS}ms`;   // a redraw does not restart the bar
  bar.style.animationDuration = `${UNDO_MS}ms`;
  c.append(el("span", "tk-card-title", t.title), row, bar);
  return c;
}

function paintList() {
  if (dragging) return;   // a redraw in the middle of dragging would break it off; after it — dragend
  const list = ui.list;
  const ts = topics();
  const nodes = [];
  if (failed) nodes.push(el("p", "tk-note tk-error", failed));
  for (const c of creating) {
    const p = el("div", "tk-card tk-pending");
    p.append(el("div", "tk-card-title", c.title), el("div", "tk-card-meta", tr("Creating…", "Создаётся…")));
    nodes.push(p);
  }
  nodes.push(...ts.map(card));
  if (!ts.length && !creating.length && !failed) {
    nodes.push(el("p", "tk-note", loaded ? tr("No tasks yet.", "Задач пока нет.")
      : tr("Loading the tasks…", "Загружаю задачи…")));
  }
  list.replaceChildren(...nodes);
  ui.count.textContent = ts.length ? String(ts.length) : "";
}

// --- right column --------------------------------------------------------------------------------------------------
function paintDetail() {
  const box = ui.detail;
  const t = open && topics().find((x) => x.id === open);
  if (!t) {
    open = null;
    delete box.dataset.topic;
    if (box.dataset.sig !== "onb") box.replaceChildren(onboarding());   // no task chosen — how to use the tab
    box.dataset.sig = "onb";
    return;
  }
  // nothing changed — no redraw (review 12.10: while a session works, posts come every few seconds, and a redraw reset
  // the selected text and ran the markup of the whole history again)
  const sig = JSON.stringify([t.id, view, t.title, t.summary, t.jira, t.count, t.rev, progress(t.id)?.at,
    summarizing(t.id), entries(t.id).map((e) => [e.id, e.ts, e.text.length, e.text.slice(-24)])]);
  if (box.dataset.sig === sig) return;
  box.dataset.sig = sig;
  const keep = box.dataset.topic === t.id ? box.querySelector(".tk-history") : null;
  const atEnd = keep ? keep.scrollHeight - keep.scrollTop - keep.clientHeight < 40 : true;
  const top = keep?.scrollTop || 0;

  const head = el("header", "tk-detail-head");
  const row = el("div", "tk-detail-row");
  const seg = el("div", "seg");
  seg.setAttribute("role", "group");
  seg.setAttribute("aria-label", tr("Task view", "Вид задачи"));
  for (const [id, label, title] of [["history", tr("History", "История"), tr("The whole conversation on the task", "Вся переписка по задаче")],
    ["progress", tr("Progress", "Прогресс"), tr("The task, acceptance criteria and the main decisions", "Задача, критерии приёмки и главные решения")]]) {
    const b = el("button", null, label);
    b.type = "button";
    b.title = title;
    b.setAttribute("aria-pressed", String(view === id));
    b.addEventListener("click", () => { view = id; paintDetail(); });
    seg.append(b);
  }
  const jira = jiraLink(t, "tk-jira");
  if (jira) {
    const head2 = el("div", "tk-title-wrap");
    head2.append(el("h2", "tk-detail-title", bare(t)), jira);
    row.append(head2, seg);
  } else row.append(el("h2", "tk-detail-title", t.title), seg);
  head.append(row);
  if (t.summary) head.append(el("p", "tk-detail-summary", t.summary));

  const list = entries(t.id);
  const history = view === "progress" ? progressView(t, list) : historyView(t, list);
  const nodes = [head, history];
  const last = [...list].reverse().find((e) => e.session)?.session;
  if (last) {
    const foot = el("footer", "tk-detail-foot");
    const go = el("a", "btn primary", tr(`Go to session ${last === "hub" ? "Hub" : last}`, `Перейти в сессию ${last === "hub" ? "Хаб" : last}`));
    go.href = "#/s/" + encodeURIComponent(last);
    foot.append(go, el("span", "tk-foot-hint", tr("opens the last session where the task was worked on", "откроется последняя сессия, где шла работа по задаче")));
    nodes.push(foot);
  }
  box.dataset.topic = t.id;
  box.replaceChildren(...nodes);
  if (view === "history") history.scrollTop = atEnd ? history.scrollHeight : top;
  else askSummary(t, false);
}

// above "Progress": when it was assembled and "Refresh" — reassemble now; by itself it is reassembled while open, once per 30 minutes
function refreshBar(t, p) {
  const bar = el("div", "tk-pr-bar");
  const busy = summarizing(t.id);
  const meta = el("p", "tk-pr-meta", busy ? tr("Assembling from the conversation…", "Собираю по переписке…")
    : p ? tr("Assembled from the conversation · ", "Собрано по переписке · ") : tr("Progress is not assembled yet", "Прогресс ещё не собран"));
  if (!busy && p?.at) meta.append(stamp(p.at));
  const b = el("button", "btn small tk-pr-refresh", busy ? tr("Refreshing…", "Обновляется…") : tr("Refresh", "Обновить"));
  b.type = "button";
  b.disabled = busy;
  b.title = tr("Reassemble the task, acceptance criteria and decisions from the conversation now", "Пересобрать задачу, критерии приёмки и решения по переписке сейчас");
  b.addEventListener("click", () => askSummary(t, true));
  bar.append(meta);
  if (!t.example) bar.append(b);   // the example is not summarized — the button would do nothing
  return bar;
}

function historyView(t, list) {
  const history = el("div", "tk-history");
  if (t.count > list.length && list.length) {   // the channel keeps the last entries (mirror.py: budget), the full one is with the process
    history.append(el("p", "tk-note", tr(`Showing the last ${list.length} of ${t.count} messages.`, `Показаны последние ${list.length} из ${t.count} сообщений.`)));
  }
  if (!list.length) {
    history.append(el("p", "tk-note", tr("No messages on the task yet. Write to sessions as usual — messages about this task will get here by themselves.",
      "Сообщений по задаче пока нет. Пишите сессиям как обычно — сообщения про эту задачу попадут сюда сами.")));
  }
  for (const e of list) {
    const row = el("article", `tk-entry ${e.who === "owner" ? "tk-owner" : "tk-session"}`);
    const by = el("div", "tk-entry-by");
    by.append(byline(e.who === "owner" ? null : e.session));
    if (e.who === "owner" && e.session) by.append(el("span", "tk-to", `→ ${e.session === "hub" ? tr("Hub", "Хаб") : e.session}`));
    by.append(stamp(e.ts));
    row.append(by, md(e.text));
    history.append(row);
  }
  return history;
}

// "Progress" (owner 11.10: on one page): the task, acceptance criteria with a check mark on the done ones, the most
// important decisions — the model assembles them from the conversation (summary.py). Progress of the earlier kind
// (done / left) is recounted by the process; until then "done" and "left" are shown as criteria
function progressView(t, list) {
  const box = el("div", "tk-history tk-progress");
  const p = progress(t.id);
  if (!list.length) {
    box.append(el("p", "tk-note", tr("Progress appears once there is a conversation on the task.", "Прогресс появится, когда по задаче начнётся переписка.")));
    return box;
  }
  box.append(refreshBar(t, p));
  if (!p) return box;
  const crit = p.criteria || [...(p.done || []).map((text) => ({ text, done: true })),
    ...(p.todo || []).map((text) => ({ text, done: false }))];
  const goal = el("section", "tk-pr-sec");
  goal.append(el("h3", "tk-pr-head", tr("Task", "Задача")), el("p", "tk-pr-goal", p.goal || t.title));
  const cs = el("section", "tk-pr-sec");
  const ready = crit.filter((c) => c.done).length;
  cs.append(el("h3", "tk-pr-head", crit.length ? tr(`Acceptance criteria · ${ready} of ${crit.length}`, `Критерии приёмки · ${ready} из ${crit.length}`)
    : tr("Acceptance criteria", "Критерии приёмки")));
  if (!crit.length) cs.append(el("p", "tk-pr-none", tr("Not clear yet", "Пока не ясны")));
  else {
    const ul = el("ul", "tk-pr-list tk-pr-crit");
    for (const c of crit) {
      const li = el("li", c.done ? "is-done" : null);
      li.append(el("span", "tk-pr-mark", c.done ? "✓" : ""), el("span", null, c.text));
      li.querySelector(".tk-pr-mark").setAttribute("aria-label", c.done ? tr("done", "сделано") : tr("not done", "не сделано"));
      ul.append(li);
    }
    cs.append(ul);
  }
  const ds = el("section", "tk-pr-sec tk-pr-decisions");
  ds.append(el("h3", "tk-pr-head", tr("Decisions", "Решения")));
  const dec = p.decisions || [];
  if (!dec.length) ds.append(el("p", "tk-pr-none", tr("No decisions yet", "Решений пока нет")));
  else {
    const ul = el("ul", "tk-pr-list");
    for (const x of dec) ul.append(el("li", null, x));
    ds.append(ul);
  }
  box.append(goal, cs, ds);
  return box;
}

// no task chosen — how to use the tab (owner 09.10: "onboarding while no task is chosen")
function onboarding() {
  const box = el("div", "tk-onb");
  box.append(el("h2", "tk-onb-title", tr("How tasks work", "Как работают задачи")),
    el("p", "tk-onb-lead", tr("A task gathers in one place the whole conversation on one piece of work — even if it went through several sessions.",
      "Задача собирает в одном месте всю переписку по одному делу — даже если оно шло через несколько сессий.")));
  const steps = el("ol", "tk-onb-steps");
  // /new-task is the main way (owner 12.10: "described in passing") — separately, with command examples
  const cmds = el("span", "tk-onb-cmds");
  const pair = el("span", "tk-onb-pair");   // two variants side by side (owner 12.10: "in one line, shorter")
  const jira = !!hub.snapshot()?.jira?.url;   // Jira is not set up in the hub — no Jira variant
  const variants = jira
    ? [[tr("By a Jira key", "По ключу Jira"), "/new-task PROJ-123", tr("takes the task from Jira", "возьмёт задачу из Jira")],
      [tr("By a description", "По описанию"), tr("/new-task fix the sign-in link", "/new-task починить вход"),
        tr("finds the task in Jira or offers to create one", "найдёт задачу в Jira или предложит завести")]]
    : [[tr("By a description", "По описанию"), tr("/new-task fix the sign-in link", "/new-task починить вход"),
      tr("the request goes to the session, the task is created by itself", "запрос уйдёт сессии, задача заведётся сама")]];
  for (const [label, cmd, what] of variants) {
    const v = el("span", "tk-onb-cmd");
    v.append(el("span", "tk-onb-cmd-label", label), el("code", null, cmd), el("span", null, what));
    pair.append(v);
  }
  cmds.append(el("span", null, tr("In the input field of any session:", "В поле любой сессии:")), pair,
    el("span", null, jira ? tr("Without Jira — name the work in the field on the left.", "Без Jira — назовите дело в поле слева.")
      : tr("Or name the work briefly in the field on the left.", "Или коротко назовите дело в поле слева.")));
  for (const [head, text] of [
    [tr("Create a task with the /new-task command", "Создайте задачу командой /new-task"), cmds],
    [tr("Write to sessions as usual", "Пишите сессиям как обычно"),
      tr("Your messages about this work, the sessions' replies and the results of assignments get into the task by themselves. Answer a session with the \"Reply\" button on its line — then your answer surely gets into the same task.",
        "Ваши сообщения про это дело, ответы сессий и итоги поручений попадут в задачу сами. Отвечайте сессии кнопкой «Ответить» на её реплику — тогда ваш ответ точно попадёт в ту же задачу.")],
    [tr("Open a task on the left", "Откройте задачу слева"),
      tr("Here you will see the whole conversation and a short summary, and the button below opens the session where you stopped.",
        "Здесь будет вся переписка и короткий пересказ, а кнопка внизу откроет сессию, где вы остановились.")],
  ]) {
    const li = el("li");
    li.append(el("b", null, head), typeof text === "string" ? el("span", null, text) : text);
    steps.append(li);
  }
  const go = el("button", "btn primary", tr("Create a task", "Создать задачу"));
  go.type = "button";
  go.addEventListener("click", () => ui.root.querySelector(".tk-new-input")?.focus());
  box.append(steps, go, el("p", "tk-onb-hint", tr("Cards can be dragged, the pin keeps a task on top. Delete — with the cross: for 5 seconds, while the countdown runs, the deletion can be undone.",
    "Карточки можно перетаскивать, булавка закрепляет задачу сверху. Удалить — крестиком: 5 секунд, пока идёт отсчёт, удаление можно отменить.")));
  return box;
}

function paint() {
  paintList();
  paintDetail();
}

function build(root) {
  const wrap = el("div", "tk");
  const side = el("section", "tk-side");
  side.setAttribute("aria-label", tr("Task list", "Список задач"));
  const title = el("div", "tk-side-head");
  const count = el("span", "tk-count");
  title.append(el("h2", "tk-side-title", tr("Tasks", "Задачи")), count);
  const formError = el("p", "tk-form-error");
  formError.setAttribute("role", "status");
  const status = el("p", "tk-sr");   // announce deleting and undoing to a screen reader
  status.setAttribute("role", "status");
  const list = el("div", "tk-list");
  side.append(title, buildForm(), formError, status, list);
  const detail = el("section", "tk-detail");
  detail.setAttribute("aria-label", tr("Conversation on the task", "Переписка по задаче"));
  wrap.append(side, detail);
  root.replaceChildren(wrap);
  ui = { root, list, detail, count, formError, status };
}

export default function register(h) {
  hub = h;
  RU = hub.lang === "ru";
  AUTHOR = `плагин ${decodeURIComponent(hub.url("").split("/")[2] || "tasks")}`;   // its full name: /ext/<name>/ (core protocol value)
  hub.addStyle("web/tasks.css");
  hub.addView({
    id: "tasks",
    title: tr("Tasks", "Задачи"),
    subtitle: tr("History and activity", "История и активность"),
    icon: "web/icon.svg",
    full: true,
    async render(root, snap) {
      if (!ui || ui.root !== root) { build(root); paint(); }
      const last = snap?.feeds?.tasks || 0;
      if (!loaded || last > since) {
        const first = !loaded;
        if ((await sync()) || first) paint();
      }
    },
  });
  // Jira is set up in the hub (hub.json → jira) — the session takes an existing Jira task (with the jira-task skill, if
  // it has one); it creates nothing in Jira itself, it only offers to (owner 12.10: the plugin is used without a Jira task
  // too; nothing about acceptance criteria in the note). The key of the found one — as the line "Jira task: KEY": the
  // process binds it to the task (engine.py: jira_marked). Without Jira the request goes as is
  const JIRA_ASK = "If there is a Jira key or link above, take that task (with the jira-task skill, if you have it). If it "
    + "is a description, look for such a task in Jira; if you found exactly it, write a separate line \"Jira task: KEY\". "
    + "Not found — create nothing in Jira, only offer to create it.";
  // "/new-task <request>" in the input field of any session (hub API 1.12, owner 10.10): the request goes to the session
  // as an ordinary message, and the task is created by itself — the process gives it a short title from the request and
  // makes the request its first message (engine.py: link_requests). First the request into the channel, then the send:
  // the process waits for the message and does not give it to the classifier. The send failed — the typed text goes back
  // to the field, there is no task.
  hub.addCommand?.({
    name: "new-task", args: tr("<Jira key or request>", "<ключ Jira или запрос>"),
    hint: tr("a task: the history here, a Jira task if there is one", "задача: история здесь, задача Jira — если есть"), by: tr("Tasks", "Задачи"),
    async run(text, ctx) {
      if (!ctx?.name) throw new Error(tr("Open a session: the request goes to it", "Откройте сессию: запрос уходит ей"));
      const at = Date.now() / 1000;
      const words = hub.snapshot()?.jira?.url ? `${text}\n\n${JIRA_ASK}` : text;
      const asked = await hub.postFeed(JSON.stringify({ kind: "topic_create", request: words.slice(0, 1500),
        name: text.slice(0, 1500), session: ctx.name, at }));
      if (asked.error) throw new Error(asked.error);
      // action — a command, not an answer to an open question card (the core would otherwise rewrite the text: review
      // 12.10); wakes an executor that fell asleep, like the core's input field (dock.js: dormant)
      const s = ctx.session || {};
      const dormant = s.role === "executor" && !s.backlog && s.state === "asleep" && s.retired;
      const sent = await hub.post("/api/send", { session: ctx.name, text: words, action: true, ...(dormant ? { wake: true } : {}) });
      if (sent.error) throw new Error(sent.error);
      creating.push({ title: text.split("\n")[0].slice(0, 60), at: Date.now(), after: at });
      if (ui) paintList();
      return tr(`Sent to ${ctx.name}; the task is being created in the "Tasks" tab`, `Ушло ${ctx.name}; задача заводится во вкладке «Задачи»`);
    },
  });
}
