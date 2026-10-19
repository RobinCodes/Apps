"""Shared UI pieces, and the stylesheet the chrome is drawn with.

Two halves. The small helpers at the top are the same ones every app in this
folder has — a toast, a confirmation, a flat button — kept here so no window
code has to know how an Adw.AlertDialog is spelled.

The stylesheet is the other half, and unlike the other apps here it is
*generated* rather than written out, because the colours in it come from
whichever theme is loaded and themes change while the app is running. So
`stylesheet()` builds the text and `install_css()` swaps the provider's
contents; everything that was drawn in the old colours is restyled by GTK on
the next frame without a widget being rebuilt.

What it does not do is draw any glass. Every translucent surface in this app
is a real widget from glass.py with its own snapshot, because CSS can give a
box a background and a border radius but it cannot give it a specular edge
that knows where the pointer is. So the job here is the opposite one: to take
the backgrounds *off* the Adwaita widgets, so that the glass underneath is
what you see.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gtk  # noqa: E402

from . import housestyle, theme as theming  # noqa: E402


# ---------------------------------------------------------------- bits ----

def toast(widget, message, timeout=3):
    """Find the nearest ToastOverlay and post a message on it."""
    node = widget
    while node is not None and not isinstance(node, Adw.ToastOverlay):
        node = node.get_parent()
    if node is not None:
        node.add_toast(Adw.Toast(title=message, timeout=timeout))
        return True
    return False


def confirm(parent, heading, body, action_label, callback, destructive=True):
    """Ask before anything that can't be undone. callback() runs only on yes."""
    dialog = Adw.AlertDialog(heading=heading, body=body)
    dialog.add_response("cancel", "Cancel")
    dialog.add_response("go", action_label)
    dialog.set_response_appearance(
        "go",
        Adw.ResponseAppearance.DESTRUCTIVE if destructive
        else Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("cancel")
    dialog.set_close_response("cancel")

    def answered(dlg, result):
        if dlg.choose_finish(result) == "go":
            callback()

    dialog.choose(parent, None, answered)
    return dialog


def icon_button(icon, tooltip, action=None, css=("flat",)):
    button = Gtk.Button(icon_name=icon, tooltip_text=tooltip,
                        css_classes=list(css))
    if action:
        button.set_action_name(action)
    return button


# ---------------------------------------------------------------- CSS -----

def _mix(colour: str, other: str, amount: float) -> str:
    a = theming.rgb(colour)
    b = theming.rgb(other)
    return "#%02X%02X%02X" % tuple(
        round((a[i] + (b[i] - a[i]) * amount) * 255) for i in range(3))


def stylesheet(scheme: theming.Theme, panel_blur: int = 0,
               card_radius: int = 18) -> str:
    """The whole chrome, in the colours of one theme.

    Written against the theme rather than against libadwaita's named colours,
    which is right here for the same reason it would be wrong in a document
    app: a document should look like the rest of the desktop, and a terminal
    wears the colour scheme the user chose. A header bar that stayed Adwaita
    grey while the terminal under it turned warm would look like two
    programs.

    The house palette goes in front of it all the same. The rules below cover
    every widget this app builds on purpose; what they cannot cover is the
    ones libadwaita builds on its own — the spin button inside a preferences
    row, a tooltip, a date chooser that did not exist when this was written —
    and restating `@window_bg_color` and the rest in the theme's own colours
    is what keeps those from arriving in Adwaita grey.
    """
    fg = scheme.fg
    faint = scheme.faint()
    accent = scheme.accent
    dim = _mix(fg, scheme.bg, 0.45)
    # GTK 4.22 really does honour backdrop-filter, and what it blurs is what
    # this app has already drawn — which under a floating panel is the
    # terminal's text. A popover gets none: on Wayland it is a surface of its
    # own, so there is nothing of ours behind it to blur.
    frost = (f"backdrop-filter: blur({int(panel_blur)}px) saturate(135%);"
             if panel_blur else "")
    hover = "alpha(%s, 0.09)" % ("white" if scheme.dark else "black")
    press = "alpha(%s, 0.15)" % ("white" if scheme.dark else "black")
    line = "alpha(%s, 0.12)" % ("white" if scheme.dark else "black")

    return housestyle.palette(scheme) + f"""
/* The window itself paints nothing: everything visible is a glass widget
   from glass.py, and anything Adwaita would have filled in behind them has
   to be taken back out or there is no point drawing glass at all. */
window.prism,
window.prism > contents,
.prism headerbar,
.prism .toolbar,
.prism tabbar,
.prism tabbox,
.prism scrolledwindow,
.prism viewport,
.prism .pane-scroller,
.prism overlay {{
  background: none;
  background-color: transparent;
  box-shadow: none;
  border: none;
}}

window.prism {{
  color: {fg};
}}

.prism headerbar {{
  min-height: 38px;
  padding: 0 4px;
}}

/* The title: the directory you are in, said quietly. */
.prism .window-title {{
  font-weight: 600;
  font-size: 0.95em;
  color: {fg};
}}
.prism .window-subtitle {{
  font-size: 0.82em;
  color: {dim};
}}

.prism button.flat,
.prism button.toggle {{
  color: {dim};
  border-radius: 8px;
  min-width: 26px;
  min-height: 26px;
  padding: 2px 6px;
  border: none;
  background: none;
  box-shadow: none;
  transition: background 120ms ease, color 120ms ease;
}}
.prism button.flat:hover,
.prism button.toggle:hover {{ background: {hover}; color: {fg}; }}
.prism button.flat:active,
.prism button.toggle:checked {{ background: {press}; color: {fg}; }}
.prism button.flat:focus-visible {{ outline: 1px solid {accent}; outline-offset: -2px; }}

/* ---- tabs ---- */

.prism tabbar {{ padding: 0; }}
.prism tabbar tabbox {{ padding: 2px 4px 4px 4px; }}

.prism tabbar tab {{
  border-radius: 9px;
  margin: 0 2px;
  padding: 1px 8px;
  min-height: 28px;
  color: {dim};
  background: none;
  border: none;
  box-shadow: none;
  transition: background 140ms ease, color 140ms ease;
}}
.prism tabbar tab:hover {{ background: {hover}; color: {fg}; }}
.prism tabbar tab:checked {{
  color: {fg};
  background: alpha({accent}, 0.16);
  box-shadow: inset 0 1px 0 alpha(white, {0.18 if scheme.dark else 0.5});
}}
.prism tabbar tab:checked:hover {{ background: alpha({accent}, 0.22); }}
.prism tabbar tab .tab-indicator {{ color: {accent}; }}
/* A tab whose terminal printed something while you were elsewhere. */
.prism tabbar tab.prism-activity {{ color: {accent}; }}
.prism tabbar tab button.flat {{ min-width: 20px; min-height: 20px; }}

.prism taboverview {{ background: alpha({scheme.bg}, 0.72); }}
.prism taboverview .card {{
  border-radius: 12px;
  background: alpha({scheme.glass}, 0.85);
  box-shadow: 0 4px 12px alpha(black, 0.32);
}}

/* ---- the gap between split panes ---- */

.prism paned.pane-split > separator {{
  background: none;
  background-image: none;
  min-width: 9px;
  min-height: 9px;
}}

/* ---- cards: the palette, the find bar, the preferences ---- */

/* The radius is repeated here rather than left to the widget's own
   drawing, because backdrop-filter is clipped to the CSS box: give the box
   no corners and the blur behind a rounded card is a rectangle, which shows
   as four faint square ears outside it. */
.prism .glass-card {{
  color: {fg};
  border-radius: {card_radius}px;
  {frost}
}}

.prism .glass-card entry,
.prism .glass-card .search-entry {{
  background: alpha({"white" if scheme.dark else "black"}, {0.07 if scheme.dark else 0.05});
  color: {fg};
  border: 1px solid {line};
  border-radius: 10px;
  padding: 7px 10px;
  caret-color: {accent};
  box-shadow: none;
}}
.prism .glass-card entry:focus-within {{
  border-color: alpha({accent}, 0.75);
  box-shadow: 0 0 0 2px alpha({accent}, 0.18);
}}
.prism .glass-card entry > text > placeholder {{ color: {faint}; }}

.prism .palette-row {{
  border-radius: 9px;
  padding: 6px 10px;
  color: {fg};
}}
.prism .palette-row:hover {{ background: {hover}; }}
.prism .palette-row.chosen {{
  background: alpha({accent}, 0.20);
  box-shadow: inset 0 0 0 1px alpha({accent}, 0.38);
}}
.prism .palette-hint {{ color: {dim}; font-size: 0.85em; }}
.prism .palette-key {{
  color: {dim};
  font-size: 0.82em;
  font-family: monospace;
  background: {line};
  border-radius: 5px;
  padding: 1px 6px;
}}
.prism .palette-group {{
  color: {faint};
  font-size: 0.76em;
  font-weight: 700;
  letter-spacing: 0.07em;
  text-transform: uppercase;
  padding: 10px 10px 3px 10px;
}}
.prism .card-title {{ font-weight: 700; font-size: 1.0em; color: {fg}; }}
.prism .dim-label, .prism .caption {{ color: {dim}; }}

/* ---- the scrollbar ---- */

.prism scrollbar {{ background: none; border: none; }}
.prism scrollbar slider {{
  background: alpha({fg}, 0.22);
  border: none;
  border-radius: 8px;
  min-width: 6px;
  min-height: 24px;
  margin: 2px;
  transition: background 140ms ease;
}}
.prism scrollbar slider:hover {{ background: alpha({fg}, 0.38); }}
.prism scrollbar slider:active {{ background: alpha({accent}, 0.65); }}

/* ---- preferences, which are ordinary Adwaita rows on glass ---- */

.prism preferencespage,
.prism preferencesgroup,
.prism .boxed-list {{ background: none; }}
.prism .boxed-list row {{
  background: alpha({"white" if scheme.dark else "black"}, {0.05 if scheme.dark else 0.035});
  border: 1px solid {line};
  color: {fg};
}}
.prism popover > contents {{
  background: alpha({scheme.glass}, 0.97);
  color: {fg};
  border: 1px solid {line};
  border-radius: 12px;
/* Modest on purpose: GSK blurs an offscreen to draw a soft shadow and
   the cost goes with the radius, not with the size of the thing casting
   it. A popover at 34px measured nearly three times the cost of one at
   12px — see the note in housestyle.chrome(). */
  box-shadow: 0 4px 12px alpha(black, 0.40);
}}
.prism popover modelbutton {{ border-radius: 7px; color: {fg}; }}
.prism popover modelbutton:hover {{ background: {hover}; }}
"""


_provider: Gtk.CssProvider | None = None


def install_css(scheme: theming.Theme, display: Gdk.Display | None = None,
                panel_blur: int = 0, card_radius: int = 18):
    """Load (or reload) the stylesheet for `scheme`.

    One provider for the whole process, reloaded in place. Adding a second
    provider per theme change would leave the old rules loaded underneath,
    where they win or lose by specificity rather than by recency, and the
    second theme change would start producing colours from neither theme.
    """
    global _provider
    display = display or Gdk.Display.get_default()
    if display is None:
        return None
    if _provider is None:
        _provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(
            display, _provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _provider.load_from_string(stylesheet(scheme, panel_blur, card_radius))
    return _provider
