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

import discord
from aiohttp import WSMsgType, web

from . import config, store, tracks
from .models import TrackError, UserError

log = logging.getLogger(__name__)

TOKEN_LIFETIME = 12 * 3600
_SECRET_FILE = "mixer_secret.json"
_STATE_EVERY = 0.1  # seconds between state updates to each page
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


def mixer_link(guild_id: int, user_id: int) -> str | None:
    if not config.PUBLIC_URL:
        return None
    return f"{config.PUBLIC_URL.rstrip('/')}/mixer#{make_token(guild_id, user_id)}"


class MixerServer:
    def __init__(self, bot: discord.Client, music):
        self.bot = bot
        self.music = music  # the Music cog, which owns the players
        self._connections: dict[tuple[int, int], int] = {}  # (guild, user) -> open pages
        self._release_tasks: dict[int, asyncio.Task] = {}
        self._runner: web.AppRunner | None = None
        app = web.Application()
        app.add_routes([web.get("/", self._home), web.get("/mixer", self._page), web.get("/mixer/ws", self._socket)])
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
            await player.deck_cue(slot)
        elif cmd == "set":
            player.deck_set(slot, str(data.get("param")), float(data.get("value", 0)))
        elif cmd == "sync":
            player.deck_sync(slot)
        elif cmd == "mixer":
            player.mixer_set(str(data.get("param")), data.get("value"))
        else:
            raise UserError(f"Unknown command {cmd}.")
