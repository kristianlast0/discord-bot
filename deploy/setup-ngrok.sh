#!/usr/bin/env bash
# Run on the laptop (no sudo): installs ngrok and runs it as a service, giving the web mixer a public https address.
# Usage: bash ~/discord-music-bot/deploy/setup-ngrok.sh <ngrok authtoken> [your-static-domain.ngrok-free.app]
# Without a static domain ngrok picks a new address each time it starts; the bot asks ngrok for it when making links.
set -euo pipefail
TOKEN="${1:?Pass your ngrok authtoken (dashboard.ngrok.com > Your Authtoken)}"
DOMAIN="${2:-}"
DOMAIN="${DOMAIN#https://}"
DOMAIN="${DOMAIN%/}"
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BIN="$HOME/.local/bin"
mkdir -p "$BIN"
if [ ! -x "$BIN/ngrok" ]; then
  echo "Downloading ngrok into $BIN"
  curl -fsSL https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-amd64.tgz | tar -xz -C "$BIN" ngrok
fi
"$BIN/ngrok" config add-authtoken "$TOKEN" >/dev/null

PORT="$(grep -E '^MIXER_PORT=' "$APP_DIR/.env" | cut -d= -f2)"
PORT="${PORT:-8765}"
PUBLIC=""
URL_FLAG=""
if [ -n "$DOMAIN" ]; then
  PUBLIC="https://$DOMAIN"
  URL_FLAG="--url=https://$DOMAIN"
fi
if grep -q '^PUBLIC_URL=' "$APP_DIR/.env"; then
  sed -i "s|^PUBLIC_URL=.*|PUBLIC_URL=$PUBLIC|" "$APP_DIR/.env"
else
  echo "PUBLIC_URL=$PUBLIC" >> "$APP_DIR/.env"
fi

UNIT="$HOME/.config/systemd/user/discord-music-bot-ngrok.service"
sed -e "s|@PORT@|$PORT|" -e "s|@URL_FLAG@|$URL_FLAG|" "$APP_DIR/deploy/discord-music-bot-ngrok.service" > "$UNIT"
systemctl --user daemon-reload
systemctl --user enable --now discord-music-bot-ngrok.service
systemctl --user restart discord-music-bot.service
echo "Done. Get your personal mixer link with /mixer in Discord."
