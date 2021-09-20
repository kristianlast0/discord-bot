from multiprocessing import Process, Queue
import youtube_dl

class Downloader:

    def __init__(self):
        self.queue = Queue()

    def download(self, link, opts):
        with youtube_dl.YoutubeDL(opts) as ydl:
            print("Download Started.")
            ydl.download([link])
            print("Download Finished.")
        return

    def add(self, t):
        from datastore import ydl_opts as opts
        for i in t:
            opts["outtmpl"] = i["file"]
            Process(target = self.download, args=(i["link"], opts)).start()
        print("End of add reached.")




# async def downloader(self, search, filename):
# print("Download Started.")
# ydl_opts = {
#     'outtmpl': filename,
#     'format': 'bestaudio/best',
#     'postprocessors': [{
#         'key': 'FFmpegExtractAudio',
#         'preferredcodec': 'wav',
#         'preferredquality': self.bitRate
#     }],
#     'postprocessor_args': [
#         '-ar', '16000'
#     ],
#     'ratelimit': int(os.getenv("ratelimit")),
#     'prefer_ffmpeg': True,
#     'keepvideo': False,
#     'quiet': False
# }
# with youtube_dl.YoutubeDL(ydl_opts) as ydl:
#     ydl.download([search])
# print("Download Finished.")
# return