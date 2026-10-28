"""Line-in: a DJ's own decks and mixer, streamed from their browser and played by the bot.

The mixer page records the chosen input (the DJ mixer's USB audio, or an audio interface on its record/booth out)
as Opus with MediaRecorder and sends the chunks over a WebSocket. Here ffmpeg turns them back into PCM for a deck.
"""
import logging
import queue
import subprocess
import threading

import discord

from .mixer import FRAME

log = logging.getLogger(__name__)

TITLE_SUFFIX = "(line-in)"
SOURCE = "line-in"  # Track.source of line-in tracks


class LineInAudio(discord.AudioSource):
    """PCM from a stream of browser-recorded chunks (webm/ogg Opus, or mp4 from Safari)."""

    def __init__(self):
        self._proc = subprocess.Popen(
            ["ffmpeg", "-nostdin", "-loglevel", "error", "-probesize", "32768", "-analyzeduration", "0",
             "-fflags", "nobuffer", "-i", "pipe:0", "-f", "s16le", "-ar", "48000", "-ac", "2", "pipe:1"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        )
        self._chunks: queue.Queue[bytes | None] = queue.Queue(maxsize=300)  # ~30 s of 100 ms chunks
        self.received = 0  # bytes, for the page's status line
        threading.Thread(target=self._write, name="line-in", daemon=True).start()

    def _write(self) -> None:
        try:
            while (chunk := self._chunks.get()) is not None:
                self._proc.stdin.write(chunk)
                self._proc.stdin.flush()
        except (BrokenPipeError, ValueError, OSError):
            pass
        finally:
            try:
                self._proc.stdin.close()
            except OSError:
                pass

    def feed(self, chunk: bytes) -> None:
        self.received += len(chunk)
        try:
            self._chunks.put_nowait(chunk)
        except queue.Full:  # ffmpeg has stalled; dropping keeps us from eating memory
            log.warning("Line-in is backed up; dropping audio")

    def read(self) -> bytes:
        data = self._proc.stdout.read(FRAME)
        return data if len(data) == FRAME else b""

    def is_opus(self) -> bool:
        return False

    def cleanup(self) -> None:
        try:
            self._chunks.put_nowait(None)
        except queue.Full:
            pass
        if self._proc.poll() is None:
            self._proc.kill()
        try:
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
