"""The list of files behind a view, kept live.

One of these per browsing pane. It owns the `Gio.ListStore` of entries and the
chain of models over it, and it is the only thing in the app that knows how a
directory becomes rows:

    ListStore  ->  FilterListModel  ->  SortListModel  ->  MultiSelection
    (what is      (hidden files,       (folders first,    (what the views
     on disk)      the filter box)      then the column)   share)

Both views — the detail list and the icon grid — sit on the *same* selection
model, which is why switching between them keeps the selection and the sort
order instead of starting again.

Loading happens on a worker thread and arrives in batches, so a folder of
fifty thousand files starts drawing immediately. A `Gio.FileMonitor` then
keeps it current: a file written by something else appears, one deleted
elsewhere goes, and neither rebuilds the list — a rebuild would lose the
selection and jump the scroll position, which is what makes other file
managers unusable while something is unpacking in the background.
"""

from __future__ import annotations

import os
import threading

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gio, GLib, GObject, Gtk  # noqa: E402

from . import entries as entrymod  # noqa: E402
from . import vcs  # noqa: E402

# How long to wait after a monitor event before acting on it. Unpacking an
# archive fires thousands; coalescing them into one refresh per quarter
# second is the difference between a live listing and a stutter.
SETTLE_MS = 220


class DirModel(GObject.Object):
    """What is in one folder, as models the views can use."""

    __gsignals__ = {
        # The folder finished loading. The argument is an error message, or "".
        "loaded": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        # The contents changed under us — a count worth putting in the status bar.
        "updated": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.path = ""
        self.error = ""
        self.loading = False

        self.store = Gio.ListStore.new(entrymod.Entry)
        self.filter = Gtk.CustomFilter.new(self._visible)
        self.filtered = Gtk.FilterListModel.new(self.store, self.filter)
        # The sorter is handed in later by whoever owns the column view, so
        # that clicking a column header sorts both views.
        self.folders_first = Gtk.CustomSorter.new(self._folders_first)
        self.sorter = Gtk.MultiSorter()
        self.sorter.append(self.folders_first)
        self.sorted = Gtk.SortListModel.new(self.filtered, self.sorter)
        self.selection = Gtk.MultiSelection.new(self.sorted)

        self.needle = ""
        # A chooser narrows the listing to what the asking program said it
        # would take. None means "everything", which is every other use.
        self.allow = None
        self._generation = 0
        self._monitor: Gio.FileMonitor | None = None
        self._settle: int = 0
        self._dirty: set[str] = set()
        self.vcs_marks: dict[str, str] = {}

    # -- the chain's two rules --------------------------------------------

    def _visible(self, entry) -> bool:
        if entry.hidden and not self.config["show_hidden"]:
            return False
        if self.allow is not None and not self.allow(entry):
            return False
        return entrymod.matches(entry, self.needle)

    def _folders_first(self, left, right, _data=None) -> int:
        if not self.config["folders_first"]:
            return 0
        if left.is_dir == right.is_dir:
            return 0
        return -1 if left.is_dir else 1

    def add_column_sorter(self, sorter: Gtk.Sorter) -> None:
        """Put the column view's sorter after the folders-first rule."""
        if sorter is not None:
            self.sorter.append(sorter)

    def set_allow(self, allow) -> None:
        """Narrow the listing to the entries `allow` says yes to.

        Separate from the search needle rather than folded into it: the
        needle is the user's and changes as they type, this is the asking
        program's and holds for the life of the dialog. Folding them
        together means a search clears the file type filter.
        """
        self.allow = allow
        self.refilter()

    def refilter(self, needle: str | None = None) -> None:
        if needle is not None:
            self.needle = needle
        self.filter.changed(Gtk.FilterChange.DIFFERENT)
        self.emit("updated")

    def resort(self) -> None:
        self.folders_first.changed(Gtk.SorterChange.DIFFERENT)

    # -- loading ----------------------------------------------------------

    def load(self, path: str) -> None:
        """Start reading `path`. Old work for another folder is abandoned."""
        self.path = os.path.abspath(path)
        self.error = ""
        self.loading = True
        self._generation += 1
        generation = self._generation
        self.store.remove_all()
        self._stop_monitor()

        show_hidden = bool(self.config["show_hidden"])

        def worker():
            batches: list[list] = []

            def on_batch(batch):
                # Copied: the caller reuses its list for the next batch.
                batches.append(list(batch))
                GLib.idle_add(self._take_batch, generation, batches,
                              priority=GLib.PRIORITY_DEFAULT_IDLE)

            found, error = entrymod.read_dir(self.path, show_hidden,
                                             on_batch=on_batch)
            GLib.idle_add(self._finish, generation, error, len(found))

        threading.Thread(target=worker, daemon=True,
                         name="hermione-read").start()

    def _take_batch(self, generation: int, batches: list) -> bool:
        if generation != self._generation:
            batches.clear()
            return False
        while batches:
            batch = batches.pop(0)
            self.store.splice(self.store.get_n_items(), 0, batch)
        return False

    def _finish(self, generation: int, error: str, _count: int) -> bool:
        if generation != self._generation:
            return False
        self.loading = False
        self.error = error
        self._start_monitor()
        self.emit("loaded", error)
        if self.config["git_status"]:
            self._load_vcs(generation)
        return False

    def reload(self) -> None:
        if self.path:
            self.load(self.path)

    # -- git --------------------------------------------------------------

    def _load_vcs(self, generation: int) -> None:
        """Ask git about the folder, on a thread, and mark the rows.

        A repository with a hundred thousand files takes git a moment, and a
        folder that is not in a repository costs nothing to ask about because
        `repo_root` answers from `stat` alone.
        """
        path = self.path

        def worker():
            marks = vcs.status(path)
            GLib.idle_add(self._apply_vcs, generation, marks)

        if vcs.repo_root(path) is None:
            self.vcs_marks = {}
            return
        threading.Thread(target=worker, daemon=True,
                         name="hermione-git").start()

    def _apply_vcs(self, generation: int, marks: dict) -> bool:
        if generation != self._generation:
            return False
        self.vcs_marks = marks
        for index in range(self.store.get_n_items()):
            entry = self.store.get_item(index)
            mark = marks.get(entry.path, "")
            if mark != entry.vcs:
                entry.vcs = mark
                entry.touch()
        return False

    # -- watching ---------------------------------------------------------

    def _start_monitor(self) -> None:
        if not self.path or not os.path.isdir(self.path):
            return
        try:
            folder = Gio.File.new_for_path(self.path)
            self._monitor = folder.monitor_directory(
                Gio.FileMonitorFlags.WATCH_MOVES, None)
        except GLib.Error:
            self._monitor = None
            return
        self._monitor.set_rate_limit(200)
        self._monitor.connect("changed", self._on_change)

    def _stop_monitor(self) -> None:
        if self._monitor is not None:
            self._monitor.cancel()
            self._monitor = None
        if self._settle:
            GLib.source_remove(self._settle)
            self._settle = 0
        self._dirty.clear()

    def _on_change(self, _monitor, file, other, event) -> None:
        path = file.get_path() if file is not None else ""
        if not path:
            return
        kinds = Gio.FileMonitorEvent
        if event in (kinds.CREATED, kinds.MOVED_IN):
            self._dirty.add(path)
        elif event in (kinds.DELETED, kinds.MOVED_OUT):
            self._dirty.add(path)
        elif event in (kinds.CHANGES_DONE_HINT, kinds.ATTRIBUTE_CHANGED,
                       kinds.CHANGED):
            self._dirty.add(path)
        elif event == kinds.RENAMED:
            self._dirty.add(path)
            if other is not None and other.get_path():
                self._dirty.add(other.get_path())
        elif event == kinds.UNMOUNTED:
            self.emit("loaded", "The disk was removed")
            return
        else:
            return
        if not self._settle:
            self._settle = GLib.timeout_add(SETTLE_MS, self._settle_now)

    def _settle_now(self) -> bool:
        self._settle = 0
        paths = self._dirty
        self._dirty = set()
        if not paths:
            return False

        # Our own folder disappearing is not an update, it is an exit.
        if not os.path.isdir(self.path):
            self.emit("loaded", "This folder is gone")
            return False

        index_by_path = {}
        for index in range(self.store.get_n_items()):
            index_by_path[self.store.get_item(index).path] = index

        removals = []
        additions = []
        for path in paths:
            if os.path.dirname(path) != self.path:
                continue
            exists = os.path.lexists(path)
            if path in index_by_path:
                if not exists:
                    removals.append(index_by_path[path])
                else:
                    self._refresh_one(index_by_path[path], path)
            elif exists:
                fresh = self._read_one(path)
                if fresh is not None and self._listable(fresh):
                    additions.append(fresh)

        for index in sorted(removals, reverse=True):
            self.store.remove(index)
        for entry in additions:
            self.store.append(entry)
        if removals or additions:
            self.emit("updated")
        return False

    def _listable(self, entry) -> bool:
        """Would this entry have been in the listing when it was read?"""
        return bool(self.config["show_hidden"]) or not entry.hidden

    def _read_one(self, path: str):
        name = os.path.basename(path)
        try:
            link_info = os.lstat(path)
        except OSError:
            return None
        is_link = os.path.islink(path)
        info = link_info
        if is_link:
            try:
                info = os.stat(path)
            except OSError:
                info = None
        entry = entrymod.from_stat(path, name, info,
                                  link_info if is_link else None)
        if is_link:
            entry.link_target = os.readlink(path) if os.path.islink(path) else ""
        entry.vcs = self.vcs_marks.get(path, "")
        return entry

    def _refresh_one(self, index: int, path: str) -> None:
        """Update one row in place, keeping the object the views are bound to."""
        fresh = self._read_one(path)
        if fresh is None:
            return
        entry = self.store.get_item(index)
        for field in ("size", "mtime", "atime", "ctime", "mode", "uid", "gid",
                      "nlink", "is_dir", "is_link", "broken", "link_target",
                      "hidden", "category", "blocks"):
            setattr(entry, field, getattr(fresh, field))
        # A file rewritten needs a new thumbnail, not the old one.
        entry.thumb = None
        entry.thumb_tried = False
        entry.dir_size = None
        entry.dir_items = None
        entry.touch()

    # -- what the pane asks ------------------------------------------------

    def entry_at(self, position: int):
        return self.selection.get_item(position)

    def selected_entries(self) -> list:
        model = self.selection
        out = []
        bitset = model.get_selection()
        index = 0
        size = bitset.get_size()
        while index < size:
            position = bitset.get_nth(index)
            item = model.get_item(position)
            if item is not None:
                out.append(item)
            index += 1
        return out

    def selected_paths(self) -> list[str]:
        return [entry.path for entry in self.selected_entries()]

    def select_paths(self, paths) -> int:
        """Select these, and return the position of the first one found.

        Used after a rename, a paste or a drop: the thing that just happened
        should be what is selected, and visible.
        """
        wanted = set(paths)
        if not wanted:
            return -1
        first = -1
        self.selection.unselect_all()
        for position in range(self.selection.get_n_items()):
            entry = self.selection.get_item(position)
            if entry is not None and entry.path in wanted:
                self.selection.select_item(position, False)
                if first < 0:
                    first = position
        return first

    def position_of(self, path: str) -> int:
        for position in range(self.selection.get_n_items()):
            entry = self.selection.get_item(position)
            if entry is not None and entry.path == path:
                return position
        return -1

    def counts(self) -> tuple[int, int, int, int]:
        """(shown, total, folders shown, bytes of the files shown)."""
        shown = self.selection.get_n_items()
        folders = 0
        total_bytes = 0
        for position in range(shown):
            entry = self.selection.get_item(position)
            if entry is None:
                continue
            if entry.is_dir:
                folders += 1
            else:
                total_bytes += entry.size
        return shown, self.store.get_n_items(), folders, total_bytes

    def shutdown(self) -> None:
        self._generation += 1
        self._stop_monitor()
