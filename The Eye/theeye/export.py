"""Writing a copy out, with the grade baked in.

The promise this module keeps is that what is exported is what was on screen.
It is kept the only way it can be kept honestly: by drawing the same render
node tree that the stage draws, through the same code in `effects.py`, and
reading the result back off the GPU. There is no second implementation of the
grade for export to disagree with — which is why there is no "export looks
different from the preview" bug to find.

Three jobs:

*A still* is loaded at full size — not the possibly-scaled copy the screen
was showing — graded, rendered and saved. A 40-megapixel photograph is
graded at 40 megapixels.

*A frame of video* is the texture already on screen, which is at most the
size of the monitor because that is what the pipeline delivers. That is a
screenshot rather than a frame extraction, and the file name says so.

*A video* is decoded frame by frame, each frame graded and rendered, and the
results fed to an encoder with the original timestamps. The sound is copied
across untouched. It is slow — a few frames a second on this kind of machine
— so it reports progress and can be stopped, and it says plainly how long it
is likely to take before it starts.

Nothing here ever writes to the file it read. The chooser defaults to a new
name beside the original, and a write that would land on the source is
refused rather than confirmed, because there is no version of "overwrite your
only copy of the photograph" that is a good idea.
"""

from __future__ import annotations

import os
import threading
import time

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gst", "1.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")

from gi.repository import Gdk, GdkPixbuf, GLib, GObject, Gsk, Gst, Gtk  # noqa: E402

from . import effects, media, photo, player  # noqa: E402

# What we can write, and what the pixbuf savers call it.
STILL_FORMATS = {
    ".png": ("png", {}),
    ".jpg": ("jpeg", {"quality": "95"}),
    ".jpeg": ("jpeg", {"quality": "95"}),
    ".webp": ("webp", {"quality": "95"}),
    ".tif": ("tiff", {}),
    ".tiff": ("tiff", {}),
}

# Container to (video encoder, audio encoder, muxer). The first encoder of
# each that this machine actually has is used; a box without x264 falls back
# to whatever else is installed, and if nothing is, the export says so rather
# than producing an empty file.
VIDEO_RECIPES = {
    ".mp4": (("x264enc", "openh264enc", "vah264enc", "avenc_mpeg4"),
             ("avenc_aac", "voaacenc", "fdkaacenc"), "mp4mux"),
    ".mkv": (("x264enc", "openh264enc", "vah264enc", "vp8enc"),
             ("opusenc", "vorbisenc", "avenc_aac"), "matroskamux"),
    ".webm": (("vp9enc", "vp8enc"), ("opusenc", "vorbisenc"), "webmmux"),
}

DEFAULT_CONTAINER = ".mp4"


def suggested_name(path: str, suffix: str = "edited",
                   extension: str = "") -> str:
    """A new name beside the original — never the original itself."""
    folder = os.path.dirname(path)
    stem, ext = os.path.splitext(os.path.basename(path))
    ext = extension or ext
    candidate = os.path.join(folder, f"{stem} ({suffix}){ext}")
    index = 2
    while os.path.exists(candidate):
        candidate = os.path.join(folder, f"{stem} ({suffix} {index}){ext}")
        index += 1
    return candidate


def _refuse_overwriting_source(source: str, destination: str):
    if not source or not destination:
        return
    try:
        same = os.path.samefile(source, destination)
    except OSError:
        same = (os.path.abspath(source) == os.path.abspath(destination))
    if same:
        raise ValueError("That would overwrite the original. Choose another name.")


# ------------------------------------------------------------------ stills ----

def texture_to_pixbuf(texture: Gdk.Texture) -> GdkPixbuf.Pixbuf:
    """A texture as a pixbuf, which is what the savers take.

    `Gdk.pixbuf_get_from_texture` exists and is used: doing it by hand through
    a downloader means getting the stride and the premultiplication right, and
    getting either wrong produces a picture that is subtly, plausibly wrong —
    the worst kind.
    """
    pixbuf = Gdk.pixbuf_get_from_texture(texture)
    if pixbuf is None:
        raise ValueError("The picture could not be read back from the graphics card")
    return pixbuf


def render_still(path: str, settings, longest: int = 0) -> Gdk.Texture:
    """A graded still at full size, as a texture. Blocking.

    The rotation and the flips are applied here rather than by the grade's
    matrix, because they change the shape of the output and everything
    downstream needs the real dimensions.
    """
    still = photo.load(path, animate=False, longest=longest)
    if still.texture is None:
        raise ValueError(still.error or "The picture could not be read")
    source = still.texture
    width, height = source.get_width(), source.get_height()

    inner = Gtk.Snapshot()
    inner.append_scaled_texture(source, Gsk.ScalingFilter.TRILINEAR,
                                effects.rect(0, 0, width, height))
    node = inner.to_node()
    if node is None:
        raise ValueError("The picture could not be drawn")

    graded = effects.apply_grade(node, effects.rect(0, 0, width, height),
                                 settings, "high", 0)
    out_width, out_height = _oriented(settings, width, height)
    placed = _place(graded, settings, width, height, out_width, out_height)
    texture = effects.render_to_texture(placed, int(out_width), int(out_height))
    if texture is None:
        raise ValueError("There is no renderer available to draw the picture")
    return texture


def _oriented(settings, width, height):
    return (height, width) if int(settings["rotation"]) % 180 else (width, height)


def _place(node, settings, width, height, out_width, out_height):
    """The graded node rotated and flipped into an upright output frame."""
    rotation = int(settings["rotation"]) % 360
    if not rotation and not settings["flip_h"] and not settings["flip_v"]:
        return node
    snapshot = Gtk.Snapshot()
    snapshot.save()
    snapshot.translate(effects.point(out_width / 2, out_height / 2))
    if rotation:
        snapshot.rotate(rotation)
    if settings["flip_h"] or settings["flip_v"]:
        snapshot.scale(-1.0 if settings["flip_h"] else 1.0,
                       -1.0 if settings["flip_v"] else 1.0)
    snapshot.translate(effects.point(-width / 2, -height / 2))
    snapshot.append_node(node)
    snapshot.restore()
    return snapshot.to_node() or node


def save_texture(texture: Gdk.Texture, destination: str, quality: int = 95):
    """Write a texture to disk, in the format the name asks for."""
    extension = os.path.splitext(destination)[1].lower()
    recipe = STILL_FORMATS.get(extension)
    if recipe is None:
        raise ValueError(f"The Eye cannot write {extension or 'that'} files. "
                         "Use .png, .jpg, .webp or .tif.")
    name, options = recipe
    options = dict(options)
    if "quality" in options:
        options["quality"] = str(max(40, min(100, int(quality))))
    pixbuf = texture_to_pixbuf(texture)
    if name in ("jpeg", "tiff"):
        # Neither format has an alpha channel, and a pixbuf with one is
        # written as though the transparent parts were black. Flattening onto
        # white is what every other tool does and what anybody expects.
        pixbuf = _flatten(pixbuf)
    folder = os.path.dirname(destination)
    if folder:
        os.makedirs(folder, exist_ok=True)
    temporary = destination + ".part"
    try:
        pixbuf.savev(temporary, name, list(options.keys()), list(options.values()))
        os.replace(temporary, destination)
    except Exception:
        try:
            os.remove(temporary)
        except OSError:
            pass
        raise


def _flatten(pixbuf: GdkPixbuf.Pixbuf) -> GdkPixbuf.Pixbuf:
    if not pixbuf.get_has_alpha():
        return pixbuf
    flat = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8,
                                pixbuf.get_width(), pixbuf.get_height())
    flat.fill(0xFFFFFFFF)
    pixbuf.composite(flat, 0, 0, pixbuf.get_width(), pixbuf.get_height(),
                     0, 0, 1, 1, GdkPixbuf.InterpType.NEAREST, 255)
    return flat


def export_still(source: str, destination: str, settings, quality: int = 95,
                 longest: int = 0):
    """Load, grade, render and save a still. Blocking — use `jobs.run`."""
    _refuse_overwriting_source(source, destination)
    texture = render_still(source, settings, longest)
    save_texture(texture, destination, quality)
    return destination


def export_frame(texture: Gdk.Texture, destination: str, settings,
                 quality: int = 95):
    """Save one already-decoded frame — a screenshot of a video."""
    width, height = texture.get_width(), texture.get_height()
    inner = Gtk.Snapshot()
    inner.append_scaled_texture(texture, Gsk.ScalingFilter.LINEAR,
                                effects.rect(0, 0, width, height))
    node = inner.to_node()
    graded = effects.apply_grade(node, effects.rect(0, 0, width, height),
                                 settings, "high", 0)
    out_width, out_height = _oriented(settings, width, height)
    placed = _place(graded, settings, width, height, out_width, out_height)
    rendered = effects.render_to_texture(placed, int(out_width), int(out_height))
    save_texture(rendered or texture, destination, quality)
    return destination


# ------------------------------------------------------------------- video ----

def first_available(names) -> str | None:
    """The first of these element names that this machine actually has.

    `player.init()` first: asking the registry anything before GStreamer has
    been initialised reports that nothing is installed, which looks exactly
    like a machine with no encoders on it.
    """
    if not player.init():
        return None
    for name in names:
        if Gst.ElementFactory.find(name) is not None:
            return name
    return None


def recipe_for(destination: str):
    """(video encoder, audio encoder, muxer) for an output name, or an error."""
    extension = os.path.splitext(destination)[1].lower()
    recipe = VIDEO_RECIPES.get(extension)
    if recipe is None:
        raise ValueError(f"The Eye cannot write {extension or 'that'} video. "
                         "Use .mp4, .mkv or .webm.")
    video_names, audio_names, muxer = recipe
    if not player.init():
        raise ValueError("GStreamer is not available, so no video can be written.")
    video = first_available(video_names)
    audio = first_available(audio_names)
    if video is None:
        raise ValueError("No video encoder for that format is installed. "
                         f"One of {', '.join(video_names)} is needed.")
    if Gst.ElementFactory.find(muxer) is None:
        raise ValueError(f"The {muxer} muxer is not installed.")
    return video, audio, muxer


class VideoExport(GObject.Object):
    """One video re-encoded with the grade applied.

    Two pipelines rather than one, driven from a worker thread in between.
    The source is decoded into two `appsink`s — one for the picture, one for
    the sound — and the destination is two `appsrc`s into the encoders and the
    muxer. In between, each video frame is turned into a texture, drawn
    through `effects.apply_grade`, rendered, and read back.

    Sync survives this because every buffer keeps the timestamp and duration
    it arrived with. The muxer works from those, not from when it was handed
    the data, so the fact that the picture takes a hundred times longer than
    the sound to get through the middle does not matter at all.

    The sound is never decoded and re-encoded if it can be helped — but it
    is, because `decodebin3` has already decoded it by the time we see it.
    That is a real quality cost on a lossy track, stated here so that nobody
    has to discover it: exporting a video re-encodes its audio.
    """

    __gtype_name__ = "TheEyeVideoExport"

    __gsignals__ = {
        # fraction done 0..1, and a line of text about it
        "progress": (GObject.SignalFlags.RUN_FIRST, None, (float, str)),
        "finished": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "failed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, source: str, destination: str, settings,
                 quality: str = "high"):
        super().__init__()
        _refuse_overwriting_source(source, destination)
        self.source = source
        self.destination = destination
        self.settings = settings
        self.quality = quality
        self.frames = 0
        self.duration = 0.0
        self._stop = threading.Event()
        self._thread = None
        self._painter = None
        self._reader = None
        self._writer = None
        self._video_in = None
        self._audio_in = None
        self._video_out = None
        self._audio_out = None
        self._size = (0, 0)
        self._started = 0.0

    # -- driving ----------------------------------------------------------

    def start(self):
        if self._thread is not None:
            return False
        if not player.init():
            self._fail("GStreamer is not available")
            return False
        self._started = time.monotonic()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="the-eye-export")
        self._thread.start()
        return True

    def cancel(self):
        """Stop, and take the half-written file away with us."""
        self._stop.set()

    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # -- the work ---------------------------------------------------------

    def _run(self):
        try:
            self._build()
            self._pump()
        except Exception as exc:                        # noqa: BLE001
            self._teardown(remove=True)
            self._fail(str(exc) or "The export failed")
            return
        cancelled = self._stop.is_set()
        self._teardown(remove=cancelled)
        if cancelled:
            self._fail("Export stopped")
        else:
            GLib.idle_add(lambda: (self.emit("finished", self.destination),
                                   GLib.SOURCE_REMOVE)[1])

    def _build(self):
        video_encoder, audio_encoder, muxer = recipe_for(self.destination)
        uri = Gst.filename_to_uri(os.path.abspath(self.source))

        # The source. `sync=false` on both sinks so that decoding runs as fast
        # as the machine can manage rather than at playback speed — this is a
        # file conversion, not a performance.
        reader = Gst.parse_launch(
            f'uridecodebin3 uri="{uri}" name=src '
            "src. ! queue max-size-buffers=4 ! videoconvert "
            "! video/x-raw,format=RGBA ! appsink name=vin "
            "max-buffers=4 drop=false sync=false "
            "src. ! queue max-size-buffers=64 ! audioconvert ! audioresample "
            "! appsink name=ain max-buffers=64 drop=false sync=false")
        self._reader = reader
        self._video_in = reader.get_by_name("vin")
        self._audio_in = reader.get_by_name("ain")

        reader.set_state(Gst.State.PAUSED)
        bus = reader.get_bus()
        message = bus.timed_pop_filtered(
            10 * Gst.SECOND, Gst.MessageType.ASYNC_DONE | Gst.MessageType.ERROR)
        if message is None:
            raise ValueError("The file took too long to open")
        if message.type == Gst.MessageType.ERROR:
            error, _debug = message.parse_error()
            raise ValueError(error.message if error else "The file could not be read")
        ok, duration = reader.query_duration(Gst.Format.TIME)
        self.duration = (duration / Gst.SECOND) if ok and duration > 0 else 0.0

        # The frame size, which the destination has to be told before it will
        # negotiate. It comes from the first frame rather than from the caps,
        # because a preroll is already waiting and it is the authority.
        sample = self._video_in.try_pull_preroll(5 * Gst.SECOND)
        if sample is None:
            raise ValueError("No video could be decoded from that file")
        self._first_sample = sample
        from gi.repository import GstVideo

        info = GstVideo.VideoInfo.new_from_caps(sample.get_caps())
        width, height = info.width, info.height
        out_width, out_height = (int(h) for h in _oriented(self.settings,
                                                           width, height))
        self._size = (width, height)
        self._out_size = (out_width, out_height)
        rate_n = info.fps_n or 25
        rate_d = info.fps_d or 1

        has_audio = self._audio_in.get_static_pad("sink").is_linked()
        audio_caps = None
        if has_audio:
            preroll = self._audio_in.try_pull_preroll(2 * Gst.SECOND)
            if preroll is not None:
                audio_caps = preroll.get_caps()
                self._first_audio = preroll
            else:
                has_audio = False

        pieces = [
            f"appsrc name=vout format=time is-live=false "
            f'caps="video/x-raw,format=RGBA,width={out_width},'
            f'height={out_height},framerate={rate_n}/{rate_d}" '
            f"! queue ! videoconvert ! {video_encoder} "
            f"! queue ! {muxer} name=mux "
            f'! filesink location="{self.destination}.part"',
        ]
        if has_audio and audio_encoder and audio_caps is not None:
            pieces.append(
                f'appsrc name=aout format=time is-live=false '
                f'caps="{audio_caps.to_string()}" '
                f"! queue ! audioconvert ! {audio_encoder} ! queue ! mux.")
        self._writer = Gst.parse_launch(" ".join(pieces))
        self._video_out = self._writer.get_by_name("vout")
        self._audio_out = self._writer.get_by_name("aout")
        self._has_audio = bool(self._audio_out)

        self._painter = effects.Painter()
        if not self._painter.ok():
            raise ValueError("There is no renderer available to draw the frames")

        self._writer.set_state(Gst.State.PLAYING)
        self._reader.set_state(Gst.State.PLAYING)

    def _pump(self):
        """Move every buffer across, grading the picture on the way."""
        video_done = False
        audio_done = not self._has_audio
        pending_video = getattr(self, "_first_sample", None)
        pending_audio = getattr(self, "_first_audio", None)
        last_report = 0.0

        while not self._stop.is_set() and not (video_done and audio_done):
            moved = False
            if not video_done:
                sample = pending_video
                pending_video = None
                if sample is None:
                    sample = self._video_in.try_pull_sample(100 * Gst.MSECOND)
                if sample is not None:
                    self._push_video(sample)
                    moved = True
                elif self._video_in.props.eos:
                    self._video_out.end_of_stream()
                    video_done = True
            if not audio_done:
                sample = pending_audio
                pending_audio = None
                if sample is None:
                    sample = self._audio_in.try_pull_sample(0)
                if sample is not None:
                    self._audio_out.push_buffer(sample.get_buffer())
                    moved = True
                elif self._audio_in.props.eos:
                    self._audio_out.end_of_stream()
                    audio_done = True
            if not moved and not video_done:
                continue
            now = time.monotonic()
            if now - last_report > 0.25:
                last_report = now
                self._report()

        if self._stop.is_set():
            return
        # Wait for the encoder to finish with what it has been given.
        bus = self._writer.get_bus()
        bus.timed_pop_filtered(120 * Gst.SECOND,
                               Gst.MessageType.EOS | Gst.MessageType.ERROR)

    def _push_video(self, sample):
        from gi.repository import GstVideo

        buffer = sample.get_buffer()
        caps = sample.get_caps()
        info = GstVideo.VideoInfo.new_from_caps(caps)
        width, height = info.width, info.height
        stride = info.stride[0] if info.stride else width * 4
        ok, mapped = buffer.map(Gst.MapFlags.READ)
        if not ok:
            return
        try:
            needed = stride * height
            data = bytes(mapped.data)[:needed]
        finally:
            buffer.unmap(mapped)
        source = Gdk.MemoryTexture.new(width, height, Gdk.MemoryFormat.R8G8B8A8,
                                       GLib.Bytes.new(data), stride)

        inner = Gtk.Snapshot()
        inner.append_scaled_texture(source, Gsk.ScalingFilter.LINEAR,
                                    effects.rect(0, 0, width, height))
        node = inner.to_node()
        graded = effects.apply_grade(node, effects.rect(0, 0, width, height),
                                     self.settings, self.quality, self.frames)
        out_width, out_height = self._out_size
        placed = _place(graded, self.settings, width, height,
                        out_width, out_height)
        rendered = self._painter.render(placed, out_width, out_height)
        if rendered is None:
            return
        pixels, out_stride = effects.texture_bytes(rendered)

        out = Gst.Buffer.new_wrapped(bytes(pixels))
        # The timestamps are carried over untouched. This is what keeps the
        # sound and the picture together despite the picture taking a hundred
        # times longer to get through this function.
        out.pts = buffer.pts
        out.dts = buffer.dts
        out.duration = buffer.duration
        self._video_out.push_buffer(out)
        self.frames += 1

    def _report(self):
        if self.duration > 0 and self.frames:
            # Progress from the timestamps rather than from a frame count,
            # which is not known in advance for a variable frame rate file.
            fraction = min(0.999, self._elapsed_media() / self.duration)
        else:
            fraction = 0.0
        spent = max(0.001, time.monotonic() - self._started)
        rate = self.frames / spent
        text = f"{self.frames} frames   {rate:.1f} a second"
        if fraction > 0.02:
            left = spent / fraction - spent
            text += f"   about {_rough_time(left)} left"
        GLib.idle_add(self._emit_progress, fraction, text)

    def _elapsed_media(self) -> float:
        if self._reader is None:
            return 0.0
        ok, position = self._reader.query_position(Gst.Format.TIME)
        return (position / Gst.SECOND) if ok and position > 0 else 0.0

    def _emit_progress(self, fraction, text):
        self.emit("progress", fraction, text)
        return GLib.SOURCE_REMOVE

    def _fail(self, message):
        GLib.idle_add(lambda: (self.emit("failed", message),
                               GLib.SOURCE_REMOVE)[1])

    def _teardown(self, remove: bool):
        for pipeline in (self._reader, self._writer):
            if pipeline is not None:
                pipeline.set_state(Gst.State.NULL)
        self._reader = self._writer = None
        self._video_in = self._audio_in = None
        self._video_out = self._audio_out = None
        if self._painter is not None:
            self._painter.close()
            self._painter = None
        part = self.destination + ".part"
        if remove:
            for path in (part,):
                try:
                    os.remove(path)
                except OSError:
                    pass
        else:
            try:
                os.replace(part, self.destination)
            except OSError:
                pass


def _rough_time(seconds: float) -> str:
    """"about 3 minutes" — a figure nobody will hold us to."""
    if seconds < 90:
        return f"{max(1, int(seconds))} seconds"
    if seconds < 5400:
        return f"{int(round(seconds / 60))} minutes"
    return f"{seconds / 3600:.1f} hours"


def estimate(path: str, settings) -> str:
    """A sentence about what exporting this video is going to cost.

    Said before the export starts, because "a few minutes" and "two hours"
    are different decisions and the user is the one who should make it.
    """
    from . import metadata

    info = metadata.discover(path, 4)
    duration = (info.get_duration() / Gst.SECOND
                if info is not None and info.get_duration() else 0.0)
    if duration <= 0:
        return ("The length of this file is not known, so neither is how long "
                "exporting it will take. The original is not touched.")

    pixels, rate = 320 * 240, 25.0
    streams = info.get_video_streams() if info is not None else []
    if streams:
        stream = streams[0]
        pixels = max(1, stream.get_width() * stream.get_height())
        if stream.get_framerate_denom():
            rate = (stream.get_framerate_num() / stream.get_framerate_denom()
                    or 25.0)

    # Measured on this class of machine: about 12ms a frame for a nine-stage
    # grade at 320x240, of which the readback off the graphics card is most.
    # Both halves scale with the number of pixels, so the figure is per
    # megapixel. It is an order of magnitude rather than a promise, and the
    # progress line corrects it within a few seconds of starting.
    cost = settings.cost() if settings else 1
    per_megapixel = 0.055 + 0.009 * cost
    per_frame = per_megapixel * pixels / 1e6
    guess = duration * min(240.0, rate) * per_frame
    return (f"About {_rough_time(guess)} for {_rough_time(duration)} of video "
            f"at {pixels / 1e6:.1f} megapixels a frame. "
            "The original is not touched.")
