# Changelog

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