from mediahandler import MediaHandler
import discord
from asyncio import sleep

class VoiceConnection:

    def __init__ (self, bitRate):
        self.mh = MediaHandler(bitRate)
        self.client = None
        self.stopped = True

    def is_connected(self):
        if self.client == None:
            return False
        else:
            return(self.client.is_connected())

    async def connect(self, ctx):
        try:
            self.client = await ctx.author.voice.channel.connect()
        except:
            print("Already connected to a voice channel.")
        return

    async def disconnect(self):
        await self.stop()
        await self.client.disconnect()
        return

    async def playQueue(self, ctx, opus = True):
        if opus: encoder = discord.FFmpegOpusAudio.from_probe
        else: encoder = discord.FFmpegPCMAudio
        self.playing = False # Do not use this!!!.

        def r():
            self.playing = False
            if not self.stopped:
                if self.mh.getTrackIndex() == self.mh.incTrackIndex():
                    self.stopped = True
                    if self.client.is_playing():
                        self.client.stop()

        self.stopped = False
        while not self.stopped:
            source, stream = await self.mh.getSource(ctx)
            if stream:
                msg = await ctx.send("**[Streaming:]** " + self.mh.getName())
            else:
                msg = await ctx.send("**[Playing:]** " + self.mh.getName())
            await msg.add_reaction(emoji="📜")
            await msg.add_reaction(emoji="🔗")
            #await msg.add_reaction(emoji="👍")
            self.playing = True
            self.client.play(await encoder(source), after=lambda e:r())
            while self.playing:
                await sleep(1)

        return self.client

    async def stop(self):
        self.stopped = True
        if self.client.is_playing():
            self.client.stop()
        await sleep(1) # Increase for stability.

    async def playPause(self, ctx):
        if self.client.is_playing():
            self.client.pause()
            await ctx.send("Music paused.")
        elif self.client.is_paused():
            self.client.resume()
            await ctx.send("Music unpaused.")
        elif self.stopped:
            await self.playQueue(ctx)
        else:
            await ctx.send("The bot is not playing anything at the moment.")
        return

    # async def playQueueOld(self, ctx, opus = True):
    #     self.stopped = False
    #     if opus: encoder = discord.FFmpegOpusAudio.from_probe
    #     else: encoder = discord.FFmpegPCMAudio
    #     while not self.stopped:
    #         while self.client.is_playing() or self.client.is_paused():
    #             await sleep(1)
    #         msg = await ctx.send("**[Playing:]** " + self.mh.getCurrentName())
    #         await msg.add_reaction(emoji="📜")
    #         #await msg.add_reaction(emoji="👍")
    #         source = await encoder(self.mh.getCurrentSource())
    #         self.client.play(source)
    #         print("Playing source. Entering while is_playing loop")
    #         while self.client.is_playing() or self.client.is_paused():
    #             await sleep(1)
    #         #self.client.stop()
    #         print("Exit is_playing loop")
    #         if not self.stopped:
    #             print("Not stopped triggered")
    #             if self.mh.getTrackIndex() == self.mh.incTrackIndex():
    #                 print("Last track triggered")
    #                 self.stop()
    #         else:
    #             print("Stopped triggered")
    #         print("End of while not stopped loop")
    #     print("End of function")
    #     return self.client

    # async def getClient(self, ctx):
        #     try:
        #         user = ctx.author.voice.channel
        #     except:
        #         return None
        #     if ctx.voice_client is None:
        #         self.client = await user.connect()
        #         return self.client
        #     else:
        #         #self.client = await ctx.voice_client.move_to(user.voice.channel)
        #         return self.client