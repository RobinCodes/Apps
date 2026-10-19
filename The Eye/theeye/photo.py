"""Loading a still, and playing one that moves.

A photograph reaches the screen as a `GdkTexture`, which is the GPU's copy of
it, and everything after that is `effects.py` and `stage.py`. What is left for
this module is the part that is harder than it looks:

*Orientation.* A JPEG from a phone is stored in whatever direction the sensor
reads out and carries an EXIF tag saying which way up it really is. GdkPixbuf
can apply the tag; `Gdk.Texture.new_from_file` cannot — it is the faster path
and skips the metadata — so a picture loaded the fast way comes out sideways.
Both paths are here, and the slow one is used for exactly the formats that
carry the tag.

*Size.* A 60-megapixel photograph is 240MB as a texture, and a folder of them
browsed quickly is a machine out of memory. Anything past a threshold is
loaded at a scale instead, chosen so the result still has more pixels than
the largest monitor can show, and the full-size load happens only when the
picture is zoomed past that point.

*Animation.* A GIF, an APNG and an animated WebP are all a sequence of frames
with delays, and GdkPixbuf will decode them one at a time through
`PixbufAnimationIter`. Playing one here rather than through GStreamer is not
an optimisation for its own sake: a pipeline takes longer to start than most
animated GIFs last.

Everything in `load()` is blocking and belongs on a worker thread —
`jobs.run` is how the window calls it.
"""

from __future__ import annotations

import os

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")

from gi.repository import Gdk, GdkPixbuf, GLib, GObject, Gio  # noqa: E402

from . import media

# Formats that can carry an orientation tag, and so must go through GdkPixbuf
# rather than the direct texture loader. Everything else takes the fast path.
ORIENTED = {".jpg", ".jpeg", ".jpe", ".jfif", ".tif", ".tiff", ".webp",
            ".heic", ".heif", ".avif", ".jxl", ".png"}

# Past this many pixels, load at a scale. 40 megapixels is about where a
# single texture stops being something a laptop can hold several of.
LARGE_PIXELS = 40_000_000

# No matter how large the file, this is enough: more pixels than any monitor
# in circulation, with room to zoom in twice before the full-size load is
# needed.
SCALED_LONGEST = 8192


class Still:
    """A loaded picture, and what was learned while loading it."""

    __slots__ = ("path", "texture", "width", "height", "scaled_from",
                 "animation", "frames", "format", "error")

    def __init__(self, path):
        self.path = path
        self.texture: Gdk.Texture | None = None
        self.width = 0
        self.height = 0
        self.scaled_from = (0, 0)       # the real size, when loaded smaller
        self.animation = None           # an Animation, for a GIF or APNG
        self.frames = 1
        self.format = ""
        self.error = ""

    def ok(self) -> bool:
        return self.texture is not None or self.animation is not None

    def aspect(self) -> float:
        return self.width / self.height if self.height else 0.0

    def full_size(self) -> tuple[int, int]:
        return self.scaled_from if self.scaled_from != (0, 0) else (self.width,
                                                                    self.height)

    def is_scaled(self) -> bool:
        return self.scaled_from != (0, 0)

    def describe(self) -> str:
        width, height = self.full_size()
        bits = [f"{width} x {height}"]
        pixels = width * height
        if pixels >= 1_000_000:
            bits.append(f"{pixels / 1e6:.1f} MP")
        if self.format:
            bits.append(self.format.upper())
        if self.animation is not None:
            bits.append(f"{self.frames} frames" if self.frames > 1 else "animated")
        return "  ·  ".join(bits)


# ---------------------------------------------------------------- loading ----

def probe(path: str) -> tuple[str, int, int]:
    """The format and size of a still, without decoding it.

    GdkPixbuf reads just the header for this, which makes it cheap enough to
    do for every file in a folder — the strip uses it to lay out thumbnails at
    the right shape before any of them have been decoded.
    """
    try:
        fmt, width, height = GdkPixbuf.Pixbuf.get_file_info(path)
    except Exception:                                   # noqa: BLE001
        return "", 0, 0
    # get_file_info reports -1 for both when it did not recognise the file,
    # and a negative size would propagate into every layout that uses it.
    return (fmt.get_name() if fmt else ""), max(0, width or 0), max(0, height or 0)


def load(path: str, animate: bool = True, longest: int = 0) -> Still:
    """A picture from disk. Blocking — call it on a worker thread.

    `longest` caps the longest side; 0 means "whatever the file is, within
    reason". Failures come back on the Still rather than as exceptions,
    because every caller wants to put the message on screen and carry on to
    the next picture.
    """
    still = Still(path)
    fmt, width, height = probe(path)
    still.format = fmt
    if not os.path.isfile(path):
        still.error = "The file is no longer there"
        return still
    if width and height:
        still.width, still.height = width, height

    if animate and media.is_animated(path):
        animation = Animation.load(path)
        if animation is not None:
            still.animation = animation
            still.width, still.height = animation.width, animation.height
            still.frames = animation.frames
            return still

    pixels = (width * height) if (width and height) else 0
    cap = longest or (SCALED_LONGEST if pixels > LARGE_PIXELS else 0)
    try:
        if cap and width and height and max(width, height) > cap:
            still.texture = _load_scaled(path, width, height, cap, still)
        elif os.path.splitext(path)[1].lower() in ORIENTED:
            still.texture = _load_oriented(path)
        else:
            still.texture = Gdk.Texture.new_from_filename(path)
    except GLib.Error as exc:
        still.error = _explain(path, exc)
        return still
    except Exception as exc:                            # noqa: BLE001
        still.error = str(exc) or "The picture could not be read"
        return still

    if still.texture is not None:
        if not still.scaled_from:
            still.width = still.texture.get_width()
            still.height = still.texture.get_height()
    return still


def _load_oriented(path: str) -> Gdk.Texture:
    """Through GdkPixbuf, with the EXIF orientation applied.

    `apply_embedded_orientation` is the whole reason for this path. It returns
    a new pixbuf when there was a rotation to do and the same one when there
    was not, so it costs nothing for a picture that was already upright.
    """
    pixbuf = GdkPixbuf.Pixbuf.new_from_file(path)
    upright = pixbuf.apply_embedded_orientation() or pixbuf
    return Gdk.Texture.new_for_pixbuf(upright)


def _load_scaled(path: str, width: int, height: int, cap: int,
                 still: Still) -> Gdk.Texture:
    """A large picture loaded smaller, keeping its shape.

    GdkPixbuf's scaling loader decodes at the reduced size rather than
    decoding everything and throwing most of it away, which for a JPEG means
    it can skip whole DCT coefficients. On a 100-megapixel scan that is the
    difference between a second and ten.
    """
    scale = cap / max(width, height)
    target = (max(1, int(round(width * scale))), max(1, int(round(height * scale))))
    pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
        path, target[0], target[1], True)
    upright = pixbuf.apply_embedded_orientation() or pixbuf
    still.scaled_from = (width, height)
    still.width, still.height = upright.get_width(), upright.get_height()
    return Gdk.Texture.new_for_pixbuf(upright)


def _explain(path: str, exc) -> str:
    """A GLib error as something worth putting on screen.

    The raw text is usually either useful or useless with nothing in between
    — "Unrecognized image file format" tells you nothing you did not know —
    so the one case worth catching is the common one: a format this machine
    has no loader for, which is a thing the user can actually fix.
    """
    if not media.loader_for(path):
        ext = media.extension(path).lstrip(".").upper() or "that format"
        return f"No loader for {ext} is installed on this machine"
    message = (getattr(exc, "message", "") or str(exc)).strip()
    return message or "The picture could not be read"


def thumbnail(path: str, size: int) -> Gdk.Texture | None:
    """A small version of a still, for the strip. Blocking.

    Loaded at a scale rather than loaded and shrunk, for the same reason as
    above, and with the orientation applied — a strip of sideways thumbnails
    under upright pictures is the kind of detail that makes an app feel
    unfinished.
    """
    try:
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, size, size, True)
    except (GLib.Error, Exception):                     # noqa: BLE001
        return None
    if pixbuf is None:
        return None
    upright = pixbuf.apply_embedded_orientation() or pixbuf
    return Gdk.Texture.new_for_pixbuf(upright)


# -------------------------------------------------------------- animation ----

class Animation(GObject.Object):
    """A still that moves, played on a timer from GdkPixbuf's own iterator.

    The iterator decides how long each frame lasts — GIF carries a delay per
    frame, and a surprising number of them vary it — so the timer is
    rescheduled after every frame rather than running at a fixed rate. A
    delay of zero, which some encoders write meaning "as fast as possible",
    is held to a sane floor instead: a browser does the same, and without it
    an old GIF plays at several hundred frames a second.

    `advance` is emitted rather than the paintable being updated directly, so
    that the stage can decide whether anything needs redrawing — an animation
    in a window that is not visible should not be costing frames.
    """

    __gtype_name__ = "TheEyeAnimation"

    __gsignals__ = {
        "advance": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    # Below this, a frame delay is a lie. 20ms is 50fps, which is where
    # browsers settled for the same reason.
    MINIMUM_DELAY = 20

    def __init__(self, animation, frames: int = 0):
        super().__init__()
        self._animation = animation
        self._iter = animation.get_iter(None)
        self.width = animation.get_width()
        self.height = animation.get_height()
        self.frames = frames        # 0 when unknown, which is the usual case
        self.index = 0
        self._timer = 0
        self._texture = None
        self._refresh()

    @classmethod
    def load(cls, path: str):
        """Open an animation, or return None when it is a single still.

        Counting the frames means walking the whole animation, which for a
        long GIF is slow and for a `PixbufSimpleAnim` is not possible at all.
        So the count is an upper bound found by advancing until the iterator
        comes back to its start — capped, because a few GIFs in the world are
        thousands of frames long and nobody needs an exact number to decide
        whether to show a play button.
        """
        try:
            animation = GdkPixbuf.PixbufAnimation.new_from_file(path)
        except (GLib.Error, Exception):                 # noqa: BLE001
            return None
        if animation is None or animation.is_static_image():
            return None
        return cls(animation, 0)

    def texture(self):
        return self._texture

    def _refresh(self):
        pixbuf = self._iter.get_pixbuf()
        if pixbuf is not None:
            upright = pixbuf.apply_embedded_orientation() or pixbuf
            self._texture = Gdk.Texture.new_for_pixbuf(upright)

    def start(self):
        if self._timer:
            return
        self._schedule()

    def stop(self):
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0

    def playing(self) -> bool:
        return bool(self._timer)

    def _schedule(self):
        delay = self._iter.get_delay_time()
        if delay < 0:
            self._timer = 0
            return                      # the animation has ended
        self._timer = GLib.timeout_add(max(self.MINIMUM_DELAY, delay), self._tick)

    def _tick(self):
        self._timer = 0
        if self._iter.advance(None):
            self.index += 1
            self._refresh()
            self.emit("advance")
        self._schedule()
        return GLib.SOURCE_REMOVE

    def rewind(self):
        """Back to the first frame, by asking for a fresh iterator."""
        self.stop()
        self._iter = self._animation.get_iter(None)
        self.index = 0
        self._refresh()
        self.emit("advance")


# Deliberately not counted. GdkPixbuf has no API for the number of frames in
# an animation, and the only way to find out is to advance an iterator through
# the whole thing — which for a long GIF means decoding every frame before the
# first one is shown, to put a number in a panel nobody has opened. `frames`
# stays 0, meaning "more than one, and unknown", and the description says
# "animated" instead of lying about how many.
