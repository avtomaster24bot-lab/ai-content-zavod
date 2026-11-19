# Конструктор ИИ-контент-завода

Пошаговый статический сайт на русском: как с нуля (Алматы, без аудитории) собрать личный бренд про нейросети, продать первые пилоты «2 агента под ключ», снять контент без лица и только потом включить n8n.

Живая страница (GitHub Pages): https://avtomaster24bot-lab.github.io/ai-content-zavod/

## Что внутри

- `index.html` — конструктор: карта завода, лестница продуктов, бюджет, 11 этапов, словарь, вопросы, источники
- `css/styles.css`, `js/script.js` — оформление и галочки в localStorage
- `img/` — схемы SVG, явно подписанные «Схема» (это не скриншоты приложений)
- `workflows/sheets-llm-telegram.json` — учебный сценарий n8n: Google Таблица → черновик через API модели → проверка стиля → сообщение в Telegram. **В Instagram сам не публикует.** Перед запуском замените `REPLACE_…` и привяжите свои учётные данные. Ключей в файле нет.

## Как открыть локально

Откройте `index.html` в браузере или подайте папку любым статическим сервером. Сборки нет.

## Важно

Цены внутри помечены как предложения. Лимиты и тарифы сверялись по официальным страницам 16 ноября 2026 (Азия/Алматы) и могут измениться. Сайт не является офертой и не заменяет юридическую или налоговую консультацию.

## Агенты и неделя 1

- Инструкции: [`agents/radar.md`](agents/radar.md), [`agents/avtor.md`](agents/avtor.md). Страница: https://avtomaster24bot-lab.github.io/ai-content-zavod/agents/
- Пакет контента 16–22 ноября 2026: [`content/week1/`](content/week1/). Страница: https://avtomaster24bot-lab.github.io/ai-content-zavod/content/week1/
- Обложки каруселей (1080×1350) и аватар (1080×1080): `content/week1/img/`


## Запуск через Agnes API

С недели 2 агенты запускаются не копированием промта в чат, а скриптом [`tools/zavod.py`](tools/zavod.py) через [Agnes AI API](https://wiki.agnes-ai.com/en/docs/overview) (OpenAI-совместимый, `https://apihub.agnes-ai.com/v1`). Нужен Python 3 и `pip install pillow`.

1. Ключ кладём **только в переменную окружения**, не в файлы и не в репозиторий:
   - Windows (PowerShell): `$env:AGNES_API_KEY="ваш_ключ"`
   - macOS / Linux: `export AGNES_API_KEY="ваш_ключ"`
2. Команды (из папки репозитория):

```
# Радар: 10 тем на неделю. Свежие новости положите в файл фактов со ссылками — у Agnes нет интернета.
python tools/zavod.py radar --week "23–29 ноября 2026" --published content/week2/_published.md \
  --state "Что сейчас в сборке" --facts content/week2/_facts.md -o content/week2/00-radar-otchet.md

# Автор: готовый текст по теме и формату (сам перепишет до 3 раз, если найдёт запрещённые слова)
python tools/zavod.py avtor --topic "n8n пока не ставлю. Сначала — таблица." --format "карусель 8 слайдов" -o post.md

# Обложка 1080×1350: фон от agnes-image-2.5-flash + заголовок PT Serif
python tools/zavod.py cover --prompt "ноутбук с терминалом" --title "Одна команда.\nДва агента." --label "reels" -o cover.jpg

# Проверка текста по библии стиля
python tools/zavod.py check post.md
```

Модели по умолчанию: тексты — `agnes-2.5-pro` (лучше всех держит библию стиля в нашем тесте; платная, копейки за пост), картинки — `agnes-image-2.5-flash`. Поменять: переменные `ZAVOD_TEXT_MODEL` (например, бесплатная `agnes-3.0-flash`) и `ZAVOD_IMAGE_MODEL`.

Пакет недели 2, собранный так: [`content/week2/`](content/week2/). Страница: https://avtomaster24bot-lab.github.io/ai-content-zavod/content/week2/

## Telegram-бот завода

[`tools/tg_bot.py`](tools/tg_bot.py) — завод в Telegram, присылает **готовый контент**:

- «🎯 Темы на неделю» — радар даёт 10 тем; нажмите на тему или напишите `тема: …, формат: карусель / Reels / stories`.
- **Карусель** — альбом слайдов 1080×1350 (обложка с картинкой Agnes, внутренние слайды с номером), zip в полном качестве и подпись.
- **Reels** — MP4 1080×1920, 30–45 с: голос, крупные субтитры по фразам, хук в первые 2 секунды, финальная карточка «Напишите «завод»». Музыки нет — её добавляют в Instagram.
- **Stories** — 3–5 кадров 1080×1920 альбомом.
- Кнопки: «✅ Одобрить» (файлы и подпись → [`content/approved/<дата-формат-тема>/`](content/approved/) и push; видео больше 20 МБ остаётся только на компьютере), «🔁 Переделать», «✏️ Правка».

Как устроено: [`tools/fabrika_text.py`](tools/fabrika_text.py) пишет текст в JSON в три прохода: автор, редактор «живости», корректор. Потом идут проверка по библии стиля и повтор при ошибках. [`tools/fabrika_render.py`](tools/fabrika_render.py) рисует слайды и кадры (PT Serif + Manrope) и монтирует Reels через ffmpeg. Модель текста — `agnes-3.0-flash` (бесплатная, переменная `ZAVOD_FABRIKA_MODEL`).

Голос: сначала edge-tts (`ru-RU-DmitryNeural`), если он недоступен — офлайн Piper (`ru_RU-dmitri-medium`, файлы модели в `~/.zavod_tts/`). Установка: `pip install pillow edge-tts piper-tts`, плюс `ffmpeg`.

Запуск: задать `TELEGRAM_BOT_TOKEN` и `AGNES_API_KEY` в окружении и выполнить `tools/run_bot.sh` (фон, автоперезапуск, лог в `/workspace/zavod-bot.log`). Первый, кто напишет боту `/start`, становится владельцем; остальных бот игнорирует.
