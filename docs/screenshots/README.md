# Screen Preview Sources

The PNGs are actual app-rendered 64x64 frames enlarged to 256x256 using
nearest-neighbor scaling. They preserve every source pixel without adding an
LED-grid effect, bloom, or smoothing. They are software previews, not physical
panel photographs, and do not capture a live HA or Music Assistant session.

The album cover is a designed example using the first portrait and sample
typography. Track metadata and timing are examples. Artist portraits demonstrate
the optional artist-photo pages; these particular images were supplied directly
to the renderer rather than fetched from a Music Assistant library.

The equalizer still and four-second looping GIF use illustrative spectrum
values passed to the production equalizer renderer, not captured Audio Sync
packets. Actual bars follow the received Music Assistant spectrum.

## Artwork Credits

The three Beethoven portraits are faithful reproductions of public-domain
paintings. Wikimedia Commons identifies both the works and their reproductions
as public domain; see the source pages for jurisdiction-specific reuse notes.

| Preview | Artist and date | Source and licensing |
| --- | --- | --- |
| `artist-1.png`, sample cover | Joseph Karl Stieler, 1820 | [Beethoven.jpg](https://commons.wikimedia.org/wiki/File:Beethoven.jpg) |
| `artist-2.png` | Christian Horneman, 1803 | [Beethoven Hornemann.jpg](https://commons.wikimedia.org/wiki/File:Beethoven_Hornemann.jpg) |
| `artist-3.png` | Joseph Willibrord Maehler, 1815 | [Beethoven Maehler 1815.jpg](https://commons.wikimedia.org/wiki/File:Beethoven_M%C3%A4hler_1815.jpg) |

Portraits are resized and padded using the app's normal image loader. The
sample cover additionally includes title bands; no original album artwork is
being redistributed.

## Regeneration

From the repository root, with the development dependencies installed:

```bash
python3 docs/make-screenshots.py
```

The script imports the production rendering functions, generates each playback
page and the pause screen, and verifies dimensions, nonblank frames, PNG pixel
fidelity, and the GIF frame count. Existing artist PNGs are reused as input;
on first generation it downloads the public-domain originals from Wikimedia
Commons. No HA access, MA tokens, WLED device, or new runtime dependencies are
required.