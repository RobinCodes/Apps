"""Processing the inbox: one thing at a time, oldest first, one key each.

SYSTEM.md §4.2 asks for a daily pass of five minutes that ends with the inbox
empty, and five minutes is only realistic if each item is one decision and
one key. So the view is a queue rather than a folder: the oldest thing is
selected, the places it most likely belongs are listed under it with a
number each, and pressing the number files it.

| Key | |
|---|---|
| `1`–`9` | file it in that place |
| `Return` | file it in the first place listed |
| `Space` | open it, to look before deciding |
| `N` | open it in Notedown (Markdown only) |
| `Delete` | move it to the trash |
| `Ctrl+Z` | put the last one back |

Every one of those is an ordinary operation of the window's — a move, a trash
— run as a job and recorded, so `Ctrl+Z` here and in the window are the same
undo. A name that is taken where something is filed asks, as any move does.

*Suggest places with Claude* asks Claude Code where each item belongs, with
SYSTEM.md as its briefing and the places that exist as its choices. Its
answer only reorders the list: a suggestion is marked and goes first, and
nothing moves until a key is pressed for it.
"""

from __future__ import annotations

import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")

from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from . import dialogs, filetypes, ops, order, units  # noqa: E402

KEYS = {getattr(Gdk, f"KEY_{n}"): n for n in range(1, 10)}
KEYS.update({getattr(Gdk, f"KEY_KP_{n}"): n for n in range(1, 10)})


class InboxView:
    """The dialog. `host` is the window: it runs the jobs and keeps the undo."""

    def __init__(self, host, parent: Gtk.Widget):
        self.host = host
        self.parent = parent
        self.where = order.here()
        self.places = order.destinations(self.where) if self.where else []
        self.items: list[str] = []
        self.offered: list[order.Destination] = []
        # What Claude said, per item: (place or None, why).
        self.claude_says: dict[str, tuple] = {}
        self._asking = None
        self._busy = False

        self.dialog = dialogs._dialog("Inbox", 640)
        self.dialog.set_content_height(600)
        self.dialog.connect("closed", self._on_closed)
        body = dialogs._page(
            self.dialog, "Inbox",
            subtitle="Oldest first. A number files the selected one there, "
                     "Return takes the first place, Space opens it, Delete "
                     "trashes it — and Ctrl+Z puts the last one back.")
        self.count = Gtk.Label(xalign=0.0)
        self.count.add_css_class("dialog-body")
        body.append(self.count)

        self.listbox = Gtk.ListBox()
        self.listbox.add_css_class("panel")
        self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.listbox.connect("row-selected", lambda *_a: self._show_places())
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_child(self.listbox)
        scroller.set_min_content_height(170)
        scroller.set_vexpand(True)
        body.append(scroller)

        heading = Gtk.Label(label="Where it goes", xalign=0.0)
        heading.add_css_class("field-key")
        body.append(heading)
        self.choices = Gtk.FlowBox()
        self.choices.set_selection_mode(Gtk.SelectionMode.NONE)
        self.choices.set_max_children_per_line(3)
        self.choices.set_column_spacing(6)
        self.choices.set_row_spacing(6)
        self.choices.set_homogeneous(False)
        body.append(self.choices)

        self.advice = Gtk.Label(xalign=0.0)
        self.advice.add_css_class("dialog-body")
        self.advice.set_wrap(True)
        self.advice.set_visible(False)
        body.append(self.advice)

        self.status = Gtk.Label(xalign=0.0)
        self.status.add_css_class("where")
        self.status.set_wrap(True)
        body.append(self.status)

        self.claude_button = dialogs._button("Suggest places with Claude")
        self.claude_button.set_visible(order.claude_available())
        self.claude_button.connect("clicked", lambda _b: self.ask_claude())
        open_button = dialogs._button("Open")
        open_button.connect("clicked", lambda _b: self.open_selected())
        self.notedown_button = dialogs._button("Notedown")
        self.notedown_button.connect("clicked", lambda _b: self.to_notedown())
        trash = dialogs._button("Trash")
        trash.connect("clicked", lambda _b: self.trash_selected())
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        row = dialogs._buttons(self.claude_button, spacer, open_button,
                               self.notedown_button, trash)
        row.set_halign(Gtk.Align.FILL)
        body.append(row)

        # On the dialog, in the capture phase: the keys are this view's
        # whether the list or a button has the focus, and they must not
        # depend on the window's accelerators, which a host that embeds the
        # window's content does not carry over to a dialog.
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key)
        self.dialog.add_controller(keys)

        self.reload()

    def present(self) -> None:
        self.dialog.present(self.parent)
        GLib.idle_add(self._focus_list)

    def _focus_list(self) -> bool:
        row = self.listbox.get_selected_row()
        (row or self.listbox).grab_focus()
        return False

    # -- the queue ----------------------------------------------------------

    def reload(self, keep: int | None = None) -> None:
        """Read the inbox again, keeping the place in the queue."""
        if keep is None:
            row = self.listbox.get_selected_row()
            keep = row.get_index() if row is not None else 0
        self.items = order.inbox_items(self.where) if self.where else []
        while (child := self.listbox.get_first_child()) is not None:
            self.listbox.remove(child)
        for path in self.items:
            self.listbox.append(self._row(path))
        n = len(self.items)
        self.count.set_label("Nothing waiting. The inbox is empty." if not n
                             else f"{n} waiting")
        if n:
            row = self.listbox.get_row_at_index(max(0, min(keep, n - 1)))
            self.listbox.select_row(row)
        self._show_places()

    def _row(self, path: str) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        for side in ("start", "end"):
            getattr(box, f"set_margin_{side}")(10)
        box.set_margin_top(5)
        box.set_margin_bottom(5)
        is_dir = os.path.isdir(path)
        icon = Gtk.Image.new_from_gicon(filetypes.icon_for(path, is_dir))
        icon.set_pixel_size(16)
        icon.add_css_class("entry-icon")
        name = Gtk.Label(label=os.path.basename(path), xalign=0.0)
        name.set_ellipsize(3)
        name.set_hexpand(True)
        box.append(icon)
        box.append(name)
        said = self.claude_says.get(path)
        if said and said[0] is not None:
            hint = Gtk.Label(label=said[0].label.rsplit("› ", 1)[-1])
            hint.add_css_class("pill-badge")
            hint.add_css_class("accent")
            hint.set_tooltip_text(f"Claude: {said[0].label}")
            box.append(hint)
        try:
            age = units.human_time(os.lstat(path).st_mtime)
        except OSError:
            age = ""
        when = Gtk.Label(label=age)
        when.add_css_class("numeric")
        when.add_css_class("where")
        box.append(when)
        row = Gtk.ListBoxRow()
        row.set_child(box)
        row._path = path
        return row

    def selected(self) -> str:
        row = self.listbox.get_selected_row()
        return getattr(row, "_path", "") if row is not None else ""

    def places_for(self, path: str) -> list[order.Destination]:
        """The places offered for one item, the likeliest first."""
        ranked = order.suggest(path, self.places)
        said = self.claude_says.get(path)
        if said and said[0] is not None:
            ranked = [said[0]] + [p for p in ranked if p != said[0]]
        return ranked

    def _show_places(self) -> None:
        while (child := self.choices.get_first_child()) is not None:
            self.choices.remove(child)
        path = self.selected()
        self.offered = self.places_for(path) if path else []
        said = self.claude_says.get(path) if path else None
        for number, place in enumerate(self.offered, start=1):
            text = place.label if number > 9 else f"{number}  {place.label}"
            if said and said[0] == place:
                text += "  · Claude"
            button = dialogs._button(text, "suggested-action" if number == 1 else "")
            button.set_tooltip_text(place.path)
            button.connect("clicked", lambda _b, p=place: self.file_selected(p))
            self.choices.append(button)
        if not self.offered and path:
            note = Gtk.Label(label="No Studies, Documents or project folders "
                                   "exist yet to file it in.", xalign=0.0)
            note.add_css_class("dialog-body")
            self.choices.append(note)
        if said and said[1]:
            prefix = "Claude" if said[0] is not None else "Claude suggests"
            self.advice.set_label(f"{prefix}: {said[1]}")
            self.advice.set_visible(True)
        else:
            self.advice.set_visible(False)
        self.notedown_button.set_sensitive(
            bool(path) and order.is_markdown(path) and bool(order.notedown_entry()))

    # -- doing things ---------------------------------------------------------

    def file_selected(self, place: order.Destination) -> None:
        path = self.selected()
        if not path or self._busy:
            return
        self._busy = True
        name = os.path.basename(path)
        job = ops.Job(ops.MOVE, [path], place.path,
                      resolver=dialogs.ConflictAsker(self.parent))

        def finished(result) -> None:
            self._busy = False
            self.host.record(ops.Change(ops.MOVE, [path], place.path, result))
            if result.moved:
                self.status.set_label(f"{name} → {place.label}. Ctrl+Z puts it back.")
            elif result.skipped:
                self.status.set_label(f"{name} stayed: the name is taken there.")
            self.reload()

        self.host.run_job(job, f"Filing {name}", on_finished=finished)

    def trash_selected(self) -> None:
        path = self.selected()
        if not path or self._busy:
            return
        self._busy = True
        name = os.path.basename(path)
        job = ops.Job(ops.TRASH, [path])

        def finished(result) -> None:
            self._busy = False
            self.host.record(ops.Change(ops.TRASH, [path], result=result))
            if result.trashed:
                self.status.set_label(f"{name} went to the trash. Ctrl+Z puts it back.")
            self.reload()

        self.host.run_job(job, f"Moving {name} to the trash", on_finished=finished)

    def open_selected(self) -> None:
        path = self.selected()
        pane = self.host.current_pane()
        if path and pane is not None:
            pane.open_path(path)

    def to_notedown(self) -> None:
        path = self.selected()
        if not path or not order.is_markdown(path):
            return
        ok, why = order.open_in_notedown(path)
        self.status.set_label(f"Opened {os.path.basename(path)} in Notedown."
                              if ok else why)

    def undo(self, redo: bool = False) -> None:
        (self.host.redo if redo else self.host.undo)()
        self.status.set_label("Put back." if not redo else "Done again.")
        # The undo is a job of its own; the inbox monitor reloads this when
        # it lands, and this catches the instant ones.
        GLib.timeout_add(400, lambda: (self.reload(), False)[1])

    # -- Claude -----------------------------------------------------------------

    def ask_claude(self) -> None:
        if self._asking is not None or not self.items or self.where is None:
            return
        items = list(self.items)
        self.claude_button.set_sensitive(False)
        self.status.set_label("Asking Claude where these belong…")

        def done(ok, result, _session):
            GLib.idle_add(self._claude_answered, ok, result, items)

        self._asking = order.ask(order.placement_prompt(items, self.places, self.where),
                                 files=items, cwd=str(self.where.brain), on_done=done)

    def _claude_answered(self, ok: bool, result: str, items: list[str]) -> bool:
        self._asking = None
        self.claude_button.set_sensitive(True)
        if not ok:
            if result != "cancelled":
                self.status.set_label(f"Claude could not answer: {result[:200]}")
            return False
        said = order.parse_placements(result, items, self.places, self.where)
        self.claude_says.update(said)
        placed = sum(1 for place, _why in said.values() if place is not None)
        self.status.set_label(
            f"Claude suggested a place for {placed} of {len(items)}. "
            "Its suggestion is marked and listed first; nothing moves until "
            "you choose." if said else "Claude's answer had no suggestions in it.")
        self.reload()
        return False

    # -- keys -------------------------------------------------------------------

    def _on_key(self, _controller, keyval, _code, state) -> bool:
        control = bool(state & Gdk.ModifierType.CONTROL_MASK)
        if control and keyval in (Gdk.KEY_z, Gdk.KEY_Z):
            self.undo(redo=bool(state & Gdk.ModifierType.SHIFT_MASK))
            return True
        if control and keyval == Gdk.KEY_y:
            self.undo(redo=True)
            return True
        if control or state & Gdk.ModifierType.ALT_MASK:
            return False
        if keyval in KEYS:
            index = KEYS[keyval] - 1
            if index < len(self.offered):
                self.file_selected(self.offered[index])
            return True
        # Return and Space are a focused button's own keys; anywhere else
        # they are this view's.
        on_button = isinstance(self.dialog.get_focus(), Gtk.Button)
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and not on_button:
            if self.offered:
                self.file_selected(self.offered[0])
            return True
        if keyval == Gdk.KEY_space and not on_button:
            self.open_selected()
            return True
        if keyval in (Gdk.KEY_n, Gdk.KEY_N):
            self.to_notedown()
            return True
        if keyval == Gdk.KEY_Delete:
            self.trash_selected()
            return True
        return False

    def _on_closed(self, _dialog) -> None:
        if self._asking is not None:
            self._asking.cancel()
            self._asking = None
        self.host.forget_inbox_view(self)


def show(host, parent: Gtk.Widget) -> InboxView:
    view = InboxView(host, parent)
    view.present()
    return view

