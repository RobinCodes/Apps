"""The video engine: a GStreamer pipeline, and the frames it hands to GTK.

GTK can play video on its own — `GtkVideo` wraps a `GtkMediaFile`, and on a
machine with the right GStreamer module installed it works. It is not used
here, for two reasons. The small one is that the module is often not
installed, and an app whose central feature depends on an optional package is
an app that is broken on most machines. The large one is that `GtkMediaStream`
is a deliberately minimal interface — play, pause, seek, volume — and
everything that separates a video player from a video preview is outside it:
the playback rate, which of the three audio tracks to use, which subtitle
file, how far out of sync the sound is, stepping one frame at a time.

So the pipeline is built here. `playbin3` does the work that is genuinely
hard — demuxing, decoding, picking hardware decoders, keeping sound and
picture together — and its video output is an `appsink` that hands each frame
to `FramePaintable`, which is an ordinary `GdkPaintable` that any GTK widget
can draw. That the frames pass through the app rather than straight to a
window is what lets the grade apply to video at all: by the time a frame
reaches the screen it is a texture in a render node tree, and `effects.py`
treats it exactly as it treats a photograph.

The frames are scaled down in the pipeline, once, to no larger than the
biggest monitor attached. A 4K frame is 33MB of RGBA and copying one per
frame is more than a slow machine can do; at screen size it is 4MB and
`videoscale` does the work in C. Nothing is lost — the frame was going to be
scaled to the window anyway, and scaling twice is what would cost quality.

Threading: `appsink` delivers on a GStreamer streaming thread, where touching
a widget would eventually crash the process. Building the texture is safe
there and is done there; handing it over is a latest-wins slot read back on
the main loop, so a main loop that falls behind drops frames rather than
queueing them, which is the behaviour you want when the machine cannot keep
up.
"""

from __future__ import annotations

import os
import threading

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gst", "1.0")
gi.require_version("GstApp", "1.0")
gi.require_version("GstAudio", "1.0")
gi.require_version("GstVideo", "1.0")
gi.require_version("Gsk", "4.0")

from gi.repository import (Gdk, GLib, GObject, Graphene, Gsk, Gst,  # noqa: E402,F401
                           GstApp, GstAudio, GstVideo)

# Resolved once rather than per frame: an enum lookup through PyGObject is a
# dictionary walk, and this one would happen on every frame drawn.
LINEAR = Gsk.ScalingFilter.LINEAR

NANOS = Gst.SECOND

# playbin3's own flags, which have no Python constants of their own because
# GstPlayFlags is a private enumeration. The two that matter here are
# DEINTERLACE, which is off by default and wanted for anything from a
# camcorder or a broadcast, and FORCE_SW_DECODERS, which is how a hardware
# decoder that produces a corrupt picture gets taken out of the way.
FLAG_VIDEO = 0x001
FLAG_AUDIO = 0x002
FLAG_TEXT = 0x004
FLAG_SOFT_VOLUME = 0x010
FLAG_DEINTERLACE = 0x200
FLAG_SOFT_COLORBALANCE = 0x400
FLAG_FORCE_SW_DECODERS = 0x1000

# Above this rate, play only the keyframes. Decoding every frame of a film at
# eight times speed is work thrown away — the frames cannot all be shown — and
# trick mode is what keeps the sound and the seek bar moving smoothly instead.
TRICKMODE_ABOVE = 2.5

_initialised = False


def init():
    """Start GStreamer once, and say whether it started.

    Called from the application's startup and again from anything that might
    be reached first in a test. GStreamer's own init is idempotent but not
    free, and it is the one thing here that can fail for reasons outside the
    app — a broken plugin registry, usually — so the answer is returned
    rather than assumed.
    """
    global _initialised
    if _initialised:
        return True
    try:
        Gst.init_check(None)
    except Exception:                                   # noqa: BLE001
        return False
    _initialised = True
    return True


def available() -> bool:
    """Is there enough of GStreamer here to play anything at all?"""
    if not init():
        return False
    return all(Gst.ElementFactory.find(name) is not None
               for name in ("playbin3", "appsink", "videoconvertscale"))


# ------------------------------------------------------------- the frames ----

class FramePaintable(GObject.Object, Gdk.Paintable):
    """The most recent video frame, as something any GTK widget can draw.

    A paintable rather than a widget, so that the same drawing code handles a
    photograph and a video: `stage.py` asks for a paintable and does not know
    or care which it got.

    Its intrinsic size is the frame's size *after* the pipeline's scaling, and
    `aspect` carries the real one — a 4K video scaled to 1365x768 has to
    report the aspect ratio of the picture rather than of the texture, or a
    window fitting to it would be a pixel out.
    """

    __gtype_name__ = "TheEyeFramePaintable"

    def __init__(self):
        super().__init__()
        self._texture: Gdk.Texture | None = None
        self._width = 0
        self._height = 0
        self.aspect = 0.0
        self.frames = 0

    def set_frame(self, texture: Gdk.Texture | None, aspect: float = 0.0):
        """Take a new frame. Called on the main loop only."""
        self._texture = texture
        changed = False
        if texture is not None:
            width, height = texture.get_width(), texture.get_height()
            if (width, height) != (self._width, self._height):
                self._width, self._height = width, height
                changed = True
            self.frames += 1
        if aspect > 0 and abs(aspect - self.aspect) > 1e-4:
            self.aspect = aspect
            changed = True
        self.invalidate_contents()
        if changed:
            self.invalidate_size()

    def clear(self):
        self._texture = None
        self._width = self._height = 0
        self.aspect = 0.0
        self.frames = 0
        self.invalidate_contents()
        self.invalidate_size()

    def texture(self) -> Gdk.Texture | None:
        return self._texture

    def has_frame(self) -> bool:
        return self._texture is not None

    # -- GdkPaintable ------------------------------------------------------

    def do_get_intrinsic_width(self):
        if self.aspect > 0 and self._height:
            return int(round(self._height * self.aspect))
        return self._width

    def do_get_intrinsic_height(self):
        return self._height

    def do_get_intrinsic_aspect_ratio(self):
        if self.aspect > 0:
            return self.aspect
        if self._width and self._height:
            return self._width / self._height
        return 0.0

    def do_get_flags(self):
        # Neither flag applies, and claiming either is a GDK assertion rather
        # than an optimisation. STATIC_CONTENTS would be a lie about a video.
        # STATIC_SIZE looks true — a clip is one size all the way through —
        # but the size is zero until the first frame arrives, so it does
        # change once, and `invalidate_size` on a paintable that claimed
        # otherwise is a critical warning on every file opened.
        return 0

    def do_get_current_image(self):
        """A still of this moment, which is what a screenshot of a video is."""
        if self._texture is not None:
            return self._texture
        return Gdk.Paintable.new_empty(1, 1)

    def do_snapshot(self, snapshot, width, height):
        if self._texture is None:
            return
        bounds = Graphene.Rect().init(0, 0, width, height)
        # The texture is never magnified by much — it was scaled to the
        # monitor in the pipeline — so linear is the right filter: trilinear
        # would cost a mip chain per frame for a difference nobody can see.
        snapshot.append_scaled_texture(self._texture, LINEAR, bounds)


# ------------------------------------------------------------- the streams ----

class Track:
    """One selectable stream inside a file.

    `ident` is GStreamer's own stream id, which is what a select-streams
    event wants, and is stable for the life of the pipeline.
    """

    __slots__ = ("kind", "ident", "index", "language", "title", "codec",
                 "channels", "width", "height", "rate", "default")

    def __init__(self, kind, ident, index):
        self.kind = kind                # "video" | "audio" | "text"
        self.ident = ident
        self.index = index
        self.language = ""
        self.title = ""
        self.codec = ""
        self.channels = 0
        self.width = 0
        self.height = 0
        self.rate = 0.0
        self.default = False

    def label(self) -> str:
        """What the menu shows: whatever the file actually told us.

        Most files tell us very little, so the fallbacks matter more than the
        good case. In order of preference: a title, a language, the codec and
        the shape, and finally just a number — but never an empty row, which
        is what every player that only reads the title tag ends up showing.
        """
        bits = []
        if self.title:
            bits.append(self.title)
        if self.language and self.language.lower() not in ("und", "unknown"):
            name = LANGUAGES.get(self.language.lower()[:3], self.language)
            if name not in bits:
                bits.append(name)
        if not bits:
            shape = ""
            if self.kind == "video" and self.width:
                shape = f"{self.width}x{self.height}"
            elif self.kind == "audio" and self.channels:
                shape = CHANNELS.get(self.channels, f"{self.channels} channels")
            detail = " ".join(b for b in (self.codec, shape) if b)
            bits.append(detail or f"{self.kind.title()} {self.index + 1}")
        return "  ·  ".join(bits)


# The languages a subtitle or audio track is actually ever tagged with. A
# complete ISO 639 table is 500 lines for the benefit of nobody; anything
# missing falls through to its own code, which is still better than "und".
LANGUAGES = {
    "eng": "English", "fre": "French", "fra": "French", "ger": "German",
    "deu": "German", "spa": "Spanish", "ita": "Italian", "por": "Portuguese",
    "rus": "Russian", "jpn": "Japanese", "chi": "Chinese", "zho": "Chinese",
    "kor": "Korean", "ara": "Arabic", "hin": "Hindi", "dut": "Dutch",
    "nld": "Dutch", "swe": "Swedish", "nor": "Norwegian", "dan": "Danish",
    "fin": "Finnish", "pol": "Polish", "cze": "Czech", "ces": "Czech",
    "hun": "Hungarian", "tur": "Turkish", "gre": "Greek", "ell": "Greek",
    "heb": "Hebrew", "tha": "Thai", "vie": "Vietnamese", "ukr": "Ukrainian",
    "ron": "Romanian", "rum": "Romanian", "bul": "Bulgarian", "hrv": "Croatian",
    "srp": "Serbian", "slo": "Slovak", "slk": "Slovak", "slv": "Slovenian",
    "cat": "Catalan", "ind": "Indonesian", "may": "Malay", "msa": "Malay",
    "fil": "Filipino", "tgl": "Tagalog", "per": "Persian", "fas": "Persian",
    "ben": "Bengali", "tam": "Tamil", "tel": "Telugu", "urd": "Urdu",
    "isl": "Icelandic", "ice": "Icelandic", "est": "Estonian",
    "lav": "Latvian", "lit": "Lithuanian", "lat": "Latin",
}

CHANNELS = {1: "Mono", 2: "Stereo", 3: "2.1", 6: "5.1", 8: "7.1"}


class Chapter:
    __slots__ = ("start", "stop", "title")

    def __init__(self, start, stop, title):
        self.start = start
        self.stop = stop
        self.title = title


# -------------------------------------------------------------- the engine ----

class Engine(GObject.Object):
    """One pipeline, and everything that can be asked of it.

    Signals rather than callbacks, because several parts of the window want
    the same news — the transport bar wants the position, the strip wants to
    know when a file ended, the title wants the tags — and none of them should
    have to know about each other.
    """

    __gtype_name__ = "TheEyeEngine"

    __gsignals__ = {
        # playing, paused or stopped, as a string
        "state": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        # position and duration in seconds, both -1 when unknown
        "progress": (GObject.SignalFlags.RUN_FIRST, None, (float, float)),
        "ended": (GObject.SignalFlags.RUN_FIRST, None, ()),
        # a human message and the technical detail behind it
        "failed": (GObject.SignalFlags.RUN_FIRST, None, (str, str)),
        "tracks": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "tags": (GObject.SignalFlags.RUN_FIRST, None, ()),
        # percent, or -1 when not buffering
        "buffering": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
        "first-frame": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, max_size=(1920, 1080)):
        super().__init__()
        self.paintable = FramePaintable()
        self.path = ""
        self.duration = -1.0
        self.position = 0.0
        self.rate = 1.0
        self.tracks: list[Track] = []
        self.chapters: list[Chapter] = []
        self.tags: dict = {}
        self.artwork: Gdk.Texture | None = None
        self.frame_rate = 0.0
        self.video_size = (0, 0)
        self.has_video = False
        self.has_audio = False
        self.loop_range: tuple[float, float] | None = None
        self.state = "stopped"
        self.error = ""

        self._max_size = max_size
        self._pipeline = None
        self._sink = None
        self._bus = None
        self._poll = 0
        self._pending_seek = None
        self._selected: dict[str, str] = {}
        self._collection = None
        self._seen_frame = False
        self._slot_lock = threading.Lock()
        self._slot = None
        self._slot_scheduled = False
        self._flags = FLAG_VIDEO | FLAG_AUDIO | FLAG_TEXT | FLAG_SOFT_VOLUME
        self._volume = 0.8
        self._muted = False
        self._subtitle_font = "Sans 18"

    # -- building ---------------------------------------------------------

    def _video_sink(self):
        """The bin playbin3 renders into: orient, scale, convert, hand over.

        `videoflip video-direction=auto` is first so that a clip shot on a
        phone and tagged sideways arrives the right way up — the tag is on the
        stream, and anything downstream of the flip sees the corrected size,
        which is what the aspect ratio has to be computed from.
        """
        flip = Gst.ElementFactory.make("videoflip", "orient")
        convert = Gst.ElementFactory.make("videoconvertscale", "convert")
        sink = Gst.ElementFactory.make("appsink", "frames")
        if not all((flip, convert, sink)):
            return None, None
        try:
            flip.set_property("video-direction", "auto")
        except (TypeError, ValueError):
            pass

        width, height = self._max_size
        sink.set_property("caps", Gst.Caps.from_string(
            "video/x-raw,format=RGBA"
            f",width=(int)[1,{max(16, int(width))}]"
            f",height=(int)[1,{max(16, int(height))}]"
            ",pixel-aspect-ratio=(fraction)1/1"))
        # Two buffers and drop: the renderer is the thing that decides how
        # many frames get drawn, and a queue of undrawn frames is latency
        # with extra steps.
        sink.set_property("max-buffers", 2)
        sink.set_property("drop", True)
        sink.set_property("sync", True)
        sink.set_property("emit-signals", True)
        sink.connect("new-sample", self._on_sample)
        sink.connect("new-preroll", self._on_preroll)

        bin_ = Gst.Bin.new("eye-video-sink")
        for element in (flip, convert, sink):
            bin_.add(element)
        flip.link(convert)
        convert.link(sink)
        bin_.add_pad(Gst.GhostPad.new("sink", flip.get_static_pad("sink")))
        return bin_, sink

    def _build(self):
        if self._pipeline is not None:
            return True
        if not init():
            self.error = "GStreamer is not available"
            return False
        pipeline = Gst.ElementFactory.make("playbin3", "eye")
        sink_bin, sink = self._video_sink()
        if pipeline is None or sink_bin is None:
            self.error = "GStreamer is missing the elements needed to play video"
            return False
        pipeline.set_property("video-sink", sink_bin)
        pipeline.set_property("flags", self._flags)
        pipeline.set_property("subtitle-font-desc", self._subtitle_font)
        self._pipeline = pipeline
        self._sink = sink
        self._bus = pipeline.get_bus()
        self._bus.add_signal_watch()
        self._bus.connect("message", self._on_message)
        self._apply_volume()
        return True

    # -- the frames -------------------------------------------------------

    def _texture_from(self, sample) -> tuple[Gdk.Texture | None, float]:
        """A sample as a texture and the aspect ratio of the picture it is of.

        Runs on a GStreamer streaming thread. Creating a texture here is safe
        — a `GdkMemoryTexture` is immutable and takes a reference to the bytes
        — and doing it here rather than on the main loop is most of the point:
        the copy is the expensive part, and the main loop has frames to draw.

        The stride comes from the negotiated caps rather than being assumed to
        be four times the width. `videoscale` can produce an odd width, and
        GStreamer pads rows; a texture built on the assumption would show the
        picture sheared, one pixel further over on every row.
        """
        buffer = sample.get_buffer()
        caps = sample.get_caps()
        if buffer is None or caps is None:
            return None, 0.0
        info = GstVideo.VideoInfo.new_from_caps(caps)
        if info is None:
            return None, 0.0
        width, height = info.width, info.height
        if width <= 0 or height <= 0:
            return None, 0.0
        stride = info.stride[0] if info.stride else width * 4
        ok, mapped = buffer.map(Gst.MapFlags.READ)
        if not ok:
            return None, 0.0
        try:
            needed = stride * height
            data = bytes(mapped.data)
            if len(data) < needed:
                return None, 0.0
            texture = Gdk.MemoryTexture.new(
                width, height, Gdk.MemoryFormat.R8G8B8A8,
                GLib.Bytes.new(data[:needed]), stride)
        finally:
            buffer.unmap(mapped)

        # The display aspect ratio, which is the storage aspect times the
        # pixel aspect. Anamorphic DVD content is the case this exists for:
        # 720x576 pixels that are meant to be shown as 16:9.
        par_n = info.par_n or 1
        par_d = info.par_d or 1
        aspect = (width * par_n) / (height * par_d)
        return texture, aspect

    def _on_sample(self, sink):
        sample = sink.try_pull_sample(0)
        if sample is not None:
            self._offer(sample)
        return Gst.FlowReturn.OK

    def _on_preroll(self, sink):
        """The frame that arrives on pausing, before anything is playing.

        Without this, opening a video and not pressing play would show an
        empty window — and seeking while paused would show the old frame.
        """
        sample = sink.try_pull_preroll(0)
        if sample is not None:
            self._offer(sample)
        return Gst.FlowReturn.OK

    def _offer(self, sample):
        """Hand a frame to the main loop, latest wins.

        One idle callback is outstanding at a time and it reads whatever is in
        the slot when it runs, so a main loop that falls behind shows the
        newest frame and silently drops the ones it missed. Queueing them
        instead would turn a slow machine into a slow *and* late one, and the
        frames would still never be seen.
        """
        pair = self._texture_from(sample)
        if pair[0] is None:
            return
        with self._slot_lock:
            self._slot = pair
            if self._slot_scheduled:
                return
            self._slot_scheduled = True
        GLib.idle_add(self._deliver, priority=GLib.PRIORITY_HIGH_IDLE)

    def _deliver(self):
        with self._slot_lock:
            pair = self._slot
            self._slot = None
            self._slot_scheduled = False
        if pair is not None:
            self.paintable.set_frame(pair[0], pair[1])
            if not self._seen_frame:
                self._seen_frame = True
                self.has_video = True
                # A frame can arrive before the pipeline has finished going to
                # PAUSED, so this is the earliest moment the duration can be
                # asked for — and anything that wants to know how long the
                # file is wants to know it by now.
                self._query_duration()
                self._read_video_caps()
                self.emit("first-frame")
                self.emit("progress", self.position, self.duration)
        return GLib.SOURCE_REMOVE

    # -- the bus ----------------------------------------------------------

    def _on_message(self, _bus, message):
        kind = message.type
        if kind == Gst.MessageType.ERROR:
            error, debug = message.parse_error()
            self.error = error.message if error else "Playback failed"
            self._stop_poll()
            self._set_state_name("stopped")
            self.emit("failed", self.error, debug or "")
        elif kind == Gst.MessageType.WARNING:
            # Warnings are not failures — a missing subtitle font or a decoder
            # falling back to software both arrive here — so they are noted
            # and not shown.
            error, _debug = message.parse_warning()
            if error and not self.error:
                self.error = ""
        elif kind == Gst.MessageType.EOS:
            if self._loop_back():
                return
            self._stop_poll()
            self.position = self.duration if self.duration > 0 else self.position
            self.emit("progress", self.position, self.duration)
            self.emit("ended")
        elif kind == Gst.MessageType.SEGMENT_DONE:
            # An A-B loop: the segment ended, so start it again. Non-flushing,
            # so the sound does not click at the join.
            self._loop_segment(flush=False)
        elif kind == Gst.MessageType.STREAM_COLLECTION:
            collection = message.parse_stream_collection()
            if collection is not None:
                self._collection = collection
                self._read_collection(collection)
        elif kind == Gst.MessageType.STREAMS_SELECTED:
            self._note_selection(message)
        elif kind == Gst.MessageType.TAG:
            self._read_tags(message.parse_tag())
        elif kind == Gst.MessageType.TOC:
            toc, _updated = message.parse_toc()
            if toc is not None:
                self._read_toc(toc)
        elif kind == Gst.MessageType.BUFFERING:
            percent = message.parse_buffering()
            self.emit("buffering", percent if percent < 100 else -1)
        elif kind == Gst.MessageType.DURATION_CHANGED:
            self.duration = -1.0
            self._query_duration()
        elif kind == Gst.MessageType.ASYNC_DONE:
            self._query_duration()
            if self._pending_seek is not None:
                target = self._pending_seek
                self._pending_seek = None
                self.seek(target)
            self.emit("progress", self._query_position(), self.duration)
        elif kind == Gst.MessageType.STATE_CHANGED:
            if message.src is self._pipeline:
                _old, new, _pending = message.parse_state_changed()
                self._on_pipeline_state(new)

    def _on_pipeline_state(self, new):
        if new == Gst.State.PLAYING:
            self._set_state_name("playing")
            self._start_poll()
        elif new == Gst.State.PAUSED:
            self._set_state_name("paused")
            self._stop_poll()
            self._read_video_caps()
        elif new in (Gst.State.NULL, Gst.State.READY):
            self._set_state_name("stopped")
            self._stop_poll()

    def _set_state_name(self, name):
        if name != self.state:
            self.state = name
            self.emit("state", name)

    # -- what the file turned out to contain -------------------------------

    def _read_collection(self, collection):
        tracks = []
        counts = {"video": 0, "audio": 0, "text": 0}
        for index in range(collection.get_size()):
            stream = collection.get_stream(index)
            if stream is None:
                continue
            kind = _stream_kind(stream.get_stream_type())
            if kind is None:
                continue
            track = Track(kind, stream.get_stream_id(), counts[kind])
            counts[kind] += 1
            flags = stream.get_stream_flags()
            track.default = bool(flags & Gst.StreamFlags.SELECT)
            _describe_stream(track, stream)
            tracks.append(track)
        self.tracks = tracks
        self.has_audio = counts["audio"] > 0
        if counts["video"] == 0:
            # Nothing will ever arrive at the sink, so anything waiting for a
            # first frame has to be told now rather than never.
            self.has_video = False
        self.emit("tracks")

    def _note_selection(self, message):
        """Remember which streams playbin3 actually settled on.

        It has to be remembered rather than assumed, because `select_streams`
        wants the complete set in one event: naming only the audio track turns
        the picture off. The count and the streams come off the message
        itself, not off the collection it also carries — the collection is
        everything the file holds, and this is the subset playing.
        """
        selected = {}
        try:
            count = message.streams_selected_get_size()
        except Exception:                               # noqa: BLE001
            return
        for index in range(count):
            stream = message.streams_selected_get_stream(index)
            if stream is None:
                continue
            kind = _stream_kind(stream.get_stream_type())
            if kind:
                selected[kind] = stream.get_stream_id()
        if selected:
            self._selected.update(selected)

    def _read_tags(self, taglist):
        if taglist is None:
            return
        found = {}

        def collect(tags, tag):
            try:
                count = tags.get_tag_size(tag)
            except Exception:                           # noqa: BLE001
                return
            if not count:
                return
            value = tags.get_value_index(tag, 0)
            if isinstance(value, Gst.Sample):
                if tag in ("image", "preview-image") and self.artwork is None:
                    art = self._artwork_from(value)
                    if art is not None:
                        self.artwork = art
                return
            if isinstance(value, (str, int, float, bool)):
                found[tag] = value

        taglist.foreach(collect)
        if found:
            self.tags.update(found)
            self.emit("tags")

    def _artwork_from(self, sample) -> Gdk.Texture | None:
        """Album art out of a tag, as a texture.

        The sample holds a whole encoded JPEG or PNG rather than raw pixels,
        so this goes through the ordinary texture loader rather than through
        the video path.
        """
        buffer = sample.get_buffer()
        if buffer is None:
            return None
        ok, mapped = buffer.map(Gst.MapFlags.READ)
        if not ok:
            return None
        try:
            data = GLib.Bytes.new(bytes(mapped.data))
        finally:
            buffer.unmap(mapped)
        try:
            return Gdk.Texture.new_from_bytes(data)
        except GLib.Error:
            return None

    def _read_toc(self, toc):
        """Chapters, if the file has a table of contents.

        Matroska and MP4 both carry one; most files do not. Nested entries are
        flattened, because a chapter inside a chapter is not something a seek
        bar can show and nobody authors them anyway.
        """
        chapters = []

        def walk(entries):
            for entry in entries:
                if entry.get_entry_type() == Gst.TocEntryType.CHAPTER:
                    ok, start, stop = entry.get_start_stop_times()
                    title = ""
                    tags = entry.get_tags()
                    if tags is not None:
                        got, title = tags.get_string(Gst.TAG_TITLE)
                        if not got:
                            title = ""
                    if ok:
                        chapters.append(Chapter(
                            start / NANOS, (stop / NANOS) if stop > 0 else -1.0,
                            title or f"Chapter {len(chapters) + 1}"))
                walk(entry.get_sub_entries())

        walk(toc.get_entries())
        if chapters:
            chapters.sort(key=lambda c: c.start)
            self.chapters = chapters
            self.emit("tracks")

    def _read_video_caps(self):
        """The frame rate and the real frame size, once negotiation is done.

        The frame rate is wanted for stepping — "one frame back" has to know
        how long a frame is — and the size for the window's own fitting.
        """
        if self._sink is None:
            return
        pad = self._sink.get_static_pad("sink")
        caps = pad.get_current_caps() if pad else None
        if caps is None or caps.get_size() == 0:
            return
        info = GstVideo.VideoInfo.new_from_caps(caps)
        if info is None:
            return
        self.video_size = (info.width, info.height)
        if info.fps_d:
            self.frame_rate = info.fps_n / info.fps_d

    # -- loading ----------------------------------------------------------

    def open(self, path: str, start_at: float = 0.0, subtitle: str = "",
             play: bool = True):
        """Load a file, optionally resuming at `start_at` seconds."""
        if not self._build():
            self.emit("failed", self.error or "Cannot play video", "")
            return False
        self.stop(keep_frame=False)
        self.path = path
        self.duration = -1.0
        self.position = 0.0
        self.tracks = []
        self.chapters = []
        self.tags = {}
        self.artwork = None
        self.error = ""
        self.frame_rate = 0.0
        self.video_size = (0, 0)
        self.has_video = False
        self.has_audio = False
        self._seen_frame = False
        self._selected = {}
        self._collection = None
        self.loop_range = None
        self._pending_seek = start_at if start_at > 0.1 else None

        self._pipeline.set_property("uri", Gst.filename_to_uri(os.path.abspath(path)))
        self._pipeline.set_property("suburi",
                                    Gst.filename_to_uri(os.path.abspath(subtitle))
                                    if subtitle else None)
        self._pipeline.set_state(Gst.State.PLAYING if play else Gst.State.PAUSED)
        return True

    def stop(self, keep_frame=True):
        if self._pipeline is None:
            return
        self._stop_poll()
        # NULL rather than READY, and before anything else is touched:
        # GStreamer complains loudly, and leaks, if an element is disposed
        # from any other state.
        self._pipeline.set_state(Gst.State.NULL)
        self._set_state_name("stopped")
        if not keep_frame:
            self.paintable.clear()
        self.position = 0.0

    def shutdown(self):
        """Take the pipeline down for good. Called when a window closes."""
        if self._pipeline is None:
            return
        self._stop_poll()
        if self._bus is not None:
            try:
                self._bus.remove_signal_watch()
            except Exception:                           # noqa: BLE001
                pass
        self._pipeline.set_state(Gst.State.NULL)
        self._pipeline = None
        self._sink = None
        self._bus = None
        self.paintable.clear()

    # -- playing ----------------------------------------------------------

    def play(self):
        if self._pipeline is not None:
            self._pipeline.set_state(Gst.State.PLAYING)

    def pause(self):
        if self._pipeline is not None:
            self._pipeline.set_state(Gst.State.PAUSED)

    def toggle(self):
        if self.state == "playing":
            self.pause()
        else:
            self.play()

    def playing(self) -> bool:
        return self.state == "playing"

    # -- where we are -----------------------------------------------------

    def _query_position(self) -> float:
        if self._pipeline is None:
            return self.position
        ok, value = self._pipeline.query_position(Gst.Format.TIME)
        if ok and value >= 0:
            self.position = value / NANOS
        return self.position

    def _query_duration(self):
        if self._pipeline is None:
            return
        ok, value = self._pipeline.query_duration(Gst.Format.TIME)
        if ok and value > 0:
            self.duration = value / NANOS

    def _start_poll(self):
        if self._poll:
            return
        # Four times a second. The seek bar is a few hundred pixels wide, so a
        # faster poll could not move it further than a pixel, and the query
        # goes all the way down the pipeline.
        self._poll = GLib.timeout_add(250, self._tick)

    def _stop_poll(self):
        if self._poll:
            GLib.source_remove(self._poll)
            self._poll = 0

    def _tick(self):
        if self._pipeline is None:
            self._poll = 0
            return GLib.SOURCE_REMOVE
        self._query_position()
        if self.duration <= 0:
            self._query_duration()
        self.emit("progress", self.position, self.duration)
        return GLib.SOURCE_CONTINUE

    # -- seeking ----------------------------------------------------------

    def seek(self, seconds: float, accurate: bool = True):
        """Jump to a position.

        Accurate seeking decodes from the previous keyframe to the exact
        frame asked for, which is what a scrub bar and a frame step need and
        what the arrow keys do not — a keyframe seek is instant and lands
        within a second or so, and for a five-second nudge that is the better
        trade. The scrubbing preview asks for keyframes; everything else asks
        for accuracy.
        """
        if self._pipeline is None:
            return False
        if self.duration > 0:
            seconds = max(0.0, min(self.duration, seconds))
        else:
            seconds = max(0.0, seconds)
        flags = Gst.SeekFlags.FLUSH | (Gst.SeekFlags.ACCURATE if accurate
                                       else Gst.SeekFlags.KEY_UNIT)
        if self.loop_range is not None:
            return self._loop_segment(flush=True, from_position=seconds)
        done = self._pipeline.seek(
            self.rate, Gst.Format.TIME, flags | self._trick_flags(),
            Gst.SeekType.SET, int(seconds * NANOS), Gst.SeekType.NONE, 0)
        if done:
            self.position = seconds
            self.emit("progress", self.position, self.duration)
        return done

    def skip(self, seconds: float):
        """Relative seek — what the arrow keys do."""
        return self.seek(self._query_position() + seconds, accurate=False)

    def _trick_flags(self):
        if abs(self.rate) > TRICKMODE_ABOVE:
            return Gst.SeekFlags.TRICKMODE | Gst.SeekFlags.TRICKMODE_KEY_UNITS
        return Gst.SeekFlags(0)

    def set_rate(self, rate: float):
        """Change the playback speed, keeping the position.

        A rate change in GStreamer is a seek, which is why this is not a
        property: it has to know where it is first, and it has to re-apply
        whatever trick mode the new rate calls for.
        """
        if self._pipeline is None:
            return False
        rate = max(0.05, min(16.0, float(rate)))
        self.rate = rate
        position = self._query_position()
        return self._pipeline.seek(
            rate, Gst.Format.TIME,
            Gst.SeekFlags.FLUSH | Gst.SeekFlags.ACCURATE | self._trick_flags(),
            Gst.SeekType.SET, int(position * NANOS), Gst.SeekType.NONE, 0)

    def step(self, frames: int = 1):
        """One frame on, or one frame back.

        Forward is a step event, which is what it is for. Backwards is a seek,
        because no demuxer can play a single frame in reverse — so it needs to
        know how long a frame is, and falls back on the configured hint when
        the stream never said.
        """
        if self._pipeline is None:
            return False
        if self.state == "playing":
            self.pause()
        if frames > 0:
            return self._pipeline.send_event(
                Gst.Event.new_step(Gst.Format.BUFFERS, frames, 1.0, True, False))
        rate = self.frame_rate if self.frame_rate > 1 else 25.0
        return self.seek(self._query_position() - abs(frames) / rate)

    # -- A-B looping ------------------------------------------------------

    def set_loop_range(self, start: float | None, stop: float | None):
        """Loop a stretch of the file, or stop doing so when given None.

        A segment seek rather than a timer: GStreamer tells us when the
        segment is done, and starting the next pass without flushing means the
        sound joins without a click. A timer would be a frame or two late
        every time, and the drift is audible within half a minute.
        """
        if start is None or stop is None or stop <= start:
            self.loop_range = None
            if self._pipeline is not None:
                self._pipeline.seek(
                    self.rate, Gst.Format.TIME, Gst.SeekFlags.FLUSH,
                    Gst.SeekType.SET, int(self._query_position() * NANOS),
                    Gst.SeekType.NONE, 0)
            return True
        self.loop_range = (max(0.0, start), stop)
        return self._loop_segment(flush=True)

    def _loop_segment(self, flush: bool, from_position: float | None = None):
        if self._pipeline is None or self.loop_range is None:
            return False
        start, stop = self.loop_range
        begin = from_position if from_position is not None else start
        flags = Gst.SeekFlags.SEGMENT | Gst.SeekFlags.ACCURATE
        if flush:
            flags |= Gst.SeekFlags.FLUSH
        return self._pipeline.seek(
            self.rate, Gst.Format.TIME, flags,
            Gst.SeekType.SET, int(begin * NANOS),
            Gst.SeekType.SET, int(stop * NANOS))

    def _loop_back(self) -> bool:
        """Handle end-of-stream for a whole-file loop. True if we restarted."""
        if self.loop_range is not None:
            return self._loop_segment(flush=True)
        return False

    # -- sound ------------------------------------------------------------

    def set_volume(self, volume: float):
        """0 to 1, as a slider means it rather than as an amplifier does.

        A linear amplitude slider spends most of its travel in the range
        between "very loud" and "slightly less loud", because loudness is
        roughly the cube root of amplitude. GStreamer knows this and offers
        a cubic scale; using it is the difference between a volume control
        and a volume cliff.
        """
        self._volume = max(0.0, min(1.0, float(volume)))
        self._apply_volume()

    def set_muted(self, muted: bool):
        self._muted = bool(muted)
        self._apply_volume()

    def _apply_volume(self):
        if self._pipeline is None:
            return
        try:
            GstAudio.StreamVolume.set_volume(
                self._pipeline, GstAudio.StreamVolumeFormat.CUBIC, self._volume)
        except Exception:                               # noqa: BLE001
            self._pipeline.set_property("volume", self._volume ** 3)
        self._pipeline.set_property("mute", self._muted)

    def set_audio_offset(self, seconds: float):
        """Shift the sound against the picture, for a badly muxed file."""
        if self._pipeline is not None:
            self._pipeline.set_property("av-offset", int(seconds * NANOS))

    def audio_offset(self) -> float:
        if self._pipeline is None:
            return 0.0
        return self._pipeline.get_property("av-offset") / NANOS

    # -- subtitles --------------------------------------------------------

    def set_subtitle_file(self, path: str):
        """Load a sidecar. Costs a reload, because `suburi` is read at start.

        The position is kept, so this is invisible apart from a flicker —
        which is still better than the alternative of not being able to
        change subtitles without reopening the file.
        """
        if self._pipeline is None:
            return False
        at = self._query_position()
        was_playing = self.state == "playing"
        self._pipeline.set_state(Gst.State.READY)
        self._pipeline.set_property(
            "suburi", Gst.filename_to_uri(os.path.abspath(path)) if path else None)
        self._pending_seek = at if at > 0.1 else None
        self._pipeline.set_state(Gst.State.PLAYING if was_playing
                                 else Gst.State.PAUSED)
        return True

    def set_subtitle_offset(self, seconds: float):
        """Shift the subtitles against the picture."""
        if self._pipeline is not None:
            self._pipeline.set_property("text-offset", int(seconds * NANOS))

    def subtitle_offset(self) -> float:
        if self._pipeline is None:
            return 0.0
        return self._pipeline.get_property("text-offset") / NANOS

    def set_subtitle_font(self, description: str):
        self._subtitle_font = description
        if self._pipeline is not None:
            self._pipeline.set_property("subtitle-font-desc", description)

    # -- flags ------------------------------------------------------------

    def _set_flag(self, flag: int, on: bool):
        self._flags = (self._flags | flag) if on else (self._flags & ~flag)
        if self._pipeline is not None:
            self._pipeline.set_property("flags", self._flags)

    def set_subtitles_enabled(self, on: bool):
        self._set_flag(FLAG_TEXT, on)

    def subtitles_enabled(self) -> bool:
        return bool(self._flags & FLAG_TEXT)

    def set_deinterlace(self, on: bool):
        """Only takes effect on the next file, which is why it is not a toggle
        in the transport bar — playbin reads its flags when it builds its
        chain, and rebuilding it mid-playback is not worth the glitch."""
        self._set_flag(FLAG_DEINTERLACE, on)

    def set_hardware_decoding(self, on: bool):
        self._set_flag(FLAG_FORCE_SW_DECODERS, not on)

    # -- tracks -----------------------------------------------------------

    def select_track(self, kind: str, ident: str | None):
        """Choose which audio, video or subtitle stream plays.

        playbin3 wants the complete set in one event — naming only the audio
        track turns the video off — so the other kinds are carried over from
        whatever is playing now.
        """
        if self._pipeline is None:
            return False
        wanted = dict(self._selected)
        if ident is None:
            wanted.pop(kind, None)
        else:
            wanted[kind] = ident
        # Fill in any kind that has streams but no selection yet, or the event
        # would silently turn it off.
        for track in self.tracks:
            wanted.setdefault(track.kind, track.ident if track.default else None)
        idents = [value for value in wanted.values() if value]
        if not idents:
            return False
        self._selected = {k: v for k, v in wanted.items() if v}
        return self._pipeline.send_event(Gst.Event.new_select_streams(idents))

    def selected(self, kind: str) -> str | None:
        return self._selected.get(kind)

    def tracks_of(self, kind: str) -> list[Track]:
        return [track for track in self.tracks if track.kind == kind]

    def chapter_at(self, seconds: float) -> Chapter | None:
        found = None
        for chapter in self.chapters:
            if chapter.start <= seconds:
                found = chapter
            else:
                break
        return found

    # -- stills -----------------------------------------------------------

    def frame(self) -> Gdk.Texture | None:
        """The frame on screen now, for a screenshot or for the editor."""
        return self.paintable.texture()

    def title(self) -> str:
        """The best name for what is playing: a tag, or the file's own name."""
        for tag in ("title", "name"):
            value = self.tags.get(tag)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return os.path.basename(self.path) if self.path else ""


def _stream_kind(stream_type) -> str | None:
    if stream_type & Gst.StreamType.VIDEO:
        return "video"
    if stream_type & Gst.StreamType.AUDIO:
        return "audio"
    if stream_type & Gst.StreamType.TEXT:
        return "text"
    return None


def _describe_stream(track: Track, stream):
    """Fill in a track's language, title, codec and shape from the stream."""
    tags = stream.get_tags()
    if tags is not None:
        ok, value = tags.get_string(Gst.TAG_LANGUAGE_CODE)
        if ok and value:
            track.language = value
        ok, value = tags.get_string(Gst.TAG_LANGUAGE_NAME)
        if ok and value and not track.language:
            track.language = value
        ok, value = tags.get_string(Gst.TAG_TITLE)
        if ok and value:
            track.title = value
        for tag in (Gst.TAG_VIDEO_CODEC, Gst.TAG_AUDIO_CODEC,
                    Gst.TAG_SUBTITLE_CODEC, Gst.TAG_CODEC):
            ok, value = tags.get_string(tag)
            if ok and value:
                track.codec = value
                break
    caps = stream.get_caps()
    if caps is None or caps.get_size() == 0:
        return
    structure = caps.get_structure(0)
    if not track.codec:
        track.codec = _codec_name(structure.get_name())
    ok, value = structure.get_int("width")
    if ok:
        track.width = value
    ok, value = structure.get_int("height")
    if ok:
        track.height = value
    ok, value = structure.get_int("channels")
    if ok:
        track.channels = value
    ok, value = structure.get_int("rate")
    if ok:
        track.rate = value


# Caps names are not what anybody calls these formats.
CODEC_NAMES = {
    "video/x-h264": "H.264", "video/x-h265": "HEVC", "video/x-vp8": "VP8",
    "video/x-vp9": "VP9", "video/x-av1": "AV1", "video/mpeg": "MPEG",
    "video/x-theora": "Theora", "video/x-msmpeg": "MS MPEG-4",
    "video/x-divx": "DivX", "video/x-wmv": "WMV", "video/x-flash-video": "Flash",
    "video/x-raw": "Uncompressed", "video/x-prores": "ProRes",
    "audio/mpeg": "MP3/AAC", "audio/x-vorbis": "Vorbis", "audio/x-opus": "Opus",
    "audio/x-flac": "FLAC", "audio/x-ac3": "AC-3", "audio/x-eac3": "E-AC-3",
    "audio/x-dts": "DTS", "audio/x-wav": "WAV", "audio/x-raw": "Uncompressed",
    "audio/x-alac": "ALAC", "audio/x-wma": "WMA",
    "application/x-subtitle": "Subtitles", "text/x-raw": "Text",
    "subtitle/x-kate": "Kate", "application/x-ass": "ASS",
    "application/x-ssa": "SSA", "application/x-subtitle-vtt": "WebVTT",
    "subpicture/x-dvd": "DVD subpicture", "subpicture/x-pgs": "PGS",
}


def _codec_name(caps_name: str) -> str:
    return CODEC_NAMES.get(caps_name, caps_name.split("/")[-1].lstrip("x-").upper())
