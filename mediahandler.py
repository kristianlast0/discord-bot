from __future__ import unicode_literals
import youtube_dl
import os
import re, requests, urllib.parse, urllib.request
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from datetime import datetime
import asyncio
 
load_dotenv()
 
class MediaHandler:
    
    def __init__(self, bitRate):
        self.bitRate = bitRate
        self.download = os.getenv("download")
        self.tracks = [{"name": "undefined", "type": "queue", "pos": 0}]
        self.trackPath = os.getenv("track_path")

    def getInfo(self, ctx, search, noplaylist=True): # get all relevant search information in dict form.
        #try:
        if not search.startswith("https://youtu"):
            query_string = urllib.parse.urlencode({"search_query": search})
            formatUrl = urllib.request.urlopen("https://www.youtube.com/results?" + query_string)
            search_results = re.findall(r"watch\?v=(\S{11})", formatUrl.read().decode())
            search = "https://www.youtube.com/watch?v=" + "{}".format(search_results[0])
        with youtube_dl.YoutubeDL({'format': 'bestaudio/best','noplaylist':noplaylist}) as ydl:
            info = ydl.extract_info(search, download=False)
            #for i in info:
            #    print(info["id"]+" "+info["title"])
            i = {"title":info["title"],"link":search, "duration":info["duration"], "is_live":info["is_live"], "file": None}
            p = self.nameToPath(i["title"])
            i["file"] = p
            return([i])

    def getTrackIndex(self):
        if self.tracks[0]["pos"] == 0:
            self.tracks[0]["pos"] = 1
        return self.tracks[0]["pos"]

    def setTrackIndex(self, i):
        if i > 0 and i < len(self.tracks):
            self.tracks[0]["pos"] = i
        return i

    def incTrackIndex(self):
        if len(self.tracks) - 1 > self.getTrackIndex():
            self.tracks[0]["pos"] += 1
        return(self.getTrackIndex())

    def decTrackIndex(self):
        if self.getTrackIndex() > 1:
            self.tracks[0]["pos"] -= 1
        return(self.getTrackIndex())

    def getDURL(self, link):
        with youtube_dl.YoutubeDL({'format': 'bestaudio/best','noplaylist':True}) as ydl:
            return(ydl.extract_info(link, download=False)["url"])

    def getSource(self):
        p = self.getFile()
        if self.fileExists(p): 
            print("File based audio!")
            #os.utime(p, (access_time, modification_time))
            return(p, False)
        else:
            print("Streaming Audio!")
            return(self.getDURL(self.tracks[self.getTrackIndex()]["link"]), True)

    def getName(self):
        return(self.tracks[self.getTrackIndex()]["title"])

    def getLink(self):
        return(self.tracks[self.getTrackIndex()]["link"])

    def getDuration(self):
        return(self.tracks[self.getTrackIndex()]["duration"])

    def getFile(self):
        return(self.tracks[self.getTrackIndex()]["file"])

    def fileExists(self, filename):
        return(os.path.isfile(filename))

    def nameToPath(self, name):
        return(self.trackPath+re.sub('[^A-Za-z0-9-]+', '', name).lower()+".wav")

    def isLastTrack(self, i):
        if len(self.tracks) - 1 == i:
            print("Is finished.")
            return(True)
        else:
            print("Is not finished.")
            return(False)
    
    def addTrack(self, i):
        track = {
                "title": i["title"],
                "thumbnail": '',
                "added_at": datetime.timestamp(datetime.now()),
                "started_at": None,
                "completed_at": None,
                "duration": i["duration"],
                "link": i["link"],
                "is_live": i["is_live"],
                "file": i["file"]
            }
        self.tracks.append(track)
        return track

    def flush(self):
        self.tracks = [{"name":"undefined","type": "queue", "pos": 0}]

    def queue(self):
        if len(self.tracks) == 1: return []
        return self.tracks

################################################################################################################################

    async def addTrackOld(self, search):
 
        query_string = urllib.parse.urlencode({"search_query": search})
        formatUrl = urllib.request.urlopen("https://www.youtube.com/results?" + query_string)
        search_results = re.findall(r"watch\?v=(\S{11})", formatUrl.read().decode())
        clip = requests.get("https://www.youtube.com/watch?v=" + "{}".format(search_results[0]))
        clip2 = "https://www.youtube.com/watch?v=" + "{}".format(search_results[0])
 
        inspect = BeautifulSoup(clip.content, "html.parser")
        yt_title = inspect.find_all("meta", property="og:title")
        yt_thumbnail = inspect.find_all("meta", property="og:thumnail")
        # meta = inspect.find_all("contentDetails")
        # print(meta)
 
        for metadata in yt_title: pass
        filename = self.trackPath+re.sub('[^A-Za-z0-9-]+', '', metadata['content']).lower()+".wav"
 
        if os.path.isfile(filename):
            # print("--- Found existing file, added to queue")
            # print('')
            track = {
                "filepath": filename,
                "title": metadata['content'],
                "thumbnail": '',
                "added_at": datetime.timestamp(datetime.now()),
                "started_at": None,
                "completed_at": None,
            }
            self.tracks.append(track)
            return track
 
        print("--- Downloading track...")
        ydl_opts = {
            'outtmpl': filename,
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'wav',
                'preferredquality': self.bitRate
            }],
            'postprocessor_args': [
                '-ar', '16000'
            ],
            'rate-limit': '20k',
            'prefer_ffmpeg': True,
            'keepvideo': False,
            'quiet': False
        }
 
        with youtube_dl.YoutubeDL(ydl_opts) as ydl:
            ydl.download([clip2])
            info_dict = ydl.extract_info(clip2, download=False)
            # video_url = info_dict.get("url", None)
            # video_id = info_dict.get("id", None)
            # video_title = info_dict.get('title', None)
            # print(video_title)
            # print(video_id)
            # print(video_url)
 
            track = {
                "filepath": filename,
                "title": metadata['content'],
                "thumbnail": '',
                "added_at": datetime.timestamp(datetime.now()),
                "started_at": None,
                "completed_at": None,
                # "duration": metadata['duration']
            }
 
            print('--- Download completed')
            print('')
            self.tracks.append(track)
            return track
 
    @property
    def currentTrack(self):
        if(len(self.tracks) > 0):
            return self.tracks[self.currentTrackIndex]
 
    def next(self):
        if(self.currentTrackIndex < (len(self.tracks)-1) and len(self.tracks) > 0):
            self.currentTrackIndex = self.currentTrackIndex+1
            print("currentIndex: "+str(self.currentTrackIndex))
 
    def prev(self):
        if(self.currentTrackIndex > 0 and len(self.tracks) > 0):
            for track in self.tracks[self.currentTrackIndex:]:
                track['completed_at'] = None
            self.currentTrackIndex = self.currentTrackIndex-1
            print("currentIndex: "+str(self.currentTrackIndex))

    # def flush(self):
    #     self.tracks = []
    #     self.currentTrackIndex = 0
 
    def loadPlaylist(self, index):
        print(index)

    def startTrack(self, index):
        self.currentTrackIndex = index
        self.tracks[self.currentTrackIndex]['started_at'] = datetime.timestamp(datetime.now())
        self.tracks[self.currentTrackIndex]['completed_at'] = None
    
    def endTrack(self, index):
        self.tracks[index]['completed_at'] = datetime.timestamp(datetime.now())
 
    # @property
    # def queue(self):
    #     if len(self.tracks) == 0: return []
    #     return self.tracks
 
    @property
    def hasNextTrack(self):
        return self.currentTrackIndex < (len(self.tracks)-1)
 
    @property
    def hasPrevTrack(self):
        return self.currentTrackIndex > 0 and len(self.tracks) > 0
