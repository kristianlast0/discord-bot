"""Per-server settings (data/settings.json)."""
from . import store

_FILE = "settings.json"
_data: dict | None = None


def _all() -> dict:
    global _data
    if _data is None:
        _data = store.load(_FILE, {})
    return _data


def music_channel(guild_id: int) -> int | None:
    return _all().get(str(guild_id), {}).get("music_channel")


def set_music_channel(guild_id: int, channel_id: int | None) -> None:
    guild = _all().setdefault(str(guild_id), {})
    if channel_id:
        guild["music_channel"] = channel_id
    else:
        guild.pop("music_channel", None)
    store.save(_FILE, _all())
