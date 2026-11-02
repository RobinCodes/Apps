"""Ariadne's own window: a header bar over the view, on the house's glass.

Everything Ariadne does is in view.py, which Mind Palace hosts too; this adds
only what a window of its own needs — the header bar, the menu, the size it
was closed at — and makes the view's `room` actions reachable from the header
as well as from inside it.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, GObject, Gtk  # noqa: E402

from . import __version__, housestyle, widgets  # noqa: E402
from .config import Config  # noqa: E402
from .view import ACCELERATORS, AriadneView  # noqa: E402


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Ariadne")
        self.config = Config()
        self.geometry = housestyle.Geometry(self, self.config, minimum=(360, 320))
        self.geometry.restore()

        self.view = AriadneView(self.config)
        self.insert_action_group("room", self.view.actions)

        header = Adw.HeaderBar()
        self.title_widget = Adw.WindowTitle(title="Ariadne", subtitle="")
        header.set_title_widget(self.title_widget)
        toggle = Gtk.ToggleButton(icon_name="sidebar-show-symbolic", tooltip_text="The list (F9)")
        self.view.split.bind_property("show-sidebar", toggle, "active",
                                      GObject.BindingFlags.BIDIRECTIONAL
                                      | GObject.BindingFlags.SYNC_CREATE)
        header.pack_start(toggle)
        keep = Gtk.Button(icon_name="document-new-symbolic",
                          tooltip_text="Keep a question (Ctrl+N)")
        keep.set_action_name("room.keep-question")
        header.pack_start(keep)
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", tooltip_text="Main menu",
                                       menu_model=self._menu()))
        self.view.on_title = self._show_title

        page = Adw.ToolbarView()
        page.add_top_bar(header)
        page.set_content(self.view)
        self.glazing = housestyle.Glazing(self, self.config, extra=widgets.CSS,
                                          app_class="ariadne")
        self.glazing.wrap(page)

        for name, handler in (("preferences", self.show_preferences),
                              ("about", self.show_about)):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, fn=handler: fn())
            self.add_action(action)
        if app is not None:
            for action, keys in ACCELERATORS:
                app.set_accels_for_action(action, keys)
            app.set_accels_for_action("win.preferences", ["<Control>comma"])
        self.connect("close-request", self._closing)
        self._show_title(self.view.current.title if self.view.current else "Ariadne")

    @property
    def toasts(self):
        return self.view.toasts

    def _menu(self):
        menu = Gio.Menu()
        making = Gio.Menu()
        making.append("Keep a Question", "room.keep-question")
        making.append("New Thread", "room.new-thread")
        menu.append_section(None, making)
        thread = Gio.Menu()
        thread.append("Add a Thought", "room.add-thought")
        thread.append("Arrange", "room.arrange")
        item = Gio.MenuItem.new("Think with Claude", "room.think")
        item.set_attribute_value("hidden-when", GLib.Variant.new_string("action-missing"))
        thread.append_item(item)
        menu.append_section(None, thread)
        about = Gio.Menu()
        about.append("Preferences", "win.preferences")
        about.append("Keyboard Shortcuts", "room.shortcuts")
        about.append("About Ariadne", "win.about")
        about.append("Quit", "app.quit")
        menu.append_section(None, about)
        return menu

    def _show_title(self, title):
        self.title_widget.set_title(title or "Ariadne")
        self.title_widget.set_subtitle("Ariadne" if title and title != "Ariadne" else "")
        self.set_title(f"{title} — Ariadne" if title and title != "Ariadne" else "Ariadne")

    def show_preferences(self):
        dialog = Adw.PreferencesDialog(title="Preferences")
        dialog.add(housestyle.look_page(self.config, self.glazing.restyle))
        dialog.present(self.view)

    def show_about(self):
        Adw.AboutDialog(
            application_name="Ariadne", application_icon="ariadne", version=__version__,
            developer_name="Robin", license_type=Gtk.License.MIT_X11,
            website="https://github.com/RobinCodes",
            comments=("The questions worth keeping, and the logical threads that answer "
                      "them.\n\nA question is kept the moment it is asked, and answered "
                      "slowly — with a thread of thoughts and the arrows between them.")
        ).present(self.view)

    def _closing(self, *_):
        self.view.save_state()
        self.geometry.save()
        return False
