"""One folder on screen, and everything that can be done to what is in it.

A pane owns a `DirModel`, the two views over it, its own history, and the
operations — copy, paste, rename, compress, delete. The window owns the
chrome around it and arranges one or two of them side by side.

The split of responsibility is: *the pane knows what is selected, the window
knows what a long job looks like.* So the pane builds the job and hands it to
`host.run_job`, which puts a progress strip on screen, and tells
`host.record` what happened so that Ctrl+Z can take it back. A pane never
shows a progress bar of its own, which is what lets two panes share one.

Three things in here are less obvious than they look:

*The clipboard.* Copying files between file managers works through
`x-special/gnome-copied-files`, a GNOME convention everything on Linux now
reads: the word `copy` or `cut`, then one URI per line. Writing both that and
a plain `text/uri-list` means a copy here pastes into Nautilus, Thunar and a
text editor, and theirs pastes in here.

*Searching happens in the view.* A search does not open a window of its own;
it replaces the listing with its results and the breadcrumb says so. Pressing
Escape puts the folder back. This is the difference between searching *in*
a file manager and being sent somewhere else by one.

*Opening a file goes through the portal.* `Gtk.FileLauncher` asks the desktop,
which is right on a sandboxed system and is also the one thing in this app
that must never run in a test — a test that opens a .png opens the user's
image viewer. `HERMIONE_NO_LAUNCH` turns it into a no-op for exactly that.
"""

from __future__ import annotations

import os
import subprocess
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk  # noqa: E402

from . import (archive, dialogs, ops, order, properties, search as searchmod,
               thumbs, units, vcs, winenv)
from .dirmodel import DirModel  # noqa: E402
from .preview import Preview  # noqa: E402
from .views import FileView  # noqa: E402

# The clipboard type every Linux file manager agrees on.
GNOME_COPY = "x-special/gnome-copied-files"

HISTORY_LIMIT = 80


class Pane(Gtk.Box):
    """One browsing pane."""

    __gsignals__ = {
        "path-changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "status-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "activated": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        # What HERMIONE_NO_LAUNCH turns a hand-over to another app into:
        # (which app, the path).
        "handed-on": (GObject.SignalFlags.RUN_FIRST, None, (str, str)),
    }

    def __init__(self, host, path: str = ""):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.host = host
        self.config = host.config
        self.path = ""
        self.back_stack: list[str] = []
        self.forward_stack: list[str] = []
        self.searching = False
        self.search_label = ""
        self._search: searchmod.Search | None = None
        self._sizing: threading.Event | None = None

        self.thumbs = thumbs.Thumbnailer(
            size=256, limit_mb=int(self.config["thumbnail_limit_mb"]))
        self.model = DirModel(self.config)
        self.view = FileView(self.model, self.config, self.thumbs)

        middle = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        middle.add_css_class("canvas")
        middle.append(self.view.stack)
        self.preview = Preview(self.config)
        self.preview.set_size_request(int(self.config["preview_width"]), -1)
        # Explicitly not expanding: a widget with no hexpand of its own
        # inherits it from any child that has one, and the preview is full of
        # labels and pictures that do. Without this the pane's list is
        # squeezed to its minimum and the preview takes everything else.
        self.preview.set_hexpand(False)
        self.preview.set_visible(bool(self.config["show_preview"]))
        middle.append(self.preview)
        middle.set_vexpand(True)

        self.status = _StatusLine()
        self.status.set_visible(bool(self.config["show_status"]))

        # An Adw.ToolbarView rather than a plain box, for the bar styling and
        # the reveal animations it brings. Its extend-content-to-edge mode is
        # deliberately not used: a GtkGridView *is* the scrollable, so it is
        # allocated the viewport and scrolls its children inside it — a
        # margin added to clear the bars cannot scroll away, and the tiles
        # never pass underneath. The glass goes where things really do float
        # instead: the palette, the dialogs, the collapsed sidebar.
        self.path_strip = self._build_path_strip()
        self.frame = Adw.ToolbarView()
        self.frame.add_css_class("plain-bars")
        self.frame.add_top_bar(self.path_strip)
        self.frame.add_top_bar(self._build_filter())
        self.frame.set_content(middle)
        self.frame.add_bottom_bar(self.status)
        self.frame.set_vexpand(True)
        self.append(self.frame)

        self.model.connect("loaded", self._on_loaded)
        self.model.connect("updated", lambda _m: self._refresh_status())
        self.view.connect("activated", self._on_activated)
        self.view.connect("context", self._on_context)
        self.view.connect("selection-changed", self._on_selection)
        self.view.connect("dropped", self._on_dropped)
        self.view.connect("rename-asked", lambda _v, entry: self.rename(entry))
        self.view.watch_sort(self._sort_changed)

        self._install_shortcuts()
        if path:
            self.navigate(path, remember=False)

    # ----------------------------------------------------------- chrome ---

    def _build_path_strip(self) -> Gtk.Widget:
        """A slim line of breadcrumbs, shown only when the tab is split.

        With one pane there is one toolbar, and it is the window's. With two
        there is still one toolbar — it drives whichever pane has the focus —
        but each pane has to say which folder it is showing, or a split view
        is two identical lists.
        """
        strip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        strip.add_css_class("path-strip")
        strip.set_visible(False)
        self.crumbs = Breadcrumbs(self.navigate, self._drop_on_folder)
        self.crumbs.set_hexpand(True)
        strip.append(self.crumbs)
        return strip

    def _build_filter(self) -> Gtk.Widget:
        self.filter_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                                  spacing=6)
        self.filter_bar.add_css_class("filter-bar")
        self.filter_bar.set_visible(False)

        self.filter_entry = Gtk.SearchEntry()
        self.filter_entry.add_css_class("search")
        self.filter_entry.set_hexpand(True)
        self.filter_entry.set_placeholder_text("Filter these names…")
        self.filter_entry.connect("search-changed", self._on_filter_changed)
        self.filter_entry.connect("activate", lambda _e: self.start_search())
        key = Gtk.EventControllerKey()
        key.connect("key-pressed", self._on_filter_key)
        self.filter_entry.add_controller(key)
        self.filter_bar.append(self.filter_entry)

        self.recursive_button = Gtk.ToggleButton(label="Everywhere below")
        self.recursive_button.add_css_class("pill-flat")
        self.recursive_button.set_tooltip_text(
            "Search every folder underneath, not just this one")
        self.recursive_button.connect("toggled", self._on_recursive_toggled)
        self.filter_bar.append(self.recursive_button)

        self.contents_button = Gtk.ToggleButton(label="In contents")
        self.contents_button.add_css_class("pill-flat")
        self.contents_button.set_tooltip_text("Search inside the files as well")
        self.contents_button.connect("toggled", self._on_recursive_toggled)
        self.filter_bar.append(self.contents_button)

        close = icon_button("window-close-symbolic", "Close  (Escape)")
        close.connect("clicked", lambda _b: self.close_filter())
        self.filter_bar.append(close)
        return self.filter_bar

    # ------------------------------------------------------- navigation ---

    def navigate(self, path: str, remember: bool = True) -> None:
        """Go to a folder. Everything else here eventually calls this."""
        path = os.path.abspath(os.path.expanduser(path))
        if not os.path.isdir(path):
            if os.path.exists(path):
                self.open_path(path)
                return
            self.host.toast(f"{os.path.basename(path)} is not there")
            return
        if self.searching:
            self._stop_search()
        if remember and self.path and self.path != path:
            self.back_stack.append(self.path)
            del self.back_stack[:-HISTORY_LIMIT]
            self.forward_stack.clear()

        self.path = path
        self.search_label = ""
        self._apply_folder_view(path)
        self.thumbs.drop_pending()
        self.model.load(path)
        self.crumbs.set_path(path)
        self.config.remember_folder(path)
        # The window's one toolbar follows whichever pane is active.
        self.emit("path-changed", path)

    def open_path(self, path: str) -> None:
        """A path that may be a file: open it, or go to its folder."""
        if os.path.isdir(path):
            self.navigate(path)
            return
        entry = self.model._read_one(path)
        if entry is not None:
            self.launch(entry)

    def reveal(self, path: str) -> None:
        """Show a path's folder and select it."""
        folder = os.path.dirname(os.path.abspath(path))
        if folder != self.path:
            self.navigate(folder)
            self._select_when_loaded = path
        else:
            self._select_now(path)

    def _select_now(self, path: str) -> None:
        position = self.model.select_paths([path])
        if position >= 0:
            self.view.scroll_to(position)

    def go_back(self) -> None:
        if not self.back_stack:
            return
        target = self.back_stack.pop()
        if self.path:
            self.forward_stack.append(self.path)
        self.navigate(target, remember=False)

    def go_forward(self) -> None:
        if not self.forward_stack:
            return
        target = self.forward_stack.pop()
        if self.path:
            self.back_stack.append(self.path)
        self.navigate(target, remember=False)

    def go_up(self) -> None:
        if not self.path:
            return
        parent = os.path.dirname(self.path.rstrip(os.sep))
        if parent and parent != self.path:
            # Coming up, the folder just left is what you want selected.
            leaving = self.path
            self.navigate(parent)
            self._select_now(leaving)

    def go_home(self) -> None:
        self.navigate(os.path.expanduser("~"))

    def refresh(self) -> None:
        if self.searching:
            self.start_search()
            return
        vcs.forget()
        selected = self.model.selected_paths()
        self.model.reload()
        self._select_when_loaded = selected[0] if selected else ""

    @property
    def can_go_up(self) -> bool:
        parent = os.path.dirname(self.path.rstrip(os.sep)) if self.path else ""
        return bool(parent) and parent != self.path

    def _apply_folder_view(self, path: str) -> None:
        """Restore how this folder was last looked at."""
        remembered = self.config.folder_view(path)
        mode = remembered.get("view_mode")
        if mode in ("list", "grid"):
            self.set_view_mode(mode, remember=False)

    # -- the path, typed --------------------------------------------------

    def edit_path(self) -> None:
        """Type a path. The entry lives in the window's toolbar."""
        self.host.edit_path()

    # --------------------------------------------------------- the views ---

    def set_view_mode(self, mode: str, remember: bool = True) -> None:
        self.view.set_mode(mode)
        self.emit("status-changed")
        if remember:
            self.config["view_mode"] = mode
            if self.path:
                self.config.set_folder_view(self.path, view_mode=mode)
            self.config.save()

    def _sort_changed(self, key: str, reverse: bool) -> None:
        if (self.config["sort_key"], self.config["sort_reverse"]) == (key, reverse):
            return
        self.config["sort_key"] = key
        self.config["sort_reverse"] = reverse
        self.config.save()

    def set_split(self, split: bool) -> None:
        """Show this pane's own breadcrumbs, because there are two of them."""
        self.path_strip.set_visible(split)
        if split:
            self.crumbs.set_path(self.path)

    def restyle(self) -> None:
        """Re-apply the settings that the views bake in."""
        self.thumbs.limit_mb = int(self.config["thumbnail_limit_mb"])
        self.view.apply_columns()
        self.view.rebuild_grid()
        self.model.refilter()
        self.model.resort()
        self.preview.set_visible(bool(self.config["show_preview"]))
        self.status.set_visible(bool(self.config["show_status"]))

    # ------------------------------------------------------- the listing ---

    def _on_loaded(self, _model, error: str) -> None:
        self._refresh_status(error)
        pending = getattr(self, "_select_when_loaded", "")
        if pending:
            self._select_when_loaded = ""
            self._select_now(pending)
        if self.config["folder_sizes"]:
            self.measure_folders()
        self.emit("status-changed")

    def _refresh_status(self, error: str = "") -> None:
        shown, total, folders, size = self.model.counts()
        selected = self.model.selected_entries()
        if error:
            self.status.set_error(error)
        elif selected:
            total_size = sum(e.size for e in selected if not e.is_dir)
            dirs = sum(1 for e in selected if e.is_dir)
            parts = [f"{len(selected)} selected"]
            if total_size or not dirs:
                parts.append(units.human_size(total_size))
            self.status.set_text("   ".join(parts), self._branch_text())
        else:
            parts = [units.human_items(shown - folders, folders)]
            if size:
                parts.append(units.human_size(size))
            if shown != total:
                parts.append(f"{total - shown} hidden by the filter")
            self.status.set_text("   ".join(parts), self._branch_text())

    def _branch_text(self) -> str:
        if not self.config["git_status"] or not self.path:
            return ""
        branch = vcs.branch(self.path)
        return f"git: {branch}" if branch else ""

    def _on_selection(self, _view) -> None:
        self._refresh_status()
        if self.preview.get_visible():
            selected = self.model.selected_entries()
            if len(selected) == 1:
                self.preview.show(selected[0])
            else:
                self.preview.show_nothing(len(selected))
        self.emit("status-changed")

    # ------------------------------------------------------- opening them ---

    def _on_activated(self, _view, entry) -> None:
        if getattr(entry, "new_tab", False):
            self.host.new_tab(entry.path)
            return
        if entry.is_dir:
            self.navigate(entry.path)
            return
        self.launch(entry)

    def launch(self, entry) -> None:
        """Hand a file to whatever opens it."""
        if entry.broken:
            self.host.toast(f"{entry.name} points at nothing")
            return
        # Unless the window is picking files for somebody else, in which
        # case activating one is the answer rather than a request to open
        # the image viewer. This is the whole of the difference between the
        # file manager and the file chooser, in the view.
        if self.host.picks_files:
            self.host.pick(entry.path)
            return
        # Markdown that nothing on the desktop claims goes to Notedown, from
        # the same checkout as this. A desktop that has an answer keeps it.
        if order.wants_notedown(entry.path):
            self.open_in_notedown(entry.path)
            return
        if os.environ.get("HERMIONE_NO_LAUNCH"):
            self.emit("activated", entry.path)
            return
        launcher = Gtk.FileLauncher.new(Gio.File.new_for_path(entry.path))
        launcher.set_always_ask(False)
        launcher.launch(self.get_root(), None, self._launched, entry)

    def _launched(self, launcher, task, entry) -> None:
        try:
            launcher.launch_finish(task)
        except GLib.Error as exc:
            if "dismissed" in (exc.message or "").lower():
                return
            self.host.toast(f"Could not open {entry.name}: {exc.message}")

    def open_with(self) -> None:
        paths = self.model.selected_paths()
        if not paths:
            return

        def picked(app, make_default):
            files = [Gio.File.new_for_path(path) for path in paths]
            try:
                app.launch(files, None)
            except GLib.Error as exc:
                self.host.toast(exc.message or "It would not start")
                return
            if make_default:
                ok, why = properties.set_default_app(paths[0], app)
                if not ok:
                    self.host.toast(why)

        dialogs.ask_open_with(self, paths[0], picked)

    def open_in_notedown(self, path: str = "") -> None:
        if not path:
            chosen = [p for p in self.model.selected_paths() if order.is_markdown(p)]
            if not chosen:
                return
            path = chosen[0]
        if os.environ.get("HERMIONE_NO_LAUNCH"):
            self.emit("handed-on", "notedown", path)
            return
        ok, why = order.open_in_notedown(path)
        if not ok:
            self.host.toast(why)

    def open_terminal(self) -> None:
        argv = winenv.terminal_argv(self.path, self.config["terminal"])
        if not argv:
            self.host.toast("No terminal found on this machine")
            return
        try:
            subprocess.Popen(argv, cwd=self.path,
                             creationflags=winenv.NO_WINDOW)
        except OSError as exc:
            self.host.toast(f"Could not start it: {exc.strerror}")

    # --------------------------------------------------------- clipboard ---

    def copy(self) -> None:
        self._to_clipboard("copy")

    def cut(self) -> None:
        self._to_clipboard("cut")

    def _to_clipboard(self, operation: str) -> None:
        paths = self.model.selected_paths()
        if not paths:
            return
        uris = [GLib.filename_to_uri(path, None) for path in paths]
        payload = (operation + "\n" + "\n".join(uris)).encode("utf-8")
        providers = [
            Gdk.ContentProvider.new_for_bytes(GNOME_COPY, GLib.Bytes.new(payload)),
            Gdk.ContentProvider.new_for_bytes("text/uri-list",
                                              GLib.Bytes.new(
                                                  ("\r\n".join(uris) + "\r\n")
                                                  .encode("utf-8"))),
            Gdk.ContentProvider.new_for_value("\n".join(paths)),
        ]
        clipboard = self.get_clipboard()
        clipboard.set_content(Gdk.ContentProvider.new_union(providers))
        what = units.human_items(sum(1 for p in paths if not os.path.isdir(p)),
                                 sum(1 for p in paths if os.path.isdir(p)))
        self.host.toast(f"{'Cut' if operation == 'cut' else 'Copied'} {what}")

    def paste(self) -> None:
        clipboard = self.get_clipboard()
        formats = clipboard.get_formats()
        if formats is not None and formats.contain_mime_type(GNOME_COPY):
            clipboard.read_async([GNOME_COPY], GLib.PRIORITY_DEFAULT, None,
                                 self._paste_gnome)
            return
        clipboard.read_value_async(Gdk.FileList, GLib.PRIORITY_DEFAULT, None,
                                   self._paste_files)

    def _paste_gnome(self, clipboard, task) -> None:
        """Read the clipboard's stream — asynchronously, which is not optional.

        Reading it with a blocking `read_bytes` on the GTK thread deadlocks
        whenever this process is also the one that *owns* the clipboard,
        which is exactly the ordinary case of copying and pasting inside the
        app: the other end of that pipe is fed from the main loop, and the
        main loop is sitting in the read. Splicing it into memory
        asynchronously lets the loop keep turning and feed it.
        """
        try:
            stream, _mime = clipboard.read_finish(task)
        except GLib.Error:
            clipboard.read_value_async(Gdk.FileList, GLib.PRIORITY_DEFAULT,
                                       None, self._paste_files)
            return
        sink = Gio.MemoryOutputStream.new_resizable()
        sink.splice_async(
            stream,
            Gio.OutputStreamSpliceFlags.CLOSE_SOURCE
            | Gio.OutputStreamSpliceFlags.CLOSE_TARGET,
            GLib.PRIORITY_DEFAULT, None, self._paste_read, None)

    def _paste_read(self, sink, task, _data) -> None:
        try:
            sink.splice_finish(task)
            payload = sink.steal_as_bytes().get_data() or b""
        except GLib.Error:
            return
        paths, move = parse_copied_files(payload)
        if paths:
            self._paste_paths(paths, move=move)

    def _paste_files(self, clipboard, task) -> None:
        try:
            value = clipboard.read_value_finish(task)
        except GLib.Error:
            self.host.toast("Nothing on the clipboard to paste")
            return
        if not isinstance(value, Gdk.FileList):
            return
        paths = [f.get_path() for f in value.get_files() if f.get_path()]
        if paths:
            self._paste_paths(paths, move=False)

    def _paste_paths(self, paths: list[str], move: bool) -> None:
        if not self.path:
            return
        if any(ops.is_inside(self.path, p) and os.path.isdir(p) for p in paths):
            self.host.toast("A folder cannot be pasted into itself")
            return
        kind = ops.MOVE if move else ops.COPY
        job = ops.Job(kind, paths, self.path,
                      resolver=dialogs.ConflictAsker(self))
        self.host.run_job(job, ("Moving" if move else "Copying")
                          + f" {len(paths)} item" + ("s" if len(paths) != 1 else ""),
                          on_finished=lambda result: self._after_paste(result, kind,
                                                                      paths))
        if move:
            self.get_clipboard().set_content(None)

    def _after_paste(self, result, kind: str, paths: list[str]) -> None:
        self.host.record(ops.Change(kind, paths, self.path, result))
        landed = [os.path.join(self.path, os.path.basename(p.rstrip(os.sep)))
                  for p in paths]
        self._select_soon([p for p in landed if os.path.lexists(p)])

    def _select_soon(self, paths: list[str]) -> None:
        """Select these once the monitor has noticed them."""
        if not paths:
            return

        def later():
            position = self.model.select_paths(paths)
            if position >= 0:
                self.view.scroll_to(position)
            return False

        GLib.timeout_add(320, later)

    # ------------------------------------------------------- making things ---

    def new_folder(self) -> None:
        if not self.path:
            return
        path, error = ops.make_folder(self.path)
        if error:
            self.host.toast(f"Could not create it: {error}")
            return
        self.host.record(ops.Change("create", [path]))
        self._select_soon([path])
        GLib.timeout_add(360, lambda: (self.rename_path(path), False)[1])

    def new_file(self) -> None:
        if not self.path:
            return

        def made(kind: str, name: str):
            if kind == "folder":
                path, error = ops.make_folder(self.path, name)
            else:
                path, error = ops.make_file(self.path, name)
            if error:
                self.host.toast(f"Could not create it: {error}")
                return
            self.host.record(ops.Change("create", [path]))
            self._select_soon([path])

        dialogs.ask_create(self, self.path, made)

    def duplicate(self) -> None:
        paths = self.model.selected_paths()
        if not paths:
            return
        job = ops.Job(ops.COPY, paths, self.path)
        self.host.run_job(job, f"Duplicating {len(paths)} item"
                          + ("s" if len(paths) != 1 else ""),
                          on_finished=lambda result: self.host.record(
                              ops.Change(ops.COPY, paths, self.path, result)))

    def make_links(self) -> None:
        paths = self.model.selected_paths()
        if not paths:
            return
        job = ops.Job(ops.LINK, paths, self.path)
        self.host.run_job(job, "Making links",
                          on_finished=lambda result: self.host.record(
                              ops.Change(ops.LINK, paths, self.path, result)))

    # ------------------------------------------------------------ renaming ---

    def rename(self, entry=None) -> None:
        selected = self.model.selected_entries()
        if entry is None:
            if len(selected) > 1:
                self.rename_many()
                return
            if not selected:
                return
            entry = selected[0]
        self.rename_path(entry.path)

    def rename_path(self, path: str) -> None:
        def renamed(name: str):
            ok, why = ops.rename(path, name)
            if not ok:
                self.host.toast(f"Could not rename it: {why}")
                return
            self.host.record(ops.Change(
                "rename", [path], rename_from=os.path.basename(path),
                rename_to=name))
            self._select_soon([os.path.join(os.path.dirname(path), name)])

        dialogs.ask_rename(self, path, renamed)

    def rename_many(self) -> None:
        paths = self.model.selected_paths()
        if len(paths) < 2:
            self.rename()
            return

        def apply(plan: list[tuple[str, str]]):
            done = 0
            failures = []
            for path, name in plan:
                ok, why = ops.rename(path, name)
                if ok:
                    done += 1
                else:
                    failures.append(f"{os.path.basename(path)}: {why}")
            if failures:
                dialogs.tell(self, "Some were not renamed",
                             "\n".join(failures[:10]))
            self.host.toast(f"Renamed {done} item" + ("s" if done != 1 else ""))
            self.refresh()

        dialogs.BatchRename(self, paths, apply).present()

    # ------------------------------------------------------------ removing ---

    def trash(self) -> None:
        paths = self.model.selected_paths()
        if not paths:
            return

        def go():
            job = ops.Job(ops.TRASH, paths)
            self.host.run_job(
                job, f"Moving {len(paths)} item"
                + ("s" if len(paths) != 1 else "") + " to the trash",
                on_finished=lambda result: self._after_trash(result, paths))

        if self.config["confirm_trash"] or self._is_many(paths):
            dialogs.confirm(
                self, f"Move {len(paths)} item"
                + ("s" if len(paths) != 1 else "") + " to the trash?",
                "They can be put back afterwards.", "Move to trash", go)
        else:
            go()

    def _after_trash(self, result, paths: list[str]) -> None:
        change = ops.Change(ops.TRASH, paths, result=result)
        self.host.record(change)
        if result.trashed:
            self.host.toast(
                f"{len(result.trashed)} moved to the trash", "Put back",
                lambda: self.host.undo())

    # ------------------------------------------------------- the second brain ---

    def send_to_inbox(self) -> None:
        """Move the selection into the brain's inbox — undoable, like any move."""
        where = order.here()
        if where is None:
            self.host.toast("There is no second brain on this machine")
            return
        inbox = str(where.inbox)
        paths = [p for p in self.model.selected_paths()
                 if os.path.normcase(os.path.dirname(os.path.abspath(p)))
                 != os.path.normcase(os.path.abspath(inbox))]
        if not paths:
            if self.model.selected_paths():
                self.host.toast("That is in the inbox already")
            return
        job = order.inbox_job(paths, where)
        destination = job.destination
        what = (os.path.basename(paths[0].rstrip("/\\")) if len(paths) == 1
                else f"{len(paths)} items")

        def finished(result) -> None:
            self.host.record(ops.Change(ops.MOVE, paths, destination, result))
            if not result.moved:
                return
            renamed = [os.path.basename(new) for old, new in result.moved
                       if os.path.basename(old) != os.path.basename(new)]
            note = f" as {renamed[0]}" if len(paths) == 1 and renamed else ""
            self.host.toast(f"{what} → {dialogs._short(destination)}{note}",
                            "Undo", lambda: self.host.undo())

        self.host.run_job(job, f"Sending {what} to the inbox", on_finished=finished)

    def ask_claude(self) -> None:
        self.host.ask_claude(self.model.selected_paths(), self.path)

    def delete_for_good(self) -> None:
        paths = self.model.selected_paths()
        if not paths:
            return

        def go():
            job = ops.Job(ops.DELETE, paths)
            self.host.run_job(job, f"Deleting {len(paths)} item"
                              + ("s" if len(paths) != 1 else ""))

        what = (os.path.basename(paths[0]) if len(paths) == 1
                else f"{len(paths)} items")
        dialogs.confirm(
            self, f"Delete {what} for good?",
            "This cannot be undone — they do not go to the trash.",
            "Delete", go)

    def _is_many(self, paths: list[str]) -> bool:
        limit = int(self.config["confirm_many"])
        return bool(limit) and len(paths) >= limit

    # ----------------------------------------------------------- archives ---

    def compress(self) -> None:
        paths = self.model.selected_paths()
        if not paths:
            return

        def chosen(name: str, fmt: str):
            job = archive.compress(paths, self.path, fmt, archive_name=name)
            self.host.run_job(
                job, f"Compressing {len(paths)} item"
                + ("s" if len(paths) != 1 else ""),
                on_finished=lambda result: (
                    self.host.record(ops.Change(ops.COPY, paths, self.path, result)),
                    self._select_soon(result.created[:1])))

        dialogs.ask_archive(self, paths, chosen)

    def extract(self) -> None:
        paths = [p for p in self.model.selected_paths() if archive.is_archive(p)]
        if not paths:
            self.host.toast("Nothing selected that can be extracted")
            return
        for path in paths:
            job = archive.extract(path, self.path,
                                  resolver=dialogs.ConflictAsker(self))
            self.host.run_job(
                job, f"Extracting {os.path.basename(path)}",
                on_finished=lambda result, p=path: (
                    self.host.record(ops.Change(ops.COPY, [p], self.path, result)),
                    self._select_soon(result.created[:1])))

    # ------------------------------------------------------------ selecting ---

    def select_all(self) -> None:
        self.model.selection.select_all()

    def select_none(self) -> None:
        self.model.selection.unselect_all()

    def invert_selection(self) -> None:
        model = self.model.selection
        for position in range(model.get_n_items()):
            if model.is_selected(position):
                model.unselect_item(position)
            else:
                model.select_item(position, False)

    def select_matching(self) -> None:
        """Select by pattern — the one thing a filter box cannot do."""
        dialog = dialogs._dialog("Select")
        body = dialogs._page(dialog, "Select the ones that match",
                             subtitle="A pattern like *.png or report-??.pdf")
        entry = Gtk.Entry(text="*")
        entry.add_css_class("search")
        entry.set_activates_default(True)
        body.append(entry)
        select = dialogs._button("Select", "suggested-action")
        cancel = dialogs._button("Cancel")
        cancel.connect("clicked", lambda _b: dialog.close())
        body.append(dialogs._buttons(cancel, select))

        def go(*_args):
            import fnmatch

            pattern = entry.get_text().strip().lower()
            dialog.close()
            if not pattern:
                return
            model = self.model.selection
            model.unselect_all()
            count = 0
            for position in range(model.get_n_items()):
                item = model.get_item(position)
                if item is not None and fnmatch.fnmatch(item.name.lower(), pattern):
                    model.select_item(position, False)
                    count += 1
            self.host.toast(f"{count} selected")

        entry.connect("activate", go)
        select.connect("clicked", go)
        GLib.idle_add(lambda: (entry.grab_focus(), False)[1])
        dialog.present(self)

    # ----------------------------------------------------------- filtering ---

    def toggle_filter(self) -> None:
        if self.filter_bar.get_visible():
            self.close_filter()
        else:
            self.filter_bar.set_visible(True)
            self.filter_entry.grab_focus()

    def close_filter(self) -> None:
        self.filter_bar.set_visible(False)
        self.filter_entry.set_text("")
        self.recursive_button.set_active(False)
        self.contents_button.set_active(False)
        if self.searching:
            self._stop_search()
            self.model.reload()
        else:
            self.model.refilter("")
        self.view.grab_focus()

    def _on_filter_changed(self, entry) -> None:
        text = entry.get_text()
        if self.recursive_button.get_active() or self.contents_button.get_active():
            if len(text) >= 2:
                self.start_search()
            return
        self.model.refilter(text)
        self._refresh_status()

    def _on_filter_key(self, _controller, keyval, _code, _state) -> bool:
        if keyval == Gdk.KEY_Escape:
            self.close_filter()
            return True
        if keyval == Gdk.KEY_Down:
            self.view.grab_focus()
            return True
        return False

    def _on_recursive_toggled(self, _button) -> None:
        if self.recursive_button.get_active() or self.contents_button.get_active():
            if self.contents_button.get_active():
                self.recursive_button.set_active(True)
            if len(self.filter_entry.get_text()) >= 2:
                self.start_search()
        elif self.searching:
            self._stop_search()
            self.model.reload()

    # ------------------------------------------------------------ searching ---

    def start_search(self) -> None:
        """Replace the listing with the results of a recursive search."""
        text = self.filter_entry.get_text().strip()
        if not text or not self.path:
            return
        self._stop_search()
        self.searching = True
        self.search_label = text
        self.model._generation += 1
        self.model.store.remove_all()
        self.model.needle = ""
        self.model.refilter("")
        self.crumbs.set_search(text, self.path)
        self.emit("path-changed", self.path)

        query = searchmod.Query(
            text,
            in_contents=self.contents_button.get_active(),
            include_hidden=bool(self.config["show_hidden"]),
            follow_links=bool(self.config["search_follow_links"]),
            content_limit_mb=int(self.config["search_content_max_mb"]))

        pending: list = []

        def on_hit(hit):
            pending.append(hit.entry)
            if len(pending) == 1 or len(pending) % 40 == 0:
                GLib.idle_add(drain)

        def drain() -> bool:
            if not self.searching:
                pending.clear()
                return False
            batch, pending[:] = list(pending), []
            if batch:
                self.model.store.splice(self.model.store.get_n_items(), 0, batch)
                self._refresh_status()
            return False

        def on_done(progress):
            GLib.idle_add(drain)
            GLib.idle_add(finished, progress)

        def finished(progress) -> bool:
            if not self.searching:
                return False
            count = self.model.store.get_n_items()
            where = "everything below" if not query.in_contents else "the contents"
            self.status.set_text(
                f"{count} found by searching {where}"
                + (" — stopped at the limit" if progress.truncated else ""),
                f"{progress.elapsed:.1f}s")
            return False

        self._search = searchmod.Search(self.path, query, on_hit=on_hit,
                                        on_done=on_done)
        self.status.set_text(f"Searching for “{text}”…", "")
        self._search.start()

    def _stop_search(self) -> None:
        if self._search is not None:
            self._search.cancel()
            self._search = None
        self.searching = False
        self.search_label = ""
        self.crumbs.set_path(self.path)
        self.emit("path-changed", self.path)

    # -------------------------------------------------------- folder sizes ---

    def measure_folders(self) -> None:
        """Count what is inside every folder on screen, on a thread.

        One thread for the lot rather than one per folder: the disk is the
        limit, and forty threads walking forty folders is forty times the
        seeking for the same answer.
        """
        if self._sizing is not None:
            self._sizing.set()
        stop = threading.Event()
        self._sizing = stop
        targets = []
        model = self.model.selection
        for position in range(model.get_n_items()):
            entry = model.get_item(position)
            if entry is not None and entry.is_dir and entry.dir_size is None:
                targets.append(entry)
        if not targets:
            return

        def worker():
            for entry in targets:
                if stop.is_set():
                    return
                total, files, folders = searchmod.folder_size(entry.path, stop)
                if stop.is_set():
                    return
                GLib.idle_add(deliver, entry, total, files, folders)

        def deliver(entry, total, files, folders) -> bool:
            entry.dir_size = total
            entry.dir_items = (files, folders)
            entry.touch()
            return False

        threading.Thread(target=worker, daemon=True,
                         name="hermione-folder-size").start()

    # ------------------------------------------------------------- the rest ---

    def show_properties(self) -> None:
        paths = self.model.selected_paths() or ([self.path] if self.path else [])
        if paths:
            properties.show(self, paths, self.config, self.thumbs,
                            on_open=self.open_path)

    def copy_location(self) -> None:
        dialogs._to_clipboard(self, self.path)
        self.host.toast("Location copied")

    def copy_paths(self) -> None:
        paths = self.model.selected_paths()
        if paths:
            dialogs._to_clipboard(self, "\n".join(paths))
            self.host.toast(f"{len(paths)} path"
                            + ("s" if len(paths) != 1 else "") + " copied")

    def toggle_hidden(self) -> None:
        self.config["show_hidden"] = not self.config["show_hidden"]
        self.config.save()
        self.model.reload()

    def toggle_preview(self) -> None:
        wanted = not self.preview.get_visible()
        self.preview.set_visible(wanted)
        self.config["show_preview"] = wanted
        self.config.save()
        if wanted:
            self._on_selection(None)

    def zoom(self, delta: int) -> None:
        size = max(48, min(192, int(self.config["icon_size"]) + delta))
        if size == self.config["icon_size"]:
            return
        self.config["icon_size"] = size
        self.config.save()
        if self.view.mode == "grid":
            self.view.rebuild_grid()

    # -- the context menu -------------------------------------------------

    def _on_context(self, _view, entry, x: float, y: float) -> None:
        menu = self._context_menu(entry)
        popover = Gtk.PopoverMenu.new_from_model(menu)
        popover.set_parent(self.view.widget)
        popover.set_has_arrow(False)
        popover.set_halign(Gtk.Align.START)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        popover.set_pointing_to(rect)
        popover.popup()

    def _context_menu(self, entry) -> Gio.Menu:
        menu = Gio.Menu()
        selected = self.model.selected_entries()

        if entry is None:
            section = Gio.Menu()
            section.append("New folder", "pane.new-folder")
            section.append("New file…", "pane.new-file")
            section.append("Paste", "pane.paste")
            menu.append_section(None, section)
            view = Gio.Menu()
            view.append("Select all", "pane.select-all")
            view.append("Select matching…", "pane.select-matching")
            view.append("Hidden files", "pane.show-hidden")
            menu.append_section(None, view)
            tools = Gio.Menu()
            tools.append("Open in terminal", "pane.terminal")
            if order.claude_available():
                tools.append("Ask Claude about this folder…", "pane.ask-claude")
            tools.append("Properties", "pane.properties")
            menu.append_section(None, tools)
            return menu

        opening = Gio.Menu()
        opening.append("Open", "pane.open")
        if entry.is_dir:
            opening.append("Open in new tab", "pane.open-tab")
            opening.append("Open in the other pane", "pane.open-other")
        else:
            opening.append("Open with…", "pane.open-with")
            if order.is_markdown(entry.path) and order.notedown_entry():
                opening.append("Open in Notedown", "pane.open-notedown")
        menu.append_section(None, opening)

        edit = Gio.Menu()
        edit.append("Cut", "pane.cut")
        edit.append("Copy", "pane.copy")
        if entry.is_dir:
            edit.append("Paste into it", "pane.paste-into")
        edit.append("Duplicate", "pane.duplicate")
        edit.append("Rename…" if len(selected) < 2 else "Rename them…",
                    "pane.rename")
        edit.append("Make a link", "pane.link")
        menu.append_section(None, edit)

        brainy = Gio.Menu()
        if order.here() is not None:
            brainy.append("Send to Inbox", "pane.send-to-inbox")
        if order.claude_available():
            brainy.append("Ask Claude about these…" if len(selected) > 1
                          else "Ask Claude about it…", "pane.ask-claude")
        if brainy.get_n_items():
            menu.append_section(None, brainy)

        removal = Gio.Menu()
        removal.append("Move to trash", "pane.trash")
        removal.append("Delete for good…", "pane.delete")
        menu.append_section(None, removal)

        extras = Gio.Menu()
        if any(archive.is_archive(e.path) for e in selected or [entry]):
            extras.append("Extract here", "pane.extract")
        extras.append("Compress…", "pane.compress")
        if entry.is_dir:
            extras.append("Open in terminal", "pane.terminal-here")
            extras.append("Bookmark it", "pane.bookmark-selected")
        extras.append("Copy path", "pane.copy-paths")
        if not entry.is_dir and len(selected) < 2:
            extras.append("Checksums…", "pane.checksums")
        menu.append_section(None, extras)

        menu.append_section(None, _one_item("Properties", "pane.properties"))
        return menu

    # -- the pane's own actions -------------------------------------------

    def _install_shortcuts(self) -> None:
        """The actions the menus name, in a group of the pane's own.

        A `Gio.SimpleActionGroup` on the pane rather than on the window, so
        that in a split view each pane's menu acts on that pane — the window
        would have to know which one was clicked, and it does not have to.
        """
        group = Gio.SimpleActionGroup()
        simple = {
            "open": lambda: self._open_selected(),
            "open-tab": lambda: self._open_selected(new_tab=True),
            "open-other": lambda: self.host.open_in_other_pane(
                self._first_selected_dir()),
            "open-with": self.open_with,
            "open-notedown": lambda: self.open_in_notedown(),
            "send-to-inbox": self.send_to_inbox,
            "ask-claude": self.ask_claude,
            "cut": self.cut,
            "copy": self.copy,
            "paste": self.paste,
            "paste-into": lambda: self._paste_into_selected(),
            "duplicate": self.duplicate,
            "link": self.make_links,
            "rename": self.rename,
            "trash": self.trash,
            "delete": self.delete_for_good,
            "compress": self.compress,
            "extract": self.extract,
            "properties": self.show_properties,
            "new-folder": self.new_folder,
            "new-file": self.new_file,
            "select-all": self.select_all,
            "select-matching": self.select_matching,
            "copy-paths": self.copy_paths,
            "copy-location": self.copy_location,
            "terminal": self.open_terminal,
            "terminal-here": lambda: self._terminal_in_selected(),
            "checksums": lambda: self._checksums_of_selected(),
            "bookmark": lambda: self.host.bookmark(self.path),
            "bookmark-selected": lambda: self.host.bookmark(
                self._first_selected_dir()),
            "duplicates": lambda: self.host.show_tool("duplicates", self.path),
            "largest": lambda: self.host.show_tool("largest", self.path),
            "empty-folders": lambda: self.host.show_tool("empty", self.path),
        }
        for name, callback in simple.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, cb=callback: cb())
            group.add_action(action)

        toggles = {
            "show-hidden": ("show_hidden", lambda: self.model.reload()),
            "show-preview": ("show_preview", lambda: self.toggle_preview_sync()),
            "folder-sizes": ("folder_sizes", lambda: self.measure_folders()),
            "sort-reverse": ("sort_reverse", lambda: self.view.apply_sort()),
            "folders-first": ("folders_first", lambda: self.model.resort()),
        }
        for name, (key, after) in toggles.items():
            action = Gio.SimpleAction.new_stateful(
                name, None, GLib.Variant.new_boolean(bool(self.config[key])))
            action.connect("activate", self._toggle_setting, key, after)
            group.add_action(action)

        sort = Gio.SimpleAction.new_stateful(
            "sort", GLib.VariantType.new("s"),
            GLib.Variant.new_string(str(self.config["sort_key"])))
        sort.connect("activate", self._sort_action)
        group.add_action(sort)

        self.insert_action_group("pane", group)
        self.actions = group

    def _toggle_setting(self, action, _parameter, key: str, after) -> None:
        value = not action.get_state().get_boolean()
        action.set_state(GLib.Variant.new_boolean(value))
        self.config[key] = value
        self.config.save()
        after()

    def toggle_preview_sync(self) -> None:
        self.preview.set_visible(bool(self.config["show_preview"]))
        if self.preview.get_visible():
            self._on_selection(None)

    def _sort_action(self, action, parameter) -> None:
        key = parameter.get_string()
        action.set_state(parameter)
        self.config["sort_key"] = key
        self.config.save()
        self.view.apply_sort()

    # -- the small helpers the actions use --------------------------------

    def _open_selected(self, new_tab: bool = False) -> None:
        for entry in self.model.selected_entries()[:12]:
            if new_tab and entry.is_dir:
                self.host.new_tab(entry.path)
            else:
                self._on_activated(None, entry)
                if entry.is_dir:
                    break

    def _first_selected_dir(self) -> str:
        for entry in self.model.selected_entries():
            if entry.is_dir:
                return entry.path
        return self.path

    def _paste_into_selected(self) -> None:
        target = self._first_selected_dir()
        if target == self.path:
            self.paste()
            return
        # Paste into a folder that is not the one being shown: the simplest
        # honest way is to hand the job the other destination.
        here = self.path
        self.path = target
        try:
            self.paste()
        finally:
            self.path = here

    def _terminal_in_selected(self) -> None:
        folder = self._first_selected_dir()
        argv = winenv.terminal_argv(folder, self.config["terminal"])
        if not argv:
            self.host.toast("No terminal found on this machine")
            return
        try:
            subprocess.Popen(argv, cwd=folder, creationflags=winenv.NO_WINDOW)
        except OSError as exc:
            self.host.toast(f"Could not start it: {exc.strerror}")

    def _checksums_of_selected(self) -> None:
        paths = self.model.selected_paths()
        if paths:
            dialogs.show_checksums(self, paths[0])

    # -- drops ------------------------------------------------------------

    def _on_dropped(self, _view, paths, _is_move, target_entry) -> None:
        destination = (target_entry.path if target_entry is not None
                       else self.path)
        self._drop_paths(paths, destination)

    def _drop_on_folder(self, paths: list[str], destination: str) -> None:
        self._drop_paths(paths, destination)

    def _drop_paths(self, paths: list[str], destination: str) -> None:
        if not destination or not os.path.isdir(destination):
            return
        paths = [p for p in paths if p and os.path.abspath(p) != destination]
        if not paths:
            return
        # Same filesystem means the gesture was almost certainly "move it
        # there"; a different one means a copy, which is what every other
        # file manager does and what stops a drag from a USB stick emptying it.
        move = _same_device(paths[0], destination)

        def go(as_move: bool):
            kind = ops.MOVE if as_move else ops.COPY
            job = ops.Job(kind, paths, destination,
                          resolver=dialogs.ConflictAsker(self))
            self.host.run_job(
                job, ("Moving" if as_move else "Copying")
                + f" {len(paths)} item" + ("s" if len(paths) != 1 else ""),
                on_finished=lambda result: self.host.record(
                    ops.Change(kind, paths, destination, result)))

        go(move)


def parse_copied_files(payload: bytes) -> tuple[list[str], bool]:
    """An `x-special/gnome-copied-files` clipboard payload, as (paths, is a cut).

    The format every Linux file manager writes: the word `copy` or `cut`,
    then one URI per line. A URI that is not a local file is dropped rather
    than guessed at — a copy from a remote share is a real thing to put on
    the clipboard and not a thing this can paste.
    """
    lines = payload.decode("utf-8", "replace").splitlines()
    if not lines:
        return [], False
    move = lines[0].strip() == "cut"
    paths = []
    for line in lines[1:]:
        line = line.strip()
        if not line:
            continue
        try:
            path = GLib.filename_from_uri(line)[0]
        except GLib.Error:
            continue
        if path:
            # In Python's spelling, not GLib's: on Windows the two disagree
            # about the separator, and a path is compared as a string here.
            paths.append(os.path.normpath(path))
    return paths, move


def _same_device(path: str, other: str) -> bool:
    try:
        return os.stat(path).st_dev == os.stat(other).st_dev
    except OSError:
        return False


def _one_item(label: str, action: str) -> Gio.Menu:
    menu = Gio.Menu()
    menu.append(label, action)
    return menu


def icon_button(icon: str, tooltip: str) -> Gtk.Button:
    button = Gtk.Button.new_from_icon_name(icon)
    button.add_css_class("pill-flat")
    button.set_tooltip_text(tooltip)
    return button


class _StatusLine(Gtk.Box):
    """The one line under the list: what is here, or what went wrong."""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.add_css_class("statusbar")
        self.left = Gtk.Label(xalign=0.0)
        self.left.set_ellipsize(3)
        self.left.set_hexpand(True)
        self.right = Gtk.Label(xalign=1.0)
        self.right.add_css_class("numeric")
        self.append(self.left)
        self.append(self.right)

    def set_text(self, left: str, right: str = "") -> None:
        self.remove_css_class("warning")
        self.left.remove_css_class("is-broken")
        self.left.set_label(left)
        self.right.set_label(right)

    def set_error(self, message: str) -> None:
        self.left.set_label(message)
        self.left.add_css_class("is-broken")
        self.right.set_label("")


class Breadcrumbs(Gtk.ScrolledWindow):
    """The path, as buttons. Each one is also a drop target.

    Dropping a file on a crumb moves it to that folder, which is the quickest
    way there is of putting something three levels up.
    """

    def __init__(self, on_go, on_drop):
        super().__init__()
        self.on_go = on_go
        self.on_drop = on_drop
        self.set_policy(Gtk.PolicyType.EXTERNAL, Gtk.PolicyType.NEVER)
        self.set_hexpand(True)
        self.box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.box.add_css_class("crumbs")
        self.set_child(self.box)
        self._showing = None

    def set_path(self, path: str) -> None:
        # The window resyncs its header on every focus change, and pressing a
        # crumb focuses it: rebuilding then would destroy the button between
        # press and release, and the click would never land.
        if self._showing == ("path", path):
            return
        self._showing = ("path", path)
        self._clear()
        self._scroll_to_end()
        parts = _split_path(path)
        for index, (label, full) in enumerate(parts):
            if index:
                arrow = Gtk.Label(label="›")
                arrow.add_css_class("crumb-sep")
                self.box.append(arrow)
            button = Gtk.Button(label=_shorten(label))
            button.add_css_class("crumb")
            if index == len(parts) - 1:
                button.add_css_class("last")
            button.set_tooltip_text(full)
            button.connect("clicked", lambda _b, where=full: self.on_go(where))
            self._make_target(button, full)
            self.box.append(button)

    def set_search(self, text: str, where: str) -> None:
        if self._showing == ("search", text, where):
            return
        self._showing = ("search", text, where)
        self._clear()
        label = Gtk.Button(label=f"Searching {os.path.basename(where) or where}"
                                 f" for “{text}”")
        label.add_css_class("crumb")
        label.add_css_class("last")
        label.connect("clicked", lambda _b: self.on_go(where))
        self.box.append(label)

    def _make_target(self, button: Gtk.Widget, path: str) -> None:
        target = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.MOVE
                                    | Gdk.DragAction.COPY)
        target.connect("enter", lambda *_a: (button.add_css_class("drop-into"),
                                             Gdk.DragAction.MOVE)[1])
        target.connect("leave", lambda *_a: button.remove_css_class("drop-into"))

        def dropped(_target, value, _x, _y):
            button.remove_css_class("drop-into")
            if not isinstance(value, Gdk.FileList):
                return False
            files = [f.get_path() for f in value.get_files() if f.get_path()]
            if files:
                self.on_drop(files, path)
            return True

        target.connect("drop", dropped)
        button.add_controller(target)

    def _scroll_to_end(self) -> None:
        """Show the end of the trail, which is the folder you are in.

        After the next layout, because the width of what was just built is
        not known until then.
        """
        def later() -> bool:
            adjustment = self.get_hadjustment()
            if adjustment is not None:
                adjustment.set_value(adjustment.get_upper()
                                     - adjustment.get_page_size())
            return False

        GLib.idle_add(later, priority=GLib.PRIORITY_LOW)

    def _clear(self) -> None:
        while (child := self.box.get_first_child()) is not None:
            self.box.remove(child)


def _shorten(label: str, limit: int = 24) -> str:
    """A single path component, cut in the middle if it is absurdly long.

    Cut here rather than by ellipsizing the label, because an ellipsizing
    label can shrink to one character — and a trail of them collapses to
    `… › … › …` the moment the window is narrow, which is every window in a
    tiling column. With fixed text the trail keeps its names and the scroller
    around it shows the end, which is the folder you are actually in.
    """
    if len(label) <= limit:
        return label
    half = (limit - 1) // 2
    return label[:half] + "…" + label[-half:]


def _split_path(path: str) -> list[tuple[str, str]]:
    """A path as (label, full path) pairs, with the home folder as `Home`."""
    path = os.path.abspath(path)
    home = os.path.expanduser("~")
    parts: list[tuple[str, str]] = []
    if path == home or path.startswith(home + os.sep):
        parts.append(("Home", home))
        rest = path[len(home):].strip(os.sep)
        walker = home
    else:
        root = os.path.splitdrive(path)[0] + os.sep if os.name == "nt" else os.sep
        parts.append((os.path.splitdrive(path)[0] or "/", root))
        rest = path[len(root):].strip(os.sep)
        walker = root
    if rest:
        for piece in rest.split(os.sep):
            walker = os.path.join(walker, piece)
            parts.append((piece, walker))
    return parts
