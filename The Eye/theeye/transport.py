"""The transport bar: everything you do to a moving picture.

The seek bar is drawn here rather than being a `GtkScale` with a stylesheet.
A scale can show one value; this has to show six — where you are, how long the
file is, how much has buffered, where the chapters are, which stretch is
being looped, and where the pointer is hovering — and all six have to line up
with each other to the pixel. Drawing it is less code than fighting a widget
that was built for one number.

Hovering it extracts the frame at that moment and shows it above the bar.
This is the one genuinely expensive thing in the app's chrome: it is a
GStreamer pipeline per preview, so the position is rounded, the results are
cached, and a request only goes out after the pointer has been still for a
moment. Hovering across a two-hour film therefore costs a handful of
previews, not two thousand.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")

from gi.repository import Gdk, GLib, GObject, Gsk, Gtk  # noqa: E402

from . import glass, jobs, metadata, theme as theming, thumbs, widgets  # noqa: E402

# Speeds the menu offers. Not a free slider: these are the ones anybody wants,
# and a slider invites 1.03x.
SPEEDS = (0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 3.0, 4.0)

# How long the pointer has to be still on the seek bar before a preview frame
# is fetched, and how coarsely the position is rounded for the cache.
PREVIEW_DELAY = 180
PREVIEW_GRAIN = 2.0
PREVIEW_WIDTH = 196


class SeekBar(Gtk.Widget):
    """The timeline: position, duration, chapters, buffer and the A-B range."""

    __gtype_name__ = "TheEyeSeekBar"

    __gsignals__ = {
        # a final seek, in seconds
        "seeked": (GObject.SignalFlags.RUN_FIRST, None, (float,)),
        # a position being dragged over, for the live readout
        "scrubbing": (GObject.SignalFlags.RUN_FIRST, None, (float,)),
        "scrub-ended": (GObject.SignalFlags.RUN_FIRST, None, ()),
        # the pointer is resting over this position, or -1 when it has left
        "hovered": (GObject.SignalFlags.RUN_FIRST, None, (float,)),
    }

    HEIGHT = 26
    TRACK = 6

    def __init__(self, scheme=None):
        super().__init__()
        self._scheme = scheme or theming.get(theming.DEFAULT)
        self.position = 0.0
        self.duration = 0.0
        self.buffered = 1.0
        self.chapters: list = []
        self.loop_range: tuple | None = None
        self._hover: float | None = None
        self._dragging = False
        self.set_size_request(-1, self.HEIGHT)
        self.set_hexpand(True)
        self.set_cursor(Gdk.Cursor.new_from_name("pointer", None))

        click = Gtk.GestureClick.new()
        click.connect("pressed", self._on_press)
        click.connect("released", self._on_release)
        self.add_controller(click)

        drag = Gtk.GestureDrag.new()
        drag.connect("drag-update", self._on_drag)
        drag.connect("drag-end", self._on_drag_end)
        self.add_controller(drag)

        motion = Gtk.EventControllerMotion.new()
        motion.connect("motion", self._on_motion)
        motion.connect("leave", self._on_leave)
        self.add_controller(motion)

    def set_scheme(self, scheme):
        self._scheme = scheme
        self.queue_draw()

    def update(self, position, duration):
        if self._dragging:
            return
        changed = (abs(position - self.position) > 0.01
                   or abs((duration or 0) - self.duration) > 0.01)
        self.position = max(0.0, position)
        self.duration = max(0.0, duration or 0.0)
        if changed:
            self.queue_draw()

    def set_chapters(self, chapters):
        self.chapters = list(chapters or [])
        self.queue_draw()

    def set_loop_range(self, pair):
        self.loop_range = pair
        self.queue_draw()

    def set_buffered(self, fraction):
        self.buffered = max(0.0, min(1.0, fraction))
        self.queue_draw()

    # -- input ------------------------------------------------------------

    def _fraction_at(self, x) -> float:
        width = max(1.0, float(self.get_width()))
        return max(0.0, min(1.0, x / width))

    def _seconds_at(self, x) -> float:
        return self._fraction_at(x) * self.duration

    def _on_press(self, gesture, count, x, y):
        if self.duration <= 0:
            return
        self._dragging = True
        self.position = self._seconds_at(x)
        self.emit("scrubbing", self.position)
        self.queue_draw()

    def _on_release(self, gesture, count, x, y):
        if not self._dragging:
            return
        self._dragging = False
        self.emit("seeked", self._seconds_at(x))
        self.emit("scrub-ended")

    def _on_drag(self, gesture, dx, dy):
        if self.duration <= 0:
            return
        ok, start_x, _start_y = gesture.get_start_point()
        if not ok:
            return
        self._dragging = True
        self.position = self._seconds_at(start_x + dx)
        self.emit("scrubbing", self.position)
        self.queue_draw()

    def _on_drag_end(self, gesture, dx, dy):
        if not self._dragging:
            return
        ok, start_x, _start_y = gesture.get_start_point()
        self._dragging = False
        if ok:
            self.emit("seeked", self._seconds_at(start_x + dx))
        self.emit("scrub-ended")

    def _on_motion(self, controller, x, y):
        if self.duration <= 0:
            return
        self._hover = self._seconds_at(x)
        self.emit("hovered", self._hover)
        self.queue_draw()

    def _on_leave(self, controller):
        self._hover = None
        self.emit("hovered", -1.0)
        self.queue_draw()

    def hover_x(self) -> float:
        if self._hover is None or self.duration <= 0:
            return -1.0
        return self._hover / self.duration * float(self.get_width())

    # -- drawing ----------------------------------------------------------

    def do_snapshot(self, snapshot):
        width, height = float(self.get_width()), float(self.get_height())
        if width <= 1 or height <= 1:
            return
        scheme = self._scheme
        track_h = float(self.TRACK)
        top = (height - track_h) / 2
        radius = track_h / 2

        # The trough, as a well so it reads as a groove cut into the glass.
        glass.slab(snapshot, glass.Material.well(scheme, 1.0),
                   0, top, width, track_h, radius=radius, shadow=False,
                   grain_on=False)

        done = (self.position / self.duration) if self.duration > 0 else 0.0
        done = max(0.0, min(1.0, done))

        # What has been buffered but not yet played, which only shows for
        # something being streamed. A flat lighter band, under the played
        # part, so that it reads as "available" rather than as progress.
        if self.buffered < 0.999:
            box = glass.rounded(0, top, width * self.buffered, track_h, radius)
            snapshot.push_rounded_clip(box)
            snapshot.append_color(
                glass.white(0.16) if scheme.dark else glass.black(0.10),
                glass.rect(0, top, width * self.buffered, track_h))
            snapshot.pop()

        # The looped stretch, drawn behind the played part so the two can be
        # seen at once.
        if self.loop_range and self.duration > 0:
            start, stop = self.loop_range
            x0 = max(0.0, min(1.0, start / self.duration)) * width
            x1 = max(0.0, min(1.0, stop / self.duration)) * width
            if x1 > x0:
                snapshot.append_color(glass.rgba(scheme.accent, 0.22),
                                      glass.rect(x0, top - 3, x1 - x0, track_h + 6))
                for x in (x0, x1):
                    snapshot.append_color(glass.rgba(scheme.accent, 0.85),
                                          glass.rect(x - 1, top - 5, 2, track_h + 10))

        # The played part.
        if done > 0:
            box = glass.rounded(0, top, max(track_h, width * done), track_h, radius)
            snapshot.push_rounded_clip(box)
            snapshot.append_linear_gradient(
                glass.rect(0, top, width, track_h),
                glass.point(0, top), glass.point(width, top),
                glass.stops((0.0, glass.rgba(glass.shade(scheme.accent, -0.1), 1.0)),
                            (1.0, glass.rgba(glass.shade(scheme.accent, 0.18), 1.0))))
            snapshot.pop()

        # Chapter divisions. Hairlines rather than marks: they are a map of
        # the file, not controls, and anything thicker competes with the head.
        if self.chapters and self.duration > 0:
            for chapter in self.chapters:
                if chapter.start <= 0.01:
                    continue
                x = chapter.start / self.duration * width
                if 1 < x < width - 1:
                    snapshot.append_color(
                        glass.black(0.45) if not scheme.dark else glass.white(0.40),
                        glass.rect(x - 0.5, top, 1, track_h))

        # Where the pointer is, if it is here.
        hover_x = self.hover_x()
        if hover_x >= 0:
            snapshot.append_color(glass.white(0.45) if scheme.dark
                                  else glass.black(0.30),
                                  glass.rect(hover_x - 0.5, top - 4, 1, track_h + 8))

        # The head. A plain white disc with a shadow: it has to be findable
        # over a played bar of any colour, and white over an accent is the
        # one pairing that always works.
        if self.duration > 0:
            cx = width * done
            size = 15.0 if (self._dragging or hover_x >= 0) else 12.0
            box = glass.rounded(cx - size / 2, height / 2 - size / 2, size, size,
                                size / 2)
            snapshot.append_outset_shadow(box, glass.black(0.45), 0, 1, 0, 4)
            snapshot.push_rounded_clip(box)
            snapshot.append_color(glass.white(1.0),
                                  glass.rect(cx - size / 2, height / 2 - size / 2,
                                             size, size))
            snapshot.pop()


class Preview(Gtk.Widget):
    """The frame under the pointer, floating above the seek bar.

    Its own widget so that it can be positioned in the overlay by the window
    without a popover's arrow, padding and grab behaviour getting in the way
    — a popover that follows the pointer along a bar fights the pointer.
    """

    __gtype_name__ = "TheEyePreview"

    def __init__(self, scheme=None):
        super().__init__()
        self._scheme = scheme or theming.get(theming.DEFAULT)
        self._texture = None
        self._caption = ""
        self.set_can_target(False)
        self.set_size_request(PREVIEW_WIDTH, 128)

    def set_scheme(self, scheme):
        self._scheme = scheme
        self.queue_draw()

    def show_frame(self, texture, caption: str):
        self._texture = texture
        self._caption = caption
        if texture is not None:
            height = int(round(PREVIEW_WIDTH * texture.get_height()
                               / max(1, texture.get_width())))
            self.set_size_request(PREVIEW_WIDTH, height + 24)
        self.queue_draw()

    def do_snapshot(self, snapshot):
        width, height = float(self.get_width()), float(self.get_height())
        if width <= 1 or height <= 1:
            return
        material = glass.Material.card(self._scheme, 1.0, radius=12.0)
        glass.slab(snapshot, material, 0, 0, width, height, radius=12.0)
        if self._texture is not None:
            frame = glass.rect(4, 4, width - 8, height - 28)
            box = glass.rounded(4, 4, width - 8, height - 28, 8.0)
            snapshot.push_rounded_clip(box)
            snapshot.append_scaled_texture(self._texture,
                                           Gsk.ScalingFilter.LINEAR, frame)
            snapshot.pop()
        if self._caption:
            layout = self.create_pango_layout(self._caption)
            text_w, text_h = layout.get_pixel_size()
            snapshot.save()
            snapshot.translate(glass.point((width - text_w) / 2,
                                           height - text_h - 5))
            snapshot.append_layout(layout, glass.rgba(self._scheme.fg, 1.0))
            snapshot.restore()


class Transport(Gtk.Box):
    """The whole bar: the buttons, the timeline and the readouts."""

    __gtype_name__ = "TheEyeTransport"

    __gsignals__ = {
        "seek": (GObject.SignalFlags.RUN_FIRST, None, (float,)),
        "preview-wanted": (GObject.SignalFlags.RUN_FIRST, None, (float,)),
        "preview-done": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, scheme=None, config=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._scheme = scheme or theming.get(theming.DEFAULT)
        self._config = config or {}
        self._duration = 0.0
        self._position = 0.0
        self._remaining = False
        self._preview_timer = 0
        self._preview_at = -1.0

        self.set_margin_start(14)
        self.set_margin_end(14)
        self.set_margin_top(8)
        self.set_margin_bottom(10)

        self.seek = SeekBar(self._scheme)
        self.seek.connect("seeked", lambda _s, at: self.emit("seek", at))
        self.seek.connect("scrubbing", self._on_scrubbing)
        self.seek.connect("scrub-ended", lambda _s: self._refresh_times())
        self.seek.connect("hovered", self._on_hovered)
        self.append(self.seek)

        self.elapsed = widgets.Readout("0:00:00", xalign=0.0)
        self.total = widgets.Readout("0:00:00", xalign=1.0)
        self.total.add_css_class("dim")

        # Clicking the duration swaps it for how much is left, which is the
        # figure anybody watching something actually wants.
        total_button = Gtk.Button(child=self.total, tooltip_text="Time left")
        total_button.add_css_class("pill")
        total_button.connect("clicked", lambda _b: self._toggle_remaining())

        self.play = widgets.icon_button(
            "media-playback-start-symbolic", "Play  (Space)", "win.play-pause",
            classes=("transport", "primary"))
        self.previous = widgets.icon_button(
            "media-skip-backward-symbolic", "Previous  (Page Up)", "win.previous",
            classes=("transport",))
        self.next = widgets.icon_button(
            "media-skip-forward-symbolic", "Next  (Page Down)", "win.next",
            classes=("transport",))
        self.back = widgets.icon_button(
            "media-seek-backward-symbolic", "Back  (Left)", "win.seek-back",
            classes=("transport",))
        self.forward = widgets.icon_button(
            "media-seek-forward-symbolic", "Forward  (Right)", "win.seek-forward",
            classes=("transport",))

        self.chapter = widgets.label("", "caption", xalign=0.5, hexpand=True,
                                     ellipsize=3)

        self.volume = VolumeControl(self._scheme)
        self.speed = SpeedControl()

        self.tracks = widgets.icon_button(
            "media-view-subtitles-symbolic", "Tracks and subtitles",
            classes=("circular",))
        self.loop = widgets.toggle_button(
            "media-playlist-repeat-symbolic", "Repeat  (R)", "win.loop")
        self.fullscreen = widgets.icon_button(
            "view-fullscreen-symbolic", "Fullscreen  (F)", "win.fullscreen")

        left = widgets.row(self.elapsed, spacing=6)
        middle = widgets.row(self.previous, self.back, self.play, self.forward,
                             self.next, spacing=4, halign=Gtk.Align.CENTER)
        right = widgets.row(self.speed, self.volume, self.tracks, self.loop,
                            self.fullscreen, total_button, spacing=2,
                            halign=Gtk.Align.END)
        left.set_hexpand(True)
        right.set_hexpand(True)
        left.set_halign(Gtk.Align.START)

        self._left, self._middle, self._right = left, middle, right
        self._bar = widgets.row(left, middle, right, spacing=10)
        self._second = widgets.row(spacing=2, halign=Gtk.Align.CENTER)
        self._second.set_visible(False)
        self.append(self._bar)
        self.append(self._second)
        self.append(self.chapter)
        self._narrow = False
        self._refresh_times()

    def set_narrow(self, narrow: bool):
        """Put the right-hand controls on their own row, or take them back.

        Without this the bar's own minimum width is the window's minimum
        width, and under a tiling compositor that means the window cannot be
        put in a column — the floor was 740px with everything in one row and
        is 430 with this.
        """
        if narrow == self._narrow:
            return
        self._narrow = narrow
        if narrow:
            self._bar.remove(self._right)
            self._second.append(self._right)
            self._right.set_halign(Gtk.Align.CENTER)
            self._second.set_visible(True)
        else:
            self._second.remove(self._right)
            self._bar.append(self._right)
            self._right.set_halign(Gtk.Align.END)
            self._second.set_visible(False)

    # -- appearance -------------------------------------------------------

    def set_scheme(self, scheme):
        self._scheme = scheme
        self.seek.set_scheme(scheme)
        self.volume.set_scheme(scheme)

    # -- state ------------------------------------------------------------

    def update(self, position, duration):
        self._position = max(0.0, position)
        self._duration = max(0.0, duration or 0.0)
        self.seek.update(self._position, self._duration)
        self._refresh_times()

    def set_playing(self, playing: bool):
        self.play.set_icon_name("media-playback-pause-symbolic" if playing
                                else "media-playback-start-symbolic")
        self.play.set_tooltip_text("Pause  (Space)" if playing else "Play  (Space)")

    def set_chapters(self, chapters):
        self.seek.set_chapters(chapters)
        self.chapter.set_visible(bool(chapters))

    def set_chapter_name(self, name: str):
        self.chapter.set_text(name or "")

    def set_loop_range(self, pair):
        self.seek.set_loop_range(pair)

    def set_buffered(self, fraction):
        self.seek.set_buffered(fraction)

    def _toggle_remaining(self):
        self._remaining = not self._remaining
        self.total.set_tooltip_text("Total length" if self._remaining
                                    else "Time left")
        self._refresh_times()

    def _refresh_times(self):
        self.elapsed.set_text(metadata.human_time(self._position, long_form=True))
        if self._duration > 0:
            left = max(0.0, self._duration - self._position)
            self.total.set_text(("-" + metadata.human_time(left, long_form=True))
                                if self._remaining
                                else metadata.human_time(self._duration,
                                                         long_form=True))
        else:
            self.total.set_text(metadata.human_time(-1, long_form=True))

    def _on_scrubbing(self, _bar, seconds):
        self.elapsed.set_text(metadata.human_time(seconds, long_form=True))

    # -- the hover preview -------------------------------------------------

    def _on_hovered(self, _bar, seconds):
        if self._preview_timer:
            GLib.source_remove(self._preview_timer)
            self._preview_timer = 0
        if seconds < 0:
            self._preview_at = -1.0
            self.emit("preview-done")
            return
        rounded = round(seconds / PREVIEW_GRAIN) * PREVIEW_GRAIN
        if abs(rounded - self._preview_at) < 0.01:
            return
        self._preview_at = rounded
        self._preview_timer = GLib.timeout_add(PREVIEW_DELAY, self._ask_preview)

    def _ask_preview(self):
        self._preview_timer = 0
        if self._preview_at >= 0:
            self.emit("preview-wanted", self._preview_at)
        return GLib.SOURCE_REMOVE


class VolumeControl(Gtk.Box):
    """A mute button with a slider that appears when it is wanted.

    The slider lives in a popover rather than permanently in the bar, because
    a volume slider is adjusted about once a session and takes up room that
    the timeline wants.
    """

    __gtype_name__ = "TheEyeVolume"

    def __init__(self, scheme=None):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self._scheme = scheme or theming.get(theming.DEFAULT)
        self.button = widgets.icon_button(
            "audio-volume-high-symbolic", "Volume  (scroll, or M to mute)")
        self.append(self.button)

        self.scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL,
                                              0.0, 1.0, 0.01)
        self.scale.set_draw_value(False)
        self.scale.set_size_request(150, -1)
        self.scale.set_hexpand(True)

        mute = Gtk.Button(label="Mute")
        mute.add_css_class("pill")
        mute.set_action_name("win.mute")

        box = widgets.column(widgets.row(self.scale, spacing=6), mute, spacing=8,
                             margin_top=10, margin_bottom=10,
                             margin_start=12, margin_end=12)
        self.popover = Gtk.Popover(child=box, has_arrow=True)
        self.popover.set_parent(self.button)
        self.button.connect("clicked", lambda _b: self.popover.popup())

        # Scrolling the button is the fast way, and is what the pointer
        # expects when it is already over a volume control.
        scroll = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL
            | Gtk.EventControllerScrollFlags.DISCRETE)
        scroll.connect("scroll", self._on_scroll)
        self.button.add_controller(scroll)

    def set_scheme(self, scheme):
        self._scheme = scheme

    def _on_scroll(self, _controller, dx, dy):
        value = max(0.0, min(1.0, self.scale.get_value() - dy * 0.05))
        self.scale.set_value(value)
        return True

    def show_level(self, volume: float, muted: bool):
        if muted or volume <= 0.001:
            icon = "audio-volume-muted-symbolic"
        elif volume < 0.34:
            icon = "audio-volume-low-symbolic"
        elif volume < 0.72:
            icon = "audio-volume-medium-symbolic"
        else:
            icon = "audio-volume-high-symbolic"
        self.button.set_icon_name(icon)


class SpeedControl(Gtk.MenuButton):
    """The playback speed, as a menu of the speeds anybody uses.

    The label is the speed itself rather than an icon, because the one thing
    you need to know about playback speed is whether it is still 1x — and an
    icon cannot tell you that.
    """

    __gtype_name__ = "TheEyeSpeed"

    def __init__(self):
        super().__init__(valign=Gtk.Align.CENTER)
        self.add_css_class("pill")
        self.set_tooltip_text("Playback speed  ([ and ])")
        self._label = widgets.label("1x", "numeric", xalign=0.5)
        self.set_child(self._label)

        menu = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                       margin_top=8, margin_bottom=8,
                       margin_start=8, margin_end=8)
        for speed in SPEEDS:
            text = f"{speed:g}x" + ("   normal" if speed == 1.0 else "")
            button = Gtk.Button(label=text)
            button.add_css_class("pill")
            button.set_has_frame(False)
            button.get_child().set_xalign(0.0)
            # float(), not :g — "win.speed(1)" parses as an integer and the
            # action takes a double, which GTK reports as a type mismatch and
            # then does nothing at all.
            button.set_detailed_action_name(f"win.speed({float(speed)!r})")
            menu.append(button)
        self.set_popover(Gtk.Popover(child=menu))

    def show_speed(self, speed: float):
        self._label.set_text(f"{speed:g}x")
        self._label.remove_css_class("statusmark")
        if abs(speed - 1.0) > 0.001:
            self._label.add_css_class("statusmark")
