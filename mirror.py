"""The plugin channel `tasks` is the only data path to the page (plugin API 1.2). It keeps the last 500 posts, a post
is up to 2000 characters. So the channel is not a history log but a mirror of the registry: every tick we work out
which posts must be visible (want) and post only what is missing — pushed out, changed, new (to_post). A long entry is
cut into parts (part/of under one id) and joined on the page — the owner asked 08.10 not to cut messages."""
import json

import registry

# a core protocol value, not text to translate: how the core signs the plugin process's posts (core/reducer.py:
# post_row); a local install
AUTHOR = "плагин tasks"
LIMIT = 2000              # the core's post ceiling
BUDGET = 350              # mirror posts out of the channel's 500: the rest — the owner's posts (create, delete)
HOLD = 120                # s, how long not to repeat a sent post while the core has not recorded it


def channel(name):
    """The plugin channel is the short name: from the catalog the plugin is called "<account>:tasks", and the core lets
    only Latin letters, digits and hyphen into a channel name (the page takes it the same way: static/js/ext.js)."""
    return name.split(":")[-1]


def author(name):
    """The author of the process's posts — the full name; "плагин <HUB_EXTENSION>" is the core's protocol value
    (core/reducer.py: post_row), not text to translate."""
    return f"плагин {name}"


def state_dir(state, name):
    """Its own folder in the hub's state: a local install — ext-tasks, from the catalog — ext-<account>-tasks."""
    import os
    return os.path.join(state, "ext-" + name.replace(":", "-"))


def dump(payload):
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def key(payload):
    if payload.get("kind") == "topic":
        return f"t:{payload.get('id')}"
    if payload.get("kind") == "progress":
        return f"p:{payload.get('topic')}:{payload.get('part')}"
    return f"e:{payload.get('topic')}:{payload.get('id')}:{payload.get('part')}"


def _parts(base, text):
    """Parts of an entry's text so that every post with its wrapping fits LIMIT (escaping in JSON makes the text longer)."""
    parts, rest = [], text
    while rest or not parts:
        n = len(rest)
        while n > 1 and len(dump({**base, "part": 999, "of": 999, "text": rest[:n]})) > LIMIT:
            n = n * 3 // 4
        parts.append(rest[:n])
        rest = rest[n:]
    return [{**base, "part": i, "of": len(parts), "text": p} for i, p in enumerate(parts)]


def want(reg, budget=BUDGET):
    """Posts that must be in the channel: open topics and their entries, the freshest first within the budget. Tasks
    share the budget in turn, one entry per round (review 12.10: a chatty one pushed out the whole history of a quiet
    one); count in a topic post — how many entries there are in total: the page shows "the last K of N"."""
    topics = registry.open_topics(reg)
    # rev — the build number of a task's history: rebuilt (registry.rebuild) — the page hides the entries of the previous build
    out = [{"kind": "topic", "rev": t.get("rev", 0), "count": len(registry.entries(reg, t["id"])),
            **{k: t.get(k) for k in ("id", "title", "state", "created", "summary", "jira", "example")}} for t in topics]
    for t in topics:   # progress — as separate posts: with five fields it does not fit the topic post
        if t.get("progress"):
            out += _parts({"kind": "progress", "topic": t["id"]}, json.dumps(t["progress"], ensure_ascii=False))
    queues = {t["id"]: registry.entries(reg, t["id"])[::-1] for t in topics}   # the freshest first
    rev = {t["id"]: t.get("rev", 0) for t in topics}
    picked, room = [], budget - len(out)
    while queues:
        for tid in list(queues):
            if not queues[tid]:
                del queues[tid]
                continue
            e = queues[tid].pop(0)
            posts = _parts({"kind": "entry", "topic": tid, "rev": rev[tid],
                            **{k: e[k] for k in ("id", "who", "session", "ts")}}, e["text"])
            if len(posts) > room:
                del queues[tid]   # the next entry of this task did not fit — its older ones are not taken either
                continue
            room -= len(posts)
            picked.append(((e["ts"], e["id"]), posts))
    # post the old before the new: the channel pushes out from the start, the freshest goes last
    return out + [p for _, posts in sorted(picked, key=lambda x: x[0]) for p in posts]


def to_post(wanted, posts, sent, now, author=AUTHOR):
    """Texts of the posts that are not in the channel in the needed form. sent — {key: (text, when)}: what is already
    sent and on its way."""
    have = {}
    for p in posts:
        if p.get("by") != author:
            continue
        try:
            have[key(json.loads(p["text"]))] = p["text"]
        except (ValueError, TypeError, AttributeError):
            continue
    # a task no longer among the open ones while the channel has its "open" post — a "closed" post (review 12.10: a post
    # of the process that came out after the deletion brought it back after a page reload)
    keys = {key(w) for w in wanted}
    for k, text in have.items():
        old = json.loads(text)
        if k.startswith("t:") and k not in keys and old.get("state") == "open":
            wanted = [*wanted, {**old, "state": "done"}]
    out = []
    for w in wanted:
        text, k = dump(w), key(w)
        if have.get(k) == text:
            continue
        was = sent.get(k)
        if was and was[0] == text and now - was[1] < HOLD:
            continue
        sent[k] = (text, now)
        out.append(text)
    return out


def requests(posts, after):
    """The owner's word from the page after post number after: create a task (by a title, or with the /new-task command —
    by a request), delete (close), bring back a deleted one, summarize ("Progress" is open or "Refresh" is pressed)."""
    out = []
    for p in posts:
        seq = int(p["id"][1:])
        if seq <= after or p.get("by") != "owner":
            continue
        try:
            data = json.loads(p["text"])
        except ValueError:
            continue
        if not isinstance(data, dict):
            continue
        if data.get("kind") == "topic_create" and str(data.get("request") or "").strip() and data.get("session"):
            # /new-task: the request went to the session, the process gives the title (engine.py: waiting)
            try:
                at = float(data.get("at") or 0)
            except (TypeError, ValueError):   # a malformed post must not crash the tick (review 12.10)
                continue
            out.append({"seq": seq, "request": str(data["request"]).strip(), "session": str(data["session"]),
                        "at": at,
                        # the title — from the owner's words, without the jira-task request the page appended (12.10)
                        "name": str(data.get("name") or data["request"]).strip()})
        elif data.get("kind") == "topic_create" and str(data.get("title") or "").strip():
            out.append({"seq": seq, "title": str(data["title"]).strip()[:200]})
        elif data.get("kind") == "topic_summarize" and data.get("id"):
            out.append({"seq": seq, "summarize": str(data["id"]), "force": data.get("force") is True})
        elif data.get("kind") in ("topic_close", "topic_reopen") and data.get("id"):
            out.append({"seq": seq, data["kind"][6:]: str(data["id"])})
    return out
