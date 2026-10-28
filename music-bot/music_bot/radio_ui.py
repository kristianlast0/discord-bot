"""The radio panel (a permanent message in the music channel) and the private station browser it opens."""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import discord

from . import radio, worldradio
from .models import TrackError, UserError

if TYPE_CHECKING:
    from .cog import Music

log = logging.getLogger(__name__)

_COUNTRIES = [
    ("GB", "United Kingdom"), ("IE", "Ireland"), ("US", "United States"), ("CA", "Canada"), ("AU", "Australia"),
    ("NZ", "New Zealand"), ("FR", "France"), ("DE", "Germany"), ("ES", "Spain"), ("IT", "Italy"),
    ("NL", "Netherlands"), ("PT", "Portugal"), ("PL", "Poland"), ("SE", "Sweden"), ("NO", "Norway"),
    ("GR", "Greece"), ("TR", "Turkey"), ("IN", "India"), ("JP", "Japan"), ("KR", "South Korea"),
    ("BR", "Brazil"), ("MX", "Mexico"), ("JM", "Jamaica"), ("NG", "Nigeria"),
]
_GENRES = [
    "pop", "rock", "dance", "hits", "news", "talk", "jazz", "classical", "hip hop", "rnb", "country", "electronic",
    "chillout", "80s", "90s", "oldies", "metal", "reggae", "latin", "indie", "soul", "sports", "ambient", "christmas",
]
_GENRE_LABELS = {"rnb": "R&B", "80s": "80s", "90s": "90s"}
_ANY = "any"
_BROWSER_TIMEOUT = 14 * 60  # ephemeral messages can only be edited for 15 minutes


def _flag(code: str) -> str:
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in code.upper())


def _country_name(code: str | None) -> str:
    return next((name for c, name in _COUNTRIES if c == code), "Anywhere")


async def _report(interaction: discord.Interaction, error: Exception) -> None:
    if isinstance(error, (UserError, TrackError)):
        message = f"⚠️ {error}"
    else:
        log.error("Radio browser failed", exc_info=error)
        message = "⚠️ Something went wrong. It's been logged."
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


class SearchModal(discord.ui.Modal, title="Search radio stations"):
    query = discord.ui.TextInput(label="Station name or style", placeholder="e.g. Radio Nova, Kiss, jazz", max_length=80)

    def __init__(self, cog: Music, browser: RadioBrowser | None):
        super().__init__()
        self.cog = cog
        self.browser = browser  # None when opened from the panel

    async def on_submit(self, interaction: discord.Interaction):
        browser = self.browser or RadioBrowser(self.cog)
        browser.mode, browser.query, browser.page = "search", str(self.query).strip(), 0
        if self.browser:
            await interaction.response.defer()
            await browser.load()
            await interaction.edit_original_response(content=browser.header(), view=browser)
        else:
            await interaction.response.defer(ephemeral=True, thinking=True)
            await browser.load()
            await interaction.followup.send(browser.header(), view=browser, ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        await _report(interaction, error)


class RadioBrowser(discord.ui.View):
    """A private message with dropdowns for picking a station. Modes: "saved", "world", "search"."""

    def __init__(self, cog: Music, mode: str = "world"):
        super().__init__(timeout=_BROWSER_TIMEOUT)
        self.cog = cog
        self.mode = mode
        self.country: str | None = None
        self.genre: str | None = None
        self.query = ""
        self.page = 0
        self.stations: list[worldradio.Station] = []
        self.status = ""
        self.last_played: worldradio.Station | None = None

    async def load(self) -> None:
        """Fetch the current page and rebuild the components."""
        self.stations = []
        if self.mode == "world":
            self.stations = await worldradio.search(countrycode=self.country, tag=self.genre, page=self.page)
        elif self.mode == "search":
            self.stations = await worldradio.search(name=self.query, page=self.page)
            if not self.stations and self.page == 0:  # "jazz" is more likely a style than a name
                self.stations = await worldradio.search(tag=self.query.lower(), page=self.page)
        self.render()

    def header(self) -> str:
        if self.mode == "saved":
            title = "⭐ **Saved stations**"
        elif self.mode == "search":
            title = f"🔎 **Stations matching “{self.query}”**"
        else:
            title = f"🌍 **World radio** · {_country_name(self.country)} · {self.genre or 'any style'}"
        lines = [f"{title} · page {self.page + 1}"]
        if self.mode != "saved" and not self.stations:
            lines.append("No stations found. Try another country, style or search.")
        if self.status:
            lines.append(self.status)
        return "\n".join(lines)

    # ── Building the components ─────────────────────────────────────

    def render(self) -> None:
        self.clear_items()
        if self.mode == "world":
            self._add_select(
                "Country",
                [discord.SelectOption(label="Anywhere", value=_ANY, emoji="🌍", default=self.country is None)]
                + [discord.SelectOption(label=name, value=code, emoji=_flag(code), default=code == self.country) for code, name in _COUNTRIES],
                self._pick_country,
                row=0,
            )
            self._add_select(
                "Style",
                [discord.SelectOption(label="Any style", value=_ANY, default=self.genre is None)]
                + [discord.SelectOption(label=_GENRE_LABELS.get(g, g.title()), value=g, default=g == self.genre) for g in _GENRES],
                self._pick_genre,
                row=1,
            )

        if self.mode == "saved":
            names = list(radio.stations())
            page_names = names[self.page * worldradio.PAGE_SIZE:(self.page + 1) * worldradio.PAGE_SIZE]
            options = [discord.SelectOption(label=n[:100], value=n[:100], emoji="📻") for n in page_names]
            has_next = len(names) > (self.page + 1) * worldradio.PAGE_SIZE
        else:
            options = [
                discord.SelectOption(label=s.name, value=str(i), description=s.description, emoji="📻")
                for i, s in enumerate(self.stations)
            ]
            has_next = len(self.stations) == worldradio.PAGE_SIZE
        self._add_select("Pick a station to play", options, self._pick_station, row=2)

        self._add_button("◀", self._prev, row=3, disabled=self.page == 0)
        self._add_button("▶", self._next, row=3, disabled=not has_next)
        if self.mode != "world":
            self._add_button("World", self._to_world, row=3, emoji="🌍")
        if self.mode != "saved":
            self._add_button("Saved", self._to_saved, row=3, emoji="⭐")
        self._add_button("Search", self._search, row=3, emoji="🔎")
        played = self.last_played
        if played and played.name.lower() not in (n.lower() for n in radio.stations()):
            self._add_button(f"Save {played.name}"[:80], self._save, row=4, emoji="⭐", style=discord.ButtonStyle.success)

    def _add_select(self, placeholder: str, options: list[discord.SelectOption], callback, row: int) -> None:
        if not options:
            options = [discord.SelectOption(label="Nothing here", value="none")]
            select = discord.ui.Select(placeholder=placeholder, options=options, row=row, disabled=True)
        else:
            select = discord.ui.Select(placeholder=placeholder, options=options[:25], row=row)

        async def run(interaction: discord.Interaction):
            await callback(interaction, select.values[0])

        select.callback = run
        self.add_item(select)

    def _add_button(self, label: str, callback, row: int, emoji: str | None = None, disabled: bool = False,
                    style: discord.ButtonStyle = discord.ButtonStyle.secondary) -> None:
        button = discord.ui.Button(label=label, emoji=emoji, row=row, disabled=disabled, style=style)
        button.callback = callback
        self.add_item(button)

    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        await _report(interaction, error)

    async def _refresh(self, interaction: discord.Interaction, reload: bool = True) -> None:
        await interaction.response.defer()
        if reload:
            await self.load()
        else:
            self.render()
        await interaction.edit_original_response(content=self.header(), view=self)

    # ── Callbacks ───────────────────────────────────────────────────

    async def _pick_country(self, interaction: discord.Interaction, value: str):
        self.country, self.page, self.status = (None if value == _ANY else value), 0, ""
        await self._refresh(interaction)

    async def _pick_genre(self, interaction: discord.Interaction, value: str):
        self.genre, self.page, self.status = (None if value == _ANY else value), 0, ""
        await self._refresh(interaction)

    async def _prev(self, interaction: discord.Interaction):
        self.page, self.status = max(0, self.page - 1), ""
        await self._refresh(interaction, reload=self.mode != "saved")

    async def _next(self, interaction: discord.Interaction):
        self.page, self.status = self.page + 1, ""
        await self._refresh(interaction, reload=self.mode != "saved")

    async def _to_world(self, interaction: discord.Interaction):
        self.mode, self.page, self.status = "world", 0, ""
        await self._refresh(interaction)

    async def _to_saved(self, interaction: discord.Interaction):
        self.mode, self.page, self.status = "saved", 0, ""
        await self._refresh(interaction, reload=False)

    async def _search(self, interaction: discord.Interaction):
        await interaction.response.send_modal(SearchModal(self.cog, self))

    async def _pick_station(self, interaction: discord.Interaction, value: str):
        voice_channel = self.cog._listener_channel(interaction)
        station = None
        if self.mode == "saved":
            track = radio.track_for(value, interaction.user.mention)
        else:
            station = self.stations[int(value)]
            track = station.track(interaction.user.mention)
        await interaction.response.defer()
        self.status = await self.cog.play_station(interaction, voice_channel, track)
        if station:
            self.last_played = station
            asyncio.create_task(worldradio.count_play(station))
        self.render()
        await interaction.edit_original_response(content=self.header(), view=self)

    async def _save(self, interaction: discord.Interaction):
        station = self.last_played
        radio.add(station.name, station.url)
        self.status = f"⭐ Saved **{station.name}**. It's now in `/radio play` and the Saved list."
        await self._refresh(interaction, reload=False)


class RadioPanel(discord.ui.View):
    """The permanent panel. Its buttons keep working after restarts because they have fixed IDs."""

    def __init__(self, cog: Music):
        super().__init__(timeout=None)
        self.cog = cog

    @staticmethod
    def embed() -> discord.Embed:
        return discord.Embed(
            title="📻 Radio",
            description=(
                "Join a voice channel, then pick a station.\n"
                "⭐ **Saved** has BBC and UK stations, plus any you save.\n"
                "🌍 **World** browses thousands of stations by country and style.\n"
                "🔎 **Search** finds a station by name.\n\n"
                "Picking a station while the radio is on switches to it. During songs, it plays next."
            ),
            colour=discord.Colour.blurple(),
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        await _report(interaction, error)

    async def _open(self, interaction: discord.Interaction, mode: str) -> None:
        browser = RadioBrowser(self.cog, mode)
        await interaction.response.defer(ephemeral=True, thinking=True)
        if mode == "saved":
            browser.render()
        else:
            await browser.load()
        await interaction.followup.send(browser.header(), view=browser, ephemeral=True)

    @discord.ui.button(label="Saved", emoji="⭐", style=discord.ButtonStyle.primary, custom_id="radio-panel:saved")
    async def saved(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._open(interaction, "saved")

    @discord.ui.button(label="World", emoji="🌍", style=discord.ButtonStyle.primary, custom_id="radio-panel:world")
    async def world(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._open(interaction, "world")

    @discord.ui.button(label="Search", emoji="🔎", style=discord.ButtonStyle.secondary, custom_id="radio-panel:search")
    async def search(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(SearchModal(self.cog, None))
