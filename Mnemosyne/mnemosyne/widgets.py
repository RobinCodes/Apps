"""Shared UI pieces, and the stylesheet for what is particular to this app.

The house sheet (housestyle.py) draws the window, the glass and every stock
widget; what is here is the handful of things only Mnemosyne has — a card,
the four rating buttons, the big numbers on Today, a row of a sheet being
typed back. Colours are libadwaita's named ones, so they follow the theme.

`CSS` takes the card size from the settings, because the text of a card is
the one thing in this app that is read at arm's length and has to be as large
as the person reading it wants it.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, GLib, Gtk, Pango  # noqa: E402

from . import housestyle  # noqa: E402


def esc(text) -> str:
    """Text for a widget that reads markup — an `Adw.ActionRow` title, a toast."""
    return GLib.markup_escape_text(str(text or ""))


def toast(widget, message, timeout=3):
    """Find the nearest ToastOverlay and post a message on it."""
    node = widget
    while node is not None and not isinstance(node, Adw.ToastOverlay):
        node = node.get_parent()
    if node is not None:
        node.add_toast(Adw.Toast(title=esc(message), timeout=timeout))
        return True
    return False


def error_toast(widget, exc):
    text = str(exc).strip().splitlines()
    toast(widget, text[0] if text else "Something went wrong", timeout=6)


def confirm(parent, heading, body, action_label, callback, destructive=False):
    """Ask before doing something. callback() runs only on yes.

    `parent` is any widget inside the content, not the window: an
    `Adw.Dialog` presented on a widget finds its own way to the window, and
    the content may be hosted in a window that is not ours.
    """
    dialog = Adw.AlertDialog(heading=heading, body=body)
    dialog.add_response("cancel", "Cancel")
    dialog.add_response("go", action_label)
    dialog.set_response_appearance(
        "go",
        Adw.ResponseAppearance.DESTRUCTIVE if destructive
        else Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("go")
    dialog.set_close_response("cancel")

    def answered(dlg, result):
        if dlg.choose_finish(result) == "go":
            callback()

    dialog.choose(parent, None, answered)


def label(text="", css=(), xalign=0.0, wrap=True, markup=False, selectable=False,
          ellipsize=False, lines=0):
    widget = Gtk.Label(xalign=xalign, wrap=wrap, selectable=selectable,
                       css_classes=list(css))
    if wrap:
        widget.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    if ellipsize:
        widget.set_ellipsize(Pango.EllipsizeMode.END)
        if lines:
            widget.set_lines(lines)
    if markup:
        widget.set_markup(text)
    else:
        widget.set_text(text)
    return widget


def clamp(child, width=720):
    """Content held to a readable measure and centred, as libadwaita pages are."""
    holder = Adw.Clamp(maximum_size=width, tightening_threshold=int(width * 0.8))
    holder.set_child(child)
    return holder


def scrolled(child, width=720):
    scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                  vexpand=True, hexpand=True)
    scroller.set_child(clamp(child, width))
    return scroller


def pill(label_text, css="suggested-action", tooltip=None, icon=None):
    if icon:
        content = Adw.ButtonContent(icon_name=icon, label=label_text)
        button = Gtk.Button(child=content, css_classes=["pill", css] if css else ["pill"])
    else:
        button = Gtk.Button(label=label_text, css_classes=["pill", css] if css else ["pill"])
    if tooltip:
        button.set_tooltip_text(tooltip)
    button.set_halign(Gtk.Align.CENTER)
    return button


def flat(icon, tooltip, action=None):
    button = Gtk.Button(icon_name=icon, tooltip_text=tooltip, css_classes=["flat"])
    button.set_valign(Gtk.Align.CENTER)
    if action:
        button.set_action_name(action)
    return button


class BigNumber(Gtk.Box):
    """A number and what it counts, as on the Today page."""

    def __init__(self, caption: str):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0,
                         css_classes=["big-number"], hexpand=True)
        self.value = Gtk.Label(label="–", css_classes=["big-value", "numeric"])
        self.caption = Gtk.Label(label=caption, css_classes=["big-caption"])
        self.append(self.value)
        self.append(self.caption)

    def set(self, value, caption=None):
        self.value.set_text(str(value))
        if caption is not None:
            self.caption.set_text(caption)


def texture(pixbuf) -> Gdk.Texture:
    """A GdkPixbuf as a texture, without the deprecated `Texture.new_for_pixbuf`."""
    fmt = (Gdk.MemoryFormat.R8G8B8A8 if pixbuf.get_has_alpha()
           else Gdk.MemoryFormat.R8G8B8)
    return Gdk.MemoryTexture.new(pixbuf.get_width(), pixbuf.get_height(), fmt,
                                 pixbuf.read_pixel_bytes(), pixbuf.get_rowstride())


def clear(box: Gtk.Widget) -> None:
    """Take every child out of a box or list."""
    if isinstance(box, Gtk.ListBox):
        box.remove_all()
        return
    if isinstance(box, Adw.PreferencesGroup):
        for row in getattr(box, "_rows", []):
            box.remove(row)
        box._rows = []
        return
    child = box.get_first_child()
    while child is not None:
        following = child.get_next_sibling()
        box.remove(child)
        child = following


def group_add(group: Adw.PreferencesGroup, row: Gtk.Widget) -> Gtk.Widget:
    """Add a row to a group and remember it, so `clear` can take it out again."""
    group.add(row)
    if not hasattr(group, "_rows"):
        group._rows = []
    group._rows.append(row)
    return row


def css(card_size: float = 17) -> str:
    return CSS.replace("CARD_SIZE", f"{float(card_size):g}")


CSS = r"""
/* -------------------------------------------------------------- today -- */

.big-number { padding: 6px 4px; }
.big-value {
  font-size: 2.3em;
  font-weight: 800;
  letter-spacing: -0.02em;
}
.big-caption {
  font-size: 0.86em;
  opacity: 0.7;
}
.day-title {
  font-size: 1.45em;
  font-weight: 750;
}
.calm {
  font-size: 1.05em;
  opacity: 0.8;
}

/* -------------------------------------------------------------- cards -- */

.card-context {
  font-size: 0.86em;
  opacity: 0.66;
}
.card-front {
  font-size: CARD_SIZEpt;
  font-weight: 600;
  line-height: 1.3;
}
.card-back {
  font-size: CARD_SIZEpt;
  line-height: 1.35;
}
.card-rule {
  min-height: 1px;
  background: alpha(currentColor, 0.14);
  margin: 4px 0;
}
.new-tag {
  font-size: 0.75em;
  font-weight: 700;
  padding: 1px 8px;
  border-radius: 999px;
  background: alpha(@accent_bg_color, 0.18);
  color: @accent_color;
}
.leech-tag {
  background: alpha(@warning_bg_color, 0.22);
  color: @warning_color;
}
.session-progress trough, .session-progress progress { min-height: 3px; }

.rating {
  padding: 8px 4px;
  min-width: 64px;
}
.rating .rating-name { font-weight: 700; }
.rating .rating-when {
  font-size: 0.82em;
  opacity: 0.72;
}
.rating.again .rating-name { color: @error_color; }
.rating.hard .rating-name { color: @warning_color; }
.rating.good .rating-name { color: @success_color; }
.rating.easy .rating-name { color: @accent_color; }

.summary-figure {
  font-size: 2.6em;
  font-weight: 800;
}

/* -------------------------------------------------------------- paper -- */

.ticks-entry {
  font-family: monospace;
  font-size: 1.5em;
  letter-spacing: 0.12em;
}
.tick-row { padding: 4px 8px; }
.tick-row.current {
  background: alpha(@accent_bg_color, 0.16);
  border-radius: 8px;
}
.tick-number {
  font-weight: 700;
  opacity: 0.6;
  min-width: 26px;
}
.tick-value {
  font-family: monospace;
  font-weight: 800;
  min-width: 22px;
}
.tick-value.r1 { color: @error_color; }
.tick-value.r2 { color: @warning_color; }
.tick-value.r3 { color: @success_color; }
.tick-value.r4 { color: @accent_color; }

/* ----------------------------------------------------------- trainers -- */

.drill-prompt {
  font-size: 2.4em;
  font-weight: 800;
  letter-spacing: -0.01em;
}
.drill-prompt.red-suit { color: #c0392b; }
.drill-entry {
  font-size: 1.4em;
}
.drill-right { color: @success_color; font-weight: 700; }
.drill-wrong { color: @error_color; font-weight: 700; }
.drill-method {
  font-family: monospace;
  font-size: 0.92em;
}
.countdown {
  font-size: 1.6em;
  font-weight: 800;
}
.locus-photo { border-radius: 10px; }

/* ---------------------------------------------------------------- week -- */

.chart-caption {
  font-size: 0.82em;
  opacity: 0.7;
}

/* -------------------------------------------------------------- claude -- */

.claude-panel { padding: 8px; }
.claude-text {
  font-size: 0.97em;
  line-height: 1.4;
}

.shortcut-key {
  font-family: monospace;
  font-size: 0.86em;
  padding: 1px 6px;
  border-radius: 5px;
  background: alpha(currentColor, 0.10);
}
.shortcut-group {
  font-weight: 700;
  margin-top: 14px;
  margin-bottom: 2px;
}
"""


def install_css(config=None):
    """Put the house sheet, plus this app's own rules, on the display.

    `config` is optional so that a first install can happen before any
    settings have been read; without one the house defaults are used, which
    is the right answer for the one frame before a window exists.
    """
    scheme = housestyle.theming.get(housestyle.wanted_key(config))
    glass = bool(housestyle.setting(config, "glass"))
    size = config["card_font_size"] if config else 17
    housestyle.install(
        scheme, css(size),
        translucency=float(housestyle.setting(config, "translucency")),
        window_radius=int(housestyle.setting(config, "rounding")),
        blur=int(housestyle.setting(config, "glass_blur")) if glass else 0,
        glass=glass)
