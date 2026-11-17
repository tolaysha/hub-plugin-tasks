"""12.10: the example task — once for everyone, to see at once what "History" and "Progress" look like; deleted — does
not come back. No model is spent on it and other messages do not stick to it."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import engine  # noqa: E402
import example  # noqa: E402
import registry  # noqa: E402


class Example(unittest.TestCase):
    def setUp(self):
        self.reg = registry.load("/nonexistent/registry.json")
        self.cursor = engine.fresh_cursor(now=100)
        self.sums, self.calls = [], []
        example.seed(self.reg, now=150)

    def tick(self, posts=(), now=200, messages=None):
        engine.tick({"messages": messages or {}, "delegations": {}}, {}, list(posts), self.reg, self.cursor, now,
                    classify=lambda topics, text, **k: self.calls.append([t["id"] for t in topics]),
                    summarize=lambda t, e: self.sums.append(t))

    def test_seeded_once_with_history_and_progress(self):
        self.assertIsNone(example.seed(self.reg, now=300))
        ex = [t for t in self.reg["topics"].values() if t.get("example")]
        self.assertEqual(len(ex), 1)
        t = ex[0]
        self.assertEqual(t["state"], "open")
        self.assertTrue(t["summary"])
        self.assertTrue(t["progress"]["criteria"])
        got = registry.entries(self.reg, t["id"])
        self.assertGreaterEqual(len(got), 3)
        self.assertEqual({e["who"] for e in got}, {"owner", "session"})
        self.assertLessEqual(max(e["ts"] for e in got), t["progress"]["at"])

    def test_talk_spans_specialists_and_is_almost_done(self):
        # 12.10: shows that the task gets the conversation not only with the Hub but with specialists too
        tid = next(iter(self.reg["topics"]))
        got = registry.entries(self.reg, tid)
        self.assertGreaterEqual(len(got), 8)
        self.assertGreaterEqual(len({e["session"] for e in got} - {"hub"}), 3)
        self.assertEqual(got[-1]["session"], "hub")   # "Go to session" leads to a live session
        crit = self.reg["topics"][tid]["progress"]["criteria"]
        self.assertEqual(sum(not c["done"] for c in crit), 1)   # almost solved

    def test_old_example_upgraded_in_place(self):
        reg = registry.load("/nonexistent/registry.json")
        tid = registry.new_topic(reg, "Example: old", now=1)
        reg["topics"][tid]["example"] = True
        registry.add_entry(reg, tid, {"id": "x1", "who": "owner", "session": "hub", "text": "old", "ts": 2})
        reg["example_seeded"] = True                    # the first version of the example
        example.seed(reg, now=500)
        self.assertEqual(list(reg["topics"]), [tid])
        self.assertEqual(len(registry.entries(reg, tid)), len(example.TALK))
        self.assertEqual(reg["topics"][tid]["title"], example.TITLE)
        self.assertEqual(reg["example_seeded"], example.VERSION)

    def test_deleted_old_example_not_recreated_on_upgrade(self):
        reg = registry.load("/nonexistent/registry.json")
        tid = registry.new_topic(reg, "Example: old", now=1)
        reg["topics"][tid]["example"] = True
        registry.close_topic(reg, tid, now=2)
        reg["example_seeded"] = True
        example.seed(reg, now=500)
        self.assertEqual(reg["topics"][tid]["state"], "done")
        self.assertEqual(len(reg["topics"]), 1)

    def test_deleted_example_does_not_come_back(self):
        tid = next(iter(self.reg["topics"]))
        registry.close_topic(self.reg, tid, now=250)
        example.seed(self.reg, now=300)
        self.assertEqual(len(self.reg["topics"]), 1)

    def test_existing_hub_gets_example_too(self):
        reg = registry.load("/nonexistent/registry.json")
        registry.new_topic(reg, "Own one", now=1)
        example.seed(reg, now=2)
        self.assertEqual(len(reg["topics"]), 2)
        self.assertEqual(sum(1 for t in reg["topics"].values() if t.get("example")), 1)

    def test_no_model_for_example(self):
        tid = next(iter(self.reg["topics"]))
        posts = [{"id": "p2", "by": "owner", "text": '{"kind": "topic_summarize", "id": "%s", "force": true}' % tid}]
        msg = {"m5": {"id": "m5", "session": "hub", "text": "fix the sign-in", "created": 250, "from_owner": True}}
        self.tick(posts, now=300, messages=msg)
        self.assertEqual(self.sums, [])
        self.assertEqual(self.calls, [])   # no open tasks besides the example — the model is not called
        self.assertEqual(engine.topics_for_model(self.reg), [])


if __name__ == "__main__":
    unittest.main()
