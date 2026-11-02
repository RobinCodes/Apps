"""Shared UI pieces, and the stylesheet particular to Ariadne.

Everything that asks or tells — a confirmation, a line of text, a toast —
takes the widget it is for and finds its own way up from there, rather than
assuming the window it is in is Ariadne's: inside Mind Palace it is not.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from . import housestyle  # noqa: E402


def overlay_of(widget):
    node = widget
    while node is not None and not isinstance(node, Adw.ToastOverlay):
        node = node.get_parent()
    return node if node is not None else getattr(widget, "toasts", None)


def toast(widget, message, timeout=3, undo=None):
    """Post a message on the nearest toast overlay; `undo` adds an Undo button."""
    overlay = overlay_of(widget)
    if overlay is None:
        return None
    note = Adw.Toast(title=message, timeout=8 if undo else timeout, use_markup=False)
    if undo is not None:
        note.set_button_label("Undo")
        note.connect("button-clicked", lambda _t: undo())
    overlay.add_toast(note)
    return note


def error_toast(widget, exc):
    text = str(exc).strip().splitlines()
    toast(widget, text[0] if text else "Something went wrong", timeout=6)


def ask_text(parent, heading_text, body, initial, action_label, callback, placeholder="",
             extra=None):
    """One line of text. `callback(text)` runs on a yes; Enter in the field is a yes.

    `extra` is `(response id, label, callback(text))` for a third answer —
    "Remove the arrow" beside "Save", say.
    """
    dialog = Adw.AlertDialog(heading=heading_text, body=body or "")
    entry = Gtk.Entry(placeholder_text=placeholder or "", text=initial or "")
    dialog.set_extra_child(entry)
    dialog.add_response("cancel", "Cancel")
    if extra is not None:
        dialog.add_response(extra[0], extra[1])
        dialog.set_response_appearance(extra[0], Adw.ResponseAppearance.DESTRUCTIVE)
    dialog.add_response("go", action_label)
    dialog.set_response_appearance("go", Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("go")
    dialog.set_close_response("cancel")

    def answered(_dialog, response):
        if response == "go":
            callback(entry.get_text().strip())
        elif extra is not None and response == extra[0]:
            extra[2](entry.get_text().strip())

    def activated(_entry):
        dialog.force_close()
        answered(dialog, "go")

    entry.connect("activate", activated)
    dialog.connect("response", answered)
    dialog.present(parent)
    GLib.idle_add(lambda: (entry.grab_focus(), False)[1])
    return dialog


def heading(text, css="ariadne-heading"):
    return Gtk.Label(label=text, xalign=0, css_classes=[css])


def clear(box):
    while (child := box.get_first_child()) is not None:
        box.remove(child)


CSS = r"""
.ariadne-sidebar { background: alpha(currentColor, 0.025); }
.ariadne-sidebar-top { padding: 8px 8px 4px 8px; }
.ariadne-row-title { font-weight: 600; }
.ariadne-row-detail { font-size: 0.84em; opacity: 0.62; }
.ariadne-row-quiet { opacity: 0.55; }
.ariadne-due {
  font-size: 0.74em;
  font-weight: 700;
  padding: 1px 7px;
  border-radius: 9px;
  background: alpha(@accent_bg_color, 0.22);
}
.ariadne-heading {
  font-size: 0.8em;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  opacity: 0.6;
  margin-top: 18px;
}
.ariadne-big-title {
  font-size: 1.45em;
  font-weight: 750;
  background: transparent;
  border: none;
  box-shadow: none;
  padding: 2px 0;
}
.ariadne-meta { font-size: 0.9em; opacity: 0.75; }
.ariadne-text {
  padding: 10px 12px;
  border-radius: 8px;
  background: alpha(currentColor, 0.04);
  font-size: 1.05em;
}
.ariadne-text text { background: transparent; }
.ariadne-revisit-date {
  font-family: monospace;
  font-size: 0.88em;
  opacity: 0.6;
}
.ariadne-thread-bar { padding: 6px 10px; border-bottom: 1px solid alpha(currentColor, 0.09); }
.ariadne-answers { font-size: 0.88em; opacity: 0.7; }
.ariadne-ghost-bar {
  padding: 6px 12px;
  border-top: 1px solid alpha(@accent_bg_color, 0.35);
  background: alpha(@accent_bg_color, 0.08);
}
.ariadne-quick entry { font-size: 1.25em; }
.ariadne-key {
  font-family: monospace;
  font-size: 0.86em;
  padding: 1px 6px;
  border-radius: 5px;
  background: alpha(currentColor, 0.10);
}
"""


def install_css(config=None):
    """The house sheet, restyling the display, with Ariadne's rules on top: its own window."""
    scheme = housestyle.theming.get(housestyle.wanted_key(config))
    glass = bool(housestyle.setting(config, "glass"))
    housestyle.install(
        scheme, CSS,
        translucency=float(housestyle.setting(config, "translucency")),
        window_radius=int(housestyle.setting(config, "rounding")),
        blur=int(housestyle.setting(config, "glass_blur")) if glass else 0,
        glass=glass)


_room_css = None


def install_room_css():
    """Ariadne's own rules alone, on a provider of their own: for a room of the palace.

    The palace has the house sheet already, and restyling the display from
    inside one room would be every other room's buttons too. Every class here
    starts `ariadne-`, so these rules reach nothing else.
    """
    global _room_css
    if _room_css is None:
        _room_css = Gtk.CssProvider()
        _room_css.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), _room_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
