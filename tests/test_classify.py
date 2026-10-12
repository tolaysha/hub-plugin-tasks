import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import classify

TOPICS = [{"id": "t1", "title": "Ответить коллеге"}, {"id": "t2", "title": "Починить пробный период"}]


class Classify(unittest.TestCase):
    def test_matching_topic_returned(self):
        fake = lambda system, text, schema: {"topic": "t1", "sure": True}
        self.assertEqual(classify.classify_message(TOPICS, "напиши коллеге про контракт", ask=fake), "t1")

    def test_topics_listed_for_model(self):
        seen = []
        classify.classify_message(TOPICS, "текст", ask=lambda system, text, schema: seen.append(system) or {})
        self.assertIn("- t2: Починить пробный период", seen[0])

    def test_context_goes_to_model_with_message(self):
        seen = []
        classify.classify_message(TOPICS, "по кнопке в хаб", ask=lambda s, t, sc: seen.append(t) or {},
                                  context="Сессия: inbox. Её поручение: плагин Teams")
        self.assertIn("плагин Teams", seen[0])
        self.assertIn("по кнопке в хаб", seen[0])

    def test_topic_description_listed_for_model(self):
        seen = []
        topics = [{"id": "t1", "title": "Вкладка задач", "about": "список задач и переписка по каждой"}]
        classify.classify_message(topics, "текст", ask=lambda s, t, sc: seen.append(s) or {})
        self.assertIn("- t1: Вкладка задач — список задач и переписка по каждой", seen[0])

    def test_unsure_answer_is_none(self):
        # 09.10: без подходящей задачи модель брала ближайшую — берём только уверенный ответ
        self.assertIsNone(classify.classify_message(TOPICS, "го пуш", ask=lambda s, t, sc: {"topic": "t1", "sure": False}))
        self.assertEqual(classify.classify_message(TOPICS, "x", ask=lambda s, t, sc: {"topic": "t1", "sure": True}), "t1")

    def test_unsure_answer_accepted_when_it_matches_previous_topic(self):
        # 12.10: «подтяни мейн ещё раз» — модель сомневается, но выбрала задачу прошлого слова в этой сессии
        unsure = lambda s, t, sc: {"topic": "t2", "sure": False}
        self.assertEqual(classify.classify_message(TOPICS, "подтяни мейн", ask=unsure, accept="t2"), "t2")
        self.assertIsNone(classify.classify_message(TOPICS, "подтяни мейн", ask=unsure, accept="t1"))
        self.assertIsNone(classify.classify_message(TOPICS, "x", ask=lambda s, t, sc: {"topic": None, "sure": False},
                                                    accept="t2"))

    def test_previous_topic_not_shown_to_model(self):
        # подсказка «прошлое — про задачу X» тянула ошибки за собой (09.10): accept только подтверждает выбор модели
        seen = []
        classify.classify_message(TOPICS, "x", ask=lambda s, t, sc: seen.append(s + t) or {}, accept="t2")
        self.assertNotIn("t2:", seen[0].split("Задачи:")[1].split("\n- t2")[0])
        self.assertEqual(seen[0].count("t2"), 1)   # только в списке задач

    def test_no_match_returns_none(self):
        fake = lambda system, text, schema: {"topic": None}
        self.assertIsNone(classify.classify_message(TOPICS, "который час", ask=fake))

    def test_no_open_topics_skips_model_call(self):
        calls = []
        fake = lambda system, text, schema: calls.append(1) or {"topic": None}
        self.assertIsNone(classify.classify_message([], "что угодно", ask=fake))
        self.assertEqual(calls, [])

    def test_model_failure_is_none_not_crash(self):
        def fake(system, text, schema):
            raise ValueError("claude -p: нет входа")
        self.assertIsNone(classify.classify_message(TOPICS, "текст", ask=fake))

    def test_unknown_topic_id_from_model_is_none(self):
        fake = lambda system, text, schema: {"topic": "t9", "sure": True}
        self.assertIsNone(classify.classify_message(TOPICS, "текст", ask=fake))


class ShortTitle(unittest.TestCase):
    """Название задачи для /new-task — коротко по запросу владельца."""
    def test_model_title_cleaned(self):
        self.assertEqual(classify.short_title("длинный запрос", ask=lambda s, t, sc: {"title": "  Вход в админку. "}),
                         "Вход в админку")

    def test_model_failure_is_none(self):
        def fake(system, text, schema):
            raise OSError("нет ответа")
        self.assertIsNone(classify.short_title("запрос", ask=fake))
        self.assertIsNone(classify.short_title("запрос", ask=lambda s, t, sc: {"title": ""}))

    def test_fallback_cuts_at_word(self):
        self.assertEqual(classify.fallback_title("почини вход"), "почини вход")
        got = classify.fallback_title("почини вход в админку, после обновления падает 500 на логине у всех")
        self.assertTrue(got.endswith("…"))
        self.assertLessEqual(len(got), classify.TITLE_MAX)
        self.assertFalse(got[:-1].endswith(" "))

    def test_fallback_first_line_only(self):
        self.assertEqual(classify.fallback_title("заголовок\nподробности"), "заголовок")


if __name__ == "__main__":
    unittest.main()


class CoreChanged(unittest.TestCase):
    """Ревью 12.10 (проверка без контекста): вызов модели — внутренняя функция ядра; ядро её переименовало — сбой
    модели (None), а не падение процесса на одном и том же сообщении по кругу."""
    def test_missing_core_call_is_model_failure(self):
        from unittest import mock
        with mock.patch.dict(sys.modules, {"modules.watch.watch": None}):   # import даёт ImportError
            self.assertIsNone(classify.classify_message(TOPICS, "текст"))
            self.assertIsNone(classify.short_title("починить вход"))
