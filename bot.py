from logging import currentframe
import discord
from discord.ext import commands,tasks
import os
from dotenv import load_dotenv
import random
import pyttsx3
import os, json
import pandas as pd
import json
from datetime import datetime
import youtube_dl
import re, requests, urllib.parse, urllib.request
from asyncio import sleep

from mediahandler import MediaHandler
from voiceconnection import VoiceConnection
from downloader import Downloader

load_dotenv()

intents = discord.Intents.default()
intents.members = True
intents.reactions = True
intents.messages = True
intents.voice_states = True
 
# Get the API token from the .env file.
DISCORD_TOKEN = os.getenv("discord_token") if os.getenv("env") == "prod" else os.getenv("develop_token")

# client = discord.Client(intents=intents)
if os.getenv("env") == "prod":
    prefix = "!"
    BOT_ID = os.getenv("production_id")
else:
    prefix = os.getenv("command_prefix")
    BOT_ID = os.getenv("develop_id")

bot = commands.Bot(command_prefix=prefix)
dl = Downloader()
guilds = {}

def getguild(ctx): # get guild relevant objects in dict form.
    try:
        id = ctx.message.guild.id
        p = os.getenv("playlist_path")+str(id)
        #print(ctx.message.guild)
        if not os.path.isdir(p):
            os.mkdir(p)
        if id in guilds.keys():
            #print(guilds.get(id))
            return(guilds.get(id))
        else:
            d = {
                "guild": ctx.message.guild,
                "voice_connection": VoiceConnection("128")
            }
            guilds[id] = d
            #print(guilds.get(id))
            return d
    except:
        print("Cannot get guild from this context.")
        return None

async def auth(ctx, req = False):
    g = getguild(ctx)
    v = g["voice_connection"]
    u = ctx.author.voice
    r = False

    if ctx.author.voice and ctx.voice_client:
        if ctx.author.voice.channel.id == ctx.voice_client.channel.id:
            r = True
        elif len(ctx.voice_client.channel.members) == 1:
            await v.client.move_to(ctx.author.voice.channel)
            r = True
        else:
            r = False
            if req: await ctx.message.reply("Come to \""+ctx.voice_client.channel.name+"\" if you want to boogie!")
    elif ctx.author.voice and not ctx.voice_client:
        await v.connect(ctx)
        r = True
    else:
        r = False
        if req: await ctx.message.reply("You need to be connected to a voice channel to carry out this command.")
    return([g, v, r])

async def reactions(msg, t = "full"):
    if t == "hiddenmenu":
        r = ["🔄", "⏏️", "❌"]
    elif t == "full":
        r = ["⏯️", "⏹️", "⏮️", "⏭️", "📜", "🗃️", "💀", "⁉️"]
    for react in r:
        await msg.add_reaction(emoji=react)    

@bot.command(name='test', help="⏯️:Play/Resume and Pause")
async def test(ctx):
    g, v, r = await auth(ctx)
    await v.disconnect()
    if r:
        print("Done ",g, v, r)
    else:
        print("Permission denied")
    return

@bot.command(name='play', help="▶️:Add and play tracks.")
async def play(ctx, *search):
    g, v, r = await auth(ctx, True)
    if not r: return
    
    end = v.mh.isLastTrack(v.mh.getTrackIndex())
    if search != ():
        info = v.mh.getInfo((" ").join(search))
        downloads = []
        for t in info:
            if t:
                if not t["is_live"] and not os.path.isfile(t["file"]):
                    d = {"link":t["link"], "file":t["file"], "prio": int(os.getenv("dlspeed_0"))}
                    if v.mh.isLastTrack(v.mh.getTrackIndex()) and v.stopped and t["duration"] < 300:
                        #print("Priority Downlaod: "+t["title"])
                        d["prio"] = int(os.getenv("dlspeed_2"))
                    elif t["duration"] > 1500:
                        d["prio"] = int(os.getenv("dlspeed_0"))
                    elif len(v.mh.tracks) - v.mh.getTrackIndex() > 1:
                        d["prio"] = int(os.getenv("dlspeed_0"))
                    else:
                        d["prio"] = int(os.getenv("dlspeed_1"))
                    downloads.append(d)
                    #print(len(v.mh.tracks) - v.mh.getTrackIndex())
                v.mh.addTrack(t)
                if v.client.is_playing():
                    msg = await ctx.send("Queued: "+t["title"])
                    await msg.add_reaction(emoji="📜")
            else:
                await ctx.send(f"Failed to find result for \"" + search + "\"")
                return
        if len(downloads) > 0:
            dl.add(downloads)
        if not v.client.is_playing() and not v.client.is_paused() and v.stopped:
            if end:
                v.mh.incTrackIndex()
            async with ctx.typing():
                await sleep(5)
            await v.playQueue(ctx)
    elif v.stopped or v.client.is_paused():
            await v.playPause(ctx)
    else:
        pass

@bot.command(name='playpause', help="⏯️:Play/Resume and Pause")
async def playpause(ctx):
    g, v, r = await auth(ctx, True)
    if not r: return
    await v.playPause(ctx)
    return

@bot.command(name='stop' ,help="⏹️:Stop playing current track.")
async def stop(ctx, arg):
    g, v, r = await auth(ctx, True)
    if not r: return
    if v.client.is_playing() or v.client.is_paused():
        async with ctx.typing():
            await v.stop()
        msg = await ctx.send("**[Stopped]**")
    else:
        msg = await ctx.send("The bot is not playing anything at the moment.")
    await reactions(msg)
    return

@bot.command(name='skip', help="⏭️:Skip to next track in queue.")
async def skip(ctx):
    g, v, r = await auth(ctx, True)
    if not r: return
    async with ctx.typing():
        await v.stop()
    v.mh.incTrackIndex()
    await v.playQueue(ctx)
    return

@bot.command(name='back', help="⏮️:Skip backwards through playlist.")
async def back(ctx):
    g, v, r = await auth(ctx, True)
    if not r: return
    async with ctx.typing():
        await v.stop()
    v.mh.decTrackIndex()
    await v.playQueue(ctx)
    return

@bot.command(name='restart', help="🔄:Skip backwards through playlist.")
async def restart(ctx):
    g, v, r = await auth(ctx, True)
    if not r: return
    async with ctx.typing():
        await v.stop()
    v.mh.setTrackIndex(1)
    await v.playQueue(ctx)
    return

@bot.command(name='queue', help="📜:Display Queue.")
async def queue(ctx):
    g, v, r = await auth(ctx)
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
    g, v, r = await auth(ctx)
    json_files = [pos_json for pos_json in os.listdir(os.getenv("playlist_path")+str(g["guild"].id)+"/") if pos_json.endswith('.json')]
    if len(json_files) == 0:
        await ctx.send("**You haven't saved any playlists yet:**\nSave the current queue with: !save \{name\}")
    else:
        playlists = ""
        for index, js in enumerate(json_files):
            with open(os.path.join(os.getenv("playlist_path")+str(g["guild"].id)+"/", js)) as json_file:
                playlist = json.load(json_file)
                playlists += "**"+str(index+1)+"**: "+playlist[0]['name']+"\n"
        await ctx.send("**Saved Playlists:** \n"+playlists)

@bot.command(name='save', help="Save current playlist")
async def save(ctx, *name):
    g, v, r = await auth(ctx)
    name = (" ").join(name)
    if name == "" and v.mh.tracks[0]["name"] == "undefined":
        await ctx.send("**Please enter a name for the playlist**")
        return
    v.mh.tracks[0]["type"] = "playlist"
    if v.mh.tracks[0]["name"] == "undefined":
        v.mh.tracks[0]["name"] = name 
    with open(os.getenv("playlist_path")+str(g["guild"].id)+"/"+str(datetime.timestamp(datetime.now()))+'.json', 'w') as outfile:
        json.dump(v.mh.tracks, outfile)
        await ctx.send("**Playlist saved!:** \n"+name+" with "+str(len(v.mh.tracks) - 1)+" tracks")
    return

@bot.command(name='load', help="Load a playlist. Example: !load 1")
async def load(ctx, playlist_index):
    g, v, r = await auth(ctx, True)
    if not r: return
    playlist_index = int(playlist_index)-1
    json_files = [pos_json for pos_json in os.listdir(os.getenv("playlist_path")+str(g["guild"].id)+"/") if pos_json.endswith('.json')]
    if len(json_files) == 0:
        await ctx.send("**No playlists saved yet!**")
    with open(os.getenv("playlist_path")+str(g["guild"].id)+"/"+json_files[playlist_index]) as json_file:
        playlist = json.load(json_file)
        user = ctx.author
        if v.client.is_playing(): 
            async with ctx.typing():
                await v.stop()
        v.mh.tracks = playlist
        v.mh.setTrackIndex(1)
        await ctx.send("**Loaded playlist:** \n"+playlist[0]['name']+" with "+str(len(playlist) - 1)+" tracks")
        await v.playQueue(ctx)
    return

@bot.command(name='changename', help="Rename current playlist")
async def changename(ctx, *name):
    g, v, r = await auth(ctx)
    name = (" ").join(name)
    v.mh.tracks[0]["name"] = name
    return

@bot.command(name="remove", help="Remove a track from queue, use !queue to get track number. Example: !pop 2")
async def remove(ctx, index):
    g, v, r = await auth(ctx, True)
    if not r: return
    index = int(index)
    if index >= 0 and index <= len(v.mh.tracks) - 1:
        msg = await ctx.send("**[Removed:] **"+v.mh.tracks[index]["title"])
        await msg.add_reaction(emoji="📜")
        if index == v.mh.getTrackIndex():
            if not v.stopped:
                await v.stop()
            if v.mh.isLastTrack(index):
                v.mh.setTrackIndex(v.mh.getTrackIndex() - 1)
                v.mh.tracks.pop(index)
                return
            v.mh.tracks.pop(index)
            await v.playQueue(ctx)
        elif index < v.mh.getTrackIndex():
            v.mh.tracks.pop(index)
            v.mh.setTrackIndex(v.mh.getTrackIndex() - 1)
        else:
            v.mh.tracks.pop(index)
    else:
        await ctx.send("Are you fucking with me?")

@bot.command(name='flush', help='⏏️:Flush queue of all songs.')
async def flush(ctx):
    g, v, r = await auth(ctx, True)
    if not r: return
    async with ctx.typing():
        await v.stop()
    v.mh.flush()
    await ctx.send("**Flushing Queue**")
    return

@bot.command(name='link', help='🔗:Link current track.')
async def link(ctx):
    g, v, r = await auth(ctx)
    await ctx.send(str(v.mh.getLink()))
    return

@bot.command(name='join', category="Media Control", help="Bot will join the channel.")
async def join(ctx):
    g, v, r = await auth(ctx, True)
    if not r: return
    msg = await ctx.send("Lets Go!")
    await reactions(msg)
    return

@bot.command(name='leave', help="❌:Force bot to leave channel.")
async def leave(ctx):
    g, v, r = await auth(ctx, True)
    if not r: return
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
async def on_voice_state_change(member, before, after):
    print(member, before, after)
    return

@bot.event
async def on_message(message):
    if message.content.startswith(prefix):
        print(message.content)
    if not message.author.bot:
        if message.content.startswith("https://www.youtube.") or message.content.startswith("https://soundcloud.com/"):
            await message.add_reaction(emoji="▶️")
    # if message.content.startswith(prefix+'test'):
    #     params = message.content.split(" ")
    #     if params[1] is not None and ".youtube." not in params[1]:
    #         e = discord.Embed(title=params[1], url="https://youtube.com", description="Manual youtube embed from searched text", color=discord.Color.green())
    #         e.set_thumbnail(url="https://cdn.mos.cms.futurecdn.net/8gzcr6RpGStvZFA2qRt4v6.jpg")
    #         ctx = await bot.get_context(message, cls=commands.Context)
    #         await ctx.send(embed=e)
    await bot.process_commands(message)

@bot.event
async def on_reaction_add(reaction, user):
    reactions = ["⏯️", "⏹️", "⏮️", "⏭️", "🔄", "📜", "⏏️", "⤴️", "⤵️", "🎲", "💀", "👍", "💾", "🗃️", "▶️", "⁉️", "🔗"]
    if not user.bot:
        ctx = await bot.get_context(reaction.message, cls=commands.Context)
        if user.voice:
            for m in user.voice.channel.members:
                if str(m.id) == str(BOT_ID):
                    if str(reaction.emoji) == "▶️": ctx.command = bot.get_command('play')
                    if str(reaction.emoji) == "⏯️": ctx.command = bot.get_command('playpause')
                    if str(reaction.emoji) == "⏹️": ctx.command = bot.get_command('stop')
                    if str(reaction.emoji) == "⏭️": ctx.command = bot.get_command('skip')
                    if str(reaction.emoji) == "⏮️": ctx.command = bot.get_command('back')
                    if str(reaction.emoji) == "❌": ctx.command = bot.get_command('leave')
                    #if str(reaction.emoji) == "💾": ctx.command = bot.get_command('save')
                    if str(reaction.emoji) == "🔄": ctx.command = bot.get_command('restart')
                    if str(reaction.emoji) == "⏏️": ctx.command = bot.get_command('flush')
                    if str(reaction.emoji) == "💀": ctx.command = bot.get_command('hiddenmenu')
                    if str(reaction.emoji) == "⁉️": ctx.command = bot.get_command('help')
        if str(reaction.emoji) == "📜": ctx.command = bot.get_command('queue')
        if str(reaction.emoji) == "🗃️": ctx.command = bot.get_command('playlists')
        if str(reaction.emoji) == "🔗": ctx.command = bot.get_command('link')
        if ctx.me and str(reaction.emoji) in reactions:
            await reaction.remove(user)
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
