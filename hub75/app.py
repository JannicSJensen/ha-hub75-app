"""Music Assistant artwork bridge for Home Assistant and WLED HUB75."""

import asyncio
import io
import json
import logging
import os
import signal
import time
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp
from PIL import Image, ImageOps

LOGGER = logging.getLogger("hub75")
MAX_IMAGE_BYTES = 10 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 16 * 1024 * 1024


def select_player(states, allowed_entities):
    candidates = [
        state for state in states
        if state["entity_id"].startswith("media_player.")
        and state.get("state") == "playing"
        and state.get("attributes", {}).get("app_id") == "music_assistant"
        and state["attributes"].get("entity_picture")
        and (not allowed_entities or state["entity_id"] in allowed_entities)
    ]
    return max(candidates, key=lambda state: (
        state.get("last_changed", ""), state["entity_id"]
    ), default=None)


def pixel_chunks(image_bytes):
    with Image.open(io.BytesIO(image_bytes)) as source:
        image = ImageOps.exif_transpose(source).convert("RGBA")
        image = ImageOps.pad(image, (64, 64), Image.Resampling.LANCZOS)
        background = Image.new("RGBA", image.size, (0, 0, 0, 255))
        background.alpha_composite(image)
        colors = ["%02x%02x%02x" % pixel
                  for pixel in background.convert("RGB").getdata()]
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

    async def display_artwork(self, picture):
        image_bytes = await self.fetch_artwork(picture)
        chunks = await asyncio.to_thread(pixel_chunks, image_bytes)
        async with asyncio.timeout(45):
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
            for chunk in chunks:
                await self.post_state(chunk)
        self.owns_display = True
        self.last_health_check = time.monotonic()

    async def display_is_healthy(self):
        state = await self.get_json(self.display_url + "/json/state")
        segment = next((segment for segment in state.get("seg", [])
                        if segment.get("id") == 0), {})
        self.last_health_check = time.monotonic()
        return state.get("on") and segment.get("on") and segment.get("frz")

    async def update(self):
        states = await self.get_json(self.ha_url + "/states", self.ha_headers)
        player = select_player(states, self.options.get("player_entities", []))
        if player is None:
            if self.options.get("idle_action", "keep") == "off" and self.owns_display:
                await self.post_state({"on": False})
                self.owns_display = False
            self.last_artwork = None
            return
        picture = player["attributes"]["entity_picture"]
        if picture == self.last_artwork:
            if time.monotonic() - self.last_health_check < 30:
                return
            if await self.display_is_healthy():
                return
        await self.display_artwork(picture)
        self.last_artwork = picture
        LOGGER.info("Updated album artwork from %s", player["entity_id"])

    async def run(self):
        LOGGER.info("Watching Music Assistant; WLED target: %s", self.display_url)
        while not self.stopping.is_set():
            try:
                await self.update()
            except aiohttp.ClientResponseError as error:
                LOGGER.warning("HTTP %s from %s; retrying", error.status,
                               error.request_info.real_url.host)
            except (aiohttp.ClientError, TimeoutError, OSError, ValueError,
                    RuntimeError, Image.DecompressionBombError) as error:
                LOGGER.warning("Artwork update failed (%s); retrying", type(error).__name__)
                LOGGER.debug("Failure details", exc_info=True)
            try:
                await asyncio.wait_for(self.stopping.wait(),
                                       timeout=self.options.get("refresh_interval", 5))
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