import asyncio
import logging
import signal
import sys

import discord
from discord.ext import commands

from . import config
from .cog import Music, on_app_command_error
from .radio_ui import RadioPanel

log = logging.getLogger("music_bot")


class MusicBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix=commands.when_mentioned, intents=discord.Intents.default(), help_command=None)

    async def setup_hook(self):
        music = Music(self)
        await self.add_cog(music)
        self.add_view(RadioPanel(music))  # keeps panel buttons working after restarts
        self.tree.error(on_app_command_error)
        if config.GUILD_ID:
            guild = discord.Object(config.GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
        else:
            synced = await self.tree.sync()
        log.info("Synced %d slash commands%s", len(synced), " to the server" if config.GUILD_ID else " globally")

    async def on_ready(self):
        log.info("Logged in as %s; music folder %s", self.user, config.MUSIC_DIR)
        for guild in self.guilds:
            # After a crash Discord can still show the bot in a voice channel it no longer controls.
            if guild.me.voice and guild.me.voice.channel and not guild.voice_client:
                log.info("Leaving leftover voice connection in %s", guild.me.voice.channel)
                await guild.change_voice_state(channel=None)
        await self.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name="/play"))


    async def close(self):
        music = self.get_cog("Music")
        if music:
            await music.shutdown()
        await super().close()


async def _run() -> None:
    bot = MusicBot()
    # systemd stops the service with SIGTERM; leave voice channels cleanly instead of vanishing.
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, lambda: asyncio.create_task(bot.close()))
    async with bot:
        await bot.start(config.TOKEN)


def main():
    if not config.TOKEN:
        sys.exit("DISCORD_TOKEN isn't set. Put it in .env (see .env.example).")
    discord.utils.setup_logging(root=True)
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
