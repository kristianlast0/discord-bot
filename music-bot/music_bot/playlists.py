"""Saved playlists, per server (data/playlists.json)."""
from . import store
from .models import Track

_FILE = "playlists.json"
MAX_TRACKS = 500


def _all() -> dict:
    return store.load(_FILE, {})


def names(guild_id: int) -> list[str]:
    return sorted(_all().get(str(guild_id), {}), key=str.lower)


def _key(guild: dict, name: str) -> str | None:
    return next((k for k in guild if k.lower() == name.strip().lower()), None)


def get(guild_id: int, name: str) -> tuple[str, dict] | None:
    guild = _all().get(str(guild_id), {})
    key = _key(guild, name)
    return (key, guild[key]) if key else None


def save(guild_id: int, name: str, owner_id: int, tracks: list[Track]) -> None:
    data = _all()
    guild = data.setdefault(str(guild_id), {})
    existing = _key(guild, name)
    if existing:
        del guild[existing]
    guild[name.strip()] = {"owner": owner_id, "tracks": [t.to_dict() for t in tracks[:MAX_TRACKS]]}
    store.save(_FILE, data)


def delete(guild_id: int, name: str) -> None:
    data = _all()
    guild = data.get(str(guild_id), {})
    key = _key(guild, name)
    if key:
        del guild[key]
        store.save(_FILE, data)
