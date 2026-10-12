"""Пересказ задачи для карточки и её прогресс для «Прогресса» — одним вызовом дешёвой модели по переписке, тем же
приёмом, что recap у сторожа (modules/watch/watch.py): по просьбе страницы (engine.py), не чаще раза в EVERY. Сбой
модели — None, прежнее остаётся. «Прогресс» (владелец 11.10) — на одну страницу: задача, критерии приёмки с отметкой
«сделан», самые важные решения коротко."""
import sys

from classify import real_ask

EVERY = 1800       # с между пересказами одной задачи по «Прогрессу»; «Обновить» — без ожидания
LAST = 40          # последних записей в пересказ
ENTRY_MAX = 1500   # знаков одной записи в пересказ
CRITERIA = 6       # критериев приёмки — вместе с решениями влезает в один экран
DECISIONS = 4      # самых важных решений
ITEM_MAX = 110     # знаков одного пункта — одна-две строки

SCHEMA = {"type": "object", "required": ["summary", "goal", "criteria", "decisions"], "properties": {
    "summary": {"type": "string"}, "goal": {"type": "string"},
    "criteria": {"type": "array", "items": {"type": "object", "required": ["text", "done"], "properties": {
        "text": {"type": "string"}, "done": {"type": "boolean"}}}},
    "decisions": {"type": "array", "items": {"type": "string"}}}}
PROMPT = ("На входе — переписка владельца с сессиями по одной его задаче. Ответь JSON по-русски, очень коротко, без воды: "
          "summary — одно предложение, на чём задача стоит сейчас; goal — что нужно получить в итоге, одним коротким "
          "предложением; criteria — критерии приёмки: по чему владелец поймёт, что задача готова, до 6 пунктов по 3–10 "
          "слов (если в переписке есть критерии приёмки, AC, задачи Jira — бери ровно их, своих не придумывай), done — true, "
          "только если по переписке это уже сделано и проверено; decisions — до 4 самых важных "
          "решений владельца, по 3–12 слов, без мелочей. Только то, что есть в переписке; нечего сказать — пустой "
          "список. Ответь только JSON.")


def _line(x):
    return " ".join(str(x).split())[:ITEM_MAX]


def _criteria(value):
    out = []
    for c in value if isinstance(value, list) else []:
        if isinstance(c, dict) and _line(c.get("text") or ""):
            out.append({"text": _line(c["text"]), "done": c.get("done") is True})
    return out[:CRITERIA]


def _decisions(value):
    out = [_line(x) for x in value if isinstance(x, str)] if isinstance(value, list) else []
    return [x for x in out if x][:DECISIONS]


def summarize(title, entries, ask=real_ask):
    if not entries:
        return None
    text = f"Задача: {title}\n" + "\n".join(
        f"{'владелец' if e['who'] == 'owner' else e.get('session') or 'сессия'}: {e['text'][:ENTRY_MAX]}"
        for e in entries[-LAST:])
    try:
        out = ask(PROMPT, text, SCHEMA)
    except (OSError, ValueError, TypeError) as e:
        print(f"tasks: пересказ не удался: {e}", file=sys.stderr, flush=True)
        return None
    if not isinstance(out, dict) or not str(out.get("summary") or "").strip():
        return None
    return {"summary": " ".join(str(out["summary"]).split())[:300], "goal": " ".join(str(out.get("goal") or "").split())[:200],
            "criteria": _criteria(out.get("criteria")), "decisions": _decisions(out.get("decisions"))}


def due(last_summary_at, last_entry_at, now, every=EVERY):
    if last_summary_at is None:
        return True
    return last_entry_at > last_summary_at and now - last_summary_at >= every
