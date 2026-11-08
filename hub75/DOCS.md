# Configuration

```yaml
display_url: http://192.168.2.214
player_entities: []
idle_action: keep
pause_then_off: true
show_track_info: true
page_interval: 8
refresh_interval: 5
log_level: info
```

| Option | Meaning |
| --- | --- |
| `display_url` | WLED controller base URL. Default is your wall_01 controller. |
| `player_entities` | Optional list of Music Assistant HA media-player IDs. Empty means all MA players. |
| `idle_action` | Used only when `pause_then_off` is `false`: `keep` leaves the last frame visible; `off` turns WLED off immediately. |
| `pause_then_off` | Show "music paused" for 60 seconds after playback stops, then switch WLED off. Default: `true`. Overrides `idle_action`. |
| `show_track_info` | Alternate cover and track-information pages. Set `false` for an uninterrupted full-cover image. Default: `true`. |
| `page_interval` | Seconds each page remains visible, from 3 to 60. Default: 8. Ignored when `show_track_info` is `false`. |
| `refresh_interval` | Seconds between HA checks, from 1 to 60. Default: 5. |
| `log_level` | `debug`, `info`, `warning`, or `error`. Default: `info`. |

Save options and restart the app to apply changes. Home Assistant Core does not
need a restart. The app uses its automatically supplied Supervisor token.

## Choosing Players

Use the entity created by the **Music Assistant integration**, not the underlying
Cast, AirPlay, or speaker entity. Music Assistant entities expose
`app_id: music_assistant`, `state: playing`, and `entity_picture` while playing.
Group players are supported. For example, to follow only the kitchen player:

```yaml
player_entities:
  - media_player.sound_mad_01
```

With multiple players, the one whose state most recently changed to `playing`
wins. Players without artwork are skipped for cover selection, but still count
as playing so a temporary missing cover does not trigger a pause screen. The
app does not start, pause, or change music playback.

## Pause and Shutdown

With `pause_then_off: true`, once all selected Music Assistant players stop,
pause, or become idle, the app displays "music paused" centered on two lines.
The 60-second timer starts after the screen uploads successfully. It then sends
WLED `on: false`. Repeated idle updates do not restart the timer or wake the
display after shutdown. This behavior also works with `show_track_info: false`.

Resuming any selected player cancels the timer and restores the playing display
starting with the cover page. Resuming after shutdown turns WLED back on. If
another selected player is still playing, its cover takes over instead of
showing the pause message. Playback changes are detected on the next HA poll.

Starting the app while music is already idle does not take over or turn off an
unrelated WLED display. The pause timer is in memory and is canceled by an app
restart. Failed screen uploads and power-off requests are retried; power-off
failures do not restart the one-minute countdown. If HA cannot be reached at
the deadline, the app retries rather than turning off without checking whether
playback has resumed.

Updating from 0.3.0 enables this behavior by default, even if `idle_action` was
previously `keep`. Set `pause_then_off: false` and restart the app to retain the
old `idle_action` behavior.

## Track Information

The display alternates between a full-screen album cover and a separate
information page. Each page remains visible for `page_interval` seconds. The
information page shows a large remaining-time countdown in green at the top,
then the song title in white, artist in cyan, and album in gold. Text lines that
do not fit scroll horizontally, starting after a two-second reading pause on
the first information page. Scrolling continues across later information pages
so long titles can be read in full. Each row is clipped to its own area.

New songs and player changes start the cycle on the cover page. The countdown
uses the current playback position when the information page reappears, rather
than resuming an old timer value from before the cover page.

Remaining time is calculated from `media_duration`, `media_position`, and
`media_position_updated_at`. The app advances the countdown once per second
between HA polls, then resynchronizes when HA supplies a new position. Seeking
or changing tracks is reflected on the next HA poll. The countdown stops at
`-0:00`; live streams and unknown durations show `--:--`. Missing metadata leaves
its corresponding line blank. When playback stops, the pause screen replaces
these pages by default. With `pause_then_off: false` and `idle_action: keep`, the
last rendered frame stays visible, including its last countdown value.

Updating from 0.2.0 replaces the compact layout with these two pages. Set
`show_track_info: false` and restart the app for an uninterrupted 64x64 cover.
The HA polling
interval still defaults to five seconds; the text/countdown render loop runs
separately at one-second intervals and only uploads changed pixel chunks.

## WLED Setup

Use a HUB75 build of WLED 16.0.1 or newer. Configure the physical panel as 64x64
and set the 2D matrix size to 64x64. Use one segment for the display. The app
configures segment 0 to cover the entire matrix and clears segment mirroring,
rotation, grouping, spacing, and offset. It replaces the running effect with a
frozen pixel image without modifying saved presets.

Brightness is not raised by the app. Set it in WLED or with `light.wall_01` in
Home Assistant. A brightness of zero, or a segment brightness of zero, produces
a dark image. HUB75 panels need a properly sized independent 5V supply; WLED's
automatic current limiter does not account for HUB75 power consumption.

The app turns the display on when uploading a cover. While music is playing,
turning it off or selecting another effect may be undone by the next health
check within 30 seconds. Stop the app to control WLED independently. Stopping
the app leaves the display as it is; `idle_action` applies to playback, not to
app shutdown. Pixel images are not persistent across controller restarts, but
the app restores the cover if music is still playing.

## Troubleshooting

- **No cover:** verify the MA integration exposes artwork and the correct player
  is `playing`. Check `player_entities`; an empty list follows all MA players.
- **HTTP 401/403:** stop and start the app to obtain its Supervisor token. No
  long-lived user token should be configured.
- **HTTP 404:** the HA media proxy may be temporarily unavailable during a track
  change. The app retries on the next polling cycle.
- **Matrix-size error:** correct WLED's 2D configuration; only 64x64 is supported.
- **Connection failures:** check the controller URL and LAN reachability from HA.
- **Scrambled display:** check the HUB75 panel's scan rate and WLED wiring setup.
- **Wrong cover:** restrict `player_entities` when independent rooms play music.

The Logs tab reports successful uploads and retryable failures. Failed downloads
do not change the display, and failed uploads are retried rather than cached.
Images above 10 MiB or unsafe decoded dimensions are rejected. Debug logs can
contain temporary artwork URLs; redact them before sharing logs.