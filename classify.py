"""К какой из открытых задач относится сообщение владельца — дешёвой моделью (Haiku) со строгой JSON-схемой ответа.
Вызов — через `_claude` сторожа хаба (modules/watch/watch.py): то же окружение пользователя (вход, прокси), что у
recap, без размышлений и кэша. Сбой модели — сообщение просто остаётся без задачи."""
import sys

SCHEMA = {"type": "object", "required": ["topic", "sure"],
          "properties": {"topic": {"type": ["string", "null"]}, "sure": {"type": "boolean"}}}

PROMPT = ("Тебе дан список открытых задач владельца (id, название и о чём она), контекст (какой сессии он пишет и какое у неё "
          "поручение, последние реплики разговора в этой сессии) и само сообщение. Верни id задачи, о которой "
          "это сообщение, — по смыслу вместе с контекстом, а не по общим словам («хаб», «кнопка», «плагин» встречаются "
          "во многих задачах). Сообщение может быть про дело, которого нет в списке, — тогда null, а не ближайшая "
          "задача. sure — true, только если сообщение точно про эту задачу; сомневаешься — false. Не выдумывай новые "
          "id. Ответь только JSON.")


def real_ask(system, text, schema):
    # _claude — не API плагинов: ядро её переименовало — сбой модели, а не падение процесса на том же сообщении по
    # кругу (курсор пишется после тика; ревью 12.10)
    try:
        from modules.watch.watch import _claude
    except ImportError as e:
        raise OSError(f"нет вызова модели в ядре: {e}") from e
    return _claude(text, system, schema)


def classify_message(topics, text, ask=real_ask, context="", accept=None):
    """id задачи или None. Берём уверенный ответ модели; неуверенный — только если он совпал с accept: задачей прошлого
    слова владельца в этой сессии (engine.py). Саму accept модели не показываем — подсказка тянула ошибки за собой."""
    if not topics:
        return None
    system = PROMPT + "\nЗадачи:\n" + "\n".join(
        f"- {t['id']}: {t['title']}" + (f" — {t['about']}" if t.get("about") else "") for t in topics)
    try:
        out = ask(system, (f"Контекст: {context}\n\nСообщение:\n" if context else "") + text, SCHEMA)
    except (OSError, ValueError, TypeError) as e:
        print(f"tasks: классификация не удалась: {e}", file=sys.stderr, flush=True)
        return None
    if not isinstance(out, dict):
        return None
    topic = out.get("topic")
    if out.get("sure") is not True and (accept is None or topic != accept):   # без подходящей задачи модель брала ближайшую
        return None
    return topic if topic in {t["id"] for t in topics} else None


TITLE_MAX = 60   # знаков названия задачи — влезает в карточку списка
TITLE_SCHEMA = {"type": "object", "required": ["title"], "properties": {"title": {"type": "string"}}}
TITLE_PROMPT = ("Тебе дан запрос владельца, с которого начинается его задача. Дай задаче короткое название: 2–6 слов "
                "на языке запроса, суть дела, без точки в конце и без слов «задача», «надо», «сделать». Ответь только JSON.")


def short_title(text, ask=real_ask):
    """Название задачи для /new-task по запросу — моделью; сбой или пусто — None (engine возьмёт fallback_title)."""
    try:
        out = ask(TITLE_PROMPT, text[:4000], TITLE_SCHEMA)
    except (OSError, ValueError, TypeError) as e:
        print(f"tasks: название не придумалось: {e}", file=sys.stderr, flush=True)
        return None
    title = " ".join(str((out or {}).get("title") or "").split()).strip(" .")
    return fallback_title(title) if title else None


def fallback_title(text):
    """Начало первой строки запроса, обрезанное по слову."""
    line = " ".join((text.strip().splitlines() or [""])[0].split())
    if len(line) <= TITLE_MAX:
        return line
    cut = line[:TITLE_MAX - 1].rsplit(" ", 1)[0].rstrip(" ,.;:—-")
    return cut + "…"
