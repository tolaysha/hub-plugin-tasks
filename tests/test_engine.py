"""The extension's tick on a replay of the hub's state: the model is replaced, the channel is a list of posts."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import engine
import registry


def owner_msg(mid, created, text, session="hub"):
    return {"id": mid, "session": session, "text": text, "created": created, "from_owner": True}


def owner_in(mid, seq, text):   # timeline time — the same clock as a message's created (150 + number)
    return {"seq": seq, "ts": 145 + seq, "kind": "in", "id": mid, "text": text, "channel": "web", "sender": None}


def out(seq, text):
    return {"seq": seq, "ts": 145 + seq, "kind": "out", "text": text}


def create(seq, title):
    return {"id": f"p{seq}", "by": "owner", "text": json.dumps({"kind": "topic_create", "title": title})}


def ask_summary(seq, tid="t1", force=False):   # "Progress" is open or "Refresh" is pressed (web/main.js)
    return {"id": f"p{seq}", "by": "owner", "text": json.dumps({"kind": "topic_summarize", "id": tid, "force": force})}


def new_task(seq, request, session="hub", at=140):   # /new-task in a session's input field (web/main.js)
    return {"id": f"p{seq}", "by": "owner",
            "text": json.dumps({"kind": "topic_create", "request": request, "session": session, "at": at})}


class Context(unittest.TestCase):
    """09.10: "on the take-on button, move me to the dialog with the hub" to the executor of a Teams plugin — the model
    put it into the task about the hub's tab; the text alone cannot tell. The model gets the session, its assignment and
    the task of the previous word in it."""
    def setUp(self):
        self.reg = registry.load("/nonexistent/registry.json")
        registry.new_topic(self.reg, "The tasks tab", now=1)
        registry.new_topic(self.reg, "Teams inbox plugin", now=1)
        self.st = {"messages": {"m1": owner_msg("m1", 10, "build the Teams plugin with incoming messages"),
                                "m4": owner_msg("m4", 20, "fix the buttons", session="inbox")},
                   "delegations": {"d2": {"id": "d2", "session": "inbox", "for": None, "for_session": "hub",
                                          "created": 11, "root": "m1", "words": None, "state": "working"}},
                   "sessions": {"inbox": {"role": "executor"}}}

    def test_context_names_session_and_its_task(self):
        ctx = engine.context(self.st, self.reg, "inbox")
        self.assertIn("inbox", ctx)
        self.assertIn("build the Teams plugin with incoming messages", ctx)

    def test_context_takes_task_in_work_at_message_time(self):
        # 09.10: the session's last assignment was taken — given after the message; "the tasks plugin" went into Teams
        later = {"id": "d8", "session": "inbox", "for": None, "for_session": "hub", "created": 50, "root": "m4",
                 "state": "working", "since": 50}
        done = {**self.st["delegations"]["d2"], "state": "replied", "since": 12}
        st = {**self.st, "delegations": {"d2": done, "d8": later}}
        self.assertNotIn("fix the buttons", engine.context(st, self.reg, "inbox", before=30))   # d8 given later
        self.assertNotIn("Teams", engine.context(st, self.reg, "inbox", before=30))           # d2 handed in by 30
        self.assertIn("Teams", engine.context(st, self.reg, "inbox", before=11.5))

    def test_context_does_not_name_previous_topic(self):
        # the hint "the previous message is about task X" dragged the next mistakes after one (09.10)
        self.reg["anchors"]["m4"] = {"topic": "t2", "session": "inbox", "text": "fix the buttons", "ts": 20, "open": False}
        self.assertNotIn("Teams inbox plugin", engine.context(self.st, self.reg, "inbox", before=30))

    def test_context_passed_to_model(self):
        got = []
        engine.tick({**self.st, "messages": {**self.st["messages"],
                                             "m9": owner_msg("m9", 150, "on the button move to the hub", session="inbox")}},
                    {}, [], self.reg, engine.fresh_cursor(now=100), 300,
                    classify=lambda topics, text, context="", accept=None: got.append(context), summarize=lambda t, e: None)
        self.assertIn("build the Teams plugin", got[0])


class Dialog(unittest.TestCase):
    """12.10: the owner writes "pull main once more" to a permanent agent — the text alone does not tell the task; the
    model gets the previous lines of the conversation in this session, and its unsure choice counts if the owner's
    previous word to this session within 30 min is already in this task."""
    def setUp(self):
        self.reg = registry.load("/nonexistent/registry.json")
        registry.new_topic(self.reg, "Commands via /", now=1)
        registry.new_topic(self.reg, "Other", now=1)
        self.tl = {"dev": [owner_in("m1", 1, "make commands via slash"), out(2, "made /compact and /stop"),
                           {"seq": 3, "ts": 148, "kind": "in", "id": "x3", "text": "reminder", "sender": "hub"},
                           owner_in("m4", 4, "pull main once more"), out(5, "pulled")]}

    def test_dialog_before_message(self):
        d = engine.dialog(self.tl, "dev", "m4")
        self.assertEqual(d.splitlines(), ["owner: make commands via slash", "dev: made /compact and /stop",
                                          "hub: reminder"])
        self.assertNotIn("pulled", d)   # after the message — not context

    def test_dialog_capped(self):
        self.assertEqual(len(engine.dialog(self.tl, "dev", "m4", n=2).splitlines()), 2)
        self.assertEqual(engine.dialog(self.tl, "dev", "m9"), "")
        long = {"dev": [out(1, "a" * 5000), owner_in("m4", 2, "x")]}
        self.assertLessEqual(len(engine.dialog(long, "dev", "m4")), engine.DIALOG_CUT + 10)

    def tick(self, answer, prev_ts=150):
        seen = []
        self.reg["anchors"]["m1"] = {"topic": "t1", "session": "dev", "text": "make commands", "ts": prev_ts, "open": False}
        st = {"messages": {"m4": owner_msg("m4", 1000, "pull main once more", session="dev")}, "delegations": {}}

        def classify(topics, text, context="", accept=None):
            seen.append((context, accept))
            return answer if answer in (accept,) or answer == "sure" else None
        engine.tick(st, self.tl, [], self.reg, engine.fresh_cursor(now=100), 1001, classify=classify,
                    summarize=lambda t, e: None)
        return seen

    def test_model_gets_dialog_and_previous_topic_to_confirm(self):
        seen = self.tick("t1")
        self.assertIn("made /compact", seen[0][0])
        self.assertEqual(seen[0][1], "t1")
        self.assertEqual(self.reg["anchors"]["m4"]["topic"], "t1")

    def test_previous_word_older_than_window_not_used(self):
        seen = self.tick("t1", prev_ts=1000 - engine.FOLLOW - 1)
        self.assertIsNone(seen[0][1])
        self.assertNotIn("m4", self.reg["anchors"])

    def test_previous_word_in_closed_topic_not_used(self):
        registry.close_topic(self.reg, "t1", now=500)
        registry.new_topic(self.reg, "One more", now=1)
        self.assertIsNone(self.tick("t1")[0][1])


class ReplyButton(unittest.TestCase):
    """12.10: "Reply" on a session's line that is already in a task — the reply goes into this task without the model.
    The core writes what the owner replied to into the message label; the plugin matches the Russian form of the label
    ("в ответ на: «start of the line»" and "ответ на твой ответ по dN")."""
    def setUp(self):
        self.reg = registry.load("/nonexistent/registry.json")
        registry.new_topic(self.reg, "Commands", now=1)
        registry.new_topic(self.reg, "Other", now=1)
        self.reg["anchors"]["m1"] = {"topic": "t1", "session": "dev", "text": "make commands", "ts": 151, "open": False}
        self.long = "Made /compact and /stop [core]; " + "details " * 30
        self.tl = {"dev": [owner_in("m1", 1, "make commands"), out(2, "reading the code"),
                           {"seq": 3, "ts": 148, "kind": "in", "id": "x3", "text": "core reminder", "sender": "hub"},
                           out(3, self.long),
                           owner_in("m9", 9, "and the tests?")]}
        self.calls = []

    def run_tick(self, origin, session="dev", dels=None):
        st = {"messages": {"m9": {**owner_msg("m9", 1000, "and the tests?", session=session), "origin": origin}},
              "delegations": dels or {}}
        engine.tick(st, self.tl, [], self.reg, engine.fresh_cursor(now=100), 1001,
                    classify=lambda *a, **k: self.calls.append(1), summarize=lambda t, e: None)
        return (self.reg["anchors"].get("m9") or {}).get("topic")

    def quote(self, text):   # as core/reducer.py: _quote
        line = " ".join(text.split()).replace("]", ")").replace("[", "(")
        return line[:150] + ("…" if len(line) > 150 else "")

    def test_reply_to_session_line_after_task_word(self):
        self.assertEqual(self.run_tick(f" от владельца, в ответ на: «{self.quote(self.long)}»"), "t1")
        self.assertEqual(self.calls, [])

    def test_reply_to_intermediate_line_too(self):
        self.assertEqual(self.run_tick(" от владельца, в ответ на: «reading the code»"), "t1")

    def test_reply_to_line_outside_any_task_goes_to_model(self):
        self.reg["anchors"].pop("m1")
        self.assertIsNone(self.run_tick(" от владельца, в ответ на: «reading the code»"))
        self.assertEqual(self.calls, [1])

    def test_reply_to_closed_task_goes_to_model(self):
        registry.close_topic(self.reg, "t1", now=5)
        self.run_tick(" от владельца, в ответ на: «reading the code»")
        self.assertEqual(self.calls, [1])

    def test_reply_goes_to_latest_matching_line(self):
        # review 12.10: short lines ("reading the code", "Done.") repeat — take the last one before the reply
        self.reg["anchors"]["m5"] = {"topic": "t2", "session": "dev", "text": "another matter", "ts": 155, "open": False}
        self.tl["dev"][-1:-1] = [owner_in("m5", 5, "another matter"), out(6, "reading the code")]
        self.assertEqual(self.run_tick(" от владельца, в ответ на: «reading the code»"), "t2")

    def test_reply_to_delegation_report(self):
        dels = {"d7": {"id": "d7", "session": "ex", "root": "m1", "created": 160, "reply": "done"}}
        self.assertEqual(self.run_tick(" от владельца, ответ на твой ответ по d7", session="ex", dels=dels), "t1")
        self.assertEqual(self.calls, [])

    def test_plain_message_goes_to_model(self):
        self.run_tick(" от владельца")
        self.assertEqual(self.calls, [1])


class CardAnswer(unittest.TestCase):
    """12.10: the owner's answer to question card qN goes into the task the session worked on when it asked: their last
    word to this session before the card is already in an open task and was no earlier than FOLLOW before it — without
    the model. The core: card qN is an "ask" row of the timeline with seq N, the answer is a message with
    answers_question=qN (core/reducer.py)."""
    def setUp(self):
        self.reg = registry.load("/nonexistent/registry.json")
        registry.new_topic(self.reg, "e2e", now=1)
        registry.new_topic(self.reg, "Other", now=1)
        self.reg["anchors"]["m1"] = {"topic": "t1", "session": "dev", "text": "run e2e", "ts": 146, "open": False}
        self.tl = {"dev": [owner_in("m1", 1, "run e2e"), out(2, "running"),
                           {"seq": 3, "ts": 148, "kind": "in", "id": "x3", "text": "core reminder", "sender": "hub"},
                           {"seq": 4, "ts": 149, "kind": "ask", "request_id": "r4", "tool": "AskUserQuestion"}]}
        self.calls = []

    def run_tick(self, card="q4", dels=None, **more):
        msg = {**owner_msg("m9", 1000, "The owner's answer to question q4:\n— recheck?: yes", session="dev"),
               "answers_question": card, **more}
        engine.tick({"messages": {"m9": msg}, "delegations": dels or {}}, self.tl, [], self.reg,
                    engine.fresh_cursor(now=100), 1001,
                    classify=lambda *a, **k: self.calls.append(1), summarize=lambda t, e: None)
        return (self.reg["anchors"].get("m9") or {}).get("topic")

    def test_answer_goes_to_task_of_word_before_card(self):
        self.assertEqual(self.run_tick(), "t1")
        self.assertEqual(self.calls, [])

    def test_word_before_card_outside_tasks_goes_to_model(self):
        # the owner's last word before the card is not from the task: the earlier one (from the task) is not taken
        self.tl["dev"][3:3] = [owner_in("m5", 3, "and what about the weather")]
        self.assertIsNone(self.run_tick())
        self.assertEqual(self.calls, [1])

    def test_card_long_after_word_goes_to_model(self):
        self.tl["dev"][-1]["ts"] = 146 + engine.FOLLOW + 1
        self.run_tick()
        self.assertEqual(self.calls, [1])

    def test_closed_task_goes_to_model(self):
        registry.close_topic(self.reg, "t1", now=5)
        self.run_tick()
        self.assertEqual(self.calls, [1])

    def test_card_not_in_feed_goes_to_model(self):
        self.run_tick(card="q77")
        self.assertEqual(self.calls, [1])

    def run_with_letter(self, root):   # before the card — a letter of the executor of an assignment with word root
        self.tl["dev"][-1:-1] = [{"seq": 4, "ts": 149, "kind": "in", "id": "m8", "text": "result", "sender": "ex"}]
        self.tl["dev"][-1]["seq"] = 5
        dels = {"d6": {"id": "d6", "session": "ex", "root": root, "created": 147, "state": "done", "since": 148}}
        msg = {**owner_msg("m9", 1000, "answer", session="dev"), "answers_question": "q5"}
        engine.tick({"messages": {"m9": msg}, "delegations": dels, "sessions": {"dev": {}, "ex": {}}}, self.tl, [],
                    self.reg, engine.fresh_cursor(now=100), 1001,
                    classify=lambda *a, **k: self.calls.append(1), summarize=lambda t, e: None)
        return (self.reg["anchors"].get("m9") or {}).get("topic")

    def test_letter_from_executor_of_other_word_goes_to_model(self):
        # 12.10 (a live journal): the agent asked about another matter after the result of an executor on another owner's word
        self.assertIsNone(self.run_with_letter("m50"))
        self.assertEqual(self.calls, [1])

    def test_letter_from_executor_of_same_task_keeps_link(self):
        self.reg["anchors"]["m50"] = {"topic": "t1", "session": "hub", "text": "x", "ts": 140, "open": False}
        self.assertEqual(self.run_with_letter("m50"), "t1")
        self.assertEqual(self.calls, [])

    def test_card_of_delegation_goes_to_its_task(self):
        # a question on an assignment: the core sets for_delegation — the task by the assignment's root, no timeline needed
        self.tl["dev"] = []
        dels = {"d7": {"id": "d7", "session": "dev", "root": "m1", "created": 150}}
        self.assertEqual(self.run_tick(dels=dels, for_delegation="d7"), "t1")
        self.assertEqual(self.calls, [])


class TopicsForModel(unittest.TestCase):
    def test_goal_or_summary_goes_as_description(self):
        reg = registry.load("/nonexistent/registry.json")
        registry.new_topic(reg, "A", now=1)
        registry.new_topic(reg, "B", now=1)
        reg["topics"]["t1"]["progress"] = {"goal": "goal A"}
        reg["topics"]["t2"]["summary"] = "summary B"
        self.assertEqual([t["about"] for t in engine.topics_for_model(reg)], ["goal A", "summary B"])


class ExecutorOfTask(unittest.TestCase):
    """The owner's word to the executor of an assignment that grew from a task's message goes into the same task without
    the model (08.10: "a hybrid: structure + meaning"; 09.10: the owner's replies to an executor did not get into the
    history)."""
    def setUp(self):
        self.reg = registry.load("/nonexistent/registry.json")
        self.cursor = engine.fresh_cursor(now=100)
        self.calls = []
        registry.new_topic(self.reg, "Tab", now=1)
        self.reg["anchors"]["m2"] = {"topic": "t1", "session": "hub", "text": "go on with the tab", "ts": 2, "open": False}
        # hub task run without --for: the core sets root anyway — the hub's turn on the owner's word m2
        self.dels = {"d3": {"id": "d3", "session": "dev-1", "for": None, "for_session": "hub", "created": 3, "root": "m2"}}
        self.sessions = {"dev-1": {"role": "executor"}, "hub-dev": {"role": "agent"}}

    def tick(self, msgs):
        engine.tick({"messages": msgs, "delegations": self.dels, "sessions": self.sessions}, {}, [], self.reg,
                    self.cursor, 300,
                    classify=lambda topics, text, context="", accept=None: self.calls.append(text), summarize=lambda t, e: None)

    def test_owner_word_to_executor_goes_to_task_without_model(self):
        self.tick({"m9": owner_msg("m9", 150, "add onboarding", session="dev-1")})
        self.assertEqual(self.reg["anchors"]["m9"]["topic"], "t1")
        self.assertEqual(self.calls, [])

    def test_executor_of_closed_task_falls_back_to_model(self):
        registry.close_topic(self.reg, "t1", now=10)
        self.tick({"m9": owner_msg("m9", 150, "add onboarding", session="dev-1")})
        self.assertNotIn("m9", self.reg["anchors"])

    def test_session_executing_two_tasks_falls_back_to_model(self):
        registry.new_topic(self.reg, "Another", now=5)
        self.reg["anchors"]["m4"] = {"topic": "t2", "session": "hub", "text": "x", "ts": 4, "open": False}
        self.dels["d5"] = {"id": "d5", "session": "dev-1", "for": "m4", "for_session": None, "created": 5, "root": "m4"}
        self.tick({"m9": owner_msg("m9", 150, "something", session="dev-1")})
        self.assertEqual(self.calls, ["something"])


    def test_permanent_agent_falls_back_to_model(self):
        # an agent (hub-dev) does a task's assignment but does a lot else too — do not attach everything of it
        self.dels["d3"]["session"] = "hub-dev"
        self.tick({"m9": owner_msg("m9", 150, "about the token", session="hub-dev")})
        self.assertEqual(self.calls, ["about the token"])


class Tick(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.reg = registry.load("/nonexistent/registry.json")
        self.cursor = engine.fresh_cursor(now=100)
        self.contexts = []

    def classify(self, topics, text, context="", accept=None):
        self.contexts.append(context)
        self.calls.append(text)
        return topics[0]["id"] if "sign-in" in text else None

    def tick(self, state, timelines, posts, now=200):
        return engine.tick(state, timelines, posts, self.reg, self.cursor, now,
                           classify=self.classify,
                           summarize=lambda title, entries: {"summary": f"{len(entries)} entries", "goal": title,
                                                             "criteria": [{"text": "x", "done": True}], "decisions": []})

    def test_messages_before_first_start_not_sent_to_model(self):
        st = {"messages": {"m1": owner_msg("m1", 50, "about the sign-in")}, "delegations": {}}
        self.tick(st, {}, [create(2, "Sign-in")])
        self.assertEqual(self.calls, [])
        self.assertEqual(registry.entries(self.reg, "t1"), [])

    def test_owner_message_reply_and_later_reply_land_in_topic(self):
        posts = [create(2, "Fix the sign-in")]
        st = {"messages": {"m5": owner_msg("m5", 150, "fix the sign-in")}, "delegations": {}}
        tl = {"hub": [owner_in("m5", 5, "fix the sign-in")]}
        self.tick(st, tl, posts)
        self.assertEqual([e["id"] for e in registry.entries(self.reg, "t1")], ["m5"])

        tl["hub"].append(out(6, "fixing"))
        self.tick(st, tl, posts, now=210)
        self.assertEqual([e["text"] for e in registry.entries(self.reg, "t1")], ["fix the sign-in", "fixing"])
        self.assertEqual(self.calls, ["fix the sign-in"])   # the second tick does not call the model

    def test_unrelated_message_not_tagged(self):
        st = {"messages": {"m5": owner_msg("m5", 150, "what time is it")}, "delegations": {}}
        self.tick(st, {}, [create(2, "Sign-in")])
        self.assertEqual(self.calls, ["what time is it"])
        self.assertEqual(self.reg["anchors"], {})

    def test_no_open_topics_no_model_call(self):
        st = {"messages": {"m5": owner_msg("m5", 150, "about the sign-in")}, "delegations": {}}
        self.tick(st, {}, [])
        self.assertEqual(self.calls, [])

    def test_commands_and_session_messages_not_classified(self):
        st = {"messages": {"m5": {**owner_msg("m5", 150, "/compact sign-in"), "command": True},
                           "m6": {"id": "m6", "session": "x", "text": "sign-in", "created": 160}},
              "delegations": {}}
        self.tick(st, {}, [create(2, "Sign-in")])
        self.assertEqual(self.calls, [])

    def test_closed_topic_stops_collecting(self):
        posts = [create(2, "Sign-in")]
        st = {"messages": {"m5": owner_msg("m5", 150, "fix the sign-in")}, "delegations": {}}
        tl = {"hub": [owner_in("m5", 5, "fix the sign-in")]}
        self.tick(st, tl, posts)
        posts.append({"id": "p9", "by": "owner", "text": json.dumps({"kind": "topic_close", "id": "t1"})})
        tl["hub"].append(out(6, "after closing"))
        self.tick(st, tl, posts, now=210)
        self.assertEqual(self.reg["topics"]["t1"]["state"], "done")
        self.assertEqual(len(registry.entries(self.reg, "t1")), 1)

    def test_close_and_reopen_through_archive(self):
        posts = [create(2, "Sign-in")]
        st = {"messages": {"m5": owner_msg("m5", 150, "fix the sign-in")}, "delegations": {}}
        tl = {"hub": [owner_in("m5", 5, "fix the sign-in")]}
        archive = {}
        run = lambda now: engine.tick(st, tl, posts, self.reg, self.cursor, now, classify=self.classify,
                                      summarize=lambda t, e: None, archive=archive)
        run(200)
        posts.append({"id": "p3", "by": "owner", "text": json.dumps({"kind": "topic_close", "id": "t1"})})
        run(210)
        self.assertEqual((self.reg["entries"], self.reg["anchors"]), ({}, {}))
        self.assertIn("t1", archive)
        posts.append({"id": "p4", "by": "owner", "text": json.dumps({"kind": "topic_reopen", "id": "t1"})})
        run(220)
        self.assertEqual([e["id"] for e in registry.entries(self.reg, "t1")], ["m5"])
        self.assertEqual(archive, {})

    def test_idle_round(self):
        # 12.10: nothing new in the journal — the round is skipped, but not longer than IDLE (a /new-task wait, re-posting)
        self.assertFalse(engine.busy_round(seq=7, last_seq=7, now=130, last_full=100))
        self.assertTrue(engine.busy_round(seq=8, last_seq=7, now=101, last_full=100))
        self.assertTrue(engine.busy_round(seq=7, last_seq=7, now=100 + engine.IDLE, last_full=100))

    def test_undo_delete_reopens_topic(self):
        posts = [create(2, "Sign-in"),
                 {"id": "p3", "by": "owner", "text": json.dumps({"kind": "topic_close", "id": "t1"})},
                 {"id": "p4", "by": "owner", "text": json.dumps({"kind": "topic_reopen", "id": "t1"})}]
        self.tick({"messages": {}, "delegations": {}}, {}, posts)
        self.assertEqual(self.reg["topics"]["t1"]["state"], "open")

    def test_window_closed_by_next_owner_word_is_not_followed_again(self):
        posts = [create(2, "Sign-in")]
        st = {"messages": {"m5": owner_msg("m5", 150, "fix the sign-in")}, "delegations": {}}
        tl = {"hub": [owner_in("m5", 5, "fix the sign-in"), out(6, "ok"), owner_in("m7", 7, "what time is it")]}
        self.tick(st, tl, posts)
        self.assertFalse(self.reg["anchors"]["m5"]["open"])

    def test_no_summary_without_request(self):
        # 12.10: a summary costs tokens — it is not counted by itself
        posts = [create(2, "Sign-in")]
        st = {"messages": {"m5": owner_msg("m5", 150, "fix the sign-in")}, "delegations": {}}
        tl = {"hub": [owner_in("m5", 5, "fix the sign-in"), out(6, "fixing")]}
        self.tick(st, tl, posts)
        self.assertFalse(self.reg["topics"]["t1"]["summary"])
        self.assertNotIn("progress", self.reg["topics"]["t1"])

    def test_summary_over_whole_history_on_request(self):
        posts = [create(2, "Sign-in")]
        st = {"messages": {"m5": owner_msg("m5", 150, "fix the sign-in")}, "delegations": {}}
        tl = {"hub": [owner_in("m5", 5, "fix the sign-in"), out(6, "fixing")]}
        self.tick(st, tl, posts)
        posts.append(ask_summary(3))
        self.tick(st, tl, posts, now=210)
        t = self.reg["topics"]["t1"]
        self.assertEqual(t["summary"], "2 entries")
        self.assertEqual((t["progress"]["goal"], t["progress"]["criteria"], t["progress"]["at"]),
                         ("Sign-in", [{"text": "x", "done": True}], 210))

    def _topic(self):
        registry.new_topic(self.reg, "Sign-in", now=1)
        registry.add_entry(self.reg, "t1", {"id": "m1", "who": "owner", "session": "hub", "text": "x", "ts": 2})
        self.sums = []
        self.summarize = lambda title, entries: self.sums.append(len(entries)) or {
            "summary": "s", "goal": title, "criteria": [], "decisions": []}

    def _ask(self, posts, now):
        engine.tick({"messages": {}, "delegations": {}}, {}, posts, self.reg, self.cursor, now,
                    classify=self.classify, summarize=self.summarize)

    def test_anchor_of_message_gone_from_timeline_closes_when_old(self):
        # review 12.10: the timeline is cut — the reply window of such an anchor would never close (a scan every tick)
        registry.new_topic(self.reg, "Sign-in", now=1)
        for mid, ts in (("m1", 200 - engine.GONE - 1), ("m2", 190)):
            self.reg["anchors"][mid] = {"topic": "t1", "session": "hub", "text": "x", "ts": ts, "open": True}
        engine.tick({"messages": {}, "delegations": {}}, {"hub": []}, [], self.reg, self.cursor, 200,
                    classify=self.classify, summarize=lambda t, e: None)
        self.assertFalse(self.reg["anchors"]["m1"]["open"])
        self.assertTrue(self.reg["anchors"]["m2"]["open"])   # a fresh one may still wait in the session's queue

    def test_summary_not_more_often_than_every(self):
        self._topic()
        posts = [ask_summary(2)]
        self._ask(posts, 200)
        registry.add_entry(self.reg, "t1", {"id": "m2", "who": "owner", "session": "hub", "text": "y", "ts": 300})
        posts.append(ask_summary(3))
        self._ask(posts, 200 + 1700)          # there is something new, but 30 minutes have not passed
        posts.append(ask_summary(4))
        self._ask(posts, 200 + 1800)
        self.assertEqual(self.sums, [1, 2])

    def test_no_summary_when_nothing_new(self):
        self._topic()
        posts = [ask_summary(2)]
        self._ask(posts, 200)
        posts.append(ask_summary(3))
        self._ask(posts, 200 + 7200)
        self.assertEqual(self.sums, [1])

    def test_refresh_button_summarizes_at_once(self):
        self._topic()
        posts = [ask_summary(2)]
        self._ask(posts, 200)
        posts.append(ask_summary(3, force=True))
        self._ask(posts, 210)
        self.assertEqual(self.sums, [1, 1])

    def test_request_handled_once(self):
        self._topic()
        posts = [ask_summary(2, force=True)]
        self._ask(posts, 200)
        self._ask(posts, 300)
        self.assertEqual(self.sums, [1])

    def test_old_progress_without_criteria_recounted_on_request(self):
        # 11.10: progress of the earlier kind (done / left) is recounted into acceptance criteria
        self._topic()
        self.reg["topics"]["t1"]["progress"] = {"goal": "g", "done": ["a"], "todo": [], "decisions": [], "at": 5}
        self.cursor["summarized_at"]["t1"] = 190
        self._ask([ask_summary(2)], 200)
        self.assertIn("criteria", self.reg["topics"]["t1"]["progress"])

    def test_failed_summary_keeps_previous(self):
        registry.new_topic(self.reg, "Sign-in", now=1)
        self.reg["topics"]["t1"]["summary"] = "was"
        registry.add_entry(self.reg, "t1", {"id": "m1", "who": "owner", "session": "hub", "text": "x", "ts": 2})
        engine.tick({"messages": {}, "delegations": {}}, {}, [ask_summary(2)], self.reg, self.cursor, 200,
                    classify=self.classify, summarize=lambda title, entries: None)
        self.assertEqual(self.reg["topics"]["t1"]["summary"], "was")
        self.assertNotIn("progress", self.reg["topics"]["t1"])

    def test_lost_cursor_skips_old_requests(self):
        # review 12.10: a broken cursor (feed_seq None) — the channel's old requests are not parsed again, or there are duplicate tasks
        self.cursor["feed_seq"] = None
        self.tick({"messages": {}, "delegations": {}}, {}, [create(2, "Sign-in")])
        self.assertEqual(self.reg["topics"], {})
        self.assertEqual(self.cursor["feed_seq"], 2)
        self.tick({"messages": {}, "delegations": {}}, {}, [create(2, "Sign-in"), create(3, "New")], now=210)
        self.assertEqual([t["title"] for t in self.reg["topics"].values()], ["New"])

    def test_topic_create_handled_once(self):
        posts = [create(2, "Sign-in")]
        self.tick({"messages": {}, "delegations": {}}, {}, posts)
        self.tick({"messages": {}, "delegations": {}}, {}, posts, now=210)
        self.assertEqual(list(self.reg["topics"]), ["t1"])


class NewTaskCommand(unittest.TestCase):
    """10.10: "/new-task <request>" — the request goes to the session as an ordinary message, and the task is created by
    itself: the title is short, from the request (the model), the task's first message is this request, without the
    classifier."""
    REQ = "fix the admin sign-in, after the update the login page fails with a 500"

    def setUp(self):
        self.reg = registry.load("/nonexistent/registry.json")
        self.cursor = engine.fresh_cursor(now=100)
        self.calls, self.named = [], []

    def tick(self, state, posts, now=200, name=None):
        def namer(text):
            self.named.append(text)
            return "Admin sign-in"
        return engine.tick(state, {"hub": [owner_in("m5", 5, self.REQ)]}, posts, self.reg, self.cursor, now,
                           classify=lambda topics, text, context="", accept=None: self.calls.append(text),
                           summarize=lambda title, entries: None, name=name or namer)

    def msg(self, mid="m5", created=150, session="hub", text=None):
        return owner_msg(mid, created, text or self.REQ, session=session)

    def test_topic_named_by_model_and_request_is_its_first_message(self):
        self.tick({"messages": {"m5": self.msg()}, "delegations": {}}, [new_task(2, self.REQ)])
        self.assertEqual(self.reg["topics"]["t1"]["title"], "Admin sign-in")
        self.assertEqual(self.named, [self.REQ])
        self.assertEqual(self.reg["anchors"]["m5"]["topic"], "t1")
        self.assertEqual([e["id"] for e in registry.entries(self.reg, "t1")], ["m5"])
        self.assertEqual(self.calls, [])   # no classifier needed — the task is known

    def test_request_found_inside_rewritten_text(self):
        # review 12.10: the core may rewrite the owner's words ("The answer to question qN …: <text>") — the request is inside the text
        text = f"The answer to question q1 (\"what next?\"): {self.REQ}"
        self.tick({"messages": {"m5": self.msg(text=text)}, "delegations": {}}, [new_task(2, self.REQ)])
        self.assertEqual(self.reg["anchors"]["m5"]["topic"], "t1")

    def test_message_journaled_after_request_still_linked(self):
        posts = [new_task(2, self.REQ)]
        self.tick({"messages": {}, "delegations": {}}, posts)
        self.assertEqual(self.reg["topics"], {})   # the task waits for its message
        self.tick({"messages": {"m5": self.msg()}, "delegations": {}}, posts, now=203)
        self.assertEqual(self.reg["anchors"]["m5"]["topic"], "t1")
        self.assertEqual(list(self.reg["topics"]), ["t1"])
        self.assertEqual(self.calls, [])

    def test_browser_clock_ahead_still_links(self):
        # review 12.10: the request's time comes from the browser's clock; 40 s fast — the message is "before" the request
        self.tick({"messages": {"m5": self.msg(created=100)}, "delegations": {}}, [new_task(2, self.REQ)])
        self.assertEqual(self.reg["anchors"]["m5"]["topic"], "t1")

    def test_only_message_to_that_session_after_request(self):
        st = {"messages": {"m1": self.msg("m1", created=20),                  # the same text earlier — not it
                           "m2": self.msg("m2", created=150, session="ops"),  # to another session — not it
                           "m5": self.msg()}, "delegations": {}}
        self.tick(st, [new_task(2, self.REQ)])
        self.assertEqual(sorted(self.reg["anchors"]), ["m5"])

    def test_message_never_came_no_task(self):
        # the send failed — the typed text went back to the field, no task; repeating the command creates it
        posts = [new_task(2, self.REQ)]
        self.tick({"messages": {}, "delegations": {}}, posts)
        self.tick({"messages": {}, "delegations": {}}, posts, now=140 + engine.WAIT + 1)
        self.assertEqual(self.reg["topics"], {})
        self.assertEqual(self.reg["waiting"], [])

    def test_long_request_matched_by_its_head(self):
        # a channel post is up to 2000 characters: the page sends the start of a long request
        long = self.REQ + " details" * 300
        self.tick({"messages": {"m5": self.msg(text=long)}, "delegations": {}}, [new_task(2, long[:1500])])
        self.assertEqual(self.reg["anchors"]["m5"]["topic"], "t1")

    def test_model_failure_gives_short_title_from_request(self):
        self.tick({"messages": {"m5": self.msg()}, "delegations": {}}, [new_task(2, self.REQ)], name=lambda text: None)
        title = self.reg["topics"]["t1"]["title"]
        self.assertTrue(self.REQ.startswith(title.rstrip("…")))
        self.assertLessEqual(len(title), 60)


JIRA = {"url": "https://x.atlassian.net", "projects": ["PROJ", "UTF"]}


class JiraTask(unittest.TestCase):
    """12.10: /new-task with Jira — by a key the session takes the Jira task, by a description it looks for the same one;
    it creates nothing in Jira itself — it offers to (owner 12.10). The Jira key goes to the tab's task: into the title and
    as a link; from the session's answer — only from the line "Jira task: KEY" (when searching the session lists similar
    ones — their keys are not the task)."""
    def setUp(self):
        self.reg = registry.load("/nonexistent/registry.json")
        self.cursor = engine.fresh_cursor(now=100)
        self.named = []

    def tick(self, request, name, tl_extra=(), now=200, sent=None):
        sent = sent or f"{name}\n\nUse the jira-task skill"
        posts = [{"id": "p2", "by": "owner", "text": json.dumps(
            {"kind": "topic_create", "request": sent, "name": name, "session": "hub", "at": 140})}]
        st = {"messages": {"m5": owner_msg("m5", 150, sent)}, "delegations": {}}
        tl = {"hub": [owner_in("m5", 5, sent), *tl_extra]}

        def namer(text):
            self.named.append(text)
            return "Short name"
        engine.tick(st, tl, posts, self.reg, self.cursor, now, classify=lambda *a, **k: None,
                    summarize=lambda t, e: None, name=namer, jira=JIRA)
        return self.reg["topics"].get("t1")

    def test_key_request_titled_by_key_without_model(self):
        t = self.tick("proj-444", "proj-444")
        self.assertEqual((t["title"], t["jira"]), ("PROJ-444", "PROJ-444"))
        self.assertEqual(self.named, [])

    def test_key_title_completed_from_session_answer(self):
        self.tick("proj-444", "proj-444")
        t = self.tick("proj-444", "proj-444", tl_extra=[out(6, "PROJ-444: payment fails. AC: …"),
                                                     owner_in("m7", 7, "ok")], now=210)
        self.assertEqual(t["title"], "PROJ-444 · Short name")
        self.assertIn("payment fails", self.named[-1])

    def test_description_named_by_owner_words_and_key_from_answer(self):
        t = self.tick("x", "fix the payment bug")
        self.assertEqual((t["title"], t.get("jira")), ("Short name", None))
        self.assertEqual(self.named, ["fix the payment bug"])
        t = self.tick("x", "fix the payment bug", now=210, tl_extra=[
            out(6, "Searching Jira, UTF-8 has nothing to do with it.\nJira task: https://x.atlassian.net/browse/PROJ-90\nAC: …"),
            owner_in("m7", 7, "ok")])
        self.assertEqual((t["title"], t["jira"]), ("PROJ-90 · Short name", "PROJ-90"))

    def test_similar_tasks_listed_by_session_are_not_the_key(self):
        t = self.tick("x", "fix the sign-in", now=210, tl_extra=[
            out(6, "Similar: PROJ-90, PROJ-91 — not it. Create a new one? Criteria so far: …"), owner_in("m7", 7, "ok")])
        self.assertIsNone(t.get("jira"))

    def test_marked_bare_key_from_answer(self):
        # the line of earlier versions of the plugin, in Russian
        t = self.tick("x", "fix the sign-in", now=210, tl_extra=[out(6, "Нашёл.\n**Задача Jira:** proj-91\nAC: …"),
                                                             owner_in("m7", 7, "ok")])
        self.assertEqual(t["jira"], "PROJ-91")

    def test_key_in_request_with_words_taken_at_once(self):
        t = self.tick("x", "PROJ-444 fix the payment button")
        self.assertEqual((t["jira"], t["title"]), ("PROJ-444", "Short name"))

    def test_encoding_name_is_not_a_task_of_utf_project(self):
        self.assertIsNone(engine.find_jira_key("we write in UTF-8, no BOM", JIRA))
        self.assertEqual(engine.find_jira_key("UTF-8 and task UTF-31", JIRA), "UTF-31")

    def test_unknown_project_and_lookalikes_ignored(self):
        t = self.tick("x", "fix the sign-in", tl_extra=[out(6, "SHA-256 and ABC-12 are not tasks"), owner_in("m7", 7, "ok")])
        self.assertIsNone(t.get("jira"))

    def test_no_jira_settings_no_keys(self):
        posts = [{"id": "p2", "by": "owner", "text": json.dumps(
            {"kind": "topic_create", "request": "proj-444", "name": "proj-444", "session": "hub", "at": 140})}]
        st = {"messages": {"m5": owner_msg("m5", 150, "proj-444")}, "delegations": {}}
        engine.tick(st, {}, posts, self.reg, self.cursor, 200, classify=lambda *a, **k: None,
                    summarize=lambda t, e: None, name=lambda t: "Name")
        self.assertIsNone(self.reg["topics"]["t1"].get("jira"))


class JiraCreated(unittest.TestCase):
    """12.10: a task's Jira key — only a reliable one: in the /new-task command, in the session's first answer to it
    (jira_link), or the session wrote plainly that it created the task (the Russian phrasing is matched: "Я завёл задачу
    PROJ-123", the fixtures below are Russian on purpose). Guessing by frequency is removed (owner 12.10: the key of the
    branch the tests ran on became the key of the task about the tests)."""
    def setUp(self):
        self.reg = registry.load("/nonexistent/registry.json")
        registry.new_topic(self.reg, "Mailing domain", now=1)

    def add(self, mid, who, text, ts):
        registry.add_entry(self.reg, "t1", {"id": mid, "who": who, "session": "s", "text": text, "ts": ts})

    def run_rule(self, jira=JIRA):
        engine.jira_created(self.reg, jira, ["t1"])
        return self.reg["topics"]["t1"].get("jira")

    def test_session_created_task(self):
        self.add("m1:re", "session", "Связано с PROJ-3. Я завёл задачу [PROJ-123](https://x.atlassian.net/browse/PROJ-123)", 1)
        self.assertEqual(self.run_rule(), "PROJ-123")
        self.assertEqual(self.reg["topics"]["t1"]["title"], "Mailing domain")   # the title is not touched

    def test_other_phrasings(self):
        for text, key in (("Создал задачу PROJ-7 с критериями приёмки", "PROJ-7"), ("Завела PROJ-8.", "PROJ-8"),
                          ("Задача заведена: PROJ-9", None), ("создана новая задача «PROJ-10»", None)):
            self.reg["topics"]["t1"].pop("jira", None)
            self.reg["entries"]["t1"] = {}
            self.add("m1:re", "session", text, 1)
            self.assertEqual(self.run_rule(), key, text)

    def test_quoted_phrase_is_not_creation(self):
        # the session's report quotes someone else's "Я завёл задачу PROJ-123" — this is not a task it created
        self.add("m1:re", "session", "Ключ останется: там есть «Я завёл задачу PROJ-123». А тут `создал PROJ-9`.", 1)
        self.assertIsNone(self.run_rule())
        self.add("m2:re", "session", "Цитата «просто текст» закрыта. Я завёл задачу PROJ-11.", 2)
        self.assertEqual(self.run_rule(), "PROJ-11")

    def test_frequent_key_without_creation_is_not_task_key(self):
        for i in range(8):
            self.add(f"m{i}:re", "session", f"e2e on PROJ-56 passed, run {i}", i)
        self.assertIsNone(self.run_rule())

    def test_owner_words_do_not_count(self):
        self.add("m1", "owner", "создал задачу PROJ-7, посмотри", 1)
        self.assertIsNone(self.run_rule())

    def test_old_guess_dropped(self):
        self.reg["topics"]["t1"].update(jira="PROJ-56", jira_guess=True)
        self.add("m1:re", "session", "PROJ-56 PROJ-56 PROJ-56", 1)
        self.assertIsNone(self.run_rule())
        self.assertNotIn("jira_guess", self.reg["topics"]["t1"])

    def test_new_task_key_kept(self):
        self.reg["topics"]["t1"]["jira"] = "PROJ-1"            # /new-task proj-1 or a jira-task answer (jira_link)
        self.add("m1:re", "session", "Я завёл задачу PROJ-8", 1)
        self.assertEqual(self.run_rule(), "PROJ-1")

    def test_no_jira_settings_no_key(self):
        self.add("m1:re", "session", "Я завёл задачу PROJ-8", 1)
        self.assertIsNone(self.run_rule(jira=None))

    def test_tick_checks_old_topics(self):
        self.add("m1:re", "session", "Я завёл задачу PROJ-8", 1)
        engine.tick({"messages": {}, "delegations": {}}, {}, [], self.reg, engine.fresh_cursor(now=100), 200,
                    classify=lambda *a, **k: None, summarize=lambda t, e: None, jira=JIRA)
        self.assertEqual(self.reg["topics"]["t1"]["jira"], "PROJ-8")

if __name__ == "__main__":
    unittest.main()
