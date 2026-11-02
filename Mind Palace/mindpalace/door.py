"""Doors: the apps that are not rooms yet, and the rooms that would not open.

An app with no `room.py` still has its place in the sidebar. Its page says what
it is and opens it in a window of its own, on the same interpreter, through the
app's own launcher — so it is the app exactly as it runs by itself, and a door
is never less than the Start menu would have given you. Both of these keep the
room contract, so the palace has only one kind of thing to deal with.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk  # noqa: E402

from . import widgets  # noqa: E402


def _status_page(spec, description: str) -> Adw.StatusPage:
    page = Adw.StatusPage(title=spec.title, description=description, vexpand=True)
    picture = widgets.texture(spec.icon) if spec.icon else None
    if picture is not None:
        page.set_paintable(picture)
    else:
        page.set_icon_name(spec.icon_name or widgets.FALLBACK_ICON)
    return page


class DoorRoom:
    """An app that opens in its own window. `launch(spec, target)` does the opening."""

    def __init__(self, spec, launch):
        self.spec = spec
        self.launch = launch
        description = spec.comment or spec.generic or ""
        if spec.unavailable:
            description = f"{description}\n\n{spec.unavailable}".strip()
        self.widget = _status_page(spec, description)
        self.button = Gtk.Button(label=f"Open {spec.title}", halign=Gtk.Align.CENTER,
                                 sensitive=not spec.unavailable,
                                 css_classes=["pill", "suggested-action"])
        self.button.connect("clicked", lambda _b: self.launch(self.spec, None))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.append(self.button)
        box.append(widgets_hint("It opens in a window of its own."))
        self.widget.set_child(box)

    def open(self, target) -> bool:
        return False if self.spec.unavailable else self.launch(self.spec, str(target))


def widgets_hint(text: str) -> Gtk.Label:
    return Gtk.Label(label=text, justify=Gtk.Justification.CENTER, wrap=True,
                     css_classes=["dim-label", "caption"])


class BrokenRoom:
    """A `room.py` that raised. The palace goes on, and the app can still open by itself."""

    def __init__(self, spec, problem: str, launch):
        self.spec = spec
        self.launch = launch
        self.problem = problem
        self.widget = _status_page(
            spec, f"{spec.title} could not be opened as a room.\n\n{problem}")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        if spec.launcher is not None and not spec.unavailable:
            button = Gtk.Button(label="Open it in its own window",
                                halign=Gtk.Align.CENTER, css_classes=["pill"])
            button.connect("clicked", lambda _b: self.launch(self.spec, None))
            box.append(button)
        self.widget.set_child(box)

    def open(self, target) -> bool:
        if self.spec.launcher is None or self.spec.unavailable:
            return False
        return self.launch(self.spec, str(target))
