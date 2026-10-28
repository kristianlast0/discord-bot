#!/usr/bin/env bash
# Copy the code to your Linux server over SSH and reinstall/restart it there.
# Usage: HOST=you@your-server deploy/push.sh   (or put "you@your-server" in deploy/.host, which git ignores)
set -euo pipefail
cd "$(dirname "$0")/.."
HOST="${HOST:-$(cat deploy/.host 2>/dev/null || true)}"
if [ -z "$HOST" ]; then
  echo "Say where to deploy: HOST=you@your-server deploy/push.sh, or put you@your-server in deploy/.host" >&2
  exit 1
fi
rsync -a --delete --exclude .git --exclude .venv --exclude .env --exclude data --exclude __pycache__ ./ "$HOST:~/discord-music-bot/"
ssh "$HOST" 'bash ~/discord-music-bot/deploy/install.sh'
