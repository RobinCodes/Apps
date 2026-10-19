"""The keyboard shortcuts, as a window.

Built from the table in `shortcuts.py`, so it cannot fall out of step with
what the keys actually do. `Gtk.ShortcutsWindow` would do most of this, but it
is deprecated in GTK 4.18 and gone from the plan for 5, and it insists on its
own chrome — which in an app whose whole look is hand-drawn glass is the one
window that would arrive looking like a different program.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk  # noqa: E402

from . import shortcuts, widgets  # noqa: E402

# What the "applies to" column says. Anything marked "all" says nothing: most
# rows are, and a column of "all" would be noise.
SCOPES = {"photo": "pictures", "video": "video", "edit": "while editing"}


class ShortcutsWindow(Adw.Dialog):
    """Every key, in groups, in one scrolling list."""

    __gtype_name__ = "TheEyeShortcutsWindow"

    def __init__(self):
        super().__init__(title="Keyboard Shortcuts",
                         content_width=680, content_height=720)
        page = widgets.column(spacing=0)

        header = Adw.HeaderBar()
        header.add_css_class("glass-flat")
        page.append(header)

        columns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=26,
                          margin_start=22, margin_end=22,
                          margin_top=10, margin_bottom=22,
                          homogeneous=True)
        left = widgets.column(spacing=0)
        right = widgets.column(spacing=0)
        columns.append(left)
        columns.append(right)

        # Split so that the two columns come out roughly even in height
        # rather than by count: Playing has twenty-four rows and Window has
        # six, and splitting the groups in half would leave one column empty
        # below the fold.
        groups = [(name, shortcuts.in_group(name)) for name in shortcuts.GROUPS]
        total = sum(len(rows) for _n, rows in groups)
        running = 0
        for name, rows in groups:
            target = left if running < total / 2 else right
            target.append(self._group(name, rows))
            running += len(rows) + 2

        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                      vexpand=True)
        scroller.set_child(columns)
        page.append(scroller)

        note = widgets.label(
            "Left and Right move through the folder when a picture is on "
            "screen, and seek when a video is playing.",
            "caption", xalign=0.0, wrap=True,
            margin_start=22, margin_end=22, margin_bottom=14)
        page.append(note)
        self.set_child(page)

    def _group(self, name, rows):
        box = widgets.column(spacing=2, margin_top=16)
        box.append(widgets.label(name, "heading", xalign=0.0, margin_bottom=4))
        for _action, keys, label, _group, scope in rows:
            if not keys:
                continue
            text = label
            if scope in SCOPES:
                text += f"   ({SCOPES[scope]})"
            box.append(widgets.row(
                widgets.label(text, xalign=0.0, hexpand=True, wrap=True),
                widgets.label(shortcuts.describe(keys), "mono", "dim",
                              xalign=1.0),
                spacing=12))
        return box
