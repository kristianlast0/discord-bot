# Discord music bot

A music bot you run yourself, for your own Discord server. It plays YouTube (links, playlists or search), Spotify links
(looked up on YouTube, because bots can't stream Spotify), music files on the computer it runs on, and internet radio
from around the world. It also crossfades like a DJ, has an Auto-DJ, a 24/7 mode, and a web mixer for live DJing
(including line-in from real CDJs and mixers).

Each copy is completely separate: you make your own Discord bot, run it on your own computer or server, and it only
ever uses your token and your settings.

## Commands

| Command | What it does |
| --- | --- |
| `/play <song or link> [next]` | YouTube/Spotify/SoundCloud link or playlist, or a YouTube search |
| `/local <song or folder>` | Music files in the bot's music folder (autocompletes) |
| `/radio play <station or URL>` · `list` · `add` · `remove` | Internet radio; saved stations (BBC + UK by default) in `data/stations.json` |
| `/radio browse` | Private station browser: worldwide stations by country and style, or search (radio-browser.info) |
| `/radio panel` | Posts a permanent Radio panel (Saved / World / Search buttons) in the music channel |
| `/247 on [station]` · `off` | 24/7 mode (server managers): stay in the voice channel around the clock, rejoin after restarts, and play the station when the queue is empty |
| `/dj crossfade <0-12>` · `/dj autodj <on/off>` · `/dj beatmatch <on/off>` | DJ mode: songs crossfade on two decks (default 6 s), beat-matched when tempos are close; Auto-DJ adds similar songs when the queue runs out |
| `/mixer` | Your personal link to the web mixer (also the 🎛️ button on the booth) |
| `/musicchannel [#channel]` | Restrict music commands and the player message to one channel |
| `/pause` `/resume` `/skip` `/stop` `/leave` | Playback control (you need to be in the bot's voice channel; skipping other people's songs is a vote) |
| `/queue [page]` `/nowplaying` | What's playing and what's next |
| `/volume <0-150>` `/loop <off/track/queue>` `/shuffle` `/remove <n>` `/clear` | Queue tweaks |
| `/playlist save` · `load` · `list` · `show` · `delete` | Saved playlists (only the creator or a server manager can overwrite or delete one) |

The bot leaves after `IDLE_TIMEOUT` seconds (default 5 minutes) of nothing playing or nobody listening, unless 24/7 mode is on.

## Run your own copy

### 1. Make your Discord bot

1. https://discord.com/developers/applications → **New Application** → **Bot** → **Reset Token**, and copy the token.
   Keep it secret: anyone with it controls your bot. No privileged intents are needed.
2. Invite it to your server (replace `APP_ID` with the Application ID from **General Information**):
   `https://discord.com/oauth2/authorize?client_id=APP_ID&scope=bot+applications.commands&permissions=3165184`
   (View Channels, Send Messages, Embed Links, Connect, Speak).
3. Copy `.env.example` to `.env` and fill in `DISCORD_TOKEN`, plus `GUILD_ID` (your server's ID: Discord settings →
   Advanced → Developer Mode, then right-click the server → Copy Server ID). Without `GUILD_ID` the commands work in
   every server the bot is in, but can take up to an hour to appear.

### 2a. Run it with Docker (Windows, Mac or Linux)

Install [Docker](https://docs.docker.com/get-docker/), then in this folder:

```bash
docker compose up -d
```

It restarts by itself, picks up the latest yt-dlp on every restart, and keeps settings and playlists in `./data`.
Put your own music files in `./music` for `/local`. Logs: `docker compose logs -f`. After pulling new code:
`docker compose up -d --build`.

### 2b. Run it on a Linux server without Docker

```bash
sudo apt install ffmpeg       # if it isn't installed already
git clone <this repo> && cd <repo>/music-bot
bash deploy/install.sh        # installs uv, Python 3.12 and the rest in your home folder; no sudo needed
nano .env                     # fill in DISCORD_TOKEN and GUILD_ID
systemctl --user restart discord-music-bot
```

It runs as a user service and updates yt-dlp every day at 05:00. Logs: `journalctl --user -u discord-music-bot -f`.
To keep it running after you log out, run once: `sudo loginctl enable-linger $USER`. Your music files go in `~/Music`.
To update it from another computer over SSH: `HOST=you@your-server deploy/push.sh` (or put `you@your-server` in
`deploy/.host`, which git ignores).

### 2c. Just try it on your computer

You need Python 3.11+ and [ffmpeg](https://ffmpeg.org/download.html) on your PATH.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt   # Windows: .venv\Scripts\pip install -r requirements.txt
.venv/bin/python -m music_bot               # Windows: .venv\Scripts\python -m music_bot
```

### 3. Optional: the web mixer

`/mixer` gives people a link to the web mixer, so it needs an address they can reach:

- **Just you, on the same computer:** set `PUBLIC_URL=http://127.0.0.1:8765` in `.env`.
- **Anyone, from anywhere:** a free [ngrok](https://ngrok.com) account gives you an HTTPS address. Put your authtoken
  and free static domain in `.env` as `NGROK_AUTHTOKEN` and `NGROK_DOMAIN`, set `PUBLIC_URL=https://<your domain>`,
  and start with `docker compose --profile mixer up -d`. Without Docker,
  `bash deploy/setup-ngrok.sh <authtoken> <your-domain>` sets ngrok up as a service.

The mixer itself only listens on the bot's own computer; ngrok is what makes it reachable. Each `/mixer` link is signed
for one Discord user and expires after 12 hours. ngrok's free plan includes 1 GB of data a month.

## How it works

### The DJ booth

The player message is a DJ booth: an animated picture of two turntables and a mixer (`booth.py`), with buttons for
Pause, Skip, Stop, Loop, Shuffle, Mix now, Crossfade length, Auto-DJ and the Mixer. Each song plays on the other deck
from the one before; the next song is loaded ~25 s early and mixed in with an equal-power crossfade (`mixer.py`),
beat-matched when the tempos are within 8%. Every song goes through ffmpeg's `dynaudnorm` (even loudness) and
`silenceremove` (no silent intros or long silent gaps).

### The web mixer (live DJ)

Anyone with a `/mixer` link can watch; someone in the bot's voice channel can **Take the decks**, which switches to
live mode: two decks with play/cue, pitch (±16%) and nudge, 3-band EQ with kills, a one-knob filter, channel faders, a
crossfader (smooth/cut curve, auto-fade), master volume, beat grids (`dsp.py`, cached in `data/beatgrids.json`), Sync
(tempo and beat phase), Lock, beat jump, Quantise, and click-to-jump waveforms. Finite tracks download whole into
`/dev/shm` so decks can jump anywhere; radio streams forwards. If the DJ closes the page or leaves voice for a minute,
it hands back to automatic mixing. Discord's own skip/loop/shuffle buttons are disabled while someone is live; `/play`
still adds to the queue for the DJ.

### Line-in (real CDJs and mixers)

The mixer page's **🎙️ Line-in** panel lets a DJ in the voice channel stream their own mixer's output (its USB audio,
or an audio interface on its record/booth out) to the bot. The browser records it as Opus with echo cancellation, noise
suppression and auto-gain off, and sends it over `/mixer/linein` (`linein.py`); the bot plays it on a deck and glides
the crossfader over. The deck keeps ~0.4 s buffered and corrects clock drift by up to ±0.5%. If the connection drops,
the page reconnects; after 15 s without it, mixing goes back to automatic.

## Contributing

Make a branch, commit, push and open a pull request. Never commit `.env`, bot tokens, ngrok tokens or anything in
`data/` (they're all in `.gitignore`).

## Notes

- YouTube playback goes through yt-dlp, which is against YouTube's terms of service. Fine for a private server,
  but it can break for a day or so until yt-dlp catches up (restarting the bot picks up the latest version).
- Spotify playlists are read from Spotify's public embed page (no API key), which lists up to about 100 tracks.
  Private playlists can't be read.
- Discord's voice encryption (DAVE) is handled by discord.py 2.7 and its `davey` dependency.
