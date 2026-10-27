"""One player per server: the queue, playback and auto-leave."""
import asyncio
import logging
import random
import shlex
import time

import discord

from . import config, tracks
from .models import Track, TrackError, fmt_duration

log = logging.getLogger(__name__)

_NET_BEFORE = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
_IDLE_CHECK = 15  # seconds
_QUICK_FAIL = 3  # a track that ends this fast didn't really play


def _before_options(url: str, headers: dict) -> str:
    if not url.startswith(("http://", "https://")):
        return ""
    opts = _NET_BEFORE
    if headers:
        opts += " -headers " + shlex.quote("".join(f"{k}: {v}\r\n" for k, v in headers.items()))
    return opts


class GuildPlayer:
    def __init__(self, bot: discord.Client, guild_id: int):
        self.bot = bot
        self.guild_id = guild_id
        self.queue: list[Track] = []
        self.current: Track | None = None
        self.loop_mode = "off"  # "off" | "track" | "queue"
        self.volume = config.DEFAULT_VOLUME / 100
        self.channel: discord.abc.Messageable | None = None  # where announcements go
        self._lock = asyncio.Lock()
        self._skipping = False
        self._stopping = False
        self._started = 0.0
        self._paused_at: float | None = None
        self._paused_total = 0.0
        self._idle_task: asyncio.Task | None = None

    @property
    def voice(self) -> discord.VoiceClient | None:
        guild = self.bot.get_guild(self.guild_id)
        return guild.voice_client if guild else None  # type: ignore[return-value]

    @property
    def active(self) -> bool:
        vc = self.voice
        return bool(vc and (vc.is_playing() or vc.is_paused()))

    @property
    def elapsed(self) -> float:
        end = self._paused_at or time.monotonic()
        return max(0.0, end - self._started - self._paused_total)

    async def say(self, content: str | None = None, **kwargs) -> None:
        if self.channel:
            try:
                await self.channel.send(content, **kwargs)
            except discord.HTTPException as e:
                log.warning("Couldn't post in %s: %s", self.channel, e)

    async def connect(self, channel: discord.VoiceChannel | discord.StageChannel) -> None:
        vc = self.voice
        if vc and vc.is_connected():
            if vc.channel != channel:
                await vc.move_to(channel)
        else:
            if vc:  # half-dead connection left over from a network drop
                await vc.disconnect(force=True)
            await channel.connect(self_deaf=True)
        if not self._idle_task or self._idle_task.done():
            self._idle_task = asyncio.create_task(self._idle_watch())

    def add(self, new: list[Track], front: bool = False) -> int:
        """Queue tracks; returns the queue position (1-based) of the first one."""
        if front:
            self.queue[0:0] = new
            return 1
        self.queue.extend(new)
        return len(self.queue) - len(new) + 1

    async def start(self, announce: bool = True) -> None:
        """Start playing if nothing is."""
        async with self._lock:
            vc = self.voice
            if not vc or not vc.is_connected() or self.current or self.active:
                return
            while self.queue:
                track = self.queue.pop(0)
                try:
                    url, headers = await tracks.stream_for(track)
                except TrackError as e:
                    await self.say(f"⚠️ Skipped **{track.title}**: {e}")
                    continue
                except Exception:
                    log.exception("Couldn't resolve %r", track)
                    await self.say(f"⚠️ Skipped **{track.title}**: something went wrong loading it.")
                    continue

                vc = self.voice
                if not vc or not vc.is_connected():
                    self.queue.insert(0, track)
                    return
                source = discord.PCMVolumeTransformer(
                    discord.FFmpegPCMAudio(url, before_options=_before_options(url, headers), options="-vn"),
                    volume=self.volume,
                )
                self.current = track
                self._started, self._paused_at, self._paused_total = time.monotonic(), None, 0.0
                try:
                    vc.play(source, after=self._after)
                except discord.ClientException:
                    log.exception("Couldn't start playback")
                    self.current = None
                    self.queue.insert(0, track)
                    return
                if announce:
                    await self.say(embed=self.now_playing_embed())
                return

    def _after(self, error: Exception | None) -> None:
        # Runs on the audio thread.
        if error:
            log.error("Playback error: %s", error)
        asyncio.run_coroutine_threadsafe(self._track_ended(), self.bot.loop)

    async def _track_ended(self) -> None:
        finished, self.current = self.current, None
        skipped, self._skipping = self._skipping, False
        if self._stopping:
            self._stopping = False
            return
        if finished:
            quick_fail = not skipped and self.elapsed < _QUICK_FAIL
            if quick_fail:
                finished.stream = None  # maybe an expired URL; refetch next time
                await self.say(f"⚠️ **{finished.title}** stopped straight away, so I've moved on.")
            elif self.loop_mode == "track" and not skipped:
                self.queue.insert(0, finished)
            elif self.loop_mode == "queue":
                self.queue.append(finished)
        await self.start()

    def pause(self) -> bool:
        vc = self.voice
        if vc and vc.is_playing():
            vc.pause()
            self._paused_at = time.monotonic()
            return True
        return False

    def resume(self) -> bool:
        vc = self.voice
        if vc and vc.is_paused():
            vc.resume()
            if self._paused_at:
                self._paused_total += time.monotonic() - self._paused_at
            self._paused_at = None
            return True
        return False

    def skip(self) -> bool:
        if self.active:
            self._skipping = True
            self.voice.stop()
            return True
        return False

    def stop(self) -> None:
        self.queue.clear()
        if self.active:
            self._stopping = True
            self.voice.stop()

    def set_volume(self, percent: int) -> None:
        self.volume = percent / 100
        vc = self.voice
        if vc and isinstance(vc.source, discord.PCMVolumeTransformer):
            vc.source.volume = self.volume

    def shuffle(self) -> None:
        random.shuffle(self.queue)

    async def leave(self) -> None:
        self.stop()
        vc = self.voice
        if vc:
            await vc.disconnect(force=False)
        self.reset()

    def reset(self) -> None:
        """Forget playback state after the bot leaves voice (by command, idle, or being kicked)."""
        self.queue.clear()
        self.current = None
        self._stopping = self._skipping = False
        if self._idle_task and self._idle_task is not asyncio.current_task():
            self._idle_task.cancel()
        self._idle_task = None

    async def _idle_watch(self) -> None:
        idle = 0
        while True:
            await asyncio.sleep(_IDLE_CHECK)
            vc = self.voice
            if not vc or not vc.is_connected():
                return
            alone = not any(not m.bot for m in vc.channel.members)
            idle = idle + _IDLE_CHECK if alone or not self.active else 0
            if idle >= config.IDLE_TIMEOUT:
                await self.say("👋 Nobody's listening, so I've left the voice channel.")
                await self.leave()
                return

    def now_playing_embed(self) -> discord.Embed:
        track = self.current
        embed = discord.Embed(colour=discord.Colour.blurple())
        if not track:
            embed.description = "Nothing is playing."
            return embed
        embed.title = track.title[:256]
        embed.url = track.page_url
        progress = fmt_duration(self.elapsed) + (f" / {fmt_duration(track.duration)}" if track.duration else " · live")
        state = "⏸️ Paused" if self.voice and self.voice.is_paused() else "▶️ Now playing"
        embed.set_author(name=state)
        embed.add_field(name="Progress", value=progress)
        if track.requester:
            embed.add_field(name="Requested by", value=track.requester)
        footer = f"Volume {round(self.volume * 100)}%"
        if self.loop_mode != "off":
            footer += f" · Looping {self.loop_mode}"
        if self.queue:
            footer += f" · Up next: {self.queue[0].title[:60]}"
        embed.set_footer(text=footer)
        return embed
