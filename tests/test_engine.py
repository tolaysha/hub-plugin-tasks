"""Тик расширения на реплее состояния хаба: модель подменена, канал — списком постов."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import engine
import registry


def owner_msg(mid, created, text, session="hub"):
    return {"id": mid, "session": session, "text": text, "created": created, "from_owner": True}


def owner_in(mid, seq, text):   # время ленты — те же часы, что created сообщения (150 + номер)
    return {"seq": seq, "ts": 145 + seq, "kind": "in", "id": mid, "text": text, "channel": "web", "sender": None}


def out(seq, text):
    return {"seq": seq, "ts": 145 + seq, "kind": "out", "text": text}


def create(seq, title):
    return {"id": f"p{seq}", "by": "owner", "text": json.dumps({"kind": "topic_create", "title": title})}


def ask_summary(seq, tid="t1", force=False):   # «Прогресс» открыт или нажато «Обновить» (web/main.js)
    return {"id": f"p{seq}", "by": "owner", "text": json.dumps({"kind": "topic_summarize", "id": tid, "force": force})}


def new_task(seq, request, session="hub", at=140):   # /new-task в поле сессии (web/main.js)
    return {"id": f"p{seq}", "by": "owner",
            "text": json.dumps({"kind": "topic_create", "request": request, "session": session, "at": at})}


class Context(unittest.TestCase):
    """09.10: «по кнопке в работу, переводи меня в диалог с хабом» исполнителю Teams-плагина модель отнесла к задаче
    про вкладку хаба — по одному тексту не угадать. Модели — сессия, её поручение и задача прошлого слова в ней."""
    def setUp(self):
        self.reg = registry.load("/nonexistent/registry.json")
        registry.new_topic(self.reg, "Вкладка задач", now=1)
        registry.new_topic(self.reg, "Плагин Teams", now=1)
        self.st = {"messages": {"m1": owner_msg("m1", 10, "делай плагин Teams с входящими"),
                                "m4": owner_msg("m4", 20, "кнопки поправь", session="inbox")},
                   "delegations": {"d2": {"id": "d2", "session": "inbox", "for": None, "for_session": "hub",
                                          "created": 11, "root": "m1", "words": None, "state": "working"}},
                   "sessions": {"inbox": {"role": "executor"}}}

    def test_context_names_session_and_its_task(self):
        ctx = engine.context(self.st, self.reg, "inbox")
        self.assertIn("inbox", ctx)
        self.assertIn("делай плагин Teams с входящими", ctx)

    def test_context_takes_task_in_work_at_message_time(self):
        # 09.10: брали последнее поручение сессии — выданное позже сообщения; «плагин задачи» ушёл в Teams
        later = {"id": "d8", "session": "inbox", "for": None, "for_session": "hub", "created": 50, "root": "m4",
                 "state": "working", "since": 50}
        done = {**self.st["delegations"]["d2"], "state": "replied", "since": 12}
        st = {**self.st, "delegations": {"d2": done, "d8": later}}
        self.assertNotIn("кнопки поправь", engine.context(st, self.reg, "inbox", before=30))   # d8 выдано позже
        self.assertNotIn("Teams", engine.context(st, self.reg, "inbox", before=30))           # d2 к 30 уже сдано
        self.assertIn("Teams", engine.context(st, self.reg, "inbox", before=11.5))

    def test_context_does_not_name_previous_topic(self):
        # подсказка «прошлое сообщение — про задачу X» тянула за одной ошибкой следующие (09.10)
        self.reg["anchors"]["m4"] = {"topic": "t2", "session": "inbox", "text": "кнопки поправь", "ts": 20, "open": False}
        self.assertNotIn("Плагин Teams", engine.context(self.st, self.reg, "inbox", before=30))

    def test_context_passed_to_model(self):
        got = []
        engine.tick({**self.st, "messages": {**self.st["messages"],
                                             "m9": owner_msg("m9", 150, "по кнопке переводи в хаб", session="inbox")}},
                    {}, [], self.reg, engine.fresh_cursor(now=100), 300,
                    classify=lambda topics, text, context="", accept=None: got.append(context), summarize=lambda t, e: None)
        self.assertIn("делай плагин Teams", got[0])


class Dialog(unittest.TestCase):
    """12.10: постоянному агенту владелец пишет «подтяни мейн ещё раз» — по одному тексту задачу не понять; модели —
    прошлые реплики разговора в этой сессии, а её неуверенный выбор засчитывается, если прошлое слово владельца этой
    сессии за 30 мин уже в этой задаче."""
    def setUp(self):
        self.reg = registry.load("/nonexistent/registry.json")
        registry.new_topic(self.reg, "Команды через /", now=1)
        registry.new_topic(self.reg, "Другое", now=1)
        self.tl = {"dev": [owner_in("m1", 1, "сделай команды через слеш"), out(2, "сделал /compact и /stop"),
                           {"seq": 3, "ts": 148, "kind": "in", "id": "x3", "text": "напоминание", "sender": "hub"},
                           owner_in("m4", 4, "подтяни мейн ещё раз"), out(5, "подтянул")]}

    def test_dialog_before_message(self):
        d = engine.dialog(self.tl, "dev", "m4")
        self.assertEqual(d.splitlines(), ["владелец: сделай команды через слеш", "dev: сделал /compact и /stop",
                                          "hub: напоминание"])
        self.assertNotIn("подтянул", d)   # после сообщения — не контекст

    def test_dialog_capped(self):
        self.assertEqual(len(engine.dialog(self.tl, "dev", "m4", n=2).splitlines()), 2)
        self.assertEqual(engine.dialog(self.tl, "dev", "m9"), "")
        long = {"dev": [out(1, "а" * 5000), owner_in("m4", 2, "x")]}
        self.assertLessEqual(len(engine.dialog(long, "dev", "m4")), engine.DIALOG_CUT + 10)

    def tick(self, answer, prev_ts=150):
        seen = []
        self.reg["anchors"]["m1"] = {"topic": "t1", "session": "dev", "text": "сделай команды", "ts": prev_ts, "open": False}
        st = {"messages": {"m4": owner_msg("m4", 1000, "подтяни мейн ещё раз", session="dev")}, "delegations": {}}

        def classify(topics, text, context="", accept=None):
            seen.append((context, accept))
            return answer if answer in (accept,) or answer == "sure" else None
        engine.tick(st, self.tl, [], self.reg, engine.fresh_cursor(now=100), 1001, classify=classify,
                    summarize=lambda t, e: None)
        return seen

    def test_model_gets_dialog_and_previous_topic_to_confirm(self):
        seen = self.tick("t1")
        self.assertIn("сделал /compact", seen[0][0])
        self.assertEqual(seen[0][1], "t1")
        self.assertEqual(self.reg["anchors"]["m4"]["topic"], "t1")

    def test_previous_word_older_than_window_not_used(self):
        seen = self.tick("t1", prev_ts=1000 - engine.FOLLOW - 1)
        self.assertIsNone(seen[0][1])
        self.assertNotIn("m4", self.reg["anchors"])

    def test_previous_word_in_closed_topic_not_used(self):
        registry.close_topic(self.reg, "t1", now=500)
        registry.new_topic(self.reg, "Ещё", now=1)
        self.assertIsNone(self.tick("t1")[0][1])


class ReplyButton(unittest.TestCase):
    """12.10: «Ответить» на реплику сессии, которая уже в задаче, — ответ в эту задачу без модели. Ядро пишет, на что
    ответил владелец, в метку сообщения: «в ответ на: «начало реплики»» и «ответ на твой ответ по dN»."""
    def setUp(self):
        self.reg = registry.load("/nonexistent/registry.json")
        registry.new_topic(self.reg, "Команды", now=1)
        registry.new_topic(self.reg, "Другое", now=1)
        self.reg["anchors"]["m1"] = {"topic": "t1", "session": "dev", "text": "сделай команды", "ts": 151, "open": False}
        self.long = "Сделал /compact и /stop [ядро]; " + "подробности " * 30
        self.tl = {"dev": [owner_in("m1", 1, "сделай команды"), out(2, "читаю код"),
                           {"seq": 3, "ts": 148, "kind": "in", "id": "x3", "text": "напоминание ядра", "sender": "hub"},
                           out(3, self.long),
                           owner_in("m9", 9, "а тесты?")]}
        self.calls = []

    def run_tick(self, origin, session="dev", dels=None):
        st = {"messages": {"m9": {**owner_msg("m9", 1000, "а тесты?", session=session), "origin": origin}},
              "delegations": dels or {}}
        engine.tick(st, self.tl, [], self.reg, engine.fresh_cursor(now=100), 1001,
                    classify=lambda *a, **k: self.calls.append(1), summarize=lambda t, e: None)
        return (self.reg["anchors"].get("m9") or {}).get("topic")

    def quote(self, text):   # как core/reducer.py: _quote
        line = " ".join(text.split()).replace("]", ")").replace("[", "(")
        return line[:150] + ("…" if len(line) > 150 else "")

    def test_reply_to_session_line_after_task_word(self):
        self.assertEqual(self.run_tick(f" от владельца, в ответ на: «{self.quote(self.long)}»"), "t1")
        self.assertEqual(self.calls, [])

    def test_reply_to_intermediate_line_too(self):
        self.assertEqual(self.run_tick(" от владельца, в ответ на: «читаю код»"), "t1")

    def test_reply_to_line_outside_any_task_goes_to_model(self):
        self.reg["anchors"].pop("m1")
        self.assertIsNone(self.run_tick(" от владельца, в ответ на: «читаю код»"))
        self.assertEqual(self.calls, [1])

    def test_reply_to_closed_task_goes_to_model(self):
        registry.close_topic(self.reg, "t1", now=5)
        self.run_tick(" от владельца, в ответ на: «читаю код»")
        self.assertEqual(self.calls, [1])

    def test_reply_goes_to_latest_matching_line(self):
        # ревью 12.10: короткие реплики («читаю код», «Готово.») повторяются — берём последнюю перед ответом
        self.reg["anchors"]["m5"] = {"topic": "t2", "session": "dev", "text": "другое дело", "ts": 155, "open": False}
        self.tl["dev"][-1:-1] = [owner_in("m5", 5, "другое дело"), out(6, "читаю код")]
        self.assertEqual(self.run_tick(" от владельца, в ответ на: «читаю код»"), "t2")

    def test_reply_to_delegation_report(self):
        dels = {"d7": {"id": "d7", "session": "ex", "root": "m1", "created": 160, "reply": "готово"}}
        self.assertEqual(self.run_tick(" от владельца, ответ на твой ответ по d7", session="ex", dels=dels), "t1")
        self.assertEqual(self.calls, [])

    def test_plain_message_goes_to_model(self):
        self.run_tick(" от владельца")
        self.assertEqual(self.calls, [1])


class CardAnswer(unittest.TestCase):
    """12.10: ответ владельца на карточку вопроса qN — в задачу, над которой сессия работала, когда спросила: его
    последнее слово этой сессии перед карточкой уже в открытой задаче и было не раньше FOLLOW до неё — без модели.
    Ядро: карточка qN — строка «ask» ленты с seq N, ответ — сообщение с answers_question=qN (core/reducer.py)."""
    def setUp(self):
        self.reg = registry.load("/nonexistent/registry.json")
        registry.new_topic(self.reg, "e2e", now=1)
        registry.new_topic(self.reg, "Другое", now=1)
        self.reg["anchors"]["m1"] = {"topic": "t1", "session": "dev", "text": "запусти e2e", "ts": 146, "open": False}
        self.tl = {"dev": [owner_in("m1", 1, "запусти e2e"), out(2, "запускаю"),
                           {"seq": 3, "ts": 148, "kind": "in", "id": "x3", "text": "напоминание ядра", "sender": "hub"},
                           {"seq": 4, "ts": 149, "kind": "ask", "request_id": "r4", "tool": "AskUserQuestion"}]}
        self.calls = []

    def run_tick(self, card="q4", dels=None, **more):
        msg = {**owner_msg("m9", 1000, "Ответ владельца на вопрос q4:\n— перепроверить?: да", session="dev"),
               "answers_question": card, **more}
        engine.tick({"messages": {"m9": msg}, "delegations": dels or {}}, self.tl, [], self.reg,
                    engine.fresh_cursor(now=100), 1001,
                    classify=lambda *a, **k: self.calls.append(1), summarize=lambda t, e: None)
        return (self.reg["anchors"].get("m9") or {}).get("topic")

    def test_answer_goes_to_task_of_word_before_card(self):
        self.assertEqual(self.run_tick(), "t1")
        self.assertEqual(self.calls, [])

    def test_word_before_card_outside_tasks_goes_to_model(self):
        # последнее слово владельца перед карточкой — не из задачи: прежнее (из задачи) не берём
        self.tl["dev"][3:3] = [owner_in("m5", 3, "а что с погодой")]
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

    def run_with_letter(self, root):   # перед карточкой — письмо исполнителя поручения со словом root
        self.tl["dev"][-1:-1] = [{"seq": 4, "ts": 149, "kind": "in", "id": "m8", "text": "итог", "sender": "ex"}]
        self.tl["dev"][-1]["seq"] = 5
        dels = {"d6": {"id": "d6", "session": "ex", "root": root, "created": 147, "state": "done", "since": 148}}
        msg = {**owner_msg("m9", 1000, "ответ", session="dev"), "answers_question": "q5"}
        engine.tick({"messages": {"m9": msg}, "delegations": dels, "sessions": {"dev": {}, "ex": {}}}, self.tl, [],
                    self.reg, engine.fresh_cursor(now=100), 1001,
                    classify=lambda *a, **k: self.calls.append(1), summarize=lambda t, e: None)
        return (self.reg["anchors"].get("m9") or {}).get("topic")

    def test_letter_from_executor_of_other_word_goes_to_model(self):
        # 12.10 (живой журнал): агент спросил про другое дело после итога исполнителя по другому слову владельца
        self.assertIsNone(self.run_with_letter("m50"))
        self.assertEqual(self.calls, [1])

    def test_letter_from_executor_of_same_task_keeps_link(self):
        self.reg["anchors"]["m50"] = {"topic": "t1", "session": "hub", "text": "x", "ts": 140, "open": False}
        self.assertEqual(self.run_with_letter("m50"), "t1")
        self.assertEqual(self.calls, [])

    def test_card_of_delegation_goes_to_its_task(self):
        # вопрос по поручению: ядро ставит for_delegation — задача по root поручения, лента не нужна
        self.tl["dev"] = []
        dels = {"d7": {"id": "d7", "session": "dev", "root": "m1", "created": 150}}
        self.assertEqual(self.run_tick(dels=dels, for_delegation="d7"), "t1")
        self.assertEqual(self.calls, [])


class TopicsForModel(unittest.TestCase):
    def test_goal_or_summary_goes_as_description(self):
        reg = registry.load("/nonexistent/registry.json")
        registry.new_topic(reg, "А", now=1)
        registry.new_topic(reg, "Б", now=1)
        reg["topics"]["t1"]["progress"] = {"goal": "цель А"}
        reg["topics"]["t2"]["summary"] = "пересказ Б"
        self.assertEqual([t["about"] for t in engine.topics_for_model(reg)], ["цель А", "пересказ Б"])


class ExecutorOfTask(unittest.TestCase):
    """Слово владельца исполнителю поручения, которое выросло из сообщения задачи, — в ту же задачу без модели
    (08.10: «гибрид: структура + смысл»; 09.10: ответы владельца исполнителю не попадали в историю)."""
    def setUp(self):
        self.reg = registry.load("/nonexistent/registry.json")
        self.cursor = engine.fresh_cursor(now=100)
        self.calls = []
        registry.new_topic(self.reg, "Вкладка", now=1)
        self.reg["anchors"]["m2"] = {"topic": "t1", "session": "hub", "text": "продолжай вкладку", "ts": 2, "open": False}
        # hub task run без --for: ядро всё равно ставит root — ход хаба по слову владельца m2
        self.dels = {"d3": {"id": "d3", "session": "dev-1", "for": None, "for_session": "hub", "created": 3, "root": "m2"}}
        self.sessions = {"dev-1": {"role": "executor"}, "hub-dev": {"role": "agent"}}

    def tick(self, msgs):
        engine.tick({"messages": msgs, "delegations": self.dels, "sessions": self.sessions}, {}, [], self.reg,
                    self.cursor, 300,
                    classify=lambda topics, text, context="", accept=None: self.calls.append(text), summarize=lambda t, e: None)

    def test_owner_word_to_executor_goes_to_task_without_model(self):
        self.tick({"m9": owner_msg("m9", 150, "добавь онбординг", session="dev-1")})
        self.assertEqual(self.reg["anchors"]["m9"]["topic"], "t1")
        self.assertEqual(self.calls, [])

    def test_executor_of_closed_task_falls_back_to_model(self):
        registry.close_topic(self.reg, "t1", now=10)
        self.tick({"m9": owner_msg("m9", 150, "добавь онбординг", session="dev-1")})
        self.assertNotIn("m9", self.reg["anchors"])

    def test_session_executing_two_tasks_falls_back_to_model(self):
        registry.new_topic(self.reg, "Другая", now=5)
        self.reg["anchors"]["m4"] = {"topic": "t2", "session": "hub", "text": "x", "ts": 4, "open": False}
        self.dels["d5"] = {"id": "d5", "session": "dev-1", "for": "m4", "for_session": None, "created": 5, "root": "m4"}
        self.tick({"m9": owner_msg("m9", 150, "что-то", session="dev-1")})
        self.assertEqual(self.calls, ["что-то"])


    def test_permanent_agent_falls_back_to_model(self):
        # агент (hub-dev) исполняет поручение задачи, но делает и много другого — не прицеплять всё его подряд
        self.dels["d3"]["session"] = "hub-dev"
        self.tick({"m9": owner_msg("m9", 150, "про токен", session="hub-dev")})
        self.assertEqual(self.calls, ["про токен"])


class Tick(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.reg = registry.load("/nonexistent/registry.json")
        self.cursor = engine.fresh_cursor(now=100)
        self.contexts = []

    def classify(self, topics, text, context="", accept=None):
        self.contexts.append(context)
        self.calls.append(text)
        return topics[0]["id"] if "вход" in text else None

    def tick(self, state, timelines, posts, now=200):
        return engine.tick(state, timelines, posts, self.reg, self.cursor, now,
                           classify=self.classify,
                           summarize=lambda title, entries: {"summary": f"{len(entries)} записей", "goal": title,
                                                             "criteria": [{"text": "x", "done": True}], "decisions": []})

    def test_messages_before_first_start_not_sent_to_model(self):
        st = {"messages": {"m1": owner_msg("m1", 50, "про вход")}, "delegations": {}}
        self.tick(st, {}, [create(2, "Вход")])
        self.assertEqual(self.calls, [])
        self.assertEqual(registry.entries(self.reg, "t1"), [])

    def test_owner_message_reply_and_later_reply_land_in_topic(self):
        posts = [create(2, "Починить вход")]
        st = {"messages": {"m5": owner_msg("m5", 150, "почини вход")}, "delegations": {}}
        tl = {"hub": [owner_in("m5", 5, "почини вход")]}
        self.tick(st, tl, posts)
        self.assertEqual([e["id"] for e in registry.entries(self.reg, "t1")], ["m5"])

        tl["hub"].append(out(6, "чиню"))
        self.tick(st, tl, posts, now=210)
        self.assertEqual([e["text"] for e in registry.entries(self.reg, "t1")], ["почини вход", "чиню"])
        self.assertEqual(self.calls, ["почини вход"])   # второй тик модель не зовёт

    def test_unrelated_message_not_tagged(self):
        st = {"messages": {"m5": owner_msg("m5", 150, "который час")}, "delegations": {}}
        self.tick(st, {}, [create(2, "Вход")])
        self.assertEqual(self.calls, ["который час"])
        self.assertEqual(self.reg["anchors"], {})

    def test_no_open_topics_no_model_call(self):
        st = {"messages": {"m5": owner_msg("m5", 150, "про вход")}, "delegations": {}}
        self.tick(st, {}, [])
        self.assertEqual(self.calls, [])

    def test_commands_and_session_messages_not_classified(self):
        st = {"messages": {"m5": {**owner_msg("m5", 150, "/compact вход"), "command": True},
                           "m6": {"id": "m6", "session": "x", "text": "вход", "created": 160}},
              "delegations": {}}
        self.tick(st, {}, [create(2, "Вход")])
        self.assertEqual(self.calls, [])

    def test_closed_topic_stops_collecting(self):
        posts = [create(2, "Вход")]
        st = {"messages": {"m5": owner_msg("m5", 150, "почини вход")}, "delegations": {}}
        tl = {"hub": [owner_in("m5", 5, "почини вход")]}
        self.tick(st, tl, posts)
        posts.append({"id": "p9", "by": "owner", "text": json.dumps({"kind": "topic_close", "id": "t1"})})
        tl["hub"].append(out(6, "после закрытия"))
        self.tick(st, tl, posts, now=210)
        self.assertEqual(self.reg["topics"]["t1"]["state"], "done")
        self.assertEqual(len(registry.entries(self.reg, "t1")), 1)

    def test_close_and_reopen_through_archive(self):
        posts = [create(2, "Вход")]
        st = {"messages": {"m5": owner_msg("m5", 150, "почини вход")}, "delegations": {}}
        tl = {"hub": [owner_in("m5", 5, "почини вход")]}
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
        # 12.10: в журнале ничего нового — круг пропускаем, но не дольше IDLE (ожидание /new-task, повтор постов)
        self.assertFalse(engine.busy_round(seq=7, last_seq=7, now=130, last_full=100))
        self.assertTrue(engine.busy_round(seq=8, last_seq=7, now=101, last_full=100))
        self.assertTrue(engine.busy_round(seq=7, last_seq=7, now=100 + engine.IDLE, last_full=100))

    def test_undo_delete_reopens_topic(self):
        posts = [create(2, "Вход"),
                 {"id": "p3", "by": "owner", "text": json.dumps({"kind": "topic_close", "id": "t1"})},
                 {"id": "p4", "by": "owner", "text": json.dumps({"kind": "topic_reopen", "id": "t1"})}]
        self.tick({"messages": {}, "delegations": {}}, {}, posts)
        self.assertEqual(self.reg["topics"]["t1"]["state"], "open")

    def test_window_closed_by_next_owner_word_is_not_followed_again(self):
        posts = [create(2, "Вход")]
        st = {"messages": {"m5": owner_msg("m5", 150, "почини вход")}, "delegations": {}}
        tl = {"hub": [owner_in("m5", 5, "почини вход"), out(6, "ок"), owner_in("m7", 7, "который час")]}
        self.tick(st, tl, posts)
        self.assertFalse(self.reg["anchors"]["m5"]["open"])

    def test_no_summary_without_request(self):
        # 12.10: пересказ стоит токенов — сам по себе не считается
        posts = [create(2, "Вход")]
        st = {"messages": {"m5": owner_msg("m5", 150, "почини вход")}, "delegations": {}}
        tl = {"hub": [owner_in("m5", 5, "почини вход"), out(6, "чиню")]}
        self.tick(st, tl, posts)
        self.assertFalse(self.reg["topics"]["t1"]["summary"])
        self.assertNotIn("progress", self.reg["topics"]["t1"])

    def test_summary_over_whole_history_on_request(self):
        posts = [create(2, "Вход")]
        st = {"messages": {"m5": owner_msg("m5", 150, "почини вход")}, "delegations": {}}
        tl = {"hub": [owner_in("m5", 5, "почини вход"), out(6, "чиню")]}
        self.tick(st, tl, posts)
        posts.append(ask_summary(3))
        self.tick(st, tl, posts, now=210)
        t = self.reg["topics"]["t1"]
        self.assertEqual(t["summary"], "2 записей")
        self.assertEqual((t["progress"]["goal"], t["progress"]["criteria"], t["progress"]["at"]),
                         ("Вход", [{"text": "x", "done": True}], 210))

    def _topic(self):
        registry.new_topic(self.reg, "Вход", now=1)
        registry.add_entry(self.reg, "t1", {"id": "m1", "who": "owner", "session": "hub", "text": "x", "ts": 2})
        self.sums = []
        self.summarize = lambda title, entries: self.sums.append(len(entries)) or {
            "summary": "п", "goal": title, "criteria": [], "decisions": []}

    def _ask(self, posts, now):
        engine.tick({"messages": {}, "delegations": {}}, {}, posts, self.reg, self.cursor, now,
                    classify=self.classify, summarize=self.summarize)

    def test_anchor_of_message_gone_from_timeline_closes_when_old(self):
        # ревью 12.10: лента обрезана — окно ответа такого якоря не закрылось бы никогда (перебор каждый тик)
        registry.new_topic(self.reg, "Вход", now=1)
        for mid, ts in (("m1", 200 - engine.GONE - 1), ("m2", 190)):
            self.reg["anchors"][mid] = {"topic": "t1", "session": "hub", "text": "x", "ts": ts, "open": True}
        engine.tick({"messages": {}, "delegations": {}}, {"hub": []}, [], self.reg, self.cursor, 200,
                    classify=self.classify, summarize=lambda t, e: None)
        self.assertFalse(self.reg["anchors"]["m1"]["open"])
        self.assertTrue(self.reg["anchors"]["m2"]["open"])   # свежее — может ещё ждать в очереди сессии

    def test_summary_not_more_often_than_every(self):
        self._topic()
        posts = [ask_summary(2)]
        self._ask(posts, 200)
        registry.add_entry(self.reg, "t1", {"id": "m2", "who": "owner", "session": "hub", "text": "y", "ts": 300})
        posts.append(ask_summary(3))
        self._ask(posts, 200 + 1700)          # новое есть, но 30 минут не прошло
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
        # 11.10: прогресс прежнего вида (сделано / осталось) пересчитывается в критерии приёмки
        self._topic()
        self.reg["topics"]["t1"]["progress"] = {"goal": "в", "done": ["a"], "todo": [], "decisions": [], "at": 5}
        self.cursor["summarized_at"]["t1"] = 190
        self._ask([ask_summary(2)], 200)
        self.assertIn("criteria", self.reg["topics"]["t1"]["progress"])

    def test_failed_summary_keeps_previous(self):
        registry.new_topic(self.reg, "Вход", now=1)
        self.reg["topics"]["t1"]["summary"] = "было"
        registry.add_entry(self.reg, "t1", {"id": "m1", "who": "owner", "session": "hub", "text": "x", "ts": 2})
        engine.tick({"messages": {}, "delegations": {}}, {}, [ask_summary(2)], self.reg, self.cursor, 200,
                    classify=self.classify, summarize=lambda title, entries: None)
        self.assertEqual(self.reg["topics"]["t1"]["summary"], "было")
        self.assertNotIn("progress", self.reg["topics"]["t1"])

    def test_lost_cursor_skips_old_requests(self):
        # ревью 12.10: битый курсор (feed_seq None) — старые просьбы канала не разбираем заново, иначе задачи-дубли
        self.cursor["feed_seq"] = None
        self.tick({"messages": {}, "delegations": {}}, {}, [create(2, "Вход")])
        self.assertEqual(self.reg["topics"], {})
        self.assertEqual(self.cursor["feed_seq"], 2)
        self.tick({"messages": {}, "delegations": {}}, {}, [create(2, "Вход"), create(3, "Новая")], now=210)
        self.assertEqual([t["title"] for t in self.reg["topics"].values()], ["Новая"])

    def test_topic_create_handled_once(self):
        posts = [create(2, "Вход")]
        self.tick({"messages": {}, "delegations": {}}, {}, posts)
        self.tick({"messages": {}, "delegations": {}}, {}, posts, now=210)
        self.assertEqual(list(self.reg["topics"]), ["t1"])


class NewTaskCommand(unittest.TestCase):
    """10.10: «/new-task <запрос>» — запрос уходит сессии как обычное сообщение, а задача заводится сама: название —
    коротко по запросу (модель), первое сообщение задачи — этот запрос, без классификатора."""
    REQ = "почини вход в админку, после обновления падает 500 на логине"

    def setUp(self):
        self.reg = registry.load("/nonexistent/registry.json")
        self.cursor = engine.fresh_cursor(now=100)
        self.calls, self.named = [], []

    def tick(self, state, posts, now=200, name=None):
        def namer(text):
            self.named.append(text)
            return "Вход в админку"
        return engine.tick(state, {"hub": [owner_in("m5", 5, self.REQ)]}, posts, self.reg, self.cursor, now,
                           classify=lambda topics, text, context="", accept=None: self.calls.append(text),
                           summarize=lambda title, entries: None, name=name or namer)

    def msg(self, mid="m5", created=150, session="hub", text=None):
        return owner_msg(mid, created, text or self.REQ, session=session)

    def test_topic_named_by_model_and_request_is_its_first_message(self):
        self.tick({"messages": {"m5": self.msg()}, "delegations": {}}, [new_task(2, self.REQ)])
        self.assertEqual(self.reg["topics"]["t1"]["title"], "Вход в админку")
        self.assertEqual(self.named, [self.REQ])
        self.assertEqual(self.reg["anchors"]["m5"]["topic"], "t1")
        self.assertEqual([e["id"] for e in registry.entries(self.reg, "t1")], ["m5"])
        self.assertEqual(self.calls, [])   # классификатор не нужен — задача известна

    def test_request_found_inside_rewritten_text(self):
        # ревью 12.10: ядро может переписать слова владельца («Ответ на вопрос qN …: <текст>») — запрос внутри текста
        text = f"Ответ на вопрос q1 («что дальше?»): {self.REQ}"
        self.tick({"messages": {"m5": self.msg(text=text)}, "delegations": {}}, [new_task(2, self.REQ)])
        self.assertEqual(self.reg["anchors"]["m5"]["topic"], "t1")

    def test_message_journaled_after_request_still_linked(self):
        posts = [new_task(2, self.REQ)]
        self.tick({"messages": {}, "delegations": {}}, posts)
        self.assertEqual(self.reg["topics"], {})   # задача ждёт своё сообщение
        self.tick({"messages": {"m5": self.msg()}, "delegations": {}}, posts, now=203)
        self.assertEqual(self.reg["anchors"]["m5"]["topic"], "t1")
        self.assertEqual(list(self.reg["topics"]), ["t1"])
        self.assertEqual(self.calls, [])

    def test_browser_clock_ahead_still_links(self):
        # ревью 12.10: время просьбы ставят часы браузера; спешат на 40 с — сообщение «раньше» просьбы
        self.tick({"messages": {"m5": self.msg(created=100)}, "delegations": {}}, [new_task(2, self.REQ)])
        self.assertEqual(self.reg["anchors"]["m5"]["topic"], "t1")

    def test_only_message_to_that_session_after_request(self):
        st = {"messages": {"m1": self.msg("m1", created=20),                  # тот же текст раньше — не он
                           "m2": self.msg("m2", created=150, session="ops"),  # другой сессии — не он
                           "m5": self.msg()}, "delegations": {}}
        self.tick(st, [new_task(2, self.REQ)])
        self.assertEqual(sorted(self.reg["anchors"]), ["m5"])

    def test_message_never_came_no_task(self):
        # отправка не удалась — набранное вернулось в поле, задачи нет; повтор команды заведёт её
        posts = [new_task(2, self.REQ)]
        self.tick({"messages": {}, "delegations": {}}, posts)
        self.tick({"messages": {}, "delegations": {}}, posts, now=140 + engine.WAIT + 1)
        self.assertEqual(self.reg["topics"], {})
        self.assertEqual(self.reg["waiting"], [])

    def test_long_request_matched_by_its_head(self):
        # пост канала — до 2000 знаков: длинный запрос страница шлёт началом
        long = self.REQ + " подробности" * 300
        self.tick({"messages": {"m5": self.msg(text=long)}, "delegations": {}}, [new_task(2, long[:1500])])
        self.assertEqual(self.reg["anchors"]["m5"]["topic"], "t1")

    def test_model_failure_gives_short_title_from_request(self):
        self.tick({"messages": {"m5": self.msg()}, "delegations": {}}, [new_task(2, self.REQ)], name=lambda text: None)
        title = self.reg["topics"]["t1"]["title"]
        self.assertTrue(self.REQ.startswith(title.rstrip("…")))
        self.assertLessEqual(len(title), 60)


JIRA = {"url": "https://x.atlassian.net", "projects": ["IT", "UTF"]}


class JiraTask(unittest.TestCase):
    """12.10: /new-task при Jira — по ключу сессия берёт задачу Jira, по описанию ищет такую же; сама в Jira ничего не
    заводит — предлагает (владелец 12.10). Ключ Jira — к задаче вкладки: в название и ссылкой; из ответа сессии — только
    со строки «Задача Jira: KEY» (при поиске сессия перечисляет похожие — их ключи не задача)."""
    def setUp(self):
        self.reg = registry.load("/nonexistent/registry.json")
        self.cursor = engine.fresh_cursor(now=100)
        self.named = []

    def tick(self, request, name, tl_extra=(), now=200, sent=None):
        sent = sent or f"{name}\n\nИспользуй скилл jira-task"
        posts = [{"id": "p2", "by": "owner", "text": json.dumps(
            {"kind": "topic_create", "request": sent, "name": name, "session": "hub", "at": 140})}]
        st = {"messages": {"m5": owner_msg("m5", 150, sent)}, "delegations": {}}
        tl = {"hub": [owner_in("m5", 5, sent), *tl_extra]}

        def namer(text):
            self.named.append(text)
            return "Короткое имя"
        engine.tick(st, tl, posts, self.reg, self.cursor, now, classify=lambda *a, **k: None,
                    summarize=lambda t, e: None, name=namer, jira=JIRA)
        return self.reg["topics"].get("t1")

    def test_key_request_titled_by_key_without_model(self):
        t = self.tick("it-444", "it-444")
        self.assertEqual((t["title"], t["jira"]), ("IT-444", "IT-444"))
        self.assertEqual(self.named, [])

    def test_key_title_completed_from_session_answer(self):
        self.tick("it-444", "it-444")
        t = self.tick("it-444", "it-444", tl_extra=[out(6, "IT-444: падает оплата Stripe. AC: …"),
                                                     owner_in("m7", 7, "ок")], now=210)
        self.assertEqual(t["title"], "IT-444 · Короткое имя")
        self.assertIn("падает оплата", self.named[-1])

    def test_description_named_by_owner_words_and_key_from_answer(self):
        t = self.tick("x", "пофиксить баг со страйпом")
        self.assertEqual((t["title"], t.get("jira")), ("Короткое имя", None))
        self.assertEqual(self.named, ["пофиксить баг со страйпом"])
        t = self.tick("x", "пофиксить баг со страйпом", now=210, tl_extra=[
            out(6, "Ищу в Jira, UTF-8 тут ни при чём.\nЗадача Jira: https://x.atlassian.net/browse/IT-90\nAC: …"),
            owner_in("m7", 7, "ок")])
        self.assertEqual((t["title"], t["jira"]), ("IT-90 · Короткое имя", "IT-90"))

    def test_similar_tasks_listed_by_session_are_not_the_key(self):
        t = self.tick("x", "почини вход", now=210, tl_extra=[
            out(6, "Похожие: IT-90, IT-91 — не то. Завести новую? Пока критерии: …"), owner_in("m7", 7, "ок")])
        self.assertIsNone(t.get("jira"))

    def test_marked_bare_key_from_answer(self):
        t = self.tick("x", "почини вход", now=210, tl_extra=[out(6, "Нашёл.\n**Задача Jira:** it-91\nAC: …"),
                                                             owner_in("m7", 7, "ок")])
        self.assertEqual(t["jira"], "IT-91")

    def test_key_in_request_with_words_taken_at_once(self):
        t = self.tick("x", "IT-444 поправить кнопку оплаты")
        self.assertEqual((t["jira"], t["title"]), ("IT-444", "Короткое имя"))

    def test_encoding_name_is_not_a_task_of_utf_project(self):
        self.assertIsNone(engine.find_jira_key("пишем в UTF-8, без BOM", JIRA))
        self.assertEqual(engine.find_jira_key("UTF-8 и задача UTF-31", JIRA), "UTF-31")

    def test_unknown_project_and_lookalikes_ignored(self):
        t = self.tick("x", "почини вход", tl_extra=[out(6, "SHA-256 и ABC-12 — не задачи"), owner_in("m7", 7, "ок")])
        self.assertIsNone(t.get("jira"))

    def test_no_jira_settings_no_keys(self):
        posts = [{"id": "p2", "by": "owner", "text": json.dumps(
            {"kind": "topic_create", "request": "it-444", "name": "it-444", "session": "hub", "at": 140})}]
        st = {"messages": {"m5": owner_msg("m5", 150, "it-444")}, "delegations": {}}
        engine.tick(st, {}, posts, self.reg, self.cursor, 200, classify=lambda *a, **k: None,
                    summarize=lambda t, e: None, name=lambda t: "Имя")
        self.assertIsNone(self.reg["topics"]["t1"].get("jira"))


class JiraCreated(unittest.TestCase):
    """12.10: ключ Jira у задачи — только надёжный: в команде /new-task, в первом ответе сессии на неё (jira_link) или
    сессия прямо написала, что завела задачу («Я завёл задачу IT-123»). Угадывание по частоте убрано (владелец 12.10:
    ключ ветки, на которой гоняли тесты, встал ключом задачи про тесты)."""
    def setUp(self):
        self.reg = registry.load("/nonexistent/registry.json")
        registry.new_topic(self.reg, "Домен для рассылки", now=1)

    def add(self, mid, who, text, ts):
        registry.add_entry(self.reg, "t1", {"id": mid, "who": who, "session": "s", "text": text, "ts": ts})

    def run_rule(self, jira=JIRA):
        engine.jira_created(self.reg, jira, ["t1"])
        return self.reg["topics"]["t1"].get("jira")

    def test_session_created_task(self):
        self.add("m1:re", "session", "Связано с IT-3. Я завёл задачу [IT-123](https://x.atlassian.net/browse/IT-123)", 1)
        self.assertEqual(self.run_rule(), "IT-123")
        self.assertEqual(self.reg["topics"]["t1"]["title"], "Домен для рассылки")   # название не трогаем

    def test_other_phrasings(self):
        for text, key in (("Создал задачу IT-7 с критериями приёмки", "IT-7"), ("Завела IT-8.", "IT-8"),
                          ("Задача заведена: IT-9", None), ("создана новая задача «IT-10»", None)):
            self.reg["topics"]["t1"].pop("jira", None)
            self.reg["entries"]["t1"] = {}
            self.add("m1:re", "session", text, 1)
            self.assertEqual(self.run_rule(), key, text)

    def test_quoted_phrase_is_not_creation(self):
        # отчёт сессии цитирует чужое «Я завёл задачу IT-123» — это не заведённая ею задача
        self.add("m1:re", "session", "Ключ останется: там есть «Я завёл задачу IT-123». А тут `создал IT-9`.", 1)
        self.assertIsNone(self.run_rule())
        self.add("m2:re", "session", "Цитата «просто текст» закрыта. Я завёл задачу IT-11.", 2)
        self.assertEqual(self.run_rule(), "IT-11")

    def test_frequent_key_without_creation_is_not_task_key(self):
        for i in range(8):
            self.add(f"m{i}:re", "session", f"e2e по IT-56 прошли, прогон {i}", i)
        self.assertIsNone(self.run_rule())

    def test_owner_words_do_not_count(self):
        self.add("m1", "owner", "создал задачу IT-7, посмотри", 1)
        self.assertIsNone(self.run_rule())

    def test_old_guess_dropped(self):
        self.reg["topics"]["t1"].update(jira="IT-56", jira_guess=True)
        self.add("m1:re", "session", "IT-56 IT-56 IT-56", 1)
        self.assertIsNone(self.run_rule())
        self.assertNotIn("jira_guess", self.reg["topics"]["t1"])

    def test_new_task_key_kept(self):
        self.reg["topics"]["t1"]["jira"] = "IT-1"            # /new-task it-1 или ответ jira-task (jira_link)
        self.add("m1:re", "session", "Я завёл задачу IT-8", 1)
        self.assertEqual(self.run_rule(), "IT-1")

    def test_no_jira_settings_no_key(self):
        self.add("m1:re", "session", "Я завёл задачу IT-8", 1)
        self.assertIsNone(self.run_rule(jira=None))

    def test_tick_checks_old_topics(self):
        self.add("m1:re", "session", "Я завёл задачу IT-8", 1)
        engine.tick({"messages": {}, "delegations": {}}, {}, [], self.reg, engine.fresh_cursor(now=100), 200,
                    classify=lambda *a, **k: None, summarize=lambda t, e: None, jira=JIRA)
        self.assertEqual(self.reg["topics"]["t1"]["jira"], "IT-8")

if __name__ == "__main__":
    unittest.main()
