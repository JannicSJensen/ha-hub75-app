import importlib.util
import io
import re
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

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
        self.assertTrue(config["options"]["show_track_info"])
        self.assertTrue(config["options"]["pause_then_off"])
        self.assertEqual(config["options"]["page_interval"], 8)
        self.assertEqual(config["options"]["rotation"], "0")
        self.assertEqual(config["schema"]["rotation"], "list(0|45|90|135|180)")
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


class TrackInfoTests(unittest.TestCase):
    def test_countdown_uses_position_timestamp_and_clamps_at_zero(self):
        selected = player()
        selected["attributes"].update(media_duration=240, media_position=60,
                                      media_position_updated_at="2026-11-08T12:00:00Z")
        now = datetime(2026, 11, 8, 12, 0, 10, tzinfo=timezone.utc)
        self.assertEqual(APP.remaining_time(selected, now), "-2:50")
        selected["attributes"]["media_position"] = 300
        self.assertEqual(APP.remaining_time(selected, now), "-0:00")
        selected["state"] = "paused"
        selected["attributes"]["media_position"] = 60
        self.assertEqual(APP.remaining_time(selected, now), "-3:00")

    def test_unknown_duration_and_long_tracks(self):
        selected = player()
        self.assertEqual(APP.remaining_time(selected), "--:--")
        for duration in (None, "unavailable", float("nan"), float("inf"), 0):
            selected["attributes"]["media_duration"] = duration
            self.assertEqual(APP.remaining_time(selected), "--:--")
        selected["attributes"].update(media_duration=3661, media_position=0)
        self.assertEqual(APP.remaining_time(selected), "-1:01:01")

    def test_cover_and_information_pages_flip_on_interval_boundaries(self):
        selected = player()
        cover = Image.new("RGB", (64, 64), "red")
        for seconds in (0, 7.99, 16, 23.99):
            self.assertEqual(APP.render_frame(cover, selected, seconds).tobytes(),
                             cover.tobytes())
        for seconds in (8, 15.99, 24):
            self.assertNotEqual(APP.render_frame(cover, selected, seconds).tobytes(),
                                cover.tobytes())
        self.assertNotEqual(APP.render_frame(cover, selected, 3, page_interval=3).tobytes(),
                            cover.tobytes())
        self.assertEqual(APP.render_frame(cover, selected, 6, page_interval=3).tobytes(),
                         cover.tobytes())

    def test_all_four_information_rows_fit_panel(self):
        selected = player()
        selected["attributes"].update(media_title="Paradise", media_artist="Coldplay",
                                      media_album_name="Mylo Xyloto", media_duration=240)
        frame = APP.render_frame(Image.new("RGB", (64, 64), "red"), selected, 8)
        self.assertEqual(frame.size, (64, 64))
        self.assertIsNotNone(frame.crop((0, 0, 64, 20)).getbbox())
        for top in (22, 36, 50):
            self.assertIsNotNone(frame.crop((0, top, 64, top + 14)).getbbox())

    def test_long_text_scrolls_without_touching_timer(self):
        selected = player()
        selected["attributes"].update(media_title="A very long song title beyond the panel",
                                      media_artist="Artist", media_album_name="Album")
        cover = Image.new("RGB", (64, 64), "red")
        first = APP.render_frame(cover, selected, 8)
        later = APP.render_frame(cover, selected, 13)
        self.assertNotEqual(first.crop((0, 22, 64, 36)).tobytes(),
                            later.crop((0, 22, 64, 36)).tobytes())
        self.assertEqual(first.crop((0, 0, 64, 20)).tobytes(),
                         later.crop((0, 0, 64, 20)).tobytes())

    def test_hour_countdown_fits_without_entering_text_rows(self):
        selected = player()
        selected["attributes"].update(media_duration=3661)
        frame = APP.render_frame(Image.new("RGB", (64, 64), "red"), selected, 8)
        self.assertIsNotNone(frame.crop((0, 0, 64, 20)).getbbox())
        self.assertIsNone(frame.crop((0, 20, 64, 22)).getbbox())

    def test_long_text_continues_scrolling_on_later_information_pages(self):
        selected = player()
        selected["attributes"]["media_title"] = "A very long song title beyond the panel"
        cover = Image.new("RGB", (64, 64), "red")
        first = APP.render_frame(cover, selected, 8)
        second = APP.render_frame(cover, selected, 24)
        self.assertNotEqual(first.crop((0, 22, 64, 36)).tobytes(),
                            second.crop((0, 22, 64, 36)).tobytes())


    def test_pause_message_fits_on_two_centered_lines(self):
        frame = APP.render_paused_frame()
        self.assertEqual(frame.size, (64, 64))
        self.assertIsNone(frame.crop((0, 0, 64, 19)).getbbox())
        self.assertIsNotNone(frame.crop((0, 19, 64, 34)).getbbox())
        self.assertIsNotNone(frame.crop((0, 35, 64, 50)).getbbox())
        self.assertIsNone(frame.crop((0, 50, 64, 64)).getbbox())


class RotationTests(unittest.TestCase):
    def test_zero_degrees_preserves_pixels(self):
        frame = Image.new("RGB", (64, 64), "red")
        self.assertEqual(APP.rotate_frame(frame, "0").tobytes(), frame.tobytes())

    def test_clockwise_rotation_preserves_right_angle_pixels(self):
        frame = Image.new("RGB", (64, 64), "black")
        frame.putpixel((0, 0), (255, 0, 0))
        frame.putpixel((63, 0), (0, 255, 0))
        rotated = APP.rotate_frame(frame, "90")
        self.assertEqual(rotated.getpixel((63, 0)), (255, 0, 0))
        self.assertEqual(rotated.getpixel((63, 63)), (0, 255, 0))
        rotated = APP.rotate_frame(frame, "180")
        self.assertEqual(rotated.getpixel((63, 63)), (255, 0, 0))
        self.assertEqual(rotated.getpixel((0, 63)), (0, 255, 0))

    def test_diagonal_rotations_fit_panel_with_black_corners(self):
        frame = Image.new("RGB", (64, 64), "white")
        for angle in ("45", "135"):
            rotated = APP.rotate_frame(frame, angle)
            self.assertEqual(rotated.size, (64, 64))
            self.assertEqual(rotated.getpixel((32, 32)), (255, 255, 255))
            for corner in ((0, 0), (63, 0), (0, 63), (63, 63)):
                self.assertEqual(rotated.getpixel(corner), (0, 0, 0))

    def test_unsupported_rotation_is_rejected(self):
        with self.assertRaises(ValueError):
            APP.rotate_frame(Image.new("RGB", (64, 64)), "30")


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
            "display_url": self.base, "player_entities": [], "idle_action": "keep",
            "show_track_info": False,
            "pause_then_off": False,
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

    async def test_countdown_updates_only_changed_chunks_without_fetching_art(self):
        self.bridge.options["show_track_info"] = True
        self.states[0]["attributes"].update(media_title="Song", media_artist="Artist",
                                            media_album_name="Album", media_duration=240)
        with patch.object(APP, "remaining_time", return_value="-3:00"):
            await self.bridge.update()
        self.assertEqual(len(self.posts), 17)
        self.bridge.scroll_started -= 8
        with patch.object(APP, "remaining_time", return_value="-3:00"):
            await self.bridge.update(poll=False)
        self.posts.clear()
        with patch.object(APP, "remaining_time", return_value="-2:59"):
            await self.bridge.update(poll=False)
        self.assertGreater(len(self.posts), 0)
        self.assertLess(len(self.posts), 16)
        self.assertTrue(all(payload["seg"][0]["i"][0] < 1280 for payload in self.posts))
        self.assertEqual(len(self.auth), 1)

    async def test_new_song_on_same_album_updates_metadata_without_redownload(self):
        self.bridge.options["show_track_info"] = True
        self.states[0]["attributes"]["media_title"] = "First song"
        await self.bridge.update()
        self.bridge.scroll_started -= 8
        await self.bridge.update(poll=False)
        self.posts.clear()
        self.states[0]["attributes"]["media_title"] = "Second song"
        await self.bridge.update()
        self.assertGreater(len(self.posts), 0)
        self.assertEqual(len(self.auth), 1)
        self.assertEqual(self.bridge.last_chunks,
                         APP.pixel_chunks(APP.load_cover(self.art)))

    async def test_page_flips_do_not_redownload_cover(self):
        self.bridge.options["show_track_info"] = True
        await self.bridge.update()
        cover_chunks = self.bridge.last_chunks
        self.posts.clear()
        await self.bridge.update(poll=False)
        self.assertEqual(self.posts, [])
        self.bridge.scroll_started -= 8
        await self.bridge.update(poll=False)
        self.assertNotEqual(self.bridge.last_chunks, cover_chunks)
        self.bridge.scroll_started -= 8
        await self.bridge.update(poll=False)
        self.assertEqual(self.bridge.last_chunks, cover_chunks)
        self.assertEqual(len(self.auth), 1)

    async def test_pause_screen_stays_for_one_minute_then_switches_off_once(self):
        self.bridge.options["pause_then_off"] = True
        await self.bridge.update()
        self.states[0]["state"] = "paused"
        self.posts.clear()
        await self.bridge.update()
        self.assertEqual(self.bridge.last_chunks, APP.pixel_chunks(APP.render_paused_frame()))
        self.assertEqual(len(self.posts), 17)
        deadline = self.bridge.pause_until
        self.posts.clear()
        await self.bridge.update(poll=False)
        self.assertEqual(self.posts, [])
        self.assertEqual(self.bridge.pause_until, deadline)
        self.bridge.pause_until -= 61
        await self.bridge.update()
        self.assertEqual(self.posts, [{"on": False}])
        await self.bridge.update()
        self.assertEqual(self.posts, [{"on": False}])

    async def test_resume_cancels_shutdown_and_restores_cached_cover(self):
        self.bridge.options["pause_then_off"] = True
        await self.bridge.update()
        self.states[0]["state"] = "idle"
        await self.bridge.update()
        self.assertIsNotNone(self.bridge.pause_until)
        self.states[0]["state"] = "playing"
        self.posts.clear()
        await self.bridge.update()
        self.assertIsNone(self.bridge.pause_until)
        self.assertTrue(self.posts[0]["on"])
        self.assertEqual(self.bridge.last_chunks, APP.pixel_chunks(APP.load_cover(self.art)))
        self.assertEqual(len(self.auth), 1)

    async def test_resume_after_shutdown_turns_display_on(self):
        self.bridge.options["pause_then_off"] = True
        await self.bridge.update()
        self.states[0]["state"] = "idle"
        await self.bridge.update()
        self.bridge.pause_until -= 61
        await self.bridge.update()
        self.assertFalse(self.bridge.owns_display)
        self.states[0]["state"] = "playing"
        self.posts.clear()
        await self.bridge.update()
        self.assertTrue(self.posts[0]["on"])
        self.assertTrue(self.bridge.owns_display)

    async def test_other_playing_player_does_not_trigger_pause(self):
        self.bridge.options["pause_then_off"] = True
        await self.bridge.update()
        self.states[0]["state"] = "idle"
        self.states.append(player("media_player.bedroom"))
        await self.bridge.update()
        self.assertIsNone(self.bridge.pause_until)

    async def test_playing_without_artwork_does_not_trigger_pause(self):
        self.bridge.options["pause_then_off"] = True
        await self.bridge.update()
        self.states[0]["attributes"]["entity_picture"] = None
        self.posts.clear()
        await self.bridge.update()
        self.assertEqual(self.posts, [])
        self.assertIsNone(self.bridge.pause_until)

    async def test_starting_idle_does_not_take_over_display(self):
        self.bridge.options["pause_then_off"] = True
        self.states = []
        await self.bridge.update()
        self.assertEqual(self.posts, [])

    async def test_failed_pause_upload_is_retried_before_timer_starts(self):
        self.bridge.options["pause_then_off"] = True
        await self.bridge.update()
        self.states = []
        self.reject_pixels = True
        with self.assertRaises(RuntimeError):
            await self.bridge.update()
        self.assertIsNone(self.bridge.pause_until)
        self.reject_pixels = False
        await self.bridge.update()
        self.assertIsNotNone(self.bridge.pause_until)

    async def test_failed_shutdown_retries_without_restarting_pause_minute(self):
        self.bridge.options["pause_then_off"] = True
        await self.bridge.update()
        self.states = []
        await self.bridge.update()
        self.bridge.pause_until -= 61
        deadline = self.bridge.pause_until
        with patch.object(self.bridge, "post_state", side_effect=RuntimeError("offline")):
            with self.assertRaises(RuntimeError):
                await self.bridge.update()
        self.assertTrue(self.bridge.owns_display)
        self.assertEqual(self.bridge.pause_until, deadline)
        await self.bridge.update()
        self.assertFalse(self.bridge.owns_display)
        self.assertIsNone(self.bridge.pause_until)

    async def test_rotation_applies_to_cover_information_and_pause_pages(self):
        self.bridge.options.update(rotation="90", show_track_info=True, pause_then_off=True)
        cover = Image.new("RGB", (64, 64), "black")
        cover.putpixel((0, 0), (255, 0, 0))
        self.art = image_bytes(cover)
        await self.bridge.update()
        self.assertEqual(self.bridge.last_chunks, APP.pixel_chunks(APP.rotate_frame(cover, "90")))
        self.bridge.scroll_started -= 8
        with patch.object(APP, "remaining_time", return_value="-3:00"):
            await self.bridge.update(poll=False)
            information = APP.render_frame(cover, self.states[0], 8)
        self.assertEqual(self.bridge.last_chunks,
                         APP.pixel_chunks(APP.rotate_frame(information, "90")))
        self.states = []
        await self.bridge.update()
        self.assertEqual(self.bridge.last_chunks,
                         APP.pixel_chunks(APP.rotate_frame(APP.render_paused_frame(), "90")))


if __name__ == "__main__":
    unittest.main()