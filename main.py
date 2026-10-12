"""Процесс личного расширения «Задачи» (hub-up: HUB_STATE_DIR, PYTHONPATH на код хаба, HUB_EXTENSION). Читает журнал
хаба через core.tail.Tail — готовое состояние и ленты сессий, как у страницы, — и раз в TICK с делает тик (engine.py):
задачи, привязка сообщений владельца, история, пересказ. Потом сверяет канал плагина с реестром (mirror.py) и постит
недостающее от имени плагина. Реестр и курсор — в <состояние>/ext-tasks, не в папке расширения: файл, который
меняется каждый тик, перезагружал бы открытые вкладки (страница следит за mtime файлов расширения)."""
import json
import os
import sys
import time

sys.dont_write_bytecode = True   # без __pycache__ в папке расширения — та же причина

from core import spool  # noqa: E402
from core.daemon import default_state_dir  # noqa: E402
from core.tail import Tail  # noqa: E402

import classify  # noqa: E402
import engine  # noqa: E402
import example  # noqa: E402
import mirror  # noqa: E402
import registry  # noqa: E402
import summary  # noqa: E402

NAME = os.environ.get("HUB_EXTENSION", "tasks")
STATE = default_state_dir()
OWN = mirror.state_dir(STATE, NAME)   # из каталога — «<аккаунт>:tasks»: своя папка, канал — короткое имя
REG_PATH = os.path.join(OWN, "registry.json")
CURSOR_PATH = os.path.join(OWN, "cursor.json")
ARCHIVE_PATH = os.path.join(OWN, "archive.json")   # записи и якоря удалённых задач (registry.archive_closed)
TICK = 3


def load_cursor(now):
    try:
        with open(CURSOR_PATH, encoding="utf-8") as f:
            cursor = json.load(f)
        if not isinstance(cursor, dict) or "last_message" not in cursor:
            raise ValueError("не курсор")
        return cursor
    except FileNotFoundError:
        return engine.fresh_cursor(now)
    except (OSError, ValueError):   # битый: старые просьбы канала не разбирать заново (engine.collect_requests)
        print("tasks: курсор не читается — начинаю с текущего места канала", file=sys.stderr, flush=True)
        return {**engine.fresh_cursor(now), "feed_seq": None}


def take_rewind(cursor):
    """Разобрать заново слова владельца с момента since (после правки привязки, 12.10): файл rewind.json рядом с реестром
    кладут руками, пока процесс жив, — курсор он держит в памяти, поэтому правка cursor.json снаружи затёрлась бы.
    Уже привязанные сообщения не трогаются (engine.tick пропускает якоря)."""
    path = os.path.join(OWN, "rewind.json")
    try:
        with open(path, encoding="utf-8") as f:
            since = float(json.load(f)["since"])
        os.remove(path)
    except (OSError, ValueError, KeyError, TypeError):
        return False
    cursor["last_message"] = min(cursor["last_message"], since)
    print(f"tasks: разбираю заново слова владельца с {since}", file=sys.stderr, flush=True)
    return True


def jira_settings():
    """Jira хаба (hub.json → jira: url, projects) — те же, что подсвечивают ключи на странице (modules/jira/links.py)."""
    try:
        from core.catalog import config_path
        from modules.jira import links
        with open(config_path(STATE), encoding="utf-8") as f:
            return links.settings(json.load(f))
    except (OSError, ValueError, ImportError):
        return None


def save_json(path, data):
    registry.save(path, data)


def post(text):
    # пост процесса плагина — от его имени, как `hub post` из процесса плагина (bin/hub: put)
    spool.put(os.path.join(STATE, "spool"), "ext.post", None,
              {"plugin": mirror.channel(NAME), "text": text, "channel": "plugin", "owner": False, "sender": NAME},
              src=f"ext:{NAME}")


def main():
    reg, cursor, sent = registry.load(REG_PATH), load_cursor(time.time()), {}
    if example.seed(reg, time.time()):   # задача-пример — один раз на хаб
        registry.save(REG_PATH, reg)
    tail = Tail(os.path.join(STATE, "journal.jsonl"))
    jira = jira_settings()   # настройки хаба меняются редко — с перезапуском расширения
    print(f"tasks: старт, задач {len(reg['topics'])}, состояние {OWN}", file=sys.stderr, flush=True)
    archive = registry.load_archive(ARCHIVE_PATH)
    if registry.archive_closed(reg, archive):   # удалённые до архива (12.10) — разом при старте
        registry.save(ARCHIVE_PATH, archive)
        registry.save(REG_PATH, reg)
    last_seq, last_full = None, 0.0
    while True:
        tail.poll()   # читает только этот поток — без блокировки
        state, timelines = tail.reader.state, tail.reader.timelines
        now = time.time()
        rewound = take_rewind(cursor)
        if not rewound and not engine.busy_round(state.get("seq"), last_seq, now, last_full):
            time.sleep(TICK)   # в журнале ничего нового — без копий реестра (владелец 12.10)
            continue
        last_seq, last_full = state.get("seq"), now
        posts = list((state.get("feeds") or {}).get(mirror.channel(NAME)) or [])
        reg_before = json.dumps(reg, sort_keys=True)
        shelf = {k: (len(v["entries"]), len(v["anchors"])) for k, v in archive.items()}
        engine.tick(state, timelines, posts, reg, cursor, now,
                    classify=classify.classify_message, summarize=summary.summarize, name=classify.short_title,
                    jira=jira, archive=archive)
        for text in mirror.to_post(mirror.want(reg), posts, sent, now, author=mirror.author(NAME)):
            post(text)
        if {k: (len(v["entries"]), len(v["anchors"])) for k, v in archive.items()} != shelf:
            save_json(ARCHIVE_PATH, archive)   # архив — до реестра: сбой между ними не теряет записи
        if json.dumps(reg, sort_keys=True) != reg_before:
            save_json(REG_PATH, reg)
        save_json(CURSOR_PATH, cursor)
        time.sleep(TICK)

if __name__ == "__main__":
    main()
