# Architecture

The app is a single Python process in a container. It has no web UI, listening
ports, HA configuration mounts, or dependency on Dasher. Options are supplied
by Supervisor in `/data/options.json`.

## Data Flow

1. Poll `http://supervisor/core/api/states` using `SUPERVISOR_TOKEN`.
2. Select a playing `media_player` with `app_id: music_assistant` and artwork,
   optionally restricted by `player_entities`.
3. Choose the player with the most recent `last_changed` timestamp. This is the
   time it entered `playing`, not the time its volume or position last updated.
4. Download the `entity_picture`. Relative `/api/` pictures use the Supervisor
   proxy; absolute HTTP/HTTPS pictures are fetched without the Supervisor token.
5. Use Pillow to apply EXIF orientation, preserve aspect ratio, pad to 64x64,
   and composite transparency onto black. Cache the decoded cover. With
   `show_track_info`, alternate the full 64x64 cover with a separate information
   page. Use a large countdown above three fourteen-pixel metadata rows.
6. Verify WLED reports a 64x64 matrix. Turn it on and configure segment 0 in a
   separate request before writing pixels, preserving its existing brightness.
7. Upload 16 sequential 256-pixel JSON requests to WLED `/json/state` for an
   initial frame. Subsequent frames only upload chunks whose pixels changed.

WLED's individual-pixel control freezes segment 0. Pixels are row-major;
WLED handles physical matrix mapping. The app changes the current effect but
does not write presets or upload files to the controller's flash storage.

## Caching and Recovery

The artwork URL, decoded cover, and last successful frame's chunks are cached in
memory. Unchanged artwork is not downloaded again. With track information
enabled, a one-second render loop interpolates elapsed playback from HA's
position timestamp, updates the countdown, and scrolls long text. HA state
polling retains its independently configured interval. A track/player/metadata
change resets the page cycle and scroll position even when the cover URL is
unchanged. Each page is visible for `page_interval` seconds; the cover comes
first. Metadata scrolling counts time spent on information pages, continuing
across later cycles instead of repeatedly showing only the beginning of a line.

Full-cover mode skips unchanged frames entirely. Every 30 seconds, the app checks that WLED is
on and segment 0 is on and frozen. An unhealthy state triggers another upload.
Cache state resets when no eligible player remains or the app restarts.

Failed uploads are not cached. Network, HTTP, decoding, and oversized-image
errors are logged and retried on the next poll. Artwork downloads are limited
to 10 MiB; Pillow also limits decoded image dimensions. Each HTTP request has a
15-second timeout, and the WLED upload phase has a 45-second overall timeout.

The single polling loop serializes uploads. It does not parallelize WLED
requests. If the selected track changes during an upload, the next poll picks
up the new artwork.

## Permissions and Limitations

When no selected Music Assistant player is playing, a display previously taken
over by the app enters the pause state by default. A shared frame uploader writes
"music paused" and starts a monotonic 60-second deadline only after a successful
upload. Idle rendering checks the deadline at one-second intervals; HA is polled
again before shutdown to avoid switching off after playback has resumed. The
deadline is cleared after shutdown, returning to normal HA polling.

Playing resumes cancel the deadline and invalidate the old frame so the cover
is redrawn and WLED is turned on. Temporary artwork loss does not count as a
playback stop. The timer is in memory; app startup while idle does not take over
the display. `pause_then_off: false` preserves the legacy `idle_action` modes.

`homeassistant_api: true` grants HA administrator access through the Supervisor
proxy. The app only reads states and artwork, but the supplied credential must
still be treated as an administrator secret. It is never sent to WLED or to
external artwork servers. No Supervisor-management permissions are requested.

Only one 64x64 display is supported. Multiple rooms share it using the selection
rule above. Players without an artwork URL are skipped. A station logo may be
shown when Music Assistant supplies that as its current artwork.

For consistent results, use a single WLED segment and avoid other automations
controlling the display while this app is running. A health check only observes
WLED's power/freeze state, not an image checksum, so another frozen pixel source
cannot be distinguished from this app's own image.

## Tests

`tests/test_app.py` covers manifest structure and version/changelog consistency,
player selection, pixel ordering, padding, transparency, token isolation,
unchanged-artwork caching, restart recovery, idle behavior, rejected uploads,
incorrect matrix dimensions, oversized downloads, countdown extrapolation,
metadata layout, scrolling, page boundaries, configurable flip intervals,
new-song page resets, incremental frame uploads, the one-minute pause screen,
resume cancellation, shutdown retries, and idle startup. HTTP integration tests
use in-process aiohttp servers without HA credentials or real hardware.