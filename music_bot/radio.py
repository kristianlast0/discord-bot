"""Saved internet radio stations (data/stations.json)."""
import re

from . import store
from .models import Track, TrackError

_FILE = "stations.json"
_BBC = "http://as-hls-ww-live.akamaized.net/{pool}/live/ww/{id}/{id}.isml/{id}-audio%3d128000.norewind.m3u8"
_BAUER = "direct=true&aw_0_1st.playerid=BMUK_Airable&aw_0_1st.skey=6778249992"  # Bauer streams refuse requests without these
# Only used until someone adds or removes a station; then data/stations.json holds the full list.
DEFAULT_STATIONS = {
    "BBC Radio 1": _BBC.format(pool="pool_01505109", id="bbc_radio_one"),
    "BBC Radio 1Xtra": _BBC.format(pool="pool_92079267", id="bbc_1xtra"),
    "BBC Radio 1 Dance": _BBC.format(pool="pool_62063831", id="bbc_radio_one_dance"),
    "BBC Radio 2": _BBC.format(pool="pool_74208725", id="bbc_radio_two"),
    "BBC Radio 3": _BBC.format(pool="pool_23461179", id="bbc_radio_three"),
    "BBC Radio 4": _BBC.format(pool="pool_55057080", id="bbc_radio_fourfm"),
    "BBC Radio 4 Extra": _BBC.format(pool="pool_26173715", id="bbc_radio_four_extra"),
    "BBC Radio 5 Live": _BBC.format(pool="pool_89021708", id="bbc_radio_five_live"),
    "BBC Radio 6 Music": _BBC.format(pool="pool_81827798", id="bbc_6music"),
    "BBC Asian Network": _BBC.format(pool="pool_22108647", id="bbc_asian_network"),
    "BBC World Service": "https://stream.live.vc.bbcmedia.co.uk/bbc_world_service",
    "Capital": "https://media-ssl.musicradio.com/CapitalUK",
    "Capital XTRA": "https://media-ssl.musicradio.com/CapitalXTRALondonMP3",
    "Heart": "https://media-ssl.musicradio.com/HeartUK",
    "Heart 80s": "https://media-ssl.musicradio.com/Heart80s",
    "Smooth": "https://media-ssl.musicradio.com/SmoothUK",
    "Classic FM": "https://media-ssl.musicradio.com/ClassicFMMP3",
    "Radio X": "https://media-ssl.musicradio.com/RadioXUK",
    "LBC": "https://media-ssl.musicradio.com/LBCUK",
    "Kiss": "https://live-kiss.sharp-stream.com/kiss100.mp3",
    "Kisstory": f"https://live-bauerkiss.sharp-stream.com/kisstory.aac?{_BAUER}",
    "Magic": f"https://stream-mz.hellorayo.co.uk/magicnational.mp3?{_BAUER}",
    "Absolute Radio": f"http://stream-ar.planetradio.co.uk/absoluteradiohigh.aac?{_BAUER}",
    "Absolute 80s": f"http://stream-ar.planetradio.co.uk/absolute80shigh.aac?{_BAUER}",
    "Absolute 90s": f"http://stream-ar.planetradio.co.uk/absolute90shigh.aac?{_BAUER}",
    "Greatest Hits Radio": f"https://stream-mz.hellorayo.co.uk/net2westmidlands.aac?{_BAUER}",
    "Virgin Radio": "https://radio.virginradio.co.uk/stream",
    "talkSPORT": "https://radio.talksport.com/stream",
    "Times Radio": "https://timesradio.wireless.radio/stream",
    "Radio Paradise": "https://stream.radioparadise.com/mp3-192",
    "SomaFM Groove Salad": "https://ice1.somafm.com/groovesalad-128-mp3",
}
_URL_RE = re.compile(r"^https?://\S+$", re.I)


def stations() -> dict[str, str]:
    return store.load(_FILE, DEFAULT_STATIONS)


def _find(name: str) -> str | None:
    return next((n for n in stations() if n.lower() == name.strip().lower()), None)


def add(name: str, url: str) -> None:
    if not _URL_RE.match(url.strip()):
        raise TrackError("The stream needs to be an http(s) URL.")
    data = stations()
    existing = _find(name)
    if existing:
        del data[existing]
    data[name.strip()] = url.strip()
    store.save(_FILE, data)


def remove(name: str) -> str:
    existing = _find(name)
    if not existing:
        raise TrackError(f"There's no station called “{name}”.")
    data = stations()
    del data[existing]
    store.save(_FILE, data)
    return existing


def track_for(name_or_url: str, requester: str | None) -> Track:
    text = name_or_url.strip()
    if _URL_RE.match(text):
        return Track(text, "stream", text, page_url=text, requester=requester)
    name = _find(text)
    if not name:
        raise TrackError(f"There's no station called “{text}”. See `/radio list`, or paste a stream URL.")
    return Track(f"📻 {name}", "stream", stations()[name], requester=requester)
