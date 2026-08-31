"""Shared UI pieces: diff rendering, confirmation dialogs, small helpers."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, GLib, Gtk, Pango  # noqa: E402


def _rgba(spec):
    colour = Gdk.RGBA()
    colour.parse(spec)
    return colour


# Alpha-blended so the same values read correctly on light and dark backgrounds.
ADD_BG = _rgba("rgba(46,160,67,0.20)")
DEL_BG = _rgba("rgba(248,81,73,0.20)")
META_FG = _rgba("rgba(128,128,128,0.95)")

MAX_DIFF_LINES = 4000  # past this a diff is unreadable anyway, and slow to render


def _overlay(widget):
    """The nearest ToastOverlay above this widget, if there is one."""
    node = widget
    while node is not None and not isinstance(node, Adw.ToastOverlay):
        node = node.get_parent()
    return node


def toast(widget, message, timeout=3):
    """Find the nearest ToastOverlay and post a message on it."""
    node = _overlay(widget)
    if node is not None:
        node.add_toast(Adw.Toast(title=message, timeout=timeout))


def undo_toast(widget, message, label, callback, timeout=6):
    """A toast with the way back attached.

    For changes that are easier to take back than to ask about first: the
    dialog confirm() would put in front of one of these costs more than the
    mistake does.
    """
    node = _overlay(widget)
    if node is None:
        return
    notice = Adw.Toast(title=message, timeout=timeout, button_label=label)
    notice.connect("button-clicked", lambda *_: callback())
    node.add_toast(notice)


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


def prompt(parent, heading, body, action_label, callback, placeholder="", text=""):
    """Single-line text prompt. callback(value) runs on confirm."""
    dialog = Adw.AlertDialog(heading=heading, body=body)
    entry = Gtk.Entry(placeholder_text=placeholder, text=text, activates_default=True)
    entry.set_margin_top(6)
    entry.set_margin_start(12)
    entry.set_margin_end(12)
    entry.set_margin_bottom(6)
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
    GLib.idle_add(entry.grab_focus)


def open_url(widget, url):
    if not url:
        return
    try:
        Gtk.UriLauncher(uri=url).launch(widget.get_root(), None, None)
    except Exception:  # noqa: BLE001 - falls back to xdg-open below
        Gtk.show_uri(widget.get_root(), url, Gdk.CURRENT_TIME)


def status_page(icon, title, description, child=None):
    page = Adw.StatusPage(icon_name=icon, title=title, description=description)
    if child:
        page.set_child(child)
    return page


def pill(text, css=None):
    label = Gtk.Label(label=text, css_classes=["pill-badge"] + ([css] if css else []))
    label.set_valign(Gtk.Align.CENTER)
    return label


def diff_text_view(text):
    """A monospace, read-only view of one hunk with +/- lines tinted."""
    view = Gtk.TextView(
        editable=False,
        cursor_visible=False,
        monospace=True,
        wrap_mode=Gtk.WrapMode.NONE,
        top_margin=4,
        bottom_margin=4,
        left_margin=8,
        right_margin=8,
    )
    buf = view.get_buffer()
    tag_add = buf.create_tag("add", background_rgba=ADD_BG)
    tag_del = buf.create_tag("del", background_rgba=DEL_BG)
    tag_meta = buf.create_tag("meta", style=Pango.Style.ITALIC, foreground_rgba=META_FG)

    lines = text.splitlines()
    if len(lines) > MAX_DIFF_LINES:
        lines = lines[:MAX_DIFF_LINES] + [f"… {len(lines) - MAX_DIFF_LINES} more lines not shown"]
    for i, line in enumerate(lines):
        end = buf.get_end_iter()
        payload = line + ("\n" if i < len(lines) - 1 else "")
        if line.startswith("+"):
            buf.insert_with_tags(end, payload, tag_add)
        elif line.startswith("-"):
            buf.insert_with_tags(end, payload, tag_del)
        elif line.startswith("\\") or line.startswith("@@"):
            buf.insert_with_tags(end, payload, tag_meta)
        else:
            buf.insert(end, payload)
    return view


class HunkRow(Gtk.Box):
    """One hunk of a diff, with its own action buttons."""

    def __init__(self, index, hunk_text, actions, on_action):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, css_classes=["card", "hunk-row"])
        self.index = index

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, css_classes=["hunk-head"])
        first_line = hunk_text.splitlines()[0] if hunk_text else "@@"
        title = Gtk.Label(label=first_line, xalign=0, hexpand=True, css_classes=["dim-label", "monospace"])
        title.set_ellipsize(Pango.EllipsizeMode.END)
        header.append(title)
        for label, action_id, css in actions:
            btn = Gtk.Button(label=label, css_classes=["flat", "small-button"] + ([css] if css else []))
            btn.connect("clicked", lambda _b, a=action_id: on_action(a, self.index))
            header.append(btn)
        self.append(header)

        body = "\n".join(hunk_text.splitlines()[1:])
        self.append(diff_text_view(body))


class DiffView(Gtk.ScrolledWindow):
    """Renders a one-file diff as a stack of individually actionable hunks."""

    def __init__(self):
        super().__init__(hexpand=True, vexpand=True)
        self.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.on_hunk_action = None
        self._box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=10,
            margin_top=10, margin_bottom=10, margin_start=10, margin_end=10,
        )
        self.set_child(self._box)
        self.placeholder("text-x-generic-symbolic", "No file selected", "Pick a file to see its diff.")

    def _reset(self):
        child = self._box.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self._box.remove(child)
            child = nxt

    def placeholder(self, icon, title, description):
        self._reset()
        page = Adw.StatusPage(icon_name=icon, title=title, description=description)
        page.set_vexpand(True)
        self._box.append(page)

    def set_diff(self, header, hunks, actions=(), note=None):
        self._reset()
        if note:
            banner = Gtk.Label(label=note, xalign=0, css_classes=["dim-label"], wrap=True)
            self._box.append(banner)
        if not hunks:
            body = "This file has no textual changes to show — it may be binary, empty, or a mode change."
            page = Adw.StatusPage(icon_name="dialog-information-symbolic", title="Nothing to display", description=body)
            page.set_vexpand(True)
            self._box.append(page)
            return
        for i, hunk in enumerate(hunks):
            self._box.append(HunkRow(i, hunk, actions, self._fire))

    def set_plain(self, text):
        """A whole diff with no per-hunk actions — used by the history tab."""
        self._reset()
        if not text.strip():
            self.placeholder("dialog-information-symbolic", "Empty diff", "This commit changes no file contents.")
            return
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["card"])
        card.append(diff_text_view(text))
        self._box.append(card)

    def _fire(self, action, index):
        if self.on_hunk_action:
            self.on_hunk_action(action, index)


CSS = """
.pill-badge {
  padding: 1px 7px;
  border-radius: 999px;
  font-size: 0.78em;
  font-weight: bold;
  background: alpha(currentColor, 0.12);
}
.badge-ahead  { color: #2ec27e; }
.badge-behind { color: #e5a50a; }
.badge-dirty  { color: #e66100; }
.badge-clean  { color: alpha(currentColor, 0.5); }
.badge-ci-ok   { color: #2ec27e; }
.badge-ci-fail { color: #e01b24; }
.hunk-row { padding: 0; }
.hunk-head {
  padding: 4px 4px 4px 10px;
  border-bottom: 1px solid alpha(currentColor, 0.12);
}
.small-button { padding: 1px 8px; font-size: 0.85em; min-height: 0; }
.monospace { font-family: monospace; }
.repo-row-path { font-size: 0.82em; }
.file-status {
  font-family: monospace;
  font-weight: bold;
  min-width: 16px;
}
.st-M { color: #e5a50a; }
.st-A { color: #2ec27e; }
.st-D { color: #e01b24; }
.st-R { color: #3584e4; }
.st-U { color: #e01b24; }
.commit-sha { font-family: monospace; font-size: 0.85em; }
.stage-button {
  font-family: monospace;
  font-weight: bold;
  min-width: 22px;
  padding: 0 6px;
}
.lint-warning {
  color: #e5a50a;
  font-size: 0.88em;
}
"""


def install_css():
    provider = Gtk.CssProvider()
    provider.load_from_string(CSS)
    display = Gdk.Display.get_default()
    if display:
        Gtk.StyleContext.add_provider_for_display(
            display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
