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

class Bot:

    def __init__(self)
    # client = discord.Client(intents=intents)
    self.prefix = "!" if os.getenv("env") == "prod" else os.getenv("command_prefix")
    self.bot = commands.Bot(command_prefix=self.self.prefix)
    self.dl = Downloader()
    self.guilds = {}

    def getguild(ctx): # get guild relevant objects in dict form.
        try:
            id = ctx.message.guild.id
            p = os.getenv("playlist_path")+str(id)
            #print(ctx.message.guild)
            if not os.path.isdir(p):
                os.mkdir(p)
            if id in self.guilds.keys():
                print("Guild found!")
                return(self.guilds.get(id))
            else:
                d = {
                    "guild": ctx.message.guild,
                    "voice_connection": VoiceConnection("128")
                }
                self.guilds[id] = d
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

    @self.bot.command(name='play', help="▶️:Add and play tracks.")
    async def play(ctx, *search):
        g, v, c = await auth(ctx)
        if not c:
            await ctx.send(f"Thats not possible.")
            return
        end = v.mh.isLastTrack(v.mh.getTrackIndex())
        if search != ():
            i = v.mh.getInfo(ctx, (" ").join(search))
            d = []
            for t in i:
                if t:
                    if not t["is_live"] and not os.path.isfile(t["file"]):
                        d.append({"link":t["link"], "file":t["file"]})
                    v.mh.addTrack(t)
                    if c.is_playing():
                        msg = await ctx.send("Queued: "+t["title"])
                        await msg.add_reaction(emoji="📜")
                else:
                    await ctx.send(f"Failed to find result for \"" + search + "\"")
                    return
            if len(d) > 0:
                self.dl.add(d)
            if not c.is_playing() and not c.is_paused() and v.stopped:
                if end:
                    v.mh.incTrackIndex()
                await v.playQueue(ctx)
        else:
            if v.stopped or v.c.is_paused():
                await v.playPause(ctx)
        return

    @self.bot.command(name='playpause', help="⏯️:Play/Resume and Pause")
    async def playpause(ctx):
        g, v, c = await auth(ctx)
        if not c:
            await ctx.send(f"Thats not possible.")
            return
        await v.playPause(ctx)
        return

    @self.bot.command(name='stop' ,help="⏹️:Stop playing current track.")
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

    @self.bot.command(name='skip', help="⏭️:Skip to next track in queue.")
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

    @self.bot.command(name='back', help="⏮️:Skip backwards through playlist.")
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

    @self.bot.command(name='restart', help="🔄:Skip backwards through playlist.")
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

    @self.bot.command(name='queue', help="📜:Display Queue.")
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

    @self.bot.command(name='playlists', help="Show saved playlists.")
    async def playlists(ctx):
        g, v, c = await auth(ctx)
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

    @self.bot.command(name='save', help="Save current playlist")
    async def save(ctx, *name):
        g, v, c = await auth(ctx)
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

    @self.bot.command(name='load', help="Load a playlist. Example: !load 1")
    async def load(ctx, playlist_index):
        g, v, c = await auth(ctx)
        if not c:
            await ctx.send(f"Thats not possible.")
            return
        playlist_index = int(playlist_index)-1
        json_files = [pos_json for pos_json in os.listdir(os.getenv("playlist_path")+str(g["guild"].id)+"/") if pos_json.endswith('.json')]
        if len(json_files) == 0:
            await ctx.send("**No playlists saved yet!**")
        with open(os.getenv("playlist_path")+str(g["guild"].id)+"/"+json_files[playlist_index]) as json_file:
            playlist = json.load(json_file)
            user = ctx.author
            if c.is_playing(): 
                async with ctx.typing():
                    await v.stop()
            v.mh.tracks = playlist
            v.mh.setTrackIndex(1)
            await ctx.send("**Loaded playlist:** \n"+playlist[0]['name']+" with "+str(len(playlist) - 1)+" tracks")
            await v.playQueue(ctx)
        return

    @self.bot.command(name='changename', help="Rename current playlist")
    async def changename(ctx, *name):
        g, v, c = await auth(ctx)
        name = (" ").join(name)
        v.mh.tracks[0]["name"] = name
        return

    @self.bot.command(name="remove", help="Remove a track from queue, use !queue to get track number. Example: !pop 2")
    async def remove(ctx, index):
        g, v, c = await auth(ctx)
        if not c:
            await ctx.send(f"Thats not possible.")
            return
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

    @self.bot.command(name='flush', help='⏏️:Flush queue of all songs.')
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

    @self.bot.command(name='link', help='🔗:Link current track.')
    async def link(ctx):
        g, v, c = await auth(ctx)
        await ctx.send(str(v.mh.getLink()))
        return

    @self.bot.command(name='join', category="Media Control", help="Bot will join the channel.")
    async def join(ctx):
        g, v, c = await auth(ctx)
        if not c:
            await ctx.send(f"Thats not possible.")
            return
        msg = await ctx.send("Lets Go!")
        await reactions(msg)
        return

    @self.bot.command(name='leave', help="❌:Force bot to leave channel.")
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

    @self.bot.command(name='hiddenmenu', help="💀")
    async def hiddenmenu(ctx):
        from datastore import roll_responses as rr
        msg = await ctx.send("Hidden Menu")
        await reactions(msg, "hiddenmenu")

    @self.bot.command(name='roll', help="Roll 0-1000, upper limit can be stated.")
    async def roll(ctx, roll = 1000):
        from datastore import roll_responses as rr
        msg = await ctx.send(str(ctx.author) + " rolled " + str(random.randint(0, roll)) + str(rr.get(roll, "")))

    @self.commands.command(pass_context=True)
    async def emoji(ctx):
        msg = await bot.say("working")
        reactions = ['dart']
        for emoji in reactions:
            await bot.add_reaction(msg, emoji)

    @self.bot.event
    async def on_message(message):
        #print("on_message event")
        print(message.content)
        if message.content.startswith(self.prefix+'test'):
            params = message.content.split(" ")
            if params[1] is not None and ".youtube." not in params[1]:
                e = discord.Embed(title=params[1], url="https://youtube.com", description="Manual youtube embed from searched text", color=discord.Color.green())
                e.set_thumbnail(url="https://cdn.mos.cms.futurecdn.net/8gzcr6RpGStvZFA2qRt4v6.jpg")
                ctx = await bot.get_context(message, cls=commands.Context)
                await ctx.send(embed=e)
        await bot.process_commands(message)

    @self.bot.event
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

    @self.bot.event
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

    @self.bot.event
    async def on_ready():
        print("Bot is ready!")





bot = Bot

while True:
    try:
        if __name__ == "__main__" :
            bot.run(DISCORD_TOKEN)
            print()
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