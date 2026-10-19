"""Shared UI pieces, and the stylesheet that decides what the page looks like.

The CSS at the bottom is most of the design of this app. The preview is built
from plain GTK widgets, so what makes it read as a typeset page rather than as
a column of labels is entirely the spacing and the type scale here: headings
that step down at a ratio rather than by guesswork, a measure that stops the
lines getting too long to track, and code that sits on a tinted ground instead
of in a box with a border.

Colours are given as libadwaita's named colours wherever one exists, so the
whole thing follows the system accent and the light/dark switch without this
file knowing which it is.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from . import housestyle  # noqa: E402


def toast(widget, message, timeout=3):
    """Find the nearest ToastOverlay and post a message on it."""
    node = widget
    while node is not None and not isinstance(node, Adw.ToastOverlay):
        node = node.get_parent()
    if node is not None:
        node.add_toast(Adw.Toast(title=message, timeout=timeout))
        return True
    return False


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
        else Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("cancel")
    dialog.set_close_response("cancel")

    def answered(dlg, result):
        if dlg.choose_finish(result) == "go":
            callback()

    dialog.choose(parent, None, answered)


def ask_text(parent, heading, body, initial, action_label, callback,
             select_stem=False, placeholder=""):
    """Ask for one line of text. callback(text) runs only on a yes.

    Enter in the field answers yes, the way a rename box does everywhere else.
    """
    dialog = Adw.AlertDialog(heading=heading, body=body or "")
    entry = Gtk.Entry(placeholder_text=placeholder or "")
    entry.set_text(initial or "")
    dialog.set_extra_child(entry)
    dialog.add_response("cancel", "Cancel")
    dialog.add_response("go", action_label)
    dialog.set_response_appearance("go", Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("go")
    dialog.set_close_response("cancel")

    def answered(_dlg, response):
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
        if select_stem and initial:
            stem = len(initial.rsplit(".", 1)[0]) if "." in initial.lstrip(".") else len(initial)
            entry.select_region(0, stem)
        return False

    GLib.idle_add(focus)
    return dialog


def copy_text(widget, text, note="Copied"):
    display = widget.get_display() or Gdk.Display.get_default()
    if display:
        display.get_clipboard().set(text)
        toast(widget, note)


def small_button(label, css=None, tooltip=None):
    button = Gtk.Button(label=label,
                        css_classes=["flat", "small-button"] + ([css] if css else []))
    if tooltip:
        button.set_tooltip_text(tooltip)
    button.set_valign(Gtk.Align.CENTER)
    return button


def icon_button(icon, tooltip, action=None):
    button = Gtk.Button(icon_name=icon, tooltip_text=tooltip, css_classes=["flat"])
    if action:
        button.set_action_name(action)
    return button


# The family names CSS chooses a font for rather than looks one up by.
GENERIC_FAMILIES = ("monospace", "sans-serif", "serif", "system-ui",
                    "cursive", "fantasy")


def css_font_family(name: str) -> str:
    """A font family as CSS wants to be handed one; "" for "leave it alone".

    A real name needs quoting, because half of them have a space in it. A
    generic one must not be quoted: `monospace` asks for whichever monospace
    font the desktop has settled on, and `"monospace"` asks for a font of that
    name, which on most machines is nothing at all.
    """
    name = (name or "").strip()
    if not name:
        return ""
    if name.lower() in GENERIC_FAMILIES:
        return name.lower()
    return '"{}"'.format(name.replace("\\", "").replace('"', ""))


CSS = r"""
/* ------------------------------------------------------------- editor -- */

.editor-gutter {
  background: alpha(currentColor, 0.025);
}

.editor-text {
  padding-top: 4px;
}

.editor-text text selection {
  background: alpha(@accent_bg_color, 0.35);
}

/* ------------------------------------------------------------ preview -- */

.preview-area {
  background: @view_bg_color;
}

.md-page {
  padding: 26px 30px 120px 30px;
}

.md-heading {
  margin-top: 22px;
  margin-bottom: 6px;
}

/* A heading at the very top of a document needs no space above it. */
.md-page > .md-heading:first-child {
  margin-top: 2px;
}

.md-h1 {
  font-size: 1.85em;
  font-weight: 800;
  letter-spacing: -0.015em;
  margin-top: 8px;
  margin-bottom: 10px;
  padding-bottom: 6px;
  border-bottom: 1px solid alpha(currentColor, 0.13);
}
.md-h2 {
  font-size: 1.45em;
  font-weight: 750;
  letter-spacing: -0.01em;
  margin-top: 26px;
  padding-bottom: 4px;
  border-bottom: 1px solid alpha(currentColor, 0.09);
}
.md-h3 { font-size: 1.22em; font-weight: 700; }
.md-h4 { font-size: 1.08em; font-weight: 700; }
.md-h5 { font-size: 1.00em; font-weight: 700; opacity: 0.88; }
.md-h6 {
  font-size: 0.92em;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  opacity: 0.72;
}

.md-para {
  margin-bottom: 12px;
  line-height: 1.6;
}

.md-item-text {
  margin-bottom: 0;
  line-height: 1.45;
}

.md-list {
  margin-bottom: 12px;
  margin-left: 4px;
}

.md-item {
  margin-bottom: 1px;
}

/* A list nested inside an item has already been spaced by the item. */
.md-item .md-list {
  margin-bottom: 0;
  margin-top: 1px;
}

.md-item .md-code,
.md-item .md-quote,
.md-item .md-table {
  margin-top: 4px;
  margin-bottom: 6px;
}

.md-marker {
  opacity: 0.55;
  font-feature-settings: "tnum";
  line-height: 1.45;
}

.md-task {
  margin-top: 2px;
}

.md-task-done {
  opacity: 0.55;
}

.md-quote {
  border-left: 3px solid alpha(@accent_bg_color, 0.55);
  padding: 2px 0 2px 14px;
  margin: 4px 0 14px 2px;
  opacity: 0.9;
}
.md-quote .md-para:last-child { margin-bottom: 0; }

.md-rule {
  margin: 22px 0;
  background: alpha(currentColor, 0.16);
  min-height: 1px;
}

/* Code: a tinted ground rather than a boxed-in card. The border is only on
   the header, which is what separates the label from the code itself. */
.md-code {
  background: alpha(currentColor, 0.045);
  border: 1px solid alpha(currentColor, 0.09);
  border-radius: 8px;
  margin: 6px 0 16px 0;
}
.md-code-head {
  padding: 1px 4px 1px 12px;
  border-bottom: 1px solid alpha(currentColor, 0.07);
}
.md-code-lang {
  font-size: 0.78em;
  font-family: monospace;
  opacity: 0.55;
  letter-spacing: 0.04em;
}
.md-code-copy {
  min-height: 22px;
  min-width: 22px;
  padding: 1px;
  opacity: 0.5;
}
.md-code-copy:hover { opacity: 1; }
.md-code-body {
  font-family: monospace;
  font-size: 0.92em;
  padding: 10px 12px;
  line-height: 1.45;
}

.md-table {
  margin: 4px 0 16px 0;
  border: 1px solid alpha(currentColor, 0.13);
  border-radius: 7px;
}
.md-cell {
  padding: 6px 12px;
  border-top: 1px solid alpha(currentColor, 0.09);
  border-right: 1px solid alpha(currentColor, 0.09);
}
.md-table > .md-cell:last-child { border-right: none; }
.md-cell-head {
  font-weight: 700;
  background: alpha(currentColor, 0.05);
  border-top: none;
}

.md-math {
  margin: 12px 0 16px 0;
  font-size: 1.05em;
}

.md-figure {
  margin: 8px 0 16px 0;
}
.md-image {
  border-radius: 6px;
}
.md-caption {
  font-size: 0.88em;
  opacity: 0.65;
  margin-top: 2px;
}

.md-notice {
  font-size: 0.88em;
  padding: 10px 12px;
  border-radius: 6px;
  background: alpha(currentColor, 0.05);
  border: 1px dashed alpha(currentColor, 0.18);
}
.md-missing { color: #c64600; }
.md-remote { opacity: 0.7; }

.md-html-box {
  margin: 4px 0 14px 0;
  padding: 8px 12px;
  border-radius: 6px;
  background: alpha(currentColor, 0.04);
  border-left: 3px solid alpha(currentColor, 0.2);
}
.md-html-tag {
  font-size: 0.72em;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  opacity: 0.45;
}
.md-html {
  font-family: monospace;
  font-size: 0.88em;
  opacity: 0.8;
}

.md-frontmatter {
  margin: 0 0 22px 0;
  padding: 10px 14px;
  border-radius: 8px;
  background: alpha(@accent_bg_color, 0.07);
  border: 1px solid alpha(@accent_bg_color, 0.18);
}
.md-fm-key {
  font-size: 0.82em;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  opacity: 0.55;
}
.md-fm-value { font-size: 0.95em; }

.md-footnotes {
  margin-top: 28px;
  font-size: 0.92em;
  opacity: 0.85;
}
.md-footnote-mark {
  font-family: monospace;
  font-size: 0.85em;
  opacity: 0.6;
  padding-top: 2px;
  min-width: 28px;
}
.md-footnotes .md-para { margin-bottom: 4px; }

.md-empty {
  opacity: 0.45;
  font-size: 1.05em;
}

/* -------------------------------------------------------------- chrome -- */

.status-strip {
  padding: 2px 10px;
  border-top: 1px solid alpha(currentColor, 0.10);
  font-size: 0.84em;
}
.status-strip label { opacity: 0.72; }
.status-strip .status-loud { opacity: 1; font-weight: 600; }
.status-modified { color: #e5a50a; font-weight: 700; }

.small-button { padding: 1px 8px; font-size: 0.85em; min-height: 0; }
.monospace { font-family: monospace; }

.outline-pane {
  background: @sidebar_bg_color;
}
.outline-pane listview { background: transparent; }
.outline-row {
  padding: 3px 6px;
  border-radius: 6px;
}
.outline-row label { font-size: 0.92em; }
.outline-h1 { font-weight: 800; }
.outline-h2 { font-weight: 650; }
.outline-h3 { opacity: 0.92; }
.outline-h4, .outline-h5, .outline-h6 { opacity: 0.78; font-size: 0.9em; }
.outline-current {
  background: alpha(@accent_bg_color, 0.18);
  font-weight: 700;
}
.outline-empty {
  padding: 18px 12px;
  opacity: 0.55;
  font-size: 0.9em;
}
.outline-header {
  padding: 4px 6px 4px 12px;
  border-bottom: 1px solid alpha(currentColor, 0.10);
}

.find-bar {
  padding: 5px 8px;
  border-top: 1px solid alpha(currentColor, 0.12);
  background: alpha(currentColor, 0.03);
}
.find-count {
  font-size: 0.84em;
  opacity: 0.6;
  font-family: monospace;
}
.find-no-match entry { color: #e01b24; }

.tab-title-modified { font-style: italic; }

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
