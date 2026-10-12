"""Канал плагина `tasks` — единственный путь данных к странице (API плагинов 1.2). Он хранит последние 500 постов,
пост — до 2000 знаков. Поэтому канал не журнал истории, а зеркало реестра: каждый тик считаем, какие посты должны
быть видны (want), и постим только недостающее — вытесненное, изменившееся, новое (to_post). Длинная запись режется
на части (part/of у одного id) и склеивается на странице — владелец 08.10 просил сообщения не обрезать."""
import json

import registry

AUTHOR = "плагин tasks"   # так ядро подписывает посты процесса плагина (core/reducer.py: _ext_post); личная установка
LIMIT = 2000              # потолок поста в ядре
BUDGET = 350              # постов зеркала из 500 канала: остальное — посты владельца (создать, удалить)
HOLD = 120                # с, сколько не повторять отправленный пост, пока ядро его не записало


def channel(name):
    """Канал плагина — короткое имя: из каталога плагин зовётся «<аккаунт>:tasks», а ядро пускает в имя канала только
    латиницу, цифры и дефис (страница берёт так же: static/js/ext.js)."""
    return name.split(":")[-1]


def author(name):
    """Подпись постов процесса — полное имя (core/reducer.py: _ext_post — «плагин <HUB_EXTENSION>»)."""
    return f"плагин {name}"


def state_dir(state, name):
    """Своя папка в состоянии хаба: личная установка — ext-tasks, из каталога — ext-<аккаунт>-tasks."""
    import os
    return os.path.join(state, "ext-" + name.replace(":", "-"))


def dump(payload):
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def key(payload):
    if payload.get("kind") == "topic":
        return f"t:{payload.get('id')}"
    if payload.get("kind") == "progress":
        return f"p:{payload.get('topic')}:{payload.get('part')}"
    return f"e:{payload.get('topic')}:{payload.get('id')}:{payload.get('part')}"


def _parts(base, text):
    """Части текста записи так, чтобы каждый пост с обвязкой влез в LIMIT (экранирование в JSON удлиняет текст)."""
    parts, rest = [], text
    while rest or not parts:
        n = len(rest)
        while n > 1 and len(dump({**base, "part": 999, "of": 999, "text": rest[:n]})) > LIMIT:
            n = n * 3 // 4
        parts.append(rest[:n])
        rest = rest[n:]
    return [{**base, "part": i, "of": len(parts), "text": p} for i, p in enumerate(parts)]


def want(reg, budget=BUDGET):
    """Посты, которые должны быть в канале: открытые темы и их записи, свежие — первыми в пределах бюджета. Бюджет
    задачи делят по очереди, по записи за круг (ревью 12.10: болтливая вытесняла всю историю тихой); count в посте
    темы — сколько записей всего: страница покажет «последние K из N»."""
    topics = registry.open_topics(reg)
    # rev — номер сборки истории задачи: пересобрали (registry.rebuild) — страница прячет записи прежней сборки
    out = [{"kind": "topic", "rev": t.get("rev", 0), "count": len(registry.entries(reg, t["id"])),
            **{k: t.get(k) for k in ("id", "title", "state", "created", "summary", "jira", "example")}} for t in topics]
    for t in topics:   # прогресс — отдельными постами: с пятью полями он не влезает в пост темы
        if t.get("progress"):
            out += _parts({"kind": "progress", "topic": t["id"]}, json.dumps(t["progress"], ensure_ascii=False))
    queues = {t["id"]: registry.entries(reg, t["id"])[::-1] for t in topics}   # свежие — первыми
    rev = {t["id"]: t.get("rev", 0) for t in topics}
    picked, room = [], budget - len(out)
    while queues:
        for tid in list(queues):
            if not queues[tid]:
                del queues[tid]
                continue
            e = queues[tid].pop(0)
            posts = _parts({"kind": "entry", "topic": tid, "rev": rev[tid],
                            **{k: e[k] for k in ("id", "who", "session", "ts")}}, e["text"])
            if len(posts) > room:
                del queues[tid]   # следующая запись этой задачи не влезла — её старые тоже не берём
                continue
            room -= len(posts)
            picked.append(((e["ts"], e["id"]), posts))
    # постим старое раньше нового: канал вытесняет с начала, свежее уходит последним
    return out + [p for _, posts in sorted(picked, key=lambda x: x[0]) for p in posts]


def to_post(wanted, posts, sent, now, author=AUTHOR):
    """Тексты постов, которых в канале нет в нужном виде. sent — {ключ: (текст, когда)}: что уже отправлено и в пути."""
    have = {}
    for p in posts:
        if p.get("by") != author:
            continue
        try:
            have[key(json.loads(p["text"]))] = p["text"]
        except (ValueError, TypeError, AttributeError):
            continue
    # задача, которой больше нет среди открытых, а в канале её пост «открыта», — пост «закрыта» (ревью 12.10: пост
    # процесса, вышедший после удаления, возвращал её после перезагрузки страницы)
    keys = {key(w) for w in wanted}
    for k, text in have.items():
        old = json.loads(text)
        if k.startswith("t:") and k not in keys and old.get("state") == "open":
            wanted = [*wanted, {**old, "state": "done"}]
    out = []
    for w in wanted:
        text, k = dump(w), key(w)
        if have.get(k) == text:
            continue
        was = sent.get(k)
        if was and was[0] == text and now - was[1] < HOLD:
            continue
        sent[k] = (text, now)
        out.append(text)
    return out


def requests(posts, after):
    """Слово владельца со страницы после поста номер after: создать задачу (по названию или командой /new-task —
    по запросу), удалить (закрыть), вернуть удалённую, пересказать (открыт «Прогресс» или нажато «Обновить»)."""
    out = []
    for p in posts:
        seq = int(p["id"][1:])
        if seq <= after or p.get("by") != "owner":
            continue
        try:
            data = json.loads(p["text"])
        except ValueError:
            continue
        if not isinstance(data, dict):
            continue
        if data.get("kind") == "topic_create" and str(data.get("request") or "").strip() and data.get("session"):
            # /new-task: запрос ушёл сессии, название даст процесс (engine.py: waiting)
            try:
                at = float(data.get("at") or 0)
            except (TypeError, ValueError):   # кривой пост не должен ронять тик (ревью 12.10)
                continue
            out.append({"seq": seq, "request": str(data["request"]).strip(), "session": str(data["session"]),
                        "at": at,
                        # название — по словам владельца, без дописанной страницей просьбы про jira-task (12.10)
                        "name": str(data.get("name") or data["request"]).strip()})
        elif data.get("kind") == "topic_create" and str(data.get("title") or "").strip():
            out.append({"seq": seq, "title": str(data["title"]).strip()[:200]})
        elif data.get("kind") == "topic_summarize" and data.get("id"):
            out.append({"seq": seq, "summarize": str(data["id"]), "force": data.get("force") is True})
        elif data.get("kind") in ("topic_close", "topic_reopen") and data.get("id"):
            out.append({"seq": seq, data["kind"][6:]: str(data["id"])})
    return out
