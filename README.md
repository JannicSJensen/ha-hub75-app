# HUB75 Album Artwork

A standalone Home Assistant app that displays Music Assistant album artwork on
a 64x64 HUB75 matrix running WLED. No Dasher dependency, HA YAML automation,
Music Assistant API key, or manually created Home Assistant token is required.

## Requirements

- Home Assistant OS with Supervisor and the Music Assistant integration enabled.
- An amd64 or aarch64 host.
- WLED 16.0.1 or newer with HUB75 support and a configured 64x64 matrix.
- Network access from Home Assistant to the WLED controller.

## Install

Open **Settings > Apps > App store**
(called **Add-ons** on older HA versions), select **Repositories** from the menu,
and add:

```text
https://github.com/JannicSJensen/ha-hub75-app
```

Install **HUB75 Album Artwork**, set `display_url`, and start the app. The
Supervisor builds the image on your device; no external container registry is
required. Leave `player_entities` empty to follow any Music Assistant player.

For local installation before publishing, place the `hub75` folder in your HA
local app directory, conventionally `/addons/hub75` on the host. Refresh the app
store and install it from **Local apps**. The local apps directory is not the
Home Assistant `/config` directory; use Samba or an SSH app with access to it.

See [hub75/DOCS.md](hub75/DOCS.md) for settings and troubleshooting,
[docs/versioning.md](docs/versioning.md) for updates and releases, and
[docs/architecture.md](docs/architecture.md) for the implementation.

## Behavior

The app checks HA every five seconds and follows the most recently started
playing Music Assistant player that has artwork. Multiple independent players
share one display, so the most recent player takes precedence. If it stops,
another playing player takes over. Artwork changes cause a new upload; position
and volume updates do not.

Artwork is fitted to 64x64 with black padding and sent as 16 sequential pixel
requests. Current brightness is preserved. When playback stops, the last cover
remains visible by default; `idle_action: off` turns the display off instead.
While music is playing, a health check every 30 seconds restores artwork after a
WLED restart or a change to its current effect.

The app uses the Supervisor-provided token only for the HA API and HA artwork
proxy. It does not send that credential to WLED or external artwork hosts. Keep
WLED on a trusted LAN. Saved presets and playlists are not written or deleted.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests -v
docker build -t ha-hub75-app:dev hub75
```

The tests validate the HA manifest and run against mock HTTP servers, with no HA
credentials or physical display required. CI tests Python 3.13 and builds both
CPU architectures. The configured app version is `0.1.0`.