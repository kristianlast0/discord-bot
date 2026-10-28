#!/usr/bin/env bash
# Daily: keep yt-dlp current (YouTube changes often) and restart the bot if it changed.
set -euo pipefail
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PY="$APP_DIR/.venv/bin/python"
before="$("$PY" -m yt_dlp --version)"
"$HOME/.local/bin/uv" pip install --python "$PY" -U "yt-dlp[default,deno]"
after="$("$PY" -m yt_dlp --version)"
if [ "$before" != "$after" ]; then
  echo "yt-dlp $before -> $after, restarting bot"
  systemctl --user try-restart discord-music-bot.service
fi
