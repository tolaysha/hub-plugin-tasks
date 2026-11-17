"""Which of the open tasks an owner's message belongs to — by a cheap model (Haiku) with a strict JSON answer schema.
The call goes through the hub watcher's `_claude` (modules/watch/watch.py): the same user environment (sign-in, proxy)
as recap, without thinking and cache. A model failure — the message simply stays without a task."""
import sys

SCHEMA = {"type": "object", "required": ["topic", "sure"],
          "properties": {"topic": {"type": ["string", "null"]}, "sure": {"type": "boolean"}}}

PROMPT = ("You are given the owner's list of open tasks (id, title and what it is about), the context (which session "
          "they are writing to and what assignment it has, the last lines of the conversation in that session) and the "
          "message itself. Return the id of the task this message is about — by meaning together with the context, not "
          "by common words (\"hub\", \"button\", \"plugin\" occur in many tasks). The message may be about something "
          "that is not on the list — then null, not the closest task. sure — true only if the message is certainly "
          "about this task; if in doubt — false. Do not invent new ids. Answer with JSON only.")


def real_ask(system, text, schema):
    # _claude is not part of the plugin API: if the core renamed it, that is a model failure, not the process crashing
    # on the same message over and over (the cursor is written after the tick; review 12.10)
    try:
        from modules.watch.watch import _claude
    except ImportError as e:
        raise OSError(f"no model call in the core: {e}") from e
    return _claude(text, system, schema)


def classify_message(topics, text, ask=real_ask, context="", accept=None):
    """A task id or None. A sure answer of the model is taken; an unsure one — only if it matches accept: the task of the
    owner's previous word in this session (engine.py). accept itself is not shown to the model — the hint dragged
    mistakes along."""
    if not topics:
        return None
    system = PROMPT + "\nTasks:\n" + "\n".join(
        f"- {t['id']}: {t['title']}" + (f" — {t['about']}" if t.get("about") else "") for t in topics)
    try:
        out = ask(system, (f"Context: {context}\n\nMessage:\n" if context else "") + text, SCHEMA)
    except (OSError, ValueError, TypeError) as e:
        print(f"tasks: classification failed: {e}", file=sys.stderr, flush=True)
        return None
    if not isinstance(out, dict):
        return None
    topic = out.get("topic")
    if out.get("sure") is not True and (accept is None or topic != accept):   # with no fitting task the model took the closest
        return None
    return topic if topic in {t["id"] for t in topics} else None


TITLE_MAX = 60   # characters of a task title — fits the list card
TITLE_SCHEMA = {"type": "object", "required": ["title"], "properties": {"title": {"type": "string"}}}
TITLE_PROMPT = ("You are given the owner's request that starts their task. Give the task a short title: 2–6 words in "
                "the language of the request, the gist of the work, no period at the end and no words like \"task\", "
                "\"need to\", \"do\". Answer with JSON only.")


def short_title(text, ask=real_ask):
    """A task title for /new-task from the request — by the model; a failure or empty — None (engine takes fallback_title)."""
    try:
        out = ask(TITLE_PROMPT, text[:4000], TITLE_SCHEMA)
    except (OSError, ValueError, TypeError) as e:
        print(f"tasks: no title came up: {e}", file=sys.stderr, flush=True)
        return None
    title = " ".join(str((out or {}).get("title") or "").split()).strip(" .")
    return fallback_title(title) if title else None


def fallback_title(text):
    """The start of the request's first line, cut at a word."""
    line = " ".join((text.strip().splitlines() or [""])[0].split())
    if len(line) <= TITLE_MAX:
        return line
    cut = line[:TITLE_MAX - 1].rsplit(" ", 1)[0].rstrip(" ,.;:—-")
    return cut + "…"
