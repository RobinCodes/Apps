"""The look: one stylesheet, generated from the settings.

Why generated rather than a static file. Three of the things that make this
app look the way it does are settings — how translucent the window is, how
far the glass blurs, how round the corners are — and GTK's CSS has no way to
be handed a number at runtime. So the sheet is a template, the numbers go in
when it is built, and changing one in the preferences rebuilds and reloads it.
Everything else about it is fixed.

How the glass is actually made, since "liquid glass" is four effects and not
one:

*Translucency.* The window's own background is removed and a single rounded
slab is drawn inside it at `translucency` alpha, so the desktop shows through
the whole app rather than through a hole in it.

*The three modules.* The slab is a frame rather than a sheet: the toolbar,
the places and the listing are three rounded cards laid on it with a gap
between them, and the slab shows through the gaps. Which means the cards
share one alpha and the slab is the sheer thing behind them — the depth is
in the gaps, not in giving each card a different strength. Trying it the
other way round is what made the toolbar read as a different material from
the list it sat on.

*Blur.* `backdrop-filter` blurs whatever a layer is drawn over — real blur,
done by GSK on the GPU. It only means anything where a layer floats over
content, so it is spent there: the command palette, in-window dialogs, the
collapsed sidebar, the progress sheet. A header bar in the layout's flow has
nothing behind it but the slab, and blurring a flat colour costs a full
offscreen pass to produce the same flat colour, so it does not get one.

*The rim.* Glass is visible at its edges. Every surface gets a hairline of
white at the top and a hairline of shadow at the bottom, through inset
box-shadows — one pixel each, which is what reads as a bevel rather than as
a border.

*The sheen.* A wide, very faint diagonal highlight, as if a light source sat
above and to the left. `glass.py` adds one more that follows the pointer.

Colours come from libadwaita's named palette — `@window_bg_color`,
`@accent_color` and the rest — so the whole sheet needs no second copy of
itself to change colour. What those names *mean* comes from the house theme:
`housestyle.palette()` restates the entire libadwaita palette in the chosen
theme's colours and is emitted in front of this sheet, so one list of eight
palettes drives every rule below and every widget nobody wrote a rule for.
The handful of places that need a tint rather than a colour use
`alpha(currentColor, x)`, which works in either because `currentColor` flips
with the text.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")

from gi.repository import Gdk, Gtk  # noqa: E402

from . import housestyle, theme as theming  # noqa: E402

# One provider, loaded once and reloaded in place. Adding a second provider
# per settings change would leave the old one still applying underneath.
_provider: Gtk.CssProvider | None = None

# The gap between the three modules, and between them and the edge of the
# slab. The window reads this to space the boxes; the sheet reads it for the
# slab's padding. One number, so a card can never sit a pixel off the others.
GAP = 7


TEMPLATE = """
/* ------------------------------------------------------------- window --- */

/* The window paints nothing. The slab inside it does, which is what lets the
   desktop through and what makes one rounded outline rather than several. */
window.hermione,
window.hermione > contents,
window.hermione .window-contents {
  background: none;
  background-color: transparent;
  box-shadow: none;
}

/* The slab is the frame the three modules are laid on, so it carries the
   padding that becomes the gap around them as well as the rounded outline,
   the rim and the clip. It is the sheerest surface in the window: the cards
   are what hold text, the slab is what shows between them. */
.slab {
  border-radius: %(radius)dpx;
  padding: %(gap)dpx;
  background-color: alpha(%(chrome)s, %(slab_alpha).3f);
  /* The diagonal sheen and the aurora are drawn by `glass.Slab` from a
     cached texture rather than listed here. Five gradients in a CSS
     `background-image` are five full-slab blends on every repaint —
     measured at 62 ms a frame against 6 for a flat slab — and this is the
     biggest surface in the window. See housestyle.aurora_texture.
     What stays is the one-pixel lift along the very top edge, which is a
     device pixel and must not be scaled with the rest. */
  background-image:
    linear-gradient(to bottom,
      alpha(%(sheen)s, %(sheen_edge).3f) 0px,
      alpha(%(sheen)s, 0.0) 2px);
  box-shadow:
    inset 0 1px 0 alpha(%(sheen)s, %(rim_top).3f),
    inset 0 0 0 1px alpha(%(sheen)s, %(rim).3f),
    inset 0 -1px 0 alpha(%(shadow)s, %(rim_bottom).3f);
  /* GTK has no `overflow`, so the clip that keeps a selected row from
     squaring off the bottom corner is done in glass.Slab's snapshot. */
}

/* Maximised or tiled, a rounded corner is a transparent notch against the
   next window, so the slab squares itself off. */
/* `.squared` is put on by the window itself: niri only reports the tiled
   state when prefer-no-csd is set, so the CSS state alone cannot be relied
   on to know whether a corner is against the edge of the screen. */
window.maximized .slab,
window.fullscreen .slab,
.slab.squared {
  border-radius: 0;
}

/* libadwaita gives its split view's panes and its toolbar view's bars solid
   backgrounds of their own, behind anything we put there. Left alone, the
   sidebar comes out completely opaque however translucent its own tint is,
   and the pane's toolbar ends up the *least* see-through surface in the
   window — more solid than the file list it sits above, which reads as
   upside down. So they are cleared, and every surface below sets its own. */
/* `.plain-bars` is put on the two toolbar views that are part of the window
   itself. Scoped to those rather than to `toolbarview`, because libadwaita
   builds dialogs and preferences pages out of the same widget and clearing
   their backgrounds too would leave them see-through. */
overlay-split-view > widget.sidebar-pane,
overlay-split-view > widget.content-pane,
.plain-bars,
.plain-bars > revealer.top-bar,
.plain-bars > revealer.bottom-bar,
.plain-bars > revealer > windowhandle,
.plain-bars > revealer > windowhandle > box {
  background: none;
  background-color: transparent;
}

/* And the hairline libadwaita rules between the two halves of the split
   view, which with the cards apart lands in the middle of the gap as a line
   drawn down the wallpaper. The gap is the edge now. */
overlay-split-view > widget.sidebar-pane,
overlay-split-view > widget.content-pane {
  box-shadow: none;
  border: none;
}

/* The listing's ground is the card it sits in, below. */
.canvas { background: none; background-color: transparent; }

/* ------------------------------------------------------------- modules --- */

/* Three cards on the slab: the toolbar across the top, the places and the
   listing side by side under it. Each is a surface with a gap around it, so
   the slab shows between them — which is what stops the window reading as
   one sheet with lines ruled on it, and what lets every corner in the
   window be a round one.

   They share `card_alpha`. Giving each a strength of its own is the obvious
   move and it is what made the toolbar look like a different material from
   the list: three tints of the same glass meeting at a hard edge reads as a
   mistake, not as depth. The depth is the gap. */
.module {
  border-radius: %(module_radius)dpx;
  box-shadow:
    inset 0 1px 0 alpha(%(sheen)s, %(rim_top).3f),
    inset 0 0 0 1px alpha(%(sheen)s, %(rim).3f),
    0 1px 4px alpha(%(shadow)s, %(module_shadow).3f);
}

/* The listing's card is the one holding a wall of text, so it is the one
   that takes the view colour rather than the window's. */
.module.files-card {
  background-color: alpha(@view_bg_color, %(card_alpha).3f);
}

/* A strip inside a card — column headers, the status line, the filter bar,
   a split pane's breadcrumbs. Tinted with light rather than with a second
   alpha of the window colour: on a translucent card a darker patch reads as
   a hole, which is the same rule the controls on the toolbar follow. */
.inner-strip {
  background-color: alpha(%(sheen)s, %(inner_tint).3f);
}

/* -------------------------------------------------------------- chrome --- */

/* The top module: one toolbar, the width of the window. */
.chrome {
  background-color: alpha(%(chrome)s, %(card_alpha).3f);
  background-image: linear-gradient(to bottom,
    alpha(%(sheen)s, %(chrome_sheen).3f),
    alpha(%(sheen)s, 0.0) 60%%);
  border: none;
}

headerbar.chrome,
windowhandle > box.chrome {
  min-height: 42px;
  padding: 0 8px;
}

/* The chooser's question, as the module along the bottom — the same card as
   the toolbar, which is what makes it read as the other end of the window
   rather than as something stuck to the listing. */
.question-bar {
  min-height: 44px;
  padding: 6px 10px;
}

/* What the asking program called the dialog. Quiet: it is a caption on the
   bar, not a heading — the file list is what the eye should land on. */
.question-title {
  font-weight: 650;
  color: alpha(currentColor, 0.80);
  padding-right: 4px;
}

.question-bar button.suggested-action {
  min-height: 30px;
  padding: 2px 16px;
  font-weight: 600;
}

headerbar.chrome > windowhandle > box { margin: 0; }

/* The window's own buttons, made round and quiet. */
windowcontrols button {
  min-width: 22px;
  min-height: 22px;
  margin: 0 1px;
  border-radius: 999px;
}

.statusbar {
  min-height: 26px;
  padding: 1px 12px;
  font-size: 0.84em;
  box-shadow: inset 0 1px 0 alpha(%(sheen)s, %(rim).3f);
  background-color: alpha(%(sheen)s, %(inner_tint).3f);
}

.statusbar label { color: alpha(currentColor, 0.72); }
.statusbar .strong { color: alpha(currentColor, 0.95); font-weight: 600; }

/* The layers that really do float over content, and so really are blurred.
   Not a dialog, though: libadwaita puts `.floating` on every in-window
   Adw.Dialog, and a blur there re-renders the whole window behind it on
   every repaint — see the dialogs below. */
.floating:not(dialog) {
  background-color: alpha(%(chrome)s, %(float_alpha).3f);
  %(blur)s
  border-radius: %(panel_radius)dpx;
  box-shadow:
    inset 0 1px 0 alpha(%(sheen)s, %(rim_top_strong).3f),
    inset 0 0 0 1px alpha(%(sheen)s, %(rim).3f),
    0 16px 48px alpha(%(shadow)s, 0.40),
    0 2px 8px alpha(%(shadow)s, 0.20);
}

/* ------------------------------------------------------------- sidebar --- */

/* The places, as the left-hand module. No hairline down its right edge any
   more: the gap between the cards is the edge. */
.sidebar-glass {
  background-color: alpha(%(chrome)s, %(card_alpha).3f);
}

/* Collapsed, the sidebar slides over the list and blurs it. */
.sidebar-glass.overlay {
  %(blur)s
  background-color: alpha(%(chrome)s, %(float_alpha).3f);
}

.sidebar-glass list,
.sidebar-glass listview {
  background: none;
  padding: 2px 6px;
}

.place-row {
  border-radius: 9px;
  padding: 5px 8px;
  margin: 1px 0;
  min-height: 26px;
  transition: background-color 140ms %(ease)s, color 140ms %(ease)s;
}

.place-row:hover { background-color: alpha(currentColor, 0.07); }

.place-row.current {
  background-color: alpha(@accent_bg_color, 0.20);
  color: @accent_color;
  font-weight: 600;
}

.place-row.current image { color: @accent_color; }
.place-row image { color: alpha(currentColor, 0.72); }
.place-row .detail { font-size: 0.78em; color: alpha(currentColor, 0.50); }
.place-row.drop-into {
  background-color: alpha(@accent_bg_color, 0.30);
  box-shadow: inset 0 0 0 1px @accent_color;
}

.group-heading {
  font-size: 0.74em;
  font-weight: 700;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: alpha(currentColor, 0.42);
  padding: 10px 10px 3px 10px;
}

/* ---------------------------------------------------------------- rows --- */

/* The list. No grid lines, no alternating stripes: the only marks on it are
   the ones the user put there. */
columnview,
listview,
gridview {
  background: none;
  color: @view_fg_color;
}

columnview > listview > row,
listview > row {
  border-radius: 9px;
  margin: 0 6px;
  padding: 0;
  min-height: %(row_height)dpx;
  transition: background-color 120ms %(ease)s,
              box-shadow 120ms %(ease)s;
}

columnview > listview > row:hover,
listview > row:hover { background-color: alpha(currentColor, 0.055); }

columnview > listview > row:selected,
listview > row:selected,
gridview > child:selected {
  background-color: alpha(@accent_bg_color, 0.26);
  color: @view_fg_color;
  box-shadow: inset 0 0 0 1px alpha(@accent_color, 0.38);
}

columnview > listview > row:selected:hover,
listview > row:selected:hover {
  background-color: alpha(@accent_bg_color, 0.34);
}

/* Keyboard focus, drawn as a ring rather than a dotted outline. */
columnview > listview > row:focus-visible,
listview > row:focus-visible,
gridview > child:focus-visible {
  outline: none;
  box-shadow: inset 0 0 0 2px alpha(@accent_color, 0.75);
}

/* The column headers: a quiet strip, no buttons that look like buttons. */
columnview > header {
  background-color: alpha(%(sheen)s, %(inner_tint).3f);
  box-shadow: inset 0 -1px 0 alpha(%(sheen)s, %(rim).3f);
}

columnview > header > button {
  background: none;
  border: none;
  box-shadow: none;
  border-radius: 0;
  min-height: 24px;
  padding: 0 8px;
  font-size: 0.80em;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: alpha(currentColor, 0.48);
  transition: color 120ms %(ease)s, background-color 120ms %(ease)s;
}

columnview > header > button:hover {
  color: alpha(currentColor, 0.85);
  background-color: alpha(currentColor, 0.05);
}

.cell-name { font-weight: 450; }
.cell-dim { color: alpha(currentColor, 0.55); }
/* Tabular figures, so a column of sizes lines up on the decimal point
   without a monospace font doing the work. */
.numeric {
  font-feature-settings: "tnum";
  color: alpha(currentColor, 0.62);
  padding-right: 10px;
}
.monospace { font-family: monospace; }

.entry-icon { -gtk-icon-size: 16px; }
.entry-icon.dimmed { opacity: 0.55; }

/* A link, and a link to nothing. */
.is-link image { opacity: 0.80; }
.is-broken { color: @error_color; }
.is-hidden { opacity: 0.58; }

/* git's one letter, as a dot rather than a word. */
.vcs-dot {
  min-width: 6px;
  min-height: 6px;
  border-radius: 999px;
  margin: 0 4px;
}

/* ---------------------------------------------------------------- grid --- */

gridview { padding: 8px 10px; }

gridview > child {
  padding: 6px;
  margin: 3px;
  border-radius: 12px;
  transition: background-color 130ms %(ease)s, box-shadow 130ms %(ease)s;
}

gridview > child:hover { background-color: alpha(currentColor, 0.06); }

.tile-image {
  border-radius: 8px;
  background-color: alpha(%(field)s, %(field_alpha).3f);
  box-shadow:
    0 2px 6px alpha(%(shadow)s, 0.22),
    inset 0 0 0 1px alpha(%(sheen)s, 0.08);
}

.tile-label {
  font-size: 0.86em;
  margin-top: 5px;
}

.tile-label.selected { font-weight: 600; }

/* The slim line of breadcrumbs a pane shows when the tab is split in two.
   Quieter than the toolbar above it: it is a label, not a control strip. */
.path-strip {
  padding: 1px 4px 1px 8px;
  min-height: 26px;
  background-color: alpha(%(sheen)s, %(inner_tint).3f);
  box-shadow: inset 0 -1px 0 alpha(%(sheen)s, %(rim).3f);
}

/* The half of a split view the toolbar is pointing at. */
.path-strip.active {
  background-color: alpha(%(sheen)s, %(inner_tint_strong).3f);
  box-shadow: inset 0 -2px 0 alpha(@accent_color, 0.75);
}

.path-strip.active .crumb.last { color: @accent_color; }

/* --------------------------------------------------------- breadcrumbs --- */

.crumbs { padding: 0 2px; }
.crumb label { }

.crumb {
  min-height: 26px;
  padding: 2px 8px;
  border-radius: 8px;
  background: none;
  box-shadow: none;
  border: none;
  font-weight: 450;
  color: alpha(currentColor, 0.66);
  transition: background-color 120ms %(ease)s, color 120ms %(ease)s;
}

.crumb:hover {
  background-color: alpha(currentColor, 0.08);
  color: alpha(currentColor, 0.95);
}

.crumb.last { color: @window_fg_color; font-weight: 650; }
.crumb.drop-into {
  background-color: alpha(@accent_bg_color, 0.30);
  box-shadow: inset 0 0 0 1px @accent_color;
}
.crumb-sep { color: alpha(currentColor, 0.28); font-size: 0.8em; }

/* The path, typed rather than clicked. */
/* A field on glass is a raised piece of it, not a hole cut in it. Tinting
   these darker than the bar they sit on is what made the toolbar look
   inside out: the bar read as solid and the controls on it read as gaps. */
.path-entry {
  border-radius: 999px;
  min-height: 30px;
  padding: 2px 12px;
  background-color: alpha(%(field)s, %(field_alpha).3f);
  box-shadow: inset 0 1px 0 alpha(%(sheen)s, %(rim_top).3f),
              inset 0 0 0 1px alpha(%(sheen)s, %(rim).3f);
  border: none;
  font-family: monospace;
  font-size: 0.92em;
}

.path-entry:focus-within {
  background-color: alpha(%(field)s, %(field_strong).3f);
  box-shadow: inset 0 0 0 1px alpha(@accent_color, 0.60),
              0 0 0 3px alpha(@accent_color, 0.14);
}

/* ------------------------------------------------------------- buttons --- */

/* Flat, round, and only visible once the pointer is on them. */
/* A MenuButton is a widget wrapping a button, so a class put on it lands
   one node above the one that draws the frame — hence the extra selectors,
   without which the menus keep GTK's default raised look while everything
   beside them is flat. */
button.pill-flat,
menubutton.pill-flat > button,
.chrome button.flat,
.chrome menubutton > button,
.toolbutton {
  border-radius: 999px;
  min-width: 30px;
  min-height: 30px;
  padding: 2px 6px;
  background: none;
  border: none;
  box-shadow: none;
  color: alpha(currentColor, 0.78);
  transition: background-color 130ms %(ease)s,
              color 130ms %(ease)s,
              box-shadow 130ms %(ease)s;
}

button.pill-flat:hover,
menubutton.pill-flat > button:hover,
.chrome menubutton > button:hover,
.chrome button.flat:hover,
.toolbutton:hover {
  background-color: alpha(currentColor, 0.10);
  color: @window_fg_color;
  box-shadow: inset 0 1px 0 alpha(%(sheen)s, 0.10);
}

button.pill-flat:active,
menubutton.pill-flat > button:active,
.chrome button.flat:active,
.toolbutton:active { background-color: alpha(currentColor, 0.16); }

button.pill-flat:checked,
menubutton.pill-flat > button:checked,
.chrome button.flat:checked,
.toolbutton:checked {
  background-color: alpha(@accent_bg_color, 0.28);
  color: @accent_color;
  box-shadow: inset 0 0 0 1px alpha(@accent_color, 0.30);
}

button.pill-flat:disabled,
.toolbutton:disabled { opacity: 0.38; background: none; }

/* A group of buttons that reads as one piece of glass — raised out of the
   bar, by the same hairline-and-highlight as everything else. */
.button-cluster {
  border-radius: 999px;
  padding: 2px;
  background-color: alpha(%(field)s, %(cluster_alpha).3f);
  box-shadow: inset 0 1px 0 alpha(%(sheen)s, %(rim_top).3f),
              inset 0 0 0 1px alpha(%(sheen)s, %(rim).3f);
}

.button-cluster button { min-width: 26px; min-height: 26px; }

/* The one coloured button a dialog is allowed. */
button.suggested-action {
  border-radius: 999px;
  background-image: linear-gradient(to bottom,
    alpha(%(sheen)s, 0.14), alpha(%(sheen)s, 0.0));
  box-shadow: inset 0 1px 0 alpha(%(sheen)s, 0.18),
              0 1px 3px alpha(%(shadow)s, 0.25);
}

button.destructive-action { border-radius: 999px; }

/* ---------------------------------------------------------------- tabs --- */

tabbar {
  background: none;
  box-shadow: inset 0 -1px 0 alpha(%(sheen)s, %(rim).3f);
}

tabbar tabbox { padding: 3px 4px; }

tabbar tab {
  border-radius: 9px 9px 0 0;
  margin: 0 1px;
  padding: 2px 8px;
  min-height: 28px;
  background: none;
  box-shadow: none;
  color: alpha(currentColor, 0.60);
  transition: background-color 140ms %(ease)s, color 140ms %(ease)s;
}

tabbar tab:hover { background-color: alpha(currentColor, 0.07); }

tabbar tab:checked {
  background-color: alpha(%(sheen)s, %(tab_alpha).3f);
  color: @window_fg_color;
  box-shadow: inset 0 1px 0 alpha(%(sheen)s, 0.12);
}

tabbar tab button.flat { min-width: 20px; min-height: 20px; border-radius: 999px; }

/* ------------------------------------------------------------- popovers --- */

/* Separate surfaces, so there is nothing of ours behind them to blur — but
   the tint, the rim and the radius still belong to the same family. */
popover > arrow,
popover > contents {
  border-radius: %(panel_radius)dpx;
  background-color: alpha(%(popover)s, %(popover_alpha).3f);
  box-shadow:
    inset 0 1px 0 alpha(%(sheen)s, %(rim_top_strong).3f),
    inset 0 0 0 1px alpha(%(sheen)s, %(rim).3f),
    0 12px 36px alpha(%(shadow)s, 0.38);
  padding: 5px;
  %(popover_blur)s
}

popover.menu > contents { padding: 4px; }

popover.menu modelbutton {
  border-radius: 8px;
  min-height: 28px;
  padding: 3px 8px;
  transition: background-color 110ms %(ease)s;
}

popover.menu modelbutton:hover { background-color: alpha(currentColor, 0.09); }
popover.menu separator {
  background-color: alpha(currentColor, 0.10);
  margin: 4px 2px;
}

/* --------------------------------------------------------------- panels --- */

.panel {
  border-radius: %(panel_radius)dpx;
  background-color: alpha(%(sheen)s, %(panel_alpha).3f);
  box-shadow:
    inset 0 1px 0 alpha(%(sheen)s, %(rim_top).3f),
    inset 0 0 0 1px alpha(%(sheen)s, %(rim).3f);
}

/* The notes that link to this one, in Properties: rows on the panel's own
   glass rather than a list with a background of its own. */
.backlinks { background: none; }
.backlinks > row {
  border-radius: 8px;
  padding: 2px 6px;
  transition: background-color 110ms %(ease)s;
}
.backlinks > row:hover { background-color: alpha(currentColor, 0.07); }

.preview-pane {
  background-color: alpha(%(sheen)s, %(inner_tint).3f);
  box-shadow: inset 1px 0 0 alpha(%(sheen)s, %(rim).3f);
}

.preview-title { font-weight: 650; font-size: 1.02em; }
.preview-kind { font-size: 0.82em; color: alpha(currentColor, 0.55); }
.preview-image {
  border-radius: 10px;
  box-shadow: 0 4px 12px alpha(%(shadow)s, 0.28),
              inset 0 0 0 1px alpha(%(sheen)s, 0.08);
}
.preview-text {
  font-family: monospace;
  font-size: 0.86em;
  background: none;
  color: alpha(currentColor, 0.80);
}
.preview-text text { background: none; }

/* A label/value pair, as used by properties and the preview. */
.field-key {
  font-size: 0.80em;
  font-weight: 600;
  letter-spacing: 0.03em;
  text-transform: uppercase;
  color: alpha(currentColor, 0.44);
}
.field-value { font-size: 0.94em; }

/* ----------------------------------------------------------- ask claude --- */

/* Floats over the listing as the palette does, so it is the same glass. */
.ask-panel {
  border-radius: 18px;
  padding: 14px;
  background-color: alpha(%(chrome)s, %(palette_alpha).3f);
  %(palette_blur)s
  box-shadow:
    inset 0 1px 0 alpha(%(sheen)s, 0.16),
    inset 0 0 0 1px alpha(%(sheen)s, %(rim).3f),
    0 20px 60px alpha(%(shadow)s, 0.45);
}

/* ------------------------------------------------------------- palette --- */

/* The command palette: the one place the glass is the point. */
.palette {
  border-radius: 20px;
  background-color: alpha(%(chrome)s, %(palette_alpha).3f);
  %(palette_blur)s
  box-shadow:
    inset 0 1px 0 alpha(%(sheen)s, 0.16),
    inset 0 0 0 1px alpha(%(sheen)s, %(rim).3f),
    0 28px 80px alpha(%(shadow)s, 0.55),
    0 4px 16px alpha(%(shadow)s, 0.28);
}

.palette-entry {
  background: none;
  border: none;
  box-shadow: none;
  font-size: 1.18em;
  padding: 14px 18px;
  min-height: 30px;
}

.palette-entry:focus-within { outline: none; }

.palette-list { background: none; }

.palette-row {
  border-radius: 10px;
  padding: 7px 10px;
  margin: 1px 6px;
  transition: background-color 110ms %(ease)s;
}

/* The list box styles its own rows, so the soft selection has to be put on
   the row and Adwaita's solid one taken off it. */
.palette-list > row {
  background: none;
  border-radius: 10px;
  margin: 1px 6px;
  padding: 0;
}

.palette-list > row:selected {
  background-color: alpha(@accent_bg_color, 0.28);
  box-shadow: inset 0 0 0 1px alpha(@accent_color, 0.35);
  color: inherit;
}

.palette-list > row:selected label { color: @window_fg_color; }
.palette-row.active { background-color: alpha(@accent_bg_color, 0.26); }
.palette-row .shortcut {
  font-size: 0.78em;
  font-family: monospace;
  color: alpha(currentColor, 0.45);
  padding: 1px 6px;
  border-radius: 6px;
  background-color: alpha(currentColor, 0.07);
}
.palette-row .where { font-size: 0.80em; color: alpha(currentColor, 0.46); }

.palette-scrim { background-color: alpha(%(shadow)s, 0.28); }

/* -------------------------------------------------------------- dialogs --- */

/* Adw.Dialog is drawn inside the window, so a blur here would be real — and
   that is exactly why there is none. It re-renders and blurs the whole
   window behind the dialog on every repaint inside it, a hover included:
   5 ms a frame without, 23 ms with, measured on this machine's GPU. The
   window behind is dimmed by libadwaita anyway, so the glass is just made a
   little more solid instead, and libadwaita's own sheet carries the shadow. */
dialog.hermione-dialog > contents,
.dialog-glass {
  border-radius: 20px;
  background-color: alpha(%(chrome)s, %(dialog_alpha).3f);
  box-shadow:
    inset 0 1px 0 alpha(%(sheen)s, 0.14),
    inset 0 0 0 1px alpha(%(sheen)s, %(rim).3f);
}

.dialog-title { font-size: 1.12em; font-weight: 700; }
.dialog-body { color: alpha(currentColor, 0.76); }

/* The progress of a long copy, as a thin line rather than a dialog. */
.job-strip {
  border-radius: 14px;
  padding: 8px 12px;
  background-color: alpha(%(chrome)s, %(float_alpha).3f);
  %(blur)s
  box-shadow:
    inset 0 0 0 1px alpha(%(sheen)s, %(rim).3f),
    0 12px 32px alpha(%(shadow)s, 0.38);
}

progressbar trough {
  min-height: 4px;
  border-radius: 999px;
  background-color: alpha(currentColor, 0.14);
}

progressbar progress {
  min-height: 4px;
  border-radius: 999px;
  background-image: linear-gradient(to right,
    alpha(@accent_bg_color, 0.85), @accent_bg_color);
}

/* --------------------------------------------------------------- toasts --- */

toast {
  border-radius: 999px;
  background-color: alpha(%(chrome)s, %(float_alpha).3f);
  %(blur)s
  box-shadow: inset 0 0 0 1px alpha(%(sheen)s, %(rim).3f),
              0 10px 28px alpha(%(shadow)s, 0.36);
}

/* ---------------------------------------------------------- scrollbars --- */

/* Thin, round, and absent until used.
   The visible bar is the slider's padding box and the part you can grab is
   its transparent border — which is how Adwaita does it, and the only way
   that keeps the geometry valid: take the border away and GTK computes a
   negative minimum for the slider. Hovering thickens the bar by moving one
   pixel from the border to the padding box, so it grows without the whole
   scrollbar relaying out. */
scrollbar {
  background: none;
  border: none;
}

scrollbar > range > trough {
  background: none;
  border: none;
}

scrollbar > range > trough > slider {
  min-width: 8px;
  min-height: 8px;
  margin: 2px;
  border: 4px solid transparent;
  border-radius: 999px;
  background-clip: padding-box;
  background-color: alpha(currentColor, 0.24);
  transition: background-color 140ms %(ease)s, border-width 140ms %(ease)s;
}

scrollbar > range > trough > slider:hover {
  background-color: alpha(currentColor, 0.44);
  border-width: 3px;
}

scrollbar > range > trough > slider:active {
  background-color: alpha(currentColor, 0.58);
  border-width: 3px;
}

scrollbar > range:disabled > trough > slider { background-color: transparent; }

/* The shadow a scrolled view draws at its edge, softened. */
overshoot.top, overshoot.bottom { background: none; }
undershoot.top, undershoot.bottom, undershoot.left, undershoot.right {
  background: none;
}

/* ----------------------------------------------------------- the rest --- */

.filter-bar {
  padding: 4px 8px;
  background-color: alpha(%(sheen)s, %(inner_tint).3f);
  box-shadow: inset 0 1px 0 alpha(%(sheen)s, %(rim).3f);
}

entry.search {
  border-radius: 999px;
  min-height: 28px;
  padding: 2px 10px;
  background-color: alpha(%(field)s, %(field_alpha).3f);
  box-shadow: inset 0 1px 0 alpha(%(sheen)s, %(rim_top).3f),
              inset 0 0 0 1px alpha(%(sheen)s, %(rim).3f);
  border: none;
}

entry.search:focus-within {
  box-shadow: inset 0 0 0 1px alpha(@accent_color, 0.55),
              0 0 0 3px alpha(@accent_color, 0.12);
}

/* The drop target, drawn on the view as a whole. */
.drop-active {
  box-shadow: inset 0 0 0 2px alpha(@accent_color, 0.65);
  border-radius: %(panel_radius)dpx;
}

.pill-badge {
  padding: 1px 8px;
  border-radius: 999px;
  font-size: 0.76em;
  font-weight: 700;
  letter-spacing: 0.02em;
  background-color: alpha(currentColor, 0.12);
}

.pill-badge.accent {
  background-color: alpha(@accent_bg_color, 0.26);
  color: @accent_color;
}

.empty-title { font-size: 1.18em; font-weight: 650; opacity: 0.85; }
.empty-body { opacity: 0.56; }

separator {
  background-color: alpha(currentColor, 0.09);
  min-width: 1px;
  min-height: 1px;
}

/* Adwaita gives a view-switcher button a 100px minimum each, which on its
   own puts the window's narrowest width past what a tiling column gives it. */
.tab-switcher > button { min-width: 0; }

/* Keep every row of the window out of the business of being taller than it
   needs to be — this app is a list of names, not a form. */
row.compact { min-height: 24px; }
"""


def _category_rules(colour_icons: bool, dark: bool) -> str:
    """One rule per file category, so an icon can be tinted by what it is.

    Generated rather than written out, because the categories and their
    colours live in `filetypes.py` and having the list in two places is how
    they come to disagree. Light and dark get different colours: see
    `filetypes.LIGHT_COLOURS`.
    """
    from . import filetypes

    if not colour_icons:
        return ".entry-icon { color: alpha(currentColor, 0.72); }\n"
    lines = [".entry-icon { color: alpha(currentColor, 0.72); }"]
    for key in filetypes.CATEGORIES:
        colour = filetypes.accent(key, dark)
        if not colour:
            continue
        lines.append(f".cat-{key} {{ color: {colour}; }}")
        # In a selected row the tint fights the accent, so it steps back.
        lines.append(f"row:selected .cat-{key} {{ color: alpha({colour}, 0.85); }}")
    return "\n".join(lines) + "\n"


def _vcs_rules() -> str:
    from . import vcs

    lines = []
    for mark, colour in vcs.COLOURS.items():
        safe = {"M": "modified", "A": "added", "D": "deleted", "R": "renamed",
                "?": "untracked", "!": "ignored", "U": "conflict"}[mark]
        lines.append(f".vcs-{safe} {{ background-color: {colour}; }}")
        lines.append(f".vcs-text-{safe} {{ color: {colour}; }}")
    return "\n".join(lines) + "\n"


def scheme_for(config, dark: bool) -> theming.Theme:
    """Which of the house themes is in force.

    `dark` is what the *desktop* is asking for, and it only decides anything
    while `follow_system` is on; otherwise the setting names the theme
    outright. Passed in rather than read here because the two callers that
    have it — the application's startup and its `notify::dark` handler —
    already know, and the portal's file chooser runs in a process where
    asking again would be a second D-Bus round trip for an answer it was
    handed.
    """
    if config["follow_system"]:
        return theming.get(config["dark_theme"] if dark
                           else config["light_theme"])
    return theming.get(config["theme"])


def _numbers(config, dark: bool) -> dict:
    """The template's holes, filled from the settings and the theme.

    Dark and light want different glass. On a dark theme the rim and the sheen
    are white at low alpha and read as light catching an edge; on a light one
    the same white is invisible, so the rim leans on a soft shadow instead and
    the tint is stronger — a pale translucent panel over a pale desktop
    needs more of itself to stay legible.

    Note which `dark` decides them: the *theme's*, not the desktop's. Asking
    the desktop was right while the colours came from libadwaita and became
    wrong the moment they came from a theme — a Porcelain window in a dark
    session would otherwise get a dark theme's rim, which on warm paper is
    invisible, and the whole window would go flat.
    """
    translucency = float(config["translucency"])
    blur = int(config["glass_blur"]) if config["glass"] else 0
    radius = int(config["rounding"])
    row = {"compact": 26, "normal": 32, "roomy": 40}.get(config["row_height"], 32)
    scheme = scheme_for(config, dark)
    dark = scheme.dark

    blur_rule = (f"backdrop-filter: blur({blur}px) saturate(145%);"
                 if blur else "")
    # A popover is its own surface on Wayland, so there is nothing of ours
    # behind it; the blur would cost an offscreen and change nothing.
    popover_blur = ""
    palette_blur = (f"backdrop-filter: blur({min(blur + 14, 90)}px) saturate(160%);"
                    if blur else "")

    return {
        "radius": radius,
        "gap": GAP,
        # A card inside a rounded slab wants a smaller radius than the slab
        # or the two curves fight. Strictly concentric would be `radius -
        # GAP`, which at the default comes out at 9 and reads as a square
        # with the corners filed off; the point of the cards is that they
        # are obviously round, so they keep more than that. Floored at 10 so
        # they stay cards even when the window itself is square.
        "module_radius": max(10, min(radius - 3, 16)),
        "panel_radius": max(8, min(radius, 18)),
        "row_height": row,
        "alpha": translucency,
        "ease": "cubic-bezier(0.22, 0.61, 0.36, 1)",
        "blur": blur_rule,
        "popover_blur": popover_blur,
        "palette_blur": palette_blur,
        "sheen": "#ffffff" if dark else "#ffffff",
        "shadow": "#000000",
        # What a raised control on the glass is tinted with: light on a dark
        # theme, where white reads as a lit surface, and white again on a
        # pale one, where it reads as frosted rather than as a shadow.
        "field": "#ffffff",
        "field_alpha": 0.07 if dark else 0.55,
        "field_strong": 0.11 if dark else 0.72,
        "cluster_alpha": 0.05 if dark else 0.42,
        # The slab and the cards are tinted with the theme's *glass* rather
        # than its window colour: glass is a touch lighter than the thing it
        # sits on in every theme here, which is what stops a translucent
        # card over a dark desktop reading as a hole.
        "chrome": "@house_glass",
        "popover": "@popover_bg_color",
        # The alphas. Each one is a judgement rather than a formula, so they
        # are written out instead of derived.
        "sheen_top": 0.075 if dark else 0.34,
        "sheen_edge": 0.16 if dark else 0.50,
        "rim": 0.090 if dark else 0.035,
        "rim_top": 0.130 if dark else 0.58,
        "rim_top_strong": 0.14 if dark else 0.65,
        "rim_bottom": 0.18 if dark else 0.06,
        # Each surface is a fraction of the one setting, so dragging the
        # transparency slider moves the whole window together rather than
        # pulling it apart.
        # The ladder of depth is two rungs, not four: the cards, and the
        # slab showing between them. All three cards sit at `card_alpha` —
        # they are one material, cut into three pieces — and the slab behind
        # them is the sheer thing, which is what the gaps are for. Giving
        # each card an alpha of its own was the earlier arrangement, and
        # three strengths of the same glass meeting at a hard edge is what
        # made the toolbar read as a different substance from the listing.
        "card_alpha": translucency + (1.0 - translucency) * 0.45,
        "slab_alpha": translucency * (0.34 if dark else 0.42),
        "chrome_sheen": 0.045 if dark else 0.28,
        # A strip inside a card is lifted with light, never cut in with a
        # darker tint — the same rule the controls on the toolbar follow.
        "inner_tint": 0.045 if dark else 0.30,
        "inner_tint_strong": 0.080 if dark else 0.46,
        "module_shadow": 0.22 if dark else 0.10,
        "float_alpha": 0.72 if dark else 0.78,
        "dialog_alpha": 0.90 if dark else 0.93,
        "palette_alpha": 0.70 if dark else 0.80,
        "popover_alpha": 0.86 if dark else 0.92,
        "panel_alpha": 0.045 if dark else 0.55,
        "tab_alpha": 0.10 if dark else 0.55,
    }


def css_text(config, dark: bool = True) -> str:
    """The whole sheet: the house palette, then this app's own rules.

    The palette has to come first — a `@define-color` has to be seen before
    the rule that uses it — and the app's rules come last so that where the
    two overlap this one wins.
    """
    scheme = scheme_for(config, dark)
    return (housestyle.palette(scheme, float(config["translucency"]))
            + TEMPLATE % _numbers(config, dark)
            + _category_rules(bool(config["colour_icons"]), scheme.dark)
            + _vcs_rules())


def install(config, dark: bool = True) -> Gtk.CssProvider | None:
    """Put the sheet on the display, or replace the one already there."""
    global _provider
    display = Gdk.Display.get_default()
    if display is None:
        return None
    text = css_text(config, dark)
    # libadwaita still decides a few things from its own idea of light or
    # dark rather than from the palette — the shade under a popover, the
    # colour of a spinner, whether a symbolic icon is drawn light. Telling it
    # which way round the *theme* is keeps those in step; left alone, a
    # Porcelain window in a dark session gets white icons on warm paper.
    housestyle.force_scheme(scheme_for(config, dark))
    if _provider is None:
        _provider = Gtk.CssProvider()
        _provider.load_from_string(text)
        Gtk.StyleContext.add_provider_for_display(
            display, _provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    else:
        # Reloading the same provider is what makes a settings change take
        # effect everywhere at once, including on widgets already built.
        _provider.load_from_string(text)
    return _provider


def update(config, dark: bool = True) -> None:
    install(config, dark)
