"""A task's summary for the card and its progress for "Progress" — in one call of a cheap model over the conversation,
the same way as the watcher's recap (modules/watch/watch.py): at the page's request (engine.py), at most once per EVERY.
A model failure — None, the previous one stays. "Progress" (owner 11.10) fits one page: the task, the acceptance
criteria marked "done", the most important decisions in short."""
import sys

from classify import real_ask

EVERY = 1800       # s between summaries of one task on "Progress"; "Refresh" — without waiting
LAST = 40          # last entries into the summary
ENTRY_MAX = 1500   # characters of one entry into the summary
CRITERIA = 6       # acceptance criteria — together with the decisions they fit one screen
DECISIONS = 4      # most important decisions
ITEM_MAX = 110     # characters of one item — one or two lines

SCHEMA = {"type": "object", "required": ["summary", "goal", "criteria", "decisions"], "properties": {
    "summary": {"type": "string"}, "goal": {"type": "string"},
    "criteria": {"type": "array", "items": {"type": "object", "required": ["text", "done"], "properties": {
        "text": {"type": "string"}, "done": {"type": "boolean"}}}},
    "decisions": {"type": "array", "items": {"type": "string"}}}}
PROMPT = ("The input is the owner's conversation with sessions about one of their tasks. Answer with JSON in the "
          "language of the conversation, very briefly, no filler: summary — one sentence on where the task stands now; "
          "goal — what must be achieved in the end, in one short sentence; criteria — acceptance criteria: how the owner "
          "will know the task is done, up to 6 items of 3–10 words (if the conversation has acceptance criteria, AC, "
          "Jira tasks — take exactly those, do not invent your own), done — true only if, by the conversation, it is "
          "already done and checked; decisions — up to 4 most important decisions of the owner, 3–12 words each, no "
          "trifles. Only what is in the conversation; nothing to say — an empty list. Answer with JSON only.")


def _line(x):
    return " ".join(str(x).split())[:ITEM_MAX]


def _criteria(value):
    out = []
    for c in value if isinstance(value, list) else []:
        if isinstance(c, dict) and _line(c.get("text") or ""):
            out.append({"text": _line(c["text"]), "done": c.get("done") is True})
    return out[:CRITERIA]


def _decisions(value):
    out = [_line(x) for x in value if isinstance(x, str)] if isinstance(value, list) else []
    return [x for x in out if x][:DECISIONS]


def summarize(title, entries, ask=real_ask):
    if not entries:
        return None
    text = f"Task: {title}\n" + "\n".join(
        f"{'owner' if e['who'] == 'owner' else e.get('session') or 'session'}: {e['text'][:ENTRY_MAX]}"
        for e in entries[-LAST:])
    try:
        out = ask(PROMPT, text, SCHEMA)
    except (OSError, ValueError, TypeError) as e:
        print(f"tasks: summary failed: {e}", file=sys.stderr, flush=True)
        return None
    if not isinstance(out, dict) or not str(out.get("summary") or "").strip():
        return None
    return {"summary": " ".join(str(out["summary"]).split())[:300], "goal": " ".join(str(out.get("goal") or "").split())[:200],
            "criteria": _criteria(out.get("criteria")), "decisions": _decisions(out.get("decisions"))}


def due(last_summary_at, last_entry_at, now, every=EVERY):
    if last_summary_at is None:
        return True
    return last_entry_at > last_summary_at and now - last_summary_at >= every
