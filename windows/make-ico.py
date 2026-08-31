"""Build a Windows .ico from an SVG, using the GTK runtime that is already here.

Windows shortcuts want an .ico, and the apps ship .svg. rsvg-convert (part of
the same MSYS2 stack that provides GTK) rasterises each size, and the sizes
are then packed into an .ico container by hand — the format allows a PNG to
be stored verbatim since Vista, so no image library is needed for the packing.

    python make-ico.py icon.svg icon.ico
"""

from __future__ import annotations

import os
import struct
import subprocess
import sys
import tempfile

# 16 for the title bar, 256 for the large-icon views, the rest in between.
SIZES = (16, 24, 32, 48, 64, 128, 256)


def rasterise(svg: str, size: int, out: str) -> bytes | None:
    try:
        subprocess.run(
            ["rsvg-convert", "-w", str(size), "-h", str(size), "-o", out, svg],
            check=True, capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    with open(out, "rb") as fh:
        return fh.read()


def build(svg: str, ico: str) -> int:
    """Write `ico` from `svg`. Returns how many sizes made it in."""
    images: list[tuple[int, bytes]] = []
    with tempfile.TemporaryDirectory() as tmp:
        for size in SIZES:
            png = rasterise(svg, size, os.path.join(tmp, f"{size}.png"))
            if png:
                images.append((size, png))
    if not images:
        return 0

    # ICONDIR: reserved, type 1 (icon), image count.
    out = [struct.pack("<HHH", 0, 1, len(images))]
    # Each ICONDIRENTRY is 16 bytes and they all precede the image data.
    offset = 6 + 16 * len(images)
    for size, png in images:
        # 256 is stored as 0 — the field is one byte.
        dim = 0 if size >= 256 else size
        out.append(struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset))
        offset += len(png)
    out.extend(png for _, png in images)

    with open(ico, "wb") as fh:
        fh.write(b"".join(out))
    return len(images)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    count = build(sys.argv[1], sys.argv[2])
    if not count:
        sys.exit(f"could not rasterise {sys.argv[1]} — is rsvg-convert on PATH?")
    print(f"{sys.argv[2]}: {count} sizes")
