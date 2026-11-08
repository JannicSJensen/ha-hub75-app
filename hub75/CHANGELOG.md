# Changelog

## 0.8.1

- Add a GitHub documentation gallery covering album artwork, three artist images, track information, equalizer, and the pause screen.
- Include an animated equalizer example and previews in the HA app README and documentation.
- Generate previews with the actual screen renderer; document sample data, public-domain image sources, and regeneration steps.
- No changes to playback or display behavior.

## 0.8.0

- Add optional artist-photo pages between the album cover and track-information page.
- Configure one to three photos with `artist_image_count`; include only distinct, available images.
- Fetch artist artwork from the Music Assistant library through optional MA URL/token settings.
- Load photos in the background, cache them per artist, and skip failed or ambiguous lookups.
- Support public artwork and authenticated MA image proxies without sharing HA/MA tokens with external hosts.
- Retain track information, equalizer, rotation, countdown, and pause-then-off behavior.

## 0.7.0

- Add an optional 16-band equalizer page using Music Assistant's WLED Audio Sync provider.
- Receive version-2 Audio Sync packets on multicast 239.0.0.1 with a configurable zone port and interface.
- Add `show_equalizer` (disabled by default), `audio_sync_port` (11988), and `audio_sync_interface`.
- Rotate between cover, track information, and equalizer; support cover/equalizer mode without track information.
- Render equalizer-enabled DDP playback at up to 20 FPS, retaining HTTP support at a slower rate.
- Clear stale spectrum data after two seconds and retain the pause-then-off behavior.
- Enable host networking for multicast reception; open the Audio Sync listener only when equalizer is enabled.
- Document Music Assistant zone grouping, latency limits, and separation from DDP pixel streaming.

## 0.6.0

- Add an `output_protocol` dropdown: HTTP JSON (default) or native DDP over UDP port 4048.
- Render DDP track-information pages at up to ten frames per second for smoother scrolling.
- Resend static frames every half second, independently of HA requests.
- Preserve rotation, page flipping, cover caching, and the one-minute pause screen in both modes.
- Stop DDP streaming and release realtime mode before powering WLED off or stopping the app.
- Validate WLED realtime reception, LED mapping, timeout, and brightness settings without changing saved configuration.
- Test packet layout, real local UDP delivery, concurrent resends, and transport failure recovery.

## 0.5.0

- Add a configuration dropdown for clockwise rotation: 0, 45, 90, 135, or 180 degrees.
- Apply rotation consistently to album covers, track information, and the pause screen.
- Fit diagonal rotations inside the 64x64 panel with black padding instead of cropping.

## 0.4.0

- Show a centered "music paused" screen for 60 seconds when all selected players stop.
- Turn WLED off after the pause minute and cancel shutdown if music resumes.
- Restore the playing display automatically when music resumes after shutdown.
- Add `pause_then_off`, enabled by default; disabling it retains `idle_action`.
- Retry failed pause uploads and shutdown commands without prematurely starting or resetting the timer.
- Avoid triggering pause behavior during temporary artwork loss or idle app startup.

## 0.3.0

- Alternate between a full-screen album cover and a separate track-information page.
- Enlarge the live countdown, song title, artist, and album on the information page.
- Add `page_interval` to configure each page's duration, defaulting to eight seconds.
- Start new songs on the cover page and continue long-text scrolling across later pages.
- Reuse cached artwork during page flips and preserve cover-only mode.

## 0.2.0

- Display the song title, artist, and album beneath a compact album cover.
- Add a one-second countdown based on HA playback position and its timestamp.
- Scroll long metadata lines and show an unknown-time indicator for live streams.
- Upload only changed pixel chunks and reuse decoded artwork during timer updates.
- Add `show_track_info` to switch back to the original full-cover layout.

## 0.1.0

- Standalone Home Assistant app for WLED 64x64 HUB75 album artwork.
- Automatic Music Assistant player selection and optional player restrictions.
- Supervisor-authenticated artwork access with no user-managed credentials.
- Sequential pixel uploads, unchanged-artwork caching, and display recovery.
- Configurable idle behavior and polling interval.