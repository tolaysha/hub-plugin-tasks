import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import summary

ENTRIES = [{"who": "owner", "session": "hub", "text": "надо почистить рассылки"},
           {"who": "session", "session": "backend", "text": "кампании отключены, возвраты — отдельно"}]
FULL = {"summary": "Рассылки отключены, возвраты ещё не сделаны.", "goal": "Почистить рассылки",
        "criteria": [{"text": "Кампании отключены", "done": True}, {"text": "Возвраты сделаны", "done": False}],
        "decisions": ["Возвраты — отдельно"]}


class Summary(unittest.TestCase):
    """Пересказ для карточки и прогресс для «Прогресса» — одним вызовом модели по переписке задачи."""
    def test_sends_history_and_returns_progress(self):
        seen = []

        def fake(system, text, schema):
            seen.append(text)
            return FULL
        got = summary.summarize("Почистить рассылки", ENTRIES, ask=fake)
        self.assertEqual(got, FULL)
        self.assertIn("backend: кампании отключены", seen[0])
        self.assertIn("владелец: надо почистить", seen[0])

    def test_prompt_takes_jira_acceptance_criteria(self):
        seen = []
        summary.summarize("IT-444 · Вход", ENTRIES, ask=lambda s, t, sc: seen.append(s) or FULL)
        self.assertIn("Jira", seen[0])

    def test_lists_cleaned_and_capped(self):
        # 11.10: «Прогресс» — на одну страницу: мало коротких пунктов
        crit = [{"text": "  а  ", "done": True}, {"text": "", "done": True}, {"text": "б" * 500, "done": "да"}, "в"] + [
            {"text": f"п{i}", "done": False} for i in range(20)]
        fake = lambda s, t, sc: {**FULL, "criteria": crit, "decisions": ["  р  ", ""] + [f"р{i}" for i in range(20)]}
        got = summary.summarize("Т", ENTRIES, ask=fake)
        self.assertEqual(got["criteria"][0], {"text": "а", "done": True})
        self.assertEqual(got["criteria"][1]["done"], False)   # не true — не сделан
        self.assertLessEqual(len(got["criteria"]), summary.CRITERIA)
        self.assertTrue(all(len(c["text"]) <= summary.ITEM_MAX for c in got["criteria"]))
        self.assertEqual(got["decisions"][0], "р")
        self.assertLessEqual(len(got["decisions"]), summary.DECISIONS)

    def test_missing_fields_become_empty(self):
        got = summary.summarize("Т", ENTRIES, ask=lambda s, t, sc: {"summary": "коротко"})
        self.assertEqual((got["goal"], got["criteria"], got["decisions"]), ("", [], []))

    def test_empty_entries_no_model_call(self):
        calls = []
        self.assertIsNone(summary.summarize("Т", [], ask=lambda s, t, sc: calls.append(1) or FULL))
        self.assertEqual(calls, [])

    def test_model_failure_is_none(self):
        def fake(system, text, schema):
            raise OSError("claude -p: нет ответа за 90 с")
        self.assertIsNone(summary.summarize("Т", ENTRIES, ask=fake))

    def test_due_when_never_summarized(self):
        self.assertTrue(summary.due(None, 100, now=200))

    def test_not_due_right_after_summary(self):
        self.assertFalse(summary.due(190, 195, now=200, every=600))

    def test_due_after_debounce_window(self):
        self.assertTrue(summary.due(100, 150, now=800, every=600))


if __name__ == "__main__":
    unittest.main()
