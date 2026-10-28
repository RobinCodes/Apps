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
bars float and get out of the way once the pointer rests on the picture or
leaves the window (not over a paused video, an open panel or the bars
themselves); the strip of thumbnails and the side panels take room from the
picture instead, because a photograph with its bottom fifth permanently behind
a panel is not being shown to you. The picture can be dragged anywhere, into
the empty space past its edges included; a double-click brings it back.

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

## Paper

Class notes are taken on paper, and the second brain starts from a
photograph of each page (`SecondBrain/SYSTEM.md` §6). **Import notebook
pages** — Ctrl+Shift+I, the menu, the palette, or a right-click in the strip
— takes the photographs marked in the strip, or the one on screen, and goes
through them a page at a time.

* **The corners are found.** Paper on a desk is the brightest large thing in
  the picture, so the page is the largest bright region at a few hundred
  pixels across, and its corners are that region's extreme points along the
  diagonals. They arrive as four grips on the photograph, drawn in the same
  glass as the rest, with the outside of the page dimmed and a magnifier
  under the grip being dragged. When nothing page-shaped was found — a white
  desk, an open notebook of two pages — the grips sit just inside the
  photograph and the panel says so.
* **The page is straightened to its real shape.** A trapezium does not say
  how tall the paper was; a pinhole camera's view of a rectangle does, and
  Zhang and He's construction recovers it from the four corners. Within 7% of
  √2 it is drawn as √2, because the notebook is A5.
* **The light is taken out.** The brightest pixel of every block of about a
  line of handwriting is the paper there, whatever the ink did; spread three
  blocks so a filled box is not read as a shadow, smoothed, and divided out,
  it leaves the paper evenly white. A tone curve then takes the ink to black
  and the paper to pure white, which is also what makes the file small.
* **It is saved small**: a greyscale JPEG at 1600 pixels on the long side,
  at the best quality that comes in under 250KB — these sync through git, to
  every machine, for good. A typical page is about a hundred kilobytes.
* **It is read.** By Claude, when Claude Code is installed: handwriting, in
  Hungarian, with the mathematics as `$…$`, into Markdown. By Tesseract
  otherwise — `tesseract -l hun+eng`, with the brain's own
  `config/tessdata` as its language data when that exists — which reads
  print well and handwriting poorly. Or by nobody, and the page is filed as
  an image. The reading runs after the page is filed, beside the next one.

A small form asks where it goes and which day it was. **Where**: the inbox,
or a subject — a folder under `Projects/Studies`. **The day** starts as the
day the camera says the photograph was taken. Filed under a subject, the
page image becomes `Studies/<Subject>/pages/YYYY-MM-DD-<n>.jpg`, and the page
goes into that day's note, `Studies/<Subject>/YYYY-MM-DD.md`, under
`## Pages` — the image, a comment saying where it came from, and its text —
made if it is not there, and the existing note if the day already has one in
the older `2026.10.27.` form. Filed into the inbox, the image and a note beside
it go there through the brain's own capture, with the provenance in the
note's front matter: the photograph, when it was taken, which page it says it
is, who read it.

Every lesson's page starts with a header line, `2026-10-27 · Dinamika · 14`
(the handbook's convention), and the reader's text is looked at for it: the
lesson's day, topic and page number go into an inbox note's front matter, and
a page filed under a different day than its header says is pointed out.

Claude's text goes in as the Markdown it is, its headings held below `##` so
a page can never end the section it is in. Tesseract's goes in a fenced
block: until it is rewritten (§6, "within 3 days"), nothing in it should be a
card, and a `::` misread out of handwriting would otherwise go straight into
the schedule.

Nothing is written into a photograph and nothing is deleted. A page is a new
file made from it, and the photographs stay where they were.

### Palace loci

A memory palace is built from places that are really walked, and each locus
is photographed (the handbook, §3.2). **Use as a palace locus** puts the
photograph on screen under a locus of a palace in `learning/palaces/` — or of
a new palace, made with `kind: palace` in its front matter — and under an
existing locus or a new one at the end of the walk. The photograph is copied,
as a JPEG of at most 1600 pixels, into a folder named after the palace beside
it, numbered by its locus — `Lakás/01-bejarati-ajto.jpg` — and linked as an
indented `![](…)` under that locus (`SYSTEM.md` §8). Open that folder here and
the strip is the walk, in order.

### Asking Claude

**Ask Claude about this picture** (Ctrl+Shift+A) opens a panel where the
editor goes: a typed question about the picture on screen — or about the
frame on screen, for a video — and the answer streaming in as it is written.
It is read-only: Claude Code runs in the picture's own folder in its default
mode, sees the picture and changes nothing, and *Continue in Claude Desk*
carries the conversation on where work can be done. Hermione's panel works
the same way.

### Several at once, and every command by name

Ctrl+click in the strip marks a thumbnail, Shift+click everything from the
last one marked, Ctrl+M the one on screen; a right-click offers what can be
done with the marks. **Ctrl+K** is the palette: every command in the app by
name, from the same table the keys are bound from, so it holds the ones with
no key of their own as well.

A text field in the window gets its keys before the shortcuts do. GTK runs an
application's accelerators first, and nearly every key here is a single
letter, so without that typing "quiz" anywhere — the palette, the day of a
page, the preferences' search — closed the window at the q.

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

**Porcelain** is the light default and **Hearth** the dark one. With
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
| `~/Projects/Studies/<Subject>/pages/` | Notebook pages, straightened, beside the day's notes they are in. |
| `~/SecondBrain/inbox/` | Notebook pages not yet given a subject, each with a note carrying where it came from. |
| `~/SecondBrain/learning/palaces/<Palace>/` | Photographs of a palace's loci, numbered in the order of the walk. |

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

For notebook pages, three more things help and none is required. `numpy`
makes the straightening a few array operations; without it GdkPixbuf and
cairo do the same work, a page in under two seconds here. `tesseract` (and
`tesseract-data-hun`) reads print; Claude Code reads handwriting. With
neither, pages are filed as images and the panel says why.

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

### On Windows

Through MSYS2's UCRT64 GTK, like the rest of the apps here: `the-eye.bat`
from a command line, or `windows\install.ps1`, which makes the Start Menu
shortcut and offers The Eye under *Open with* for every type it can open.
The suite passes there, video export included.

What it is offered for is asked, not listed. A Windows build of GdkPixbuf
has no loader for OpenEXR, DDS or QOI, which a Linux box with glycin opens
without noticing, so the formats The Eye *claims* are JPEG, PNG, GIF and TIFF
everywhere plus whatever else this machine's loaders report
(`media.claimed()`), and the installer registers exactly those by running
`the-eye.py --list-types`. A file in a format with no loader is still
recognised, listed and explained — "no loader for EXR on this machine" — but
nothing offers to open one.

Three things differ there and are handled where they arise: MSYS2's Python
spells paths with `/` while `os.scandir` joins with `\`, so paths are joined
by hand and compared as Windows compares them; Gio's content types are
extensions rather than MIME types, so they are converted before being asked
about; and a GL context belongs to the thread that made it, so a renderer
used off the main thread — the video export's — is Cairo there.

## The tests

```bash
python3 tests/test_theeye.py
```

About a hundred checks. Most of them are arithmetic — a known colour through a
known grade, compared against the number it should come out as — because that
is where the bugs that matter would be: a transposed matrix or a channel swap
looks plausible on screen and is obvious in a number. The rest build widgets,
play the clips and export them.

The paper half is checked the same way, against a page whose answers are
known: `fixtures.py` photographs a ruled, written-on sheet through a pinhole
camera tilted towards it, under a light that falls off to one corner, so where
its corners land and what shape it really is are numbers rather than
opinions. The suite finds the corners, recovers the shape, compares the
numpy, cairo and plain Python warps against each other, files pages into a
scratch brain — never the real one — and reads them with stand-ins for
Tesseract and Claude that check how they were called. Where Tesseract is
installed with a language, it also reads the page's printed header for real.

Test media is built rather than checked in: a second or two and a megabyte,
by `tests/fixtures.py`. The EXIF test file is assembled byte by byte on
purpose — a parser tested against the output of the library that inspired it
only proves the two agree.

Nothing in the suite puts a window on screen or makes a noise. Widgets are
built and drawn into offscreen render nodes but never presented, and every
video is opened muted, so running the tests while using the machine does not
interrupt anything.
