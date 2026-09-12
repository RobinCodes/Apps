"""Shared UI pieces: the start page, the legend, dialogs, the stylesheet."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, GObject, Gtk  # noqa: E402

from . import filetypes  # noqa: E402
from .units import human_percent, human_size  # noqa: E402


def toast(widget, message, timeout=3):
    """Find the nearest ToastOverlay and post a message on it."""
    node = widget
    while node is not None and not isinstance(node, Adw.ToastOverlay):
        node = node.get_parent()
    if node is not None:
        node.add_toast(Adw.Toast(title=message, timeout=timeout))
    return node


def error_toast(widget, exc):
    text = str(exc).strip().splitlines()
    toast(widget, text[0] if text else "Something went wrong", timeout=6)


def confirm(parent, heading, body, action_label, callback, destructive=True):
    """Ask before anything that can't be undone. callback() runs only on yes."""
    dialog = Adw.AlertDialog(heading=heading, body=body)
    dialog.add_response("cancel", "Cancel")
    dialog.add_response("go", action_label)
    dialog.set_response_appearance(
        "go",
        Adw.ResponseAppearance.DESTRUCTIVE if destructive
        else Adw.ResponseAppearance.SUGGESTED,
    )
    dialog.set_default_response("cancel")
    dialog.set_close_response("cancel")

    def answered(dlg, result):
        if dlg.choose_finish(result) == "go":
            callback()

    dialog.choose(parent, None, answered)


def copy_text(widget, text, note="Copied"):
    display = Gdk.Display.get_default()
    if display:
        display.get_clipboard().set(text)
        toast(widget, note)


# ------------------------------------------------------------ start page ---

class VolumeRow(Gtk.Button):
    """One filesystem, with a bar showing how full it is."""

    def __init__(self, volume, on_scan):
        super().__init__()
        self.add_css_class("volume-card")
        self.volume = volume
        self.connect("clicked", lambda _b: on_scan(volume.path))

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                      margin_top=12, margin_bottom=12,
                      margin_start=14, margin_end=14)

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        title = Gtk.Label(label=volume.label, xalign=0, hexpand=True,
                          ellipsize=3, css_classes=["heading"])
        top.append(title)
        if volume.network:
            top.append(Gtk.Label(label="network", css_classes=["pill-badge"],
                                 valign=Gtk.Align.CENTER))
        box.append(top)

        meter = Meter()
        meter.set_fraction(volume.fraction)
        box.append(meter)

        if volume.total:
            detail = (f"{human_size(volume.free)} free of {human_size(volume.total)}"
                      f"  ·  {human_percent(volume.used, volume.total)} used")
        else:
            detail = volume.path
        box.append(Gtk.Label(label=detail, xalign=0,
                             css_classes=["dim-label", "caption"]))

        where = Gtk.Label(label=volume.path, xalign=0, ellipsize=3,
                          css_classes=["dim-label", "caption", "monospace"])
        box.append(where)
        self.set_child(box)


class Meter(Gtk.DrawingArea):
    """A horizontal bar. Used for how full a disk is, and for row shares."""

    def __init__(self, height=9):
        super().__init__()
        self.fraction = 0.0
        self.set_content_height(height)
        self.set_hexpand(True)
        self.set_draw_func(self._draw)

    def set_fraction(self, fraction):
        self.fraction = max(0.0, min(1.0, fraction))
        self.queue_draw()

    def _draw(self, _area, ctx, width, height):
        from .listview import _rounded

        colour = self.get_color()
        radius = height / 2
        _rounded(ctx, 0, 0, width, height, radius)
        ctx.set_source_rgba(colour.red, colour.green, colour.blue, 0.14)
        ctx.fill()
        filled = width * self.fraction
        if filled < 1:
            return
        _rounded(ctx, 0, 0, max(filled, height), height, radius)
        # Green until it matters, amber when it starts to, red when it does.
        if self.fraction < 0.75:
            ctx.set_source_rgb(0.18, 0.76, 0.49)
        elif self.fraction < 0.92:
            ctx.set_source_rgb(0.90, 0.65, 0.04)
        else:
            ctx.set_source_rgb(0.88, 0.20, 0.20)
        ctx.fill()


class StartPage(Gtk.ScrolledWindow):
    """What the window shows before anything has been scanned."""

    def __init__(self, on_scan, on_choose):
        super().__init__(hexpand=True, vexpand=True)
        self.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.on_scan = on_scan

        clamp = Adw.Clamp(maximum_size=780, tightening_threshold=600)
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14,
                           margin_top=28, margin_bottom=28,
                           margin_start=18, margin_end=18)

        heading = Gtk.Label(label="What is taking up the disk?", xalign=0,
                            css_classes=["title-1"])
        self.box.append(heading)
        self.box.append(Gtk.Label(
            label="Pick a filesystem or a folder. Nothing is changed by looking.",
            xalign=0, wrap=True, css_classes=["dim-label"]))

        self.volumes_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                                   margin_top=10)
        self.box.append(self.volumes_box)

        choose = Gtk.Button(label="Choose a folder…", halign=Gtk.Align.START,
                            css_classes=["pill"], margin_top=6)
        choose.connect("clicked", lambda _b: on_choose())
        self.box.append(choose)

        self.recent_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                                  margin_top=14)
        self.box.append(self.recent_box)

        clamp.set_child(self.box)
        self.set_child(clamp)

    def set_volumes(self, volumes):
        while (child := self.volumes_box.get_first_child()) is not None:
            self.volumes_box.remove(child)
        for volume in volumes:
            self.volumes_box.append(VolumeRow(volume, self.on_scan))

    def set_recent(self, paths):
        while (child := self.recent_box.get_first_child()) is not None:
            self.recent_box.remove(child)
        if not paths:
            return
        self.recent_box.append(Gtk.Label(label="Recent", xalign=0,
                                         css_classes=["heading", "dim-label"]))
        for path in paths[:8]:
            button = Gtk.Button(label=path, halign=Gtk.Align.START,
                                css_classes=["flat", "recent-path"])
            button.get_child().set_ellipsize(3)
            button.connect("clicked", lambda _b, p=path: self.on_scan(p))
            self.recent_box.append(button)


# ---------------------------------------------------------------- legend ---

class Legend(Gtk.FlowBox):
    """What the colours on the map mean. Only worth showing in type mode."""

    def __init__(self):
        super().__init__()
        self.set_selection_mode(Gtk.SelectionMode.NONE)
        self.set_max_children_per_line(14)
        self.set_row_spacing(2)
        self.set_column_spacing(10)
        self.add_css_class("legend")
        for index, label in enumerate(filetypes.LABELS):
            self.append(self._entry(index, label))

    def _entry(self, index, label):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        dot = Gtk.DrawingArea()
        dot.set_content_width(9)
        dot.set_content_height(9)
        dot.set_valign(Gtk.Align.CENTER)
        colour = filetypes.colour_of(index)
        dot.set_draw_func(lambda _a, ctx, w, h, c=colour: _dot(ctx, w, h, c))
        box.append(dot)
        box.append(Gtk.Label(label=label, css_classes=["caption", "dim-label"]))
        return box


def _dot(ctx, width, height, colour):
    from .listview import _rounded

    _rounded(ctx, 0.5, 0.5, width - 1, height - 1, 2.5)
    ctx.set_source_rgb(*colour)
    ctx.fill()


# ----------------------------------------------------------- breadcrumbs ---

class Breadcrumbs(Gtk.ScrolledWindow):
    """Where the map is zoomed to, and the way back out of it."""

    __gsignals__ = {
        "go": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
    }

    def __init__(self):
        super().__init__()
        self.set_policy(Gtk.PolicyType.EXTERNAL, Gtk.PolicyType.NEVER)
        self.set_hexpand(True)
        self.box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=1)
        self.box.add_css_class("breadcrumbs")
        self.set_child(self.box)

    def set_chain(self, node, root):
        while (child := self.box.get_first_child()) is not None:
            self.box.remove(child)
        if node is None:
            return
        chain = []
        walker = node
        while walker is not None:
            chain.append(walker)
            if walker is root:
                break
            walker = walker.parent
        chain.reverse()

        for index, step in enumerate(chain):
            if index:
                self.box.append(Gtk.Label(label="›", css_classes=["dim-label"]))
            label = step.name if index else _short(step.name)
            button = Gtk.Button(label=label, css_classes=["flat", "crumb"])
            button.get_child().set_ellipsize(3)
            button.set_tooltip_text(step.path)
            if step is chain[-1]:
                button.add_css_class("crumb-current")
            button.connect("clicked", lambda _b, s=step: self.emit("go", s))
            self.box.append(button)


def _short(path: str) -> str:
    import os

    home = os.path.expanduser("~")
    if path == home:
        return "~"
    if path.startswith(home + os.sep):
        return "~" + path[len(home):]
    return path


# ------------------------------------------------------------ stylesheet ---

CSS = """
.volume-card {
  padding: 0;
  border-radius: 12px;
  background: alpha(currentColor, 0.05);
  border: 1px solid alpha(currentColor, 0.10);
}
.volume-card:hover { background: alpha(currentColor, 0.09); }
.recent-path {
  padding: 2px 8px;
  font-family: monospace;
  font-size: 0.88em;
}
.pill-badge {
  padding: 1px 8px;
  border-radius: 999px;
  font-size: 0.76em;
  font-weight: bold;
  background: alpha(currentColor, 0.13);
}
.monospace { font-family: monospace; }
/* Tabular figures, so a column of sizes lines up on the decimal point. */
.numeric { font-feature-settings: "tnum"; padding-right: 8px; }
.cell-text { padding-right: 6px; }
.status-strip {
  padding: 3px 12px;
  border-top: 1px solid alpha(currentColor, 0.12);
  font-size: 0.86em;
}
.status-strip.warning { color: #e5a50a; }
.legend { padding: 5px 8px 6px 8px; }
.breadcrumbs { padding: 2px 4px; }
.crumb {
  padding: 1px 7px;
  min-height: 0;
  font-size: 0.88em;
}
.crumb-current { font-weight: bold; }
/* The map is its own thing and paints its own ground; without this the
   scrolled-window frame around it shows through at the corners. */
.treemap-frame {
  border-radius: 8px;
  border: 1px solid alpha(currentColor, 0.14);
}
.data-table { font-size: 0.94em; }
.scan-banner { padding: 4px 12px; }
/* Adwaita gives view-switcher buttons a 100px minimum each, which on its own
   sets the window's narrowest possible width at well over 500px. */
.tab-switcher > button { min-width: 0; }
"""


def install_css():
    provider = Gtk.CssProvider()
    provider.load_from_string(CSS)
    display = Gdk.Display.get_default()
    if display:
        Gtk.StyleContext.add_provider_for_display(
            display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
