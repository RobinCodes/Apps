"""The folder in the side bar: every file in it, the way an IDE shows them.

Built on GTK's own directory listing (Gtk.DirectoryList), which reads each
folder off the main loop and goes on watching it, so a file made by a
terminal, a git pull or another editor appears without anything being
refreshed. A folder is only read when it is first expanded: opening one with
a hundred thousand files somewhere below it costs what its top level costs.

Everything is shown — dotfiles, .git, build leftovers — because the point is
to see what is actually in the folder. "Show hidden files" in the tree's menu
hides the dotfiles for anyone who would rather not.

The tree only reports what happened: a file was activated, or a file was
renamed, trashed or chosen as the main document. What that means for the
open tabs is the window's business.
"""

from __future__ import annotations

import os
import re

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from . import widgets  # noqa: E402

ATTRIBUTES = ",".join((
    "standard::name",
    "standard::display-name",
    "standard::type",
    "standard::is-hidden",
    "standard::is-backup",
    "standard::is-symlink",
))

# The status letters git gives a file, each with its colour.
GIT_CSS = {"M": "git-modified", "A": "git-added", "?": "git-added", "D": "git-deleted",
           "U": "git-conflict", "R": "git-renamed", "C": "git-renamed", "T": "git-modified"}

_DIGITS = re.compile(r"(\d+)")


def _natural(name: str):
    """chapter2 before chapter10, the way a person counts."""
    return [int(part) if part.isdigit() else part for part in _DIGITS.split(name.casefold())]


def _path_of(info: Gio.FileInfo) -> str | None:
    file = info.get_attribute_object("standard::file")
    return file.get_path() if file is not None else None


def _is_dir(info: Gio.FileInfo) -> bool:
    return info.get_file_type() == Gio.FileType.DIRECTORY


class FileTree(Gtk.Box):
    """A folder's files, lazily, with git's letters beside the changed ones.

    `on_open(path)` is called when a file is activated. `on_request(kind,
    payload)` carries the rest to the window: "open-folder" (payload: a
    folder, or None to ask for one), "close-folder", "main" (a path),
    "renamed" ((old, new)), "trashed" (a path) and "hidden" (whether hidden
    files are now shown, to remember).
    """

    def __init__(self, on_open, on_request):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("file-tree")
        self.on_open = on_open
        self.on_request = on_request

        self.folder: str | None = None
        self.show_hidden = True
        self.main_path: str | None = None
        self.current_path: str | None = None
        self._git: dict[str, str] = {}        # path -> git's letter for it
        self._git_dirs: set[str] = set()      # folders with a change below them
        self._rows: set = set()               # the row widgets currently bound

        # One filter and one sorter for every folder's listing, so a single
        # `changed` reaches all of them at once.
        self._filter = Gtk.CustomFilter.new(self._visible, None)
        self._sorter = Gtk.CustomSorter.new(self._compare, None)

        self._build_actions()
        self.append(self._build_header())

        self.selection = Gtk.SingleSelection(autoselect=False, can_unselect=True)
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._setup)
        factory.connect("bind", self._bind)
        factory.connect("unbind", self._unbind)
        self.list = Gtk.ListView(model=self.selection, factory=factory,
                                 single_click_activate=True)
        self.list.add_css_class("navigation-sidebar")
        self.list.connect("activate", self._on_activate)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.list.add_controller(keys)

        scroller = Gtk.ScrolledWindow(child=self.list, vexpand=True, hexpand=True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self.stack = Gtk.Stack(vexpand=True)
        self.stack.add_named(self._build_empty(), "empty")
        self.stack.add_named(scroller, "tree")
        self.append(self.stack)
        self.stack.set_visible_child_name("empty")

    # ------------------------------------------------------------------ UI --

    def _build_header(self) -> Gtk.Widget:
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        header.add_css_class("file-tree-header")
        self.title = Gtk.Label(label="Files", xalign=0, hexpand=True,
                               ellipsize=Pango.EllipsizeMode.MIDDLE, css_classes=["heading"])
        header.append(self.title)

        new = Gtk.MenuButton(label="New", css_classes=["flat", "small-button"])
        new.set_tooltip_text("A new file or folder at the top of this folder")
        menu = Gio.Menu()
        menu.append("New file…", "tree.new-file-top")
        menu.append("New folder…", "tree.new-folder-top")
        new.set_menu_model(menu)
        new.set_valign(Gtk.Align.CENTER)
        self.new_button = new
        header.append(new)

        more = Gtk.MenuButton(label="⋯", css_classes=["flat", "small-button"])
        more.set_tooltip_text("Folder options")
        options = Gio.Menu()
        view = Gio.Menu()
        view.append("Show hidden files", "tree.hidden")
        view.append("Collapse all", "tree.collapse")
        options.append_section(None, view)
        folder = Gio.Menu()
        folder.append("Open another folder…", "tree.open-folder")
        folder.append("Show in file manager", "tree.reveal-top")
        folder.append("Close folder", "tree.close-folder")
        options.append_section(None, folder)
        more.set_menu_model(options)
        more.set_valign(Gtk.Align.CENTER)
        header.append(more)
        return header

    def _build_empty(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                      valign=Gtk.Align.CENTER, margin_start=16, margin_end=16)
        box.append(Gtk.Label(label="No folder open", css_classes=["dim-label"]))
        blurb = Gtk.Label(
            label="Open a folder to see every file in it, and to compile a "
                  "multi-file document from its main file.",
            wrap=True, justify=Gtk.Justification.CENTER,
            css_classes=["dim-label", "caption"])
        box.append(blurb)
        button = Gtk.Button(label="Open Folder…", halign=Gtk.Align.CENTER)
        button.set_action_name("win.open-folder")
        box.append(button)
        return box

    def _build_actions(self):
        group = Gio.SimpleActionGroup()

        def on_path(name, handler):
            action = Gio.SimpleAction.new(name, GLib.VariantType.new("s"))
            action.connect("activate", lambda _a, value: handler(value.get_string()))
            group.add_action(action)

        def plain(name, handler):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda *_: handler())
            group.add_action(action)

        on_path("open", self._open_path)
        on_path("open-as-folder", lambda p: self.on_request("open-folder", p))
        on_path("open-external", self._open_external)
        on_path("reveal", self._reveal)
        on_path("set-main", lambda p: self.on_request("main", p))
        on_path("new-file", self._new_file)
        on_path("new-folder", self._new_folder)
        on_path("rename", self._rename)
        on_path("trash", self._trash)
        on_path("copy-path", lambda p: widgets.copyable(self, p, "Path copied"))

        plain("new-file-top", lambda: self.folder and self._new_file(self.folder))
        plain("new-folder-top", lambda: self.folder and self._new_folder(self.folder))
        plain("reveal-top", lambda: self.folder and self._open_external(self.folder))
        plain("open-folder", lambda: self.on_request("open-folder", None))
        plain("close-folder", lambda: self.on_request("close-folder", None))
        plain("collapse", self.collapse_all)

        hidden = Gio.SimpleAction.new_stateful("hidden", None, GLib.Variant.new_boolean(True))

        def toggle_hidden(action, _param):
            self.set_show_hidden(not action.get_state().get_boolean())
            self.on_request("hidden", self.show_hidden)

        hidden.connect("activate", toggle_hidden)
        group.add_action(hidden)
        self._hidden_action = hidden
        self.insert_action_group("tree", group)

    # --------------------------------------------------------------- model --

    def set_folder(self, folder: str | None):
        """Show `folder`, or the empty page for None."""
        self.folder = folder
        self._rows.clear()
        if not folder:
            self.selection.set_model(None)
            self.title.set_label("Files")
            self.title.set_tooltip_text(None)
            self.stack.set_visible_child_name("empty")
            self.new_button.set_sensitive(False)
            return
        root = self._listing(Gio.File.new_for_path(folder))
        self.model = Gtk.TreeListModel.new(root, False, False, self._children)
        self.selection.set_model(self.model)
        self.title.set_label(os.path.basename(os.path.normpath(folder)) or folder)
        self.title.set_tooltip_text(folder.replace(os.path.expanduser("~"), "~", 1))
        self.stack.set_visible_child_name("tree")
        self.new_button.set_sensitive(True)

    def _listing(self, file: Gio.File) -> Gio.ListModel:
        listing = Gtk.DirectoryList(file=file, attributes=ATTRIBUTES, monitored=True)
        filtered = Gtk.FilterListModel(model=listing, filter=self._filter)
        return Gtk.SortListModel(model=filtered, sorter=self._sorter)

    def _children(self, item, *_):
        if not _is_dir(item):
            return None
        file = item.get_attribute_object("standard::file")
        return self._listing(file) if file is not None else None

    def _visible(self, info, *_):
        if self.show_hidden:
            return True
        return not (info.get_is_hidden() or info.get_is_backup())

    def _compare(self, a, b, *_):
        dir_a, dir_b = _is_dir(a), _is_dir(b)
        if dir_a != dir_b:
            return Gtk.Ordering.SMALLER if dir_a else Gtk.Ordering.LARGER
        key_a, key_b = _natural(a.get_display_name()), _natural(b.get_display_name())
        if key_a < key_b:
            return Gtk.Ordering.SMALLER
        if key_a > key_b:
            return Gtk.Ordering.LARGER
        return Gtk.Ordering.EQUAL

    def set_show_hidden(self, show: bool):
        self.show_hidden = show
        self._hidden_action.set_state(GLib.Variant.new_boolean(show))
        self._filter.changed(Gtk.FilterChange.DIFFERENT)

    def collapse_all(self):
        model = getattr(self, "model", None)
        if model is None:
            return
        # Collapsing the top-level rows takes everything below with them.
        for index in range(model.get_n_items() - 1, -1, -1):
            row = model.get_row(index)
            if row is not None and row.get_depth() == 0 and row.get_expanded():
                row.set_expanded(False)

    # ---------------------------------------------------------------- rows --

    def _setup(self, _factory, list_item):
        expander = Gtk.TreeExpander()
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        name = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.MIDDLE)
        tag = widgets.pill("main", "badge-ok")
        tag.set_tooltip_text("The main document: every file in this folder compiles through it")
        badge = Gtk.Label(css_classes=["git-badge"], width_chars=1)
        box.append(name)
        box.append(tag)
        box.append(badge)
        expander.set_child(box)
        expander._parts = (name, tag, badge)
        expander._path = None
        expander._is_dir = False

        click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        click.connect("pressed", self._on_context, list_item)
        expander.add_controller(click)
        list_item.set_child(expander)

    def _bind(self, _factory, list_item):
        row = list_item.get_item()
        expander = list_item.get_child()
        expander.set_list_row(row)
        info = row.get_item()
        expander._path = _path_of(info)
        expander._is_dir = _is_dir(info)
        expander._hidden = info.get_is_hidden() or info.get_is_backup()
        expander._parts[0].set_label(info.get_display_name())
        self._style(expander)
        self._rows.add(expander)

    def _unbind(self, _factory, list_item):
        expander = list_item.get_child()
        expander.set_list_row(None)
        self._rows.discard(expander)

    def _style(self, expander):
        name, tag, badge = expander._parts
        path = expander._path
        for css, wanted in (
            ("tree-dir", expander._is_dir),
            ("tree-hidden", getattr(expander, "_hidden", False)),
            ("tree-current", bool(path) and path == self.current_path),
        ):
            if wanted:
                name.add_css_class(css)
            else:
                name.remove_css_class(css)
        tag.set_visible(bool(path) and path == self.main_path)

        letter = self._git.get(path, "") if path else ""
        if not letter and expander._is_dir and path in self._git_dirs:
            letter = "•"
        badge.set_label(letter)
        for css in set(GIT_CSS.values()) | {"git-dir"}:
            badge.remove_css_class(css)
        if letter == "•":
            badge.add_css_class("git-dir")
        elif letter:
            badge.add_css_class(GIT_CSS.get(letter, "git-modified"))
        badge.set_tooltip_text(_GIT_WORDS.get(letter))

    def _restyle(self):
        for expander in list(self._rows):
            self._style(expander)

    def set_main(self, path: str | None):
        self.main_path = path
        self._restyle()

    def set_current(self, path: str | None):
        self.current_path = path
        self._restyle()

    def set_git(self, top: str | None, files: list[dict] | None):
        """Git's letters for the changed files, from a Git Manager status."""
        self._git.clear()
        self._git_dirs.clear()
        if top and files:
            top = os.path.normpath(top)
            for entry in files:
                path = os.path.normpath(os.path.join(top, entry.get("path", "")))
                self._git[path] = entry.get("label") or "M"
                folder = os.path.dirname(path)
                while len(folder) >= len(top) and folder not in self._git_dirs:
                    self._git_dirs.add(folder)
                    parent = os.path.dirname(folder)
                    if parent == folder:
                        break
                    folder = parent
        self._restyle()

    # ------------------------------------------------------------- actions --

    def _row_at(self, position):
        row = self.selection.get_item(position)
        return row, row.get_item() if row is not None else None

    def _on_activate(self, _list, position):
        row, info = self._row_at(position)
        if info is None:
            return
        if _is_dir(info):
            row.set_expanded(not row.get_expanded())
            return
        path = _path_of(info)
        if path:
            self._open_path(path)

    def _selected(self):
        position = self.selection.get_selected()
        if position == Gtk.INVALID_LIST_POSITION:
            return None, None
        return self._row_at(position)

    def _on_key(self, _controller, keyval, _keycode, _state):
        _row, info = self._selected()
        if info is None:
            return False
        path = _path_of(info)
        if keyval == Gdk.KEY_F2 and path:
            self._rename(path)
            return True
        if keyval == Gdk.KEY_Delete and path:
            self._trash(path)
            return True
        return False

    def _on_context(self, gesture, _n_press, x, y, list_item):
        expander = list_item.get_child()
        path = getattr(expander, "_path", None)
        if not path:
            return
        self.selection.set_selected(list_item.get_position())
        popover = Gtk.PopoverMenu.new_from_model(self._menu_for(path, expander._is_dir))
        popover.set_parent(expander)
        popover.set_has_arrow(False)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        popover.set_pointing_to(rect)
        popover.connect("closed", lambda p: GLib.idle_add(p.unparent))
        popover.popup()
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    def _menu_for(self, path: str, is_dir: bool) -> Gio.Menu:
        def item(label, action, target=path):
            entry = Gio.MenuItem.new(label, None)
            entry.set_action_and_target_value(action, GLib.Variant.new_string(target))
            return entry

        menu = Gio.Menu()
        first = Gio.Menu()
        if is_dir:
            first.append_item(item("Open as the folder", "tree.open-as-folder"))
        else:
            first.append_item(item("Open", "tree.open"))
            if path.lower().endswith((".tex", ".ltx")) and path != self.main_path:
                first.append_item(item("Use as the main document", "tree.set-main"))
        first.append_item(item("Open with another app" if not is_dir else "Show in file manager",
                               "tree.open-external"))
        menu.append_section(None, first)

        make = Gio.Menu()
        where = path if is_dir else os.path.dirname(path)
        make.append_item(item("New file…", "tree.new-file", where))
        make.append_item(item("New folder…", "tree.new-folder", where))
        menu.append_section(None, make)

        change = Gio.Menu()
        change.append_item(item("Rename…", "tree.rename"))
        change.append_item(item("Move to trash", "tree.trash"))
        change.append_item(item("Copy path", "tree.copy-path"))
        if not is_dir:
            change.append_item(item("Show in file manager", "tree.reveal"))
        menu.append_section(None, change)
        return menu

    def _open_path(self, path: str):
        if os.path.isdir(path):
            return
        self.on_open(path)

    def _launcher_done(self, launcher, result, *_):
        try:
            launcher.launch_finish(result)
        except GLib.Error as exc:
            widgets.error_toast(self, exc)

    def _open_external(self, path: str):
        launcher = Gtk.FileLauncher.new(Gio.File.new_for_path(path))
        launcher.launch(self.get_root(), None, self._launcher_done)

    def _reveal(self, path: str):
        launcher = Gtk.FileLauncher.new(Gio.File.new_for_path(path))

        def done(source, result, *_):
            try:
                source.open_containing_folder_finish(result)
            except GLib.Error as exc:
                widgets.error_toast(self, exc)

        launcher.open_containing_folder(self.get_root(), None, done)

    def _new_file(self, folder: str):
        def make(name):
            target = self._child_path(folder, name)
            if target is None:
                return
            try:
                # O_EXCL: never truncate a file that already has that name.
                os.close(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644))
            except FileExistsError:
                widgets.toast(self, f"{name} already exists there.")
                return
            except OSError as exc:
                widgets.error_toast(self, exc)
                return
            self.on_open(target)

        widgets.ask_text(self.get_root(), "New file", _where(folder, self.folder),
                         "chapter.tex", "Create", make)

    def _new_folder(self, folder: str):
        def make(name):
            target = self._child_path(folder, name)
            if target is None:
                return
            try:
                os.makedirs(target, exist_ok=False)
            except OSError as exc:
                widgets.error_toast(self, exc)

        widgets.ask_text(self.get_root(), "New folder", _where(folder, self.folder),
                         "figures", "Create", make)

    def _child_path(self, folder: str, name: str) -> str | None:
        name = name.strip()
        if not name or name in (".", "..") or "/" in name or os.sep in name:
            widgets.toast(self, "That is not a name a file can have.")
            return None
        return os.path.join(folder, name)

    def _rename(self, path: str):
        old_name = os.path.basename(path)

        def rename(name):
            target = self._child_path(os.path.dirname(path), name)
            if target is None or target == path:
                return
            if os.path.exists(target):
                widgets.toast(self, f"{name} already exists there.")
                return
            try:
                os.rename(path, target)
            except OSError as exc:
                widgets.error_toast(self, exc)
                return
            self.on_request("renamed", (path, target))

        widgets.ask_text(self.get_root(), f"Rename {old_name}", None, old_name, "Rename",
                         rename, select_stem=True)

    def _trash(self, path: str):
        name = os.path.basename(path)

        def trash():
            try:
                Gio.File.new_for_path(path).trash(None)
            except GLib.Error as exc:
                widgets.error_toast(self, exc)
                return
            self.on_request("trashed", path)
            widgets.toast(self, f"Moved {name} to the trash")

        what = "the folder and everything in it" if os.path.isdir(path) else "it"
        widgets.confirm(self.get_root(), f"Move {name} to the trash?",
                        f"You can get {what} back from the trash.", "Move to trash", trash)


def _where(folder: str, top: str | None) -> str:
    if top and folder != top and folder.startswith(top):
        return "In " + os.path.relpath(folder, top)
    return "At the top of " + os.path.basename(os.path.normpath(folder))


_GIT_WORDS = {
    "M": "Changed since the last commit",
    "A": "New, staged for the next commit",
    "?": "New, not in git yet",
    "D": "Deleted",
    "U": "In conflict: both sides changed it",
    "R": "Renamed",
    "C": "Copied",
    "T": "Type changed",
    "•": "Something in here has changed",
}
