"""Music Assistant artwork bridge for Home Assistant and WLED HUB75."""

import asyncio
import io
import json
import logging
import math
import os
import signal
import socket
import struct
import time
from collections import OrderedDict
from datetime import datetime, timezone
from contextlib import suppress
from ipaddress import IPv4Address
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit

import aiohttp
from PIL import Image, ImageDraw, ImageFont, ImageOps

LOGGER = logging.getLogger("hub75")
MAX_IMAGE_BYTES = 10 * 1024 * 1024
DDP_PORT = 4048
DDP_PAYLOAD_BYTES = 1440
AUDIO_SYNC_GROUP = "239.0.0.1"
AUDIO_SYNC_PACKET = struct.Struct("<6s2sffBB16sHff")
Image.MAX_IMAGE_PIXELS = 16 * 1024 * 1024


def select_player(states, allowed_entities, require_picture=True):
    candidates = [
        state for state in states
        if state["entity_id"].startswith("media_player.")
        and state.get("state") == "playing"
        and state.get("attributes", {}).get("app_id") == "music_assistant"
        and (not require_picture or state["attributes"].get("entity_picture"))
        and (not allowed_entities or state["entity_id"] in allowed_entities)
    ]
    return max(candidates, key=lambda state: (
        state.get("last_changed", ""), state["entity_id"]
    ), default=None)


def load_cover(image_bytes):
    with Image.open(io.BytesIO(image_bytes)) as source:
        image = ImageOps.exif_transpose(source).convert("RGBA")
        image = ImageOps.pad(image, (64, 64), Image.Resampling.LANCZOS)
        background = Image.new("RGBA", image.size, (0, 0, 0, 255))
        background.alpha_composite(image)
        return background.convert("RGB")


def remaining_time(player, now=None):
    attributes = player.get("attributes", {})
    try:
        duration = float(attributes.get("media_duration", 0))
        position = float(attributes.get("media_position", 0))
    except (TypeError, ValueError):
        return "--:--"
    if not math.isfinite(duration) or not math.isfinite(position) or duration <= 0:
        return "--:--"
    timestamp = attributes.get("media_position_updated_at") or player.get("last_updated")
    if player.get("state") == "playing" and timestamp:
        try:
            updated = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
            position += max(0, ((now or datetime.now(timezone.utc)) - updated).total_seconds())
        except (TypeError, ValueError, AttributeError):
            pass
    seconds = math.ceil(max(0, duration - max(0, position)))
    minutes, seconds = divmod(seconds, 60)
    if minutes >= 60:
        hours, minutes = divmod(minutes, 60)
        return f"-{hours}:{minutes:02}:{seconds:02}"
    return f"-{minutes}:{seconds:02}"


def decode_audio_sync(packet):
    if len(packet) != AUDIO_SYNC_PACKET.size:
        return None
    header, _, sample_raw, sample_smth, _, _, spectrum, _, magnitude, frequency = (
        AUDIO_SYNC_PACKET.unpack(packet))
    if header != b"00002\x00" or not all(math.isfinite(value) for value in (
            sample_raw, sample_smth, magnitude, frequency)):
        return None
    return tuple(spectrum)


def artist_image_sources(artist, base_url):
    images = [artist.get("image"), *artist.get("metadata", {}).get("images", [])]
    sources = []
    for image in images:
        if not isinstance(image, dict) or image.get("type") not in ("thumb", "fanart", "landscape"):
            continue
        path = image.get("path")
        if not path:
            continue
        if image.get("remotely_accessible") and urlsplit(path).scheme in ("http", "https"):
            source = (path, False)
        elif image.get("proxy_id"):
            source = (base_url + "/imageproxy/" + quote(str(image["proxy_id"]), safe="")
                      + "?size=64", True)
        else:
            query = urlencode({"path": quote(path, safe=""),
                               "provider": image.get("provider", ""), "size": 64})
            source = (base_url + "/imageproxy?" + query, True)
        if source not in sources:
            sources.append(source)
    return sources[:12]


def render_equalizer(spectrum):
    if len(spectrum) != 16:
        raise ValueError("Equalizer requires 16 spectrum bands")
    frame = Image.new("RGB", (64, 64), "black")
    draw = ImageDraw.Draw(frame)
    for index, amplitude in enumerate(spectrum):
        height = round(max(0, min(255, amplitude)) * 56 / 255)
        for row in range(height):
            color = "#73dce8" if row < 28 else "#efc871" if row < 45 else "#ff6678"
            draw.line((index * 4, 59 - row, index * 4 + 2, 59 - row), fill=color)
    draw.line((0, 60, 63, 60), fill="#24484c")
    return frame


def render_frame(cover, player, scroll_seconds=0, now=None, page_interval=8,
                 equalizer=None, show_track_info=True, artist_images=()):
    elapsed = max(0, scroll_seconds)
    artist_images = artist_images[:3]
    page_count = 1 + len(artist_images) + bool(show_track_info) + (equalizer is not None)
    page_index = int(elapsed // page_interval) % page_count
    if page_index == 0:
        return cover.copy()
    if page_index <= len(artist_images):
        return artist_images[page_index - 1].copy()
    if equalizer is not None and page_index == page_count - 1:
        return render_equalizer(equalizer)
    completed_cycles = elapsed // (page_count * page_interval)
    scroll_seconds = completed_cycles * page_interval + elapsed % page_interval
    frame = Image.new("RGB", (64, 64), "black")
    font = ImageFont.load_default(size=11)
    attributes = player.get("attributes", {})
    timer = remaining_time(player, now)
    timer_font = ImageFont.load_default(size=18)
    draw = ImageDraw.Draw(frame)
    while draw.textlength(timer, font=timer_font) > 64:
        timer_font = ImageFont.load_default(size=timer_font.size - 1)
    timer_width = math.ceil(draw.textlength(timer, font=timer_font))
    timer_top = timer_font.getbbox(timer)[1]
    draw.text(((64 - timer_width) // 2, -timer_top), timer,
              font=timer_font, fill="#93e9ac")
    lines = (
        (attributes.get("media_title") or "", "white"),
        (attributes.get("media_artist") or "", "#73dce8"),
        (attributes.get("media_album_name") or "", "#efc871"),
    )
    for index, (text, color) in enumerate(lines):
        text = " ".join(str(text).split())[:512]
        row = Image.new("RGB", (64, 14), "black")
        draw = ImageDraw.Draw(row)
        width = math.ceil(draw.textlength(text, font=font))
        top = font.getbbox(text)[1] if text else 0
        if width > 64:
            offset = int(max(0, scroll_seconds - 2) * 6) % (width + 16)
            draw.text((-offset, -top), text, font=font, fill=color)
            draw.text((width + 16 - offset, -top), text, font=font, fill=color)
        else:
            draw.text(((64 - width) // 2, -top), text, font=font, fill=color)
        frame.paste(row, (0, 22 + index * 14))
    return frame


def render_paused_frame():
    frame = Image.new("RGB", (64, 64), "black")
    draw = ImageDraw.Draw(frame)
    font = ImageFont.load_default(size=13)
    for text, top in (("music", 19), ("paused", 35)):
        width = math.ceil(draw.textlength(text, font=font))
        draw.text(((64 - width) // 2, top - font.getbbox(text)[1]),
                  text, font=font, fill="white")
    return frame


def rotate_frame(frame, degrees):
    angle = int(degrees)
    if angle not in (0, 45, 90, 135, 180):
        raise ValueError("rotation must be 0, 45, 90, 135, or 180 degrees")
    if angle == 0:
        return frame
    rotated = frame.rotate(-angle, resample=Image.Resampling.BICUBIC,
                           expand=True, fillcolor="black")
    return ImageOps.pad(rotated, (64, 64), Image.Resampling.LANCZOS, color="black")


def pixel_chunks(image_bytes):
    image = image_bytes if isinstance(image_bytes, Image.Image) else load_cover(image_bytes)
    colors = ["%02x%02x%02x" % pixel for pixel in image.convert("RGB").getdata()]
    return [
        {"seg": [{"id": 0, "i": [offset, *colors[offset:offset + 256]]}]}
        for offset in range(0, len(colors), 256)
    ]


def ddp_packets(rgb_data):
    if len(rgb_data) != 64 * 64 * 3:
        raise ValueError("DDP requires a 64x64 RGB frame")
    return [
        struct.pack("!BBBBIH", 0x41 if offset + DDP_PAYLOAD_BYTES >= len(rgb_data) else 0x40,
                    0, 0x0B, 1, offset, len(rgb_data[offset:offset + DDP_PAYLOAD_BYTES]))
        + rgb_data[offset:offset + DDP_PAYLOAD_BYTES]
        for offset in range(0, len(rgb_data), DDP_PAYLOAD_BYTES)
    ]


class AudioSyncReceiver(asyncio.DatagramProtocol):
    def __init__(self, port=11988, interface="0.0.0.0"):
        self.port = int(port)
        if not 0 <= self.port <= 65535:
            raise ValueError("audio_sync_port must be a valid UDP port")
        self.interface = str(IPv4Address(interface))
        self.transport = None
        self.bands = (0,) * 16
        self.received_at = None

    async def start(self):
        receiver_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            receiver_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            receiver_socket.bind(("0.0.0.0", self.port))
            receiver_socket.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                                       socket.inet_aton(AUDIO_SYNC_GROUP)
                                       + socket.inet_aton(self.interface))
            receiver_socket.setblocking(False)
            self.port = receiver_socket.getsockname()[1]
            self.transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
                lambda: self, sock=receiver_socket)
        except BaseException:
            receiver_socket.close()
            raise
        LOGGER.info("Receiving WLED Audio Sync on %s:%s (interface %s)",
                    AUDIO_SYNC_GROUP, self.port, self.interface)

    def datagram_received(self, packet, address):
        bands = decode_audio_sync(packet)
        if bands is not None:
            self.bands = bands
            self.received_at = time.monotonic()

    def spectrum(self):
        if self.received_at is None or time.monotonic() - self.received_at >= 2:
            return (0,) * 16
        return self.bands

    def clear(self):
        self.bands = (0,) * 16
        self.received_at = None

    def close(self):
        if self.transport is not None:
            self.transport.close()
            self.transport = None
        self.clear()


class ArtworkBridge:
    def __init__(self, session, options, token, ha_url="http://supervisor/core/api"):
        self.session = session
        self.options = options
        self.ha_url = ha_url.rstrip("/")
        self.ha_headers = {"Authorization": f"Bearer {token}"}
        self.display_url = options["display_url"].rstrip("/")
        if urlsplit(self.display_url).scheme not in ("http", "https"):
            raise ValueError("display_url must use HTTP or HTTPS")
        self.output_protocol = options.get("output_protocol", "http")
        if self.output_protocol not in ("http", "ddp"):
            raise ValueError("output_protocol must be http or ddp")
        self.audio_sync = (AudioSyncReceiver(options.get("audio_sync_port", 11988),
                                             options.get("audio_sync_interface", "0.0.0.0"))
                           if options.get("show_equalizer", False) else None)
        self.ddp_socket = None
        self.ddp_address = None
        self.ddp_active = False
        self.ddp_heartbeat = None
        self.ddp_lock = asyncio.Lock()
        self.last_ddp_frame = None
        self.last_ddp_send = 0
        self.last_artwork = None
        self.last_health_check = 0
        self.owns_display = False
        self.current_player = None
        self.playback_active = False
        self.pause_until = None
        self.cover = None
        self.cover_url = None
        self.last_chunks = None
        self.track_key = None
        self.scroll_started = time.monotonic()
        self.stopping = asyncio.Event()
        self.ma_url = options.get("music_assistant_url", "").rstrip("/")
        self.ma_token = options.get("music_assistant_token", "")
        self.artist_image_count = int(options.get("artist_image_count", 3))
        if not 1 <= self.artist_image_count <= 3:
            raise ValueError("artist_image_count must be between 1 and 3")
        self.artist_enabled = options.get("show_artist_images", False)
        if self.artist_enabled and self.ma_url:
            if urlsplit(self.ma_url).scheme not in ("http", "https") or not urlsplit(self.ma_url).hostname:
                raise ValueError("music_assistant_url must be an HTTP or HTTPS server URL")
        self.artist_key = ""
        self.artist_images = ()
        self.artist_cache = OrderedDict()
        self.artist_task = None

    async def get_json(self, url, headers=None):
        async with self.session.get(url, headers=headers) as response:
            response.raise_for_status()
            return await response.json()

    async def post_state(self, payload):
        async with self.session.post(
            self.display_url + "/json/state", json=payload
        ) as response:
            response.raise_for_status()
            result = await response.json()
        if result.get("success") is not True:
            raise RuntimeError("WLED rejected the update")

    async def fetch_artwork(self, picture):
        if picture.startswith("/api/"):
            url = self.ha_url + picture[4:]
            headers = self.ha_headers
        elif urlsplit(picture).scheme in ("http", "https"):
            url = picture
            headers = None
        else:
            raise ValueError("Unsupported artwork URL")
        return await self.fetch_image(url, headers)

    async def fetch_image(self, url, headers=None):
        image_bytes = bytearray()
        async with self.session.get(url, headers=headers) as response:
            response.raise_for_status()
            async for chunk in response.content.iter_chunked(65536):
                image_bytes.extend(chunk)
                if len(image_bytes) > MAX_IMAGE_BYTES:
                    raise ValueError("Artwork exceeds 10 MiB")
        return bytes(image_bytes)

    async def ma_command(self, command, args):
        headers = {"Authorization": f"Bearer {self.ma_token}"}
        async with self.session.post(self.ma_url + "/api", headers=headers,
                                     json={"message_id": "artist", "command": command,
                                           "args": args}) as response:
            response.raise_for_status()
            result = await response.json()
        if isinstance(result, dict) and (result.get("error") or result.get("error_code")):
            raise RuntimeError("Music Assistant rejected the artist lookup")
        return result.get("result", result) if isinstance(result, dict) else result

    async def load_artist_images(self, name, key):
        images = []
        try:
            async with asyncio.timeout(45):
                artists = await self.ma_command("music/artists/library_items",
                                                {"search": name, "limit": 25})
                matches = [artist for artist in artists if isinstance(artist, dict)
                           and " ".join(str(artist.get("name", "")).split()).casefold() == key]
                if len(matches) == 1:
                    artist = matches[0]
                    artist = await self.ma_command("music/artists/get", {
                        "item_id": str(artist["item_id"]),
                        "provider_instance_id_or_domain": artist.get("provider", "library"),
                    })
                    for url, authenticated in artist_image_sources(artist, self.ma_url):
                        try:
                            headers = ({"Authorization": f"Bearer {self.ma_token}"}
                                       if authenticated else None)
                            data = await self.fetch_image(url, headers)
                            image = await asyncio.to_thread(load_cover, data)
                            if all(image.tobytes() != previous.tobytes() for previous in images):
                                images.append(image)
                            if len(images) >= self.artist_image_count:
                                break
                        except (aiohttp.ClientError, OSError, ValueError,
                                Image.DecompressionBombError):
                            continue
        except (aiohttp.ClientError, TimeoutError, OSError, ValueError, RuntimeError,
                TypeError, KeyError) as error:
            LOGGER.warning("Artist artwork unavailable (%s); keeping other pages", type(error).__name__)
        self.artist_cache[key] = (time.monotonic() + (3600 if images else 300), tuple(images))
        self.artist_cache.move_to_end(key)
        while len(self.artist_cache) > 32:
            self.artist_cache.popitem(last=False)
        if key == self.artist_key:
            self.artist_images = tuple(images)
            if images:
                self.scroll_started = time.monotonic()
                LOGGER.info("Loaded %s artist photo pages", len(images))

    async def update_artist_images(self, player):
        name = " ".join(str(player.get("attributes", {}).get("media_artist") or "").split())
        key = name.casefold()
        if key != self.artist_key:
            if self.artist_task is not None:
                self.artist_task.cancel()
                with suppress(asyncio.CancelledError):
                    await self.artist_task
                self.artist_task = None
            self.artist_key = key
            self.artist_images = ()
        if not (self.artist_enabled and self.ma_url and self.ma_token and key):
            return
        cached = self.artist_cache.get(key)
        if cached is not None and cached[0] > time.monotonic():
            self.artist_cache.move_to_end(key)
            self.artist_images = cached[1]
        elif self.artist_task is None or self.artist_task.done():
            self.artist_task = asyncio.create_task(self.load_artist_images(name, key))

    async def display_artwork(self, picture, player=None):
        if picture != self.cover_url:
            image_bytes = await self.fetch_artwork(picture)
            self.cover = await asyncio.to_thread(load_cover, image_bytes)
            self.cover_url = picture
        frame = self.cover
        if player is not None:
            await self.update_artist_images(player)
        if player is not None and (self.options.get("show_track_info", True)
                                   or self.audio_sync is not None or self.artist_images):
            frame = await asyncio.to_thread(
                render_frame, self.cover, player, time.monotonic() - self.scroll_started,
                page_interval=self.options.get("page_interval", 8),
                equalizer=self.audio_sync.spectrum() if self.audio_sync is not None else None,
                show_track_info=self.options.get("show_track_info", True),
                artist_images=self.artist_images,
            )
        missing_frame = (self.last_ddp_frame is None if self.output_protocol == "ddp"
                         else self.last_chunks is None)
        full_update = missing_frame or picture != self.last_artwork
        await self.display_frame(frame, full_update)

    async def validate_ddp_settings(self):
        config = await self.get_json(self.display_url + "/json/cfg")
        live = config.get("if", {}).get("live", {})
        if live.get("maxbri") is not False:
            raise ValueError("DDP requires disabling Force max brightness in WLED Sync Interfaces")
        if not live.get("en") or not live.get("rlm") or live.get("offset", 0) != 0:
            raise ValueError("DDP requires realtime reception, LED mapping, and a zero LED offset in WLED")
        if live.get("timeout", 0) < 10:
            raise ValueError("DDP requires a WLED realtime timeout of at least one second")

    def close_ddp_socket(self):
        if self.ddp_socket is not None:
            self.ddp_socket.close()
            self.ddp_socket = None
            self.ddp_address = None

    async def send_ddp_frame(self, rgb_data, force=False):
        async with self.ddp_lock:
            await self.send_ddp_packets(rgb_data, force)

    async def send_ddp_packets(self, rgb_data, force):
        if (not force and rgb_data == self.last_ddp_frame
                and time.monotonic() - self.last_ddp_send < 0.5):
            return
        loop = asyncio.get_running_loop()
        if self.ddp_socket is None:
            addresses = await loop.getaddrinfo(urlsplit(self.display_url).hostname, DDP_PORT,
                                              type=socket.SOCK_DGRAM)
            family, sock_type, protocol, _, self.ddp_address = addresses[0]
            self.ddp_socket = socket.socket(family, sock_type, protocol)
            self.ddp_socket.setblocking(False)
        self.ddp_active = True
        try:
            for packet in ddp_packets(rgb_data):
                await loop.sock_sendto(self.ddp_socket, packet, self.ddp_address)
        except OSError:
            self.close_ddp_socket()
            self.last_ddp_send = 0
            raise
        self.last_ddp_frame = rgb_data
        self.last_ddp_send = time.monotonic()
        if self.ddp_heartbeat is None and not self.stopping.is_set():
            self.ddp_heartbeat = asyncio.create_task(self.run_ddp_heartbeat())

    async def run_ddp_heartbeat(self):
        while not self.stopping.is_set():
            try:
                await asyncio.wait_for(self.stopping.wait(), timeout=0.5)
            except TimeoutError:
                try:
                    await self.keep_ddp_alive()
                except (OSError, TimeoutError) as error:
                    LOGGER.warning("DDP keepalive failed (%s); retrying", type(error).__name__)

    async def stop_ddp(self):
        if self.ddp_heartbeat is not None:
            self.ddp_heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await self.ddp_heartbeat
            self.ddp_heartbeat = None
        self.close_ddp_socket()
        self.last_ddp_frame = None

    async def keep_ddp_alive(self):
        async with self.ddp_lock:
            if self.output_protocol == "ddp" and self.last_ddp_frame is not None:
                await self.send_ddp_packets(self.last_ddp_frame, force=False)

    async def turn_off_display(self):
        payload = {"on": False}
        if self.output_protocol == "ddp":
            await self.stop_ddp()
            payload.update(live=False, tt=0)
        await self.post_state(payload)
        self.ddp_active = False

    async def close(self):
        if self.artist_task is not None:
            self.artist_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.artist_task
            self.artist_task = None
        if self.audio_sync is not None:
            self.audio_sync.close()
        await self.stop_ddp()
        if self.ddp_active:
            await self.post_state({"live": False})
            self.ddp_active = False

    async def display_frame(self, frame, full_update):
        frame = await asyncio.to_thread(rotate_frame, frame, self.options.get("rotation", "0"))
        chunks = None
        if self.output_protocol == "http":
            chunks = await asyncio.to_thread(pixel_chunks, frame)
        async with asyncio.timeout(45):
            if full_update:
                info = await self.get_json(self.display_url + "/json/info")
                if info.get("leds", {}).get("matrix") != {"w": 64, "h": 64}:
                    raise ValueError("WLED must be configured as a 64x64 matrix")
                if self.output_protocol == "ddp":
                    await self.validate_ddp_settings()
                payload = {
                    "on": True, "tt": 0, "live": False,
                    "seg": [{
                        "id": 0, "on": True, "start": 0, "stop": 64,
                        "startY": 0, "stopY": 64, "grp": 1, "spc": 0, "of": 0,
                        "rev": False, "rY": False, "mi": False, "mY": False,
                        "tp": False, "m12": 0, "fx": 0,
                    }],
                }
                if self.output_protocol == "ddp":
                    payload["lor"] = 0
                    payload["seg"][0]["frz"] = False
                await self.post_state(payload)
            if self.output_protocol == "ddp":
                await self.send_ddp_frame(frame.convert("RGB").tobytes(), force=full_update)
            else:
                for index, chunk in enumerate(chunks):
                    if full_update or chunk != self.last_chunks[index]:
                        await self.post_state(chunk)
        self.last_chunks = chunks
        self.owns_display = True
        if full_update:
            self.last_health_check = time.monotonic()

    async def display_is_healthy(self):
        state = await self.get_json(self.display_url + "/json/state")
        segment = next((segment for segment in state.get("seg", [])
                        if segment.get("id") == 0), {})
        self.last_health_check = time.monotonic()
        if self.output_protocol == "ddp":
            info = await self.get_json(self.display_url + "/json/info")
            return state.get("on") and segment.get("on") and info.get("live")
        return state.get("on") and segment.get("on") and segment.get("frz")

    async def update(self, poll=True):
        if poll:
            states = await self.get_json(self.ha_url + "/states", self.ha_headers)
            self.current_player = select_player(states, self.options.get("player_entities", []))
            self.playback_active = select_player(
                states, self.options.get("player_entities", []), require_picture=False
            ) is not None
            if not self.playback_active and self.audio_sync is not None:
                self.audio_sync.clear()
        if self.playback_active and self.pause_until is not None:
            self.pause_until = None
            self.last_chunks = None
            self.last_ddp_frame = None
        player = self.current_player
        if player is None:
            if self.playback_active:
                await self.keep_ddp_alive()
                return
            await self.update_artist_images({})
            self.last_artwork = None
            self.track_key = None
            if self.options.get("pause_then_off", True):
                if self.owns_display and self.pause_until is None:
                    await self.display_frame(render_paused_frame(), full_update=True)
                    self.pause_until = time.monotonic() + 60
                    LOGGER.info("Music paused; turning off WLED in 60 seconds")
                elif self.owns_display and time.monotonic() >= self.pause_until:
                    await self.turn_off_display()
                    self.owns_display = False
                    self.pause_until = None
                    self.last_chunks = None
                    LOGGER.info("Turned off WLED after music paused")
                elif self.owns_display:
                    await self.keep_ddp_alive()
            else:
                if self.options.get("idle_action", "keep") == "off" and self.owns_display:
                    await self.turn_off_display()
                    self.owns_display = False
                elif self.owns_display:
                    await self.keep_ddp_alive()
                self.last_chunks = None
            return
        picture = player["attributes"]["entity_picture"]
        attributes = player["attributes"]
        track_key = (player["entity_id"], picture, attributes.get("media_content_id"),
                     attributes.get("media_title"), attributes.get("media_artist"),
                     attributes.get("media_album_name"))
        if track_key != self.track_key:
            self.scroll_started = time.monotonic()
            self.track_key = track_key
            if self.audio_sync is not None:
                self.audio_sync.clear()
        if picture == self.last_artwork:
            healthy = True
            if time.monotonic() - self.last_health_check >= 30:
                healthy = await self.display_is_healthy()
                if not healthy:
                    self.last_chunks = None
                    self.last_ddp_frame = None
            cover_only = (not self.options.get("show_track_info", True)
                          and self.audio_sync is None and not self.artist_enabled)
            if healthy and cover_only:
                if self.output_protocol == "http" or self.last_ddp_frame is not None:
                    await self.keep_ddp_alive()
                    return
        await self.display_artwork(picture, player)
        if picture != self.last_artwork:
            LOGGER.info("Updated album artwork from %s", player["entity_id"])
        self.last_artwork = picture

    def render_interval(self):
        if self.output_protocol == "ddp" and self.owns_display:
            if self.current_player is not None and self.audio_sync is not None:
                return 0.05
            return (0.1 if self.current_player is not None
                    and self.options.get("show_track_info", True) else 0.5)
        if self.artist_enabled or self.options.get("show_track_info", True) or self.audio_sync is not None or self.pause_until is not None:
            return 1
        return self.options.get("refresh_interval", 5)

    async def run(self):
        try:
            if self.artist_enabled and not (self.ma_url and self.ma_token):
                LOGGER.warning("Artist pages need music_assistant_url and music_assistant_token; skipping photos")
            if self.audio_sync is not None:
                await self.audio_sync.start()
            await self.run_updates()
        finally:
            await self.close()

    async def run_updates(self):
        LOGGER.info("Watching Music Assistant; WLED target: %s (%s)",
                    self.display_url, self.output_protocol)
        next_poll = 0
        while not self.stopping.is_set():
            poll = (time.monotonic() >= next_poll
                    or (self.pause_until is not None
                        and time.monotonic() >= self.pause_until))
            succeeded = False
            try:
                await self.update(poll=poll)
                succeeded = True
            except aiohttp.ClientResponseError as error:
                LOGGER.warning("HTTP %s from %s; retrying", error.status,
                               error.request_info.real_url.host)
            except ValueError as error:
                LOGGER.warning("Invalid display configuration or artwork: %s; retrying", error)
            except (aiohttp.ClientError, TimeoutError, OSError,
                    RuntimeError, Image.DecompressionBombError) as error:
                LOGGER.warning("Artwork update failed (%s); retrying", type(error).__name__)
                LOGGER.debug("Failure details", exc_info=True)
            if poll:
                next_poll = time.monotonic() + self.options.get("refresh_interval", 5)
            try:
                interval = min(self.render_interval(), max(0.01, next_poll - time.monotonic()))
                if not succeeded:
                    interval = max(1, interval)
                await asyncio.wait_for(self.stopping.wait(),
                                       timeout=interval)
            except TimeoutError:
                pass


async def main():
    options = json.loads(Path("/data/options.json").read_text())
    logging.basicConfig(
        level=options.get("log_level", "info").upper(),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        raise RuntimeError("SUPERVISOR_TOKEN is missing; run as a Home Assistant app")
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        bridge = ArtworkBridge(session, options, token)
        for signal_number in (signal.SIGTERM, signal.SIGINT):
            asyncio.get_running_loop().add_signal_handler(signal_number, bridge.stopping.set)
        await bridge.run()


if __name__ == "__main__":
    asyncio.run(main())