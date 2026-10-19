"""The file manager, asked to pick something.

When another program says "choose a file", what it gets here is a Hermione
window — the same places, the same tabs, the same search, the same grid of
thumbnails — with a bar across the bottom asking the question. That is the
whole point of the thing: a file chooser is a file manager that has to give
an answer, and keeping two of them means learning two sets of keys for the
same job.

So `ChooserWindow` subclasses `MainWindow` rather than reimplementing a
listing, and the differences are small enough to name:

*It answers once and goes away.* `on_done` is called with an `Answer` or
with None for a cancel, exactly once, however the window is closed — the
button, Escape, the title bar's cross, or the asking program hanging up.

*It never writes the settings.* The portal runs in a process of its own
beside whatever Hermione the user already has open, and both would be
writing the same `config.json`. The chooser reads it and leaves it alone;
the session of the real window is not the chooser's to overwrite.

*Activating a file answers instead of launching it.* Double-clicking a
`.png` in a file manager opens the image viewer; in a chooser it is the
answer. `Pane.launch` asks the host which it is.

*The listing can be narrowed.* The filters a program hands over become one
`DirModel.allow` predicate and a dropdown. Folders always pass it — a
filter that hides the folders is a filter you cannot navigate past.
"""

from __future__ import annotations

import dataclasses
import fnmatch
import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")

from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from . import dialogs  # noqa: E402
from .window import MainWindow  # noqa: E402

# What the portal calls them: 0 is a shell glob, 1 is a MIME type.
GLOB, MIME = 0, 1


# ------------------------------------------------------------- the question ---

@dataclasses.dataclass
class Filter:
    """One entry in the dialog's file-type dropdown.

    A list of patterns rather than one, because "Images" is `*.png`,
    `*.jpg`, `image/*` and so on, and the program that asked is entitled to
    describe it however it likes.
    """

    name: str
    patterns: list[tuple[int, str]] = dataclasses.field(default_factory=list)

    def accepts(self, name: str, mime: str) -> bool:
        if not self.patterns:
            return True
        for kind, text in self.patterns:
            if kind == MIME:
                if _mime_matches(text, mime):
                    return True
            elif fnmatch.fnmatch(name.lower(), text.lower()):
                # Case-insensitively: a filter written `*.JPG` and a file
                # called `holiday.jpg` are the same answer to the user, and
                # no program means the distinction when it writes a glob.
                return True
        return False


@dataclasses.dataclass
class Choice:
    """An extra question the asking program wants put on the bar.

    With options it is a dropdown; with none it is a switch, which is what
    the portal means by a choice whose only values are `true` and `false`.
    """

    key: str
    label: str
    options: list[tuple[str, str]] = dataclasses.field(default_factory=list)
    initial: str = ""


@dataclasses.dataclass
class Ask:
    """Everything the portal was told, in the shape this window wants it."""

    mode: str = "open"                  # open | save | save-many
    title: str = ""
    accept_label: str = ""
    multiple: bool = False
    directory: bool = False
    folder: str = ""
    name: str = ""                      # the suggested name, when saving
    names: list[str] = dataclasses.field(default_factory=list)   # save-many
    filters: list[Filter] = dataclasses.field(default_factory=list)
    current_filter: int = -1
    choices: list[Choice] = dataclasses.field(default_factory=list)

    def default_title(self) -> str:
        if self.title:
            return self.title
        if self.mode == "save":
            return "Save as"
        if self.mode == "save-many":
            return "Save into"
        if self.directory:
            return "Choose a folder"
        return "Choose files" if self.multiple else "Choose a file"

    def default_accept(self) -> str:
        if self.accept_label:
            # The portal passes these with a mnemonic underscore in them.
            return self.accept_label.replace("_", "")
        return "Save" if self.mode.startswith("save") else "Open"


@dataclasses.dataclass
class Answer:
    """What the dialog came back with."""

    paths: list[str] = dataclasses.field(default_factory=list)
    choices: list[tuple[str, str]] = dataclasses.field(default_factory=list)
    chosen_filter: Filter | None = None

    @property
    def uris(self) -> list[str]:
        return [GLib.filename_to_uri(p, None) for p in self.paths]


def _mime_matches(pattern: str, mime: str) -> bool:
    if not mime:
        return False
    if pattern == mime:
        return True
    # `image/*` is the only wildcard the portal's callers actually use.
    if pattern.endswith("/*"):
        return mime.startswith(pattern[:-1])
    return False


def _mime_of(name: str) -> str:
    guess = Gio.content_type_guess(name, None)[0] or ""
    return Gio.content_type_get_mime_type(guess) or ""


# ---------------------------------------------------------------- the window ---

class ChooserWindow(MainWindow):
    """A Hermione window with a question along the bottom."""

    # Pane.launch asks this before handing a file to the desktop.
    picks_files = True

    def __init__(self, app, ask: Ask, on_done):
        self.ask = ask
        self._on_done = on_done
        self._answered = False
        self._closing = False
        self._choice_widgets: dict[str, Gtk.Widget] = {}

        folder = ask.folder if os.path.isdir(ask.folder) else ""
        super().__init__(app, folder)

        self.set_title(ask.default_title())
        self.modules.append(self._build_question())
        self._apply_filter()
        if ask.mode == "save":
            GLib.idle_add(self._focus_name)

    # ------------------------------------------------------------ the bar ---

    def _build_question(self) -> Gtk.Widget:
        """The fourth module: what is being asked, and the two answers.

        Across the bottom and the full width of the window, so it reads as
        the counterpart of the toolbar rather than as something stuck to the
        listing — the same card, the same alpha, the same radius.
        """
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        bar.add_css_class("chrome")
        bar.add_css_class("module")
        bar.add_css_class("question-bar")

        # What the asking program called it. It goes on the bar rather than
        # only in the window title because under a compositor that draws no
        # title there is nowhere else for it to be — and "choose a file" is
        # not the question, "choose a file to upload" is.
        if self.ask.title:
            caption = Gtk.Label(label=self.ask.title, xalign=0.0)
            caption.add_css_class("question-title")
            caption.set_ellipsize(3)
            caption.set_max_width_chars(28)
            bar.append(caption)

        if self.ask.mode == "save":
            label = Gtk.Label(label="Name")
            label.add_css_class("field-key")
            bar.append(label)
            self.name_entry = Gtk.Entry()
            self.name_entry.add_css_class("path-entry")
            self.name_entry.set_hexpand(True)
            self.name_entry.set_text(self.ask.name)
            self.name_entry.connect("activate", lambda _e: self._accept())
            bar.append(self.name_entry)
        else:
            self.name_entry = None
            self.summary = Gtk.Label(xalign=0.0)
            self.summary.set_hexpand(True)
            self.summary.set_ellipsize(3)
            self.summary.add_css_class("dialog-body")
            bar.append(self.summary)

        for choice in self.ask.choices:
            bar.append(self._build_choice(choice))

        if len(self.ask.filters) > 1:
            self.filter_drop = Gtk.DropDown.new_from_strings(
                [f.name for f in self.ask.filters])
            self.filter_drop.set_tooltip_text("Show only")
            self.filter_drop.set_selected(max(0, self.ask.current_filter))
            self.filter_drop.connect("notify::selected",
                                     lambda *_a: self._apply_filter())
            bar.append(self.filter_drop)
        else:
            self.filter_drop = None

        cancel = Gtk.Button(label="Cancel")
        cancel.add_css_class("pill-flat")
        cancel.connect("clicked", lambda _b: self._finish(None))
        bar.append(cancel)

        self.accept_button = Gtk.Button(label=self.ask.default_accept())
        self.accept_button.add_css_class("suggested-action")
        self.accept_button.connect("clicked", lambda _b: self._accept())
        bar.append(self.accept_button)

        self._refresh_question()
        return bar

    def _build_choice(self, choice: Choice) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        if choice.options:
            labels = [label for _id, label in choice.options]
            drop = Gtk.DropDown.new_from_strings(labels)
            keys = [key for key, _label in choice.options]
            if choice.initial in keys:
                drop.set_selected(keys.index(choice.initial))
            drop._keys = keys
            self._choice_widgets[choice.key] = drop
            label = Gtk.Label(label=choice.label)
            label.add_css_class("field-key")
            box.append(label)
            box.append(drop)
        else:
            switch = Gtk.CheckButton(label=choice.label)
            switch.set_active(choice.initial == "true")
            self._choice_widgets[choice.key] = switch
            box.append(switch)
        return box

    def _focus_name(self) -> bool:
        if self.name_entry is None:
            return False
        self.name_entry.grab_focus()
        # Select the stem and leave the suffix alone: renaming
        # `report.pdf` almost never means renaming the `.pdf`.
        stem = os.path.splitext(self.ask.name)[0]
        self.name_entry.select_region(0, len(stem) or -1)
        return False

    # ---------------------------------------------------------- filtering ---

    def _chosen_filter(self) -> Filter | None:
        if not self.ask.filters:
            return None
        index = (self.filter_drop.get_selected() if self.filter_drop
                 else max(0, self.ask.current_filter))
        if 0 <= index < len(self.ask.filters):
            return self.ask.filters[index]
        return None

    def _apply_filter(self) -> None:
        """Hand the current filter to every pane's model.

        Folders are always let through, whatever the filter says. A filter
        that hid them would leave the dialog unable to reach anything that
        is not in the folder it opened on, which is most things.
        """
        chosen = self._chosen_filter()
        if self.ask.directory:
            def allow(entry):
                return entry.is_dir
        elif chosen is None:
            allow = None
        else:
            def allow(entry):
                if entry.is_dir:
                    return True
                return chosen.accepts(entry.name, _mime_of(entry.name))
        for pane in self.all_panes():
            pane.model.set_allow(allow)
        self._refresh_question()

    def new_tab(self, path: str = "", focus: bool = False):
        """A tab opened later gets the filter too."""
        pane = super().new_tab(path, focus)
        if hasattr(self, "accept_button"):
            self._apply_filter()
        pane.connect("status-changed", lambda *_a: self._refresh_question())
        return pane

    # ----------------------------------------------------------- answering ---

    def _selected(self) -> list[str]:
        pane = self.current_pane()
        if pane is None:
            return []
        return pane.model.selected_paths()

    def _refresh_question(self) -> None:
        """Say what Open would take, and grey it out when it would take
        nothing."""
        if not hasattr(self, "accept_button"):
            return
        chosen = [p for p in self._selected()
                  if os.path.isdir(p) == self.ask.directory]
        if self.ask.mode == "save":
            self.accept_button.set_sensitive(True)
            return
        if self.ask.mode == "save-many":
            self.accept_button.set_sensitive(True)
            return
        if self.ask.directory:
            # With nothing picked, a folder chooser offers the folder you
            # are standing in, which is what every other one does.
            pane = self.current_pane()
            here = pane.path if pane is not None else ""
            self.summary.set_text(
                ", ".join(os.path.basename(p) or p for p in chosen)
                if chosen else (f"This folder — {_short(here)}" if here else ""))
            self.accept_button.set_sensitive(bool(chosen or here))
            return
        if not chosen:
            self.summary.set_text("Nothing picked")
        elif len(chosen) == 1:
            self.summary.set_text(os.path.basename(chosen[0]))
        else:
            self.summary.set_text(f"{len(chosen)} files")
        self.accept_button.set_sensitive(bool(chosen))

    def pick(self, path: str) -> None:
        """A file was activated — double-clicked, or Return pressed on it."""
        if self.ask.mode == "save" and self.name_entry is not None:
            self.name_entry.set_text(os.path.basename(path))
            self._accept()
            return
        if self.ask.directory:
            return
        self._answer([path])

    def _accept(self) -> None:
        pane = self.current_pane()
        if pane is None:
            return
        here = pane.path

        if self.ask.mode == "save":
            name = (self.name_entry.get_text() if self.name_entry else "").strip()
            if not name or os.sep in name or name in (".", ".."):
                self.toast("That is not a name a file can have")
                return
            target = os.path.join(here, name)
            if os.path.exists(target):
                dialogs.confirm(
                    self, f"Replace {name}?",
                    f"{_short(here)} already has a file by that name. "
                    "Saving replaces what is there.",
                    "Replace", lambda: self._answer([target]))
                return
            self._answer([target])
            return

        if self.ask.mode == "save-many":
            self._answer([os.path.join(here, n) for n in self.ask.names])
            return

        chosen = self._selected()
        folders = [p for p in chosen if os.path.isdir(p)]
        files = [p for p in chosen if not os.path.isdir(p)]

        if self.ask.directory:
            self._answer(folders or [here])
            return
        if not files and len(folders) == 1:
            # One folder picked and files are what was asked for: go into it
            # rather than refusing — which is what the double click would
            # have done anyway.
            pane.navigate(folders[0])
            return
        if not files:
            self.toast("Pick a file first")
            return
        self._answer(files if self.ask.multiple else files[:1])

    def _answer(self, paths: list[str]) -> None:
        self._finish(Answer(paths=paths,
                            choices=self._read_choices(),
                            chosen_filter=self._chosen_filter()))

    def _read_choices(self) -> list[tuple[str, str]]:
        out = []
        for choice in self.ask.choices:
            widget = self._choice_widgets.get(choice.key)
            if isinstance(widget, Gtk.DropDown):
                keys = getattr(widget, "_keys", [])
                index = widget.get_selected()
                if 0 <= index < len(keys):
                    out.append((choice.key, keys[index]))
            elif isinstance(widget, Gtk.CheckButton):
                out.append((choice.key,
                            "true" if widget.get_active() else "false"))
        return out

    def _finish(self, answer: Answer | None) -> None:
        """Answer once, whatever happens next.

        Every way out of this window ends up here — the buttons, Escape, the
        cross, and the asking program hanging up — and the portal is owed
        exactly one reply, so the second and later calls do nothing.
        """
        if self._answered:
            return
        self._answered = True
        for pane in self.all_panes():
            pane.model.shutdown()
        callback, self._on_done = self._on_done, None
        if callback is not None:
            callback(answer)
        # Unless we are already inside the close handler, where destroying
        # the window from under GTK is how a dialog crashes on its way out.
        if not self._closing:
            self.destroy()

    def close_from_portal(self) -> None:
        """The program that asked gave up on us."""
        self._finish(None)

    # ------------------------------------------- what it does not inherit ---

    def _on_close(self, _window) -> bool:
        """Close without writing anything down.

        `MainWindow` saves the session, the geometry and the last folder
        here. The portal is a second process beside whatever Hermione the
        user already has open, and both of them saving the same file means
        the one that happens to close last wins — so this one never writes.
        """
        self._closing = True
        for job in list(self.jobs):
            job.cancel()
        self._finish(None)
        return False

    def _save_session(self) -> None:
        return

    def _on_key(self, controller, keyval, code, state) -> bool:
        if keyval == Gdk.KEY_Escape and not self._something_is_open():
            self._finish(None)
            return True
        return super()._on_key(controller, keyval, code, state)

    def _something_is_open(self) -> bool:
        """Escape belongs to whatever is on top of the listing first."""
        pane = self.current_pane()
        return bool(self.palette.get_visible()
                    or (pane is not None and pane.filter_bar.get_visible())
                    or self.path_area.get_visible_child_name() == "entry")


def _short(path: str) -> str:
    home = os.path.expanduser("~")
    if path == home:
        return "Home"
    if path.startswith(home + os.sep):
        return "~" + path[len(home):]
    return path


def present(app, ask: Ask, on_done) -> ChooserWindow:
    """Build a chooser, put it on screen, and hand back the window."""
    window = ChooserWindow(app, ask, on_done)
    window.present()
    return window
