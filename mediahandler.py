from __future__ import unicode_literals
import youtube_dl
import os
import re, requests, urllib.parse, urllib.request
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from datetime import datetime
 
load_dotenv()
 
class MediaHandler():
 
    tracks = []
    currentTrackIndex = 0
    bitRate = "128"
    trackPath = ""
    stopped = False
 
    def __init__(self):
        self.tracks = []
        self.currentTrackIndex = 0
        self.trackPath = os.getenv("track_path")
 
    async def addTrack(self, search):
 
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
            self.currentTrackIndex = self.currentTrackIndex-1
            print("currentIndex: "+str(self.currentTrackIndex))

    def pop(self, index):
        i = int(index)
        if len(self.tracks) >= i and i >= 0:
            x = self.tracks[i - 1]
            self.tracks.pop(i - 1)
            if i < self.currentTrackIndex:
                self.currentTrackIndex -= 1
            return(x)
        else:
            return(0)

    def flush(self):
        self.tracks = []
        self.currentTrackIndex = 0
 
    def loadPlaylist(self, index):
        print(index)

    def startTrack(self, index):
        self.currentTrackIndex = index
        self.tracks[self.currentTrackIndex]['started_at'] = datetime.timestamp(datetime.now())
        self.tracks[self.currentTrackIndex]['completed_at'] = None
    
    def endTrack(self, index):
        self.tracks[index]['completed_at'] = datetime.timestamp(datetime.now())
 
    @property
    def queue(self):
        if len(self.tracks) == 0: return []
        return self.tracks
 
    @property
    def hasNextTrack(self):
        return self.currentTrackIndex < (len(self.tracks)-1)
 
    @property
    def hasPrevTrack(self):
        return self.currentTrackIndex > 0 and len(self.tracks) > 0
