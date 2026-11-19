#!/usr/bin/env python3
"""Telegram-бот ИИ-контент-завода (long polling).

Переменные окружения (никогда не пишите их в файлы репозитория):
  TELEGRAM_BOT_TOKEN — токен бота от @BotFather
  AGNES_API_KEY      — ключ Agnes AI API

Доступ: первый, кто отправит /start, становится владельцем (chat_id хранится в ~/.zavod_owner).
Остальных бот игнорирует. Публикации в Instagram бот не делает — только черновики.
"""
import datetime as dt, json, os, re, subprocess, sys, threading, time, traceback, urllib.request, urllib.error, uuid, io
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import zavod  # noqa: E402
import fabrika_text  # noqa: E402
import fabrika_render  # noqa: E402
import shutil  # noqa: E402

ROOT = zavod.ROOT
HOME = Path.home()
OWNER_FILE = HOME / ".zavod_owner"
STATE_FILE = HOME / ".zavod_bot_state.json"
WORK = HOME / ".zavod_bot"
APPROVED = ROOT / "content" / "approved"
TZ = dt.timezone(dt.timedelta(hours=5))  # Азия/Алматы
MAX = 4000  # запас до лимита Telegram 4096
EXIT_CONFLICT = 3

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
API = "https://api.telegram.org/bot" + TOKEN
lock = threading.Lock()


def log(msg):
    msg = str(msg)
    for secret in (TOKEN, os.environ.get("AGNES_API_KEY", "")):
        if secret:
            msg = msg.replace(secret, "***")
    print(dt.datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


class Conflict(Exception):
    pass


# ---------- Telegram API ----------

def tg(method, _timeout=70, **params):
    data = json.dumps(params).encode()
    req = urllib.request.Request(f"{API}/{method}", data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=_timeout) as r:
            return json.load(r)["result"]
    except urllib.error.HTTPError as e:
        body = e.read()[:300].decode("utf-8", "replace")
        if e.code == 409:
            raise Conflict(body)
        raise RuntimeError(f"Telegram {method} {e.code}: {body}")


def tg_multipart(method, fields, files, timeout=300):
    """files: список (имя_поля, имя_файла, байты, mime)."""
    boundary = uuid.uuid4().hex
    buf = io.BytesIO()
    for k, v in fields.items():
        buf.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    for field, filename, content, mime in files:
        buf.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{filename}\"\r\n"
                  f"Content-Type: {mime}\r\n\r\n".encode())
        buf.write(content)
        buf.write(b"\r\n")
    buf.write(f"--{boundary}--\r\n".encode())
    req = urllib.request.Request(f"{API}/{method}", data=buf.getvalue(),
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)["result"]
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Telegram {method} {e.code}: {e.read()[:300].decode('utf-8', 'replace')}")


def chunks(text, size=MAX):
    out, cur = [], ""
    for para in text.split("\n"):
        while len(para) > size:
            out.append(cur) if cur else None
            cur = ""
            out.append(para[:size]); para = para[size:]
        if len(cur) + len(para) + 1 > size:
            out.append(cur); cur = para
        else:
            cur = (cur + "\n" + para) if cur else para
    if cur:
        out.append(cur)
    return [c for c in out if c.strip()] or ["(пусто)"]


def send(chat, text, buttons=None):
    """Отправляет длинный текст частями; кнопки — на последнем сообщении."""
    parts = chunks(text)
    for i, part in enumerate(parts):
        p = {"chat_id": chat, "text": part, "disable_web_page_preview": True}
        if buttons and i == len(parts) - 1:
            p["reply_markup"] = {"inline_keyboard": buttons}
        tg("sendMessage", **p)


def send_doc(chat, name, data, caption="", mime="text/markdown"):
    if isinstance(data, str):
        data = data.encode("utf-8")
    tg_multipart("sendDocument", {"chat_id": chat, "caption": caption[:1000]}, [("document", name, data, mime)])


def send_photo(chat, path, caption=""):
    tg_multipart("sendPhoto", {"chat_id": chat, "caption": caption[:1000]},
                 [("photo", Path(path).name, Path(path).read_bytes(), "image/jpeg")])


def send_album(chat, paths):
    """Альбом до 10 фото одним сообщением."""
    paths = [Path(p) for p in paths][:10]
    media = [{"type": "photo", "media": f"attach://f{i}"} for i in range(len(paths))]
    files = [(f"f{i}", p.name, p.read_bytes(), "image/jpeg") for i, p in enumerate(paths)]
    tg_multipart("sendMediaGroup", {"chat_id": chat, "media": json.dumps(media)}, files)


def send_video(chat, path, thumb=None, seconds=0):
    files = [("video", Path(path).name, Path(path).read_bytes(), "video/mp4")]
    fields = {"chat_id": chat, "supports_streaming": "true", "width": 1080, "height": 1920, "duration": int(seconds)}
    if thumb:
        files.append(("thumbnail", "thumb.jpg", Path(thumb).read_bytes(), "image/jpeg"))
        fields["thumbnail"] = "attach://thumbnail"
    tg_multipart("sendVideo", fields, files, timeout=600)


def typing(chat, action="typing"):
    try:
        tg("sendChatAction", chat_id=chat, action=action)
    except Exception:
        pass


# ---------- состояние ----------

def load_state():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"offset": 0, "topics": [], "drafts": {}, "await_edit": None, "week": ""}


def save_state(st):
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(STATE_FILE)


def owner():
    try:
        return int(OWNER_FILE.read_text().strip())
    except Exception:
        return None


# ---------- логика завода ----------

MENU = [[{"text": "🎯 Темы на неделю", "callback_data": "radar"}],
        [{"text": "✍️ Написать пост по теме", "callback_data": "howto"}],
        [{"text": "📂 Одобренные", "callback_data": "approved"}]]

HELP = ("Я — ИИ-контент-завод. Делаю готовый контент, публикуешь ты сам.\n\n"
        "🎯 Темы на неделю — радар даёт 10 тем на следующую неделю.\n"
        "Нажми на тему — получишь готовое: карусель (альбом слайдов + zip), Reels (видео с голосом и субтитрами) "
        "или stories (кадры 1080×1920) и подпись.\n\n"
        "Или напиши сам:\nтема: Почему я начинаю с таблицы, формат: карусель 8 слайдов\n\n"
        "Под черновиком: ✅ Одобрить (сохраню в репозиторий), 🔁 Переделать, ✏️ Правка.")


def next_week():
    today = dt.datetime.now(TZ).date()
    mon = today + dt.timedelta(days=(7 - today.weekday()) % 7 or 7)
    sun = mon + dt.timedelta(days=6)
    months = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября",
              "октября", "ноября", "декабря"]
    if mon.month == sun.month:
        return f"{mon.day}–{sun.day} {months[mon.month - 1]} {mon.year} (Пн–Вс)"
    return f"{mon.day} {months[mon.month - 1]} – {sun.day} {months[sun.month - 1]} {sun.year} (Пн–Вс)"


def latest(pattern):
    files = sorted(ROOT.glob(pattern))
    return files[-1] if files else None


def published_text():
    lines = []
    p = latest("content/week*/_published.md")
    if p:
        lines.append(p.read_text(encoding="utf-8"))
    wk = latest("content/week*/00-radar-otchet.md")
    if wk:
        sel = wk.read_text(encoding="utf-8").split("## Выбрано 7 тем")
        if len(sel) > 1:
            lines.append("Последняя неделя (выбранные темы):" + sel[1])
    for f in sorted(APPROVED.glob("*.md"))[-14:]:
        first = f.read_text(encoding="utf-8").splitlines()[0:1]
        lines.append(f"- одобрено {f.stem[:10]}: {first[0] if first else f.stem}")
    return "\n".join(lines)


def facts_text():
    f = ROOT / "content" / "facts.md"
    if not f.exists():
        f = latest("content/week*/_facts.md")
    if not f:
        return ""
    return ("(Файл фактов: " + str(f.relative_to(ROOT)) + ". Если даты фактов старше 10 дней — "
            "не выдавай их за новости этой недели.)\n" + f.read_text(encoding="utf-8"))


STATE_TEXT = ("Instagram только запускается, публикации начались, клиентов пока нет. Радар и автор "
              "запускаются через Agnes AI API скриптом и Telegram-ботом. n8n ещё не ставил. "
              "Оффер: пилот «2 агента под ключ», предложение 80–120 тыс. ₸, Kaspi, можно 50/50.")


def do_radar(chat):
    st = load_state()
    week = next_week()
    send(chat, f"Запускаю радар на неделю {week}. Это 30–90 секунд…")
    typing(chat)
    md = zavod.radar(week, published_text(), STATE_TEXT, facts_text())
    rows = zavod.parse_radar(md)
    with lock:
        st = load_state()
        st["week"] = week
        st["topics"] = rows
        save_state(st)
    send_doc(chat, "radar.md", md, f"Полный отчёт радара, {week}")
    if not rows:
        send(chat, "Не смог разобрать таблицу тем. Полный отчёт — в файле выше. Можно написать тему вручную:\n"
                   "тема: …, формат: …", MENU)
        return
    lines = [f"🎯 Темы на {week}\n"]
    for r in rows:
        lines.append(f"{r['n']}. {r['topic']}\n   Формат: {r['format']}\n   Хук: {r['hook']}\n")
    lines.append("Нажми на номер темы — соберу готовый пост (карусель — 1–2 мин, Reels — 2–4 мин).")
    btns, row = [], []
    for r in rows:
        row.append({"text": f"{r['n']}. {r['topic'][:28]}", "callback_data": f"t:{r['n']}"})
        if len(row) == 2:
            btns.append(row); row = []
    if row:
        btns.append(row)
    send(chat, "\n".join(lines), btns)


def fmt_norm(f):
    f = f.lower()
    if "карус" in f:
        return "карусель 8 слайдов"
    if "stor" in f or "сторис" in f or "истори" in f:
        return "серия stories 4 кадра"
    if "reel" in f or "рилс" in f or "видео" in f:
        return "сценарий Reels 30–45 секунд (таблица сек/экран/голос/титры)"
    return f or "карусель 8 слайдов"


def label_for(fmt):
    return "reels" if "reels" in fmt.lower() else ("stories" if "stories" in fmt.lower() else "карусель")


def title_of(text):
    for line in text.splitlines():
        if line.startswith("# "):
            t = re.sub(r"[*_`#]", "", line[2:]).strip()
            t = re.sub(r"^(Reels|Карусель|Stories)[^:]*:\s*", "", t, flags=re.I).strip(" «»\"")
            return t
    return ""


def cover_title(t):
    t = t[:70]
    parts = re.split(r"(?<=[.!?])\s+", t, maxsplit=1)
    return "\n".join(parts)


KIND_RU = {"carousel": "карусель", "reels": "Reels", "stories": "stories"}


def draft_buttons(did):
    return [[{"text": "✅ Одобрить", "callback_data": f"a:{did}"},
             {"text": "🔁 Переделать", "callback_data": f"r:{did}"}],
            [{"text": "✏️ Правка", "callback_data": f"e:{did}"}]]


def make_draft(chat, topic, fmt, extra="", draft_id=None, keep_cover=None):
    """Готовый контент: текст (JSON) → рендер (слайды / видео / кадры) → отправка."""
    t0 = time.time()
    did = dt.datetime.now(TZ).strftime("%m%d%H%M%S")
    out = WORK / did
    out.mkdir(parents=True, exist_ok=True)
    prog = lambda m: send(chat, m)
    kind, data, issues = fabrika_text.generate(topic, fmt, extra, log=log, progress=prog)
    caption = fabrika_text.caption_text(data)
    (out / "content.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    info = {}
    if kind == "carousel":
        prog(f"🎨 Рисую слайды ({len(data['slides']) + 1} шт.)…")
        typing(chat, "upload_photo")
        files = fabrika_render.render_carousel(data, out / "slides", log)
        zipf = fabrika_render.zip_files(files, out / "karusel.zip")
        send_album(chat, files)
        send_doc(chat, "karusel.zip", zipf.read_bytes(), "Все слайды в полном качестве (1080×1350)", "application/zip")
        media = [str(f) for f in files] + [str(zipf)]
    elif kind == "stories":
        prog(f"🎨 Рисую кадры stories ({len(data['frames'])} шт.)…")
        typing(chat, "upload_photo")
        files = fabrika_render.render_stories(data, out / "stories", log)
        send_album(chat, files)
        media = [str(f) for f in files]
    else:
        final, thumb, info = fabrika_render.render_reels(data, out / "reels", log, prog)
        prog("📤 Загружаю видео…")
        typing(chat, "upload_video")
        send_video(chat, final, thumb, info.get("seconds", 0))
        media = [str(final), str(thumb), str(out / "reels" / "script.txt")]
    (out / "caption.txt").write_text(caption, encoding="utf-8")
    with lock:
        st = load_state()
        st["drafts"][did] = {"topic": topic, "format": fmt, "kind": kind, "data": data, "caption": caption,
                             "dir": str(out), "media": media, "issues": issues, "chat": chat}
        for k in list(st["drafts"])[:-30]:
            old = st["drafts"].pop(k, None)
            if old and old.get("dir"):
                shutil.rmtree(old["dir"], ignore_errors=True)
        save_state(st)
    took = int(time.time() - t0)
    head = {"carousel": "📝 Подпись к карусели — копируй целиком:",
            "reels": "📝 Подпись к Reels — копируй целиком:",
            "stories": "📝 Stories готовы. Музыку и стикеры добавь в Instagram."}[kind]
    extra_line = ""
    if kind == "reels":
        extra_line = f"\n\n⏱ {info.get('seconds')} с · голос: {info.get('tts', '')}. Музыку добавь в Instagram."
    if issues:
        log("остались замечания: " + "; ".join(issues))
    body = head + ("\n\n" + caption if kind != "stories" else "") + extra_line + f"\n\n(собрано за {took} с)"
    send(chat, body, draft_buttons(did))


def slug(t):
    tr = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                  "a b v g d e e zh z i y k l m n o p r s t u f h ts ch sh sch _ y _ e yu ya".split()))
    tr.update({"ъ": "", "ь": ""})
    s = "".join(tr.get(c, c) for c in t.lower())
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:40] or "post"


def git(*args):
    r = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()[:300]}")
    return r.stdout


def approve(chat, did):
    st = load_state()
    d = st["drafts"].get(did)
    if not d or "kind" not in d:
        send(chat, "Этот черновик уже не найден. Сделай новый.", MENU); return
    data = d["data"]
    title = data.get("title") or data.get("hook") or (data.get("frames") or [{}])[0].get("title") or d["topic"]
    name = dt.datetime.now(TZ).strftime("%Y-%m-%d") + "-" + d["kind"] + "-" + slug(title)
    dest = APPROVED / name
    dest.mkdir(parents=True, exist_ok=True)
    to_git, skipped = [], []
    for m in d["media"]:
        p = Path(m)
        if not p.exists():
            continue
        q = dest / p.name
        shutil.copy2(p, q)
        if q.suffix == ".zip":
            continue  # zip — только для отправки в Telegram, в репозитории есть сами слайды
        if q.stat().st_size > 20 * 1024 * 1024:
            skipped.append(q)
        else:
            to_git.append(q)
    (dest / "caption.txt").write_text(d["caption"], encoding="utf-8")
    (dest / "content.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    readme = [f"# {title}", "", f"Формат: {KIND_RU[d['kind']]}. Одобрено в Telegram "
              f"{dt.datetime.now(TZ):%d.%m.%Y %H:%M} (Алматы).", "", "## Подпись", "", d["caption"]]
    if skipped:
        readme += ["", "Видео больше 20 МБ — в репозиторий не добавлено, лежит на компьютере завода: "
                   + ", ".join(str(x) for x in skipped)]
    (dest / "README.md").write_text("\n".join(readme) + "\n", encoding="utf-8")
    to_git += [dest / "caption.txt", dest / "content.json", dest / "README.md"]
    try:
        with lock:
            git("pull", "--rebase", "--autostash", "-q")
            git("add", *[str(f) for f in to_git])
            git("commit", "-q", "-m", f"Approved via Telegram: {name}")
            git("push", "-q")
        url = f"https://github.com/avtomaster24bot-lab/ai-content-zavod/tree/main/content/approved/{name}"
        note = ("\n\n⚠️ Видео больше 20 МБ, в GitHub не положил — оно на компьютере завода." if skipped else "")
        send(chat, f"✅ Сохранено в репозиторий:\n{url}{note}\n\nМузыку и публикацию в Instagram делаешь сам.", MENU)
    except Exception as e:
        log(f"git error: {e}")
        send(chat, f"Файлы сохранены на компьютере ({dest.relative_to(ROOT)}), но отправить в GitHub не вышло.", MENU)


def handle_callback(cq):
    chat = cq["message"]["chat"]["id"]
    data = cq.get("data", "")
    try:
        tg("answerCallbackQuery", callback_query_id=cq["id"])
    except Exception:
        pass
    st = load_state()
    if data == "radar":
        do_radar(chat)
    elif data == "howto":
        send(chat, "Напиши одним сообщением:\nтема: …, формат: карусель / Reels / stories")
    elif data == "approved":
        files = sorted(APPROVED.glob("*.md"))[-10:]
        send(chat, "Одобренные (последние 10):\n" + ("\n".join("• " + f.name for f in files) if files else "пока пусто"), MENU)
    elif data.startswith("t:"):
        r = next((x for x in st.get("topics", []) if x["n"] == data[2:]), None)
        if not r:
            send(chat, "Темы устарели — запусти радар заново.", MENU); return
        topic = "\n".join(f"{k}: {r.get(k, '')}" for k in ["topic", "hook", "angle", "stage", "source", "cta"])
        make_draft(chat, topic, fmt_norm(r.get("format", "")))
    elif data[:2] in ("a:", "r:", "e:"):
        did = data[2:]
        d = st["drafts"].get(did)
        if not d or "kind" not in d:
            send(chat, "Этот черновик из старой версии бота. Выбери тему заново — теперь пришлю готовый контент.", MENU); return
        if data.startswith("a:"):
            approve(chat, did)
        elif data.startswith("r:"):
            make_draft(chat, d["topic"], d["format"], "Напиши заново, другим заходом и другими словами. Не повторяй предыдущую версию.")
        else:
            with lock:
                st = load_state(); st["await_edit"] = did; save_state(st)
            send(chat, "✏️ Напиши, что поправить (одним сообщением). Например: «короче, убери слайд 6, хук жёстче».")


TOPIC_RE = re.compile(r"тема\s*:\s*(?P<t>.+?)(?:[,;\n]\s*формат\s*:\s*(?P<f>.+))?$", re.I | re.S)


def handle_message(msg):
    chat = msg["chat"]["id"]
    text = (msg.get("text") or "").strip()
    st = load_state()
    if text.startswith("/start") or text.startswith("/menu") or text.startswith("/help"):
        send(chat, HELP, MENU); return
    if text.startswith("/radar"):
        do_radar(chat); return
    if st.get("await_edit"):
        did = st["await_edit"]
        with lock:
            st = load_state(); st["await_edit"] = None; save_state(st)
        d = st["drafts"].get(did)
        if d:
            prev = json.dumps(d.get("data"), ensure_ascii=False) if d.get("data") else d.get("text", "")
            extra = ("Правка редактора — выполни точно: " + text + "\n\nПредыдущая версия (исправь её, "
                     "а не пиши с нуля, сохрани всё, что правка не трогает):\n" + prev)
            make_draft(chat, d["topic"], d["format"], extra)
            return
    m = TOPIC_RE.search(text)
    if m:
        make_draft(chat, m.group("t").strip(), fmt_norm((m.group("f") or "").strip()))
        return
    send(chat, "Не понял. Нажми кнопку или напиши:\nтема: …, формат: карусель / Reels / stories", MENU)


def worker(fn, arg, chat):
    try:
        fn(arg)
    except zavod.AgnesError as e:
        log(f"Agnes error: {e}")
        try:
            send(chat, "😕 Agnes сейчас не ответил как надо. Попробуй ещё раз через минуту.\n(" + str(e)[:200] + ")", MENU)
        except Exception:
            pass
    except Exception as e:
        log("error: " + "".join(traceback.format_exception_only(type(e), e)).strip())
        try:
            send(chat, "😕 Что-то пошло не так. Попробуй ещё раз.", MENU)
        except Exception:
            pass


def main():
    if not TOKEN:
        sys.exit("Нет TELEGRAM_BOT_TOKEN")
    WORK.mkdir(exist_ok=True)
    me = tg("getMe")
    log(f"бот @{me.get('username')} запущен; владелец: {'задан' if owner() else 'ещё нет (ждём /start)'}")
    st = load_state()
    offset = st.get("offset", 0)
    while True:
        try:
            ups = tg("getUpdates", _timeout=70, offset=offset, timeout=50,
                     allowed_updates=["message", "callback_query"])
        except Conflict as e:
            log(f"409 Conflict: этим ботом уже пользуется другой процесс/вебхук. Останавливаюсь. {e}")
            sys.exit(EXIT_CONFLICT)
        except Exception as e:
            log(f"getUpdates: {e}"); time.sleep(5); continue
        for u in ups:
            offset = u["update_id"] + 1
            msg = u.get("message")
            cq = u.get("callback_query")
            frm = (msg or cq or {}).get("from", {})
            chat = (msg or {}).get("chat", {}).get("id") or ((cq or {}).get("message") or {}).get("chat", {}).get("id")
            if not chat:
                continue
            own = owner()
            if own is None and msg and (msg.get("text") or "").startswith("/start") and msg["chat"]["type"] == "private":
                OWNER_FILE.write_text(str(chat)); os.chmod(OWNER_FILE, 0o600)
                own = chat
                log("владелец назначен")
            if chat != own:
                log(f"игнорирую чужой чат (id …{str(chat)[-3:]})")
                continue
            log(f"апдейт: {'кнопка ' + cq.get('data', '') if cq else 'сообщение'}")
            if cq:
                threading.Thread(target=worker, args=(handle_callback, cq, chat), daemon=True).start()
            else:
                threading.Thread(target=worker, args=(handle_message, msg, chat), daemon=True).start()
        with lock:
            st = load_state(); st["offset"] = offset; save_state(st)


if __name__ == "__main__":
    main()
