"""Worldwide radio search via radio-browser.info, a free community directory of ~50,000 stations."""
import random
from dataclasses import dataclass

import aiohttp

from .models import Track, TrackError

_SERVERS = ["https://all.api.radio-browser.info", "https://de1.api.radio-browser.info", "https://de2.api.radio-browser.info"]
_HEADERS = {"User-Agent": "discord-music-bot/1.0"}  # the directory asks apps to identify themselves
PAGE_SIZE = 25  # Discord's limit for a dropdown


@dataclass
class Station:
    uuid: str
    name: str
    url: str
    country: str
    countrycode: str
    tags: str
    bitrate: int
    homepage: str

    @property
    def description(self) -> str:
        parts = [self.country or "Unknown country"]
        if self.tags:
            parts.append(", ".join(self.tags.split(",")[:3]))
        if self.bitrate:
            parts.append(f"{self.bitrate} kbps")
        return " · ".join(parts)[:100]

    def track(self, requester: str | None) -> Track:
        title = f"📻 {self.name}" + (f" ({self.country})" if self.country else "")
        return Track(title, "stream", self.url, page_url=self.homepage or None, requester=requester)


async def _get(path: str, params: dict) -> list | dict:
    timeout = aiohttp.ClientTimeout(total=8)
    servers = _SERVERS[:1] + random.sample(_SERVERS[1:], len(_SERVERS) - 1)
    async with aiohttp.ClientSession(headers=_HEADERS, timeout=timeout) as session:
        for server in servers:
            try:
                async with session.get(server + path, params=params) as resp:
                    if resp.status == 200:
                        return await resp.json(content_type=None)
            except (aiohttp.ClientError, TimeoutError):
                continue
    raise TrackError("The world radio directory isn't answering right now. Try again in a minute.")


async def search(*, countrycode: str | None = None, tag: str | None = None, name: str | None = None, page: int = 0) -> list[Station]:
    params = {
        "order": "votes",
        "reverse": "true",
        "hidebroken": "true",
        "limit": PAGE_SIZE,
        "offset": page * PAGE_SIZE,
    }
    if countrycode:
        params["countrycode"] = countrycode
    if tag:
        params["tag"] = tag
    if name:
        params["name"] = name
    rows = await _get("/json/stations/search", params)
    stations, seen = [], set()
    for row in rows:
        url = row.get("url_resolved") or row.get("url")
        key = (row.get("name", "").strip().lower(), url)
        if not url or key in seen:
            continue
        seen.add(key)
        stations.append(Station(
            uuid=row["stationuuid"],
            name=(row.get("name") or "Unnamed station").strip()[:100],
            url=url,
            country=row.get("country") or "",
            countrycode=row.get("countrycode") or "",
            tags=row.get("tags") or "",
            bitrate=int(row.get("bitrate") or 0),
            homepage=row.get("homepage") or "",
        ))
    return stations


async def count_play(station: Station) -> None:
    """The directory ranks stations by plays; it asks apps to report them."""
    try:
        await _get(f"/json/url/{station.uuid}", {})
    except TrackError:
        pass
