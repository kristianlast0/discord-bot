# Discord music bot

Plays music in a Discord voice channel from YouTube (links, playlists or search), Spotify links (looked up on
YouTube, because bots can't stream Spotify), music files on the laptop, and internet radio. Runs on the ideaPad
(192.168.1.55) as a user systemd service.

## Commands

| Command | What it does |
| --- | --- |
| `/play <song or link> [next]` | YouTube/Spotify/SoundCloud link or playlist, or a YouTube search |
| `/local <song or folder>` | Files in `~/Music` on the laptop (autocompletes) |
| `/radio play <station or URL>` · `list` · `add` · `remove` | Internet radio; saved stations (BBC + UK by default) in `data/stations.json` |
| `/radio browse` | Private station browser: worldwide stations by country and style, or search (radio-browser.info) |
| `/radio panel` | Posts a permanent Radio panel (Saved / World / Search buttons) in the music channel |
| `/247 on [station]` · `off` | 24/7 mode (server managers): stay in the voice channel around the clock, rejoin after restarts, and play the station when the queue is empty |
| `/dj crossfade <0-12>` · `/dj autodj <on/off>` | DJ mode: songs crossfade on two decks (default 6 s); Auto-DJ adds similar songs when the queue runs out |
| `/mixer` | Your personal link to the web mixer (also the 🎛️ button on the booth) |
| `/musicchannel [#channel]` | Restrict music commands and the player message to one channel |
| `/pause` `/resume` `/skip` `/stop` `/leave` | Playback control (you need to be in the bot's voice channel) |
| `/queue [page]` `/nowplaying` | What's playing and what's next |
| `/volume <0-150>` `/loop <off/track/queue>` `/shuffle` `/remove <n>` `/clear` | Queue tweaks |
| `/playlist save` · `load` · `list` · `show` · `delete` | Saved playlists (only the creator or a server manager can overwrite or delete one) |

The bot leaves after `IDLE_TIMEOUT` seconds (default 5 minutes) of nothing playing or nobody listening, unless 24/7 mode is on.

## The DJ booth

The player message is a DJ booth: an animated picture of two turntables and a mixer (`booth.py`), with buttons for
Pause, Skip, Stop, Loop, Shuffle, Mix now, Crossfade length and Auto-DJ. Each song plays on the other deck from the one
before; the next song is loaded ~25 s early and mixed in with an equal-power crossfade (`mixer.py`). Every song goes
through ffmpeg's `dynaudnorm` (even loudness) and `silenceremove` (no silent intros or long silent gaps).

## The web mixer (live DJ)

`/mixer` gives each person a personal link (signed with their Discord ID, valid 12 hours) to a web page served by the
bot on `127.0.0.1:8765` and published through ngrok (`deploy/setup-ngrok.sh`, which sets `PUBLIC_URL`). Anyone can
watch; someone in the bot's voice channel can **Take the decks**, which switches the mixer to live mode: two decks with
play/cue, pitch (±16%) and nudge, 3-band EQ with kills, a one-knob filter, channel faders, a crossfader (smooth/cut
curve, auto-fade), master volume, beat grids (`dsp.py`, cached in `data/beatgrids.json`), Sync (tempo and beat phase), Lock, beat jump, Quantise, and
click-to-jump waveforms. Finite tracks download whole into `/dev/shm` so decks can jump anywhere; radio streams forwards.
Automatic mixes beat-match songs within ±8% (`/dj beatmatch`), then ease the pitch back to normal. If the DJ closes the
page or leaves voice for a minute, it hands back to automatic mixing. Discord's own skip/loop/shuffle buttons are
disabled while someone is live; `/play` still adds to the queue for the DJ.

### Line-in (real CDJs and mixers)

The mixer page's **🎙️ Line-in** panel lets a DJ in the voice channel stream their own mixer's output (its USB audio,
or an audio interface on its record/booth out) to the bot. The browser records it as Opus with echo cancellation, noise
suppression and auto-gain off, and sends it over `/mixer/linein` (`linein.py`); the bot plays it on a deck and glides
the crossfader over. The deck keeps ~0.4 s buffered and corrects clock drift by up to ±0.5%. If the connection drops,
the page reconnects; after 15 s without it, mixing goes back to automatic.

## First-time setup

1. **Create the bot**: https://discord.com/developers/applications → New Application → **Bot** → Reset Token,
   and copy the token. No privileged intents are needed.
2. **Invite it** (replace `APP_ID` with the Application ID from General Information):
   `https://discord.com/oauth2/authorize?client_id=APP_ID&scope=bot+applications.commands&permissions=3165184`
   (View Channels, Send Messages, Embed Links, Connect, Speak).
3. **Deploy** from WSL: `deploy/push.sh` copies the code to `~/discord-music-bot` on the laptop and runs
   `deploy/install.sh` there. That installs `uv`, Python 3.12 and the dependencies in your home folder (no sudo),
   then sets up the services.
4. **Configure** on the laptop: edit `~/discord-music-bot/.env` to set `DISCORD_TOKEN` and `GUILD_ID`, then
   `systemctl --user restart discord-music-bot`.

## Running it

- Logs: `journalctl --user -u discord-music-bot -f`
- Restart: `systemctl --user restart discord-music-bot`
- Update the code: edit it here, then run `deploy/push.sh` again.
- Local music: copy files into `~/Music` on the laptop, e.g. `rsync -av ~/some-album jamie@192.168.1.55:Music/`
- yt-dlp updates itself every day at 05:00 (`discord-music-bot-update.timer`), because YouTube changes often.
  If YouTube stops working, run `systemctl --user start discord-music-bot-update` to update straight away.

## Notes

- YouTube playback goes through yt-dlp, which is against YouTube's terms of service. Fine for a private server,
  but it can break for a day or so until yt-dlp catches up.
- Spotify playlists are read from Spotify's public embed page (no API key), which lists up to about 100 tracks.
  Private playlists can't be read.
- Discord's voice encryption (DAVE) is handled by discord.py 2.7 and its `davey` dependency.
