"""Every window that is not the main one.

They have two things in common worth stating once.

*They are `Adw.Dialog`s, not `Gtk.Window`s.* A libadwaita dialog is drawn
inside the window that owns it, which means the glass blur behind it is real:
there is app content behind the dialog to blur. A separate toplevel would be
its own surface with nothing of ours behind it, and the same CSS would cost a
full offscreen pass to blur a transparent hole.

*The one that answers a worker thread blocks it.* `ConflictAsker` is called
from inside a copy, on the copy's thread, and has to return an answer. It
posts the dialog to the GTK thread and waits on an `Event` — which is the
whole point: the copy is supposed to stop until the person has decided.
"""

from __future__ import annotations

import os
import re
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from . import archive, filetypes, ops, search, units  # noqa: E402


def _dialog(title: str, width: int = 420) -> Adw.Dialog:
    dialog = Adw.Dialog()
    dialog.set_title(title)
    dialog.set_content_width(width)
    dialog.add_css_class("hermione-dialog")
    return dialog


def _page(dialog: Adw.Dialog, title: str, *, subtitle: str = "") -> Gtk.Box:
    """A dialog's body: a header with the title, and a box to fill.

    Written out rather than using `Adw.PreferencesPage` because these are
    small, and a preferences page puts its own background behind the glass.

    The body goes in a scroller, which matters on a small screen: the
    properties of one file come to about 750 pixels, and the screen this was
    written on is 768 tall. A dialog taller than the window it is drawn
    inside would otherwise be clipped with no way to reach the rest of it.
    """
    view = Adw.ToolbarView()
    # The glass goes on this rather than on the dialog's own internal node,
    # whose name is libadwaita's business and not part of its API.
    view.add_css_class("dialog-glass")
    header = Adw.HeaderBar()
    header.set_show_title(False)
    header.add_css_class("flat")
    view.add_top_bar(header)

    body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    body.set_margin_start(20)
    body.set_margin_end(20)
    body.set_margin_bottom(20)
    body.set_margin_top(4)

    heading = Gtk.Label(label=title, xalign=0.0)
    heading.add_css_class("dialog-title")
    heading.set_wrap(True)
    body.append(heading)
    if subtitle:
        note = Gtk.Label(label=subtitle, xalign=0.0)
        note.add_css_class("dialog-body")
        note.set_wrap(True)
        body.append(note)

    scroller = Gtk.ScrolledWindow()
    scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scroller.set_propagate_natural_height(True)
    scroller.set_child(body)
    view.set_content(scroller)
    dialog.set_child(view)
    return body


def _buttons(*children: Gtk.Widget) -> Gtk.Box:
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    row.set_halign(Gtk.Align.END)
    row.set_margin_top(6)
    for child in children:
        row.append(child)
    return row


def _button(label: str, kind: str = "") -> Gtk.Button:
    """A dialog button: flat by default, or one of Adwaita's coloured kinds."""
    button = Gtk.Button(label=label)
    button.add_css_class(kind or "pill-flat")
    return button


# ------------------------------------------------------------- conflicts ---

class ConflictAsker:
    """What `ops.Job` calls when a name is taken.

    One of these per job. It remembers an answer given "for all of them", so
    a copy of nine hundred files asks once.
    """

    def __init__(self, parent: Gtk.Widget):
        self.parent = parent
        self.blanket = ""
        self._cancelled = False

    def __call__(self, source: str, target: str, is_dir: bool):
        if self._cancelled:
            return ops.CANCEL
        if self.blanket:
            return self.blanket

        answer: list = []
        done = threading.Event()
        GLib.idle_add(self._ask, source, target, is_dir, answer, done)
        # The worker waits here. That is the point: nothing is overwritten
        # while the question is on screen.
        done.wait()
        if not answer:
            return ops.CANCEL
        action, forever = answer[0]
        if forever:
            self.blanket = action
        if action == ops.CANCEL:
            self._cancelled = True
        return action

    def _ask(self, source: str, target: str, is_dir: bool,
             answer: list, done: threading.Event) -> bool:
        name = os.path.basename(target)
        dialog = _dialog("Already there", 460)
        kind = "folder" if is_dir else "file"
        body = _page(dialog, f"A {kind} called “{name}” is already here",
                     subtitle="Which one do you want to keep?")

        body.append(_compare(source, target))

        apply_all = Gtk.CheckButton(label="Do this for everything else too")
        apply_all.set_margin_top(4)
        body.append(apply_all)

        def answer_with(action):
            answer.append((action, apply_all.get_active()))
            dialog.close()

        keep_both = _button("Keep both")
        keep_both.connect("clicked", lambda _b: answer_with(ops.RENAME))
        skip = _button("Skip")
        skip.connect("clicked", lambda _b: answer_with(ops.SKIP))
        replace = _button("Replace", "destructive-action")
        replace.connect("clicked", lambda _b: answer_with(ops.REPLACE))

        row = _buttons(skip, keep_both, replace)
        if is_dir:
            merge = _button("Merge", "suggested-action")
            merge.connect("clicked", lambda _b: answer_with(ops.MERGE))
            row.append(merge)
        body.append(row)

        # Closing the dialog any other way is a cancel, and the worker must
        # be released either way or the job hangs for ever.
        dialog.connect("closed", lambda _d: (answer or answer.append((ops.CANCEL, False)),
                                             done.set()))
        dialog.present(self.parent)
        return False


def _compare(source: str, target: str) -> Gtk.Widget:
    """The two files side by side: which is newer, which is bigger."""
    grid = Gtk.Grid(column_spacing=16, row_spacing=4)
    grid.add_css_class("panel")
    grid.set_margin_top(4)
    for column, (heading, path) in enumerate((("Moving", source),
                                              ("Already here", target))):
        label = Gtk.Label(label=heading, xalign=0.0)
        label.add_css_class("field-key")
        grid.attach(label, column, 0, 1, 1)
        try:
            info = os.lstat(path)
            size = (units.human_size(info.st_size) if not os.path.isdir(path)
                    else "folder")
            when = units.human_time(info.st_mtime)
        except OSError:
            size, when = "—", "—"
        for row, text in enumerate((size, when), start=1):
            value = Gtk.Label(label=text, xalign=0.0)
            value.add_css_class("field-value")
            grid.attach(value, column, row, 1, 1)
    for child in (grid,):
        child.set_margin_start(12)
        child.set_margin_end(12)
        child.set_margin_top(10)
        child.set_margin_bottom(10)
    return grid


# ---------------------------------------------------------------- naming ---

def ask_rename(parent: Gtk.Widget, path: str, on_done) -> None:
    """Rename one thing. `on_done(new_name)` only when it changed."""
    old = os.path.basename(path.rstrip(os.sep))
    dialog = _dialog("Rename")
    body = _page(dialog, f"Rename “{old}”")

    entry = Gtk.Entry(text=old)
    entry.add_css_class("search")
    entry.set_activates_default(True)
    body.append(entry)

    warning = Gtk.Label(xalign=0.0)
    warning.add_css_class("dialog-body")
    warning.set_visible(False)
    body.append(warning)

    rename = _button("Rename", "suggested-action")
    cancel = _button("Cancel")
    cancel.connect("clicked", lambda _b: dialog.close())
    body.append(_buttons(cancel, rename))

    def check(*_args):
        name = entry.get_text().strip()
        bad = (not name or name in (".", "..") or "/" in name
               or (os.sep != "/" and os.sep in name))
        taken = (name != old and name
                 and os.path.lexists(os.path.join(os.path.dirname(path), name)))
        rename.set_sensitive(bool(name) and not bad and not taken)
        if taken:
            warning.set_label("Something here already has that name")
        elif bad and name:
            warning.set_label("A name cannot contain a slash")
        warning.set_visible(bool(taken or (bad and name)))

    def commit(*_args):
        name = entry.get_text().strip()
        if name and name != old and rename.get_sensitive():
            dialog.close()
            on_done(name)

    entry.connect("changed", check)
    entry.connect("activate", commit)
    rename.connect("clicked", commit)
    check()

    # Select the stem, not the extension: renaming a file almost never means
    # renaming what kind of file it is.
    stem, suffix = ops.split_name(old)
    GLib.idle_add(lambda: (entry.grab_focus(),
                           entry.select_region(0, len(stem) if suffix else -1),
                           False)[-1])
    dialog.present(parent)


def ask_create(parent: Gtk.Widget, folder: str, on_done) -> None:
    """A new folder or file. `on_done(kind, name)` with kind "folder"/"file"."""
    dialog = _dialog("New")
    body = _page(dialog, "Create something new", subtitle=f"in {_short(folder)}")

    kinds = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    kinds.add_css_class("button-cluster")
    kinds.set_halign(Gtk.Align.START)
    folder_button = Gtk.ToggleButton(label="Folder", active=True)
    file_button = Gtk.ToggleButton(label="Empty file")
    file_button.set_group(folder_button)
    for button in (folder_button, file_button):
        button.add_css_class("pill-flat")
        kinds.append(button)
    body.append(kinds)

    entry = Gtk.Entry(text="New Folder")
    entry.add_css_class("search")
    entry.set_activates_default(True)
    body.append(entry)

    create = _button("Create", "suggested-action")
    cancel = _button("Cancel")
    cancel.connect("clicked", lambda _b: dialog.close())
    body.append(_buttons(cancel, create))

    def swap(*_args):
        if file_button.get_active() and entry.get_text() == "New Folder":
            entry.set_text("new-file.txt")
        elif folder_button.get_active() and entry.get_text() == "new-file.txt":
            entry.set_text("New Folder")

    def commit(*_args):
        name = entry.get_text().strip()
        if not name or "/" in name:
            return
        dialog.close()
        on_done("folder" if folder_button.get_active() else "file", name)

    folder_button.connect("toggled", swap)
    entry.connect("activate", commit)
    create.connect("clicked", commit)
    GLib.idle_add(lambda: (entry.grab_focus(), entry.select_region(0, -1), False)[-1])
    dialog.present(parent)


# --------------------------------------------------------- batch renaming ---

class BatchRename:
    """Renaming many files at once, with the answer shown before it happens.

    Four rules, which is as many as anybody uses: replace some text, add a
    prefix or suffix, number them, and change the case. The preview is the
    feature — a batch rename you cannot see before it runs is a batch rename
    you run twice.
    """

    def __init__(self, parent: Gtk.Widget, paths: list[str], on_done):
        self.parent = parent
        self.paths = list(paths)
        self.on_done = on_done
        self.dialog = _dialog("Rename many", 560)
        body = _page(self.dialog, f"Rename {len(paths)} items")

        self.find = Gtk.Entry(placeholder_text="Find…")
        self.replace = Gtk.Entry(placeholder_text="Replace with…")
        self.prefix = Gtk.Entry(placeholder_text="Prefix")
        self.suffix = Gtk.Entry(placeholder_text="Suffix (before the extension)")
        for entry in (self.find, self.replace, self.prefix, self.suffix):
            entry.add_css_class("search")
            entry.connect("changed", self._refresh)

        self.regex = Gtk.CheckButton(label="The “find” text is a regular expression")
        self.regex.connect("toggled", self._refresh)

        self.numbering = Gtk.CheckButton(label="Number them")
        self.numbering.connect("toggled", self._refresh)
        self.start_at = Gtk.SpinButton.new_with_range(0, 100000, 1)
        self.start_at.set_value(1)
        self.start_at.connect("value-changed", self._refresh)
        self.digits = Gtk.SpinButton.new_with_range(1, 6, 1)
        self.digits.set_value(2)
        self.digits.connect("value-changed", self._refresh)

        self.case = Gtk.DropDown.new_from_strings(
            ["Leave the case alone", "lower case", "UPPER CASE", "Title Case"])
        self.case.connect("notify::selected", self._refresh)

        pairs = Gtk.Grid(column_spacing=8, row_spacing=8)
        pairs.attach(self.find, 0, 0, 1, 1)
        pairs.attach(self.replace, 1, 0, 1, 1)
        pairs.attach(self.prefix, 0, 1, 1, 1)
        pairs.attach(self.suffix, 1, 1, 1, 1)
        for child in (self.find, self.replace, self.prefix, self.suffix):
            child.set_hexpand(True)
        body.append(pairs)
        body.append(self.regex)

        numbers = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        numbers.append(self.numbering)
        numbers.append(Gtk.Label(label="from"))
        numbers.append(self.start_at)
        numbers.append(Gtk.Label(label="with"))
        numbers.append(self.digits)
        numbers.append(Gtk.Label(label="digits"))
        body.append(numbers)
        body.append(self.case)

        self.preview = Gtk.ListBox()
        self.preview.add_css_class("panel")
        self.preview.set_selection_mode(Gtk.SelectionMode.NONE)
        scroller = Gtk.ScrolledWindow()
        scroller.set_child(self.preview)
        scroller.set_min_content_height(180)
        scroller.set_vexpand(True)
        body.append(scroller)

        self.note = Gtk.Label(xalign=0.0)
        self.note.add_css_class("dialog-body")
        body.append(self.note)

        self.apply = _button("Rename", "suggested-action")
        self.apply.connect("clicked", self._commit)
        cancel = _button("Cancel")
        cancel.connect("clicked", lambda _b: self.dialog.close())
        body.append(_buttons(cancel, self.apply))

        self._refresh()

    def present(self) -> None:
        self.dialog.present(self.parent)

    # -- the rules --------------------------------------------------------

    def plan(self) -> list[tuple[str, str]]:
        """(old path, new name) for everything that would change."""
        find = self.find.get_text()
        replace = self.replace.get_text()
        prefix = self.prefix.get_text()
        suffix = self.suffix.get_text()
        mode = self.case.get_selected()
        pattern = None
        if find and self.regex.get_active():
            try:
                pattern = re.compile(find)
            except re.error:
                return []

        out = []
        start = int(self.start_at.get_value())
        width = int(self.digits.get_value())
        for index, path in enumerate(self.paths):
            old = os.path.basename(path.rstrip(os.sep))
            stem, ext = ops.split_name(old)
            if find:
                if pattern is not None:
                    try:
                        stem = pattern.sub(replace, stem)
                    except re.error:
                        pass
                else:
                    stem = stem.replace(find, replace)
            if mode == 1:
                stem = stem.lower()
            elif mode == 2:
                stem = stem.upper()
            elif mode == 3:
                stem = stem.title()
            if prefix:
                stem = prefix + stem
            if suffix:
                stem = stem + suffix
            if self.numbering.get_active():
                stem = f"{stem} {str(start + index).zfill(width)}".strip()
            out.append((path, (stem + ext).strip()))
        return out

    def _refresh(self, *_args) -> None:
        while (child := self.preview.get_first_child()) is not None:
            self.preview.remove(child)
        plan = self.plan()
        changed = 0
        clashes = set()
        seen = set()
        for path, new in plan:
            old = os.path.basename(path.rstrip(os.sep))
            if new != old:
                changed += 1
            key = os.path.join(os.path.dirname(path), new)
            if key in seen or (new != old and os.path.lexists(key)):
                clashes.add(new)
            seen.add(key)

        for path, new in plan[:300]:
            old = os.path.basename(path.rstrip(os.sep))
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row.set_margin_start(10)
            row.set_margin_end(10)
            row.set_margin_top(3)
            row.set_margin_bottom(3)
            left = Gtk.Label(label=old, xalign=0.0)
            left.add_css_class("cell-dim")
            left.set_ellipsize(3)
            left.set_hexpand(True)
            arrow = Gtk.Label(label="→")
            arrow.add_css_class("crumb-sep")
            right = Gtk.Label(label=new, xalign=0.0)
            right.set_ellipsize(3)
            right.set_hexpand(True)
            if new in clashes or not new:
                right.add_css_class("is-broken")
            row.append(left)
            row.append(arrow)
            row.append(right)
            self.preview.append(row)

        bad = bool(clashes) or any(not new or "/" in new for _p, new in plan)
        if clashes:
            self.note.set_label("Some of those names clash — nothing will be renamed")
        elif not changed:
            self.note.set_label("Nothing would change")
        else:
            self.note.set_label(f"{changed} of {len(plan)} would be renamed")
        self.apply.set_sensitive(bool(changed) and not bad)

    def _commit(self, _button) -> None:
        plan = [(path, new) for path, new in self.plan()
                if new != os.path.basename(path.rstrip(os.sep))]
        self.dialog.close()
        self.on_done(plan)


# -------------------------------------------------------------- confirming ---

def confirm(parent: Gtk.Widget, title: str, body: str, action: str,
            on_yes, destructive: bool = True) -> None:
    """A yes/no question, as an Adw.AlertDialog — the one case where the
    platform's own dialog is exactly right."""
    dialog = Adw.AlertDialog.new(title, body)
    dialog.add_response("cancel", "Cancel")
    dialog.add_response("go", action)
    dialog.set_response_appearance(
        "go", Adw.ResponseAppearance.DESTRUCTIVE if destructive
        else Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("cancel")
    dialog.set_close_response("cancel")
    dialog.connect("response",
                   lambda _d, response: on_yes() if response == "go" else None)
    dialog.present(parent)


def tell(parent: Gtk.Widget, title: str, body: str = "") -> None:
    dialog = Adw.AlertDialog.new(title, body)
    dialog.add_response("ok", "All right")
    dialog.set_default_response("ok")
    dialog.present(parent)


# -------------------------------------------------------------- checksums ---

def show_checksums(parent: Gtk.Widget, path: str) -> None:
    """The file's digests, computed on a thread and copyable."""
    dialog = _dialog("Checksums", 520)
    body = _page(dialog, os.path.basename(path),
                 subtitle="Computed from the file as it is now")

    rows = {}
    for name in ("md5", "sha1", "sha256"):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        key = Gtk.Label(label=name.upper(), xalign=0.0)
        key.add_css_class("field-key")
        key.set_size_request(70, -1)
        value = Gtk.Label(label="…", xalign=0.0, selectable=True)
        value.add_css_class("monospace")
        value.set_ellipsize(3)
        value.set_hexpand(True)
        copy = Gtk.Button.new_from_icon_name("edit-copy-symbolic")
        copy.add_css_class("pill-flat")
        copy.set_sensitive(False)
        row.append(key)
        row.append(value)
        row.append(copy)
        body.append(row)
        rows[name] = (value, copy)

    close = _button("Close")
    close.connect("clicked", lambda _b: dialog.close())
    body.append(_buttons(close))

    stop = threading.Event()
    dialog.connect("closed", lambda _d: stop.set())

    def worker():
        for name in ("md5", "sha1", "sha256"):
            digest = search.file_hash(path, algorithm=name, stop=stop)
            if stop.is_set():
                return
            GLib.idle_add(deliver, name, digest)

    def deliver(name: str, digest: str) -> bool:
        value, copy = rows[name]
        value.set_label(digest or "could not be read")
        if digest:
            copy.set_sensitive(True)
            copy.connect("clicked", lambda _b, d=digest: _to_clipboard(parent, d))
        return False

    threading.Thread(target=worker, daemon=True, name="hermione-hash").start()
    dialog.present(parent)


def _to_clipboard(widget: Gtk.Widget, text: str) -> None:
    display = widget.get_display()
    if display is not None:
        display.get_clipboard().set(text)


# -------------------------------------------------------------- open with ---

def ask_open_with(parent: Gtk.Widget, path: str, on_pick) -> None:
    """Which program should open this? `on_pick(appinfo, make_default)`."""
    mime = filetypes.mime_type(path, os.path.isdir(path))
    apps = Gio.AppInfo.get_all_for_type(mime) if mime else []
    dialog = _dialog("Open with", 460)
    body = _page(dialog, f"Open “{os.path.basename(path)}” with",
                 subtitle=filetypes.type_label(path))

    listbox = Gtk.ListBox()
    listbox.add_css_class("panel")
    listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
    scroller = Gtk.ScrolledWindow()
    scroller.set_child(listbox)
    scroller.set_min_content_height(220)
    scroller.set_vexpand(True)
    body.append(scroller)

    default = Gio.AppInfo.get_default_for_type(mime, False) if mime else None
    for app in apps:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.set_margin_start(10)
        row.set_margin_end(10)
        row.set_margin_top(6)
        row.set_margin_bottom(6)
        icon = Gtk.Image.new_from_gicon(app.get_icon() or
                                        Gio.ThemedIcon.new("application-x-executable"))
        icon.set_pixel_size(24)
        label = Gtk.Label(label=app.get_display_name(), xalign=0.0)
        label.set_hexpand(True)
        row.append(icon)
        row.append(label)
        if default is not None and app.equal(default):
            badge = Gtk.Label(label="default")
            badge.add_css_class("pill-badge")
            row.append(badge)
        row._app = app
        listbox.append(row)

    if not apps:
        empty = Gtk.Label(label="Nothing on this machine claims this kind of file")
        empty.add_css_class("empty-body")
        empty.set_margin_top(12)
        empty.set_margin_bottom(12)
        listbox.append(empty)

    make_default = Gtk.CheckButton(label="Always use it for this kind of file")
    body.append(make_default)

    open_button = _button("Open", "suggested-action")
    cancel = _button("Cancel")
    cancel.connect("clicked", lambda _b: dialog.close())
    body.append(_buttons(cancel, open_button))

    def chosen(*_args):
        row = listbox.get_selected_row()
        child = row.get_child() if row is not None else None
        app = getattr(child, "_app", None)
        if app is None:
            return
        dialog.close()
        on_pick(app, make_default.get_active())

    listbox.connect("row-activated", lambda *_a: chosen())
    open_button.connect("clicked", chosen)
    dialog.present(parent)


# --------------------------------------------------------------- archives ---

def ask_archive(parent: Gtk.Widget, paths: list[str], on_done) -> None:
    """Make an archive of these. `on_done(name, format)`."""
    dialog = _dialog("Compress", 460)
    count = len(paths)
    what = (os.path.basename(paths[0].rstrip(os.sep)) if count == 1
            else f"{count} items")
    body = _page(dialog, f"Compress {what}")

    stem, _ext = ops.split_name(os.path.basename(paths[0].rstrip(os.sep)))
    entry = Gtk.Entry(text=stem if count == 1 else "archive")
    entry.add_css_class("search")
    entry.set_activates_default(True)
    body.append(entry)

    formats = archive.available_formats()
    names = [label for _key, label, _suffix in formats]
    picker = Gtk.DropDown.new_from_strings(names)
    body.append(picker)

    make = _button("Compress", "suggested-action")
    cancel = _button("Cancel")
    cancel.connect("clicked", lambda _b: dialog.close())
    body.append(_buttons(cancel, make))

    def commit(*_args):
        name = entry.get_text().strip()
        if not name:
            return
        key = formats[picker.get_selected()][0]
        dialog.close()
        on_done(name, key)

    entry.connect("activate", commit)
    make.connect("clicked", commit)
    GLib.idle_add(lambda: (entry.grab_focus(), entry.select_region(0, -1), False)[-1])
    dialog.present(parent)


def _short(path: str) -> str:
    home = os.path.expanduser("~")
    if path == home:
        return "~"
    if path.startswith(home + os.sep):
        return "~" + path[len(home):]
    return path
