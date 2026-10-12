"""Один тик расширения — чистая функция от состояния хаба (core.tail.Tail): слово владельца со страницы (создать,
удалить задачу) → реестр; новые сообщения владельца → тема по смыслу (дешёвая модель); якоря открытых тем → записи
истории (collect.py); изменилась история — пересказ. Модель подменяется в тестах."""
import re

import chain
import classify as classify_mod
import collect
import registry
import summary

TEXT_MAX = 4000   # знаков сообщения модели для решения «к какой задаче» — хватает, длинное дорого


def fresh_cursor(now):
    """Первый запуск: старые сообщения в модель не гоним — привязываем только написанное после."""
    return {"last_message": now, "feed_seq": 0, "summarized_at": {}, "stale": {}}


IDLE = 60   # с: круг без новых событий журнала пропускается (main.py), но полный круг — не реже


def busy_round(seq, last_seq, now, last_full):
    """Делать ли круг: в журнале новое — да; нет — раз в IDLE всё равно (ожидание /new-task истекает, вытесненные посты
    зеркала досылаются). Пустые круги — без копий реестра для сравнения (владелец 12.10)."""
    return seq != last_seq or now - last_full >= IDLE


def tick(state, timelines, posts, reg, cursor, now, classify, summarize, name=None, jira=None, archive=None):
    asked = {}   # пересказы, о которых просила страница: id задачи → «Обновить» (сразу)
    for req in collect_requests(posts, cursor):
        if "summarize" in req:
            asked[req["summarize"]] = asked.get(req["summarize"], False) or req["force"]
        elif "close" in req:
            registry.close_topic(reg, req["close"], now=now)
        elif "reopen" in req:
            if registry.reopen_topic(reg, req["reopen"]) and archive is not None:
                registry.unarchive(reg, archive, req["reopen"])
        elif "request" in req:
            reg.setdefault("waiting", []).append({k: req[k] for k in ("request", "session", "at", "name") if k in req})
        else:
            registry.new_topic(reg, req["title"], now=now)
    if archive is not None:   # удалённые — в архив (registry.archive_closed)
        registry.archive_closed(reg, archive)
    link_requests(state, reg, now, name, jira)

    for mid, msg in sorted((state.get("messages") or {}).items(), key=lambda kv: kv[1]["created"]):
        if msg["created"] <= cursor["last_message"]:
            continue
        cursor["last_message"] = msg["created"]
        if not msg.get("from_owner") or msg.get("command") or mid in reg["anchors"]:
            continue
        tid = (executor_topic(state, reg, msg.get("session")) or reply_topic(state, timelines, reg, msg)
               or card_topic(state, timelines, reg, msg))
        if not tid:
            topics = topics_for_model(reg)
            ctx = context(state, reg, msg.get("session"), before=msg["created"])
            talk = dialog(timelines, msg.get("session"), mid)
            if talk:
                ctx += "\nперед этим в разговоре:\n" + talk
            tid = classify(topics, msg["text"][:TEXT_MAX], context=ctx,
                           accept=previous_topic(reg, msg.get("session"), msg["created"])) if topics else None
        if tid:
            reg["anchors"][mid] = {"topic": tid, "session": msg.get("session"), "text": msg["text"],
                                   "ts": msg["created"], "open": True}

    changed = set()
    for mid, a in reg["anchors"].items():
        if not a.get("open") or (reg["topics"].get(a["topic"]) or {}).get("state") != "open":
            continue
        got, closed, dels = collect.entries(state, timelines, mid, a)
        if closed is None:   # сообщения нет в ленте: свежее — может ждать в очереди, старое — лента обрезана
            closed = now - a["ts"] > GONE
        for e in got:
            if registry.add_entry(reg, a["topic"], e):
                changed.add(a["topic"])
        # окно ответов закрыто следующим входящим, а итог поручения ещё может прийти — следим, пока он не пришёл
        dmap = state.get("delegations") or {}
        if closed and not any(did in dmap and not dmap[did].get("reply") for did in dels):
            a["open"] = False

    jira_link(reg, jira, name)
    jira_created(reg, jira, changed | {t["id"] for t in registry.open_topics(reg) if t.get("jira_checked") != JIRA_RULE})

    # пересказ (владелец 12.10: он стоит токенов) — только по просьбе страницы: открыт «Прогресс» — если в задаче есть
    # новое и прошло summary.EVERY; «Обновить» — сразу. Прогресс прежнего вида (без критериев) пересчитывается по просьбе
    cursor.pop("stale", None)
    cursor.pop("recount", None)
    for tid, force in asked.items():
        t = reg["topics"].get(tid) or {}
        got = registry.entries(reg, tid)
        if t.get("state") != "open" or not got or t.get("example"):
            continue
        old = "progress" in t and "criteria" not in t["progress"]
        if not (force or old or summary.due(cursor["summarized_at"].get(tid), got[-1]["ts"], now)):
            continue
        out = summarize(t["title"], got)
        if out:   # сбой модели — None, прежние пересказ и прогресс остаются
            t["summary"] = out["summary"]
            t["progress"] = {**{k: out[k] for k in ("goal", "criteria", "decisions")}, "at": now}
        cursor["summarized_at"][tid] = now
    return changed


GONE = 6 * 3600   # с: якорь, чьего сообщения нет в ленте сессии дольше, — окно ответа закрыто (ревью 12.10)
SKEW = 300   # с — время просьбы ставят часы браузера, а сообщения — часы ядра; на другом устройстве они расходятся
WAIT = 600   # с, сколько /new-task ждёт своё сообщение в журнале; не дождался — отправка не удалась, задачи нет


def link_requests(state, reg, now, name=None, jira=None):
    """/new-task <запрос> (владелец 10.10): страница отправила запрос сессии как обычное сообщение и попросила задачу.
    Сообщение ищем в журнале — от владельца той сессии, не раньше просьбы, с текстом просьбы в начале (в посте канала
    длинный запрос — только его начало, пост до 2000 знаков); нашли — задача с коротким
    названием по запросу и это сообщение её первое (якорь — без классификатора). Не нашли за WAIT — задачи нет."""
    wait = reg.get("waiting") or []
    if not wait:
        return
    flat = lambda t: " ".join(str(t or "").split())
    msgs = sorted((state.get("messages") or {}).items(), key=lambda kv: kv[1]["created"])
    left = []
    for w in wait:
        fit = [(m, msg["created"]) for m, msg in msgs if msg.get("from_owner") and msg.get("session") == w["session"]
               and msg["created"] >= w["at"] - SKEW and m not in reg["anchors"]
               # внутри, а не в начале: ядро могло переписать слова владельца (ответ на карточку — ревью 12.10)
               and flat(w["request"]) in flat(msg.get("text"))]
        # первое после просьбы; нет — ближайшее перед ней (часы браузера спешат), не раньше SKEW
        mid = next((m for m, c in fit if c >= w["at"] - 5), None) or (fit[-1][0] if fit else None)
        if not mid:
            if now - w["at"] <= WAIT:
                left.append(w)
            continue
        msg = state["messages"][mid]
        words = w.get("name") or w["request"]
        key = jira_key_only(words, jira)
        title = key or (name or (lambda t: None))(words) or classify_mod.fallback_title(words)
        tid = registry.new_topic(reg, title, now=now)
        reg["topics"][tid]["first"] = mid   # сообщение, с которого задача началась: ключ Jira ищем в ответе на него
        key = key or find_jira_key(words, jira)   # «/new-task IT-444 поправить кнопку» — ключ назвал владелец
        if key:
            reg["topics"][tid]["jira"] = key
        reg["anchors"][mid] = {"topic": tid, "session": msg.get("session"), "text": msg["text"],
                               "ts": msg["created"], "open": True}
    reg["waiting"] = left


def _jira_res(jira):
    """Ключи задач Jira — только проектов из настроек хаба (hub.json → jira; как modules/jira/links.py): UTF-8,
    SHA-256 похожи на ключ. Сначала ссылка на задачу, потом голый ключ."""
    if not jira or not jira.get("projects"):
        return None
    keys = "|".join(re.escape(p) for p in sorted(jira["projects"], key=len, reverse=True))
    url = re.compile(rf"/browse/((?:{keys})-\d+)\b") if jira.get("url") else None
    return url, re.compile(rf"(?<![\w/-])((?:{keys})-\d+)(?![\w-])", re.I)


def jira_key_only(text, jira):
    """«/new-task it-444» — весь запрос и есть ключ: задача Jira по ключу, название — ключ, модель не нужна."""
    res = _jira_res(jira)
    m = res and res[1].fullmatch(" ".join(str(text).split()))
    return m.group(1).upper() if m else None


def find_jira_key(text, jira):
    res = _jira_res(jira)
    if not res:
        return None
    url, key = res
    m = url and url.search(text or "")
    if m:
        return m.group(1).upper()
    # у хаба есть проект UTF — «UTF-8» в тексте не задача (12.10)
    found = [k.upper() for k in key.findall(text or "") if k.upper() not in ENCODINGS]
    return found[0] if found else None


ENCODINGS = {"UTF-8", "UTF-16", "UTF-32"}


JIRA_RULE = 3   # версия правила ключа: сменилось — открытые задачи пересматриваются заново


def jira_created(reg, jira, tids):
    """Ключ Jira у любой задачи — когда сессия прямо написала, что завела её («Я завёл задачу IT-123», «Создала IT-8»).
    Угадывание по частоте убрано (владелец 12.10: ключ ветки, на которой гоняли тесты, встал ключом задачи про тесты);
    прежние догадки снимаются. Название не трогаем; ключ /new-task (jira_link) не перебиваем."""
    res = _jira_res(jira)
    made = res and re.compile(r"(?<!\w)(?:завёл|завел|завела|создал|создала)(?:\s+новую)?(?:\s+(?:задачу|тикет))?"
                              rf"\s*[\[«\"(]?\s*({res[1].pattern})", re.I)
    for tid in tids:
        t = reg["topics"].get(tid)
        if not t or t.get("example"):
            continue
        t["jira_checked"] = JIRA_RULE
        if t.pop("jira_guess", None):   # догадка прежнего правила
            t.pop("jira", None)
        if t.get("jira") or not made:
            continue
        for e in registry.entries(reg, tid):
            key = e["who"] == "session" and _said(made, e["text"])
            if key:
                t["jira"] = key
                break


def _said(made, text):
    """Ключ из фразы «завёл задачу KEY», сказанной сессией от себя: внутри «…», "…" или `…` строки — цитата."""
    for m in made.finditer(text):
        before = text[text.rfind("\n", 0, m.start()) + 1:m.start()]
        if before.count("«") > before.count("»") or before.count('"') % 2 or before.count("`") % 2:
            continue
        return m.group(1).upper()
    return None


MARK = re.compile(r"Задача Jira\W{0,6}:\W{0,6}(.*)", re.I)   # строка ответа, которую просит /new-task (web/main.js)


def jira_marked(text, jira):
    """Ключ из строки «Задача Jira: KEY» (или ссылки) в ответе сессии; ключи вне неё — похожие задачи из поиска, не эта."""
    for m in MARK.finditer(text or ""):
        key = find_jira_key(m.group(1), jira)
        if key:
            return key
    return None


def jira_link(reg, jira, name=None):
    """Задача из /new-task: сессия ответила (окно ответа закрыто следующим входящим) — ключ Jira из строки «Задача Jira:
    KEY» её ответа к задаче и в название: «IT-444 · Короткое имя». Один раз на задачу."""
    for t in registry.open_topics(reg):
        first = t.get("first")
        a = reg["anchors"].get(first or "")
        if not first or t.get("jira_named") or not a or a.get("open"):
            continue
        reply = ((reg["entries"].get(t["id"]) or {}).get(f"{first}:re") or {}).get("text") or ""
        key = t.get("jira") or jira_marked(reply, jira)
        t["jira_named"] = True
        if not key:
            continue
        t["jira"] = key
        if t["title"].upper() == key:
            short = (name or (lambda x: None))(reply) if reply else None
            t["title"] = f"{key} · {short}" if short else key
        elif not t["title"].upper().startswith(key):
            t["title"] = f"{key} · {t['title']}"


def topics_for_model(reg):
    """Открытые задачи для модели: название и о чём она — цель из «Прогресса» или пересказ (09.10: по одному названию
    «Вкладка «Задачи» в хабе» модель тянула туда «правку карточек вопросов»)."""
    return [{"id": t["id"], "title": t["title"],
             "about": ((t.get("progress") or {}).get("goal") or t.get("summary") or "")[:300]}
            for t in registry.open_topics(reg) if not t.get("example")]


CONTEXT_MAX = 600   # знаков поручения сессии в контексте модели


def context(state, reg, session, before=float("inf")):
    """Что знает ядро о сообщении, кроме текста: кому оно и какое у той сессии поручение (слова владельца, с которых оно
    началось). Одно сообщение «поправь кнопку» без этого не угадать (09.10: сообщения исполнителю Teams-плагина легли в
    задачу про вкладку хаба). Задачу прошлого сообщения не подсказываем — одна ошибка модели тянула за собой следующие."""
    if not session:
        return ""
    out = [f"сессия {session}"]
    msgs, dels = state.get("messages") or {}, state.get("delegations") or {}
    # поручение, которое было в работе в момент сообщения: выданное раньше и не сданное к нему (09.10: брали последнее
    # поручение сессии — выданное через час, и «плагин задачи» уходил в задачу Teams)
    mine = [d for d in dels.values() if d["session"] == session and d["created"] <= before
            and (d.get("state") in chain.LIVE or (d.get("since") or d["created"]) >= before)]
    if mine:
        d = max(mine, key=lambda d: d["created"])
        words = (msgs.get(chain.task_root(d) or "") or {}).get("text") or d.get("words") or ""
        if words:
            out.append(f"её поручение: «{' '.join(str(words).split())[:CONTEXT_MAX]}»")
    return "; ".join(out)


DIALOG_LINES = 4    # прошлых реплик разговора — модели в контекст
DIALOG_CUT = 400    # знаков одной реплики
FOLLOW = 30 * 60    # с: неуверенный выбор модели засчитывается, если прошлое слово владельца в сессии за это время — в той же задаче


def dialog(timelines, session, mid, n=DIALOG_LINES):
    """Реплики разговора в сессии перед сообщением mid (12.10: «подтяни мейн ещё раз» постоянному агенту по одному
    тексту не отнести ни к какой задаче — по разговору понятно)."""
    line = timelines.get(session) or []
    at = next((i for i, x in enumerate(line) if x.get("kind") == "in" and x.get("id") == mid), None)
    if at is None:
        return ""
    prev = [x for x in line[:at] if x.get("kind") in ("in", "out") and not x.get("error") and str(x.get("text") or "").strip()]
    who = lambda x: session if x["kind"] == "out" else (x.get("sender") or "владелец")
    return "\n".join(f"{who(x)}: {' '.join(str(x['text']).split())[:DIALOG_CUT]}" for x in prev[-n:])


def previous_topic(reg, session, before):
    """Открытая задача прошлого слова владельца этой сессии, если оно было не раньше FOLLOW до сообщения."""
    prev = [a for a in reg["anchors"].values() if a.get("session") == session and before - FOLLOW <= a["ts"] < before]
    if not prev:
        return None
    tid = max(prev, key=lambda a: a["ts"])["topic"]
    return tid if (reg["topics"].get(tid) or {}).get("state") == "open" else None


QUOTE = re.compile(r", в ответ на: «(.*)»$", re.S)   # метка ядра (core/reducer.py: _origin, _quote)
REPLY_TO = re.compile(r"ответ на твой ответ по (d\d+)")


def _flat(text):   # как core/reducer.py: _quote — одной строкой, скобки [] → ()
    return " ".join(str(text or "").split()).replace("]", ")").replace("[", "(")


def reply_topic(state, timelines, reg, msg):
    """Владелец нажал «Ответить» на реплику сессии (12.10): реплика — в открытой задаче, значит и ответ в ней, без модели.
    Реплика сессии — в её ленте после слова владельца; это слово — якорь задачи. Ответ на итог поручения — по его root."""
    origin = msg.get("origin") or ""
    m = REPLY_TO.search(origin)
    d = (state.get("delegations") or {}).get(m.group(1)) if m else None
    if d:
        return _open_topic(reg, chain.task_root(d))
    m = QUOTE.search(origin)
    if not m:
        return None
    quote = m.group(1).removesuffix("…")
    if not quote:
        return None
    # последняя реплика с этим началом перед ответом (ревью 12.10: «Готово.» повторяется), её слово владельца — якорь
    word, found = None, None
    for x in timelines.get(msg.get("session")) or []:
        if x.get("kind") == "in" and x.get("id") == msg["id"]:
            break
        if x.get("kind") == "in" and not x.get("sender"):   # слово владельца; напоминания ядра и письма сессий — нет
            word = x.get("id")
        elif x.get("kind") == "out" and _flat(x.get("text")).startswith(quote) and word:
            found = _open_topic(reg, word)
    return found


def card_topic(state, timelines, reg, msg):
    """Ответ владельца на карточку вопроса qN (владелец 12.10, без модели): вопрос по поручению — задача поручения;
    иначе — задача последнего слова владельца этой сессии перед карточкой, если оно было не раньше FOLLOW до неё.
    Карточка — строка «ask» ленты сессии с seq N (core/reducer.py: qid = q<seq>); её нет в ленте — решает модель."""
    card = str(msg.get("answers_question") or "")
    if not card:
        return None
    d = (state.get("delegations") or {}).get(msg.get("for_delegation") or "")
    if d and (tid := _open_topic(reg, chain.task_root(d))):
        return tid
    word, other = None, False
    for x in timelines.get(msg.get("session")) or []:
        if x.get("kind") == "in" and not x.get("sender"):   # слово владельца; напоминания ядра и письма сессий — нет
            word, other = x, False
        elif x.get("kind") == "in" and word and x.get("sender") in (state.get("sessions") or {}):
            # итог или вопрос сессии, чьё поручение — от другого слова владельца: сессия могла перейти на него (12.10:
            # агент спросил про другое дело после итога своего исполнителя, а слово владельца ему было про третье)
            root = sender_root(state, x["sender"], x["ts"])
            other = other or bool(root) and _open_topic(reg, root) != _open_topic(reg, word["id"])
        elif x.get("kind") == "ask" and f"q{x.get('seq')}" == card:
            ok = word and not other and x["ts"] - word["ts"] <= FOLLOW
            return _open_topic(reg, word["id"]) if ok else None
    return None


LETTER = 300   # с: итог поручения доходит письмом позже его сдачи (12.10: через 6 с)


def sender_root(state, session, at):
    """Слово владельца (root ядра), по которому сессия работала в момент at: её поручение, выданное раньше и не сданное
    за LETTER до at (итог приходит письмом уже после сдачи); нет поручения — None."""
    mine = [d for d in (state.get("delegations") or {}).values() if d["session"] == session and d["created"] <= at
            and (d.get("state") in chain.LIVE or (d.get("since") or d["created"]) >= at - LETTER)]
    return chain.task_root(max(mine, key=lambda d: d["created"])) if mine else None


def _open_topic(reg, mid):
    a = reg["anchors"].get(mid or "")
    return a["topic"] if a and (reg["topics"].get(a["topic"]) or {}).get("state") == "open" else None


def executor_topic(state, reg, session):
    """Открытая задача, чьё поручение (root ядра — сообщение этой задачи) исполняет эта сессия-исполнитель (role
    executor: поднята под поручение): слово владельца ей — в эту задачу без модели. Постоянные агенты делают много дел —
    их сообщения решает модель; исполнитель поручений двух задач — тоже модель."""
    if ((state.get("sessions") or {}).get(session) or {}).get("role") != "executor":
        return None
    found = set()
    for d in (state.get("delegations") or {}).values():
        a = reg["anchors"].get(chain.task_root(d) or "")
        if d["session"] == session and a and (reg["topics"].get(a["topic"]) or {}).get("state") == "open":
            found.add(a["topic"])
    return found.pop() if len(found) == 1 else None


def collect_requests(posts, cursor):
    from mirror import requests
    if cursor["feed_seq"] is None:   # курсор потерян (main.py): старые просьбы не разбираем заново — задачи-дубли
        cursor["feed_seq"] = max((int(p["id"][1:]) for p in posts), default=0)
        return []
    got = requests(posts, cursor["feed_seq"])
    if posts:
        cursor["feed_seq"] = max(cursor["feed_seq"], max(int(p["id"][1:]) for p in posts))
    return got
