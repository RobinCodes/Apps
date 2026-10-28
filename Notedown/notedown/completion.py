"""The list that opens when `[[` is typed: the notes there are to link to.

A link is only worth typing if it arrives somewhere, and the surest way to
make it arrive is to pick the note rather than remember its exact title. So
two brackets open a short list under the cursor, narrowed by every letter
typed after them, and Enter or Tab puts the note's name in and closes the
link.

The text view keeps the keyboard the whole time. The popover never takes the
focus — it would have to give it back after every letter — so the editor
hands it the four keys a list needs (Up, Down, Enter or Tab, and Escape) and
everything else is typing, which is what it was anyway.

Where the notes come from is not this module's business: `source(query)`
returns `(title, detail, text to insert)` for each, and the window answers it
from the Order's index.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

LIMIT = 8

# What cannot be in a link's target, so typing one means the target is done:
# `#` starts a heading, `|` a label, and a bracket ends or breaks the link.
_ENDS_TARGET = set("[]|#^\n")


class LinkCompletion:
    """Notes by title, under the cursor, while a `[[` is open before it."""

    def __init__(self, editor, source):
        self.editor = editor
        self.source = source
        self.items: list[tuple[str, str, str]] = []
        self.index = 0            # the item Enter would take
        self.start = -1           # buffer offset just after the [[
        self._dismissed = -1      # the [[ that Escape was pressed in
        self._idle = 0
        self.popover: Gtk.Popover | None = None
        self.list: Gtk.ListBox | None = None

    @property
    def active(self) -> bool:
        return self.start >= 0 and bool(self.items)

    # -- following the text -------------------------------------------------

    def schedule(self):
        """Look again once the buffer has settled — one look per keystroke."""
        if not self._idle:
            self._idle = GLib.idle_add(self._look)

    def _look(self):
        self._idle = 0
        self.update()
        return GLib.SOURCE_REMOVE

    def update(self):
        """Open, narrow or close the list for where the cursor now is."""
        start, query = self._context()
        if start < 0 or start == self._dismissed:
            if start != self._dismissed:
                self._dismissed = -1
            self.close()
            return
        try:
            items = list(self.source(query))[:LIMIT]
        except Exception:  # noqa: BLE001 - a failed lookup is an empty list
            items = []
        if items != self.items:
            self.index = 0
        self.start = start
        self.items = items
        if items:
            self._show()
        else:
            self._hide()

    def _context(self) -> tuple[int, str]:
        """Where the open `[[` before the cursor is, and what follows it."""
        editor = self.editor
        if not editor.markdown:
            return -1, ""
        buf = editor.buffer
        if buf.get_selection_bounds():
            return -1, ""
        cursor = buf.get_iter_at_mark(buf.get_insert())
        line = cursor.copy()
        line.set_line_offset(0)
        before = buf.get_text(line, cursor, True)
        opening = before.rfind("[[")
        if opening < 0:
            return -1, ""
        query = before[opening + 2:]
        if len(query) > 80 or any(char in _ENDS_TARGET for char in query):
            return -1, ""
        # Inside a code span or a fence the brackets are code, not a link.
        if before[:opening].count("`") % 2 or editor.in_code_block(line):
            return -1, ""
        return line.get_offset() + opening + 2, query

    # -- the keys -----------------------------------------------------------

    def key(self, keyval, state) -> bool:
        """The editor offers every key here first; True means it was ours."""
        if not self.active:
            return False
        if state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK):
            return False
        if keyval in (Gdk.KEY_Down, Gdk.KEY_KP_Down):
            self.move(1)
            return True
        if keyval in (Gdk.KEY_Up, Gdk.KEY_KP_Up):
            self.move(-1)
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab):
            self.accept()
            return True
        if keyval == Gdk.KEY_Escape:
            self.dismiss()
            return True
        return False

    def move(self, step: int):
        if not self.items:
            return
        self.index = max(0, min(len(self.items) - 1, self.index + step))
        if self.list is not None and self.list.get_row_at_index(self.index) is not None:
            self.list.select_row(self.list.get_row_at_index(self.index))

    def accept(self, index: int | None = None):
        """Put the chosen note's name in, and close the link after it."""
        if not self.active:
            return
        index = self.index if index is None else index
        _title, _detail, text = self.items[max(0, min(index, len(self.items) - 1))]
        buf = self.editor.buffer
        cursor = buf.get_iter_at_mark(buf.get_insert())
        start = buf.get_iter_at_offset(self.start)
        offset = self.start
        buf.begin_user_action()
        buf.delete(start, cursor)
        buf.insert(buf.get_iter_at_offset(offset), text)
        after = buf.get_iter_at_offset(offset + len(text))
        ahead = after.copy()
        ahead.forward_chars(2)
        if buf.get_text(after, ahead, True) == "]]":
            buf.place_cursor(ahead)           # the link was already closed
        else:
            buf.insert(after, "]]")
        buf.end_user_action()
        self.close()

    def dismiss(self):
        """Escape: this `[[` gets no list again, until another one is typed."""
        self._dismissed = self.start
        self.close()

    def close(self):
        self.start = -1
        self.items = []
        self.index = 0
        self._hide()

    def destroy(self):
        if self._idle:
            GLib.source_remove(self._idle)
            self._idle = 0
        if self.popover is not None:
            self.popover.unparent()
            self.popover = None

    # -- the popover --------------------------------------------------------

    def _build(self):
        self.popover = Gtk.Popover(autohide=False, has_arrow=False, can_focus=False,
                                   position=Gtk.PositionType.BOTTOM)
        self.popover.add_css_class("link-completion")
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE,
                                can_focus=False, activate_on_single_click=True)
        self.list.connect("row-activated", lambda _list, row: self.accept(row.get_index()))
        self.popover.set_child(self.list)
        self.popover.set_parent(self.editor.view)

    def _show(self):
        # A popover is a surface of its own, and one needs a window under it:
        # an editor that is not on screen keeps the list, and draws nothing.
        if not self.editor.view.get_mapped():
            return
        if self.popover is None:
            self._build()
        while (row := self.list.get_first_child()) is not None:
            self.list.remove(row)
        for title, detail, _text in self.items:
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            box.append(Gtk.Label(label=title, xalign=0, max_width_chars=36,
                                 ellipsize=Pango.EllipsizeMode.END,
                                 css_classes=["link-completion-title"]))
            if detail:
                box.append(Gtk.Label(label=detail, xalign=0, max_width_chars=36,
                                     ellipsize=Pango.EllipsizeMode.START,
                                     css_classes=["link-completion-detail"]))
            self.list.append(Gtk.ListBoxRow(child=box, can_focus=False))
        self.list.select_row(self.list.get_row_at_index(self.index))

        view = self.editor.view
        buf = self.editor.buffer
        where = view.get_iter_location(buf.get_iter_at_offset(max(0, self.start - 2)))
        x, y = view.buffer_to_window_coords(Gtk.TextWindowType.WIDGET, where.x, where.y)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = x, y, 1, max(1, where.height)
        self.popover.set_pointing_to(rect)
        if not self.popover.get_visible():
            self.popover.popup()

    def _hide(self):
        if self.popover is not None and self.popover.get_visible():
            self.popover.popdown()
