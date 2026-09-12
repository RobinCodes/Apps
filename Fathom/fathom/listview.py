"""The listed half: the tree, the biggest files, and the file types.

Three `Gtk.ColumnView`s over the same scan. The tree is the one with the
interesting machinery behind it, for two reasons.

*Rows are made when they are looked at.* A `Gtk.TreeListModel` asks for a
child model only when a row is expanded, so a scan of a million files builds
a handful of `Gio.ListStore`s rather than a million widgets. The stores it has
built are kept, so that a row whose size changes — because the user just
deleted something inside it — can be found again and told to redraw.

*Files are not objects until they are on screen.* `scan.py` keeps files in
arrays on their parent directory; a `Row` is the wrapper that makes one of
them look like an item, and only the visible ones ever exist.

Sorting is per level, which is what `Gtk.TreeListRowSorter` is for: sorting a
tree flat would put a 40 GB folder's children above its siblings.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gio, GObject, Gtk, Pango  # noqa: E402

from . import filetypes, scan  # noqa: E402
from .units import human_count, human_percent, human_size, human_time  # noqa: E402


class Row(GObject.Object):
    """One line: a directory, a file, or the free space on the disk."""

    __gsignals__ = {
        # Something under this row changed size. The cells listen so a
        # deletion updates every total above it without rebuilding the tree.
        "refreshed": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, node, parent_total: int = 0):
        super().__init__()
        self.node = node
        self.parent_total = parent_total

    @property
    def is_dir(self) -> bool:
        return isinstance(self.node, scan.Dir)

    @property
    def name(self) -> str:
        node = self.node
        if isinstance(node, scan.Dir) and node.parent is None:
            return node.name        # the root keeps its whole path
        return node.name

    @property
    def path(self) -> str:
        return self.node.path

    def value(self, basis: str) -> int:
        return scan.value_of(self.node, basis)

    def refresh(self):
        self.emit("refreshed")


class FreeRow(Row):
    """Free space, shown as a sibling of the root so the tree adds up to the disk."""

    def __init__(self, free_bytes: int, parent_total: int):
        super().__init__(None, parent_total)
        self.free = free_bytes

    @property
    def is_dir(self) -> bool:
        return False

    @property
    def name(self) -> str:
        return "Free space"

    @property
    def path(self) -> str:
        return ""

    def value(self, basis: str) -> int:
        return self.free


# ------------------------------------------------------------ the cells ----

class SizeBar(Gtk.DrawingArea):
    """A proportion, drawn. Reading a column of percentages is work; this isn't."""

    def __init__(self):
        super().__init__()
        self.fraction = 0.0
        self.set_content_width(56)
        self.set_content_height(10)
        self.set_valign(Gtk.Align.CENTER)
        self.set_draw_func(self._draw)

    def set_fraction(self, fraction: float):
        fraction = 0.0 if fraction < 0 else (1.0 if fraction > 1 else fraction)
        if abs(fraction - self.fraction) > 0.0005:
            self.fraction = fraction
            self.queue_draw()

    def _draw(self, _area, ctx, width, height):
        colour = self.get_color()
        radius = height / 2
        _rounded(ctx, 0, 0, width, height, radius)
        ctx.set_source_rgba(colour.red, colour.green, colour.blue, 0.13)
        ctx.fill()
        filled = width * self.fraction
        if filled < 1:
            return
        _rounded(ctx, 0, 0, max(filled, height), height, radius)
        # Warmer the fuller: a row that is most of its parent should look like
        # the answer to the question without the number being read.
        heat = self.fraction
        ctx.set_source_rgb(0.20 + 0.62 * heat, 0.55 - 0.22 * heat, 0.85 - 0.62 * heat)
        ctx.fill()


def _rounded(ctx, x, y, width, height, radius):
    import math

    radius = min(radius, width / 2, height / 2)
    ctx.new_sub_path()
    ctx.arc(x + width - radius, y + radius, radius, -math.pi / 2, 0)
    ctx.arc(x + width - radius, y + height - radius, radius, 0, math.pi / 2)
    ctx.arc(x + radius, y + height - radius, radius, math.pi / 2, math.pi)
    ctx.arc(x + radius, y + radius, radius, math.pi, 1.5 * math.pi)
    ctx.close_path()


class Swatch(Gtk.DrawingArea):
    """The colour this row has on the map, so the two views read as one thing."""

    def __init__(self):
        super().__init__()
        self.colour = filetypes.DIR_COLOUR
        self.folder = False
        self.set_content_width(10)
        self.set_content_height(10)
        self.set_valign(Gtk.Align.CENTER)
        self.set_draw_func(self._draw)

    def set_for(self, row: Row):
        if isinstance(row, FreeRow):
            self.colour, self.folder = (0.45, 0.48, 0.52), False
        elif row.is_dir:
            self.colour, self.folder = filetypes.DIR_COLOUR, True
        else:
            self.colour = filetypes.colour_of(filetypes.category_of(row.name))
            self.folder = False
        self.queue_draw()

    def _draw(self, _area, ctx, width, height):
        ctx.set_source_rgb(*self.colour)
        if self.folder:
            _rounded(ctx, 0.5, 1.5, width - 1, height - 3, 2)
            ctx.set_line_width(1.6)
            ctx.stroke()
        else:
            _rounded(ctx, 0.5, 0.5, width - 1, height - 1, 2.5)
            ctx.fill()


# ------------------------------------------------------------- the tree ----

class TreeView(Gtk.ScrolledWindow):
    """The folder tree, with a column per thing worth knowing about a folder."""

    __gsignals__ = {
        "selected": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "activated": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "menu": (GObject.SignalFlags.RUN_FIRST, None, (object, float, float)),
    }

    def __init__(self, config):
        super().__init__(hexpand=True, vexpand=True)
        self.config = config
        self.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

        self.root: scan.Dir | None = None
        self.free_bytes = 0
        self._stores: dict[int, Gio.ListStore] = {}
        self._rows: dict[int, Row] = {}
        self._tree_model: Gtk.TreeListModel | None = None
        self._selecting = False

        self.view = Gtk.ColumnView(hexpand=True, vexpand=True)
        self.view.add_css_class("data-table")
        self.view.set_show_row_separators(False)
        self.view.set_show_column_separators(False)
        self.view.set_reorderable(False)
        self.view.connect("activate", self._on_activate)
        self._build_columns()
        self.set_child(self.view)

        secondary = Gtk.GestureClick()
        secondary.set_button(3)
        secondary.connect("pressed", self._on_secondary)
        self.view.add_controller(secondary)

    # ----------------------------------------------------------- contents --

    def set_root(self, root: scan.Dir, free_bytes: int = 0):
        self.root = root
        self.free_bytes = free_bytes
        self._stores = {}
        self._rows = {}

        top = Gio.ListStore(item_type=Row)
        total = scan.value_of(root, self.config["size_basis"])
        if free_bytes and self.config["show_free_space"]:
            total += free_bytes
        root_row = Row(root, total)
        self._rows[id(root)] = root_row
        top.append(root_row)
        if free_bytes and self.config["show_free_space"]:
            top.append(FreeRow(free_bytes, total))

        # autoexpand is deliberately False. It expands every row as the model
        # creates it, which on a scan of a home directory means building rows
        # for all 350,000 files before the window can draw — the opposite of
        # what a lazy tree model is for. _open_hot_branch below does the one
        # useful part of it and stops.
        self._tree_model = Gtk.TreeListModel.new(
            top, False, False, self._children_of)
        row_sorter = Gtk.TreeListRowSorter(sorter=self.view.get_sorter())
        sorted_model = Gtk.SortListModel(model=self._tree_model, sorter=row_sorter)
        selection = Gtk.SingleSelection(model=sorted_model)
        selection.set_autoselect(False)
        selection.set_can_unselect(True)
        selection.connect("notify::selected", self._on_selection)
        self.view.set_model(selection)
        self.view.sort_by_column(self._size_column, Gtk.SortType.DESCENDING)
        self._open_hot_branch()

    def _open_hot_branch(self):
        """Open the root, then follow the biggest child while it dominates.

        A scan whose answer is one folder ten levels down should arrive with
        that folder already on screen. Following only children that are most
        of their parent keeps it from opening a branch where the space is
        actually spread about — there the top level is the answer.
        """
        model = self._tree_model
        if model is None or model.get_n_items() == 0:
            return
        row = model.get_row(0)
        if row is None:
            return
        row.set_expanded(True)
        if not self.config["auto_expand"]:
            return

        basis = self.config["size_basis"]
        node = self.root
        for _step in range(8):
            children = node.subdirs if node is not None else []
            if not children:
                return
            biggest = max(children, key=lambda sub: scan.value_of(sub, basis))
            parent_value = scan.value_of(node, basis)
            if not parent_value:
                return
            if scan.value_of(biggest, basis) / parent_value < 0.5:
                return
            position = self._position_of(biggest)
            if position is None:
                return
            child_row = model.get_row(position)
            if child_row is None:
                return
            child_row.set_expanded(True)
            node = biggest

    def _children_of(self, item):
        """The model for a row's children, made the first time it is opened."""
        row = item
        if not isinstance(row, Row) or not row.is_dir:
            return None
        node = row.node
        if not node.subdirs and not node.fnames:
            return None

        cached = self._stores.get(id(node))
        if cached is not None:
            return cached

        basis = self.config["size_basis"]
        total = scan.value_of(node, basis) or 1
        store = Gio.ListStore(item_type=Row)
        for sub in node.subdirs:
            child = Row(sub, total)
            self._rows[id(sub)] = child
            store.append(child)
        for index in range(len(node.fnames)):
            store.append(Row(scan.FileRef(node, index), total))
        self._stores[id(node)] = store
        return store

    def row_for(self, node) -> Row | None:
        """The live Row for a directory, if the tree has one made."""
        return self._rows.get(id(node))

    def refresh_totals(self, node):
        """Redraw the size cells of a directory and everything above it."""
        walker = node if isinstance(node, scan.Dir) else node.parent
        while walker is not None:
            row = self._rows.get(id(walker))
            if row is not None:
                row.refresh()
            walker = walker.parent

    def forget(self, node):
        """Drop the cached child models for a deleted subtree."""
        if isinstance(node, scan.Dir):
            for sub in node.walk():
                self._stores.pop(id(sub), None)
                self._rows.pop(id(sub), None)

    def store_for(self, node) -> Gio.ListStore | None:
        return self._stores.get(id(node))

    # ------------------------------------------------------------ columns --

    def _build_columns(self):
        name = Gtk.ColumnViewColumn(title="Name", factory=self._name_factory())
        name.set_expand(True)
        name.set_resizable(True)
        name.set_sorter(Gtk.CustomSorter.new(_by_name))
        self.view.append_column(name)
        self.columns = {"name": name}

        self._size_column = self._value_column(
            "Size", lambda row: human_size(row.value(self.config["size_basis"]),
                                           self.config["si_units"]),
            _by_value(self.config), key="alloc")

        self.columns["size"] = self._value_column(
            "Apparent", lambda row: ("" if isinstance(row, FreeRow)
                                     else human_size(_apparent(row),
                                                     self.config["si_units"])),
            Gtk.CustomSorter.new(_by_apparent), key="size")

        percent = Gtk.ColumnViewColumn(title="% of parent",
                                       factory=self._percent_factory())
        percent.set_resizable(True)
        percent.set_fixed_width(124)
        percent.set_sorter(_by_value(self.config))
        self.view.append_column(percent)
        self.columns["percent"] = percent

        self.columns["items"] = self._value_column(
            "Items", lambda row: (human_count(row.node.nfiles + row.node.ndirs)
                                  if row.is_dir else ""),
            Gtk.CustomSorter.new(_by_items), key="items", width=84)
        self.columns["files"] = self._value_column(
            "Files", lambda row: human_count(row.node.nfiles) if row.is_dir else "",
            Gtk.CustomSorter.new(_by_files), key="files")
        self.columns["folders"] = self._value_column(
            "Folders", lambda row: human_count(row.node.ndirs) if row.is_dir else "",
            Gtk.CustomSorter.new(_by_folders), key="folders")
        self.columns["modified"] = self._value_column(
            "Modified",
            lambda row: ("" if isinstance(row, FreeRow) else human_time(row.node.mtime)),
            Gtk.CustomSorter.new(_by_mtime), key="modified", align=0.0, width=118)

        self.apply_columns()

    def apply_columns(self):
        for key, column in self.columns.items():
            if key == "name":
                continue
            column.set_visible(bool(self.config["columns"].get(key, True)))

    def _value_column(self, title, text_of, sorter, *, key, align=1.0, width=92):
        column = Gtk.ColumnViewColumn(
            title=title, factory=_text_factory(text_of, align))
        column.set_resizable(True)
        column.set_fixed_width(width)
        column.set_sorter(sorter)
        self.view.append_column(column)
        return column

    def _name_factory(self):
        factory = Gtk.SignalListItemFactory()

        def setup(_factory, item):
            expander = Gtk.TreeExpander()
            expander.set_indent_for_icon(True)
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
            swatch = Swatch()
            label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE)
            box.append(swatch)
            box.append(label)
            expander.set_child(box)
            item.set_child(expander)
            item._swatch = swatch
            item._label = label

        def bind(_factory, item):
            tree_row = item.get_item()
            row = tree_row.get_item()
            expander = item.get_child()
            expander.set_list_row(tree_row)
            item._swatch.set_for(row)
            item._label.set_text(row.name)
            item._label.set_tooltip_text(row.path or None)
            if isinstance(row, FreeRow):
                item._label.add_css_class("dim-label")
            else:
                item._label.remove_css_class("dim-label")

        factory.connect("setup", setup)
        factory.connect("bind", bind)
        return factory

    def _percent_factory(self):
        factory = Gtk.SignalListItemFactory()

        def setup(_factory, item):
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            bar = SizeBar()
            label = Gtk.Label(xalign=1.0, width_chars=6)
            label.add_css_class("numeric")
            box.append(bar)
            box.append(label)
            item.set_child(box)
            item._bar = bar
            item._label = label

        def update(item):
            row = item.get_item().get_item()
            value = row.value(self.config["size_basis"])
            total = row.parent_total or 1
            item._bar.set_fraction(value / total)
            item._label.set_text(human_percent(value, total))

        _bind_with_refresh(factory, setup, update)
        return factory

    # -------------------------------------------------------- interaction --

    def selected_row(self) -> Row | None:
        model = self.view.get_model()
        if model is None:
            return None
        item = model.get_selected_item()
        return item.get_item() if item is not None else None

    def _on_selection(self, model, _param):
        if self._selecting:
            return
        row = self.selected_row()
        node = row.node if row is not None and not isinstance(row, FreeRow) else None
        self.emit("selected", node)

    def _on_activate(self, _view, position):
        model = self.view.get_model()
        item = model.get_item(position)
        if item is None:
            return
        row = item.get_item()
        if isinstance(row, FreeRow):
            return
        self.emit("activated", row.node)

    def _on_secondary(self, gesture, _n_press, px, py):
        row = self.selected_row()
        if row is None or isinstance(row, FreeRow):
            return
        self.emit("menu", row.node, px, py)
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    def select_node(self, node) -> bool:
        """Put the cursor on a node, opening whatever has to open to reach it.

        Returns False when the node is not in this tree — which happens when
        the treemap is showing a zoomed-in root the tree is not.
        """
        if self._tree_model is None or node is None:
            return False
        chain = []
        walker = node if isinstance(node, scan.Dir) else node.parent
        while walker is not None:
            chain.append(walker)
            walker = walker.parent
        chain.reverse()

        # Expanding a row is what makes its children exist in the flat model,
        # so this has to go down the chain in order.
        for ancestor in chain:
            position = self._position_of(ancestor)
            if position is None:
                return False
            tree_row = self._tree_model.get_row(position)
            if tree_row is not None and not tree_row.get_expanded():
                tree_row.set_expanded(True)

        position = self._position_of(node)
        if position is None:
            return False
        model = self.view.get_model()
        sorted_position = _find(model, self._tree_model.get_row(position))
        if sorted_position is None:
            return False
        self._selecting = True
        try:
            model.set_selected(sorted_position)
        finally:
            self._selecting = False
        self.view.scroll_to(sorted_position, None, Gtk.ListScrollFlags.NONE, None)
        return True

    def _position_of(self, node):
        model = self._tree_model
        if model is None:
            return None
        for position in range(model.get_n_items()):
            row = model.get_row(position)
            if row is None:
                continue
            item = row.get_item()
            if isinstance(item, Row) and item.node is node:
                return position
            if (isinstance(item, Row) and isinstance(node, scan.FileRef)
                    and isinstance(item.node, scan.FileRef)
                    and item.node == node):
                return position
        return None


def _find(model, tree_row):
    if tree_row is None:
        return None
    for position in range(model.get_n_items()):
        if model.get_item(position) is tree_row:
            return position
    return None


# -------------------------------------------------------- the flat views ---

class FilesView(Gtk.ScrolledWindow):
    """The biggest files in the scan, which is the other way to ask the question."""

    __gsignals__ = {
        "selected": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "activated": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "menu": (GObject.SignalFlags.RUN_FIRST, None, (object, float, float)),
    }

    def __init__(self, config):
        super().__init__(hexpand=True, vexpand=True)
        self.config = config
        self.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self._total = 1

        self.view = Gtk.ColumnView(hexpand=True, vexpand=True)
        self.view.add_css_class("data-table")
        self.view.connect("activate", self._on_activate)
        self.store = Gio.ListStore(item_type=Row)
        self.set_child(self.view)

        name = Gtk.ColumnViewColumn(title="File", factory=self._name_factory())
        name.set_expand(True)
        name.set_resizable(True)
        name.set_sorter(Gtk.CustomSorter.new(_by_name))
        self.view.append_column(name)

        size_column = None
        for title, text_of, width, align, sorter in (
            ("Size", lambda row: human_size(row.value(self.config["size_basis"]),
                                            self.config["si_units"]), 92, 1.0,
             _by_value(self.config)),
            ("Type", lambda row: filetypes.label_of(
                filetypes.category_of(row.name)), 100, 0.0,
             Gtk.CustomSorter.new(_by_type)),
            ("Modified", lambda row: human_time(row.node.mtime), 118, 0.0,
             Gtk.CustomSorter.new(_by_mtime)),
        ):
            column = Gtk.ColumnViewColumn(title=title,
                                          factory=_text_factory(text_of, align))
            column.set_fixed_width(width)
            column.set_resizable(True)
            column.set_sorter(sorter)
            self.view.append_column(column)
            if title == "Size":
                size_column = column

        folder = Gtk.ColumnViewColumn(
            title="Folder", factory=_text_factory(
                lambda row: _folder_of(row.node), 0.0, dim=True))
        folder.set_expand(True)
        folder.set_resizable(True)
        folder.set_sorter(Gtk.CustomSorter.new(_by_folder))
        self.view.append_column(folder)

        # The sorter has to be built after the columns, because it is the
        # column view's own and it is empty until they exist.
        sorted_model = Gtk.SortListModel(model=self.store,
                                         sorter=self.view.get_sorter())
        selection = Gtk.SingleSelection(model=sorted_model)
        selection.set_autoselect(False)
        selection.set_can_unselect(True)
        selection.connect("notify::selected", self._on_selection)
        self.view.set_model(selection)
        self.view.sort_by_column(size_column, Gtk.SortType.DESCENDING)

        secondary = Gtk.GestureClick()
        secondary.set_button(3)
        secondary.connect("pressed", self._on_secondary)
        self.view.add_controller(secondary)

    def set_files(self, refs, total: int):
        self._total = total or 1
        rows = [Row(ref, self._total) for ref in refs]
        self.store.remove_all()
        # splice in one go: appending five thousand rows one at a time emits
        # five thousand items-changed signals and the view relays out on each.
        if rows:
            self.store.splice(0, 0, rows)

    def _name_factory(self):
        factory = Gtk.SignalListItemFactory()

        def setup(_factory, item):
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
            swatch = Swatch()
            label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE)
            box.append(swatch)
            box.append(label)
            item.set_child(box)
            item._swatch = swatch
            item._label = label

        def bind(_factory, item):
            row = item.get_item()
            item._swatch.set_for(row)
            item._label.set_text(row.name)
            item._label.set_tooltip_text(row.path)

        factory.connect("setup", setup)
        factory.connect("bind", bind)
        return factory

    def selected_row(self):
        model = self.view.get_model()
        return model.get_selected_item() if model is not None else None

    def _on_selection(self, _model, _param):
        row = self.selected_row()
        self.emit("selected", row.node if row is not None else None)

    def _on_activate(self, _view, position):
        row = self.view.get_model().get_item(position)
        if row is not None:
            self.emit("activated", row.node)

    def _on_secondary(self, gesture, _n_press, px, py):
        row = self.selected_row()
        if row is None:
            return
        self.emit("menu", row.node, px, py)
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    def remove(self, ref):
        for position in range(self.store.get_n_items()):
            row = self.store.get_item(position)
            if row.node == ref:
                self.store.remove(position)
                break


class TypeRow(GObject.Object):
    def __init__(self, extension, files, size, alloc, total):
        super().__init__()
        self.extension = extension
        self.files = files
        self.size = size
        self.alloc = alloc
        self.total = total or 1

    @property
    def name(self) -> str:
        return f".{self.extension}" if self.extension else "(no extension)"

    def value(self, basis: str) -> int:
        return self.alloc if basis == "alloc" else self.size


class TypesView(Gtk.ScrolledWindow):
    """Every extension in the scan, biggest first. Answers "what kind of thing"."""

    def __init__(self, config):
        super().__init__(hexpand=True, vexpand=True)
        self.config = config
        self.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self._all: list[TypeRow] = []

        self.view = Gtk.ColumnView(hexpand=True, vexpand=True)
        self.view.add_css_class("data-table")
        self.store = Gio.ListStore(item_type=TypeRow)
        self.set_child(self.view)

        name = Gtk.ColumnViewColumn(title="Extension", factory=self._name_factory())
        name.set_fixed_width(180)
        name.set_resizable(True)
        name.set_sorter(Gtk.CustomSorter.new(_by_name))
        self.view.append_column(name)

        size_column = None
        for title, text_of, width, align, sorter in (
            ("Size", lambda row: human_size(row.value(self.config["size_basis"]),
                                            self.config["si_units"]), 92, 1.0,
             _by_value(self.config)),
            ("Files", lambda row: human_count(row.files), 84, 1.0,
             Gtk.CustomSorter.new(_by_file_count)),
            ("Kind", lambda row: filetypes.label_of(_kind_of(row)), 106, 0.0,
             Gtk.CustomSorter.new(_by_kind)),
        ):
            column = Gtk.ColumnViewColumn(title=title,
                                          factory=_text_factory(text_of, align))
            column.set_fixed_width(width)
            column.set_resizable(True)
            column.set_sorter(sorter)
            self.view.append_column(column)
            if title == "Size":
                size_column = column

        share = Gtk.ColumnViewColumn(title="Share", factory=self._share_factory())
        share.set_expand(True)
        share.set_resizable(True)
        share.set_sorter(_by_value(self.config))
        self.view.append_column(share)

        sorted_model = Gtk.SortListModel(model=self.store,
                                         sorter=self.view.get_sorter())
        self.view.set_model(Gtk.NoSelection(model=sorted_model))
        self.view.sort_by_column(size_column, Gtk.SortType.DESCENDING)

    def set_rows(self, rows, total: int):
        self._all = [TypeRow(ext, files, size, alloc, total)
                     for ext, files, size, alloc in rows]
        self.apply_filter("")

    def apply_filter(self, text: str):
        self.store.remove_all()
        rows = _matching(self._all, text, attribute="name")
        if rows:
            self.store.splice(0, 0, rows)

    def _name_factory(self):
        factory = Gtk.SignalListItemFactory()

        def setup(_factory, item):
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
            swatch = Swatch()
            label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
            box.append(swatch)
            box.append(label)
            item.set_child(box)
            item._swatch = swatch
            item._label = label

        def bind(_factory, item):
            row = item.get_item()
            item._swatch.colour = filetypes.colour_of(_kind_of(row))
            item._swatch.folder = False
            item._swatch.queue_draw()
            item._label.set_text(row.name)

        factory.connect("setup", setup)
        factory.connect("bind", bind)
        return factory

    def _share_factory(self):
        factory = Gtk.SignalListItemFactory()

        def setup(_factory, item):
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            bar = SizeBar()
            bar.set_content_width(120)
            bar.set_hexpand(True)
            label = Gtk.Label(xalign=1.0, width_chars=6)
            label.add_css_class("numeric")
            box.append(bar)
            box.append(label)
            item.set_child(box)
            item._bar = bar
            item._label = label

        def bind(_factory, item):
            row = item.get_item()
            value = row.value(self.config["size_basis"])
            item._bar.set_fraction(value / row.total)
            item._label.set_text(human_percent(value, row.total))

        factory.connect("setup", setup)
        factory.connect("bind", bind)
        return factory


# ------------------------------------------------------------- plumbing ----

def _text_factory(text_of, align, dim=False):
    factory = Gtk.SignalListItemFactory()

    def setup(_factory, item):
        label = Gtk.Label(xalign=align, ellipsize=Pango.EllipsizeMode.MIDDLE)
        label.add_css_class("numeric" if align > 0.5 else "cell-text")
        if dim:
            label.add_css_class("dim-label")
        item.set_child(label)

    def update(item):
        row = item.get_item()
        if isinstance(row, Gtk.TreeListRow):
            row = row.get_item()
        item.get_child().set_text(text_of(row))

    _bind_with_refresh(factory, setup, update)
    return factory


def _bind_with_refresh(factory, setup, update):
    """Wire a factory up so a row can ask its cells to redraw.

    GTK binds a cell once and leaves it; a value that changes afterwards —
    which is every total above something the user just deleted — would show
    the old number until the row scrolled out of view and back.
    """

    def on_bind(_factory, item):
        update(item)
        model_row = item.get_item()
        if isinstance(model_row, Gtk.TreeListRow):
            model_row = model_row.get_item()
        if isinstance(model_row, Row):
            item._refresh_id = model_row.connect(
                "refreshed", lambda *_: update(item))
            item._refresh_on = model_row

    def on_unbind(_factory, item):
        source = getattr(item, "_refresh_on", None)
        if source is not None:
            source.disconnect(item._refresh_id)
            item._refresh_on = None

    factory.connect("setup", setup)
    factory.connect("bind", on_bind)
    factory.connect("unbind", on_unbind)


def _matching(rows, text, attribute="name"):
    """Filter rows already in memory, by the same rules a search uses."""
    matches = scan.name_matcher(text)
    if matches is None:
        return list(rows)
    return [row for row in rows if matches(getattr(row, attribute))]


def _folder_of(node) -> str:
    parent = node.parent if isinstance(node, scan.FileRef) else node.parent
    return parent.path if parent is not None else ""


def _apparent(row) -> int:
    node = row.node
    return node.size if isinstance(node, scan.Dir) else node.size


def _kind_of(row) -> int:
    """Which colour category an extension row belongs to."""
    if not row.extension:
        return filetypes.OTHER
    return filetypes.category_of("x." + row.extension)


def _by_name(left, right, _data=None):
    return _cmp(left.name.lower(), right.name.lower())


def _by_type(left, right, _data=None):
    return _cmp(filetypes.label_of(filetypes.category_of(left.name)),
                filetypes.label_of(filetypes.category_of(right.name)))


def _by_kind(left, right, _data=None):
    return _cmp(filetypes.label_of(_kind_of(left)),
                filetypes.label_of(_kind_of(right)))


def _by_file_count(left, right, _data=None):
    return _cmp(left.files, right.files)


def _by_folder(left, right, _data=None):
    return _cmp(_folder_of(left.node).lower(), _folder_of(right.node).lower())


def _by_apparent(left, right, _data=None):
    return _cmp(_apparent(left), _apparent(right))


def _by_items(left, right, _data=None):
    return _cmp(_count(left), _count(right))


def _by_files(left, right, _data=None):
    return _cmp(left.node.nfiles if left.is_dir else -1,
                right.node.nfiles if right.is_dir else -1)


def _by_folders(left, right, _data=None):
    return _cmp(left.node.ndirs if left.is_dir else -1,
                right.node.ndirs if right.is_dir else -1)


def _by_mtime(left, right, _data=None):
    return _cmp(_mtime(left), _mtime(right))


def _by_value(config):
    basis = config

    def compare(left, right, _data=None):
        return _cmp(left.value(basis["size_basis"]), right.value(basis["size_basis"]))

    return Gtk.CustomSorter.new(compare)


def _count(row) -> int:
    return row.node.nfiles + row.node.ndirs if row.is_dir else -1


def _mtime(row) -> float:
    if isinstance(row, FreeRow):
        return 0.0
    return row.node.mtime


def _cmp(left, right) -> int:
    if left < right:
        return Gtk.Ordering.SMALLER
    if left > right:
        return Gtk.Ordering.LARGER
    return Gtk.Ordering.EQUAL
