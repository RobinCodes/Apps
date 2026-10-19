# The Eye

A viewer for photographs and video, with a colour grading editor built into
it. One window, one folder, and the same set of adjustments whether what is on
screen is a still or a film.

```bash
./the-eye.py                 reopen whatever you were last looking at
./the-eye.py ~/Pictures      a folder
./the-eye.py holiday.mp4     a file, as a position in its folder
./the-eye.py -n photo.jpg    in a window of its own
```

Press **?** inside for the list of keys.

## What it is

Most viewers are one of two things: a picture viewer that cannot play video,
or a video player that is useless for photographs. The Eye is one program
because by the time anything reaches the screen it is a `GdkPaintable`, and
from that point on the zoom, the fit modes, the rotation, the grade and the
before-and-after split do not care which it was. That is not a trick to save
code — it is why a folder of a holiday, which is photographs and clips mixed
together, can be gone through with one set of arrow keys.

| | |
|---|---|
| **Stills** | JPEG, PNG, GIF, WebP, AVIF, HEIC, JPEG XL, TIFF, BMP, TGA, ICO, SVG, QOI, OpenEXR, PNM, XPM, DDS — whatever GdkPixbuf has a loader for, which it is asked rather than assumed. Animated GIF, APNG and WebP play on their own. |
| **Video** | Whatever GStreamer can decode: H.264, HEVC, VP8, VP9, AV1, MPEG-2, ProRes and the rest, in MP4, Matroska, WebM, AVI, MOV, TS and more. |
| **Sound** | Played, with its album art if it has any. |
| **Camera raw** | Opened where the pixbuf raw loader can manage it, and listed separately so that the JPEGs beside them are what you see first. |

## Looking

The picture fills the window and the controls are glass laid over it. The two
bars float and get out of the way while something is playing; the strip of
thumbnails and the side panels take room from the picture instead, because a
photograph with its bottom fifth permanently behind a panel is not being shown
to you.

* **Fit** the window, the width or the height, **fill** it, or show it at
  **actual size** — and zoom anywhere in between. Zooming keeps the point
  under the pointer under the pointer, which is the whole difference between
  a zoom that is a tool and one that is a lottery.
* **The strip** along any edge, showing a poster frame and a length for every
  video and a progress bar across anything half-watched.
* **Sorting** by name, date, size or kind, forwards or backwards, or shuffled.
  Names sort the way a person would read them: `IMG_9` before `IMG_10`, and
  `Ähre` next to `Ahre` rather than after `Zebra`.
* **A slideshow**, in which a video plays to its end rather than being cut off
  after four seconds.
* **Details** — the size, the shape, the format, and for a photograph the
  camera, the lens, the exposure, the aperture, the ISO, the focal length and
  where the camera was standing.

## Playing

Everything a video player is expected to do, and the reason this one does not
simply use `GtkVideo`: `GtkMediaStream` offers play, pause, seek and volume,
and everything that separates a player from a preview is outside it.

* Playback **speed** from a quarter to four times, with trick mode above 2.5x
  so that a fast scan stays smooth.
* **Frame stepping**, forwards with a step event and backwards with a seek.
* **Audio, video and subtitle tracks**, each listed with whatever the file
  actually said about it — a title, a language, the codec and the shape, and
  never an empty row.
* **Subtitle files** found beside the video by name, including `.ass` with its
  styling, and shifted earlier or later a quarter of a second at a time.
* **Sound shifted** against the picture, for a badly muxed file.
* **Chapters**, drawn on the timeline and listed in the menu.
* **An A-B loop**, done with a segment seek so the join does not click.
* **A frame under the pointer** when hovering the timeline.
* **Carrying on where you stopped** — for up to six hundred files, and not for
  a position in the last few seconds, because stopping in the credits means
  you finished it.

## Editing

The editor is a colour grading panel. It changes how a picture looks; it never
changes the file.

Start from a **look** — twenty-four of them, from *Enhance*, which is what a
good print shop would do without being asked, through *Cinema* and *Golden
Hour* to *Noir* and *Cyanotype* — and then take it too far or pull it back
with the sliders. Every slider reads zero when it is doing nothing, so
anything away from the middle is something you did.

| | |
|---|---|
| **Light** | Exposure in stops, contrast, a filmic S-curve that cannot clip, midtones, shadows and highlights masked to their own end of the range, and the black and white points. |
| **Colour** | Temperature, tint, saturation, hue, monochrome and duotone, split toning into the shadows and the highlights, and a colour wash in any of seven blend modes. |
| **Texture** | Clarity, sharpening, bloom with a threshold, film halation, diffusion glow, and grain that moves on a video and holds still on a photograph. |
| **Frame** | A vignette, either way. Rotation and flips. |

**Before and after** splits the picture down a draggable divider.
**Export** writes a copy: a still at full size, a single frame of a video, or
the whole video re-encoded with the grade applied to every frame.

### How it works, and why that matters

Everything above is built out of render nodes that GSK already knows how to
draw — a colour matrix, a blur, a blend, a mask, a gradient. It runs on the
graphics card, nothing is read back per frame, and the *same node tree* draws
the preview and the export. There is no second implementation of the grade for
the export to disagree with, which is why there is no "it looked different
before I saved it" to discover.

Three parts of it are worth knowing about:

* **Exposure is really exposure.** The values a colour matrix works on are
  sRGB-encoded, so scaling them by two is nearly three stops. One stop is a
  scale of 2^(1/2.2). Get that wrong and the control is a brightness knob
  wearing a photographer's label.
* **The bright pass is a clamp.** GSK clamps a colour matrix to 0–1, so
  `(value − threshold) × slope` deletes everything below the threshold. That
  is how bloom and halation find the highlights without a shader.
* **The unsharp mask is exact.** `DIFFERENCE(0.5 + a/2, b/2)` is exactly
  `0.5 + (a−b)/2` for every value in range — it cannot clamp and cannot fold —
  and mid grey is the fixed point of both soft light and overlay. So
  sharpening adds local contrast and leaves flat areas bit-for-bit alone. The
  test suite checks both halves of that rather than taking it on trust.

What it deliberately does not have is a tone curve with arbitrary control
points. A curve is the one piece of a grading pipeline that cannot be
assembled out of blend modes, so the editor offers the two curve shapes that
can be — a gamma and an S — and does not pretend to more.

## The glass

The panels are painted, not borrowed. There is no backdrop-blur protocol an
application can ask a Wayland compositor for, so a merely translucent window
shows whatever happens to be behind it — which here is usually the picture,
and a transport bar that is a hole onto a photograph is a transport bar you
cannot read.

What each panel *does* blur is the picture underneath it, through CSS
`backdrop-filter`, which reaches what the application itself has drawn. That
changes the whole design of the material: a bar over an unblurred photograph
has to be nearly opaque to stay legible, and a bar over a blurred one does
not. So with the blur on, the glass is drawn at about half the opacity, and
that thinness over a real blur is what looks like glass rather than like a
dark panel. Turning the blur off in the preferences falls back to the thicker
material, which is the right look for a machine that cannot spare it.

Ten themes, the house's, from [`house/theme.py`](../house/README.md) — the
same ten as the rest of the suite, so Porcelain here is the same Porcelain
as in the terminal. Each carries two separate colours for two separate jobs:
the chrome, which can be as decorative as it likes, and the **matte** — the
surround the picture sits on — which is a neutral grey in every one of them,
because the eye judges a colour against its surroundings and a warm surround
makes every photograph on it look cold. **Studio** takes that to its
conclusion: an 18% grey surround, the condition a print is judged in, with the
panels kept dark.

**Porcelain** is the light default and **Moss** the dark one. With
**Follow the desktop** on, which it is by default, The Eye uses one in a light
session and the other in a dark one. `T` still cycles through all ten, and
picking one that way turns the following off — otherwise the next change of
desktop would undo it, which looks like the key not working.

## Performance

It was written on a laptop with 3.5GB of memory and a 1366×768 screen, and the
three things that make it usable there are worth stating.

* **Video frames are scaled in the pipeline**, once, to no larger than the
  biggest monitor attached. A 4K frame is 33MB of RGBA and copying one per
  frame is more than a slow machine can do; at screen size it is 4MB, and
  since it was going to be scaled to the window anyway nothing is lost.
* **The grade is flattened between stages.** Nearly every stage draws its
  input more than once — a blend needs it as the backdrop and again as the
  source — and GSK re-renders a referenced subtree each time rather than
  keeping the result. Measured: one bloom 4.7ms, two 14.3, three 30.7, four
  62.9. Rendering the chain so far into a texture and carrying on from that
  makes it linear: the heaviest look went from 833ms a frame to 24.
* **Effect quality** can be turned down, which halves the blur radii and drops
  the decorative stages.

An ungraded picture costs exactly one textured quad; `is_neutral()` is checked
before any of this happens.

## The keyboard

Left and Right are the one deliberately overloaded pair: they move through the
folder when a picture is on screen and seek when a video is playing. Both
meanings are too well established to give up, and which one is wanted is never
ambiguous once you can see what is on screen. Everything else follows whatever
it is replacing — Space and F from every player, `[` and `]` from VLC, `+`,
`-`, `0` and `1` from every image viewer.

Press **?** for the full list, which is generated from the same table the keys
are bound from, so it cannot fall behind.

## Where things are kept

| | |
|---|---|
| `~/.config/the-eye/config.json` | Settings. Only what differs from the defaults, so a new default in a later version actually reaches you. |
| `~/.local/share/the-eye/state.json` | Where you stopped watching, recent folders, and grades for files whose folder cannot be written to. |
| `~/.cache/the-eye/thumbnails/` | Thumbnails, named after the file and the time it last changed — so an edited picture gets a new one and cannot be served a stale one. Emptied from the preferences. |
| `.the-eye.grades.json` | Beside the pictures. A grade travels with the folder; copy the folder elsewhere and the grades come too. |

**Nothing is ever written into a picture or a video.** A grade goes in the
sidecar, exporting writes a new file, and an export that would land on the
file it read is refused rather than confirmed.

## Running it

Arch, or anything with GTK 4.12 or newer:

```bash
sudo pacman -S gtk4 libadwaita python-gobject \
               gstreamer gst-plugins-base gst-plugins-good \
               gst-plugins-bad gst-plugins-ugly gst-libav
```

The photograph half works without GStreamer; it says so once and carries on.
Extra pixbuf loaders — `libheif`, `libavif`, `libjxl`, `webp-pixbuf-loader` —
add their formats, and The Eye says "no loader for HEIC on this machine"
rather than showing a grey rectangle.

Set up the same way as the rest of the apps here — a symlink into
`~/.local/bin` so it is on the path and can still update itself from the
checkout, and a desktop entry so it appears in the launcher and under
*Open With*:

```bash
ln -sf "$PWD/the-eye.py" ~/.local/bin/the-eye
cp the-eye.desktop ~/.local/share/applications/
cp the-eye.svg ~/.local/share/icons/hicolor/scalable/apps/
update-desktop-database ~/.local/share/applications
gtk-update-icon-cache -f -t ~/.local/share/icons/hicolor 2>/dev/null
```

Nothing is copied but the icon and the entry: the app runs out of this
checkout, which is what lets it fast-forward itself when the repository moves
on. To make it the default for a file type, use your file manager's *Open
With* — that choice is the user's and no installer should take it.

There is a `the-eye.bat` for Windows through MSYS2, written to the same shape
as the other apps here. It is **untested**: GStreamer exists for MSYS2 but
this has never been run there, and the pipeline is the half most likely to
need work.

## The tests

```bash
python3 tests/test_theeye.py
```

About seventy checks. Most of them are arithmetic — a known colour through a
known grade, compared against the number it should come out as — because that
is where the bugs that matter would be: a transposed matrix or a channel swap
looks plausible on screen and is obvious in a number. The rest build widgets,
play the clips and export them.

Test media is built rather than checked in: about a fifth of a second and two
hundred kilobytes, by `tests/fixtures.py`. The EXIF test file is assembled byte
by byte on purpose — a parser tested against the output of the library that
inspired it only proves the two agree.

Nothing in the suite puts a window on screen or makes a noise. Widgets are
built and drawn into offscreen render nodes but never presented, and every
video is opened muted, so running the tests while using the machine does not
interrupt anything.
