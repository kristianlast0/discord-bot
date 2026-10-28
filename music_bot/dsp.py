"""Audio processing for the live mixer: 3-band EQ, a one-knob filter, and tempo (BPM) detection.

Filters are RBJ "audio EQ cookbook" biquads run with scipy, keeping their state between 20 ms frames.
"""
import math

import numpy as np
from scipy.signal import sosfilt

RATE = 48000
EQ_KILL = -40.0  # dB; the EQ knobs' bottom position cuts a band out
_LOW_HZ, _MID_HZ, _HIGH_HZ = 250.0, 1000.0, 4000.0


def _norm(b0, b1, b2, a0, a1, a2) -> np.ndarray:
    return np.array([[b0 / a0, b1 / a0, b2 / a0, 1.0, a1 / a0, a2 / a0]])


def _low_shelf(hz: float, db: float) -> np.ndarray:
    a, w = 10 ** (db / 40), 2 * math.pi * hz / RATE
    cos, alpha = math.cos(w), math.sin(w) / 2 * math.sqrt(2)
    sq = 2 * math.sqrt(a) * alpha
    return _norm(a * ((a + 1) - (a - 1) * cos + sq), 2 * a * ((a - 1) - (a + 1) * cos), a * ((a + 1) - (a - 1) * cos - sq),
                 (a + 1) + (a - 1) * cos + sq, -2 * ((a - 1) + (a + 1) * cos), (a + 1) + (a - 1) * cos - sq)


def _high_shelf(hz: float, db: float) -> np.ndarray:
    a, w = 10 ** (db / 40), 2 * math.pi * hz / RATE
    cos, alpha = math.cos(w), math.sin(w) / 2 * math.sqrt(2)
    sq = 2 * math.sqrt(a) * alpha
    return _norm(a * ((a + 1) + (a - 1) * cos + sq), -2 * a * ((a - 1) + (a + 1) * cos), a * ((a + 1) + (a - 1) * cos - sq),
                 (a + 1) - (a - 1) * cos + sq, 2 * ((a - 1) - (a + 1) * cos), (a + 1) - (a - 1) * cos - sq)


def _peak(hz: float, db: float, q: float = 0.7) -> np.ndarray:
    a, w = 10 ** (db / 40), 2 * math.pi * hz / RATE
    alpha = math.sin(w) / (2 * q)
    cos = math.cos(w)
    return _norm(1 + alpha * a, -2 * cos, 1 - alpha * a, 1 + alpha / a, -2 * cos, 1 - alpha / a)


def _pass(kind: str, hz: float, q: float = 0.9) -> np.ndarray:
    w = 2 * math.pi * hz / RATE
    alpha, cos = math.sin(w) / (2 * q), math.cos(w)
    if kind == "low":
        return _norm((1 - cos) / 2, 1 - cos, (1 - cos) / 2, 1 + alpha, -2 * cos, 1 - alpha)
    return _norm((1 + cos) / 2, -(1 + cos), (1 + cos) / 2, 1 + alpha, -2 * cos, 1 - alpha)


class DeckFilters:
    """EQ and filter for one deck. Knob values: EQ in dB (EQ_KILL..+6), filter -1 (low-pass) .. 0 (off) .. +1 (high-pass)."""

    def __init__(self):
        self.low = self.mid = self.high = 0.0
        self.filter = 0.0
        self._key = None
        self._sos: np.ndarray | None = None
        self._zi: np.ndarray | None = None

    def _design(self) -> np.ndarray | None:
        stages = []
        if abs(self.low) > 0.05:
            stages.append(_low_shelf(_LOW_HZ, self.low))
        if abs(self.mid) > 0.05:
            stages.append(_peak(_MID_HZ, self.mid))
        if abs(self.high) > 0.05:
            stages.append(_high_shelf(_HIGH_HZ, self.high))
        if self.filter < -0.02:  # sweep the low-pass cutoff from 20 kHz down to 150 Hz
            stages.append(_pass("low", 20000 * (150 / 20000) ** -self.filter))
        elif self.filter > 0.02:  # sweep the high-pass cutoff from 20 Hz up to 6 kHz
            stages.append(_pass("high", 20 * (6000 / 20) ** self.filter))
        return np.vstack(stages) if stages else None

    def process(self, audio: np.ndarray) -> np.ndarray:
        """audio: float32 array of shape (samples, 2)."""
        key = (round(self.low, 2), round(self.mid, 2), round(self.high, 2), round(self.filter, 3))
        if key != self._key:
            sos = self._design()
            if sos is None or self._sos is None or len(sos) != len(self._sos):
                self._zi = None if sos is None else np.zeros((len(sos), 2, 2))  # stage count changed: restart state
            self._sos, self._key = sos, key
        if self._sos is None:
            return audio
        out, self._zi = sosfilt(self._sos, audio, axis=0, zi=self._zi)
        return out.astype(np.float32, copy=False)


# ── Tempo detection ─────────────────────────────────────────────────

ANALYSIS_RATE = 11025


def detect_bpm(samples: np.ndarray, rate: int = ANALYSIS_RATE) -> float | None:
    """Estimate tempo from mono audio: onset strength (spectral flux) then autocorrelation, preferring 80-170 BPM."""
    if len(samples) < rate * 10:
        return None
    n, hop = 1024, 128
    frames = np.lib.stride_tricks.sliding_window_view(samples, n)[::hop] * np.hanning(n)
    mag = np.log1p(np.abs(np.fft.rfft(frames, axis=1)) * 10)
    flux = np.maximum(0, np.diff(mag, axis=0)).sum(axis=1)
    flux -= np.convolve(flux, np.ones(32) / 32, mode="same")  # remove slow loudness changes
    flux = np.maximum(flux, 0)
    if not flux.any():
        return None
    env_rate = rate / hop
    ac = np.correlate(flux, flux, mode="full")[len(flux) - 1:]

    def at(lag: float) -> float:
        i = int(lag)
        if i + 1 >= len(ac):
            return 0.0
        return (1 - (lag - i)) * ac[i] + (lag - i) * ac[i + 1]

    def score(bpm: float, multiples) -> float:
        beat = env_rate * 60 / bpm
        return sum(weight * at(beat * mult) for mult, weight in multiples)

    # Coarse pass: beats also line up 2 and 4 beats along, which favours the true tempo over its multiples.
    coarse = ((1, 1.0), (2, 0.5), (4, 0.25))
    candidates = np.arange(60.0, 200.0, 0.5)
    scores = [score(b, coarse) * math.exp(-0.5 * (math.log2(b / 120) / 0.9) ** 2) for b in candidates]
    best = float(candidates[int(np.argmax(scores))])
    # Fine pass: a peak 8-16 beats away pins the tempo down far more precisely than the next beat does.
    fine = ((4, 0.5), (8, 1.0), (16, 1.0))
    around = np.arange(best - 1.5, best + 1.5, 0.02)
    best = float(around[int(np.argmax([score(b, fine) for b in around]))])
    while best < 78:
        best *= 2
    while best > 180:
        best /= 2
    return round(best, 1)
