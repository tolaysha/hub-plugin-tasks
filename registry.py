"""Реестр задач владельца и их история: темы, якоря (сообщение владельца → тема) и записи истории. Канал плагина
хранит только последние 500 постов, поэтому полная история — здесь, а в канал идёт её видимая часть (mirror.py).
Файл — вне папки расширения: иначе вкладка мигает от авто-reload по mtime файлов расширения."""
import json
import os
import sys
import time


def load(path):
    """Реестр с диска; нет файла — пустой. Битый — откладываем рядом (registry.json.broken-<время>) и начинаем пустой:
    иначе первое же сохранение затёрло бы всю историю (ревью 12.10)."""
    try:
        with open(path, encoding="utf-8") as f:
            reg = json.load(f)
        if not isinstance(reg, dict):
            raise ValueError("не объект")
    except FileNotFoundError:
        reg = {}
    except (OSError, ValueError) as e:
        aside = f"{path}.broken-{int(time.time())}"
        try:
            os.replace(path, aside)
        except OSError:
            aside = "не отложен"
        print(f"tasks: реестр не читается ({e}) — отложен: {aside}; начинаю пустой", file=sys.stderr, flush=True)
        reg = {}
    for key, empty in (("topics", {}), ("seq", 0), ("anchors", {}), ("entries", {})):
        reg.setdefault(key, empty)
    return reg


def load_archive(path):
    """Архив удалённых задач: {id задачи: {entries, anchors}}; нет файла — пустой; битый — отложен, как реестр."""
    data = load(path)
    return {k: v for k, v in data.items() if isinstance(v, dict) and "entries" in v}


def save(path, data):
    """Атомарно и на диск (fsync): после сбоя питания файл — прежний или новый, не пустой."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def new_topic(reg, title, now):
    reg["seq"] = reg.get("seq", 0) + 1
    tid = f"t{reg['seq']}"
    reg["topics"][tid] = {"id": tid, "title": title, "state": "open", "created": now,
                          "closed_at": None, "summary": ""}
    return tid


def close_topic(reg, tid, now):
    t = reg["topics"].get(tid)
    if not t or t["state"] != "open":
        return False
    t["state"], t["closed_at"] = "done", now
    return True


def reopen_topic(reg, tid):
    """«Вернуть» сразу после удаления на странице: задача с прежней историей снова открыта."""
    t = reg["topics"].get(tid)
    if not t or t["state"] != "done":
        return False
    t["state"], t["closed_at"] = "open", None
    return True


def archive_closed(reg, archive):
    """Записи и якоря удалённых задач — в архив (владелец 12.10): рабочий реестр не растёт, а его сравнение каждый тик
    (main.py) остаётся дешёвым. Сама задача остаётся в реестре — название, когда удалена. True — что-то перенесли."""
    moved = False
    for tid, t in reg["topics"].items():
        if t["state"] == "open":
            continue
        anchors = {m: a for m, a in reg["anchors"].items() if a["topic"] == tid}
        entries = reg["entries"].pop(tid, None)
        if not anchors and not entries:
            continue
        box = archive.setdefault(tid, {"entries": {}, "anchors": {}})
        box["entries"].update(entries or {})
        box["anchors"].update(anchors)
        for m in anchors:
            del reg["anchors"][m]
        moved = True
    return moved


def unarchive(reg, archive, tid):
    """«Вернуть» удалённую: её записи и якоря — из архива обратно в реестр. True — было что вернуть."""
    box = archive.pop(tid, None)
    if not box:
        return False
    reg["entries"].setdefault(tid, {}).update(box["entries"])
    reg["anchors"].update(box["anchors"])
    return True


def open_topics(reg):
    return sorted((t for t in reg["topics"].values() if t["state"] == "open"), key=lambda t: t["created"])


def add_entry(reg, tid, entry):
    """Запись истории темы по id; True — новая или изменилась (ответ сессии дописался)."""
    box = reg["entries"].setdefault(tid, {})
    if box.get(entry["id"]) == entry:
        return False
    box[entry["id"]] = dict(entry)
    return True


def entries(reg, tid):
    return sorted((reg["entries"].get(tid) or {}).values(), key=lambda e: (e["ts"], e["id"]))


def rebuild(reg, tid, keep_dels=()):
    """Пересобрать историю задачи после правки якорей: записи снятых якорей уходят, записи оставшихся (их сообщение
    и ответ «mid:re») и поручений keep_dels остаются — ответы из обрезанных лент заново не достать (09.10: стёр всё —
    вышел «монолог»). Якоря снова открыты, номер сборки растёт — страница прячет записи прежней сборки."""
    mine = {m for m, a in reg["anchors"].items() if a["topic"] == tid}
    keep = mine | {f"{m}:re" for m in mine} | set(keep_dels)
    reg["entries"][tid] = {k: v for k, v in (reg["entries"].get(tid) or {}).items() if k in keep}
    for m in mine:
        reg["anchors"][m]["open"] = True
    reg["topics"][tid]["rev"] = reg["topics"][tid].get("rev", 0) + 1
