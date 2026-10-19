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

from gi.repository import Adw, Gdk, GLib, Gtk, Pango  # noqa: E402

from . import config, housestyle  # noqa: E402
from .backend import BYPASS_MODE, PERMISSION_MODES, plain_text  # noqa: E402
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
    "folder-download-symbolic", "folder-open-symbolic",
    "folder-symbolic", "go-bottom-symbolic", "go-up-symbolic", "list-add-symbolic",
    "mail-send-symbolic",
    "media-playback-stop-symbolic", "non-starred-symbolic", "object-select-symbolic",
    "open-menu-symbolic",
    "pan-down-symbolic", "pan-end-symbolic", "sidebar-show-symbolic", "starred-symbolic",
    "view-list-symbolic",
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


# Why an ask arrived, said the way a person would. The CLI decides most of
# these *before* it looks at the permission mode, which is the whole reason
# this exists: "Bypass all checks" and a question on screen read as the app
# ignoring the setting, until something says which of these it was.
ASK_REASONS = {
    "safetyCheck": "a safety check will not sign this off without a person",
    "hook": "a PreToolUse hook asked for you",
    "sandboxOverride": "it reads outside the working folder",
    "workingDir": "it touches a path outside the working folder",
    "classifier": "the classifier would not decide it either way",
    "subcommandResults": "one of the commands in that line needs a look",
    "permissionPromptTool": "the permission prompt tool asked for you",
    "asyncAgent": "a background agent cannot answer this for you",
    "rule": "a permission rule covers this tool",
    "mode": "the permission mode does not cover it",
}


def ask_reason(request):
    """The most specific thing that can be said about one permission ask."""
    rule = request.get("ask_rule") or {}
    if rule:
        where = plain_text(rule.get("source"), 40)
        return ("a permissions.ask rule you wrote covers this"
                + (f" ({where})" if where else ""))
    if request.get("interactive"):
        # The protocol is explicit that a plain Allow is the wrong surface for
        # these; we have no card of the tool's own to put up instead, so the
        # least we can do is not let Allow look like an answer.
        return "this tool asks you itself — Allow only lets it ask"
    if request.get("classifier_approvable") is False:
        return ASK_REASONS["safetyCheck"]
    kind = request.get("reason_type") or ""
    if kind in ASK_REASONS:
        return ASK_REASONS[kind]
    server = plain_text(request.get("mcp_server"), 60)
    if server:
        return f"{server} asks before every call"
    return ""


def permission_reason(request, mode=""):
    """One line for the card: why this is being asked, and why now.

    Under "Bypass all checks" it leads with the mode, because that is the
    question being asked of the app — not "may this run", but "why are you
    asking me at all".
    """
    why = ask_reason(request)
    if not why:
        return ""
    detail = plain_text(request.get("reason"))
    if detail and detail.lower() not in why.lower():
        why = f"{why} — {detail}"
    if mode == BYPASS_MODE:
        label = dict(PERMISSION_MODES).get(BYPASS_MODE, BYPASS_MODE)
        return f"“{label}” does not cover this: {why}."
    return f"Asked because {why}."


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
    # The home folder itself, not only what is under it: as the parent line in
    # the folder picker it is the commonest one of all.
    if path == home:
        return "~"
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


class FolderRow(Gtk.ListBoxRow):
    """One folder in the picker, or the row that opens the file chooser."""

    def __init__(self, path, icon, title, subtitle):
        super().__init__()
        self.path = path  # None on the "Other location" row

        box = Gtk.Box(spacing=12, margin_top=8, margin_bottom=8,
                      margin_start=12, margin_end=12)
        box.append(Gtk.Image(icon_name=icon, valign=Gtk.Align.CENTER))

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        text.append(Gtk.Label(label=title, xalign=0,
                              ellipsize=Pango.EllipsizeMode.END))
        if subtitle:
            # Middle, not end: the tail of a path is what tells two folders of
            # the same name apart, so that is the part to keep.
            text.append(Gtk.Label(label=subtitle, xalign=0,
                                  ellipsize=Pango.EllipsizeMode.MIDDLE,
                                  css_classes=["session-meta", "monospace"]))
        box.append(text)
        box.append(Gtk.Image(icon_name="pan-end-symbolic", valign=Gtk.Align.CENTER,
                             css_classes=["dim"]))
        self.set_child(box)
        self.set_tooltip_text(path or "Browse the filesystem")


def folder_labels(path):
    """How a folder names itself in the picker: its own name, then where it is.

    The basename carries the identity — two checkouts both called `src` are
    told apart by the line underneath, not the line above. A folder with no
    parent to show, `/` being the one that matters here, gets no second line
    rather than a line repeating itself.
    """
    parent = os.path.dirname(path)
    return (
        os.path.basename(path) or shorten_path(path),
        shorten_path(parent) if parent and parent != path else "",
    )


def choose_folder(parent, paths, on_pick, on_browse, heading="Working folder"):
    """Offer the folders already in use before offering the whole filesystem.

    Choosing where a chat runs is nearly always choosing one of the handful of
    folders you actually work in, and a file chooser makes that the slow path:
    it opens somewhere, and everything else is navigation. Here each of those
    folders is one click, and "Other location" still reaches the rest of the
    disk for the genuinely new one.
    """
    dialog = Adw.AlertDialog(heading=heading)

    listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE,
                          css_classes=["boxed-list"])
    for path in paths:
        title, subtitle = folder_labels(path)
        listbox.append(FolderRow(path, "folder-symbolic", title, subtitle))
    listbox.append(FolderRow(None, "folder-open-symbolic", "Other location…",
                             "Browse for a folder"))

    # Tall enough for the shortlist, scrolling rather than growing past it, so
    # the dialog cannot end up taller than the window it sits in.
    frame = Gtk.ScrolledWindow(propagate_natural_height=True, max_content_height=330,
                               hscrollbar_policy=Gtk.PolicyType.NEVER,
                               child=listbox, margin_top=6)
    dialog.set_extra_child(frame)
    dialog.add_response("cancel", "Cancel")
    dialog.set_close_response("cancel")

    def activated(_box, row):
        # Close first: both branches can put another dialog on screen, and two
        # at once leaves the new one behind the old.
        dialog.close()
        if row.path is None:
            on_browse()
        else:
            on_pick(row.path)

    listbox.connect("row-activated", activated)
    # Named, the way the other composite widgets name their parts: a
    # ScrolledWindow puts a Viewport in between, and nothing should have to
    # know that to reach the rows.
    dialog.listbox = listbox

    dialog.present(parent)
    # So the list is immediately arrow-navigable and Enter picks the top folder.
    focus_soon(listbox)
    return dialog


class GroupRow(Gtk.ListBoxRow):
    """One of the three sections, ticked if this chat is already in it."""

    def __init__(self, name, current, icon):
        super().__init__()
        self.group = name

        box = Gtk.Box(spacing=10, margin_top=6, margin_bottom=6,
                      margin_start=10, margin_end=10)
        box.append(Gtk.Image(icon_name=icon, valign=Gtk.Align.CENTER))
        box.append(Gtk.Label(label=config.group_label(name), xalign=0, hexpand=True,
                             ellipsize=Pango.EllipsizeMode.END))
        # A tick rather than a selected row: the list is a set of choices, and
        # one of them is already true of this chat.
        self.tick = Gtk.Image(icon_name="object-select-symbolic",
                              valign=Gtk.Align.CENTER)
        self.tick.set_opacity(1.0 if name == current else 0.0)
        box.append(self.tick)
        self.set_child(box)


class TagRow(Gtk.ListBoxRow):
    """One tag and whether this chat wears it, or the row that makes a new one.

    The tick is drawn transparent rather than taken away, so ticking one does
    not change the width of a menu you are still reading, and so the names
    line up with the groups above whichever way round they are.
    """

    def __init__(self, name, worn=False):
        super().__init__()
        self.tag = name  # None on the "New tag…" row

        box = Gtk.Box(spacing=10, margin_top=6, margin_bottom=6,
                      margin_start=10, margin_end=10)
        box.append(Gtk.Image(icon_name="list-add-symbolic" if name is None
                             else "user-bookmarks-symbolic",
                             valign=Gtk.Align.CENTER))
        box.append(Gtk.Label(label="New tag…" if name is None else config.tag_label(name),
                             xalign=0, hexpand=True,
                             ellipsize=Pango.EllipsizeMode.END))
        self.tick = Gtk.Image(icon_name="object-select-symbolic",
                              valign=Gtk.Align.CENTER)
        self.tick.set_opacity(1.0 if worn else 0.0)
        box.append(self.tick)
        self.set_child(box)

    @property
    def worn(self):
        return self.tick.get_opacity() > 0.5

    def flip(self):
        self.tick.set_opacity(0.0 if self.worn else 1.0)


def row_menu(tags, group, worn, on_group, on_tag, on_new):
    """Everything the sidebar can do to one chat, in one menu.

    Two halves, because they answer two different questions. Above: which of
    the three sections the chat is filed in — one at a time, so picking one
    is the end of the matter and the menu closes on it. Below: what it is
    about, as many as apply, so a tag toggles in place and the menu stays
    open for the next one. Favourites is up there rather than on a button of
    its own; see SessionRow for why.
    """
    groups = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
    for name, icon in ((config.FAVOURITE_GROUP, "starred-symbolic"),
                       ("", "view-list-symbolic"),
                       (config.ARCHIVE_GROUP, "folder-download-symbolic")):
        groups.append(GroupRow(name, group, icon))

    taglist = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
    for name in tags:
        taglist.append(TagRow(name, name in worn))
    taglist.append(TagRow(None))

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    box.append(groups)
    box.append(Gtk.Separator(margin_top=4, margin_bottom=4))
    box.append(Gtk.Label(label="Tags", xalign=0, css_classes=["group-header"],
                         margin_start=12, margin_end=12, margin_bottom=2))
    box.append(taglist)

    frame = Gtk.ScrolledWindow(propagate_natural_height=True, max_content_height=380,
                               hscrollbar_policy=Gtk.PolicyType.NEVER, child=box)
    popover = Gtk.Popover(child=frame, css_classes=["menu"])

    def group_activated(_box, row):
        # Down first: filing a chat rebuilds the sidebar under this popover,
        # and a popover whose row has been destroyed takes the keyboard with
        # it on the way out.
        popover.popdown()
        on_group(row.group)

    def tag_activated(_box, row):
        if row.tag is None:
            # A dialog follows, and a popover still open underneath it takes
            # the keyboard focus with it.
            popover.popdown()
            on_new()
            return
        row.flip()
        on_tag(row.tag)

    groups.connect("row-activated", group_activated)
    taglist.connect("row-activated", tag_activated)
    # Named, the way the folder picker names its own: a ScrolledWindow puts a
    # Viewport in between, and nothing should have to know that to reach them.
    popover.listbox = groups
    popover.taglist = taglist
    return popover


def tag_chip(label, active, on_click, tooltip=""):
    """One tag in the filter above the sidebar.

    A Button and not a ToggleButton: the row of them is rebuilt whenever the
    filter changes, and a toggle that is restyled by being rebuilt would emit
    its own signal on the way in.
    """
    button = Gtk.Button(label=label, tooltip_text=tooltip,
                        css_classes=["tag-chip", "tag-chip-on"] if active
                        else ["tag-chip"])
    button.connect("clicked", lambda _b: on_click())
    return button


def focus_soon(widget):
    """Focus a widget once the dialog it lives in is on screen.

    Once, and not once per idle: `grab_focus()` returns True to say it worked,
    and an idle source reads True as "call me again". Handing it the method
    directly gives you a focus grab every time the loop goes round, which in
    an entry re-selects the text under whatever you have just typed — so a
    rename could never be more than one character long.
    """
    def grab():
        # And not at all if the dialog has gone in the meantime -- an Escape
        # before the idle runs, or a card replaced underneath it. Focusing a
        # widget that is no longer in a window logs a GTK critical and
        # focuses nothing, so ask whether it still has one.
        if widget.get_root() is not None:
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
.perm-why { font-size: 0.86em; opacity: 0.72; }
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
.group-header {
  font-size: 0.78em;
  font-weight: bold;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  opacity: 0.5;
}
/* Faint until the pointer is on them: the sidebar should read as a list of
   names, with the buttons there when you go looking for them. */
.row-action {
  min-width: 22px;
  min-height: 22px;
  padding: 0;
  margin: 0;
  opacity: 0.35;
}
.row-action:hover { opacity: 1; }
.row-action-on { opacity: 0.95; }
.archive-row { opacity: 0.75; }

/* Tags: the line under a chat's name, and the filter above the list. The
   chips are small and quiet until one is on, because the filter is a thing
   you reach for occasionally and read past the rest of the time. */
.session-tags { font-size: 0.78em; opacity: 0.72; }
.tag-bar { padding: 0 10px 6px 10px; }
.tag-chip {
  font-size: 0.78em;
  min-height: 0;
  padding: 1px 9px;
  border: none;
  box-shadow: none;
  border-radius: 999px;
  background: alpha(currentColor, 0.09);
}
.tag-chip:hover { background: alpha(currentColor, 0.17); }
.tag-chip-on {
  background: @accent_bg_color;
  color: @accent_fg_color;
}
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
