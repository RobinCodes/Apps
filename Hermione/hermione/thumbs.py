"""Pictures of files, made once and kept where everything else can find them.

Thumbnails go in `~/.cache/thumbnails` under the freedesktop naming rules: the
MD5 of the file's URI, in a `normal` (128px) or `large` (256px) directory,
written as a PNG carrying `Thumb::URI` and `Thumb::MTime`. That is a few more
rules than inventing a cache of our own would be, and it buys two things worth
having: a folder of photos another file manager has already been through
appears instantly here, and the work this app does is not thrown away the
moment the user opens a different one.

The MTime in the PNG is what makes it safe. A thumbnail whose recorded time
does not match the file's own is stale — the file was edited, or a different
file took its name — and is regenerated rather than shown.

Generation happens on a small pool of threads. Small on purpose: four threads
decoding 24-megapixel JPEGs will use more memory than the machine this is
written on has, and the disk is the limit anyway. Work is dropped rather than
queued when the user has moved on — a scroll through a big folder must not
leave a thousand jobs behind it.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")

from gi.repository import Gdk, GdkPixbuf, GLib  # noqa: E402

from . import filetypes, winenv  # noqa: E402

APP = "hermione"

# The spec's sizes. A thumbnail is asked for at the size the view wants and
# served from the smallest cache directory that can satisfy it.
SIZES = (("normal", 128), ("large", 256), ("x-large", 512), ("xx-large", 1024))


def cache_root() -> str:
    return os.path.join(winenv.cache_home(), "thumbnails")


def uri_for(path: str) -> str:
    try:
        return GLib.filename_to_uri(os.path.abspath(path), None)
    except GLib.Error:
        # A name that is not valid UTF-8 still deserves a thumbnail.
        return "file://" + os.path.abspath(path)


def digest_for(path: str) -> str:
    return hashlib.md5(uri_for(path).encode("utf-8")).hexdigest()


def bucket_for(size: int) -> tuple[str, int]:
    """The cache directory a request of `size` pixels belongs in."""
    for name, pixels in SIZES:
        if size <= pixels:
            return name, pixels
    return SIZES[-1]


def cached_path(path: str, size: int = 256) -> str:
    bucket, _pixels = bucket_for(size)
    return os.path.join(cache_root(), bucket, digest_for(path) + ".png")


def failed_path(path: str) -> str:
    return os.path.join(cache_root(), "fail", APP, digest_for(path) + ".png")


def _stamp(path: str) -> str:
    try:
        return str(int(os.lstat(path).st_mtime))
    except OSError:
        return ""


def valid_cached(path: str, size: int = 256) -> str:
    """A usable cached thumbnail for `path`, or "".

    Checks the recorded mtime, which is the whole point of the spec: a cache
    keyed on the name alone would show yesterday's picture of a file that has
    been replaced since.
    """
    wanted = _stamp(path)
    if not wanted:
        return ""
    bucket, _pixels = bucket_for(size)
    # A bigger thumbnail than asked for is better than none; a smaller one
    # would be blurry, so it is not used.
    order = [name for name, _p in SIZES]
    for name in order[order.index(bucket):]:
        candidate = os.path.join(cache_root(), name, digest_for(path) + ".png")
        if not os.path.exists(candidate):
            continue
        if _recorded_mtime(candidate) == wanted:
            return candidate
    return ""


def _recorded_mtime(png: str) -> str:
    """The Thumb::MTime a thumbnail PNG carries, without decoding the image."""
    try:
        # The text chunks come before the image data, so the head of the file
        # is enough — decoding the whole thing here would defeat the purpose
        # of the check. 64 kB rather than a few: a thumbnailer is allowed to
        # put a colour profile in front of them.
        with open(png, "rb") as handle:
            head = handle.read(65536)
    except OSError:
        return ""
    marker = b"Thumb::MTime"
    index = head.find(marker)
    if index < 0:
        return ""
    rest = head[index + len(marker):index + len(marker) + 24]
    digits = b""
    for byte in rest:
        char = bytes([byte])
        if char.isdigit():
            digits += char
        elif digits:
            break
    return digits.decode("ascii", "ignore")


def known_bad(path: str) -> bool:
    """Has this file already failed to produce a thumbnail?

    Recorded so that a folder of 300 unreadable videos is one attempt each,
    not one attempt per scroll.
    """
    marker = failed_path(path)
    if not os.path.exists(marker):
        return False
    return _recorded_mtime(marker) == _stamp(path)


def can_thumbnail(entry, limit_mb: int = 80) -> bool:
    """Is this worth trying?

    The size limit is the important half. Decoding a 2 GB raw video or a
    400-megapixel TIFF to draw a 72px tile is how a file manager comes to a
    halt in a folder of them.
    """
    if entry.is_dir or entry.broken:
        return False
    category = entry.category
    if category not in ("image", "video", "document"):
        return False
    if category == "document" and filetypes.extension(entry.name) != "pdf":
        return False
    if limit_mb and entry.size > limit_mb * 1024 * 1024:
        return False
    if category == "image":
        ext = filetypes.extension(entry.name)
        if ext and ext not in filetypes.PIXBUF_READABLE and not _has_converter():
            return False
    elif category == "video" and not (winenv.tool("ffmpegthumbnailer")
                                      or winenv.tool("ffmpeg")):
        return False
    elif category == "document" and not (winenv.tool("pdftoppm")
                                         or winenv.tool("pdftocairo")):
        return False
    return True


def _has_converter() -> bool:
    return bool(winenv.tool("magick") or winenv.tool("convert"))


# ------------------------------------------------------------ generating ---

def generate(path: str, size: int = 256) -> str:
    """Make a thumbnail for `path` and return where it was written, or "".

    Runs on a worker thread. Writes the spec's text chunks, writes the
    failure marker when it cannot, and never raises.
    """
    bucket, pixels = bucket_for(size)
    out = os.path.join(cache_root(), bucket, digest_for(path) + ".png")
    stamp = _stamp(path)
    if not stamp:
        return ""
    try:
        os.makedirs(os.path.dirname(out), exist_ok=True)
    except OSError:
        return ""

    category = filetypes.category(os.path.basename(path))
    pixbuf = None
    try:
        if category == "image":
            pixbuf = _image_pixbuf(path, pixels)
        elif category == "video":
            pixbuf = _video_pixbuf(path, pixels)
        elif category == "document":
            pixbuf = _pdf_pixbuf(path, pixels)
    except (GLib.Error, OSError, subprocess.SubprocessError, ValueError):
        pixbuf = None

    if pixbuf is None:
        _mark_failed(path, stamp)
        return ""

    try:
        pixbuf.savev(out, "png",
                     ["tEXt::Thumb::URI", "tEXt::Thumb::MTime",
                      "tEXt::Software"],
                     [uri_for(path), stamp, "Hermione"])
    except (GLib.Error, OSError):
        return ""
    return out


def _image_pixbuf(path: str, pixels: int):
    """An image, scaled down on load rather than after it.

    `new_from_file_at_scale` lets the loader skip most of the work for a JPEG
    — it decodes at a reduced scale — which is the difference between a
    folder of photos appearing at once and appearing one second at a time.
    """
    ext = filetypes.extension(os.path.basename(path))
    if ext and ext not in filetypes.PIXBUF_READABLE:
        return _converted_pixbuf(path, pixels)
    pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, pixels, pixels, True)
    if pixbuf is None:
        return None
    # Phones write the orientation in EXIF rather than rotating the pixels.
    rotated = pixbuf.apply_embedded_orientation()
    return rotated or pixbuf


def _converted_pixbuf(path: str, pixels: int):
    tool = winenv.tool("magick") or winenv.tool("convert")
    if not tool:
        return None
    args = ([tool, path + "[0]", "-auto-orient", "-thumbnail",
             f"{pixels}x{pixels}", "png:-"] if tool.endswith("convert")
            else [tool, path + "[0]", "-auto-orient", "-thumbnail",
                  f"{pixels}x{pixels}", "png:-"])
    done = subprocess.run(args, capture_output=True, timeout=25,
                          creationflags=winenv.NO_WINDOW)
    if done.returncode != 0 or not done.stdout:
        return None
    return _pixbuf_from_bytes(done.stdout)


def _video_pixbuf(path: str, pixels: int):
    tool = winenv.tool("ffmpegthumbnailer")
    if tool:
        done = subprocess.run([tool, "-i", path, "-o", "-", "-c", "png",
                               "-s", str(pixels), "-q", "8"],
                              capture_output=True, timeout=30,
                              creationflags=winenv.NO_WINDOW)
        if done.returncode == 0 and done.stdout:
            return _pixbuf_from_bytes(done.stdout)
    tool = winenv.tool("ffmpeg")
    if not tool:
        return None
    # A few seconds in, because the first frame of a film is usually black.
    done = subprocess.run([tool, "-v", "error", "-ss", "3", "-i", path,
                           "-frames:v", "1", "-vf",
                           f"scale={pixels}:-1", "-f", "image2pipe",
                           "-vcodec", "png", "-"],
                          capture_output=True, timeout=30,
                          creationflags=winenv.NO_WINDOW)
    if done.returncode != 0 or not done.stdout:
        return None
    return _pixbuf_from_bytes(done.stdout)


def _pdf_pixbuf(path: str, pixels: int):
    tool = winenv.tool("pdftoppm")
    if tool:
        done = subprocess.run([tool, "-png", "-f", "1", "-l", "1",
                               "-scale-to", str(pixels), path],
                              capture_output=True, timeout=30,
                              creationflags=winenv.NO_WINDOW)
        if done.returncode == 0 and done.stdout:
            return _pixbuf_from_bytes(done.stdout)
    tool = winenv.tool("pdftocairo")
    if not tool:
        return None
    done = subprocess.run([tool, "-png", "-f", "1", "-l", "1",
                           "-scale-to", str(pixels), path, "-"],
                          capture_output=True, timeout=30,
                          creationflags=winenv.NO_WINDOW)
    if done.returncode != 0 or not done.stdout:
        return None
    return _pixbuf_from_bytes(done.stdout)


def _pixbuf_from_bytes(data: bytes):
    loader = GdkPixbuf.PixbufLoader()
    try:
        loader.write(data)
        loader.close()
    except GLib.Error:
        try:
            loader.close()
        except GLib.Error:
            pass
        return None
    return loader.get_pixbuf()


def _mark_failed(path: str, stamp: str) -> None:
    marker = failed_path(path)
    try:
        os.makedirs(os.path.dirname(marker), exist_ok=True)
        # The spec says a 1x1 PNG with the same text chunks.
        blank = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 1, 1)
        blank.fill(0)
        blank.savev(marker, "png",
                    ["tEXt::Thumb::URI", "tEXt::Thumb::MTime"],
                    [uri_for(path), stamp])
    except (GLib.Error, OSError):
        pass


# -------------------------------------------------------------- the pool ---

class Thumbnailer:
    """Thumbnails on demand, a couple at a time, newest request first.

    `request` is called from the GTK thread for a row that has come into
    view; `on_ready(entry, texture)` is called back on the GTK thread. Work
    for rows that have since scrolled away is dropped by generation number
    rather than cancelled, which is both simpler and enough: the unit of work
    is one thumbnail and it is short.
    """

    def __init__(self, size: int = 256, limit_mb: int = 80, workers: int = 2):
        self.size = size
        self.limit_mb = limit_mb
        self.workers = max(1, workers)
        self._lock = threading.Lock()
        self._queue: list[tuple[int, object]] = []      # (generation, entry)
        self._pending: set[str] = set()
        self._running = 0
        self._generation = 0
        self._on_ready = None

    def set_callback(self, on_ready) -> None:
        self._on_ready = on_ready

    def set_size(self, size: int) -> None:
        if size != self.size:
            self.size = size
            self.drop_pending()

    def drop_pending(self) -> None:
        """Forget everything not yet started. For a folder change."""
        with self._lock:
            self._generation += 1
            self._queue.clear()
            self._pending.clear()

    def texture_for(self, entry) -> Gdk.Texture | None:
        """A thumbnail that already exists on disk, loaded now.

        Called on the GTK thread while a row is being bound, so it has to be
        cheap: it only ever touches the cache, never a generator.
        """
        if entry.thumb is not None:
            return entry.thumb
        cached = valid_cached(entry.path, self.size)
        if not cached:
            return None
        try:
            texture = Gdk.Texture.new_from_filename(cached)
        except GLib.Error:
            return None
        entry.thumb = texture
        return texture

    def request(self, entry) -> None:
        """Ask for a thumbnail that is not in the cache yet."""
        if entry.thumb is not None or entry.thumb_tried:
            return
        if not can_thumbnail(entry, self.limit_mb):
            entry.thumb_tried = True
            return
        with self._lock:
            if entry.path in self._pending:
                return
            self._pending.add(entry.path)
            # Last asked for, first made: the rows under the cursor are the
            # ones the user is looking at.
            self._queue.append((self._generation, entry))
            start = self._running < self.workers
            if start:
                self._running += 1
        if start:
            threading.Thread(target=self._work, daemon=True,
                             name="hermione-thumbs").start()

    def _work(self) -> None:
        while True:
            with self._lock:
                while self._queue:
                    generation, entry = self._queue.pop()
                    if generation == self._generation:
                        break
                    self._pending.discard(entry.path)
                else:
                    self._running -= 1
                    return
            if known_bad(entry.path):
                entry.thumb_tried = True
                with self._lock:
                    self._pending.discard(entry.path)
                continue
            png = generate(entry.path, self.size)
            with self._lock:
                self._pending.discard(entry.path)
                stale = generation != self._generation
            entry.thumb_tried = True
            if png and not stale:
                GLib.idle_add(self._deliver, entry, png,
                              priority=GLib.PRIORITY_DEFAULT_IDLE)

    def _deliver(self, entry, png: str) -> bool:
        try:
            entry.thumb = Gdk.Texture.new_from_filename(png)
        except GLib.Error:
            return False
        if self._on_ready is not None:
            self._on_ready(entry)
        else:
            entry.touch()
        return False
