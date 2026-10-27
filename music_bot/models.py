from __future__ import annotations

from dataclasses import dataclass, field

from discord import app_commands


class TrackError(Exception):
    """A track couldn't be found or played; the message is shown to the user."""


class UserError(app_commands.AppCommandError):
    """Something the user did wrong; shown to them as-is."""


@dataclass(eq=False)
class Track:
    title: str
    # "ytdl": a page URL or "ytsearch1:..." query that yt-dlp resolves when the track starts
    # "local": a path relative to MUSIC_DIR
    # "stream": a direct audio stream URL (internet radio)
    kind: str
    source: str
    duration: int | None = None  # seconds; None for radio and unknown lengths
    page_url: str | None = None  # link shown in embeds
    requester: str | None = None
    # (stream URL, HTTP headers, time fetched) from the lookup, reused if the track starts soon after
    stream: tuple[str, dict, float] | None = field(default=None, repr=False)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "kind": self.kind,
            "source": self.source,
            "duration": self.duration,
            "page_url": self.page_url,
        }

    @classmethod
    def from_dict(cls, data: dict, requester: str | None = None) -> Track:
        return cls(
            title=data["title"],
            kind=data["kind"],
            source=data["source"],
            duration=data.get("duration"),
            page_url=data.get("page_url"),
            requester=requester,
        )


def fmt_duration(seconds: int | float | None) -> str:
    if seconds is None:
        return "live"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"
