"""The example task (owner 12.10): once per hub — shows at once what "History" and "Progress" look like; deleted with
the cross — it does not come back (reg["example_seeded"]). No model is spent on it: the engine does not give it to the
classifier and does not summarize it."""
import registry

VERSION = 2   # the example's content; grew — an open example of a previous version is updated in place
TITLE = "Example: fix the sign-in link"
SUMMARY = ("An example task: almost done, only a check on production is left. Look at \"History\" and \"Progress\", "
           "then delete it with the cross.")
# the owner's conversation with the Hub and specialists about one piece of work — as the tab collects it (owner 12.10:
# shows that other sessions get into the task too; the task is almost solved)
TALK = [
    ("owner", "hub", "/new-task The sign-in link from the email opens a blank page — fix it"),
    ("session", "hub", "Task created. backend looks for the cause, frontend checks the sign-in page, qa runs it at the end."),
    ("session", "backend", "Cause: the link lives 10 minutes, and the email often arrives later. I suggest 24 hours and "
                           "a one-time link. Go ahead?"),
    ("owner", "backend", "Yes, 24 hours, and let it work once."),
    ("session", "backend", "Done: the link lives 24 hours and works once, tests added. The MR is open."),
    ("session", "frontend", "The blank page was also caused by a client error on an expired link. Now we show "
                            "\"The link has expired — send a new one?\". Checked in Chrome and Safari."),
    ("owner", "qa", "Run the whole sign-in by link on staging."),
    ("session", "qa", "Everything passed on staging: a new link signs in, a repeated one does not, an expired one offers "
                      "to send a new one. No remarks."),
    ("owner", "hub", "Roll it out."),
    ("session", "hub", "Rolled out to production. Left: check the sign-in by link on production — I'll check and report."),
]
PROGRESS = {
    "goal": "The sign-in link from the email opens the account",
    "criteria": [{"text": "The link from the email opens the account", "done": True},
                 {"text": "The link lives 24 hours and works once", "done": True},
                 {"text": "An expired link offers to send a new one", "done": True},
                 {"text": "Tests and a check in Chrome and Safari", "done": True},
                 {"text": "A staging run without remarks", "done": True},
                 {"text": "Checked on production", "done": False}],
    "decisions": ["The link lives 24 hours", "The link is one-time", "Expired — offer a new one",
                  "Roll out after the qa run on staging"],
}


def seed(reg, now):
    done = reg.get("example_seeded")
    if done == VERSION:
        return None
    reg["example_seeded"] = VERSION
    if done:   # an example of a previous version: open — update in place, deleted — do not bring back
        tid = next((t["id"] for t in registry.open_topics(reg) if t.get("example")), None)
        if tid:
            fill(reg, tid, now)
        return tid
    tid = registry.new_topic(reg, TITLE, now=now)
    fill(reg, tid, now)
    return tid


def fill(reg, tid, now):
    t = reg["topics"][tid]
    t.update(title=TITLE, example=True, summary=SUMMARY, rev=t.get("rev", 0) + 1)
    reg["entries"][tid] = {}
    for i, (who, session, text) in enumerate(TALK):   # a conversation over the last hours, half an hour between lines
        registry.add_entry(reg, tid, {"id": f"x{i + 1}", "who": who, "session": session, "text": text,
                                      "ts": now - (len(TALK) - i) * 1800})
    t["progress"] = {**PROGRESS, "at": now}
