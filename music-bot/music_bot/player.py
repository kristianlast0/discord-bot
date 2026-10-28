"""One player per server: the queue, the two-deck mixer, the DJ booth message and auto-leave."""
import asyncio
import base64
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
import numpy as np

from . import booth, config, dsp, linein, radio, settings, store, tracks
from .mixer import FRAME_SECONDS, NUDGE, PITCH_RANGE, Deck, Mixer, frames
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
_LIVE_BOOTH_EVERY = 4  # seconds between booth redraws while someone mixes live
_GRID_FILE = "beatgrids.json"  # cached (BPM, beat time) per song
_ANALYSE_FROM, _ANALYSE_SECONDS = 10, 120  # which part of a song the beat grid is worked out from
_WAVE_COLUMNS = 800  # width of the whole-track waveform
_DETAIL_RATE = 100  # waveform peaks per second, for the zoomed view
_AUTO_MATCH_RANGE = 0.08  # automatic mixes only beat-match songs within ±8% of each other's tempo
_PITCH_GLIDE = 0.0005  # per tick (0.2%/s): after an automatic beat-matched mix, ease back to normal speed


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


def _beat_grid_for(deck: Deck) -> tuple[float, float] | None:
    """Beat grid from the deck's own audio (blocking; run in an executor), so it lines up exactly with playback."""
    rate = 48000
    start = _ANALYSE_FROM * rate if deck.loaded > _ANALYSE_FROM + 30 else 0
    pcm = deck.store.read_raw(start, _ANALYSE_SECONDS * rate)
    mono = pcm.astype(np.float32).mean(axis=1) / 32768
    mono = mono[: len(mono) // 4 * 4].reshape(-1, 4).mean(axis=1)  # 48 kHz -> 12 kHz
    return dsp.beat_grid(mono, dsp.ANALYSIS_RATE, start=start / rate)


def _waveform_for(deck: Deck) -> dict:
    """Peaks for the whole-track waveform and the zoomed view (blocking; run in an executor)."""
    rate, chunk = 48000, 48000 * 10
    detail: list[int] = []
    for start in range(0, deck.store.written, chunk):
        pcm = deck.store.read_raw(start, chunk)
        detail += dsp.overview(pcm, max(1, round(len(pcm) / rate * _DETAIL_RATE)))
    peaks = np.array(detail or [0], dtype=np.uint8)
    edges = np.linspace(0, len(peaks), _WAVE_COLUMNS + 1).astype(int)
    overview = np.maximum.reduceat(peaks, np.minimum(edges[:-1], len(peaks) - 1))
    return {
        "overview": overview.tolist(),
        "detail": base64.b64encode(peaks.tobytes()).decode(),
        "detail_rate": _DETAIL_RATE,
        "length": deck.store.written / rate,
    }


def _audio_for(playable: tracks.Playable, track: Track) -> discord.AudioSource:
    options = _audio_options(track)
    if playable.info:
        return YtdlpAudio(playable.info, options)
    remote = playable.url.startswith(("http://", "https://"))
    return discord.FFmpegPCMAudio(playable.url, before_options=_NET_BEFORE if remote else "", options=options)


async def send_mixer_link(interaction: discord.Interaction) -> None:
    """Reply privately with the person's own mixer link."""
    from .web import mixer_link  # web imports this module

    link = await mixer_link(interaction.guild_id, interaction.user.id)
    if not link:
        raise UserError("The web mixer isn't reachable right now (ngrok isn't running on the bot's computer).")
    view = discord.ui.View()
    view.add_item(discord.ui.Button(label="Open the mixer", emoji="🎛️", url=link))
    await interaction.response.send_message(
        "Your personal mixer link (valid 12 hours, don't share it, it acts as you). "
        "Anyone can watch; to take the decks you need to be in the bot's voice channel.",
        view=view, ephemeral=True,
    )


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
        self.mix_now.disabled = not p.has_next or bool(p.live_dj)
        for item in (self.skip, self.loop, self.shuffle, self.crossfade, self.autodj):
            item.disabled = bool(p.live_dj)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.data.get("custom_id") != self.mixer.custom_id:  # anyone may open the mixer to watch
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

    @discord.ui.button(emoji="🎛️", label="Mixer", style=discord.ButtonStyle.secondary, row=1)
    async def mixer(self, interaction: discord.Interaction, button: discord.ui.Button):
        await send_mixer_link(interaction)


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
        self.live_dj: tuple[int, str] | None = None  # (member ID, name) of whoever is mixing live
        self.consoles = 0  # open mixer pages; tempo detection only runs when someone might use it
        self.queue_version = 0  # bumped whenever the queue changes, so mixer pages know to redraw it
        self.quantize = True  # live jumps and cue points snap to beats
        self._analysing: set[int] = set()  # id() of decks being analysed

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

    def check_not_live(self) -> None:
        if self.live_dj:
            raise UserError(f"🎧 {self.live_dj[1]} is mixing live, so the decks are theirs. Open the mixer with `/mixer` to watch.")

    def skip_votes_needed(self) -> int:
        return len(self.listeners()) // 2 + 1  # more than half the people listening

    def vote_skip(self, member: discord.Member, mix: bool = False) -> tuple[bool, str]:
        """Skip (or mix out) now if the member may, otherwise count their vote. Returns (skipped, message for them)."""
        self.check_not_live()
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

    def _on_air(self, include_line_in: bool = True) -> tuple[int, Deck] | None:
        """The (slot, deck) people mostly hear."""
        mixer = self._mixer
        if not mixer:
            return None
        best, best_gain = None, 0.0
        for slot, deck in enumerate(mixer.slots):
            if deck and deck.playing and not deck.finished and (include_line_in or not deck.live_input):
                gain = deck.volume * deck.gain * (mixer.crossfader_gain(slot) if mixer.manual else 1.0)
                if gain > best_gain:
                    best, best_gain = (slot, deck), gain
        return best

    def _booth_view(self) -> booth.BoothView:
        decks: list[booth.DeckView | None] = [None, None]
        mixer, main, slot = self._mixer, self._deck, self._slot
        spinning = not self.paused
        for i, deck in enumerate(mixer.slots if mixer else (None, None)):
            if not deck or (deck.finished and not (mixer and mixer.manual)):
                continue
            level = deck.volume * deck.gain * (mixer.crossfader_gain(i) if mixer.manual else 1.0)
            decks[i] = booth.DeckView(deck.track.title, deck.elapsed, deck.track.duration,
                                      spinning and deck.playing and not deck.finished, level if deck.playing else 0.0, deck is main)
        idle = 1 - slot
        if decks[idle] is None and self.queue and not (mixer and mixer.manual):
            cued = self.queue[0]
            decks[idle] = booth.DeckView(cued.title, 0, cued.duration, False, 0.0, False)
        if mixer and mixer.manual:
            crossfader = mixer.crossfader
        else:
            crossfader = float(slot)
            other = mixer.slots[idle] if mixer else None
            if main and other and other.playing and not other.finished:  # mid-mix: the fader slides toward the incoming deck
                crossfader = idle + (slot - idle) * main.gain
        return booth.BoothView(decks=(decks[0], decks[1]), crossfader=crossfader, paused=self.paused, footer="")

    def now_playing_embed(self) -> discord.Embed:
        track = self.current
        embed = discord.Embed(colour=discord.Colour.blurple())
        if not track:
            embed.description = "Nothing is playing."
            return embed
        embed.title = track.title[:256]
        embed.url = track.page_url
        state = "⏸️ Paused" if self.paused else f"🎧 Live: {self.live_dj[1]}" if self.live_dj else "▶️ Now playing"
        embed.set_author(name=f"{state} · Deck {'AB'[self._slot]}")
        lines = []
        if self.paused:
            lines.append(f"Paused at {fmt_duration(self.elapsed)} / {fmt_duration(track.duration)}")
        elif track.duration:
            ends = int(time.time() + max(0.0, track.duration - self.elapsed))
            lines.append(f"Ends <t:{ends}:R> · {fmt_duration(track.duration)}")
        else:
            lines.append("Live")
        if self.live_dj:
            lines.append(f"{self.live_dj[1]} is mixing live. `/mixer` to watch the decks.")
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
        self.queue_version += 1
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

    def _ensure_mixer(self) -> Mixer | None:
        """Start the stream to Discord if it isn't running."""
        vc = self.voice
        if not vc or not vc.is_connected():
            return None
        if not self._mixer:
            if vc.is_playing() or vc.is_paused():
                return None
            self._mixer = Mixer(self.volume)
            try:
                vc.play(self._mixer, after=self._session_ended)
            except discord.ClientException:
                log.exception("Couldn't start playback")
                self._mixer = None
                return None
            self._conductor = asyncio.create_task(self._conduct())
        self._mixer.hold = True
        return self._mixer

    async def start(self) -> None:
        """Start playing if nothing is (in live mode the DJ decides what plays)."""
        async with self._lock:
            if self._deck or self.live_dj or not self._ensure_mixer():
                return
            await self._load_next(frames(_START_FADE))

    async def _load_next(self, fade_in: int, match: Deck | None = None) -> bool:
        """Put the next track on the free deck and fade it in, beat-matched to `match` if given. Call with the lock held."""
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
            if match:
                self._beatmatch(deck, match)
            deck.playing = True
            slot = mixer.slot_of(deck)
            if slot is None:
                slot = 1 - self._slot
                mixer.load(slot, deck)
            self.current, self._deck, self._slot = track, deck, slot
            self.skip_votes.clear()
            self.queue_version += 1
            asyncio.create_task(self._analyse(deck))
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
        if self._primed and self._primed.track is track:
            primed, self._primed = self._primed, None
            return primed
        self._close_primed()
        return None

    def _close_primed(self) -> None:
        primed, self._primed = self._primed, None
        if not primed:
            return
        slot = self._mixer.slot_of(primed) if self._mixer else None
        if slot is not None:
            self._mixer.load(slot, None)
        else:
            primed.close()

    async def _prime(self) -> None:
        """Load the next track onto the other deck ahead of time, so the mix starts on cue."""
        try:
            candidate = await self._peek_next()
            if candidate is None or (self._primed and self._primed.track is candidate):
                return
            deck = await self._open_deck(candidate, quiet=True)
            if deck is None:
                return
            if self._stopping or not self._mixer or self.live_dj:
                deck.close()
                return
            self._close_primed()
            deck.playing = False  # cued on the other deck until the mix
            self._mixer.load(1 - self._slot, deck)
            self._primed = deck
            asyncio.create_task(self._analyse(deck))
            await self.refresh_player()  # show it cued up on the other deck
        finally:
            self._priming = None

    def _should_prime(self, deck: Deck) -> bool:
        if deck.track.kind == "stream":
            return False  # radio doesn't end; the next track loads when someone skips
        if deck.remaining is not None:
            return deck.remaining <= frames(self._crossfade_seconds() + _PRIME_AHEAD)
        return bool(deck.track.duration) and deck.elapsed >= deck.track.duration - self._crossfade_seconds() - _PRIME_AHEAD

    async def _conduct(self) -> None:
        """Watches the playing deck: loads the next track early, starts each mix on cue, keeps the booth fresh."""
        try:
            while self._mixer:
                await asyncio.sleep(_TICK)
                vc, deck = self.voice, self._deck
                now = time.monotonic()
                if self.live_dj:
                    self._follow_live(now)
                    continue
                if not vc or not deck or vc.is_paused() or self._lock.locked():
                    continue
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
                others = [d for d in self._mixer.decks if d is not deck and d.playing and not d.finished]
                if deck.pitch != 1.0 and not others:  # after a beat-matched mix, drift back to normal speed
                    step = min(_PITCH_GLIDE, abs(1.0 - deck.pitch))
                    deck.pitch += step if deck.pitch < 1.0 else -step
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
            crossfading = not ended and (mix or not skipped) and crossfade
            await self._load_next(fade_in, match=deck if crossfading else None)

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
        self._primed = None
        self.live_dj = None
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
        self.queue_version += 1
        self.live_dj = None
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
        self.live_dj = None
        if self._idle_task and self._idle_task is not asyncio.current_task():
            self._idle_task.cancel()
        self._idle_task = None
        asyncio.create_task(self.clear_player())

    # ── Live mixing (the web mixer) ─────────────────────────────────

    def _follow_live(self, now: float) -> None:
        """In live mode, "now playing" is whichever deck is loudest in the mix."""
        on_air = self._on_air()
        if on_air and on_air[1] is not self._deck:
            self._slot, self._deck = on_air
            self.current = self._deck.track
            self._remember(self.current)
            self._last_booth = 0  # redraw now
        slots = self._mixer.slots
        for slot, deck in enumerate(slots):
            master = slots[1 - slot]
            if deck and deck.sync_lock:
                if master and master.grid and deck.grid:
                    self._hold_sync(deck, master)
                else:
                    deck.nudge = 0.0
        if now - self._last_booth >= _LIVE_BOOTH_EVERY and not self._message_lock.locked() and self.current:
            asyncio.create_task(self.refresh_player())

    async def live_take(self, member: discord.Member) -> None:
        """Hand the decks to `member`. Whatever is playing carries on exactly as it sounds."""
        if self.live_dj and self.live_dj[0] != member.id and not member.guild_permissions.manage_guild:
            raise UserError(f"{self.live_dj[1]} is on the decks right now.")
        self.check_listener(member)
        async with self._lock:
            mixer = self._ensure_mixer()
            if not mixer:
                raise UserError("I couldn't start the audio. Try `/play` first.")
            if self._priming:
                self._priming.cancel()
            self._primed = None
            for slot, deck in enumerate(mixer.slots):
                if deck and deck is not self._deck and deck.fading_out:
                    mixer.load(slot, None)  # a half-finished automatic fade: drop it
                elif deck:
                    deck.clear_fades()
            mixer.crossfader = float(self._slot) if self._deck else 0.0
            mixer.manual = True
            self.live_dj = (member.id, member.display_name)
            for deck in mixer.decks:
                if deck.grid is None:
                    asyncio.create_task(self._analyse(deck))
        await self.notice(f"🎧 {member.display_name} has taken the decks and is mixing live.", lifetime=60)
        await self.refresh_player()

    async def live_release(self) -> None:
        """Back to automatic mixing: the deck on air carries on, anything else fades away."""
        if not self.live_dj:
            return
        name = self.live_dj[1]
        async with self._lock:
            self.live_dj = None
            mixer = self._mixer
            if not mixer:
                return
            on_air = self._on_air(include_line_in=False)  # a DJ's line-in leaves with them
            for slot, deck in enumerate(mixer.slots):
                if not deck or (on_air and deck is on_air[1]):
                    continue
                if deck.live_input:
                    deck.fade_out(frames(2))
                    asyncio.create_task(self._eject_later(deck, 2.5))  # its fade stalls if the audio has stopped
                elif deck.playing and not deck.finished:
                    deck.fade_out(frames(2))
                else:
                    mixer.load(slot, None)
            mixer.manual = False
            mixer.crossfader = float(on_air[0]) if on_air else 0.0
            for deck in mixer.decks:
                deck.sync_lock, deck.nudge = False, 0.0
            if on_air:
                self._slot, self._deck = on_air
                self.current = self._deck.track
                self._deck.clear_fades()
            else:
                self.current = self._deck = None
        if not on_air:
            await self.start()
        await self.notice(f"🎛️ {name} has left the decks. Back to automatic mixing.", lifetime=60)
        await self.refresh_player()

    async def linein_start(self, member: discord.Member, audio: discord.AudioSource) -> None:
        """Put a DJ's own decks on air: their line-in goes on a deck and the crossfader glides over to it."""
        if self.live_dj and self.live_dj[0] != member.id and not member.guild_permissions.manage_guild:
            raise UserError(f"{self.live_dj[1]} is on the decks right now.")
        self.check_listener(member)
        async with self._lock:
            mixer = self._ensure_mixer()
            if not mixer:
                raise UserError("I couldn't start the audio.")
            if self._priming:
                self._priming.cancel()
            self._primed = None
            track = Track(f"🎙️ {member.display_name} {linein.TITLE_SUFFIX}", "stream", linein.SOURCE, requester=member.mention)
            deck = Deck(track, audio, lookahead=frames(5))
            deck.live_input = True
            # A reconnect replaces the old line-in deck; otherwise use the deck that isn't on air.
            old = next((i for i, d in enumerate(mixer.slots) if d and d.track.source == linein.SOURCE), None)
            slot = old if old is not None else (1 - self._slot if self._deck else 0)
            for d in mixer.decks:
                d.clear_fades()
                d.sync_lock = False
            mixer.load(slot, deck)
            first_time = not (self.live_dj and mixer.manual)
            mixer.manual = True
            if first_time:
                mixer.crossfader = float(1 - slot)
                mixer.move_crossfader(float(slot), 3.0)  # fade from whatever was playing into the DJ
            else:
                mixer.crossfader = float(slot)
            self.live_dj = (member.id, member.display_name)
            self.current, self._deck, self._slot = track, deck, slot
        if first_time:
            await self.notice(f"🎙️ {member.display_name} is live from their own decks.", lifetime=60)
        await self.refresh_player(repost=first_time)

    async def linein_stop(self, member_id: int) -> None:
        """The DJ stopped their line-in: fade it out and go back to automatic mixing."""
        mixer = self._mixer
        if not mixer or not self.live_dj or self.live_dj[0] != member_id:
            return
        await self.live_release()

    async def _eject_later(self, deck: Deck, seconds: float) -> None:
        await asyncio.sleep(seconds)
        slot = self._mixer.slot_of(deck) if self._mixer else None
        if slot is not None:
            self._mixer.load(slot, None)

    def _live_deck(self, slot: int) -> Deck:
        if not self.live_dj or not self._mixer:
            raise UserError("Take the decks first.")
        deck = self._mixer.slots[slot]
        if not deck:
            raise UserError(f"Deck {'AB'[slot]} is empty. Load a track onto it first.")
        return deck

    async def deck_load(self, slot: int, track: Track) -> None:
        if not self.live_dj or not self._mixer:
            raise UserError("Take the decks first.")
        deck = await self._open_deck(track, quiet=True)
        if deck is None:
            raise UserError(f"Couldn't load **{track.title}**.")
        deck.playing = False
        old = self._mixer.slots[slot]
        if old:  # a new record keeps the deck's settings, like a real mixer channel
            deck.volume, deck.pitch, deck.filters = old.volume, old.pitch, old.filters
        self._mixer.load(slot, deck)
        if old is self._deck:
            self._deck, self.current = deck, track
        asyncio.create_task(self._analyse(deck))

    def deck_cue(self, slot: int) -> None:
        """Like a CDJ: while playing, jump back to the cue point and stop; while stopped, set the cue point here."""
        deck = self._live_deck(slot)
        if not deck.seekable:
            raise UserError("Radio can't be cued.")
        if deck.playing:
            deck.playing = False
            deck.seek(deck.cue_point)
        else:
            deck.cue_point = deck.seek(self._snap(deck, deck.elapsed))

    def deck_play(self, slot: int, playing: bool) -> None:
        deck = self._live_deck(slot)
        if deck.finished:
            raise UserError("That track has finished. Jump back or load another.")
        deck.playing = playing

    def _snap(self, deck: Deck, seconds: float) -> float:
        """With quantise on, land exactly on a beat."""
        if not self.quantize or not deck.grid:
            return seconds
        period, first = deck.beat_period, deck.grid[1]
        return first + round((seconds - first) / period) * period

    def deck_seek(self, slot: int, seconds: float) -> float:
        deck = self._live_deck(slot)
        if not deck.seekable:
            raise UserError("Radio is live, so it can't jump around.")
        if self.quantize and deck.grid and deck.playing:
            # Keep the beat going: move by a whole number of beats, so the deck stays in time.
            period = deck.beat_period
            seconds = deck.elapsed + round((seconds - deck.elapsed) / period) * period
        elif not deck.playing:
            seconds = self._snap(deck, seconds)
        return deck.seek(seconds)

    def deck_beatjump(self, slot: int, beats: float) -> float:
        deck = self._live_deck(slot)
        if not deck.seekable:
            raise UserError("Radio is live, so it can't jump around.")
        if not deck.grid:
            raise UserError("Beat jump needs the beat grid, which appears a few seconds after loading.")
        return deck.seek(deck.elapsed + beats * deck.beat_period)

    def deck_set(self, slot: int, param: str, value: float) -> None:
        deck = self._live_deck(slot)
        if param == "volume":
            deck.volume = min(1.0, max(0.0, value))
        elif param in ("low", "mid", "high"):
            setattr(deck.filters, param, min(6.0, max(dsp.EQ_KILL, value)))
        elif param == "filter":
            deck.filters.filter = min(1.0, max(-1.0, value))
        elif param == "pitch":
            if deck.sync_lock:
                raise UserError("This deck is locked to the other one. Turn off Lock to move its pitch.")
            deck.pitch = min(1 + PITCH_RANGE, max(1 - PITCH_RANGE, value))
        elif param == "nudge":
            if not deck.sync_lock:
                deck.nudge = min(NUDGE, max(-NUDGE, value))
        else:
            raise UserError(f"Unknown control {param}.")

    @staticmethod
    def _match(deck: Deck, master: Deck, limit: float) -> tuple[float, float] | None:
        """(pitch, multiple) that brings `deck` to `master`'s tempo, counting half and double time; None if too far."""
        target = master.effective_bpm
        options = [(target * m / deck.bpm, m) for m in (1.0, 2.0, 0.5)]
        pitch, mult = min(options, key=lambda o: abs(o[0] - 1))
        return (pitch, mult) if abs(pitch - 1) <= limit else None

    @staticmethod
    def _beat_error(deck: Deck, master: Deck) -> float:
        """How far `deck`'s beats are from `master`'s, in seconds of deck time (positive: deck is behind)."""
        master_phase = master.beat_phase()
        if deck.sync_mult == 2.0:
            target = (master_phase * 2) % 1
        elif deck.sync_mult == 0.5:  # one deck beat per two master beats: either of two spots works
            here = deck.beat_phase()
            target = min((master_phase / 2, (master_phase + 1) / 2), key=lambda t: abs(((t - here + 0.5) % 1) - 0.5))
        else:
            target = master_phase
        return (((target - deck.beat_phase() + 0.5) % 1) - 0.5) * deck.beat_period

    def deck_sync(self, slot: int) -> float:
        """Match this deck's tempo to the other deck's and line its beats up. Returns the new pitch."""
        deck, master = self._live_deck(slot), self._mixer.slots[1 - slot]
        if not deck.grid or not master or not master.grid:
            raise UserError("Both decks need their beat grid first (it appears a few seconds after loading).")
        found = self._match(deck, master, PITCH_RANGE)
        if not found:
            raise UserError(f"{deck.bpm:g} and {master.effective_bpm:g} BPM are too far apart to sync (more than ±{PITCH_RANGE:.0%}).")
        deck.pitch, deck.sync_mult = found
        if deck.seekable:
            deck.seek(deck.elapsed + self._beat_error(deck, master))
        return deck.pitch

    def deck_lock(self, slot: int, on: bool) -> None:
        """Keep this deck's tempo and beats locked to the other deck (until turned off)."""
        deck, master = self._live_deck(slot), self._mixer.slots[1 - slot]
        if on:
            self.deck_sync(slot)
            if master:
                master.sync_lock = False
        deck.sync_lock = on
        deck.nudge = 0.0

    def _hold_sync(self, deck: Deck, master: Deck) -> None:
        """Called a few times a second for a locked deck: follow the master's pitch and gently correct any drift."""
        deck.pitch = min(1 + PITCH_RANGE, max(1 - PITCH_RANGE, master.effective_bpm * deck.sync_mult / deck.bpm))
        if not (deck.playing and master.playing):
            deck.nudge = 0.0
            return
        error = self._beat_error(deck, master)
        deck.nudge = max(-0.03, min(0.03, error * 2)) if abs(error) > 0.004 else 0.0

    def _beatmatch(self, incoming: Deck, outgoing: Deck) -> None:
        """For automatic mixes: match the incoming song's tempo to the outgoing one and start it on the beat."""
        if not settings.dj(self.guild_id)["beatmatch"] or not incoming.seekable:
            return
        if not incoming.grid or not outgoing.grid or not outgoing.playing:
            return
        found = self._match(incoming, outgoing, _AUTO_MATCH_RANGE)
        if not found:
            return
        incoming.pitch, incoming.sync_mult = found
        start = incoming.elapsed + self._beat_error(incoming, outgoing)
        if start < 0:
            start += incoming.beat_period
        incoming.seek(start)

    def mixer_set(self, param: str, value) -> None:
        if not self.live_dj or not self._mixer:
            raise UserError("Take the decks first.")
        mixer = self._mixer
        if param == "crossfader":
            mixer._move = None
            mixer.crossfader = min(1.0, max(0.0, float(value)))
        elif param == "curve":
            mixer.curve = "cut" if value == "cut" else "smooth"
        elif param == "master":
            mixer.master = min(1.5, max(0.0, float(value)))
        elif param == "quantize":
            self.quantize = bool(value)
        elif param == "autofade":  # glide the crossfader to deck `value[0]` over value[1] seconds
            target, seconds = value
            mixer.move_crossfader(float(target), min(60.0, max(0.5, float(seconds))))
        else:
            raise UserError(f"Unknown control {param}.")

    def take_from_queue(self, index: int) -> Track:
        if not 0 <= index < len(self.queue):
            raise UserError("That track isn't in the queue any more.")
        return self.queue.pop(index)

    async def _analyse(self, deck: Deck) -> None:
        """Work out a deck's beat grid and waveform in the background, from its own downloaded audio."""
        if not deck.seekable or id(deck) in self._analysing:
            return
        self._analysing.add(id(deck))
        try:
            track = deck.track
            key = tracks.video_id(track.page_url) or tracks.video_id(track.source) or f"{track.kind}:{track.source}"
            loop = asyncio.get_running_loop()
            need = _ANALYSE_FROM + _ANALYSE_SECONDS
            while not deck.store.done and deck.loaded < need:
                if deck not in (self._mixer.slots if self._mixer else ()):
                    return  # ejected meanwhile
                await asyncio.sleep(0.5)
            if deck.grid is None:
                cached = store.load(_GRID_FILE, {}).get(key)
                if cached:
                    deck.grid = tuple(cached)
                else:
                    grid = await loop.run_in_executor(None, _beat_grid_for, deck)
                    if grid:
                        deck.grid = grid
                        cache = store.load(_GRID_FILE, {})
                        cache[key] = list(grid)
                        store.save(_GRID_FILE, cache)
            while not deck.store.done:
                if deck not in (self._mixer.slots if self._mixer else ()):
                    return
                await asyncio.sleep(1)
            deck.wave = await loop.run_in_executor(None, _waveform_for, deck)
        except Exception:
            log.exception("Analysis failed for %s", deck.track.title)
        finally:
            self._analysing.discard(id(deck))

    def wave_for(self, slot: int) -> dict | None:
        deck = self._mixer.slots[slot] if self._mixer else None
        return {**deck.wave, "key": id(deck)} if deck and deck.wave else None

    def snapshot(self) -> dict:
        """Everything the mixer page shows, sent several times a second."""
        mixer = self._mixer
        decks = []
        for slot in (0, 1):
            deck = mixer.slots[slot] if mixer else None
            if not deck:
                decks.append(None)
                continue
            f = deck.filters
            decks.append({
                "title": deck.track.title, "duration": deck.track.duration, "elapsed": round(deck.elapsed, 2),
                "playing": deck.playing and not deck.finished, "ended": deck.finished, "on_air": deck is self._deck,
                "volume": deck.volume, "low": f.low, "mid": f.mid, "high": f.high, "filter": f.filter,
                "pitch": deck.pitch, "nudge": deck.nudge, "rate": deck.rate if deck.playing else 0.0,
                "bpm": deck.bpm, "effective_bpm": deck.effective_bpm, "first_beat": deck.grid[1] if deck.grid else None,
                "seekable": deck.seekable, "loaded": round(deck.loaded, 1), "complete": deck.eof,
                "cue": deck.cue_point, "sync_lock": deck.sync_lock,
                "wave_key": id(deck) if deck.wave else None,
                "level": round(deck.level, 4), "page_url": deck.track.page_url,
            })
        vc = self.voice
        return {
            "live": {"id": str(self.live_dj[0]), "name": self.live_dj[1]} if self.live_dj else None,
            "paused": self.paused,
            "voice": vc.channel.name if vc and vc.channel else None,
            "mixer": {
                "crossfader": round(mixer.crossfader, 4) if mixer else 0.0,
                "curve": mixer.curve if mixer else "smooth",
                "master": mixer.master if mixer else 1.0,
                "manual": bool(mixer and mixer.manual),
                "quantize": self.quantize,
            },
            "decks": decks,
            "line_in": next(({"name": d.track.title, "buffer": round((d.store.written - d.position) / 48000, 2)}
                             for d in (mixer.decks if mixer else []) if d.live_input), None),
            "queue_key": hash(tuple(id(t) for t in self.queue[:50])),
        }

    def queue_snapshot(self) -> list[dict]:
        return [{"title": t.title, "duration": t.duration, "auto": t.auto, "filler": t.filler, "by": t.requester}
                for t in self.queue[:50]]

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
