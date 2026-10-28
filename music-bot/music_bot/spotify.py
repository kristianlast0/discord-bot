"""Spotify links -> (artists, title, seconds), read from Spotify's public embed page (no API key needed).

Bots can't stream Spotify audio, so each track is played from a YouTube search instead.
Embed pages list up to about 100 tracks of a playlist or album.
"""
import json
import re

import aiohttp

from .models import TrackError

_URL_RE = re.compile(r"^https?://(open\.spotify\.com|spotify\.link|spotify\.app\.link)/", re.I)
_PATH_RE = re.compile(r"open\.spotify\.com/(?:intl-[a-z-]+/)?(?:embed/)?(track|album|playlist|artist)/([A-Za-z0-9]+)", re.I)
_NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)
_HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"}


def is_spotify_url(text: str) -> bool:
    return bool(_URL_RE.match(text))


def _seconds(ms) -> int | None:
    return int(ms) // 1000 if ms else None


def _clean(text: str) -> str:
    return text.replace(" ", " ").strip()


async def fetch(url: str) -> list[tuple[str, str, int | None]]:
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(headers=_HEADERS, timeout=timeout) as session:
        match = _PATH_RE.search(url)
        if not match:  # short link: follow it to the real open.spotify.com URL
            async with session.get(url) as resp:
                match = _PATH_RE.search(str(resp.url)) or _PATH_RE.search(await resp.text())
            if not match:
                raise TrackError("I couldn't work out what that Spotify link points to.")
        kind, item_id = match.group(1).lower(), match.group(2)
        async with session.get(f"https://open.spotify.com/embed/{kind}/{item_id}") as resp:
            if resp.status != 200:
                raise TrackError(f"Spotify didn't return that {kind} (HTTP {resp.status}). Is it private?")
            html = await resp.text()

    found = _NEXT_DATA_RE.search(html)
    try:
        entity = json.loads(found.group(1))["props"]["pageProps"]["state"]["data"]["entity"]
    except (AttributeError, KeyError, TypeError, ValueError):
        raise TrackError("Spotify's page has changed and I couldn't read it. Try a YouTube link instead.")

    if entity.get("trackList"):
        return [
            (_clean(t.get("subtitle", "")), _clean(t["title"]), _seconds(t.get("duration")))
            for t in entity["trackList"]
            if t.get("title") and t.get("isPlayable", True)
        ]
    if kind == "track" and entity.get("name"):
        artists = ", ".join(_clean(a["name"]) for a in entity.get("artists", []) if a.get("name"))
        return [(artists, _clean(entity["name"]), _seconds(entity.get("duration")))]
    raise TrackError(f"That Spotify {kind} has no playable tracks.")
