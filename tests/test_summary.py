import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import summary

ENTRIES = [{"who": "owner", "session": "hub", "text": "need to clean up the mailings"},
           {"who": "session", "session": "backend", "text": "campaigns are off, refunds separately"}]
FULL = {"summary": "Mailings are off, refunds are not done yet.", "goal": "Clean up the mailings",
        "criteria": [{"text": "Campaigns are off", "done": True}, {"text": "Refunds are done", "done": False}],
        "decisions": ["Refunds separately"]}


class Summary(unittest.TestCase):
    """A summary for the card and progress for "Progress" — in one model call over the task's conversation."""
    def test_sends_history_and_returns_progress(self):
        seen = []

        def fake(system, text, schema):
            seen.append(text)
            return FULL
        got = summary.summarize("Clean up the mailings", ENTRIES, ask=fake)
        self.assertEqual(got, FULL)
        self.assertIn("backend: campaigns are off", seen[0])
        self.assertIn("owner: need to clean up", seen[0])

    def test_prompt_takes_jira_acceptance_criteria(self):
        seen = []
        summary.summarize("PROJ-123 · Sign-in", ENTRIES, ask=lambda s, t, sc: seen.append(s) or FULL)
        self.assertIn("Jira", seen[0])

    def test_lists_cleaned_and_capped(self):
        # 11.10: "Progress" fits one page: few short items
        crit = [{"text": "  a  ", "done": True}, {"text": "", "done": True}, {"text": "b" * 500, "done": "yes"}, "c"] + [
            {"text": f"p{i}", "done": False} for i in range(20)]
        fake = lambda s, t, sc: {**FULL, "criteria": crit, "decisions": ["  d  ", ""] + [f"d{i}" for i in range(20)]}
        got = summary.summarize("T", ENTRIES, ask=fake)
        self.assertEqual(got["criteria"][0], {"text": "a", "done": True})
        self.assertEqual(got["criteria"][1]["done"], False)   # not true — not done
        self.assertLessEqual(len(got["criteria"]), summary.CRITERIA)
        self.assertTrue(all(len(c["text"]) <= summary.ITEM_MAX for c in got["criteria"]))
        self.assertEqual(got["decisions"][0], "d")
        self.assertLessEqual(len(got["decisions"]), summary.DECISIONS)

    def test_missing_fields_become_empty(self):
        got = summary.summarize("T", ENTRIES, ask=lambda s, t, sc: {"summary": "in short"})
        self.assertEqual((got["goal"], got["criteria"], got["decisions"]), ("", [], []))

    def test_empty_entries_no_model_call(self):
        calls = []
        self.assertIsNone(summary.summarize("T", [], ask=lambda s, t, sc: calls.append(1) or FULL))
        self.assertEqual(calls, [])

    def test_model_failure_is_none(self):
        def fake(system, text, schema):
            raise OSError("claude -p: no answer in 90 s")
        self.assertIsNone(summary.summarize("T", ENTRIES, ask=fake))

    def test_due_when_never_summarized(self):
        self.assertTrue(summary.due(None, 100, now=200))

    def test_not_due_right_after_summary(self):
        self.assertFalse(summary.due(190, 195, now=200, every=600))

    def test_due_after_debounce_window(self):
        self.assertTrue(summary.due(100, 150, now=800, every=600))


if __name__ == "__main__":
    unittest.main()
