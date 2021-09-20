from multiprocessing import Process
import youtube_dl as ydl

class Downloader:

    def __init__(self):
        from datastore import ydl_opts as opts
        self.opts = opts
        self.started = []
        self.finished = []
    
    def add(self, t):
        self.started.append(t)

    def download(self, link, dir, opts):
        print("Download Started.")
        opts["outtmpl"] = dir
        ydl.download([link])
        print("Download Finished.")
        return

if __name__ == '__main__':
    dl = Downloader
    p = Process(target=dl.download, args=(dl.started, dl.finished, dl.opts))
    p.start()
    p.join()




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