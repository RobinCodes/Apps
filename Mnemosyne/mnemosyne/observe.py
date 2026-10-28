"""The observation trainer's pictures: finding them, shrinking them, changing them.

*What changed?* shows a photograph for a few seconds, then shows it again with
one region quietly altered — mirrored, recoloured, or covered with a patch
cloned from elsewhere in the same picture — and asks where. It trains the
thing the curriculum's second module is about: looking at a scene closely
enough to have *seen* it, rather than to have recognised it.

Done with GdkPixbuf and plain bytes, so it needs nothing GTK does not already
bring. Three things keep it cheap on a 3 GB machine:

* a photograph is decoded straight to the size it is shown at (`load`), never
  at the camera's forty megapixels;
* the changed copy is made once per round, and only the region is touched;
* the edge of the change is feathered over a few pixels, so the trainer asks
  about the *content* of a region and not about a seam around it — which is
  also what makes "subtle" mean subtle.
"""

from __future__ import annotations

import os
import random

import gi

gi.require_version("GdkPixbuf", "2.0")

from gi.repository import GdkPixbuf, GLib  # noqa: E402

from .trainers import Change  # noqa: E402

PHOTO_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")
MAX_SCAN = 6000          # entries walked before giving up looking for more
FEATHER = 6              # pixels over which a change fades into the photograph


def find_photos(folder: str, limit: int = 400) -> list[str]:
    """Up to `limit` photographs under `folder`, without walking a whole drive."""
    found: list[str] = []
    seen = 0
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in files:
            seen += 1
            if name.lower().endswith(PHOTO_SUFFIXES) and not name.startswith("."):
                found.append(os.path.join(root, name))
                if len(found) >= limit:
                    return found
        if seen >= MAX_SCAN:
            break
    return found


def load(path: str, max_side: int = 1100) -> GdkPixbuf.Pixbuf | None:
    """The photograph decoded at no more than `max_side`, upright, without alpha."""
    try:
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, max_side, max_side, True)
    except GLib.Error:
        return None
    pixbuf = pixbuf.apply_embedded_orientation() or pixbuf
    if pixbuf.get_has_alpha():
        flat = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8,
                                    pixbuf.get_width(), pixbuf.get_height())
        flat.fill(0x000000ff)
        pixbuf.composite(flat, 0, 0, pixbuf.get_width(), pixbuf.get_height(),
                         0, 0, 1, 1, GdkPixbuf.InterpType.NEAREST, 255)
        pixbuf = flat
    return pixbuf


def _region(pixbuf, x, y, w, h) -> tuple[bytearray, int]:
    """A region's pixels as packed RGB bytes, and their row stride (w * 3)."""
    sub = pixbuf.new_subpixbuf(x, y, w, h).copy()
    data = sub.get_pixels()
    stride = sub.get_rowstride()
    channels = sub.get_n_channels()
    out = bytearray(w * h * 3)
    for row in range(h):
        line = data[row * stride:row * stride + w * channels]
        if channels == 3:
            out[row * w * 3:(row + 1) * w * 3] = line
        else:
            for col in range(w):
                out[(row * w + col) * 3:(row * w + col) * 3 + 3] = \
                    line[col * channels:col * channels + 3]
    return out, w * 3


def detail(pixbuf):
    """A scorer for `trainers.plan_change`: how much is going on in a region."""

    def score(x, y, w, h):
        pixels, _ = _region(pixbuf, x, y, w, h)
        step = max(3, (w * h) // 400) * 3
        values = pixels[::step]
        if not values:
            return 0.0
        mean = sum(values) / len(values)
        return sum((v - mean) ** 2 for v in values) / len(values)

    return score


def apply(pixbuf: GdkPixbuf.Pixbuf, change: Change) -> GdkPixbuf.Pixbuf:
    """A copy of the photograph with `change` made to it."""
    out = pixbuf.copy()
    original, _ = _region(pixbuf, change.x, change.y, change.w, change.h)
    if change.kind == "mirror":
        flipped = pixbuf.new_subpixbuf(change.x, change.y, change.w, change.h).flip(True)
        replacement, _ = _region(flipped, 0, 0, change.w, change.h)
    elif change.kind == "clone":
        replacement, _ = _region(pixbuf, change.sx, change.sy, change.w, change.h)
    else:
        replacement = _recolour(original)
    blended = _feather(original, replacement, change.w, change.h)
    patch = GdkPixbuf.Pixbuf.new_from_bytes(
        GLib.Bytes.new(bytes(blended)), GdkPixbuf.Colorspace.RGB, False, 8,
        change.w, change.h, change.w * 3)
    patch.copy_area(0, 0, change.w, change.h, out, change.x, change.y)
    return out


def _recolour(pixels: bytearray) -> bytearray:
    """Turn the region's colours a third of the way round the wheel, half strength."""
    out = bytearray(len(pixels))
    for i in range(0, len(pixels), 3):
        r, g, b = pixels[i], pixels[i + 1], pixels[i + 2]
        out[i] = (r + b) // 2
        out[i + 1] = (g + r) // 2
        out[i + 2] = (b + g) // 2
    return out


def _feather(original: bytearray, replacement: bytearray, w: int, h: int) -> bytearray:
    out = bytearray(replacement)
    edge = max(1, min(FEATHER, w // 4, h // 4))
    for row in range(h):
        dy = min(row, h - 1 - row)
        for col in range(w):
            d = min(dy, col, w - 1 - col)
            if d >= edge:
                continue
            weight = (d + 0.5) / edge
            i = (row * w + col) * 3
            for c in range(3):
                out[i + c] = int(original[i + c] * (1 - weight) + replacement[i + c] * weight)
    return out


def pick(photos: list[str], rng: random.Random, avoid: str | None = None) -> str | None:
    choices = [p for p in photos if p != avoid] or photos
    return rng.choice(choices) if choices else None
