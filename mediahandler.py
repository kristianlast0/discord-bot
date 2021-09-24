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
        self.emptylist = {"name": "undefined", "type": "queue", "pos": 0, "version":os.getenv("version")}
        self.bitRate = bitRate
        self.tracks = [{"name": "undefined", "type": "queue", "pos": 1}]
        self.trackPath = os.getenv("track_path")

    def getInfo(self, search, noplaylist=True): # get all relevant search information in dict form.
        if not search.startswith("https://www.youtube.") and not search.startswith("https://open.spotify.com") and not search.startswith("https://soundcloud.com/"):
            print("BS search for: "+search)
            query_string = urllib.parse.urlencode({"search_query": search})
            formatUrl = urllib.request.urlopen("https://www.youtube.com/results?" + query_string)
            search_results = re.findall(r"watch\?v=(\S{11})", formatUrl.read().decode())
            search = "https://www.youtube.com/watch?v=" + "{}".format(search_results[0])
        with youtube_dl.YoutubeDL({'format': 'bestaudio/best','noplaylist':noplaylist}) as ydl:
            info = ydl.extract_info(search, download=False)
            if search.startswith("https://www.youtube.") :
                i = {"title":info["title"],"link":search, "duration":info["duration"], "is_live":info["is_live"], "file": None}
            else:
                i = {"title":info["title"],"link":search, "duration":info["duration"], "is_live": "None", "file": None}
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
        return(self.trackPath+re.sub('[^A-Za-z0-9-]+', '', name).lower()+"."+os.getenv("codec"))

    def isLastTrack(self, i):
        if len(self.tracks) - 1 == i or i == 1 and len(self.tracks) == 1:
            return(True)
        else:
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
        self.tracks = [self.emptylist]

    def queue(self):
        if len(self.tracks) == 1: return []
        return self.tracks