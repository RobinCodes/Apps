"""The picture: a squarified treemap, drawn with Cairo.

A treemap gives every file a rectangle whose area is its size, nested inside
its folder's rectangle. Laid out naively the rectangles come out as slivers
you cannot click or read, so this uses the squarified algorithm of Bruls,
Huizing and van Wijk (2000): take the children biggest first, keep adding them
to the current row for as long as doing so improves the worst aspect ratio in
that row, then start a new row across the remaining space. The result is
mostly squares, which is what makes a treemap legible.

Two things keep it fast enough to resize smoothly.

*Recursion stops at the pixel.* There is no point laying out a 4 KB file
inside a 3-pixel rectangle. Below `MIN_AREA` a directory is drawn as one solid
block, so the cell count depends on the size of the window rather than the size
of the disk: a million files and a thousand both come out around twenty
thousand rectangles.

*The map is drawn once.* Twenty thousand Cairo rectangles is tens of
milliseconds, which is fine once and terrible on every mouse move. So the map
goes onto an ImageSurface that is only rebuilt when the window resizes or the
root changes, and hovering just blits that surface and draws one outline on
top.
"""

from __future__ import annotations

import math

import gi

gi.require_version("Gtk", "4.0")

import cairo  # noqa: E402
from gi.repository import GLib, GObject, Gtk  # noqa: E402

from . import filetypes, jobs, scan  # noqa: E402
from .units import human_size  # noqa: E402

# Below this many square pixels a directory is a block rather than a map.
MIN_AREA = 26.0
# And below this, nothing is drawn at all — it cannot be seen or hit.
MIN_SIDE = 1.2
# A folder deep enough to be all border and no content is not worth drawing.
MAX_DEPTH = 16
# The inset that makes a folder look like a container rather than a file, and
# the strip along its top that its name goes in.
#
# Both have to shrink with the box. A flat 2px border costs a 20-pixel square
# 36% of its area, and six levels of that leaves 7% of the map showing what is
# actually in it — measured at 36% coverage on a real home directory before
# these became a function of the box rather than a constant.
HEADER = 13.0


def _inset(w: float, h: float) -> float:
    """How much border a folder of this size can afford to give up."""
    short = min(w, h)
    if short < 18:
        return 0.0
    return 1.0 if short < 52 else 2.0


class Cell:
    """One rectangle on the map."""

    __slots__ = ("x", "y", "w", "h", "node", "depth", "category", "value", "group")

    def __init__(self, x, y, w, h, node, depth, category, value, group):
        self.x = x
        self.y = y
        self.w = w
        self.h = h
        self.node = node            # a scan.Dir, a scan.FileRef, or None
        self.depth = depth
        self.category = category
        self.value = value
        self.group = group          # index of the top-level branch it is in

    @property
    def is_dir(self) -> bool:
        return isinstance(self.node, scan.Dir)

    def contains(self, px: float, py: float) -> bool:
        return self.x <= px < self.x + self.w and self.y <= py < self.y + self.h


# --------------------------------------------------------------- the maths --

def squarify(values: list[float], x: float, y: float, w: float, h: float):
    """Lay `values` (already sorted, biggest first) into the given rectangle.

    Yields (index, x, y, w, h) in the order the values came in. Everything is
    in the caller's units; the total of `values` is taken to fill the whole
    rectangle exactly.
    """
    total = 0.0
    for value in values:
        total += value
    if total <= 0 or w <= 0 or h <= 0:
        return

    scale = (w * h) / total
    index = 0
    count = len(values)

    while index < count and w > 0 and h > 0:
        short = min(w, h)
        row_sum = 0.0
        row_end = index
        best = math.inf

        # Grow the row while the worst aspect ratio in it keeps improving.
        while row_end < count:
            area = values[row_end] * scale
            if area <= 0:
                row_end += 1
                continue
            trial_sum = row_sum + area
            trial_worst = _worst(values, index, row_end + 1, scale, trial_sum, short)
            if row_end > index and trial_worst > best:
                break
            row_sum = trial_sum
            best = trial_worst
            row_end += 1

        if row_end == index:        # nothing left with any area to it
            break

        thickness = row_sum / short if short else 0.0
        if thickness <= 0:
            break
        horizontal = w >= h
        offset = 0.0
        for i in range(index, row_end):
            area = values[i] * scale
            length = (area / thickness) if thickness else 0.0
            if horizontal:
                yield i, x, y + offset, thickness, length
            else:
                yield i, x + offset, y, length, thickness
            offset += length

        if horizontal:
            x += thickness
            w -= thickness
        else:
            y += thickness
            h -= thickness
        index = row_end


def _worst(values, start, end, scale, row_sum, short):
    """The worst aspect ratio in a row — the number squarify minimises."""
    if row_sum <= 0:
        return math.inf
    biggest = values[start] * scale
    smallest = values[end - 1] * scale
    if smallest <= 0:
        return math.inf
    side = short * short
    return max((side * biggest) / (row_sum * row_sum),
               (row_sum * row_sum) / (side * smallest))


# ------------------------------------------------------------- the layout ---

def build(root, width: float, height: float, basis: str = "alloc",
          free_bytes: int = 0, max_cells: int = 40000) -> list[Cell]:
    """Every rectangle for `root`, parents before their children.

    Draw order is list order, so a child painted after its parent covers it —
    and hit testing walks the list backwards to find the deepest cell first.
    """
    cells: list[Cell] = []
    if width < 2 or height < 2:
        return cells

    total = scan.value_of(root, basis) + max(0, free_bytes)
    if total <= 0:
        return cells

    if free_bytes > 0:
        # Free space shares the map with the root so that the picture is of
        # the disk rather than of the part of it that is full. It is laid out
        # alongside as a peer, which is exactly what it is.
        used = scan.value_of(root, basis)
        values = [float(used), float(free_bytes)]
        order = sorted(range(2), key=lambda i: values[i], reverse=True)
        placed = list(squarify([values[i] for i in order], 0, 0, width, height))
        for slot, x, y, w, h in placed:
            which = order[slot]
            if which == 1:
                cells.append(Cell(x, y, w, h, FREE, 0, -1, free_bytes, -1))
            else:
                _lay(root, x, y, w, h, 0, cells, basis, -1, max_cells)
        return cells

    _lay(root, 0, 0, width, height, 0, cells, basis, -1, max_cells)
    return cells


class _FreeSpace:
    """Stands in for the unused part of the disk in the map and the tooltip."""

    __slots__ = ()
    is_dir = False
    name = "Free space"
    path = ""


FREE = _FreeSpace()


def _lay(node, x, y, w, h, depth, cells, basis, group, max_cells):
    value = scan.value_of(node, basis)
    is_root = depth == 0
    if not is_root:
        cells.append(Cell(x, y, w, h, node, depth, -1, value, group))

    if len(cells) >= max_cells or depth >= MAX_DEPTH:
        return
    if w * h < MIN_AREA or w < MIN_SIDE * 2 or h < MIN_SIDE * 2:
        return

    # Inside a folder's rectangle, leave a border so the nesting is visible,
    # and a strip along the top for its name when the box is big enough.
    inset = 0.0 if is_root else _inset(w, h)
    header = HEADER if (not is_root and h > 46 and w > 70) else 0.0
    ix, iy = x + inset, y + inset + header
    iw, ih = w - 2 * inset, h - 2 * inset - header
    if iw < MIN_SIDE * 2 or ih < MIN_SIDE * 2:
        return

    children = []
    for sub in node.subdirs:
        children.append((float(scan.value_of(sub, basis)), sub))
    fvalues = node.fallocs if basis == "alloc" else node.fsizes
    for index in range(len(node.fnames)):
        children.append((float(fvalues[index]), index))
    if not children:
        return

    children.sort(key=lambda item: item[0], reverse=True)
    values = [item[0] for item in children]

    for slot, cx, cy, cw, ch in squarify(values, ix, iy, iw, ih):
        if cw < MIN_SIDE or ch < MIN_SIDE:
            # Everything after this one is smaller still; squarify hands them
            # back biggest first within a row, but rows shrink monotonically,
            # so stopping here loses only what could not be seen anyway.
            continue
        _value, what = children[slot]
        child_group = slot if is_root else group
        if isinstance(what, scan.Dir):
            _lay(what, cx, cy, cw, ch, depth + 1, cells, basis,
                 child_group, max_cells)
        else:
            ref = scan.FileRef(node, what)
            cells.append(Cell(cx, cy, cw, ch, ref, depth + 1,
                              filetypes.category_of(node.fnames[what]),
                              _value, child_group))
        if len(cells) >= max_cells:
            return


# ---------------------------------------------------------------- the view --

class TreemapView(Gtk.DrawingArea):
    """The map itself: draws it, finds what is under the pointer, reports it."""

    __gsignals__ = {
        # A cell was clicked, or the pointer moved onto one. The argument is
        # a scan.Dir, a scan.FileRef or None, passed as a plain Python object.
        "selected": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "hovered": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "activated": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "menu": (GObject.SignalFlags.RUN_FIRST, None, (object, float, float)),
    }

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_draw_func(self._draw)
        self.set_size_request(220, 160)

        self.root = None
        self.free_bytes = 0
        self._cells: list[Cell] = []
        self._grid: dict[tuple[int, int], list[Cell]] = {}
        self._cell_size = 32.0
        self._surface: cairo.ImageSurface | None = None
        self._surface_size = (0, 0)
        self._hover: Cell | None = None
        self._selected = None
        self._relayout_id = 0
        self._generation = 0
        self._dark = False

        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        motion.connect("leave", self._on_leave)
        self.add_controller(motion)

        click = Gtk.GestureClick()
        click.set_button(1)
        click.connect("pressed", self._on_click)
        self.add_controller(click)

        secondary = Gtk.GestureClick()
        secondary.set_button(3)
        secondary.connect("pressed", self._on_secondary)
        self.add_controller(secondary)

        self.set_has_tooltip(True)
        self.connect("query-tooltip", self._on_tooltip)
        self.connect("resize", lambda *_: self._schedule_relayout())

    # ------------------------------------------------------------ contents --

    def set_root(self, node, free_bytes: int = 0):
        self.root = node
        self.free_bytes = free_bytes
        self._hover = None
        self._relayout()

    def refresh(self):
        """Re-lay the map after something under it changed."""
        self._relayout()

    def set_selected(self, node):
        if node is self._selected:
            return
        self._selected = node
        self.queue_draw()

    @property
    def cells(self) -> list[Cell]:
        return self._cells

    # -------------------------------------------------------------- layout --

    def _schedule_relayout(self):
        # Resizing a window fires a stream of these; laying out once at the end
        # of it is the difference between a smooth drag and a slideshow.
        if self._relayout_id:
            GLib.source_remove(self._relayout_id)
        self._relayout_id = GLib.timeout_add(40, self._relayout_now)

    def _relayout_now(self):
        self._relayout_id = 0
        self._relayout()
        return GLib.SOURCE_REMOVE

    def _relayout(self):
        """Lay the map out and paint it, on a thread.

        A home directory is a fifth of a second of layout and about as much
        again of Cairo, which on the main loop is a visible stutter every time
        the window is dragged wider. Neither half touches GTK, so both happen
        off it and the previous picture — stretched to the new size, which
        reads as the map catching up rather than as a blank — stays on screen
        until the new one is ready.
        """
        width, height = self.get_width(), self.get_height()
        if self.root is None or width < 2 or height < 2:
            self._cells = []
            self._surface = None
            self.queue_draw()
            return

        self._generation += 1
        generation = self._generation
        root = self.root
        dark = _is_dark(self)
        self._dark = dark
        basis = self.config["size_basis"]
        mode = self.config["treemap_colour"]
        labels = self.config["treemap_labels"]
        free = self.free_bytes if self.config["show_free_space"] else 0

        def work():
            cells = build(root, width, height, basis, free)
            surface = render(cells, width, height, dark=dark, mode=mode,
                             labels=labels)
            return generation, cells, surface, (width, height)

        jobs.run(work, self._laid_out)

    def _laid_out(self, result):
        generation, cells, surface, size = result
        if generation != self._generation:
            return          # the window moved on while this was being drawn
        self._cells = cells
        self._surface = surface
        self._surface_size = size
        self._index_cells(*size)
        # A cell object is made fresh by every layout, so the one the pointer
        # was over belongs to a map that no longer exists.
        self._hover = None
        self.queue_draw()

    # ------------------------------------------------------------ pointer --

    def _on_motion(self, _controller, px, py):
        cell = self.cell_at(px, py)
        if cell is self._hover:
            return
        self._hover = cell
        self.emit("hovered", cell.node if cell else None)
        self.queue_draw()

    def _on_leave(self, _controller):
        if self._hover is not None:
            self._hover = None
            self.emit("hovered", None)
            self.queue_draw()

    def _on_click(self, gesture, n_press, px, py):
        cell = self.cell_at(px, py)
        node = cell.node if cell else None
        if node is FREE:
            node = None
        # A double click arrives as press 1 and then press 2, so the single
        # click has already selected it — the second press only zooms.
        if n_press == 2:
            self.emit("activated", node)
        else:
            self.emit("selected", node)
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    def _on_secondary(self, gesture, _n_press, px, py):
        cell = self.cell_at(px, py)
        node = cell.node if cell and cell.node is not FREE else None
        if node is None:
            return
        self.emit("selected", node)
        self.emit("menu", node, px, py)
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    def _on_tooltip(self, _widget, px, py, _keyboard, tooltip):
        cell = self.cell_at(px, py)
        if cell is None:
            return False
        if cell.node is FREE:
            tooltip.set_text(f"Free space \u2014 {human_size(cell.value)}")
            return True
        node = cell.node
        where = GLib.markup_escape_text(node.path)
        if isinstance(node, scan.Dir):
            detail = (f"{human_size(cell.value)} \u00b7 {node.nfiles:,} files "
                      f"in {node.ndirs:,} folders")
        else:
            detail = (f"{human_size(cell.value)} \u00b7 "
                      f"{filetypes.label_of(cell.category)}")
        tooltip.set_markup(f"<b>{where}</b>\n{detail}")
        return True


    def _index_cells(self, width, height):
        """A coarse grid over the map, so hit testing is not a linear scan.

        Twenty thousand rectangles times sixty mouse-move events a second is
        more comparisons than the layout itself took.
        """
        self._grid = {}
        if not self._cells:
            return
        self._cell_size = max(24.0, max(width, height) / 32.0)
        size = self._cell_size
        grid = self._grid
        for cell in self._cells:
            if cell.node is FREE:
                continue
            x0 = int(cell.x / size)
            y0 = int(cell.y / size)
            x1 = int((cell.x + cell.w) / size)
            y1 = int((cell.y + cell.h) / size)
            for gx in range(x0, x1 + 1):
                for gy in range(y0, y1 + 1):
                    grid.setdefault((gx, gy), []).append(cell)

    def cell_at(self, px: float, py: float) -> Cell | None:
        """The deepest cell under the point — the one the user means."""
        if not self._cells:
            return None
        bucket = self._grid.get((int(px / self._cell_size),
                                 int(py / self._cell_size)))
        if bucket:
            for cell in reversed(bucket):
                if cell.contains(px, py):
                    return cell
        for cell in reversed(self._cells):
            if cell.contains(px, py):
                return cell
        return None

    # ------------------------------------------------------------ drawing ---

    def _draw(self, _area, ctx, width, height):
        dark = _is_dark(self)
        if dark != self._dark and self.root is not None:
            # The user switched the system between light and dark. Every
            # colour in the surface is now the wrong one.
            self._schedule_relayout()

        if self._surface is None:
            _ground(ctx, width, height, dark)
            return

        had_width, had_height = self._surface_size
        ctx.save()
        if (had_width, had_height) != (width, height) and had_width and had_height:
            ctx.scale(width / had_width, height / had_height)
        ctx.set_source_surface(self._surface, 0, 0)
        ctx.paint()
        ctx.restore()

        # The highlights are drawn from the cell list, which belongs to the
        # surface's geometry rather than the window's. While a resize is still
        # catching up the two disagree, and an outline in the wrong place is
        # worse than none.
        if (had_width, had_height) != (width, height):
            return

        if self._selected is not None:
            for cell in self._cells:
                if cell.node is self._selected:
                    _outline(ctx, cell, (1.0, 1.0, 1.0), 2.0)
                    _outline(ctx, cell, (0.05, 0.05, 0.08), 0.8, inset=1.4)
                    break
        if self._hover is not None and self._hover.node is not self._selected:
            ctx.save()
            ctx.rectangle(self._hover.x, self._hover.y, self._hover.w, self._hover.h)
            ctx.set_source_rgba(1, 1, 1, 0.22)
            ctx.fill()
            ctx.restore()
            _outline(ctx, self._hover, (1.0, 1.0, 1.0), 1.2)


def render(cells, width, height, *, dark=True, mode="type",
           labels=True) -> cairo.ImageSurface:
    """Paint a laid-out map onto a surface of its own.

    A module function rather than a method because it touches nothing but
    Cairo, which means it can be — and is — run on a worker thread while the
    old picture stays on screen.
    """
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, max(1, int(width)),
                                 max(1, int(height)))
    ctx = cairo.Context(surface)
    _ground(ctx, width, height, dark)
    ctx.select_font_face("sans-serif", cairo.FONT_SLANT_NORMAL,
                         cairo.FONT_WEIGHT_BOLD)
    ctx.set_font_size(9.5)

    for cell in cells:
        if cell.node is FREE:
            _free_block(ctx, cell, dark)
        elif cell.is_dir:
            _folder(ctx, cell, dark, labels)
        else:
            _file(ctx, cell, mode, dark)
    return surface


def _is_dark(widget) -> bool:
    colour = widget.get_color()
    return (colour.red + colour.green + colour.blue) / 3.0 > 0.5


def _ground(ctx, width, height, dark):
    ctx.set_source_rgb(*((0.10, 0.11, 0.13) if dark else (0.93, 0.94, 0.95)))
    ctx.rectangle(0, 0, width, height)
    ctx.fill()


def _folder(ctx, cell, dark, labels):
    """A folder: a dim plate with a border, and its name if there is room."""
    depth = min(cell.depth, 8)
    shade = 0.16 + depth * 0.022 if dark else 0.86 - depth * 0.018
    ctx.rectangle(cell.x, cell.y, cell.w, cell.h)
    ctx.set_source_rgb(shade, shade + 0.012, shade + 0.03)
    ctx.fill()

    if cell.w > 3 and cell.h > 3:
        ctx.set_line_width(1.0)
        ctx.rectangle(cell.x + 0.5, cell.y + 0.5, cell.w - 1, cell.h - 1)
        ctx.set_source_rgba(*((1, 1, 1, 0.13) if dark else (0, 0, 0, 0.20)))
        ctx.stroke()

    if not labels or cell.h <= 46 or cell.w <= 70:
        return
    name = cell.node.name
    ctx.save()
    pad = _inset(cell.w, cell.h)
    ctx.rectangle(cell.x + pad, cell.y, cell.w - 2 * pad, HEADER)
    ctx.clip()
    ctx.set_source_rgba(*((1, 1, 1, 0.80) if dark else (0, 0, 0, 0.72)))
    ctx.move_to(cell.x + pad + 2, cell.y + HEADER - 3.5)
    ctx.show_text(name)
    ctx.restore()


def _file(ctx, cell, mode, dark):
    """A file: a coloured rectangle with a cushion on it.

    The cushion is a two-stop gradient rather than the per-pixel surface
    Van Wijk's paper describes — at a twentieth of the cost it still gives the
    map the bumpy, ridged look that makes the boundaries between neighbouring
    files of the same colour visible at all.
    """
    if mode == "type":
        red, green, blue = filetypes.colour_of(cell.category)
    elif mode == "folder":
        red, green, blue = _hue(cell.group * 0.147 + 0.06)
    else:
        red, green, blue = _hue(cell.depth * 0.11 + 0.02)

    if not dark:
        # The same hues, a shade deeper: a colour picked to glow on a near
        # black ground goes pale and chalky on a white one.
        red, green, blue = red * 0.90, green * 0.90, blue * 0.90

    ctx.rectangle(cell.x, cell.y, cell.w, cell.h)
    ctx.set_source_rgb(red, green, blue)
    ctx.fill()

    if cell.w < 3 or cell.h < 3:
        return

    gradient = cairo.LinearGradient(cell.x, cell.y,
                                    cell.x + cell.w, cell.y + cell.h)
    gradient.add_color_stop_rgba(0.0, 1, 1, 1, 0.26)
    gradient.add_color_stop_rgba(0.45, 1, 1, 1, 0.03)
    gradient.add_color_stop_rgba(1.0, 0, 0, 0, 0.30)
    ctx.rectangle(cell.x, cell.y, cell.w, cell.h)
    ctx.set_source(gradient)
    ctx.fill()

    if cell.w > 4 and cell.h > 4:
        ctx.set_line_width(1.0)
        ctx.rectangle(cell.x + 0.5, cell.y + 0.5, cell.w - 1, cell.h - 1)
        ctx.set_source_rgba(0, 0, 0, 0.22)
        ctx.stroke()


def _free_block(ctx, cell, dark):
    """Free space: hatched, so it never reads as a folder full of something."""
    ctx.save()
    ctx.rectangle(cell.x, cell.y, cell.w, cell.h)
    ctx.clip()
    base = 0.13 if dark else 0.80
    ctx.set_source_rgb(base, base + 0.01, base + 0.02)
    ctx.paint()

    ctx.set_line_width(1.0)
    ctx.set_source_rgba(*((1, 1, 1, 0.07) if dark else (0, 0, 0, 0.07)))
    step = 9
    start = int(cell.x - cell.h)
    for offset in range(start, int(cell.x + cell.w), step):
        ctx.move_to(offset, cell.y + cell.h)
        ctx.line_to(offset + cell.h, cell.y)
    ctx.stroke()

    if cell.w > 76 and cell.h > 26:
        ctx.select_font_face("sans-serif", cairo.FONT_SLANT_NORMAL,
                             cairo.FONT_WEIGHT_NORMAL)
        ctx.set_font_size(11)
        ctx.set_source_rgba(*((1, 1, 1, 0.50) if dark else (0, 0, 0, 0.45)))
        text = f"Free — {human_size(cell.value)}"
        extents = ctx.text_extents(text)
        ctx.move_to(cell.x + (cell.w - extents.width) / 2,
                    cell.y + (cell.h + extents.height) / 2)
        ctx.show_text(text)
    ctx.restore()


def _outline(ctx, cell, colour, width, inset=0.0):
    ctx.save()
    ctx.set_line_width(width)
    ctx.set_source_rgb(*colour)
    half = width / 2 + inset
    ctx.rectangle(cell.x + half, cell.y + half,
                  max(0.0, cell.w - 2 * half), max(0.0, cell.h - 2 * half))
    ctx.stroke()
    ctx.restore()


def _hue(position: float) -> tuple[float, float, float]:
    """A colour wheel with the muddy part of it skipped."""
    import colorsys

    return colorsys.hsv_to_rgb(position % 1.0, 0.58, 0.86)
