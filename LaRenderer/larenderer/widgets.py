"""Shared UI pieces: the problems list, dialogs, small helpers, the stylesheet."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gtk, Pango  # noqa: E402

SEVERITY_ICON = {
    "error": "✕",      # a cross
    "warning": "⚠",    # a warning triangle
    "badbox": "▤",     # a box, appropriately
}

SEVERITY_CSS = {
    "error": "sev-error",
    "warning": "sev-warning",
    "badbox": "sev-badbox",
}

SEVERITY_LABEL = {"error": "Error", "warning": "Warning", "badbox": "Bad box"}


def toast(widget, message, timeout=3):
    """Find the nearest ToastOverlay and post a message on it."""
    node = widget
    while node is not None and not isinstance(node, Adw.ToastOverlay):
        node = node.get_parent()
    if node is not None:
        node.add_toast(Adw.Toast(title=message, timeout=timeout))


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
        Adw.ResponseAppearance.DESTRUCTIVE if destructive else Adw.ResponseAppearance.SUGGESTED,
    )
    dialog.set_default_response("cancel")
    dialog.set_close_response("cancel")

    def answered(dlg, result):
        if dlg.choose_finish(result) == "go":
            callback()

    dialog.choose(parent, None, answered)


def copyable(widget, text, note="Copied"):
    display = Gdk.Display.get_default()
    if display:
        display.get_clipboard().set(text)
        toast(widget, note)


def small_button(label, css=None, tooltip=None):
    button = Gtk.Button(label=label, css_classes=["flat", "small-button"] + ([css] if css else []))
    if tooltip:
        button.set_tooltip_text(tooltip)
    button.set_valign(Gtk.Align.CENTER)
    return button


def pill(text, css=None):
    label = Gtk.Label(label=text, css_classes=["pill-badge"] + ([css] if css else []))
    label.set_valign(Gtk.Align.CENTER)
    return label


class ProblemsList(Gtk.ScrolledWindow):
    """Errors, warnings and bad boxes. Clicking one jumps the editor to it."""

    def __init__(self, on_activate):
        super().__init__(hexpand=True)
        self.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        # Grow with the number of problems rather than reserving a fixed slab
        # of the editor, which looks wrong when there is only one of them.
        self.set_propagate_natural_height(True)
        self.set_max_content_height(240)
        self.on_activate = on_activate
        self._box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self._box.add_css_class("problems-list")
        self._box.connect("row-activated", self._activated)
        self.set_child(self._box)
        self._messages = []

    def set_messages(self, messages, missing_hint=""):
        self._messages = list(messages)
        while (row := self._box.get_first_child()) is not None:
            self._box.remove(row)

        if missing_hint:
            self._box.append(self._hint_row(missing_hint))

        if not messages:
            if not missing_hint:
                empty = Gtk.Label(
                    label="No errors, no warnings.",
                    css_classes=["dim-label"], xalign=0,
                    margin_top=10, margin_bottom=10, margin_start=12,
                )
                row = Gtk.ListBoxRow(activatable=False, child=empty)
                self._box.append(row)
            return

        for index, message in enumerate(messages):
            self._box.append(self._row(index, message))

    def _hint_row(self, hint):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
                      margin_top=8, margin_bottom=8, margin_start=12, margin_end=12)
        icon = Gtk.Label(label="⬇", css_classes=["sev-missing", "sev-glyph"])
        icon.set_valign(Gtk.Align.START)
        box.append(icon)
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        inner.append(Gtk.Label(label="Missing packages", xalign=0, css_classes=["heading"]))
        command = Gtk.Label(label=hint, xalign=0, selectable=True,
                            css_classes=["monospace", "dim-label"], wrap=True)
        inner.append(command)
        box.append(inner)
        copy = small_button("Copy", tooltip="Copy the install command")
        copy.connect("clicked", lambda _b: copyable(self, hint, "Install command copied"))
        box.append(copy)
        row = Gtk.ListBoxRow(activatable=False, child=box)
        row.add_css_class("hint-row")
        return row

    def _row(self, index, message):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
                      margin_top=5, margin_bottom=5, margin_start=12, margin_end=12)

        glyph = Gtk.Label(label=SEVERITY_ICON.get(message.severity, "•"))
        glyph.add_css_class(SEVERITY_CSS.get(message.severity, ""))
        glyph.add_css_class("sev-glyph")
        glyph.set_valign(Gtk.Align.START)
        box.append(glyph)

        text = Gtk.Label(label=message.text, xalign=0, hexpand=True, wrap=True,
                         wrap_mode=Pango.WrapMode.WORD_CHAR)
        box.append(text)

        where = "line %d" % message.line if message.line else "—"
        if message.file:
            where = "%s:%s" % (message.file, message.line or "?")
        locator = Gtk.Label(label=where, css_classes=["dim-label", "monospace", "locator"])
        locator.set_valign(Gtk.Align.START)
        box.append(locator)

        row = Gtk.ListBoxRow(child=box)
        row.set_activatable(bool(message.line) and not message.file)
        row._index = index
        return row

    def _activated(self, _box, row):
        index = getattr(row, "_index", None)
        if index is None:
            return
        message = self._messages[index]
        if message.line and not message.file:
            self.on_activate(message.line)


CSS = """
.editor-gutter {
  background: alpha(currentColor, 0.03);
}
.preview-area {
  background: @window_bg_color;
}
/* Dark pages get their own backdrop rather than the window's, so they read the
   same whether or not the app itself is dark — a near-black sheet on a white
   pane is the one combination that hurts. */
.preview-area-dark {
  background: #2c2c2c;
}
.pdf-page {
  background: white;
  box-shadow: 0 1px 5px alpha(black, 0.28);
}
/* #171717 is where the colour matrix in preview.py puts white, so a sheet that
   has not been rendered yet is already the colour it will be. The hairline
   takes over from the shadow, which is invisible against a dark backdrop. */
.pdf-page-dark {
  background: #171717;
  box-shadow: 0 0 0 1px alpha(white, 0.09), 0 1px 6px alpha(black, 0.45);
}
.status-strip {
  padding: 3px 10px;
  border-top: 1px solid alpha(currentColor, 0.12);
  font-size: 0.86em;
}
.status-strip.ok    { color: #2ec27e; }
.status-strip.bad   { color: #e01b24; }
.pill-badge {
  padding: 1px 7px;
  border-radius: 999px;
  font-size: 0.78em;
  font-weight: bold;
  background: alpha(currentColor, 0.12);
}
.badge-error   { color: #e01b24; }
.badge-warning { color: #e5a50a; }
.badge-ok      { color: #2ec27e; }
.badge-missing { color: #c64600; }
.small-button { padding: 1px 8px; font-size: 0.85em; min-height: 0; }
.monospace { font-family: monospace; }
.sev-glyph { font-size: 1.0em; min-width: 16px; }
.sev-error   { color: #e01b24; font-weight: bold; }
.sev-warning { color: #e5a50a; font-weight: bold; }
.sev-badbox  { color: #9a9996; }
.sev-missing { color: #c64600; font-weight: bold; }
.locator { font-size: 0.82em; }
.hint-row { background: alpha(#c64600, 0.10); }
.problems-list row { border-bottom: 1px solid alpha(currentColor, 0.07); }
.package-missing { color: alpha(currentColor, 0.55); }
.log-view { font-family: monospace; font-size: 0.85em; }
.compiling { color: #e5a50a; }
"""


def install_css():
    provider = Gtk.CssProvider()
    provider.load_from_string(CSS)
    display = Gdk.Display.get_default()
    if display:
        Gtk.StyleContext.add_provider_for_display(
            display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
