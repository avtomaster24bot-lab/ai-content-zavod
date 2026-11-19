#!/usr/bin/env bash
# Запуск Telegram-бота завода в фоне с автоперезапуском.
# Нужны переменные окружения TELEGRAM_BOT_TOKEN и AGNES_API_KEY (в файлы их не пишем).
# Лог: ${ZAVOD_BOT_LOG:-/workspace/zavod-bot.log}. Остановить: kill "$(cat ~/.zavod_bot.pid)"
set -u
DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG="${ZAVOD_BOT_LOG:-/workspace/zavod-bot.log}"
PIDFILE="$HOME/.zavod_bot.pid"

if [ -z "${TELEGRAM_BOT_TOKEN:-}" ] || [ -z "${AGNES_API_KEY:-}" ]; then
  echo "Нужны переменные TELEGRAM_BOT_TOKEN и AGNES_API_KEY" >&2; exit 1
fi
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "Бот уже запущен, PID $(cat "$PIDFILE")"; exit 0
fi

supervise() {
  while true; do
    python3 -u "$DIR/tools/tg_bot.py" >>"$LOG" 2>&1
    code=$?
    if [ "$code" -eq 3 ]; then
      echo "$(date '+%F %T') 409 Conflict — бот занят другим процессом, перезапуск остановлен" >>"$LOG"; break
    fi
    echo "$(date '+%F %T') бот завершился (код $code), перезапуск через 10 с" >>"$LOG"
    sleep 10
  done
  rm -f "$PIDFILE"
}

nohup bash -c "$(declare -f supervise); DIR='$DIR' LOG='$LOG' PIDFILE='$PIDFILE' supervise" >/dev/null 2>&1 &
echo $! >"$PIDFILE"
echo "Бот запущен, PID $(cat "$PIDFILE"), лог: $LOG"
