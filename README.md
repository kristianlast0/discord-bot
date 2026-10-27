# Discord music bot

Plays music in a Discord voice channel from YouTube (links, playlists or search), Spotify links (looked up on
YouTube, because bots can't stream Spotify), music files on the laptop, and internet radio. Runs on the ideaPad
(192.168.1.55) as a user systemd service.

## Commands

| Command | What it does |
| --- | --- |
| `/play <song or link> [next]` | YouTube/Spotify/SoundCloud link or playlist, or a YouTube search |
| `/local <song or folder>` | Files in `~/Music` on the laptop (autocompletes) |
| `/radio play <station or URL>` · `list` · `add` · `remove` | Internet radio; stations saved in `data/stations.json` |
| `/pause` `/resume` `/skip` `/stop` `/leave` | Playback control (you need to be in the bot's voice channel) |
| `/queue [page]` `/nowplaying` | What's playing and what's next |
| `/volume <0-150>` `/loop <off/track/queue>` `/shuffle` `/remove <n>` `/clear` | Queue tweaks |
| `/playlist save` · `load` · `list` · `show` · `delete` | Saved playlists (only the creator or a server manager can overwrite or delete one) |

The bot leaves after `IDLE_TIMEOUT` seconds (default 5 minutes) of nothing playing or nobody listening.

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
