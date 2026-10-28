#!/usr/bin/env bash
# Copy the code to the machine that runs the bot and reinstall/restart it there.
# Defaults to Jamie's laptop; deploy elsewhere with HOST=you@your-server deploy/push.sh
set -euo pipefail
HOST="${HOST:-jamie@192.168.1.55}"
cd "$(dirname "$0")/.."
rsync -a --delete --exclude .git --exclude .venv --exclude .env --exclude data --exclude __pycache__ ./ "$HOST:~/discord-music-bot/"
ssh "$HOST" 'bash ~/discord-music-bot/deploy/install.sh'
