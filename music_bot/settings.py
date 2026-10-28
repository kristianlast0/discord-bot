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


def player_message(guild_id: int) -> tuple[int, int] | None:
    """(channel ID, message ID) of the live player message, so a crash doesn't leave it behind."""
    saved = _all().get(str(guild_id), {}).get("player_message")
    return tuple(saved) if saved else None


def set_player_message(guild_id: int, channel_id: int | None, message_id: int | None = None) -> None:
    guild = _all().setdefault(str(guild_id), {})
    if channel_id and message_id:
        guild["player_message"] = [channel_id, message_id]
    elif guild.pop("player_message", None) is None:
        return
    store.save(_FILE, _all())


def stay(guild_id: int) -> dict | None:
    """24/7 mode: {"channel": voice channel ID, "station": saved station name or None}."""
    return _all().get(str(guild_id), {}).get("stay")


def set_stay(guild_id: int, channel_id: int | None, station: str | None = None) -> None:
    guild = _all().setdefault(str(guild_id), {})
    if channel_id:
        guild["stay"] = {"channel": channel_id, "station": station}
    else:
        guild.pop("stay", None)
    store.save(_FILE, _all())


DJ_DEFAULTS = {"crossfade": 6, "autodj": False, "beatmatch": True}


def dj(guild_id: int) -> dict:
    """DJ settings: crossfade length in seconds (0 = off) and whether Auto-DJ keeps the queue going."""
    return {**DJ_DEFAULTS, **_all().get(str(guild_id), {}).get("dj", {})}


def set_dj(guild_id: int, **changes) -> None:
    _all().setdefault(str(guild_id), {}).setdefault("dj", {}).update(changes)
    store.save(_FILE, _all())
