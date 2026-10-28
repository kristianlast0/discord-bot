"""Two-deck mixing: each track plays on a Deck, and the Mixer sums the decks into the one stream Discord hears.

In automatic mode a crossfade is two decks playing at once, one fading out while the other fades in.
In live mode a person drives it from the web mixer: per-deck play/cue, faders, EQ, filter and pitch, plus a crossfader.
"""
import collections
import logging
import math
import os
import tempfile
import threading
import time

import discord
import numpy as np

from .dsp import DeckFilters

log = logging.getLogger(__name__)

FRAME = discord.opus.Encoder.FRAME_SIZE  # 3840 bytes: 20 ms of 48 kHz stereo 16-bit audio
SAMPLES = FRAME // 4  # 960 stereo samples per frame
RATE = 48000
FRAME_SECONDS = 0.02
SILENCE = b"\0" * FRAME
PITCH_RANGE = 0.16  # the pitch fader goes ±16%
NUDGE = 0.04  # holding nudge speeds up or slows down by 4%


def frames(seconds: float) -> int:
    return int(seconds / FRAME_SECONDS)


class _FileStore:
    """The whole track as 16-bit PCM in a temp file (in RAM via /dev/shm for normal-length songs), written by a thread
    as fast as ffmpeg decodes. Any part that has arrived can be read, so the deck can jump around."""

    def __init__(self, audio: discord.AudioSource, title: str, long: bool):
        folder = "/dev/shm" if os.path.isdir("/dev/shm") and not long else None
        self._fd, self._path = tempfile.mkstemp(prefix="music-bot-deck-", suffix=".pcm", dir=folder)
        self._audio = audio
        self._lock = threading.Lock()
        self._closed = False
        self.written = 0  # samples
        self.done = False
        threading.Thread(target=self._fill, name=f"deck-{title[:20]}", daemon=True).start()

    def _fill(self) -> None:
        try:
            while not self._closed:
                data = self._audio.read()
                if not data:
                    break
                with self._lock:
                    if self._closed:
                        break
                    os.write(self._fd, data)
                    self.written += len(data) // 4
        except Exception:
            log.exception("Deck download failed")
        finally:
            self.done = True

    def read(self, start: int, count: int) -> np.ndarray:
        end = min(start + count, self.written)
        if end <= start:
            return np.zeros((0, 2), np.float32)
        with self._lock:
            if self._closed:
                return np.zeros((0, 2), np.float32)
            data = os.pread(self._fd, (end - start) * 4, start * 4)
        return np.frombuffer(data, np.int16).reshape(-1, 2).astype(np.float32) / 32768

    def read_raw(self, start: int, count: int) -> np.ndarray:
        """int16 samples, for analysis and waveforms."""
        end = min(start + count, self.written)
        with self._lock:
            if self._closed or end <= start:
                return np.zeros((0, 2), np.int16)
            data = os.pread(self._fd, (end - start) * 4, start * 4)
        return np.frombuffer(data, np.int16).reshape(-1, 2)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            os.close(self._fd)
            try:
                os.unlink(self._path)
            except OSError:
                pass
        threading.Thread(target=self._audio.cleanup, daemon=True).start()


class _StreamStore:
    """A live stream (radio): a short read-ahead buffer that only goes forwards."""

    def __init__(self, audio: discord.AudioSource, title: str, lookahead: int):
        self._audio = audio
        self._chunks: collections.deque[np.ndarray] = collections.deque()
        self._base = 0  # sample index of the first buffered sample
        self._buffered = 0
        self._lookahead = lookahead * SAMPLES
        self._cv = threading.Condition()
        self._closed = False
        self.done = False
        threading.Thread(target=self._fill, name=f"deck-{title[:20]}", daemon=True).start()

    @property
    def written(self) -> int:
        return self._base + self._buffered

    def _fill(self) -> None:
        try:
            while True:
                with self._cv:
                    while self._buffered >= self._lookahead and not self._closed:
                        self._cv.wait()
                    if self._closed:
                        return
                data = self._audio.read()
                if not data:
                    return
                chunk = np.frombuffer(data, np.int16).reshape(-1, 2).astype(np.float32) / 32768
                with self._cv:
                    self._chunks.append(chunk)
                    self._buffered += len(chunk)
        except Exception:
            log.exception("Stream reader failed")
        finally:
            with self._cv:
                self.done = True
                self._cv.notify_all()

    def read(self, start: int, count: int) -> np.ndarray:
        with self._cv:
            while self._chunks and self._base + len(self._chunks[0]) <= start:  # already played
                gone = self._chunks.popleft()
                self._base += len(gone)
                self._buffered -= len(gone)
            self._cv.notify()
            parts, have, pos = [], 0, self._base
            for chunk in self._chunks:
                if have >= count + (start - self._base):
                    break
                parts.append(chunk)
                have += len(chunk)
            if not parts:
                return np.zeros((0, 2), np.float32)
            joined = np.concatenate(parts) if len(parts) > 1 else parts[0]
        return joined[max(0, start - self._base): max(0, start - self._base) + count]

    def close(self) -> None:
        with self._cv:
            self._closed = True
            self._cv.notify_all()
        threading.Thread(target=self._audio.cleanup, daemon=True).start()


class Deck:
    """One track on a deck. Finite tracks download completely (so you can jump around); radio streams forwards."""

    def __init__(self, track, audio: discord.AudioSource, lookahead: int):
        self.track = track
        self.seekable = track.kind != "stream"
        if self.seekable:
            self.store = _FileStore(audio, track.title, long=(track.duration or 0) > 20 * 60)
        else:
            self.store = _StreamStore(audio, track.title, lookahead)
        self.playing = True  # live mode loads decks paused ("cued")
        self.volume = 1.0  # channel fader
        self.pitch = 1.0  # playback speed; changes tempo and pitch together, like a turntable
        self.nudge = 0.0
        self.filters = DeckFilters()
        self.grid: tuple[float, float] | None = None  # (BPM, a beat's time in seconds), from analysis
        self.sync_lock = False  # keep tempo and beats locked to the other deck
        self.sync_mult = 1.0  # 2 or 0.5 when synced in double or half time
        self.cue_point = 0.0  # seconds
        self.wave: dict | None = None  # waveform data for the mixer page, once the download finishes
        self.level = 0.0  # loudness after the fader, for the meters
        self.played = 0  # frames output
        self.position = 0.0  # in source samples
        self.created = time.monotonic()
        self._jump: tuple[float, float] | None = None  # (target, position when asked), applied on the next frame
        self._declick = False
        self._last_gain = 0.0
        self._fade_in = 0
        self._fade_out_at: int | None = None
        self._fade_out_len = 1

    @property
    def eof(self) -> bool:
        return self.store.done

    @property
    def bpm(self) -> float | None:
        return self.grid[0] if self.grid else None

    @property
    def rate(self) -> float:
        return max(0.5, self.pitch * (1 + self.nudge))

    @property
    def effective_bpm(self) -> float | None:
        return round(self.bpm * self.pitch, 2) if self.bpm else None

    @property
    def elapsed(self) -> float:
        return self.position / RATE

    @property
    def loaded(self) -> float:
        """Seconds of the track downloaded so far."""
        return self.store.written / RATE

    @property
    def beat_period(self) -> float | None:
        return 60 / self.bpm if self.bpm else None

    def beat_phase(self, at: float | None = None) -> float | None:
        """Where we are within the current beat, 0-1, at track time `at` (default: now)."""
        if not self.grid:
            return None
        return (((self.elapsed if at is None else at) - self.grid[1]) / self.beat_period) % 1.0

    @property
    def remaining(self) -> int | None:
        """Frames left to play, once the whole track has arrived; None until then."""
        if not self.store.done:
            return None
        return int(max(0.0, self.store.written - self.position) / self.rate / SAMPLES)

    @property
    def fading_out(self) -> bool:
        return self._fade_out_at is not None

    @property
    def finished(self) -> bool:
        if self._fade_out_at is not None and self.played - self._fade_out_at >= self._fade_out_len:
            return True
        return self.store.done and self.position >= self.store.written - 2

    @property
    def gain(self) -> float:
        """The automatic fade envelope. Equal-power curves (sin in, cos out) keep a crossfade evenly loud."""
        g = 1.0
        if self._fade_in and self.played < self._fade_in:
            g = math.sin(self.played / self._fade_in * math.pi / 2)
        if self._fade_out_at is not None:
            t = min(1.0, (self.played - self._fade_out_at) / self._fade_out_len)
            g *= math.cos(t * math.pi / 2)
        return g

    def fade_in(self, length: int) -> None:
        self._fade_in = max(0, length)

    def fade_out(self, length: int) -> None:
        if self._fade_out_at is None:
            self._fade_out_at = self.played
            self._fade_out_len = max(1, length)

    def clear_fades(self) -> None:
        self._fade_in, self._fade_out_at = 0, None

    def seek(self, seconds: float) -> float:
        """Jump to `seconds` (as far as has downloaded). Returns where it actually lands."""
        if not self.seekable:
            return self.elapsed
        limit = max(0, self.store.written - SAMPLES)
        target = min(max(0.0, seconds * RATE), limit)
        if self.playing and self.played:
            self._jump = (target, self.position)  # applied on the next frame, with a quick fade so it doesn't click
        else:
            self.position = target
        return target / RATE

    def render(self, crossfader_gain: float) -> np.ndarray | None:
        """The next 20 ms, processed and faded, or None if paused or nothing has arrived yet."""
        if not self.playing:
            self.level = 0.0
            return None
        fade_this = self._jump is not None
        rate = self.rate
        start = int(self.position)
        frac = self.position - start
        src = self.store.read(start, int(math.ceil(frac + SAMPLES * rate)) + 2)
        available = len(src) - 1 - frac
        if available <= 0:
            self.level = 0.0
            return None
        count = min(SAMPLES, int(available / rate))
        if count <= 0:
            return None
        if rate == 1.0 and frac == 0:
            out = src[:count].copy()
        else:
            idx = frac + np.arange(count) * rate
            base = np.arange(len(src))
            out = np.stack([np.interp(idx, base, src[:, 0]), np.interp(idx, base, src[:, 1])], axis=1).astype(np.float32)
        self.position += count * rate
        if count < SAMPLES:
            out = np.vstack([out, np.zeros((SAMPLES - count, 2), np.float32)])

        out = self.filters.process(out)
        target = self.volume * self.gain * crossfader_gain
        if abs(target - self._last_gain) > 1e-4:  # ramp across the frame so fader moves never click
            out *= np.linspace(self._last_gain, target, SAMPLES, dtype=np.float32)[:, None]
        elif target != 1.0:
            out *= target
        self._last_gain = target
        if fade_this:  # jumping: fade this frame out, then fade the new spot in
            out *= np.linspace(1, 0, SAMPLES, dtype=np.float32)[:, None]
            target, asked_at = self._jump
            # Keep the time that played since the jump was asked for, so beat-preserving jumps stay on the beat.
            self.position, self._jump, self._declick = target + (self.position - asked_at), None, True
        elif self._declick:
            out[:240] *= np.linspace(0, 1, 240, dtype=np.float32)[:, None]
            self._declick = False
        self.level = float(np.sqrt(np.mean(out * out)))
        self.played += 1
        return out

    def close(self) -> None:
        self.store.close()


class Mixer(discord.AudioSource):
    """What the voice client plays: decks A and B through a crossfader. The stream keeps going while `hold` is set."""

    def __init__(self, volume: float):
        self.volume = volume
        self.hold = True  # play silence instead of ending when no deck has audio (e.g. while the next track loads)
        self.manual = False  # live mode: the crossfader decides who's heard, and finished decks stay loaded
        self.crossfader = 0.0  # 0 = Deck A, 1 = Deck B
        self.curve = "smooth"  # or "cut": both decks at full volume until the very ends
        self.master = 1.0
        self.slots: list[Deck | None] = [None, None]
        self._move: tuple[float, float, float, float] | None = None  # automated crossfader move: from, to, start, seconds
        self._lock = threading.Lock()

    @property
    def decks(self) -> list[Deck]:
        with self._lock:
            return [d for d in self.slots if d]

    def slot_of(self, deck: Deck) -> int | None:
        with self._lock:
            return next((i for i, d in enumerate(self.slots) if d is deck), None)

    def load(self, slot: int, deck: Deck | None) -> None:
        with self._lock:
            old, self.slots[slot] = self.slots[slot], deck
        if old and old is not deck:
            old.close()

    def clear(self) -> None:
        with self._lock:
            decks, self.slots = [d for d in self.slots if d], [None, None]
        for deck in decks:
            deck.close()

    def move_crossfader(self, target: float, seconds: float) -> None:
        self._move = (self.crossfader, target, time.monotonic(), max(0.05, seconds))

    def crossfader_gain(self, slot: int) -> float:
        x = self.crossfader
        if self.curve == "cut":
            return min(1.0, max(0.0, (1 - x if slot == 0 else x) / 0.06))
        return math.cos(x * math.pi / 2) if slot == 0 else math.sin(x * math.pi / 2)

    def read(self) -> bytes:
        if self._move:
            start, target, began, seconds = self._move
            t = min(1.0, (time.monotonic() - began) / seconds)
            self.crossfader = start + (target - start) * t
            if t >= 1:
                self._move = None
        mix = None
        with self._lock:
            slots = list(enumerate(self.slots))
        for slot, deck in slots:
            if deck is None:
                continue
            if deck.finished:
                if self.manual:
                    deck.playing = False  # stays on the deck, showing it ended, until the DJ loads another
                else:
                    self.load(slot, None)
                continue
            out = deck.render(self.crossfader_gain(slot) if self.manual else 1.0)
            if out is not None:
                mix = out if mix is None else mix + out
        if mix is None:
            return SILENCE if self.hold or any(d for _, d in slots) else b""
        mix *= self.master * self.volume
        np.clip(mix, -1.0, 1.0, out=mix)
        return (mix * 32767).astype(np.int16).tobytes()

    def is_opus(self) -> bool:
        return False

    def cleanup(self) -> None:
        self.clear()
