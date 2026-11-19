"""Генерация готового контента в JSON: карусель, Reels, stories. Два прохода: автор → редактор «живости»."""
import json, re, sys
from pathlib import Path

import os
import zavod

MODEL = os.environ.get("ZAVOD_FABRIKA_MODEL", "agnes-3.0-flash")  # бесплатная и быстрая; 2.5-pro — если есть баланс

VOICE = """
ГОЛОС — САМОЕ ВАЖНОЕ.
Пиши как живой парень из Алматы, который за чаем объясняет знакомому, что он сегодня собрал и зачем.
- Разговорный русский. Коротко. Конкретно. Глаголы, а не существительные («я проверяю», а не «осуществляется проверка»).
- Лёгкая ирония и разговорные слова уместны, но не вставляй одни и те же словечки («короче», «вот тут интересно») в каждый текст. Нельзя канцелярит и пресс-релиз.
- Примеры плохих фраз (так НЕ писать): «Это вариант, не обязательство», «Осуществляю настройку», «Данный подход позволяет», «В рамках пилота», «Цена — предложение» сухой строкой, «Граница оффера», «Ступень воронки».
- Примеры хороших фраз (по духу, не копируй дословно): «Цену называю сразу: 80–120 тысяч, зависит от задач. Можно в два платежа через Kaspi.», «Охваты не обещаю. Обещаю, что темы на неделю у вас будут.», «Вы пишете одно слово — дальше моя работа.»
- Никаких внутренних пометок: не пиши «самоотчёт», «проход», «ступень», «хук», «CTA», «библия стиля», «пластик» в готовом тексте.
- Не выдумывай цифры, сроки, длительность («за день», «15 минут»), кейсы, клиентов, отзывы. Нет факта — не пиши.
- Цена пилота «2 агента под ключ», если уместна: 80–120 тыс. ₸, зависит от задач, можно 50/50 через Kaspi. Говори её по-человечески.
- Обращайся к читателю на «вы», везде одинаково.
- Без жаргона и молодёжного сленга («катка», «рейки», «драфт», «вайб»). Разговорно, но грамотно.
- Не обещай ничего бесплатного и никаких «разборов в подарок».
- Призыв один, в конце: написать «завод» в директ.
"""

SCHEMAS = {
    "carousel": """Верни ТОЛЬКО JSON без пояснений и без ```:
{
 "title": "заголовок обложки, 3–7 слов, цепляет",
 "subtitle": "подзаголовок обложки, до 10 слов",
 "cover_visual": "описание картинки для обложки ПО-АНГЛИЙСКИ: предметы/сцена, без людей крупным планом, без текста, без денег/купюр, без экранов с интерфейсом",
 "slides": [ {"heading": "до 6 слов", "body": "15–30 слов, 1–3 коротких предложения"} ],
 "caption": "подпись к посту: 3–6 коротких живых предложений, в конце призыв написать «завод»",
 "hashtags": ["#алматы", "#контентзавод", "..."]
}
slides — 6–8 штук (обложка не входит). Последний слайд — призыв написать «завод» в директ. Предпоследний — честно, что не входит / чего не обещаю.""",
    "reels": """Верни ТОЛЬКО JSON без пояснений и без ```:
{
 "hook": "текст на экране в первые 2 секунды, 3–6 слов",
 "scenes": [
   {"visual": "описание кадра ПО-АНГЛИЙСКИ для генерации картинки: вертикальный кадр, предметы, свет, без текста и без лиц",
    "phrases": ["фраза голоса 5–14 слов", "ещё фраза"]}
 ],
 "caption": "подпись к Reels: 3–5 коротких предложений, в конце призыв написать «завод»",
 "hashtags": ["#алматы", "#контентзавод", "..."]
}
scenes — ровно 4, в каждой 3 фразы (всего 11–12 фраз), каждая фраза 8–11 слов. ВСЕГО 90–115 слов — это 33–43 секунды речи, короче нельзя. Первая фраза первой сцены — это хук вслух. Последняя фраза — призыв написать «завод» в директ.
Фразы пишутся ДЛЯ ГОЛОСА: как говорят, а не как пишут. Цифры пиши словами («восемьдесят–сто двадцать тысяч тенге»), без символов ₸, %, /, кавычек-ёлочек внутри фраз нет, кроме слова «завод».""",
    "stories": """Верни ТОЛЬКО JSON без пояснений и без ```:
{
 "frames": [ {"title": "крупно, до 6 слов", "text": "до 18 слов, можно пусто", "visual": "описание фона ПО-АНГЛИЙСКИ, без текста и лиц, без экранов с интерфейсом, без денег"} ]
}
frames — 4 штуки (можно 3–5). Последний кадр — вопрос или призыв написать «завод» в директ.""",
}

EDITOR = """Ты — редактор. Перед тобой JSON с готовым постом для Instagram. Перепиши ВСЕ тексты внутри так, чтобы звучало живо, по-разговорному, как реальный человек из Алматы объясняет за чаем. Убери канцелярит, пафос, повторы и роботские обороты. Сохрани смысл, факты, структуру JSON, количество элементов и лимиты длины. Не добавляй цифр и фактов, которых не было. Поля с описанием картинок (visual, cover_visual) оставь по-английски.
""" + VOICE + "\nВерни ТОЛЬКО исправленный JSON."


def kind_of(fmt):
    f = (fmt or "").lower()
    if "reel" in f or "рилс" in f or "видео" in f:
        return "reels"
    if "stor" in f or "сторис" in f or "истори" in f:
        return "stories"
    return "carousel"


def parse_json(text):
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    a, b = t.find("{"), t.rfind("}")
    if a < 0 or b < 0:
        raise ValueError("нет JSON")
    return json.loads(t[a:b + 1])


def _words(s):
    return len(re.findall(r"\w+", s or ""))


def validate(kind, d):
    p = []
    if kind == "carousel":
        sl = d.get("slides") or []
        if not (5 <= len(sl) <= 9): p.append(f"слайдов {len(sl)}, нужно 6–8")
        for i, s in enumerate(sl, 2):
            if not s.get("heading"): p.append(f"слайд {i}: нет заголовка")
            if _words(s.get("body")) > 36: p.append(f"слайд {i}: текст длиннее 30 слов")
            if _words(s.get("heading")) > 8: p.append(f"слайд {i}: заголовок длиннее 6 слов")
        if not d.get("title"): p.append("нет title")
        if not d.get("caption"): p.append("нет caption")
    elif kind == "reels":
        sc = d.get("scenes") or []
        if not (3 <= len(sc) <= 5): p.append(f"сцен {len(sc)}, нужно 4")
        ph = [x for s in sc for x in (s.get("phrases") or [])]
        w = sum(_words(x) for x in ph)
        if w < 70: p.append(f"всего {w} слов, нужно 85–110")
        if w > 125: p.append(f"всего {w} слов, нужно 85–110")
        if not d.get("hook"): p.append("нет hook")
        if not d.get("caption"): p.append("нет caption")
    else:
        fr = d.get("frames") or []
        if not (3 <= len(fr) <= 5): p.append(f"кадров {len(fr)}, нужно 3–5")
        for i, f in enumerate(fr, 1):
            if not f.get("title"): p.append(f"кадр {i}: нет title")
            if _words(f.get("text")) > 24: p.append(f"кадр {i}: текст длиннее 18 слов")
    return p


def flat_text(kind, d):
    """Весь видимый пользователю текст — для проверки check()."""
    parts = []
    if kind == "carousel":
        parts += [d.get("title", ""), d.get("subtitle", "")]
        for s in d.get("slides", []):
            parts += [s.get("heading", ""), s.get("body", "")]
    elif kind == "reels":
        parts.append(d.get("hook", ""))
        for s in d.get("scenes", []):
            parts += s.get("phrases", [])
    else:
        for f in d.get("frames", []):
            parts += [f.get("title", ""), f.get("text", "")]
    parts += [d.get("caption", ""), " ".join(d.get("hashtags", []))]
    return "\n".join(p for p in parts if p)


INTERNAL = re.compile(r"самоотч|ступен[ья] воронки|\bхук\b|библи[яи] стиля|\bcta\b|пластик", re.I)


def issues_of(kind, d):
    text = flat_text(kind, d)
    iss = validate(kind, d) + zavod.check(text)
    if kind == "stories" and not re.search(r"завод", text, re.I):
        pass
    if kind == "stories":
        iss = [i for i in iss if "кодового слова" not in i] if re.search(r"завод", text) else iss
    if INTERNAL.search(text):
        iss.append("в тексте служебные слова (самоотчёт/хук/ступень/пластик) — убрать")
    return iss


PROOF = """Ты — корректор русского языка. В JSON ниже исправь ТОЛЬКО опечатки, ошибки в словах, грамматику, пунктуацию и
обращение (везде на «вы», со строчной буквы: «вы», «вам», «ваш»). Замени сленг («катка», «рейки», «драфт») обычными словами. Больше ничего не меняй: ни смысл,
ни длину, ни структуру, ни поля visual/cover_visual. Верни ТОЛЬКО JSON."""


def _ask(system, user, log, tries=3, temperature=0.8):
    last = None
    for _ in range(tries):
        out = zavod.chat(system, user, model=MODEL, temperature=temperature, max_tokens=6000)
        try:
            return parse_json(out)
        except Exception as e:
            last = e
            log(f"плохой JSON ({e}), повторяю")
            user += "\n\nВНИМАНИЕ: прошлый ответ не был валидным JSON. Верни строго один JSON-объект."
    raise zavod.AgnesError(f"модель не вернула JSON: {last}")


def generate(topic, fmt, extra="", log=None, progress=None):
    """Возвращает (kind, data, issues). data — проверенный JSON для рендера."""
    log = log or (lambda m: print(m, file=sys.stderr))
    progress = progress or (lambda m: None)
    kind = kind_of(fmt)
    system = zavod._read(zavod.ROOT / "agents/avtor.md") + "\n\n" + VOICE
    user = f"Тема:\n{topic}\n\nФормат: {fmt}\n{('Дополнительно: ' + extra) if extra else ''}\n\n{SCHEMAS[kind]}"
    progress("✍️ Пишу текст…")
    d = _ask(system, user, log)
    progress("🧹 Редактор делает текст живым…")
    d2 = _ask(EDITOR, json.dumps(d, ensure_ascii=False), log, temperature=0.55)
    if not validate(kind, d2):
        d = d2
    iss = issues_of(kind, d)
    for _ in range(2):
        if not iss:
            break
        log("замечания: " + "; ".join(iss))
        progress("🔧 Дорабатываю по замечаниям…")
        hint = ""
        if any("слов, нужно" in i for i in iss):
            hint = (" Если текста мало — добавь новые фразы с конкретикой по теме (что я делаю, что видно на экране, "
                    "что получит зритель), чтобы всего было 90–115 слов.")
        fix = _ask(EDITOR, json.dumps(d, ensure_ascii=False) +
                   "\n\nИсправь эти замечания: " + "; ".join(iss) + "." + hint, log, temperature=0.5)
        if len(issues_of(kind, fix)) <= len(iss):
            d = fix
        iss = issues_of(kind, d)
    progress("🔎 Вычитываю опечатки…")
    try:
        p = _ask(PROOF, json.dumps(d, ensure_ascii=False), log, tries=2, temperature=0.1)
        if not validate(kind, p) and len(issues_of(kind, p)) <= len(iss):
            d, iss = p, issues_of(kind, p)
    except Exception as e:
        log(f"корректор пропущен: {e}")
    return kind, d, iss


def caption_text(d):
    cap = (d.get("caption") or "").strip()
    raw = [t if t.startswith("#") else "#" + t for t in (d.get("hashtags") or [])]
    raw = [t for t in raw if not re.search(r"[a-z]", t.lower()) or t.lower() == "#n8n"]
    tags = " ".join(list(dict.fromkeys(raw))[:5])
    return (cap + ("\n\n" + tags if tags else "")).strip()
