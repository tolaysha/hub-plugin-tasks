"""What goes into a topic's history around an owner's message: their word, the session's replies up to their next word
in it and the results of the assignments that came from that word (owner 08.10: "your question — my answer, my question
— your answer, everything around my message")."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import collect


def owner_in(mid, seq, text="do it"):
    return {"seq": seq, "ts": seq, "kind": "in", "id": mid, "text": text, "channel": "web", "sender": None}


def out(seq, text="reply", **kw):
    return {"seq": seq, "ts": seq, "kind": "out", "text": text, **kw}


class Around(unittest.TestCase):
    def test_replies_after_owner_message_until_next_incoming(self):
        line = [out(1, "earlier"), owner_in("m2", 2), {"seq": 3, "kind": "tool", "name": "Bash"},
                out(4, "done"), owner_in("m5", 5), out(6, "about something else")]
        got, closed = collect.around(line, "m2")
        self.assertEqual([i["text"] for i in got], ["done"])
        self.assertTrue(closed)

    def test_window_stays_open_until_next_owner_word(self):
        got, closed = collect.around([owner_in("m2", 2), out(3, "doing it")], "m2")
        self.assertEqual([i["text"] for i in got], ["doing it"])
        self.assertFalse(closed)

    def test_any_next_incoming_message_closes_window(self):
        # a busy session (the hub): after a message from another session it answers that one, not the owner
        line = [owner_in("m2", 2), out(3, "raised an executor"),
                {"seq": 4, "kind": "in", "id": "m4", "text": "from the executor", "channel": "session", "sender": "dev-1"},
                out(5, "reply to the executor")]
        got, closed = collect.around(line, "m2")
        self.assertEqual([i["text"] for i in got], ["raised an executor"])
        self.assertTrue(closed)

    def test_error_and_empty_replies_skipped(self):
        line = [owner_in("m2", 2), out(3, "You've hit your limit", error=True), out(4, "  "), out(5, "ok")]
        self.assertEqual([i["text"] for i in collect.around(line, "m2")[0]], ["ok"])

    def test_anchor_trimmed_from_timeline_gives_nothing(self):
        self.assertEqual(collect.around([out(9, "someone else's")], "m2"), ([], None))


class Entries(unittest.TestCase):
    def test_one_reply_per_owner_message_the_last_one(self):
        timelines = {"hub": [owner_in("m2", 2), out(3, "looking"), out(4, "fixing"), out(5, "done: sign-in works")]}
        got, _, _ = collect.entries({"delegations": {}}, timelines, "m2", {"session": "hub", "text": "x", "ts": 2})
        self.assertEqual([(e["id"], e["text"]) for e in got[1:]], [("m2:re", "done: sign-in works")])

    def state(self):
        return {"messages": {"m2": {"id": "m2", "session": "hub", "text": "fix the sign-in", "created": 2,
                                    "from_owner": True}},
                "delegations": {"d7": {"id": "d7", "session": "dev-1", "for": "m2", "for_session": None, "root": "m2",
                                       "created": 3, "since": 9, "reply": "sign-in fixed"}}}

    def test_owner_message_replies_and_delegation_result(self):
        timelines = {"hub": [owner_in("m2", 2, "fix the sign-in"), out(4, "assigned dev-1")]}
        got, closed, dels = collect.entries(self.state(), timelines, "m2", {"session": "hub", "text": "fix the sign-in", "ts": 2})
        self.assertEqual(dels, ["d7"])
        self.assertEqual([(e["id"], e["who"], e["session"]) for e in got],
                         [("m2", "owner", "hub"), ("m2:re", "session", "hub"), ("d7", "session", "dev-1")])
        self.assertEqual(got[-1]["text"], "sign-in fixed")
        self.assertFalse(closed)

    def test_delegation_without_reply_yet_not_listed(self):
        st = self.state()
        st["delegations"]["d7"]["reply"] = None
        got, _, _ = collect.entries(st, {}, "m2", {"session": "hub", "text": "fix the sign-in", "ts": 2})
        self.assertEqual([e["id"] for e in got], ["m2"])


if __name__ == "__main__":
    unittest.main()
