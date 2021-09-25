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

    def checkChannelEmpty(self): # This returns just bot until member reconnects. Useless.
        if len(self.client.channel.members) > 1:
            return False
        else:
            return True

    def getChannel(self):
        return(self.client.channel)
    
    def getSessionid(self):
        return(self.client.session_id)

    async def playQueue(self, ctx, opus = True):
        if opus: encoder = discord.FFmpegOpusAudio.from_probe
        else: encoder = discord.FFmpegPCMAudio
        self.__playing = False

        def r():
            self.__playing = False
            if not self.stopped:
                if self.mh.getTrackIndex() == self.mh.incTrackIndex():
                    self.stopped = True
                    if self.client.is_playing():
                        self.client.stop()

        self.stopped = False
        while not self.stopped:
            source, stream = self.mh.getSource()
            if stream:
                msg = await ctx.send("**[Streaming:]** " + self.mh.getName())
            else:
                msg = await ctx.send("**[Playing:]** " + self.mh.getName())
            await msg.add_reaction(emoji="📜")
            await msg.add_reaction(emoji="🔗")
            #await msg.add_reaction(emoji="👍")
            self.__playing = True
            self.client.play(await encoder(source), after=lambda e:r())
            while self.__playing:
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
        elif self.stopped and len(self.mh.tracks) > 1:
            await self.playQueue(ctx)
        else:
            await ctx.send("The bot cannot playing anything at the moment. Is the playlist empty?")
        return
        