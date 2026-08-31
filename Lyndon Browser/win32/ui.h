/* ui.h — the drawing vocabulary the Windows build shares.
 *
 * libadwaita gives the Linux build a palette, a font, rows with a title and a
 * subtitle, and switches that already know what they look like in the dark.
 * None of that exists here, so it is written once and used by the chrome, the
 * panel and the preferences window rather than three times over.
 *
 * Everything is drawn into a 32-bit DIB that this file owns, and the rounded
 * shapes are filled by evaluating a signed distance field per pixel. That is
 * the whole reason for the DIB: GDI's RoundRect has no antialiasing, so a
 * 9px corner drawn with it is a visible staircase, and a browser built out of
 * staircased corners looks twenty years old however good the colours are.
 * Text still goes through GDI, which is better at it than anything worth
 * writing here.
 *
 * Everything takes a DPI and scales by it. A window dragged to a display with
 * a different scale factor gets WM_DPICHANGED and redraws at the new size, so
 * no measurement may be stored in pixels.
 */
#pragma once

#include "lyndon.h"

#include <windows.h>

G_BEGIN_DECLS

/* ----------------------------------------------------------------- canvas */

/* A double buffer with its pixels reachable. Every window here repaints whole
 * on any change; without the buffering the tab strip flickers over the page
 * on each title update, and without the pixels the corners are jagged. */
typedef struct {
  HDC       dc;        /* GDI target, for text                     */
  guint32  *px;        /* BGRX, top-down, `width` per row, or NULL */
  int       width, height;

  HDC       target;
  HBITMAP   bitmap;
  HBITMAP   previous;

  /* Every primitive intersects with this. The pixel-level drawing below
   * cannot see GDI's clip region, and the scrolling lists need one. */
  RECT      clip;
} LyCanvas;

gboolean ly_canvas_begin (LyCanvas *canvas, HDC target, int width, int height);
void     ly_canvas_end   (LyCanvas *canvas);

/* Restrict drawing to `r`. Also set on the DC, so text is clipped too.
 * Returns the previous clip, to be handed back to ly_canvas_unclip. */
RECT ly_canvas_clip   (LyCanvas *canvas, RECT r);
void ly_canvas_unclip (LyCanvas *canvas, RECT previous);

/* ------------------------------------------------------------------ theme */

typedef struct {
  COLORREF bg;          /* the window behind everything            */
  COLORREF chrome;      /* toolbars and the tab strip              */
  COLORREF surface;     /* cards, the selected tab, the entry      */
  COLORREF surface_alt; /* hover fills                             */
  COLORREF text;
  COLORREF text_dim;
  COLORREF line;
  COLORREF field;       /* text inputs                             */
  COLORREF accent;      /* accent_bg_color                         */
  COLORREF accent_text; /* accent_fg_color                         */
  COLORREF accent_fg;   /* accent_color: accent used *as* text     */
  COLORREF success;
  COLORREF warning;
  COLORREF danger;
  COLORREF shadow;
  gboolean dark;
} LyTheme;

gboolean ly_system_is_dark (void);
/* What the window should actually use: the Appearance setting when it
 * names one, and the system otherwise. */
gboolean ly_wants_dark (LyConfig *cfg);
void     ly_theme_load     (LyTheme *theme, gboolean dark);
void     ly_apply_dark_titlebar (HWND hwnd, gboolean dark);

/* Mix `over` into `under` at `amount` (0..1). The stylesheet is written in
 * terms of alpha(currentColor, 0.09) and colour-mix, so the port needs the
 * same arithmetic to land on the same greys. */
COLORREF ly_mix (COLORREF under, COLORREF over, double amount);

/* ------------------------------------------------------------------ fonts */

typedef struct {
  HFONT normal;
  HFONT small_;
  HFONT bold;
  HFONT tiny_bold;   /* the shield count and other numerals */
  HFONT title;
  int   dpi;
} LyFonts;

void ly_fonts_make (LyFonts *fonts, int dpi);
void ly_fonts_free (LyFonts *fonts);

static inline int
ly_scale (int dpi, int value)
{
  return MulDiv (value, dpi, 96);
}

/* ---------------------------------------------------------------- drawing */

/* All of these blend, so `alpha` below 1 composites onto what is there. */
void ly_fill       (LyCanvas *c, RECT r, COLORREF colour);
void ly_fill_alpha (LyCanvas *c, RECT r, COLORREF colour, double alpha);

/* Antialiased. `radius` is in pixels and is clamped to half the shorter side. */
void ly_round      (LyCanvas *c, RECT r, double radius, COLORREF colour,
                    double alpha);
void ly_round_ring (LyCanvas *c, RECT r, double radius, double thickness,
                    COLORREF colour, double alpha);
/* The soft drop under a selected tab or a card: box-shadow 0 1px 2px. */
void ly_round_shadow (LyCanvas *c, RECT r, double radius, COLORREF colour,
                      double alpha, int offset_y, int spread);

void ly_text  (LyCanvas *c, RECT r, const char *utf8, COLORREF colour,
               HFONT font, UINT flags);
int  ly_text_width (LyCanvas *c, const char *utf8, HFONT font);

/* The line-drawn icon set. No icon font is involved, so nothing can fall back
 * to a missing-glyph box on a machine without Segoe UI Symbol. Drawn into the
 * DIB with the same antialiasing as the shapes. */
typedef enum {
  LY_GLYPH_BACK,
  LY_GLYPH_FORWARD,
  LY_GLYPH_RELOAD,
  LY_GLYPH_STOP,
  LY_GLYPH_PLUS,
  LY_GLYPH_CLOSE,
  LY_GLYPH_SHIELD,
  LY_GLYPH_MENU,
  LY_GLYPH_STAR,
  LY_GLYPH_CLOCK,
  LY_GLYPH_DOWNLOAD,
  LY_GLYPH_CHECK,
  LY_GLYPH_CHEVRON_DOWN,
  LY_GLYPH_HOME,
  LY_GLYPH_LOCK,
  LY_GLYPH_WARNING,
  LY_GLYPH_GLOBE,
  LY_GLYPH_LIBRARY,
} LyGlyph;

void ly_glyph (LyCanvas *c, RECT r, LyGlyph glyph, COLORREF colour,
               gboolean filled);

/* A switch, drawn the way libadwaita's AdwSwitchRow looks. */
void ly_switch (LyCanvas *c, RECT r, gboolean on, const LyTheme *theme);

G_END_DECLS
