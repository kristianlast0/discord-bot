from logging import currentframe
import discord
from discord.ext import commands,tasks
import os
from dotenv import load_dotenv
import random
from mediahandler import MediaHandler
import pyttsx3
import os, json
import pandas as pd
import json
from datetime import datetime
import youtube_dl
import re, requests, urllib.parse, urllib.request

load_dotenv()

# intents = discord.Intents.default()
# intents.members = True
# intents.reactions = True
# intents.messages = True
 
# Get the API token from the .env file.

DISCORD_TOKEN = os.getenv("discord_token") if os.getenv("env") == "prod" else os.getenv("develop_token")

# client = discord.Client(intents=intents)
prefix = "!" if os.getenv("env") == "prod" else os.getenv("command_prefix")
bot = commands.Bot(command_prefix=prefix)
guilds = {}

def getguild(ctx): # get guild relevant objects in dict form.
    # Check what context: Message,Etc
    id = ctx.message.guild.id
    if id in guilds.keys():
        print("Guild found!")
        return(guilds.get(id))
    else:
        d = {
            "guild": ctx.message.guild,
            "media_handler": MediaHandler,
            "voice_connection": VoiceConnection()
        }
        guilds[id] = d
        print("Guild added.")
        print(guilds)
        return d

def getInfo(search, noplaylist=True): # get all relevant search information in dict form.
    try:
        if not search.startswith("https://youtu"):
            query_string = urllib.parse.urlencode({"search_query": search})
            formatUrl = urllib.request.urlopen("https://www.youtube.com/results?" + query_string)
            search_results = re.findall(r"watch\?v=(\S{11})", formatUrl.read().decode())
            search = "https://www.youtube.com/watch?v=" + "{}".format(search_results[0])
        with youtube_dl.YoutubeDL({'format': 'bestaudio/best','noplaylist':noplaylist}) as ydl:
            info = ydl.extract_info(search, download=False)
            return({"title":info["title"], "link":search, "duration":info["duration"], "format": info["formats"][0]})
    except:
        return None

def getDURL(link): # get direct url for video.
    with youtube_dl.YoutubeDL({'format': 'bestaudio/best','noplaylist':True}) as ydl:
        return(ydl.extract_info(link, download=False)["url"])

class VoiceConnection():
    def __init__ (self):
        self.isplaying = False
        self.vchannel = None

    async def getClient(self, ctx):
        user = ctx.author
        if ctx.voice_client is None: 
            return await user.voice.channel.connect()
        else:
            await ctx.voice_client.move_to(user.voice.channel)
            return ctx.voice_clien

@bot.command(name='test')
async def test(ctx, *search):

    search = (" ").join(search)
    guild = getguild(ctx)
    voice = await guild['voice_connection'].getClient(ctx)

    i = getInfo(search)
    if i != None:
        guild["media_handler"].addTrackNew(guild["media_handler"], i)
    else:
        await ctx.send(f"Failed to find result!")
        return
#### Below here is trash.
    try:
        voice.play(await discord.FFmpegOpusAudio.from_probe(getDURL(i["link"])))
    except:
        pass

    if search != "":
        print(search)
        added_track = await guild['media_handler'].addTrackNew(search)
        if added_track[0] == None:
            guild['media_handler'].downloader(added_track[1],added_track[2], added_track[3], added_track[4])
        if guild['media_handler'].currentTrack['completed_at'] is not None and guild['media_handler'].hasNextTrack == True:
            guild['media_handler'].next()
        msg = await ctx.send(f"Queued: **{added_track['title']}**")
        reactions = ["⬅️", "➡️"]
        for react in reactions:
            await msg.add_reaction(emoji=react)
        playTrack(ctx, voice_client, guild['media_handler'].currentTrackIndex)
    elif search == "":
        if guild['media_handler'].stopped == True and not guild['media_handler'].is_playing():
            playTrack(ctx, guild['media_handler'], guild['media_handler'].currentTrackIndex)
        if guild['media_handler'].stopped == False and voice_client.is_paused():
            await guild['media_handler'].resume()

######################################################################################################################################

@bot.command(name='join', category="Media Control", help="Bot will join the channel.")
async def join(ctx):
    if ctx.message.author.voice:
        channel = ctx.message.author.voice.channel
    await channel.connect()
    await welcome(ctx)

@bot.command(name='leave', help="Force bot to leave channel.")
async def leave(ctx):
    voice_client = ctx.message.guild.voice_client
    if voice_client.is_connected():
        await voice_client.disconnect()
    else:
        await ctx.send("The bot is not connected to a voice channel.")
 
@bot.command(name='flush', help='Flush queue of all songs.')
async def flush(ctx):
    guild = getguild(ctx)
    voice = await guild['voice_connection'].getClient(ctx)
    mh.flush()
    await ctx.send("Flushing queue.")
    print("Flushing queue.")

def onPlayerStopped(ctx, vc, lastTrackIndex):
    guild = getguild(ctx)
    voice = guild['voice_connection'].getClient(ctx)
    print("onPlayerStopped")
    if mh.stopped == False:
        mh.endTrack(lastTrackIndex)
        if mh.hasNextTrack == True and lastTrackIndex < (len(mh.tracks)-1):
            mh.next()
            playTrack(ctx, vc, mh.currentTrackIndex)

def playTrack(ctx, vc, trackIndex):
    guild = getguild(ctx)
    if not vc.is_playing():
        guild['media_handler'].startTrack(trackIndex)
        source = discord.FFmpegPCMAudio(guild['media_handler'].currentTrack['filepath'])
        vc.play(source, after=lambda e:r())
        # guild['media_handler'].stopped = False

def r():
    print('stopped')
    return True

@bot.command(name='play', help="Play a song from youtube. Links can be used as well as names of songs. Not providing an argument will just resume playing from current location in playlist.")
async def play(ctx, *search):
    search = (" ").join(search)
    guild = getguild(ctx)
    vc = await guild['voice_connection'].getClient(ctx)

    print(search)
    print(search)
    print(search)
 
    if search != "":
        print(search)
        added_track = await guild['media_handler'].addTrack(guild['media_handler'], search)
        if guild['media_handler'].currentTrack['completed_at'] is not None and guild['media_handler'].hasNextTrack == True:
            guild['media_handler'].next()
        msg = await ctx.send(f"Queued: **{added_track['title']}**")
        reactions = ["⬅️", "➡️"]
        for react in reactions:
            await msg.add_reaction(emoji=react)
        playTrack(ctx, vc, guild['media_handler'].currentTrackIndex)
    elif search == "":
        if guild['media_handler'].stopped == True and not vc.is_playing():
            playTrack(ctx, vc, guild['media_handler'].currentTrackIndex)
        if guild['media_handler'].stopped == False and vc.is_paused():
            await vc.resume()
 
@bot.command(name='pause', help="Pause song. Use !resume or !play to continue.")
async def pause(ctx):
    voice_client = ctx.message.guild.voice_client
    if voice_client.is_playing():
        await voice_client.pause()
        await ctx.send("Music paused, use !resume to continue")
    else:
        await ctx.send("The bot is not playing anything at the moment.")
 
@bot.command(name='skip', help="Skip to next track in queue.")
async def skip(ctx):
    guild = getguild(ctx)
    vc = await guild['voice_connection'].getClient(ctx)

    if vc.is_playing() and mh.hasNextTrack == True:
        mh.stopped = False
        vc.stop()
    else:
        await ctx.send("The bot is not playing anything or has no next track.")

@bot.command(name="pop", help="Remove a track from queue, use !queue to get track number. Example: !pop 2")
async def pop(ctx, index):
    index = int(index) - 1
    if index >= 0 and index <= len(mh.tracks):
        if index == mh.currentTrackIndex:
            print("Pop current track")
            voice_client = ctx.message.guild.voice_client
            if voice_client.is_playing():
                mh.stopped = True
                voice_client.stop()
            mh.tracks.pop(index)
            await play(ctx)
        if index < mh.currentTrackIndex:
            print("popping previos track")
            mh.tracks.pop(index)
            mh.currentTrackIndex -= 1
        if index > mh.currentTrackIndex:
            print("Popping future track")
            mh.tracks.pop(index)
    else:
        await ctx.send("Are you fucking with me?")

@bot.command(name='welcome', help="Trigger the bots welcome message.")
async def welcome(ctx):
    from datastore import welcome_messages as wm
    vc = ctx.message.guild.voice_client
    x = random.choice(list(wm.items()))
    print(x[0], ">", x[1])

    path = os.getenv("tts_path") + x[0] + ".mp3"
    if os.path.exists(path) and os.path.isfile(path):
        print("TTS file exists, playing from disc.")
        vc.play(discord.FFmpegPCMAudio(path))
    else:
        print("TTS does not yet exist, creating!")
        engine = pyttsx3.init()
        engine.save_to_file(x[1], path)
        engine.runAndWait()
        vc.play(discord.FFmpegPCMAudio(path))

@bot.command(name='back', help="Skip backwards through playlist.")
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

@bot.command(name='restart', help="Restart queue from start of playlist.")
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
 
@bot.command(name='resume', help="Resume playing from current location in playlist." )
async def resume(ctx):
    voice_client = ctx.message.guild.voice_client
    if voice_client.is_paused():
        await voice_client.resume()
    else:
        await ctx.send("The bot was not playing anything before this. Use play command")
 
@bot.command(name='stop', help="Stop playing current track.")
async def stop(ctx):
    mh.stopped = True
    voice_client = ctx.message.guild.voice_client
    if voice_client.is_playing():
        await voice_client.stop()
    else:
        await ctx.send("The bot is not playing anything at the moment.")

@bot.command(name='save', help="Save current playlist. Example: !save playlist_name")
async def save(ctx, *name):
    name = (" ").join(name)
    if name == "":
        await ctx.send("**Please enter a name for the playlist**")
        return
    playlist = { "name": name, "tracks": mh.tracks }
    with open(os.getenv("playlist_path")+str(datetime.timestamp(datetime.now()))+'.json', 'w') as outfile:
        json.dump(playlist, outfile)
        await ctx.send("**Playlist saved!:** \n"+name+" with "+str(len(mh.tracks))+" tracks")

@bot.command(name='playlists', help="Show saved playlists.")
async def playlists(ctx):
    json_files = [pos_json for pos_json in os.listdir(os.getenv("playlist_path")) if pos_json.endswith('.json')]
    if len(json_files) == 0:
        await ctx.send("**You haven't saved any playlists yet:**\nSave the current queue with: !save \{name\}")
    else:
        playlists = ""
        for index, js in enumerate(json_files):
            with open(os.path.join(os.getenv("playlist_path"), js)) as json_file:
                playlist = json.load(json_file)
                playlists += "**"+str(index+1)+"**: "+playlist['name']+"\n"
        await ctx.send("**Saved Playlists:** \n"+playlists)

@bot.command(name='load', help="Load a playlist, use !playlist to get playlist number. Example: !load 1")
async def load(ctx, playlist_index):
    playlist_index = int(playlist_index)-1
    json_files = [pos_json for pos_json in os.listdir(os.getenv("playlist_path")) if pos_json.endswith('.json')]
    if len(json_files) == 0:
        await ctx.send("**No playlists saved yet!**")
    with open(os.getenv("playlist_path")+json_files[playlist_index]) as json_file:
        playlist = json.load(json_file)
        user = ctx.author
        if user.voice is None or user.voice.channel is None: return
        voice_channel = user.voice.channel
        if ctx.voice_client is None:
            vc = await voice_channel.connect()
        else:
            await ctx.voice_client.move_to(voice_channel)
            vc = ctx.voice_client
        if vc.is_playing(): vc.stop()
        mh.tracks = []
        mh.currentTrackIndex = 0
        for track in playlist['tracks']:
            await mh.addTrack(track['title'])
        playTrack(ctx, vc, mh.currentTrackIndex)
        await ctx.send("**Loaded playlist:** \n"+playlist['name']+" with "+str(len(playlist['tracks']))+" tracks")

@bot.command(name='queue', help="Display queue.")
async def queue(ctx):
    trackList = ""
    for idx,track in enumerate(mh.queue):
        trackList += "**"+str(idx+1)+": "+("[Playing]** " if mh.currentTrackIndex == idx else "**")+track['title']+"\n"
    msg = await ctx.send("**Media Queue:** \n"+trackList)
    reactions = ["⬅️", "➡️"]
    for react in reactions:
        await msg.add_reaction(emoji=react)
 
@bot.command(name='insult', help="The bot is an asshole.")
async def insult(ctx):
    from datastore import insult_adj as adj
    from datastore import insult_noun as noun
    i = "You " + random.choice (adj) + " " + random.choice (noun)
    await ctx.send(i)

@bot.command(name='roll', help="Roll")
async def roll(ctx, roll = 1000):
    from datastore import roll_responses as rr
    await ctx.send(str(ctx.author) + " rolled " + str(random.randint(0, roll)) + str(rr.get(roll, "")))

@commands.command(pass_context=True)
async def emoji(ctx):
    msg = await bot.say("working")
    reactions = ['dart']
    for emoji in reactions: 
        await bot.add_reaction(msg, emoji)

@bot.event
async def on_message(message):
    print("on_message event")
    print(message.content)
    if message.content.startswith(prefix+'test'):
        params = message.content.split(" ")
        if params[1] is not None and ".youtube." not in params[1]:
            e = discord.Embed(title=params[1], url="https://youtube.com", description="Manual youtube embed from searched text", color=discord.Color.green())
            e.set_thumbnail(url="https://cdn.mos.cms.futurecdn.net/8gzcr6RpGStvZFA2qRt4v6.jpg")
            ctx = await bot.get_context(message, cls=commands.Context)
            await ctx.send(embed=e)
    await bot.process_commands(message)

@bot.event
async def on_reaction_add(reaction, user):
    print("on_reaction_add event")
    print(user.bot)
    if not user.bot:
        ctx = await bot.get_context(reaction.message, cls=commands.Context)
        if str(reaction.emoji) == "➡️": ctx.command = bot.get_command('skip')
        if str(reaction.emoji) == "⬅️": ctx.command = bot.get_command('back')
        await bot.invoke(ctx)

@bot.event
async def on_reaction_remove(reaction, user):
    print("on_reaction_remove event")
    print(user.bot)
    if not user.bot:
        ctx = await bot.get_context(reaction.message, cls=commands.Context)
        if str(reaction.emoji) == "➡️": ctx.command = bot.get_command('skip')
        if str(reaction.emoji) == "⬅️": ctx.command = bot.get_command('back')
        await bot.invoke(ctx)

@bot.event
async def on_ready():
    print("Bot is ready!")
 
if __name__ == "__main__" :
    bot.run(DISCORD_TOKEN)
