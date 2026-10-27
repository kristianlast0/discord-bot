"""Slash commands. Replies are private (ephemeral); the live player message is the only public post."""
import logging
import random

import discord
from discord import app_commands
from discord.ext import commands

from . import library, playlists, radio, settings, tracks
from .models import Track, TrackError, UserError, fmt_duration
from .player import GuildPlayer
from .radio_ui import RadioBrowser, RadioPanel

log = logging.getLogger(__name__)
_PAGE = 10


def _requester(interaction: discord.Interaction) -> str:
    return interaction.user.mention


def _summary(found: list[Track], position: int, starting: bool) -> str:
    first = found[0]
    if len(found) == 1:
        where = "Playing" if starting else f"Queued at #{position}:"
        return f"{where} **{first.title}** ({fmt_duration(first.duration)})"
    total = sum(t.duration or 0 for t in found)
    where = "Playing" if starting else f"Queued from #{position}:"
    return f"{where} **{len(found)} tracks** ({fmt_duration(total)}), beginning with **{first.title}**"


async def _reply(interaction: discord.Interaction, content: str | None = None, **kwargs) -> None:
    await interaction.response.send_message(content, ephemeral=True, **kwargs)


class Music(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.players: dict[int, GuildPlayer] = {}

    def player(self, guild: discord.Guild) -> GuildPlayer:
        if guild.id not in self.players:
            self.players[guild.id] = GuildPlayer(self.bot, guild.id)
        return self.players[guild.id]

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Keep music commands in the server's music channel, if one is set."""
        channel_id = settings.music_channel(interaction.guild_id) if interaction.guild_id else None
        if channel_id and interaction.channel_id != channel_id and interaction.command.name != "musicchannel":
            if self.bot.get_channel(channel_id):
                raise UserError(f"Music commands go in <#{channel_id}>.")
        return True

    def _listener_channel(self, interaction: discord.Interaction):
        """The caller's voice channel, if the bot may join or already plays there."""
        voice = getattr(interaction.user, "voice", None)
        if not voice or not voice.channel:
            raise UserError("Join a voice channel first.")
        p = self.player(interaction.guild)
        vc = p.voice
        if vc and vc.is_connected() and vc.channel != voice.channel and p.active:
            raise UserError(f"I'm already playing in {vc.channel.mention}. Join me there.")
        return voice.channel

    def _control(self, interaction: discord.Interaction) -> GuildPlayer:
        """For commands that change playback: caller must be in the bot's channel."""
        p = self.player(interaction.guild)
        p.check_listener(interaction.user)
        return p

    async def _enqueue(self, interaction: discord.Interaction, channel, found: list[Track], front: bool) -> None:
        """Called after deferring: join, queue, start, and reply."""
        p = self.player(interaction.guild)
        await p.connect(channel)
        p.channel = interaction.channel
        starting = p.current is None and not p.active and not p.queue
        position = p.add(found, front=front)
        if not starting:
            await p.refresh_player()
            await interaction.followup.send(f"➕ {_summary(found, position, False)}", ephemeral=True)
            return
        await p.start()
        if p.current is None:
            await interaction.followup.send("⚠️ I couldn't play any of that.", ephemeral=True)
            return
        await interaction.followup.send(f"▶️ {_summary(found, position, True)}", ephemeral=True)

    async def play_station(self, interaction: discord.Interaction, channel, track: Track) -> str:
        """Radio replaces radio straight away; during songs it plays next. Returns a status line."""
        p = self.player(interaction.guild)
        await p.connect(channel)
        p.channel = interaction.channel
        if p.current and p.current.kind == "stream":
            p.add([track], front=True)
            p.skip()
            return f"📻 Switching to **{track.title}**."
        if p.current or p.active:
            p.add([track], front=True)
            await p.refresh_player()
            return f"📻 **{track.title}** will start after the current track."
        p.add([track])
        await p.start()
        if p.current is not track:
            raise UserError(f"**{track.title}** isn't working right now. Try another station.")
        return f"▶️ Playing **{track.title}**."

    # ── Setup ───────────────────────────────────────────────────────

    @app_commands.command(description="Choose the text channel for music commands and the player")
    @app_commands.describe(channel="Leave empty to allow music commands in any channel")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def musicchannel(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None):
        if channel is None:
            settings.set_music_channel(interaction.guild_id, None)
            await _reply(interaction, "Music commands now work in any channel.")
            return
        perms = channel.permissions_for(interaction.guild.me)
        if not (perms.view_channel and perms.send_messages and perms.embed_links):
            raise UserError(f"I need View Channel, Send Messages and Embed Links in {channel.mention}.")
        settings.set_music_channel(interaction.guild_id, channel.id)
        await self.player(interaction.guild).refresh_player(repost=True)
        await _reply(interaction, f"🎵 Music commands and the player now live in {channel.mention}.")

    # ── Adding music ────────────────────────────────────────────────

    @app_commands.command(description="Play from YouTube, Spotify or another link, or search YouTube")
    @app_commands.describe(query="Song name, or a YouTube / Spotify / SoundCloud link (playlists work too)", next="Play it next instead of at the end of the queue")
    @app_commands.guild_only()
    async def play(self, interaction: discord.Interaction, query: str, next: bool = False):
        channel = self._listener_channel(interaction)
        await interaction.response.defer(ephemeral=True, thinking=True)
        found = await tracks.lookup(query, _requester(interaction))
        await self._enqueue(interaction, channel, found, next)

    @app_commands.command(description="Play music files stored on the bot's computer")
    @app_commands.describe(query="A song or a whole folder (start typing to search)", next="Play it next instead of at the end of the queue")
    @app_commands.guild_only()
    async def local(self, interaction: discord.Interaction, query: str, next: bool = False):
        channel = self._listener_channel(interaction)
        await interaction.response.defer(ephemeral=True, thinking=True)
        found = await tracks.local(query, _requester(interaction))
        await self._enqueue(interaction, channel, found, next)

    @local.autocomplete("query")
    async def _local_autocomplete(self, interaction: discord.Interaction, current: str):
        return [app_commands.Choice(name=label, value=value) for label, value in library.suggest(current)]

    radio_group = app_commands.Group(name="radio", description="Internet radio", guild_only=True)

    @radio_group.command(name="play", description="Play an internet radio station")
    @app_commands.describe(station="A saved station (start typing) or a stream URL")
    async def radio_play(self, interaction: discord.Interaction, station: str):
        channel = self._listener_channel(interaction)
        track = radio.track_for(station, _requester(interaction))
        await interaction.response.defer(ephemeral=True, thinking=True)
        await interaction.followup.send(await self.play_station(interaction, channel, track), ephemeral=True)

    @radio_group.command(name="browse", description="Browse radio stations from around the world")
    async def radio_browse(self, interaction: discord.Interaction):
        browser = RadioBrowser(self)
        await interaction.response.defer(ephemeral=True, thinking=True)
        await browser.load()
        await interaction.followup.send(browser.header(), view=browser, ephemeral=True)

    @radio_group.command(name="panel", description="Post the radio panel (buttons anyone can use) in the music channel")
    @app_commands.default_permissions(manage_guild=True)
    async def radio_panel(self, interaction: discord.Interaction):
        channel_id = settings.music_channel(interaction.guild_id)
        channel = (self.bot.get_channel(channel_id) if channel_id else None) or interaction.channel
        await channel.send(embed=RadioPanel.embed(), view=RadioPanel(self))
        await _reply(interaction, f"📻 Posted the radio panel in {channel.mention}. Pin it to keep it handy.")

    @radio_play.autocomplete("station")
    async def _station_autocomplete(self, interaction: discord.Interaction, current: str):
        return [
            app_commands.Choice(name=name, value=name)
            for name in radio.stations()
            if current.lower() in name.lower()
        ][:25]

    @radio_group.command(name="list", description="Show the saved radio stations")
    async def radio_list(self, interaction: discord.Interaction):
        lines = [f"• **{name}**" for name in radio.stations()] or ["No stations saved."]
        await _reply(interaction, "\n".join(lines))

    @radio_group.command(name="add", description="Save a radio station (or replace one with the same name)")
    @app_commands.describe(name="What to call it", url="The stream URL (.mp3, .aac, .m3u8, .pls or .m3u)")
    async def radio_add(self, interaction: discord.Interaction, name: app_commands.Range[str, 1, 80], url: str):
        radio.add(name, url)
        await _reply(interaction, f"📻 Saved **{name.strip()}**.")

    @radio_group.command(name="remove", description="Delete a saved radio station")
    async def radio_remove(self, interaction: discord.Interaction, name: str):
        removed = radio.remove(name)
        await _reply(interaction, f"🗑️ Removed **{removed}**.")

    radio_remove.autocomplete("name")(_station_autocomplete)

    # ── Playback control ────────────────────────────────────────────

    @app_commands.command(description="Pause the music")
    @app_commands.guild_only()
    async def pause(self, interaction: discord.Interaction):
        p = self._control(interaction)
        if not p.pause():
            raise UserError("Nothing is playing.")
        await _reply(interaction, "⏸️ Paused.")
        await p.refresh_player()

    @app_commands.command(description="Carry on after pausing")
    @app_commands.guild_only()
    async def resume(self, interaction: discord.Interaction):
        p = self._control(interaction)
        if not p.resume():
            raise UserError("Nothing is paused.")
        await _reply(interaction, "▶️ Resumed.")
        await p.refresh_player()

    @app_commands.command(description="Skip the current track")
    @app_commands.guild_only()
    async def skip(self, interaction: discord.Interaction):
        p = self._control(interaction)
        title = p.current.title if p.current else None
        if not p.skip():
            raise UserError("Nothing is playing.")
        await _reply(interaction, f"⏭️ Skipped **{title}**.")

    @app_commands.command(description="Stop the music and clear the queue")
    @app_commands.guild_only()
    async def stop(self, interaction: discord.Interaction):
        self._control(interaction).stop()
        await _reply(interaction, "⏹️ Stopped and cleared the queue.")

    @app_commands.command(description="Stop and leave the voice channel")
    @app_commands.guild_only()
    async def leave(self, interaction: discord.Interaction):
        await self._control(interaction).leave()
        await _reply(interaction, "👋 Left the voice channel.")

    @app_commands.command(description="Set the volume")
    @app_commands.describe(percent="0 to 150 (default 50)")
    @app_commands.guild_only()
    async def volume(self, interaction: discord.Interaction, percent: app_commands.Range[int, 0, 150]):
        p = self._control(interaction)
        p.set_volume(percent)
        await _reply(interaction, f"🔊 Volume set to {percent}%.")
        await p.refresh_player()

    @app_commands.command(description="Repeat the current track or the whole queue")
    @app_commands.choices(mode=[
        app_commands.Choice(name="Off", value="off"),
        app_commands.Choice(name="This track", value="track"),
        app_commands.Choice(name="Whole queue", value="queue"),
    ])
    @app_commands.guild_only()
    async def loop(self, interaction: discord.Interaction, mode: app_commands.Choice[str]):
        p = self._control(interaction)
        p.loop_mode = mode.value
        icons = {"off": "➡️ Looping off.", "track": "🔂 Looping this track.", "queue": "🔁 Looping the queue."}
        await _reply(interaction, icons[mode.value])
        await p.refresh_player()

    @app_commands.command(description="Shuffle the queue")
    @app_commands.guild_only()
    async def shuffle(self, interaction: discord.Interaction):
        p = self._control(interaction)
        p.shuffle()
        await _reply(interaction, f"🔀 Shuffled {len(p.queue)} tracks.")
        await p.refresh_player()

    @app_commands.command(description="Remove a track from the queue")
    @app_commands.describe(position="Its number in /queue")
    @app_commands.guild_only()
    async def remove(self, interaction: discord.Interaction, position: app_commands.Range[int, 1]):
        p = self._control(interaction)
        if position > len(p.queue):
            raise UserError(f"The queue only has {len(p.queue)} tracks.")
        removed = p.queue.pop(position - 1)
        await _reply(interaction, f"🗑️ Removed **{removed.title}**.")
        await p.refresh_player()

    @app_commands.command(description="Empty the queue (the current track keeps playing)")
    @app_commands.guild_only()
    async def clear(self, interaction: discord.Interaction):
        p = self._control(interaction)
        count = len(p.queue)
        p.queue.clear()
        await _reply(interaction, f"🧹 Cleared {count} tracks from the queue.")
        await p.refresh_player()

    # ── Information ─────────────────────────────────────────────────

    @app_commands.command(description="Bring the player back to the bottom of the channel")
    @app_commands.guild_only()
    async def nowplaying(self, interaction: discord.Interaction):
        p = self.player(interaction.guild)
        if not p.current:
            raise UserError("Nothing is playing.")
        await _reply(interaction, "👇 Here's the player.")
        await p.refresh_player(repost=True)

    @app_commands.command(description="Show the queue")
    @app_commands.describe(page="Page number")
    @app_commands.guild_only()
    async def queue(self, interaction: discord.Interaction, page: app_commands.Range[int, 1] = 1):
        p = self.player(interaction.guild)
        pages = max(1, -(-len(p.queue) // _PAGE))
        page = min(page, pages)
        start = (page - 1) * _PAGE
        lines = []
        if p.current:
            lines.append(f"**Now:** {p.current.title} ({fmt_duration(p.current.duration)})\n")
        for i, t in enumerate(p.queue[start:start + _PAGE], start=start + 1):
            lines.append(f"`{i}.` {t.title[:80]} ({fmt_duration(t.duration)})")
        if not p.queue:
            lines.append("The queue is empty. Add something with `/play`.")
        embed = discord.Embed(title="Queue", description="\n".join(lines), colour=discord.Colour.blurple())
        total = sum(t.duration or 0 for t in p.queue)
        footer = f"{len(p.queue)} tracks · {fmt_duration(total)} · page {page}/{pages}"
        if p.loop_mode != "off":
            footer += f" · looping {p.loop_mode}"
        embed.set_footer(text=footer)
        await _reply(interaction, embed=embed)

    # ── Saved playlists ─────────────────────────────────────────────

    playlist_group = app_commands.Group(name="playlist", description="Saved playlists", guild_only=True)

    async def _playlist_autocomplete(self, interaction: discord.Interaction, current: str):
        return [
            app_commands.Choice(name=n, value=n)
            for n in playlists.names(interaction.guild_id)
            if current.lower() in n.lower()
        ][:25]

    def _check_owner(self, interaction: discord.Interaction, saved: dict) -> None:
        if saved["owner"] != interaction.user.id and not interaction.user.guild_permissions.manage_guild:
            raise UserError(f"Only <@{saved['owner']}> or a server manager can change that playlist.")

    @playlist_group.command(name="save", description="Save the current track and queue as a playlist")
    @app_commands.describe(name="Playlist name (an existing one with this name is replaced)")
    async def playlist_save(self, interaction: discord.Interaction, name: app_commands.Range[str, 1, 80]):
        p = self.player(interaction.guild)
        current = [p.current] if p.current else []
        if not current and not p.queue:
            raise UserError("Nothing is playing or queued to save.")
        existing = playlists.get(interaction.guild_id, name)
        if existing:
            self._check_owner(interaction, existing[1])
        to_save = current + p.queue
        playlists.save(interaction.guild_id, name, interaction.user.id, to_save)
        kept = min(len(to_save), playlists.MAX_TRACKS)
        await _reply(interaction, f"💾 Saved **{name.strip()}** ({kept} tracks).")

    @playlist_group.command(name="load", description="Queue a saved playlist")
    @app_commands.describe(name="Playlist name", shuffle="Shuffle it first")
    @app_commands.autocomplete(name=_playlist_autocomplete)
    async def playlist_load(self, interaction: discord.Interaction, name: str, shuffle: bool = False):
        channel = self._listener_channel(interaction)
        saved = playlists.get(interaction.guild_id, name)
        if not saved:
            raise UserError(f"There's no playlist called “{name}”.")
        found = [Track.from_dict(d, _requester(interaction)) for d in saved[1]["tracks"]]
        if not found:
            raise UserError("That playlist is empty.")
        if shuffle:
            random.shuffle(found)
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self._enqueue(interaction, channel, found, front=False)

    @playlist_group.command(name="list", description="Show the saved playlists")
    async def playlist_list(self, interaction: discord.Interaction):
        names = playlists.names(interaction.guild_id)
        if not names:
            raise UserError("No playlists saved yet. Use `/playlist save` while music is queued.")
        lines = []
        for n in names:
            _, saved = playlists.get(interaction.guild_id, n)
            lines.append(f"• **{n}** · {len(saved['tracks'])} tracks · by <@{saved['owner']}>")
        await _reply(interaction, "\n".join(lines))

    @playlist_group.command(name="show", description="List the tracks in a saved playlist")
    @app_commands.autocomplete(name=_playlist_autocomplete)
    async def playlist_show(self, interaction: discord.Interaction, name: str):
        saved = playlists.get(interaction.guild_id, name)
        if not saved:
            raise UserError(f"There's no playlist called “{name}”.")
        key, data = saved
        lines = [f"`{i}.` {t['title'][:80]} ({fmt_duration(t.get('duration'))})" for i, t in enumerate(data["tracks"][:25], 1)]
        if len(data["tracks"]) > 25:
            lines.append(f"…and {len(data['tracks']) - 25} more")
        embed = discord.Embed(title=key, description="\n".join(lines) or "Empty.", colour=discord.Colour.blurple())
        await _reply(interaction, embed=embed)

    @playlist_group.command(name="delete", description="Delete a saved playlist")
    @app_commands.autocomplete(name=_playlist_autocomplete)
    async def playlist_delete(self, interaction: discord.Interaction, name: str):
        saved = playlists.get(interaction.guild_id, name)
        if not saved:
            raise UserError(f"There's no playlist called “{name}”.")
        self._check_owner(interaction, saved[1])
        playlists.delete(interaction.guild_id, name)
        await _reply(interaction, f"🗑️ Deleted **{saved[0]}**.")

    # ── Events ──────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if member.id == self.bot.user.id and before.channel and after.channel is None:
            p = self.players.get(member.guild.id)
            if p:
                p.reset()


async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    original = error.original if isinstance(error, app_commands.CommandInvokeError) else error
    if isinstance(original, (UserError, TrackError)):
        message = f"⚠️ {original}"
    elif isinstance(original, app_commands.NoPrivateMessage):
        message = "⚠️ That only works in a server."
    elif isinstance(original, discord.ClientException) and "voice" in str(original).lower():
        message = f"⚠️ Voice problem: {original}"
    else:
        log.error("Command /%s failed", interaction.command.qualified_name if interaction.command else "?", exc_info=original)
        message = "⚠️ Something went wrong. It's been logged."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except discord.HTTPException:
        pass
