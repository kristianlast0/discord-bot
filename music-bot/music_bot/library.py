"""Local music files under MUSIC_DIR."""
import time
from pathlib import Path, PurePosixPath

import mutagen

from . import config
from .models import Track, TrackError

AUDIO_EXTS = {".mp3", ".flac", ".ogg", ".opus", ".m4a", ".aac", ".wav", ".wma", ".webm"}
_RESCAN_AFTER = 60  # seconds

_files: list[str] = []  # relative POSIX paths
_dirs: list[str] = []  # relative POSIX paths ending in "/"
_scanned_at = float("-inf")


def _scan() -> None:
    global _files, _dirs, _scanned_at
    files, dirs = [], set()
    root = config.MUSIC_DIR
    if root.is_dir():
        for p in root.rglob("*"):
            if p.suffix.lower() in AUDIO_EXTS and p.is_file():
                rel = p.relative_to(root).as_posix()
                files.append(rel)
                parent = PurePosixPath(rel).parent
                while str(parent) != ".":
                    dirs.add(f"{parent}/")
                    parent = parent.parent
    _files, _dirs = sorted(files, key=str.lower), sorted(dirs, key=str.lower)
    _scanned_at = time.monotonic()


def _index() -> tuple[list[str], list[str]]:
    if time.monotonic() - _scanned_at > _RESCAN_AFTER:
        _scan()
    return _files, _dirs


def _matches(items: list[str], query: str) -> list[str]:
    words = query.lower().split()
    return [i for i in items if all(w in i.lower() for w in words)]


def suggest(query: str, limit: int = 25) -> list[tuple[str, str]]:
    """(label, value) pairs for autocomplete: folders first, then files."""
    files, dirs = _index()
    hits = [(f"📁 {d}", d) for d in _matches(dirs, query)] + [(f, f) for f in _matches(files, query)]
    return [(label[:100], value[:100]) for label, value in hits[:limit]]


def resolve(rel: str) -> Path:
    root = config.MUSIC_DIR.resolve()
    path = (root / rel).resolve()
    if root not in path.parents or not path.is_file():
        raise TrackError(f"**{rel}** isn't in the music folder any more.")
    return path


def _make_track(rel: str, requester: str | None) -> Track:
    title, duration = PurePosixPath(rel).stem, None
    try:
        audio = mutagen.File(config.MUSIC_DIR / rel, easy=True)
        if audio is not None:
            if getattr(audio.info, "length", None):
                duration = int(audio.info.length)
            tags = audio.tags or {}
            name = (tags.get("title") or [None])[0]
            artist = (tags.get("artist") or [None])[0]
            if name:
                title = f"{artist} - {name}" if artist else name
    except Exception:
        pass
    return Track(title, "local", rel, duration, requester=requester)


def tracks_for(query: str, requester: str | None) -> list[Track]:
    """Blocking (reads tags): run in an executor."""
    _scan()
    query = query.strip()
    folder = query if query.endswith("/") else f"{query}/"
    if folder in _dirs:
        chosen = [f for f in _files if f.startswith(folder)]
    elif query in _files:
        chosen = [query]
    else:
        file_hits = _matches(_files, query)
        dir_hits = _matches(_dirs, query)
        if file_hits:
            chosen = file_hits[:1]
        elif dir_hits:
            chosen = [f for f in _files if f.startswith(dir_hits[0])]
        else:
            chosen = []
    if not chosen:
        if not _files:
            raise TrackError(f"The music folder ({config.MUSIC_DIR}) has no audio files in it.")
        raise TrackError(f"No local music matches “{query}”.")
    return [_make_track(rel, requester) for rel in chosen]
