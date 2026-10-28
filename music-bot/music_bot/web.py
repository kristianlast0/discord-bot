"""The web mixer: a page with two decks, EQ, faders and a crossfader.

It's served on localhost only and reached through ngrok. Each person gets their own link from `/mixer` in Discord:
a token signed with a secret only the bot knows, naming their server and Discord ID, valid for 12 hours.
The token travels in the URL's #fragment, so it never shows up in request logs.
"""
import asyncio
import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from pathlib import Path

import aiohttp
import discord
from aiohttp import WSMsgType, web

from . import config, linein, store, tracks
from .models import TrackError, UserError

log = logging.getLogger(__name__)

TOKEN_LIFETIME = 12 * 3600
_SECRET_FILE = "mixer_secret.json"
_STATE_EVERY = 0.25  # seconds between state updates to each page (the page animates in between; ngrok's free plan has 1 GB/month)
_LINE_IN_GRACE = 15  # seconds a dropped line-in connection has to come back before mixing goes back to automatic
_SEAT_GRACE = 60  # seconds the DJ can be gone (page closed, or out of voice) before mixing goes back to automatic
_PAGE = (Path(__file__).parent / "static" / "mixer.html").read_text()


def _secret() -> bytes:
    secret = store.load(_SECRET_FILE, None)
    if not secret:
        secret = secrets.token_hex(32)
        store.save(_SECRET_FILE, secret)
    return secret.encode()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def make_token(guild_id: int, user_id: int) -> str:
    payload = f"{guild_id}.{user_id}.{int(time.time()) + TOKEN_LIFETIME}".encode()
    return f"{_b64(payload)}.{_b64(hmac.new(_secret(), payload, hashlib.sha256).digest()[:16])}"


def read_token(token: str) -> tuple[int, int] | None:
    """(guild ID, user ID) if the token is genuine and unexpired."""
    try:
        payload_part, sig_part = token.split(".")
        payload = _unb64(payload_part)
        expected = hmac.new(_secret(), payload, hashlib.sha256).digest()[:16]
        if not hmac.compare_digest(_unb64(sig_part), expected):
            return None
        guild_id, user_id, expires = map(int, payload.decode().split("."))
        return (guild_id, user_id) if expires > time.time() else None
    except (ValueError, UnicodeDecodeError):
        return None


_NGROK_API = "http://127.0.0.1:4040/api/tunnels"


async def public_url() -> str | None:
    """PUBLIC_URL if set; otherwise ask the local ngrok agent which address it currently has."""
    if config.PUBLIC_URL:
        return config.PUBLIC_URL.rstrip("/")
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2)) as session:
            async with session.get(_NGROK_API) as resp:
                tunnels = (await resp.json()).get("tunnels", [])
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
        return None
    for tunnel in tunnels:
        if tunnel.get("public_url", "").startswith("https://") and str(config.MIXER_PORT) in tunnel.get("config", {}).get("addr", ""):
            return tunnel["public_url"].rstrip("/")
    return None


async def mixer_link(guild_id: int, user_id: int) -> str | None:
    base = await public_url()
    return f"{base}/mixer#{make_token(guild_id, user_id)}" if base else None


class MixerServer:
    def __init__(self, bot: discord.Client, music):
        self.bot = bot
        self.music = music  # the Music cog, which owns the players
        self._connections: dict[tuple[int, int], int] = {}  # (guild, user) -> open pages
        self._release_tasks: dict[int, asyncio.Task] = {}
        self._runner: web.AppRunner | None = None
        app = web.Application()
        app.add_routes([web.get("/", self._home), web.get("/mixer", self._page), web.get("/mixer/ws", self._socket),
                        web.get("/mixer/linein", self._line_in)])
        self._line_in_stops: dict[int, asyncio.Task] = {}
        self._app = app

    async def start(self) -> None:
        self._runner = web.AppRunner(self._app, access_log=None)
        await self._runner.setup()
        await web.TCPSite(self._runner, "127.0.0.1", config.MIXER_PORT).start()
        log.info("Web mixer listening on 127.0.0.1:%d (public URL: %s)", config.MIXER_PORT, config.PUBLIC_URL or "not set")

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()

    async def _home(self, request: web.Request) -> web.Response:
        return web.Response(text="DJ mixer. Get your personal link with /mixer in Discord.")

    async def _page(self, request: web.Request) -> web.Response:
        return web.Response(text=_PAGE, content_type="text/html", headers={"Cache-Control": "no-store"})

    async def _socket(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=64 * 1024)
        await ws.prepare(request)
        try:
            first = await ws.receive_json(timeout=10)
        except (asyncio.TimeoutError, TypeError, ValueError):
            await ws.close(code=4001, message=b"no token")
            return ws
        ids = read_token(str(first.get("token", "")))
        guild = self.bot.get_guild(ids[0]) if ids else None
        if not guild:
            await ws.send_json({"type": "denied", "message": "This mixer link has expired or isn't valid. Get a fresh one with /mixer in Discord."})
            await ws.close()
            return ws
        user_id = ids[1]
        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except discord.HTTPException:
                await ws.send_json({"type": "denied", "message": "You're not in that Discord server any more."})
                await ws.close()
                return ws

        player = self.music.player(guild)
        key = (guild.id, user_id)
        self._connections[key] = self._connections.get(key, 0) + 1
        player.consoles += 1
        if player.live_dj and player.live_dj[0] == user_id and guild.id in self._release_tasks:
            self._release_tasks.pop(guild.id).cancel()
        await ws.send_json({"type": "hello", "you": {"id": str(user_id), "name": member.display_name}})
        sender = asyncio.create_task(self._send_state(ws, guild, player, user_id))
        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                try:
                    data = json.loads(msg.data)
                except ValueError:
                    continue
                # Loading a track takes a few seconds; don't hold up the faders meanwhile.
                asyncio.create_task(self._run(ws, guild, player, user_id, data))
        finally:
            sender.cancel()
            player.consoles -= 1
            self._connections[key] -= 1
            if not self._connections[key]:
                del self._connections[key]
                if player.live_dj and player.live_dj[0] == user_id:
                    self._schedule_release(guild.id, player, user_id)
        return ws

    async def _identify(self, ws: web.WebSocketResponse) -> tuple[discord.Guild, discord.Member] | None:
        """Read the token the page sends first; reply "denied" and return None if it's no good."""
        try:
            first = await ws.receive_json(timeout=10)
        except (asyncio.TimeoutError, TypeError, ValueError):
            return None
        ids = read_token(str(first.get("token", "")))
        guild = self.bot.get_guild(ids[0]) if ids else None
        member = guild.get_member(ids[1]) if guild else None
        if not member:
            await ws.send_json({"type": "denied", "message": "This mixer link has expired or isn't valid, or you're not in the voice channel. Get a fresh link with /mixer."})
            return None
        return guild, member

    async def _line_in(self, request: web.Request) -> web.WebSocketResponse:
        """Audio from a DJ's own decks: binary chunks from the page's recorder, played on a deck."""
        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=1024 * 1024)
        await ws.prepare(request)
        who = await self._identify(ws)
        if not who:
            await ws.close()
            return ws
        guild, member = who
        player = self.music.player(guild)
        if not self._in_voice(guild, player, member.id):
            # If the bot isn't in voice yet, follow the DJ into theirs.
            if not player.voice and member.voice and member.voice.channel:
                try:
                    self.music.check_voice_allowed(guild, member.voice.channel)
                    await player.connect(member.voice.channel)
                except (UserError, discord.ClientException) as e:
                    await ws.send_json({"type": "error", "message": str(e)})
                    await ws.close()
                    return ws
            else:
                await ws.send_json({"type": "error", "message": "Join the bot's voice channel in Discord first."})
                await ws.close()
                return ws
        pending = self._line_in_stops.pop(guild.id, None)
        if pending:
            pending.cancel()
        audio = linein.LineInAudio()
        try:
            await player.linein_start(member, audio)
        except UserError as e:
            audio.cleanup()
            await ws.send_json({"type": "error", "message": str(e)})
            await ws.close()
            return ws
        await ws.send_json({"type": "live"})
        log.info("%s is live from line-in", member.display_name)
        try:
            async for msg in ws:
                if msg.type != WSMsgType.BINARY:
                    continue
                if not player.live_dj or player.live_dj[0] != member.id:  # handed back, or a manager took over
                    await ws.send_json({"type": "ended", "message": "You're no longer on air."})
                    break
                audio.feed(msg.data)
        finally:
            if player.live_dj and player.live_dj[0] == member.id:
                self._line_in_stops[guild.id] = asyncio.create_task(self._stop_line_in_later(guild.id, player, member.id))
        await ws.close()
        return ws

    async def _stop_line_in_later(self, guild_id: int, player, user_id: int) -> None:
        """Give a dropped connection a moment to come back (the page retries) before leaving the decks."""
        await asyncio.sleep(_LINE_IN_GRACE)
        self._line_in_stops.pop(guild_id, None)
        await player.linein_stop(user_id)

    def _schedule_release(self, guild_id: int, player, user_id: int) -> None:
        async def later():
            await asyncio.sleep(_SEAT_GRACE)
            self._release_tasks.pop(guild_id, None)
            if player.live_dj and player.live_dj[0] == user_id:
                await player.live_release()

        old = self._release_tasks.pop(guild_id, None)
        if old:
            old.cancel()
        self._release_tasks[guild_id] = asyncio.create_task(later())

    def _in_voice(self, guild: discord.Guild, player, user_id: int) -> bool:
        vc, member = player.voice, guild.get_member(user_id)
        return bool(vc and vc.channel and member and member.voice and member.voice.channel == vc.channel)

    async def _send_state(self, ws: web.WebSocketResponse, guild: discord.Guild, player, user_id: int) -> None:
        queue_key = None
        wave_keys: list = [None, None]
        away_since = None
        try:
            while not ws.closed:
                state = player.snapshot()
                state["type"] = "state"
                is_dj = bool(player.live_dj and player.live_dj[0] == user_id)
                in_voice = self._in_voice(guild, player, user_id)
                state["you_dj"] = is_dj
                state["in_voice"] = in_voice
                if state["queue_key"] != queue_key:
                    queue_key = state["queue_key"]
                    state["queue"] = player.queue_snapshot()
                await ws.send_json(state)
                for slot in (0, 1):  # waveforms are big, so each is sent once per track
                    key = (state["decks"][slot] or {}).get("wave_key")
                    if key != wave_keys[slot]:
                        wave = player.wave_for(slot) if key else None
                        await ws.send_json({"type": "wave", "slot": slot, "wave": wave})
                        wave_keys[slot] = key
                if is_dj and not in_voice:  # the DJ left the voice channel
                    away_since = away_since or time.monotonic()
                    if time.monotonic() - away_since > _SEAT_GRACE:
                        await player.live_release()
                        await ws.send_json({"type": "error", "message": "You left the voice channel, so the decks went back to automatic."})
                else:
                    away_since = None
                await asyncio.sleep(_STATE_EVERY)
        except (ConnectionResetError, RuntimeError, asyncio.CancelledError):
            pass

    async def _run(self, ws: web.WebSocketResponse, guild: discord.Guild, player, user_id: int, data: dict) -> None:
        try:
            await self._command(guild, player, user_id, data)
        except (UserError, TrackError) as e:
            await self._reply(ws, {"type": "error", "message": str(e).replace("**", "")})
        except Exception:
            log.exception("Mixer command failed: %s", data)
            await self._reply(ws, {"type": "error", "message": "Something went wrong. It's been logged."})

    @staticmethod
    async def _reply(ws: web.WebSocketResponse, message: dict) -> None:
        if not ws.closed:
            try:
                await ws.send_json(message)
            except ConnectionResetError:
                pass

    async def _command(self, guild: discord.Guild, player, user_id: int, data: dict) -> None:
        cmd = data.get("cmd")
        member = guild.get_member(user_id)
        mention = f"<@{user_id}>"

        if cmd == "join":  # bring the bot into the caller's voice channel
            if not member or not member.voice or not member.voice.channel:
                raise UserError("Join a voice channel in Discord first.")
            self.music.check_voice_allowed(guild, member.voice.channel)
            await player.connect(member.voice.channel)
            return
        if cmd == "line_in_stop":  # the DJ pressed Stop on their line-in
            task = self._line_in_stops.pop(guild.id, None)
            if task:
                task.cancel()
            await player.linein_stop(user_id)
            return
        if cmd == "take":
            if not member:
                raise UserError("Join the bot's voice channel in Discord first.")
            await player.live_take(member)
            return
        if cmd == "queue_add":  # anyone in the voice channel can add songs, like /play
            if not self._in_voice(guild, player, user_id):
                raise UserError("Join the bot's voice channel to add songs.")
            found = await tracks.lookup(str(data.get("query", ""))[:300], mention)
            player.add(found)
            await player.refresh_player()
            return

        # Everything else drives the decks, which only the DJ can do.
        if not player.live_dj or player.live_dj[0] != user_id:
            raise UserError("Take the decks first.")
        slot = int(data.get("slot", 0)) % 2
        if cmd == "release":
            await player.live_release()
        elif cmd == "load":
            index = int(data.get("index", -1))
            track = player.take_from_queue(index)
            try:
                await player.deck_load(slot, track)
            except Exception:
                player.queue.insert(index, track)
                raise
        elif cmd == "load_search":
            found = await tracks.lookup(str(data.get("query", ""))[:300], mention)
            await player.deck_load(slot, found[0])
            if len(found) > 1:
                player.add(found[1:])
        elif cmd == "queue_remove":
            player.take_from_queue(int(data.get("index", -1)))
        elif cmd in ("play", "pause"):
            player.deck_play(slot, cmd == "play")
        elif cmd == "cue":
            player.deck_cue(slot)
        elif cmd == "seek":
            player.deck_seek(slot, float(data.get("value", 0)))
        elif cmd == "beatjump":
            player.deck_beatjump(slot, float(data.get("value", 0)))
        elif cmd == "lock":
            player.deck_lock(slot, bool(data.get("value")))
        elif cmd == "set":
            player.deck_set(slot, str(data.get("param")), float(data.get("value", 0)))
        elif cmd == "sync":
            player.deck_sync(slot)
        elif cmd == "mixer":
            player.mixer_set(str(data.get("param")), data.get("value"))
        else:
            raise UserError(f"Unknown command {cmd}.")
