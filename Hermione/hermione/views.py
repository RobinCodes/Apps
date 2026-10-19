"""The two ways of looking at a folder, over one selection.

A detail list (`Gtk.ColumnView`) and an icon grid (`Gtk.GridView`), both bound
to the same `DirModel`, so switching between them keeps the selection, the
sort and the scroll roughly where they were.

The thing worth knowing about both: a cell is built once and rebound many
times. `Gtk.SignalListItemFactory` recycles a few dozen widgets for a folder
of fifty thousand files, so `setup` makes widgets and `bind` only ever sets
values on them. Anything connected in `bind` has to be disconnected in
`unbind` or the handler count grows with every scroll — which is why every
cell keeps the handler id of the entry it is showing.

Thumbnails are asked for in `bind` and never waited for: the cell draws the
file-type icon, and if a picture turns up later the entry emits `changed` and
the cell that is showing it — if it still is — swaps it in.
"""

from __future__ import annotations

import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")

from gi.repository import Gdk, Gio, GLib, GObject, Gtk, Pango  # noqa: E402

from . import filetypes, units, vcs  # noqa: E402

# Which style class a git mark gets. Kept next to the stylesheet's own
# mapping by name rather than by colour.
VCS_CLASS = {
    vcs.MODIFIED: "modified", vcs.ADDED: "added", vcs.DELETED: "deleted",
    vcs.RENAMED: "renamed", vcs.UNTRACKED: "untracked", vcs.IGNORED: "ignored",
    vcs.CONFLICT: "conflict",
}


class Cell:
    """The widgets of one cell, and the entry currently in them.

    A small class rather than a bag of attributes on the list item, because
    every cell needs the same two things done to it — bind and unbind — and
    getting the handler bookkeeping wrong is invisible until the app has been
    scrolling for ten minutes.
    """

    def __init__(self, widget: Gtk.Widget, update):
        self.widget = widget
        self.update = update
        self.entry = None
        self.handler = 0

    def bind(self, entry) -> None:
        self.unbind()
        self.entry = entry
        self.handler = entry.connect("changed", lambda _e: self.update(entry))
        self.update(entry)

    def unbind(self) -> None:
        if self.entry is not None and self.handler:
            self.entry.disconnect(self.handler)
        self.entry = None
        self.handler = 0


def _icon_image(size: int = 16) -> Gtk.Image:
    image = Gtk.Image()
    image.set_pixel_size(size)
    image.add_css_class("entry-icon")
    return image


def _apply_icon(image: Gtk.Image, entry, colour: bool = True) -> None:
    """Put the right icon, in the right colour, on an existing image widget.

    Idempotent on purpose, and that is not a nicety. Every one of these calls
    is made from `bind`, and setting a style class invalidates the widget's
    style, which queues a resize, which relays the list out, which binds the
    cells again. Removing and re-adding the same class on every bind is
    therefore an infinite loop that ends in a `RecursionError` rather than on
    screen. So nothing is touched unless it is actually different.
    """
    wanted = f"cat-{entry.category}" if colour else ""
    current = getattr(image, "_cat", None)
    if current != wanted:
        if current:
            image.remove_css_class(current)
        if wanted:
            image.add_css_class(wanted)
        image._cat = wanted

    icon = filetypes.icon_name_for(entry.path, entry.is_dir)
    if getattr(image, "_icon_name", None) != icon:
        image.set_from_gicon(filetypes.icon_for(entry.path, entry.is_dir))
        image._icon_name = icon

    if entry.broken != bool(getattr(image, "_broken", False)):
        if entry.broken:
            image.add_css_class("is-broken")
        else:
            image.remove_css_class("is-broken")
        image._broken = entry.broken


class FileView(GObject.Object):
    """Both views, and the one API the pane uses to drive them.

    Not a widget itself: it hands out `.stack`, which holds the two scrolled
    windows, and everything else goes through methods. That keeps the pane
    from having to know which view is showing.
    """

    __gsignals__ = {
        "activated": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "context": (GObject.SignalFlags.RUN_FIRST, None, (object, float, float)),
        "selection-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        # (paths, is_move, target entry or None)
        "dropped": (GObject.SignalFlags.RUN_FIRST, None, (object, bool, object)),
        "drag-started": (GObject.SignalFlags.RUN_FIRST, None, ()),
        # Something wants a rename started on this entry.
        "rename-asked": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
    }

    def __init__(self, model, config, thumbnailer):
        super().__init__()
        self.model = model
        self.config = config
        self.thumbs = thumbnailer
        self._typeahead = ""
        self._typeahead_at = 0.0

        self.column_view = self._build_columns()
        self.grid_view = self._build_grid()

        self.list_scroller = _scroller(self.column_view)
        self.grid_scroller = _scroller(self.grid_view)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_transition_duration(140)
        self.stack.add_named(self.list_scroller, "list")
        self.stack.add_named(self.grid_scroller, "grid")
        self.stack.set_vexpand(True)

        model.selection.connect("selection-changed",
                                lambda *_: self.emit("selection-changed"))
        thumbnailer.set_callback(self._thumb_arrived)
        self.set_mode(config["view_mode"])

    # -- which view -------------------------------------------------------

    @property
    def mode(self) -> str:
        return self.stack.get_visible_child_name() or "list"

    def set_mode(self, mode: str) -> None:
        name = "grid" if mode == "grid" else "list"
        self.stack.set_visible_child_name(name)
        self.thumbs.set_size(max(128, int(self.config["icon_size"]) * 2)
                             if name == "grid" else 128)

    @property
    def widget(self) -> Gtk.Widget:
        return self.grid_view if self.mode == "grid" else self.column_view

    def grab_focus(self) -> None:
        self.widget.grab_focus()

    # -- the detail list --------------------------------------------------

    def _build_columns(self) -> Gtk.ColumnView:
        view = Gtk.ColumnView()
        # Set before the columns are made: the factories and apply_sort below
        # reach back through self.column_view.
        self.column_view = view
        view.set_show_row_separators(False)
        view.set_show_column_separators(False)
        view.set_enable_rubberband(True)
        view.set_reorderable(False)
        view.set_vexpand(True)
        view.add_css_class("data-table")

        name = Gtk.ColumnViewColumn.new("Name", self._name_factory())
        name.set_expand(True)
        name.set_resizable(True)
        name.set_sorter(Gtk.CustomSorter.new(_by_name))
        view.append_column(name)
        self._columns = {"name": name}

        for key, title, factory, sorter, width in (
            ("size", "Size", self._size_factory(), _by_size, 96),
            ("type", "Type", self._type_factory(), _by_type, 140),
            ("modified", "Modified", self._time_factory(), _by_time, 132),
            ("permissions", "Access", self._mode_factory(), _by_mode, 104),
            ("owner", "Owner", self._owner_factory(), _by_owner, 110),
        ):
            column = Gtk.ColumnViewColumn.new(title, factory)
            column.set_resizable(True)
            column.set_fixed_width(width)
            column.set_sorter(Gtk.CustomSorter.new(sorter))
            view.append_column(column)
            self._columns[key] = column

        self.model.add_column_sorter(view.get_sorter())
        view.set_model(self.model.selection)
        self.apply_columns()
        self.apply_sort()

        view.connect("activate", self._on_activate_row)
        self._add_controllers(view)
        return view

    def apply_columns(self) -> None:
        """Show the columns the settings ask for."""
        for key, column in self._columns.items():
            if key == "name":
                continue
            column.set_visible(bool(self.config["columns"].get(key, False)))

    def apply_sort(self) -> None:
        """Point the column view's sorter at the setting, without a loop.

        `sort_by_column` emits nothing that lands back here, so the only
        direction that needs care is the other one — a header click, which
        writes the setting in `_remember_sort`.
        """
        key = self.config["sort_key"]
        column = self._columns.get(key) or self._columns["name"]
        direction = (Gtk.SortType.DESCENDING if self.config["sort_reverse"]
                     else Gtk.SortType.ASCENDING)
        self.column_view.sort_by_column(column, direction)

    def watch_sort(self, on_changed) -> None:
        """Call `on_changed(key, reverse)` whenever a header is clicked.

        `Gtk.ColumnViewSorter` says which column is sorting and which way,
        so the setting can follow the view without the two chasing each
        other: `apply_sort` writes to the view and emits nothing that lands
        back here.
        """
        sorter = self.column_view.get_sorter()
        if sorter is None:
            return
        sorter.connect("changed", lambda *_a: on_changed(*self.current_sort()))

    def current_sort(self) -> tuple[str, bool]:
        """Which key and direction the view is sorting by now."""
        sorter = self.column_view.get_sorter()
        if not isinstance(sorter, Gtk.ColumnViewSorter):
            return self.config["sort_key"], bool(self.config["sort_reverse"])
        column = sorter.get_primary_sort_column()
        reverse = sorter.get_primary_sort_order() == Gtk.SortType.DESCENDING
        for key, candidate in self._columns.items():
            if candidate is column:
                return key, reverse
        return "name", reverse

    # -- the cells --------------------------------------------------------

    def _name_factory(self) -> Gtk.SignalListItemFactory:
        factory = Gtk.SignalListItemFactory()

        def setup(_factory, item):
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            box.set_margin_start(4)
            dot = Gtk.Box()
            dot.add_css_class("vcs-dot")
            dot.set_valign(Gtk.Align.CENTER)
            dot.set_visible(False)
            icon = _icon_image(16)
            label = Gtk.Label(xalign=0.0)
            label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            label.add_css_class("cell-name")
            link = Gtk.Image.new_from_icon_name("emblem-symbolic-link")
            link.set_pixel_size(10)
            link.set_visible(False)
            link.add_css_class("cell-dim")
            box.append(dot)
            box.append(icon)
            box.append(label)
            box.append(link)

            def update(entry):
                label.set_label(entry.name)
                _apply_icon(icon, entry, bool(self.config["colour_icons"]))
                if link.get_visible() != entry.is_link:
                    link.set_visible(entry.is_link)
                mark = entry.vcs if self.config["git_status"] else ""
                wanted = ("vcs-" + VCS_CLASS.get(mark, "modified")) if mark else ""
                if getattr(dot, "_vcs", None) != wanted:
                    previous = getattr(dot, "_vcs", "")
                    if previous:
                        dot.remove_css_class(previous)
                    if wanted:
                        dot.add_css_class(wanted)
                        dot.set_tooltip_text(vcs.LABELS.get(mark, ""))
                    dot._vcs = wanted
                    dot.set_visible(bool(mark))
                tip = entry.link_target if entry.is_link else None
                if box.get_tooltip_text() != tip:
                    box.set_tooltip_text(tip)
                _set_class(label, "is-hidden", entry.hidden)
                _set_class(label, "is-broken", entry.broken)

            item.set_child(box)
            item._cell = Cell(box, update)
            # So a click can be turned back into a row; see _position_at.
            box._item = item

        factory.connect("setup", setup)
        factory.connect("bind", lambda _f, item: item._cell.bind(item.get_item()))
        factory.connect("unbind", lambda _f, item: item._cell.unbind())
        return factory

    def _simple_factory(self, render, classes=("numeric",), align=1.0):
        factory = Gtk.SignalListItemFactory()

        def setup(_factory, item):
            label = Gtk.Label(xalign=align)
            for name in classes:
                label.add_css_class(name)
            label.set_ellipsize(Pango.EllipsizeMode.END)

            def update(entry):
                label.set_label(render(entry))

            item.set_child(label)
            item._cell = Cell(label, update)
            label._item = item

        factory.connect("setup", setup)
        factory.connect("bind", lambda _f, item: item._cell.bind(item.get_item()))
        factory.connect("unbind", lambda _f, item: item._cell.unbind())
        return factory

    def _size_factory(self):
        def render(entry):
            if entry.is_dir:
                if entry.dir_size is not None:
                    return units.human_size(entry.dir_size, self.config.get("si_units", False))
                if entry.dir_items is not None:
                    files, folders = entry.dir_items
                    return units.human_items(files, folders)
                return "—"
            return units.human_size(entry.size, self.config.get("si_units", False))
        return self._simple_factory(render)

    def _type_factory(self):
        return self._simple_factory(lambda e: filetypes.label(e.category),
                                    classes=("cell-dim",), align=0.0)

    def _time_factory(self):
        return self._simple_factory(lambda e: units.human_time(e.mtime),
                                    classes=("numeric",), align=1.0)

    def _mode_factory(self):
        return self._simple_factory(
            lambda e: ("d" if e.is_dir else ("l" if e.is_link else "-"))
            + units.human_mode(e.mode),
            classes=("monospace", "cell-dim"), align=0.0)

    def _owner_factory(self):
        return self._simple_factory(_owner_text, classes=("cell-dim",), align=0.0)

    # -- the grid ---------------------------------------------------------

    def _build_grid(self) -> Gtk.GridView:
        factory = Gtk.SignalListItemFactory()
        tile = int(self.config["icon_size"])

        def setup(_factory, item):
            # Every size here is set once, now. Changing a size request from
            # inside `bind` asks for a relayout, the relayout rebinds the
            # cells, and the rebind changes the size again — a recursion that
            # ends in the interpreter's stack limit rather than on screen.
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            box.set_halign(Gtk.Align.CENTER)
            frame = Gtk.Box()
            frame.set_halign(Gtk.Align.CENTER)
            frame.set_valign(Gtk.Align.END)
            frame.set_size_request(tile, tile)
            picture = Gtk.Picture()
            picture.set_can_shrink(True)
            # CONTAIN, not COVER: a photograph cropped to a square tells you
            # less about itself than the whole of it does, smaller.
            picture.set_content_fit(Gtk.ContentFit.CONTAIN)
            picture.add_css_class("tile-image")
            picture.set_size_request(tile, tile)
            picture.set_visible(False)
            icon = _icon_image(int(tile * 0.62))
            frame.append(icon)
            frame.append(picture)
            label = Gtk.Label(xalign=0.5)
            label.add_css_class("tile-label")
            label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            label.set_lines(2)
            label.set_wrap(True)
            label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            label.set_justify(Gtk.Justification.CENTER)
            label.set_max_width_chars(max(9, tile // 8))
            # Pango hyphenates a word it breaks, which turns `photo-06.png`
            # into `photo-06.p-` / `ng`. A filename is not prose and must not
            # grow punctuation it does not have.
            attributes = Pango.AttrList()
            attributes.insert(Pango.attr_insert_hyphens_new(False))
            label.set_attributes(attributes)
            box.append(frame)
            box.append(label)

            def update(entry):
                label.set_label(entry.name)
                texture = (self.thumbs.texture_for(entry)
                           if self.config["show_thumbnails"] else None)
                if texture is not None:
                    picture.set_paintable(texture)
                    picture.set_visible(True)
                    icon.set_visible(False)
                else:
                    picture.set_visible(False)
                    icon.set_visible(True)
                    _apply_icon(icon, entry, bool(self.config["colour_icons"]))
                    if self.config["show_thumbnails"]:
                        self.thumbs.request(entry)
                _set_class(label, "is-hidden", entry.hidden)
                _set_class(box, "is-broken", entry.broken)

            item.set_child(box)
            item._cell = Cell(box, update)
            box._item = item

        factory.connect("setup", setup)
        factory.connect("bind", lambda _f, item: item._cell.bind(item.get_item()))
        factory.connect("unbind", lambda _f, item: item._cell.unbind())

        view = Gtk.GridView.new(self.model.selection, factory)
        view.set_enable_rubberband(True)
        view.set_max_columns(24)
        view.set_min_columns(1)
        view.set_vexpand(True)
        view.connect("activate", self._on_activate_row)
        self._add_controllers(view)
        return view

    def rebuild_grid(self) -> None:
        """Remake the grid after the tile size changed.

        The size is baked into the cells when they are built, so there is no
        way to change it other than a new factory — which is cheap, because
        there are only ever a few dozen cells.
        """
        was = self.mode
        parent = self.grid_scroller
        self.grid_view = self._build_grid()
        parent.set_child(self.grid_view)
        self.thumbs.drop_pending()
        self.set_mode(was)

    # -- interaction ------------------------------------------------------

    def _add_controllers(self, view: Gtk.Widget) -> None:
        right = Gtk.GestureClick()
        right.set_button(Gdk.BUTTON_SECONDARY)
        right.connect("pressed", self._on_right_click, view)
        view.add_controller(right)

        middle = Gtk.GestureClick()
        middle.set_button(Gdk.BUTTON_MIDDLE)
        middle.connect("pressed", self._on_middle_click, view)
        view.add_controller(middle)

        long_press = Gtk.GestureLongPress()
        long_press.set_touch_only(True)
        long_press.connect("pressed", lambda _g, x, y: self._context_at(view, x, y))
        view.add_controller(long_press)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        view.add_controller(keys)

        source = Gtk.DragSource()
        source.set_actions(Gdk.DragAction.COPY | Gdk.DragAction.MOVE)
        source.connect("prepare", self._on_drag_prepare, view)
        source.connect("drag-begin", self._on_drag_begin)
        view.add_controller(source)

        target = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY
                                    | Gdk.DragAction.MOVE)
        target.set_preload(True)
        target.connect("drop", self._on_drop, view)
        target.connect("enter", self._on_drag_enter, view)
        target.connect("motion", self._on_drag_motion, view)
        target.connect("leave", self._on_drag_leave, view)
        view.add_controller(target)

    def _on_activate_row(self, _view, position: int) -> None:
        entry = self.model.selection.get_item(position)
        if entry is not None:
            self.emit("activated", entry)

    def _on_right_click(self, gesture, _n, x, y, view) -> None:
        self._context_at(view, x, y)
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    def _context_at(self, view, x: float, y: float) -> None:
        position = self._position_at(view, x, y)
        entry = None
        if position >= 0:
            entry = self.model.selection.get_item(position)
            # Right-clicking outside the selection selects what was clicked,
            # which is what every file manager does and what stops a menu
            # acting on something off screen.
            if not self.model.selection.is_selected(position):
                self.model.selection.select_item(position, True)
        self.emit("context", entry, x, y)

    def _on_middle_click(self, _gesture, _n, x, y, view) -> None:
        position = self._position_at(view, x, y)
        if position < 0:
            return
        entry = self.model.selection.get_item(position)
        if entry is not None and entry.is_dir:
            self.emit("activated", _MiddleClick(entry))

    def _position_at(self, view, x: float, y: float) -> int:
        """Which row is under this point? -1 for the empty space below them.

        GTK offers no "row at these coordinates" for these views, so the cell
        widgets carry their own `Gtk.ListItem` — whose `position` is always
        current, including after a re-sort — and this finds the nearest one.
        Walking up from the picked widget covers a click on a cell; searching
        back down covers a click on the row's own background, where the
        picked widget is the row and the item is on a child of it.
        """
        picked = view.pick(x, y, Gtk.PickFlags.DEFAULT)
        walker = picked
        while walker is not None and walker is not view:
            item = getattr(walker, "_item", None)
            if item is not None:
                return item.get_position()
            walker = walker.get_parent()

        row = picked
        while row is not None and row.get_parent() is not None and row is not view:
            found = _find_item(row)
            if found is not None:
                return found.get_position()
            row = row.get_parent()
        return -1

    def _on_key(self, _controller, keyval, _code, state) -> bool:
        if state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK):
            return False
        if keyval == Gdk.KEY_F2:
            entries = self.model.selected_entries()
            if entries:
                self.emit("rename-asked", entries[0])
            return True
        character = Gdk.keyval_to_unicode(keyval)
        if character and chr(character).isprintable() and chr(character) != " ":
            return self._type_ahead(chr(character))
        return False

    def _type_ahead(self, character: str) -> bool:
        """Jump to the first name starting with what was typed.

        A second keystroke within a second extends the search rather than
        starting a new one, which is how `ls` users expect to find `README`
        by typing `re`.
        """
        now = GLib.get_monotonic_time() / 1_000_000
        if now - self._typeahead_at > 1.0:
            self._typeahead = ""
        self._typeahead_at = now
        self._typeahead += character.lower()

        model = self.model.selection
        count = model.get_n_items()
        for position in range(count):
            entry = model.get_item(position)
            if entry is not None and entry.name.lower().startswith(self._typeahead):
                model.select_item(position, True)
                self.scroll_to(position)
                return True
        # Nothing starts with it; try the last character on its own before
        # giving up, so a typo does not wedge the search.
        if len(self._typeahead) > 1:
            self._typeahead = character.lower()
            return self._type_ahead("")
        return True

    def scroll_to(self, position: int, select: bool = False) -> None:
        """Bring a row into view, optionally selecting it as well."""
        if position < 0:
            return
        flags = Gtk.ListScrollFlags.FOCUS
        if select:
            flags |= Gtk.ListScrollFlags.SELECT
        try:
            self.widget.scroll_to(position, flags, None)
        except (TypeError, AttributeError, OverflowError):
            pass

    # -- dragging ---------------------------------------------------------

    def _on_drag_prepare(self, _source, x, y, view):
        position = self._position_at(view, x, y)
        if position >= 0 and not self.model.selection.is_selected(position):
            self.model.selection.select_item(position, True)
        paths = self.model.selected_paths()
        if not paths:
            return None
        files = [Gio.File.new_for_path(path) for path in paths]
        return Gdk.ContentProvider.new_typed(Gdk.FileList, files)

    def _on_drag_begin(self, source, _drag) -> None:
        """Give the drag something to carry under the pointer.

        The icon theme hands back a paintable, which is all a drag source
        wants; a widget paintable of something never shown draws nothing.
        """
        self.emit("drag-started")
        entries = self.model.selected_entries()
        if not entries:
            return
        theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
        if len(entries) > 1:
            icon = Gio.ThemedIcon.new_from_names(
                ["edit-copy-symbolic", "document-send-symbolic"])
        else:
            icon = filetypes.icon_for(entries[0].path, entries[0].is_dir)
        paintable = theme.lookup_by_gicon(icon, 32, 1,
                                          Gtk.TextDirection.NONE,
                                          Gtk.IconLookupFlags.PRELOAD)
        if paintable is not None:
            source.set_icon(paintable, 16, 16)

    def _on_drag_enter(self, _target, _x, _y, view):
        view.add_css_class("drop-active")
        return Gdk.DragAction.COPY

    def _on_drag_leave(self, _target, view) -> None:
        view.remove_css_class("drop-active")

    def _on_drag_motion(self, _target, x, y, view):
        """Dropping on a folder puts things in it; elsewhere, here."""
        position = self._position_at(view, x, y)
        entry = self.model.selection.get_item(position) if position >= 0 else None
        if entry is not None and entry.is_dir:
            return Gdk.DragAction.MOVE
        return Gdk.DragAction.COPY

    def _on_drop(self, _target, value, x, y, view) -> bool:
        view.remove_css_class("drop-active")
        if not isinstance(value, Gdk.FileList):
            return False
        paths = [f.get_path() for f in value.get_files() if f.get_path()]
        if not paths:
            return False
        position = self._position_at(view, x, y)
        entry = self.model.selection.get_item(position) if position >= 0 else None
        target = entry if (entry is not None and entry.is_dir) else None
        # A drag within the same filesystem is a move by convention, between
        # them a copy; the pane decides, because it knows where "here" is.
        self.emit("dropped", paths, False, target)
        return True

    # -- thumbnails -------------------------------------------------------

    def _thumb_arrived(self, entry) -> None:
        entry.touch()


class _MiddleClick:
    """Marks an activation that came from the middle button.

    The pane opens these in a new tab. A wrapper rather than a second signal,
    because everything else about the two is identical.
    """

    def __init__(self, entry):
        self.entry = entry
        self.new_tab = True

    def __getattr__(self, name):
        return getattr(self.entry, name)


def _scroller(child: Gtk.Widget) -> Gtk.ScrolledWindow:
    scroller = Gtk.ScrolledWindow()
    scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
    scroller.set_child(child)
    scroller.set_vexpand(True)
    scroller.set_hexpand(True)
    return scroller


def _find_item(widget: Gtk.Widget, depth: int = 0):
    """The `Gtk.ListItem` a cell under `widget` belongs to, or None."""
    if depth > 4:
        return None
    item = getattr(widget, "_item", None)
    if item is not None:
        return item
    child = widget.get_first_child()
    while child is not None:
        found = _find_item(child, depth + 1)
        if found is not None:
            return found
        child = child.get_next_sibling()
    return None


def _set_class(widget: Gtk.Widget, name: str, on: bool) -> None:
    """Add or remove a class, and only when it is not already so.

    Called from `bind`; see `_apply_icon` for why that matters.
    """
    if on == widget.has_css_class(name):
        return
    if on:
        widget.add_css_class(name)
    else:
        widget.remove_css_class(name)


def _owner_text(entry) -> str:
    if entry.uid < 0:
        return ""
    try:
        import pwd

        user = pwd.getpwuid(entry.uid).pw_name
    except (ImportError, KeyError, OverflowError):
        user = str(entry.uid)
    return user


# The column sorters. Plain functions so they can be tested without a view.

def _by_name(left, right, _data=None) -> int:
    from .entries import natural_key

    a, b = natural_key(left.name), natural_key(right.name)
    return -1 if a < b else (1 if a > b else 0)


def _by_size(left, right, _data=None) -> int:
    a, b = left.sort_size, right.sort_size
    return -1 if a < b else (1 if a > b else _by_name(left, right))


def _by_time(left, right, _data=None) -> int:
    a, b = left.mtime, right.mtime
    return -1 if a < b else (1 if a > b else _by_name(left, right))


def _by_type(left, right, _data=None) -> int:
    a = (left.category, left.extension)
    b = (right.category, right.extension)
    return -1 if a < b else (1 if a > b else _by_name(left, right))


def _by_mode(left, right, _data=None) -> int:
    a, b = left.mode, right.mode
    return -1 if a < b else (1 if a > b else _by_name(left, right))


def _by_owner(left, right, _data=None) -> int:
    a, b = _owner_text(left), _owner_text(right)
    return -1 if a < b else (1 if a > b else _by_name(left, right))
