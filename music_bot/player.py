"""One player per server: the queue, playback, the live player message and auto-leave."""
import asyncio
import json
import logging
import os
import random
import subprocess
import sys
import tempfile
import time

import discord

from . import config, radio, settings, tracks
from .models import Track, TrackError, UserError, fmt_duration

log = logging.getLogger(__name__)

_NET_BEFORE = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
_IDLE_CHECK = 15  # seconds
_QUICK_FAIL = 3  # a track that ends this fast didn't really play
_NOTICE_LIFETIME = 30  # seconds before warnings delete themselves
_LOOP_NEXT = {"off": "track", "track": "queue", "queue": "off"}
_FILLER_RETRY_MAX = 300  # seconds between attempts when the 24/7 station is down


class YtdlpAudio(discord.FFmpegPCMAudio):
    """yt-dlp downloads the audio (in the small chunks YouTube expects) and pipes it into ffmpeg.

    ffmpeg fetching YouTube URLs itself gets 403 Forbidden most of the time.
    """

    def __init__(self, info: dict):
        fd, self._info_path = tempfile.mkstemp(prefix="music-bot-", suffix=".json")
        with os.fdopen(fd, "w") as f:
            json.dump(info, f)
        self._ytdlp = subprocess.Popen(
            [sys.executable, "-m", "yt_dlp", "--quiet", "--no-warnings", "--no-progress",
             "-f", "bestaudio/best", "--load-info-json", self._info_path, "-o", "-"],
            stdout=subprocess.PIPE,
        )
        try:
            super().__init__(self._ytdlp.stdout, pipe=True, options="-vn")
        except Exception:
            self._stop_ytdlp()
            raise

    def _stop_ytdlp(self) -> None:
        if self._ytdlp.poll() is None:
            self._ytdlp.kill()
        try:
            self._ytdlp.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.unlink(self._info_path)
        except OSError:
            pass

    def cleanup(self) -> None:
        self._stop_ytdlp()  # first, so it doesn't complain about ffmpeg closing the pipe
        super().cleanup()


def _audio_for(playable: tracks.Playable) -> discord.AudioSource:
    if playable.info:
        return YtdlpAudio(playable.info)
    remote = playable.url.startswith(("http://", "https://"))
    return discord.FFmpegPCMAudio(playable.url, before_options=_NET_BEFORE if remote else "", options="-vn")


class PlayerControls(discord.ui.View):
    """Buttons under the player message."""

    def __init__(self, player: "GuildPlayer"):
        super().__init__(timeout=None)
        self.player = player

    def sync(self) -> None:
        vc = self.player.voice
        paused = bool(vc and vc.is_paused())
        self.toggle.emoji, self.toggle.label = ("▶️", "Resume") if paused else ("⏸️", "Pause")
        mode = self.player.loop_mode
        self.loop.label = {"off": "Loop", "track": "Loop: track", "queue": "Loop: queue"}[mode]
        self.loop.style = discord.ButtonStyle.secondary if mode == "off" else discord.ButtonStyle.success
        self.shuffle.disabled = len(self.player.queue) < 2
        votes = len(self.player.skip_votes)
        self.skip.label = f"Skip ({votes}/{self.player.skip_votes_needed()})" if votes else "Skip"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        self.player.check_listener(interaction.user)
        return True

    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        if isinstance(error, (UserError, TrackError)):
            message = f"⚠️ {error}"
        else:
            log.error("Player button failed", exc_info=error)
            message = "⚠️ Something went wrong. It's been logged."
        if not interaction.response.is_done():
            await interaction.response.send_message(message, ephemeral=True)

    async def _redraw(self, interaction: discord.Interaction) -> None:
        self.sync()
        await interaction.response.edit_message(embed=self.player.now_playing_embed(), view=self)

    @discord.ui.button(emoji="⏸️", label="Pause", style=discord.ButtonStyle.primary)
    async def toggle(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.player.resume():
            self.player.pause()
        await self._redraw(interaction)

    @discord.ui.button(emoji="⏭️", label="Skip", style=discord.ButtonStyle.secondary)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        skipped, message = self.player.vote_skip(interaction.user)
        if skipped:  # the next track replaces this message
            await interaction.response.send_message(message, ephemeral=True)
        else:
            await self._redraw(interaction)
            await interaction.followup.send(message, ephemeral=True)

    @discord.ui.button(emoji="⏹️", label="Stop", style=discord.ButtonStyle.danger)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.player.check_in_charge(interaction.user, [self.player.current, *self.player.queue], "stop everyone's music")
        self.player.stop()  # deletes this message
        await interaction.response.defer()

    @discord.ui.button(emoji="🔁", label="Loop", style=discord.ButtonStyle.secondary)
    async def loop(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.player.loop_mode = _LOOP_NEXT[self.player.loop_mode]
        await self._redraw(interaction)

    @discord.ui.button(emoji="🔀", label="Shuffle", style=discord.ButtonStyle.secondary)
    async def shuffle(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.player.shuffle()
        await self._redraw(interaction)


class GuildPlayer:
    def __init__(self, bot: discord.Client, guild_id: int):
        self.bot = bot
        self.guild_id = guild_id
        self.queue: list[Track] = []
        self.current: Track | None = None
        self.loop_mode = "off"  # "off" | "track" | "queue"
        self.volume = config.DEFAULT_VOLUME / 100
        self.channel: discord.abc.Messageable | None = None  # last channel a command came from
        self._lock = asyncio.Lock()
        self._skipping = False
        self._stopping = False
        self.skip_votes: set[int] = set()  # member IDs voting to skip the current track
        self._started = 0.0
        self._paused_at: float | None = None
        self._paused_total = 0.0
        self._idle_task: asyncio.Task | None = None
        self._message: discord.Message | None = None  # the live player message
        self._message_lock = asyncio.Lock()
        self._controls: PlayerControls | None = None
        self._filler_failures = 0
        self._filler_retry_at = 0.0
        self._filler_retry_task: asyncio.Task | None = None

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

    @property
    def announce_channel(self) -> discord.abc.Messageable | None:
        """The server's music channel if one is set, otherwise wherever the last command came from."""
        channel_id = settings.music_channel(self.guild_id)
        return (self.bot.get_channel(channel_id) if channel_id else None) or self.channel

    def check_listener(self, member: discord.abc.User) -> None:
        """Only people in the bot's voice channel may change playback."""
        vc = self.voice
        if not vc or not vc.is_connected():
            raise UserError("I'm not in a voice channel right now. Use `/play` to start some music.")
        voice = getattr(member, "voice", None)
        if not voice or voice.channel != vc.channel:
            raise UserError(f"Join {vc.channel.mention} to control the music.")

    def listeners(self) -> list[discord.Member]:
        vc = self.voice
        return [m for m in vc.channel.members if not m.bot] if vc and vc.channel else []

    def in_charge(self, member: discord.Member, affected: list[Track | None]) -> bool:
        """Server managers, people alone with the bot, and people whose own music it is can act without a vote."""
        if member.guild_permissions.manage_guild:
            return True
        if not any(m.id != member.id for m in self.listeners()):
            return True
        return all(t.requester_id == member.id for t in affected if t and not t.filler)

    def can_skip_now(self, member: discord.Member) -> bool:
        return self.in_charge(member, [self.current])

    def check_in_charge(self, member: discord.Member, affected: list[Track | None], action: str) -> None:
        if not self.in_charge(member, affected):
            raise UserError(f"Only a server manager can {action} while other people's music is queued. Use Skip to vote instead.")

    def skip_votes_needed(self) -> int:
        return len(self.listeners()) // 2 + 1  # more than half the people listening

    def vote_skip(self, member: discord.Member) -> tuple[bool, str]:
        """Skip now if the member may, otherwise count their vote. Returns (skipped, message for them)."""
        if not self.current or not self.active:
            raise UserError("Nothing is playing.")
        title = self.current.title
        if self.can_skip_now(member):
            self.skip()
            return True, f"⏭️ Skipped **{title}**."
        present = {m.id for m in self.listeners()}
        self.skip_votes = (self.skip_votes | {member.id}) & present  # people who left don't count
        votes, needed = len(self.skip_votes), self.skip_votes_needed()
        if votes >= needed:
            self.skip()
            asyncio.create_task(self.notice(f"⏭️ **{title}** was skipped by vote ({votes}/{needed})."))
            return True, f"⏭️ Your vote skipped **{title}**."
        return False, f"🗳️ You voted to skip **{title}** ({votes}/{needed}). Others can vote with the Skip button or `/skip`."

    async def notice(self, content: str, lifetime: float | None = _NOTICE_LIFETIME) -> None:
        """A short message in the music channel that deletes itself."""
        channel = self.announce_channel
        if channel:
            try:
                await channel.send(content, delete_after=lifetime)
            except discord.HTTPException as e:
                log.warning("Couldn't post in %s: %s", channel, e)

    async def refresh_player(self, repost: bool = False) -> None:
        """Update the live player message, or post it fresh at the bottom of the channel."""
        async with self._message_lock:
            channel = self.announce_channel
            if not self.current or not channel:
                return
            if self._controls is None:
                self._controls = PlayerControls(self)
            self._controls.sync()
            embed = self.now_playing_embed()
            if self._message and self._message.channel.id != getattr(channel, "id", None):
                repost = True
            if self._message and not repost:
                try:
                    await self._message.edit(embed=embed, view=self._controls)
                    return
                except discord.NotFound:
                    self._message = None
                except discord.HTTPException as e:
                    log.warning("Couldn't update the player message: %s", e)
                    return
            await self._delete_message()
            try:
                self._message = await channel.send(embed=embed, view=self._controls)
                settings.set_player_message(self.guild_id, self._message.channel.id, self._message.id)
            except discord.HTTPException as e:
                log.warning("Couldn't post the player message in %s: %s", channel, e)

    async def clear_player(self) -> None:
        async with self._message_lock:
            await self._delete_message()

    async def _delete_message(self) -> None:
        message, self._message = self._message, None
        if message:
            settings.set_player_message(self.guild_id, None)
            try:
                await message.delete()
            except discord.HTTPException:
                pass

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

    async def start(self) -> None:
        """Start playing if nothing is."""
        async with self._lock:
            vc = self.voice
            if not vc or not vc.is_connected() or self.current or self.active:
                return
            while self.queue or self._queue_filler():
                track = self.queue.pop(0)
                try:
                    playable = await tracks.stream_for(track)
                except TrackError as e:
                    if track.filler:
                        await self._filler_failed(str(e))
                    else:
                        await self.notice(f"⚠️ Skipped **{track.title}**: {e}")
                    continue
                except Exception:
                    log.exception("Couldn't resolve %r", track)
                    await self.notice(f"⚠️ Skipped **{track.title}**: something went wrong loading it.")
                    continue

                vc = self.voice
                if not vc or not vc.is_connected():
                    self.queue.insert(0, track)
                    return
                try:
                    source = discord.PCMVolumeTransformer(_audio_for(playable), volume=self.volume)
                except Exception:
                    log.exception("Couldn't start ffmpeg for %r", track)
                    await self.notice(f"⚠️ Skipped **{track.title}**: couldn't start the audio.")
                    continue
                self.current = track
                self.skip_votes.clear()
                self._started, self._paused_at, self._paused_total = time.monotonic(), None, 0.0
                try:
                    vc.play(source, after=self._after)
                except discord.ClientException:
                    log.exception("Couldn't start playback")
                    self.current = None
                    self.queue.insert(0, track)
                    return
                await self.refresh_player(repost=True)
                self.prefetch()
                return
            await self.clear_player()  # queue finished

    def _queue_filler(self) -> bool:
        """In 24/7 mode with a station, queue the station when nothing else is queued."""
        stay = settings.stay(self.guild_id)
        if not stay or not stay.get("station") or time.monotonic() < self._filler_retry_at:
            return False
        try:
            track = radio.track_for(stay["station"], None)
        except TrackError:  # the station was removed from the list
            return False
        track.filler = True
        self.queue.append(track)
        return True

    async def _filler_failed(self, reason: str) -> None:
        """Back off (15 s, 30 s, ... 5 min) instead of hammering a station that's down."""
        self._filler_failures += 1
        delay = min(_FILLER_RETRY_MAX, 15 * 2 ** (self._filler_failures - 1))
        self._filler_retry_at = time.monotonic() + delay
        if self._filler_failures == 1:
            await self.notice(f"⚠️ The 24/7 station isn't working ({reason}). I'll keep trying.", lifetime=120)
        if not self._filler_retry_task or self._filler_retry_task.done():
            self._filler_retry_task = asyncio.create_task(self._retry_filler(delay))

    async def _retry_filler(self, delay: float) -> None:
        await asyncio.sleep(delay + 1)
        await self.start()

    def restart_filler(self) -> None:
        """After 24/7 settings change: forget past failures, and swap out the old station if it's playing."""
        self._filler_failures, self._filler_retry_at = 0, 0.0
        if self.current and self.current.filler:
            self.skip()

    def prefetch(self) -> None:
        """Look up the next YouTube track in the background, so it starts quickly."""
        upcoming = self.queue[0] if self.queue else None
        if upcoming and upcoming.kind == "ytdl" and not upcoming.resolved:
            task = asyncio.create_task(tracks.stream_for(upcoming))
            task.add_done_callback(lambda t: t.cancelled() or t.exception())  # failures retry when it starts

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
            if (settings.stay(self.guild_id) or {}).get("station"):
                await self.start()  # back to the 24/7 station
            else:
                await self.clear_player()
            return
        if finished and finished.filler:
            if not skipped and self.elapsed < _QUICK_FAIL:
                await self._filler_failed("it stopped straight away")
            elif not skipped:
                self._filler_failures = 0
        elif finished:
            quick_fail = not skipped and self.elapsed < _QUICK_FAIL
            if quick_fail:
                finished.resolved = None  # maybe expired; refetch next time
                await self.notice(f"⚠️ **{finished.title}** stopped straight away, so I've moved on.")
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
        if len(self.queue) < 2:
            raise UserError("There's not enough in the queue to shuffle.")
        random.shuffle(self.queue)

    async def leave(self) -> None:
        self.stop()
        vc = self.voice
        if vc:
            await vc.disconnect(force=False)
        self.reset()
        await self.clear_player()

    def reset(self) -> None:
        """Forget playback state after the bot leaves voice (by command, idle, or being kicked)."""
        self.queue.clear()
        self.current = None
        self._stopping = self._skipping = False
        if self._idle_task and self._idle_task is not asyncio.current_task():
            self._idle_task.cancel()
        self._idle_task = None
        asyncio.create_task(self.clear_player())

    async def _idle_watch(self) -> None:
        idle = 0
        while True:
            await asyncio.sleep(_IDLE_CHECK)
            vc = self.voice
            if not vc or not vc.is_connected():
                return
            if settings.stay(self.guild_id):  # 24/7 mode never leaves
                idle = 0
                continue
            alone = not any(not m.bot for m in vc.channel.members)
            idle = idle + _IDLE_CHECK if alone or not self.active else 0
            if idle >= config.IDLE_TIMEOUT:
                await self.notice("👋 Nobody's listening, so I've left the voice channel.", lifetime=60)
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
        paused = self.voice and self.voice.is_paused()
        embed.set_author(name="⏸️ Paused" if paused else "▶️ Now playing")
        if paused:
            embed.add_field(name="Position", value=f"{fmt_duration(self.elapsed)} / {fmt_duration(track.duration)}")
        else:
            embed.add_field(name="Length", value=fmt_duration(track.duration) if track.duration else "Live")
        if track.filler:
            embed.add_field(name="Playing because", value="24/7 mode. `/play` something to take over")
        elif track.requester:
            embed.add_field(name="Requested by", value=track.requester)
        if self.queue:
            up_next = "\n".join(f"`{i}.` {t.title[:60]}" for i, t in enumerate(self.queue[:3], 1))
            if len(self.queue) > 3:
                up_next += f"\n…and {len(self.queue) - 3} more"
            embed.add_field(name="Up next", value=up_next, inline=False)
        footer = f"Volume {round(self.volume * 100)}%"
        if settings.stay(self.guild_id):
            footer += " · 24/7"
        if self.loop_mode != "off":
            footer += f" · Looping {self.loop_mode}"
        if self.skip_votes:
            footer += f" · Skip votes {len(self.skip_votes)}/{self.skip_votes_needed()}"
        embed.set_footer(text=footer)
        return embed
