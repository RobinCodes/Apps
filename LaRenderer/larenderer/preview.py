"""The rendered document: a scrolling column of pages.

Pages are rasterised by poppler's pdftoppm, one subprocess per page, and only
when they are about to come into view — a hundred-page document costs the same
to open as a two-page one. Textures are cached per (page, dpi).

On recompile the existing pages keep their old textures until the new ones
arrive, so the preview never blinks white while you are typing.

Dark pages are a colour matrix on the way to the screen rather than a second
rasterisation, so turning them on costs nothing and the cache stays valid.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")

from gi.repository import Adw, Gdk, GLib, Graphene, Gtk  # noqa: E402

from . import compiler, jobs  # noqa: E402

PAGE_GAP = 14
PAGE_MARGIN = 16
SCROLL_SETTLE_MS = 70
RESIZE_SETTLE_MS = 130
PRELOAD = 1                 # pages either side of the viewport
CACHE_LIMIT = 60            # textures; a page is ~1-3 MB at 110 dpi
ZOOM_STEPS = [0.25, 0.33, 0.5, 0.67, 0.75, 0.9, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0]
MIN_DPI, MAX_DPI = 24, 600

FIT_WIDTH = "fit-width"
FIT_PAGE = "fit-page"

# How the pages are lit: with the rest of the app, or pinned one way.
THEME_AUTO = "auto"
THEME_LIGHT = "light"
THEME_DARK = "dark"
THEMES = (THEME_AUTO, THEME_LIGHT, THEME_DARK)
THEME_LABEL = {
    THEME_AUTO: "Follow the app",
    THEME_LIGHT: "Always light",
    THEME_DARK: "Always dark",
}

# Where white and black land once a page is inverted. Not 0 and 1: paper at
# #171717 rather than pure black, and text a shade under white, is easier to
# read on a lit screen than maximum contrast. 0.09 is also the sheet colour in
# widgets.py, so a page that has not rendered yet is already the colour it is
# about to be.
PAPER, INK = 0.09, 0.93

# Inverting alone would turn a blue hyperlink yellow. Inverting and then
# rotating the hue a half turn — the pair CSS dark-mode filters use — flips
# lightness and leaves hue where it was, and because both steps are linear they
# collapse into one matrix: the SVG specification's 180° hue rotation, negated.
# Rows are output channels, and each row sums to 1, which is why the constant
# term below is simply INK.
_INVERT = (
    (0.574, -1.430, -0.144),
    (-0.426, -0.430, -0.144),
    (-0.426, -1.430, 0.856),
)


def _dark_matrix() -> Graphene.Matrix:
    span = INK - PAPER
    rows = [[span * value for value in row] + [0.0] for row in _INVERT]
    rows.append([0.0, 0.0, 0.0, 1.0])          # alpha, untouched
    # graphene multiplies the colour in as a row vector, so what is written
    # above goes in transposed: column i of it drives output channel i.
    return Graphene.Matrix().init_from_float(
        [rows[column][row] for row in range(4) for column in range(4)]
    )


DARK_MATRIX = _dark_matrix()
DARK_OFFSET = Graphene.Vec4().init(INK, INK, INK, 0.0)


class PageImage(Gtk.Picture):
    """A page's pixels, inverted by the renderer when the pages are dark.

    Doing it here rather than in pdftoppm means no second rasterisation, no
    second cache, and no delay when the theme changes — the same texture is
    simply drawn through a colour matrix.
    """

    def __init__(self, dark: bool = False):
        super().__init__(can_shrink=True)
        self.dark = dark

    def set_dark(self, dark: bool):
        if dark == self.dark:
            return
        self.dark = dark
        self.queue_draw()

    def do_snapshot(self, snapshot):
        if not self.dark:
            Gtk.Picture.do_snapshot(self, snapshot)
            return
        snapshot.push_color_matrix(DARK_MATRIX, DARK_OFFSET)
        Gtk.Picture.do_snapshot(self, snapshot)
        snapshot.pop()


class Page(Gtk.Overlay):
    """One page: a blank sheet, its texture once rendered, and a synctex flash."""

    def __init__(self, index: int, dark: bool = False):
        super().__init__()
        self.index = index
        self.dpi = 0
        self.dark = dark
        self._flash = None

        self.picture = PageImage(dark=dark)
        self.picture.set_content_fit(Gtk.ContentFit.FILL)
        self.picture.add_css_class("pdf-page")
        if dark:
            self.picture.add_css_class("pdf-page-dark")
        self.set_child(self.picture)

        self.marker = Gtk.DrawingArea(can_target=False)
        self.marker.set_draw_func(self._draw_marker)
        self.add_overlay(self.marker)

        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.START)

    def set_pixel_size(self, width: int, height: int):
        self.picture.set_size_request(width, height)
        self.set_size_request(width, height)

    def set_dark(self, dark: bool):
        if dark == self.dark:
            return
        self.dark = dark
        self.picture.set_dark(dark)
        if dark:
            self.picture.add_css_class("pdf-page-dark")
        else:
            self.picture.remove_css_class("pdf-page-dark")
        self.marker.queue_draw()

    def set_texture(self, texture, dpi: int):
        self.picture.set_paintable(texture)
        self.dpi = dpi

    def flash(self, rect):
        """rect is (x, y, w, h) in this page's pixel coordinates, or None."""
        self._flash = rect
        self.marker.queue_draw()
        if rect:
            GLib.timeout_add(1600, self._clear_flash)

    def _clear_flash(self):
        self._flash = None
        self.marker.queue_draw()
        return GLib.SOURCE_REMOVE

    def _draw_marker(self, _area, cr, _w, _h, *_):
        if not self._flash:
            return
        x, y, w, h = self._flash
        # The marker is painted over the page rather than through the colour
        # matrix, so on a dark page it needs its own, brighter amber.
        if self.dark:
            cr.set_source_rgba(1.0, 0.82, 0.25, 0.26)
        else:
            cr.set_source_rgba(0.98, 0.75, 0.10, 0.34)
        cr.rectangle(x, y, w, h)
        cr.fill()
        if self.dark:
            cr.set_source_rgba(1.0, 0.78, 0.25, 0.90)
        else:
            cr.set_source_rgba(0.90, 0.55, 0.05, 0.85)
        cr.set_line_width(1.5)
        cr.rectangle(x, y, w, h)
        cr.stroke()


class Preview(Gtk.Box):
    """The preview pane: zoom controls live in the window, state lives here."""

    def __init__(self, on_page_changed=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.on_page_changed = on_page_changed

        self.pdf: str | None = None
        self.page_count = 0
        self.page_pt = (612.0, 792.0)
        self.zoom = FIT_WIDTH
        self.page_theme = THEME_AUTO
        self._dark = False
        self._pages: list[Page] = []
        self._cache: dict[tuple[int, int], Gdk.Texture] = {}
        self._order: list[tuple[int, int]] = []
        self._queue = jobs.Serial()
        self._scroll_timer = 0
        self._resize_timer = 0
        self._current = 1
        self._dpi = 96

        self.column = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=PAGE_GAP,
            margin_top=PAGE_MARGIN, margin_bottom=PAGE_MARGIN,
            margin_start=PAGE_MARGIN, margin_end=PAGE_MARGIN,
            halign=Gtk.Align.CENTER,
        )

        self.scroller = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        self.scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.scroller.set_child(self.column)
        self.scroller.add_css_class("preview-area")

        self.empty = Adw.StatusPage(
            icon_name="document-print-preview-symbolic",
            title="Nothing rendered yet",
            description="Type something, or press Ctrl+R to compile.",
            vexpand=True,
        )

        self.stack = Gtk.Stack(vexpand=True, hexpand=True)
        self.stack.add_named(self.empty, "empty")
        self.stack.add_named(self.scroller, "pages")
        self.stack.set_visible_child_name("empty")
        self.append(self.stack)

        self.scroller.get_vadjustment().connect("value-changed", self._on_scroll)
        self.scroller.connect_after("map", lambda *_: self._schedule_resize())

        self._style = Adw.StyleManager.get_default()
        self._style.connect("notify::dark", lambda *_: self._apply_page_theme())
        self._apply_page_theme()

        # Ctrl+scroll zooms. CAPTURE, so we see the event before the
        # ScrolledWindow turns it into a scroll.
        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        scroll.connect("scroll", self._on_ctrl_scroll)
        self.scroller.add_controller(scroll)

        # Fit-width follows the viewport, and GTK4 has no plain "resized"
        # signal on a widget, so the width is polled instead.
        self._last_width = 0
        GLib.timeout_add(250, self._poll_width)

    # -- loading -----------------------------------------------------------

    def show_result(self, result: compiler.Result):
        """Point the preview at a fresh compile. Keeps scroll and old pixels."""
        if not result.pdf or result.pages <= 0:
            return
        changed_geometry = (
            result.pages != self.page_count or result.page_pt != self.page_pt
        )
        self.pdf = result.pdf
        self.page_count = result.pages
        self.page_pt = result.page_pt

        self._cache.clear()
        self._order.clear()
        self._queue.bump()

        # Mark every page as needing a fresh render. Without this the "already
        # at this dpi" check in _render_visible would skip the whole document,
        # because a recompile changes the pixels but not the resolution — the
        # preview would keep showing the previous compile's pages. The textures
        # themselves stay up until the new ones land, so nothing blinks.
        for page in self._pages:
            page.dpi = 0

        if changed_geometry:
            self._rebuild()
        else:
            self._resize_pages()
        self.stack.set_visible_child_name("pages")
        self._render_visible()
        self._current = 0          # force the callback, so the strip is right
        self._update_current_page()

    def clear(self):
        self.pdf = None
        self.page_count = 0
        self._cache.clear()
        self._order.clear()
        self._queue.bump()
        while (child := self.column.get_first_child()) is not None:
            self.column.remove(child)
        self._pages.clear()
        self.stack.set_visible_child_name("empty")

    def _rebuild(self):
        while (child := self.column.get_first_child()) is not None:
            self.column.remove(child)
        self._pages = []
        for i in range(self.page_count):
            page = Page(i + 1, dark=self._dark)
            self._pages.append(page)
            self.column.append(page)
        self._resize_pages()

    # -- light or dark pages -----------------------------------------------

    def set_page_theme(self, theme: str):
        self.page_theme = theme if theme in THEMES else THEME_AUTO
        self._apply_page_theme()

    def _apply_page_theme(self):
        dark = self.page_theme == THEME_DARK or (
            self.page_theme == THEME_AUTO and self._style.get_dark()
        )
        if dark == self._dark:
            return
        self._dark = dark
        for page in self._pages:
            page.set_dark(dark)
        if dark:
            self.scroller.add_css_class("preview-area-dark")
        else:
            self.scroller.remove_css_class("preview-area-dark")

    @property
    def dark_pages(self) -> bool:
        return self._dark

    def page_theme_label(self) -> str:
        return THEME_LABEL[self.page_theme]

    # -- zoom --------------------------------------------------------------

    def _viewport_size(self) -> tuple[int, int]:
        width = self.scroller.get_width() or 700
        height = self.scroller.get_height() or 800
        return width, height

    def _compute_dpi(self) -> int:
        page_w, page_h = self.page_pt
        view_w, view_h = self._viewport_size()
        usable_w = max(120, view_w - 2 * PAGE_MARGIN - 18)   # 18 for the scrollbar
        usable_h = max(120, view_h - 2 * PAGE_MARGIN)

        if self.zoom == FIT_WIDTH:
            dpi = usable_w * 72.0 / page_w
        elif self.zoom == FIT_PAGE:
            dpi = min(usable_w * 72.0 / page_w, usable_h * 72.0 / page_h)
        else:
            dpi = 96.0 * float(self.zoom)
        return int(max(MIN_DPI, min(MAX_DPI, round(dpi))))

    def set_zoom(self, zoom):
        self.zoom = zoom
        self._resize_pages()
        self._render_visible()

    def zoom_in(self):
        current = self._effective_scale()
        for step in ZOOM_STEPS:
            if step > current + 0.001:
                self.set_zoom(step)
                return
        self.set_zoom(ZOOM_STEPS[-1])

    def zoom_out(self):
        current = self._effective_scale()
        for step in reversed(ZOOM_STEPS):
            if step < current - 0.001:
                self.set_zoom(step)
                return
        self.set_zoom(ZOOM_STEPS[0])

    def _effective_scale(self) -> float:
        if isinstance(self.zoom, str):
            return self._compute_dpi() / 96.0
        return float(self.zoom)

    def zoom_label(self) -> str:
        if self.zoom == FIT_WIDTH:
            return "Fit width"
        if self.zoom == FIT_PAGE:
            return "Fit page"
        return f"{round(float(self.zoom) * 100)}%"

    def _resize_pages(self):
        self._dpi = self._compute_dpi()
        page_w, page_h = self.page_pt
        width = int(round(page_w / 72.0 * self._dpi))
        height = int(round(page_h / 72.0 * self._dpi))
        for page in self._pages:
            page.set_pixel_size(width, height)

    def _on_ctrl_scroll(self, controller, _dx, dy):
        state = controller.get_current_event_state()
        if not (state & Gdk.ModifierType.CONTROL_MASK):
            return False
        if dy < 0:
            self.zoom_in()
        elif dy > 0:
            self.zoom_out()
        return True

    # -- what is on screen -------------------------------------------------

    def _page_height(self) -> float:
        return self.page_pt[1] / 72.0 * self._dpi

    def _visible_range(self) -> tuple[int, int]:
        if not self._pages:
            return 0, -1
        adjustment = self.scroller.get_vadjustment()
        top = adjustment.get_value()
        bottom = top + adjustment.get_page_size()
        stride = self._page_height() + PAGE_GAP
        if stride <= 0:
            return 0, len(self._pages) - 1
        first = int(max(0, (top - PAGE_MARGIN) // stride) - PRELOAD)
        last = int((bottom - PAGE_MARGIN) // stride) + PRELOAD
        return max(0, first), min(len(self._pages) - 1, max(0, last))

    def _on_scroll(self, _adjustment):
        if self._scroll_timer:
            GLib.source_remove(self._scroll_timer)
        self._scroll_timer = GLib.timeout_add(SCROLL_SETTLE_MS, self._after_scroll)
        self._update_current_page()

    def _after_scroll(self):
        self._scroll_timer = 0
        self._render_visible()
        return GLib.SOURCE_REMOVE

    def _update_current_page(self):
        if not self._pages:
            return
        adjustment = self.scroller.get_vadjustment()
        stride = self._page_height() + PAGE_GAP
        if stride <= 0:
            return
        middle = adjustment.get_value() + adjustment.get_page_size() * 0.35
        page = int((middle - PAGE_MARGIN) // stride) + 1
        page = max(1, min(self.page_count, page))
        if page != self._current:
            self._current = page
            if self.on_page_changed:
                self.on_page_changed(page, self.page_count)

    @property
    def current_page(self) -> int:
        return self._current

    def _poll_width(self):
        width = self.scroller.get_width()
        if width and width != self._last_width:
            self._last_width = width
            if isinstance(self.zoom, str):
                self._schedule_resize()
        return GLib.SOURCE_CONTINUE

    def _schedule_resize(self):
        if self._resize_timer:
            GLib.source_remove(self._resize_timer)
        self._resize_timer = GLib.timeout_add(RESIZE_SETTLE_MS, self._after_resize)

    def _after_resize(self):
        self._resize_timer = 0
        if self._pages:
            previous = self._dpi
            self._resize_pages()
            if self._dpi != previous:
                self._render_visible()
        return GLib.SOURCE_REMOVE

    # -- rendering ---------------------------------------------------------

    def _render_visible(self):
        if not self.pdf or not self._pages:
            return
        first, last = self._visible_range()
        dpi = self._dpi
        scale = self.get_scale_factor() or 1
        render_dpi = int(dpi * scale)
        generation = self._queue.generation

        for index in range(first, last + 1):
            page = self._pages[index]
            key = (index + 1, render_dpi)
            cached = self._cache.get(key)
            if cached is not None:
                if page.dpi != render_dpi:
                    page.set_texture(cached, render_dpi)
                continue
            if page.dpi == render_dpi:
                continue
            self._queue.submit(
                lambda n=index + 1, d=render_dpi: (n, d, compiler.render_page(self.pdf, n, d)),
                self._on_rendered,
                generation,
            )

    def _on_rendered(self, payload):
        number, dpi, data = payload
        if not data:
            return
        try:
            texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))
        except GLib.Error:
            return
        self._remember((number, dpi), texture)
        index = number - 1
        if 0 <= index < len(self._pages):
            self._pages[index].set_texture(texture, dpi)

    def _remember(self, key, texture):
        self._cache[key] = texture
        self._order.append(key)
        while len(self._order) > CACHE_LIMIT:
            self._cache.pop(self._order.pop(0), None)

    # -- navigation --------------------------------------------------------

    def scroll_to_page(self, number: int):
        number = max(1, min(self.page_count, number))
        stride = self._page_height() + PAGE_GAP
        adjustment = self.scroller.get_vadjustment()
        adjustment.set_value(PAGE_MARGIN + (number - 1) * stride)
        self._update_current_page()

    def next_page(self):
        self.scroll_to_page(self._current + 1)

    def previous_page(self):
        self.scroll_to_page(self._current - 1)

    def flash_position(self, page: int, x_pt: float, y_pt: float):
        """Highlight where synctex says the cursor's line landed, and go there."""
        if not (1 <= page <= len(self._pages)):
            return
        px_per_pt = self._dpi / 72.0
        page_w = self.page_pt[0] * px_per_pt
        x = max(0.0, x_pt * px_per_pt - 6)
        y = max(0.0, y_pt * px_per_pt - 14)
        width = min(page_w - x, page_w * 0.92)
        self._pages[page - 1].flash((x, y, max(60.0, width), 20.0))

        stride = self._page_height() + PAGE_GAP
        target = PAGE_MARGIN + (page - 1) * stride + y_pt * px_per_pt
        adjustment = self.scroller.get_vadjustment()
        view = adjustment.get_page_size()
        if not (adjustment.get_value() < target < adjustment.get_value() + view - 40):
            adjustment.set_value(max(0, target - view * 0.35))
        self._update_current_page()
