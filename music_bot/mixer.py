"""Two-deck mixing: each track plays on a Deck, and the Mixer sums the decks into the one stream Discord hears.

In automatic mode a crossfade is two decks playing at once, one fading out while the other fades in.
In live mode a person drives it from the web mixer: per-deck play/cue, faders, EQ, filter and pitch, plus a crossfader.
"""
import collections
import logging
import math
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


class Deck:
    """One track's audio. A thread reads ahead from ffmpeg, so the mixer never waits on the network
    and we learn exactly how much is left once ffmpeg reaches the end."""

    def __init__(self, track, audio: discord.AudioSource, lookahead: int):
        self.track = track
        self.audio = audio
        self.playing = True  # live mode loads decks paused ("cued")
        self.volume = 1.0  # channel fader
        self.pitch = 1.0  # playback speed; changes tempo and pitch together, like a turntable
        self.nudge = 0.0
        self.filters = DeckFilters()
        self.bpm: float | None = None  # detected tempo at pitch 1.0
        self.level = 0.0  # loudness after the fader, for the meters
        self.played = 0  # frames output
        self.consumed = 0  # source samples used up
        self.created = time.monotonic()
        self.eof = False
        self._buffer: collections.deque[np.ndarray] = collections.deque()
        self._lookahead = lookahead
        self._closed = False
        self._cv = threading.Condition()
        self._src = np.zeros((0, 2), np.float32)  # samples being resampled
        self._pos = 0.0  # fractional read position in _src
        self._last_gain = 0.0
        self._fade_in = 0
        self._fade_out_at: int | None = None
        self._fade_out_len = 1
        threading.Thread(target=self._fill, name=f"deck-{track.title[:20]}", daemon=True).start()

    def _fill(self) -> None:
        try:
            while True:
                with self._cv:
                    while len(self._buffer) >= self._lookahead and not self._closed:
                        self._cv.wait()
                    if self._closed:
                        return
                data = self.audio.read()
                if not data:
                    return
                if len(data) < FRAME:
                    data += b"\0" * (FRAME - len(data))
                samples = np.frombuffer(data, np.int16).reshape(-1, 2).astype(np.float32) / 32768
                with self._cv:
                    self._buffer.append(samples)
        except Exception:
            log.exception("Deck reader failed for %s", self.track.title)
        finally:
            with self._cv:
                self.eof = True
                self._cv.notify_all()

    @property
    def rate(self) -> float:
        return max(0.5, self.pitch * (1 + self.nudge))

    @property
    def effective_bpm(self) -> float | None:
        return round(self.bpm * self.pitch, 1) if self.bpm else None

    @property
    def elapsed(self) -> float:
        return self.consumed / RATE

    @property
    def remaining(self) -> int | None:
        """Frames left to play, once ffmpeg has finished; None while it's still going."""
        if not self.eof:
            return None
        samples = len(self._buffer) * SAMPLES + len(self._src) - self._pos
        return int(samples / self.rate / SAMPLES)

    @property
    def fading_out(self) -> bool:
        return self._fade_out_at is not None

    @property
    def finished(self) -> bool:
        if self._fade_out_at is not None and self.played - self._fade_out_at >= self._fade_out_len:
            return True
        return self.eof and not self._buffer and len(self._src) - self._pos < 2

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

    def render(self, crossfader_gain: float) -> np.ndarray | None:
        """The next 20 ms, processed and faded, or None if paused or nothing is buffered yet."""
        if not self.playing:
            self.level = 0.0
            return None
        rate = self.rate
        need = self._pos + SAMPLES * rate + 2
        if len(self._src) < need:
            parts = [self._src]
            have = len(self._src)
            with self._cv:
                while have < need and self._buffer:
                    part = self._buffer.popleft()
                    parts.append(part)
                    have += len(part)
                self._cv.notify()
            self._src = np.concatenate(parts) if len(parts) > 1 else self._src
        available = len(self._src) - 1 - self._pos
        if available <= 0:
            self.level = 0.0
            return None
        count = min(SAMPLES, int(available / rate))
        if count <= 0:
            return None
        if rate == 1.0 and self._pos == int(self._pos):
            start = int(self._pos)
            out = self._src[start:start + count].copy()
        else:
            idx = self._pos + np.arange(count) * rate
            base = np.arange(len(self._src))
            out = np.stack([np.interp(idx, base, self._src[:, 0]), np.interp(idx, base, self._src[:, 1])], axis=1).astype(np.float32)
        self._pos += count * rate
        used = int(self._pos)
        self._src, self._pos = self._src[used:], self._pos - used
        self.consumed += used
        if count < SAMPLES:
            out = np.vstack([out, np.zeros((SAMPLES - count, 2), np.float32)])

        out = self.filters.process(out)
        target = self.volume * self.gain * crossfader_gain
        if abs(target - self._last_gain) > 1e-4:  # ramp across the frame so fader moves never click
            out *= np.linspace(self._last_gain, target, SAMPLES, dtype=np.float32)[:, None]
        elif target != 1.0:
            out *= target
        self._last_gain = target
        self.level = float(np.sqrt(np.mean(out * out)))
        self.played += 1
        return out

    def close(self) -> None:
        with self._cv:
            self._closed = True
            self._cv.notify_all()
        # Killing yt-dlp/ffmpeg can take a moment; never do it on the audio thread.
        threading.Thread(target=self.audio.cleanup, daemon=True).start()


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
