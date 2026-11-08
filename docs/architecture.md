# Architecture

The app is a single Python process in a container. It has no web UI,
and optionally launches yt-dlp/Node.js and FFmpeg subprocesses for video. It has no
HA configuration mounts, or dependency on Dasher. Host networking allows an
optional UDP multicast listener for Audio Sync. Options are supplied
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
7. Apply the configured clockwise `rotation` to the complete frame in the
   shared uploader. Diagonal rotations expand and then fit within 64x64 with
   black padding. The same transformation applies to the pause screen.
8. Upload 16 sequential 256-pixel JSON requests to WLED `/json/state` for an
   initial HTTP frame. Subsequent HTTP frames only upload changed chunks.
   With `output_protocol: ddp`, send the RGB24 frame in nine UDP packets to
   port 4048 on the hostname from `display_url`; HTTP still handles setup/off.

WLED's HTTP individual-pixel control freezes segment 0; DDP uses realtime mode.
Pixels are row-major;
WLED handles physical matrix mapping. The app changes the current effect but
does not write presets or upload files to the controller's flash storage.

## Caching and Recovery

### YouTube Video

`show_video` is opt-in and requires DDP. `VideoPage` selects the latest playing
MA entity with artist/title independently of artwork availability. Its key
includes entity, media ID, artist, and title; changes cancel/await old work and
clear the current frame. Normal rendering still needs a selected cover player.

An asynchronous worker runs yt-dlp as a subprocess, using flat `ytsearch5`
metadata followed by full resolution of up to three conservative artist/title
and duration matches. Search metadata is capped at 2 MiB, each extraction at
45 seconds, and the lookup overall at 90 seconds. The 32-entry in-memory LRU
stores video IDs for 24 hours or negative results for five minutes. It does not
cache signed URLs; candidates are revalidated and re-resolved on worker start.
Only HTTPS streams under `*.googlevideo.com`, without URL credentials or
nonstandard ports, are accepted. Formats are direct HTTPS video at most 360p.

The container bundles yt-dlp with its matching EJS scripts and Node.js 22+;
`--js-runtimes node` enables the runtime without remote-component downloads.
Subprocesses receive only PATH, a temporary HOME, and LANG, not Supervisor/MA
tokens. Arguments are passed directly without a shell; yt-dlp ignores user
configuration and does not use cookies/login. Extractor stderr and signed URLs
are not logged. Only a narrow set of sanitized public HTTP headers is passed
to FFmpeg; authentication and cookie headers are excluded.

FFmpeg decodes one stream at a time, discards audio, scales/pads or crops to
64x64, and emits `rgb24` over stdout at 5-20 FPS. Decode/filter threads are
limited to one. The worker reads exactly 12,288 bytes per frame with a 15-second
timeout, paces against an extrapolated HA playback anchor plus `video_offset`,
drops late frames, and reseeks on timing discontinuities over two seconds.
Only the latest Pillow frame is retained. Stale frames (two seconds) and failed
streams disappear from the page cycle; errors back off five minutes and normal
EOF disables video until the track key changes. Music-video edits and speaker
latency make synchronization best-effort.

The shared renderer inserts video last, after cover/photos/info/EQ, and shared
rotation transforms the completed frame before DDP serialization. Background
HA polling in video mode prevents a slow state request from blocking animation.
Only one state request is outstanding; its result is consumed before scheduling
the next. Pause-timeout shutdown requires fresh HA state. Existing non-video
polling/upload behavior remains unchanged.

Track changes, pause, idle, and shutdown cancel/await the worker. Process
creation is shielded so cancellation still reaps a just-created process.
Shutdown drains stdout while terminating to avoid full-pipe deadlocks, then
kills/reaps a process that does not exit in two seconds. Optional video failure
does not change MA playback or interfere with the existing pause screen/off.
Queries expose artist/title and the public client IP to YouTube; unofficial
extraction is subject to service changes, access rights, and applicable terms.

### Artist Photos

`show_artist_images` enables optional artist-photo lookup through authenticated
Music Assistant HTTP commands. `music/artists/library_items` searches HA's artist
name, followed by `music/artists/get` only for a unique normalized exact match.
The library must contain the artist. No heuristic artist-name splitting, stock
search, or external fallback is used. `music_assistant_url` and the password-field
`music_assistant_token` are independent of HA's Supervisor credential.

A background task downloads up to `artist_image_count` distinct photos (maximum
three). Only thumb/fanart/landscape images are accepted. Public URLs receive no
credentials; proxy-ID and legacy path/provider imageproxy requests use only the
MA token. The existing streaming image-size limit, Pillow decoder, padding and
shared rotation apply. Failed images are skipped, and identical RGB frames are
deduplicated. A 45-second overall lookup timeout bounds optional work.

An LRU cache holds up to 32 artists, expiring successful results after one hour
and negative results after five minutes. A changed artist cancels/awaits the old
task and removes old photos; only results for the current artist become visible.
Photos arriving asynchronously restart the page cycle at the cover, avoiding
mid-cycle page-count jumps. Shutdown cancels/awaits the task before closing the
shared HTTP session. No artist request blocks normal frame rendering.

The shared renderer inserts available photos after the cover and before optional
information/equalizer pages. It counts actual pages, so unavailable photos do
not create blank or repeated pages. Metadata scrolling continues to count time
spent on information pages even with extra photo pages between visits.

### Playback Frames

The artwork URL, decoded cover, and last successful frame's chunks are cached in
memory. Unchanged artwork is not downloaded again. With track information
enabled, a render loop interpolates elapsed playback from HA's
position timestamp, updates the countdown, and scrolls long text. HA state
polling retains its independently configured interval. HTTP renders once per
second; DDP renders up to ten times per second (twenty with equalizer enabled) and sends only when the frame
changes or a keepalive is due. A track/player/metadata
change resets the page cycle and scroll position even when the cover URL is
unchanged. Each page is visible for `page_interval` seconds; the cover comes
first. Metadata scrolling counts time spent on information pages, continuing
across later cycles instead of repeatedly showing only the beginning of a line.

HTTP full-cover mode skips unchanged frames entirely. Every 30 seconds, the app checks that WLED is
on and segment 0 is on and frozen (HTTP), or realtime mode is active (DDP).
An unhealthy state triggers another upload.
Cache state resets when no eligible player remains or the app restarts.

DDP uses ten-byte network-order headers and at most 1440 bytes of RGB24 payload
per packet. Offsets are bytes, destination is display 1, and only the final
packet sets PUSH. Sequence zero intentionally disables sequence filtering;
WLED's late-packet heuristic assumes fewer packets than a 64x64 frame needs.
PUSH helps coordinate presentation but does not guarantee atomic display or
delivery. The latest successfully sent RGB buffer is cached; no UDP receipt is
acknowledged. A nonblocking socket and an asyncio lock serialize all packets
from foreground rendering and a half-second background resend task. Resends
read the cached frame under that lock, avoiding stale-frame races. The task
keeps static cover, idle-keep, and pause frames live even during slow HA requests.

Before full DDP initialization, read `/json/cfg` and require realtime reception,
LED mapping, zero offset, a timeout of at least one second, and disabled forced
maximum brightness. Saved configuration is never mutated. Initialization clears
realtime override and the old HTTP freeze. Shutdown cancels and awaits the
resend task, closes the socket, then sends `live: false` together with `on: false`.
Stopping the app releases realtime mode without powering off or persisting the
streamed frame; WLED returns to its underlying effect.

Failed uploads are not cached. Network, HTTP, decoding, and oversized-image
errors are logged and retried on the next poll. Artwork downloads are limited
to 10 MiB; Pillow also limits decoded image dimensions. Each HTTP request has a
15-second timeout, and the WLED upload phase has a 45-second overall timeout.

The single polling/render loop serializes HTTP uploads. It does not parallelize WLED
requests; the separate DDP keepalive task shares the packet-send lock. If the
selected track changes during an upload, the next poll picks
up the new artwork.

## Permissions and Limitations

### Audio Sync

`show_equalizer` creates an asyncio datagram receiver bound to the configured
`audio_sync_port`, joining multicast 239.0.0.1 on `audio_sync_interface`. The
manifest uses `host_network: true`; no listener opens when this feature is off.
Music Assistant's merged WLED Audio Sync provider sends 40 Hz feature packets
for a virtual zone grouped with a real speaker. Its v2 wire format is 44 bytes,
little-endian `<6s2sffBB16sHff>`, with header `00002\0` and sixteen FFT bytes.
Wrong lengths/versions and nonfinite float fields are ignored. The app stores
only the latest spectrum and its monotonic receipt time, clearing bands after
two seconds without valid input or when playback/track state changes.

The equalizer is an optional third page, or the second page when track
information is disabled. Existing page intervals, scrolling, rotation and pause
behavior are shared. It uses already-extracted MA bands rather than performing
FFT or capturing audio. No WLED Audio Reactive usermod is required: received
features are converted into RGB pixels and uploaded through the chosen output
transport. Audio Sync reception does not wake an idle display by itself.

Packets have neither identity nor timestamps. Zone selection must match the
HA player restriction; timing is best-effort and MA's latency compensation
must be tuned for the speakers. Network and HA-request delays can stall frame
updates. Multicast is unauthenticated and should remain on a trusted LAN.
Closing the app releases the receiver transport and multicast membership.

### Home Assistant Access

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
resume cancellation, shutdown retries, idle startup, rotation pixel ordering,
diagonal fitting, and consistent rotation of all pages. DDP tests validate packet
headers and reassembly, real local UDP delivery, keepalive without HA updates,
stale-frame prevention, safe brightness rejection, send and off retries,
realtime recovery, and no packets after shutdown. HTTP integration tests use
in-process aiohttp servers without HA credentials or real hardware.

Audio Sync tests cover exact MA/WLED packet decoding, invalid packet rejection,
real loopback multicast reception, silence and stale-data clearing, equalizer
page order, pixel bounds, both transports, rotation, track resets, pause
overrides, idle startup, opt-in receiver lifecycle, and manifest network/options.

Artist-page tests cover page order and limits, MA command shapes, public/proxy
credential isolation, duplicate images, failed/ambiguous/missing library matches,
negative-cache expiry, artist changes, DDP rotation, unchanged-cover reuse, slow
background requests, and cancellation at shutdown.

Video tests cover conservative matches, page order/fallback, ID-only cache and
fresh URL resolution, unsafe stream rejection, subprocess credential isolation,
bounded metadata, spawn cancellation, terminate/kill/reap, HA position anchors,
configuration validation, actual FFmpeg moving-frame decoding with both fit
modes, DDP rotation, stale frames, pause cleanup, fresh-state shutdown safety,
and animation during slow HA polls. Generated test media is served locally;
tests do not call YouTube or require a physical panel.