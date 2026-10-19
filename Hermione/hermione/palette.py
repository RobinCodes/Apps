"""One box that does everything: the command palette.

Ctrl+Shift+P, type three letters, press Return. It searches four things at
once — the app's own commands, the sidebar's places, the folders visited
recently, and what is in the folder on screen — because the question "what do
I want to do next" rarely sorts itself into those categories before it is
asked.

Matching is a fuzzy subsequence with scoring rather than a substring test, so
`dnl` finds Downloads and `nwt` finds "New tab". The scoring is the part that
makes it feel right: a match at the start of a word counts for more than one
in the middle, consecutive characters count for more than scattered ones, and
a short name beats a long one on an equal score. Typed a path instead —
anything starting with `/`, `~` or `.` — and it completes directories.

It floats over the window rather than being a popover, which is what makes
the blur behind it real: a popover on Wayland is its own surface with nothing
of ours behind it to blur.
"""

from __future__ import annotations

import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")

from gi.repository import Gdk, GLib, Gtk  # noqa: E402

MAX_ROWS = 60


class Item:
    """One thing the palette can offer."""

    def __init__(self, label: str, detail: str = "", icon: str = "",
                 shortcut: str = "", action=None, sort_hint: int = 0):
        self.label = label
        self.detail = detail
        self.icon = icon or "pan-end-symbolic"
        self.shortcut = shortcut
        self.action = action
        # Lower sorts first among equal scores: commands before places before
        # the contents of the folder, because that is the order of intent.
        self.sort_hint = sort_hint
        self.score = 0


def score(needle: str, haystack: str) -> int:
    """How well `needle` matches `haystack`. 0 means not at all.

    A plain subsequence match with three bonuses: the start of a word, a run
    of consecutive characters, and an early match. Scattered matches still
    count, which is what lets three letters find a long name.
    """
    if not needle:
        return 1
    text = haystack.lower()
    want = needle.lower()
    if want in text:
        # An outright substring always wins, and winning more the earlier and
        # the more of the name it covers.
        at = text.index(want)
        bonus = 400 if at == 0 else (200 if not text[at - 1].isalnum() else 0)
        return 1000 + bonus - at * 2 + int(60 * len(want) / max(1, len(text)))

    total = 0
    position = 0
    run = 0
    for character in want:
        found = text.find(character, position)
        if found < 0:
            return 0
        if found == position and position > 0:
            run += 1
            total += 18 + run * 6
        else:
            run = 0
            total += 10
        if found == 0 or (found > 0 and not text[found - 1].isalnum()):
            total += 25
        total -= min(found - position, 12)
        position = found + 1
    return max(1, total)


def rank(items: list[Item], needle: str, limit: int = MAX_ROWS) -> list[Item]:
    """The matching items, best first."""
    out = []
    for item in items:
        best = score(needle, item.label)
        if item.detail:
            # A match on where something is counts, but for less than a match
            # on what it is called.
            best = max(best, score(needle, item.detail) - 150)
        if best <= 0:
            continue
        item.score = best
        out.append(item)
    out.sort(key=lambda i: (-i.score, i.sort_hint, len(i.label), i.label.lower()))
    return out[:limit]


class Palette(Gtk.Box):
    """The floating box. One per window; `open()` and `close()`.

    The provider is a callable handed in by the window: it returns the list of
    items, built fresh each time it is opened so that "go to this folder"
    knows about the folder that is open now.
    """

    def __init__(self, provider, navigate=None, on_closed=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.provider = provider
        self.navigate = navigate
        self.on_closed = on_closed
        self.items: list[Item] = []
        self.shown: list[Item] = []
        self.active = 0

        self.add_css_class("palette")
        self.set_size_request(560, -1)
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.START)
        self.set_margin_top(80)
        self.set_visible(False)

        self.entry = Gtk.Entry()
        self.entry.add_css_class("palette-entry")
        self.entry.set_placeholder_text("Go to, or do…")
        self.entry.connect("changed", lambda _e: self._refresh())
        self.entry.connect("activate", lambda _e: self._run_active())
        self.append(self.entry)

        self.list = Gtk.ListBox()
        self.list.add_css_class("palette-list")
        self.list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.list.connect("row-activated", self._on_row)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_max_content_height(380)
        scroller.set_propagate_natural_height(True)
        scroller.set_child(self.list)
        self.append(scroller)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.entry.add_controller(keys)

    # -- opening and closing ----------------------------------------------

    def open(self, prefill: str = "") -> None:
        self.items = self.provider()
        self.set_visible(True)
        self.entry.set_text(prefill)
        self.entry.set_position(-1)
        self._refresh()
        GLib.idle_add(self._focus)

    def _focus(self) -> bool:
        self.entry.grab_focus()
        return False

    def close(self) -> None:
        if not self.get_visible():
            return
        self.set_visible(False)
        self.entry.set_text("")
        if self.on_closed is not None:
            self.on_closed()

    # -- the list ---------------------------------------------------------

    def _refresh(self) -> None:
        text = self.entry.get_text().strip()
        if text[:1] in ("/", "~", ".") and text:
            self.shown = _path_items(text, self.navigate)
        else:
            self.shown = rank(self.items, text)
        while (child := self.list.get_first_child()) is not None:
            self.list.remove(child)
        for item in self.shown:
            self.list.append(_row_for(item))
        self.active = 0
        self._highlight()

    def _highlight(self) -> None:
        """Mark the active row, and keep it on screen.

        The focus stays in the entry the whole time the palette is open — the
        arrow keys move a highlight, not the focus — so the row is scrolled
        to rather than focused.
        """
        row = self.list.get_row_at_index(self.active)
        if row is None:
            return
        self.list.select_row(row)
        parent = self.list.get_parent()
        if isinstance(parent, Gtk.Viewport):
            scroller = parent.get_parent()
            adjustment = (scroller.get_vadjustment()
                          if isinstance(scroller, Gtk.ScrolledWindow) else None)
            if adjustment is not None:
                ok, rect = row.compute_bounds(self.list)
                if ok:
                    _scroll_into_view(adjustment, rect.get_y(),
                                      rect.get_height())

    def _move(self, delta: int) -> None:
        if not self.shown:
            return
        self.active = max(0, min(len(self.shown) - 1, self.active + delta))
        self._highlight()

    def _on_key(self, _controller, keyval, _code, state) -> bool:
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True
        if keyval in (Gdk.KEY_Down, Gdk.KEY_Tab):
            self._move(1)
            return True
        if keyval in (Gdk.KEY_Up, Gdk.KEY_ISO_Left_Tab):
            self._move(-1)
            return True
        if keyval == Gdk.KEY_Page_Down:
            self._move(8)
            return True
        if keyval == Gdk.KEY_Page_Up:
            self._move(-8)
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self._run_active()
            return True
        # Ctrl+P / Ctrl+N for the people whose fingers expect them there.
        if state & Gdk.ModifierType.CONTROL_MASK:
            if keyval == Gdk.KEY_n:
                self._move(1)
                return True
            if keyval == Gdk.KEY_p:
                self._move(-1)
                return True
        return False

    def _on_row(self, _list, row) -> None:
        index = row.get_index()
        if 0 <= index < len(self.shown):
            self.active = index
            self._run_active()

    def _run_active(self) -> None:
        if not self.shown:
            return
        item = self.shown[min(self.active, len(self.shown) - 1)]
        self.close()
        if item.action is not None:
            item.action()


def _row_for(item: Item) -> Gtk.Widget:
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
    row.add_css_class("palette-row")
    icon = Gtk.Image.new_from_icon_name(item.icon)
    icon.set_pixel_size(16)
    icon.add_css_class("entry-icon")
    label = Gtk.Label(label=item.label, xalign=0.0)
    label.set_ellipsize(3)
    row.append(icon)
    row.append(label)
    if item.detail:
        where = Gtk.Label(label=item.detail, xalign=0.0)
        where.add_css_class("where")
        where.set_ellipsize(1)
        where.set_hexpand(True)
        row.append(where)
    else:
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        row.append(spacer)
    if item.shortcut:
        keys = Gtk.Label(label=item.shortcut)
        keys.add_css_class("shortcut")
        row.append(keys)
    wrapper = Gtk.ListBoxRow()
    wrapper.set_child(row)
    wrapper.set_activatable(True)
    return wrapper


def _scroll_into_view(adjustment, top: float, height: float) -> None:
    value = adjustment.get_value()
    page = adjustment.get_page_size()
    if top < value:
        adjustment.set_value(top)
    elif top + height > value + page:
        adjustment.set_value(top + height - page)


def _path_items(text: str, navigate=None) -> list[Item]:
    """Folder completion, for when a path is typed instead of a name."""
    expanded = os.path.expanduser(text)
    if expanded.endswith(os.sep):
        directory, fragment = expanded, ""
    else:
        directory, fragment = os.path.split(expanded)
    directory = directory or os.sep

    def go(where: str):
        return (lambda: navigate(where)) if navigate is not None else None

    items: list[Item] = []
    if os.path.isdir(expanded) and not fragment:
        items.append(Item(expanded, "open this folder", "folder-symbolic",
                          action=go(expanded)))
    try:
        with os.scandir(directory) as scanner:
            names = sorted(
                (item for item in scanner
                 if item.name.lower().startswith(fragment.lower())),
                key=lambda item: item.name.lower())
    except OSError:
        return items
    for item in names[:MAX_ROWS]:
        try:
            if not item.is_dir():
                continue
        except OSError:
            continue
        if item.name.startswith(".") and not fragment.startswith("."):
            continue
        full = os.path.join(directory, item.name)
        items.append(Item(item.name, dialogs_short(directory),
                          "folder-symbolic", action=go(full)))
    return items


def dialogs_short(path: str) -> str:
    home = os.path.expanduser("~")
    if path == home:
        return "~"
    if path.startswith(home + os.sep):
        return "~" + path[len(home):]
    return path
