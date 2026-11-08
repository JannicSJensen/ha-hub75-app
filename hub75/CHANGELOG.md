# Changelog

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