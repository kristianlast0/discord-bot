import logging
import sys

import discord
from discord.ext import commands

from . import config
from .cog import Music, on_app_command_error

log = logging.getLogger("music_bot")


class MusicBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix=commands.when_mentioned, intents=discord.Intents.default(), help_command=None)

    async def setup_hook(self):
        await self.add_cog(Music(self))
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
        await self.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name="/play"))


def main():
    if not config.TOKEN:
        sys.exit("DISCORD_TOKEN isn't set. Put it in .env (see .env.example).")
    MusicBot().run(config.TOKEN, root_logger=True)


if __name__ == "__main__":
    main()
