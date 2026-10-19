"""The pane on the right: what this file actually is.

A preview is worth having only if it is instant and never lies. Both of those
rule things out:

*Nothing is read on the GTK thread.* A text preview of a file on a sleeping
network mount would otherwise freeze the window for as long as the disk takes
to wake up. Everything is read on a worker and arrives by `idle_add`, and a
reply for a file the user has already scrolled past is dropped by generation
number.

*Only the start of a file is read.* Sixty-four kilobytes of a text file is
more than fits on the screen, and reading a four-gigabyte log to show its
first page is how a preview pane becomes the reason the app is slow.

What each kind of file gets: an image or a PDF gets its picture, a text file
gets its first page, an archive gets its contents, a video or a sound file
gets what `ffprobe` says about it, a folder gets a count, and everything gets
its name, type, size and dates.
"""

from __future__ import annotations

import os
import subprocess
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")

from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from . import archive, filetypes, thumbs, units, winenv  # noqa: E402

TEXT_BYTES = 64 * 1024
ARCHIVE_ROWS = 60


class Preview(Gtk.Box):
    """One of these per pane. `show(entry)` or `show(None)`."""

    def __init__(self, config):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.config = config
        self.add_css_class("preview-pane")
        self.entry = None
        self._generation = 0

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        for side in ("start", "end", "top", "bottom"):
            getattr(self.body, f"set_margin_{side}")(14)
        scroller.set_child(self.body)
        self.append(scroller)

        self.art = Gtk.Box()
        self.art.set_halign(Gtk.Align.CENTER)
        self.body.append(self.art)

        self.title = Gtk.Label(xalign=0.0)
        self.title.add_css_class("preview-title")
        self.title.set_wrap(True)
        self.title.set_wrap_mode(2)
        self.title.set_selectable(True)
        self.body.append(self.title)

        self.kind = Gtk.Label(xalign=0.0)
        self.kind.add_css_class("preview-kind")
        self.kind.set_wrap(True)
        self.body.append(self.kind)

        self.facts = Gtk.Grid(column_spacing=10, row_spacing=4)
        self.body.append(self.facts)

        self.extra = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.body.append(self.extra)

        self.show_nothing()

    # -- the two states ---------------------------------------------------

    def show_nothing(self, many: int = 0) -> None:
        self._generation += 1
        self.entry = None
        _empty(self.art)
        _empty(self.extra)
        _clear_grid(self.facts)
        if many > 1:
            self.title.set_label(f"{many} items")
            self.kind.set_label("Select one of them to look at it")
        else:
            self.title.set_label("Nothing selected")
            self.kind.set_label("")

    def show(self, entry) -> None:
        if entry is None:
            self.show_nothing()
            return
        if self.entry is not None and entry.path == self.entry.path:
            return
        self._generation += 1
        generation = self._generation
        self.entry = entry

        _empty(self.art)
        _empty(self.extra)
        _clear_grid(self.facts)

        self.title.set_label(entry.name)
        self.kind.set_label(entry.type_label())
        self._facts(entry)
        self._art(entry)

        category = entry.category
        if entry.is_dir:
            self._load(generation, _folder_facts, entry.path)
        elif category in ("text", "code") or _looks_textual(entry):
            self._load(generation, _text_head, entry.path)
        elif category == "archive":
            self._load(generation, _archive_list, entry.path)
        elif category in ("video", "audio"):
            self._load(generation, _media_facts, entry.path)

    # -- the parts --------------------------------------------------------

    def _facts(self, entry) -> None:
        rows = []
        if not entry.is_dir:
            rows.append(("Size", units.human_size(entry.size)))
        rows.append(("Modified", units.human_time(entry.mtime)))
        rows.append(("Access", ("d" if entry.is_dir else "-")
                     + units.human_mode(entry.mode)))
        if entry.is_link:
            rows.append(("Links to", entry.link_target or "?"))
        for row, (key, value) in enumerate(rows):
            label = Gtk.Label(label=key, xalign=0.0)
            label.add_css_class("field-key")
            shown = Gtk.Label(label=value, xalign=0.0)
            shown.add_css_class("field-value")
            shown.set_ellipsize(3)
            shown.set_hexpand(True)
            shown.set_selectable(True)
            self.facts.attach(label, 0, row, 1, 1)
            self.facts.attach(shown, 1, row, 1, 1)

    def _art(self, entry) -> None:
        """The picture, if there is one to be had without waiting."""
        texture = None
        if self.config["show_thumbnails"]:
            cached = thumbs.valid_cached(entry.path, 512)
            if cached:
                try:
                    texture = Gdk.Texture.new_from_filename(cached)
                except GLib.Error:
                    texture = None
        if texture is None and filetypes.is_image(entry.name) \
                and entry.size < 12 * 1024 * 1024:
            # A small image is quicker to load whole than to thumbnail.
            try:
                texture = Gdk.Texture.new_from_filename(entry.path)
            except GLib.Error:
                texture = None
        if texture is not None:
            picture = Gtk.Picture.new_for_paintable(texture)
            picture.set_content_fit(Gtk.ContentFit.CONTAIN)
            picture.set_size_request(-1, 180)
            picture.add_css_class("preview-image")
            self.art.append(picture)
            return

        icon = Gtk.Image.new_from_gicon(
            filetypes.icon_for(entry.path, entry.is_dir))
        icon.set_pixel_size(64)
        icon.add_css_class("entry-icon")
        icon.add_css_class(f"cat-{entry.category}")
        self.art.append(icon)
        # Ask for a thumbnail anyway; it will arrive for the next look.
        if self.config["show_thumbnails"] and thumbs.can_thumbnail(
                entry, int(self.config["thumbnail_limit_mb"])):
            self._load(self._generation, _make_thumbnail, entry.path)

    def _load(self, generation: int, worker, path: str) -> None:
        def run():
            try:
                result = worker(path)
            except (OSError, GLib.Error, subprocess.SubprocessError):
                result = None
            GLib.idle_add(self._arrived, generation, result)

        threading.Thread(target=run, daemon=True, name="hermione-preview").start()

    def _arrived(self, generation: int, result) -> bool:
        if generation != self._generation or result is None:
            return False
        kind, payload = result
        if kind == "text":
            self.extra.append(_text_view(payload))
        elif kind == "rows":
            for key, value in payload:
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                label = Gtk.Label(label=key, xalign=0.0)
                label.add_css_class("field-key")
                label.set_size_request(86, -1)
                shown = Gtk.Label(label=value, xalign=0.0)
                shown.add_css_class("field-value")
                shown.set_ellipsize(3)
                shown.set_hexpand(True)
                row.append(label)
                row.append(shown)
                self.extra.append(row)
        elif kind == "list":
            heading, names = payload
            title = Gtk.Label(label=heading, xalign=0.0)
            title.add_css_class("field-key")
            self.extra.append(title)
            for name in names:
                label = Gtk.Label(label=name, xalign=0.0)
                label.add_css_class("preview-text")
                label.set_ellipsize(1)
                self.extra.append(label)
        elif kind == "thumb":
            try:
                texture = Gdk.Texture.new_from_filename(payload)
            except GLib.Error:
                return False
            _empty(self.art)
            picture = Gtk.Picture.new_for_paintable(texture)
            picture.set_content_fit(Gtk.ContentFit.CONTAIN)
            picture.set_size_request(-1, 180)
            picture.add_css_class("preview-image")
            self.art.append(picture)
        return False


# ----------------------------------------------------------- the workers ---
# Each returns (kind, payload) or None, and runs off the GTK thread.

def _text_head(path: str):
    with open(path, "rb") as handle:
        raw = handle.read(TEXT_BYTES)
    if b"\0" in raw[:4096]:
        return None
    text = raw.decode("utf-8", "replace")
    if len(raw) == TEXT_BYTES:
        text += "\n…"
    return "text", text


def _archive_list(path: str):
    members, error = archive.list_contents(path, ARCHIVE_ROWS + 1)
    if error and not members:
        return "rows", [("Archive", error)]
    names = [member.name for member in members[:ARCHIVE_ROWS]]
    if len(members) > ARCHIVE_ROWS:
        names.append("…")
    total = sum(member.size for member in members)
    heading = f"{len(members)} entries, {units.human_size(total)} unpacked"
    return "list", (heading, names)


def _folder_facts(path: str):
    files = 0
    folders = 0
    try:
        with os.scandir(path) as scanner:
            for item in scanner:
                try:
                    if item.is_dir(follow_symlinks=False):
                        folders += 1
                    else:
                        files += 1
                except OSError:
                    files += 1
    except OSError as exc:
        return "rows", [("Folder", exc.strerror or "cannot be read")]
    return "rows", [("Contains", units.human_items(files, folders))]


def _media_facts(path: str):
    """Duration, dimensions and codec, from ffprobe where it exists."""
    tool = winenv.tool("ffprobe")
    if not tool:
        return None
    done = subprocess.run(
        [tool, "-v", "error", "-show_entries",
         "format=duration,bit_rate:stream=codec_name,codec_type,width,height,"
         "channels,sample_rate",
         "-of", "default=noprint_wrappers=1", path],
        capture_output=True, text=True, timeout=12,
        creationflags=winenv.NO_WINDOW)
    if done.returncode != 0:
        return None
    found = {}
    streams = []
    current = {}
    for line in done.stdout.splitlines():
        key, _, value = line.partition("=")
        if key == "codec_name" and current:
            streams.append(current)
            current = {}
        if key in ("codec_name", "codec_type", "width", "height", "channels",
                   "sample_rate"):
            current[key] = value
        else:
            found[key] = value
    if current:
        streams.append(current)

    rows = []
    duration = found.get("duration", "")
    try:
        rows.append(("Length", units.human_duration(float(duration))))
    except ValueError:
        pass
    for stream in streams:
        if stream.get("codec_type") == "video" or "width" in stream:
            size = f"{stream.get('width', '?')}×{stream.get('height', '?')}"
            rows.append(("Picture", f"{size}  {stream.get('codec_name', '')}"))
        elif stream.get("codec_type") == "audio" or "channels" in stream:
            rate = stream.get("sample_rate", "")
            rate = f"{int(rate) // 1000} kHz" if rate.isdigit() else ""
            rows.append(("Sound", " ".join(
                part for part in (stream.get("codec_name", ""),
                                  f"{stream.get('channels', '')} ch", rate)
                if part.strip())))
    rate = found.get("bit_rate", "")
    if rate.isdigit():
        rows.append(("Bitrate", f"{int(rate) // 1000} kb/s"))
    return ("rows", rows) if rows else None


def _make_thumbnail(path: str):
    png = thumbs.generate(path, 512)
    return ("thumb", png) if png else None


def _text_view(text: str) -> Gtk.Widget:
    view = Gtk.TextView()
    view.set_editable(False)
    view.set_monospace(True)
    view.set_cursor_visible(False)
    view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
    view.add_css_class("preview-text")
    view.get_buffer().set_text(text)
    frame = Gtk.ScrolledWindow()
    frame.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    frame.set_min_content_height(200)
    frame.set_max_content_height(460)
    frame.set_propagate_natural_height(True)
    frame.add_css_class("panel")
    frame.set_child(view)
    return frame


def _looks_textual(entry) -> bool:
    """Would this be readable as text even though its category says otherwise?"""
    mime = filetypes.mime_type(entry.path)
    return bool(mime and (mime.startswith("text/")
                          or mime in ("application/json", "application/xml",
                                      "application/javascript",
                                      "application/x-shellscript")))


def _empty(box: Gtk.Box) -> None:
    while (child := box.get_first_child()) is not None:
        box.remove(child)


def _clear_grid(grid: Gtk.Grid) -> None:
    while (child := grid.get_first_child()) is not None:
        grid.remove(child)
