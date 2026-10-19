"""Shared UI pieces: the problems list, dialogs, small helpers, the stylesheet."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, GLib, Gtk, Pango  # noqa: E402

from . import housestyle  # noqa: E402

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


def ask_text(parent, heading, body, initial, action_label, callback, select_stem=False,
             secret=False):
    """Ask for one line of text. callback(text) runs only on a yes.

    Enter in the field answers yes, the way a rename box does everywhere
    else. `select_stem` preselects a file name without its extension, so
    typing replaces "chapter" and keeps ".tex".
    """
    dialog = Adw.AlertDialog(heading=heading, body=body or "")
    entry = Gtk.PasswordEntry(show_peek_icon=True) if secret else Gtk.Entry()
    entry.set_text(initial or "")
    # Enter is handled below rather than with activates-default, which would
    # press the button as well and answer twice.
    dialog.set_extra_child(entry)
    dialog.add_response("cancel", "Cancel")
    dialog.add_response("go", action_label)
    dialog.set_response_appearance("go", Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("go")
    dialog.set_close_response("cancel")

    def answered(dlg, response):
        if response == "go":
            text = entry.get_text().strip()
            if text:
                callback(text)

    def activated(_entry):
        dialog.force_close()
        answered(dialog, "go")

    entry.connect("activate", activated)
    dialog.connect("response", answered)
    dialog.present(parent)

    def focus():
        entry.grab_focus()
        if select_stem and initial and not secret:
            stem = len(initial.rsplit(".", 1)[0]) if "." in initial.lstrip(".") else len(initial)
            entry.select_region(0, stem)
        return False

    GLib.idle_add(focus)
    return dialog


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
    """Errors, warnings and bad boxes. Clicking one jumps the editor to it.

    `on_activate(line)` handles a line in the document itself. When
    `on_message(message)` is given it handles every row instead — a
    document made of several files has problems in files other than the one
    on screen, and only the window knows how to open those.
    """

    def __init__(self, on_activate, on_message=None):
        super().__init__(hexpand=True)
        self.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        # Grow with the number of problems rather than reserving a fixed slab
        # of the editor, which looks wrong when there is only one of them.
        self.set_propagate_natural_height(True)
        self.set_max_content_height(240)
        self.on_activate = on_activate
        self.on_message = on_message
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
        # In a document of several files the locator carries a path; keep its
        # end, which is the part that says which file, and let the message
        # have the width.
        locator = Gtk.Label(label=where, css_classes=["dim-label", "monospace", "locator"],
                            ellipsize=Pango.EllipsizeMode.START, max_width_chars=26)
        locator.set_tooltip_text(where)
        locator.set_valign(Gtk.Align.START)
        box.append(locator)

        row = Gtk.ListBoxRow(child=box)
        row.set_activatable(bool(message.line) and (not message.file or bool(self.on_message)))
        row._index = index
        return row

    def _activated(self, _box, row):
        index = getattr(row, "_index", None)
        if index is None:
            return
        message = self._messages[index]
        if self.on_message is not None:
            self.on_message(message)
        elif message.line and not message.file:
            self.on_activate(message.line)


CSS = """
/* The gutter is a DrawingArea beside the ScrolledWindow rather than inside
   it, so it has to paint the same surface the text does or the editor reads
   as two panes. It used to be a 3% tint over the window's own background,
   which worked while that background was opaque Adwaita grey; under the
   house style the window is glass, and a transparent strip down the side of
   a block of code is a strip of wallpaper running through it. */
.editor-gutter {
  background-color: @view_bg_color;
  background-image: linear-gradient(alpha(currentColor, 0.03),
                                    alpha(currentColor, 0.03));
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

/* The folder side bar. */
.file-tree-header {
  padding: 4px 6px 4px 10px;
  border-bottom: 1px solid alpha(currentColor, 0.10);
}
.file-tree listview { background: transparent; }
.file-tree listview.navigation-sidebar > row {
  min-height: 26px;
  padding-top: 0;
  padding-bottom: 0;
  margin-top: 0;
  margin-bottom: 0;
}
.tree-dir { font-weight: 600; }
.tree-hidden { opacity: 0.6; }
.tree-current { color: @accent_color; font-weight: bold; }
.git-badge { font-family: monospace; font-weight: bold; font-size: 0.85em; min-width: 12px; }
.git-modified { color: #e5a50a; }
.git-added    { color: #2ec27e; }
.git-deleted  { color: #e01b24; }
.git-conflict { color: #e01b24; }
.git-renamed  { color: #3584e4; }
.git-dir      { color: alpha(currentColor, 0.45); }
.git-heading { font-weight: bold; }
.git-summary { font-size: 0.9em; }
.overleaf-note { font-size: 0.9em; }
"""


def install_css(config=None):
    """Put the house sheet, plus this app's own rules, on the display.

    The house sheet is the palettes and the shared idiom — see housestyle.py —
    and `CSS` above is only what is particular to this app. One provider,
    reloaded in place, so a theme change takes effect on widgets that have
    already been built.

    `config` is optional so that a first install can happen before any
    settings have been read; without one the house defaults are used, which
    is the right answer for the one frame before a window exists.
    """
    scheme = housestyle.theming.get(housestyle.wanted_key(config))
    glass = bool(housestyle.setting(config, "glass"))
    housestyle.install(
        scheme, CSS,
        translucency=float(housestyle.setting(config, "translucency")),
        window_radius=int(housestyle.setting(config, "rounding")),
        blur=int(housestyle.setting(config, "glass_blur")) if glass else 0,
        glass=glass)
