#!/usr/bin/env python3
"""ИИ-контент-завод: агенты «Радар» и «Автор» и обложки через Agnes AI API.

Ключ берётся ТОЛЬКО из переменной окружения AGNES_API_KEY. Никогда не пишите ключ в файлы.

Примеры:
  python tools/zavod.py radar --week "23–29 ноября 2026" --published content/week2/_published.md \
      --state "Создаю Instagram, записываю первый Reels, клиентов пока нет" --facts content/week2/_facts.md \
      -o content/week2/00-radar-otchet.md
  python tools/zavod.py avtor --topic "n8n пока не ставлю. Сначала — таблица." --format "карусель 8 слайдов" -o out.md
  python tools/zavod.py cover --prompt "кремовый фон, ноутбук с терминалом" --title "Одна команда.\\nДва агента." -o cover.jpg
  python tools/zavod.py check out.md      # проверка на запрещённые слова и кодовое слово
"""
import argparse, base64, io, json, os, re, sys, time, urllib.request, urllib.error
from pathlib import Path

BASE = os.environ.get("AGNES_BASE_URL", "https://apihub.agnes-ai.com/v1")
ROOT = Path(__file__).resolve().parent.parent
TEXT_MODEL = os.environ.get("ZAVOD_TEXT_MODEL", "agnes-2.5-pro")
# Если на платную модель не хватает баланса — переключаемся на бесплатные.
FALLBACK_MODELS = ["agnes-2.5-flash", "agnes-3.0-flash"]
IMAGE_MODEL = os.environ.get("ZAVOD_IMAGE_MODEL", "agnes-image-2.5-flash")

BANNED = [
    "инновацион", "революцион", "уникальн", "прорыв", "синерги", "экосистем", "парадигм",
    "драйвер роста", "масштабир", "бесшов", "комплексный подход", "персонализированный опыт",
    "заменит всех", "без усилий", "пассивный доход", "гарантированн", "взорвёт", "взорвет",
    "хак продвижения", "секрет, который", "10x", "просто добавь ии", "за 5 минут",
    "невероятн", "максимальн", "абсолютн", "легендарн", "топов", "имба",
]
# латиница, которая допустима в тексте; остальные английские слова — признак «протечки»
LATIN_OK = {"n8n", "kaspi", "reels", "reel", "instagram", "chatgpt", "claude", "gemini", "openai", "telegram",
            "google", "agnes", "mcp", "api", "ai", "python", "zavod", "py", "github", "stories", "codex",
            "anthropic", "gpt", "live", "astra", "sol", "tilda", "marketing_dir", "dictation", "content",
            "protection", "voice", "standard", "advanced", "beta", "flash", "pro", "tools", "md", "facebook",
            "meta", "postiz", "tiktok", "larry", "vpn", "mvp", "cta", "dm", "excel", "sheets", "notion", "pdf"}

STYLE = ("Minimal flat editorial illustration on warm cream paper background (#F4EFE4), "
         "dark teal (#1F665C) and mustard orange (#D69228) accents, thin clean lines, calm, lots of empty "
         "space in the top 45% of the image for a headline, subject placed in the lower half. "
         "Absolutely no text, no letters, no numbers, no logos, no people's faces. Subject: ")


class AgnesError(Exception):
    """Понятная ошибка Agnes API (без ключа в тексте)."""


def _key():
    k = os.environ.get("AGNES_API_KEY")
    if not k:
        raise AgnesError("Нет AGNES_API_KEY в переменных окружения. См. README, раздел «Запуск через Agnes API».")
    return k


def _post(path, body, timeout=300, retries=3):
    data = json.dumps(body).encode()
    last = None
    for attempt in range(retries):
        req = urllib.request.Request(BASE + path, data=data, headers={
            "Authorization": "Bearer " + _key(), "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            msg = e.read()[:400].decode("utf-8", "replace")
            last = AgnesError(f"Agnes API ответил {e.code}: {msg}")
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(5 * (attempt + 1)); continue
            raise last
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = AgnesError(f"Сеть / таймаут Agnes API: {e}")
            time.sleep(5 * (attempt + 1))
    raise last


_NO_QUOTA = {"until": 0}


def _quota_error(e):
    m = str(e)
    return ("402" in m or "403" in m) and ("quota" in m.lower() or "balance" in m.lower() or "credit" in m.lower())


def chat(system, user, model=None, max_tokens=16000, temperature=0.7):
    first = model or TEXT_MODEL
    models = [first] + [m for m in FALLBACK_MODELS if m != first]
    if time.time() < _NO_QUOTA["until"] and first not in FALLBACK_MODELS:
        models = [m for m in models if m in FALLBACK_MODELS]  # баланс пуст — сразу бесплатные
    last = None
    for model in models:
        try:
            r = _post("/chat/completions", {
                "model": model, "temperature": temperature, "max_tokens": max_tokens,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
            break
        except AgnesError as e:
            last = e
            if _quota_error(e):
                print(f"[{model}] нет баланса — пробую бесплатную модель", file=sys.stderr)
                if model not in FALLBACK_MODELS:
                    _NO_QUOTA["until"] = time.time() + 1800
                continue
            raise
    else:
        raise last
    ch = r["choices"][0]
    text = (ch["message"].get("content") or "").strip()  # reasoning_content игнорируем
    if ch.get("finish_reason") == "length":
        print("ВНИМАНИЕ: ответ обрезан по лимиту токенов (max_tokens).", file=sys.stderr)
    u = r.get("usage", {})
    print(f"[{model}] токены: вход {u.get('prompt_tokens')}, выход {u.get('completion_tokens')}", file=sys.stderr)
    if not text:
        raise AgnesError("Модель вернула пустой ответ.")
    return text


def check(text):
    """Список проблем по правилам avtor.md (пустой список = прошло)."""
    body = re.split(r"\n\**\s*#*\s*Самоотч[её]т", text)[0]  # самоотчёт не проверяем
    low = body.lower()
    issues = [f"запрещённое слово: «{w}»" for w in BANNED if w in low]
    if not re.search(r"[«“\"]завод[»”\"]", text):
        issues.append("нет кодового слова «завод»")
    for m in re.finditer(r"\d[\d\s]*\s?(%|процент|подписчик|просмотр|клиент|час(?:а|ов)? в день)", low):
        if not m.group(0).strip().startswith("50"):  # 50/50 — механика оплаты, не статистика
            issues.append(f"цифра без источника?: «{m.group(0).strip()}»")
    clean = re.sub(r"https?://\S+|\S+\.(?:com|io|ai|org|net|me|kz|ru|md|py)\S*|`[^`]*`|#\S+", " ", low)
    latin = {w for w in re.findall(r"\b[a-z]{4,}\b", clean) if w not in LATIN_OK}
    if latin:
        issues.append("английские слова в русском тексте: " + ", ".join(sorted(latin)))
    return issues


def _read(p):
    return Path(p).read_text(encoding="utf-8") if p else ""


RADAR_RULES = """Сделай вывод строго по разделу 6 инструкции: абзац о периоде и источниках, таблица ровно из 10 тем, блок «не брать на этой неделе». Ссылки ставь только из списка фактов выше и копируй их символ в символ.
Жёсткие правила бренда:
- кодовое слово в продающем действии одно — «завод»; других слов-триггеров («агент», «голос», «api» и т. п.) не придумывай;
- не обещай сроки («за 48 часов»), экономию времени («2 часа в день»), количество материалов и результат, если этого нет в фактах;
- не утверждай, что сервис доступен или недоступен в Казахстане, если в фактах этого нет — пиши «не проверял»;
- не приписывай пилоту конкретные модели других компаний."""


def radar(week, published="", state="", facts="", model=None):
    """Возвращает markdown-отчёт радара."""
    user = f"""Неделя: {week}. Часовой пояс: Азия/Алматы.

Что уже опубликовано или запланировано за последние 14 дней (не повторяй без новой мысли):
{published or 'нет данных'}

Что сейчас реально происходит в сборке завода:
{state or 'нет данных'}

Проверенные факты недели (открыты человеком, со ссылками). Используй ТОЛЬКО их как новости; других новостей не придумывай. У тебя нет доступа в интернет:
{facts or 'нет — делай темы только на процессе сборки'}

{RADAR_RULES}"""
    return chat(_read(ROOT / "agents/radar.md"), user, model)


def parse_radar(md):
    """Строки таблицы радара -> список словарей (номер, тема, хук, формат, ...)."""
    rows = []
    for line in md.splitlines():
        if not re.match(r"^\|\s*\d+\s*\|", line):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 5:
            continue
        keys = ["n", "topic", "hook", "angle", "format", "stage", "why", "source", "cta"]
        rows.append({k: (cells[i] if i < len(cells) else "") for i, k in enumerate(keys)})
    return rows


def avtor(topic, fmt, extra="", model=None, tries=3, log=None):
    """Пишет текст и прогоняет проверку; до `tries` попыток. Возвращает (текст, проблемы)."""
    system = _read(ROOT / "agents/avtor.md")
    user = f"""Тема из радара:
{topic}

Формат: {fmt}
{('Дополнительно: ' + extra) if extra else ''}

Сдай только финальный текст после трёх проходов (не показывай черновики): полный текст под формат по правилам раздела 3, затем «Подпись», затем «Самоотчёт» одной строкой. Пиши только по-русски (кроме названий сервисов). Не придумывай цифры, сроки и кейсы. Цена пилота — только как предложение. Один призыв: написать «завод» в директ. Оформи в Markdown, первая строка — заголовок «# …»."""
    text, issues = "", []
    for _ in range(max(1, tries)):
        text = chat(system, user, model)
        issues = check(text)
        if not issues:
            break
        (log or (lambda m: print(m, file=sys.stderr)))("Проверка не пройдена: " + "; ".join(issues))
        user += "\n\nПредыдущая версия не прошла проверку: " + "; ".join(issues) + ". Перепиши без этого."
    return text, issues


SERIF = "/usr/share/fonts/truetype/sand-box/google/PT Serif/PTSerif-Bold.ttf"
SANS = "/usr/share/fonts/truetype/sand-box/google/Manrope/Manrope-VariableFont_wght.ttf"


def _font(path, size):
    from PIL import ImageFont
    return ImageFont.truetype(path, size)


def wrap_title(title, font, max_w=920, max_lines=4):
    """Переносит заголовок по ширине; явные \\n сохраняет."""
    from PIL import Image, ImageDraw
    d = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    out = []
    for para in title.split("\n"):
        line = ""
        for w in para.split():
            t = (line + " " + w).strip()
            if d.textlength(t, font=font) <= max_w or not line:
                line = t
            else:
                out.append(line); line = w
        if line:
            out.append(line)
    return out[:max_lines]


def overlay(img, title, label, footer="личный бренд · Алматы"):
    """Заголовок в стиле недели 1: PT Serif, тёмно-зелёный, оранжевая черта. Холст 1080×1350."""
    from PIL import Image, ImageDraw
    img = img.convert("RGB")
    W, H = 1080, 1350
    r = max(W / img.width, H / img.height)
    img = img.resize((round(img.width * r), round(img.height * r)), Image.LANCZOS)
    l, t = (img.width - W) // 2, (img.height - H) // 2
    img = img.crop((l, t, l + W, t + H))
    d = ImageDraw.Draw(img)
    green, teal, orange = (24, 44, 38), (31, 102, 92), (214, 146, 40)
    d.text((80, 78), label.upper(), font=_font(SANS, 30), fill=teal)
    d.rectangle((80, 122, 280, 128), fill=orange)
    # подбираем кегль: чтобы явные строки не переносились (без «висящих» слов)
    want = len(title.split("\n"))
    for size in (96, 88, 80, 72, 64):
        ft = _font(SERIF, size)
        lines = wrap_title(title, ft)
        if len(lines) <= max(want, 1):
            break
    y = 170
    for line in lines:
        d.text((80, y), line, font=ft, fill=green)
        y += int(size * 1.23)
    d.text((80, H - 90), footer, font=_font(SANS, 28), fill=teal)
    return img


def image(prompt, model=None, size="2K", ratio="3:4", styled=True):
    """Генерирует картинку Agnes и возвращает PIL.Image."""
    from PIL import Image
    body = {"model": model or IMAGE_MODEL, "prompt": (STYLE + prompt) if styled else prompt,
            "size": size, "ratio": ratio, "return_base64": True}
    r = _post("/images/generations", body, timeout=300)
    item = (r.get("data") or [{}])[0]
    if item.get("b64_json"):
        raw = base64.b64decode(item["b64_json"])
    elif item.get("url"):
        with urllib.request.urlopen(item["url"], timeout=120) as f:
            raw = f.read()
    else:
        raise AgnesError("Agnes не вернул картинку.")
    img = Image.open(io.BytesIO(raw))
    print(f"[{body['model']}] получено {img.width}×{img.height}", file=sys.stderr)
    return img


def cover(prompt, title, label="карусель", raw_path=None, out_path=None, styled=True):
    img = image(prompt, styled=styled)
    if raw_path:
        Path(raw_path).parent.mkdir(parents=True, exist_ok=True)
        img.convert("RGB").save(raw_path, quality=92)
    final = overlay(img, title, label) if title else img.convert("RGB")
    if out_path:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        final.save(out_path, quality=90)
    return final


# ---------------- CLI ----------------

def _out(text, path):
    if path:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(text.rstrip() + "\n", encoding="utf-8")
        print(f"Сохранено: {path}", file=sys.stderr)
    else:
        print(text)


def _arg_text(v):
    return _read(v) if v and Path(v).exists() else (v or "")


def main():
    p = argparse.ArgumentParser(description="ИИ-контент-завод на Agnes AI API")
    s = p.add_subparsers(dest="cmd", required=True)
    r = s.add_parser("radar"); r.add_argument("--week", required=True)
    r.add_argument("--published", help="файл или текст: что уже вышло"); r.add_argument("--state", required=True)
    r.add_argument("--facts", help="файл с проверенными новостями и ссылками"); r.add_argument("--model")
    r.add_argument("-o", "--output")
    w = s.add_parser("avtor"); w.add_argument("--topic", required=True); w.add_argument("--format", required=True)
    w.add_argument("--extra"); w.add_argument("--model"); w.add_argument("--tries", type=int, default=3)
    w.add_argument("-o", "--output")
    c = s.add_parser("cover"); c.add_argument("--prompt", required=True); c.add_argument("--title")
    c.add_argument("--label", default="карусель"); c.add_argument("--raw", help="сохранить фон без текста")
    c.add_argument("--no-style", action="store_true", help="не добавлять фирменный стиль к промпту")
    c.add_argument("-o", "--output", required=True)
    k = s.add_parser("check"); k.add_argument("file")
    a = p.parse_args()
    try:
        if a.cmd == "radar":
            _out(radar(a.week, _arg_text(a.published), a.state, _arg_text(a.facts), a.model), a.output)
        elif a.cmd == "avtor":
            text, issues = avtor(a.topic, a.format, a.extra or "", a.model, a.tries)
            if issues:
                print("После всех попыток остались замечания: " + "; ".join(issues), file=sys.stderr)
            _out(text, a.output)
        elif a.cmd == "cover":
            cover(a.prompt, (a.title or "").replace("\\n", "\n"), a.label, a.raw, a.output, not a.no_style)
            print(f"Сохранено: {a.output}", file=sys.stderr)
        elif a.cmd == "check":
            issues = check(_read(a.file))
            print("OK" if not issues else "\n".join(issues))
            sys.exit(1 if issues else 0)
    except AgnesError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
