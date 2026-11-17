"""One tick of the extension — a pure function of the hub's state (core.tail.Tail): the owner's word from the page
(create, delete a task) → the registry; the owner's new messages → a topic by meaning (a cheap model); anchors of open
topics → history entries (collect.py); the history changed — a summary. The model is replaced in tests."""
import re

import chain
import classify as classify_mod
import collect
import registry
import summary

TEXT_MAX = 4000   # characters of a message to the model for "which task" — enough, a long one is expensive


def fresh_cursor(now):
    """First start: old messages do not go to the model — only what is written after is bound."""
    return {"last_message": now, "feed_seq": 0, "summarized_at": {}, "stale": {}}


IDLE = 60   # s: a round without new journal events is skipped (main.py), but a full round — at least this often


def busy_round(seq, last_seq, now, last_full):
    """Whether to make a round: something new in the journal — yes; nothing — once per IDLE anyway (a /new-task wait
    expires, pushed-out mirror posts are re-sent). Empty rounds — without copies of the registry to compare (owner 12.10)."""
    return seq != last_seq or now - last_full >= IDLE


def tick(state, timelines, posts, reg, cursor, now, classify, summarize, name=None, jira=None, archive=None):
    asked = {}   # summaries the page asked for: task id → "Refresh" (at once)
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
    if archive is not None:   # deleted ones go to the archive (registry.archive_closed)
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
                ctx += "\nearlier in the conversation:\n" + talk
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
        if closed is None:   # the message is not in the timeline: a fresh one may wait in the queue, an old one — the timeline is cut
            closed = now - a["ts"] > GONE
        for e in got:
            if registry.add_entry(reg, a["topic"], e):
                changed.add(a["topic"])
        # the reply window is closed by the next incoming message, but an assignment's result may still come — watch until it does
        dmap = state.get("delegations") or {}
        if closed and not any(did in dmap and not dmap[did].get("reply") for did in dels):
            a["open"] = False

    jira_link(reg, jira, name)
    jira_created(reg, jira, changed | {t["id"] for t in registry.open_topics(reg) if t.get("jira_checked") != JIRA_RULE})

    # a summary (owner 12.10: it costs tokens) — only at the page's request: "Progress" is open — if the task has
    # something new and summary.EVERY has passed; "Refresh" — at once. Progress of the earlier kind (no criteria) is recounted on request
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
        if out:   # a model failure — None, the previous summary and progress stay
            t["summary"] = out["summary"]
            t["progress"] = {**{k: out[k] for k in ("goal", "criteria", "decisions")}, "at": now}
        cursor["summarized_at"][tid] = now
    return changed


GONE = 6 * 3600   # s: an anchor whose message is missing from the session's timeline longer — the reply window is closed (review 12.10)
SKEW = 300   # s — a request's time comes from the browser's clock, a message's from the core's; on another device they differ
WAIT = 600   # s, how long /new-task waits for its message in the journal; not there — the send failed, no task


def link_requests(state, reg, now, name=None, jira=None):
    """/new-task <request> (owner 10.10): the page sent the request to the session as an ordinary message and asked for a
    task. The message is looked for in the journal — from the owner to that session, not before the request, with the
    request's text at the start (a channel post has only the start of a long request, a post is up to 2000 characters);
    found — a task with a short title from the request, and this message is its first (an anchor — without the
    classifier). Not found within WAIT — no task."""
    wait = reg.get("waiting") or []
    if not wait:
        return
    flat = lambda t: " ".join(str(t or "").split())
    msgs = sorted((state.get("messages") or {}).items(), key=lambda kv: kv[1]["created"])
    left = []
    for w in wait:
        fit = [(m, msg["created"]) for m, msg in msgs if msg.get("from_owner") and msg.get("session") == w["session"]
               and msg["created"] >= w["at"] - SKEW and m not in reg["anchors"]
               # inside, not at the start: the core may have rewritten the owner's words (an answer to a card — review 12.10)
               and flat(w["request"]) in flat(msg.get("text"))]
        # the first after the request; none — the closest before it (the browser's clock runs fast), not earlier than SKEW
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
        reg["topics"][tid]["first"] = mid   # the message the task started with: the Jira key is looked for in the reply to it
        key = key or find_jira_key(words, jira)   # "/new-task PROJ-123 fix the button" — the owner named the key
        if key:
            reg["topics"][tid]["jira"] = key
        reg["anchors"][mid] = {"topic": tid, "session": msg.get("session"), "text": msg["text"],
                               "ts": msg["created"], "open": True}
    reg["waiting"] = left


def _jira_res(jira):
    """Jira task keys — only of the projects in the hub's settings (hub.json → jira; as modules/jira/links.py): UTF-8,
    SHA-256 look like a key. First a link to the task, then a bare key."""
    if not jira or not jira.get("projects"):
        return None
    keys = "|".join(re.escape(p) for p in sorted(jira["projects"], key=len, reverse=True))
    url = re.compile(rf"/browse/((?:{keys})-\d+)\b") if jira.get("url") else None
    return url, re.compile(rf"(?<![\w/-])((?:{keys})-\d+)(?![\w-])", re.I)


def jira_key_only(text, jira):
    """"/new-task proj-123" — the whole request is the key: a Jira task by key, the title is the key, no model needed."""
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
    # a hub may have a project UTF — "UTF-8" in a text is not a task (12.10)
    found = [k.upper() for k in key.findall(text or "") if k.upper() not in ENCODINGS]
    return found[0] if found else None


ENCODINGS = {"UTF-8", "UTF-16", "UTF-32"}


JIRA_RULE = 3   # the version of the key rule: changed — open tasks are looked through again


def jira_created(reg, jira, tids):
    """A Jira key for any task — when the session wrote plainly that it created it ("I created task PROJ-123"; the
    pattern below knows only the Russian phrasing). Guessing by frequency is removed (owner 12.10: the key of the branch
    the tests ran on became the key of the task about the tests); earlier guesses are dropped. The title is not touched;
    the /new-task key (jira_link) is not overridden."""
    res = _jira_res(jira)
    made = res and re.compile(r"(?<!\w)(?:завёл|завел|завела|создал|создала)(?:\s+новую)?(?:\s+(?:задачу|тикет))?"
                              rf"\s*[\[«\"(]?\s*({res[1].pattern})", re.I)
    for tid in tids:
        t = reg["topics"].get(tid)
        if not t or t.get("example"):
            continue
        t["jira_checked"] = JIRA_RULE
        if t.pop("jira_guess", None):   # a guess of the earlier rule
            t.pop("jira", None)
        if t.get("jira") or not made:
            continue
        for e in registry.entries(reg, tid):
            key = e["who"] == "session" and _said(made, e["text"])
            if key:
                t["jira"] = key
                break


def _said(made, text):
    """The key from the phrase "created task KEY" said by the session itself: inside «…», "…" or `…` of the line it is a quote."""
    for m in made.finditer(text):
        before = text[text.rfind("\n", 0, m.start()) + 1:m.start()]
        if before.count("«") > before.count("»") or before.count('"') % 2 or before.count("`") % 2:
            continue
        return m.group(1).upper()
    return None


# the reply line /new-task asks for (web/main.js); the Russian form — of earlier versions of the plugin
MARK = re.compile(r"(?:Jira task|Задача Jira)\W{0,6}:\W{0,6}(.*)", re.I)


def jira_marked(text, jira):
    """The key from the line "Jira task: KEY" (or a link) in the session's reply; keys outside it are similar tasks from the search, not this one."""
    for m in MARK.finditer(text or ""):
        key = find_jira_key(m.group(1), jira)
        if key:
            return key
    return None


def jira_link(reg, jira, name=None):
    """A task from /new-task: the session replied (the reply window is closed by the next incoming message) — the Jira key
    from the line "Jira task: KEY" of its reply goes to the task and into the title: "PROJ-123 · Short name". Once per task."""
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
    """Open tasks for the model: the title and what it is about — the goal from "Progress" or the summary (09.10: by the
    title alone, "The Tasks tab in the hub", the model pulled "the edit of question cards" there)."""
    return [{"id": t["id"], "title": t["title"],
             "about": ((t.get("progress") or {}).get("goal") or t.get("summary") or "")[:300]}
            for t in registry.open_topics(reg) if not t.get("example")]


CONTEXT_MAX = 600   # characters of the session's assignment in the model's context


def context(state, reg, session, before=float("inf")):
    """What the core knows about a message besides its text: whom it is to and what assignment that session has (the
    owner's words it started from). A lone "fix the button" cannot be placed without it (09.10: messages to the executor
    of another plugin landed in the task about the hub's tab). The previous message's task is not hinted — one mistake of
    the model dragged the next ones along."""
    if not session:
        return ""
    out = [f"session {session}"]
    msgs, dels = state.get("messages") or {}, state.get("delegations") or {}
    # the assignment that was in work at the moment of the message: given before it and not handed in by then (09.10: the
    # session's last assignment was taken — given an hour later, and "the tasks plugin" went into another plugin's task)
    mine = [d for d in dels.values() if d["session"] == session and d["created"] <= before
            and (d.get("state") in chain.LIVE or (d.get("since") or d["created"]) >= before)]
    if mine:
        d = max(mine, key=lambda d: d["created"])
        words = (msgs.get(chain.task_root(d) or "") or {}).get("text") or d.get("words") or ""
        if words:
            out.append(f"its assignment: «{' '.join(str(words).split())[:CONTEXT_MAX]}»")
    return "; ".join(out)


DIALOG_LINES = 4    # previous lines of the conversation — into the model's context
DIALOG_CUT = 400    # characters of one line
FOLLOW = 30 * 60    # s: an unsure choice of the model counts if the owner's previous word in the session within this time is in the same task


def dialog(timelines, session, mid, n=DIALOG_LINES):
    """Lines of the conversation in the session before message mid (12.10: "pull main once more" to a permanent agent
    cannot be placed in any task by its text alone — the conversation makes it clear)."""
    line = timelines.get(session) or []
    at = next((i for i, x in enumerate(line) if x.get("kind") == "in" and x.get("id") == mid), None)
    if at is None:
        return ""
    prev = [x for x in line[:at] if x.get("kind") in ("in", "out") and not x.get("error") and str(x.get("text") or "").strip()]
    who = lambda x: session if x["kind"] == "out" else (x.get("sender") or "owner")
    return "\n".join(f"{who(x)}: {' '.join(str(x['text']).split())[:DIALOG_CUT]}" for x in prev[-n:])


def previous_topic(reg, session, before):
    """The open task of the owner's previous word to this session, if it was no earlier than FOLLOW before the message."""
    prev = [a for a in reg["anchors"].values() if a.get("session") == session and before - FOLLOW <= a["ts"] < before]
    if not prev:
        return None
    tid = max(prev, key=lambda a: a["ts"])["topic"]
    return tid if (reg["topics"].get(tid) or {}).get("state") == "open" else None


QUOTE = re.compile(r", в ответ на: «(.*)»$", re.S)   # the core's label (core/reducer.py: _origin, _quote), in its Russian form
REPLY_TO = re.compile(r"ответ на твой ответ по (d\d+)")


def _flat(text):   # as core/reducer.py: _quote — one line, brackets [] → ()
    return " ".join(str(text or "").split()).replace("]", ")").replace("[", "(")


def reply_topic(state, timelines, reg, msg):
    """The owner pressed "Reply" on a session's line (12.10): the line is in an open task, so the reply is in it too,
    without the model. The session's line is in its timeline after the owner's word; that word is the task's anchor. A
    reply to an assignment's result — by its root."""
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
    # the last line with this start before the reply (review 12.10: "Done." repeats), its owner's word is the anchor
    word, found = None, None
    for x in timelines.get(msg.get("session")) or []:
        if x.get("kind") == "in" and x.get("id") == msg["id"]:
            break
        if x.get("kind") == "in" and not x.get("sender"):   # the owner's word; the core's reminders and sessions' letters are not
            word = x.get("id")
        elif x.get("kind") == "out" and _flat(x.get("text")).startswith(quote) and word:
            found = _open_topic(reg, word)
    return found


def card_topic(state, timelines, reg, msg):
    """The owner's answer to question card qN (owner 12.10, without the model): a question on an assignment — the
    assignment's task; otherwise — the task of the owner's last word to this session before the card, if it was no
    earlier than FOLLOW before it. The card is the "ask" row of the session's timeline with seq N (core/reducer.py:
    qid = q<seq>); it is not in the timeline — the model decides."""
    card = str(msg.get("answers_question") or "")
    if not card:
        return None
    d = (state.get("delegations") or {}).get(msg.get("for_delegation") or "")
    if d and (tid := _open_topic(reg, chain.task_root(d))):
        return tid
    word, other = None, False
    for x in timelines.get(msg.get("session")) or []:
        if x.get("kind") == "in" and not x.get("sender"):   # the owner's word; the core's reminders and sessions' letters are not
            word, other = x, False
        elif x.get("kind") == "in" and word and x.get("sender") in (state.get("sessions") or {}):
            # a result or question of a session whose assignment comes from another owner's word: the session may have
            # switched to it (12.10: an agent asked about another matter after its executor's result, and the owner's
            # word to it was about a third one)
            root = sender_root(state, x["sender"], x["ts"])
            other = other or bool(root) and _open_topic(reg, root) != _open_topic(reg, word["id"])
        elif x.get("kind") == "ask" and f"q{x.get('seq')}" == card:
            ok = word and not other and x["ts"] - word["ts"] <= FOLLOW
            return _open_topic(reg, word["id"]) if ok else None
    return None


LETTER = 300   # s: an assignment's result arrives as a letter after it is handed in (12.10: 6 s later)


def sender_root(state, session, at):
    """The owner's word (the core's root) the session worked on at the moment at: its assignment given before and not
    handed in earlier than LETTER before at (the result comes as a letter after the hand-in); no assignment — None."""
    mine = [d for d in (state.get("delegations") or {}).values() if d["session"] == session and d["created"] <= at
            and (d.get("state") in chain.LIVE or (d.get("since") or d["created"]) >= at - LETTER)]
    return chain.task_root(max(mine, key=lambda d: d["created"])) if mine else None


def _open_topic(reg, mid):
    a = reg["anchors"].get(mid or "")
    return a["topic"] if a and (reg["topics"].get(a["topic"]) or {}).get("state") == "open" else None


def executor_topic(state, reg, session):
    """The open task whose assignment (the core's root is a message of this task) this executor session does (role
    executor: raised for the assignment): the owner's word to it goes into this task without the model. Permanent agents
    do many things — their messages are decided by the model; an executor of assignments of two tasks — by the model too."""
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
    if cursor["feed_seq"] is None:   # the cursor is lost (main.py): old requests are not parsed again — duplicate tasks
        cursor["feed_seq"] = max((int(p["id"][1:]) for p in posts), default=0)
        return []
    got = requests(posts, cursor["feed_seq"])
    if posts:
        cursor["feed_seq"] = max(cursor["feed_seq"], max(int(p["id"][1:]) for p in posts))
    return got
