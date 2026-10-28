"""One player per server: the queue, the two-deck mixer, the DJ booth message and auto-leave."""
import asyncio
import collections
import io
import json
import logging
import os
import random
import subprocess
import sys
import tempfile
import time

import discord

from . import booth, config, radio, settings, tracks
from .mixer import FRAME_SECONDS, Deck, Mixer, frames
from .models import Track, TrackError, UserError, fmt_duration

log = logging.getLogger(__name__)

_NET_BEFORE = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
# Cut silence at the start, and any gap over 4 s (long fade-outs, hidden-track gaps), so mixes land on music.
_TRIM = "silenceremove=start_periods=1:start_threshold=-50dB:stop_periods=-1:stop_duration=4:stop_threshold=-50dB"
_LEVEL = "dynaudnorm=f=250:g=15"  # evens out loudness between songs; cheap enough for the laptop
_IDLE_CHECK = 15  # seconds
_QUICK_FAIL = 3  # a track that ends this fast didn't really play
_NOTICE_LIFETIME = 30  # seconds before warnings delete themselves
_LOOP_NEXT = {"off": "track", "track": "queue", "queue": "off"}
_FILLER_RETRY_MAX = 300  # seconds between attempts when the 24/7 station is down
_TICK = 0.25  # how often the conductor checks the decks
_PRIME_AHEAD = 25  # start loading the next track this many seconds before the mix
_PRIME_RETRY = 10  # seconds between attempts to find a next track (e.g. Auto-DJ lookups)
_LOOKAHEAD = 15  # seconds each deck reads ahead, on top of the crossfade
_SKIP_FADE = 1.0
_START_FADE = 0.3
_STALL_LIMIT = 30  # seconds a deck may take to make a sound before we give up on it
_BOOTH_EVERY = 30  # seconds between booth redraws while playing...
_BOOTH_MIXING_EVERY = 3  # ...and while two decks are mixing
_CROSSFADE_STEPS = (0, 3, 6, 10)
_AUTODJ_LENGTH = (60, 600)  # Auto-DJ skips clips and hour-long mixes


def _audio_options(track: Track) -> str:
    filters = [_LEVEL] if track.kind == "stream" else [_TRIM, _LEVEL]
    return "-vn -af " + ",".join(filters)


class YtdlpAudio(discord.FFmpegPCMAudio):
    """yt-dlp downloads the audio (in the small chunks YouTube expects) and pipes it into ffmpeg.

    ffmpeg fetching YouTube URLs itself gets 403 Forbidden most of the time.
    """

    def __init__(self, info: dict, options: str):
        fd, self._info_path = tempfile.mkstemp(prefix="music-bot-", suffix=".json")
        with os.fdopen(fd, "w") as f:
            json.dump(info, f)
        self._ytdlp = subprocess.Popen(
            [sys.executable, "-m", "yt_dlp", "--quiet", "--no-warnings", "--no-progress",
             "-f", "bestaudio/best", "--load-info-json", self._info_path, "-o", "-"],
            stdout=subprocess.PIPE,
        )
        try:
            super().__init__(self._ytdlp.stdout, pipe=True, options=options)
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


def _audio_for(playable: tracks.Playable, track: Track) -> discord.AudioSource:
    options = _audio_options(track)
    if playable.info:
        return YtdlpAudio(playable.info, options)
    remote = playable.url.startswith(("http://", "https://"))
    return discord.FFmpegPCMAudio(playable.url, before_options=_NET_BEFORE if remote else "", options=options)


class PlayerControls(discord.ui.View):
    """Buttons under the DJ booth."""

    def __init__(self, player: "GuildPlayer"):
        super().__init__(timeout=None)
        self.player = player

    def sync(self) -> None:
        p = self.player
        paused = p.paused
        self.toggle.emoji, self.toggle.label = ("▶️", "Resume") if paused else ("⏸️", "Pause")
        self.loop.label = {"off": "Loop", "track": "Loop: track", "queue": "Loop: queue"}[p.loop_mode]
        self.loop.style = discord.ButtonStyle.secondary if p.loop_mode == "off" else discord.ButtonStyle.success
        self.shuffle.disabled = len(p.queue) < 2
        votes = len(p.skip_votes)
        self.skip.label = f"Skip ({votes}/{p.skip_votes_needed()})" if votes else "Skip"
        dj = settings.dj(p.guild_id)
        self.crossfade.label = f"Crossfade: {dj['crossfade']}s" if dj["crossfade"] else "Crossfade: off"
        self.autodj.label = "Auto-DJ: on" if dj["autodj"] else "Auto-DJ: off"
        self.autodj.style = discord.ButtonStyle.success if dj["autodj"] else discord.ButtonStyle.secondary
        self.mix_now.disabled = not p.has_next

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        self.player.check_listener(interaction.user)
        return True

    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        if isinstance(error, (UserError, TrackError)):
            message = f"⚠️ {error}"
        else:
            log.error("Player button failed", exc_info=error)
            message = "⚠️ Something went wrong. It's been logged."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    async def _redraw(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await self.player.refresh_player()

    async def _vote(self, interaction: discord.Interaction, mix: bool) -> None:
        skipped, message = self.player.vote_skip(interaction.user, mix=mix)
        if skipped:  # the next track redraws the booth
            await interaction.response.send_message(message, ephemeral=True)
        else:
            await self._redraw(interaction)
            await interaction.followup.send(message, ephemeral=True)

    @discord.ui.button(emoji="⏸️", label="Pause", style=discord.ButtonStyle.primary, row=0)
    async def toggle(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.player.resume():
            self.player.pause()
        await self._redraw(interaction)

    @discord.ui.button(emoji="⏭️", label="Skip", style=discord.ButtonStyle.secondary, row=0)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._vote(interaction, mix=False)

    @discord.ui.button(emoji="⏹️", label="Stop", style=discord.ButtonStyle.danger, row=0)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.player.check_in_charge(interaction.user, [self.player.current, *self.player.queue], "stop everyone's music")
        self.player.stop()  # deletes this message
        await interaction.response.defer()

    @discord.ui.button(emoji="🔁", label="Loop", style=discord.ButtonStyle.secondary, row=0)
    async def loop(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.player.loop_mode = _LOOP_NEXT[self.player.loop_mode]
        await self._redraw(interaction)

    @discord.ui.button(emoji="🔀", label="Shuffle", style=discord.ButtonStyle.secondary, row=0)
    async def shuffle(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.player.shuffle()
        await self._redraw(interaction)

    @discord.ui.button(emoji="🎚️", label="Mix now", style=discord.ButtonStyle.primary, row=1)
    async def mix_now(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._vote(interaction, mix=True)

    @discord.ui.button(emoji="〰️", label="Crossfade", style=discord.ButtonStyle.secondary, row=1)
    async def crossfade(self, interaction: discord.Interaction, button: discord.ui.Button):
        current = settings.dj(self.player.guild_id)["crossfade"]
        later = [s for s in _CROSSFADE_STEPS if s > current]
        settings.set_dj(self.player.guild_id, crossfade=later[0] if later else _CROSSFADE_STEPS[0])
        await self._redraw(interaction)

    @discord.ui.button(emoji="🤖", label="Auto-DJ", style=discord.ButtonStyle.secondary, row=1)
    async def autodj(self, interaction: discord.Interaction, button: discord.ui.Button):
        settings.set_dj(self.player.guild_id, autodj=not settings.dj(self.player.guild_id)["autodj"])
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
        self.skip_votes: set[int] = set()  # member IDs voting to skip the current track
        self._lock = asyncio.Lock()
        self._mixer: Mixer | None = None
        self._deck: Deck | None = None  # the deck playing self.current
        self._slot = 1  # booth deck (0 = A, 1 = B) of the current track; alternates like a real DJ
        self._primed: Deck | None = None  # the next track, loaded and waiting on the other deck
        self._priming: asyncio.Task | None = None
        self._last_prime_attempt = 0.0
        self._conductor: asyncio.Task | None = None
        self._stopping = False
        self._mix_until = 0.0
        self._last_booth = 0.0
        self._recent: collections.deque[str] = collections.deque(maxlen=200)  # YouTube IDs, so Auto-DJ doesn't repeat
        self._seed: str | None = None  # the YouTube ID Auto-DJ finds similar songs for
        self._idle_task: asyncio.Task | None = None
        self._message: discord.Message | None = None  # the live booth message
        self._message_lock = asyncio.Lock()
        self._controls: PlayerControls | None = None
        self._filler_failures = 0
        self._filler_retry_at = 0.0
        self._filler_retry_task: asyncio.Task | None = None

    # ── State ───────────────────────────────────────────────────────

    @property
    def voice(self) -> discord.VoiceClient | None:
        guild = self.bot.get_guild(self.guild_id)
        return guild.voice_client if guild else None  # type: ignore[return-value]

    @property
    def active(self) -> bool:
        vc = self.voice
        return bool(vc and (vc.is_playing() or vc.is_paused()))

    @property
    def paused(self) -> bool:
        vc = self.voice
        return bool(vc and vc.is_paused())

    @property
    def elapsed(self) -> float:
        return self._deck.elapsed if self._deck else 0.0

    @property
    def has_next(self) -> bool:
        stay = settings.stay(self.guild_id) or {}
        return bool(self.queue) or settings.dj(self.guild_id)["autodj"] or bool(stay.get("station"))

    @property
    def announce_channel(self) -> discord.abc.Messageable | None:
        """The server's music channel if one is set, otherwise wherever the last command came from."""
        channel_id = settings.music_channel(self.guild_id)
        return (self.bot.get_channel(channel_id) if channel_id else None) or self.channel

    def _crossfade_seconds(self) -> int:
        return settings.dj(self.guild_id)["crossfade"]

    # ── Who may do what ─────────────────────────────────────────────

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
        return all(t.requester_id == member.id for t in affected if t and not (t.filler or t.auto))

    def can_skip_now(self, member: discord.Member) -> bool:
        return self.in_charge(member, [self.current])

    def check_in_charge(self, member: discord.Member, affected: list[Track | None], action: str) -> None:
        if not self.in_charge(member, affected):
            raise UserError(f"Only a server manager can {action} while other people's music is queued. Use Skip to vote instead.")

    def skip_votes_needed(self) -> int:
        return len(self.listeners()) // 2 + 1  # more than half the people listening

    def vote_skip(self, member: discord.Member, mix: bool = False) -> tuple[bool, str]:
        """Skip (or mix out) now if the member may, otherwise count their vote. Returns (skipped, message for them)."""
        if not self.current or not self.active:
            raise UserError("Nothing is playing.")
        if mix and not self.has_next:
            raise UserError("There's nothing to mix into. Queue something first, or turn on Auto-DJ.")
        title = self.current.title
        done = f"🎚️ Mixing out of **{title}**." if mix else f"⏭️ Skipped **{title}**."
        if self.can_skip_now(member):
            self.skip(mix=mix)
            return True, done
        present = {m.id for m in self.listeners()}
        self.skip_votes = (self.skip_votes | {member.id}) & present  # people who left don't count
        votes, needed = len(self.skip_votes), self.skip_votes_needed()
        if votes >= needed:
            self.skip(mix=mix)
            asyncio.create_task(self.notice(f"⏭️ **{title}** was skipped by vote ({votes}/{needed})."))
            return True, done
        return False, f"🗳️ You voted to skip **{title}** ({votes}/{needed}). Others can vote with Skip or `/skip`."

    # ── Messages ────────────────────────────────────────────────────

    async def notice(self, content: str, lifetime: float | None = _NOTICE_LIFETIME) -> None:
        """A short message in the music channel that deletes itself."""
        channel = self.announce_channel
        if channel:
            try:
                await channel.send(content, delete_after=lifetime)
            except discord.HTTPException as e:
                log.warning("Couldn't post in %s: %s", channel, e)

    async def refresh_player(self, repost: bool = False) -> None:
        """Redraw the DJ booth message, or post it fresh at the bottom of the channel."""
        async with self._message_lock:
            channel = self.announce_channel
            if not self.current or not channel:
                return
            if self._controls is None:
                self._controls = PlayerControls(self)
            self._controls.sync()
            self._last_booth = time.monotonic()
            try:
                image, filename = await asyncio.get_running_loop().run_in_executor(None, booth.render, self._booth_view())
            except Exception:
                log.exception("Couldn't draw the booth")
                image = None
            embed = self.now_playing_embed()
            files = []
            if image:
                embed.set_image(url=f"attachment://{filename}")
                files = [discord.File(io.BytesIO(image), filename=filename)]
            if self._message and self._message.channel.id != getattr(channel, "id", None):
                repost = True
            if self._message and not repost:
                try:
                    await self._message.edit(embed=embed, attachments=files, view=self._controls)
                    return
                except discord.NotFound:
                    self._message = None
                except discord.HTTPException as e:
                    log.warning("Couldn't update the player message: %s", e)
                    return
                files = [discord.File(io.BytesIO(image), filename=filename)] if image else []
            await self._delete_message()
            try:
                self._message = await channel.send(embed=embed, files=files, view=self._controls)
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

    def _booth_view(self) -> booth.BoothView:
        decks: list[booth.DeckView | None] = [None, None]
        main, slot = self._deck, self._slot
        others = [d for d in (self._mixer.decks if self._mixer else []) if d is not main and not d.finished]
        spinning = not self.paused
        if main:
            decks[slot] = booth.DeckView(main.track.title, main.elapsed, main.track.duration, spinning, main.gain, True)
        if others:
            out = others[0]
            decks[1 - slot] = booth.DeckView(out.track.title, out.elapsed, out.track.duration, spinning, out.gain, False)
        else:
            cued = self._primed.track if self._primed else (self.queue[0] if self.queue else None)
            if cued:
                decks[1 - slot] = booth.DeckView(cued.title, 0, cued.duration, False, 0.0, False)
        crossfader = float(slot)
        if main and others:  # mid-mix: the fader slides toward the incoming deck
            crossfader = (1 - slot) + (slot - (1 - slot)) * main.gain
        return booth.BoothView(decks=(decks[0], decks[1]), crossfader=crossfader, paused=self.paused, footer="")

    def now_playing_embed(self) -> discord.Embed:
        track = self.current
        embed = discord.Embed(colour=discord.Colour.blurple())
        if not track:
            embed.description = "Nothing is playing."
            return embed
        embed.title = track.title[:256]
        embed.url = track.page_url
        embed.set_author(name=("⏸️ Paused" if self.paused else "▶️ Now playing") + f" · Deck {'AB'[self._slot]}")
        lines = []
        if self.paused:
            lines.append(f"Paused at {fmt_duration(self.elapsed)} / {fmt_duration(track.duration)}")
        elif track.duration:
            ends = int(time.time() + max(0.0, track.duration - self.elapsed))
            lines.append(f"Ends <t:{ends}:R> · {fmt_duration(track.duration)}")
        else:
            lines.append("Live")
        if track.filler:
            lines.append("Playing because of 24/7 mode. `/play` something to take over.")
        elif track.auto:
            lines.append("Picked by 🤖 Auto-DJ")
        elif track.requester:
            lines.append(f"Requested by {track.requester}")
        embed.description = "\n".join(lines)
        if self.queue:
            up_next = "\n".join(
                f"`{i}.` {'🤖 ' if t.auto else ''}{t.title[:60]}" for i, t in enumerate(self.queue[:3], 1)
            )
            if len(self.queue) > 3:
                up_next += f"\n…and {len(self.queue) - 3} more"
            embed.add_field(name="Up next", value=up_next, inline=False)
        dj = settings.dj(self.guild_id)
        footer = [f"Volume {round(self.volume * 100)}%", f"Crossfade {dj['crossfade']}s" if dj["crossfade"] else "Crossfade off"]
        if dj["autodj"]:
            footer.append("Auto-DJ on")
        if settings.stay(self.guild_id):
            footer.append("24/7")
        if self.loop_mode != "off":
            footer.append(f"Looping {self.loop_mode}")
        if self.skip_votes:
            footer.append(f"Skip votes {len(self.skip_votes)}/{self.skip_votes_needed()}")
        embed.set_footer(text=" · ".join(footer))
        return embed

    # ── Queue ───────────────────────────────────────────────────────

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
        """Queue tracks ahead of any Auto-DJ picks; returns the queue position (1-based) of the first one."""
        self.queue = [t for t in self.queue if not t.filler]  # the 24/7 station only fills an empty queue
        at = 0 if front else next((i for i, t in enumerate(self.queue) if t.auto), len(self.queue))
        self.queue[at:at] = new
        return at + 1

    def shuffle(self) -> None:
        mine = [t for t in self.queue if not (t.auto or t.filler)]
        if len(mine) < 2:
            raise UserError("There's not enough in the queue to shuffle.")
        random.shuffle(mine)
        self.queue = mine + [t for t in self.queue if t.auto or t.filler]

    def prefetch(self) -> None:
        """Look up the next YouTube track in the background, so it starts quickly."""
        upcoming = self.queue[0] if self.queue else None
        if upcoming and upcoming.kind == "ytdl" and not upcoming.resolved:
            task = asyncio.create_task(tracks.stream_for(upcoming))
            task.add_done_callback(lambda t: t.cancelled() or t.exception())  # failures retry when it starts

    # ── Playback ────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start playing if nothing is."""
        async with self._lock:
            vc = self.voice
            if not vc or not vc.is_connected() or self._deck:
                return
            if not self._mixer:
                if vc.is_playing() or vc.is_paused():
                    return
                self._mixer = Mixer(self.volume)
                try:
                    vc.play(self._mixer, after=self._session_ended)
                except discord.ClientException:
                    log.exception("Couldn't start playback")
                    self._mixer = None
                    return
                self._conductor = asyncio.create_task(self._conduct())
            self._mixer.hold = True
            await self._load_next(frames(_START_FADE))

    async def _load_next(self, fade_in: int) -> bool:
        """Put the next track on the free deck and fade it in. Call with the lock held."""
        if self._priming:
            try:
                await self._priming
            except Exception:
                pass
        mixer = self._mixer
        while mixer and not self._stopping:
            track = await self._next_track()
            if track is None:
                break
            deck = self._take_primed(track) or await self._open_deck(track)
            if deck is None:
                continue
            if self._stopping or mixer is not self._mixer:
                deck.close()
                return False
            deck.fade_in(fade_in)
            mixer.add(deck)
            self.current, self._deck = track, deck
            self._slot ^= 1
            self.skip_votes.clear()
            self._remember(track)
            await self.refresh_player(repost=True)
            self.prefetch()
            return True
        if mixer:
            mixer.hold = False  # nothing left: the stream ends once the last deck fades out
        return False

    async def _next_track(self) -> Track | None:
        if self.queue:
            return self.queue.pop(0)
        auto = await self._autodj_pick()
        if auto:
            return auto
        if self._queue_filler():
            return self.queue.pop(0)
        return None

    async def _peek_next(self) -> Track | None:
        """What will play next, queueing an Auto-DJ pick or the 24/7 station if the queue is empty."""
        if self.loop_mode == "track" and self.current and not self.current.filler:
            return self.current
        if self.queue:
            return self.queue[0]
        auto = await self._autodj_pick()
        if auto:
            self.queue.append(auto)
            return auto
        if self._queue_filler():
            return self.queue[0]
        return None

    async def _open_deck(self, track: Track, quiet: bool = False) -> Deck | None:
        try:
            playable = await tracks.stream_for(track)
            audio = _audio_for(playable, track)
        except TrackError as e:
            if not quiet:
                if track.filler:
                    await self._filler_failed(str(e))
                else:
                    await self.notice(f"⚠️ Skipped **{track.title}**: {e}")
            return None
        except Exception:
            log.exception("Couldn't load %r", track)
            if not quiet:
                await self.notice(f"⚠️ Skipped **{track.title}**: something went wrong loading it.")
            return None
        return Deck(track, audio, lookahead=frames(_LOOKAHEAD + self._crossfade_seconds()))

    def _take_primed(self, track: Track) -> Deck | None:
        primed, self._primed = self._primed, None
        if primed and primed.track is track:
            return primed
        if primed:
            primed.close()
        return None

    def _close_primed(self) -> None:
        if self._primed:
            self._primed.close()
            self._primed = None

    async def _prime(self) -> None:
        """Load the next track onto the other deck ahead of time, so the mix starts on cue."""
        try:
            candidate = await self._peek_next()
            if candidate is None or (self._primed and self._primed.track is candidate):
                return
            deck = await self._open_deck(candidate, quiet=True)
            if deck is None:
                return
            if self._stopping or not self._mixer:
                deck.close()
                return
            self._close_primed()
            self._primed = deck
            await self.refresh_player()  # show it cued up on the other deck
        finally:
            self._priming = None

    def _should_prime(self, deck: Deck) -> bool:
        if deck.track.kind == "stream":
            return False  # radio doesn't end; the next track loads when someone skips
        if deck.remaining is not None:
            return True
        return bool(deck.track.duration) and deck.elapsed >= deck.track.duration - self._crossfade_seconds() - _PRIME_AHEAD

    async def _conduct(self) -> None:
        """Watches the playing deck: loads the next track early, starts each mix on cue, keeps the booth fresh."""
        try:
            while self._mixer:
                await asyncio.sleep(_TICK)
                vc, deck = self.voice, self._deck
                if not vc or not deck or vc.is_paused() or self._lock.locked():
                    continue
                now = time.monotonic()
                expected = self.current if self.loop_mode == "track" else (self.queue[0] if self.queue else None)
                primed_ok = self._primed and (expected is None or self._primed.track is expected)
                if (self._should_prime(deck) and not self._priming and not primed_ok
                        and now - self._last_prime_attempt > _PRIME_RETRY):
                    self._last_prime_attempt = now
                    self._priming = asyncio.create_task(self._prime())
                fade = frames(self._crossfade_seconds())
                remaining = deck.remaining
                if deck.finished or (fade and remaining is not None and remaining <= fade and self.has_next):
                    await self._advance(deck, skipped=False)
                    continue
                if deck.played == 0 and not deck.eof and now - deck.created > _STALL_LIMIT:
                    await self.notice(f"⚠️ **{deck.track.title}** never started, so I've moved on.")
                    await self._advance(deck, skipped=True)
                    continue
                every = _BOOTH_MIXING_EVERY if now < self._mix_until else _BOOTH_EVERY
                if now - self._last_booth >= every and not self._message_lock.locked():
                    asyncio.create_task(self.refresh_player())
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("The conductor stopped")

    async def _advance(self, deck: Deck, skipped: bool, mix: bool = False) -> None:
        """Move on from `deck`: fade it out and bring in the next track (a crossfade unless it's a plain skip)."""
        async with self._lock:
            if deck is not self._deck:
                return  # already moved on (e.g. two quick skips)
            finished = self.current
            crossfade = frames(self._crossfade_seconds())
            ended = deck.finished
            remaining = deck.remaining
            if skipped and not mix:
                fade_out, fade_in = frames(_SKIP_FADE), frames(_START_FADE)
            elif crossfade and not ended:
                length = crossfade if remaining is None or mix else max(1, min(crossfade, remaining))
                fade_out = fade_in = length
            else:
                fade_out, fade_in = frames(_SKIP_FADE), frames(_START_FADE)
            if not ended:
                deck.fade_out(fade_out)
            self.current = self._deck = None
            self._mix_until = time.monotonic() + fade_out * FRAME_SECONDS

            if finished.filler:
                if not skipped and deck.elapsed < _QUICK_FAIL:
                    await self._filler_failed("it stopped straight away")
                elif not skipped:
                    self._filler_failures = 0
            elif not skipped and deck.elapsed < _QUICK_FAIL:
                finished.resolved = None  # maybe expired; refetch next time
                await self.notice(f"⚠️ **{finished.title}** stopped straight away, so I've moved on.")
            elif self.loop_mode == "track" and not skipped:
                self.queue.insert(0, finished)
            elif self.loop_mode == "queue":
                self.queue.append(finished)
            await self._load_next(fade_in)

    def _session_ended(self, error: Exception | None) -> None:
        # Runs on the audio thread when the mixer stream stops.
        if error:
            log.error("Playback error: %s", error)
        asyncio.run_coroutine_threadsafe(self._on_session_end(), self.bot.loop)

    async def _on_session_end(self) -> None:
        if self._conductor and self._conductor is not asyncio.current_task():
            self._conductor.cancel()
        self._conductor = None
        if self._mixer:
            self._mixer.clear()
        self._mixer = None
        self.current = self._deck = None
        self._close_primed()
        stopping, self._stopping = self._stopping, False
        station = (settings.stay(self.guild_id) or {}).get("station")
        vc = self.voice
        if vc and vc.is_connected() and (self.queue or (stopping and station)):
            await self.start()  # e.g. music queued during the last fade, or back to the 24/7 station after /stop
            if self.current:
                return
        await self.clear_player()

    def pause(self) -> bool:
        vc = self.voice
        if vc and vc.is_playing():
            vc.pause()
            return True
        return False

    def resume(self) -> bool:
        vc = self.voice
        if vc and vc.is_paused():
            vc.resume()
            return True
        return False

    def skip(self, mix: bool = False) -> bool:
        if not self._deck:
            return False
        asyncio.create_task(self._advance(self._deck, skipped=True, mix=mix))
        return True

    def stop(self) -> None:
        self.queue.clear()
        self._close_primed()
        if self._mixer:
            self._stopping = True
            self._mixer.hold = False
            self._mixer.clear()
            self.current = self._deck = None
            if self.paused:
                self.voice.resume()  # a paused stream never reaches its end

    def set_volume(self, percent: int) -> None:
        self.volume = percent / 100
        if self._mixer:
            self._mixer.volume = self.volume

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
        self._close_primed()
        if self._mixer:
            self._mixer.clear()
        self._mixer = None
        if self._conductor and self._conductor is not asyncio.current_task():
            self._conductor.cancel()
        self._conductor = None
        self.current = self._deck = None
        self._stopping = False
        if self._idle_task and self._idle_task is not asyncio.current_task():
            self._idle_task.cancel()
        self._idle_task = None
        asyncio.create_task(self.clear_player())

    # ── Auto-DJ ─────────────────────────────────────────────────────

    def _remember(self, track: Track) -> None:
        video = tracks.video_id(track.page_url) or tracks.video_id(track.source)
        if video:
            self._recent.append(video)
            if not track.filler:
                self._seed = video

    async def _autodj_pick(self) -> Track | None:
        """A song similar to the last one, from YouTube's Mix for it."""
        if not settings.dj(self.guild_id)["autodj"] or not self._seed:
            return None
        try:
            options = await tracks.related(self._seed)
        except TrackError as e:
            log.warning("Auto-DJ couldn't find songs: %s", e)
            return None
        low, high = _AUTODJ_LENGTH
        for track in options:
            video = tracks.video_id(track.source)
            if video and video not in self._recent and track.duration and low <= track.duration <= high:
                self._recent.append(video)
                track.auto = True
                return track
        return None

    # ── 24/7 station ────────────────────────────────────────────────

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
        self.queue = [t for t in self.queue if not t.filler]
        if self.current and self.current.filler:
            self.skip()

    # ── Auto-leave ──────────────────────────────────────────────────

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
