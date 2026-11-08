"""Music Assistant artwork bridge for Home Assistant and WLED HUB75."""

import asyncio
import io
import json
import logging
import math
import os
import signal
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp
from PIL import Image, ImageDraw, ImageFont, ImageOps

LOGGER = logging.getLogger("hub75")
MAX_IMAGE_BYTES = 10 * 1024 * 1024
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


def render_frame(cover, player, scroll_seconds=0, now=None, page_interval=8):
    page_seconds = max(0, scroll_seconds) % (2 * page_interval)
    if page_seconds < page_interval:
        return cover.copy()
    completed_cycles = max(0, scroll_seconds) // (2 * page_interval)
    scroll_seconds = completed_cycles * page_interval + page_seconds - page_interval
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


def pixel_chunks(image_bytes):
    image = image_bytes if isinstance(image_bytes, Image.Image) else load_cover(image_bytes)
    colors = ["%02x%02x%02x" % pixel for pixel in image.convert("RGB").getdata()]
    return [
        {"seg": [{"id": 0, "i": [offset, *colors[offset:offset + 256]]}]}
        for offset in range(0, len(colors), 256)
    ]


class ArtworkBridge:
    def __init__(self, session, options, token, ha_url="http://supervisor/core/api"):
        self.session = session
        self.options = options
        self.ha_url = ha_url.rstrip("/")
        self.ha_headers = {"Authorization": f"Bearer {token}"}
        self.display_url = options["display_url"].rstrip("/")
        if urlsplit(self.display_url).scheme not in ("http", "https"):
            raise ValueError("display_url must use HTTP or HTTPS")
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
        image_bytes = bytearray()
        async with self.session.get(url, headers=headers) as response:
            response.raise_for_status()
            async for chunk in response.content.iter_chunked(65536):
                image_bytes.extend(chunk)
                if len(image_bytes) > MAX_IMAGE_BYTES:
                    raise ValueError("Artwork exceeds 10 MiB")
        return bytes(image_bytes)

    async def display_artwork(self, picture, player=None):
        if picture != self.cover_url:
            image_bytes = await self.fetch_artwork(picture)
            self.cover = await asyncio.to_thread(load_cover, image_bytes)
            self.cover_url = picture
        frame = self.cover
        if player is not None and self.options.get("show_track_info", True):
            frame = await asyncio.to_thread(
                render_frame, self.cover, player, time.monotonic() - self.scroll_started,
                page_interval=self.options.get("page_interval", 8),
            )
        full_update = self.last_chunks is None or picture != self.last_artwork
        await self.display_frame(frame, full_update)

    async def display_frame(self, frame, full_update):
        chunks = await asyncio.to_thread(pixel_chunks, frame)
        async with asyncio.timeout(45):
            if full_update:
                info = await self.get_json(self.display_url + "/json/info")
                if info.get("leds", {}).get("matrix") != {"w": 64, "h": 64}:
                    raise ValueError("WLED must be configured as a 64x64 matrix")
                await self.post_state({
                    "on": True, "tt": 0,
                    "seg": [{
                        "id": 0, "on": True, "start": 0, "stop": 64,
                        "startY": 0, "stopY": 64, "grp": 1, "spc": 0, "of": 0,
                        "rev": False, "rY": False, "mi": False, "mY": False,
                        "tp": False, "m12": 0, "fx": 0,
                    }],
                })
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
        return state.get("on") and segment.get("on") and segment.get("frz")

    async def update(self, poll=True):
        if poll:
            states = await self.get_json(self.ha_url + "/states", self.ha_headers)
            self.current_player = select_player(states, self.options.get("player_entities", []))
            self.playback_active = select_player(
                states, self.options.get("player_entities", []), require_picture=False
            ) is not None
        if self.playback_active and self.pause_until is not None:
            self.pause_until = None
            self.last_chunks = None
        player = self.current_player
        if player is None:
            if self.playback_active:
                return
            self.last_artwork = None
            self.track_key = None
            if self.options.get("pause_then_off", True):
                if self.owns_display and self.pause_until is None:
                    await self.display_frame(render_paused_frame(), full_update=True)
                    self.pause_until = time.monotonic() + 60
                    LOGGER.info("Music paused; turning off WLED in 60 seconds")
                elif self.owns_display and time.monotonic() >= self.pause_until:
                    await self.post_state({"on": False})
                    self.owns_display = False
                    self.pause_until = None
                    self.last_chunks = None
                    LOGGER.info("Turned off WLED after music paused")
            else:
                if self.options.get("idle_action", "keep") == "off" and self.owns_display:
                    await self.post_state({"on": False})
                    self.owns_display = False
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
        if picture == self.last_artwork:
            healthy = True
            if time.monotonic() - self.last_health_check >= 30:
                healthy = await self.display_is_healthy()
                if not healthy:
                    self.last_chunks = None
            if healthy and not self.options.get("show_track_info", True):
                return
        await self.display_artwork(picture, player)
        if picture != self.last_artwork:
            LOGGER.info("Updated album artwork from %s", player["entity_id"])
        self.last_artwork = picture

    async def run(self):
        LOGGER.info("Watching Music Assistant; WLED target: %s", self.display_url)
        next_poll = 0
        while not self.stopping.is_set():
            try:
                poll = (time.monotonic() >= next_poll
                        or (self.pause_until is not None
                            and time.monotonic() >= self.pause_until))
                await self.update(poll=poll)
                if poll:
                    next_poll = time.monotonic() + self.options.get("refresh_interval", 5)
            except aiohttp.ClientResponseError as error:
                LOGGER.warning("HTTP %s from %s; retrying", error.status,
                               error.request_info.real_url.host)
            except (aiohttp.ClientError, TimeoutError, OSError, ValueError,
                    RuntimeError, Image.DecompressionBombError) as error:
                LOGGER.warning("Artwork update failed (%s); retrying", type(error).__name__)
                LOGGER.debug("Failure details", exc_info=True)
            try:
                await asyncio.wait_for(self.stopping.wait(),
                                       timeout=1 if (self.options.get("show_track_info", True)
                                                     or self.pause_until is not None)
                                       else self.options.get("refresh_interval", 5))
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