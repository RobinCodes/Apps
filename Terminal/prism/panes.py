"""Splitting one tab into several terminals.

A tab holds a tree: either a single terminal, or a Gtk.Paned whose two halves
are each another tree of the same kind. Splitting replaces a leaf with a
Paned containing that leaf and a new one; closing a leaf replaces its Paned
with whichever sibling is left. That is the whole model, and keeping it to
two children per node is what makes both operations three lines rather than a
layout engine.

The gaps between panes are the Paned's own separators, widened and left
transparent, so what shows between two sheets of glass is the light behind
them. There is no drawn divider at all — the shadow each pane casts is the
divider, which is the entire reason the panes have shadows.

Directional focus (the Alt+arrow kind) is geometric rather than structural:
it asks where every pane is on screen and picks the nearest one in that
direction. Walking the tree instead gives the answer that is right in the
data structure and wrong on the screen as soon as the splits are uneven.
"""

from __future__ import annotations

import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Graphene", "1.0")

from gi.repository import GLib, GObject, Graphene, Gtk  # noqa: E402

from . import shell  # noqa: E402
from .terminal import TerminalPane  # noqa: E402


class PaneGroup(Gtk.Box):
    """Every terminal in one tab, and which of them has the keyboard."""

    __gtype_name__ = "PrismPaneGroup"

    __gsignals__ = {
        # The last pane closed: the tab has nothing left in it.
        "emptied": (GObject.SignalFlags.RUN_FIRST, None, ()),
        # The focused pane changed, or its title did.
        "changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "activity": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "open-uri": (GObject.SignalFlags.RUN_LAST, bool, (str,)),
    }

    def __init__(self, config, scheme, light, cwd=None, command=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._config = config
        self._scheme = scheme
        self._light = light
        self._panes: list[TerminalPane] = []
        self._active: TerminalPane | None = None

        first = self._make_pane(cwd, command)
        self.append(first)
        self._set_active(first)

    # ------------------------------------------------------------ panes --

    def _make_pane(self, cwd=None, command=None) -> TerminalPane:
        # Splits never inherit the command: a pane opened beside `prism -e
        # htop` is a shell, not a second htop.
        pane = TerminalPane(self._config, self._scheme, self._light, cwd=cwd,
                            command=command)
        pane.set_hexpand(True)
        pane.set_vexpand(True)
        pane.connect("title-changed", lambda _p: self.emit("changed"))
        pane.connect("activity", lambda _p: self.emit("activity"))
        pane.connect("exited", self._on_exited)
        pane.connect("open-uri", lambda _p, uri: self.emit("open-uri", uri))

        focus = Gtk.EventControllerFocus()
        focus.connect("enter", lambda _c, p=pane: self._set_active(p))
        pane.terminal.add_controller(focus)

        self._panes.append(pane)
        return pane

    @property
    def panes(self) -> list[TerminalPane]:
        return list(self._panes)

    @property
    def active(self) -> TerminalPane | None:
        return self._active

    def _set_active(self, pane: TerminalPane | None):
        if pane is self._active:
            return
        self._active = pane
        many = len(self._panes) > 1
        for other in self._panes:
            other.set_focused(not many or other is pane)
        self.emit("changed")

    def focus_active(self):
        if self._active is not None:
            self._active.grab_terminal_focus()

    # ----------------------------------------------------------- splits --

    def split(self, horizontal: bool):
        """A new terminal beside (horizontal) or below the focused one.

        The new one starts in the same directory as the old, which is the
        only reason anyone splits a terminal rather than opening a tab.
        """
        target = self._active
        if target is None:
            return None
        cwd = target.cwd()
        parent = target.get_parent()
        orientation = (Gtk.Orientation.HORIZONTAL if horizontal
                       else Gtk.Orientation.VERTICAL)

        paned = Gtk.Paned(orientation=orientation, wide_handle=True,
                          resize_start_child=True, resize_end_child=True,
                          shrink_start_child=False, shrink_end_child=False)
        paned.add_css_class("pane-split")
        paned.set_hexpand(True)
        paned.set_vexpand(True)

        fresh = self._make_pane(cwd)
        self._replace(parent, target, paned)
        paned.set_start_child(target)
        paned.set_end_child(fresh)

        # Half and half, once the widget knows how big it is.
        def halve(*_args):
            extent = (paned.get_width() if horizontal else paned.get_height())
            if extent > 0:
                paned.set_position(extent // 2)
            return False

        GLib.idle_add(halve)

        fresh.grab_terminal_focus()
        self._set_active(fresh)
        self.emit("changed")
        return fresh

    def _replace(self, parent, child, replacement):
        """Put `replacement` where `child` is, in whatever kind of parent."""
        if isinstance(parent, Gtk.Paned):
            # Taking a Paned out of a Paned makes GTK print "Error finding
            # last focus widget of GtkPaned": on unparenting, GtkPaned is
            # asked to forget its focus child, and its handler for that
            # warns whenever it cannot work out which half the focus used to
            # be in — which it never can, once the half in question is on
            # its way out. Pointing it at the other half first means the
            # question is never asked. It is cosmetic, but it is a warning
            # on everybody's terminal every time they close a split.
            survivor = (parent.get_end_child() if parent.get_start_child() is child
                        else parent.get_start_child())
            if survivor is not None and survivor is not child:
                parent.set_focus_child(survivor)
            if parent.get_start_child() is child:
                parent.set_start_child(None)
                parent.set_start_child(replacement)
            else:
                parent.set_end_child(None)
                parent.set_end_child(replacement)
        else:
            self.remove(child)
            self.append(replacement)

    def close_active(self):
        if self._active is not None:
            self.close(self._active)

    def close(self, pane: TerminalPane):
        """Take one terminal out, and collapse the split it was half of."""
        if pane not in self._panes:
            return
        self._panes.remove(pane)
        parent = pane.get_parent()

        if not self._panes:
            if isinstance(parent, Gtk.Paned):
                self._detach(parent, pane)
            else:
                self.remove(pane)
            self._active = None
            self.emit("emptied")
            return

        if isinstance(parent, Gtk.Paned):
            sibling = (parent.get_end_child() if parent.get_start_child() is pane
                       else parent.get_start_child())
            # Move the keyboard into the half that is staying, before taking
            # the other one out. GtkPaned works out which of its two children
            # last had the focus by walking up from whatever the window's
            # focus is now, and warns when that walk reaches neither of them
            # — which is what happens if the focus has already been sent to a
            # pane in some other split, or left nowhere. Landing it on the
            # sibling keeps the walk inside this Paned, and is where the
            # focus wanted to go anyway: next to what you just closed.
            inheritor = self._pane_within(sibling)
            if inheritor is not None:
                inheritor.grab_terminal_focus()
            self._detach(parent, pane)
            if sibling is not None:
                parent.set_start_child(None)
                parent.set_end_child(None)
                self._replace(parent.get_parent(), parent, sibling)
        else:
            self.remove(pane)

        if self._active is pane or self._active not in self._panes:
            self._active = None
            self._set_active(self._panes[-1])
        self.focus_active()
        self.emit("changed")

    def _pane_within(self, widget):
        """The first terminal in `widget`, which may be one itself."""
        if widget is None:
            return None
        for pane in self._panes:
            if pane is widget or pane.is_ancestor(widget):
                return pane
        return None

    @staticmethod
    def _detach(paned: Gtk.Paned, child):
        if paned.get_start_child() is child:
            paned.set_start_child(None)
        elif paned.get_end_child() is child:
            paned.set_end_child(None)

    # ------------------------------------------------------------ focus --

    def _centres(self):
        out = []
        for pane in self._panes:
            ok, origin = pane.compute_point(self, Graphene.Point().init(0, 0))
            if not ok:
                continue
            out.append((pane,
                        origin.x + pane.get_width() / 2,
                        origin.y + pane.get_height() / 2))
        return out

    def focus_direction(self, dx: int, dy: int) -> bool:
        """The nearest pane that way, or nothing when there is none."""
        if self._active is None:
            return False
        places = dict((pane, (x, y)) for pane, x, y in self._centres())
        here = places.get(self._active)
        if here is None:
            return False
        best, best_cost = None, None
        for pane, (x, y) in places.items():
            if pane is self._active:
                continue
            along = (x - here[0]) * dx + (y - here[1]) * dy
            if along <= 1:
                continue        # not in that direction at all
            across = abs((x - here[0]) * dy + (y - here[1]) * dx)
            # Distance along the direction asked for, plus a heavy penalty
            # for drifting sideways, so "right" never means "down and right".
            cost = along + across * 3
            if best_cost is None or cost < best_cost:
                best, best_cost = pane, cost
        if best is None:
            return False
        best.grab_terminal_focus()
        self._set_active(best)
        return True

    def focus_cycle(self, step: int = 1) -> bool:
        if len(self._panes) < 2 or self._active is None:
            return False
        index = (self._panes.index(self._active) + step) % len(self._panes)
        self._panes[index].grab_terminal_focus()
        self._set_active(self._panes[index])
        return True

    # ----------------------------------------------------------- upkeep --

    def _on_exited(self, pane, _status):
        # Not straight away: this is running inside VTE's own `child-exited`
        # emission, and taking the widget apart underneath the signal that
        # is still being delivered is how a terminal emulator segfaults when
        # you type `exit`.
        GLib.idle_add(self.close, pane)

    def apply_config(self, config, scheme):
        self._config = config
        self._scheme = scheme
        for pane in self._panes:
            pane.apply_config(config, scheme)

    def set_light(self, light):
        """Adopt another window's light.

        A tab dragged into a different window keeps its panes, and the panes
        hold a reference to the light they were built with — which belongs to
        the window they came from, is positioned in that window's
        coordinates, and would keep it alive after it closed. So the window
        that receives a tab hands it its own.
        """
        self._light = light
        for pane in self._panes:
            pane.set_light(light)

    def title(self) -> str:
        pane = self._active
        if pane is None:
            return "Prism"
        return shell.tab_name(pane.title, pane.cwd())

    def busy_command(self) -> str | None:
        """What is running, anywhere in this tab — for the close prompt."""
        for pane in self._panes:
            name = pane.foreground_command()
            if name:
                return name
        return None
