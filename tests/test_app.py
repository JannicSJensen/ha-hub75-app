import importlib.util
import io
import re
import unittest
from pathlib import Path

import aiohttp
import yaml
from aiohttp import web
from PIL import Image

SPEC = importlib.util.spec_from_file_location(
    "hub75_app", Path(__file__).resolve().parents[1] / "hub75" / "app.py"
)
APP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(APP)


def image_bytes(image):
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def player(entity_id="media_player.kitchen", changed="2026-11-08T12:00:00+00:00",
           state="playing", picture="/api/media_player_proxy/cover?token=picture-token"):
    return {"entity_id": entity_id, "last_changed": changed, "state": state,
            "attributes": {"app_id": "music_assistant", "entity_picture": picture}}


class ManifestTests(unittest.TestCase):
    def test_home_assistant_repository_and_app_manifest(self):
        root = Path(__file__).resolve().parents[1]
        repository = yaml.safe_load((root / "repository.yaml").read_text())
        config = yaml.safe_load((root / "hub75" / "config.yaml").read_text())
        self.assertEqual(repository["url"], config["url"])
        self.assertEqual(set(config["arch"]), {"amd64", "aarch64"})
        self.assertEqual(set(config["options"]), set(config["schema"]))
        self.assertTrue(config["homeassistant_api"])
        self.assertTrue(config["init"])
        self.assertNotIn("image", config)
        self.assertRegex(config["version"], r"^\d+\.\d+\.\d+$")
        changelog = (root / "hub75" / "CHANGELOG.md").read_text()
        self.assertIn("## " + config["version"], changelog)
        for name in ("Dockerfile", "requirements.txt", "app.py", "README.md", "DOCS.md"):
            self.assertTrue((root / "hub75" / name).is_file(), name)
        entity_schema = config["schema"]["player_entities"][0]
        self.assertTrue(entity_schema.startswith("match(") and entity_schema.endswith(")"))
        pattern = entity_schema[6:-1]
        self.assertIsNotNone(re.fullmatch(pattern, "media_player.sound_mad_01"))
        self.assertIsNone(re.fullmatch(pattern, "light.wall_01"))


class SelectionTests(unittest.TestCase):
    def test_latest_playing_music_assistant_player(self):
        old = player()
        new = player("media_player.bedroom", "2026-11-08T13:00:00+00:00")
        television = player("media_player.tv", "2026-11-08T14:00:00+00:00")
        television["attributes"]["app_id"] = "video"
        self.assertIs(APP.select_player([old, new, television], []), new)
        new["state"] = "idle"
        self.assertIs(APP.select_player([old, new], []), old)

    def test_restricted_players_and_missing_artwork(self):
        selected = player()
        self.assertIsNone(APP.select_player([selected], ["media_player.bedroom"]))
        self.assertIs(APP.select_player([selected], [selected["entity_id"]]), selected)
        selected["attributes"]["entity_picture"] = None
        self.assertIsNone(APP.select_player([selected], []))

    def test_pixels_are_row_major_in_bounded_chunks(self):
        image = Image.new("RGB", (64, 64), "black")
        image.putpixel((0, 0), (255, 0, 0))
        image.putpixel((63, 0), (0, 255, 0))
        image.putpixel((0, 1), (0, 0, 255))
        chunks = APP.pixel_chunks(image_bytes(image))
        self.assertEqual(len(chunks), 16)
        self.assertEqual(chunks[0]["seg"][0]["i"][1], "ff0000")
        self.assertEqual(chunks[0]["seg"][0]["i"][64], "00ff00")
        self.assertEqual(chunks[0]["seg"][0]["i"][65], "0000ff")
        self.assertEqual(chunks[-1]["seg"][0]["i"][0], 3840)
        self.assertTrue(all(len(chunk["seg"][0]["i"]) == 257 for chunk in chunks))

    def test_padding_and_transparency_are_black(self):
        image = Image.new("RGBA", (32, 64), (255, 0, 0, 128))
        colors = APP.pixel_chunks(image_bytes(image))[0]["seg"][0]["i"]
        self.assertEqual(colors[1], "000000")
        self.assertEqual(colors[33], "800000")


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.states = [player()]
        self.posts = []
        self.auth = []
        self.matrix = {"w": 64, "h": 64}
        self.healthy = True
        self.reject_pixels = False
        self.art = image_bytes(Image.new("RGB", (64, 64), (10, 20, 30)))
        server = web.Application()
        server.router.add_get("/core/api/states", self.get_states)
        server.router.add_get("/core/api/media_player_proxy/cover", self.get_art)
        server.router.add_get("/external-cover", self.get_art)
        server.router.add_get("/json/info", self.get_info)
        server.router.add_get("/json/state", self.get_state)
        server.router.add_post("/json/state", self.post_state)
        self.runner = web.AppRunner(server)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.base = f"http://127.0.0.1:{port}"
        self.session = aiohttp.ClientSession()
        self.bridge = APP.ArtworkBridge(self.session, {
            "display_url": self.base, "player_entities": [], "idle_action": "keep"
        }, "supervisor-token", self.base + "/core/api")

    async def asyncTearDown(self):
        await self.session.close()
        await self.runner.cleanup()

    async def get_states(self, request):
        self.assertEqual(request.headers.get("Authorization"), "Bearer supervisor-token")
        return web.json_response(self.states)

    async def get_art(self, request):
        self.auth.append(request.headers.get("Authorization"))
        return web.Response(body=self.art, content_type="image/png")

    async def get_info(self, request):
        return web.json_response({"leds": {"matrix": self.matrix}})

    async def get_state(self, request):
        return web.json_response({"on": self.healthy, "seg": [
            {"id": 0, "on": True, "frz": self.healthy}
        ]})

    async def post_state(self, request):
        self.assertNotIn("Authorization", request.headers)
        payload = await request.json()
        self.posts.append(payload)
        if self.reject_pixels and "i" in payload.get("seg", [{}])[0]:
            return web.json_response({"success": False})
        return web.json_response({"success": True})

    async def test_upload_then_skip_unchanged_artwork(self):
        await self.bridge.update()
        self.assertEqual(len(self.posts), 17)
        self.assertTrue(self.posts[0]["on"])
        self.assertNotIn("i", self.posts[0]["seg"][0])
        self.assertNotIn("bri", self.posts[0])
        self.assertEqual(self.auth, ["Bearer supervisor-token"])
        await self.bridge.update()
        self.assertEqual(len(self.posts), 17)
        self.bridge.last_health_check = 0
        await self.bridge.update()
        self.assertEqual(len(self.posts), 17)

    async def test_track_change_and_display_restart(self):
        await self.bridge.update()
        self.states[0]["attributes"]["entity_picture"] += "&cache=new"
        await self.bridge.update()
        self.assertEqual(len(self.posts), 34)
        self.bridge.last_health_check = 0
        self.healthy = False
        await self.bridge.update()
        self.assertEqual(len(self.posts), 51)

    async def test_external_artwork_never_receives_supervisor_token(self):
        self.states[0]["attributes"]["entity_picture"] = self.base + "/external-cover"
        await self.bridge.update()
        self.assertEqual(self.auth, [None])

    async def test_idle_keep_and_optional_off(self):
        await self.bridge.update()
        self.states = []
        await self.bridge.update()
        self.assertEqual(len(self.posts), 17)
        self.bridge.options["idle_action"] = "off"
        await self.bridge.update()
        self.assertEqual(self.posts[-1], {"on": False})
        await self.bridge.update()
        self.assertEqual(len(self.posts), 18)

    async def test_wrong_matrix_does_not_change_display(self):
        self.matrix = {"w": 32, "h": 32}
        with self.assertRaisesRegex(ValueError, "64x64"):
            await self.bridge.update()
        self.assertEqual(self.posts, [])

    async def test_rejected_upload_is_not_cached(self):
        self.reject_pixels = True
        with self.assertRaises(RuntimeError):
            await self.bridge.update()
        self.assertIsNone(self.bridge.last_artwork)
        self.reject_pixels = False
        await self.bridge.update()
        self.assertIsNotNone(self.bridge.last_artwork)

    async def test_oversize_image_does_not_change_display(self):
        self.art = b"x" * (APP.MAX_IMAGE_BYTES + 1)
        with self.assertRaisesRegex(ValueError, "10 MiB"):
            await self.bridge.update()
        self.assertEqual(self.posts, [])


if __name__ == "__main__":
    unittest.main()