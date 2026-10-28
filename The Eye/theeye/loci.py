"""Use as a palace locus: a photograph of a real place, put under its locus.

A memory palace is built from places that are really walked (HANDBOOK §3.2),
and step three is to photograph each locus. This dialog is where those
photographs go: pick the palace — or name a new one, which is made with
`kind: palace` in its front matter — pick the locus or name a new one at
the end of the walk, and the photograph is copied beside the palace and
linked under that locus as `![](…)` (SYSTEM.md §8). Mnemosyne's Walk then
shows it, and The Eye opens the palace's folder as the walk in order,
because the copies are numbered by locus.
"""

from __future__ import annotations

import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk  # noqa: E402

from . import filing, jobs, widgets  # noqa: E402

NEW_PALACE = "A new palace…"
NEW_LOCUS = "A new locus…"


class LocusDialog(Adw.Dialog):
    """The three questions — which palace, which locus, what is there — and Put it there."""

    __gtype_name__ = "TheEyeLocusDialog"

    def __init__(self, photo: str, toast, where=None):
        super().__init__(title="Use as a Palace Locus", content_width=440)
        self.photo = photo
        self._toast = toast
        self._where = where
        self.palaces = filing.palaces(where)
        self.loci = []

        page = widgets.column(spacing=0)
        header = Adw.HeaderBar()
        header.add_css_class("glass-flat")
        page.append(header)
        body = widgets.column(spacing=6, margin_start=22, margin_end=22,
                              margin_top=6, margin_bottom=22)
        page.append(body)
        body.append(widgets.label(
            f"{os.path.basename(photo)} is copied beside the palace, in a folder "
            "of its own, and shown under the locus. The photograph itself is left "
            "where it is.", "caption", xalign=0.0, wrap=True))

        body.append(widgets.label("Palace", "caption", xalign=0.0, margin_top=8))
        names = [palace.title for palace in self.palaces] + [NEW_PALACE]
        self.palace = Gtk.DropDown.new_from_strings(names)
        self.palace.connect("notify::selected", lambda *_: self._palace_changed())
        body.append(self.palace)
        self.palace_name = Gtk.Entry(placeholder_text="The way to school")
        body.append(self.palace_name)

        body.append(widgets.label("Locus", "caption", xalign=0.0, margin_top=8))
        self.locus = Gtk.DropDown.new_from_strings([NEW_LOCUS])
        self.locus.connect("notify::selected", lambda *_: self._locus_changed())
        body.append(self.locus)
        self.locus_name = Gtk.Entry(placeholder_text="The front door")
        self.locus_name.connect("activate", lambda _e: self.put())
        body.append(self.locus_name)
        self.item = Gtk.Entry(placeholder_text="What is placed there — can wait")
        self.item.connect("activate", lambda _e: self.put())
        body.append(self.item)
        self.note = widgets.label("", "caption", xalign=0.0, wrap=True)
        body.append(self.note)

        self.button = Gtk.Button(label="Put It There", margin_top=10)
        self.button.add_css_class("pill")
        self.button.add_css_class("suggested-action")
        self.button.connect("clicked", lambda _b: self.put())
        body.append(self.button)
        self.set_child(page)
        self._palace_changed()

    # -- the choices --------------------------------------------------------

    def _chosen_palace(self):
        index = self.palace.get_selected()
        return self.palaces[index] if index < len(self.palaces) else None

    def _palace_changed(self):
        palace = self._chosen_palace()
        self.palace_name.set_visible(palace is None)
        self.loci = filing.loci(palace.path) if palace is not None else []
        labels = [f"{locus.number}. {locus.name}" for locus in self.loci] + [NEW_LOCUS]
        self.locus.set_model(Gtk.StringList.new(labels))
        self.locus.set_selected(0)
        self._locus_changed()

    def _chosen_locus(self):
        index = self.locus.get_selected()
        return self.loci[index] if index < len(self.loci) else None

    def _locus_changed(self):
        locus = self._chosen_locus()
        self.locus_name.set_visible(locus is None)
        self.item.set_visible(locus is None)
        if locus is not None and locus.item:
            self.note.set_text(f"There already: {locus.item}")
        elif locus is not None:
            self.note.set_text("Nothing is placed there yet.")
        else:
            self.note.set_text("A new locus goes at the end of the walk.")

    # -- doing it -----------------------------------------------------------

    def put(self):
        palace = self._chosen_palace()
        locus = self._chosen_locus()
        title = self.palace_name.get_text().strip()
        name = locus.name if locus is not None else self.locus_name.get_text().strip()
        if palace is None and not title:
            self.note.set_text("Name the new palace first.")
            return
        if not name:
            self.note.set_text("Name the locus first.")
            return
        item = "" if locus is not None else self.item.get_text().strip()
        photo, where = self.photo, self._where
        self.button.set_sensitive(False)

        def work():
            path = palace.path if palace is not None else filing.create_palace(title, where)
            copy = filing.link_locus(path, name, photo, item)
            return path, copy

        def done(result):
            path, _copy = result
            title = filing.brain.title_of(path.read_text(encoding="utf-8"), path)
            self._toast(f"Under “{name}” in {title}")
            if self.get_parent() is not None:       # presented, so there to close
                self.close()

        def failed(exc):
            self.button.set_sensitive(True)
            self.note.set_text(str(exc) or "The photograph could not be put there")

        jobs.run(work, done, failed)
