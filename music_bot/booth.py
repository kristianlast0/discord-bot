"""Draws the DJ booth (two turntables and a mixer) as an animated GIF.

Discord loops the GIF itself, so the records spin smoothly without the bot editing the message.
"""
import io
import math
from dataclasses import dataclass
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

from .models import fmt_duration

W, H = 640, 300
_SPIN_FRAMES = 12
_FRAME_MS = 150  # 12 x 150 ms = one turn every 1.8 s, i.e. 33⅓ rpm
_RECORD = 200
_CENTRES = ((150, 128), (490, 128))

_BG = (20, 22, 28)
_PANEL = (32, 35, 44)
_PLATTER = (44, 48, 58)
_VINYL = (12, 12, 14)
_GROOVE = (30, 30, 36)
_TEXT = (230, 232, 240)
_DIM = (130, 135, 150)
_DECK_COLOURS = ((88, 101, 242), (235, 69, 158))  # Deck A blurple, Deck B pink
_ON_AIR = (67, 181, 129)


@dataclass
class DeckView:
    title: str
    elapsed: float
    duration: int | None
    spinning: bool
    level: float  # 0-1: how loud this deck is in the mix right now
    on_air: bool  # the deck people are mainly hearing


@dataclass
class BoothView:
    decks: tuple[DeckView | None, DeckView | None]  # (A, B)
    crossfader: float  # 0 = all Deck A, 1 = all Deck B
    paused: bool
    footer: str


@lru_cache(maxsize=8)
def _font(size: int) -> ImageFont.FreeTypeFont:
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def _clean(text: str) -> str:
    return "".join(c for c in text if ord(c) < 0x10000 and c.isprintable()).strip()


def _fit(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> str:
    text = _clean(text)
    if draw.textlength(text, font=font) <= width:
        return text
    while text and draw.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return text + "…"


@lru_cache(maxsize=2)
def _record(colour: tuple[int, int, int]) -> Image.Image:
    """A vinyl record with a coloured label and a bright mark, so you can see it turn."""
    size = _RECORD * 2  # draw at 2x and shrink, for smooth edges
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((0, 0, size - 1, size - 1), fill=_VINYL)
    for r in range(size // 2 - 12, size // 6 + 20, -14):
        c = size // 2
        d.ellipse((c - r, c - r, c + r, c + r), outline=_GROOVE, width=2)
    c, label = size // 2, size // 6
    d.ellipse((c - label, c - label, c + label, c + label), fill=colour)
    d.rectangle((c - 6, c - label + 10, c + 6, c - label + 46), fill=(255, 255, 255))
    d.ellipse((c - 8, c - 8, c + 8, c + 8), fill=_BG)
    d.pieslice((12, 12, size - 12, size - 12), 30, 36, fill=(38, 38, 46))  # light catching the grooves
    return img.resize((_RECORD, _RECORD), Image.LANCZOS)


def _static_layer(view: BoothView) -> Image.Image:
    img = Image.new("RGB", (W, H), _BG)
    d = ImageDraw.Draw(img)
    title_font, small = _font(15), _font(12)
    for i, (cx, cy) in enumerate(_CENTRES):
        deck = view.decks[i]
        colour = _DECK_COLOURS[i]
        d.rounded_rectangle((cx - 140, 6, cx + 140, H - 8), radius=14, fill=_PANEL)
        d.ellipse((cx - 108, cy - 108, cx + 108, cy + 108), fill=_PLATTER)
        d.text((cx - 128, 14), f"DECK {'AB'[i]}", font=small, fill=colour)
        if deck and deck.on_air:
            d.rounded_rectangle((cx + 70, 12, cx + 128, 30), radius=6, fill=_ON_AIR)
            d.text((cx + 99, 21), "ON AIR", font=small, fill=(10, 20, 15), anchor="mm")
        # Tonearm
        base = (cx + 118, cy - 92)
        tip = (cx + 52, cy + 30) if deck else (cx + 126, cy - 10)
        d.ellipse((base[0] - 9, base[1] - 9, base[0] + 9, base[1] + 9), fill=(90, 95, 110))
        d.line((base, tip), fill=(170, 175, 190), width=5)
        d.rectangle((tip[0] - 6, tip[1] - 4, tip[0] + 6, tip[1] + 8), fill=(170, 175, 190))
        # Title and progress
        if deck:
            d.text((cx - 128, 244), _fit(d, deck.title, title_font, 256), font=title_font, fill=_TEXT)
            if deck.duration:
                frac = max(0.0, min(1.0, deck.elapsed / deck.duration))
                d.rounded_rectangle((cx - 128, 270, cx + 128, 276), radius=3, fill=_PLATTER)
                d.rounded_rectangle((cx - 128, 270, cx - 128 + max(6, int(256 * frac)), 276), radius=3, fill=colour)
                times = f"{fmt_duration(deck.elapsed)} / {fmt_duration(deck.duration)}"
            else:
                times = "LIVE"
            d.text((cx + 128, 290), times, font=small, fill=_DIM, anchor="rs")
        else:
            d.text((cx - 128, 244), "Empty", font=title_font, fill=_DIM)

    # Mixer between the decks
    d.rounded_rectangle((292, 6, 348, H - 8), radius=10, fill=_PANEL)
    d.text((320, 20), "MIX", font=small, fill=_DIM, anchor="mm")
    # Crossfader
    d.rounded_rectangle((298, 268, 342, 274), radius=3, fill=_BG)
    knob = 300 + int(40 * view.crossfader)
    d.rounded_rectangle((knob - 6, 260, knob + 6, 282), radius=3, fill=_TEXT)
    d.text((300, 292), "A", font=small, fill=_DECK_COLOURS[0], anchor="mm")
    d.text((340, 292), "B", font=small, fill=_DECK_COLOURS[1], anchor="mm")
    if view.paused:
        d.rectangle((310, 36, 316, 52), fill=_TEXT)
        d.rectangle((324, 36, 330, 52), fill=_TEXT)
    return img


def _meters(d: ImageDraw.ImageDraw, view: BoothView, frame: int) -> None:
    """Level meters for each deck: they bounce while playing and show how much of each deck is in the mix."""
    for i, x in enumerate((303, 325)):
        deck = view.decks[i]
        level = deck.level if deck and deck.spinning else 0.0
        bounce = 0.75 + 0.25 * abs(math.sin(frame * 1.7 + i * 2.1)) if level else 0
        lit = int(13 * level * bounce)
        for seg in range(13):
            y = 236 - seg * 13
            if seg < lit:
                colour = (235, 80, 80) if seg >= 11 else (240, 190, 60) if seg >= 8 else _DECK_COLOURS[i]
            else:
                colour = (40, 43, 52)
            d.rectangle((x, y, x + 12, y + 9), fill=colour)


def render(view: BoothView) -> tuple[bytes, str]:
    """(image bytes, filename): an animated GIF while a record spins, otherwise a still PNG."""
    base = _static_layer(view)
    spinning = any(d and d.spinning for d in view.decks) and not view.paused
    frame_count = _SPIN_FRAMES if spinning else 1
    frames = []
    for f in range(frame_count):
        img = base.copy()
        for i, (cx, cy) in enumerate(_CENTRES):
            deck = view.decks[i]
            if not deck:
                continue
            angle = -360 * f / _SPIN_FRAMES if deck.spinning and spinning else 0
            rec = _record(_DECK_COLOURS[i]).rotate(angle, resample=Image.BICUBIC)
            img.paste(rec, (cx - _RECORD // 2, cy - _RECORD // 2), rec)
        _meters(ImageDraw.Draw(img), view, f)
        frames.append(img)

    buf = io.BytesIO()
    if frame_count == 1:
        frames[0].save(buf, format="PNG", optimize=True)
        return buf.getvalue(), "booth.png"
    palette = frames[0].convert("P", palette=Image.ADAPTIVE, colors=96)
    frames = [f.quantize(palette=palette, dither=Image.Dither.NONE) for f in frames]
    frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:], duration=_FRAME_MS, loop=0, optimize=True)
    return buf.getvalue(), "booth.gif"
