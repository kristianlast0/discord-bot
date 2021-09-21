from logging import currentframe
import discord
from discord.ext import commands,tasks
import os
from dotenv import load_dotenv
import random
from mediahandler import MediaHandler
from voiceconnection import VoiceConnection
from downloader import Downloader
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
dl = Downloader()
guilds = {}

def getguild(ctx): # get guild relevant objects in dict form.
    try:
        id = ctx.message.guild.id
        if id in guilds.keys():
            print("Guild found!")
            return(guilds.get(id))
        else:
            d = {
                "guild": ctx.message.guild,
                "voice_connection": VoiceConnection("128")
            }
            guilds[id] = d
            print("Guild added.")
            #print(guilds)
            return d
    except:
        print("Cannot get guild from this context.")
        return None

async def auth(ctx):
    g = getguild(ctx)
    v = g["voice_connection"]
    if ctx.author.voice and not v.is_connected():
        await v.connect(ctx)
    return([g, v, v.client])
    
async def reactions(msg, t = "full"):
    if t == "hiddenmenu":
        r = ["🔄", "⏏️", "❌"]
    elif t == "full":
        r = ["⏯️", "⏹️", "⏮️", "⏭️", "📜", "💀"]
    for react in r:
        await msg.add_reaction(emoji=react)    

@bot.command(name='play', help="▶️:Add and play tracks.")
async def play(ctx, *search):
    g, v, c = await auth(ctx)
    if not c:
        await ctx.send(f"Thats not possible.")
        return
    end = v.mh.isFinished()
    if search != ():
        i = v.mh.getInfo(ctx, (" ").join(search))
        d = []
        for t in i:
            if t:
                if not t["is_live"] and not os.path.isfile(t["file"]):
                    d.append({"link":t["link"], "file":t["file"]})
                v.mh.addTrack(t)
                if c.isplaying():
                    msg = await ctx.send("Queued: "+t["title"])
                    await msg.add_reaction(emoji="📜")
            else:
                await ctx.send(f"Failed to find result for \"" + search + "\"")
                return
        if len(d) > 0:
            dl.add(d)
        if not c.is_playing() and not c.is_paused() and v.stopped:
            if end:
                v.mh.incTrackIndex()
            await v.playQueue(ctx)
    else:
        if v.stopped or v.c.is_paused():
            await v.playPause(ctx)
    return

@bot.command(name='playpause', help="⏯️:Play/Resume and Pause")
async def playpause(ctx):
    g, v, c = await auth(ctx)
    if not c:
        await ctx.send(f"Thats not possible.")
        return
    await v.playPause(ctx)
    return

@bot.command(name='stop' ,help="⏹️:Stop playing current track.")
async def stop(ctx, arg):
    g, v, c = await auth(ctx)
    if not c:
        await ctx.send(f"Thats not possible.")
        return
    if c.is_playing() or c.is_paused():
        async with ctx.typing():
            await v.stop()
        msg = await ctx.send("**[Stopped]**")
    else:
        msg = await ctx.send("The bot is not playing anything at the moment.")
    await reactions(msg)
    return

@bot.command(name='skip', help="⏭️:Skip to next track in queue.")
async def skip(ctx):
    g, v, c = await auth(ctx)
    if not c:
        await ctx.send(f"Thats not possible.")
        return
    async with ctx.typing():
        await v.stop()
    v.mh.incTrackIndex()
    await v.playQueue(ctx)
    return

@bot.command(name='back', help="⏮️:Skip backwards through playlist.")
async def back(ctx):
    g, v, c = await auth(ctx)
    if not c:
        await ctx.send(f"Thats not possible.")
        return
    async with ctx.typing():
        await v.stop()
    v.mh.decTrackIndex()
    await v.playQueue(ctx)
    return

@bot.command(name='restart', help="🔄:Skip backwards through playlist.")
async def restart(ctx):
    g, v, c = await auth(ctx)
    if not c:
        await ctx.send(f"Thats not possible.")
        return
    async with ctx.typing():
        await v.stop()
    v.mh.setTrackIndex(1)
    await v.playQueue(ctx)
    return

@bot.command(name='queue', help="📜:Display Queue.")
async def queue(ctx):
    g, v, c = await auth(ctx)
    trackList = ""
    for idx,track in enumerate(v.mh.queue()):
        if idx > 0:
            trackList += "**"+str(idx)+": "+("[Playing]** " if v.mh.getTrackIndex() == idx else "**")+track['title']+"\n"
    msg = await ctx.send("**Media Queue:** " + v.mh.tracks[0]["name"] + "\n"+trackList)
    await reactions(msg)
    print(v.mh.tracks)
    return

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
                playlists += "**"+str(index+1)+"**: "+playlist[0]['name']+"\n"
        await ctx.send("**Saved Playlists:** \n"+playlists)

@bot.command(name='changename', help="Rename current playlist")
async def changename(ctx, *name):
    g, v, c = await auth(ctx)
    name = (" ").join(name)
    v.mh.tracks[0]["name"] = name
    return

@bot.command(name='save', help="Save current playlist")
async def save(ctx, *name):
    g, v, c = await auth(ctx)
    name = (" ").join(name)
    if name == "" and v.mh.tracks[0]["name"] == "undefined":
        await ctx.send("**Please enter a name for the playlist**")
        return
    v.mh.tracks[0]["type"] = "playlist"
    if v.mh.tracks[0]["name"] == "undefined":
        v.mh.tracks[0]["name"] = name 
    with open(os.getenv("playlist_path")+str(datetime.timestamp(datetime.now()))+'.json', 'w') as outfile:
        json.dump(v.mh.tracks, outfile)
        await ctx.send("**Playlist saved!:** \n"+name+" with "+str(len(v.mh.tracks) - 1)+" tracks")
    return

@bot.command(name='load', help="Load a playlist. Example: !load 1")
async def load(ctx, playlist_index):
    g, v, c = await auth(ctx)
    if not c:
        await ctx.send(f"Thats not possible.")
        return
    playlist_index = int(playlist_index)-1
    json_files = [pos_json for pos_json in os.listdir(os.getenv("playlist_path")) if pos_json.endswith('.json')]
    if len(json_files) == 0:
        await ctx.send("**No playlists saved yet!**")
    with open(os.getenv("playlist_path")+json_files[playlist_index]) as json_file:
        playlist = json.load(json_file)
        user = ctx.author
        if c.is_playing(): 
            async with ctx.typing():
                await v.stop()
        v.mh.tracks = playlist
        v.mh.setTrackIndex(1)
        await v.playQueue(ctx)
        await ctx.send("**Loaded playlist:** \n"+playlist['name']+" with "+str(len(playlist['tracks']))+" tracks")
    return

@bot.command(name='flush', help='⏏️:Flush queue of all songs.')
async def flush(ctx):
    g, v, c = await auth(ctx)
    if not c:
        await ctx.send(f"Thats not possible.")
        return
    async with ctx.typing():
        await v.stop()
    v.mh.flush()
    await ctx.send("**Flushing Queue**")
    return

@bot.command(name='link', help='🔗:Link current track.')
async def link(ctx):
    g, v, c = await auth(ctx)
    if not c:
        await ctx.send(f"Thats not possible.")
        return
    await ctx.send(str(v.mh.getLink()))
    return

@bot.command(name='join', category="Media Control", help="Bot will join the channel.")
async def join(ctx):
    g, v, c = await auth(ctx)
    if not c:
        await ctx.send(f"Thats not possible.")
        return
    msg = await ctx.send("Lets Go!")
    await reactions(msg)
    return

@bot.command(name='leave', help="❌:Force bot to leave channel.")
async def leave(ctx):
    g, v, c = await auth(ctx)
    if not c:
        await ctx.send(f"Thats not possible.")
        return
    voice_client = ctx.message.guild.voice_client
    if voice_client.is_connected():
        await v.stop()
        await voice_client.disconnect()
    else:
        await ctx.send("The bot is not connected to a voice channel.")
    return

@bot.command(name='hiddenmenu', help="💀")
async def hiddenmenu(ctx):
    from datastore import roll_responses as rr
    msg = await ctx.send("Hidden Menu")
    await reactions(msg, "hiddenmenu")

@bot.command(name='roll', help="Roll 0-1000, upper limit can be stated.")
async def roll(ctx, roll = 1000):
    from datastore import roll_responses as rr
    msg = await ctx.send(str(ctx.author) + " rolled " + str(random.randint(0, roll)) + str(rr.get(roll, "")))

@commands.command(pass_context=True)
async def emoji(ctx):
    msg = await bot.say("working")
    reactions = ['dart']
    for emoji in reactions:
        await bot.add_reaction(msg, emoji)

@bot.event
async def on_message(message):
    #print("on_message event")
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
    #print("on_reaction_add event")
    #print(user.bot)
    if not user.bot: # ⏯️ ⏹️ ⏮️ ⏭️ 🔄 📜 ⏏️ ⤴️ ⤵️ 🎲 💀 👍 ⬇️
        ctx = await bot.get_context(reaction.message, cls=commands.Context)
        if str(reaction.emoji) == "⏯️": ctx.command = bot.get_command('playpause')
        if str(reaction.emoji) == "⏹️": ctx.command = bot.get_command('stop')
        if str(reaction.emoji) == "⏭️": ctx.command = bot.get_command('skip')
        if str(reaction.emoji) == "⏮️": ctx.command = bot.get_command('back')
        if str(reaction.emoji) == "📜": ctx.command = bot.get_command('queue')
        if str(reaction.emoji) == "❌": ctx.command = bot.get_command('leave')
        if str(reaction.emoji) == "🔄": ctx.command = bot.get_command('restart')
        if str(reaction.emoji) == "⏏️": ctx.command = bot.get_command('flush')
        if str(reaction.emoji) == "🔗": ctx.command = bot.get_command('link')
        if str(reaction.emoji) == "💀": ctx.command = bot.get_command('hiddenmenu')
        await bot.invoke(ctx)

@bot.event
async def on_reaction_remove(reaction, user):
    #print("on_reaction_remove event")
    #print(user.bot)
    if not user.bot:
        ctx = await bot.get_context(reaction.message, cls=commands.Context)
        if str(reaction.emoji) == "⏯️": ctx.command = bot.get_command('playpause')
        if str(reaction.emoji) == "⏹️": ctx.command = bot.get_command('stop')
        if str(reaction.emoji) == "⏭️": ctx.command = bot.get_command('skip')
        if str(reaction.emoji) == "⏮️": ctx.command = bot.get_command('back')
        if str(reaction.emoji) == "📜": ctx.command = bot.get_command('queue')
        if str(reaction.emoji) == "❌": ctx.command = bot.get_command('leave')
        if str(reaction.emoji) == "🔄": ctx.command = bot.get_command('restart')
        if str(reaction.emoji) == "⏏️": ctx.command = bot.get_command('flush')
        if str(reaction.emoji) == "🔗": ctx.command = bot.get_command('link')
        if str(reaction.emoji) == "💀": ctx.command = bot.get_command('hiddenmenu')
        await bot.invoke(ctx)

@bot.event
async def on_ready():
    print("Bot is ready!")

while True:
    try:
        if __name__ == "__main__" :
            bot.run(DISCORD_TOKEN)
        break
    except ValueError:
        print("Shit just went south. Restarting.")

######################################################################################################################################

# @bot.command(name='download', help="⬇️:Preemptively download tracks.")
# async def download(ctx, *search):
#     g, v, c = await auth(ctx)
#     #print(search)
#     if search != ():
#         i = v.mh.getInfo(ctx, (" ").join(search), False)
#         d = []
#         for t in i:
#             if t:
#                 if not t["is_live"] and not os.path.isfile(t["file"]):
#                     d.append({"link":t["link"], "file":t["file"]})
#                 msg = await ctx.send("Downloading: "+str(t["title"]))
#                 await msg.add_reaction(emoji="📜")
#             else:
#                 await ctx.send(f"Failed to find result!")
#                 return
#         if len(d) > 0:
#             dl.add(d)
#     else:
#         await ctx.send(f"Download command requires a link or search term.")        
#     return


# def onPlayerStopped(ctx, vc, lastTrackIndex):
#     guild = getguild(ctx)
#     voice = guild['voice_connection'].getClient(ctx)
#     print("onPlayerStopped")
#     if mh.stopped == False:
#         mh.endTrack(lastTrackIndex)
#         if mh.hasNextTrack == True and lastTrackIndex < (len(mh.tracks)-1):
#             mh.next()
#             playTrack(ctx, vc, mh.currentTrackIndex)

# def playTrack(ctx, vc, trackIndex):
#     guild = getguild(ctx)
#     if not vc.is_playing():
#         guild['media_handler'].startTrack(trackIndex)
#         source = discord.FFmpegPCMAudio(guild['media_handler'].currentTrack['filepath'])
#         vc.play(source, after=lambda e:r())
#         # guild['media_handler'].stopped = False

# def r():
#     print('stopped')
#     return True

# @bot.command(name='play', help="Play a song from youtube. Links can be used as well as names of songs. Not providing an argument will just resume playing from current location in playlist.")
# async def play(ctx, *search):
#     search = (" ").join(search)
#     guild = getguild(ctx)
#     vc = await guild['voice_connection'].getClient(ctx)

#     print(search)
#     print(search)
#     print(search)
 
#     if search != "":
#         print(search)
#         added_track = await guild['media_handler'].addTrack(guild['media_handler'], search)
#         if guild['media_handler'].currentTrack['completed_at'] is not None and guild['media_handler'].hasNextTrack == True:
#             guild['media_handler'].next()
#         msg = await ctx.send(f"Queued: **{added_track['title']}**")
#         reactions = ["⬅️", "➡️"]
#         for react in reactions:
#             await msg.add_reaction(emoji=react)
#         playTrack(ctx, vc, guild['media_handler'].currentTrackIndex)
#     elif search == "":
#         if guild['media_handler'].stopped == True and not vc.is_playing():
#             playTrack(ctx, vc, guild['media_handler'].currentTrackIndex)
#         if guild['media_handler'].stopped == False and vc.is_paused():
#             await vc.resume()

# @bot.command(name="pop", help="Remove a track from queue, use !queue to get track number. Example: !pop 2")
# async def pop(ctx, index):
#     index = int(index) - 1
#     if index >= 0 and index <= len(mh.tracks):
#         if index == mh.currentTrackIndex:
#             print("Pop current track")
#             voice_client = ctx.message.guild.voice_client
#             if voice_client.is_playing():
#                 mh.stopped = True
#                 voice_client.stop()
#             mh.tracks.pop(index)
#             await play(ctx)
#         if index < mh.currentTrackIndex:
#             print("popping previos track")
#             mh.tracks.pop(index)
#             mh.currentTrackIndex -= 1
#         if index > mh.currentTrackIndex:
#             print("Popping future track")
#             mh.tracks.pop(index)
#     else:
#         await ctx.send("Are you fucking with me?")

# @bot.command(name='welcome', help="Trigger the bots welcome message.")
# async def welcome(ctx):
#     from datastore import welcome_messages as wm
#     vc = ctx.message.guild.voice_client
#     x = random.choice(list(wm.items()))
#     print(x[0], ">", x[1])

#     path = os.getenv("tts_path") + x[0] + ".mp3"
#     if os.path.exists(path) and os.path.isfile(path):
#         print("TTS file exists, playing from disc.")
#         vc.play(discord.FFmpegPCMAudio(path))
#     else:
#         print("TTS does not yet exist, creating!")
#         engine = pyttsx3.init()
#         engine.save_to_file(x[1], path)
#         engine.runAndWait()
#         vc.play(discord.FFmpegPCMAudio(path))
 
# @bot.command(name='insult', help="The bot is an asshole.")
# async def insult(ctx):
#     from datastore import insult_adj as adj
#     from datastore import insult_noun as noun
#     i = "You " + random.choice (adj) + " " + random.choice (noun)
#     await ctx.send(i)