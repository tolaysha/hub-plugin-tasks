import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import registry


class Registry(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "registry.json")

    def test_new_topic_is_open_and_listed(self):
        reg = registry.load(self.path)
        tid = registry.new_topic(reg, "Ответить коллеге", now=100)
        self.assertEqual(tid, "t1")
        self.assertEqual(reg["topics"][tid]["title"], "Ответить коллеге")
        self.assertEqual(reg["topics"][tid]["state"], "open")
        self.assertEqual([t["id"] for t in registry.open_topics(reg)], ["t1"])

    def test_closed_topic_not_in_open_list(self):
        reg = registry.load(self.path)
        tid = registry.new_topic(reg, "Починить пробный период", now=100)
        self.assertTrue(registry.close_topic(reg, tid, now=200))
        self.assertEqual(registry.open_topics(reg), [])
        self.assertEqual(reg["topics"][tid]["closed_at"], 200)

    def test_reopen_returns_deleted_topic_with_history(self):
        reg = registry.load(self.path)
        tid = registry.new_topic(reg, "Тема", now=1)
        registry.add_entry(reg, tid, {"id": "m1", "who": "owner", "session": "hub", "text": "x", "ts": 2})
        registry.close_topic(reg, tid, now=3)
        self.assertTrue(registry.reopen_topic(reg, tid))
        self.assertEqual((reg["topics"][tid]["state"], reg["topics"][tid]["closed_at"]), ("open", None))
        self.assertEqual(len(registry.entries(reg, tid)), 1)
        self.assertFalse(registry.reopen_topic(reg, tid))
        self.assertFalse(registry.reopen_topic(reg, "t9"))

    def test_rebuild_keeps_replies_of_kept_anchors_and_drops_removed(self):
        # пересборка не стирает ответы, которых из лент уже не достать (09.10: «как будто монолог»)
        reg = registry.load(self.path)
        tid = registry.new_topic(reg, "Тема", now=1)
        reg["anchors"]["m1"] = {"topic": tid, "session": "hub", "text": "a", "ts": 1, "open": False}
        for eid in ("m1", "m1:re", "m2", "m2:re", "d5"):
            registry.add_entry(reg, tid, {"id": eid, "who": "owner", "session": "hub", "text": eid, "ts": 2})
        registry.rebuild(reg, tid, keep_dels={"d5"})
        self.assertEqual([e["id"] for e in registry.entries(reg, tid)], ["d5", "m1", "m1:re"])
        self.assertTrue(reg["anchors"]["m1"]["open"])

    def test_close_unknown_topic_is_false_not_error(self):
        reg = registry.load(self.path)
        self.assertFalse(registry.close_topic(reg, "t9", now=100))

    def test_save_then_load_roundtrip(self):
        reg = registry.load(self.path)
        tid = registry.new_topic(reg, "Документация", now=100)
        registry.add_entry(reg, tid, {"id": "m5", "who": "owner", "session": "hub", "text": "начнём", "ts": 101})
        registry.save(self.path, reg)
        back = registry.load(self.path)
        self.assertEqual(back["topics"][tid]["title"], "Документация")
        self.assertEqual([e["id"] for e in registry.entries(back, tid)], ["m5"])

    def test_load_missing_file_is_empty_registry(self):
        reg = registry.load(os.path.join(self.tmp, "no-such-file.json"))
        self.assertEqual(reg, {"topics": {}, "seq": 0, "anchors": {}, "entries": {}})

    def test_load_old_registry_without_history_fields(self):
        # реестр, записанный до истории тем (только topics/seq), читается без ошибки
        with open(self.path, "w", encoding="utf-8") as f:
            f.write('{"topics": {}, "seq": 2}')
        reg = registry.load(self.path)
        self.assertEqual((reg["seq"], reg["anchors"], reg["entries"]), (2, {}, {}))

    def test_add_entry_dedupes_by_id_and_reports_change(self):
        reg = registry.load(self.path)
        tid = registry.new_topic(reg, "Тема", now=1)
        e = {"id": "s:7", "who": "session", "session": "dev", "text": "готово", "ts": 5}
        self.assertTrue(registry.add_entry(reg, tid, e))
        self.assertFalse(registry.add_entry(reg, tid, dict(e)))
        self.assertTrue(registry.add_entry(reg, tid, {**e, "text": "готово, тесты зелёные"}))
        self.assertEqual(len(registry.entries(reg, tid)), 1)

    def test_entries_in_time_order(self):
        reg = registry.load(self.path)
        tid = registry.new_topic(reg, "Тема", now=1)
        registry.add_entry(reg, tid, {"id": "b", "who": "session", "session": "x", "text": "2", "ts": 20})
        registry.add_entry(reg, tid, {"id": "a", "who": "owner", "session": "x", "text": "1", "ts": 10})
        self.assertEqual([e["id"] for e in registry.entries(reg, tid)], ["a", "b"])



class Broken(unittest.TestCase):
    """Ревью 12.10: битый реестр молча становился пустым и сразу сохранялся поверх — вся история терялась."""
    def test_broken_registry_set_aside_not_overwritten(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "registry.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write('{"topics": {"t1": ')
            reg = registry.load(path)
            self.assertEqual(reg["topics"], {})
            aside = [n for n in os.listdir(d) if n.startswith("registry.json.broken-")]
            self.assertEqual(len(aside), 1)
            with open(os.path.join(d, aside[0]), encoding="utf-8") as f:
                self.assertEqual(f.read(), '{"topics": {"t1": ')

    def test_missing_registry_is_fresh(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(registry.load(os.path.join(d, "registry.json"))["topics"], {})
            self.assertEqual(os.listdir(d), [])



class Archive(unittest.TestCase):
    """12.10: записи и якоря удалённых задач — в архив (отдельный файл): рабочий реестр не растёт, его сравнение каждый
    тик остаётся дешёвым; «Вернуть» поднимает задачу из архива."""
    def setUp(self):
        self.reg = registry.load("/nonexistent/registry.json")
        for title in ("А", "Б"):
            tid = registry.new_topic(self.reg, title, now=1)
            registry.add_entry(self.reg, tid, {"id": f"m{tid}", "who": "owner", "session": "s", "text": "x", "ts": 2})
            self.reg["anchors"][f"m{tid}"] = {"topic": tid, "session": "s", "text": "x", "ts": 2, "open": False}
        self.archive = {}

    def test_closed_topic_data_moves_to_archive(self):
        registry.close_topic(self.reg, "t1", now=5)
        self.assertTrue(registry.archive_closed(self.reg, self.archive))
        self.assertNotIn("t1", self.reg["entries"])
        self.assertEqual(sorted(self.reg["anchors"]), ["mt2"])
        self.assertIn("t1", self.reg["topics"])   # сама задача (название, когда удалена) остаётся
        self.assertEqual(list(self.archive["t1"]["entries"]), ["mt1"])
        self.assertEqual(list(self.archive["t1"]["anchors"]), ["mt1"])
        self.assertFalse(registry.archive_closed(self.reg, self.archive))   # нечего переносить

    def test_reopen_restores_from_archive(self):
        registry.close_topic(self.reg, "t1", now=5)
        registry.archive_closed(self.reg, self.archive)
        registry.reopen_topic(self.reg, "t1")
        self.assertTrue(registry.unarchive(self.reg, self.archive, "t1"))
        self.assertEqual([e["id"] for e in registry.entries(self.reg, "t1")], ["mt1"])
        self.assertIn("mt1", self.reg["anchors"])
        self.assertEqual(self.archive, {})


if __name__ == "__main__":
    unittest.main()
