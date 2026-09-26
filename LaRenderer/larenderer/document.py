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

from . import compiler, project, widgets
from .editor import Editor
from .preview import Preview

UNTITLED = "Untitled.tex"

# Files the editor reads as LaTeX: highlighting, and \end{} closing on Enter.
LATEX_EXTENSIONS = (".tex", ".ltx", ".sty", ".cls", ".bib", ".dtx", ".ins", ".bbx",
                    ".cbx", ".lbx", ".def", ".clo", ".cfg", ".fd", ".tikz", ".pgf")


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

        # The file compiled on this document's behalf the last time it was:
        # itself, or the main document it is a part of. The preview shows
        # that file's pages, and SyncTeX and Export go through it.
        self.compiled_root: str | None = None
        # A document found nearby that \inputs this one, for a part opened
        # without its folder. None until looked for; "" when there is none.
        self.nearby_root: str | None = None

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
        self.editor.latex = self.is_latex

        self.preview = Preview(
            on_page_changed=lambda page, total: window.document_page_changed(
                self, page, total))
        self.preview.zoom = window.zoom_from_config()
        self.preview.on_zoom_changed = lambda: window.document_zoom_changed(self)
        self.preview.set_page_theme(str(config["pdf_theme"]))

        self.problems = widgets.ProblemsList(
            on_activate=self.editor.goto_line,
            on_message=lambda message: window.open_message(self, message))
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
        # The remembered split is a share of the width, applied once the
        # paned knows how wide it is; after that GTK keeps the proportion
        # itself as the window or the side bar changes size.
        self._split_placed = False
        self.paned.connect("notify::max-position", self._place_split)

        # What goes into the Adw.TabPage.
        self.root = self.paned

    def _place_split(self, paned, _pspec):
        if self._split_placed:
            return
        total = paned.get_property("max-position")
        if total <= 100:
            return
        self._split_placed = True
        config = self.window.config
        fraction = float(config["split_fraction"] or 0)
        if not 0.15 <= fraction <= 0.85:
            # An older config only has pixels, measured against a window
            # with no side bar; turn that into the share it was then.
            width = max(1, int(config["window_width"]))
            fraction = min(0.75, max(0.3, int(config["split_position"]) / width))
        paned.set_position(int(total * fraction))

    def split_fraction(self) -> float | None:
        total = self.paned.get_property("max-position")
        if total <= 100:
            return None
        return self.paned.get_position() / total

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

    @property
    def is_tex(self) -> bool:
        """A .tex file, or a buffer never saved — which starts life as one."""
        return self.path is None or project.is_tex(self.path)

    @property
    def is_latex(self) -> bool:
        return self.path is None or self.path.lower().endswith(LATEX_EXTENSIONS)

    def set_path(self, path: str | None):
        """Point the document at a new file: Save As, or a rename in the tree."""
        self.path = path
        self.nearby_root = None
        self.editor.set_latex(self.is_latex)

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
