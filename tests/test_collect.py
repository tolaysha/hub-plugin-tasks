"""Что попадает в историю темы вокруг сообщения владельца: его слово, ответы сессии до его следующего слова в ней
и итоги поручений, которые от этого слова пошли (владелец 08.10: «твой вопрос — мой ответ, мой вопрос — твой ответ,
всё, что вокруг моего сообщения»)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import collect


def owner_in(mid, seq, text="сделай"):
    return {"seq": seq, "ts": seq, "kind": "in", "id": mid, "text": text, "channel": "web", "sender": None}


def out(seq, text="ответ", **kw):
    return {"seq": seq, "ts": seq, "kind": "out", "text": text, **kw}


class Around(unittest.TestCase):
    def test_replies_after_owner_message_until_next_incoming(self):
        line = [out(1, "раньше"), owner_in("m2", 2), {"seq": 3, "kind": "tool", "name": "Bash"},
                out(4, "сделала"), owner_in("m5", 5), out(6, "про другое")]
        got, closed = collect.around(line, "m2")
        self.assertEqual([i["text"] for i in got], ["сделала"])
        self.assertTrue(closed)

    def test_window_stays_open_until_next_owner_word(self):
        got, closed = collect.around([owner_in("m2", 2), out(3, "делаю")], "m2")
        self.assertEqual([i["text"] for i in got], ["делаю"])
        self.assertFalse(closed)

    def test_any_next_incoming_message_closes_window(self):
        # загруженная сессия (хаб): после сообщения от другой сессии она отвечает уже ей, не владельцу
        line = [owner_in("m2", 2), out(3, "поднял исполнителя"),
                {"seq": 4, "kind": "in", "id": "m4", "text": "от исполнителя", "channel": "session", "sender": "dev-1"},
                out(5, "ответ исполнителю")]
        got, closed = collect.around(line, "m2")
        self.assertEqual([i["text"] for i in got], ["поднял исполнителя"])
        self.assertTrue(closed)

    def test_error_and_empty_replies_skipped(self):
        line = [owner_in("m2", 2), out(3, "You've hit your limit", error=True), out(4, "  "), out(5, "ок")]
        self.assertEqual([i["text"] for i in collect.around(line, "m2")[0]], ["ок"])

    def test_anchor_trimmed_from_timeline_gives_nothing(self):
        self.assertEqual(collect.around([out(9, "чужое")], "m2"), ([], None))


class Entries(unittest.TestCase):
    def test_one_reply_per_owner_message_the_last_one(self):
        timelines = {"hub": [owner_in("m2", 2), out(3, "смотрю"), out(4, "чиню"), out(5, "готово: вход работает")]}
        got, _, _ = collect.entries({"delegations": {}}, timelines, "m2", {"session": "hub", "text": "x", "ts": 2})
        self.assertEqual([(e["id"], e["text"]) for e in got[1:]], [("m2:re", "готово: вход работает")])

    def state(self):
        return {"messages": {"m2": {"id": "m2", "session": "hub", "text": "почини вход", "created": 2,
                                    "from_owner": True}},
                "delegations": {"d7": {"id": "d7", "session": "dev-1", "for": "m2", "for_session": None, "root": "m2",
                                       "created": 3, "since": 9, "reply": "вход починен"}}}

    def test_owner_message_replies_and_delegation_result(self):
        timelines = {"hub": [owner_in("m2", 2, "почини вход"), out(4, "поручила dev-1")]}
        got, closed, dels = collect.entries(self.state(), timelines, "m2", {"session": "hub", "text": "почини вход", "ts": 2})
        self.assertEqual(dels, ["d7"])
        self.assertEqual([(e["id"], e["who"], e["session"]) for e in got],
                         [("m2", "owner", "hub"), ("m2:re", "session", "hub"), ("d7", "session", "dev-1")])
        self.assertEqual(got[-1]["text"], "вход починен")
        self.assertFalse(closed)

    def test_delegation_without_reply_yet_not_listed(self):
        st = self.state()
        st["delegations"]["d7"]["reply"] = None
        got, _, _ = collect.entries(st, {}, "m2", {"session": "hub", "text": "почини вход", "ts": 2})
        self.assertEqual([e["id"] for e in got], ["m2"])


if __name__ == "__main__":
    unittest.main()
