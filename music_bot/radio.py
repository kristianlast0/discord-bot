"""Saved internet radio stations (data/stations.json)."""
import re

from . import store
from .models import Track, TrackError

_FILE = "stations.json"
DEFAULT_STATIONS = {
    "Capital": "https://media-ssl.musicradio.com/CapitalUK",
    "Heart": "https://media-ssl.musicradio.com/HeartUK",
    "Heart 80s": "https://media-ssl.musicradio.com/Heart80s",
    "Smooth": "https://media-ssl.musicradio.com/SmoothUK",
    "Classic FM": "https://media-ssl.musicradio.com/ClassicFMMP3",
    "Radio Paradise": "https://stream.radioparadise.com/mp3-192",
    "SomaFM Groove Salad": "https://ice1.somafm.com/groovesalad-128-mp3",
}
_URL_RE = re.compile(r"^https?://\S+$", re.I)


def stations() -> dict[str, str]:
    return store.load(_FILE, DEFAULT_STATIONS)


def _find(name: str) -> str | None:
    return next((n for n in stations() if n.lower() == name.strip().lower()), None)


def add(name: str, url: str) -> None:
    if not _URL_RE.match(url.strip()):
        raise TrackError("The stream needs to be an http(s) URL.")
    data = stations()
    existing = _find(name)
    if existing:
        del data[existing]
    data[name.strip()] = url.strip()
    store.save(_FILE, data)


def remove(name: str) -> str:
    existing = _find(name)
    if not existing:
        raise TrackError(f"There's no station called “{name}”.")
    data = stations()
    del data[existing]
    store.save(_FILE, data)
    return existing


def track_for(name_or_url: str, requester: str | None) -> Track:
    text = name_or_url.strip()
    if _URL_RE.match(text):
        return Track(text, "stream", text, page_url=text, requester=requester)
    name = _find(text)
    if not name:
        raise TrackError(f"There's no station called “{text}”. See `/radio list`, or paste a stream URL.")
    return Track(f"📻 {name}", "stream", stations()[name], requester=requester)
