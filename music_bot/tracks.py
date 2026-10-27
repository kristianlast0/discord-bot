"""Turning what people type into tracks, and tracks into something ffmpeg can play."""
import asyncio
import re
import time
from urllib.parse import urlparse

import aiohttp
import yt_dlp

from . import config, library, spotify
from .models import Track, TrackError

_URL_RE = re.compile(r"^https?://", re.I)
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_STREAM_TTL = 3600  # YouTube stream URLs last ~6 hours; refetch well before that
_UNAVAILABLE = {"[Private video]", "[Deleted video]"}

_COMMON = {"quiet": True, "no_warnings": True, "noplaylist": True, "format": "bestaudio/best", "source_address": "0.0.0.0"}
# Playlist links are read "flat" (titles only); single videos get fully extracted.
_LOOKUP_OPTS = {**_COMMON, "extract_flat": "in_playlist", "playlistend": config.MAX_PLAYLIST_IMPORT}
_STREAM_OPTS = _COMMON


def _extract(query: str, opts: dict) -> dict:
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(query, download=False)


async def _ytdl(query: str, opts: dict) -> dict:
    try:
        return await asyncio.get_running_loop().run_in_executor(None, _extract, query, opts)
    except yt_dlp.utils.DownloadError as e:
        message = _ANSI_RE.sub("", str(e)).removeprefix("ERROR: ")
        raise TrackError(message[:300]) from None


def _int(value) -> int | None:
    return int(value) if value else None


def _from_entry(entry: dict, requester: str | None) -> Track:
    page = entry.get("webpage_url") or entry.get("url")
    track = Track(
        title=entry.get("title") or page,
        kind="ytdl",
        source=page,
        duration=_int(entry.get("duration")),
        page_url=page if page and page.startswith("http") else None,
        requester=requester,
    )
    stream_url = entry.get("url")
    if entry.get("_type", "video") == "video" and stream_url and stream_url != page:
        track.stream = (stream_url, entry.get("http_headers") or {}, time.time())
    return track


async def lookup(query: str, requester: str | None) -> list[Track]:
    """YouTube/Spotify/other links, or a YouTube search."""
    query = query.strip()
    if spotify.is_spotify_url(query):
        return [
            Track(f"{artists} - {title}" if artists else title, "ytdl", f"ytsearch1:{artists} {title}", duration, requester=requester)
            for artists, title, duration in (await spotify.fetch(query))[: config.MAX_PLAYLIST_IMPORT]
        ]

    info = await _ytdl(query if _URL_RE.match(query) else f"ytsearch1:{query}", _LOOKUP_OPTS)
    if info.get("entries") is None:
        return [_from_entry(info, requester)]
    found = [_from_entry(e, requester) for e in info["entries"] if e and e.get("title") not in _UNAVAILABLE]
    if not found:
        raise TrackError(f"Nothing found for “{query}”." if not _URL_RE.match(query) else "That playlist is empty.")
    return found


async def local(query: str, requester: str | None) -> list[Track]:
    return await asyncio.get_running_loop().run_in_executor(None, library.tracks_for, query, requester)


async def _unwrap_playlist_file(url: str) -> str:
    """Radio stations often publish .pls/.m3u files that just point at the real stream."""
    if not urlparse(url).path.lower().endswith((".pls", ".m3u")):
        return url
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
        async with session.get(url) as resp:
            text = await resp.text(errors="replace")
    for line in text.splitlines():
        line = line.strip()
        if line.lower().startswith("file") and "=" in line:
            line = line.split("=", 1)[1].strip()
        if _URL_RE.match(line):
            return line
    raise TrackError("That playlist file has no stream URLs in it.")


async def stream_for(track: Track) -> tuple[str, dict]:
    """(URL or path for ffmpeg, HTTP headers)."""
    if track.kind == "local":
        return str(library.resolve(track.source)), {}
    if track.kind == "stream":
        return await _unwrap_playlist_file(track.source), {}
    if track.stream and time.time() - track.stream[2] < _STREAM_TTL:
        return track.stream[0], track.stream[1]

    info = await _ytdl(track.source, _STREAM_OPTS)
    if info.get("entries") is not None:
        entries = [e for e in info["entries"] if e]
        if not entries:
            raise TrackError("No YouTube results.")
        info = entries[0]
    if not info.get("url"):
        raise TrackError("No playable audio found.")
    track.page_url = track.page_url or info.get("webpage_url")
    track.duration = track.duration or _int(info.get("duration"))
    track.stream = (info["url"], info.get("http_headers") or {}, time.time())
    return track.stream[0], track.stream[1]
