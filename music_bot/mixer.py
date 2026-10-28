"""Two-deck mixing: each track plays on a Deck, and the Mixer sums the decks into the one stream Discord hears.

A crossfade is just two decks playing at once, one fading out while the other fades in.
"""
import collections
import logging
import math
import threading
import time
import warnings

import discord

with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)  # audioop is fine on 3.12; discord.py pulls audioop-lts on 3.13+
    import audioop

log = logging.getLogger(__name__)

FRAME = discord.opus.Encoder.FRAME_SIZE  # 3840 bytes: 20 ms of 48 kHz stereo 16-bit audio
FRAME_SECONDS = 0.02
SILENCE = b"\0" * FRAME


def frames(seconds: float) -> int:
    return int(seconds / FRAME_SECONDS)


class Deck:
    """One track's audio. A thread reads ahead from ffmpeg, so the mixer never waits on the network
    and we learn exactly how much is left once ffmpeg reaches the end."""

    def __init__(self, track, audio: discord.AudioSource, lookahead: int):
        self.track = track
        self.audio = audio
        self.played = 0  # frames
        self.created = time.monotonic()
        self.eof = False
        self._buffer: collections.deque[bytes] = collections.deque()
        self._lookahead = lookahead
        self._closed = False
        self._cv = threading.Condition()
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
                with self._cv:
                    self._buffer.append(data)
        except Exception:
            log.exception("Deck reader failed for %s", self.track.title)
        finally:
            with self._cv:
                self.eof = True
                self._cv.notify_all()

    @property
    def elapsed(self) -> float:
        return self.played * FRAME_SECONDS

    @property
    def remaining(self) -> int | None:
        """Frames left to play, once ffmpeg has finished; None while it's still going."""
        return len(self._buffer) if self.eof else None

    @property
    def fading_out(self) -> bool:
        return self._fade_out_at is not None

    @property
    def finished(self) -> bool:
        if self._fade_out_at is not None and self.played - self._fade_out_at >= self._fade_out_len:
            return True
        return self.eof and not self._buffer

    @property
    def gain(self) -> float:
        """Equal-power fades: sin in, cos out, so the overall loudness stays even through a crossfade."""
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

    def read(self) -> bytes | None:
        """The next frame with fades applied, or None if nothing is buffered yet."""
        with self._cv:
            if not self._buffer:
                return None
            data = self._buffer.popleft()
            self._cv.notify()
        gain = self.gain
        self.played += 1
        return data if gain >= 0.999 else audioop.mul(data, 2, gain)

    def close(self) -> None:
        with self._cv:
            self._closed = True
            self._cv.notify_all()
        # Killing yt-dlp/ffmpeg can take a moment; never do it on the audio thread.
        threading.Thread(target=self.audio.cleanup, daemon=True).start()


class Mixer(discord.AudioSource):
    """What the voice client plays. Decks come and go; the stream keeps going while `hold` is set."""

    def __init__(self, volume: float):
        self.volume = volume
        self.hold = True  # play silence instead of ending when no deck has audio (e.g. while the next track loads)
        self._decks: list[Deck] = []
        self._lock = threading.Lock()

    @property
    def decks(self) -> list[Deck]:
        with self._lock:
            return list(self._decks)

    def add(self, deck: Deck) -> None:
        with self._lock:
            self._decks.append(deck)

    def clear(self) -> None:
        with self._lock:
            decks, self._decks = self._decks, []
        for deck in decks:
            deck.close()

    def read(self) -> bytes:
        out = None
        for deck in self.decks:
            if deck.finished:
                with self._lock:
                    if deck in self._decks:
                        self._decks.remove(deck)
                deck.close()
                continue
            frame = deck.read()
            if frame is not None:
                out = frame if out is None else audioop.add(out, frame, 2)
        if out is None:
            return SILENCE if self.hold or self.decks else b""
        return out if self.volume == 1 else audioop.mul(out, 2, self.volume)

    def is_opus(self) -> bool:
        return False

    def cleanup(self) -> None:
        self.clear()
