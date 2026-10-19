"""What a file is, in full — and the one place its permissions can be changed.

A properties dialog is mostly reading, with one piece of real editing in it.
Both halves need care for the same reason: they are about a thing on a disk
that may be enormous, may be on a network, and may be forty thousand things
at once.

*Sizes are counted on a thread.* A folder's size is a walk, and the walk is
why every file manager's properties dialog has a moment of blankness. Here
the dialog opens immediately with "counting…" and the number arrives when it
arrives; closing the dialog stops the walk.

*Permissions are shown as three dropdowns and a switch*, not as nine
checkboxes and not as an octal field. Nine checkboxes is an invitation to
make a mistake, and the octal is shown beside them for anybody who thinks in
it. Applying a mode to a whole tree goes through `ops.set_mode`, which knows
that a directory needs `x` wherever it has `r`.
"""

from __future__ import annotations

import os
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from . import dialogs, filetypes, ops, search, units, vcs  # noqa: E402

# What each dropdown offers, as (label, the three bits it sets).
ACCESS = (
    ("Nothing", 0o0),
    ("Read only", 0o4),
    ("Read and write", 0o6),
)


class Properties:
    """One dialog, for one selection."""

    def __init__(self, parent: Gtk.Widget, paths: list[str], config,
                 thumbnailer=None):
        self.parent = parent
        self.paths = [p for p in paths if p]
        self.config = config
        self.thumbs = thumbnailer
        self.stop = threading.Event()
        self.mode_bits = 0
        self.dialog = dialogs._dialog("Properties", 480)
        self.dialog.connect("closed", lambda _d: self.stop.set())

        if len(self.paths) == 1:
            self._build_single(self.paths[0])
        else:
            self._build_many()

    def present(self) -> None:
        self.dialog.present(self.parent)

    # -- one thing --------------------------------------------------------

    def _build_single(self, path: str) -> None:
        name = os.path.basename(path.rstrip(os.sep)) or path
        try:
            info = os.lstat(path)
        except OSError:
            body = dialogs._page(self.dialog, name,
                                 subtitle="It is not there any more")
            close = dialogs._button("Close")
            close.connect("clicked", lambda _b: self.dialog.close())
            body.append(dialogs._buttons(close))
            return

        is_link = os.path.islink(path)
        is_dir = os.path.isdir(path)
        body = dialogs._page(self.dialog, name,
                             subtitle=filetypes.type_label(path, is_dir, is_link))

        body.append(self._header_art(path, is_dir))

        fields = _FieldList()
        body.append(fields.widget)

        fields.add("Where", dialogs._short(os.path.dirname(path)))
        if is_link:
            target = os.readlink(path)
            fields.add("Points at", target
                       + ("" if os.path.exists(path) else "  (nothing there)"))

        self.size_row = fields.add("Size", "counting…" if is_dir
                                   else units.human_size(info.st_size))
        if is_dir:
            self.items_row = fields.add("Contents", "counting…")
        else:
            self.items_row = None
            if info.st_size:
                fields.add("On disk",
                           units.human_size(getattr(info, "st_blocks", 0) * 512))

        fields.add("Modified", units.exact_time(info.st_mtime))
        fields.add("Accessed", units.exact_time(info.st_atime))
        fields.add("Changed", units.exact_time(info.st_ctime))

        owner = _owner_name(info)
        group = _group_name(info)
        if owner or group:
            fields.add("Owner", f"{owner}:{group}" if group else owner)
        if getattr(info, "st_nlink", 1) > 1 and not is_dir:
            fields.add("Hard links", str(info.st_nlink))

        mark = vcs.status(os.path.dirname(path)).get(os.path.abspath(path), "")
        if mark:
            fields.add("Git", vcs.LABELS.get(mark, mark))

        free = _free_space(path)
        if free:
            fields.add("Free on this disk", free)

        if not os.name == "nt":
            body.append(self._permissions(path, info, is_dir))

        body.append(self._actions(path, is_dir))

        if is_dir:
            self._count_folder(path)

    def _header_art(self, path: str, is_dir: bool) -> Gtk.Widget:
        """A big icon, or the file's own thumbnail where there is one."""
        frame = Gtk.Box()
        frame.set_halign(Gtk.Align.CENTER)
        frame.set_margin_top(2)
        frame.set_margin_bottom(6)
        texture = None
        if self.thumbs is not None:
            from . import thumbs as thumbmod

            cached = thumbmod.valid_cached(path, 256)
            if cached:
                try:
                    from gi.repository import Gdk

                    texture = Gdk.Texture.new_from_filename(cached)
                except GLib.Error:
                    texture = None
        if texture is not None:
            picture = Gtk.Picture.new_for_paintable(texture)
            picture.set_content_fit(Gtk.ContentFit.CONTAIN)
            picture.set_size_request(128, 128)
            picture.add_css_class("preview-image")
            frame.append(picture)
        else:
            icon = Gtk.Image.new_from_gicon(filetypes.icon_for(path, is_dir))
            icon.set_pixel_size(72)
            icon.add_css_class("entry-icon")
            icon.add_css_class(f"cat-{filetypes.category(path, is_dir)}")
            frame.append(icon)
        return frame

    # -- permissions ------------------------------------------------------

    def _permissions(self, path: str, info, is_dir: bool) -> Gtk.Widget:
        import stat as statmod

        self.mode_bits = statmod.S_IMODE(info.st_mode)
        writable = os.access(path, os.W_OK) and info.st_uid == os.getuid()

        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        panel.add_css_class("panel")
        panel.set_margin_top(6)
        for side in ("start", "end", "top", "bottom"):
            getattr(panel, f"set_margin_{side}")(12)

        heading = Gtk.Label(label="Permissions", xalign=0.0)
        heading.add_css_class("field-key")
        panel.append(heading)

        grid = Gtk.Grid(column_spacing=12, row_spacing=6)
        self.pickers = {}
        for row, (label, shift) in enumerate((("Owner", 6), ("Group", 3),
                                              ("Everyone", 0))):
            key = Gtk.Label(label=label, xalign=0.0)
            key.add_css_class("field-value")
            picker = Gtk.DropDown.new_from_strings([text for text, _bits in ACCESS])
            picker.set_selected(_access_index(self.mode_bits, shift))
            picker.set_sensitive(writable)
            picker.connect("notify::selected", self._mode_changed, shift)
            grid.attach(key, 0, row, 1, 1)
            grid.attach(picker, 1, row, 1, 1)
            self.pickers[shift] = picker
        panel.append(grid)

        self.executable = Gtk.Switch()
        self.executable.set_active(bool(self.mode_bits & 0o111))
        self.executable.set_sensitive(writable)
        self.executable.set_halign(Gtk.Align.END)
        self.executable.connect("notify::active", self._exec_changed)
        exec_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        exec_label = Gtk.Label(
            label="Can be entered" if is_dir else "Can be run as a program",
            xalign=0.0)
        exec_label.add_css_class("field-value")
        exec_label.set_hexpand(True)
        exec_row.append(exec_label)
        exec_row.append(self.executable)
        panel.append(exec_row)

        self.octal = Gtk.Label(xalign=0.0)
        self.octal.add_css_class("monospace")
        self.octal.add_css_class("cell-dim")
        panel.append(self.octal)
        self._show_octal()

        if is_dir and writable:
            apply_all = dialogs._button("Apply to everything inside")
            apply_all.set_halign(Gtk.Align.START)
            apply_all.connect("clicked", lambda _b: self._apply_recursive(path))
            panel.append(apply_all)

        if not writable:
            note = Gtk.Label(label="Only the owner can change these", xalign=0.0)
            note.add_css_class("dialog-body")
            panel.append(note)

        self._path = path
        return panel

    def _mode_changed(self, picker, _param, shift: int) -> None:
        bits = ACCESS[picker.get_selected()][1]
        self.mode_bits &= ~(0o6 << shift)
        self.mode_bits |= bits << shift
        self._commit_mode()

    def _exec_changed(self, switch, _param) -> None:
        if switch.get_active():
            # Only where there is read access to go with it: an executable
            # bit on a file nobody may read does nothing.
            for shift in (6, 3, 0):
                if self.mode_bits & (0o4 << shift):
                    self.mode_bits |= 0o1 << shift
        else:
            self.mode_bits &= ~0o111
        self._commit_mode()

    def _commit_mode(self) -> None:
        self._show_octal()
        ok, why = ops.set_mode(self._path, self.mode_bits)
        if not ok:
            dialogs.tell(self.parent, "Could not change the permissions", why)

    def _show_octal(self) -> None:
        self.octal.set_label(f"{units.human_mode(self.mode_bits)}   "
                             f"{self.mode_bits:04o}")

    def _apply_recursive(self, path: str) -> None:
        def go():
            ok, why = ops.set_mode(path, self.mode_bits, recursive=True)
            if not ok:
                dialogs.tell(self.parent, "Some items were not changed", why)

        dialogs.confirm(
            self.parent, "Apply to everything inside?",
            f"Every file and folder under “{os.path.basename(path)}” will be "
            f"set to {self.mode_bits:04o}. Folders keep the access they need "
            "to be opened.", "Apply", go, destructive=True)

    # -- the buttons ------------------------------------------------------

    def _actions(self, path: str, is_dir: bool) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.set_margin_top(8)
        if not is_dir:
            digests = dialogs._button("Checksums")
            digests.connect("clicked",
                            lambda _b: dialogs.show_checksums(self.parent, path))
            row.append(digests)
        copy_path = dialogs._button("Copy path")
        copy_path.connect("clicked",
                          lambda _b: dialogs._to_clipboard(self.parent, path))
        row.append(copy_path)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        row.append(spacer)
        close = dialogs._button("Close", "suggested-action")
        close.connect("clicked", lambda _b: self.dialog.close())
        row.append(close)
        return row

    # -- counting ---------------------------------------------------------

    def _count_folder(self, path: str) -> None:
        def worker():
            total, files, folders = search.folder_size(path, self.stop)
            if not self.stop.is_set():
                GLib.idle_add(deliver, total, files, folders)

        def deliver(total: int, files: int, folders: int) -> bool:
            self.size_row.set_label(units.human_size(total, False))
            if self.items_row is not None:
                self.items_row.set_label(units.human_items(files, folders))
            return False

        threading.Thread(target=worker, daemon=True,
                         name="hermione-size").start()

    # -- many things ------------------------------------------------------

    def _build_many(self) -> None:
        body = dialogs._page(self.dialog, f"{len(self.paths)} items selected")
        fields = _FieldList()
        body.append(fields.widget)

        counts = search.categories_present(self.paths)
        folders = counts.get("folder", 0)
        fields.add("What they are",
                   ", ".join(f"{count} × {filetypes.label(key)}"
                             for key, count in sorted(counts.items(),
                                                      key=lambda kv: -kv[1])))
        fields.add("Where", dialogs._short(os.path.dirname(self.paths[0])))
        self.size_row = fields.add("Size", "counting…")
        self.items_row = fields.add("Contents", "counting…") if folders else None

        body.append(self._actions_many())
        self._count_many()

    def _actions_many(self) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.set_margin_top(8)
        copy_paths = dialogs._button("Copy paths")
        copy_paths.connect(
            "clicked",
            lambda _b: dialogs._to_clipboard(self.parent, "\n".join(self.paths)))
        row.append(copy_paths)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        row.append(spacer)
        close = dialogs._button("Close", "suggested-action")
        close.connect("clicked", lambda _b: self.dialog.close())
        row.append(close)
        return row

    def _count_many(self) -> None:
        def worker():
            total = 0
            files = 0
            folders = 0
            for path in self.paths:
                if self.stop.is_set():
                    return
                if os.path.isdir(path) and not os.path.islink(path):
                    folders += 1
                    sub_total, sub_files, sub_folders = search.folder_size(
                        path, self.stop)
                    total += sub_total
                    files += sub_files
                    folders += sub_folders
                else:
                    files += 1
                    try:
                        total += os.lstat(path).st_size
                    except OSError:
                        pass
            if not self.stop.is_set():
                GLib.idle_add(deliver, total, files, folders)

        def deliver(total, files, folders) -> bool:
            self.size_row.set_label(units.human_size(total))
            if self.items_row is not None:
                self.items_row.set_label(units.human_items(files, folders))
            return False

        threading.Thread(target=worker, daemon=True,
                         name="hermione-size").start()


class _FieldList:
    """A column of label/value pairs, which is most of this dialog."""

    def __init__(self):
        self.widget = Gtk.Grid(column_spacing=14, row_spacing=5)
        self.widget.add_css_class("panel")
        for side in ("start", "end", "top", "bottom"):
            getattr(self.widget, f"set_margin_{side}")(12)
        self.row = 0

    def add(self, key: str, value: str) -> Gtk.Label:
        label = Gtk.Label(label=key, xalign=0.0)
        label.add_css_class("field-key")
        label.set_valign(Gtk.Align.START)
        shown = Gtk.Label(label=value, xalign=0.0, selectable=True)
        shown.add_css_class("field-value")
        shown.set_wrap(True)
        shown.set_wrap_mode(2)
        shown.set_hexpand(True)
        shown.set_max_width_chars(42)
        self.widget.attach(label, 0, self.row, 1, 1)
        self.widget.attach(shown, 1, self.row, 1, 1)
        self.row += 1
        return shown


def _access_index(mode: int, shift: int) -> int:
    bits = (mode >> shift) & 0o6
    if bits & 0o2:
        return 2
    if bits & 0o4:
        return 1
    return 0


def _owner_name(info) -> str:
    try:
        import pwd

        return pwd.getpwuid(info.st_uid).pw_name
    except (ImportError, KeyError, AttributeError, OverflowError):
        uid = getattr(info, "st_uid", -1)
        return str(uid) if uid >= 0 else ""


def _group_name(info) -> str:
    try:
        import grp

        return grp.getgrgid(info.st_gid).gr_name
    except (ImportError, KeyError, AttributeError, OverflowError):
        gid = getattr(info, "st_gid", -1)
        return str(gid) if gid >= 0 else ""


def _free_space(path: str) -> str:
    import shutil

    try:
        usage = shutil.disk_usage(path if os.path.isdir(path)
                                  else os.path.dirname(path))
    except OSError:
        return ""
    return (f"{units.human_size(usage.free)} of "
            f"{units.human_size(usage.total)}")


def show(parent: Gtk.Widget, paths: list[str], config, thumbnailer=None) -> None:
    Properties(parent, paths, config, thumbnailer).present()


# The "open with" default, applied. Here rather than in dialogs.py because
# it is about a file's properties, not about a dialog.
def set_default_app(path: str, app: Gio.AppInfo) -> tuple[bool, str]:
    mime = filetypes.mime_type(path, os.path.isdir(path))
    if not mime:
        return False, "That kind of file has no type"
    try:
        app.set_as_default_for_type(mime)
    except GLib.Error as exc:
        return False, exc.message or "Could not set it"
    return True, ""
