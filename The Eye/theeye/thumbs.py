"""Thumbnails: making them, caching them, and not making them twice.

A strip of a hundred thumbnails is a hundred decodes, and for video a hundred
GStreamer pipelines. Neither can happen on the main loop and neither should
happen twice, so this module is three caches in front of two makers.

*The texture cache* holds what has already been decoded this session, keyed by
path and size, with the least recently used dropped when it is full. A
thumbnail is around 50KB as a texture, so a few hundred of them is a few tens
of megabytes — worth holding, not worth holding without a limit.

*The disk cache* holds a PNG per thumbnail under the cache directory, named
after a hash of the path, the size, and the file's own modification time and
length. Putting the modification time in the *name* rather than checking it
afterwards is what makes the cache self-invalidating: an edited picture has a
different key, so it cannot be served a stale thumbnail, and the old entry is
simply never asked for again.

*The in-flight set* stops the same thumbnail being started twice, which
otherwise happens constantly — the strip asks for what is visible, the user
scrolls back, and the same request arrives before the first has finished.

Requests are served by a small pool of worker threads, newest first. Last-in
first-out is deliberate: when somebody scrolls quickly through a thousand
files, the thumbnails worth decoding are the ones now on screen, not the ones
that were on screen two seconds ago.
"""

from __future__ import annotations

import hashlib
import os
import queue
import threading

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gst", "1.0")

from gi.repository import Gdk, GdkPixbuf, GLib, Gst  # noqa: E402

from . import media, photo, player, winenv

CACHE_DIR = os.path.join(winenv.cache_home("the-eye"), "thumbnails")

# Enough for a long strip and a few screens of scrollback.
MEMORY_LIMIT = 512

# Two threads. One is not enough to keep a strip filling while a video poster
# is being extracted; four would have four GStreamer pipelines competing for
# the same disk, which is slower than two.
WORKERS = 2

# Where in a video to take the poster from. Not the first frame: films open on
# black, and a strip of black rectangles is no use at all.
POSTER_AT = 0.12
POSTER_MINIMUM = 1.0            # but never past this if the clip is tiny
POSTER_TIMEOUT = 5 * Gst.SECOND


def _key(path: str, size: int) -> str | None:
    """A cache name that changes when the file does."""
    try:
        stat = os.stat(path)
    except OSError:
        return None
    seed = f"{os.path.abspath(path)}\x00{size}\x00{int(stat.st_mtime)}\x00{stat.st_size}"
    return hashlib.sha256(seed.encode("utf-8", "replace")).hexdigest()


class Cache:
    """Thumbnails for whoever asks, eventually.

    `get` is the whole interface. It answers immediately from memory when it
    can, and otherwise returns None and calls back on the main loop when the
    thumbnail is ready. A caller that is gone by then is handled by the
    callback itself checking — this module never holds a widget.
    """

    def __init__(self, size: int = 108):
        self.size = size
        self._memory: dict[tuple[str, int], Gdk.Texture] = {}
        self._order: list[tuple[str, int]] = []
        self._pending: set[tuple[str, int]] = set()
        self._failed: set[tuple[str, int]] = set()
        # Per cache, not per class: two caches at different sizes must not
        # deliver each other's callbacks.
        self._waiting: dict[tuple[str, int], list] = {}
        self._lock = threading.Lock()
        self._queue: queue.LifoQueue = queue.LifoQueue()
        self._threads: list[threading.Thread] = []
        self._generation = 0

    # -- asking -----------------------------------------------------------

    def get(self, path: str, size: int = 0, on_ready=None) -> Gdk.Texture | None:
        """A thumbnail now, or None and `on_ready(path, texture)` later."""
        size = size or self.size
        key = (path, size)
        with self._lock:
            found = self._memory.get(key)
            if found is not None:
                self._touch(key)
                return found
            if key in self._failed:
                return None
            if key in self._pending:
                if on_ready is not None:
                    self._waiting.setdefault(key, []).append(on_ready)
                return None
            self._pending.add(key)
            if on_ready is not None:
                self._waiting.setdefault(key, []).append(on_ready)
            generation = self._generation
        self._ensure_workers()
        self._queue.put((generation, path, size))
        return None

    def have(self, path: str, size: int = 0) -> bool:
        return (path, size or self.size) in self._memory

    def forget(self, path: str):
        """Drop every size of one file — after it has been edited or deleted."""
        with self._lock:
            for key in [k for k in self._memory if k[0] == path]:
                self._memory.pop(key, None)
                if key in self._order:
                    self._order.remove(key)
            self._failed = {k for k in self._failed if k[0] != path}

    def clear(self):
        """Forget everything, and abandon whatever is queued.

        The generation counter is how work already in the queue is abandoned
        without being able to cancel it: a worker checks the generation it was
        given against the current one and drops the job if the strip has moved
        on. Cheaper and more reliable than a cancellation flag per request.
        """
        with self._lock:
            self._memory.clear()
            self._order.clear()
            self._pending.clear()
            self._failed.clear()
            self._waiting.clear()
            self._generation += 1

    # -- the workers ------------------------------------------------------

    def _ensure_workers(self):
        if self._threads:
            return
        for index in range(WORKERS):
            thread = threading.Thread(target=self._work, daemon=True,
                                      name=f"thumbs-{index}")
            thread.start()
            self._threads.append(thread)

    def _work(self):
        while True:
            generation, path, size = self._queue.get()
            try:
                with self._lock:
                    stale = generation != self._generation
                if stale:
                    continue
                texture = self._make(path, size)
                GLib.idle_add(self._landed, path, size, texture,
                              priority=GLib.PRIORITY_DEFAULT_IDLE)
            except Exception:                           # noqa: BLE001
                GLib.idle_add(self._landed, path, size, None)
            finally:
                self._queue.task_done()

    def _landed(self, path, size, texture):
        key = (path, size)
        with self._lock:
            self._pending.discard(key)
            if texture is not None:
                self._memory[key] = texture
                self._touch(key)
                self._trim()
            else:
                self._failed.add(key)
            callbacks = self._waiting.pop(key, [])
        for callback in callbacks:
            try:
                callback(path, texture)
            except Exception:                           # noqa: BLE001
                pass
        return GLib.SOURCE_REMOVE

    def _touch(self, key):
        try:
            self._order.remove(key)
        except ValueError:
            pass
        self._order.append(key)

    def _trim(self):
        while len(self._order) > MEMORY_LIMIT:
            oldest = self._order.pop(0)
            self._memory.pop(oldest, None)

    # -- making -----------------------------------------------------------

    def _make(self, path: str, size: int) -> Gdk.Texture | None:
        """Decode or extract a thumbnail. Runs on a worker thread."""
        key = _key(path, size)
        cached = self._from_disk(key) if key else None
        if cached is not None:
            return cached
        if media.is_audio(path):
            # A sound file has no frames to grab, but it may carry a picture
            # in its tags, and a strip of blank boxes through a music folder
            # is no use to anybody.
            texture = album_art(path, size)
        elif media.is_playable(path):
            texture = poster(path, size)
        else:
            texture = photo.thumbnail(path, size)
        if texture is not None and key:
            self._to_disk(key, texture)
        return texture

    def _from_disk(self, key: str) -> Gdk.Texture | None:
        path = os.path.join(CACHE_DIR, key[:2], key + ".png")
        if not os.path.isfile(path):
            return None
        try:
            return Gdk.Texture.new_from_filename(path)
        except GLib.Error:
            # A half-written file from an interrupted run. Remove it rather
            # than failing every time it is asked for.
            try:
                os.remove(path)
            except OSError:
                pass
            return None

    def _to_disk(self, key: str, texture: Gdk.Texture):
        folder = os.path.join(CACHE_DIR, key[:2])
        path = os.path.join(folder, key + ".png")
        try:
            os.makedirs(folder, exist_ok=True)
            # Through a temporary file in the same folder, so that an
            # interrupted write cannot leave a truncated PNG where a complete
            # one is expected.
            tmp = path + f".{os.getpid()}.tmp"
            texture.save_to_png(tmp)
            os.replace(tmp, path)
        except (OSError, GLib.Error):
            pass


def cache_size() -> int:
    """How much disk the thumbnails are using — for the preferences page."""
    total = 0
    for root, _dirs, files in os.walk(CACHE_DIR):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def empty_cache() -> int:
    """Delete every cached thumbnail; returns how many went."""
    gone = 0
    for root, _dirs, files in os.walk(CACHE_DIR, topdown=False):
        for name in files:
            try:
                os.remove(os.path.join(root, name))
                gone += 1
            except OSError:
                pass
        try:
            os.rmdir(root)
        except OSError:
            pass
    return gone


# --------------------------------------------------------------- album art ----

def album_art(path: str, size: int = 0) -> Gdk.Texture | None:
    """The picture embedded in a sound file's tags, if it has one. Blocking.

    Read through the same discoverer that reads everything else about a
    media file, so this costs one pass over the header rather than a pipeline
    of its own. The tag holds a whole encoded JPEG or PNG rather than raw
    pixels, so it goes through the ordinary loader.
    """
    from . import metadata

    info = metadata.discover(path, 4)
    if info is None:
        return None
    tags = info.get_tags()
    if tags is None:
        return None
    for tag in (Gst.TAG_IMAGE, Gst.TAG_PREVIEW_IMAGE):
        ok, sample = tags.get_sample_index(tag, 0)
        if not ok or sample is None:
            continue
        buffer = sample.get_buffer()
        if buffer is None:
            continue
        mapped_ok, mapped = buffer.map(Gst.MapFlags.READ)
        if not mapped_ok:
            continue
        try:
            data = GLib.Bytes.new(bytes(mapped.data))
        finally:
            buffer.unmap(mapped)
        try:
            texture = Gdk.Texture.new_from_bytes(data)
        except GLib.Error:
            continue
        if size > 0 and max(texture.get_width(), texture.get_height()) > size:
            return _scaled(texture, size)
        return texture
    return None


def _scaled(texture: Gdk.Texture, size: int) -> Gdk.Texture:
    """A texture shrunk to fit a box, keeping its shape.

    Through a pixbuf rather than a render node: this runs on a worker thread,
    where there is no renderer to draw with.
    """
    pixbuf = Gdk.pixbuf_get_from_texture(texture)
    if pixbuf is None:
        return texture
    width, height = pixbuf.get_width(), pixbuf.get_height()
    scale = size / max(width, height)
    smaller = pixbuf.scale_simple(max(1, int(width * scale)),
                                  max(1, int(height * scale)),
                                  GdkPixbuf.InterpType.BILINEAR)
    return Gdk.Texture.new_for_pixbuf(smaller or pixbuf)


# ------------------------------------------------------------- video poster ----

def poster(path: str, size: int = 0, at: float = -1.0) -> Gdk.Texture | None:
    """A frame from a video, as a thumbnail. Blocking, and slow.

    A pipeline of its own rather than the player's: it has to run on a worker
    thread, it has to be torn down the moment it has what it came for, and it
    must not disturb whatever is playing.

    The frame comes from a little way in rather than from the start, because
    the start of a film is black and the start of a phone clip is usually the
    inside of a pocket. Pulling it needs a seek, which needs the file to be
    PAUSED first — a preroll — and the whole thing is given a few seconds
    before being abandoned.
    """
    if not player.init():
        return None
    pipeline = Gst.ElementFactory.make("playbin3", None)
    sink = Gst.ElementFactory.make("appsink", None)
    if pipeline is None or sink is None:
        return None
    caps = "video/x-raw,format=RGBA,pixel-aspect-ratio=(fraction)1/1"
    if size > 0:
        # Height as a range with the width free, so the aspect ratio is kept
        # and a panoramic frame does not come back as a tall sliver.
        caps += f",height=(int)[1,{int(size)}]"
    sink.set_property("caps", Gst.Caps.from_string(caps))
    sink.set_property("max-buffers", 1)
    sink.set_property("drop", False)
    sink.set_property("sync", False)

    convert = Gst.ElementFactory.make("videoconvertscale", None)
    flip = Gst.ElementFactory.make("videoflip", None)
    if convert is None or flip is None:
        return None
    try:
        flip.set_property("video-direction", "auto")
    except (TypeError, ValueError):
        pass
    bin_ = Gst.Bin.new(None)
    for element in (flip, convert, sink):
        bin_.add(element)
    flip.link(convert)
    convert.link(sink)
    bin_.add_pad(Gst.GhostPad.new("sink", flip.get_static_pad("sink")))

    pipeline.set_property("video-sink", bin_)
    pipeline.set_property("uri", Gst.filename_to_uri(os.path.abspath(path)))
    # No sound while fetching a picture. Without this, scrolling a folder of
    # videos plays a chord.
    pipeline.set_property("flags", player.FLAG_VIDEO)

    try:
        pipeline.set_state(Gst.State.PAUSED)
        bus = pipeline.get_bus()
        message = bus.timed_pop_filtered(
            POSTER_TIMEOUT, Gst.MessageType.ASYNC_DONE | Gst.MessageType.ERROR)
        if message is None or message.type == Gst.MessageType.ERROR:
            return None

        when = at
        if when < 0:
            ok, duration = pipeline.query_duration(Gst.Format.TIME)
            if ok and duration > 0:
                when = max(0.0, min(duration / Gst.SECOND * POSTER_AT,
                                    duration / Gst.SECOND - 0.1))
                if duration / Gst.SECOND < POSTER_MINIMUM:
                    when = 0.0
            else:
                when = 0.0
        if when > 0.05:
            pipeline.seek_simple(
                Gst.Format.TIME,
                Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT, int(when * Gst.SECOND))
            bus.timed_pop_filtered(POSTER_TIMEOUT,
                                   Gst.MessageType.ASYNC_DONE | Gst.MessageType.ERROR)

        sample = sink.try_pull_preroll(POSTER_TIMEOUT)
        if sample is None:
            sample = sink.try_pull_sample(POSTER_TIMEOUT)
        if sample is None:
            return None
        return _texture_from_sample(sample)
    except Exception:                                   # noqa: BLE001
        return None
    finally:
        # Always, and before the local goes out of scope: GStreamer leaks and
        # complains if an element is disposed from any state but NULL.
        pipeline.set_state(Gst.State.NULL)


def _texture_from_sample(sample) -> Gdk.Texture | None:
    from gi.repository import GstVideo

    buffer = sample.get_buffer()
    caps = sample.get_caps()
    if buffer is None or caps is None:
        return None
    info = GstVideo.VideoInfo.new_from_caps(caps)
    if info is None or info.width <= 0 or info.height <= 0:
        return None
    stride = info.stride[0] if info.stride else info.width * 4
    ok, mapped = buffer.map(Gst.MapFlags.READ)
    if not ok:
        return None
    try:
        needed = stride * info.height
        data = bytes(mapped.data)
        if len(data) < needed:
            return None
        return Gdk.MemoryTexture.new(
            info.width, info.height, Gdk.MemoryFormat.R8G8B8A8,
            GLib.Bytes.new(data[:needed]), stride)
    finally:
        buffer.unmap(mapped)
