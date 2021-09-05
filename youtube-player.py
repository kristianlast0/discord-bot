import discord
from discord.ext import commands,tasks
import os
from dotenv import load_dotenv
import random
from mediahandler import MediaHandler
import pyttsx3
import json
from datastore import welcome
load_dotenv()
 
# Get the API token from the .env file.
#DISCORD_TOKEN = os.getenv("discord_token")
DISCORD_TOKEN = os.getenv("develop_token")

client = discord.Client()
 
if DISCORD_TOKEN == os.getenv("discord_token"): bot = commands.Bot(command_prefix='!')
else: bot = commands.Bot(command_prefix='#')
 
mh = MediaHandler()
 
@bot.command(name='join')
async def join(ctx):
    if ctx.message.author.voice:
        channel = ctx.message.author.voice.channel
    await channel.connect()
    await welcome(ctx)

@bot.command(name='leave')
async def leave(ctx):
    voice_client = ctx.message.guild.voice_client
    if voice_client.is_connected():
        await voice_client.disconnect()
    else:
        await ctx.send("The bot is not connected to a voice channel.")
 
@bot.command(name='flush', help='Flush queue.')
async def flush(ctx):
    mh.flush()
    await ctx.send("Flushing queue.")
    print("Flushing queue.")

def onPlayerStopped(ctx, vc, lastTrackIndex):
    print("onPlayerStopped")
    if mh.stopped == False:
        mh.endTrack(lastTrackIndex)
        if mh.hasNextTrack == True and lastTrackIndex < (len(mh.tracks)-1):
            mh.next()
            playTrack(ctx, vc, mh.currentTrackIndex)

def playTrack(ctx, vc, trackIndex):
    if not vc.is_playing():
        mh.startTrack(trackIndex)
        source = discord.FFmpegPCMAudio(mh.currentTrack['filepath'])
        vc.play(source, after=lambda e:onPlayerStopped(ctx, vc, trackIndex))
        mh.stopped = False

@bot.command(name='play')
async def play(ctx, *search):
    search = (" ").join(search)
    user = ctx.author
    if user.voice is None or user.voice.channel is None: return
    voice_channel = user.voice.channel
    if ctx.voice_client is None: 
        vc = await voice_channel.connect()
        await welcome(ctx)
    else:
        await ctx.voice_client.move_to(voice_channel)
        vc = ctx.voice_client
 
    if search != "":
        added_track = await mh.addTrack(search)
        if mh.currentTrack['completed_at'] is not None and mh.hasNextTrack == True:
            mh.next()
        await ctx.send(f"Queued: **{added_track['title']}**")
        playTrack(ctx, vc, mh.currentTrackIndex)
    elif search == "":
        if mh.stopped == True and not vc.is_playing():
            playTrack(ctx, vc, mh.currentTrackIndex)
        if mh.stopped == False and vc.is_paused():
            await vc.resume()
 
@bot.command(name='pause')
async def pause(ctx):
    voice_client = ctx.message.guild.voice_client
    if voice_client.is_playing():
        await voice_client.pause()
        await ctx.send("Music paused, use !resume to continue")
    else:
        await ctx.send("The bot is not playing anything at the moment.")
 
@bot.command(name='skip')
async def skip(ctx):
    user = ctx.author
    if user.voice is None or user.voice.channel is None: return
    voice_channel = user.voice.channel
    if ctx.voice_client is None:
        vc = await voice_channel.connect()
    else:
        await ctx.voice_client.move_to(voice_channel)
        vc = ctx.voice_client

    if vc.is_playing() and mh.hasNextTrack == True:
        mh.stopped = False
        vc.stop()
    else:
        await ctx.send("The bot is not playing anything or has no next track.")
 
@bot.command(name='welcome')
async def welcome(ctx):
    f, p = random.choice(list(welcome.items()))
    print(f, p)
    vc = ctx.message.guild.voice_client
    path = os.getenv("track_path")+'tts-welcome.mp3'
    engine = pyttsx3.init()
    engine.save_to_file('Whats up bitches', path)
    engine.runAndWait()
    if os.path.isfile(path):
        source = discord.FFmpegPCMAudio(path)
        vc.play(source)

@bot.command(name='back')
async def back(ctx):
    user = ctx.author
    if user.voice is None or user.voice.channel is None: return
    voice_channel = user.voice.channel
    if ctx.voice_client is None:
        vc = await voice_channel.connect()
    else:
        await ctx.voice_client.move_to(voice_channel)
        vc = ctx.voice_client

    if vc.is_playing():
        mh.stopped = True
        vc.stop()
    mh.prev()
    playTrack(ctx, vc, mh.currentTrackIndex)

@bot.command(name='restart')
async def restart(ctx):
    user = ctx.author
    if user.voice is None or user.voice.channel is None: return
    voice_channel = user.voice.channel
    if ctx.voice_client is None:
        vc = await voice_channel.connect()
    else:
        await ctx.voice_client.move_to(voice_channel)
        vc = ctx.voice_client

    if vc.is_playing():
        mh.stopped = True
        vc.stop()
    mh.currentTrackIndex = 0
    playTrack(ctx, vc, mh.currentTrackIndex)
 
@bot.command(name='resume')
async def resume(ctx):
    voice_client = ctx.message.guild.voice_client
    if voice_client.is_paused():
        await voice_client.resume()
    else:
        await ctx.send("The bot was not playing anything before this. Use play command")
 
@bot.command(name='stop')
async def stop(ctx):
    mh.stopped = True
    voice_client = ctx.message.guild.voice_client
    if voice_client.is_playing():
        await voice_client.stop()
    else:
        await ctx.send("The bot is not playing anything at the moment.")
 
@bot.command(name='queue')
async def queue(ctx):
    trackList = ""
    for idx,track in enumerate(mh.queue):
        trackList += "**"+str(idx+1)+": "+("[Playing]** " if mh.currentTrackIndex == idx else "**")+track['title']+"\n"
    await ctx.send("**Media Queue:** \n"+trackList)
 
@bot.command(name='insult')
async def insult(ctx):
    adj = ["absolute", "utter", "incompetent", "hidious", "unbareable", "total", "massive", "useless"]
    noun = ["cock nosher", "tool", "cretin", "hoop sniffer", "imbecile", "lemming"]
    i = "You " + random.choice (adj) + " " + random.choice (noun)
    await ctx.send("You " + random.choice (adj) + " " + random.choice (noun))
 
@bot.event
async def on_ready():
    print("Bot is ready!")
 
if __name__ == "__main__" :
    bot.run(DISCORD_TOKEN)