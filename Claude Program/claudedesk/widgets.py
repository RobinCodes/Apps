"""The app's visual language: stylesheet, small helpers, tool presentation.

Everything here is deliberately restrained. libadwaita already provides a
coherent modern look, so this sheet only adds what it has no opinion about —
the shape of a message, the tint of a code card, the colour of a status dot —
and inherits the rest, including the light/dark switch, from the system theme.
"""

from __future__ import annotations

import os
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from .backend import QUESTION_TOOL  # noqa: E402

# Wide windows are not the problem; wide *text* is. Prose stays inside this.
READING_WIDTH = 820

TOOL_ICONS = {
    "Bash": "utilities-terminal-symbolic",
    "Read": "text-x-generic-symbolic",
    "Write": "document-new-symbolic",
    "Edit": "document-edit-symbolic",
    "NotebookEdit": "document-edit-symbolic",
    "Glob": "system-search-symbolic",
    "Grep": "system-search-symbolic",
    "WebFetch": "web-browser-symbolic",
    "WebSearch": "system-search-symbolic",
    "Task": "system-run-symbolic",
    "TaskCreate": "checkbox-checked-symbolic",
    "TaskUpdate": "checkbox-checked-symbolic",
    "Skill": "applications-science-symbolic",
    QUESTION_TOOL: "dialog-question-symbolic",
}

# The one field worth showing when a tool call is collapsed to a single line.
TOOL_SUMMARY_KEYS = {
    "Bash": "command",
    "Read": "file_path",
    "Write": "file_path",
    "Edit": "file_path",
    "NotebookEdit": "notebook_path",
    "Glob": "pattern",
    "Grep": "pattern",
    "WebFetch": "url",
    "WebSearch": "query",
    "Task": "description",
    "Skill": "skill",
}


GENERIC_TOOL_ICON = "applications-utilities-symbolic"

# Every icon the app draws with. Anything added elsewhere belongs here too, or
# ensure_icons() cannot vouch for it.
UI_ICONS = (
    "dialog-password-symbolic", "dialog-warning-symbolic", "edit-copy-symbolic",
    "folder-symbolic", "go-bottom-symbolic", "go-up-symbolic", "list-add-symbolic",
    "mail-send-symbolic",
    "media-playback-stop-symbolic", "object-select-symbolic", "open-menu-symbolic",
    "pan-down-symbolic", "pan-end-symbolic", "sidebar-show-symbolic",
    "view-more-symbolic", "weather-clear-night-symbolic", "window-close-symbolic",
)
APP_ICONS = tuple(sorted(set(UI_ICONS) | set(TOOL_ICONS.values()) | {GENERIC_TOOL_ICON}))

# What we fall back to. A libadwaita app is drawn against these names in the
# first place, so it is the theme most likely to have all of them.
FALLBACK_ICON_THEME = "Adwaita"
PROBE_SIZE = 16


def tool_icon(name):
    return TOOL_ICONS.get(name, GENERIC_TOOL_ICON)


def undrawable_icons(theme, names=APP_ICONS):
    """The names this theme resolves to a file but cannot actually draw.

    GTK 4.20 dropped librsvg for symbolic icons in favour of its own smaller
    SVG renderer, which understands less than librsvg did. A theme carrying
    icons it cannot parse — elementary is one — fails silently: the lookup
    succeeds, the widget is built and sized, and nothing is painted. An
    icon-only button becomes an invisible button.

    Asking for the paintable is not enough to catch that, so snapshot each one
    and look at the bounds it claims. An icon that drew nothing reports none.
    """
    white = [Gdk.RGBA(red=1, green=1, blue=1, alpha=1)]
    missing = []
    for name in names:
        icon = theme.lookup_icon(name, None, PROBE_SIZE, 1, Gtk.TextDirection.NONE, 0)
        snapshot = Gtk.Snapshot.new()
        icon.snapshot_symbolic(snapshot, PROBE_SIZE, PROBE_SIZE, white)
        node = snapshot.to_node()
        if node is None or node.get_bounds().size.width <= 0:
            missing.append(name)
    return missing


def ensure_icons(display):
    """Keep the user's icon theme unless it leaves buttons blank.

    Switching the whole app rather than patching the icons individually is
    deliberate: half elementary and half Adwaita in one header looks like a
    bug of its own, and the theme is only overridden when it is already
    failing to draw.
    """
    theme = Gtk.IconTheme.get_for_display(display)
    broken = undrawable_icons(theme)
    if not broken:
        return None

    original = theme.get_theme_name()
    settings = Gtk.Settings.get_default()
    if settings is None:
        return None
    settings.set_property("gtk-icon-theme-name", FALLBACK_ICON_THEME)

    remaining = undrawable_icons(Gtk.IconTheme.get_for_display(display))
    if len(remaining) >= len(broken):
        # No better off — keep what the user chose rather than swap one set of
        # blank buttons for another.
        settings.set_property("gtk-icon-theme-name", original)
        return None

    print(
        f"claude-desk: the {original} icon theme cannot draw "
        f"{len(broken)} of the {len(APP_ICONS)} icons this app uses "
        f"({', '.join(broken[:3])}{'…' if len(broken) > 3 else ''}); "
        f"using {FALLBACK_ICON_THEME} instead.",
        file=sys.stderr,
    )
    return original


def tool_summary(name, payload):
    """One line describing what a tool call is about to do."""
    if not isinstance(payload, dict):
        return ""
    if name == QUESTION_TOOL:
        return _questions_summary(payload)
    key = TOOL_SUMMARY_KEYS.get(name)
    if key and payload.get(key):
        value = str(payload[key])
        if key.endswith("path"):
            value = shorten_path(value)
        return " ".join(value.split())
    for fallback in ("description", "command", "file_path", "pattern", "query", "prompt"):
        if payload.get(fallback):
            return " ".join(str(payload[fallback]).split())
    return ", ".join(payload.keys())


def _questions_summary(payload):
    """Asking is the whole of what that call does, so show the question."""
    asked = [str(item.get("question", "")) for item in payload.get("questions") or []
             if isinstance(item, dict) and item.get("question")]
    if not asked:
        return ""
    first = " ".join(asked[0].split())
    return f"{first} (+{len(asked) - 1} more)" if len(asked) > 1 else first


def match_commands(prefix, commands):
    """The commands a half-typed name could still become, best first.

    What is in front of the cursor is worth more than what is buried in the
    middle of a name, so a prefix of the name comes first, then a prefix of
    one of its aliases, then a name that merely contains it. Within a group
    the child's own order is kept: it puts this project's commands first.
    """
    prefix = (prefix or "").lower().lstrip("/")
    starts, aliased, contains = [], [], []
    for command in commands or []:
        name = str(command.get("name") or "")
        if not name:
            continue
        aliases = [str(alias).lower() for alias in command.get("aliases") or []]
        lowered = name.lower()
        if lowered.startswith(prefix):
            starts.append(command)
        elif any(alias.startswith(prefix) for alias in aliases):
            aliased.append(command)
        elif prefix and prefix in lowered:
            contains.append(command)
    return starts + aliased + contains


def command_hint(command):
    """The one line shown beside a command's name."""
    description = " ".join(str(command.get("description") or "").split())
    # Skill descriptions are written for the model and often open with the
    # conditions for using them; the first sentence is the part a person
    # reading a menu needs.
    for stop in (". ", " — ", " - "):
        head = description.split(stop)[0]
        if 12 <= len(head) < len(description):
            description = head
            break
    return description


def shorten_path(path):
    home = os.path.expanduser("~")
    if path.startswith(home + os.sep):
        return "~" + path[len(home):]
    return path


def toast(widget, message, timeout=3):
    node = widget
    while node is not None and not isinstance(node, Adw.ToastOverlay):
        node = node.get_parent()
    if node is not None:
        node.add_toast(Adw.Toast(title=message, timeout=timeout))


def copy_to_clipboard(widget, text):
    display = widget.get_display() if widget else Gdk.Display.get_default()
    if display:
        display.get_clipboard().set(text)


def confirm(parent, heading, body, action_label, callback, destructive=True):
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


def prompt(parent, heading, body, action_label, callback, text=""):
    dialog = Adw.AlertDialog(heading=heading, body=body)
    entry = Gtk.Entry(text=text, activates_default=True, margin_top=6,
                      margin_start=12, margin_end=12, margin_bottom=6)
    dialog.set_extra_child(entry)
    dialog.add_response("cancel", "Cancel")
    dialog.add_response("go", action_label)
    dialog.set_response_appearance("go", Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("go")
    dialog.set_close_response("cancel")

    def answered(dlg, result):
        if dlg.choose_finish(result) == "go":
            value = entry.get_text().strip()
            if value:
                callback(value)

    dialog.choose(parent, None, answered)
    focus_soon(entry)


def focus_soon(widget):
    """Focus a widget once the dialog it lives in is on screen.

    Once, and not once per idle: `grab_focus()` returns True to say it worked,
    and an idle source reads True as "call me again". Handing it the method
    directly gives you a focus grab every time the loop goes round, which in
    an entry re-selects the text under whatever you have just typed — so a
    rename could never be more than one character long.
    """
    def grab():
        widget.grab_focus()
        return GLib.SOURCE_REMOVE

    GLib.idle_add(grab)


def icon_button(icon_name, tooltip, on_click, css=("flat",)):
    button = Gtk.Button(icon_name=icon_name, tooltip_text=tooltip, css_classes=list(css))
    button.connect("clicked", lambda _b: on_click())
    return button


def clear_box(box):
    child = box.get_first_child()
    while child is not None:
        following = child.get_next_sibling()
        box.remove(child)
        child = following


CSS = """
/* ---------------------------------------------------------- messages --- */
.msg-user {
  background: alpha(@accent_bg_color, 0.14);
  border-radius: 14px;
  padding: 10px 14px;
}
.msg-text { padding: 2px 2px; }
.msg-heading { margin-top: 6px; }
.msg-quote {
  border-left: 3px solid alpha(currentColor, 0.25);
  padding-left: 10px;
  opacity: 0.85;
}
.msg-rule {
  background: alpha(currentColor, 0.15);
  min-height: 1px;
  margin: 8px 0;
}
.turn-meta {
  font-size: 0.78em;
  opacity: 0.45;
  padding: 2px 4px 10px 4px;
}
.notice-card {
  background: alpha(@warning_color, 0.12);
  border-radius: 10px;
  padding: 8px 12px;
  font-size: 0.9em;
}
.error-card {
  background: alpha(@error_color, 0.14);
  border-radius: 10px;
  padding: 8px 12px;
}

/* ------------------------------------------------------------- code ---- */
.code-card {
  background: alpha(currentColor, 0.05);
  border: 1px solid alpha(currentColor, 0.10);
  border-radius: 10px;
}
.code-head {
  padding: 2px 4px 2px 12px;
  border-bottom: 1px solid alpha(currentColor, 0.10);
  font-size: 0.8em;
  opacity: 0.7;
}
.code-body { background: transparent; font-size: 0.92em; }
.code-body text { background: transparent; }

/* ----------------------------------------------------------- tables ---- */
.table-card { padding: 2px 0; }
.md-table { border: 1px solid alpha(currentColor, 0.13); border-radius: 8px; }
.md-th, .md-td { padding: 6px 12px; }
.md-th {
  font-weight: bold;
  background: alpha(currentColor, 0.07);
  border-bottom: 1px solid alpha(currentColor, 0.18);
}
.md-td { border-bottom: 1px solid alpha(currentColor, 0.07); }
.md-td-alt { background: alpha(currentColor, 0.035); }

/* ------------------------------------------------------------- maths --- */
/* Noto Sans Math carries the glyphs the UI font has never heard of; Pango
   falls back per glyph, so the list only has to name a better first choice. */
.math { font-family: "Noto Sans Math", "Liberation Serif", "Noto Serif", serif; }
.math-card { padding: 2px 0; }
.math-display { font-size: 1.15em; padding: 2px 6px; }
.math-script { font-size: 0.74em; }
.math-bigop { padding: 1px 0; }
.math-bold { font-weight: bold; }
.math-italic { font-style: italic; }
.math-frac { padding: 0 2px; margin: 0 4px; }
.math-rule {
  min-height: 1px;
  background: alpha(currentColor, 0.8);
  margin: 2px 0;
}
.math-radical { font-size: 1.2em; }
.math-radicand {
  border-top: 1px solid alpha(currentColor, 0.8);
  padding: 1px 4px 0 2px;
  margin-top: 3px;
}
.math-limits { padding: 0 2px; margin: 0 3px; }
.math-grid { padding: 2px 6px; }
.math-sqrt { margin: 0 2px; }
.math-matrix { margin: 0 4px; }
.math-delim {
  min-width: 7px;
  margin: 0 3px;
  border-top: 1.5px solid alpha(currentColor, 0.8);
  border-bottom: 1.5px solid alpha(currentColor, 0.8);
}
.math-delim-left { border-left: 1.5px solid alpha(currentColor, 0.8); }
.math-delim-right { border-right: 1.5px solid alpha(currentColor, 0.8); }
.math-paren.math-delim-left  { border-radius: 16px 0 0 16px; }
.math-paren.math-delim-right { border-radius: 0 16px 16px 0; }
.math-brace.math-delim-left  { border-radius: 12px 0 0 12px; }
.math-brace.math-delim-right { border-radius: 0 12px 12px 0; }
.math-bar { border-top-width: 0; border-bottom-width: 0; min-width: 1px; }

/* ------------------------------------------------------------ tools ---- */
.tool-card {
  background: alpha(currentColor, 0.045);
  border: 1px solid alpha(currentColor, 0.09);
  border-radius: 10px;
}
.tool-head { padding: 6px 8px 6px 10px; }
.tool-name { font-weight: bold; font-size: 0.85em; }
.tool-summary { font-family: monospace; font-size: 0.85em; opacity: 0.72; }
.tool-body {
  border-top: 1px solid alpha(currentColor, 0.09);
  padding: 2px 0;
}
.tool-failed { border-color: alpha(@error_color, 0.45); }
.thinking-row { font-size: 0.9em; opacity: 0.6; }

/* ------------------------------------------- permissions and questions -- */
.perm-card {
  background: alpha(@accent_bg_color, 0.10);
  border: 1px solid alpha(@accent_bg_color, 0.45);
  border-radius: 12px;
  padding: 12px 14px;
}
.perm-title { font-weight: bold; }
.perm-detail { font-family: monospace; font-size: 0.85em; opacity: 0.8; }
.ask-chip {
  font-size: 0.75em;
  padding: 1px 9px;
  border-radius: 999px;
  background: alpha(currentColor, 0.12);
  opacity: 0.8;
}
.ask-question { font-weight: bold; padding: 2px 0 2px 0; }
.ask-option { border-radius: 8px; padding: 3px 6px 3px 2px; }
.ask-option:hover { background: alpha(currentColor, 0.07); }
.ask-label { font-weight: bold; }
.ask-desc { font-size: 0.88em; opacity: 0.7; }
.ask-other { font-size: 0.92em; }
.ask-hint { font-size: 0.8em; opacity: 0.55; padding: 0 2px; }

/* ---------------------------------------------------- slash commands --- */
.cmd-strip {
  background: @card_bg_color;
  border: 1px solid alpha(currentColor, 0.13);
  border-radius: 12px;
  padding: 4px;
}
.cmd-row { border-radius: 8px; padding: 3px 8px; min-height: 0; }
.cmd-row-active { background: alpha(@accent_bg_color, 0.18); }
.cmd-name { font-family: monospace; font-weight: bold; }
.cmd-args { font-family: monospace; font-size: 0.85em; opacity: 0.55; }
.cmd-desc { font-size: 0.85em; opacity: 0.62; }
.cmd-hint { font-size: 0.75em; opacity: 0.45; padding: 2px 8px 0 8px; }
.report-card {
  background: alpha(currentColor, 0.05);
  border: 1px solid alpha(currentColor, 0.12);
  border-radius: 12px;
  padding: 10px 14px;
}
.report-title { font-family: monospace; font-weight: bold; font-size: 0.9em; }

/* --------------------------------------------------------- sidebar ----- */
.session-name { font-weight: bold; }
.session-meta { font-size: 0.8em; opacity: 0.6; }
.dot { min-width: 8px; min-height: 8px; border-radius: 999px; }
.dot-busy    { background: @accent_bg_color; }
.dot-ready   { background: @success_color; }
.dot-waiting { background: @warning_color; }
.dot-asleep  { background: alpha(currentColor, 0.28); }

/* -------------------------------------------------------- composer ----- */
.composer {
  background: @card_bg_color;
  border: 1px solid alpha(currentColor, 0.13);
  border-radius: 14px;
  padding: 4px 4px 4px 10px;
}
.composer:focus-within { border-color: alpha(@accent_bg_color, 0.75); }
.composer-entry, .composer-entry text { background: transparent; }
.composer-hint { font-size: 0.78em; opacity: 0.45; padding: 4px 8px 0 8px; }
.send-button { border-radius: 10px; min-width: 32px; min-height: 32px; }
.jump-button { margin: 0 18px 12px 0; }

/* --------------------------------------------------- queued messages --- */
.queue-strip { padding: 0 2px; }
.queue-head { font-size: 0.78em; opacity: 0.5; padding: 0 8px 2px 8px; }
.queue-row {
  border-left: 3px solid alpha(@accent_bg_color, 0.5);
  border-radius: 4px;
  background: alpha(currentColor, 0.05);
}
.queue-edit { padding: 3px 8px; min-height: 0; font-size: 0.9em; }
.queue-index { font-family: monospace; font-size: 0.8em; }

.folder-chip {
  padding: 2px 10px;
  border-radius: 999px;
  background: alpha(currentColor, 0.08);
}
.folder-chip:hover { background: alpha(currentColor, 0.15); }

.status-chip {
  font-size: 0.8em;
  padding: 1px 9px;
  border-radius: 999px;
  background: alpha(currentColor, 0.10);
}
.monospace { font-family: monospace; }
.dim { opacity: 0.6; }
"""


def install_css():
    provider = Gtk.CssProvider()
    provider.load_from_string(CSS)
    display = Gdk.Display.get_default()
    if display:
        Gtk.StyleContext.add_provider_for_display(
            display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
