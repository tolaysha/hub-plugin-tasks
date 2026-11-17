# Tasks — an Agents Hub plugin

A "Tasks" tab on the hub's page: the whole conversation on one piece of work — your messages, the sessions' replies,
the results of assignments — in one place, even if the work went through several sessions. Each task has "History" and
"Progress": the task, the acceptance criteria marked "done" and the main decisions.

## How to use it

- **Create a task** — in the input field of any session, `/new-task <what to do>`: the request goes to that session as
  an ordinary message, and the tab gets a task with a short title. If the hub has Jira set up (`hub.json` → `jira`),
  `/new-task PROJ-123` takes the existing Jira task (with the `jira-task` skill, if the session has it), and
  `/new-task <description>` asks the session to look for such a task in Jira. The session creates nothing in Jira
  itself: if it finds nothing, it offers to create one and works without a Jira task.
  Without the command — name the work briefly in the field on the left.
- **Write to sessions as usual** — messages about the work get into the task by themselves. A reply with the "Reply"
  button on a session's line gets into the same task exactly and without the model. An answer to a question card does
  too, if the session asked while working on the task (your last message to it before the card is in the task, no more
  than 30 minutes earlier).
- **"Progress"** is assembled from the conversation when you open it (at most once per 30 minutes, if there is
  something new), or at once — with the "Refresh" button.
- **The Jira key** of a task — as a link in the card, if it was in `/new-task`, if the session wrote the line
  "Jira task: PROJ-…" in its first answer, or later wrote plainly that it created a task (if you agreed to create one).
- **Order** — the pin keeps a task on top, cards can be dragged (from the keyboard — Alt+↑/↓). Kept in the browser.
- **Delete** — the cross; for 5 seconds, while the countdown runs, it can be undone. The task leaves the tab, and its
  conversation stays in the plugin's archive on disk (`archive.json` in its state folder) — to erase it completely,
  delete the file.

On the first start the tab has an example task: look at it and delete it.

The tab follows the page language: English by default, Russian when the hub's page is in Russian.

## How it works

The plugin's process (`main.py`) reads the hub's journal every 3 seconds (read only) and keeps its own registry of tasks:

1. Your message is bound to a task without the model if it is a reply with the "Reply" button, an answer to a question
   card asked on the task, a message to the executor of this task's assignment, or a `/new-task` request. Otherwise a
   cheap model decides which of the open tasks it belongs to (or none).
2. The session's reply to the message and the results of the assignments that came from it are added to it.
3. The visible part of the registry goes to the page through the plugin channel (`hub feed tasks`).

## Model use

The plugin makes **its own extra requests to Claude Haiku** (`claude -p --model haiku`) through this computer's Claude
Code sign-in — to the same provider as the hub's sessions; there are no other recipients.

| What | When | Tokens per call |
|---|---|---|
| Sorting a message — which task | your message, except "Reply", answers to cards on a task, messages to a task's executor and `/new-task` | ~2 thousand (+~80 per open task) |
| Summary and "Progress" | "Progress" opened (at most once per 30 min, if there is something new) or "Refresh" | ~10–15 thousand (grows with the history, up to the last 40 entries) |
| Task title | `/new-task` | ~1.3 thousand |

**Example:** 5 open tasks, 7 hours of work, about 100 of your messages and 10 summaries a day — **~300 thousand Haiku
tokens a day**. With a Claude subscription — from its limits; at the API price of Haiku 4.5 ($1 / $5 per 1 million
input / output tokens) — up to ~$0.5 a day. To spend less, answer with the "Reply" button more often and press
"Refresh" less often.

## Data and access

- **Reads:** the hub's journal (read only) and the Jira settings from `hub.json` (the address and projects — for links
  to tasks).
- **Writes:** only its own folder in the hub's state (`<state>/ext-<plugin name>/`: the registry, the cursor, the archive
  of deleted tasks) and posts to its own channel — as an `ext.post` fact through the core's spool, in its own name. It
  does not write to the journal directly and does not change anyone else's state.
- **Network:** only the model calls above — no tools, no MCP, no settings (`--tools ""`, `--strict-mcp-config`,
  `--setting-sources ""`). The model receives: your message, the last 4 lines of the conversation, the session's
  assignment and the list of open tasks; for a summary — up to the last 40 entries of the task.
- **Page:** only the hub's plugin API (`hub.feed`, `hub.postFeed`, `hub.post("/api/send")` — on your `/new-task`
  command). The conversation is drawn by the same markup module as the hub's feed (with HTML sanitizing).
- **Jira:** the plugin does not go to Jira and creates nothing there. With Jira set up, `/new-task` asks the session to
  find an existing task — the session does that with its own tools; it only offers to create a new one.
- **Keys:** the plugin does not read or keep any itself. The core's model call takes this computer's Claude Code
  sign-in (like the hub's watcher) and gives it only to the `claude` process; pool accounts are not used — the cost
  goes to the main sign-in.

## Installation

From the hub's catalog ("Settings" → "Plugins") or with a link to this repository. You need:

- a hub with plugin API 1.13+ (commands via "/"); the page language needs 1.25+, on older hubs the tab is in English;
- Python 3.12+ — `run.sh` finds it itself (your own path — the `HUB_PYTHON` variable);
- a Claude Code sign-in on this computer (for the Haiku calls);
- optionally — Jira in `hub.json` → `jira` (`url`, `projects`) and the `jira-task` skill for the sessions.

## Limitations

- The model may put a message into the wrong task or miss it; "Reply" is more reliable.
- Dragging cards does not work on a phone; order and pinning live in the browser, not on the hub.
- The plugin relies on internal modules of the hub (the watcher's model call, the feed's markup): a hub update may
  change them. Then the page shows the conversation as plain text, and the process works without the model: tasks are
  created, "Reply" and answers to cards are bound, other messages and "Progress" are not.

## Tests

```
PYTHONPATH=<hub folder> python3.12 -m unittest discover -s tests
```
