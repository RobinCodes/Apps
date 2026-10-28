"""The house stylesheet, generated from a theme.

This file is the house style's *shape* half, as `theme.py` is its colour half,
and like it this is the same file in every app — one copy each, kept in step by
`house/sync.py`.

Two sheets come out of here and they do different jobs.

`palette()` is the short one and the important one. libadwaita names every
colour it draws with — `@window_bg_color`, `@accent_bg_color`, `@card_bg_color`
and two dozen more — and documents that an application may redefine them. So a
theme is applied to a stock Adwaita window by restating that whole palette in
the theme's own colours, and then every widget nobody has written a rule for —
a spin button, a toast, an `AdwPreferencesPage`, a date picker that did not
exist when this was written — comes out in the theme anyway. It is nine lines
of plumbing that save a thousand lines of selectors, and it is why an app can
join the house style without its own chrome being rewritten.

`chrome()` is the long one: the house's own idiom on top of that. Flat buttons,
because a bar of raised buttons on a sheet of glass looks like a bar of buttons
on a sheet of glass. One pixel of light along the top edge of every surface and
one pixel of shadow along the bottom, which is what reads as a bevel rather
than as a border. Radii that are concentric rather than equal. Sliders with a
flat trough, because the well behind them is drawn by `glass.py` and two
troughs is one too many.

What is deliberately *not* here is anything an individual app has to say about
its own content — the treemap's frame, the filmstrip, a diff's gutter. Those
stay in each app's `widgets.py`, which appends them to this. The division is
not stylistic: a rule here has to be true of a terminal, a file manager, a
picture viewer, a disk map and three editors at once, and most rules are not.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gtk  # noqa: E402

from . import theme as theming  # noqa: E402

# The gap between two surfaces of glass, and between a surface and the edge of
# the slab it sits on. One number for the whole house, so a card can never sit
# a pixel off the one beside it.
GAP = 7

# The radii, in the order a window nests them. A card inside a rounded slab
# wants a smaller radius than the slab or the two curves fight; strictly
# concentric would be `window - GAP`, which at the default comes out at 9 and
# reads as a square with its corners filed off, so a card keeps more than that
# and is floored at 10 to stay obviously round even in a square window.
WINDOW_RADIUS = 16
CARD_RADIUS = 14
PANEL_RADIUS = 16
CONTROL_RADIUS = 9
PILL_RADIUS = 999

EASE = "cubic-bezier(0.22, 0.61, 0.36, 1)"


def card_radius(window_radius: int) -> int:
    return max(10, min(int(window_radius) - 3, 16))


def panel_radius(window_radius: int) -> int:
    return max(8, min(int(window_radius), 18))


# --------------------------------------------------------------- palette ----

def palette(scheme: theming.Theme, translucency: float = 1.0) -> str:
    """libadwaita's whole named palette, restated in one theme's colours.

    `translucency` is not applied here. A named colour is used in places this
    file cannot see — inside a popover that is its own Wayland surface, behind
    a drag icon — and a transparent `@window_bg_color` makes holes in all of
    them. The see-through surfaces are the ones below, which ask for alpha
    explicitly.
    """
    s = scheme
    dark = s.dark
    ink = theming.on(s.accent)
    # Each of these is the theme's own background moved a step, rather than a
    # colour of its own, so a new theme needs eight hex values and not forty.
    lift = (lambda n: theming.shade(s.bg, n)) if dark else (lambda n: theming.shade(s.bg, -n))
    view = lift(0.015)
    raised = lift(0.045)
    floating = lift(0.075)
    sunken = theming.shade(s.bg, -0.35) if dark else theming.shade(s.bg, -0.06)
    hairline = "rgba(255,255,255,0.11)" if dark else "rgba(0,0,0,0.11)"
    return f"""
/* ---------------------------------------------------- libadwaita palette ---
   Restated so that every stock widget follows the theme without a rule of
   its own. Order matters only in that a definition must precede its use. */
@define-color window_bg_color {s.bg};
@define-color window_fg_color {s.fg};
@define-color view_bg_color {view};
@define-color view_fg_color {s.fg};
@define-color headerbar_bg_color {raised};
@define-color headerbar_fg_color {s.fg};
@define-color headerbar_border_color {hairline};
@define-color headerbar_backdrop_color {s.bg};
@define-color headerbar_shade_color rgba(0,0,0,{0.36 if dark else 0.07});
@define-color headerbar_darker_shade_color rgba(0,0,0,{0.45 if dark else 0.10});
@define-color sidebar_bg_color {lift(0.03)};
@define-color sidebar_fg_color {s.fg};
@define-color sidebar_backdrop_color {s.bg};
@define-color sidebar_border_color {hairline};
@define-color sidebar_shade_color rgba(0,0,0,{0.32 if dark else 0.07});
@define-color secondary_sidebar_bg_color {lift(0.02)};
@define-color secondary_sidebar_fg_color {s.fg};
@define-color secondary_sidebar_backdrop_color {s.bg};
@define-color secondary_sidebar_border_color {hairline};
@define-color secondary_sidebar_shade_color rgba(0,0,0,{0.28 if dark else 0.06});
@define-color card_bg_color {raised};
@define-color card_fg_color {s.fg};
@define-color card_shade_color rgba(0,0,0,{0.36 if dark else 0.07});
@define-color thumbnail_bg_color {raised};
@define-color thumbnail_fg_color {s.fg};
@define-color dialog_bg_color {floating};
@define-color dialog_fg_color {s.fg};
@define-color popover_bg_color {floating};
@define-color popover_fg_color {s.fg};
@define-color popover_shade_color rgba(0,0,0,{0.40 if dark else 0.08});
@define-color shade_color rgba(0,0,0,{0.36 if dark else 0.07});
@define-color scrollbar_outline_color rgba({'0,0,0' if dark else '255,255,255'},0.5);
@define-color borders {hairline};

@define-color accent_bg_color {s.accent};
@define-color accent_fg_color {ink};
/* The accent used as *text* is not the accent used as a fill: the fill is
   judged against a label on top of it and the text against the window
   behind it, and on a light theme those want different colours. */
@define-color accent_color {s.accent if not dark else theming.shade(s.accent, 0.10)};

@define-color destructive_bg_color {'#C0392B' if dark else '#B02C1E'};
@define-color destructive_fg_color #FFFFFF;
@define-color destructive_color {'#FF8A7A' if dark else '#9E2714'};
@define-color success_bg_color {s.ansi[2] if dark else s.ansi[10]};
@define-color success_fg_color {theming.on(s.ansi[2] if dark else s.ansi[10])};
@define-color success_color {s.ansi[2] if dark else s.ansi[10]};
@define-color warning_bg_color {s.ansi[3] if dark else s.ansi[11]};
@define-color warning_fg_color {theming.on(s.ansi[3] if dark else s.ansi[11])};
@define-color warning_color {s.ansi[3] if dark else s.ansi[11]};
@define-color error_bg_color {s.ansi[1] if dark else s.ansi[9]};
@define-color error_fg_color {theming.on(s.ansi[1] if dark else s.ansi[9])};
@define-color error_color {s.ansi[1] if dark else s.ansi[9]};

/* The named hues, for the handful of widgets that reach for one directly.
   Taken from the theme's own palette so that a chart or a tag in one app is
   the same blue as in the next. */
@define-color blue_3 {s.ansi[4]};
@define-color green_3 {s.ansi[2]};
@define-color yellow_3 {s.ansi[3]};
@define-color orange_3 {theming.mix(s.ansi[1], s.ansi[3], 0.5)};
@define-color red_3 {s.ansi[1]};
@define-color purple_3 {s.ansi[5]};
@define-color light_1 {s.ansi[15]};
@define-color dark_5 {s.ansi[0]};

/* Not libadwaita's: the house's own names, so that an app's own sheet can
   reach for the theme without being handed every colour as a format hole. */
@define-color house_bg {s.bg};
@define-color house_fg {s.fg};
@define-color house_dim {s.dim};
@define-color house_accent {s.accent};
@define-color house_on_accent {ink};
@define-color house_glass {s.glass};
@define-color house_sunken {sunken};
@define-color house_raised {raised};
@define-color house_hairline {hairline};
@define-color house_matte {s.matte};
"""


# ---------------------------------------------------------------- chrome ----

def chrome(scheme: theming.Theme, translucency: float = 1.0,
           window_radius: int = WINDOW_RADIUS, blur: int = 0,
           glass: bool = True) -> str:
    """The house idiom: flat controls, bevelled surfaces, concentric radii."""
    s = scheme
    dark = s.dark
    fg, dim, accent = s.fg, s.dim, s.accent
    ink = theming.on(accent)
    hover = "rgba(255,255,255,0.09)" if dark else "rgba(0,0,0,0.055)"
    pressed = "rgba(255,255,255,0.15)" if dark else "rgba(0,0,0,0.105)"
    sunk = "rgba(0,0,0,0.30)" if dark else "rgba(0,0,0,0.075)"
    line = "rgba(255,255,255,0.11)" if dark else "rgba(0,0,0,0.11)"
    bar = "255,255,255" if dark else "0,0,0"
    floating = theming.shade(s.glass, 0.075 if dark else -0.025)

    # Light catching an edge is white at low alpha on a dark theme and simply
    # invisible on a light one, so a pale theme leans on a soft shadow for its
    # rim instead and takes a much stronger sheen to stay legible.
    rim_top = (0.130 if dark else 0.58) * s.rim
    rim_all = (0.090 if dark else 0.035) * s.rim
    rim_bottom = 0.18 if dark else 0.06
    sheen_top = (0.075 if dark else 0.34) * s.rim

    card = max(0.0, min(1.0, translucency + (1.0 - translucency) * 0.45))
    slab = max(0.0, min(1.0, translucency * (0.34 if dark else 0.42)))

    blur_rule = (f"backdrop-filter: blur({int(blur)}px) saturate(145%);"
                 if blur and glass else "")
    radius = int(window_radius)
    return f"""
/* ------------------------------------------------------------- surfaces ---
   The window paints nothing. The slab inside it does, which is what lets the
   desktop through at the edges and what makes one rounded outline rather
   than a stack of them. */
window.house, window.house > contents, window.house .window-contents {{
  background: none;
  background-color: transparent;
  box-shadow: none;
}}

/* The slab is the frame the modules are laid on, so it carries the padding
   that becomes the gap around them as well as the outline, the rim and the
   clip. It is the sheerest surface in the window: the cards hold the text,
   the slab is what shows between them. */
.slab {{
  border-radius: {radius}px;
  padding: {GAP}px;
  background-color: alpha(@house_glass, {slab:.3f});
  /* The sheen and the aurora are *not* here, and that is a performance
     decision rather than a stylistic one. They used to be five gradients in
     `background-image`, and GSK re-blends every one of them over the whole
     slab on every repaint: measured on this machine, a flat slab cost 6.3 ms
     a frame, the two sheen gradients took it to 23.2, and each aurora blob
     added about 13 more — 62 ms for the lot, on the largest surface in the
     window, every time anything damaged it. They are drawn once into a small
     texture instead and blitted by `Slab.do_snapshot`; the layer is so soft
     that upscaling it from 256 px wide is indistinguishable. */
  box-shadow:
    inset 0 1px 0 alpha(white, {rim_top:.3f}),
    inset 0 0 0 1px alpha(white, {rim_all:.3f}),
    inset 0 -1px 0 alpha(black, {rim_bottom:.3f});
}}

/* The slab has two jobs and they want different strengths.

   As a *frame* — Hermione's arrangement, where the interface is cut into
   cards laid on it with a gap between — it is the sheerest surface in the
   window and the cards are what hold the text. That is the rule above.

   As a *sheet* — everywhere else, where the content sits straight on it —
   it is the only painted surface there is, and at a frame's alpha the whole
   window comes out at a third of itself and every label is fighting the
   wallpaper. So it takes the card's strength instead. One class, put on by
   `Glazing.wrap`, because which of the two an app is is a fact about its
   layout rather than a setting. */
.slab.sheet {{ background-color: alpha(@house_glass, {card:.3f}); }}

/* Maximised or tiled, a rounded corner is a transparent notch against the
   next window, so the slab squares itself off. `.squared` is put on by the
   window itself: niri only reports the tiled state when prefer-no-csd is
   set, so the CSS state alone cannot be trusted to know whether a corner is
   against the edge of the screen. */
window.maximized .slab, window.fullscreen .slab, .slab.squared {{
  border-radius: 0;
}}

/* A module: one of the cards cut out of the single sheet of glass. All of
   them share one alpha — they are one material in several pieces, and the
   depth is in the gaps rather than in giving each piece its own strength. */
.module, .card-glass {{
  border-radius: {card_radius(radius)}px;
  background-color: alpha(@house_glass, {card:.3f});
  box-shadow:
    inset 0 1px 0 alpha(white, {rim_top:.3f}),
    inset 0 0 0 1px alpha(white, {rim_all:.3f}),
    0 1px 4px alpha(black, {0.22 if dark else 0.10:.3f});
}}

/* Anything that floats clear of the layout: a palette, a sheet, a progress
   overlay. This is the one place a blur is worth its offscreen pass, because
   behind it is content with real detail rather than a flat tint.

   The drop shadow is deliberately modest, and so is every other one below.
   GSK renders a soft shadow by blurring an offscreen, and the cost goes with
   the blur radius rather than with the size of the thing casting it: twelve
   popover-sized boxes measured 16.8 ms a frame at `0 10px 34px` and 6.2 ms
   at `0 4px 12px` — nearly three times, for a difference you have to be told
   about to see. Past a frame's budget a menu stops feeling attached to the
   click that opened it, which on a 4 GB laptop is most of what "the popups
   are laggy" means. A shadow here says "this floats"; it does not have to be
   the deepest shadow that can be drawn.

   `:not(dialog)` is load-bearing. libadwaita puts `.floating` on every
   in-window Adw.Dialog, so a bare `.floating` gave each preferences dialog
   this blur — a re-render and blur of the whole window behind it on every
   repaint inside the dialog. Measured on this machine's GPU with a
   preferences dialog open: 5 ms a frame without it, 23 ms with it, which is
   a dropped frame on every hover. A dialog sits over a dimmed window, so
   there is nothing behind it worth blurring anyway. */
.floating:not(dialog) {{
  background-color: alpha(@house_glass, {0.72 if dark else 0.80:.3f});
  {blur_rule}
  border-radius: {panel_radius(radius)}px;
  box-shadow:
    inset 0 1px 0 alpha(white, {rim_top * 1.1:.3f}),
    inset 0 0 0 1px alpha(white, {rim_all:.3f}),
    0 6px 16px alpha(black, {0.45 if dark else 0.18:.3f});
}}

/* A strip inside a card is lifted with light, never cut in with a darker
   tint — the same rule the controls on a toolbar follow. Cutting in reads as
   a hole in the glass; lifting reads as another layer of it. */
.inset {{
  background-color: alpha(white, {0.045 if dark else 0.30:.3f});
  border-radius: {CONTROL_RADIUS}px;
}}
.inset-strong {{ background-color: alpha(white, {0.080 if dark else 0.46:.3f}); }}
.well {{ background-color: {sunk}; border-radius: {CONTROL_RADIUS}px; }}

/* libadwaita gives its split views' panes and its toolbar views' bars solid
   backgrounds of their own, behind anything put there. Left alone a sidebar
   comes out completely opaque however translucent its tint, and a pane's
   toolbar ends up the *least* see-through surface in the window — more solid
   than the list it sits above, which reads as upside down. */
.glass-flat, .glass-flat > *, headerbar.glass-flat,
.toolbar.glass-flat, box.glass-flat {{ background: none; box-shadow: none; }}

/* --------------------------------------------------------------- text --- */

label {{ color: {fg}; }}
label.dim, .dim, label.dim-label, .dim-label {{ color: {dim}; }}
label.numeric, .numeric {{ font-feature-settings: "tnum" 1; }}
label.title, .title {{ font-weight: 600; }}
label.heading, .heading {{ font-weight: 600; font-size: 1.05em; }}
label.caption, .caption {{ font-size: 0.84em; color: {dim}; }}
label.mono, .mono, .monospace {{ font-family: monospace; font-size: 0.92em; }}
.accented {{ color: @accent_color; }}

/* ------------------------------------------------------------ controls --- */

/* Flat by default. A raised button on glass looks like a button lying on
   glass; a tinted one looks like part of it. */
button {{
  background: none; border: none; box-shadow: none;
  color: {fg}; border-radius: {CONTROL_RADIUS}px; padding: 6px 10px;
  transition: background 120ms {EASE}, color 120ms {EASE};
}}
button:hover {{ background: {hover}; }}
button:active, button:checked {{ background: {pressed}; }}
button:disabled {{ color: {dim}; opacity: 0.55; }}
button.circular {{ border-radius: {PILL_RADIUS}px; padding: 7px;
                   min-width: 20px; min-height: 20px; }}
button.pill {{ border-radius: {PILL_RADIUS}px; padding: 5px 14px; }}
button.flat {{ background: none; }}
button.flat:hover {{ background: {hover}; }}
button.raised, button.field {{
  background: alpha(white, {0.07 if dark else 0.55:.3f});
  box-shadow: inset 0 1px 0 alpha(white, {rim_top:.3f});
}}
button.raised:hover, button.field:hover {{
  background: alpha(white, {0.11 if dark else 0.72:.3f});
}}
button.suggested-action {{ background: {accent}; color: {ink}; }}
button.suggested-action:hover {{ background: {theming.shade(accent, 0.12)}; }}
button.suggested-action:active {{ background: {theming.shade(accent, -0.08)}; }}
button.destructive-action {{ background: @destructive_bg_color; color: #FFFFFF; }}
button.chip {{
  border-radius: {PILL_RADIUS}px; padding: 4px 12px; font-size: 0.88em;
  background: {hover}; color: {fg};
}}
/* Element-and-class, not a lone class: `button.chip` already sets a
   background and would win on specificity, so the selected look would never
   light up. */
button.chip:checked, button.badge-on, button.chip.badge-on {{
  background: {accent}; color: {ink};
}}

/* The focus ring, and the one rule here it is worth being careful about.

   GTK sets `:focus-visible` on the focused widget *and on every one of its
   ancestors* — measured, not assumed. So a bare `:focus-visible {{ outline }}`
   does not draw one ring around the focused control, it draws a nested stack
   of them: around the entry, around the box holding it, around the pane, and
   around the window. That is what the purple rectangles were.

   `:focus` is no help either, because a composite widget keeps the real focus
   on an inner node — an entry's `text`, a spin button's `text` — so the
   widget you want the ring on is never the one that is `:focus`. Which leaves
   naming the controls that should have one, which is also what Adwaita does.
   Containers are not in the list, and that is the whole point of it. */
button:focus-visible, checkbutton:focus-visible, radiobutton:focus-visible,
switch:focus-visible, scale:focus-visible, expander:focus-visible,
modelbutton:focus-visible, dropdown:focus-visible, combobox:focus-visible,
colorswatch:focus-visible, tab:focus-visible,
row:focus-visible, listview > row:focus-visible,
columnview > listview > row:focus-visible {{
  outline: 2px solid {accent};
  outline-offset: 2px;
}}
/* A row's ring goes just inside it: offset outwards, it is drawn over the
   row above and below, and a list of focusable rows looks like a ladder. */
row:focus-visible, listview > row:focus-visible {{ outline-offset: -2px; }}

entry, spinbutton, searchentry, .entry {{
  background: {sunk}; color: {fg}; border: none;
  border-radius: {CONTROL_RADIUS}px; padding: 6px 10px; box-shadow: none;
}}
entry:focus-within, spinbutton:focus-within, searchentry:focus-within {{
  outline: 2px solid {accent}; outline-offset: -1px;
}}
entry > text, searchentry > text {{ color: {fg}; }}
entry placeholder, entry > text > placeholder {{ color: {dim}; opacity: 1; }}

scale {{ min-height: 20px; }}
scale trough {{ background: {sunk}; border-radius: {PILL_RADIUS}px;
                min-height: 5px; border: none; }}
scale highlight {{ background: {accent}; border-radius: {PILL_RADIUS}px;
                   min-height: 5px; }}
/* No negative margin on the knob. The usual trick is to let it overhang the
   trough with one, which works until a theme underneath sets a different
   min-width: the two are subtracted, GTK gets a negative minimum, and every
   slider in the app logs a warning per measure. The trough is given the
   height instead, which cannot come out negative whatever wins. */
scale slider {{
  background: #FFFFFF; border-radius: {PILL_RADIUS}px;
  min-width: 14px; min-height: 14px; margin: 0;
  box-shadow: 0 1px 2px rgba(0,0,0,0.40);
  transition: transform 110ms {EASE};
}}
scale:hover slider {{ box-shadow: 0 1px 3px rgba(0,0,0,0.50); }}
scale marks, scale marks indicator {{ color: {dim}; }}

switch {{ background: {sunk}; border: none; box-shadow: none; }}
switch:checked {{ background: {accent}; }}
switch > slider {{ background: #FFFFFF; box-shadow: 0 1px 2px rgba(0,0,0,0.4); }}
scrollbar > range > trough > slider {{ margin: 0; }}

checkbutton check, checkbutton radio {{ background: {sunk}; border: none; }}
checkbutton check:checked, checkbutton radio:checked {{
  background: {accent}; color: {ink};
}}

progressbar trough {{ background: {sunk}; border-radius: {PILL_RADIUS}px;
                      min-height: 4px; }}
progressbar progress {{ background: {accent}; border-radius: {PILL_RADIUS}px;
                        min-height: 4px; }}

levelbar block.filled {{ background: {accent}; }}
levelbar trough {{ background: {sunk}; }}

scrollbar {{ background: transparent; border: none; }}
scrollbar slider {{
  background: rgba({bar},0.26); border-radius: {PILL_RADIUS}px;
  min-width: 7px; min-height: 7px; border: 3px solid transparent;
}}
scrollbar slider:hover {{ background: rgba({bar},0.42); }}
scrollbar slider:active {{ background: rgba({bar},0.56); }}

/* ---------------------------------------------------------------- lists --- */

listview, listview > row, list, row, columnview, gridview {{
  background: transparent;
}}
listview > row:selected, row:selected, columnview > listview > row:selected {{
  background: {accent}; color: {ink}; border-radius: 10px;
}}
listview > row:hover, row.activatable:hover {{
  background: {hover}; border-radius: 10px;
}}
/* Quiet text on a selected row is quiet *against the accent*, not against
   the window. Left alone it keeps the theme's dim colour, which was chosen
   to sit on the background and on a filled row is the one piece of text
   nobody can read. */
/* `label {{ color }}` above sets the colour on each label directly, which
   beats the selected row's colour *inherited* into it however specific the
   row's rule is — so the row has to name the labels as well, or the text on
   a filled row stays the colour it had on the background. */
row:selected label, listview > row:selected label,
row:selected > box label {{ color: {ink}; }}
row:selected .dim, row:selected label.dim,
row:selected .dim-label, row:selected label.dim-label,
row:selected .caption, row:selected label.caption,
row:selected .subtitle, row:selected .numeric,
row:selected .monospace, row:selected .mono {{
  color: alpha({ink}, 0.72);
}}
columnview header button {{
  background: none; border-radius: 8px; color: {dim}; font-weight: 600;
}}
columnview header button:hover {{ background: {hover}; }}

/* ------------------------------------------------------------ floating --- */

/* Popovers, dialogs and toasts are the one place a near-solid surface is
   right: they float clear of the layout and have to be readable over
   whatever is underneath, which in a viewer is a photograph. A popover is
   also its own Wayland surface, so there is nothing of ours behind it to
   blur and the blur would cost an offscreen pass to change nothing. */
popover > contents {{
  background: {floating}; color: {fg};
  border: 1px solid {line};
  border-radius: 14px;
  box-shadow: 0 4px 12px rgba(0,0,0,{0.40 if dark else 0.16});
}}
/* The arrow is a dozen pixels of triangle underneath the contents. Giving it
   the same shadow buys nothing and costs a second blurred offscreen on every
   menu that opens. */
popover > arrow {{
  background: {floating}; color: {fg};
  border: 1px solid {line};
  box-shadow: none;
}}
popover modelbutton, popover.menu button {{ border-radius: 8px; color: {fg}; }}
popover modelbutton:hover {{ background: {hover}; }}

dialog, .dialog-page, window.dialog, .card {{
  background: {floating}; color: {fg}; border-radius: {PANEL_RADIUS}px;
}}
preferencespage, preferencesgroup, .preferences {{ background: transparent; }}
preferencesgroup > box > label {{ color: {fg}; }}

toast > widget, .toast {{
  background: {floating}; color: {fg}; border-radius: {PILL_RADIUS}px;
  border: 1px solid {line};
  box-shadow: 0 3px 10px rgba(0,0,0,{0.38 if dark else 0.15});
}}
tooltip, tooltip.background {{
  background: {floating}; color: {fg}; border-radius: 10px;
  border: 1px solid {line};
}}

.pill-badge, .badge {{
  background: {hover}; color: {dim}; border-radius: {PILL_RADIUS}px;
  padding: 1px 8px; font-size: 0.78em;
}}
.badge-on {{ background: {accent}; color: {ink}; }}
.separator-hair {{ background: {line}; min-width: 1px; min-height: 1px; }}

/* Tabs, where an app has them. A selected tab is lifted with light rather
   than outlined, so the row reads as one piece of glass with one tile
   raised out of it. */
.tab {{
  border-radius: 10px 10px 0 0; padding: 4px 10px; color: {dim};
  background: none;
}}
.tab:hover {{ background: {hover}; color: {fg}; }}
.tab:checked, .tab.active {{
  background: alpha(white, {0.10 if dark else 0.55:.3f}); color: {fg};
  box-shadow: inset 0 1px 0 alpha(white, {rim_top:.3f});
}}

/* The blurred backdrop, where a real one is affordable. `backdrop-filter`
   blurs what the application has already drawn under the widget, so it only
   means anything where a layer floats over content — and it is clipped to
   the CSS box, so every surface that asks for it must also declare the
   radius its glass is drawn with, or the blur is a rectangle with square
   ears past the rounded corners. Hence the radius classes. */
.glass-blur {{ {blur_rule} }}
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


def sheet(scheme: theming.Theme, translucency: float = 1.0,
          window_radius: int = WINDOW_RADIUS, blur: int = 0,
          glass: bool = True) -> str:
    """The whole house sheet for one theme: the palette, then the idiom."""
    return (palette(scheme, translucency)
            + chrome(scheme, translucency, window_radius, blur, glass))


# -------------------------------------------------------------- install ----

_providers: dict[int, Gtk.CssProvider] = {}

# The theme the sheet was last installed from. A process-wide answer because
# the sheet is a process-wide thing: there is one provider on the display, so
# there is one theme in force however many windows are open. Widgets that
# draw rather than style — a meter, a treemap, a waveform — read it here
# instead of being handed a theme through four constructors.
_current: theming.Theme | None = None


def current() -> theming.Theme:
    """Whichever theme the sheet was last installed from.

    The house default before anything has been installed, so a widget built
    during startup draws in a real palette rather than crashing on None.
    """
    return _current or theming.get(theming.DEFAULT)


def setting(config, key, default=None):
    """One house setting out of whichever mapping the app keeps.

    Through `get` rather than `[]` so that a dict, a `Config` and a
    `theming.Settings` all work, and so that a key the app has not adopted
    yet falls back to the house default rather than raising — which is what
    lets an app take these on one at a time.
    """
    if default is None:
        default = theming.SETTINGS.get(key)
    if config is None:
        return default
    try:
        value = config[key]
    except (KeyError, TypeError, IndexError):
        return default
    return default if value is None else value


def wanted_key(config) -> str:
    """Which theme is in force, for whichever settings an app keeps.

    Not always the `theme` setting: with `follow_system` on, the desktop
    picks which of the house pair it is.
    """
    if setting(config, "follow_system"):
        if system_is_dark():
            return setting(config, "dark_theme")
        return setting(config, "light_theme")
    return setting(config, "theme")


def force_scheme(scheme: theming.Theme) -> None:
    """Tell libadwaita which way round the theme is.

    A few things it decides from its own idea of light or dark rather than
    from the palette: the shade under a popover, the colour of a spinner,
    whether a symbolic icon is drawn light or dark. Left alone, a Porcelain
    window in a dark session gets white icons on warm paper. Separate from
    `install()` for the apps that build their own sheet and only want this
    part of the plumbing.
    """
    Adw.StyleManager.get_default().set_color_scheme(
        Adw.ColorScheme.FORCE_DARK if scheme.dark
        else Adw.ColorScheme.FORCE_LIGHT)


def install(scheme: theming.Theme, extra: str = "", translucency: float = 1.0,
            window_radius: int = WINDOW_RADIUS, blur: int = 0,
            glass: bool = True,
            display: Gdk.Display | None = None) -> Gtk.CssProvider | None:
    """Put the house sheet plus `extra` on the display, replacing what is there.

    One provider for the process, reloaded in place. Adding a second provider
    per theme change would leave the old rules loaded underneath, where they
    win or lose by specificity rather than by recency — and the *second* theme
    change would then start producing colours from neither theme.

    libadwaita is told which way round the theme is as well as what colour it
    is. A few things it still decides for itself — the shade under a popover,
    the colour of a spinner, whether an icon is drawn light or dark — come
    from its own idea of light or dark rather than from the palette, and a
    dark theme under a light `AdwStyleManager` gets black icons on it.
    """
    global _current
    _current = scheme
    display = display or Gdk.Display.get_default()
    if display is None:
        return None
    text = sheet(scheme, translucency, window_radius, blur, glass)
    if extra:
        text += extra
    provider = _providers.get(id(display))
    if provider is None:
        provider = _providers[id(display)] = Gtk.CssProvider()
        provider.load_from_string(text)
        Gtk.StyleContext.add_provider_for_display(
            display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    else:
        provider.load_from_string(text)

    force_scheme(scheme)
    return provider


class SystemScheme:
    """What the desktop is asking for, light or dark, and when it changes.

    `AdwStyleManager:dark` is the obvious place to read this and it is the
    wrong one here, for a reason worth writing down because the bug it causes
    is silent. `install()` forces the colour scheme so that libadwaita's own
    light/dark decisions match the theme, and from then on `get_dark()`
    returns *what was forced*, not what the desktop wants. An app that read it
    to decide which half of `follow_system` to use would read back its own
    answer, and the first switch to a light theme would latch there for ever.

    So the question is put to the source instead: the XDG settings portal's
    `org.freedesktop.appearance` `color-scheme`, which is 1 for "prefer dark",
    2 for "prefer light" and 0 for "no preference". It is the same value
    libadwaita itself reads, and it keeps answering honestly after the scheme
    has been forced.

    **Nothing here blocks, and that is the whole design.** The first version
    used `new_for_bus_sync` + `call_sync`, which looks harmless and is not:
    a synchronous proxy with auto-start *activates* the portal if it is not
    running, and then waits for it. On this machine
    `systemd-analyze blame --user` put `xdg-desktop-portal.service` at 25.3
    seconds after boot, so the first app started after logging in sat there
    with no window on screen for the whole of it. Hence: the proxy is created
    asynchronously, with `DO_NOT_AUTO_START` so it never starts the service
    itself, and the value is read asynchronously too. Until an answer arrives
    the one taken at startup stands, and when it arrives the watchers are told
    and the theme follows. An app's first frame never waits on a bus.
    """

    PORTAL = ("org.freedesktop.portal.Desktop",
              "/org/freedesktop/portal/desktop",
              "org.freedesktop.portal.Settings")
    NAMESPACE = "org.freedesktop.appearance"
    KEY = "color-scheme"

    def __init__(self):
        # Taken before `install()` can force anything, so it is still the
        # desktop's own answer however the portal turns out.
        self._dark = self._initial()
        self._proxy = None
        self._listeners: list = []
        self._connect()

    @staticmethod
    def _initial() -> bool:
        try:
            return bool(Adw.StyleManager.get_default().get_dark())
        except Exception:
            return True

    def _connect(self) -> None:
        """Ask the portal, without starting it and without waiting for it."""
        try:
            from gi.repository import Gio

            Gio.DBusProxy.new_for_bus(
                Gio.BusType.SESSION,
                Gio.DBusProxyFlags.DO_NOT_AUTO_START
                | Gio.DBusProxyFlags.DO_NOT_LOAD_PROPERTIES,
                None, *self.PORTAL, None, self._proxy_ready, None)
        except Exception:
            pass          # no Gio, no bus, no session: the startup value stands

    def _proxy_ready(self, _source, result, _data) -> None:
        from gi.repository import Gio, GLib

        try:
            self._proxy = Gio.DBusProxy.new_for_bus_finish(result)
        except Exception:
            return
        # DO_NOT_AUTO_START means a portal that is not up yet simply has no
        # owner. It will have one later, so watch for that rather than
        # giving up — this is what makes the answer arrive on its own a
        # few seconds into the session instead of never.
        self._proxy.connect("g-signal", self._on_signal)
        self._proxy.connect("notify::g-name-owner",
                            lambda *_a: self._read())
        self._read()

    def _read(self) -> None:
        if self._proxy is None or not self._proxy.get_name_owner():
            return
        from gi.repository import Gio, GLib

        def answered(proxy, result, method):
            try:
                value = proxy.call_finish(result)
            except Exception:
                if method == "ReadOne":
                    ask("Read")       # an older portal has only Read
                return
            self._take(value.unpack())

        def ask(method):
            self._proxy.call(
                method, GLib.Variant("(ss)", (self.NAMESPACE, self.KEY)),
                Gio.DBusCallFlags.NO_AUTO_START, 5000, None, answered, method)

        ask("ReadOne")

    def _take(self, raw) -> bool:
        """A portal answer, which arrives wrapped in a variant or two."""
        while isinstance(raw, tuple) and raw:
            raw = raw[0]
        if hasattr(raw, "unpack"):
            raw = raw.unpack()
        try:
            number = int(raw)
        except (TypeError, ValueError):
            return self._dark
        before = self._dark
        if number in (1, 2):
            self._dark = number == 1
        if self._dark != before:
            self._announce()
        return self._dark

    def _announce(self) -> None:
        for callback in list(self._listeners):
            try:
                callback(self._dark)
            except Exception:
                pass

    def _on_signal(self, _proxy, _sender, signal, params) -> None:
        if signal != "SettingChanged":
            return
        try:
            namespace, key, value = params.unpack()
        except Exception:
            return
        if namespace == self.NAMESPACE and key == self.KEY:
            self._take(value)

    @property
    def dark(self) -> bool:
        return self._dark

    def watch(self, callback) -> None:
        """Call `callback(dark)` whenever the desktop changes its mind.

        No handler id to give back, because this object is a process-wide
        singleton and a window that connects to one has to disconnect: use
        `unwatch` from the window's `destroy`, or the closure keeps the
        window alive and the next change is delivered to every window the
        process has ever opened.
        """
        self._listeners.append(callback)

    def unwatch(self, callback) -> None:
        if callback in self._listeners:
            self._listeners.remove(callback)


_system: SystemScheme | None = None


def system() -> SystemScheme:
    """The one `SystemScheme` for this process, made on first use."""
    global _system
    if _system is None:
        _system = SystemScheme()
    return _system


def system_is_dark() -> bool:
    """What the desktop is asking for, honestly, even after a force."""
    return system().dark


# ---------------------------------------------------------- the widgets ----

# Two pieces of the look CSS cannot do, so they live here beside the sheet
# that does the rest.
#
# *Clipping.* GTK's CSS has no `overflow`, so a rounded container does not
# round what its children paint: a selected row at the bottom of a list squares
# off the corner of the window, and the whole illusion of one slab of glass
# goes with it. `Slab` rounds its children by pushing a rounded clip in its own
# snapshot, which is both the correct way and a single GSK node.
#
# *The highlight that follows the pointer.* Real glass has a bright spot where
# the light is, and it moves when you move. `Sheen` paints one very faint
# radial gradient under everything else and follows the pointer, which is what
# makes the surface read as a material rather than as a flat translucent
# rectangle. One gradient node, redrawn only when the pointer has moved far
# enough to be worth a frame, and switched off while the window has no focus —
# a highlight animating in a window nobody is using is a waste of a GPU.

gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")

from gi.repository import Graphene, Gsk  # noqa: E402


# ------------------------------------------------------------- the light ----

# How big the aurora texture is drawn before being scaled up to fill a slab.
# The layer is four very soft blobs and a diagonal sheen; there is nothing in
# it with an edge, so a quarter-megapixel is already more than the eye can
# use and the upscale costs nothing.
AURORA_WIDTH = 256
AURORA_HEIGHT = 160

_aurora_cache: dict[tuple, "Gdk.Texture"] = {}


def aurora_texture(scheme: theming.Theme, translucency: float = 1.0):
    """The sheen and the drifting light, baked once into a small texture.

    This used to be five gradients in the slab's CSS `background-image`, and
    the reason it is not any more is worth the paragraph. GSK re-blends every
    layer of a `background-image` over the whole box on every repaint, and
    the slab is the largest surface in the window — so the cost was paid
    again on every keystroke, scroll and popover that damaged it. Measured
    here at 1100x700: a flat slab 6.3 ms a frame, the two sheen gradients
    took it to 23.2, and each of the three aurora blobs added about 13 more.
    62 ms for the whole layer, for something nobody is supposed to notice.

    Drawn into a texture it is one scaled blit, and because every stop in it
    is soft the 256 px original is indistinguishable from the real thing at
    any window size. Cairo rather than GSK, so this needs no renderer and can
    be built before the window is realised; cached per theme, because a theme
    change is the only thing that invalidates it.

    Returns None when Cairo is missing, and the caller simply draws no light.
    """
    key = (scheme.key, round(float(translucency), 2))
    if key in _aurora_cache:
        return _aurora_cache[key]
    try:
        import cairo
    except ImportError:
        return None

    from gi.repository import GLib

    width, height = AURORA_WIDTH, AURORA_HEIGHT
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    context = cairo.Context(surface)

    # Low, and lower than it looks like it should be. A warm hue reads far
    # stronger than a cold one at the same alpha — an early cut had the
    # ochres at 0.30 and turned a near-black pane the colour of milky coffee
    # — and this is the background of every empty area in the window, so it
    # has to be something you notice only when it is taken away.
    glow = 0.22 if scheme.dark else 0.14
    hues = (list(scheme.aurora) * 4)[:4]
    # Placed off-centre and off-edge so that none of them has a visible
    # middle, and wide enough to overlap, which is what makes one field of
    # colour rather than three spots.
    spots = ((0.12, 0.08), (0.88, 0.26), (0.46, 1.04), (0.72, 0.72))
    for hue, (fx, fy) in zip(hues, spots):
        red, green, blue = theming.rgb(hue)
        cx, cy = fx * width, fy * height
        radius = 0.68 * width
        gradient = cairo.RadialGradient(cx, cy, 0.0, cx, cy, radius)
        gradient.add_color_stop_rgba(0.0, red, green, blue, glow)
        gradient.add_color_stop_rgba(1.0, red, green, blue, 0.0)
        context.set_source(gradient)
        context.paint()

    # The sheen: a wide diagonal highlight as if the light sat above and to
    # the left, and a one-pixel lift along the very top edge where a real
    # edge would catch it. The edge is drawn in device pixels rather than
    # scaled, so it is added by the caller, not baked in here.
    sheen = (0.075 if scheme.dark else 0.34) * scheme.rim
    diagonal = cairo.LinearGradient(0, 0, width * 0.75, height * 0.75)
    diagonal.add_color_stop_rgba(0.0, 1, 1, 1, sheen)
    diagonal.add_color_stop_rgba(0.42, 1, 1, 1, 0.0)
    context.set_source(diagonal)
    context.paint()

    surface.flush()
    texture = Gdk.MemoryTexture.new(
        width, height, Gdk.MemoryFormat.B8G8R8A8_PREMULTIPLIED,
        GLib.Bytes.new(bytes(surface.get_data())), surface.get_stride())
    _aurora_cache[key] = texture
    return texture


def forget_aurora() -> None:
    """Drop the cached textures. For a settings change that retunes them."""
    _aurora_cache.clear()


class Slab(Gtk.Box):
    """The window's one pane of glass: rounded, and rounded all the way down.

    The radius itself lives in the stylesheet, which is also what paints the
    background, so it is set in one place. What this adds is the clip:
    `Gtk.Overflow.HIDDEN` honours the CSS corner radius, so a selected row at
    the bottom of a list stops squaring off the corner.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.add_css_class("slab")
        self.set_overflow(Gtk.Overflow.HIDDEN)
        self._aurora = None

    def set_light(self, scheme, translucency: float = 1.0,
                  enabled: bool = True) -> None:
        """Which theme's light to draw behind the content, or none at all."""
        self._aurora = (aurora_texture(scheme, translucency) if enabled
                        else None)
        self.queue_draw()

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        """The light first, then whatever the box holds.

        GTK has already drawn the CSS background and border by the time this
        runs, so the texture lands on the slab's own colour and under every
        child — which is where light coming through glass belongs.
        """
        texture = self._aurora
        if texture is not None:
            width, height = self.get_width(), self.get_height()
            if width > 0 and height > 0:
                snapshot.append_scaled_texture(
                    texture, Gsk.ScalingFilter.LINEAR,
                    Graphene.Rect().init(0, 0, width, height))
        Gtk.Box.do_snapshot(self, snapshot)

    def set_square(self, square: bool) -> None:
        """Corners off, for a window that covers the whole screen.

        A rounded corner against the edge of the display is a transparent
        notch showing the wallpaper, which reads as a bug rather than a style.
        Tiled in a column it reads as intended, so only the maximised and
        fullscreen cases square themselves off.
        """
        if square:
            self.add_css_class("squared")
        else:
            self.remove_css_class("squared")


class Sheen(Gtk.Widget):
    """A soft highlight under the content, where the pointer is.

    Sized to the widget it is overlaid on and drawn with one radial gradient.
    `strength` is deliberately tiny: this is meant to be noticed only when it
    is taken away.
    """

    def __init__(self, strength: float = 0.055, radius: int = 420):
        super().__init__()
        self._strength = strength
        self._radius = radius
        self._x = -1.0
        self._y = -1.0
        self._active = True
        self.set_can_target(False)         # never eats a click
        self.set_can_focus(False)

    def set_strength(self, strength: float) -> None:
        self._strength = max(0.0, min(0.5, strength))
        self.queue_draw()

    def set_active(self, active: bool) -> None:
        if active != self._active:
            self._active = active
            self.queue_draw()

    def track(self, widget: Gtk.Widget) -> None:
        """Follow the pointer over `widget` — normally the window.

        The controller goes on the other widget rather than on this one,
        because this one cannot be a target: it has to be transparent to
        clicks, and a widget that cannot be targeted gets no motion events of
        its own.
        """
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        motion.connect("leave", self._on_leave)
        widget.add_controller(motion)
        self._source = widget

    def _on_motion(self, controller, x: float, y: float) -> None:
        widget = controller.get_widget()
        # The highlight spans the whole window, so every move of it repaints
        # the whole window — under an open dialog too, where it cannot even
        # be seen. Moving the pointer round a preferences dialog was a
        # full-window frame every twelve pixels.
        visible_dialog = getattr(widget, "get_visible_dialog", None)
        if callable(visible_dialog) and visible_dialog() is not None:
            if self._x >= 0:
                self._on_leave(controller)
            return
        point = Graphene.Point().init(x, y)
        ok, local = widget.compute_point(self, point)
        if not ok:
            return
        # A pixel of pointer movement is not worth a frame; a dozen is.
        if abs(local.x - self._x) < 8 and abs(local.y - self._y) < 8:
            return
        self._x, self._y = local.x, local.y
        self.queue_draw()

    def _on_leave(self, _controller) -> None:
        self._x = self._y = -1.0
        self.queue_draw()

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        if not self._active or self._strength <= 0 or self._x < 0:
            return
        width = self.get_width()
        height = self.get_height()
        if width <= 0 or height <= 0:
            return
        radius = float(self._radius)
        bounds = Graphene.Rect().init(self._x - radius, self._y - radius,
                                      radius * 2, radius * 2)
        centre = Graphene.Point().init(self._x, self._y)
        bright = Gdk.RGBA()
        bright.red = bright.green = bright.blue = 1.0
        bright.alpha = self._strength
        clear = Gdk.RGBA()
        clear.red = clear.green = clear.blue = 1.0
        clear.alpha = 0.0
        snapshot.append_radial_gradient(
            bounds, centre, radius, radius, 0.0, 1.0,
            [_stop(0.0, bright),
             _stop(0.55, _fade(bright, 0.35)),
             _stop(1.0, clear)])


def _stop(offset: float, colour: Gdk.RGBA) -> Gsk.ColorStop:
    """One gradient stop.

    Built field by field rather than as `Gsk.ColorStop(offset=…, color=…)`:
    it is a plain C struct with no constructor, so PyGObject takes no keywords
    for it and the keyword form raises a TypeError — inside a `do_snapshot`,
    which swallows it into a warning on stderr and draws nothing. The
    highlight was silently absent for exactly that reason.
    """
    stop = Gsk.ColorStop()
    stop.offset = offset
    stop.color = colour
    return stop


def _fade(colour: Gdk.RGBA, factor: float) -> Gdk.RGBA:
    out = Gdk.RGBA()
    out.red, out.green, out.blue = colour.red, colour.green, colour.blue
    out.alpha = colour.alpha * factor
    return out


def rounded_clip(snapshot: Gtk.Snapshot, width: float, height: float,
                 radius: float) -> None:
    """Push a rounded clip of this size. For the other widgets that need one."""
    bounds = Graphene.Rect().init(0, 0, width, height)
    rounded = Gsk.RoundedRect()
    rounded.init_from_rect(bounds, radius)
    snapshot.push_rounded_clip(rounded)


def tint(hex_colour: str, alpha: float) -> Gdk.RGBA:
    """A Gdk.RGBA from `#rrggbb` and an alpha, for the widgets that draw."""
    colour = Gdk.RGBA()
    if not colour.parse(hex_colour):
        colour.parse("#808080")
    colour.alpha = alpha
    return colour


# ------------------------------------------------------------- the glass ----

class Glazing:
    """One window's worth of house style, wired up and kept in step.

    Every app needs the same six or seven lines at startup — work out which
    theme is in force, install the sheet, put a sheen on the slab, follow the
    desktop if it is being followed — and the same three on every settings
    change. They are here once rather than in each `window.py`, so that an app
    joining the house style is a `Glazing(self, config)` and not a rewrite.

    `config` is whichever mapping the app already keeps its settings in, read
    through `get` so a dict and a `Config` both work. The keys it looks for
    are the house ones — `theme`, `follow_system`, `dark_theme`,
    `light_theme`, `translucency`, `glass`, `glass_blur`, `rounding` — and any
    the app has not got fall back to the house default rather than raising,
    which is what lets an app adopt them one at a time.
    """

    def __init__(self, window, config, extra: str = "", app_class: str = ""):
        self.window = window
        self.config = config
        self.extra = extra
        self.scheme = theming.get(self.wanted_key())
        self._watching = None
        window.add_css_class("house")
        if app_class:
            window.add_css_class(app_class)
        self.apply()
        self.follow()

    # -- which theme ------------------------------------------------------

    def _get(self, key, default=None):
        return setting(self.config, key, default)

    def wanted_key(self) -> str:
        return wanted_key(self.config)

    # -- applying ---------------------------------------------------------

    def apply(self) -> theming.Theme:
        """Install the sheet for whichever theme is now in force."""
        self.scheme = theming.get(self.wanted_key())
        glass = bool(self._get("glass", True))
        install(self.scheme, self.extra,
                translucency=float(self._get("translucency", 1.0)),
                window_radius=int(self._get("rounding", WINDOW_RADIUS)),
                blur=int(self._get("glass_blur", 0)) if glass else 0,
                glass=glass,
                display=self.window.get_display())
        return self.scheme

    def follow(self) -> None:
        """Change with the desktop, while `follow_system` is on.

        The listener is held and dropped on the window's `destroy`: the
        `SystemScheme` is process-wide, so a closure left on it keeps a
        destroyed window alive and delivers the next change to every window
        the process has ever opened.
        """
        if self._watching is not None:
            return
        self._watching = lambda _dark: (self.apply(), self.relight())
        system().watch(self._watching)
        self.window.connect("destroy", lambda *_: self.stop())

    def stop(self) -> None:
        if self._watching is not None:
            system().unwatch(self._watching)
            self._watching = None

    # -- the slab ---------------------------------------------------------

    def wrap(self, content: Gtk.Widget, strength: float = 0.05) -> Slab:
        """Put `content` on a slab of glass and make it the window's content.

        The whole assembly, because every app wants the same one and getting
        it slightly different in each is how a house style stops being one:

            AdwApplicationWindow        (paints nothing; transparent)
              Slab                      (the rounded translucent pane, whose
                                         CSS padding is the gap round the
                                         content, and which clips to its own
                                         corners)
                GtkOverlay
                  content
                  Sheen                 (the highlight under the pointer)

        Afterwards `self.slab`, `self.overlay` and `self.sheen` are the three
        pieces, so an app that wants to float something of its own — a command
        palette, a progress strip — adds it to the overlay and it lands above
        the sheen and inside the clip.

        The slab squares its own corners off while the window covers the
        screen: a rounded corner against the edge of a display is a
        transparent notch showing the wallpaper, which reads as a bug rather
        than as a style. Tiled in a column it reads as intended, so only
        maximised and fullscreen are squared.
        """
        self.slab = Slab(orientation=Gtk.Orientation.VERTICAL)
        # A sheet rather than a frame: the content sits straight on this one,
        # so it is the only painted surface in the window and has to be
        # strong enough to read against a wallpaper. See `.slab.sheet`.
        self.slab.add_css_class("sheet")
        self.slab.set_light(self.scheme, float(self._get("translucency", 1.0)),
                            bool(self._get("glass", True)))
        self.overlay = Gtk.Overlay()
        content.set_vexpand(True)
        self.overlay.set_child(content)

        self.sheen = Sheen(strength if self._get("glass", True) else 0.0)
        self.overlay.add_overlay(self.sheen)
        self.sheen.track(self.window)
        self.window.connect(
            "notify::is-active",
            lambda *_: self.sheen.set_active(self.window.is_active()))

        self.slab.append(self.overlay)
        setter = getattr(self.window, "set_content", None)
        if callable(setter):
            setter(self.slab)
        else:                            # a plain Gtk.Window
            self.window.set_child(self.slab)

        for signal in ("notify::maximized", "notify::fullscreened"):
            self.window.connect(signal, lambda *_: self.square())
        self.square()
        return self.slab

    def square(self) -> None:
        """Corners on or off, from the window's current state."""
        slab = getattr(self, "slab", None)
        if slab is None:
            return
        slab.set_square(bool(self.window.is_maximized()
                             or self.window.is_fullscreen()))

    def relight(self) -> None:
        """Hand the slab the current theme's light again, after a change."""
        slab = getattr(self, "slab", None)
        if slab is not None:
            slab.set_light(self.scheme, float(self._get("translucency", 1.0)),
                           bool(self._get("glass", True)))

    def restyle(self) -> None:
        """Reinstall the sheet and push the glass settings back into the slab.

        What an app calls from its preferences when anything in the Look page
        changes, so that `apply` stays the one that only touches colour.
        """
        self.apply()
        self.relight()
        sheen = getattr(self, "sheen", None)
        if sheen is not None:
            sheen.set_strength(0.05 if self._get("glass", True) else 0.0)


# ------------------------------------------------------- the theme rows ----

THEME_BLURB = (
    "The same eight themes in every app here, so that choosing one chooses it "
    "everywhere. Porcelain is the light one the house is tuned for and "
    "Nocturne the dark one drawn to sit beside it; the rest are there for the "
    "rooms Porcelain is wrong in."
)


def theme_group(config, apply, title: str = "Theme",
                description: str = THEME_BLURB):
    """The house theme rows, as one `Adw.PreferencesGroup`.

    Every app wants the same four: follow the desktop or not, which theme when
    not, and which pair when so. They are here once rather than written out
    eight times, which is also what stops one app's list drifting out of order
    from the next one's.

    `config` is written through whichever way it supports — a `set()` that
    validates and saves, if it has one, and otherwise an item assignment and a
    `save()` — and `apply()` is called after every change. For an app using
    `Glazing` that is `glazing.apply`; for one that has more to restyle than a
    stylesheet it is the window's own method.

    The two halves are shown and hidden rather than greyed, because a disabled
    combo still reads as a thing you are meant to be able to use and a hidden
    one reads as a question that does not apply. The dark and light rows offer
    only the themes that are dark and light respectively: "use this one in a
    light session" with a black theme in the list is an invitation to make the
    one mistake the setting exists to prevent.
    """
    group = Adw.PreferencesGroup(title=title, description=description)

    def changed(key, value):
        setter = getattr(config, "set", None)
        if callable(setter):
            if setter(key, value) is False:
                return
        else:
            config[key] = value
            save = getattr(config, "save", None)
            if callable(save):
                save()
        apply()

    follow = Adw.SwitchRow(
        title="Follow the desktop",
        subtitle="Use the light theme in a light session and the dark one in "
                 "a dark one",
        active=bool(config["follow_system"]))
    group.add(follow)

    def combo(key, row_title, row_subtitle, schemes):
        names = [scheme.name for scheme in schemes]
        keys = [scheme.key for scheme in schemes]
        current = theming.ALIASES.get(config[key], config[key])
        row = Adw.ComboRow(title=row_title, model=Gtk.StringList.new(names))
        if current in keys:
            row.set_selected(keys.index(current))
        row.set_subtitle(row_subtitle or theming.get(current).blurb)

        def picked(widget, _param):
            chosen = keys[widget.get_selected()]
            row.set_subtitle(row_subtitle or theming.get(chosen).blurb)
            changed(key, chosen)

        row.connect("notify::selected", picked)
        group.add(row)
        return row

    everything = theming.ordered()
    one = combo("theme", "Theme", "", everything)
    dark = combo("dark_theme", "When dark", "The one for a dark session",
                 [s for s in everything if s.dark])
    light = combo("light_theme", "When light", "The one for a light session",
                  [s for s in everything if not s.dark])

    def shown(*_args):
        following = follow.get_active()
        one.set_visible(not following)
        dark.set_visible(following)
        light.set_visible(following)

    def toggled(row, _param):
        changed("follow_system", row.get_active())
        shown()

    follow.connect("notify::active", toggled)
    shown()
    return group


GLASS_BLURB = (
    "The window is a single translucent pane with the interface laid on it. "
    "None of this comes from the compositor — there is no backdrop-blur "
    "protocol an application can ask a Wayland compositor for — so it is "
    "painted here instead, which is why it looks the same over a full-screen "
    "browser as over the desktop."
)


def glass_group(config, restyle, title: str = "Glass",
                description: str = GLASS_BLURB):
    """The house glass rows: how much light, how far it blurs, how round.

    `restyle` is called after each change — `Glazing.restyle` for an app that
    uses one, which reinstalls the sheet and pushes the settings back into the
    slab.
    """
    group = Adw.PreferencesGroup(title=title, description=description)

    def changed(key, value):
        setter = getattr(config, "set", None)
        if callable(setter):
            if setter(key, value) is False:
                return
        else:
            config[key] = theming.clamp(key, value)
            save = getattr(config, "save", None)
            if callable(save):
                save()
        restyle()

    def switch(key, row_title, subtitle):
        row = Adw.SwitchRow(title=row_title, subtitle=subtitle,
                            active=bool(setting(config, key)))
        row.connect("notify::active",
                    lambda r, _p: changed(key, r.get_active()))
        group.add(row)
        return row

    def spin(key, row_title, subtitle, low, high, step=1):
        row = Adw.SpinRow.new_with_range(low, high, step)
        row.set_title(row_title)
        row.set_subtitle(subtitle)
        row.set_value(int(setting(config, key)))
        row.connect("notify::value",
                    lambda r, _p: changed(key, int(r.get_value())))
        group.add(row)
        return row

    switch("glass", "Painted glass",
           "Off, every surface is flat. The cheapest of these settings by a "
           "long way.")

    low, high = theming.RANGES["translucency"]
    row = Adw.ActionRow(
        title="Translucency",
        subtitle="Never taken literally: a window you cannot read is worse "
                 "than one that is not quite as pretty.")
    scale = Gtk.Scale(
        adjustment=Gtk.Adjustment(lower=low, upper=high, step_increment=0.01,
                                  page_increment=0.05,
                                  value=float(setting(config, "translucency"))),
        draw_value=True, digits=2)
    scale.set_size_request(190, -1)
    scale.set_valign(Gtk.Align.CENTER)
    scale.connect("value-changed",
                  lambda s: changed("translucency", round(s.get_value(), 2)))
    row.add_suffix(scale)
    group.add(row)

    spin("glass_blur", "Blur under a floating panel",
         "How far a palette or a dialog blurs what is behind it. 0 turns it "
         "off, and the panel goes back to being nearly solid so the text "
         "under it still does not compete.",
         *theming.RANGES["glass_blur"], 2)
    spin("rounding", "Corner radius",
         "Of the window itself. The cards inside it follow, a little tighter, "
         "so the two curves do not fight.",
         *theming.RANGES["rounding"])
    switch("animations", "Animations",
           "The highlight that follows the pointer, and the transitions.")
    return group


def look_page(config, restyle, icon_name="applications-graphics-symbolic"):
    """The whole house Look page: the themes, then the glass.

    One call per app, so that the page is in the same order with the same
    wording everywhere. An app with more to say about its own appearance adds
    its groups to the page this returns.
    """
    page = Adw.PreferencesPage(title="Look", icon_name=icon_name)
    page.add(theme_group(config, restyle))
    page.add(glass_group(config, restyle))
    return page


# --------------------------------------------------------- the geometry ----

class Geometry:
    """Remembering the size a window was closed at, and coming back at it.

    One implementation for all nine apps, because it had nine and they had
    drifted — and because the rule in them was wrong in a way that was
    invisible on a stacking desktop and total on this one.

    **The size is always remembered.** The old rule was "a tiling compositor
    decides the geometry, so the number it gives back says nothing about what
    the user wants — don't save it". That reads well and on a machine where
    niri *is* the desktop it means the size is never saved at all, and every
    window opens at its built-in default for ever. The size niri gave a window
    is still the size that window was; saving it costs nothing while tiled,
    because a tiled window is given its column's width whatever it asks for,
    and is exactly right the moment the window is floated, or the same
    checkout is run on a stacking desktop or on Windows.

    **The maximise is not**, and that asymmetry is the real content of this
    class. niri honours `set_maximized` by handing the window the whole
    output, which overrides the column widths the user chose — and
    `is_maximized()` then reports true, so the flag re-saves itself and the
    window takes the whole screen on every start for ever. So a remembered
    maximise is applied before the map (the only way to come up maximised
    without a frame at the wrong size first), and handed back once if the
    compositor turns out to be a tiling one.

    **Asking is the awkward part.** On Wayland the toplevel's capabilities are
    a placeholder until the compositor's first configure, which only arrives
    *after* the map, so the answer is taken in a `notify::capabilities`
    handler and taken once — a maximise the user asks for later is theirs.
    No MINIMIZE is the tell: niri reports 51 (edge constraints, inhibit
    shortcuts, maximize, fullscreen) and X11 reports 242.

    One thing this cannot do from inside the app: niri's own
    `default-column-width` wins over the size a window asks for. With
    `proportion 0.5` set globally every window opens at half the screen
    however well it remembers. The matching `window-rule` with an empty
    `default-column-width` is what lets the app's own answer through, and it
    lives in the compositor's config, not here.
    """

    #: Below this a saved size is treated as rubbish rather than honoured.
    FLOOR = 200

    def __init__(self, window, config, prefix: str = "window",
                 minimum: tuple[int, int] | None = None):
        self.window = window
        self.config = config
        self.prefix = prefix
        self.tiling = False
        self._settled = False
        self._maximize_pending = False
        self._minimum = minimum

    # -- reading whichever shape of config the app keeps -------------------

    def _key(self, name: str) -> str:
        return f"{self.prefix}_{name}"

    def get(self, name: str, default):
        key = self._key(name)
        try:
            return self.config[key]
        except (KeyError, TypeError, IndexError):
            return getattr(self.config, key, default)

    def set(self, name: str, value) -> None:
        key = self._key(name)
        try:
            self.config[key] = value
        except TypeError:
            setattr(self.config, key, value)

    def _save_config(self) -> None:
        save = getattr(self.config, "save", None)
        if callable(save):
            save()

    # -- coming up --------------------------------------------------------

    def restore(self) -> None:
        """Ask for the remembered size, and the maximise if there was one.

        Called from the window's constructor, before it is presented: both
        have to be in place before the first map or the window is drawn once
        at the wrong size and then again at the right one.
        """
        width = int(self.get("width", 0) or 0)
        height = int(self.get("height", 0) or 0)
        if width >= self.FLOOR and height >= self.FLOOR:
            self.window.set_default_size(width, height)
        if self._minimum:
            self.window.set_size_request(*self._minimum)
        if self.get("maximized", False):
            self.window.maximize()
            self._maximize_pending = True
        self.window.connect("realize", lambda *_: self._on_realize())

    def _on_realize(self) -> None:
        surface = self.window.get_surface()
        if surface is None:
            return
        if hasattr(surface, "get_capabilities"):
            surface.connect("notify::capabilities", lambda *_: self._settle())
        self._settle()

    def _settle(self) -> None:
        """Decide once whether a tiling compositor is in charge."""
        if self._settled:
            return
        surface = self.window.get_surface()
        if surface is None or not hasattr(surface, "get_capabilities"):
            self._settled = True          # GTK before 4.20: assume stacking
            self._maximize_pending = False
            return
        try:
            capabilities = int(surface.get_capabilities())
        except (AttributeError, TypeError, ValueError):
            return
        if capabilities in (0, 3):
            return                        # still the placeholder — wait
        self._settled = True
        minimize = getattr(Gdk.ToplevelCapabilities, "MINIMIZE", None)
        self.tiling = bool(minimize is not None and not (capabilities & minimize))
        if not self.tiling:
            self._maximize_pending = False
            return
        # A tiling compositor. Hand the maximise back, once, and forget it was
        # ever asked for — left in the file it would flash the window to full
        # width on every start.
        if self._maximize_pending:
            self._maximize_pending = False
            if self.window.is_maximized():
                self.window.unmaximize()
        if self.get("maximized", False):
            self.set("maximized", False)
            self._save_config()

    # -- going down -------------------------------------------------------

    def save(self) -> None:
        """Write the size, and the maximise where it means anything.

        The *default* size rather than the current one: GTK keeps that at
        whatever the window was last time it was neither maximised nor
        fullscreen, which is exactly the size to come back at. It falls back
        to the live size for the case where GTK has not filled it in.
        """
        if not self.tiling:
            self.set("maximized", bool(self.window.is_maximized()))
            if self.window.is_maximized() or self.window.is_fullscreen():
                return
        elif self.window.is_fullscreen():
            return
        width, height = self.window.get_default_size()
        if width < self.FLOOR or height < self.FLOOR:
            width, height = self.window.get_width(), self.window.get_height()
        if width >= self.FLOOR and height >= self.FLOOR:
            self.set("width", int(width))
            self.set("height", int(height))
