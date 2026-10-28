"""Settings, read from the environment or a .env file next to the project."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


_load_env(ROOT / ".env")

TOKEN = os.environ.get("DISCORD_TOKEN", "")
# Commands appear instantly in this server; without it they are global and can take up to an hour.
GUILD_ID = int(os.environ["GUILD_ID"]) if os.environ.get("GUILD_ID") else None
MUSIC_DIR = Path(os.environ.get("MUSIC_DIR", "~/Music")).expanduser()
DATA_DIR = Path(os.environ.get("DATA_DIR", ROOT / "data")).expanduser()
IDLE_TIMEOUT = int(os.environ.get("IDLE_TIMEOUT", "300"))  # seconds before leaving an idle/empty channel
DEFAULT_VOLUME = int(os.environ.get("DEFAULT_VOLUME", "50"))  # percent
MAX_PLAYLIST_IMPORT = int(os.environ.get("MAX_PLAYLIST_IMPORT", "200"))  # tracks taken from one playlist link
MIXER_PORT = int(os.environ.get("MIXER_PORT", "8765"))
MIXER_HOST = os.environ.get("MIXER_HOST", "127.0.0.1")  # only this computer by default; Docker sets 0.0.0.0 inside the container
PUBLIC_URL = os.environ.get("PUBLIC_URL", "")  # how people reach it, e.g. https://example.ngrok-free.app
