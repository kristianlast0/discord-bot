from multiprocessing import Process, Queue
#import youtube_dl
from dotenv import load_dotenv
import os, time, youtube_dl
load_dotenv()

class Downloader:

    def __init__(self):
        self.queue = Queue()

    def download(self, link, opts):
        with youtube_dl.YoutubeDL(opts) as ydl:
            start_time = time.time()
            ydl.download([link])
            print("Finished downloading:"+str(opts["outtmpl"])+". Rate:"+str(+opts["ratelimit"])+"KB/s. Time to download:"+str(time.time() - start_time)+" secs")
        return

    def add(self, t):
        from datastore import ydl_opts as opts
        for i in t:
            print(i["prio"])
            opts["outtmpl"] = i["file"]
            opts['postprocessors'][0]["preferredcodec"] = os.getenv("codec")
            opts["ratelimit"] = i["prio"]
            if os.getenv("play_partial_files") == "True":
                opts["nopart"] = True
            Process(target = self.download, args=(i["link"], opts)).start()
        