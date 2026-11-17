"""What goes into a topic's history around an owner's message (owner 08.10: "everything around my message"): the
message itself, the session's replies to it — up to the next incoming message to that session, and the results of the
assignments that came from it down the chain (chain.py). The window ends at any incoming message, not only the owner's:
a busy session (the hub) goes on answering others, and that is not the task's history (checked on a live journal
09.10); an assignment's result comes through the chain (the core's root, chain.py).
Session timelines — Tail.reader.timelines: "in" rows (messages to the session) and "out" rows (its speech)."""
import chain


def around(line, mid):
    """The session's lines after message mid in its timeline: ([out rows], whether the window is closed by the next
    incoming message); mid is not in the timeline (cut off, or the message still waits in the session's queue) — None:
    the engine decides by the anchor's age."""
    got, after = [], False
    for item in line:
        if not after:
            after = item.get("kind") == "in" and item.get("id") == mid
            continue
        if item.get("kind") == "in":
            return got, True
        if item.get("kind") == "out" and not item.get("error") and (item.get("text") or "").strip():
            got.append(item)
    return got, (False if after else None)


def entries(state, timelines, mid, anchor):
    """History entries for an anchor: ([{id, who, session, text, ts}], whether the session's reply window is closed,
    [assignment ids]). The live timeline is cut (the last rows; after a journal rotation — from zero), so a reply is
    caught while the window is open (a tick every 3 s) and kept in the registry from then on: a rebuild does not erase
    it (registry.rebuild)."""
    session = anchor["session"]
    line = timelines.get(session) or []
    got = [{"id": mid, "who": "owner", "session": session, "text": anchor["text"], "ts": anchor["ts"]}]
    replies, closed = around(line, mid)
    if replies:   # the session's reply is the turn's result, its last line; while the turn runs, the entry updates in place
        last = replies[-1]   # (09.10: intermediate "tests are red…", "adding styles…" in the history are noise)
        got.append({"id": f"{mid}:re", "who": "session", "session": session, "text": last["text"], "ts": last["ts"]})
    dels = chain.inherit(state, mid)
    for did in dels:
        d = state["delegations"][did]
        if d.get("reply"):
            got.append({"id": did, "who": "session", "session": d["session"], "text": str(d["reply"]),
                        "ts": d.get("since") or d["created"]})
    return got, closed, dels
