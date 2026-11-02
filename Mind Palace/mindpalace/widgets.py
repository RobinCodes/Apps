"""Shared pieces: the palace's own stylesheet, an icon from a file, a flat row.

The house sheet (`housestyle`) is the palettes and the shared idiom; `CSS` here
is only what is particular to the palace: its sidebar, Home's cards, the panel
Claude answers in. A room's own rules are the room's, on a provider of its own
or scoped to its content, so that nothing here has to know about them.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")

from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

CSS = """
.palace-sidebar { background: none; }
.palace-sidebar row {
  border-radius: 9px;
  margin: 1px 6px;
  padding: 5px 8px;
}
.palace-sidebar row label { font-size: 0.97em; }
.door-mark { opacity: 0.5; }
.home-page { padding: 14px 16px 22px 16px; }
.home-day { font-size: 1.5em; font-weight: 300; }
.home-sync { font-size: 0.88em; }
.home-sync.warning { color: @warning_color; }
.home-card { padding: 13px 15px; }
.home-card-title { font-weight: 600; font-size: 0.92em; opacity: 0.75; }
.home-number { font-size: 2.1em; font-weight: 300; }
.home-unit { font-size: 0.92em; opacity: 0.75; }
.home-line { padding: 3px 6px; min-height: 0; font-weight: normal; }
.search-hit-snippet { font-size: 0.88em; opacity: 0.75; }
.palace-ask {
  border-radius: 14px;
  padding: 12px;
  background-color: @popover_bg_color;
  box-shadow: 0 4px 12px alpha(black, 0.28);
}
.palace-ask-prompt { border-radius: 9px; padding: 6px 8px; background: alpha(currentColor, 0.06); }
.palace-ask-answer { font-size: 0.96em; }
.palace-ask-about { font-size: 0.86em; opacity: 0.75; }
"""

FALLBACK_ICON = "application-x-executable-symbolic"

# One texture per file for the life of the process: the sidebar and a door's
# page draw the same icon, and an .svg is not free to load.
_textures: dict = {}


def texture(path):
    """The picture in a file, or None if it cannot be read."""
    key = str(path)
    if key not in _textures:
        try:
            _textures[key] = Gdk.Texture.new_from_filename(key)
        except (GLib.Error, TypeError):
            _textures[key] = None
    return _textures[key]


def room_icon(spec, size: int = 22) -> Gtk.Image:
    """A room's icon at `size`: its app's file, or a themed icon when there is none."""
    picture = texture(spec.icon) if spec.icon else None
    if picture is not None:
        image = Gtk.Image.new_from_paintable(picture)
    else:
        image = Gtk.Image.new_from_icon_name(spec.icon_name or FALLBACK_ICON)
    image.set_pixel_size(size)
    return image


def label(text: str = "", *classes, wrap: bool = False, ellipsize: bool = False,
          selectable: bool = False) -> Gtk.Label:
    """A left-aligned label, which is nearly every label in the palace."""
    widget = Gtk.Label(label=text, xalign=0.0, wrap=wrap, selectable=selectable,
                       css_classes=list(classes))
    if wrap:
        # Otherwise a long line's natural width is the whole line, and one
        # note title can decide how wide the window has to be.
        widget.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        widget.set_natural_wrap_mode(Gtk.NaturalWrapMode.NONE)
    if ellipsize:
        widget.set_ellipsize(Pango.EllipsizeMode.END)
    return widget


def line_button(text: str, tooltip: str, callback) -> Gtk.Button:
    """A flat row of text that does something: a note, an inbox item."""
    inner = label(text, ellipsize=True)
    button = Gtk.Button(child=inner, tooltip_text=tooltip,
                        css_classes=["flat", "home-line"])
    button.connect("clicked", lambda _b: callback())
    return button


def clear(box: Gtk.Widget) -> None:
    """Take every child out of a box or a flow box."""
    child = box.get_first_child()
    while child is not None:
        following = child.get_next_sibling()
        box.remove(child)
        child = following
