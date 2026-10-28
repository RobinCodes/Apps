"""Test media, built rather than checked in.

A repository of sample photographs and video clips would be the larger half of
this project by size, would need licences, and would still not cover the cases
that matter — so the suite makes its own. Everything here is small on purpose:
the machine this was written on has 3.5GB of memory, and a test that needs a
4K clip to prove a point has not understood the point.

What gets made, and what each one is for:

* `test.png` / `test.jpg` — a luminance ramp with colour patches over it and a
  gradient below. Grading is visible on it and measurable in it: the ramp
  shows a contrast change, the patches a saturation one.
* `alpha.png` — has holes in it, for the chequerboard and for the flattening
  that JPEG export has to do.
* `exif.jpg` — a hand-built EXIF block. The parser is tested against bytes
  laid out by hand rather than by the same library that reads them, which is
  the only way that test means anything.
* `anim.gif` — six frames, for the animation path.
* `clip.mp4` — a short clip with sound, for playing, seeking and exporting.
* `multi.mkv` — two audio tracks, for track selection.
* `clip.srt` — a sidecar, for subtitle discovery.
* `tone.ogg` — sound with no pictures, for the audio-only path.
* `withart.mp3` — sound *with* a cover image in its tags, which is the other
  half of that path and needs ffmpeg to build.
* `page.jpg` — a notebook page photographed on a desk: ruled, written on,
  with a header line in print, lit unevenly and seen at an angle through a
  pinhole camera — so the corners and the true shape of the page are known
  exactly (`PAGE_CORNERS`, `PAGE_ASPECT`), and the camera's date is in its
  EXIF as a phone's would be.

`build()` skips anything already there, so a suite run twice is not a suite
that encodes twice. It needs Pillow for the stills and `gst-launch-1.0` for
the clips, and says which are missing rather than failing: a machine without
them can still run most of the suite.
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess

STILLS = ("test.png", "test.jpg", "alpha.png", "exif.jpg", "anim.gif", "page.jpg")
CLIPS = ("clip.mp4", "multi.mkv", "tone.ogg", "withart.mp3")
SIDECARS = ("clip.srt",)


def have_pillow() -> bool:
    try:
        import PIL  # noqa: F401
    except ImportError:
        return False
    return True


def have_gst() -> bool:
    return shutil.which("gst-launch-1.0") is not None


def gst_launch() -> str:
    """gst-launch-1.0, by its full path.

    Not by its name: Windows' CreateProcess adds `.exe` only to a name with no
    extension of its own, and to it `gst-launch-1.0` already has one — `.0` —
    so every clip failed to build there and the video half of the suite was
    skipped without anybody being told why. `shutil.which` knows PATHEXT.
    """
    return shutil.which("gst-launch-1.0") or "gst-launch-1.0"


def have_ffmpeg() -> bool:
    """Only needed for the one fixture with cover art in it.

    Attaching a picture to an MP3's tags is a few flags in ffmpeg and a
    muxer-specific dance in GStreamer, and this is a test fixture rather
    than a feature — so the easy tool wins, and the test is skipped where it
    is missing.
    """
    return shutil.which("ffmpeg") is not None


def build(folder: str) -> list[str]:
    """Make whatever is missing. Returns the names that could not be made."""
    os.makedirs(folder, exist_ok=True)
    missing = []
    if have_pillow():
        _stills(folder)
        _exif(folder)
        _page(folder)
    else:
        missing.extend(STILLS)
    if have_gst():
        _clips(folder)
    else:
        missing.extend(CLIPS)
    if have_pillow() and have_ffmpeg():
        _tagged_audio(folder)
    _sidecar(folder)
    return [name for name in missing
            if not os.path.exists(os.path.join(folder, name))]


def _stills(folder):
    from PIL import Image

    path = os.path.join(folder, "test.png")
    if not os.path.exists(path):
        width, height = 320, 200
        image = Image.new("RGB", (width, height))
        pixels = image.load()
        for y in range(height):
            for x in range(width):
                if y < height // 2:
                    # A luminance ramp, banded with a warm patch every 20px, so
                    # that both contrast and saturation changes are measurable.
                    value = int(255 * x / (width - 1))
                    pixels[x, y] = ((value, value, value) if (x // 20) % 2 == 0
                                    else (value, int(value * 0.6),
                                          int(value * 0.3)))
                else:
                    t = y / (height - 1)
                    pixels[x, y] = (int(60 + 150 * t), int(90 + 120 * t),
                                    int(180 + 60 * t))
        image.save(path)
        image.save(os.path.join(folder, "test.jpg"), quality=92)

    path = os.path.join(folder, "alpha.png")
    if not os.path.exists(path):
        image = Image.new("RGBA", (120, 80), (0, 0, 0, 0))
        pixels = image.load()
        for y in range(80):
            for x in range(120):
                if (x // 20 + y // 20) % 2 == 0:
                    pixels[x, y] = (230, 90, 60, 255)
        image.save(path)

    path = os.path.join(folder, "anim.gif")
    if not os.path.exists(path):
        frames = [Image.new("RGB", (64, 48), (30 * i, 80, 200 - 20 * i))
                  for i in range(6)]
        frames[0].save(path, save_all=True, append_images=frames[1:],
                       duration=80, loop=0)


# The page photographed by `_page`: where its corners land, as fractions of
# the photograph, clockwise from the top left; the shape of the paper; the
# line printed at the top; and the camera's time.
PAGE_SIZE = (1480, 2100)
PAGE_ASPECT = PAGE_SIZE[0] / PAGE_SIZE[1]
PAGE_PHOTO = (3000, 2250)
PAGE_HEADER = "2026-10-21 Dinamika 14"
PAGE_TAKEN = "2026:10:27 19:05:00"
PAGE_CORNERS: tuple = ()


def _camera(tilt=30.0, turn=6.0, focal=2600.0, distance=3300.0):
    """The page's corners through a pinhole camera tilted towards it."""
    import math

    width, height = PAGE_SIZE
    across, down = PAGE_PHOTO
    t, r = math.radians(tilt), math.radians(turn)
    points = []
    for x, y in ((-width / 2, -height / 2), (width / 2, -height / 2),
                 (width / 2, height / 2), (-width / 2, height / 2)):
        x, y = x * math.cos(r) - y * math.sin(r), x * math.sin(r) + y * math.cos(r)
        depth = distance + y * math.sin(t)
        points.append((across / 2 + focal * x / depth,
                       down / 2 + focal * y * math.cos(t) / depth))
    return points


def _perspective(target, source):
    """PIL's eight coefficients, mapping output points back to input ones."""
    rows, values = [], []
    for (x, y), (u, v) in zip(target, source):
        rows.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        values.append(u)
        rows.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        values.append(v)
    n = len(values)
    m = [row + [value] for row, value in zip(rows, values)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        m[col], m[pivot] = m[pivot], m[col]
        for r in range(n):
            if r != col:
                factor = m[r][col] / m[col][col]
                m[r] = [a - factor * b for a, b in zip(m[r], m[col])]
    return [m[r][n] / m[r][r] for r in range(n)]


def _page(folder):
    global PAGE_CORNERS
    import random

    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    quad = _camera()
    PAGE_CORNERS = tuple((x / PAGE_PHOTO[0], y / PAGE_PHOTO[1]) for x, y in quad)
    path = os.path.join(folder, "page.jpg")
    if os.path.exists(path):
        return
    width, height = PAGE_SIZE
    sheet = Image.new("L", PAGE_SIZE, 236)
    draw = ImageDraw.Draw(sheet)
    for y in range(240, height - 80, 64):
        draw.line([(60, y), (width - 60, y)], fill=200, width=2)
    draw.line([(370, 0), (370, height)], fill=185, width=3)
    try:
        font = ImageFont.load_default(size=64)
    except TypeError:                                   # an old Pillow
        font = ImageFont.load_default()
    draw.text((410, 120), PAGE_HEADER, fill=30, font=font)
    scribble = random.Random(4)
    for row in range(1, 26):
        y = 240 + row * 64 - 22
        x = 410
        while x < width - 200:
            length = scribble.randint(40, 180)
            draw.line([(x, y), (x + length, y - 4)], fill=scribble.randint(30, 70), width=5)
            x += length + scribble.randint(20, 50)
    draw.rectangle([700, 1500, 900, 1700], fill=50)

    rectangle = ((0, 0), (width, 0), (width, height), (0, height))
    coefficients = _perspective(quad, rectangle)
    photo = Image.new("RGB", PAGE_PHOTO, (70, 52, 40))
    warped = sheet.convert("RGB").transform(PAGE_PHOTO, Image.PERSPECTIVE,
                                            coefficients, Image.BILINEAR)
    mask = Image.new("L", PAGE_SIZE, 255).transform(PAGE_PHOTO, Image.PERSPECTIVE,
                                                   coefficients, Image.BILINEAR)
    photo.paste(warped, (0, 0), mask)
    # The light falls off towards one corner, as a desk lamp's does.
    falloff = Image.linear_gradient("L").resize(PAGE_PHOTO).rotate(35)
    dim = Image.eval(photo, lambda v: int(v * 0.55))
    photo = Image.composite(photo, dim, falloff.point(lambda v: 255 - v // 2))
    photo = photo.filter(ImageFilter.GaussianBlur(1.2))
    exif = Image.Exif()
    exif.get_ifd(0x8769)[0x9003] = PAGE_TAKEN
    photo.save(path, quality=88, exif=exif.tobytes())


def _clips(folder):
    """Two clips and a sound file, through gst-launch.

    `ultrafast` and a low bitrate because nothing here is being judged on
    picture quality, and because the encode is the slowest part of a test run.
    """
    jobs = [
        ("clip.mp4",
         "videotestsrc num-buffers=60 pattern=smpte "
         "! video/x-raw,width=320,height=240,framerate=30/1 "
         "! x264enc speed-preset=ultrafast bitrate=300 ! h264parse "
         "! queue ! mp4mux name=m ! filesink location={path} "
         "audiotestsrc num-buffers=90 wave=sine ! audioconvert "
         "! avenc_aac ! queue ! m."),
        ("multi.mkv",
         "videotestsrc num-buffers=48 pattern=colors "
         "! video/x-raw,width=240,height=180,framerate=24/1 "
         "! x264enc speed-preset=ultrafast bitrate=200 ! h264parse "
         "! queue ! matroskamux name=m ! filesink location={path} "
         "audiotestsrc num-buffers=70 wave=sine freq=440 ! audioconvert "
         "! vorbisenc ! queue ! m. "
         "audiotestsrc num-buffers=70 wave=square freq=220 ! audioconvert "
         "! vorbisenc ! queue ! m."),
        ("tone.ogg",
         "audiotestsrc num-buffers=120 wave=sine freq=330 ! audioconvert "
         "! vorbisenc ! oggmux ! filesink location={path}"),
    ]
    for name, pipeline in jobs:
        path = os.path.join(folder, name)
        if os.path.exists(path):
            continue
        command = [gst_launch(), "-q"] + pipeline.format(
            path=path).split()
        try:
            subprocess.run(command, check=True, capture_output=True,
                           timeout=120)
        except (OSError, subprocess.SubprocessError):
            # Missing encoder, usually. The suite reports it as skipped.
            if os.path.exists(path):
                os.remove(path)


def _tagged_audio(folder):
    from PIL import Image

    path = os.path.join(folder, "withart.mp3")
    if os.path.exists(path):
        return
    cover = os.path.join(folder, "cover.jpg")
    if not os.path.exists(cover):
        Image.new("RGB", (300, 300), (200, 70, 110)).save(cover)
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error",
             "-f", "lavfi", "-i", "sine=f=440:d=2", "-i", cover,
             "-map", "0:a", "-map", "1:v", "-c:v", "mjpeg",
             "-disposition:v", "attached_pic", "-id3v2_version", "3",
             "-metadata", "artist=A Test", "-metadata", "album=Fixtures",
             path],
            check=True, capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        if os.path.exists(path):
            os.remove(path)


def _sidecar(folder):
    path = os.path.join(folder, "clip.srt")
    if os.path.exists(path):
        return
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("1\n00:00:00,500 --> 00:00:01,500\nThe first line.\n\n"
                     "2\n00:00:01,600 --> 00:00:02,000\nAnd a second.\n")


# ------------------------------------------------------------------- EXIF ----
# A JPEG with an EXIF block assembled byte by byte. Deliberately not written
# with a library: a parser tested against the output of the library that
# inspired it only proves the two agree.

EXIF_EXPECTED = {
    "camera": "FUJIFILM X-T5",
    "lens": "XF 35mm F1.4 R",
    "exposure": "1/250 s",
    "aperture": "f/2.8",
    "iso": "ISO 400",
    "flash": "Yes, auto",
}

# Trafalgar Square, to five decimal places, which is what the parser should
# come back with from the degrees-minutes-seconds in the file.
EXIF_LATITUDE = 51.50726
EXIF_LONGITUDE = -0.12757


def _exif(folder):
    import io

    from PIL import Image

    path = os.path.join(folder, "exif.jpg")
    if os.path.exists(path):
        return
    image = Image.new("RGB", (640, 480), (90, 120, 160))
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=80)
    jpeg = buffer.getvalue()

    def ascii_tag(tag, text):
        return tag, 2, len(text) + 1, text.encode() + b"\x00"

    def rational(tag, numerator, denominator):
        return tag, 5, 1, struct.pack(">II", numerator, denominator)

    def short(tag, value):
        return tag, 3, 1, struct.pack(">HH", value, 0)

    def rationals(tag, triples):
        return tag, 5, len(triples), b"".join(
            struct.pack(">II", n, d) for n, d in triples)

    def build_ifd(entries, base, following=0):
        out = struct.pack(">H", len(entries))
        extra = b""
        start = base + 2 + len(entries) * 12 + 4
        for tag, kind, count, data in sorted(entries):
            if len(data) <= 4:
                payload = data + b"\x00" * (4 - len(data))
            else:
                payload = struct.pack(">I", start + len(extra))
                extra += data
            out += struct.pack(">HHI", tag, kind, count) + payload
        return out + struct.pack(">I", following), extra

    exif_entries = [
        rational(0x829A, 1, 250), rational(0x829D, 28, 10), short(0x8827, 400),
        ascii_tag(0x9003, "2024:06:01 18:22:04"), rational(0x920A, 35, 1),
        short(0xA405, 52), short(0x9209, 0x19),
        ascii_tag(0xA434, "XF 35mm F1.4 R"),
    ]
    gps_entries = [
        ascii_tag(1, "N"), rationals(2, [(51, 1), (30, 1), (2613, 100)]),
        ascii_tag(3, "W"), rationals(4, [(0, 1), (7, 1), (3927, 100)]),
        rational(6, 23, 1),
    ]
    ifd0_entries = [
        ascii_tag(0x010F, "FUJIFILM"), ascii_tag(0x0110, "X-T5"),
        short(0x0112, 1), ascii_tag(0x0132, "2024:06:01 18:22:04"),
        ascii_tag(0x013B, "R. Rovenszky"),
    ]
    # IFD0 holds pointers to the other two, so its own size has to be known
    # before they can be placed.
    count = len(ifd0_entries) + 2
    ifd0_size = 2 + count * 12 + 4
    exif_base = 8 + ifd0_size
    exif_bytes, exif_extra = build_ifd(exif_entries, exif_base)
    gps_base = exif_base + len(exif_bytes) + len(exif_extra)
    gps_bytes, gps_extra = build_ifd(gps_entries, gps_base)

    full = ifd0_entries + [(0x8769, 4, 1, struct.pack(">I", exif_base)),
                           (0x8825, 4, 1, struct.pack(">I", gps_base))]
    tail = gps_base + len(gps_bytes) + len(gps_extra)
    out = struct.pack(">H", len(full))
    extra = b""
    for tag, kind, size, data in sorted(full):
        if len(data) <= 4:
            payload = data + b"\x00" * (4 - len(data))
        else:
            payload = struct.pack(">I", tail + len(extra))
            extra += data
        out += struct.pack(">HHI", tag, kind, size) + payload
    out += struct.pack(">I", 0)

    tiff = (b"MM" + struct.pack(">HI", 42, 8) + out + exif_bytes + exif_extra
            + gps_bytes + gps_extra + extra)
    app1 = (b"\xFF\xE1" + struct.pack(">H", len(tiff) + 8)
            + b"Exif\x00\x00" + tiff)
    with open(path, "wb") as handle:
        handle.write(jpeg[:2] + app1 + jpeg[2:])
