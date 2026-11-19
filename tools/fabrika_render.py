"""Рендер готового контента: слайды карусели, кадры stories, Reels MP4 (голос + субтитры)."""
import concurrent.futures as cf, json, os, re, shutil, subprocess, sys, time, urllib.request, zipfile, wave
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter, ImageFont

import zavod

SERIF = "/usr/share/fonts/truetype/sand-box/google/PT Serif/PTSerif-Bold.ttf"
SANS = "/usr/share/fonts/truetype/sand-box/google/Manrope/Manrope-VariableFont_wght.ttf"
CREAM, GREEN, TEAL, ORANGE, INK = (244, 239, 228), (24, 44, 38), (31, 102, 92), (214, 146, 40), (40, 46, 42)
HANDLE = os.environ.get("ZAVOD_HANDLE", "ИИ-завод · Алматы")
NBSP = "\u00a0"
SHORT = r"(?:в|во|и|с|со|к|ко|о|об|у|а|я|на|не|ни|за|по|до|из|от|без|для|что|как|но|мой|моя|это|вы|мы|он|под|над|про|при|или|вам|мне|все|уже)"


def sans(size, weight="Medium"):
    f = ImageFont.truetype(SANS, size)
    try:
        f.set_variation_by_name(weight)
    except Exception:
        pass
    return f


def serif(size):
    return ImageFont.truetype(SERIF, size)


def typograph(t):
    """Неразрывные пробелы после коротких слов и перед тире — чтобы не висели в конце строки."""
    t = re.sub(r"\s+", " ", (t or "").strip())
    t = re.sub(rf"(?i)(^|\s)({SHORT})\s", lambda m: m.group(1) + m.group(2) + NBSP, t)
    t = re.sub(rf"(?i)(^|\s)({SHORT})\s", lambda m: m.group(1) + m.group(2) + NBSP, t)
    t = t.replace(" —", NBSP + "—").replace(" –", NBSP + "–")
    t = re.sub(r"\s(же|ли|бы)\b", NBSP + r"\1", t)
    return t


def wrap(text, font, max_w, orphan=True):
    d = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    lines = []
    for para in typograph(text).split("\n"):
        cur = ""
        for w in para.split(" "):
            t = (cur + " " + w).strip()
            if not cur or d.textlength(t.replace(NBSP, " "), font=font) <= max_w:
                cur = t
            else:
                lines.append(cur); cur = w
        if cur:
            lines.append(cur)
    # не оставляем одно слово на последней строке, если можно перенести соседнее
    if orphan and len(lines) >= 2 and " " not in lines[-1] and lines[-2].count(" ") >= 1:
        head, last_w = lines[-2].rsplit(" ", 1)
        cand = last_w + " " + lines[-1]
        if d.textlength(cand.replace(NBSP, " "), font=font) <= max_w:
            lines[-2], lines[-1] = head, cand
    return [l.replace(NBSP, " ") for l in lines]


def balanced(text, font, max_w):
    """Строки примерно одинаковой длины (для субтитров и хука): сужаем ширину, пока не прибавится строка."""
    base = wrap(text, font, max_w, orphan=False)
    n, w, best = len(base), max_w, base
    while w > max_w * 0.5:
        w -= 20
        cand = wrap(text, font, w, orphan=False)
        if len(cand) > n:
            break
        best = cand
    return best


def fit(text, make_font, max_w, max_h, sizes, spacing=1.25, max_lines=99, balance=False):
    """Подбирает самый крупный кегль, при котором текст влезает в рамку без разрыва слов."""
    d = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    for s in sizes:
        f = make_font(s)
        lines = balanced(text, f, max_w) if balance else wrap(text, f, max_w)
        too_wide = any(d.textlength(l, font=f) > max_w for l in lines)
        h = len(lines) * s * spacing
        if h <= max_h and not too_wide and len(lines) <= max_lines:
            return f, lines, s
    f = make_font(sizes[-1])
    return f, wrap(text, f, max_w), sizes[-1]


def draw_lines(d, xy, lines, font, size, fill, spacing=1.25, anchor_x="l", width=None):
    x, y = xy
    for l in lines:
        if anchor_x == "c":
            lw = d.textlength(l, font=font)
            d.text((x + (width - lw) / 2, y), l, font=font, fill=fill)
        else:
            d.text((x, y), l, font=font, fill=fill)
        y += int(size * spacing)
    return y


def cover_crop(img, W, H):
    img = img.convert("RGB")
    r = max(W / img.width, H / img.height)
    img = img.resize((round(img.width * r), round(img.height * r)), Image.LANCZOS)
    l, t = (img.width - W) // 2, (img.height - H) // 2
    return img.crop((l, t, l + W, t + H))


def footer(d, W, H, n=None, total=None, dark=False, swipe=False):
    col = CREAM if dark else TEAL
    d.text((80, H - 92), HANDLE, font=sans(28, "SemiBold"), fill=col)
    if n is not None:
        t = f"{n}/{total}"
        f = sans(28, "SemiBold")
        d.text((W - 80 - d.textlength(t, font=f), H - 92), t, font=f, fill=col)
    if swipe:
        f = sans(30, "Bold")
        t = "листайте →"
        d.text((W - 80 - d.textlength(t, font=f), H - 92), t, font=f, fill=ORANGE)


# ---------------- карусель ----------------

def carousel_cover(bg, title, subtitle, total):
    W, H = 1080, 1350
    img = cover_crop(bg, W, H)
    # мягкая подложка под текст сверху, чтобы читалось на любом фоне
    over = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(over)
    for y in range(0, 760):
        a = int(235 * max(0, 1 - y / 760) ** 0.8)
        od.line([(0, y), (W, y)], fill=CREAM + (a,))
    img = Image.alpha_composite(img.convert("RGBA"), over).convert("RGB")
    d = ImageDraw.Draw(img)
    d.text((80, 80), "КАРУСЕЛЬ · ИИ-ЗАВОД", font=sans(28, "Bold"), fill=TEAL)
    d.rectangle((80, 124, 280, 130), fill=ORANGE)
    f, lines, s = fit(title, serif, 920, 430, list(range(104, 60, -4)), 1.15, max_lines=4)
    y = draw_lines(d, (80, 170), lines, f, s, GREEN, 1.15)
    if subtitle:
        f2, l2, s2 = fit(subtitle, lambda z: sans(z, "Medium"), 900, 150, [44, 40, 36, 32], 1.3)
        draw_lines(d, (80, y + 24), l2, f2, s2, INK, 1.3)
    # плашка внизу для подписи и «листайте»
    d.rectangle((0, H - 130, W, H), fill=CREAM)
    footer(d, W, H, swipe=True)
    return img


def carousel_slide(n, total, heading, body, last=False):
    W, H = 1080, 1350
    bg, fg, sub = (GREEN, CREAM, (215, 225, 218)) if last else (CREAM, GREEN, INK)
    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)
    d.text((80, 70), f"{n:02d}", font=serif(110), fill=ORANGE)
    top, bottom = 250, H - 150
    f, lines, s = fit(heading, serif, 920, 400, list(range(100, 52, -4)), 1.15, max_lines=4)
    hh = int(len(lines) * s * 1.15)
    room = bottom - top - hh - 60
    f2, l2, s2 = fit(body, lambda z: sans(z, "Medium"), 900, room, list(range(64, 32, -2)), 1.4)
    bh = int(len(l2) * s2 * 1.4)
    block = hh + 60 + bh
    y = top + max(0, (bottom - top - block) // 2 - 30)
    d.rectangle((80, y - 40, 200, y - 34), fill=ORANGE)
    y = draw_lines(d, (80, y), lines, f, s, fg, 1.15)
    draw_lines(d, (80, y + 60), l2, f2, s2, sub, 1.4)
    footer(d, W, H, n, total, dark=last)
    return img


def render_carousel(data, out_dir, log=print):
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    slides = data["slides"]
    total = len(slides) + 1
    try:
        bg = zavod.image(data.get("cover_visual") or data["title"])
    except Exception as e:
        log(f"обложка: Agnes не дал картинку ({e}), рисую фирменный фон")
        bg = Image.new("RGB", (1080, 1350), CREAM)
    files = []
    p = out / "01.jpg"; carousel_cover(bg, data["title"], data.get("subtitle", ""), total).save(p, quality=92); files.append(p)
    for i, s in enumerate(slides, 2):
        p = out / f"{i:02d}.jpg"
        carousel_slide(i, total, s["heading"], s.get("body", ""), last=(i == total)).save(p, quality=92)
        files.append(p)
    return files


# ---------------- stories ----------------

def story_frame(bg, n, total, title, text, last=False):
    W, H = 1080, 1920
    img = cover_crop(bg, W, H) if bg else Image.new("RGB", (W, H), CREAM)
    over = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(over)
    if last:
        od.rectangle((0, 0, W, H), fill=GREEN + (215,))
    else:
        for y in range(0, 1150):
            a = int(240 * max(0, 1 - y / 1150) ** 0.7)
            od.line([(0, y), (W, y)], fill=CREAM + (a,))
    img = Image.alpha_composite(img.convert("RGBA"), over).convert("RGB")
    d = ImageDraw.Draw(img)
    fg, sub = (CREAM, (225, 232, 226)) if last else (GREEN, INK)
    # индикатор кадров
    seg = (W - 160 - (total - 1) * 12) / total
    for i in range(total):
        x = 80 + i * (seg + 12)
        d.rounded_rectangle((x, 150, x + seg, 160), 5, fill=ORANGE if i < n else (210, 200, 180))
    y0 = 270 if not last else 560
    f, lines, s = fit(title, serif, 880, 560, list(range(120, 64, -4)), 1.12, max_lines=5)
    th = int(len(lines) * s * 1.12)
    l2 = []
    if text:
        f2, l2, s2 = fit(text, lambda z: sans(z, "SemiBold"), 880, 460, list(range(64, 38, -2)), 1.35)
    bh = th + (40 + int(len(l2) * s2 * 1.35) if l2 else 0)
    if not last:  # подложка-карточка: текст читается на любом фоне
        card = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(card).rounded_rectangle((50, y0 - 40, W - 50, y0 + bh + 40), 36, fill=CREAM + (236,))
        img = Image.alpha_composite(img.convert("RGBA"), card).convert("RGB")
        d = ImageDraw.Draw(img)
        seg = (W - 160 - (total - 1) * 12) / total
        for i in range(total):
            x = 80 + i * (seg + 12)
            d.rounded_rectangle((x, 150, x + seg, 160), 5, fill=ORANGE if i < n else (210, 200, 180))
    y = draw_lines(d, (100, y0), lines, f, s, fg, 1.12)
    if l2:
        draw_lines(d, (100, y + 40), l2, f2, s2, sub, 1.35)
    if last:
        bw, bh = 640, 120
        x, yb = (W - bw) // 2, H - 520
        d.rounded_rectangle((x, yb, x + bw, yb + bh), 60, fill=ORANGE)
        t = "Написать «завод»"
        ft = sans(46, "Bold")
        d.text((x + (bw - d.textlength(t, font=ft)) / 2, yb + 32), t, font=ft, fill=GREEN)
    d.text((80, 186), HANDLE, font=sans(30, "SemiBold"), fill=ORANGE if last else TEAL)
    return img


def render_stories(data, out_dir, log=print):
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    frames = data["frames"][:5]
    with cf.ThreadPoolExecutor(5) as ex:
        bgs = list(ex.map(lambda f: _safe_image(f.get("visual") or f["title"], "9:16", log), frames))
    files = []
    for i, (f, bg) in enumerate(zip(frames, bgs), 1):
        p = out / f"story-{i}.jpg"
        story_frame(bg, i, len(frames), f["title"], f.get("text", ""), last=(i == len(frames))).save(p, quality=92)
        files.append(p)
    return files


import threading
_IMG_SEM = threading.Semaphore(int(os.environ.get("ZAVOD_IMG_PARALLEL", "2")))


TEXTY = re.compile(r"\b(sign|signs|text|texts|words?|letters?|caption|label|title|headline|message|messages|chat|notes?|"
                   r"writing|written|handwrit\w*|typed|typing|code|terminal|document|page|list|menu|app|ui|interface|"
                   r"screen showing|displaying|reads?|saying|logo|brand|money|cash|banknotes?|dollars?)\b", re.I)


def clean_visual(p):
    """Убираем из описания всё, что провоцирует модель рисовать «буквы-каракули»."""
    p = TEXTY.sub("", p or "")
    p = re.sub(r"\s+", " ", p).strip(" ,.")
    return (p + ". Absolutely no text, letters or symbols anywhere in the image; screens and paper are blank or out of focus.").strip()


def _safe_image(prompt, ratio, log, styled=True, prefix=None):
    """Картинка Agnes с ограничением параллельности и паузами при 429 (лимит бесплатного ключа)."""
    prompt = clean_visual(prompt)
    for attempt, pause in enumerate([0, 8, 15, 25, 40]):
        time.sleep(pause)
        try:
            with _IMG_SEM:
                if prefix:
                    return zavod.image(prefix + prompt, ratio=ratio, styled=False)
                return zavod.image(prompt, ratio=ratio, styled=styled)
        except Exception as e:
            msg = str(e)
            if "429" in msg or "rate" in msg.lower() or "503" in msg:
                log(f"картинка: лимит Agnes, жду (попытка {attempt + 1})")
                continue
            log(f"картинка не получилась ({msg[:160]})")
            return None
    return None


# ---------------- Reels ----------------

PIPER_DIR = Path(os.environ.get("ZAVOD_TTS_DIR", str(Path.home() / ".zavod_tts")))
PIPER_VOICE = os.environ.get("ZAVOD_PIPER_VOICE", "ru_RU-dmitri-medium")
EDGE_VOICE = os.environ.get("ZAVOD_EDGE_VOICE", "ru-RU-DmitryNeural")
REELS_STYLE = ("Cinematic vertical photo, shallow depth of field, warm evening light, muted teal and amber tones, "
               "realistic, calm, high detail. No text, no letters, no logos, no faces, no money or banknotes. "
               "Any screens are out of focus or show only soft abstract light, never readable text or app UI. Scene: ")


def _edge(text, path):
    import asyncio, edge_tts
    mp3 = str(path) + ".mp3"
    asyncio.run(edge_tts.Communicate(text, EDGE_VOICE, rate="+5%").save(mp3))
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", mp3, "-ar", "24000", "-ac", "1", str(path)], check=True)
    os.remove(mp3)


def _piper(text, path):
    r = subprocess.run([sys.executable, "-m", "piper", "-m", str(PIPER_DIR / f"{PIPER_VOICE}.onnx"),
                        "--length-scale", "0.95", "--sentence-silence", "0.1", "-f", str(path)],
                       input=text.encode(), capture_output=True)
    if r.returncode != 0:
        raise RuntimeError("озвучка не получилась: " + r.stderr.decode()[-300:])


def spoken(t):
    """Текст для голоса: символы и диапазоны — словами (субтитры остаются как есть)."""
    t = t.replace("«", "").replace("»", "").replace("₸", " тенге").replace("тыс.", "тысяч")
    t = re.sub(r"\b50\s*/\s*50\b", "пятьдесят на пятьдесят", t)
    t = re.sub(r"(\d+)\s*[–—-]\s*(\d+)", r"от \1 до \2", t)
    t = re.sub(r"(\d+)\s*%", r"\1 процентов", t)
    t = t.replace("API", "эй-пи-ай").replace("Kaspi", "Каспи").replace("Reels", "рилс").replace("Instagram", "инстаграм")
    t = t.replace("n8n", "эн-восемь-эн").replace("Agnes", "Агнес").replace("ChatGPT", "чат джи-пи-ти")
    return re.sub(r"\s+", " ", t).strip()


def tts_all(phrases, tmp, log=print):
    """Озвучивает все фразы ОДНИМ голосом: edge-tts (Microsoft, онлайн), если он стабильно отвечает, иначе Piper (офлайн)."""
    paths = [tmp / f"ph{i:02d}.wav" for i in range(len(phrases))]
    if os.environ.get("ZAVOD_TTS", "auto") != "piper":
        try:
            for i, (p, w) in enumerate(zip(phrases, paths)):
                tries = 1 if i == 0 else 3  # первая фраза — быстрая проверка, доступен ли сервис
                for k in range(tries):
                    try:
                        _edge(p, w); break
                    except Exception:
                        if k == tries - 1:
                            raise
                        time.sleep(1.5)
            return paths, "edge-tts " + EDGE_VOICE
        except Exception as e:
            log(f"edge-tts недоступен ({type(e).__name__}), озвучиваю Piper")
    for p, w in zip(phrases, paths):
        _piper(p, w)
    return paths, "piper " + PIPER_VOICE


def wav_dur(p):
    with wave.open(str(p)) as w:
        return w.getnframes() / w.getframerate()


def sub_png(text, path, W=1080):
    """Субтитр: крупный белый текст на тёмной полупрозрачной плашке."""
    f, lines, s = fit(text, lambda z: sans(z, "ExtraBold"), 900, 330, list(range(78, 46, -2)), 1.18, max_lines=3, balance=True)
    h = int(len(lines) * s * 1.18) + 64
    img = Image.new("RGBA", (W, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    widths = [d.textlength(l, font=f) for l in lines]
    bw = int(max(widths)) + 80
    x0 = (W - bw) // 2
    d.rounded_rectangle((x0, 0, x0 + bw, h), 34, fill=(16, 28, 24, 215))
    y = 26
    for l, lw in zip(lines, widths):
        d.text(((W - lw) / 2, y), l, font=f, fill=(255, 250, 240), stroke_width=1, stroke_fill=(16, 28, 24))
        y += int(s * 1.18)
    img.save(path)


def hook_png(text, path, W=1080, H=1920):
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    f, lines, s = fit(text.upper(), serif, 920, 700, list(range(130, 70, -4)), 1.1, max_lines=4, balance=True)
    h = int(len(lines) * s * 1.1)
    y = (H - h) // 2 - 120
    d.rounded_rectangle((60, y - 60, W - 60, y + h + 60), 40, fill=CREAM + (238,))
    d.rectangle((W // 2 - 80, y - 60, W // 2 + 80, y - 52), fill=ORANGE)
    draw_lines(d, (80, y), lines, f, s, GREEN, 1.1, anchor_x="c", width=920)
    img.save(path)


def cta_png(path, W=1080, H=1920):
    img = Image.new("RGB", (W, H), GREEN)
    d = ImageDraw.Draw(img)
    f, lines, s = fit("Хотите такой же завод у себя?", serif, 900, 520, list(range(110, 64, -4)), 1.12)
    h = int(len(lines) * s * 1.12)
    y = (H - h) // 2 - 220
    d.rectangle((W // 2 - 80, y - 60, W // 2 + 80, y - 52), fill=ORANGE)
    draw_lines(d, (90, y), lines, f, s, CREAM, 1.12, anchor_x="c", width=900)
    bw, bh = 760, 140
    x, yb = (W - bw) // 2, y + h + 90
    d.rounded_rectangle((x, yb, x + bw, yb + bh), 70, fill=ORANGE)
    t = "Напишите «завод» в директ"
    ft = sans(46, "ExtraBold")
    d.text((x + (bw - d.textlength(t, font=ft)) / 2, yb + 40), t, font=ft, fill=GREEN)
    ft2 = sans(36, "SemiBold")
    d.text(((W - d.textlength(HANDLE, font=ft2)) / 2, yb + bh + 60), HANDLE, font=ft2, fill=(200, 214, 205))
    img.save(path)


def agnes_video(prompt, seconds=6, timeout=150, log=print):
    """Пробует Agnes video 2.5 flash (9:16). Возвращает байты mp4 или None."""
    t0 = time.time()
    body = {"model": "agnes-video-2.5-flash", "prompt": REELS_STYLE + prompt, "seconds": str(seconds),
            "mode": "text", "size": "720P", "aspect_ratio": "9:16"}
    try:
        r = zavod._post("/videos", body, timeout=60, retries=1)
    except Exception as e:
        log(f"видео Agnes: не принято ({str(e)[:120]})")
        return None
    vid = r.get("video_id") or r.get("id")
    base = zavod.BASE.rsplit("/v1", 1)[0]
    while time.time() - t0 < timeout:
        time.sleep(4)
        try:
            req = urllib.request.Request(f"{base}/agnesapi?video_id={vid}&model_name=agnes-video-2.5-flash",
                                         headers={"Authorization": "Bearer " + zavod._key()})
            s = json.load(urllib.request.urlopen(req, timeout=30))
        except Exception:
            continue
        if s.get("status") == "completed" and s.get("url"):
            with urllib.request.urlopen(s["url"], timeout=120) as f:
                return f.read()
        if s.get("status") == "failed":
            log(f"видео Agnes: failed {s.get('error')}")
            return None
    log("видео Agnes: не успело за отведённое время")
    return None


def _run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg: " + r.stderr[-600:])


def scene_clip(src_img, src_video, dur, out, idx):
    """Клип сцены 1080×1920 длительностью dur: видео Agnes (зациклено) или картинка с плавным зумом."""
    fps = 30
    if src_video:
        _run(["ffmpeg", "-y", "-loglevel", "error", "-stream_loop", "-1", "-i", str(src_video), "-t", f"{dur:.2f}",
              "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=30,format=yuv420p",
              "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", str(out)])
        return
    n = max(1, int(dur * fps))
    zin = idx % 2 == 0
    z = f"1+0.10*on/{n}" if zin else f"1.10-0.10*on/{n}"
    # плавный Ken Burns: картинка масштабируется до 1620×2880, затем zoompan в 1080×1920
    _run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-i", str(src_img), "-t", f"{dur:.2f}",
          "-vf", f"scale=1620:2880:force_original_aspect_ratio=increase,crop=1620:2880,"
                 f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={n}:s=1080x1920:fps={fps},format=yuv420p",
          "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", str(out)])


def render_reels(data, out_dir, log=print, progress=None, use_video=True):
    progress = progress or (lambda m: None)
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    tmp = out / "_tmp"; tmp.mkdir(exist_ok=True)
    scenes = data["scenes"][:5]
    phrases = [(si, p.strip()) for si, s in enumerate(scenes) for p in s.get("phrases", []) if p.strip()]

    progress("🖼 Делаю картинки и пробую видео Agnes…")
    with cf.ThreadPoolExecutor(8) as ex:
        img_f = [ex.submit(_safe_image, s.get("visual", ""), "9:16", log, False, REELS_STYLE) for s in scenes]
        vid_f = [ex.submit(agnes_video, clean_visual(s.get("visual", "")), 6, 150, log) for s in scenes] if use_video else []
        progress("🎙 Делаю озвучку…")
        wavs, engine = tts_all([spoken(p) for _, p in phrases], tmp, log)
        engines = {engine}
        imgs = [f.result() for f in img_f]
        vids = [f.result() for f in vid_f] if vid_f else [None] * len(scenes)
    used_video = sum(1 for v in vids if v)
    log(f"озвучка: {','.join(engines)}; видео Agnes: {used_video}/{len(scenes)}")

    # укладываемся в 30–45 с: если речь длиннее ~43 с, слегка ускоряем голос (не больше чем в 1.18 раза)
    gap = 0.18
    speech = sum(wav_dur(w) for w in wavs) + gap * len(wavs) + 0.4
    if speech > 41.5:
        k = min(1.18, speech / 41.0)
        for w in wavs:
            f2 = w.with_name("fast_" + w.name)
            _run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(w), "-filter:a", f"atempo={k:.3f}", str(f2)])
            f2.replace(w)
        log(f"голос ускорен в {k:.2f} раза")
    # тайминги: фраза + пауза
    t, timeline = 0.4, []
    for (si, p), w in zip(phrases, wavs):
        d = wav_dur(w)
        timeline.append((si, p, t, t + d))
        t += d + gap
    cta_len = 2.2
    total = t + cta_len
    # аудио-дорожка
    concat = tmp / "voice.txt"
    lines = []
    sil = tmp / "sil.wav"
    _run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono", "-t", f"{gap}", str(sil)])
    lead = tmp / "lead.wav"
    _run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono", "-t", "0.4", str(lead)])
    norm = []
    for w in [lead] + [x for pair in zip(wavs, [sil] * len(wavs)) for x in pair]:
        n2 = tmp / ("n_" + w.name)
        if not n2.exists():
            _run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(w), "-ar", "22050", "-ac", "1", str(n2)])
        norm.append(n2)
    concat.write_text("".join(f"file '{p.name}'\n" for p in norm))
    voice = tmp / "voice.wav"
    _run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", str(voice)])

    progress("🎬 Монтирую…")
    # длительность каждой сцены = от начала её первой фразы до начала следующей сцены
    starts = {}
    for si, p, a, b in timeline:
        starts.setdefault(si, a)
    order = sorted(starts)
    bounds = []
    for k, si in enumerate(order):
        a = 0 if k == 0 else starts[si]
        b = starts[order[k + 1]] if k + 1 < len(order) else t
        bounds.append((si, a, b))
    clips = []
    for k, (si, a, b) in enumerate(bounds):
        src_img = tmp / f"scene{si}.jpg"
        im = imgs[si] if si < len(imgs) and imgs[si] is not None else Image.new("RGB", (1080, 1920), GREEN)
        bgimg = cover_crop(im, 1080, 1920).filter(ImageFilter.GaussianBlur(2.2))
        bgimg = Image.blend(bgimg, Image.new("RGB", bgimg.size, (10, 18, 15)), 0.18)
        bgimg.save(src_img, quality=92)
        src_vid = None
        if si < len(vids) and vids[si]:
            src_vid = tmp / f"scene{si}.mp4"; src_vid.write_bytes(vids[si])
        c = tmp / f"clip{k}.mp4"
        scene_clip(src_img, src_vid, b - a, c, k)
        clips.append(c)
    cta_img = tmp / "cta.png"; cta_png(cta_img)
    cta_clip = tmp / "cta.mp4"
    _run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-i", str(cta_img), "-t", f"{cta_len}",
          "-vf", "fps=30,format=yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", str(cta_clip)])
    clips.append(cta_clip)
    (tmp / "clips.txt").write_text("".join(f"file '{c.name}'\n" for c in clips))
    base = tmp / "base.mp4"
    _run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(tmp / "clips.txt"), "-c", "copy", str(base)])

    # оверлеи: хук (первые ~2.2 с, вместо субтитра первой фразы) и субтитры
    inputs, filters = ["-i", str(base), "-i", str(voice)], []
    hook = tmp / "hook.png"; hook_png(data.get("hook") or phrases[0][1], hook)
    inputs += ["-i", str(hook)]
    hook_end = max(2.2, timeline[0][3])
    filters.append(f"[0:v][2:v]overlay=0:0:enable='between(t,0,{hook_end:.2f})'[v0]")
    last = "v0"
    idx = 3
    for i, (si, p, a, b) in enumerate(timeline):
        if i == 0 and b <= hook_end + 0.01:
            continue
        sp = tmp / f"sub{i:02d}.png"; sub_png(p, sp)
        inputs += ["-i", str(sp)]
        a2 = max(a, hook_end) if i == 0 else a
        filters.append(f"[{last}][{idx}:v]overlay=0:H-h-420:enable='between(t,{a2:.2f},{b + gap:.2f})'[v{idx}]")
        last = f"v{idx}"; idx += 1
    final = out / "reels.mp4"
    _run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", ";".join(filters),
          "-map", f"[{last}]", "-map", "1:a", "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
          "-c:a", "aac", "-b:a", "160k", "-af", "apad", "-t", f"{total:.2f}", "-movflags", "+faststart", str(final)])
    # превью-кадр для обложки Reels
    thumb = out / "reels-cover.jpg"
    _run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "1.0", "-i", str(final), "-frames:v", "1", "-q:v", "3", str(thumb)])
    (out / "script.txt").write_text("\n".join(f"[{a:5.1f}–{b:5.1f}] {p}" for _, p, a, b in timeline), encoding="utf-8")
    shutil.rmtree(tmp, ignore_errors=True)
    return final, thumb, {"seconds": round(total, 1), "video_clips": used_video, "tts": ",".join(sorted(engines))}


def zip_files(files, path):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(f, Path(f).name)
    return path
