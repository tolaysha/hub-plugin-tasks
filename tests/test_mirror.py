"""Сверка канала плагина с реестром: канал хранит последние 500 постов (EXTENDING.md), поэтому вкладка видит то, что
расширение держит в канале, — вытесненное постится снова, лишнего не постим."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mirror
import registry


def reg_with(n_entries=1, text="коротко"):
    reg = {"topics": {}, "seq": 0, "anchors": {}, "entries": {}}
    tid = registry.new_topic(reg, "Тема", now=1)
    for i in range(n_entries):
        registry.add_entry(reg, tid, {"id": f"m{i}", "who": "owner", "session": "hub", "text": text, "ts": 10 + i})
    return reg, tid


def feed_of(texts, by=mirror.AUTHOR):
    return [{"id": f"p{i + 1}", "by": by, "text": t} for i, t in enumerate(texts)]


class Want(unittest.TestCase):
    def test_every_post_fits_channel_limit(self):
        reg, _ = reg_with(1, text='длинно "с кавычками"\n' * 400)
        posts = mirror.want(reg)
        self.assertGreater(len(posts), 2)
        self.assertTrue(all(len(mirror.dump(p)) <= 2000 for p in posts))

    def test_long_entry_parts_glue_back_without_loss(self):
        text = "абв\n" * 1500
        reg, _ = reg_with(1, text=text)
        parts = sorted((p for p in mirror.want(reg) if p["kind"] == "entry"), key=lambda p: p["part"])
        self.assertEqual("".join(p["text"] for p in parts), text)
        self.assertEqual({p["of"] for p in parts}, {len(parts)})

    def test_closed_topic_not_mirrored(self):
        reg, tid = reg_with(2)
        registry.close_topic(reg, tid, now=50)
        self.assertEqual(mirror.want(reg), [])

    def test_budget_keeps_topics_and_newest_entries(self):
        reg, tid = reg_with(30)
        got = mirror.want(reg, budget=11)
        self.assertEqual(sum(p["kind"] == "topic" for p in got), 1)
        self.assertEqual([p["id"] for p in got if p["kind"] == "entry"], [f"m{i}" for i in range(20, 30)])

    def test_budget_shared_between_topics(self):
        # ревью 12.10: болтливая задача вытесняла всю историю тихой — теперь задачи делят бюджет по очереди
        reg, busy = reg_with(40)
        quiet = registry.new_topic(reg, "Тихая", now=1)
        for i in range(3):
            registry.add_entry(reg, quiet, {"id": f"q{i}", "who": "owner", "session": "s", "text": "x", "ts": i})
        got = mirror.want(reg, budget=12)
        self.assertEqual(sum(p["kind"] == "entry" and p["topic"] == quiet for p in got), 3)
        self.assertEqual(sum(p["kind"] == "entry" and p["topic"] == busy for p in got), 7)

    def test_topic_post_carries_total_count(self):
        reg, tid = reg_with(30)
        topic = next(p for p in mirror.want(reg, budget=11) if p["kind"] == "topic")
        self.assertEqual(topic["count"], 30)


class Progress(unittest.TestCase):
    def test_progress_posted_in_parts_that_glue_back(self):
        reg, tid = reg_with(0)
        prog = {"goal": "цель " * 60, "done": ["сделано " * 20] * 6, "todo": ["осталось " * 20] * 6,
                "decisions": ["решено"], "at": 5}
        reg["topics"][tid]["progress"] = prog
        parts = sorted((p for p in mirror.want(reg) if p["kind"] == "progress"), key=lambda p: p["part"])
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(mirror.dump(p)) <= 2000 for p in parts))
        self.assertEqual(json.loads("".join(p["text"] for p in parts)), prog)
        self.assertEqual(len({mirror.key(p) for p in parts}), len(parts))

    def test_no_progress_no_post(self):
        reg, _ = reg_with(0)
        self.assertEqual([p["kind"] for p in mirror.want(reg)], ["topic"])

    def test_progress_not_inside_topic_post(self):
        reg, tid = reg_with(0)
        reg["topics"][tid]["progress"] = {"goal": "x", "done": [], "todo": [], "decisions": [], "at": 1}
        self.assertNotIn("progress", next(p for p in mirror.want(reg) if p["kind"] == "topic"))


class Rebuild(unittest.TestCase):
    def test_topic_and_entries_carry_rev_and_rebuild_reposts(self):
        reg, tid = reg_with(1)
        before = mirror.want(reg)
        self.assertEqual({p.get("rev") for p in before}, {0})
        reg["topics"][tid]["rev"] = 1   # история пересобрана: страница прячет записи прежней сборки
        after = mirror.want(reg)
        self.assertEqual({p.get("rev") for p in after}, {1})
        self.assertEqual(len(mirror.to_post(after, feed_of([mirror.dump(p) for p in before]), {}, now=1)), len(after))


class Naming(unittest.TestCase):
    """Из каталога плагин ставится под именем «<аккаунт>:tasks» (EXTENDING.md): канал — короткое имя (ядро пускает в имя
    канала только латиницу, цифры, дефис), подпись постов процесса — полное («плагин <аккаунт>:tasks»)."""
    def test_market_name(self):
        self.assertEqual(mirror.channel("acc:tasks"), "tasks")
        self.assertEqual(mirror.author("acc:tasks"), "плагин acc:tasks")
        self.assertEqual(mirror.state_dir("/s", "acc:tasks"), "/s/ext-acc-tasks")

    def test_local_name_keeps_old_places(self):
        self.assertEqual((mirror.channel("tasks"), mirror.author("tasks"), mirror.state_dir("/s", "tasks")),
                         ("tasks", "плагин tasks", "/s/ext-tasks"))

    def test_own_posts_recognized_by_full_author(self):
        reg, _ = reg_with(1)
        want = mirror.want(reg)
        feed = feed_of([mirror.dump(p) for p in want], by="плагин acc:tasks")
        self.assertEqual(mirror.to_post(want, feed, {}, now=100, author="плагин acc:tasks"), [])


class ToPost(unittest.TestCase):
    def test_already_in_channel_not_posted_again(self):
        reg, _ = reg_with(1)
        want = mirror.want(reg)
        self.assertEqual(mirror.to_post(want, feed_of([mirror.dump(p) for p in want]), {}, now=100), [])

    def test_evicted_post_is_posted_again(self):
        reg, _ = reg_with(2)
        want = mirror.want(reg)
        in_feed = [mirror.dump(p) for p in want][1:]   # первый вытеснен
        self.assertEqual(mirror.to_post(want, feed_of(in_feed), {}, now=100), [mirror.dump(want[0])])

    def test_closed_topic_gets_closed_post(self):
        # ревью 12.10: пост «задача открыта», опубликованный после удаления, возвращал её после перезагрузки страницы
        reg, tid = reg_with(1)
        feed = feed_of([mirror.dump(p) for p in mirror.want(reg)])
        registry.close_topic(reg, tid, now=50)
        out = [json.loads(t) for t in mirror.to_post(mirror.want(reg), feed, {}, now=100)]
        self.assertEqual([(p["kind"], p["id"], p["state"]) for p in out], [("topic", tid, "done")])
        done = feed_of([mirror.dump(p) for p in mirror.want(reg)] + [mirror.dump(out[0])])
        self.assertEqual(mirror.to_post(mirror.want(reg), done, {}, now=200), [])

    def test_changed_topic_posted_again(self):
        reg, tid = reg_with(0)
        old = [mirror.dump(p) for p in mirror.want(reg)]
        reg["topics"][tid]["summary"] = "сделано половина"
        self.assertEqual(len(mirror.to_post(mirror.want(reg), feed_of(old), {}, now=100)), 1)

    def test_others_posts_with_same_key_do_not_count(self):
        reg, _ = reg_with(0)
        want = mirror.want(reg)
        self.assertEqual(len(mirror.to_post(want, feed_of([mirror.dump(want[0])], by="owner"), {}, now=100)), 1)

    def test_just_sent_not_repeated_while_in_flight(self):
        reg, _ = reg_with(1)
        want = mirror.want(reg)
        sent = {}
        first = mirror.to_post(want, [], sent, now=100)
        self.assertEqual(len(first), len(want))
        self.assertEqual(mirror.to_post(want, [], sent, now=110), [])
        self.assertEqual(len(mirror.to_post(want, [], sent, now=100 + mirror.HOLD + 1)), len(want))

    def test_dump_is_stable(self):
        self.assertEqual(mirror.dump({"b": 1, "a": "я"}), json.dumps({"a": "я", "b": 1}, ensure_ascii=False,
                                                                     sort_keys=True))


class OwnerRequests(unittest.TestCase):
    def test_create_and_close_from_owner_only_after_cursor(self):
        posts = [{"id": "p3", "by": "owner", "text": json.dumps({"kind": "topic_create", "title": "Новая"})},
                 {"id": "p4", "by": "hub", "text": json.dumps({"kind": "topic_create", "title": "Чужая"})},
                 {"id": "p5", "by": "owner", "text": json.dumps({"kind": "topic_close", "id": "t1"})},
                 {"id": "p6", "by": "owner", "text": "не json"},
                 {"id": "p7", "by": "owner", "text": json.dumps({"kind": "topic_reopen", "id": "t1"})}]
        self.assertEqual(mirror.requests(posts, after=3),
                         [{"seq": 5, "close": "t1"}, {"seq": 7, "reopen": "t1"}])
        self.assertEqual(mirror.requests(posts, after=0)[0], {"seq": 3, "title": "Новая"})

    def test_malformed_request_skipped(self):
        # ревью 12.10: «at»: «x» ронял тик — процесс падал по кругу на том же посте
        posts = [{"id": "p3", "by": "owner", "text": json.dumps({"kind": "topic_create", "request": "r", "session": "hub",
                                                                 "at": "x"})},
                 {"id": "p4", "by": "owner", "text": json.dumps({"kind": "topic_close", "id": "t1"})}]
        self.assertEqual(mirror.requests(posts, after=0), [{"seq": 4, "close": "t1"}])

    def test_new_task_command_request(self):
        # /new-task: названия нет — есть запрос, ушедший сессии; процесс назовёт задачу сам
        posts = [{"id": "p3", "by": "owner", "text": json.dumps(
            {"kind": "topic_create", "request": " почини вход ", "session": "hub", "at": 140.5})}]
        self.assertEqual(mirror.requests(posts, after=0),
                         [{"seq": 3, "request": "почини вход", "session": "hub", "at": 140.5, "name": "почини вход"}])

    def test_new_task_with_jira_hint_names_by_owner_words(self):
        # 12.10: к запросу дописана просьба про jira-task — название задачи только по словам владельца
        posts = [{"id": "p3", "by": "owner", "text": json.dumps(
            {"kind": "topic_create", "request": "it-444\n\nИспользуй скилл jira-task…", "name": "it-444",
             "session": "hub", "at": 1})}]
        self.assertEqual(mirror.requests(posts, after=0)[0]["name"], "it-444")

    def test_summarize_request(self):
        # 12.10: пересказ — по «Прогрессу» (не чаще раза в 30 мин) или кнопкой «Обновить» (сразу)
        posts = [{"id": "p3", "by": "owner", "text": json.dumps({"kind": "topic_summarize", "id": "t1"})},
                 {"id": "p4", "by": "owner", "text": json.dumps({"kind": "topic_summarize", "id": "t2", "force": True})}]
        self.assertEqual(mirror.requests(posts, after=0),
                         [{"seq": 3, "summarize": "t1", "force": False}, {"seq": 4, "summarize": "t2", "force": True}])

    def test_topic_post_carries_jira_key(self):
        reg = registry.load("/nonexistent/registry.json")
        registry.new_topic(reg, "IT-444 · Вход", now=1)
        reg["topics"]["t1"]["jira"] = "IT-444"
        self.assertEqual(mirror.want(reg)[0]["jira"], "IT-444")


if __name__ == "__main__":
    unittest.main()
