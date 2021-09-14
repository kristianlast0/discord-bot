from mediahandler import MediaHandler
import discord
from asyncio import sleep

class VoiceConnection():
    mh = MediaHandler
    client = None
    stopped = True
    def __init__ (self):
        pass

    async def getClient(self, ctx):
        user = ctx.author.voice.channel
        #print(user)
        if user == None: return None
        if ctx.voice_client is None: 
            self.client = await user.connect()
            return self.client
        else:
            #self.client = await ctx.voice_client.move_to(user.voice.channel)
            return self.client
    
    async def disconnect(ctx):
        return

    async def playPCM(self, ctx):
        if self.mh.tracksNew[0]["pos"] == 0:
            self.mh.tracksNew[0]["pos"] = 1
        self.stopped = False
        while not self.stopped:
            source = discord.FFmpegPCMAudio(DURL)
            await ctx.voice_client.play(source, after=lambda e:self.EOA(self))
            self.client = ctx.voice_client
        return self.client

    async def playQueueOpus(self, ctx):
        if self.mh.tracksNew[0]["pos"] == 0:
            self.mh.tracksNew[0]["pos"] = 1
        self.stopped = False
        while not self.stopped:
            print("Loop")
            source = await discord.FFmpegOpusAudio.from_probe(self.mh.getCurrentDURL(self.mh))
            ctx.voice_client.play(source, after=lambda e:self.EOA(self))
            self.client = ctx.voice_client
            while self.client.is_playing():
                await sleep(1)
        return self.client

    def stop(self):
        self.stopped = True
        self.client.stop()

    def EOA(self):
        if not self.stopped and len(self.mh.tracksNew) - 1 > self.mh.tracksNew[0]["pos"]:
            self.mh.tracksNew[0]["pos"] += 1
            print("New track available: "+str(self.mh.tracksNew[0]["pos"]))
            return
        else :
            self.stopped = True
            print("No track available! "+str(self.mh.tracksNew[0]["pos"]))
        
            