"""Generate documentation previews using the real 64x64 screen renderer."""

import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hub75"))

from app import load_cover, render_equalizer, render_frame, render_paused_frame

OUTPUT = ROOT / "docs" / "screenshots"
PORTRAITS = (
    ("artist-1", "https://upload.wikimedia.org/wikipedia/commons/6/6f/Beethoven.jpg"),
    ("artist-2", "https://upload.wikimedia.org/wikipedia/commons/0/0e/Beethoven_Hornemann.jpg"),
    ("artist-3", "https://upload.wikimedia.org/wikipedia/commons/8/8f/Beethoven_M%C3%A4hler_1815.jpg"),
)


def portrait(name, url):
    cached = OUTPUT / f"{name}.png"
    if cached.exists():
        with Image.open(cached) as image:
            return image.convert("RGB").resize((64, 64), Image.Resampling.NEAREST)
    request = Request(url, headers={
        "User-Agent": "HUB75-documentation/1.0 (https://github.com/JannicSJensen/ha-hub75-app)"
    })
    with urlopen(request, timeout=30) as response:
        return load_cover(response.read())


def enlarged(frame):
    assert frame.size == (64, 64) and frame.mode == "RGB"
    assert frame.getbbox() is not None
    return frame.resize((256, 256), Image.Resampling.NEAREST)


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    artists = tuple(portrait(name, url) for name, url in PORTRAITS)
    cover = artists[0].copy()
    draw = ImageDraw.Draw(cover)
    font = ImageFont.load_default(size=7)
    draw.rectangle((0, 0, 63, 10), fill="#183e38")
    draw.rectangle((0, 51, 63, 63), fill="#183e38")
    draw.text((9, 1), "BEETHOVEN", fill="white", font=font)
    draw.text((5, 53), "SYMPHONY No. 9", fill="#efc871", font=font)
    now = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)
    player = {"state": "playing", "attributes": {
        "media_title": "Ode to Joy",
        "media_artist": "Beethoven",
        "media_album_name": "Symphony 9",
        "media_duration": 300,
        "media_position": 77,
        "media_position_updated_at": now.isoformat(),
    }}
    spectrum = (60, 95, 140, 180, 235, 255, 205, 160, 110, 75, 120, 175, 215, 150, 95, 50)
    names = ("album-cover", "artist-1", "artist-2", "artist-3", "track-info", "equalizer")
    frames = {
        name: render_frame(cover, player, scroll_seconds=index * 8, now=now,
                           artist_images=artists, equalizer=spectrum)
        for index, name in enumerate(names)
    }
    frames["paused"] = render_paused_frame()
    for name, frame in frames.items():
        path = OUTPUT / f"{name}.png"
        enlarged(frame).save(path, optimize=True)
        with Image.open(path) as saved:
            assert saved.resize((64, 64), Image.Resampling.NEAREST).tobytes() == frame.tobytes()
    animation = []
    for step in range(40):
        bands = tuple(round(30 + 225 * (1 + math.sin(step * math.tau / 40 + band * 0.6)) / 2)
                      for band in range(16))
        animation.append(enlarged(render_equalizer(bands)))
    animation_path = OUTPUT / "equalizer-animated.gif"
    animation[0].save(animation_path, save_all=True, append_images=animation[1:],
                      duration=100, loop=0, disposal=2)
    with Image.open(animation_path) as saved:
        assert saved.size == (256, 256) and saved.n_frames == 40
    print("Verified 7 pixel-exact PNG previews and a 40-frame equalizer GIF.")


if __name__ == "__main__":
    main()