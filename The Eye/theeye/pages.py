"""Import notebook pages: photographs of paper, one page at a time, into the brain.

The panel this module builds is the same day's step of the learning loop
(SYSTEM.md §6, HANDBOOK §1.3): the pages of a lesson photographed, then each
one straightened, read and filed. It works on the photographs marked in the
strip, or on the one on screen, and goes through them in order:

1. **The corners.** The page is looked for as soon as the photograph is
   shown (`paper.find_corners`), and its four corners are put on it as grips
   that can be dragged — with a magnifier under the one being dragged, since
   a corner is a few pixels on a laptop screen and a fingertip is not. When
   nothing page-like was found the grips sit just inside the photograph and
   the panel says so.
2. **The preview.** A small straightened, enhanced copy beside the form, made
   again whenever a corner is let go of, so what will be filed is what is
   seen.
3. **The form.** Where it goes — the inbox, or one of the subjects under
   `Studies` — the day of the lesson, which starts as the day the camera
   says the photograph was taken, and who reads it: Claude when Claude Code
   is installed, Tesseract when it is, nobody otherwise.

*File this page* hands the page to a background worker and the panel moves
on to the next one at once. One worker, so pages arrive in their note in the
order they were filed; the reading of each runs beside it, because Claude
can take a minute over a page and the next one should not wait for that.
Nothing is written into a photograph, and nothing is deleted: a page is a
new file made from it.
"""

from __future__ import annotations

import datetime
import os
import queue
import shutil
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")

from gi.repository import Gdk, GLib, GObject, Gsk, Gtk  # noqa: E402

from . import (filing, glass, jobs, metadata, paper, photo, reading,  # noqa: E402
               theme as theming, widgets)

HANDLE = 11.0        # the radius of a corner's grip
REACH = 30.0         # how far from a grip a press still takes hold of it
PAD = 26.0           # room around the photograph, so a grip at its edge fits
LOUPE = 58.0         # the radius of the magnifier shown while dragging
MAGNIFY = 3.0

# What the photograph is held at while its page is looked for and previewed.
PREVIEW_SOURCE = 1200
PREVIEW_LONGEST = 440


class CornerEditor(Gtk.Widget):
    """A photograph with the page's four corners on it, to be dragged.

    Outside the page the photograph is dimmed, so the eye goes to what will
    be kept; the edge is drawn in the accent; each corner is a small piece of
    the house's glass with a dot at the exact point. Corners are held as
    fractions of the photograph's width and height, so the editor, the
    preview and the full-size straightening all mean the same thing by them.
    """

    __gtype_name__ = "TheEyeCornerEditor"

    __gsignals__ = {
        # a corner was let go of somewhere new
        "changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, scheme=None):
        super().__init__()
        self._scheme = scheme or theming.get(theming.DEFAULT)
        self._texture = None
        self._corners = list(paper.FALLBACK)
        self._active = -1
        self._hover = -1
        self._origin = (0.0, 0.0)
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_size_request(220, 180)

        drag = Gtk.GestureDrag.new()
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", self._on_drag_end)
        self.add_controller(drag)

        motion = Gtk.EventControllerMotion.new()
        motion.connect("motion", self._on_motion)
        motion.connect("leave", lambda _c: self._set_hover(-1))
        self.add_controller(motion)

    def set_scheme(self, scheme):
        self._scheme = scheme
        self.queue_draw()

    def set_texture(self, texture):
        self._texture = texture
        self.queue_draw()

    def set_corners(self, corners):
        self._corners = [(float(x), float(y)) for x, y in corners]
        self.queue_draw()

    def corners(self) -> tuple:
        return tuple(self._corners)

    # -- geometry ----------------------------------------------------------

    def frame(self) -> tuple[float, float, float, float]:
        """Where the photograph is drawn: fitted, centred, with room for grips."""
        width, height = float(self.get_width()), float(self.get_height())
        if self._texture is not None:
            tw, th = self._texture.get_width(), self._texture.get_height()
        else:
            tw, th = 4, 3
        scale = max(0.01, min((width - PAD * 2) / max(1, tw),
                              (height - PAD * 2) / max(1, th)))
        dw, dh = tw * scale, th * scale
        return (width - dw) / 2, (height - dh) / 2, dw, dh

    def to_widget(self, corner) -> tuple[float, float]:
        x, y, w, h = self.frame()
        return x + corner[0] * w, y + corner[1] * h

    def from_widget(self, x: float, y: float) -> tuple[float, float]:
        fx, fy, w, h = self.frame()
        return (min(1.0, max(0.0, (x - fx) / max(1.0, w))),
                min(1.0, max(0.0, (y - fy) / max(1.0, h))))

    def grip_at(self, x: float, y: float) -> int:
        best, best_distance = -1, REACH
        for index, corner in enumerate(self._corners):
            cx, cy = self.to_widget(corner)
            distance = ((cx - x) ** 2 + (cy - y) ** 2) ** 0.5
            if distance <= best_distance:
                best, best_distance = index, distance
        return best

    # -- input -------------------------------------------------------------

    def _set_hover(self, index):
        if index != self._hover:
            self._hover = index
            self.set_cursor(Gdk.Cursor.new_from_name("grab" if index >= 0 else "default",
                                                     None))
            self.queue_draw()

    def _on_motion(self, _controller, x, y):
        if self._active < 0:
            self._set_hover(self.grip_at(x, y))

    def _on_drag_begin(self, gesture, x, y):
        self._active = self.grip_at(x, y)
        if self._active < 0:
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self._origin = self.to_widget(self._corners[self._active])
        self.set_cursor(Gdk.Cursor.new_from_name("grabbing", None))
        self.queue_draw()

    def _on_drag_update(self, _gesture, dx, dy):
        if self._active < 0:
            return
        self._corners[self._active] = self.from_widget(self._origin[0] + dx,
                                                       self._origin[1] + dy)
        self.queue_draw()

    def _on_drag_end(self, _gesture, _dx, _dy):
        if self._active < 0:
            return
        self._active = -1
        self.set_cursor(None)
        self.queue_draw()
        self.emit("changed")

    # -- drawing -----------------------------------------------------------

    def _quad(self, builder, points):
        builder.move_to(*points[0])
        for point in points[1:]:
            builder.line_to(*point)
        builder.close()

    def do_snapshot(self, snapshot):
        if self._texture is None:
            return
        scheme = self._scheme
        x, y, w, h = self.frame()
        snapshot.append_scaled_texture(self._texture, Gsk.ScalingFilter.LINEAR,
                                       glass.rect(x, y, w, h))
        points = [self.to_widget(corner) for corner in self._corners]

        # Outside the page, dimmed: the photograph with the page cut out of it.
        outside = Gsk.PathBuilder.new()
        outside.add_rect(glass.rect(x, y, w, h))
        self._quad(outside, points)
        snapshot.append_fill(outside.to_path(), Gsk.FillRule.EVEN_ODD, glass.black(0.5))

        edge = Gsk.PathBuilder.new()
        self._quad(edge, points)
        snapshot.append_stroke(edge.to_path(), Gsk.Stroke.new(2.0),
                               glass.rgba(scheme.accent, 0.95))
        for index, (px, py) in enumerate(points):
            self._draw_grip(snapshot, px, py, index in (self._active, self._hover))
        if self._active >= 0:
            self._draw_loupe(snapshot, points[self._active], (x, y, w, h))

    def _draw_grip(self, snapshot, x, y, lifted):
        radius = HANDLE * (1.25 if lifted else 1.0)
        box = glass.rounded(x - radius, y - radius, radius * 2, radius * 2, radius)
        snapshot.append_outset_shadow(box, glass.black(0.45), 0, 2, 0, 8)
        snapshot.push_rounded_clip(box)
        snapshot.append_linear_gradient(
            glass.rect(x - radius, y - radius, radius * 2, radius * 2),
            glass.point(x, y - radius), glass.point(x, y + radius),
            glass.stops((0.0, glass.white(0.97)), (1.0, glass.white(0.80))))
        snapshot.pop()
        glass.ring(snapshot, glass.rgba(self._scheme.accent, 1.0), x - radius,
                   y - radius, radius * 2, radius * 2, radius=radius, thickness=2.5)
        dot = 2.2
        snapshot.push_rounded_clip(glass.rounded(x - dot, y - dot, dot * 2, dot * 2, dot))
        snapshot.append_color(glass.rgba(self._scheme.accent, 1.0),
                              glass.rect(x - dot, y - dot, dot * 2, dot * 2))
        snapshot.pop()

    def _draw_loupe(self, snapshot, point, frame):
        """The photograph again, magnified around the corner being dragged.

        Up and to the side of the corner, on whichever side has room, so the
        hand doing the dragging is never over it.
        """
        px, py = point
        x, y, w, h = frame
        r = LOUPE
        cx = px - r * 1.5 if px > self.get_width() / 2 else px + r * 1.5
        cy = py - r * 1.5 if py > r * 2.4 else py + r * 1.5
        box = glass.rounded(cx - r, cy - r, r * 2, r * 2, r)
        snapshot.append_outset_shadow(box, glass.black(0.5), 0, 4, 0, 14)
        snapshot.push_rounded_clip(box)
        snapshot.append_color(glass.rgba(self._scheme.matte, 1.0),
                              glass.rect(cx - r, cy - r, r * 2, r * 2))
        snapshot.save()
        snapshot.translate(glass.point(cx, cy))
        snapshot.scale(MAGNIFY, MAGNIFY)
        snapshot.translate(glass.point(-px, -py))
        snapshot.append_scaled_texture(self._texture, Gsk.ScalingFilter.LINEAR,
                                       glass.rect(x, y, w, h))
        snapshot.restore()
        accent = glass.rgba(self._scheme.accent, 0.95)
        snapshot.append_color(accent, glass.rect(cx - r, cy - 0.5, r * 2, 1))
        snapshot.append_color(accent, glass.rect(cx - 0.5, cy - r, 1, r * 2))
        snapshot.pop()
        glass.ring(snapshot, glass.white(0.9), cx - r, cy - r, r * 2, r * 2,
                   radius=r, thickness=2.0)


# ----------------------------------------------------------------- the work ----

@dataclass
class Task:
    """One page, as the form left it."""
    path: str
    corners: tuple
    subject: str
    day: datetime.date
    reader: str
    taken: datetime.datetime | None


class Filer:
    """The background worker: straighten, encode, file, then read.

    `report(event, task, detail)` is called on the main loop. Events are
    "filed" (detail: the `filing.Filed`), "read" (detail: the header found,
    a `reading.Header`), "unread" (detail: why) and "failed" (detail: why).
    """

    def __init__(self, report, where=None, straighten=None, ocr=None, transcribe=None):
        self._report = report
        self._where = where
        self._straighten = straighten or paper.straighten
        self._ocr = ocr or reading.ocr
        self._transcribe = transcribe or reading.transcribe
        self._queue: queue.Queue = queue.Queue()
        self._thread = None
        self._lock = threading.Lock()       # the notes, written one at a time
        self._count = threading.Lock()
        self.waiting = 0                     # filed or being read

    def add(self, task: Task) -> None:
        with self._count:
            self.waiting += 1
        self._queue.put(task)
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, daemon=True,
                                            name="the-eye-pages")
            self._thread.start()

    def busy(self) -> bool:
        return self.waiting > 0

    def _say(self, event, task, detail=None, done=False):
        if done:
            with self._count:
                self.waiting = max(0, self.waiting - 1)
        jobs.main(self._report, event, task, detail)

    def _run(self):
        while True:
            task = self._queue.get()
            try:
                self._one(task)
            except Exception as exc:                    # noqa: BLE001
                self._say("failed", task, str(exc) or "The page could not be filed",
                          done=True)

    def _one(self, task: Task):
        page = self._straighten(task.path, task.corners)
        data = paper.jpeg(page)
        with self._lock:
            filed = filing.file_page(data, task.subject, task.day, source=task.path,
                                     taken=task.taken, reader=task.reader,
                                     where=self._where)
        self._say("filed", task, filed, done=task.reader == "none")
        if task.reader == "none":
            return
        scratch = Path(tempfile.mkdtemp(prefix="the-eye-read-"))

        def landed(ok, text):
            try:
                if ok:
                    self._place_text(task, filed, text)
                elif text != "cancelled":
                    self._say("unread", task, text)
            finally:
                shutil.rmtree(scratch, ignore_errors=True)
                self._say("settled", task, None, done=True)

        if task.reader == "claude":
            image = scratch / "page.jpg"
            image.write_bytes(data)
            self._transcribe(image, landed)
            return
        # Tesseract reads the page losslessly: the JPEG's ringing round the
        # strokes costs it letters it would otherwise get.
        image = scratch / "page.png"
        paper.to_pixbuf(page).savev(str(image), "png", [], [])

        def read():
            try:
                text = self._ocr(image)
            except reading.Unreadable as exc:
                landed(False, str(exc))
                return
            except Exception as exc:                    # noqa: BLE001
                landed(False, f"Tesseract failed: {exc}")
                return
            landed(True, text)

        threading.Thread(target=read, daemon=True, name="the-eye-ocr").start()

    def _place_text(self, task, filed, text):
        header = reading.header(text)
        body = filing.format_text(text, task.reader)
        with self._lock:
            placed = filing.add_text(filed.note, filed.link, body)
            if filed.subject == filing.INBOX and header.day:
                filing.update_meta(filed.note, {"lesson": f"{header.day:%Y-%m-%d}",
                                                "topic": header.topic,
                                                "page": header.page})
        if placed:
            self._say("read", task, header)
        else:
            self._say("unread", task, f"{filed.note.name} no longer has that page in it, "
                                      "so its text was not added")


# ----------------------------------------------------------------- the panel ----

def day_of(path: str) -> tuple[datetime.date, datetime.datetime | None, str]:
    """The day to file a photograph under, the camera's time, and where it came from."""
    taken = metadata.taken(path)
    if taken is not None:
        return taken.date(), taken, "the day the camera took it"
    try:
        stamp = datetime.datetime.fromtimestamp(os.path.getmtime(path))
    except OSError:
        return datetime.date.today(), None, "today"
    return stamp.date(), None, "the file's date — the camera did not say"


class Importer(Gtk.Box):
    """The panel: one page at a time, then the next."""

    __gtype_name__ = "TheEyeImporter"

    __gsignals__ = {
        "closed": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    SIDE = 300

    def __init__(self, scheme, config, toast, where=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                         margin_start=14, margin_end=14, margin_top=10,
                         margin_bottom=14)
        self.config = config
        self._toast = toast
        self._where = where
        self._pages: list[str] = []
        self._index = 0
        self._filed = 0
        self._found = paper.FALLBACK
        self._pixbuf = None
        self._generation = 0
        self._preview_timer = 0
        self._day_edited = False
        self.filer = Filer(self._on_report, where)

        self.counter = widgets.label("", "caption", xalign=0.0, ellipsize=3)
        header = widgets.row(
            widgets.column(widgets.label("Notebook pages", "heading", xalign=0.0),
                           self.counter, spacing=0, hexpand=True),
            widgets.icon_button("window-close-symbolic", "Close  (Esc)",
                                on_click=self.close),
            spacing=6)
        self.append(header)

        self.editor = CornerEditor(scheme)
        self.editor.connect("changed", lambda _e: self._schedule_preview())

        self.preview = Gtk.Picture(can_shrink=True, content_fit=Gtk.ContentFit.CONTAIN)
        self.preview.set_size_request(-1, 220)
        self.preview.set_vexpand(True)
        self.found = widgets.label("", "caption", xalign=0.0, wrap=True)

        self.subject = Gtk.DropDown.new_from_strings([filing.INBOX])
        self.day = Gtk.Entry(placeholder_text="YYYY-MM-DD")
        self.day.connect("changed", lambda _e: self._note_day_edit())
        self.day.connect("activate", lambda _e: self.file_page())
        self.day_note = widgets.label("", "caption", xalign=0.0, wrap=True)
        self._readers = [r for r in reading.READERS if reading.available(r)]
        self.reader = Gtk.DropDown.new_from_strings(
            [reading.NAMES[r].capitalize() if r != "none" else "Nobody — the image only"
             for r in self._readers])
        self.reader_note = widgets.label(self._reader_note(), "caption", xalign=0.0,
                                         wrap=True)

        reset = Gtk.Button(label="Reset corners")
        reset.add_css_class("pill")
        reset.connect("clicked", lambda _b: self._reset_corners())
        skip = Gtk.Button(label="Skip")
        skip.add_css_class("pill")
        skip.connect("clicked", lambda _b: self.next_page())
        self.file_button = Gtk.Button(label="File this page")
        self.file_button.add_css_class("pill")
        self.file_button.add_css_class("suggested-action")
        self.file_button.connect("clicked", lambda _b: self.file_page())
        self.progress = widgets.label("", "caption", xalign=0.0, wrap=True)

        self.side = widgets.column(
            self.preview, self.found,
            widgets.label("Where it goes", "caption", xalign=0.0, margin_top=6),
            self.subject,
            widgets.label("The day of the lesson", "caption", xalign=0.0),
            self.day, self.day_note,
            widgets.label("Read by", "caption", xalign=0.0),
            self.reader, self.reader_note,
            widgets.row(reset, skip, spacing=4, margin_top=4),
            self.file_button, self.progress,
            spacing=4)
        self.side.set_size_request(self.SIDE, -1)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                      propagate_natural_width=True)
        scroller.set_child(self.side)
        self.scroller = scroller
        self.body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12,
                            vexpand=True)
        self.body.append(self.editor)
        self.body.append(scroller)
        self.append(self.body)

    # -- outside -----------------------------------------------------------

    def set_scheme(self, scheme):
        self.editor.set_scheme(scheme)

    def set_narrow(self, narrow: bool):
        """Below the window's breakpoint the form goes under the photograph."""
        self.body.set_orientation(Gtk.Orientation.VERTICAL if narrow
                                  else Gtk.Orientation.HORIZONTAL)
        self.side.set_size_request(-1 if narrow else self.SIDE, -1)

    def start(self, paths: list[str]) -> bool:
        stills = [path for path in paths if os.path.isfile(path)]
        if not stills:
            return False
        self._pages = stills
        self._index = 0
        self._filed = 0
        names = filing.subjects(self._where)
        self.subject.set_model(Gtk.StringList.new(names))
        wanted = self.config["page_subject"]
        self.subject.set_selected(names.index(wanted) if wanted in names else 0)
        reader = self.config["page_reader"] or reading.default_reader()
        if reader in self._readers:
            self.reader.set_selected(self._readers.index(reader))
        self._show()
        return True

    def close(self):
        self._generation += 1
        self.emit("closed")

    # -- one page ----------------------------------------------------------

    def _show(self):
        path = self._pages[self._index]
        self._generation += 1
        generation = self._generation
        self.counter.set_text(f"Page {self._index + 1} of {len(self._pages)}   ·   "
                              f"{os.path.basename(path)}")
        day, _taken, why = day_of(path)
        self.day.set_text(f"{day:%Y-%m-%d}")
        self._day_edited = False
        self.day_note.set_text(f"From {why}.")
        self.editor.set_texture(None)
        self.preview.set_paintable(None)
        self.found.set_text("Looking for the page…")
        self.file_button.set_sensitive(False)
        self._pixbuf = None

        def work():
            still = photo.load(path, animate=False, longest=2048)
            source = paper.load(path, PREVIEW_SOURCE)
            corners, confidence = paper.find_corners(source)
            return still, source, corners, confidence

        def done(result):
            if generation != self._generation:
                return
            still, source, corners, confidence = result
            if not still.ok():
                self.found.set_text(still.error or "That photograph could not be read")
                return
            self._pixbuf = source
            self._found = corners
            self.editor.set_texture(still.texture)
            self.editor.set_corners(corners)
            self.found.set_text(
                "Found the page. Drag a corner if it is off."
                if confidence >= paper.CONFIDENT else
                "The page could not be told from what is around it — drag the "
                "corners onto its corners.")
            self.file_button.set_sensitive(True)
            self._schedule_preview(now=True)

        def failed(exc):
            if generation == self._generation:
                self.found.set_text(str(exc) or "That photograph could not be read")

        jobs.run(work, done, failed)

    def _reset_corners(self):
        self.editor.set_corners(self._found)
        self._schedule_preview(now=True)

    def _schedule_preview(self, now: bool = False):
        if self._preview_timer:
            GLib.source_remove(self._preview_timer)
            self._preview_timer = 0
        if now:
            self._make_preview()
        else:
            self._preview_timer = GLib.timeout_add(120, self._make_preview)

    def _make_preview(self):
        self._preview_timer = 0
        source, corners, generation = self._pixbuf, self.editor.corners(), self._generation
        if source is None:
            return GLib.SOURCE_REMOVE

        def work():
            return paper.straighten(source, corners, longest=PREVIEW_LONGEST)

        def done(page):
            if generation == self._generation and corners == self.editor.corners():
                self.preview.set_paintable(paper.texture_of(page))

        jobs.run(work, done, lambda exc: self.found.set_text(str(exc)))
        return GLib.SOURCE_REMOVE

    def _note_day_edit(self):
        self._day_edited = True

    def _reader_note(self) -> str:
        missing = [reading.NAMES[r] for r in ("claude", "tesseract")
                   if r not in self._readers]
        if not missing:
            return "Claude reads handwriting well; Tesseract reads print, locally."
        if "Claude" in missing and "Tesseract" in missing:
            return ("Neither Claude Code nor Tesseract is installed here, so pages "
                    "are filed as images only.")
        if "Claude" in missing:
            return "Claude Code is not installed here; Tesseract reads print well and handwriting poorly."
        return "Tesseract is not installed here; Claude reads the page."

    def file_page(self):
        if not self._pages or not self.file_button.get_sensitive():
            return
        day = reading.parse_day(self.day.get_text())
        if day is None:
            self._toast(f"“{self.day.get_text()}” is not a day — write it as 2026-10-27")
            return
        path = self._pages[self._index]
        names = self.subject.get_model()
        subject = names.get_string(self.subject.get_selected()) or filing.INBOX
        reader = (self._readers[self.reader.get_selected()]
                  if self._readers else "none")
        _day, taken, _why = day_of(path)
        self.config["page_subject"] = subject
        self.config["page_reader"] = reader
        self.filer.add(Task(path, self.editor.corners(), subject, day, reader, taken))
        self._filed += 1
        self._refresh_progress()
        self.next_page()

    def next_page(self):
        if self._index + 1 < len(self._pages):
            self._index += 1
            self._show()
            return
        filed = self._filed
        self.close()
        if filed:
            self._toast(f"{filed} page{'s' if filed > 1 else ''} filed"
                        + ("; the text is still being read" if self.filer.busy() else ""))

    # -- reports from the worker -------------------------------------------

    def _refresh_progress(self):
        waiting = self.filer.waiting
        self.progress.set_text(f"{waiting} page{'s' if waiting != 1 else ''} being "
                               "filed or read" if waiting else "")

    def _on_report(self, event, task, detail):
        self._refresh_progress()
        name = os.path.basename(task.path)
        if event == "failed":
            self._toast(f"{name}: {detail}", 6)
        elif event == "unread":
            self._toast(f"{name} was filed without its text. {detail}", 7)
        elif event == "read" and detail is not None and detail.day \
                and task.subject != filing.INBOX and detail.day != task.day:
            self._toast(f"{name} says {detail.day:%Y-%m-%d} at the top; it was filed "
                        f"under {task.day:%Y-%m-%d}", 7)
        elif event == "settled" and not self.filer.busy() and not self.get_mapped():
            self._toast("Every page is filed and read")
