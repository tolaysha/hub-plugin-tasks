"""The registry of the owner's tasks and their history: topics, anchors (an owner's message → a topic) and history
entries. The plugin channel keeps only the last 500 posts, so the full history is here, and the channel gets its visible
part (mirror.py). The file is outside the extension folder: otherwise the tab blinks from the auto-reload on the mtime of
the extension's files."""
import json
import os
import sys
import time


def load(path):
    """The registry from disk; no file — empty. A broken one is set aside next to it (registry.json.broken-<time>) and we
    start empty: otherwise the very first save would overwrite the whole history (review 12.10)."""
    try:
        with open(path, encoding="utf-8") as f:
            reg = json.load(f)
        if not isinstance(reg, dict):
            raise ValueError("not an object")
    except FileNotFoundError:
        reg = {}
    except (OSError, ValueError) as e:
        aside = f"{path}.broken-{int(time.time())}"
        try:
            os.replace(path, aside)
        except OSError:
            aside = "not set aside"
        print(f"tasks: the registry is unreadable ({e}) — set aside: {aside}; starting empty", file=sys.stderr, flush=True)
        reg = {}
    for key, empty in (("topics", {}), ("seq", 0), ("anchors", {}), ("entries", {})):
        reg.setdefault(key, empty)
    return reg


def load_archive(path):
    """The archive of deleted tasks: {task id: {entries, anchors}}; no file — empty; broken — set aside, like the registry."""
    data = load(path)
    return {k: v for k, v in data.items() if isinstance(v, dict) and "entries" in v}


def save(path, data):
    """Atomically and to disk (fsync): after a power failure the file is the old or the new one, not empty."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def new_topic(reg, title, now):
    reg["seq"] = reg.get("seq", 0) + 1
    tid = f"t{reg['seq']}"
    reg["topics"][tid] = {"id": tid, "title": title, "state": "open", "created": now,
                          "closed_at": None, "summary": ""}
    return tid


def close_topic(reg, tid, now):
    t = reg["topics"].get(tid)
    if not t or t["state"] != "open":
        return False
    t["state"], t["closed_at"] = "done", now
    return True


def reopen_topic(reg, tid):
    """"Undo" right after a deletion on the page: the task with its previous history is open again."""
    t = reg["topics"].get(tid)
    if not t or t["state"] != "done":
        return False
    t["state"], t["closed_at"] = "open", None
    return True


def archive_closed(reg, archive):
    """Entries and anchors of deleted tasks go to the archive (owner 12.10): the working registry does not grow, and its
    comparison every tick (main.py) stays cheap. The task itself stays in the registry — the title, when deleted. True —
    something was moved."""
    moved = False
    for tid, t in reg["topics"].items():
        if t["state"] == "open":
            continue
        anchors = {m: a for m, a in reg["anchors"].items() if a["topic"] == tid}
        entries = reg["entries"].pop(tid, None)
        if not anchors and not entries:
            continue
        box = archive.setdefault(tid, {"entries": {}, "anchors": {}})
        box["entries"].update(entries or {})
        box["anchors"].update(anchors)
        for m in anchors:
            del reg["anchors"][m]
        moved = True
    return moved


def unarchive(reg, archive, tid):
    """Bring back a deleted one: its entries and anchors from the archive back into the registry. True — there was something to bring back."""
    box = archive.pop(tid, None)
    if not box:
        return False
    reg["entries"].setdefault(tid, {}).update(box["entries"])
    reg["anchors"].update(box["anchors"])
    return True


def open_topics(reg):
    return sorted((t for t in reg["topics"].values() if t["state"] == "open"), key=lambda t: t["created"])


def add_entry(reg, tid, entry):
    """A topic's history entry by id; True — new or changed (the session's reply grew)."""
    box = reg["entries"].setdefault(tid, {})
    if box.get(entry["id"]) == entry:
        return False
    box[entry["id"]] = dict(entry)
    return True


def entries(reg, tid):
    return sorted((reg["entries"].get(tid) or {}).values(), key=lambda e: (e["ts"], e["id"]))


def rebuild(reg, tid, keep_dels=()):
    """Rebuild a task's history after an edit of the anchors: entries of removed anchors go, entries of the remaining
    ones (their message and the reply "mid:re") and of the assignments keep_dels stay — replies from cut timelines cannot
    be fetched again (09.10: erased everything — a "monologue" came out). The anchors are open again, the build number
    grows — the page hides the entries of the previous build."""
    mine = {m for m, a in reg["anchors"].items() if a["topic"] == tid}
    keep = mine | {f"{m}:re" for m in mine} | set(keep_dels)
    reg["entries"][tid] = {k: v for k, v in (reg["entries"].get(tid) or {}).items() if k in keep}
    for m in mine:
        reg["anchors"][m]["open"] = True
    reg["topics"][tid]["rev"] = reg["topics"][tid].get("rev", 0) + 1
