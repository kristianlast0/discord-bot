from mediahandler import MediaHandler
import discord

class VoiceConnection():
    mh = MediaHandler
    vc = None
    def __init__ (self):
        pass

    async def getClient(self, ctx):
        user = ctx.author
        if ctx.voice_client is None: 
            self.vc = await user.voice.channel.connect()
            return self.vc
        else:
            self.vc = await ctx.voice_client.move_to(user.voice.channel)
            return self.vc
    
    async def playPCM(self, ctx, DURL):
        print("Play PCM :" + DURL)
        #source = discord.FFmpegPCMAudio(guild['media_handler'].currentTrack['filepath'])
        await self.vc.play(DURL, after=lambda e:self.r("Stopped"))
        #await c.play(source, after=lambda e:r())


    async def playOpus(self, ctx, DURL):
        print("Play Opus :" + DURL)
        await self.vc.play(discord.FFmpegOpusAudio.from_probe(DURL))
        return(self.vc)
    
    async def is_playing(self, ctx):
        isplaying = await self.getClient(ctx).is_playing()
        return(isplaying)

    def r(p):
        print(p)