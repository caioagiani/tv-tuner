"""Áudio pela UPnP da TV (porta 9197): volume, mudo e tocar mídia.

Medido na AU7700 (31/10/2026): GetVolume acompanha a tecla de volume (0→1→0),
então o número é o volume real da TV, não um valor de UPnP à parte.
Tocar mídia é o AVTransport: a TV busca a URL sozinha — por isso o arquivo é
servido pelo painel numa URL que a TV alcança.
"""
import re
import urllib.request
from xml.sax.saxutils import escape

RC = ("RenderingControl1", "urn:schemas-upnp-org:service:RenderingControl:1")
AV = ("AVTransport1", "urn:schemas-upnp-org:service:AVTransport:1")


def _soap(ip, svc, action, args="", timeout=3):
    path, urn = svc
    body = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
        f'<u:{action} xmlns:u="{urn}"><InstanceID>0</InstanceID>{args}</u:{action}>'
        "</s:Body></s:Envelope>"
    ).encode()
    req = urllib.request.Request(f"http://{ip}:9197/upnp/control/{path}", data=body, headers={
        "Content-Type": 'text/xml; charset="utf-8"', "SOAPACTION": f'"{urn}#{action}"',
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _field(xml, name):
    m = re.search(rf"<{name}>([^<]*)<", xml)
    return m.group(1) if m else None


def volume(ip):
    return int(_field(_soap(ip, RC, "GetVolume", "<Channel>Master</Channel>"), "CurrentVolume"))


def set_volume(ip, level):
    level = max(0, min(100, int(level)))
    _soap(ip, RC, "SetVolume", f"<Channel>Master</Channel><DesiredVolume>{level}</DesiredVolume>")
    return level


def muted(ip):
    return _field(_soap(ip, RC, "GetMute", "<Channel>Master</Channel>"), "CurrentMute") == "1"


def set_mute(ip, on):
    _soap(ip, RC, "SetMute", f"<Channel>Master</Channel><DesiredMute>{1 if on else 0}</DesiredMute>")


def transport(ip):
    """PLAYING, PAUSED_PLAYBACK, STOPPED, NO_MEDIA_PRESENT…"""
    return _field(_soap(ip, AV, "GetTransportInfo"), "CurrentTransportState")


def play_url(ip, url, mime="audio/mpeg", title="TV Tuner"):
    kind = "audioItem.musicTrack" if mime.startswith("audio") else "videoItem"
    meta = (
        '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
        f'<item id="0" parentID="-1" restricted="1"><dc:title>{escape(title)}</dc:title>'
        f'<upnp:class>object.item.{kind}</upnp:class>'
        f'<res protocolInfo="http-get:*:{mime}:*">{escape(url)}</res></item></DIDL-Lite>'
    )
    try:
        _soap(ip, AV, "Stop")
    except Exception:
        pass
    _soap(ip, AV, "SetAVTransportURI",
          f"<CurrentURI>{escape(url)}</CurrentURI><CurrentURIMetaData>{escape(meta)}</CurrentURIMetaData>", timeout=10)
    _soap(ip, AV, "Play", "<Speed>1</Speed>", timeout=10)


def pause(ip):
    _soap(ip, AV, "Pause")


def resume(ip):
    _soap(ip, AV, "Play", "<Speed>1</Speed>")


def stop(ip):
    _soap(ip, AV, "Stop")
