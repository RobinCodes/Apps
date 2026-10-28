"""The window: the text on the left, the page on the right, the outline beside.

Owns everything that is about the app rather than about one document — the
tabs, the files, the menus, the keyboard — and forwards the rest to whichever
document is in front.

Two things here are shaped by the machine this was written on.

*It has to fit in a tiling column.* The desktop is niri on a 1366-wide screen,
where the useful column widths are about 455, 683 and 900 pixels. So the
header carries icons rather than labelled buttons, the outline is a pane that
collapses, and an `Adw.Breakpoint` at 700px folds the sidebar away and turns
the split vertical. The window's minimum width is checked by the test suite,
because a header button added without thinking is what pushes it past a column
and makes the app unusable where it lives.

*It must not ask to be maximised.* A tiling compositor honours `set_maximized`
by handing the window the whole output, which throws away the arrangement the
user made. The size is still remembered — see `housestyle.Geometry`, which is
where both halves of that rule now live.
"""

from __future__ import annotations

import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from . import __version__, brain, claude, export, housestyle, jobs, markdown  # noqa: E402
from . import order, render, widgets, winenv  # noqa: E402
from .assistant import Assistant  # noqa: E402
from .backlinks import Backlinks  # noqa: E402
from .config import Config, DEFAULTS  # noqa: E402
from .document import MARKDOWN_EXTENSIONS, UNTITLED, Document  # noqa: E402
from .outline import Outline  # noqa: E402
from .search import FindBar  # noqa: E402
from .switcher import Switcher  # noqa: E402

MAX_FILE_BYTES = 40 * 1024 * 1024

# Every key the window answers to. Kept here rather than inside the method
# that installs them so that the suite can hold this, the sheet below and the
# README's table to one another — a key bound twice is a key that does one of
# its two things, silently.
ACCELERATORS = (
    ("win.new", ["<Control>n", "<Control>t"]),
    ("win.open", ["<Control>o"]),
    ("win.save", ["<Control>s"]),
    ("win.save-as", ["<Control><Shift>s"]),
    ("win.close-tab", ["<Control>w"]),
    ("win.preferences", ["<Control>comma"]),
    ("win.find", ["<Control>f"]),
    ("win.replace", ["<Control>h"]),
    ("win.find-next", ["<Control>g", "F3"]),
    ("win.find-previous", ["<Control><Shift>g", "<Shift>F3"]),
    ("win.bold", ["<Control>b"]),
    ("win.italic", ["<Control>i"]),
    ("win.code", ["<Control>e"]),
    ("win.strike", ["<Control><Shift>x"]),
    ("win.highlight", ["<Control><Shift>h"]),
    ("win.link", ["<Control>k"]),
    ("win.quote", ["<Control><Shift>q"]),
    ("win.list-bullet", ["<Control><Shift>l"]),
    ("win.list-ordered", ["<Control><Shift>o"]),
    ("win.list-task", ["<Control><Shift>t"]),
    ("win.toggle-task", ["<Control>d"]),
    ("win.comment", ["<Control>slash"]),
    ("win.toggle-sidebar", ["F9"]),
    ("win.toggle-focus", ["<Control><Shift>f"]),
    ("win.statistics", ["<Control><Shift>c"]),
    ("win.shortcuts", ["<Control>question"]),
    ("win.export-html", ["<Control><Shift>e"]),
    ("win.zoom-in", ["<Control>plus", "<Control>equal"]),
    ("win.zoom-out", ["<Control>minus"]),
    ("win.zoom-reset", ["<Control>0"]),
    ("win.next-tab", ["<Control>Page_Down", "<Control>Tab"]),
    ("win.previous-tab", ["<Control>Page_Up", "<Control><Shift>Tab"]),
    # The Order.
    ("win.switcher", ["<Control>p"]),
    ("win.toggle-backlinks", ["<Control><Shift>b"]),
    ("win.daily-note", ["<Control><Shift>d"]),
    ("win.send-to-inbox", ["<Control><Shift>i"]),
    ("win.ask-claude", ["<Control><Shift>a"]),
)

# The in-app sheet (Ctrl+?), in the groups it is read in.
SHORTCUTS = [
    ("Files", [
        ("Ctrl+N / Ctrl+T", "New document"), ("Ctrl+O", "Open"),
        ("Ctrl+S", "Save"), ("Ctrl+Shift+S", "Save as"),
        ("Ctrl+W", "Close tab"), ("Ctrl+Shift+E", "Export as HTML"),
    ]),
    ("Writing", [
        ("Ctrl+B", "Bold"), ("Ctrl+I", "Italic"),
        ("Ctrl+E", "Inline code"), ("Ctrl+K", "Link"),
        ("Ctrl+Shift+X", "Strikethrough"), ("Ctrl+Shift+H", "Highlight"),
        ("Ctrl+1…6", "Heading level"), ("Ctrl+7", "No heading"),
        ("Ctrl+Shift+L", "Bulleted list"), ("Ctrl+Shift+O", "Numbered list"),
        ("Ctrl+Shift+T", "Task list"), ("Ctrl+D", "Tick or untick"),
        ("Ctrl+Shift+Q", "Block quote"), ("Ctrl+/", "Comment out"),
    ]),
    ("The second brain", [
        ("[[", "Link to a note"), ("Ctrl+P", "Go to a note"),
        ("Ctrl+Shift+B", "Backlinks"), ("Ctrl+Shift+D", "Today's note"),
        ("Ctrl+Shift+I", "Send to the inbox"), ("Ctrl+Shift+A", "Ask Claude"),
    ]),
    ("Moving about", [
        ("Ctrl+F", "Find"), ("Ctrl+H", "Replace"),
        ("Ctrl+G", "Next match"), ("F9", "Outline"),
        ("Ctrl+Tab", "Next tab"), ("Alt+1/2/3", "Text, split, page"),
    ]),
    ("The view", [
        ("Ctrl++ / Ctrl+-", "Text size"), ("Ctrl+0", "Reset text size"),
        ("Ctrl+Shift+F", "Focus mode"), ("Ctrl+Shift+C", "Statistics"),
    ]),
    ("In the text", [
        ("Enter", "Continue the list or quote"),
        ("Enter twice", "Leave the list"),
        ("Tab", "Indent the item, or next table cell"),
        ("Backspace", "Remove the list marker"),
        ("Paste a URL", "Wraps the selection in a link"),
        ("Double-click the page", "Jump to that line in the text"),
    ]),
]



def _markdown_filter():
    markdown_filter = Gtk.FileFilter()
    markdown_filter.set_name("Markdown")
    for extension in MARKDOWN_EXTENSIONS:
        markdown_filter.add_pattern("*" + extension)
    return markdown_filter


def _hidden_without(label, action):
    """A menu item that is not drawn at all while its action does not exist."""
    item = Gio.MenuItem.new(label, action)
    item.set_attribute_value("hidden-when", GLib.Variant.new_string("action-missing"))
    return item


def with_extension(path, extension):
    """`path`, with `extension` added if its name has none of its own.

    A name the user gave an extension to — notes.txt, README.markdown — is
    theirs and stays as it is.
    """
    if not extension or os.path.splitext(os.path.basename(path))[1]:
        return path
    return path + extension

class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Notedown")
        self.config = Config()
        self.active: Document | None = None
        self._by_page: dict = {}
        self._closing = False
        self._setting_view = False
        self._reload_asked: dict[str, float] = {}

        # The Order's index of every note: one per process, shared by every
        # window, and the same file every app that joins the brain keeps up.
        # The app refreshes it once at start-up, off the main loop.
        self.notes = brain.Notes.shared()
        self._index_ready = order.index_ready(self.notes)
        self._resolved: dict[tuple[str, str], str] = {}
        self._backlinks_for: tuple[str | None, list] = (None, [])

        # The remembered size and maximise, applied before the first map —
        # see housestyle.Geometry for why the two are treated differently
        # under a tiling compositor.
        self.geometry = housestyle.Geometry(self, self.config, minimum=(360, 320))
        self.geometry.restore()

        self._build_actions()
        self._build_ui()
        self._install_accelerators()

        self.connect("close-request", self._on_close)
        # Files change behind our back — a terminal, another editor — and the
        # document should notice when the window comes back.
        self.connect("notify::is-active", self._on_active_changed)

        GLib.idle_add(self._start_up)

    # ------------------------------------------------- the active document --
    #
    # Everything below the tab bar belongs to one document at a time, and the
    # rest of this class is written as though there were only ever one.

    @property
    def editor(self):
        return self.active.editor

    @property
    def preview(self):
        return self.active.preview

    @property
    def path(self) -> str | None:
        return self.active.path if self.active else None

    @property
    def documents(self) -> list[Document]:
        return [self._by_page[page] for page in self._pages()
                if page in self._by_page]

    def _pages(self):
        return [self.tabs.get_nth_page(index) for index in range(self.tabs.get_n_pages())]

    # ------------------------------------------------------------ building --

    def _build_ui(self):
        self.toasts = Adw.ToastOverlay()
        # The house style: the window paints nothing and everything sits on
        # one rounded, translucent slab of glass inside it. `self.glazing` is
        # what the Look page calls back into when a setting changes.
        self.glazing = housestyle.Glazing(self, self.config,
                                          extra=widgets.CSS,
                                          app_class="notedown")
        self.glazing.wrap(self.toasts)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.toasts.set_child(outer)

        self.header = Adw.HeaderBar()
        self.title = Adw.WindowTitle(title="Notedown", subtitle="")
        self.header.set_title_widget(self.title)
        outer.append(self.header)

        # -- header, start ------------------------------------------------
        self.new_button = Gtk.Button(icon_name="tab-new-symbolic",
                                     tooltip_text="New (Ctrl+N or Ctrl+T)")
        self.new_button.set_action_name("win.new")
        self.header.pack_start(self.new_button)

        open_button = Gtk.Button(icon_name="document-open-symbolic",
                                 tooltip_text="Open (Ctrl+O)")
        open_button.set_action_name("win.open")
        self.header.pack_start(open_button)

        self.sidebar_button = Gtk.ToggleButton(
            icon_name="view-list-symbolic", tooltip_text="Outline (F9)")
        self.sidebar_button.set_action_name("win.toggle-sidebar")
        self.header.pack_start(self.sidebar_button)

        # -- header, end --------------------------------------------------
        menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic",
                                     tooltip_text="Main menu",
                                     menu_model=self._main_menu())
        self.header.pack_end(menu_button)

        format_button = Gtk.MenuButton(icon_name="format-text-rich-symbolic",
                                       tooltip_text="Formatting",
                                       menu_model=self._format_menu())
        self.header.pack_end(format_button)

        find_button = Gtk.Button(icon_name="edit-find-symbolic",
                                 tooltip_text="Find (Ctrl+F)")
        find_button.set_action_name("win.find")
        self.header.pack_end(find_button)

        self.view_buttons = self._view_switcher()
        self.header.pack_end(self.view_buttons)

        # -- tabs ----------------------------------------------------------
        self.tabs = Adw.TabView(vexpand=True)
        self.tabs.connect("notify::selected-page", self._tab_switched)
        self.tabs.connect("close-page", self._tab_closing)
        self.tabs.connect("page-attached", lambda *_: self._update_title())
        self.tabs.connect("page-detached", lambda *_: self._update_title())

        self.tab_bar = Adw.TabBar(view=self.tabs, autohide=True)
        outer.append(self.tab_bar)

        # -- body: outline | tabs ------------------------------------------
        self.outline = Outline(on_activate=self._outline_activated)
        self.outline.set_size_request(150, -1)

        self.side_paned = Gtk.Paned(
            orientation=Gtk.Orientation.HORIZONTAL,
            position=self.config["sidebar_width"],
            resize_start_child=False, resize_end_child=True,
            shrink_start_child=False, shrink_end_child=False,
            vexpand=True,
        )
        self.side_paned.set_start_child(self.outline)
        self.side_paned.set_end_child(self.tabs)
        self.outline.set_visible(bool(self.config["sidebar_visible"]))
        self.sidebar_button.set_active(bool(self.config["sidebar_visible"]))

        # -- Claude, at the side -------------------------------------------
        # An overlay split rather than another pane: below the breakpoint it
        # slides over the page instead of taking a column the window does
        # not have. Only there at all when Claude Code is installed.
        self.claude_split = Adw.OverlaySplitView(
            content=self.side_paned, sidebar_position=Gtk.PackType.END,
            show_sidebar=False, enable_show_gesture=False,
            min_sidebar_width=260, max_sidebar_width=420, sidebar_width_fraction=0.34,
            vexpand=True)
        self.assistant = Assistant(self) if claude.available() else None
        if self.assistant is not None:
            self.claude_split.set_sidebar(self.assistant)
        outer.append(self.claude_split)

        # -- backlinks, under the page -------------------------------------
        self.backlinks = Backlinks(
            on_open=self.open_at_line,
            on_unwritten=lambda target: self.offer_new_note(self.active, target))
        self.backlinks.set_reveal_child(bool(self.config["backlinks_visible"]))
        outer.append(self.backlinks)

        # -- find bar and status strip -------------------------------------
        self.find = FindBar(get_editor=lambda: self.active.editor if self.active else None)
        outer.append(self.find)

        outer.append(self._status_strip())

        self._install_breakpoint()
        self._install_drop_target()

    def _view_switcher(self):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, css_classes=["linked"])
        self._view_toggles = {}
        for name, icon, tip in (
            ("editor", "text-editor-symbolic", "Text only (Ctrl+1)"),
            ("split", "view-dual-symbolic", "Text and page (Ctrl+2)"),
            ("preview", "view-paged-symbolic", "Page only (Ctrl+3)"),
        ):
            button = Gtk.ToggleButton(icon_name=icon, tooltip_text=tip)
            button.connect("toggled", self._view_toggled, name)
            box.append(button)
            self._view_toggles[name] = button
        return box

    def _status_strip(self):
        strip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        strip.add_css_class("status-strip")

        self.status_position = Gtk.Label(label="1:1", xalign=0)
        strip.append(self.status_position)

        self.status_words = Gtk.Label(label="", xalign=0)
        strip.append(self.status_words)

        # How many cards Mnemosyne will find in this note — counted by the
        # same parser it counts them with.
        self.status_cards = Gtk.Label(label="", xalign=0, visible=False,
                                      tooltip_text="Cards in this note (SYSTEM.md §7)")
        strip.append(self.status_cards)

        self.status_reading = Gtk.Label(label="", xalign=0)
        strip.append(self.status_reading)

        strip.append(Gtk.Box(hexpand=True))

        self.status_state = Gtk.Label(label="", xalign=1)
        strip.append(self.status_state)

        self.status_kind = Gtk.Label(label="", xalign=1)
        strip.append(self.status_kind)
        return strip

    def _install_breakpoint(self):
        """Below 700px the outline folds away and the split goes vertical.

        Measured rather than guessed: the test suite asserts the window can be
        made 360px wide, which is narrower than the narrowest column on this
        screen.
        """
        condition = Adw.BreakpointCondition.new_length(
            Adw.BreakpointConditionLengthType.MAX_WIDTH, 700, Adw.LengthUnit.PX)
        self.narrow = Adw.Breakpoint.new(condition)
        self.narrow.connect("apply", lambda *_: self._set_narrow(True))
        self.narrow.connect("unapply", lambda *_: self._set_narrow(False))
        self.add_breakpoint(self.narrow)
        self._narrow = False

    def _set_narrow(self, narrow):
        """Give the header back its width, and stack the panes.

        At a column width the three-way view switcher and the New button are
        the difference between the window fitting and the window being clipped
        — and both are in the menu anyway, which is the test of whether a
        header button can be dropped.
        """
        self._narrow = narrow
        self.outline.set_visible(bool(self.config["sidebar_visible"]) and not narrow)
        self.view_buttons.set_visible(not narrow)
        self.new_button.set_visible(not narrow)
        self.status_reading.set_visible(not narrow)
        self.status_kind.set_visible(not narrow)
        self.claude_split.set_collapsed(narrow)
        self.find.set_narrow(narrow)
        for document in self.documents:
            document.paned.set_orientation(
                Gtk.Orientation.VERTICAL if narrow else Gtk.Orientation.HORIZONTAL)

    def _install_drop_target(self):
        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)

        def dropped(_target, value, _x, _y):
            for gfile in value.get_files():
                path = gfile.get_path()
                if path:
                    self.open_path(path)
            return True

        drop.connect("drop", dropped)
        self.add_controller(drop)

    # -------------------------------------------------------------- menus --

    def _main_menu(self):
        menu = Gio.Menu()

        self.recent_menu = Gio.Menu()
        files = Gio.Menu()
        files.append("New", "win.new")
        files.append("New Window", "app.new-window")
        files.append("Open…", "win.open")
        files.append_submenu("Open Recent", self.recent_menu)
        menu.append_section(None, files)

        # The Order. "Study" is there only when Mnemosyne is, and the Claude
        # items only when Claude Code is: an item that can only fail is
        # worse than no item.
        order_items = Gio.Menu()
        order_items.append("Go to Note…", "win.switcher")
        order_items.append("Today's Note", "win.daily-note")
        order_items.append("Send to Inbox", "win.send-to-inbox")
        order_items.append_item(_hidden_without("Study This Note", "win.study"))
        menu.append_section(None, order_items)

        asking = Gio.Menu()
        asking.append_item(_hidden_without("Ask Claude…", "win.ask-claude"))
        asking.append_item(_hidden_without("Make Cards with Claude", "win.claude-cards"))
        asking.append_item(_hidden_without("Continue in Claude Desk", "win.claude-desk"))
        menu.append_section(None, asking)

        saving = Gio.Menu()
        saving.append("Save", "win.save")
        saving.append("Save As…", "win.save-as")
        saving.append("Reload from Disk", "win.reload")
        menu.append_section(None, saving)

        exporting = Gio.Menu()
        exporting.append("Export as HTML…", "win.export-html")
        exporting.append("Export as Plain Text…", "win.export-text")
        exporting.append("Export with pandoc…", "win.export-pandoc")
        exporting.append("Copy as HTML", "win.copy-html")
        menu.append_section(None, exporting)

        panes = Gio.Menu()
        panes.append("Text Only", "win.view-editor")
        panes.append("Text and Page", "win.view-split")
        panes.append("Page Only", "win.view-preview")
        menu.append_section(None, panes)

        view = Gio.Menu()
        view.append("Outline", "win.toggle-sidebar")
        view.append("Backlinks", "win.toggle-backlinks")
        view.append("Focus Mode", "win.toggle-focus")
        view.append("Typewriter Scrolling", "win.toggle-typewriter")
        view.append("Word Wrap", "win.toggle-wrap")
        view.append("Line Numbers", "win.toggle-gutter")
        view.append("Sync Scrolling", "win.toggle-sync")
        view.append("Full-Width Page", "win.toggle-full-width")
        view.append("Load Remote Images", "win.toggle-remote-images")
        menu.append_submenu("View", view)

        size = Gio.Menu()
        size.append("Bigger Text", "win.zoom-in")
        size.append("Smaller Text", "win.zoom-out")
        size.append("Reset Text Size", "win.zoom-reset")
        size.append("Fonts…", "win.fonts")
        menu.append_submenu("Text", size)

        tools = Gio.Menu()
        tools.append("Document Statistics", "win.statistics")
        tools.append("Auto-save", "win.toggle-auto-save")
        menu.append_section(None, tools)

        about = Gio.Menu()
        about.append("Preferences", "win.preferences")
        about.append("Keyboard Shortcuts", "win.shortcuts")
        about.append("About Notedown", "win.about")
        about.append("Quit", "app.quit")
        menu.append_section(None, about)
        return menu

    def _format_menu(self):
        menu = Gio.Menu()

        marks = Gio.Menu()
        marks.append("Bold", "win.bold")
        marks.append("Italic", "win.italic")
        marks.append("Inline Code", "win.code")
        marks.append("Strikethrough", "win.strike")
        marks.append("Highlight", "win.highlight")
        marks.append("Link…", "win.link")
        menu.append_section(None, marks)

        headings = Gio.Menu()
        for level in range(1, 7):
            headings.append(f"Heading {level}", f"win.heading{level}")
        headings.append("No Heading", "win.heading0")
        menu.append_submenu("Headings", headings)

        blocks = Gio.Menu()
        blocks.append("Bulleted List", "win.list-bullet")
        blocks.append("Numbered List", "win.list-ordered")
        blocks.append("Task List", "win.list-task")
        blocks.append("Tick / Untick", "win.toggle-task")
        blocks.append("Block Quote", "win.quote")
        blocks.append("Code Block", "win.code-block")
        menu.append_section(None, blocks)

        insert = Gio.Menu()
        insert.append("Table…", "win.table")
        insert.append("Horizontal Rule", "win.rule")
        insert.append("Footnote", "win.footnote")
        insert.append("Date", "win.date")
        insert.append("Comment Out", "win.comment")
        menu.append_section(None, insert)
        return menu

    def _rebuild_recent_menu(self):
        self.recent_menu.remove_all()
        recent = [path for path in self.config["recent_files"] if os.path.exists(path)]
        if not recent:
            item = Gio.MenuItem.new("Nothing yet", None)
            item.set_action_and_target_value("win.noop", None)
            self.recent_menu.append_item(item)
            return
        for path in recent[:12]:
            label = os.path.basename(path)
            item = Gio.MenuItem.new(label, None)
            item.set_action_and_target_value("win.open-recent",
                                             GLib.Variant.new_string(path))
            self.recent_menu.append_item(item)
        self.recent_menu.append("Clear", "win.clear-recent")

    # ------------------------------------------------------------ actions --

    def _build_actions(self):
        simple = {
            "new": lambda: self.new_document(),
            "open": self.open_dialog,
            "save": lambda: self.save_document(self.active),
            "save-as": self.save_as,
            "reload": self.reload_document,
            "close-tab": self.close_current_tab,
            "find": lambda: self.find.open(),
            "replace": lambda: self.find.open(replace=True),
            "find-next": lambda: self.find.next_match(),
            "find-previous": lambda: self.find.previous_match(),
            "export-html": self.export_html,
            "export-text": self.export_text,
            "export-pandoc": self.export_pandoc,
            "copy-html": self.copy_html,
            "statistics": self.show_statistics,
            "shortcuts": self.show_shortcuts,
            "about": self.show_about,
            "zoom-in": lambda: self.zoom(1),
            "zoom-out": lambda: self.zoom(-1),
            "zoom-reset": lambda: self.zoom(0),
            "fonts": self.show_fonts,
            "preferences": self.show_preferences,
            "clear-recent": self.clear_recent,
            "noop": lambda: None,
            # Formatting.
            "bold": lambda: self.editor.wrap_selection("**"),
            "italic": lambda: self.editor.wrap_selection("*"),
            "code": lambda: self.editor.wrap_selection("`"),
            "strike": lambda: self.editor.wrap_selection("~~"),
            "highlight": lambda: self.editor.wrap_selection("=="),
            "link": self.insert_link,
            "quote": lambda: self.editor.toggle_quote(),
            "list-bullet": lambda: self.editor.toggle_list(),
            "list-ordered": lambda: self.editor.toggle_list(ordered=True),
            "list-task": lambda: self.editor.toggle_list(task=True),
            "toggle-task": lambda: self.editor.toggle_task(),
            "code-block": self.insert_code_block,
            "table": self.insert_table,
            "rule": lambda: self.editor.insert_snippet("\n---\n\n"),
            "footnote": self.insert_footnote,
            "date": self.insert_date,
            "comment": lambda: self.editor.toggle_comment(),
            "next-tab": lambda: self.tabs.select_next_page(),
            "previous-tab": lambda: self.tabs.select_previous_page(),
            # The Order.
            "switcher": self.show_switcher,
            "daily-note": self.open_daily_note,
            "send-to-inbox": self.send_to_inbox,
        }
        for name, handler in simple.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, fn=handler: self._guarded(fn))
            self.add_action(action)

        for level in range(0, 7):
            action = Gio.SimpleAction.new(f"heading{level}", None)
            action.connect("activate",
                           lambda _a, _p, n=level: self._guarded(
                               lambda: self.editor.set_heading(n)))
            self.add_action(action)

        recent = Gio.SimpleAction.new("open-recent", GLib.VariantType.new("s"))
        recent.connect("activate", lambda _a, value: self.open_path(value.get_string()))
        self.add_action(recent)

        # Stateful settings, so the menu shows a tick.
        self._toggles = {
            "toggle-sidebar": ("sidebar_visible", self._apply_sidebar),
            "toggle-backlinks": ("backlinks_visible", self._apply_backlinks),
            "toggle-focus": ("focus_mode", self._apply_focus),
            "toggle-typewriter": ("typewriter", self._apply_typewriter),
            "toggle-wrap": ("wrap", self._apply_wrap),
            "toggle-gutter": ("show_gutter", self._apply_gutter),
            "toggle-sync": ("sync_scroll", lambda: None),
            "toggle-remote-images": ("load_remote_images", self._apply_remote_images),
            "toggle-auto-save": ("auto_save", lambda: None),
        }
        # Not a plain boolean in the config — the measure is a number, so it
        # gets a toggle of its own that flips between "clamped" and "not".
        full_width = Gio.SimpleAction.new_stateful(
            "toggle-full-width", None,
            GLib.Variant.new_boolean(not self.config["reading_width"]))
        full_width.connect("activate", self._toggle_full_width)
        self.add_action(full_width)
        for name, (key, apply) in self._toggles.items():
            action = Gio.SimpleAction.new_stateful(
                name, None, GLib.Variant.new_boolean(bool(self.config[key])))
            action.connect("activate", self._toggle_activated, name)
            self.add_action(action)

        view = Gio.SimpleAction.new_stateful(
            "view-mode", GLib.VariantType.new("s"),
            GLib.Variant.new_string(self._view_mode_from_config()))
        view.connect("activate", lambda _a, value: self.set_view_mode(value.get_string()))
        self.add_action(view)

        self._offer_study()
        self._offer_claude()

    def _guarded(self, fn):
        """Run an action only when there is a document for it to act on."""
        if self.active is None:
            return
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - reported, never fatal
            import traceback

            traceback.print_exc()
            widgets.error_toast(self, exc)

    def _toggle_full_width(self, action, _parameter):
        wide = bool(self.config["reading_width"])      # currently clamped
        self.config["reading_width"] = 0 if wide else 820
        action.set_state(GLib.Variant.new_boolean(wide))
        for document in self.documents:
            document.preview.set_reading_width(int(self.config["reading_width"]))
        self.config.save()

    def _toggle_activated(self, action, _parameter, name):
        key, apply = self._toggles[name]
        value = not bool(self.config[key])
        self.config[key] = value
        action.set_state(GLib.Variant.new_boolean(value))
        apply()
        self.config.save()

    def _install_accelerators(self):
        app = self.get_application()
        if app is None:
            return
        for action, keys in ACCELERATORS:
            app.set_accels_for_action(action, keys)

        for level in range(1, 7):
            app.set_accels_for_action(f"win.heading{level}", [f"<Control>{level}"])
        app.set_accels_for_action("win.heading0", ["<Control>7"])

        # The view modes take Alt, because Ctrl+1..6 are the headings.
        for index, mode in enumerate(("editor", "split", "preview"), start=1):
            action = Gio.SimpleAction.new(f"view-{mode}", None)
            action.connect("activate", lambda _a, _p, m=mode: self.set_view_mode(m))
            self.add_action(action)
            app.set_accels_for_action(f"win.view-{mode}", [f"<Alt>{index}"])

        escape = Gtk.EventControllerKey()
        escape.connect("key-pressed", self._on_window_key)
        self.add_controller(escape)

    def _on_window_key(self, _controller, keyval, _keycode, _state):
        if keyval == Gdk.KEY_Escape and self.find.is_open():
            self.find.close()
            return True
        return False

    # ------------------------------------------------------------- startup --

    def _start_up(self):
        self._rebuild_recent_menu()
        # A file named on the command line has already opened by now; the
        # session is only restored when nothing else asked for a document.
        if self.tabs.get_n_pages() == 0:
            for path in list(self.config["open_files"]):
                if os.path.isfile(path):
                    self.open_path(path, focus=False, remember=False)
        if self.tabs.get_n_pages() == 0:
            self.new_document(focus=False)
        if self.active is None and self.tabs.get_n_pages():
            self.tabs.set_selected_page(self.tabs.get_nth_page(0))
        self.set_view_mode(self._view_mode_from_config(), remember=False)
        self._apply_sidebar()
        return GLib.SOURCE_REMOVE

    # ----------------------------------------------------------- documents --

    def new_document(self, focus=True, text=""):
        document = Document(self, None)
        if text:
            document.set_text(text)
        else:
            document.set_text("")
        self._attach(document, focus=focus)
        return document

    def _attach(self, document, focus=True):
        page = self.tabs.append(document.root)
        self._by_page[page] = document
        page.set_title(document.name)
        page.set_tooltip(document.path or "Not saved yet")
        document.paned.set_orientation(
            Gtk.Orientation.VERTICAL if self._narrow else Gtk.Orientation.HORIZONTAL)
        if focus:
            self.tabs.set_selected_page(page)
        self._apply_view_to(document)
        # The first page appended selects itself, and that notification
        # arrives before the line above has told us which document it is. So
        # settle it here rather than relying on a signal that has been and
        # gone.
        if self.tabs.get_selected_page() is page:
            self._tab_switched()
        return page

    def _page_of(self, document):
        for page, candidate in self._by_page.items():
            if candidate is document:
                return page
        return None

    def open_dialog(self):
        dialog = Gtk.FileDialog(title="Open a document")
        filters = Gio.ListStore.new(Gtk.FileFilter)

        markdown_filter = _markdown_filter()
        filters.append(markdown_filter)

        text_filter = Gtk.FileFilter()
        text_filter.set_name("Text files")
        text_filter.add_mime_type("text/*")
        filters.append(text_filter)

        every = Gtk.FileFilter()
        every.set_name("All files")
        every.add_pattern("*")
        filters.append(every)

        dialog.set_filters(filters)
        dialog.set_default_filter(markdown_filter)
        if self.config["last_folder"] and os.path.isdir(self.config["last_folder"]):
            dialog.set_initial_folder(Gio.File.new_for_path(self.config["last_folder"]))

        def chosen(source, result):
            try:
                gfile = source.open_finish(result)
            except GLib.Error:
                return
            if gfile and gfile.get_path():
                self.open_path(gfile.get_path())

        dialog.open(self, None, chosen)

    def open_path(self, path: str, focus=True, remember=True):
        """Open a file, or bring its tab to the front if it is already open."""
        path = os.path.abspath(os.path.expanduser(path))
        if os.path.isdir(path):
            self.config["last_folder"] = path
            self.config.save()
            widgets.toast(self, f"{os.path.basename(path)} is a folder")
            return None

        for document in self.documents:
            if document.is_at(path):
                page = self._page_of(document)
                if page is not None and focus:
                    self.tabs.set_selected_page(page)
                return document

        try:
            size = os.path.getsize(path)
        except OSError as exc:
            widgets.error_toast(self, exc)
            return None
        if size > MAX_FILE_BYTES:
            widgets.toast(self, f"{os.path.basename(path)} is too big to open "
                                f"({size // (1024 * 1024)} MB)")
            return None

        try:
            text, encoding, newline = _read_text(path)
        except OSError as exc:
            widgets.error_toast(self, exc)
            return None

        # An empty, never-saved, unedited tab is a placeholder; fill it rather
        # than opening a second one beside it.
        document = self.active
        if not (document is not None and document.path is None
                and not document.modified and not document.text().strip()):
            document = Document(self, path)
            self._attach(document, focus=focus)
        else:
            document.set_path(path)

        document.encoding = encoding
        document.newline = newline
        document.set_path(path)
        document.set_text(text)
        document.editor.mark_saved()
        document.mtime = _mtime(path)

        page = self._page_of(document)
        if page is not None:
            page.set_title(document.name)
            page.set_tooltip(path)
            if focus:
                self.tabs.set_selected_page(page)

        if remember:
            self.config.remember_file(path)
            self.config["last_folder"] = os.path.dirname(path)
            self.config.save()
            self._rebuild_recent_menu()
        self._update_title()
        return document

    def save_document(self, document, silent=False, then=None):
        if document is None:
            return
        if not document.path:
            self.save_as(then=then)
            return

        text = document.text_to_write()
        path = document.path

        def write():
            if self.config["backup_on_save"] and os.path.exists(path):
                try:
                    import shutil

                    shutil.copy2(path, path + "~")
                except OSError:
                    pass
            tmp = path + ".notedown-tmp"
            with open(tmp, "w", encoding=document.encoding, newline="") as fh:
                fh.write(text)
            os.replace(tmp, path)
            return _mtime(path)

        def done(mtime):
            document.editor.mark_saved()
            document.saved_text = text
            document.mtime = mtime
            self.config.remember_file(path)
            self.config.save()
            self._rebuild_recent_menu()
            self._update_title()
            self._update_modified()
            # Its title, its aliases and its links, into the index now rather
            # than at the next start-up, so a link to it resolves at once.
            self.index_note(path, text)
            if not silent:
                widgets.toast(self, f"Saved {os.path.basename(path)}")
            if then:
                then()

        jobs.run(write, done, lambda exc: widgets.error_toast(self, exc))

    def save_as(self, then=None):
        document = self.active
        if document is None:
            return
        dialog = Gtk.FileDialog(title="Save as")
        dialog.set_initial_name(document.name if document.path else UNTITLED)
        # Without a type to offer, the Windows dialog saves exactly the name
        # typed — "notes" becomes a file with no extension, which Explorer
        # shows as a plain "File" and opens with nothing.
        filters = Gio.ListStore.new(Gtk.FileFilter)
        markdown_filter = _markdown_filter()
        filters.append(markdown_filter)
        every = Gtk.FileFilter()
        every.set_name("All files")
        every.add_pattern("*")
        filters.append(every)
        dialog.set_filters(filters)
        dialog.set_default_filter(markdown_filter)
        folder = document.folder or self.config["last_folder"]
        if folder and os.path.isdir(folder):
            dialog.set_initial_folder(Gio.File.new_for_path(folder))

        def chosen(source, result):
            try:
                gfile = source.save_finish(result)
            except GLib.Error:
                return
            path = gfile.get_path() if gfile else None
            if not path:
                return
            self._confirm_extended(path, ".md", save_to)

        def save_to(path):
            document.set_path(path)
            page = self._page_of(document)
            if page is not None:
                page.set_title(document.name)
                page.set_tooltip(path)
            self.config["last_folder"] = os.path.dirname(path)
            self.save_document(document, then=then)

        dialog.save(self, None, chosen)

    def reload_document(self):
        document = self.active
        if document is None or not document.path:
            return

        def do_reload():
            try:
                text, encoding, newline = _read_text(document.path)
            except OSError as exc:
                widgets.error_toast(self, exc)
                return
            line, _ = document.editor.cursor_position()
            document.encoding = encoding
            document.newline = newline
            document.set_text(text)
            document.editor.mark_saved()
            document.mtime = _mtime(document.path)
            document.editor.goto_line(line, focus=False)
            self._update_title()
            widgets.toast(self, "Reloaded from disk")

        if document.modified:
            widgets.confirm(
                self, "Reload and lose your changes?",
                f"{document.name} has unsaved changes. Reloading takes the "
                f"version on disk instead.",
                "Reload", do_reload)
        else:
            do_reload()

    def close_current_tab(self):
        page = self.tabs.get_selected_page()
        if page is not None:
            self.tabs.close_page(page)

    # ------------------------------------------------------ tabs and title --

    def _tab_switched(self, *_):
        page = self.tabs.get_selected_page()
        self.active = self._by_page.get(page) if page else None
        if self.active is None:
            return
        self.active.ensure_rendered()
        self._apply_view_to(self.active)
        self._update_title()
        self._update_status()
        self._update_outline()
        self._update_backlinks()
        if self.assistant is not None:
            self.assistant.update_about()
        self.active.editor.view.grab_focus()

    def _tab_closing(self, tabs, page):
        document = self._by_page.get(page)
        if document is None:
            return False
        if not document.modified:
            document.close()
            self._by_page.pop(page, None)
            return False

        def save_then_close():
            self.save_document(document, then=lambda: self._finish_close(page))

        def discard():
            document.close()
            self._by_page.pop(page, None)
            tabs.close_page_finish(page, True)

        dialog = Adw.AlertDialog(
            heading="Save before closing?",
            body=f"{document.name} has changes that are not on disk.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("discard", "Discard")
        dialog.add_response("save", "Save")
        dialog.set_response_appearance("discard", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("save")
        dialog.set_close_response("cancel")

        def answered(dlg, result):
            answer = dlg.choose_finish(result)
            if answer == "save":
                save_then_close()
            elif answer == "discard":
                discard()
            else:
                tabs.close_page_finish(page, False)

        dialog.choose(self, None, answered)
        return True

    def _finish_close(self, page):
        document = self._by_page.pop(page, None)
        if document is not None:
            document.close()
        self.tabs.close_page_finish(page, True)

    def _update_title(self):
        document = self.active
        if document is None:
            self.title.set_title("Notedown")
            self.title.set_subtitle("")
            self.set_title("Notedown")
            return

        mark = "• " if document.modified else ""
        self.title.set_title(mark + document.name)
        folder = document.folder
        if folder:
            home = os.path.expanduser("~")
            if folder.startswith(home):
                folder = "~" + folder[len(home):]
        self.title.set_subtitle(folder or "Not saved yet")
        self.set_title(f"{mark}{document.name} — Notedown")

        page = self._page_of(document)
        if page is not None:
            page.set_title(mark + document.name)

    # --------------------------------------------------- document callbacks --

    def document_edited(self, document):
        # Only the cheap half. The word count and the outline come from the
        # parse, which happens on the render tick a moment later.
        if document is self.active:
            self._update_title()
            self._update_modified()

    def document_rendered(self, document):
        if document is self.active:
            self._update_status()
            self._update_outline()
            self._update_backlinks(ask=False)

    def document_cursor_moved(self, document):
        if document is not self.active:
            return
        line, column = document.editor.cursor_position()
        self.status_position.set_text(f"{line}:{column}")
        selected = document.editor.selection_length()
        if selected:
            self.status_position.set_text(f"{line}:{column}  ({selected} selected)")
        self.outline.mark_line(line - 1)

    def _update_status(self):
        document = self.active
        if document is None:
            return
        parsed = document.parsed
        if parsed is not None and document.is_markdown:
            stats = markdown.statistics_of(parsed, document.text())
            self.status_words.set_text(f"{stats['words']:,} words")
            self.status_reading.set_text(
                f"{stats['reading_minutes']} min read" if stats["reading_minutes"] else "")
        else:
            self.status_words.set_text(f"{len(document.text()):,} characters")
            self.status_reading.set_text("")

        cards = len(parsed.cards) if parsed is not None and document.is_markdown else 0
        self.status_cards.set_text(f"{cards} {'card' if cards == 1 else 'cards'}")
        self.status_cards.set_visible(bool(cards))
        study = self.lookup_action("study")
        if study is not None:
            study.set_enabled(bool(cards and document.path))

        self._update_modified()
        self.status_kind.set_text("Markdown" if document.is_markdown else "Plain text")

    def _update_modified(self):
        document = self.active
        if document is None:
            return
        if document.modified:
            self.status_state.set_text("Unsaved")
            self.status_state.add_css_class("status-modified")
        else:
            self.status_state.set_text("Saved" if document.path else "")
            self.status_state.remove_css_class("status-modified")

    def _update_outline(self):
        document = self.active
        if document is None or document.parsed is None:
            self.outline.set_entries([])
            return
        self.outline.set_entries(markdown.outline(document.parsed))
        line, _ = document.editor.cursor_position()
        self.outline.mark_line(line - 1)

    def _outline_activated(self, line):
        if self.active is None:
            return
        self.active.editor.goto_line(line + 1)
        self.active.sync_preview_to_cursor()

    def open_url(self, document, url: str) -> bool:
        """A link clicked in the preview. Local Markdown opens in a tab."""
        if url.startswith("#"):
            return False
        if url.startswith(render.WIKI):
            self.follow_link(document, *render.parse_wiki_href(url))
            return True
        if "://" not in url and not url.startswith("mailto:"):
            target = url if os.path.isabs(url) else os.path.join(document.folder, url)
            target = os.path.normpath(target.split("#")[0])
            if os.path.isfile(target):
                self.open_path(target)
                return True
            widgets.toast(self, f"No such file: {url}")
            return True

        launcher = Gtk.UriLauncher.new(url)
        launcher.launch(self, None, None)
        return True

    # ---------------------------------------------------- the second brain --
    #
    # The Order (SYSTEM.md) from inside the editor: links that lead somewhere,
    # the notes that link back, every note by name, today's page, the inbox,
    # Mnemosyne and Claude. What is not about widgets is in order.py.

    def resolve_link(self, target: str, near: str | None = None) -> str:
        """The file `[[target]]` leads to from the note at `near`; "" for none yet.

        Remembered until the index next changes: a page asks again on every
        keystroke, and the answer only moves when a note does.
        """
        asked = (brain.key(target), os.path.dirname(near) if near else "")
        if asked not in self._resolved:
            self._resolved[asked] = order.resolve(self._index(), target, near)
        return self._resolved[asked]

    def _index(self):
        """The index, once it is there to be read; None until then."""
        return self.notes if self._index_ready else None

    def resolve_links(self, parsed, near: str | None):
        """Fill in where every `[[link]]` in a parsed page leads."""
        for node in markdown.wiki_links(parsed):
            node.url = self.resolve_link(node.text, near)

    def notes_changed(self):
        """The index has moved on: forget where links led, and look again."""
        self._index_ready = True          # whatever moved it has built it
        self._resolved.clear()
        self._backlinks_for = (None, [])
        for document in self.documents:
            document.links_changed()
        self._update_backlinks()

    def index_note(self, path: str, text: str | None = None):
        """One note into the index, off the main loop, and then what shows it.

        Off the loop because the start-up refresh may be holding the index
        for a while, and a save is no reason for the window to wait for it.
        Only notes: a shell script saved from here is not one.
        """
        if not path.lower().endswith(brain.NOTE_SUFFIXES):
            return
        jobs.run(lambda: self.notes.update(Path(path), text),
                 lambda _done: self.notes_changed(), lambda _exc: None)

    def link_suggestions(self, query: str, near: str | None):
        """(title, where, what to type) for the list `[[` opens."""
        if query.strip():
            try:
                found = [(note.title, str(note.path))
                         for note in self.notes.find(query, limit=12)] \
                    if self._index_ready else []
            except sqlite3.Error:
                found = []
        else:
            found = [(title, path) for title, _where, path in self.recent_notes()]
        return [(title, order.folder_of(path),
                 order.link_text(title, path, near, self.resolve_link))
                for title, path in found if not order.same_file(path, near)][:8]

    def recent_notes(self, leave_out: str | None = None):
        """The notes opened last, newest first, as (title, where, path)."""
        return [(order.title_for(path), order.folder_of(path), path)
                for path in self.config["recent_files"]
                if path.lower().endswith(MARKDOWN_EXTENSIONS) and os.path.isfile(path)
                and not order.same_file(path, leave_out)]

    def _notes_called(self, query: str):
        if not self._index_ready:
            return []
        try:
            found = self.notes.find(query, limit=40)
        except sqlite3.Error:
            return []
        return [(note.title, order.folder_of(str(note.path)), str(note.path))
                for note in found if note.path.is_file()]

    def show_switcher(self):
        """Ctrl+P: any note in the Order, by name."""
        dialog = Switcher(find=self._notes_called,
                          recent=lambda: self.recent_notes(self.path),
                          on_open=self.open_path)
        # Presented from inside the window's content, not from the window:
        # a host that embeds the content has a window of its own.
        dialog.present(self.toasts)
        return dialog

    def follow_link(self, document, target: str, heading: str = ""):
        """A `[[link]]` clicked: the note it leads to, or the offer to write it."""
        near = document.path if document is not None else None
        path = self.resolve_link(target, near)
        if not path or not os.path.isfile(path):
            self.offer_new_note(document, target)
            return
        opened = self.open_path(path)
        if opened is not None and heading and not opened.reveal_heading(heading):
            widgets.toast(self, f"{opened.name} has no heading “{heading}”")

    def offer_new_note(self, document, target: str):
        """Ask before starting the note a link names — beside the note it is in."""
        if document is None:
            return
        if document.folder:
            where = order.new_note_path(document.folder, target)
            body = (f"No note is called “{target}” yet. Notedown can start one beside "
                    f"{document.name}, as {os.path.relpath(where, document.folder)}.")
        elif brain.places().exists():
            where = None
            body = (f"No note is called “{target}” yet. This one is not saved "
                    f"anywhere, so the new note would start in the inbox.")
        else:
            widgets.toast(self, "Save this note first — a new note is made beside it")
            return
        widgets.confirm(self.toasts, f"Create “{target}”?", body, "Create",
                        lambda: self.make_note(target, where), destructive=False)

    def make_note(self, target: str, where: str | None):
        """Start the note `[[target]]` names, at `where` (or in the inbox), and open it."""
        title = target.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1].strip() or "Untitled"

        def work():
            if where is None:
                path = str(brain.capture_text(f"# {title}\n", title=title,
                                              origin="notedown"))
            else:
                path = where
                if not os.path.exists(path):
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    with open(path, "x", encoding="utf-8", newline="\n") as fh:
                        fh.write(f"# {title}\n\n")
            self.notes.update(Path(path))
            return path

        def done(path):
            self.notes_changed()
            self.open_path(path)

        jobs.run(work, done, lambda exc: widgets.error_toast(self, exc))

    def open_at_line(self, path: str, line: int):
        """A backlink clicked: that note, with the cursor on the line that links."""
        document = self.open_path(path)
        if document is not None:
            document.editor.goto_line(line)
            GLib.idle_add(lambda: (document.sync_preview_to_cursor(),
                                   GLib.SOURCE_REMOVE)[1])

    def _update_backlinks(self, ask=True):
        """The drawer, for the note in front — asked of the index only while open.

        `ask=False` is for the render tick, when the note's own links may
        have changed but what links to it cannot have.
        """
        if not self.backlinks.get_reveal_child() or self.active is None:
            return
        document = self.active
        known, links = self._backlinks_for
        if ask or known != document.path:
            links = []
            if document.path and self._index_ready:
                try:
                    links = self.notes.backlinks(document.path)
                except sqlite3.Error:
                    links = []
            self._backlinks_for = (document.path, links)
        self.backlinks.show(links, order.unwritten(document.parsed),
                            saved=bool(document.path))

    def _apply_backlinks(self):
        self.backlinks.set_reveal_child(bool(self.config["backlinks_visible"]))
        self._update_backlinks()

    def open_daily_note(self):
        """Ctrl+Shift+D: today's page in the journal, begun with its date if new."""
        if not brain.places().exists():
            widgets.toast(self, "There is no second brain on this machine to keep a "
                                "journal in")
            return
        try:
            path = str(brain.daily_note())
        except OSError as exc:
            widgets.error_toast(self, exc)
            return
        document = self.open_path(path)
        if document is not None:
            document.editor.goto_offset(document.editor.buffer.get_char_count())
            document.editor.view.grab_focus()
            self.index_note(path)

    def send_to_inbox(self):
        """Ctrl+Shift+I: the selection — or a note not filed anywhere yet — to the inbox.

        A saved note with nothing selected already has its place, so it is
        not sent; a new one, sent whole, leaves its tab empty for the next
        thought, because the thought is in the inbox now and a copy is a bug.
        """
        document = self.active
        selected = document.editor.selected_text()
        whole = not selected.strip()
        if whole and document.path:
            widgets.toast(self, f"Select what to send — {document.name} already has a place")
            return
        text = document.text() if whole else selected
        if not text.strip():
            widgets.toast(self, "There is nothing to send to the inbox")
            return
        if not brain.places().exists():
            widgets.toast(self, "There is no second brain on this machine to send it to")
            return
        # Where it came from, which the Order asks every capture to keep: the
        # note, home-relative, and the lines the selection was on.
        meta = {}
        bounds = document.editor.buffer.get_selection_bounds()
        if not whole and document.path and bounds:
            first, last = bounds[0].get_line() + 1, bounds[1].get_line() + 1
            if bounds[1].starts_line() and last > first:
                last -= 1
            meta = {"source": brain.places().relative(document.path),
                    "lines": str(first) if first == last else f"{first}-{last}"}

        def work():
            path = brain.capture_text(text, origin="notedown", meta=meta)
            return str(path), path.read_text(encoding="utf-8")

        def done(result):
            path, written = result
            if whole and document in self.documents and document.text() == text:
                document.set_text("")
            self.index_note(path)
            self._toast_with_undo(
                f"Sent to the inbox as {os.path.basename(path)}",
                lambda: self._take_back(path, written, document, text if whole else None))

        jobs.run(work, done, lambda exc: widgets.error_toast(self, exc))

    def _take_back(self, path, written, document, text):
        """Undo a capture: the inbox note goes again, if nobody has touched it since.

        Pressing Undo is the human act that SYSTEM.md asks deleting to be;
        nothing here removes a file the person has not just asked to remove.
        """
        try:
            with open(path, encoding="utf-8") as fh:
                untouched = fh.read() == written
        except OSError:
            return
        if not untouched:
            widgets.toast(self, "It has been edited since, so it stays in the inbox")
            return
        try:
            os.remove(path)
        except OSError as exc:
            widgets.error_toast(self, exc)
            return
        jobs.run(self.notes.refresh, lambda _n: self.notes_changed(), lambda _e: None)
        if text is not None and document in self.documents and not document.text():
            document.set_text(text)
            document.editor.buffer.set_modified(True)
            self._update_title()
            self._update_modified()
        widgets.toast(self, "Taken back out of the inbox")

    def _toast_with_undo(self, title, undo):
        toast = Adw.Toast(title=title, button_label="Undo", timeout=8, use_markup=False)
        toast.connect("button-clicked", lambda _toast: undo())
        self.toasts.add_toast(toast)
        return toast

    def _offer_study(self):
        """"Study This Note" exists while Mnemosyne is installed beside Notedown."""
        script = order.mnemosyne()
        present = self.lookup_action("study") is not None
        if script and not present:
            action = Gio.SimpleAction.new("study", None)
            action.connect("activate", lambda *_: self._guarded(self.study_note))
            action.set_enabled(False)          # until a saved note has cards
            self.add_action(action)
        elif not script and present:
            self.remove_action("study")
        return script

    def study_note(self):
        """`mnemosyne --study <this note>`, saved first so it studies what is on screen."""
        document = self.active
        script = order.mnemosyne()
        if document is None or not document.path or script is None:
            return

        def launch():
            argv = order.study_command(document.path, script, sys.executable)
            try:
                self._spawn(argv, os.path.dirname(script))
            except OSError as exc:
                widgets.error_toast(self, exc)
                return
            widgets.toast(self, f"Studying {document.name} in Mnemosyne")

        if document.modified:
            self.save_document(document, silent=True, then=launch)
        else:
            launch()

    def _spawn(self, argv, cwd):
        subprocess.Popen(argv, cwd=cwd, creationflags=winenv.NO_WINDOW,
                         start_new_session=not winenv.WINDOWS)

    def _offer_claude(self):
        """Claude's three actions — only when Claude Code is on this machine."""
        if not claude.available():
            return
        for name, handler in (("ask-claude", lambda: self.show_claude()),
                              ("claude-cards", self._claude_cards),
                              ("claude-desk", self._claude_desk)):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, fn=handler: self._guarded(fn))
            self.add_action(action)

    def show_claude(self, show: bool | None = None):
        """Ctrl+Shift+A: Claude at the side, with the cursor in the request."""
        if self.assistant is None:
            return
        if show is None:
            show = not self.claude_split.get_show_sidebar()
        self.claude_split.set_show_sidebar(show)
        if show:
            self.assistant.focus_request()
        elif self.active is not None:
            self.active.editor.view.grab_focus()

    def _claude_cards(self):
        self.show_claude(True)
        self.assistant.make_cards()

    def _claude_desk(self):
        if self.assistant is not None:
            self.assistant.continue_in_desk()

    # -------------------------------------------------------------- views --

    def _view_mode_from_config(self):
        editor = bool(self.config["editor_visible"])
        preview = bool(self.config["preview_visible"])
        if editor and preview:
            return "split"
        return "preview" if preview else "editor"

    def set_view_mode(self, mode: str, remember=True):
        self.config["editor_visible"] = mode in ("editor", "split")
        self.config["preview_visible"] = mode in ("preview", "split")
        self._setting_view = True
        try:
            for name, button in self._view_toggles.items():
                button.set_active(name == mode)
        finally:
            self._setting_view = False
        for document in self.documents:
            self._apply_view_to(document)
        if remember:
            self.config.save()

    def _view_toggled(self, button, name):
        if self._setting_view:
            return
        if button.get_active():
            self.set_view_mode(name)
        elif not any(other.get_active() for other in self._view_toggles.values()):
            # The last one cannot be turned off; something has to be showing.
            button.set_active(True)

    def _apply_view_to(self, document):
        document.set_panes(bool(self.config["editor_visible"]),
                           bool(self.config["preview_visible"]))
        if self.config["preview_visible"]:
            document.ensure_rendered()

    def _apply_sidebar(self):
        visible = bool(self.config["sidebar_visible"])
        self.outline.set_visible(visible and not self._narrow)
        self.sidebar_button.set_active(visible)
        if visible and self._narrow:
            widgets.toast(self, "The outline needs a wider window")

    def _apply_focus(self):
        for document in self.documents:
            document.editor.set_focus_mode(bool(self.config["focus_mode"]))

    def _apply_typewriter(self):
        for document in self.documents:
            document.editor.set_typewriter(bool(self.config["typewriter"]))

    def _apply_wrap(self):
        for document in self.documents:
            document.editor.set_wrap(bool(self.config["wrap"]))

    def _apply_gutter(self):
        for document in self.documents:
            document.editor.set_gutter_visible(bool(self.config["show_gutter"]))

    def _apply_fonts(self):
        for document in self.documents:
            document.apply_fonts()

    def _apply_remote_images(self):
        for document in self.documents:
            document.preview.load_remote_images = bool(self.config["load_remote_images"])
            document.render_now()

    def zoom(self, direction: int):
        """Ctrl+± moves both halves a point at a time; Ctrl+0 is the shipped size.

        Only the size moves. A family chosen in the Fonts dialog is a choice
        about what the text looks like, not about how big it is, and zooming
        is no reason to take it back.
        """
        if self.active is None:
            return
        if direction == 0:
            text = int(DEFAULTS["editor_font_size"])
            page = int(DEFAULTS["preview_font_size"])
        else:
            text = int(self.config["editor_font_size"]) + direction
            page = int(self.config["preview_font_size"]) + direction
        self.config["editor_font_size"] = text
        self.config["preview_font_size"] = page
        self._apply_fonts()
        # Both halves clamp what they are handed, so the config keeps what
        # they settled on rather than what it asked for — otherwise holding
        # Ctrl+− wanders off past the limit and the way back is as long.
        self.config["editor_font_size"] = self.active.editor.get_font_size()
        self.config["preview_font_size"] = render.page_font()[1]
        self.config.save()

    # ------------------------------------------------------------ inserting --

    def insert_link(self):
        """Ctrl+K. A URL already on the clipboard fills the target in."""
        clipboard = self.get_display().get_clipboard()

        def arrived(source, result):
            try:
                text = (source.read_text_finish(result) or "").strip()
            except GLib.Error:
                text = ""
            url = text if text.startswith(("http://", "https://", "mailto:")) else ""
            self.editor.make_link(url)

        clipboard.read_text_async(None, arrived)

    def insert_code_block(self):
        from .editor import CURSOR, SELECTION

        self.editor.insert_snippet(f"```{CURSOR}\n{SELECTION}\n```\n")

    def insert_table(self):
        widgets.ask_text(
            self, "Insert a table", "Columns and rows, as 3x2.", "3x2", "Insert",
            lambda answer: self._do_insert_table(answer), placeholder="3x2")

    def _do_insert_table(self, answer):
        match = re.match(r"^\s*(\d+)\s*[x×*,]\s*(\d+)\s*$", answer)
        columns, rows = (int(match.group(1)), int(match.group(2))) if match else (3, 2)
        self.editor.insert_table(max(1, min(columns, 12)), max(1, min(rows, 50)))

    def insert_footnote(self):
        """A reference at the cursor, and its definition at the end."""
        text = self.editor.get_text()
        used = {int(n) for n in re.findall(r"\[\^(\d+)\]", text)}
        number = next(n for n in range(1, 1000) if n not in used)
        self.editor.insert_snippet(f"[^{number}]")
        buffer = self.editor.buffer
        end = buffer.get_end_iter()
        buffer.insert(end, f"\n\n[^{number}]: ")

    def insert_date(self):
        self.editor.insert_snippet(time.strftime("%Y-%m-%d"))

    # ------------------------------------------------------------ exporting --

    def _export_dialog(self, title, suggested, handler):
        document = self.active
        if document is None:
            return
        dialog = Gtk.FileDialog(title=title)
        dialog.set_initial_name(suggested)
        folder = document.folder or self.config["last_folder"]
        if folder and os.path.isdir(folder):
            dialog.set_initial_folder(Gio.File.new_for_path(folder))

        def chosen(source, result):
            try:
                gfile = source.save_finish(result)
            except GLib.Error:
                return
            if gfile and gfile.get_path():
                extension = os.path.splitext(suggested)[1]
                self._confirm_extended(gfile.get_path(), extension, handler)

        dialog.save(self, None, chosen)

    def _confirm_extended(self, path, extension, then):
        """then(path), with `extension` added when the name was typed without one.

        The dialog already asked about replacing the name as typed; a name
        this adds to is one nobody was asked about, so that gets its own
        question.
        """
        full = with_extension(path, extension)
        if full == path or not os.path.exists(full):
            then(full)
            return
        widgets.confirm(
            self, f"Replace {os.path.basename(full)}?",
            "A file with that name already exists in this folder. Saving will "
            "replace what is in it.",
            "Replace", lambda: then(full))

    def export_html(self):
        document = self.active
        if document is None:
            return
        stem = os.path.splitext(document.name)[0]
        self._export_dialog("Export as HTML", stem + ".html", self._write_html)

    def _write_html(self, path):
        document = self.active
        document.ensure_rendered()
        body = export.to_html(
            document.parsed, title=os.path.splitext(document.name)[0],
            style=str(self.config["export_style"]),
            standalone=bool(self.config["export_standalone"]),
            base_dir=document.folder, embed_images=True)

        def write():
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(body)
            return path

        jobs.run(write,
                 lambda _p: widgets.toast(self, f"Exported {os.path.basename(path)}"),
                 lambda exc: widgets.error_toast(self, exc))

    def export_text(self):
        document = self.active
        if document is None:
            return
        stem = os.path.splitext(document.name)[0]
        self._export_dialog("Export as plain text", stem + ".txt", self._write_text)

    def _write_text(self, path):
        document = self.active
        document.ensure_rendered()
        body = export.to_text(document.parsed)

        def write():
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(body)
            return path

        jobs.run(write,
                 lambda _p: widgets.toast(self, f"Exported {os.path.basename(path)}"),
                 lambda exc: widgets.error_toast(self, exc))

    def export_pandoc(self):
        if not export.pandoc_path():
            widgets.toast(self, "pandoc is not installed — HTML and plain text "
                                "are built in", timeout=6)
            return
        document = self.active
        stem = os.path.splitext(document.name)[0]
        self._export_dialog(
            "Export with pandoc — name it with the extension you want",
            stem + ".pdf", self._write_pandoc)

    def _write_pandoc(self, path):
        document = self.active
        source = document.text()
        folder = document.folder or os.path.dirname(path)
        widgets.toast(self, f"Running pandoc for {os.path.basename(path)}…")

        jobs.run(lambda: export.pandoc_convert(source, path, folder),
                 lambda _r: widgets.toast(self, f"Exported {os.path.basename(path)}"),
                 lambda exc: widgets.error_toast(self, exc))

    def copy_html(self):
        document = self.active
        if document is None:
            return
        document.ensure_rendered()
        body = export.to_html(document.parsed, standalone=False,
                              base_dir=document.folder)
        widgets.copy_text(self, body, "HTML copied")

    def clear_recent(self):
        self.config["recent_files"] = []
        self.config.save()
        self._rebuild_recent_menu()

    # --------------------------------------------------------------- panes --

    def show_statistics(self):
        document = self.active
        if document is None:
            return
        document.ensure_rendered()
        stats = markdown.statistics_of(document.parsed, document.text())
        body = "\n".join([
            f"Words             {stats['words']:,}",
            f"Characters        {stats['characters']:,}",
            f"With spaces       {stats['characters_with_spaces']:,}",
            f"Sentences         {stats['sentences']:,}",
            f"Lines             {stats['lines']:,}",
            f"Reading time      about {stats['reading_minutes']} min",
        ])
        dialog = Adw.AlertDialog(heading=document.name, body="")
        label = Gtk.Label(label=body, xalign=0, selectable=True,
                          css_classes=["monospace"])
        dialog.set_extra_child(label)
        dialog.add_response("close", "Close")
        dialog.set_default_response("close")
        dialog.present(self)

    def show_preferences(self):
        """The theme and the glass — the house Look page.

        Named and placed the way every other app here names and places it,
        which is the whole point: the first version of this put the themes
        on an "Appearance…" item inside the *text size* section, and the
        answer that came back was that Notedown had no themes. Everything
        else in this app stays a toggle in the menu, where it is already
        easier to reach than a settings page.
        """
        dialog = Adw.PreferencesDialog(title="Preferences")
        dialog.add(housestyle.look_page(self.config, self.glazing.restyle))
        dialog.add(self._writing_page())
        dialog.present(self)

    def _writing_page(self):
        """The handful of settings that are about the text rather than the app."""
        page = Adw.PreferencesPage(title="Writing",
                                   icon_name="document-edit-symbolic")
        group = Adw.PreferencesGroup(
            title="The page",
            description="How the rendered half reads what you type.")

        row = Adw.SwitchRow(
            title="A new line is a new line",
            subtitle="Off, consecutive lines run together into one paragraph "
                     "and a break needs two trailing spaces — which is what "
                     "Markdown itself says, and what a renderer you export to "
                     "will do.",
            active=bool(self.config["line_breaks"]))

        def toggled(switch, _param):
            self.config["line_breaks"] = switch.get_active()
            self.config.save()
            for document in self.documents:
                document.render_now()

        row.connect("notify::active", toggled)
        group.add(row)
        page.add(group)
        return page

    def show_fonts(self):
        """Choose the font for each half. It applies as it is chosen."""
        dialog = Adw.AlertDialog(
            heading="Fonts",
            body="The text you write in, and the page it is drawn as.")

        grid = Gtk.Grid(column_spacing=12, row_spacing=10)
        for row, (label, family_key, size_key) in enumerate((
                ("Text", "editor_font_family", "editor_font_size"),
                ("Page", "preview_font_family", "preview_font_size"))):
            grid.attach(Gtk.Label(label=label, xalign=1.0), 0, row, 1, 1)
            grid.attach(self._font_button(label, family_key, size_key), 1, row, 1, 1)
        dialog.set_extra_child(grid)

        dialog.add_response("defaults", "Defaults")
        dialog.add_response("close", "Done")
        dialog.set_default_response("close")
        dialog.connect("response", self._fonts_answered)
        dialog.present(self)

    def _font_button(self, label, family_key, size_key):
        description = Pango.FontDescription()
        description.set_family(self.config[family_key] or self._desktop_font_family())
        description.set_size(int(self.config[size_key]) * Pango.SCALE)

        button = Gtk.FontDialogButton(
            dialog=Gtk.FontDialog(title=f"{label} Font"),
            level=Gtk.FontLevel.FONT, font_desc=description, hexpand=True)
        button.connect("notify::font-desc", self._font_chosen, family_key, size_key)
        return button

    def _desktop_font_family(self) -> str:
        """What GTK would use if this app named no font at all."""
        settings = Gtk.Settings.get_default()
        name = settings.get_property("gtk-font-name") if settings else ""
        return Pango.FontDescription.from_string(name or "Sans 10").get_family() or "Sans"

    def _font_chosen(self, button, _parameter, family_key, size_key):
        description = button.get_font_desc()
        if description is None:
            return
        # The family and the size, and not the face: the editor draws bold
        # where the Markdown says bold, and a font that arrives bold has
        # nothing left to say it with.
        size = description.get_size() / Pango.SCALE
        if description.get_size_is_absolute():
            size *= 0.75                      # pixels at 96dpi, as points
        self.config[family_key] = description.get_family() or ""
        if size:
            self.config[size_key] = int(round(size))
        self._apply_fonts()
        self.config.save()

    def _fonts_answered(self, _dialog, response):
        if response != "defaults":
            return
        for key in ("editor_font_family", "editor_font_size",
                    "preview_font_family", "preview_font_size"):
            self.config[key] = DEFAULTS[key]
        self._apply_fonts()
        self.config.save()

    def show_shortcuts(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        for name, rows in SHORTCUTS:
            heading = Gtk.Label(label=name, xalign=0, css_classes=["shortcut-group"])
            box.append(heading)
            grid = Gtk.Grid(column_spacing=14, row_spacing=3)
            for index, (keys, what) in enumerate(rows):
                key = Gtk.Label(label=keys, xalign=1, css_classes=["shortcut-key"])
                grid.attach(key, 0, index, 1, 1)
                grid.attach(Gtk.Label(label=what, xalign=0), 1, index, 1, 1)
            box.append(grid)

        scroller = Gtk.ScrolledWindow(propagate_natural_height=True, hexpand=True)
        scroller.set_max_content_height(520)
        scroller.set_child(box)

        dialog = Adw.AlertDialog(heading="Keyboard shortcuts", body="")
        dialog.set_extra_child(scroller)
        dialog.add_response("close", "Close")
        dialog.set_default_response("close")
        dialog.present(self)

    def show_about(self):
        about = Adw.AboutDialog(
            application_name="Notedown",
            application_icon="accessories-text-editor",
            version=__version__,
            developer_name="Robin",
            comments=("A Markdown editor with the rendered page beside the text.\n\n"
                      "The preview is built from GTK widgets rather than a web "
                      "engine, so it costs a few megabytes instead of a few "
                      "hundred, and it renders the same document the exporter "
                      "writes."),
            license_type=Gtk.License.MIT_X11,
            website="https://github.com/RobinCodes",
        )
        about.present(self)

    # ------------------------------------------------------------ geometry --

    def _remember_geometry(self):
        self.geometry.save()
        if self.active is not None:
            fraction = self.active.split_fraction()
            if fraction is not None and not self._narrow:
                self.config["split_fraction"] = round(fraction, 4)
            self.config["editor_font_size"] = self.active.editor.get_font_size()
            self.config["preview_font_size"] = render.page_font()[1]
        if self.outline.get_visible():
            self.config["sidebar_width"] = self.side_paned.get_position()
        self.config.save()

    # -------------------------------------------------------- files on disk --

    def _on_active_changed(self, *_):
        if not self.props.is_active or self.active is None:
            return
        document = self.active
        if not document.path or not os.path.exists(document.path):
            return
        mtime = _mtime(document.path)
        if not document.mtime or mtime <= document.mtime:
            return
        if self._reload_asked.get(document.path) == mtime:
            return
        self._reload_asked[document.path] = mtime

        if not document.modified:
            try:
                text, encoding, newline = _read_text(document.path)
            except OSError:
                return
            document.encoding, document.newline = encoding, newline
            line, _ = document.editor.cursor_position()
            document.set_text(text)
            document.editor.mark_saved()
            document.editor.goto_line(line, focus=False)
            document.mtime = mtime
            widgets.toast(self, f"{document.name} changed on disk — reloaded")
            return

        widgets.confirm(
            self, "The file changed on disk",
            f"{document.name} was edited elsewhere, and you have unsaved "
            f"changes here. Reloading takes the version on disk.",
            "Reload", self.reload_document)

    # ------------------------------------------------------------- closing --

    def _on_close(self, *_):
        if self._closing:
            return False

        unsaved = [d for d in self.documents if d.modified]
        if not unsaved:
            self._shut_down()
            return False

        names = ", ".join(d.name for d in unsaved[:3])
        if len(unsaved) > 3:
            names += f" and {len(unsaved) - 3} more"
        dialog = Adw.AlertDialog(
            heading="Save before closing?",
            body=f"{names} {'have' if len(unsaved) > 1 else 'has'} changes that "
                 f"are not on disk.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("discard", "Discard")
        dialog.add_response("save", "Save all")
        dialog.set_response_appearance("discard", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("save")
        dialog.set_close_response("cancel")

        def answered(dlg, result):
            answer = dlg.choose_finish(result)
            if answer == "cancel":
                return
            if answer == "save":
                remaining = [d for d in unsaved if d.path]
                for document in remaining:
                    self.save_document(document, silent=True)
                if any(not d.path for d in unsaved):
                    # One has never been saved; ask where, and let the user
                    # close again once they have said.
                    self.tabs.set_selected_page(
                        self._page_of(next(d for d in unsaved if not d.path)))
                    self.save_as()
                    return
            self._closing = True
            self._shut_down()
            self.close()

        dialog.choose(self, None, answered)
        return True

    def _shut_down(self):
        self.config["open_files"] = [d.path for d in self.documents if d.path]
        self._remember_geometry()
        for document in self.documents:
            document.close()


# ---------------------------------------------------------------- helpers --


def _mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def _read_text(path: str) -> tuple[str, str, str]:
    """Read a file, working out its encoding and its line endings.

    UTF-8 is assumed, because it is what Markdown is written in; a file that
    is not valid UTF-8 is read as Latin-1 rather than refused, since losing a
    document to a mis-guessed byte is worse than showing one odd character.
    The line ending is remembered so a file written on Windows is saved back
    the way it came.
    """
    with open(path, "rb") as fh:
        blob = fh.read()

    encoding = "utf-8"
    if blob.startswith(b"\xef\xbb\xbf"):
        encoding = "utf-8-sig"
    try:
        text = blob.decode(encoding)
    except UnicodeDecodeError:
        encoding = "latin-1"
        text = blob.decode(encoding)

    newline = "\r\n" if "\r\n" in text else "\n"
    return text.replace("\r\n", "\n").replace("\r", "\n"), encoding, newline
