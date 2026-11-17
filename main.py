"""The process of the "Tasks" extension (hub-up: HUB_STATE_DIR, PYTHONPATH to the hub's code, HUB_EXTENSION). Reads the
hub's journal through core.tail.Tail — the ready state and session timelines, as the page has them — and every TICK s
makes a tick (engine.py): tasks, binding the owner's messages, history, summary. Then it checks the plugin channel
against the registry (mirror.py) and posts what is missing in the plugin's name. The registry and cursor live in
<state>/ext-tasks, not in the extension folder: a file that changes every tick would reload open tabs (the page watches
the mtime of the extension's files)."""
import json
import os
import sys
import time

sys.dont_write_bytecode = True   # no __pycache__ in the extension folder — the same reason

from core import spool  # noqa: E402
from core.daemon import default_state_dir  # noqa: E402
from core.tail import Tail  # noqa: E402

import classify  # noqa: E402
import engine  # noqa: E402
import example  # noqa: E402
import mirror  # noqa: E402
import registry  # noqa: E402
import summary  # noqa: E402

NAME = os.environ.get("HUB_EXTENSION", "tasks")
STATE = default_state_dir()
OWN = mirror.state_dir(STATE, NAME)   # from the catalog — "<account>:tasks": its own folder, the channel — the short name
REG_PATH = os.path.join(OWN, "registry.json")
CURSOR_PATH = os.path.join(OWN, "cursor.json")
ARCHIVE_PATH = os.path.join(OWN, "archive.json")   # entries and anchors of deleted tasks (registry.archive_closed)
TICK = 3


def load_cursor(now):
    try:
        with open(CURSOR_PATH, encoding="utf-8") as f:
            cursor = json.load(f)
        if not isinstance(cursor, dict) or "last_message" not in cursor:
            raise ValueError("not a cursor")
        return cursor
    except FileNotFoundError:
        return engine.fresh_cursor(now)
    except (OSError, ValueError):   # broken: do not parse the channel's old requests again (engine.collect_requests)
        print("tasks: the cursor is unreadable — starting from the current place of the channel", file=sys.stderr, flush=True)
        return {**engine.fresh_cursor(now), "feed_seq": None}


def take_rewind(cursor):
    """Parse the owner's words again from the moment since (after a fix of the binding, 12.10): the file rewind.json next
    to the registry is put by hand while the process runs — it keeps the cursor in memory, so an edit of cursor.json from
    outside would be overwritten. Messages already bound are not touched (engine.tick skips anchors)."""
    path = os.path.join(OWN, "rewind.json")
    try:
        with open(path, encoding="utf-8") as f:
            since = float(json.load(f)["since"])
        os.remove(path)
    except (OSError, ValueError, KeyError, TypeError):
        return False
    cursor["last_message"] = min(cursor["last_message"], since)
    print(f"tasks: parsing the owner's words again from {since}", file=sys.stderr, flush=True)
    return True


def jira_settings():
    """The hub's Jira (hub.json → jira: url, projects) — the same that highlight keys on the page (modules/jira/links.py)."""
    try:
        from core.catalog import config_path
        from modules.jira import links
        with open(config_path(STATE), encoding="utf-8") as f:
            return links.settings(json.load(f))
    except (OSError, ValueError, ImportError):
        return None


def save_json(path, data):
    registry.save(path, data)


def post(text):
    # a post of the plugin process — in its name, like `hub post` from a plugin process (bin/hub: put)
    spool.put(os.path.join(STATE, "spool"), "ext.post", None,
              {"plugin": mirror.channel(NAME), "text": text, "channel": "plugin", "owner": False, "sender": NAME},
              src=f"ext:{NAME}")


def main():
    reg, cursor, sent = registry.load(REG_PATH), load_cursor(time.time()), {}
    if example.seed(reg, time.time()):   # the example task — once per hub
        registry.save(REG_PATH, reg)
    tail = Tail(os.path.join(STATE, "journal.jsonl"))
    jira = jira_settings()   # the hub's settings change rarely — with a restart of the extension
    print(f"tasks: start, tasks {len(reg['topics'])}, state {OWN}", file=sys.stderr, flush=True)
    archive = registry.load_archive(ARCHIVE_PATH)
    if registry.archive_closed(reg, archive):   # deleted before the archive existed (12.10) — at once on start
        registry.save(ARCHIVE_PATH, archive)
        registry.save(REG_PATH, reg)
    last_seq, last_full = None, 0.0
    while True:
        tail.poll()   # only this thread reads — no lock
        state, timelines = tail.reader.state, tail.reader.timelines
        now = time.time()
        rewound = take_rewind(cursor)
        if not rewound and not engine.busy_round(state.get("seq"), last_seq, now, last_full):
            time.sleep(TICK)   # nothing new in the journal — no copies of the registry (owner 12.10)
            continue
        last_seq, last_full = state.get("seq"), now
        posts = list((state.get("feeds") or {}).get(mirror.channel(NAME)) or [])
        reg_before = json.dumps(reg, sort_keys=True)
        shelf = {k: (len(v["entries"]), len(v["anchors"])) for k, v in archive.items()}
        engine.tick(state, timelines, posts, reg, cursor, now,
                    classify=classify.classify_message, summarize=summary.summarize, name=classify.short_title,
                    jira=jira, archive=archive)
        for text in mirror.to_post(mirror.want(reg), posts, sent, now, author=mirror.author(NAME)):
            post(text)
        if {k: (len(v["entries"]), len(v["anchors"])) for k, v in archive.items()} != shelf:
            save_json(ARCHIVE_PATH, archive)   # the archive before the registry: a crash between them loses no entries
        if json.dumps(reg, sort_keys=True) != reg_before:
            save_json(REG_PATH, reg)
        save_json(CURSOR_PATH, cursor)
        time.sleep(TICK)

if __name__ == "__main__":
    main()
