"""The window: the layout, the actions, and the decisions between them.

The layout is an overlay, not a box. The stage fills the window and everything
else floats on top of it — the title bar, the transport, the strip of
thumbnails, the editor. That is the whole visual idea of the app: the picture
is the window, and the controls are glass laid over it. It is also why they
have to be able to get out of the way, which is what `_chrome` below is
about.

What lives here rather than anywhere else: opening things, deciding whether a
file is a picture or a film and wiring up whichever it is, moving through the
folder, the slideshow clock, remembering where a video got to, saving grades
beside their pictures, and the sixty-odd actions from `shortcuts.py`. The
drawing is in `stage.py` and `glass.py`, the colour work in `effects.py`, the
pipeline in `player.py`; none of them know about each other and all of them
are joined up here.

Two behaviours are worth knowing before reading the code.

*The arrow keys mean different things to a photograph and to a film.* Left
and Right move through the folder when a still is on screen and seek when a
video is playing. This is the one place the app deliberately overloads a key,
because both meanings are so strongly established that binding either one
everywhere would be wrong half the time.

*Opening a file opens its folder.* Every picture arrives as a position in a
reel, even when it was opened on its own from a file manager, because
"what's the next one" is the question a viewer exists to answer.
"""

from __future__ import annotations

import os
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk  # noqa: E402

from . import (config as configuration, editor as editing,  # noqa: E402
               export as exporting, filmstrip, glass, grade as grading,
               housestyle, jobs, library, looks, media, metadata, photo,
               player, stage as staging, theme as theming, thumbs,
               transport as transporting, widgets)
from .grade import Grade  # noqa: E402

# How wide the window has to be before the transport bar can hold everything
# in one row. Below this a breakpoint moves the right-hand controls onto their
# own line, which is what lets the window live in a 455px tiling column.
NARROW = 720

# A grade is kept beside its picture in a file of this name, so that it
# survives the app's own state being cleared and travels with the folder.
SIDECAR = ".the-eye.grades.json"


class MainWindow(Adw.ApplicationWindow):
    """One window, on one folder, showing one thing from it."""

    __gtype_name__ = "TheEyeWindow"

    def __init__(self, app, config=None, state=None):
        super().__init__(application=app)
        self.config = config if config is not None else configuration.Config()
        self.state = state if state is not None else configuration.State()
        self.scheme = theming.get(self._theme_key())
        self.add_css_class("the-eye")
        self.add_css_class("house")
        self.set_title("The Eye")

        self.reel = library.Reel(order=self.config["sort"],
                                 descending=self.config["sort_descending"],
                                 wrap=self.config["wrap_around"])
        self.grade = Grade()
        self._still: photo.Still | None = None
        self._animation = None
        self._loading = ""
        self._slideshow = 0
        self._hide_timer = 0
        self._chrome_shown = True
        self._hovered = set()
        self._compare = None
        self._export = None
        self._sidecars: dict[str, dict] = {}
        self._pending_resume = 0.0
        self._loop_mark = None
        self._sound_face = widgets.SoundFace(self.scheme)
        self._geometry_saved = False
        # The remembered size and maximise, applied before the first map —
        # see housestyle.Geometry for why the two are treated differently
        # under a tiling compositor.
        self.geometry = housestyle.Geometry(self, self.config)
        self.geometry.restore()

        self.thumbs = thumbs.Cache(self.config["strip_size"])
        self.engine = player.Engine(max_size=self._frame_cap())
        self._wire_engine()

        self._build()
        self._install_actions()
        self._apply_theme()
        self._apply_config()
        self.connect("close-request", self._on_close)
        self.connect("notify::fullscreened", lambda *_: self._refresh_chrome())

        # Follow the desktop between light and dark, while it is being
        # followed. The listener is held so it can be given back: the
        # SystemScheme is process-wide, so one left attached keeps a closed
        # window alive and delivers the next change to every window this
        # process has ever opened.
        self._scheme_watch = lambda _dark: self._follow_system()
        housestyle.system().watch(self._scheme_watch)
        self.connect("destroy", lambda *_: housestyle.system().unwatch(
            self._scheme_watch))

    # ----------------------------------------------------------- building ----

    def _frame_cap(self):
        """The largest video frame worth decoding: the biggest monitor here.

        Asked once. A 4K film on a 1366x768 laptop is scaled down inside the
        pipeline, which turns a 33MB copy per frame into a 4MB one — and since
        it was going to be scaled to the window anyway, nothing is lost.
        """
        widest, tallest = 1920, 1080
        display = Gdk.Display.get_default()
        if display is not None:
            monitors = display.get_monitors()
            for index in range(monitors.get_n_items()):
                monitor = monitors.get_item(index)
                area = monitor.get_geometry()
                scale = monitor.get_scale_factor() or 1
                widest = max(widest, area.width * scale)
                tallest = max(tallest, area.height * scale)
        return (widest, tallest)

    def _build(self):
        self.toasts = Adw.ToastOverlay()
        self.set_content(self.toasts)

        self.root = Gtk.Overlay()
        self.toasts.set_child(self.root)

        self.ambient = glass.Ambient(self.scheme, self.config["translucency"],
                                     self.config["motion"])
        self.root.set_child(self.ambient)

        self.stage = staging.Stage(self.scheme, self.config)
        self.stage.connect("activated", lambda _s: self._on_stage_clicked())
        self.stage.connect("stepped", lambda _s, by: self.step(by))
        self.stage.connect("scrubbed", lambda _s, by: self.engine.skip(by))
        self.stage.connect("volume-rolled", lambda _s, by: self._nudge_volume(by))
        self.stage.connect("view-changed", lambda _s: self._refresh_title())
        self.root.add_overlay(self.stage)

        self.root.add_overlay(self._top_bar())
        self.root.add_overlay(self._bottom())
        self.root.add_overlay(self._editor_panel())
        self.root.add_overlay(self._info_panel())
        self.root.add_overlay(self._osd())
        self.root.add_overlay(self._preview_card())
        self.root.add_overlay(self._progress_card())

        # Any pointer movement brings the chrome back. On the root rather than
        # on the stage, so that moving over a bar counts as activity too and
        # the bars do not fade out from under the pointer.
        motion = Gtk.EventControllerMotion.new()
        motion.connect("motion", self._on_motion)
        motion.connect("leave", lambda _c: self._on_pointer_left())
        self.root.add_controller(motion)

        drop = Gtk.DropTarget.new(Gio.File, Gdk.DragAction.COPY)
        drop.connect("drop", self._on_drop)
        self.root.add_controller(drop)

        # Narrow windows: the transport's right-hand group moves to its own
        # row. Without this the window cannot be made narrower than the bar,
        # which under a tiling compositor means it cannot be tiled.
        breakpoint_ = Adw.Breakpoint.new(
            Adw.BreakpointCondition.parse(f"max-width: {NARROW}px"))
        breakpoint_.connect("apply", lambda *_: self._set_narrow(True))
        breakpoint_.connect("unapply", lambda *_: self._set_narrow(False))
        self.add_breakpoint(breakpoint_)

    def _top_bar(self):
        self.title = widgets.label("The Eye", "title", xalign=0.0, ellipsize=3)
        self.subtitle = widgets.label("", "caption", xalign=0.0, ellipsize=3)
        titles = widgets.column(self.title, self.subtitle, spacing=0,
                                hexpand=True, valign=Gtk.Align.CENTER)

        menu = Gtk.MenuButton(icon_name="open-menu-symbolic",
                              tooltip_text="Menu", valign=Gtk.Align.CENTER)
        menu.add_css_class("circular")
        menu.set_menu_model(self._menu())
        self.menu_button = menu
        menu.connect("notify::active", lambda *_: self._restart_hide_timer())

        self.info_button = widgets.toggle_button(
            "view-more-horizontal-symbolic", "Details  (I)", "win.info")
        self.editor_button = widgets.toggle_button(
            "applications-graphics-symbolic", "Edit  (E)", "win.editor")
        strip_button = widgets.toggle_button(
            "view-grid-symbolic", "Thumbnails  (T)", "win.strip")
        self.strip_button = strip_button

        bar = widgets.row(
            widgets.icon_button("document-open-symbolic", "Open  (Ctrl+O)",
                                "win.open"),
            titles, strip_button, self.info_button, self.editor_button,
            widgets.icon_button("view-fullscreen-symbolic",
                                "Fullscreen  (F)", "win.fullscreen"),
            menu,
            widgets.icon_button("window-close-symbolic", "Close  (Ctrl+W)",
                                "win.close"),
            spacing=2, margin_start=12, margin_end=10,
            margin_top=8, margin_bottom=8)

        self.top_glass = widgets.Glass(bar, kind="bar", scheme=self.scheme,
                                       translucency=self.config["translucency"])
        self.top_reveal = Gtk.Revealer(
            child=self.top_glass, valign=Gtk.Align.START,
            transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
            transition_duration=180, reveal_child=True,
            margin_start=10, margin_end=10, margin_top=10)
        # The window has to be draggable by its glass, since there is no real
        # header bar to grab.
        drag = Gtk.WindowHandle(child=self.top_reveal)
        drag.set_valign(Gtk.Align.START)
        self._watch_hover(self.top_glass)
        return drag

    def _menu(self):
        menu = Gio.Menu()

        files = Gio.Menu()
        files.append("Open a File…", "win.open")
        files.append("Open a Folder…", "win.open-folder")
        self.recent_menu = Gio.Menu()
        files.append_submenu("Recent", self.recent_menu)
        menu.append_section(None, files)

        view = Gio.Menu()
        view.append("Fit the Window", "win.zoom-fit")
        view.append("Actual Size", "win.zoom-actual")
        view.append("Fill the Window", "win.zoom-fill")
        view.append("Slideshow", "win.slideshow")
        view.append("Sort…", "win.sort")
        menu.append_section("Looking", view)

        work = Gio.Menu()
        work.append("Edit", "win.editor")
        work.append("Export a Copy…", "win.export")
        work.append("Save This Frame…", "win.snapshot")
        work.append("Copy the Picture", "win.copy-image")
        work.append("Copy the Path", "win.copy-path")
        work.append("Show in Files", "win.show-in-files")
        work.append("Move to Wastebasket", "win.trash")
        menu.append_section("This file", work)

        app_items = Gio.Menu()
        app_items.append("Another Window", "app.new-window")
        app_items.append("Preferences…", "win.preferences")
        app_items.append("Keyboard Shortcuts", "win.shortcuts")
        app_items.append("About The Eye", "win.about")
        menu.append_section(None, app_items)
        return menu

    def _bottom(self):
        """The transport and the strip, stacked at the foot of the window."""
        self.transport = transporting.Transport(self.scheme, self.config)
        self.transport.connect("seek", lambda _t, at: self.engine.seek(at))
        self.transport.connect("preview-wanted", self._on_preview_wanted)
        self.transport.connect("preview-done", lambda _t: self._hide_preview())
        self.transport.tracks.connect("clicked",
                                      lambda _b: self._tracks_popover(_b))
        self.transport_glass = widgets.Glass(
            self.transport, kind="bar", scheme=self.scheme,
            translucency=self.config["translucency"])
        self.transport_reveal = Gtk.Revealer(
            child=self.transport_glass,
            transition_type=Gtk.RevealerTransitionType.SLIDE_UP,
            transition_duration=180, reveal_child=False)

        self.strip = filmstrip.Filmstrip(self.scheme, self.config["strip_size"])
        self.strip.set_sources(self.thumbs, self.state.progress_for)
        self.strip.connect("chosen", lambda _s, index: self.go_to(index))
        self.strip_glass = widgets.Glass(
            self.strip, kind="strip", radius=14.0, scheme=self.scheme,
            translucency=self.config["translucency"])
        self.strip_reveal = Gtk.Revealer(
            child=self.strip_glass,
            transition_type=Gtk.RevealerTransitionType.SLIDE_UP,
            transition_duration=180, reveal_child=False)

        box = widgets.column(self.strip_reveal, self.transport_reveal, spacing=8,
                             valign=Gtk.Align.END,
                             margin_start=10, margin_end=10, margin_bottom=10)
        self._watch_hover(self.strip_glass)
        self._watch_hover(self.transport_glass)
        return box

    def _editor_panel(self):
        self.editor = editing.Editor(self.scheme)
        self.editor.connect("changed", lambda _e: self._on_grade_changed())
        self.editor.connect("closed", lambda _e: self._set_editor(False))
        self.editor.connect("export-wanted", lambda _e: self.activate_action(
            "win.export", None))
        self.editor.connect("compare-toggled",
                            lambda _e, on: self._set_compare(on))
        self.editor_glass = widgets.Glass(
            self.editor, kind="panel", scheme=self.scheme,
            translucency=self.config["translucency"])
        self.editor_reveal = Gtk.Revealer(
            child=self.editor_glass, halign=Gtk.Align.END,
            transition_type=Gtk.RevealerTransitionType.SLIDE_LEFT,
            transition_duration=200, reveal_child=False,
            margin_top=78, margin_bottom=10, margin_end=10)
        return self.editor_reveal

    def _info_panel(self):
        self.info_list = widgets.column(spacing=0, margin_start=14,
                                        margin_end=14, margin_top=10,
                                        margin_bottom=14)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                      vexpand=True)
        scroller.set_child(self.info_list)
        header = widgets.row(
            widgets.label("Details", "heading", xalign=0.0, hexpand=True),
            widgets.icon_button("edit-copy-symbolic", "Copy all of this",
                                on_click=self._copy_info),
            widgets.icon_button("window-close-symbolic", "Close",
                                on_click=lambda: self._set_info(False)),
            spacing=2, margin_start=14, margin_end=10, margin_top=10)
        box = widgets.column(header, scroller, spacing=4)
        box.set_size_request(300, -1)
        self.info_glass = widgets.Glass(
            box, kind="panel", scheme=self.scheme,
            translucency=self.config["translucency"])
        self.info_reveal = Gtk.Revealer(
            child=self.info_glass, halign=Gtk.Align.START,
            transition_type=Gtk.RevealerTransitionType.SLIDE_RIGHT,
            transition_duration=200, reveal_child=False,
            margin_top=78, margin_bottom=10, margin_start=10)
        return self.info_reveal

    def _osd(self):
        """The big transient readout: the volume, the speed, the zoom.

        A card in the middle of the window rather than a toast, because it is
        not a message — it is the current value of the thing being changed,
        and it should be replaced rather than queued.
        """
        self.osd_label = widgets.label("", "osd-big", xalign=0.5)
        self.osd_glass = widgets.Glass(
            widgets.column(self.osd_label, spacing=0, margin_top=14,
                           margin_bottom=14, margin_start=26, margin_end=26),
            kind="card", scheme=self.scheme,
            translucency=self.config["translucency"])
        self.osd_reveal = Gtk.Revealer(
            child=self.osd_glass, halign=Gtk.Align.CENTER,
            valign=Gtk.Align.CENTER, reveal_child=False,
            transition_type=Gtk.RevealerTransitionType.CROSSFADE,
            transition_duration=140)
        self._osd_timer = 0
        return self.osd_reveal

    def _preview_card(self):
        self.preview = transporting.Preview(self.scheme)
        self.preview_reveal = Gtk.Revealer(
            child=self.preview, halign=Gtk.Align.START, valign=Gtk.Align.END,
            reveal_child=False,
            transition_type=Gtk.RevealerTransitionType.CROSSFADE,
            transition_duration=110, margin_bottom=96)
        self._preview_cache: dict = {}
        self._preview_busy = False
        return self.preview_reveal

    def _progress_card(self):
        """The export's progress, which is the one long operation in the app."""
        self.progress_label = widgets.label("", xalign=0.0)
        self.progress_bar = Gtk.ProgressBar(hexpand=True)
        cancel = Gtk.Button(label="Stop")
        cancel.add_css_class("pill")
        cancel.connect("clicked", lambda _b: self._cancel_export())
        box = widgets.column(
            widgets.row(widgets.label("Exporting", "title", xalign=0.0,
                                      hexpand=True), cancel, spacing=8),
            self.progress_bar, self.progress_label,
            spacing=8, margin_top=12, margin_bottom=12,
            margin_start=16, margin_end=16)
        box.set_size_request(320, -1)
        self.progress_glass = widgets.Glass(
            box, kind="card", scheme=self.scheme,
            translucency=self.config["translucency"])
        self.progress_reveal = Gtk.Revealer(
            child=self.progress_glass, halign=Gtk.Align.CENTER,
            valign=Gtk.Align.CENTER, reveal_child=False,
            transition_type=Gtk.RevealerTransitionType.CROSSFADE,
            transition_duration=140)
        return self.progress_reveal

    # ------------------------------------------------------------- actions ----

    def _install_actions(self):
        simple = {
            "open": self._choose_file,
            "open-folder": self._choose_folder,
            "reload": self.reload,
            "next": lambda: self.step(1),
            "previous": lambda: self.step(-1),
            "first": lambda: self.go_to(0),
            "last": lambda: self.go_to(len(self.reel) - 1),
            "play-pause": self._toggle_play,
            "seek-back": lambda: self._seek_relative(-self.config["seek_short"]),
            "seek-forward": lambda: self._seek_relative(self.config["seek_short"]),
            "seek-back-long": lambda: self._seek_relative(-self.config["seek_long"]),
            "seek-forward-long": lambda: self._seek_relative(self.config["seek_long"]),
            "frame-back": lambda: self.engine.step(-1),
            "frame-forward": lambda: self.engine.step(1),
            "slower": lambda: self._change_speed(-1),
            "faster": lambda: self._change_speed(1),
            "normal-speed": lambda: self._set_speed(1.0),
            "volume-up": lambda: self._nudge_volume(0.05),
            "volume-down": lambda: self._nudge_volume(-0.05),
            "mute": self._toggle_mute,
            "loop": self._cycle_loop,
            "loop-start": lambda: self._mark_loop("start"),
            "loop-end": lambda: self._mark_loop("end"),
            "loop-clear": lambda: self._mark_loop("clear"),
            "subtitles": self._toggle_subtitles,
            "next-subtitle": lambda: self._cycle_track("text"),
            "next-audio": lambda: self._cycle_track("audio"),
            "subtitle-earlier": lambda: self._shift_subtitles(-0.25),
            "subtitle-later": lambda: self._shift_subtitles(0.25),
            "audio-earlier": lambda: self._shift_audio(-0.05),
            "audio-later": lambda: self._shift_audio(0.05),
            "zoom-in": lambda: self._zoom(self.config["zoom_steps"]),
            "zoom-out": lambda: self._zoom(1 / self.config["zoom_steps"]),
            "zoom-actual": lambda: self._fit("actual"),
            "zoom-fit": lambda: self._fit("window"),
            "zoom-fill": lambda: self._fit("fill"),
            "fullscreen": self._toggle_fullscreen,
            "slideshow": self._toggle_slideshow,
            "sort": self._sort_dialog,
            "rotate-right": lambda: self._rotate(90),
            "rotate-left": lambda: self._rotate(-90),
            "flip-h": lambda: self._flip("flip_h"),
            "flip-v": lambda: self._flip("flip_v"),
            "reset-grade": self._reset_grade,
            "copy-grade": self._copy_grade,
            "paste-grade": self._paste_grade,
            "compare": lambda: self._set_compare(self._compare is None),
            "export": self._export_dialog,
            "snapshot": self._save_frame,
            "copy-image": self._copy_image,
            "copy-path": self._copy_path,
            "show-in-files": self._show_in_files,
            "trash": self._trash,
            "set-wallpaper": self._set_wallpaper,
            "theme-next": self._next_theme,
            "preferences": self._preferences,
            "shortcuts": self._shortcuts_window,
            "about": self._about,
            "close": self.close,
        }
        for name, handler in simple.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, fn=handler: fn())
            self.add_action(action)

        for name, getter in (("info", lambda: self.config["show_info"]),
                             ("editor", lambda: self.config["editor_open"]),
                             ("strip", lambda: self.config["show_strip"])):
            action = Gio.SimpleAction.new_stateful(
                name, None, GLib.Variant.new_boolean(bool(getter())))
            action.connect("activate", self._on_toggle_action, name)
            self.add_action(action)

        speed = Gio.SimpleAction.new("speed", GLib.VariantType.new("d"))
        speed.connect("activate",
                      lambda _a, value: self._set_speed(value.get_double()))
        self.add_action(speed)

        track = Gio.SimpleAction.new("track", GLib.VariantType.new("s"))
        track.connect("activate", self._on_track_action)
        self.add_action(track)

        subtitle_file = Gio.SimpleAction.new("subtitle-file",
                                             GLib.VariantType.new("s"))
        subtitle_file.connect(
            "activate",
            lambda _a, v: self.engine.set_subtitle_file(v.get_string()))
        self.add_action(subtitle_file)

        chapter = Gio.SimpleAction.new("chapter", GLib.VariantType.new("d"))
        chapter.connect("activate",
                        lambda _a, v: self.engine.seek(v.get_double()))
        self.add_action(chapter)

    def _on_toggle_action(self, action, _param, name):
        wanted = not action.get_state().get_boolean()
        if name == "info":
            self._set_info(wanted)
        elif name == "editor":
            self._set_editor(wanted)
        else:
            self._set_strip(wanted)

    def _on_track_action(self, _action, value):
        kind, _, ident = value.get_string().partition(":")
        self.engine.select_track(kind, ident or None)

    # ------------------------------------------------------------ opening ----

    def open_path(self, path: str, play: bool = True):
        """Open a file or a folder — the one entry point from outside."""
        path = os.path.abspath(os.path.expanduser(path))
        if os.path.isdir(path):
            self.open_folder(path)
            return
        if not os.path.exists(path):
            widgets.toast(self, f"There is nothing at {path}")
            return
        if media.is_playlist(path):
            self._open_playlist(path)
            return
        if not media.is_viewable(path):
            widgets.toast(self, f"The Eye does not open {media.describe(path)} files")
            return
        folder = os.path.dirname(path) or "."
        self.state.remember_file(path)
        self._scan(folder, select=path, play=play)

    def open_folder(self, folder: str):
        self.state.remember_folder(folder)
        self._scan(folder, select="", play=self.config["autoplay"])

    def _open_playlist(self, path: str):
        """A .m3u or .pls as a reel of its own.

        Only the lines that name something that exists are kept, because a
        playlist written on another machine is mostly paths that do not.
        """
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                lines = handle.read().splitlines()
        except OSError as exc:
            widgets.error_toast(self, exc)
            return
        folder = os.path.dirname(os.path.abspath(path))
        items = []
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.lower().startswith("file"):
                _, _, line = line.partition("=")
            candidate = line if os.path.isabs(line) else os.path.join(folder, line)
            if os.path.isfile(candidate) and media.is_viewable(candidate):
                try:
                    stat = os.stat(candidate)
                except OSError:
                    continue
                items.append(library.Item(candidate, os.path.basename(candidate),
                                          media.kind(candidate), stat.st_size,
                                          stat.st_mtime))
        if not items:
            widgets.toast(self, "Nothing in that playlist could be found")
            return
        # A playlist is in the order it was written, so the sort is left alone.
        self.reel.items = items
        self.reel.index = 0
        self._after_scan("", self.config["autoplay"])

    def _scan(self, folder: str, select: str, play: bool):
        self._stop_slideshow()

        def work():
            return library.scan(folder, self.config["recurse"],
                                self.config["show_hidden"])

        def done(result):
            items, trouble = result
            if trouble:
                widgets.toast(self, trouble, timeout=5)
            if not items:
                self._show_empty(folder)
                return
            self.reel.set_items(items, keep=select)
            if select:
                self.reel.go_to_path(select)
            self._after_scan(select, play)

        jobs.run(work, done, lambda exc: widgets.error_toast(self, exc))

    def _after_scan(self, select, play):
        self.thumbs.clear()
        self.strip.set_items(self.reel.items, self.reel.index)
        self._load_current(play=play)
        self._refresh_recent()

    def _show_empty(self, folder):
        self.stage.set_content(None)
        self.title.set_text("Nothing to show")
        self.subtitle.set_text(f"No pictures or video in {folder}")
        self.transport_reveal.set_reveal_child(False)
        self.strip_reveal.set_reveal_child(False)

    # ------------------------------------------------------------- loading ----

    def go_to(self, index: int):
        if self.reel.go_to(index) is not None:
            self._load_current()

    def step(self, by: int):
        item = self.reel.step(by)
        if item is None:
            if self._slideshow:
                self._stop_slideshow()
                widgets.toast(self, "That was the last one")
            return
        self._load_current()

    def _load_current(self, play: bool = True):
        item = self.reel.current()
        if item is None:
            return
        self._remember_position()
        self.engine.stop()
        self._stop_animation()
        self.grade = self._grade_for(item.path)
        self.editor.set_grade(self.grade)
        self.stage.set_grade(self.grade)
        self.strip.set_selected(self.reel.index)
        self._loading = item.path
        self._refresh_title()
        self._refresh_info()

        if item.is_playable():
            self._load_video(item, play)
        else:
            self._load_photo(item)
        self._refresh_chrome()
        self._restart_hide_timer()

    def _load_video(self, item, play: bool):
        # A sound file has no frames, so the stage gets its artwork instead —
        # or, until the tags have been read and if there turns out to be
        # none, a drawn record. The empty stage this replaces reads as a
        # broken app rather than as music.
        if item.is_audio():
            self.stage.set_content(self._sound_face)
        else:
            self.stage.set_content(self.engine.paintable)
        self.stage.set_playing(play)
        subtitle = ""
        sidecars = media.sidecar_subtitles(item.path)
        if sidecars and self.config["subtitles"]:
            subtitle = sidecars[0]
        at = (self.state.position_for(item.path)
              if self.config["resume_playback"] else 0.0)
        self._pending_resume = at
        self.engine.set_volume(self.config["volume"])
        self.engine.set_muted(self.config["muted"])
        self.engine.set_subtitles_enabled(self.config["subtitles"])
        self.engine.set_hardware_decoding(self.config["hardware_decoding"])
        self.engine.open(item.path, start_at=at, subtitle=subtitle,
                         play=play and self.config["autoplay"])
        if abs(self.config["speed"] - 1.0) > 0.001:
            GLib.timeout_add(400, lambda: (self.engine.set_rate(
                self.config["speed"]), GLib.SOURCE_REMOVE)[1])
        if at > 1:
            widgets.toast(self, f"Carrying on from "
                                f"{metadata.human_time(at, long_form=True)}")

    def _load_photo(self, item):
        path = item.path
        self.stage.set_playing(False)

        def work():
            return photo.load(path, animate=True)

        def done(still):
            if self._loading != path:
                return                  # we have moved on since
            if not still.ok():
                self.stage.set_content(None)
                self.subtitle.set_text(still.error or "Could not be shown")
                widgets.toast(self, still.error or "That picture could not be read",
                              timeout=5)
                return
            self._still = still
            if still.animation is not None:
                self._start_animation(still.animation)
            else:
                # A GdkTexture is itself a GdkPaintable, so it goes straight
                # to the stage — no wrapper, and nothing to keep in step.
                self.stage.set_content(still.texture)
            self.stage.set_fit(self.config["fit"], animate=False)
            self._refresh_title()
            self._refresh_info()

        jobs.run(work, done, lambda exc: widgets.error_toast(self, exc))

    def _start_animation(self, animation):
        self._animation = animation
        self.stage.set_content(animation.texture())
        self.stage.set_playing(True)

        def advanced(_a):
            self.stage.set_content(animation.texture(), fade=False)

        self._animation_handler = animation.connect("advance", advanced)
        animation.start()

    def _stop_animation(self):
        if self._animation is not None:
            self._animation.stop()
            self._animation = None

    def reload(self):
        item = self.reel.current()
        if item is None:
            return
        self.thumbs.forget(item.path)
        self._load_current()

    # --------------------------------------------------------------- grades ----

    def _sidecar_path(self, folder: str) -> str:
        return os.path.join(folder, SIDECAR)

    def _grade_for(self, path: str) -> Grade:
        """This picture's grade: from the sidecar beside it, or from state.

        The sidecar wins, and is where a grade is written when the folder can
        be written to. It travels with the pictures — copy the folder to
        another machine and the grades come too — which an entry in the app's
        own state does not.
        """
        if not self.config["remember_grades"]:
            return Grade()
        folder = os.path.dirname(path)
        if folder not in self._sidecars:
            self._sidecars[folder] = self._read_sidecar(folder)
        found = self._sidecars[folder].get(os.path.basename(path))
        if isinstance(found, dict):
            return Grade(found)
        return Grade(self.state.grade_for(path))

    def _read_sidecar(self, folder: str) -> dict:
        import json

        try:
            with open(self._sidecar_path(folder), encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save_grade(self):
        item = self.reel.current()
        if item is None or not self.config["remember_grades"]:
            return
        import json

        folder = os.path.dirname(item.path)
        store = dict(self._sidecars.get(folder) or {})
        trimmed = {key: value for key, value in self.grade.items()
                   if value != grading.DEFAULTS[key]}
        if trimmed:
            store[item.name] = trimmed
        else:
            store.pop(item.name, None)
        self._sidecars[folder] = store
        path = self._sidecar_path(folder)
        try:
            if store:
                tmp = path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as handle:
                    json.dump(store, handle, indent=2, sort_keys=True)
                os.replace(tmp, path)
            elif os.path.exists(path):
                os.remove(path)
        except OSError:
            # A read-only folder, a camera card, a file on a share. The grade
            # still has somewhere to live.
            self.state.remember_grade(item.path, trimmed)

    def _on_grade_changed(self):
        self.grade = self.editor.grade()
        self.stage.set_grade(self.grade)
        self._save_grade()
        self._refresh_title()

    def _rotate(self, degrees):
        self.grade.rotate(degrees)
        self.editor.set_grade(self.grade)
        self.stage.set_grade(self.grade)
        self.stage.reset_view()
        self._save_grade()

    def _flip(self, key):
        self.grade[key] = not self.grade[key]
        self.stage.set_grade(self.grade)
        self._save_grade()

    def _reset_grade(self):
        self.editor.reset_all()

    def _copy_grade(self):
        widgets.copy_text(self, self.grade.to_json(), "Grade copied")

    def _paste_grade(self):
        clipboard = Gdk.Display.get_default().get_clipboard()

        def landed(_clipboard, result):
            try:
                text = clipboard.read_text_finish(result)
            except GLib.Error:
                text = None
            if not text:
                widgets.toast(self, "There is no grade on the clipboard")
                return
            fresh = Grade.from_json(text)
            if fresh.is_neutral() and text.strip() not in ("{}", ""):
                widgets.toast(self, "That did not look like a grade")
                return
            self.editor.set_grade(fresh)
            self._on_grade_changed()
            widgets.toast(self, "Grade pasted")

        clipboard.read_text_async(None, landed)

    # --------------------------------------------------------------- player ----

    def _wire_engine(self):
        self.engine.connect("state", self._on_player_state)
        self.engine.connect("progress", self._on_progress)
        self.engine.connect("ended", lambda _e: self._on_ended())
        self.engine.connect("failed", self._on_player_failed)
        self.engine.connect("tracks", lambda _e: self._on_tracks())
        self.engine.connect("tags", lambda _e: self._on_tags())
        self.engine.connect("buffering", self._on_buffering)
        self.engine.connect("first-frame", lambda _e: self._on_first_frame())

    def _on_player_state(self, _engine, state):
        playing = state == "playing"
        self.transport.set_playing(playing)
        self.stage.set_playing(playing and self.config["editor_live"])
        self.ambient.suspend(playing)
        if playing:
            self._restart_hide_timer()
        else:
            self._show_chrome()

    def _on_progress(self, _engine, position, duration):
        self.transport.update(position, duration)
        chapter = self.engine.chapter_at(position)
        self.transport.set_chapter_name(chapter.title if chapter else "")

    def _on_tags(self):
        """Tags have arrived — which is how album art turns up, late."""
        self._refresh_title()
        item = self.reel.current()
        if (item is not None and item.is_audio()
                and self.engine.artwork is not None
                and self.stage.content() is not self.engine.artwork):
            self.stage.set_content(self.engine.artwork)
            self.stage.set_fit(self.config["fit"], animate=False)

    def _on_first_frame(self):
        self.stage.set_fit(self.config["fit"], animate=False)
        item = self.reel.current()
        if item is not None and self.engine.duration > 0:
            self.strip.note_duration(item.path, self.engine.duration)

    def _on_tracks(self):
        self.transport.set_chapters(self.engine.chapters)
        self._refresh_info()

    def _on_buffering(self, _engine, percent):
        self.transport.set_buffered(1.0 if percent < 0 else percent / 100.0)

    def _on_player_failed(self, _engine, message, _detail):
        """Report a failure, but only for the file it is about.

        A pipeline being torn down can emit an error after the window has
        already moved to the next file — which is how a photograph ends up
        captioned "cannot play a text file". If what the engine was last
        asked for is not what is on screen, the error belongs to something
        that is no longer here.
        """
        item = self.reel.current()
        if item is None or item.path != self.engine.path:
            return
        widgets.toast(self, message, timeout=6)
        self.subtitle.set_text(message)

    def _on_ended(self):
        item = self.reel.current()
        if item is not None:
            self.state.forget_position(item.path)
        loop = self.config["loop"]
        if loop == "one":
            self.engine.seek(0)
            self.engine.play()
            return
        if loop == "all" or self.reel.has_next():
            self.step(1)
            return
        self.transport.set_playing(False)

    def _toggle_play(self):
        item = self.reel.current()
        if item is None:
            return
        if item.is_playable():
            self.engine.toggle()
        elif self._animation is not None:
            if self._animation.playing():
                self._animation.stop()
            else:
                self._animation.start()
        else:
            self._toggle_slideshow()

    def _on_stage_clicked(self):
        item = self.reel.current()
        if item is not None and item.is_playable():
            self.engine.toggle()

    def _seek_relative(self, seconds):
        """Left and Right: seek a video, change picture for a still.

        The one deliberately overloaded key in the app. Both meanings are too
        well established to give up, and which one is wanted is never
        ambiguous once you can see what is on screen.
        """
        item = self.reel.current()
        if item is not None and item.is_playable():
            self.engine.skip(seconds)
        else:
            self.step(1 if seconds > 0 else -1)

    def _change_speed(self, direction):
        speeds = transporting.SPEEDS
        current = self.engine.rate
        if direction > 0:
            nxt = next((s for s in speeds if s > current + 0.001), speeds[-1])
        else:
            nxt = next((s for s in reversed(speeds) if s < current - 0.001),
                       speeds[0])
        self._set_speed(nxt)

    def _set_speed(self, speed):
        self.config["speed"] = speed
        self.transport.speed.show_speed(speed)
        if self.engine.set_rate(speed):
            self._flash(f"{speed:g}x")

    def _nudge_volume(self, by):
        self._set_volume(max(0.0, min(1.0, self.config["volume"] + by)))

    def _set_volume(self, volume):
        self.config["volume"] = volume
        if volume > 0 and self.config["muted"]:
            self.config["muted"] = False
            self.engine.set_muted(False)
        self.engine.set_volume(volume)
        self.transport.volume.scale.set_value(volume)
        self.transport.volume.show_level(volume, self.config["muted"])
        self._flash(f"{round(volume * 100)}%")

    def _toggle_mute(self):
        self.config["muted"] = not self.config["muted"]
        self.engine.set_muted(self.config["muted"])
        self.transport.volume.show_level(self.config["volume"],
                                         self.config["muted"])
        self._flash("Muted" if self.config["muted"] else
                    f"{round(self.config['volume'] * 100)}%")

    def _cycle_loop(self):
        order = ("off", "one", "all")
        nxt = order[(order.index(self.config["loop"]) + 1) % len(order)]
        self.config["loop"] = nxt
        self.transport.loop.set_active(nxt != "off")
        self._flash({"off": "Repeat off", "one": "Repeat this one",
                     "all": "Repeat the folder"}[nxt])

    def _mark_loop(self, which):
        if which == "clear":
            self._loop_mark = None
            self.engine.set_loop_range(None, None)
            self.transport.set_loop_range(None)
            self._flash("Stretch cleared")
            return
        at = self.engine.position
        if which == "start":
            self._loop_mark = at
            self._flash(f"From {metadata.human_time(at)}")
            return
        if self._loop_mark is None:
            self._flash("Mark the start first  (Shift+[)")
            return
        start, stop = min(self._loop_mark, at), max(self._loop_mark, at)
        if stop - start < 0.4:
            self._flash("That stretch is too short")
            return
        self.engine.set_loop_range(start, stop)
        self.transport.set_loop_range((start, stop))
        self._flash(f"Repeating {metadata.human_time(start)} to "
                    f"{metadata.human_time(stop)}")

    def _toggle_subtitles(self):
        wanted = not self.engine.subtitles_enabled()
        self.config["subtitles"] = wanted
        self.engine.set_subtitles_enabled(wanted)
        self._flash("Subtitles on" if wanted else "Subtitles off")

    def _cycle_track(self, kind):
        tracks = self.engine.tracks_of(kind)
        if not tracks:
            self._flash(f"No {'subtitle' if kind == 'text' else kind} tracks")
            return
        current = self.engine.selected(kind)
        idents = [track.ident for track in tracks]
        index = (idents.index(current) + 1) % len(idents) if current in idents else 0
        self.engine.select_track(kind, idents[index])
        self._flash(tracks[index].label())

    def _shift_subtitles(self, by):
        self.engine.set_subtitle_offset(self.engine.subtitle_offset() + by)
        self._flash(f"Subtitles {self.engine.subtitle_offset():+.2f}s")

    def _shift_audio(self, by):
        self.engine.set_audio_offset(self.engine.audio_offset() + by)
        self._flash(f"Sound {self.engine.audio_offset():+.2f}s")

    def _tracks_popover(self, button):
        """Everything selectable about what is playing, in one menu."""
        box = widgets.column(spacing=2, margin_top=8, margin_bottom=8,
                             margin_start=8, margin_end=8)
        any_rows = False
        for kind, title in (("video", "Video"), ("audio", "Sound"),
                            ("text", "Subtitles")):
            tracks = self.engine.tracks_of(kind)
            if not tracks:
                continue
            any_rows = True
            box.append(widgets.label(title, "caption", xalign=0.0, margin_top=6))
            current = self.engine.selected(kind)
            for track in tracks:
                row = Gtk.Button(label=track.label())
                row.add_css_class("pill")
                row.get_child().set_xalign(0.0)
                if track.ident == current:
                    row.add_css_class("badge-on")
                row.set_detailed_action_name(
                    f"win.track('{kind}:{track.ident}')")
                box.append(row)
            if kind == "text":
                off = Gtk.Button(label="None")
                off.add_css_class("pill")
                off.get_child().set_xalign(0.0)
                off.connect("clicked",
                            lambda _b: self.engine.set_subtitles_enabled(False))
                box.append(off)

        item = self.reel.current()
        if item is not None:
            sidecars = media.sidecar_subtitles(item.path)
            if sidecars:
                any_rows = True
                box.append(widgets.label("Subtitle files", "caption", xalign=0.0,
                                         margin_top=6))
                for path in sidecars:
                    row = Gtk.Button(label=media.subtitle_label(item.path, path))
                    row.add_css_class("pill")
                    row.get_child().set_xalign(0.0)
                    row.set_detailed_action_name(
                        "win.subtitle-file('" + path.replace("'", "\\'") + "')")
                    box.append(row)
        if self.engine.chapters:
            any_rows = True
            box.append(widgets.label("Chapters", "caption", xalign=0.0,
                                     margin_top=6))
            scroller = Gtk.ScrolledWindow(
                hscrollbar_policy=Gtk.PolicyType.NEVER, max_content_height=240,
                propagate_natural_height=True)
            chapters = widgets.column(spacing=2)
            for chapter in self.engine.chapters:
                row = Gtk.Button(
                    label=f"{metadata.human_time(chapter.start)}   {chapter.title}")
                row.add_css_class("pill")
                row.get_child().set_xalign(0.0)
                row.set_detailed_action_name(f"win.chapter({chapter.start:f})")
                chapters.append(row)
            scroller.set_child(chapters)
            box.append(scroller)

        if not any_rows:
            box.append(widgets.label("Nothing to choose from in this file",
                                     "dim", xalign=0.0))
        popover = Gtk.Popover(child=box)
        popover.set_parent(button)
        popover.popup()

    def _remember_position(self):
        item = self.reel.current()
        if (item is None or not item.is_playable()
                or not self.config["resume_playback"]):
            return
        self.state.remember_position(
            item.path, self.engine.position, self.engine.duration,
            self.config["resume_threshold"], self.config["resume_tail"])

    # ---------------------------------------------------------------- chrome ----

    def _set_narrow(self, narrow: bool):
        """Reflow for a window too narrow for one row of transport controls."""
        self.transport.set_narrow(narrow)
        self.subtitle.set_visible(not narrow)

    def _on_motion(self, _controller, x, y):
        self.ambient.wake()
        self._show_chrome()
        self._restart_hide_timer()

    def _on_pointer_left(self):
        """The pointer went off the window: nothing here is being reached
        for, so the bars go now rather than after the usual wait."""
        if self._hide_timer:
            GLib.source_remove(self._hide_timer)
            self._hide_timer = 0
        if self._may_hide_chrome():
            self._hide_chrome()

    def _watch_hover(self, widget):
        """Note when the pointer rests on a bar, which is never idle."""
        motion = Gtk.EventControllerMotion.new()
        motion.connect("enter", lambda *_: self._hovered.add(widget))
        motion.connect("leave", lambda *_: self._hovered.discard(widget))
        widget.add_controller(motion)

    def _may_hide_chrome(self) -> bool:
        """Is this a moment when the bars can get out of the way?

        Whenever something is being looked at and nothing is being done with
        the bars — a still as much as a video. Not when there is nothing open
        (the bar is the only way in), not over a paused video (where you are
        in it is the point of stopping), not while a panel is open (a bar
        that vanishes mid-drag loses the drag), and not while the pointer is
        on a bar or its menu is open.
        """
        if not self.config["chrome_autohide"]:
            return False
        item = self.reel.current()
        if item is None:
            return False
        if (item.is_playable() and not self.engine.playing()
                and not self._slideshow):
            return False
        if self.editor_reveal.get_reveal_child() or self.info_reveal.get_reveal_child():
            return False
        if self._hovered or self.menu_button.get_active():
            return False
        return True

    def _restart_hide_timer(self):
        if self._hide_timer:
            GLib.source_remove(self._hide_timer)
            self._hide_timer = 0
        if not self._may_hide_chrome():
            return
        self._hide_timer = GLib.timeout_add(
            int(self.config["autohide_seconds"] * 1000), self._hide_chrome)

    def _hide_chrome(self):
        self._hide_timer = 0
        if not self._may_hide_chrome():
            return GLib.SOURCE_REMOVE
        self._hovered.clear()
        self._chrome_shown = False
        self._refresh_chrome()
        self.set_cursor(Gdk.Cursor.new_from_name("none", None))
        return GLib.SOURCE_REMOVE

    def _show_chrome(self):
        if self._chrome_shown:
            return
        self._chrome_shown = True
        self.set_cursor(None)
        self._refresh_chrome()

    def _refresh_chrome(self):
        item = self.reel.current()
        playable = item is not None and item.is_playable()
        shown = self._chrome_shown
        self.top_reveal.set_reveal_child(shown)
        self.transport_reveal.set_reveal_child(shown and playable)
        self.strip_reveal.set_reveal_child(
            shown and self.config["show_strip"] and len(self.reel) > 1)
        self._inset_stage()
        if not shown:
            self._hide_preview()

    def _set_strip(self, on):
        self.config["show_strip"] = on
        self.lookup_action("strip").set_state(GLib.Variant.new_boolean(on))
        self._refresh_chrome()

    def _set_editor(self, on):
        self.config["editor_open"] = on
        self.lookup_action("editor").set_state(GLib.Variant.new_boolean(on))
        self.editor_button.set_active(on)
        self.editor_reveal.set_reveal_child(on)
        self._inset_stage()
        if not on:
            self._set_compare(False)
            self._restart_hide_timer()
        else:
            self._show_chrome()

    def _set_info(self, on):
        self.config["show_info"] = on
        self.lookup_action("info").set_state(GLib.Variant.new_boolean(on))
        self.info_button.set_active(on)
        self.info_reveal.set_reveal_child(on)
        self._inset_stage()
        if on:
            self._refresh_info()
            self._show_chrome()
        else:
            self._restart_hide_timer()

    def _inset_stage(self):
        """Give the picture the room the panels are not using.

        Which chrome floats over the picture and which takes room from it is
        the one layout decision in this app, and it comes down to how long a
        thing stays on screen.

        The two bars float. They are thin, they are what every viewer and
        every player puts over the picture, and they get out of the way on
        their own while something is playing — so the picture is briefly
        covered at the edges and then is not.

        The strip and the side panels do not float. A strip of thumbnails is
        a hundred and thirty pixels tall and stays until you turn it off, and
        a photograph with the bottom fifth of it permanently behind a panel is
        not being shown to you. The same goes double for the editor: grading
        half a picture you cannot see is not grading.

        The fit recomputes from the new size on its own, so the picture
        shrinks to meet the panel rather than sliding under it.
        """
        editor_on = self.editor_reveal.get_reveal_child()
        info_on = self.info_reveal.get_reveal_child()
        strip_on = self.strip_reveal.get_reveal_child()
        self.stage.set_margin_end(self.editor.WIDTH + 20 if editor_on else 0)
        self.stage.set_margin_start(320 if info_on else 0)
        if strip_on and self.config["strip_at"] in ("start", "end"):
            room = int(self.config["strip_size"]) + 40
            if self.config["strip_at"] == "start" and not info_on:
                self.stage.set_margin_start(room)
            elif self.config["strip_at"] == "end" and not editor_on:
                self.stage.set_margin_end(room)
            self.stage.set_margin_bottom(0)
        else:
            self.stage.set_margin_bottom(
                int(self.config["strip_size"]) + 32 if strip_on else 0)
        self.stage.reset_view()

    def _set_compare(self, on):
        self._compare = 0.5 if on else None
        self.stage.set_compare(self._compare)
        self.editor.set_compare(on)

    def _flash(self, text: str):
        """The big transient readout in the middle of the window."""
        self.osd_label.set_text(text)
        self.osd_reveal.set_reveal_child(True)
        if self._osd_timer:
            GLib.source_remove(self._osd_timer)
        self._osd_timer = GLib.timeout_add(900, self._unflash)

    def _unflash(self):
        self._osd_timer = 0
        self.osd_reveal.set_reveal_child(False)
        return GLib.SOURCE_REMOVE

    # ---------------------------------------------------------------- titles ----

    def _refresh_title(self):
        item = self.reel.current()
        if item is None:
            return
        name = self.engine.title() if item.is_playable() else item.name
        self.title.set_text(name)
        self.set_title(f"{name} — The Eye")

        bits = []
        if len(self.reel) > 1:
            bits.append(self.reel.summary())
        if item.is_playable():
            if self.engine.video_size[0]:
                bits.append(f"{self.engine.video_size[0]}x"
                            f"{self.engine.video_size[1]}")
            elif item.is_audio():
                artist = self.engine.tags.get("artist")
                album = self.engine.tags.get("album")
                bits.append(" — ".join(str(v) for v in (artist, album) if v)
                            or media.describe(item.path))
            if self.engine.duration > 0:
                bits.append(metadata.human_time(self.engine.duration))
        elif self._still is not None and self._still.ok():
            bits.append(self._still.describe())
            scale = self.stage.scale()
            if scale and abs(scale - 1.0) > 0.01:
                bits.append(f"{scale * 100:.0f}%")
        touched = len(self.grade.touched())
        if touched:
            look = looks.matching(self.grade)
            bits.append(looks.by_key(look)["name"] if look
                        else f"{touched} adjustment" + ("s" if touched > 1 else ""))
        self.subtitle.set_text("   ·   ".join(bits))

    def _refresh_info(self):
        if not self.info_reveal.get_reveal_child():
            return
        item = self.reel.current()
        if item is None:
            return
        while True:
            child = self.info_list.get_first_child()
            if child is None:
                break
            self.info_list.remove(child)
        self.info_list.append(widgets.label("Reading…", "dim", xalign=0.0))
        path = item.path

        def work():
            return metadata.rows_for(path)

        def done(rows):
            if self.reel.current() is None or self.reel.current().path != path:
                return
            while True:
                child = self.info_list.get_first_child()
                if child is None:
                    break
                self.info_list.remove(child)
            self._info_rows = rows
            for label, value in rows:
                row = widgets.column(
                    widgets.label(label, "caption", xalign=0.0),
                    widgets.label(str(value), xalign=0.0, wrap=True,
                                  selectable=True),
                    spacing=0)
                row.add_css_class("info-row")
                self.info_list.append(row)

        jobs.run(work, done, lambda exc: widgets.error_toast(self, exc))

    def _copy_info(self):
        rows = getattr(self, "_info_rows", [])
        if not rows:
            return
        widgets.copy_text(self, "\n".join(f"{k}: {v}" for k, v in rows),
                          "Details copied")

    def _refresh_recent(self):
        self.recent_menu.remove_all()
        for folder in self.state.recent_folders()[:10]:
            item = Gio.MenuItem.new(os.path.basename(folder) or folder,
                                    None)
            item.set_action_and_target_value(
                "win.open-recent", GLib.Variant.new_string(folder))
            self.recent_menu.append_item(item)
        if self.lookup_action("open-recent") is None:
            action = Gio.SimpleAction.new("open-recent",
                                          GLib.VariantType.new("s"))
            action.connect("activate",
                           lambda _a, v: self.open_path(v.get_string()))
            self.add_action(action)

    # ------------------------------------------------------------- the view ----

    def _zoom(self, factor):
        self.stage.zoom_by(factor)
        self._flash(f"{self.stage.scale() * factor * 100:.0f}%")

    def _fit(self, mode):
        self.config["fit"] = mode
        self.stage.set_fit(mode)
        self._flash({"window": "Fit", "actual": "100%", "fill": "Fill"}
                    .get(mode, mode))

    def _toggle_fullscreen(self):
        if self.is_fullscreen():
            self.unfullscreen()
        else:
            self.fullscreen()
        self._restart_hide_timer()

    # ------------------------------------------------------------ slideshow ----

    def _toggle_slideshow(self):
        if self._slideshow:
            self._stop_slideshow()
            self._flash("Slideshow stopped")
        else:
            self._start_slideshow()

    def _start_slideshow(self):
        if len(self.reel) < 2:
            widgets.toast(self, "There is only one picture here")
            return
        if self.config["slideshow_shuffle"] and self.reel.order != "random":
            self.reel.resort("random")
            self.strip.set_items(self.reel.items, self.reel.index)
        self._slideshow = GLib.timeout_add(
            int(self.config["slideshow_seconds"] * 1000), self._slideshow_tick)
        self._flash("Slideshow")
        self._restart_hide_timer()

    def _stop_slideshow(self):
        if self._slideshow:
            GLib.source_remove(self._slideshow)
            self._slideshow = 0

    def _slideshow_tick(self):
        item = self.reel.current()
        # A video in a slideshow plays to its end rather than being cut off
        # after four seconds, which is the only sensible reading of a folder
        # with both in it.
        if item is not None and item.is_playable() and self.engine.playing():
            return GLib.SOURCE_CONTINUE
        if not self.reel.has_next() and not self.config["slideshow_loop"]:
            self._stop_slideshow()
            self._flash("That was the last one")
            return GLib.SOURCE_REMOVE
        self.step(1)
        return GLib.SOURCE_CONTINUE

    # ----------------------------------------------------------- the preview ----

    def _on_preview_wanted(self, _transport, seconds):
        item = self.reel.current()
        if item is None or not item.is_video() or self._preview_busy:
            return
        key = round(seconds)
        texture = self._preview_cache.get(key)
        if texture is not None:
            self._show_preview(texture, seconds)
            return
        path = item.path
        self._preview_busy = True

        def work():
            return thumbs.poster(path, 180, at=seconds)

        def done(texture):
            self._preview_busy = False
            if texture is None:
                return
            if len(self._preview_cache) > 60:
                self._preview_cache.clear()
            self._preview_cache[key] = texture
            self._show_preview(texture, seconds)

        def failed(_exc):
            self._preview_busy = False

        jobs.run(work, done, failed)

    def _show_preview(self, texture, seconds):
        if not self.transport_reveal.get_reveal_child():
            return
        self.preview.show_frame(texture, metadata.human_time(seconds,
                                                             long_form=True))
        # Positioned by hand along the bottom, to follow the pointer along the
        # seek bar. A popover would fight the pointer for the grab.
        x = self.transport.seek.hover_x()
        if x >= 0:
            ok, bar_x, _bar_y = self.transport.seek.translate_coordinates(
                self.root, 0, 0)
            left = (bar_x + x) if ok else x
            width = self.preview.get_width() or transporting.PREVIEW_WIDTH
            left = max(10, min(self.root.get_width() - width - 10,
                               left - width / 2))
            self.preview_reveal.set_margin_start(int(left))
        self.preview_reveal.set_reveal_child(True)

    def _hide_preview(self):
        self.preview_reveal.set_reveal_child(False)

    # ------------------------------------------------------------- the files ----

    def _choose_file(self):
        dialog = Gtk.FileDialog(title="Open a picture or a video")
        dialog.set_filters(self._filters())

        def chosen(_dialog, result):
            try:
                file = dialog.open_finish(result)
            except GLib.Error:
                return
            if file is not None and file.get_path():
                self.open_path(file.get_path())

        dialog.open(self, None, chosen)

    def _choose_folder(self):
        dialog = Gtk.FileDialog(title="Open a folder")

        def chosen(_dialog, result):
            try:
                file = dialog.select_folder_finish(result)
            except GLib.Error:
                return
            if file is not None and file.get_path():
                self.open_path(file.get_path())

        dialog.select_folder(self, None, chosen)

    def _filters(self):
        # What this machine can open, not what a well-equipped one could: a
        # filter that offers .exr where there is no loader for it is a filter
        # that leads to an error.
        types = media.claimed_types()
        store = Gio.ListStore.new(Gtk.FileFilter)
        everything = Gtk.FileFilter(name="Pictures and video")
        for extension in sorted(set().union(*types.values())):
            everything.add_suffix(extension.lstrip("."))
        store.append(everything)
        for name, extensions in (("Pictures", types["photo"] | types["raw"]),
                                 ("Video", types["video"]),
                                 ("Sound", types["audio"])):
            filter_ = Gtk.FileFilter(name=name)
            for extension in sorted(extensions):
                filter_.add_suffix(extension.lstrip("."))
            store.append(filter_)
        anything = Gtk.FileFilter(name="Every file")
        anything.add_pattern("*")
        store.append(anything)
        return store

    def _on_drop(self, _target, value, _x, _y):
        if isinstance(value, Gio.File) and value.get_path():
            self.open_path(value.get_path())
            return True
        return False

    def _copy_image(self):
        item = self.reel.current()
        if item is None:
            return
        texture = (self.engine.frame() if item.is_playable()
                   else (self._still.texture if self._still else None))
        if texture is None:
            widgets.toast(self, "There is nothing to copy yet")
            return
        Gdk.Display.get_default().get_clipboard().set(texture)
        widgets.toast(self, "Picture copied")

    def _copy_path(self):
        item = self.reel.current()
        if item is not None:
            widgets.copy_text(self, item.path, "Path copied")

    def _show_in_files(self):
        item = self.reel.current()
        if item is None:
            return
        launcher = Gtk.FileLauncher.new(Gio.File.new_for_path(item.path))
        launcher.open_containing_folder(self, None, None)

    def _trash(self):
        item = self.reel.current()
        if item is None:
            return

        def go():
            try:
                Gio.File.new_for_path(item.path).trash(None)
            except GLib.Error as exc:
                widgets.error_toast(self, exc)
                return
            self.thumbs.forget(item.path)
            self.state.forget_position(item.path)
            following = self.reel.remove(item.path)
            self.strip.set_items(self.reel.items, self.reel.index)
            if following is None:
                self._show_empty(os.path.dirname(item.path))
            else:
                self._load_current()
            widgets.toast(self, f"{item.name} moved to the wastebasket")

        widgets.confirm(self, "Move to the wastebasket?",
                        f"{item.name} can be put back from there.",
                        "Move", go)

    def _set_wallpaper(self):
        widgets.toast(self, "Setting the desktop background is the desktop's "
                            "own business — The Eye cannot do it under niri.")

    # ---------------------------------------------------------------- export ----

    def _export_dialog(self):
        item = self.reel.current()
        if item is None:
            return
        if item.is_playable():
            self._export_video_dialog(item)
        else:
            self._export_still_dialog(item)

    def _export_still_dialog(self, item):
        wanted = self.config["export_format"]
        extension = (media.extension(item.path) if wanted == "same"
                     else "." + wanted)
        if extension not in exporting.STILL_FORMATS:
            extension = ".png"
        dialog = Gtk.FileDialog(
            title="Export a copy",
            initial_name=os.path.basename(
                exporting.suggested_name(item.path, "edited", extension)))

        def chosen(_dialog, result):
            try:
                file = dialog.save_finish(result)
            except GLib.Error:
                return
            if file is None or not file.get_path():
                return
            destination = file.get_path()
            settings = self.grade.copy()
            quality = self.config["export_quality"]

            def work():
                return exporting.export_still(item.path, destination, settings,
                                              quality)

            def done(path):
                widgets.toast(self, f"Written to {os.path.basename(path)}",
                              timeout=5)

            widgets.toast(self, "Exporting…")
            jobs.run(work, done, lambda exc: widgets.error_toast(self, exc))

        dialog.save(self, None, chosen)

    def _export_video_dialog(self, item):
        settings = self.grade.copy()
        if settings.is_neutral():
            widgets.toast(self, "There is nothing to apply — adjust something "
                                "first, or use Save This Frame.", timeout=5)
            return
        body = exporting.estimate(item.path, settings)
        dialog = Adw.AlertDialog(
            heading="Export this video?",
            body=f"{body}\n\nEvery frame is drawn through the grade and the "
                 "video is encoded again, so this takes a while. The sound is "
                 "re-encoded too.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("go", "Choose a name…")
        dialog.set_response_appearance("go", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("go")

        def answered(dlg, result):
            if dlg.choose_finish(result) != "go":
                return
            self._pick_video_destination(item, settings)

        dialog.choose(self, None, answered)

    def _pick_video_destination(self, item, settings):
        extension = media.extension(item.path)
        if extension not in exporting.VIDEO_RECIPES:
            extension = exporting.DEFAULT_CONTAINER
        dialog = Gtk.FileDialog(
            title="Export the video",
            initial_name=os.path.basename(
                exporting.suggested_name(item.path, "graded", extension)))

        def chosen(_dialog, result):
            try:
                file = dialog.save_finish(result)
            except GLib.Error:
                return
            if file is None or not file.get_path():
                return
            self._start_video_export(item, settings, file.get_path())

        dialog.save(self, None, chosen)

    def _start_video_export(self, item, settings, destination):
        try:
            job = exporting.VideoExport(item.path, destination, settings,
                                        self.config["effect_quality"])
        except Exception as exc:                        # noqa: BLE001
            widgets.error_toast(self, exc)
            return
        self._export = job
        self.progress_bar.set_fraction(0.0)
        self.progress_label.set_text("Starting…")
        self.progress_reveal.set_reveal_child(True)
        job.connect("progress", self._on_export_progress)
        job.connect("finished", self._on_export_finished)
        job.connect("failed", self._on_export_failed)
        job.start()

    def _on_export_progress(self, _job, fraction, text):
        self.progress_bar.set_fraction(fraction)
        self.progress_label.set_text(text)

    def _on_export_finished(self, _job, path):
        self.progress_reveal.set_reveal_child(False)
        self._export = None
        widgets.toast(self, f"Written to {os.path.basename(path)}", timeout=6)

    def _on_export_failed(self, _job, message):
        self.progress_reveal.set_reveal_child(False)
        self._export = None
        widgets.toast(self, message, timeout=6)

    def _cancel_export(self):
        if self._export is not None:
            self._export.cancel()

    def _save_frame(self):
        item = self.reel.current()
        if item is None or not item.is_playable():
            widgets.toast(self, "That only applies to a video")
            return
        texture = self.engine.frame()
        if texture is None:
            widgets.toast(self, "There is no frame on screen yet")
            return
        at = metadata.human_time(self.engine.position, long_form=True)
        suffix = "frame " + at.replace(":", ".")
        destination = exporting.suggested_name(item.path, suffix, ".png")
        dialog = Gtk.FileDialog(title="Save this frame",
                                initial_name=os.path.basename(destination))
        settings = self.grade.copy()

        def chosen(_dialog, result):
            try:
                file = dialog.save_finish(result)
            except GLib.Error:
                return
            if file is None or not file.get_path():
                return
            try:
                exporting.export_frame(texture, file.get_path(), settings,
                                       self.config["export_quality"])
                widgets.toast(self, "Frame saved")
            except Exception as exc:                    # noqa: BLE001
                widgets.error_toast(self, exc)

        dialog.save(self, None, chosen)

    # ---------------------------------------------------------- preferences ----

    def _theme_key(self) -> str:
        """Which of the eight is in force.

        Not always the `theme` setting: with `follow_system` on the desktop
        picks which of the house pair it is, and `theme` is only what the
        cycle key and the preferences last chose for themselves.
        """
        if self.config["follow_system"]:
            if housestyle.system_is_dark():
                return self.config["dark_theme"]
            return self.config["light_theme"]
        return self.config["theme"]

    def _follow_system(self):
        """The desktop changed its mind, and we are following it."""
        if not self.config["follow_system"]:
            return
        self.scheme = theming.get(self._theme_key())
        self._apply_theme()

    def _next_theme(self):
        """The cycle key. Picking a theme by hand means not following the
        desktop any more: the next thing the cycle landed on would otherwise
        be undone the moment the desktop changed, which looks like the key
        not working."""
        order = [scheme.key for scheme in theming.ordered()]
        index = (order.index(self.scheme.key) + 1) % len(order)
        self.config["follow_system"] = False
        self.config["theme"] = order[index]
        self.config.save()
        self.scheme = theming.get(order[index])
        self._apply_theme()
        self._flash(self.scheme.name)

    def _apply_theme(self):
        widgets.install_css(self.scheme, self.config["translucency"])
        self.ambient.set_scheme(self.scheme)
        self.stage.set_scheme(self.scheme)
        self.strip.set_scheme(self.scheme)
        self.transport.set_scheme(self.scheme)
        self.preview.set_scheme(self.scheme)
        self._sound_face.set_scheme(self.scheme)
        for panel in (self.top_glass, self.transport_glass, self.strip_glass,
                      self.editor_glass, self.info_glass, self.osd_glass,
                      self.progress_glass):
            panel.set_scheme(self.scheme)
            panel.set_translucency(self.config["translucency"])
            panel.set_enabled(self.config["glass"])
            panel.set_blur(self.config["backdrop_blur"])

    def _apply_config(self):
        self.ambient.set_translucency(self.config["translucency"])
        self.ambient.set_motion(self.config["motion"])
        self.stage.set_config(self.config)
        self.strip.set_size(self.config["strip_size"])
        self.strip.set_vertical(self.config["strip_at"] in ("start", "end"))
        self.thumbs.size = self.config["strip_size"]
        self.transport.volume.scale.set_value(self.config["volume"])
        self.transport.volume.show_level(self.config["volume"],
                                         self.config["muted"])
        self.transport.speed.show_speed(self.config["speed"])
        self.transport.loop.set_active(self.config["loop"] != "off")
        self.strip_button.set_active(self.config["show_strip"])
        self._set_editor(self.config["editor_open"])
        self._set_info(self.config["show_info"])
        if self.config["fullscreen_on_open"]:
            self.fullscreen()
        if not hasattr(self, "_volume_wired"):
            self._volume_wired = True
            self.transport.volume.scale.connect(
                "value-changed",
                lambda s: self._set_volume_quiet(s.get_value()))

    def _set_volume_quiet(self, volume):
        """From the slider itself, so without the big readout."""
        if abs(volume - self.config["volume"]) < 0.001:
            return
        self.config["volume"] = volume
        self.engine.set_volume(volume)
        self.transport.volume.show_level(volume, self.config["muted"])

    def _preferences(self):
        from . import prefs

        prefs.Preferences(self).present(self)

    def _sort_dialog(self):
        box = widgets.column(spacing=4, margin_top=10, margin_bottom=10,
                             margin_start=10, margin_end=10)
        for key, label in (("name", "Name"), ("date", "Date"), ("size", "Size"),
                           ("kind", "Kind"), ("random", "Shuffled")):
            button = Gtk.Button(label=label)
            button.add_css_class("pill")
            button.get_child().set_xalign(0.0)
            if self.reel.order == key:
                button.add_css_class("badge-on")
            button.connect("clicked", lambda _b, k=key: self._set_sort(k))
            box.append(button)
        reverse = Gtk.CheckButton(label="Backwards")
        reverse.set_active(self.config["sort_descending"])
        reverse.connect("toggled", lambda b: self._set_sort(None, b.get_active()))
        box.append(reverse)
        popover = Gtk.Popover(child=box)
        popover.set_parent(self.strip_button)
        popover.popup()

    def _set_sort(self, order=None, descending=None):
        if order is not None:
            self.config["sort"] = order
        if descending is not None:
            self.config["sort_descending"] = descending
        self.reel.resort(order, descending)
        self.strip.set_items(self.reel.items, self.reel.index)
        self.strip.set_selected(self.reel.index, animate=False)
        self._refresh_title()

    def _shortcuts_window(self):
        from . import help as helping

        helping.ShortcutsWindow().present(self)

    def _about(self):
        from . import __version__

        about = Adw.AboutDialog(
            application_name="The Eye",
            application_icon="the-eye",
            version=__version__,
            developer_name="Robin",
            comments="A viewer for photographs and video, with a grading "
                     "editor built into it.",
            website="https://github.com/RobinCodes",
            license_type=Gtk.License.MIT_X11)
        about.add_credit_section("Built on", [
            "GTK 4 and libadwaita", "GStreamer", "GdkPixbuf"])
        about.present(self)

    # ----------------------------------------------------------- closing down ----

    def _on_close(self, *_args):
        self._remember_position()
        self._stop_slideshow()
        self._stop_animation()
        if self._export is not None and self._export.running():
            job = self._export

            def go():
                job.cancel()
                self.destroy()

            widgets.confirm(self, "Stop the export?",
                            "The partly written file will be removed.",
                            "Stop and close", go)
            return True
        self._save_geometry()
        self.config.save()
        self.state.save()
        self.engine.shutdown()
        return False

    def _save_geometry(self):
        self.geometry.save()
