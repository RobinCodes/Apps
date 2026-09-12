"""One open document: its editor, its preview, and the state of its build.

The window used to hold all of this directly, because there was only ever one
document. With tabs there are several, and every one of them needs its own
editor buffer, its own rendered pages, its own problems list and its own
compile in flight — a document being built in a background tab must not write
its result into whichever tab happens to be in front when it finishes.

So all of it lives here, one instance per tab, and the window keeps a
reference to whichever is active. `MainWindow` still spells that reference
`self.editor`, `self.path` and so on, through properties that forward to the
active document — which is what lets the rest of the window go on being
written as though there were one document, because from its point of view
there is.
"""

from __future__ import annotations

import os

from gi.repository import Gtk

from . import compiler, widgets
from .editor import Editor
from .preview import Preview

UNTITLED = "Untitled.tex"


class Document:
    """A single .tex buffer and everything that belongs to it."""

    def __init__(self, window, path: str | None = None):
        self.window = window
        self.path = path

        # The build. `job` is the compile in flight, so it can be cancelled
        # when the text moves on; `pending` records that the text moved on
        # while one was running and another is owed.
        self.result: compiler.Result | None = None
        self.job: compiler.Job | None = None
        self.compiling = False
        self.pending = False
        self.compile_timer = 0

        # Following the cursor is right while you type and wrong on open —
        # nobody wants a freshly opened document scrolled to its last line.
        self.edited_since_load = False

        # What the shared status strip should say for this document when it is
        # brought back to the front, so switching tabs does not leave the last
        # document's numbers standing under this one's pages.
        self.status_text = ""
        self.status_css = ""
        self.problems_label = "Problems"
        self.page_text = ""

        config = window.config

        self.editor = Editor(
            on_changed=lambda: window.document_edited(self),
            on_cursor=lambda: window.document_cursor_moved(self),
        )
        self.editor.set_font_size(config["editor_font_size"])

        self.preview = Preview(
            on_page_changed=lambda page, total: window.document_page_changed(
                self, page, total))
        self.preview.zoom = window.zoom_from_config()
        self.preview.set_page_theme(str(config["pdf_theme"]))

        self.problems = widgets.ProblemsList(on_activate=self.editor.goto_line)
        self.problems_revealer = Gtk.Revealer(
            child=self.problems,
            transition_type=Gtk.RevealerTransitionType.SLIDE_UP,
            reveal_child=False,
        )

        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        left.append(self.editor)
        left.append(Gtk.Separator())
        left.append(self.problems_revealer)

        self.paned = Gtk.Paned(
            orientation=Gtk.Orientation.HORIZONTAL,
            position=config["split_position"],
            resize_start_child=True, resize_end_child=True,
            shrink_start_child=False, shrink_end_child=False,
        )
        self.paned.set_start_child(left)
        self.paned.set_end_child(self.preview)

        # What goes into the Adw.TabPage.
        self.root = self.paned

    # ------------------------------------------------------------- naming --

    @property
    def name(self) -> str:
        """What the tab and the title bar call this document."""
        return os.path.basename(self.path) if self.path else UNTITLED

    @property
    def folder(self) -> str:
        return os.path.dirname(self.path) if self.path else ""

    @property
    def modified(self) -> bool:
        return self.editor.modified

    def is_at(self, path: str) -> bool:
        """True when this document is the file at `path`.

        Compared as normalised paths, and case-folded where the filesystem is,
        so opening the same file twice from two different spellings finds the
        tab that already has it rather than making a second one.
        """
        if not self.path or not path:
            return False
        return (os.path.normcase(os.path.normpath(self.path))
                == os.path.normcase(os.path.normpath(path)))

    # ------------------------------------------------------------ building --

    def cancel_compile(self):
        """Stop anything in flight, and forget anything owed.

        Called when the document is closed: the result would have nowhere to
        go, and the job holds a child process that should not outlive the tab.
        """
        if self.compile_timer:
            from gi.repository import GLib

            GLib.source_remove(self.compile_timer)
            self.compile_timer = 0
        if self.job is not None:
            self.job.cancel()
        self.pending = False
