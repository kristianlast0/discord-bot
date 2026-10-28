#!/usr/bin/env bash
# Run on the machine that will host the bot (no sudo needed): sets up Python 3.12 + dependencies and the user services.
set -euo pipefail
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="$HOME/.local/bin:$PATH"

if ! command -v uv >/dev/null; then
  echo "Installing uv (Python manager) into ~/.local/bin"
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
[ -x "$APP_DIR/.venv/bin/python" ] || uv venv --python 3.12 "$APP_DIR/.venv"
uv pip install --python "$APP_DIR/.venv/bin/python" -U -r "$APP_DIR/requirements.txt"
mkdir -p "$APP_DIR/data" "$HOME/Music"
[ -f "$APP_DIR/.env" ] || { cp "$APP_DIR/.env.example" "$APP_DIR/.env"; chmod 600 "$APP_DIR/.env"; }

UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"
for unit in discord-music-bot.service discord-music-bot-update.service discord-music-bot-update.timer; do
  sed "s|@APP_DIR@|$APP_DIR|g" "$APP_DIR/deploy/$unit" > "$UNIT_DIR/$unit"
done
systemctl --user daemon-reload
systemctl --user enable --now discord-music-bot-update.timer
systemctl --user enable discord-music-bot.service

if [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" != "yes" ]; then
  echo "Note: to keep the bot running after you log out, run once: sudo loginctl enable-linger $USER"
fi

if grep -q '^DISCORD_TOKEN=.\+' "$APP_DIR/.env"; then
  systemctl --user restart discord-music-bot.service
  echo "Bot (re)started. Logs: journalctl --user -u discord-music-bot -f"
else
  echo "Put your bot token in $APP_DIR/.env, then: systemctl --user start discord-music-bot"
fi
