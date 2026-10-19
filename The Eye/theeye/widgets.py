"""Shared pieces: the stylesheet, and the small widgets built on the glass.

The stylesheet is generated rather than written, because the colours in it
come from whichever theme is loaded and a theme is a Python object. What it
mostly does is get out of the way: `glass.py` draws the panels, so almost
every widget here wants a transparent background and nothing else, and the
rules below are largely a long list of things not to draw.

The widgets are the handful that could not be a `Gtk.Button` with a class on
it. Each one is here for a reason given in its own docstring; anything that
libadwaita already does well is used as it comes.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, GLib, GObject, Gtk  # noqa: E402

from . import glass, housestyle, theme as theming  # noqa: E402

# Which `.r*` class matches a slab's radius, so that the blurred region is
# the same shape as the glass drawn over it.
RADIUS_CLASSES = {0: "r0", 10: "r10", 14: "r14", 16: "r16", 18: "r18", 20: "r20"}

DEFAULT_RADII = {"bar": 18, "panel": 16, "strip": 0, "card": 20, "well": 10}


def _radius_class(radius, kind: str) -> str:
    """The CSS class for a radius, picking the nearest one that exists.

    A four-cornered radius is matched to whichever edge it is flat against,
    which covers the cases that actually arise — a bar along the bottom of the
    window, a panel against one side — and falls back to the all-round class
    for anything else.
    """
    if radius is None:
        radius = DEFAULT_RADII.get(kind, 14)
    if isinstance(radius, (tuple, list)) and len(radius) == 4:
        top_left, top_right, bottom_right, bottom_left = radius
        if top_left and top_right and not bottom_right and not bottom_left:
            return "r-top"
        if bottom_right and bottom_left and not top_left and not top_right:
            return "r-bottom"
        if top_right and bottom_right and not top_left and not bottom_left:
            return "r-start"
        if top_left and bottom_left and not top_right and not bottom_right:
            return "r-end"
        radius = max(radius)
    nearest = min(RADIUS_CLASSES, key=lambda r: abs(r - float(radius)))
    return RADIUS_CLASSES[nearest]


_provider: Gtk.CssProvider | None = None


def install_css(scheme: theming.Theme, translucency: float = 0.9):
    """Put the stylesheet for `scheme` on the display, replacing any before it.

    One provider, swapped in place, so that changing theme is immediate and
    does not leave the previous theme's rules underneath fighting for
    specificity.
    """
    global _provider
    display = Gdk.Display.get_default()
    if display is None:
        return
    if _provider is None:
        _provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(
            display, _provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _provider.load_from_string(stylesheet(scheme, translucency))

    # libadwaita still decides a few things for itself — the colour of a
    # spinner, the shade of a popover's shadow — from whether it thinks the
    # system is dark. Telling it keeps those in step with our own theme.
    manager = Adw.StyleManager.get_default()
    manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK if scheme.dark
                             else Adw.ColorScheme.FORCE_LIGHT)


def stylesheet(scheme: theming.Theme, translucency: float = 0.9) -> str:
    """The CSS for one theme.

    The house palette comes first: the rules below cover every widget this
    app builds on purpose, and restating libadwaita's named colours in the
    theme's own is what keeps the ones it builds for itself — a spin button
    inside a preferences row, a tooltip, a colour chooser — from arriving in
    Adwaita grey over a photograph.
    """
    fg = scheme.fg
    dim = scheme.dim
    accent = scheme.accent
    on_accent = "#0A0C10" if theming.luminance(accent) > 0.45 else "#FFFFFF"
    hover = "rgba(255,255,255,0.09)" if scheme.dark else "rgba(0,0,0,0.06)"
    pressed = "rgba(255,255,255,0.15)" if scheme.dark else "rgba(0,0,0,0.11)"
    sunk = "rgba(0,0,0,0.30)" if scheme.dark else "rgba(0,0,0,0.12)"
    card = glass.shade(scheme.glass, 0.06 if scheme.dark else -0.02)
    blur = glass.blur_css()
    return housestyle.palette(scheme, translucency) + f"""
/* The window itself is transparent: every surface is drawn by glass.py, and
   a background here would sit on top of the ambient layer. */
window.the-eye, window.the-eye > * {{
  background: transparent;
  color: {fg};
}}
window.the-eye {{ font-feature-settings: "tnum" 1; }}

/* Panels draw themselves. Anything libadwaita would paint underneath has to
   be turned off explicitly, or there are two backgrounds and the glass is
   the one underneath. */
.glass-flat, .glass-flat > *, headerbar.glass-flat,
.toolbar.glass-flat, box.glass-flat {{ background: none; box-shadow: none; }}

label {{ color: {fg}; }}
label.dim, .dim {{ color: {dim}; }}
label.numeric {{ font-feature-settings: "tnum" 1; }}
label.title {{ font-weight: 600; }}
label.heading {{ font-weight: 600; font-size: 1.05em; }}
label.caption, .caption {{ font-size: 0.84em; color: {dim}; }}
label.mono, .mono {{ font-family: monospace; font-size: 0.92em; }}

/* Buttons are flat by default here: a bar of raised buttons on a sheet of
   glass looks like a bar of buttons on a sheet of glass. */
button {{
  background: none; border: none; box-shadow: none;
  color: {fg}; border-radius: 9px; padding: 6px 10px;
  transition: background 120ms ease, color 120ms ease;
}}
button:hover {{ background: {hover}; }}
button:active, button:checked {{ background: {pressed}; }}
button:disabled {{ color: {dim}; opacity: 0.55; }}
button.circular {{ border-radius: 999px; padding: 7px; min-width: 20px; min-height: 20px; }}
button.transport {{ padding: 9px; border-radius: 999px; }}
button.transport.primary {{
  background: {accent}; color: {on_accent};
}}
button.transport.primary:hover {{ background: {glass.shade(accent, 0.12)}; }}
button.transport.primary:active {{ background: {glass.shade(accent, -0.08)}; }}
button.suggested-action {{ background: {accent}; color: {on_accent}; }}
button.destructive-action {{ background: #C0392B; color: #FFFFFF; }}
button.pill {{ border-radius: 999px; padding: 5px 14px; }}
button.chip {{
  border-radius: 999px; padding: 4px 12px; font-size: 0.88em;
  background: {hover}; color: {fg};
}}
button.chip:checked {{ background: {accent}; color: {on_accent}; }}
/* Element-and-class, not bare class: `button.chip` already sets a background
   and would win on specificity against a lone `.badge-on`, so the selected
   look would never light up. */
button.badge-on, button.chip.badge-on, .pill.badge-on {{
  background: {accent}; color: {on_accent};
}}

/* The one place a focus ring is wanted is where the keyboard put it. */
:focus-visible {{ outline: 2px solid {accent}; outline-offset: 2px; }}

/* Sliders. The trough is drawn flat here and the glass well is drawn behind
   it by the panel, so the two do not double up. */
scale {{ min-height: 20px; }}
scale trough {{
  background: {sunk}; border-radius: 999px; min-height: 5px; border: none;
}}
scale highlight {{ background: {accent}; border-radius: 999px; min-height: 5px; }}
scale slider {{
  background: #FFFFFF; border-radius: 999px;
  min-width: 13px; min-height: 13px; margin: -5px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.45);
  transition: transform 110ms ease;
}}
scale:hover slider {{ box-shadow: 0 1px 5px rgba(0,0,0,0.55); }}
scale.seek trough {{ min-height: 7px; }}
scale.seek highlight {{ min-height: 7px; }}
scale.editor slider {{ min-width: 15px; min-height: 15px; margin: -6px; }}
scale marks, scale marks indicator {{ color: {dim}; }}

/* Entries and spin buttons sit in a well drawn by the panel, so they are
   transparent with just enough of a hairline to find their edges. */
entry, spinbutton {{
  background: {sunk}; color: {fg}; border: none; border-radius: 9px;
  padding: 6px 10px; box-shadow: none;
}}
entry:focus-within, spinbutton:focus-within {{ outline: 2px solid {accent}; outline-offset: -1px; }}
entry > text {{ color: {fg}; }}
entry placeholder, entry > text > placeholder {{ color: {dim}; opacity: 1; }}

switch {{ background: {sunk}; border: none; box-shadow: none; }}
switch:checked {{ background: {accent}; }}
switch > slider {{ background: #FFFFFF; box-shadow: 0 1px 2px rgba(0,0,0,0.4); }}

checkbutton check, checkbutton radio {{ background: {sunk}; border: none; }}
checkbutton check:checked, checkbutton radio:checked {{
  background: {accent}; color: {on_accent};
}}

/* Lists: the information panel and the track menus. */
listview, listview > row, list, row {{ background: transparent; }}
listview > row:selected, row:selected {{
  background: {accent}; color: {on_accent}; border-radius: 10px;
}}
listview > row:hover, row.activatable:hover {{ background: {hover}; border-radius: 10px; }}
row.info-row {{ padding: 5px 2px; }}

/* Popovers and dialogs are the one place a solid surface is right: they
   float clear of everything and have to be readable over any picture. */
popover > contents, popover > arrow {{
  background: {card}; color: {fg};
  border: 1px solid rgba(255,255,255,{0.10 if scheme.dark else 0.55});
  border-radius: 14px;
/* Modest on purpose: GSK blurs an offscreen to draw a soft shadow and
   the cost goes with the radius, not with the size of the thing casting
   it. A popover at 34px measured nearly three times the cost of one at
   12px — see the note in housestyle.chrome(). */
  box-shadow: 0 4px 12px rgba(0,0,0,{0.40 if scheme.dark else 0.16});
}}
popover.menu button {{ border-radius: 8px; }}
.card, dialog, .dialog-page, window.dialog {{
  background: {card}; color: {fg}; border-radius: 16px;
}}
preferencespage, preferencesgroup, .preferences {{ background: transparent; }}
preferencesgroup > box > label {{ color: {fg}; }}

/* Toasts, which in this app appear over a picture rather than over a page. */
toast > widget, .toast {{
  background: {card}; color: {fg}; border-radius: 999px;
  border: 1px solid rgba(255,255,255,{0.12 if scheme.dark else 0.5});
  box-shadow: 0 3px 10px rgba(0,0,0,0.38);
}}

tooltip, tooltip.background {{
  background: {card}; color: {fg}; border-radius: 10px;
  border: 1px solid rgba(255,255,255,{0.10 if scheme.dark else 0.5});
}}

scrollbar {{ background: transparent; border: none; }}
scrollbar slider {{
  background: rgba({'255,255,255' if scheme.dark else '0,0,0'},0.26);
  border-radius: 999px; min-width: 7px; min-height: 7px; border: 3px solid transparent;
}}
scrollbar slider:hover {{ background: rgba({'255,255,255' if scheme.dark else '0,0,0'},0.42); }}

/* The strip of thumbnails. The selected one is drawn by the widget, not
   here, because it needs the picture's own shape. */
.strip {{ background: transparent; }}

progressbar trough {{ background: {sunk}; border-radius: 999px; min-height: 4px; }}
progressbar progress {{ background: {accent}; border-radius: 999px; min-height: 4px; }}

.pill-badge {{
  background: {hover}; color: {dim}; border-radius: 999px;
  padding: 1px 8px; font-size: 0.78em;
}}
.badge-on {{ background: {accent}; color: {on_accent}; }}
.osd-big {{ font-size: 2.1em; font-weight: 600; }}
.statusmark {{ color: {accent}; }}

/* The blurred backdrop. `backdrop-filter` blurs what the application has
   already drawn under the widget, which here is the picture — so this is the
   one part of the material that is real rather than painted. It is clipped to
   the CSS box, so every panel that asks for it must also declare the radius
   its slab is drawn with, or the blur is a rectangle with square ears
   sticking out past the rounded corners. Hence the radius classes. */
.glass-blur {{ {blur} }}
.glass-blur.r0 {{ border-radius: 0; }}
.glass-blur.r10 {{ border-radius: 10px; }}
.glass-blur.r14 {{ border-radius: 14px; }}
.glass-blur.r16 {{ border-radius: 16px; }}
.glass-blur.r18 {{ border-radius: 18px; }}
.glass-blur.r20 {{ border-radius: 20px; }}
.glass-blur.r-top {{ border-radius: 18px 18px 0 0; }}
.glass-blur.r-bottom {{ border-radius: 0 0 18px 18px; }}
.glass-blur.r-start {{ border-radius: 0 18px 18px 0; }}
.glass-blur.r-end {{ border-radius: 18px 0 0 18px; }}
"""


# ------------------------------------------------------------------ panels ----

class Glass(Gtk.Widget):
    """A single-child container that draws a sheet of glass behind its child.

    A widget of its own rather than a CSS background, because the material is
    a dozen gradients and a three-colour border and CSS can express none of
    it. `kind` picks which of `glass.Material`'s constructors is used, and
    `radius` can be one number or four when a bar is flush against an edge.
    """

    __gtype_name__ = "TheEyeGlass"

    def __init__(self, child=None, kind="bar", radius=None, scheme=None,
                 translucency=0.9, enabled=True, inset=0.0, blur=True,
                 radius_class=""):
        super().__init__()
        self.set_layout_manager(Gtk.BinLayout())
        self._kind = kind
        self._radius = radius
        self._scheme = scheme or theming.get(theming.DEFAULT)
        self._translucency = translucency
        self._enabled = enabled
        self._inset = inset
        self._blur = blur
        self._radius_class = radius_class or _radius_class(radius, kind)
        self.add_css_class("glass-flat")
        self._child = None
        self._apply_blur()
        if child is not None:
            self.set_child(child)

    def _apply_blur(self):
        """Put the blur classes on, or take them off.

        Both have to move together: the blur is clipped to the CSS box, so the
        radius class without the blur class is a rounded rectangle nothing is
        drawn into, and the blur class without the radius is a blurred
        rectangle poking out of the slab's corners.
        """
        wanted = self._blur and self._enabled
        for name, on in (("glass-blur", wanted),
                         (self._radius_class, wanted and bool(self._radius_class))):
            if not name:
                continue
            if on:
                self.add_css_class(name)
            else:
                self.remove_css_class(name)

    def set_blur(self, blur: bool):
        if blur != self._blur:
            self._blur = blur
            self._apply_blur()
            self.queue_draw()

    def blurred(self) -> bool:
        return self._blur and self._enabled

    def set_child(self, child):
        if self._child is not None:
            self._child.unparent()
        self._child = child
        if child is not None:
            child.set_parent(self)

    def get_child(self):
        return self._child

    def set_scheme(self, scheme):
        self._scheme = scheme
        self.queue_draw()

    def set_translucency(self, value):
        self._translucency = value
        self.queue_draw()

    def set_enabled(self, enabled):
        """Whether to draw the material at all, or leave a flat panel."""
        self._enabled = enabled
        self._apply_blur()
        self.queue_draw()

    def material(self):
        builder = getattr(glass.Material, self._kind, glass.Material.bar)
        return builder(self._scheme, self._translucency, blurred=self.blurred())

    def do_snapshot(self, snapshot):
        width, height = float(self.get_width()), float(self.get_height())
        if width > 0 and height > 0:
            if self._enabled:
                glass.slab(snapshot, self.material(), self._inset, self._inset,
                           width - self._inset * 2, height - self._inset * 2,
                           radius=self._radius)
            else:
                # Everything off: a flat panel in the glass's own tint, which
                # still reads as a surface and costs one node.
                material = self.material()
                box = glass.rounded(0, 0, width, height,
                                    self._radius if self._radius is not None
                                    else material.radius)
                snapshot.push_rounded_clip(box)
                snapshot.append_color(material.top, glass.rect(0, 0, width, height))
                snapshot.pop()
        if self._child is not None:
            self.snapshot_child(self._child, snapshot)

    def do_dispose(self):
        if self._child is not None:
            self._child.unparent()
            self._child = None
        Gtk.Widget.do_dispose(self)


class Well(Gtk.Widget):
    """A recess for something to sit in — the seek bar's trough, a text field.

    Same idea as `Glass` with the light coming from the other direction, which
    is the whole of what makes a shape read as sunk rather than raised.
    """

    __gtype_name__ = "TheEyeWell"

    def __init__(self, child=None, radius=10.0, scheme=None, translucency=0.9):
        super().__init__()
        self.set_layout_manager(Gtk.BinLayout())
        self._radius = radius
        self._scheme = scheme or theming.get(theming.DEFAULT)
        self._translucency = translucency
        self._child = None
        if child is not None:
            self.set_child(child)

    def set_child(self, child):
        if self._child is not None:
            self._child.unparent()
        self._child = child
        if child is not None:
            child.set_parent(self)

    def set_scheme(self, scheme):
        self._scheme = scheme
        self.queue_draw()

    def do_snapshot(self, snapshot):
        width, height = float(self.get_width()), float(self.get_height())
        if width > 0 and height > 0:
            glass.slab(snapshot,
                       glass.Material.well(self._scheme, self._translucency),
                       0, 0, width, height, radius=self._radius, shadow=False)
        if self._child is not None:
            self.snapshot_child(self._child, snapshot)

    def do_dispose(self):
        if self._child is not None:
            self._child.unparent()
            self._child = None
        Gtk.Widget.do_dispose(self)


# ----------------------------------------------------------------- controls ----

def icon_button(icon: str, tooltip: str, action: str = "", on_click=None,
                classes=("circular",)) -> Gtk.Button:
    """A flat round icon button, which is most of the chrome in this app."""
    button = Gtk.Button(icon_name=icon, tooltip_text=tooltip,
                        valign=Gtk.Align.CENTER)
    for name in classes:
        button.add_css_class(name)
    if action:
        button.set_action_name(action)
    if on_click is not None:
        button.connect("clicked", lambda _b: on_click())
    return button


def toggle_button(icon: str, tooltip: str, action: str = "",
                  classes=("circular",)) -> Gtk.ToggleButton:
    button = Gtk.ToggleButton(icon_name=icon, tooltip_text=tooltip,
                              valign=Gtk.Align.CENTER)
    for name in classes:
        button.add_css_class(name)
    if action:
        button.set_action_name(action)
    return button


def label(text: str, *classes, xalign=0.0, **kwargs) -> Gtk.Label:
    widget = Gtk.Label(label=text, xalign=xalign, **kwargs)
    for name in classes:
        widget.add_css_class(name)
    return widget


def row(*children, spacing=8, **kwargs) -> Gtk.Box:
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=spacing,
                  **kwargs)
    for child in children:
        box.append(child)
    return box


def column(*children, spacing=8, **kwargs) -> Gtk.Box:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing,
                  **kwargs)
    for child in children:
        box.append(child)
    return box


class Readout(Gtk.Label):
    """A label that never changes width as its text changes.

    A clock counting up beside a seek bar moves the bar sideways every time it
    gains a digit, and the bar is the thing being aimed at. This reserves room
    for the widest string it will ever hold and right-aligns within it, which
    is also why the stylesheet asks for tabular figures.
    """

    __gtype_name__ = "TheEyeReadout"

    def __init__(self, widest="0:00:00", **kwargs):
        super().__init__(**kwargs)
        self.add_css_class("numeric")
        self.set_widest(widest)

    def set_widest(self, text: str):
        layout = self.create_pango_layout(text)
        width, _height = layout.get_pixel_size()
        self.set_size_request(width + 2, -1)


class SoundFace(GObject.Object, Gdk.Paintable):
    """What to put on the stage when what is playing has no pictures in it.

    A sound file with no embedded artwork would otherwise leave the stage
    empty, which reads as "this is broken" rather than as "this is music".
    So it gets a record: a dark disc, a few grooves, a label in the theme's
    accent and a spindle hole.

    Drawn rather than shipped as an icon because it has to pick up whichever
    theme is loaded, and because at any size it is nine nodes — the icon
    would be a texture to load, scale and keep.
    """

    __gtype_name__ = "TheEyeSoundFace"

    SIZE = 512

    def __init__(self, scheme=None):
        super().__init__()
        self._scheme = scheme or theming.get(theming.DEFAULT)

    def set_scheme(self, scheme):
        self._scheme = scheme
        self.invalidate_contents()

    def do_get_intrinsic_width(self):
        return self.SIZE

    def do_get_intrinsic_height(self):
        return self.SIZE

    def do_get_intrinsic_aspect_ratio(self):
        return 1.0

    def do_snapshot(self, snapshot, width, height):
        scheme = self._scheme
        extent = min(width, height)
        if extent <= 1:
            return
        cx, cy = width / 2, height / 2
        radius = extent * 0.42

        def disc(r, colour):
            box = glass.rounded(cx - r, cy - r, r * 2, r * 2, r)
            snapshot.push_rounded_clip(box)
            snapshot.append_color(colour, glass.rect(cx - r, cy - r, r * 2, r * 2))
            snapshot.pop()

        disc(radius, glass.rgba(glass.shade(scheme.glass, -0.35
                                            if scheme.dark else -0.55), 1.0))
        # Grooves: concentric rings, drawn as borders so each is one node.
        for step in range(1, 6):
            r = radius * (0.95 - step * 0.11)
            glass.ring(snapshot,
                       glass.white(0.05) if scheme.dark else glass.black(0.05),
                       cx - r, cy - r, r * 2, r * 2, radius=r, thickness=1.0)
        disc(radius * 0.34, glass.rgba(scheme.accent, 0.85))
        disc(radius * 0.07, glass.rgba(scheme.matte, 1.0))


# ------------------------------------------------------------------ toasts ----

def toast(widget, message: str, timeout: int = 3):
    """Find the nearest toast overlay and put a message on it."""
    node = widget
    while node is not None and not isinstance(node, Adw.ToastOverlay):
        node = node.get_parent()
    if node is not None:
        node.add_toast(Adw.Toast(title=message, timeout=timeout))
    return node


def error_toast(widget, exc, prefix: str = ""):
    text = str(exc).strip().splitlines()
    first = text[0] if text else "Something went wrong"
    toast(widget, f"{prefix}{first}" if prefix else first, timeout=6)


def confirm(parent, heading: str, body: str, action_label: str, callback,
            destructive: bool = True):
    """Ask before anything that cannot be undone. `callback()` runs on yes."""
    dialog = Adw.AlertDialog(heading=heading, body=body)
    dialog.add_response("cancel", "Cancel")
    dialog.add_response("go", action_label)
    dialog.set_response_appearance(
        "go", Adw.ResponseAppearance.DESTRUCTIVE if destructive
        else Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("cancel")
    dialog.set_close_response("cancel")

    def answered(dlg, result):
        if dlg.choose_finish(result) == "go":
            callback()

    dialog.choose(parent, None, answered)


def copy_text(widget, text: str, note: str = "Copied"):
    display = Gdk.Display.get_default()
    if display is not None:
        display.get_clipboard().set(text)
        toast(widget, note)
