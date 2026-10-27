#!/usr/bin/env bash
# Run from WSL: copy the code to the laptop and reinstall/restart there.
set -euo pipefail
HOST="${HOST:-jamie@192.168.1.55}"
cd "$(dirname "$0")/.."
rsync -a --delete --exclude .git --exclude .venv --exclude .env --exclude data --exclude __pycache__ ./ "$HOST:~/discord-music-bot/"
ssh "$HOST" 'bash ~/discord-music-bot/deploy/install.sh'
