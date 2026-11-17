import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import classify

TOPICS = [{"id": "t1", "title": "Reply to a colleague"}, {"id": "t2", "title": "Fix the trial period"}]


class Classify(unittest.TestCase):
    def test_matching_topic_returned(self):
        fake = lambda system, text, schema: {"topic": "t1", "sure": True}
        self.assertEqual(classify.classify_message(TOPICS, "write to the colleague about the contract", ask=fake), "t1")

    def test_topics_listed_for_model(self):
        seen = []
        classify.classify_message(TOPICS, "text", ask=lambda system, text, schema: seen.append(system) or {})
        self.assertIn("- t2: Fix the trial period", seen[0])

    def test_context_goes_to_model_with_message(self):
        seen = []
        classify.classify_message(TOPICS, "about the button in the hub", ask=lambda s, t, sc: seen.append(t) or {},
                                  context="Session: inbox. Its assignment: a chat plugin")
        self.assertIn("a chat plugin", seen[0])
        self.assertIn("about the button in the hub", seen[0])

    def test_topic_description_listed_for_model(self):
        seen = []
        topics = [{"id": "t1", "title": "The tasks tab", "about": "a list of tasks and the conversation on each"}]
        classify.classify_message(topics, "text", ask=lambda s, t, sc: seen.append(s) or {})
        self.assertIn("- t1: The tasks tab — a list of tasks and the conversation on each", seen[0])

    def test_unsure_answer_is_none(self):
        # 09.10: with no fitting task the model took the closest — only a sure answer is taken
        self.assertIsNone(classify.classify_message(TOPICS, "go push", ask=lambda s, t, sc: {"topic": "t1", "sure": False}))
        self.assertEqual(classify.classify_message(TOPICS, "x", ask=lambda s, t, sc: {"topic": "t1", "sure": True}), "t1")

    def test_unsure_answer_accepted_when_it_matches_previous_topic(self):
        # 12.10: "pull main once more" — the model is unsure but chose the task of the previous word in this session
        unsure = lambda s, t, sc: {"topic": "t2", "sure": False}
        self.assertEqual(classify.classify_message(TOPICS, "pull main", ask=unsure, accept="t2"), "t2")
        self.assertIsNone(classify.classify_message(TOPICS, "pull main", ask=unsure, accept="t1"))
        self.assertIsNone(classify.classify_message(TOPICS, "x", ask=lambda s, t, sc: {"topic": None, "sure": False},
                                                    accept="t2"))

    def test_previous_topic_not_shown_to_model(self):
        # the hint "the previous one is about task X" dragged mistakes along (09.10): accept only confirms the model's choice
        seen = []
        classify.classify_message(TOPICS, "x", ask=lambda s, t, sc: seen.append(s + t) or {}, accept="t2")
        self.assertNotIn("t2:", seen[0].split("Tasks:")[1].split("\n- t2")[0])
        self.assertEqual(seen[0].count("t2"), 1)   # only in the list of tasks

    def test_no_match_returns_none(self):
        fake = lambda system, text, schema: {"topic": None}
        self.assertIsNone(classify.classify_message(TOPICS, "what time is it", ask=fake))

    def test_no_open_topics_skips_model_call(self):
        calls = []
        fake = lambda system, text, schema: calls.append(1) or {"topic": None}
        self.assertIsNone(classify.classify_message([], "anything", ask=fake))
        self.assertEqual(calls, [])

    def test_model_failure_is_none_not_crash(self):
        def fake(system, text, schema):
            raise ValueError("claude -p: not signed in")
        self.assertIsNone(classify.classify_message(TOPICS, "text", ask=fake))

    def test_unknown_topic_id_from_model_is_none(self):
        fake = lambda system, text, schema: {"topic": "t9", "sure": True}
        self.assertIsNone(classify.classify_message(TOPICS, "text", ask=fake))


class ShortTitle(unittest.TestCase):
    """A task title for /new-task — short, from the owner's request."""
    def test_model_title_cleaned(self):
        self.assertEqual(classify.short_title("a long request", ask=lambda s, t, sc: {"title": "  Admin sign-in. "}),
                         "Admin sign-in")

    def test_model_failure_is_none(self):
        def fake(system, text, schema):
            raise OSError("no answer")
        self.assertIsNone(classify.short_title("request", ask=fake))
        self.assertIsNone(classify.short_title("request", ask=lambda s, t, sc: {"title": ""}))

    def test_fallback_cuts_at_word(self):
        self.assertEqual(classify.fallback_title("fix the sign-in"), "fix the sign-in")
        got = classify.fallback_title("fix the admin sign-in, after the update everyone gets a 500 on login page")
        self.assertTrue(got.endswith("…"))
        self.assertLessEqual(len(got), classify.TITLE_MAX)
        self.assertFalse(got[:-1].endswith(" "))

    def test_fallback_first_line_only(self):
        self.assertEqual(classify.fallback_title("heading\ndetails"), "heading")


if __name__ == "__main__":
    unittest.main()


class CoreChanged(unittest.TestCase):
    """Review 12.10 (a check without context): the model call is an internal function of the core; the core renamed it —
    a model failure (None), not the process crashing on the same message over and over."""
    def test_missing_core_call_is_model_failure(self):
        from unittest import mock
        with mock.patch.dict(sys.modules, {"modules.watch.watch": None}):   # import gives ImportError
            self.assertIsNone(classify.classify_message(TOPICS, "text"))
            self.assertIsNone(classify.short_title("fix the sign-in"))
