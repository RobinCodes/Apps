"""The editor: the grade, with handles on it.

Every control here is one entry in `CONTROLS` below, and the panel is built
from that table rather than by hand. The table is the only place a slider's
range, its step, its units and its explanation live, so adding a control to
`grade.py` and adding it here is one line each and they cannot drift apart.

Three decisions worth stating.

*The sliders are centred on neutral.* Every one of them reads zero when it is
doing nothing, and the mark on the trough is at zero rather than at the far
left. That is what makes a panel of twenty sliders readable at a glance:
anything away from the middle is something you did.

*The looks come first.* A slider is only useful to somebody who already knows
what they want to change. Most of the time what somebody wants is for the
picture to look better, and that is a look — so the looks are at the top, and
the sliders below are for taking one too far or pulling it back.

*Nothing is applied to the file.* The grade lives beside the picture in a
sidecar, or in the app's own state when the folder is not writable. Exporting
is an explicit act with a file chooser, and the original is never touched.
The panel says so, once, at the bottom, because an editor that might be
modifying your photographs is one you cannot relax in.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, GObject, Gtk  # noqa: E402

from . import grade as grading, looks as looking, theme as theming, widgets  # noqa: E402
from .grade import Grade  # noqa: E402


class Control:
    """One slider: what it drives, how far it goes, and what it is for."""

    __slots__ = ("key", "name", "low", "high", "step", "unit", "blurb", "digits")

    def __init__(self, key, name, low, high, step=0.01, unit="", blurb="",
                 digits=2):
        self.key = key
        self.name = name
        self.low = low
        self.high = high
        self.step = step
        self.unit = unit
        self.blurb = blurb
        self.digits = digits

    def neutral(self):
        return grading.DEFAULTS[self.key]

    def format(self, value) -> str:
        if abs(value - self.neutral()) < self.step / 2:
            return "—"
        if self.digits == 0:
            return f"{value:+.0f}{self.unit}"
        text = f"{value:+.{self.digits}f}".rstrip("0").rstrip(".")
        return f"{text}{self.unit}"


# The panel, group by group. The order within a group is the order a darkroom
# would work in, which is also the order `grade.matrix()` composes them.
GROUPS: list[tuple[str, str, list[Control]]] = [
    ("Light", "How bright, and how much separates bright from dark.", [
        Control("exposure", "Exposure", -3.0, 3.0, 0.05, " EV",
                "The whole picture brighter or darker, in stops, as a camera "
                "means it."),
        Control("contrast", "Contrast", -1.0, 1.0, 0.01, "",
                "Stretched about middle grey. Pushed far enough, the ends "
                "clip — which is sometimes what you want."),
        Control("filmic", "Filmic", -1.0, 1.0, 0.01, "",
                "An S-curve that cannot clip: contrast through the middle "
                "with black and white left where they are."),
        Control("midtones", "Midtones", -1.0, 1.0, 0.01, "",
                "A gamma curve. Lifts or lowers everything between black and "
                "white without moving either."),
        Control("shadows", "Shadows", -1.0, 1.0, 0.01, "",
                "Opens or closes the dark end only, weighted by how dark each "
                "part already is."),
        Control("highlights", "Highlights", -1.0, 1.0, 0.01, "",
                "The same for the bright end. Pull it down to recover a sky."),
        Control("blacks", "Black point", -1.0, 1.0, 0.01, "",
                "Where black sits. Lifted is the faded-print look."),
        Control("whites", "White point", -1.0, 1.0, 0.01, "",
                "Where white sits. Raised brings the highlights to clipping."),
    ]),
    ("Colour", "Which colours, and how much of them.", [
        Control("temperature", "Temperature", -1.0, 1.0, 0.01, "",
                "Cool to warm, trading blue against red, keeping the "
                "brightness where it was."),
        Control("tint", "Tint", -1.0, 1.0, 0.01, "",
                "Green to magenta — the other axis, and the one that fixes "
                "fluorescent light."),
        Control("saturation", "Saturation", -1.0, 1.0, 0.01, "",
                "All the colour at once. Greys stay exactly grey at any "
                "setting."),
        Control("hue", "Hue shift", -180.0, 180.0, 1.0, "°",
                "Rotates every hue. A few degrees fixes a cast; ninety is a "
                "different planet.", 0),
    ]),
    ("Texture", "Detail, and the way light behaves around it.", [
        Control("clarity", "Clarity", -1.0, 1.0, 0.01, "",
                "Local contrast at a wide radius. Backwards, it is a "
                "soft-focus filter."),
        Control("sharpen", "Sharpen", 0.0, 1.0, 0.01, "",
                "The same at a fine radius, for detail rather than for "
                "presence."),
        Control("bloom", "Bloom", 0.0, 1.0, 0.01, "",
                "Light spilling out of the highlights, the way a bright lamp "
                "does through a lens."),
        Control("bloom_threshold", "Bloom threshold", 0.0, 1.0, 0.01, "",
                "How bright a part of the picture has to be before it blooms. "
                "Lower reaches further down into the midtones."),
        Control("halation", "Halation", 0.0, 1.0, 0.01, "",
                "Film's red fringe around a highlight — light scattering back "
                "off the base of the film."),
        Control("glow", "Glow", 0.0, 1.0, 0.01, "",
                "Diffusion over the whole frame, highlights and shadows "
                "alike."),
        Control("grain", "Grain", 0.0, 1.0, 0.01, "",
                "Film grain. It moves on a video and holds still on a "
                "photograph, as real grain does."),
        Control("grain_size", "Grain size", 0.5, 3.0, 0.05, "x",
                "How coarse the grain is. Larger is older film and smaller "
                "is a faster one."),
    ]),
    ("Frame", "What happens at the edges.", [
        Control("vignette", "Vignette", -1.0, 1.0, 0.01, "",
                "Corners burned down, or — negative — dodged up."),
    ]),
]

# The colour controls that are a colour and a strength rather than a number.
TINTS = (
    ("shadow_tint", "shadow_tint_amount", "Shadows",
     "A colour into the dark end. Teal here with amber above is most of what "
     "a graded film looks like."),
    ("highlight_tint", "highlight_tint_amount", "Highlights",
     "And one into the bright end."),
    ("wash", "wash_amount", "Wash",
     "A colour laid over everything, in whichever mode is chosen."),
)

# A short palette for the tint pickers. A full colour wheel is not useful here
# — split toning lives at low saturation, and these are the hues that work.
SWATCHES = ("#1E4C5A", "#2A5A8C", "#4A3A7A", "#6E2F4A", "#8C4A1C", "#A8761E",
            "#2F6B4A", "#5A6B2F", "#8C8C8C", "#D8C4A8", "#FFB66B", "#FF4FD8")


class SliderRow(Gtk.Box):
    """One control: its name, its value, and the slider itself.

    The name is a button. Clicking it puts the control back to neutral, which
    is the thing anybody wants most often from a panel like this and is
    otherwise a drag back to a value you cannot hit exactly.
    """

    __gtype_name__ = "TheEyeSliderRow"

    __gsignals__ = {
        "edited": (GObject.SignalFlags.RUN_FIRST, None, (str, float)),
    }

    def __init__(self, control: Control, value: float):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.control = control
        self._quiet = False

        self.name = Gtk.Button(has_frame=False)
        self.name.add_css_class("pill")
        self.name.set_tooltip_text(f"{control.blurb}\n\nClick to reset.")
        inner = widgets.label(control.name, xalign=0.0, hexpand=True)
        self.readout = widgets.label(control.format(value), "numeric", "dim",
                                     xalign=1.0)
        self.name.set_child(widgets.row(inner, self.readout, spacing=6))
        self.name.connect("clicked", lambda _b: self.reset())
        self.append(self.name)

        self.scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, control.low, control.high, control.step)
        self.scale.add_css_class("editor")
        self.scale.set_draw_value(False)
        self.scale.set_value(value)
        # One mark, at the neutral value, so that "doing nothing" is a place
        # on the trough rather than a number to remember.
        self.scale.add_mark(float(control.neutral()), Gtk.PositionType.BOTTOM, None)
        self.scale.connect("value-changed", self._on_changed)
        self.append(self.scale)

    def _on_changed(self, scale):
        value = scale.get_value()
        self.readout.set_text(self.control.format(value))
        self.readout.remove_css_class("dim")
        self.readout.remove_css_class("statusmark")
        if abs(value - self.control.neutral()) < self.control.step / 2:
            self.readout.add_css_class("dim")
        else:
            self.readout.add_css_class("statusmark")
        if not self._quiet:
            self.emit("edited", self.control.key, value)

    def set_value(self, value):
        """Put a value in without reporting it back — for loading a look."""
        self._quiet = True
        try:
            self.scale.set_value(value)
        finally:
            self._quiet = False

    def reset(self):
        self.scale.set_value(float(self.control.neutral()))


class TintRow(Gtk.Box):
    """A colour and how much of it — split toning, and the wash."""

    __gtype_name__ = "TheEyeTintRow"

    __gsignals__ = {
        "edited": (GObject.SignalFlags.RUN_FIRST, None, (str, str)),
        "amount-edited": (GObject.SignalFlags.RUN_FIRST, None, (str, float)),
    }

    def __init__(self, colour_key, amount_key, name, blurb, colour, amount,
                 modes=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.colour_key = colour_key
        self.amount_key = amount_key
        self._quiet = False

        title = widgets.label(name, xalign=0.0, hexpand=True)
        title.set_tooltip_text(blurb)
        self.swatch = Gtk.MenuButton(tooltip_text=f"{name} colour")
        self.swatch.add_css_class("circular")
        self._dot = Gtk.DrawingArea()
        self._dot.set_size_request(18, 18)
        self._dot.set_draw_func(self._draw_dot)
        self.swatch.set_child(self._dot)
        self.swatch.set_popover(self._palette())
        self.append(widgets.row(title, self.swatch, spacing=6))

        self.scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL,
                                              0.0, 1.0, 0.01)
        self.scale.add_css_class("editor")
        self.scale.set_draw_value(False)
        self.scale.add_mark(0.0, Gtk.PositionType.BOTTOM, None)
        self.scale.connect("value-changed", self._on_amount)
        self.append(self.scale)

        self.modes = None
        if modes:
            self.modes = Gtk.DropDown.new_from_strings(
                [m.replace("-", " ").title() for m in modes])
            self.modes.set_tooltip_text("How the colour is laid over the picture")
            self.append(self.modes)

        self._colour = colour or SWATCHES[0]
        self.set_values(colour, amount)

    def _palette(self):
        grid = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE,
                           min_children_per_line=6, max_children_per_line=6,
                           margin_top=8, margin_bottom=8,
                           margin_start=8, margin_end=8,
                           row_spacing=6, column_spacing=6)
        for colour in SWATCHES:
            button = Gtk.Button(tooltip_text=colour)
            button.add_css_class("circular")
            area = Gtk.DrawingArea()
            area.set_size_request(22, 22)
            area.set_draw_func(self._draw_swatch, colour)
            button.set_child(area)
            button.connect("clicked", self._pick, colour)
            grid.append(button)
        return Gtk.Popover(child=grid)

    def _pick(self, _button, colour):
        self._colour = colour
        self._dot.queue_draw()
        self.swatch.get_popover().popdown()
        if self.scale.get_value() <= 0.001:
            # Choosing a colour when the strength is zero means "use this",
            # so it is given a strength worth seeing rather than silently
            # doing nothing.
            self.scale.set_value(0.35)
        self.emit("edited", self.colour_key, colour)

    def _draw_dot(self, _area, cr, width, height, *_a):
        self._draw_swatch(_area, cr, width, height, self._colour)

    def _draw_swatch(self, _area, cr, width, height, colour):
        r, g, b = grading.rgb(colour)
        radius = min(width, height) / 2 - 1
        cr.arc(width / 2, height / 2, radius, 0, 6.2832)
        cr.set_source_rgb(r, g, b)
        cr.fill_preserve()
        cr.set_source_rgba(1, 1, 1, 0.35)
        cr.set_line_width(1)
        cr.stroke()

    def _on_amount(self, scale):
        if not self._quiet:
            self.emit("amount-edited", self.amount_key, scale.get_value())

    def set_values(self, colour, amount, mode=None):
        self._quiet = True
        try:
            if colour:
                self._colour = colour
            self._dot.queue_draw()
            self.scale.set_value(amount or 0.0)
            if self.modes is not None and mode:
                strings = self.modes.get_model()
                target = mode.replace("-", " ").title()
                for index in range(strings.get_n_items()):
                    if strings.get_string(index) == target:
                        self.modes.set_selected(index)
                        break
        finally:
            self._quiet = False

    def colour(self) -> str:
        return self._colour


class Editor(Gtk.Box):
    """The whole panel."""

    __gtype_name__ = "TheEyeEditor"

    __gsignals__ = {
        "changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "export-wanted": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "compare-toggled": (GObject.SignalFlags.RUN_FIRST, None, (bool,)),
        "closed": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    WIDTH = 304

    def __init__(self, scheme=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._scheme = scheme or theming.get(theming.DEFAULT)
        self._grade = Grade()
        self._rows: dict[str, SliderRow] = {}
        self._tints: dict[str, TintRow] = {}
        self._look_buttons: dict[str, Gtk.Button] = {}
        self._quiet = False
        self.set_size_request(self.WIDTH, -1)

        self.append(self._header())
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                      vexpand=True)
        scroller.set_child(self._body())
        self.append(scroller)
        self.append(self._footer())

    # -- the pieces --------------------------------------------------------

    def _header(self):
        title = widgets.label("Edit", "heading", xalign=0.0, hexpand=True)
        self.compare = widgets.toggle_button(
            "view-dual-symbolic", "Before and after  (\\)", classes=("circular",))
        self.compare.connect("toggled",
                             lambda b: self.emit("compare-toggled", b.get_active()))
        reset = widgets.icon_button("edit-undo-symbolic", "Reset everything",
                                    on_click=self.reset_all)
        close = widgets.icon_button("window-close-symbolic", "Close the editor",
                                    on_click=lambda: self.emit("closed"))
        return widgets.row(title, self.compare, reset, close, spacing=2,
                           margin_start=14, margin_end=10,
                           margin_top=10, margin_bottom=6)

    def _body(self):
        box = widgets.column(spacing=4, margin_start=14, margin_end=14,
                             margin_bottom=14)
        box.append(self._looks())
        box.append(self._geometry())
        for name, blurb, controls in GROUPS:
            box.append(self._group_header(name, blurb))
            for control in controls:
                row = SliderRow(control, self._grade[control.key])
                row.connect("edited", self._on_slider)
                self._rows[control.key] = row
                box.append(row)
            if name == "Colour":
                box.append(self._group_header(
                    "Toning", "A colour into one end of the range, or over "
                              "all of it."))
                for colour_key, amount_key, label, blurb in TINTS:
                    row = TintRow(colour_key, amount_key, label, blurb,
                                  self._grade[colour_key],
                                  self._grade[amount_key],
                                  grading.WASH_MODES if colour_key == "wash"
                                  else None)
                    row.connect("edited", self._on_colour)
                    row.connect("amount-edited", self._on_slider)
                    if row.modes is not None:
                        row.modes.connect("notify::selected", self._on_wash_mode)
                    self._tints[colour_key] = row
                    box.append(row)
                box.append(self._monochrome())
        return box

    def _group_header(self, name, blurb):
        title = widgets.label(name, "title", xalign=0.0)
        note = widgets.label(blurb, "caption", xalign=0.0, wrap=True)
        return widgets.column(title, note, spacing=1, margin_top=14,
                              margin_bottom=2)

    def _monochrome(self):
        self.mono = Gtk.CheckButton(label="Monochrome")
        self.mono.set_tooltip_text(
            "Desaturate on the weights the eye uses. With a toning colour "
            "chosen below, the picture's brightness drives a ramp to that "
            "colour instead — which is what a toned silver print is.")
        self.mono.connect("toggled", self._on_mono)
        self.duotone = TintRow("duotone", "", "Tone towards",
                               "The colour a monochrome ramp runs to.",
                               self._grade["duotone"], 0.0)
        self.duotone.scale.set_visible(False)
        self.duotone.connect("edited", self._on_colour)
        self.duotone.set_sensitive(False)
        return widgets.column(self.mono, self.duotone, spacing=4, margin_top=8)

    def _geometry(self):
        left = widgets.icon_button("object-rotate-left-symbolic",
                                   "Rotate left  (Shift+R)", "win.rotate-left")
        right = widgets.icon_button("object-rotate-right-symbolic",
                                    "Rotate right  (R)", "win.rotate-right")
        flip_h = widgets.icon_button("object-flip-horizontal-symbolic",
                                     "Flip across  (H)", "win.flip-h")
        flip_v = widgets.icon_button("object-flip-vertical-symbolic",
                                     "Flip over  (V)", "win.flip-v")
        row = widgets.row(left, right, flip_h, flip_v, spacing=2,
                          halign=Gtk.Align.CENTER, margin_top=8)
        return row

    def _looks(self):
        box = widgets.column(spacing=6, margin_top=4)
        for category in looking.CATEGORIES:
            box.append(widgets.label(category, "caption", xalign=0.0,
                                     margin_top=6))
            flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE,
                               min_children_per_line=2,
                               max_children_per_line=3,
                               row_spacing=5, column_spacing=5,
                               homogeneous=True)
            for look in looking.in_category(category):
                button = Gtk.Button(label=look["name"])
                button.add_css_class("chip")
                button.set_tooltip_text(look["blurb"])
                button.connect("clicked", self._on_look, look["key"])
                self._look_buttons[look["key"]] = button
                flow.append(button)
            box.append(flow)
        return box

    def _footer(self):
        export = Gtk.Button(label="Export…")
        export.add_css_class("suggested-action")
        export.add_css_class("pill")
        export.set_tooltip_text("Write a copy with this grade baked in  (Ctrl+E)")
        export.connect("clicked", lambda _b: self.emit("export-wanted"))
        copy = widgets.icon_button("edit-copy-symbolic", "Copy this grade",
                                   "win.copy-grade")
        paste = widgets.icon_button("edit-paste-symbolic", "Paste a grade",
                                    "win.paste-grade")
        note = widgets.label(
            "The original file is never changed. A grade is remembered beside "
            "the picture; Export writes a new copy.",
            "caption", xalign=0.0, wrap=True)
        return widgets.column(
            widgets.row(export, copy, paste, spacing=4), note, spacing=6,
            margin_start=14, margin_end=14, margin_top=6, margin_bottom=12)

    # -- talking to the grade ---------------------------------------------

    def set_grade(self, settings: Grade):
        """Show a grade — when the picture changes, or a look is loaded."""
        self._grade = settings
        self._quiet = True
        try:
            for key, row in self._rows.items():
                row.set_value(settings[key])
            for key, row in self._tints.items():
                amount_key = row.amount_key
                row.set_values(settings[key],
                               settings[amount_key] if amount_key else 0.0,
                               settings["wash_mode"] if key == "wash" else None)
            self.mono.set_active(bool(settings["monochrome"]))
            self.duotone.set_values(settings["duotone"], 0.0)
            self.duotone.set_sensitive(bool(settings["monochrome"]))
        finally:
            self._quiet = False
        self._mark_look()

    def grade(self) -> Grade:
        return self._grade

    def _on_slider(self, _row, key, value):
        if self._quiet:
            return
        self._grade[key] = value
        self._mark_look()
        self.emit("changed")

    def _on_colour(self, _row, key, colour):
        if self._quiet:
            return
        self._grade[key] = colour
        self._mark_look()
        self.emit("changed")

    def _on_wash_mode(self, dropdown, _param):
        if self._quiet:
            return
        index = dropdown.get_selected()
        if 0 <= index < len(grading.WASH_MODES):
            self._grade["wash_mode"] = grading.WASH_MODES[index]
            self.emit("changed")

    def _on_mono(self, button):
        if self._quiet:
            return
        self._grade["monochrome"] = button.get_active()
        self.duotone.set_sensitive(button.get_active())
        self._mark_look()
        self.emit("changed")

    def _on_look(self, _button, key):
        self.set_grade(looking.grade_for(key))
        self.emit("changed")

    def reset_all(self):
        kept = {k: self._grade[k] for k in ("rotation", "flip_h", "flip_v")}
        fresh = Grade()
        fresh.update(kept)
        self.set_grade(fresh)
        self.emit("changed")

    def _mark_look(self):
        """Light up the look that is selected, if the sliders still match it."""
        current = looking.matching(self._grade)
        for key, button in self._look_buttons.items():
            if key == current:
                button.add_css_class("badge-on")
            else:
                button.remove_css_class("badge-on")

    def set_compare(self, on: bool):
        if self.compare.get_active() != on:
            self.compare.set_active(on)
