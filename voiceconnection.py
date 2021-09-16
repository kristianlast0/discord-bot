from mediahandler import MediaHandler
import discord
from asyncio import sleep

class VoiceConnection:

    def __init__ (self, bitRate):
        self.mh = MediaHandler(bitRate)
        self.client = None
        self.stopped = True

    async def getClient(self, ctx):
        try:
            user = ctx.author.voice.channel
        except:
            return None
        if ctx.voice_client is None:
            self.client = await user.connect()
            return self.client
        else:
            #self.client = await ctx.voice_client.move_to(user.voice.channel)
            return self.client

    async def playQueue(self, ctx, opus = True):
        if opus: encoder = discord.FFmpegOpusAudio.from_probe
        else: encoder = discord.FFmpegPCMAudio
        self.stopped = False
        while not self.stopped:
            msg = await ctx.send("**[Playing:]** " + self.mh.getCurrentName())
            await msg.add_reaction(emoji="📜")
            source = await encoder(self.mh.getCurrentSource())
            await self.client.play(source)
            while self.client.is_playing():
                await sleep(1)
            if not self.stopped(self):
                if self.mh.getTrackIndex() == self.mh.incTrackIndex():
                    self.stopped = True
        return self.client

    async def stop(self):
        self.stopped = True
        if self.client.is_playing():
            self.client.stop()

    async def playpause(self, ctx):
        if self.client.is_playing():
            await self.client.pause()
            await ctx.send("Music paused.")
        elif self.client.is_paused():
            await self.client.resume()
            await ctx.send("Music unpaused.")
        elif self.stopped:
            await self.playQueue(ctx)
        else:
            await ctx.send("The bot is not playing anything at the moment.")
        return

    async def flush(self):
        await self.stop()
        self.mh.flush()         
