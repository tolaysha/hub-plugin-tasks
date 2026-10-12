// Вкладка «Задачи» (личное расширение): слева список задач — создать, удалить крестиком; справа — вся переписка по
// открытой задаче из разных сессий и кнопка перехода в последнюю сессию (владелец 08.10). Данные — канал плагина
// `tasks` (API 1.2): процесс расширения держит в нём зеркало своего реестра (mirror.py) — темы и записи истории,
// длинные записи частями (part/of). Свои просьбы (создать, удалить) страница пишет в тот же канал от владельца.
// Разметка, подпись участника и время — модули страницы хаба: переписка выглядит так же, как в ленте сессии. Они не
// часть API плагинов (ревью 12.10): поменялись в ядре — вкладка работает дальше на простом виде, а не пропадает.
const [mdMod, whoMod, domMod] = await Promise.all(["/static/js/markdown.js", "/static/js/who.js", "/static/js/dom.js"]
  .map((u) => import(u).catch(() => ({}))));
const plain = (tag, cls, text) => { const n = document.createElement(tag); n.className = cls; n.textContent = text; return n; };
const md = typeof mdMod.md === "function" ? mdMod.md : (text) => plain("div", "tk-plain", text);
const byline = typeof whoMod.byline === "function" ? whoMod.byline : (name) => plain("b", "byline", name || "Вы");
const stamp = typeof domMod.stamp === "function" ? domMod.stamp
  : (ts) => plain("span", "tk-time", new Date(ts * 1000).toLocaleString("ru", { dateStyle: "short", timeStyle: "short" }));

let AUTHOR = "плагин tasks";     // так ядро подписывает посты процесса плагина; из каталога — «плагин <аккаунт>:tasks»
let hub = null;
const el = (...a) => hub.el(...a);

const store = new Map();     // ключ поста (mirror.py: key) → данные последнего поста с этим ключом
const closing = new Set();   // задачи, которые владелец удалил, а процесс ещё не подтвердил
let creating = [];           // [{title, at}] — создаются: пост ушёл, задача ещё не пришла
let since = 0, loading = false, loaded = false, failed = "";
let open = null;             // id открытой задачи
let view = "history";        // что справа у открытой задачи: «История» или «Прогресс» (владелец 09.10)
const deleting = new Map();  // id → {title, until, timer} — нажат крестик: карточка на месте с отсчётом, удалится по таймеру
const SKEW = 300;            // с — часы браузера и процесса расходятся (ревью 12.10), как engine.SKEW
const UNDO_MS = 5000;        // сколько можно передумать (владелец 10.10: удаление «как будто зависает» — видно отсчёт)
let ticker = null;           // обновляет «Удалится через N с», пока есть что удалять
const asking = new Map();    // id задачи → {at} — страница попросила пересказ, новый ещё не пришёл
const asked = new Map();     // id задачи → когда «Прогресс» сам просил пересказ (не просить по кругу)
const ASK_AGAIN = 300000;    // мс — сам «Прогресс» просит не чаще
const ASK_EVERY = 1800;      // с — по «Прогрессу» не чаще (владелец 12.10; процесс проверяет сам: summary.EVERY)
const ASK_WAIT = 120000;     // мс — дольше пересказ не ждём: «Обновить» снова доступна
// порядок и закрепление (владелец 12.10: «переносить по порядку и закреплять, как на доске») — вкус владельца, хранится
// в этом браузере: процессу и каналу они не нужны. Закреплённые — сверху; перетащенные — в своём порядке; новые
// и ни разу не тронутые — сверху своей группы, по последнему сообщению
const ORDER_KEY = "tasks.order", PIN_KEY = "tasks.pinned";
const saved = (k) => { try { const v = JSON.parse(localStorage.getItem(k) || "[]"); return Array.isArray(v) ? v : []; } catch { return []; } };
const keep = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* приватный режим — до перезагрузки */ } };
let order = saved(ORDER_KEY);
const pinned = new Set(saved(PIN_KEY));
let dragging = null;         // id перетаскиваемой задачи: пока тянут, список не перерисовывается
let ui = null;               // узлы вида: строятся один раз, поле ввода не теряет набранное между снимками

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
    failed = "Не удалось загрузить задачи. Обновите страницу; если не поможет — проверьте, что процесс расширения tasks запущен.";
    return true;
  } finally {
    loading = false;
  }
}

function topics() {
  const all = [...store.values()].filter((d) => d.kind === "topic" && d.state === "open" && !closing.has(d.id));
  // заведённая кнопкой — по названию; командой /new-task — по времени (название даёт процесс)
  creating = creating.filter((c) => !all.some((t) => (c.after ? t.created >= c.after - SKEW : t.title === c.title))
    && Date.now() - c.at < 60000);
  const rank = new Map(order.map((id, i) => [id, i]));
  const at = (t) => rank.get(t.id) ?? -1;
  return all.map((t) => ({ ...t, last: lastTs(t.id), pinned: pinned.has(t.id) }))
    .sort((a, b) => (b.pinned - a.pinned) || (at(a) - at(b)) || (b.last - a.last));
}

// перенести задачу id на место задачи to (before — перед ней, иначе после); порядок — весь видимый список
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
  for (const id of [...pinned]) if (!store.has(`t:${id}`)) pinned.delete(id);   // удалённые не копим
  keep(PIN_KEY, [...pinned]);
  paintList();
}

function entries(tid) {
  const byId = new Map();
  const rev = store.get(`t:${tid}`)?.rev || 0;   // записи прежней сборки истории (mirror.py: rev) не показываем
  for (const d of store.values()) {
    if (d.kind !== "entry" || d.topic !== tid || (d.rev || 0) !== rev) continue;
    if (!byId.has(d.id)) byId.set(d.id, []);
    byId.get(d.id).push(d);
  }
  const out = [];
  for (const [id, all] of byId) {
    // запись стала короче — хвост прежней версии (другой of) остаётся в канале: берём части одной версии (ревью 12.10)
    const of = all.find((p) => p.part === 0)?.of;
    const parts = all.filter((p) => p.of === of && p.part < of).sort((a, b) => a.part - b.part);
    if (!parts.length || parts.length !== of) continue;   // ещё не все части пришли — не показываем недособранное
    out.push({ id, who: parts[0].who, session: parts[0].session, ts: parts[0].ts, text: parts.map((p) => p.text).join("") });
  }
  return out.sort((a, b) => a.ts - b.ts || (a.id < b.id ? -1 : 1));
}

// прогресс задачи (summary.py) — JSON частями (part/of): собрать и разобрать; не все части пришли — null
function progress(tid) {
  let parts = [...store.values()].filter((d) => d.kind === "progress" && d.topic === tid).sort((a, b) => a.part - b.part);
  // прогресс стал короче — хвостовые части прежнего (с другим of) остаются в канале: берём части одного прогресса
  parts = parts.filter((p) => p.of === parts[0]?.of && p.part < p.of);
  if (!parts.length || parts.length !== parts[0].of) return null;
  try { return JSON.parse(parts.map((p) => p.text).join("")); } catch { return null; }
}

// пересказ стоит токенов (владелец 12.10) — процесс считает его только по просьбе: открыт «Прогресс» — если в задаче
// есть новое и прошло 30 минут, «Обновить» — сразу
function summarizing(tid) {
  const a = asking.get(tid);
  if (a && ((progress(tid)?.at || 0) >= a.at - 5 || Date.now() - a.at * 1000 > ASK_WAIT)) asking.delete(tid);
  return asking.has(tid);
}

async function askSummary(t, force) {
  const list = entries(t.id);
  if (!list.length || t.example || summarizing(t.id)) return;   // пример процесс не пересказывает
  const newest = list[list.length - 1].ts, p = progress(t.id);
  if (!force) {
    const due = !p?.criteria || (newest > p.at && Date.now() / 1000 - p.at >= ASK_EVERY);
    // сам — не чаще раза в ASK_AGAIN на задачу: пока идёт ход, последняя запись меняется каждые несколько секунд
    if (!due || Date.now() - (asked.get(t.id) || 0) < ASK_AGAIN) return;
    asked.set(t.id, Date.now());
  }
  asking.set(t.id, { at: Date.now() / 1000 });
  let r;
  try { r = await hub.postFeed(JSON.stringify({ kind: "topic_summarize", id: t.id, force })); } catch (e) { r = { error: String(e) }; }
  if (r?.error) asking.delete(t.id);
  if (open === t.id) paintDetail();
}

// ключ Jira задачи (engine.py: jira_link) — ссылкой на задачу в Jira; в названии он уже не нужен
const bare = (t) => (t.jira && t.title.startsWith(`${t.jira} · `) ? t.title.slice(t.jira.length + 3) : t.title);
function jiraLink(t, cls) {
  const url = hub.snapshot()?.jira?.url;
  if (!t.jira || !url) return null;
  const a = el("a", cls, `${t.jira} ↗`);
  a.href = `${url}/browse/${encodeURIComponent(t.jira)}`;
  a.target = "_blank";
  a.rel = "noopener";
  a.title = "Открыть задачу в Jira";
  return a;
}

function lastTs(tid) {
  let last = 0;
  for (const e of entries(tid)) last = Math.max(last, e.ts);
  const t = store.get(`t:${tid}`);
  return Math.max(last, t?.created || 0);
}

function count(n) {
  const m10 = n % 10, m100 = n % 100;
  const w = m10 === 1 && m100 !== 11 ? "сообщение" : m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14) ? "сообщения" : "сообщений";
  return `${n} ${w}`;
}

// --- левая колонка -------------------------------------------------------------------------------------------------
function buildForm() {
  const form = el("form", "tk-new");
  const input = el("input", "tk-new-input");
  input.type = "text";
  input.maxLength = 200;
  input.placeholder = "Название новой задачи";
  input.setAttribute("aria-label", "Название новой задачи");
  const btn = el("button", "btn primary", "Создать");
  btn.type = "submit";
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const title = input.value.trim();
    if (!title) { input.focus(); return; }   // пустое название — назад в поле, кнопку не выключаем без причины
    btn.disabled = true;
    let r;   // postFeed не бросает — ошибку возвращает в {error} (ревью 12.10: поле очищалось молча)
    try { r = await hub.postFeed(JSON.stringify({ kind: "topic_create", title })); } catch (e) { r = { error: String(e) }; }
    if (r?.error) ui.formError.textContent = "Задача не создалась. Попробуйте ещё раз.";
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
  // карточка — контейнер; открыть задачу — своя кнопка, удалить — соседняя (кнопка в кнопке ломает клавиатуру)
  const c = el("div", "tk-card");
  c.setAttribute("aria-current", String(t.id === open));
  const main = el("button", "tk-card-main");
  main.type = "button";
  main.append(el("span", "tk-card-title", bare(t)));
  if (t.summary) main.append(el("span", "tk-card-summary", t.summary));
  const n = t.count ?? entries(t.id).length;   // в канале — последние записи, count — сколько их всего (mirror.py)
  const meta = el("span", "tk-card-meta", n ? `${count(n)} · ` : "Сообщений пока нет · ");
  meta.append(stamp(t.last));
  main.append(meta);
  main.addEventListener("click", () => { open = t.id; paint(); });
  // с клавиатуры: Alt+↑ / Alt+↓ — переставить карточку
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
  x.title = "Удалить задачу";
  x.setAttribute("aria-label", `Удалить задачу «${t.title}»`);
  x.addEventListener("click", () => remove(t));
  const pin = el("button", "tk-pin");
  pin.type = "button";
  pin.title = t.pinned ? "Открепить" : "Закрепить сверху";
  pin.setAttribute("aria-pressed", String(t.pinned));
  pin.setAttribute("aria-label", `${t.pinned ? "Открепить" : "Закрепить"} задачу «${t.title}»`);
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

// перетаскивание карточки мышью: полоса показывает, куда она встанет
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

// крестик: карточка остаётся на месте — «Удалится через N с», полоска и «Отменить»; задача удаляется по таймеру
// (история остаётся у расширения): отмена лучше вопроса «вы уверены?», а отсчёт видно — не кажется, что зависло
function remove(t) {
  if (deleting.has(t.id)) return;
  deleting.set(t.id, { title: t.title, until: Date.now() + UNDO_MS, timer: setTimeout(() => commitRemove(t.id), UNDO_MS) });
  ticker ??= setInterval(() => {
    document.querySelectorAll(".tk-del-left").forEach((n) => { n.textContent = leftText(Number(n.dataset.until)); });
    if (!deleting.size) { clearInterval(ticker); ticker = null; }
  }, 250);
  paintList();
  ui.status.textContent = `Задача «${t.title}» удалится через ${UNDO_MS / 1000} секунд. Отменить — кнопка в её карточке`;
}

const leftText = (until) => `Удалится через ${Math.max(1, Math.ceil((until - Date.now()) / 1000))} с`;

function cancelRemove(id) {
  const d = deleting.get(id);
  if (!d) return;
  clearTimeout(d.timer);
  deleting.delete(id);
  paintList();
  ui.status.textContent = `Задача «${d.title}» остаётся`;
}

async function commitRemove(id) {
  const d = deleting.get(id);
  if (!d) return;
  deleting.delete(id);
  closing.add(id);
  if (open === id) open = null;
  paint();
  ui.status.textContent = `Задача «${d.title}» удалена`;
  let r;
  try { r = await hub.postFeed(JSON.stringify({ kind: "topic_close", id })); } catch (e) { r = { error: String(e) }; }
  if (r?.error) { closing.delete(id); ui.status.textContent = "Задача не удалилась. Попробуйте ещё раз."; paint(); }
}

function deletingCard(t, d) {
  const c = el("div", "tk-card tk-deleting");
  const left = el("span", "tk-del-left", leftText(d.until));
  left.dataset.until = String(d.until);
  const undo = el("button", "btn small", "Отменить");
  undo.type = "button";
  undo.setAttribute("aria-label", `Не удалять задачу «${t.title}»`);
  undo.addEventListener("click", () => cancelRemove(t.id));
  const row = el("div", "tk-del-row");
  row.append(left, undo);
  const bar = el("span", "tk-del-bar");
  bar.style.animationDelay = `${d.until - Date.now() - UNDO_MS}ms`;   // перерисовка не начинает полоску заново
  bar.style.animationDuration = `${UNDO_MS}ms`;
  c.append(el("span", "tk-card-title", t.title), row, bar);
  return c;
}

function paintList() {
  if (dragging) return;   // перерисовка посреди перетаскивания оборвала бы его; после — dragend
  const list = ui.list;
  const ts = topics();
  const nodes = [];
  if (failed) nodes.push(el("p", "tk-note tk-error", failed));
  for (const c of creating) {
    const p = el("div", "tk-card tk-pending");
    p.append(el("div", "tk-card-title", c.title), el("div", "tk-card-meta", "Создаётся…"));
    nodes.push(p);
  }
  nodes.push(...ts.map(card));
  if (!ts.length && !creating.length && !failed) {
    nodes.push(el("p", "tk-note", loaded ? "Задач пока нет."
      : "Загружаю задачи…"));
  }
  list.replaceChildren(...nodes);
  ui.count.textContent = ts.length ? String(ts.length) : "";
}

// --- правая колонка ------------------------------------------------------------------------------------------------
function paintDetail() {
  const box = ui.detail;
  const t = open && topics().find((x) => x.id === open);
  if (!t) {
    open = null;
    delete box.dataset.topic;
    if (box.dataset.sig !== "onb") box.replaceChildren(onboarding());   // задача не выбрана — как пользоваться вкладкой
    box.dataset.sig = "onb";
    return;
  }
  // ничего не поменялось — не перерисовываем (ревью 12.10: пока сессия работает, посты идут каждые несколько секунд,
  // и перерисовка сбрасывала выделенный текст и заново прогоняла разметку всей истории)
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
  seg.setAttribute("aria-label", "Вид задачи");
  for (const [id, label, title] of [["history", "История", "Вся переписка по задаче"],
    ["progress", "Прогресс", "Задача, критерии приёмки и главные решения"]]) {
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
    const go = el("a", "btn primary", `Перейти в сессию ${last === "hub" ? "Хаб" : last}`);
    go.href = "#/s/" + encodeURIComponent(last);
    foot.append(go, el("span", "tk-foot-hint", "откроется последняя сессия, где шла работа по задаче"));
    nodes.push(foot);
  }
  box.dataset.topic = t.id;
  box.replaceChildren(...nodes);
  if (view === "history") history.scrollTop = atEnd ? history.scrollHeight : top;
  else askSummary(t, false);
}

// над «Прогрессом»: когда собран и «Обновить» — пересобрать сейчас; сам он пересобирается, когда открыт, раз в 30 минут
function refreshBar(t, p) {
  const bar = el("div", "tk-pr-bar");
  const busy = summarizing(t.id);
  const meta = el("p", "tk-pr-meta", busy ? "Собираю по переписке…" : p ? "Собрано по переписке · " : "Прогресс ещё не собран");
  if (!busy && p?.at) meta.append(stamp(p.at));
  const b = el("button", "btn small tk-pr-refresh", busy ? "Обновляется…" : "Обновить");
  b.type = "button";
  b.disabled = busy;
  b.title = "Пересобрать задачу, критерии приёмки и решения по переписке сейчас";
  b.addEventListener("click", () => askSummary(t, true));
  bar.append(meta);
  if (!t.example) bar.append(b);   // пример не пересказывается — кнопка ничего бы не сделала
  return bar;
}

function historyView(t, list) {
  const history = el("div", "tk-history");
  if (t.count > list.length && list.length) {   // канал держит последние записи (mirror.py: бюджет), полная — у процесса
    history.append(el("p", "tk-note", `Показаны последние ${list.length} из ${t.count} сообщений.`));
  }
  if (!list.length) {
    history.append(el("p", "tk-note", "Сообщений по задаче пока нет. Пишите сессиям как обычно — сообщения про эту задачу попадут сюда сами."));
  }
  for (const e of list) {
    const row = el("article", `tk-entry ${e.who === "owner" ? "tk-owner" : "tk-session"}`);
    const by = el("div", "tk-entry-by");
    by.append(byline(e.who === "owner" ? null : e.session));
    if (e.who === "owner" && e.session) by.append(el("span", "tk-to", `→ ${e.session === "hub" ? "Хаб" : e.session}`));
    by.append(stamp(e.ts));
    row.append(by, md(e.text));
    history.append(row);
  }
  return history;
}

// «Прогресс» (владелец 11.10: на одну страницу): задача, критерии приёмки с галочкой у сделанных, самые важные
// решения — модель собирает по переписке (summary.py). Прогресс прежнего вида (сделано / осталось) пересчитывается
// процессом; пока не пересчитан — «сделано» и «осталось» показываются критериями
function progressView(t, list) {
  const box = el("div", "tk-history tk-progress");
  const p = progress(t.id);
  if (!list.length) {
    box.append(el("p", "tk-note", "Прогресс появится, когда по задаче начнётся переписка."));
    return box;
  }
  box.append(refreshBar(t, p));
  if (!p) return box;
  const crit = p.criteria || [...(p.done || []).map((text) => ({ text, done: true })),
    ...(p.todo || []).map((text) => ({ text, done: false }))];
  const goal = el("section", "tk-pr-sec");
  goal.append(el("h3", "tk-pr-head", "Задача"), el("p", "tk-pr-goal", p.goal || t.title));
  const cs = el("section", "tk-pr-sec");
  const ready = crit.filter((c) => c.done).length;
  cs.append(el("h3", "tk-pr-head", crit.length ? `Критерии приёмки · ${ready} из ${crit.length}` : "Критерии приёмки"));
  if (!crit.length) cs.append(el("p", "tk-pr-none", "Пока не ясны"));
  else {
    const ul = el("ul", "tk-pr-list tk-pr-crit");
    for (const c of crit) {
      const li = el("li", c.done ? "is-done" : null);
      li.append(el("span", "tk-pr-mark", c.done ? "✓" : ""), el("span", null, c.text));
      li.querySelector(".tk-pr-mark").setAttribute("aria-label", c.done ? "сделано" : "не сделано");
      ul.append(li);
    }
    cs.append(ul);
  }
  const ds = el("section", "tk-pr-sec tk-pr-decisions");
  ds.append(el("h3", "tk-pr-head", "Решения"));
  const dec = p.decisions || [];
  if (!dec.length) ds.append(el("p", "tk-pr-none", "Решений пока нет"));
  else {
    const ul = el("ul", "tk-pr-list");
    for (const x of dec) ul.append(el("li", null, x));
    ds.append(ul);
  }
  box.append(goal, cs, ds);
  return box;
}

// задача не выбрана — как пользоваться вкладкой (владелец 09.10: «онбординг, пока задача не выбрана»)
function onboarding() {
  const box = el("div", "tk-onb");
  box.append(el("h2", "tk-onb-title", "Как работают задачи"),
    el("p", "tk-onb-lead", "Задача собирает в одном месте всю переписку по одному делу — даже если оно шло через несколько сессий."));
  const steps = el("ol", "tk-onb-steps");
  // /new-task — главный способ (владелец 12.10: «описано невзначай») — отдельно, с примерами команды
  const cmds = el("span", "tk-onb-cmds");
  const pair = el("span", "tk-onb-pair");   // два варианта рядом (владелец 12.10: «в одну линию, короче»)
  const jira = !!hub.snapshot()?.jira?.url;   // Jira в хабе не настроена — без Jira-варианта
  const variants = jira
    ? [["По ключу Jira", "/new-task IT-444", "возьмёт задачу из Jira"],
      ["По описанию", "/new-task починить вход", "найдёт задачу в Jira или предложит завести"]]
    : [["По описанию", "/new-task починить вход", "запрос уйдёт сессии, задача заведётся сама"]];
  for (const [label, cmd, what] of variants) {
    const v = el("span", "tk-onb-cmd");
    v.append(el("span", "tk-onb-cmd-label", label), el("code", null, cmd), el("span", null, what));
    pair.append(v);
  }
  cmds.append(el("span", null, "В поле любой сессии:"), pair,
    el("span", null, jira ? "Без Jira — назовите дело в поле слева." : "Или коротко назовите дело в поле слева."));
  for (const [head, text] of [
    ["Создайте задачу командой /new-task", cmds],
    ["Пишите сессиям как обычно", "Ваши сообщения про это дело, ответы сессий и итоги поручений попадут в задачу сами. Отвечайте сессии кнопкой «Ответить» на её реплику — тогда ваш ответ точно попадёт в ту же задачу."],
    ["Откройте задачу слева", "Здесь будет вся переписка и короткий пересказ, а кнопка внизу откроет сессию, где вы остановились."],
  ]) {
    const li = el("li");
    li.append(el("b", null, head), typeof text === "string" ? el("span", null, text) : text);
    steps.append(li);
  }
  const go = el("button", "btn primary", "Создать задачу");
  go.type = "button";
  go.addEventListener("click", () => ui.root.querySelector(".tk-new-input")?.focus());
  box.append(steps, go, el("p", "tk-onb-hint", "Карточки можно перетаскивать, булавка закрепляет задачу сверху. Удалить — крестиком: 5 секунд, пока идёт отсчёт, удаление можно отменить."));
  return box;
}

function paint() {
  paintList();
  paintDetail();
}

function build(root) {
  const wrap = el("div", "tk");
  const side = el("section", "tk-side");
  side.setAttribute("aria-label", "Список задач");
  const title = el("div", "tk-side-head");
  const count = el("span", "tk-count");
  title.append(el("h2", "tk-side-title", "Задачи"), count);
  const formError = el("p", "tk-form-error");
  formError.setAttribute("role", "status");
  const status = el("p", "tk-sr");   // объявить экранному диктору удаление и возврат
  status.setAttribute("role", "status");
  const list = el("div", "tk-list");
  side.append(title, buildForm(), formError, status, list);
  const detail = el("section", "tk-detail");
  detail.setAttribute("aria-label", "Переписка по задаче");
  wrap.append(side, detail);
  root.replaceChildren(wrap);
  ui = { root, list, detail, count, formError, status };
}

export default function register(h) {
  hub = h;
  AUTHOR = `плагин ${decodeURIComponent(hub.url("").split("/")[2] || "tasks")}`;   // своё полное имя: /ext/<имя>/
  hub.addStyle("web/tasks.css");
  hub.addView({
    id: "tasks",
    title: "Задачи",
    subtitle: "История и активность",
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
  // Jira настроена в хабе (hub.json → jira) — сессия берёт существующую задачу Jira (скиллом jira-task, если он есть);
  // сама в Jira ничего не заводит, только предлагает (владелец 12.10: с плагином работают и без задачи Jira; про
  // критерии приёмки в приписке — ничего). Ключ найденной — строкой «Задача Jira: KEY»: её процесс привяжет к задаче
  // (engine.py: jira_marked). Без Jira запрос уходит как есть
  const JIRA_ASK = "Если выше ключ или ссылка Jira — возьми эту задачу (скиллом jira-task, если он есть). Если описание — "
    + "поищи такую задачу в Jira; нашёл именно её — напиши отдельной строкой «Задача Jira: КЛЮЧ». Не нашёл — в Jira "
    + "ничего не заводи, только предложи завести.";
  // «/new-task <запрос>» в поле ввода любой сессии (API хаба 1.12, владелец 10.10): запрос уходит сессии как обычное
  // сообщение, а задача заводится сама — процесс назовёт её коротко по запросу и сделает запрос её первым сообщением
  // (engine.py: link_requests). Сначала просьба в канал, потом отправка: процесс ждёт сообщение и не отдаст его
  // классификатору. Отправка не удалась — набранное вернётся в поле, задачи не будет.
  hub.addCommand?.({
    name: "new-task", args: "<ключ Jira или запрос>", hint: "задача: история здесь, задача Jira — если есть", by: "Задачи",
    async run(text, ctx) {
      if (!ctx?.name) throw new Error("Откройте сессию: запрос уходит ей");
      const at = Date.now() / 1000;
      const words = hub.snapshot()?.jira?.url ? `${text}\n\n${JIRA_ASK}` : text;
      const asked = await hub.postFeed(JSON.stringify({ kind: "topic_create", request: words.slice(0, 1500),
        name: text.slice(0, 1500), session: ctx.name, at }));
      if (asked.error) throw new Error(asked.error);
      // action — команда, а не ответ на открытую карточку вопроса (ядро иначе перепишет текст: ревью 12.10); уснувшего
      // исполнителя будит, как поле ввода ядра (dock.js: dormant)
      const s = ctx.session || {};
      const dormant = s.role === "executor" && !s.backlog && s.state === "asleep" && s.retired;
      const sent = await hub.post("/api/send", { session: ctx.name, text: words, action: true, ...(dormant ? { wake: true } : {}) });
      if (sent.error) throw new Error(sent.error);
      creating.push({ title: text.split("\n")[0].slice(0, 60), at: Date.now(), after: at });
      if (ui) paintList();
      return `Ушло ${ctx.name}; задача заводится во вкладке «Задачи»`;
    },
  });
}
