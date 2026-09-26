"""One open document: its editor, its preview, and what keeps them together.

The window used to hold all of this directly, because there was only ever one
document. With tabs there are several, and each needs its own buffer, its own
rendered page and its own idea of where it is scrolled to — a document being
edited in a background tab must not write its outline into whichever tab
happens to be in front.

So it lives here, one instance per tab, and the window keeps a reference to
whichever is active. `MainWindow` still spells that `self.editor` and
`self.path`, through properties that forward to the active document, which is
what lets the rest of the window go on being written as though there were one.

The scroll sync is the interesting part. Editor and preview are two different
coordinate systems over the same text, joined only by the source line each
block came from, and either can be the one that moved. A flag says which is
being driven so the other one's scroll handler does not drive it back — two
panes taking turns pushing each other down a document is a loop that only
stops when it reaches the bottom.
"""

from __future__ import annotations

import os

from gi.repository import GLib, Gtk

from . import markdown
from .editor import Editor
from .render import Preview

UNTITLED = "Untitled.md"

# What this app opens as Markdown. Anything else opens too, with the
# highlighting off and the preview showing it as the plain text it is.
MARKDOWN_EXTENSIONS = (".md", ".markdown", ".mdown", ".mkd", ".mkdn", ".mdwn",
                       ".mdtxt", ".mdtext", ".text", ".rmd", ".qmd")

PREVIEW_DEBOUNCE_MS = 140


class Document:
    """A single Markdown buffer and everything that belongs to it."""

    def __init__(self, window, path: str | None = None):
        self.window = window
        self.path = path
        self.config = window.config

        self.encoding = "utf-8"
        self.newline = "\n"
        self.saved_text = ""
        self.mtime = 0.0
        self.parsed: markdown.Document | None = None

        self._render_timer = 0
        self._auto_save_timer = 0
        self._syncing = False
        self._dirty = True

        self.editor = Editor(
            on_changed=self._edited,
            on_cursor=lambda: window.document_cursor_moved(self),
            on_scrolled=self._editor_scrolled,
        )
        self.editor.set_font_size(self.config["editor_font_size"])
        self.editor.set_wrap(bool(self.config["wrap"]))
        self.editor.set_gutter_visible(bool(self.config["show_gutter"]))
        self.editor.set_focus_mode(bool(self.config["focus_mode"]))
        self.editor.set_typewriter(bool(self.config["typewriter"]))
        self.editor.set_markdown(self.is_markdown)

        self.preview = Preview(
            on_line=self._preview_line_activated,
            on_task=self._preview_task_toggled,
            on_link=lambda url: window.open_url(self, url),
        )
        self.preview.set_font_scale(float(self.config["preview_scale"]))
        self.preview.set_reading_width(int(self.config["reading_width"]))
        self.preview.load_remote_images = bool(self.config["load_remote_images"])
        self.preview.get_vadjustment().connect("value-changed", self._preview_scrolled)

        self.paned = Gtk.Paned(
            orientation=Gtk.Orientation.HORIZONTAL,
            resize_start_child=True, resize_end_child=True,
            shrink_start_child=False, shrink_end_child=False,
        )
        self.paned.set_start_child(self.editor)
        self.paned.set_end_child(self.preview)
        self._split_placed = False
        self.paned.connect("notify::max-position", self._place_split)

        self.root = self.paned

    # ------------------------------------------------------------- naming --

    @property
    def name(self) -> str:
        return os.path.basename(self.path) if self.path else UNTITLED

    @property
    def folder(self) -> str:
        return os.path.dirname(self.path) if self.path else ""

    @property
    def modified(self) -> bool:
        return self.editor.modified

    @property
    def is_markdown(self) -> bool:
        return self.path is None or self.path.lower().endswith(MARKDOWN_EXTENSIONS)

    def set_path(self, path: str | None):
        self.path = path
        self.editor.set_markdown(self.is_markdown)
        self.render_now()

    def is_at(self, path: str) -> bool:
        """True when this document is the file at `path`.

        Compared as normalised paths, and case-folded where the filesystem is,
        so opening the same file from two spellings finds the tab that already
        has it rather than making a second one.
        """
        if not self.path or not path:
            return False
        return (os.path.normcase(os.path.normpath(self.path))
                == os.path.normcase(os.path.normpath(path)))

    def title(self) -> str:
        """The name to show: the document's own, or the file's."""
        if self.parsed is not None:
            own = markdown.title_of(self.parsed)
            if own and not self.path:
                return own
        return self.name

    # ---------------------------------------------------------- the layout --

    def _place_split(self, paned, _pspec):
        if self._split_placed:
            return
        total = paned.get_property("max-position")
        if total <= 100:
            return
        self._split_placed = True
        fraction = float(self.config["split_fraction"] or 0.5)
        if not 0.1 <= fraction <= 0.9:
            fraction = 0.5
        paned.set_position(int(total * fraction))

    def split_fraction(self) -> float | None:
        total = self.paned.get_property("max-position")
        if total <= 100:
            return None
        return self.paned.get_position() / total

    def set_panes(self, editor_visible: bool, preview_visible: bool):
        """Show the editor, the preview, or both. Never neither."""
        if not editor_visible and not preview_visible:
            editor_visible = True
        self.editor.set_visible(editor_visible)
        self.preview.set_visible(preview_visible)

    # ------------------------------------------------------------ the text --

    def text(self) -> str:
        return self.editor.get_text()

    def set_text(self, text: str):
        self.editor.set_text(text)
        self.saved_text = text
        self.render_now()

    def _edited(self):
        self._dirty = True
        if self._render_timer:
            GLib.source_remove(self._render_timer)
        self._render_timer = GLib.timeout_add(PREVIEW_DEBOUNCE_MS, self._render_tick)
        self.window.document_edited(self)
        self._schedule_auto_save()

    def _render_tick(self):
        self._render_timer = 0
        self.render_now()
        return GLib.SOURCE_REMOVE

    def render_now(self):
        """Re-parse and rebuild the preview. Cheap enough to do on a timer."""
        if self._render_timer:
            GLib.source_remove(self._render_timer)
            self._render_timer = 0
        text = self.text()
        if self.is_markdown:
            self.parsed = markdown.parse(text)
        else:
            # Not Markdown: show it as what it is rather than inventing
            # emphasis out of a shell script's asterisks.
            self.parsed = markdown.Document(
                [markdown.Block("code", 0, text=text,
                                language=_language_for(self.path))],
                {}, {}, "", text.split("\n"))
        if self.preview.get_visible():
            self.preview.render(self.parsed, self.folder)
        self._dirty = False
        self.window.document_rendered(self)

    def ensure_rendered(self):
        """Render if something changed while the preview was hidden."""
        if self._dirty:
            self.render_now()

    # ------------------------------------------------------------ scrolling --

    def _editor_scrolled(self):
        if self._syncing or not self.config["sync_scroll"]:
            return
        if not self.preview.get_visible() or not self.editor.get_visible():
            return
        self._syncing = True
        try:
            line, within = self.editor.top_visible_line()
            self.preview.scroll_to_line(line, within)
        finally:
            GLib.idle_add(self._release_sync)

    def _preview_scrolled(self, _adjustment):
        if self._syncing or not self.config["sync_scroll"]:
            return
        if not self.preview.get_visible() or not self.editor.get_visible():
            return
        # The preview only drives the editor when the pointer is over it;
        # otherwise a preview settling after a rebuild would yank the text.
        if not self.preview.has_pointer():
            return
        self._syncing = True
        try:
            self.editor.scroll_line_to_top(self.preview.line_at_top())
        finally:
            GLib.idle_add(self._release_sync)

    def _release_sync(self):
        self._syncing = False
        return GLib.SOURCE_REMOVE

    def sync_preview_to_cursor(self):
        line, _ = self.editor.cursor_position()
        self.preview.scroll_to_line(line - 1, 0.0)

    # --------------------------------------------------------- the preview --

    def _preview_line_activated(self, line: int):
        self.editor.goto_line(line + 1)

    def _preview_task_toggled(self, line: int, column: int, ticked: bool):
        self.editor.set_task_at(line, column, ticked)

    # -------------------------------------------------------------- saving --

    def _schedule_auto_save(self):
        if self._auto_save_timer:
            GLib.source_remove(self._auto_save_timer)
            self._auto_save_timer = 0
        if not self.config["auto_save"] or not self.path:
            return
        delay = max(400, int(self.config["auto_save_delay_ms"]))
        self._auto_save_timer = GLib.timeout_add(delay, self._auto_save_tick)

    def _auto_save_tick(self):
        self._auto_save_timer = 0
        if self.modified and self.path:
            self.window.save_document(self, silent=True)
        return GLib.SOURCE_REMOVE

    def text_to_write(self) -> str:
        """The buffer as it should land on disk, with the tidying applied."""
        text = self.text()
        if self.config["trim_trailing_space"]:
            text = "\n".join(line.rstrip() for line in text.split("\n"))
        if self.config["final_newline"] and text and not text.endswith("\n"):
            text += "\n"
        if self.newline != "\n":
            text = text.replace("\n", self.newline)
        return text

    def close(self):
        """Stop anything still owed. The tab is going away."""
        for timer in ("_render_timer", "_auto_save_timer"):
            handle = getattr(self, timer)
            if handle:
                GLib.source_remove(handle)
                setattr(self, timer, 0)


def _language_for(path: str | None) -> str:
    if not path:
        return ""
    return os.path.splitext(path)[1].lstrip(".").lower()
