from mediahandler import MediaHandler
import discord
from asyncio import sleep

class VoiceConnection:

    def __init__ (self):
        self.mh = MediaHandler()
        self.client = None
        self.stopped = True

    async def getClient(self, ctx):
        #try:
        user = ctx.author.voice.channel
        #except:
            #return None
        if ctx.voice_client is None:
            self.client = await user.connect()
            return self.client
        else:
            #self.client = await ctx.voice_client.move_to(user.voice.channel)
            return self.client
    
    async def disconnect(ctx):
        return

    async def playQueue(self, ctx, opus = True):
        if self.mh.getTrackIndex() == 0:
            self.mh.setTrackIndex()
        if opus: encoder = discord.FFmpegOpusAudio.from_probe
        else: encoder = discord.FFmpegPCMAudio
        self.stopped = False
        while not self.stopped:
            print("Loop")
            msg = await ctx.send("[Playing:]** " + self.mh.getCurrentName())
            await msg.add_reaction(emoji="📜")
            source = await encoder(self.mh.getCurrentDURL())
            #try:
            await ctx.voice_client.play(source, after=lambda e:self.EOA())
            while ctx.voice_client.is_playing():
                await sleep(1)
            if not self.stopped(self):
                if self.mh.getTrackIndex() == self.mh.incTrackIndex():
                    self.stopped = True
                    return ctx.voice_client
            #except:
             #   print("playQueue tried to loop into playing track. This needs to be fixed.")
        return self.client

    async def stop(self):
        self.stopped = True
        self.client.stop()

    def EOA(self):
        # if self.mh.getTrackIndex(self.mh) == self.mh.incTrackIndex(self.mh):
        #     self.stopped = True
        #     try :
        #         self.client.stop()
        #     except:
        #         pass
        # print(self.mh.getTrackIndex(self.mh))
        return

        
            