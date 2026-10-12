"""Что попадает в историю темы вокруг сообщения владельца (владелец 08.10: «всё, что вокруг моего сообщения»): само
сообщение, ответы сессии на него — до следующего входящего сообщения в эту сессию, и итоги поручений, которые от
него пошли по цепочке (chain.py). Окно — до любого входящего, не только владельца: загруженная сессия (хаб) дальше
отвечает уже другим, и это не история задачи (проверка на живом журнале 09.10); итог поручения придёт по цепочке
(root ядра, chain.py).
Ленты сессий — Tail.reader.timelines: строки «in» (сообщения в сессию) и «out» (её речь)."""
import chain


def around(line, mid):
    """Реплики сессии после сообщения mid в её ленте: ([строки out], закрыто ли окно следующим входящим); mid в ленте
    нет (обрезана или сообщение ещё в очереди сессии) — None: решает движок по возрасту якоря."""
    got, after = [], False
    for item in line:
        if not after:
            after = item.get("kind") == "in" and item.get("id") == mid
            continue
        if item.get("kind") == "in":
            return got, True
        if item.get("kind") == "out" and not item.get("error") and (item.get("text") or "").strip():
            got.append(item)
    return got, (False if after else None)


def entries(state, timelines, mid, anchor):
    """Записи истории по якорю: ([{id, who, session, text, ts}], закрыто ли окно ответов сессии, [id поручений]).
    Живая лента обрезана (последние строки; после ротации журнала — с нуля), поэтому ответ ловится, пока окно открыто
    (тик — раз в 3 с), и дальше хранится в реестре: пересборка его не стирает (registry.rebuild)."""
    session = anchor["session"]
    line = timelines.get(session) or []
    got = [{"id": mid, "who": "owner", "session": session, "text": anchor["text"], "ts": anchor["ts"]}]
    replies, closed = around(line, mid)
    if replies:   # ответ сессии — итог хода, последняя её реплика; пока ход идёт, запись обновляется на месте
        last = replies[-1]   # (09.10: промежуточные «тесты красные…», «добавляю стили…» в истории — шум)
        got.append({"id": f"{mid}:re", "who": "session", "session": session, "text": last["text"], "ts": last["ts"]})
    dels = chain.inherit(state, mid)
    for did in dels:
        d = state["delegations"][did]
        if d.get("reply"):
            got.append({"id": did, "who": "session", "session": d["session"], "text": str(d["reply"]),
                        "ts": d.get("since") or d["created"]})
    return got, closed, dels
