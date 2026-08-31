/* ui.c — see ui.h. */

#include "ui.h"

#include <dwmapi.h>
#include <math.h>
#include <string.h>

/* ------------------------------------------------------------------ theme */

gboolean
ly_system_is_dark (void)
{
  DWORD value = 1, size = sizeof value;
  if (RegGetValueW (HKEY_CURRENT_USER,
                    L"Software\\Microsoft\\Windows\\CurrentVersion\\Themes\\Personalize",
                    L"AppsUseLightTheme", RRF_RT_REG_DWORD, NULL, &value, &size)
      != ERROR_SUCCESS)
    return FALSE;
  return value == 0;
}

gboolean
ly_wants_dark (LyConfig *cfg)
{
  if (cfg == NULL)
    return ly_system_is_dark ();
  switch (cfg->scheme) {
    case LY_SCHEME_LIGHT: return FALSE;
    case LY_SCHEME_DARK:  return TRUE;
    default:              return ly_system_is_dark ();
  }
}

/* libadwaita's named colours, so the two builds are the same grey rather than
 * two people's idea of grey. */
void
ly_theme_load (LyTheme *t, gboolean dark)
{
  t->dark = dark;
  if (dark) {
    t->bg          = RGB (0x22, 0x22, 0x26);  /* window_bg_color    */
    t->chrome      = RGB (0x2e, 0x2e, 0x32);  /* headerbar_bg_color */
    t->surface     = RGB (0x1d, 0x1d, 0x20);  /* view_bg_color      */
    t->surface_alt = RGB (0x35, 0x35, 0x3a);
    t->text        = RGB (0xff, 0xff, 0xff);
    t->text_dim    = RGB (0x9a, 0x9a, 0xa4);
    t->line        = RGB (0x3d, 0x3d, 0x43);
    t->field       = RGB (0x1d, 0x1d, 0x20);
    t->accent      = RGB (0x35, 0x84, 0xe4);  /* accent_bg_color    */
    t->accent_text = RGB (0xff, 0xff, 0xff);  /* accent_fg_color    */
    t->accent_fg   = RGB (0x78, 0xae, 0xed);  /* accent_color       */
    t->success     = RGB (0x8f, 0xf0, 0xa4);
    t->warning     = RGB (0xf8, 0xe4, 0x5c);
    t->danger      = RGB (0xff, 0x7b, 0x63);
    t->shadow      = RGB (0x00, 0x00, 0x00);
  } else {
    t->bg          = RGB (0xfa, 0xfa, 0xfb);
    t->chrome      = RGB (0xff, 0xff, 0xff);
    t->surface     = RGB (0xff, 0xff, 0xff);
    t->surface_alt = RGB (0xed, 0xed, 0xf0);
    t->text        = RGB (0x1b, 0x1d, 0x22);
    t->text_dim    = RGB (0x60, 0x65, 0x70);
    t->line        = RGB (0xd8, 0xd8, 0xdd);
    t->field       = RGB (0xff, 0xff, 0xff);
    t->accent      = RGB (0x35, 0x84, 0xe4);
    t->accent_text = RGB (0xff, 0xff, 0xff);
    t->accent_fg   = RGB (0x1b, 0x6a, 0xcb);
    t->success     = RGB (0x2e, 0xc2, 0x7e);
    t->warning     = RGB (0xc8, 0x8b, 0x00);
    t->danger      = RGB (0xc0, 0x1c, 0x28);
    t->shadow      = RGB (0x00, 0x00, 0x00);
  }
}

COLORREF
ly_mix (COLORREF under, COLORREF over, double amount)
{
  amount = CLAMP (amount, 0.0, 1.0);
  int r = (int) lround (GetRValue (under) + (GetRValue (over) - GetRValue (under)) * amount);
  int g = (int) lround (GetGValue (under) + (GetGValue (over) - GetGValue (under)) * amount);
  int b = (int) lround (GetBValue (under) + (GetBValue (over) - GetBValue (under)) * amount);
  return RGB (CLAMP (r, 0, 255), CLAMP (g, 0, 255), CLAMP (b, 0, 255));
}

void
ly_apply_dark_titlebar (HWND hwnd, gboolean dark)
{
  /* DWMWA_USE_IMMERSIVE_DARK_MODE is 20 on current Windows 10 and 11 and 19
   * on the 1809-to-1903 builds. Setting both is the usual answer; the wrong
   * one is simply refused. */
  BOOL on = dark;
  DwmSetWindowAttribute (hwnd, 20, &on, sizeof on);
  DwmSetWindowAttribute (hwnd, 19, &on, sizeof on);
}

/* ------------------------------------------------------------------ fonts */

static HFONT
make_font (int dpi, int points_x10, int weight)
{
  return CreateFontW (-MulDiv (points_x10, dpi, 720), 0, 0, 0, weight, 0, 0, 0,
                      DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS,
                      CLEARTYPE_QUALITY, VARIABLE_PITCH, L"Segoe UI Variable Text");
}

void
ly_fonts_make (LyFonts *f, int dpi)
{
  ly_fonts_free (f);
  f->dpi       = dpi;
  f->normal    = make_font (dpi, 100, FW_NORMAL);
  f->small_    = make_font (dpi,  88, FW_NORMAL);
  f->bold      = make_font (dpi, 100, FW_SEMIBOLD);
  f->tiny_bold = make_font (dpi,  76, FW_BOLD);
  f->title     = make_font (dpi, 132, FW_SEMIBOLD);

  /* Segoe UI Variable only exists from Windows 11. CreateFont never fails, it
   * substitutes — so check what came back and fall back by name rather than
   * shipping a browser that renders in whatever the substitution picked. */
  HDC screen = GetDC (NULL);
  HFONT old = SelectObject (screen, f->normal);
  wchar_t got[LF_FACESIZE] = { 0 };
  GetTextFaceW (screen, LF_FACESIZE, got);
  SelectObject (screen, old);
  ReleaseDC (NULL, screen);

  if (wcsncmp (got, L"Segoe UI Variable", 17) != 0) {
    ly_fonts_free (f);
    f->dpi = dpi;
    struct { HFONT *slot; int size; int weight; } plain[] = {
      { &f->normal,    100, FW_NORMAL   },
      { &f->small_,     88, FW_NORMAL   },
      { &f->bold,      100, FW_SEMIBOLD },
      { &f->tiny_bold,  76, FW_BOLD     },
      { &f->title,     132, FW_SEMIBOLD },
    };
    for (gsize i = 0; i < G_N_ELEMENTS (plain); i++)
      *plain[i].slot = CreateFontW (-MulDiv (plain[i].size, dpi, 720), 0, 0, 0,
                                    plain[i].weight, 0, 0, 0, DEFAULT_CHARSET,
                                    OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS,
                                    CLEARTYPE_QUALITY, VARIABLE_PITCH, L"Segoe UI");
  }
}

void
ly_fonts_free (LyFonts *f)
{
  if (f->normal)    DeleteObject (f->normal);
  if (f->small_)    DeleteObject (f->small_);
  if (f->bold)      DeleteObject (f->bold);
  if (f->tiny_bold) DeleteObject (f->tiny_bold);
  if (f->title)     DeleteObject (f->title);
  f->normal = f->small_ = f->bold = f->tiny_bold = f->title = NULL;
}

/* ----------------------------------------------------------------- canvas */

gboolean
ly_canvas_begin (LyCanvas *c, HDC target, int width, int height)
{
  memset (c, 0, sizeof *c);
  if (width <= 0 || height <= 0)
    return FALSE;

  c->target = target;
  c->width = width;
  c->height = height;
  c->dc = CreateCompatibleDC (target);
  if (c->dc == NULL)
    return FALSE;

  /* Negative height: top-down, so row 0 is the top and the arithmetic below
   * reads the way the coordinates do. */
  BITMAPINFO info = { 0 };
  info.bmiHeader.biSize        = sizeof info.bmiHeader;
  info.bmiHeader.biWidth       = width;
  info.bmiHeader.biHeight      = -height;
  info.bmiHeader.biPlanes      = 1;
  info.bmiHeader.biBitCount    = 32;
  info.bmiHeader.biCompression = BI_RGB;

  void *bits = NULL;
  c->bitmap = CreateDIBSection (target, &info, DIB_RGB_COLORS, &bits, NULL, 0);
  if (c->bitmap == NULL) {
    DeleteDC (c->dc);
    c->dc = NULL;
    return FALSE;
  }
  c->px = bits;
  c->previous = SelectObject (c->dc, c->bitmap);
  c->clip = (RECT) { 0, 0, width, height };
  return TRUE;
}

RECT
ly_canvas_clip (LyCanvas *c, RECT r)
{
  RECT was = c->clip;
  c->clip.left   = MAX (c->clip.left, r.left);
  c->clip.top    = MAX (c->clip.top, r.top);
  c->clip.right  = MIN (c->clip.right, r.right);
  c->clip.bottom = MIN (c->clip.bottom, r.bottom);
  HRGN rgn = CreateRectRgn (c->clip.left, c->clip.top, c->clip.right, c->clip.bottom);
  SelectClipRgn (c->dc, rgn);
  DeleteObject (rgn);
  return was;
}

void
ly_canvas_unclip (LyCanvas *c, RECT previous)
{
  c->clip = previous;
  HRGN rgn = CreateRectRgn (previous.left, previous.top, previous.right, previous.bottom);
  SelectClipRgn (c->dc, rgn);
  DeleteObject (rgn);
}

void
ly_canvas_end (LyCanvas *c)
{
  if (c->dc == NULL)
    return;
  /* GDI has been writing into the DIB behind our back; make sure it has
   * finished before the bits are read. */
  GdiFlush ();
  BitBlt (c->target, 0, 0, c->width, c->height, c->dc, 0, 0, SRCCOPY);
  SelectObject (c->dc, c->previous);
  DeleteObject (c->bitmap);
  DeleteDC (c->dc);
  memset (c, 0, sizeof *c);
}

/* ------------------------------------------------------------- primitives */

static inline void
blend (guint32 *p, int red, int green, int blue, double a)
{
  if (a <= 0.0)
    return;
  if (a >= 1.0) {
    *p = ((guint32) red << 16) | ((guint32) green << 8) | (guint32) blue;
    return;
  }
  guint32 d = *p;
  int dr = (int) ((d >> 16) & 0xff);
  int dg = (int) ((d >> 8) & 0xff);
  int db = (int) (d & 0xff);
  int r = (int) lround (dr + (red - dr) * a);
  int g = (int) lround (dg + (green - dg) * a);
  int b = (int) lround (db + (blue - db) * a);
  *p = ((guint32) r << 16) | ((guint32) g << 8) | (guint32) b;
}

/* Signed distance to a rounded rectangle: negative inside, and in units of
 * pixels either side of the edge, which is what makes one-pixel coverage
 * fall straight out of it. */
static inline double
rr_distance (double x, double y, double cx, double cy,
             double hw, double hh, double r)
{
  double dx = fabs (x - cx) - (hw - r);
  double dy = fabs (y - cy) - (hh - r);
  double ax = MAX (dx, 0.0);
  double ay = MAX (dy, 0.0);
  return sqrt (ax * ax + ay * ay) + MIN (MAX (dx, dy), 0.0) - r;
}

void
ly_fill_alpha (LyCanvas *c, RECT rect, COLORREF colour, double alpha)
{
  if (c->px == NULL) {
    HBRUSH b = CreateSolidBrush (colour);
    FillRect (c->dc, &rect, b);
    DeleteObject (b);
    return;
  }
  int x0 = MAX (rect.left, c->clip.left), x1 = MIN (rect.right, c->clip.right);
  int y0 = MAX (rect.top, c->clip.top),  y1 = MIN (rect.bottom, c->clip.bottom);
  int r = GetRValue (colour), g = GetGValue (colour), b = GetBValue (colour);
  for (int y = y0; y < y1; y++) {
    guint32 *row = c->px + (gsize) y * c->width;
    for (int x = x0; x < x1; x++)
      blend (row + x, r, g, b, alpha);
  }
}

void
ly_fill (LyCanvas *c, RECT rect, COLORREF colour)
{
  ly_fill_alpha (c, rect, colour, 1.0);
}

void
ly_round (LyCanvas *c, RECT rect, double radius, COLORREF colour, double alpha)
{
  double w = rect.right - rect.left, h = rect.bottom - rect.top;
  if (w <= 0 || h <= 0)
    return;
  if (c->px == NULL) {
    HBRUSH br = CreateSolidBrush (colour);
    HPEN pen = CreatePen (PS_SOLID, 1, colour);
    HBRUSH ob = SelectObject (c->dc, br);
    HPEN op = SelectObject (c->dc, pen);
    RoundRect (c->dc, rect.left, rect.top, rect.right, rect.bottom,
               (int) radius * 2, (int) radius * 2);
    SelectObject (c->dc, ob);
    SelectObject (c->dc, op);
    DeleteObject (br);
    DeleteObject (pen);
    return;
  }

  radius = CLAMP (radius, 0.0, MIN (w, h) / 2.0);
  double cx = rect.left + w / 2.0, cy = rect.top + h / 2.0;
  double hw = w / 2.0, hh = h / 2.0;

  int x0 = MAX (rect.left, c->clip.left), x1 = MIN (rect.right, c->clip.right);
  int y0 = MAX (rect.top, c->clip.top),  y1 = MIN (rect.bottom, c->clip.bottom);
  int r = GetRValue (colour), g = GetGValue (colour), b = GetBValue (colour);

  for (int y = y0; y < y1; y++) {
    guint32 *row = c->px + (gsize) y * c->width;
    for (int x = x0; x < x1; x++) {
      double d = rr_distance (x + 0.5, y + 0.5, cx, cy, hw, hh, radius);
      double cov = CLAMP (0.5 - d, 0.0, 1.0);
      if (cov > 0.0)
        blend (row + x, r, g, b, cov * alpha);
    }
  }
}

void
ly_round_ring (LyCanvas *c, RECT rect, double radius, double thickness,
               COLORREF colour, double alpha)
{
  double w = rect.right - rect.left, h = rect.bottom - rect.top;
  if (w <= 0 || h <= 0 || c->px == NULL)
    return;

  radius = CLAMP (radius, 0.0, MIN (w, h) / 2.0);
  double cx = rect.left + w / 2.0, cy = rect.top + h / 2.0;
  double hw = w / 2.0, hh = h / 2.0;

  int x0 = MAX (rect.left, c->clip.left), x1 = MIN (rect.right, c->clip.right);
  int y0 = MAX (rect.top, c->clip.top),  y1 = MIN (rect.bottom, c->clip.bottom);
  int r = GetRValue (colour), g = GetGValue (colour), b = GetBValue (colour);

  for (int y = y0; y < y1; y++) {
    guint32 *row = c->px + (gsize) y * c->width;
    for (int x = x0; x < x1; x++) {
      double d = rr_distance (x + 0.5, y + 0.5, cx, cy, hw, hh, radius);
      double outer = CLAMP (0.5 - d, 0.0, 1.0);
      double inner = CLAMP (0.5 - (d + thickness), 0.0, 1.0);
      double cov = outer - inner;
      if (cov > 0.0)
        blend (row + x, r, g, b, cov * alpha);
    }
  }
}

void
ly_round_shadow (LyCanvas *c, RECT rect, double radius, COLORREF colour,
                 double alpha, int offset_y, int spread)
{
  if (c->px == NULL || spread <= 0)
    return;
  /* A real Gaussian is not worth it at this size: a handful of expanding
   * outlines at falling alpha is indistinguishable from one at 1px blur and
   * costs nothing. */
  for (int i = spread; i >= 1; i--) {
    RECT r = { rect.left - i, rect.top - i + offset_y,
               rect.right + i, rect.bottom + i + offset_y };
    double a = alpha * (double) (spread - i + 1) / (double) (spread * (spread + 1));
    ly_round (c, r, radius + i, colour, a);
  }
}

/* ------------------------------------------------------------------- text */

void
ly_text (LyCanvas *c, RECT r, const char *utf8, COLORREF colour, HFONT font,
         UINT flags)
{
  if (utf8 == NULL || *utf8 == '\0')
    return;
  g_autofree wchar_t *w = (wchar_t *) g_utf8_to_utf16 (utf8, -1, NULL, NULL, NULL);
  if (w == NULL)
    return;
  HFONT old = SelectObject (c->dc, font);
  SetTextColor (c->dc, colour);
  SetBkMode (c->dc, TRANSPARENT);
  DrawTextW (c->dc, w, -1, &r, flags);
  SelectObject (c->dc, old);
}

int
ly_text_width (LyCanvas *c, const char *utf8, HFONT font)
{
  if (utf8 == NULL || *utf8 == '\0')
    return 0;
  g_autofree wchar_t *w = (wchar_t *) g_utf8_to_utf16 (utf8, -1, NULL, NULL, NULL);
  if (w == NULL)
    return 0;
  HFONT old = SelectObject (c->dc, font);
  SIZE size = { 0, 0 };
  GetTextExtentPoint32W (c->dc, w, (int) wcslen (w), &size);
  SelectObject (c->dc, old);
  return size.cx;
}

/* ------------------------------------------------------------------ paths */

typedef struct { double x, y; } Pt;

/* Coverage from the distance to a segment: a stroke with round caps, which is
 * what every one of these icons wants anyway. */
static void
stroke (LyCanvas *c, Pt a, Pt b, double half, COLORREF colour, double alpha)
{
  if (c->px == NULL)
    return;
  double pad = half + 1.5;
  int x0 = MAX ((int) floor (MIN (a.x, b.x) - pad), c->clip.left);
  int x1 = MIN ((int) ceil  (MAX (a.x, b.x) + pad), c->clip.right);
  int y0 = MAX ((int) floor (MIN (a.y, b.y) - pad), c->clip.top);
  int y1 = MIN ((int) ceil  (MAX (a.y, b.y) + pad), c->clip.bottom);

  double vx = b.x - a.x, vy = b.y - a.y;
  double len2 = vx * vx + vy * vy;
  int r = GetRValue (colour), g = GetGValue (colour), bl = GetBValue (colour);

  for (int y = y0; y < y1; y++) {
    guint32 *row = c->px + (gsize) y * c->width;
    for (int x = x0; x < x1; x++) {
      double px = x + 0.5 - a.x, py = y + 0.5 - a.y;
      double t = (len2 > 0.0) ? CLAMP ((px * vx + py * vy) / len2, 0.0, 1.0) : 0.0;
      double dx = px - vx * t, dy = py - vy * t;
      double cov = CLAMP (half + 0.5 - sqrt (dx * dx + dy * dy), 0.0, 1.0);
      if (cov > 0.0)
        blend (row + x, r, g, bl, cov * alpha);
    }
  }
}

static void
polyline (LyCanvas *c, const Pt *p, int n, double half, COLORREF colour,
          double alpha, gboolean close)
{
  for (int i = 0; i + 1 < n; i++)
    stroke (c, p[i], p[i + 1], half, colour, alpha);
  if (close && n > 2)
    stroke (c, p[n - 1], p[0], half, colour, alpha);
}

/* Supersampled polygon fill. The icons are twenty pixels across, so sixteen
 * samples a pixel costs nothing and removes every jagged edge. */
static void
fill_polygon (LyCanvas *c, const Pt *p, int n, COLORREF colour, double alpha)
{
  if (c->px == NULL || n < 3)
    return;
  double minx = p[0].x, maxx = p[0].x, miny = p[0].y, maxy = p[0].y;
  for (int i = 1; i < n; i++) {
    minx = MIN (minx, p[i].x); maxx = MAX (maxx, p[i].x);
    miny = MIN (miny, p[i].y); maxy = MAX (maxy, p[i].y);
  }
  int x0 = MAX ((int) floor (minx), c->clip.left);
  int x1 = MIN ((int) ceil (maxx) + 1, c->clip.right);
  int y0 = MAX ((int) floor (miny), c->clip.top);
  int y1 = MIN ((int) ceil (maxy) + 1, c->clip.bottom);
  int r = GetRValue (colour), g = GetGValue (colour), b = GetBValue (colour);

  const int S = 4;   /* 4x4 samples */
  for (int y = y0; y < y1; y++) {
    guint32 *row = c->px + (gsize) y * c->width;
    for (int x = x0; x < x1; x++) {
      int hits = 0;
      for (int sy = 0; sy < S; sy++) {
        double py = y + (sy + 0.5) / S;
        for (int sx = 0; sx < S; sx++) {
          double px = x + (sx + 0.5) / S;
          gboolean in = FALSE;
          for (int i = 0, j = n - 1; i < n; j = i++) {
            if (((p[i].y > py) != (p[j].y > py)) &&
                (px < (p[j].x - p[i].x) * (py - p[i].y) / (p[j].y - p[i].y) + p[i].x))
              in = !in;
          }
          if (in)
            hits++;
        }
      }
      if (hits)
        blend (row + x, r, g, b, alpha * hits / (double) (S * S));
    }
  }
}

/* An arc as a polyline: enough segments that the eye cannot find the joins. */
static void
arc_stroke (LyCanvas *c, double cx, double cy, double radius,
            double from, double to, double half, COLORREF colour, double alpha)
{
  const int STEPS = 24;
  Pt prev = { cx + cos (from) * radius, cy + sin (from) * radius };
  for (int i = 1; i <= STEPS; i++) {
    double a = from + (to - from) * i / STEPS;
    Pt next = { cx + cos (a) * radius, cy + sin (a) * radius };
    stroke (c, prev, next, half, colour, alpha);
    prev = next;
  }
}

/* ----------------------------------------------------------------- glyphs */

void
ly_glyph (LyCanvas *c, RECT rect, LyGlyph glyph, COLORREF colour, gboolean filled)
{
  double cx = (rect.left + rect.right) / 2.0;
  double cy = (rect.top + rect.bottom) / 2.0;
  double s  = MIN (rect.right - rect.left, rect.bottom - rect.top) / 2.0 * 0.55;
  if (s < 3.0)
    s = 3.0;
  double half = MAX (0.75, s / 5.5);   /* stroke half-width */

  switch (glyph) {
    case LY_GLYPH_BACK: {
      Pt p[3] = { { cx + s * 0.45, cy - s * 0.8 }, { cx - s * 0.45, cy },
                  { cx + s * 0.45, cy + s * 0.8 } };
      polyline (c, p, 3, half, colour, 1.0, FALSE);
      break;
    }
    case LY_GLYPH_FORWARD: {
      Pt p[3] = { { cx - s * 0.45, cy - s * 0.8 }, { cx + s * 0.45, cy },
                  { cx - s * 0.45, cy + s * 0.8 } };
      polyline (c, p, 3, half, colour, 1.0, FALSE);
      break;
    }
    case LY_GLYPH_CHEVRON_DOWN: {
      Pt p[3] = { { cx - s * 0.7, cy - s * 0.35 }, { cx, cy + s * 0.4 },
                  { cx + s * 0.7, cy - s * 0.35 } };
      polyline (c, p, 3, half, colour, 1.0, FALSE);
      break;
    }
    case LY_GLYPH_RELOAD: {
      /* Five sixths of a ring, and an arrowhead built from the tangent at the
         open end so the two always meet however the size is scaled. */
      double a0 = -G_PI * 0.30;
      arc_stroke (c, cx, cy, s * 0.82, a0, a0 + G_PI * 1.70, half, colour, 1.0);

      double hx = cx + cos (a0) * s * 0.82;
      double hy = cy + sin (a0) * s * 0.82;
      double tx = -sin (a0), ty = cos (a0);          /* tangent, anticlockwise */
      double barb = s * 0.52;
      for (int side = -1; side <= 1; side += 2) {
        double ang = atan2 (ty, tx) + side * 2.5;    /* ~143 degrees back */
        Pt seg[2] = { { hx, hy },
                      { hx + cos (ang) * barb, hy + sin (ang) * barb } };
        polyline (c, seg, 2, half, colour, 1.0, FALSE);
      }
      break;
    }
    case LY_GLYPH_STOP: {
      Pt a[2] = { { cx - s * 0.7, cy - s * 0.7 }, { cx + s * 0.7, cy + s * 0.7 } };
      Pt b[2] = { { cx + s * 0.7, cy - s * 0.7 }, { cx - s * 0.7, cy + s * 0.7 } };
      polyline (c, a, 2, half, colour, 1.0, FALSE);
      polyline (c, b, 2, half, colour, 1.0, FALSE);
      break;
    }
    case LY_GLYPH_PLUS: {
      Pt a[2] = { { cx - s * 0.8, cy }, { cx + s * 0.8, cy } };
      Pt b[2] = { { cx, cy - s * 0.8 }, { cx, cy + s * 0.8 } };
      polyline (c, a, 2, half, colour, 1.0, FALSE);
      polyline (c, b, 2, half, colour, 1.0, FALSE);
      break;
    }
    case LY_GLYPH_CLOSE: {
      Pt a[2] = { { cx - s * 0.55, cy - s * 0.55 }, { cx + s * 0.55, cy + s * 0.55 } };
      Pt b[2] = { { cx + s * 0.55, cy - s * 0.55 }, { cx - s * 0.55, cy + s * 0.55 } };
      polyline (c, a, 2, half, colour, 1.0, FALSE);
      polyline (c, b, 2, half, colour, 1.0, FALSE);
      break;
    }
    case LY_GLYPH_MENU: {
      for (int i = -1; i <= 1; i++) {
        Pt a[2] = { { cx - s * 0.8, cy + i * s * 0.62 },
                    { cx + s * 0.8, cy + i * s * 0.62 } };
        polyline (c, a, 2, half, colour, 1.0, FALSE);
      }
      break;
    }
    case LY_GLYPH_LIBRARY: {
      /* Three book spines of different heights. The hamburger sits right
         beside this one, and two sets of horizontal lines would be the same
         icon twice. */
      double top[3] = { 0.55, 0.95, 0.70 };
      for (int i = 0; i < 3; i++) {
        double x = cx + (i - 1) * s * 0.62;
        Pt spine[2] = { { x, cy - s * top[i] }, { x, cy + s * 0.85 } };
        polyline (c, spine, 2, half * 1.15, colour, 1.0, FALSE);
      }
      /* The shelf they stand on. */
      Pt shelf[2] = { { cx - s * 0.95, cy + s * 0.9 }, { cx + s * 0.95, cy + s * 0.9 } };
      polyline (c, shelf, 2, half * 0.9, colour, 0.8, FALSE);
      break;
    }
    case LY_GLYPH_CHECK: {
      Pt p[3] = { { cx - s * 0.75, cy }, { cx - s * 0.2, cy + s * 0.6 },
                  { cx + s * 0.78, cy - s * 0.6 } };
      polyline (c, p, 3, half, colour, 1.0, FALSE);
      break;
    }
    case LY_GLYPH_DOWNLOAD: {
      Pt shaft[2] = { { cx, cy - s * 0.85 }, { cx, cy + s * 0.2 } };
      Pt head[3]  = { { cx - s * 0.45, cy - s * 0.25 }, { cx, cy + s * 0.22 },
                      { cx + s * 0.45, cy - s * 0.25 } };
      Pt tray[2]  = { { cx - s * 0.8, cy + s * 0.78 }, { cx + s * 0.8, cy + s * 0.78 } };
      polyline (c, shaft, 2, half, colour, 1.0, FALSE);
      polyline (c, head, 3, half, colour, 1.0, FALSE);
      polyline (c, tray, 2, half, colour, 1.0, FALSE);
      break;
    }
    case LY_GLYPH_CLOCK: {
      arc_stroke (c, cx, cy, s * 0.85, 0, G_PI * 2, half, colour, 1.0);
      Pt hands[3] = { { cx, cy - s * 0.5 }, { cx, cy }, { cx + s * 0.45, cy + s * 0.28 } };
      polyline (c, hands, 3, half, colour, 1.0, FALSE);
      break;
    }
    case LY_GLYPH_HOME: {
      Pt roof[3] = { { cx - s * 0.9, cy + s * 0.05 }, { cx, cy - s * 0.8 },
                     { cx + s * 0.9, cy + s * 0.05 } };
      Pt body[4] = { { cx - s * 0.6, cy + s * 0.05 }, { cx - s * 0.6, cy + s * 0.8 },
                     { cx + s * 0.6, cy + s * 0.8 }, { cx + s * 0.6, cy + s * 0.05 } };
      polyline (c, roof, 3, half, colour, 1.0, FALSE);
      polyline (c, body, 4, half, colour, 1.0, FALSE);
      break;
    }
    case LY_GLYPH_WARNING: {
      Pt tri[3] = { { cx, cy - s * 0.85 }, { cx + s * 0.92, cy + s * 0.72 },
                    { cx - s * 0.92, cy + s * 0.72 } };
      polyline (c, tri, 3, half, colour, 1.0, TRUE);
      Pt bar[2] = { { cx, cy - s * 0.25 }, { cx, cy + s * 0.22 } };
      polyline (c, bar, 2, half, colour, 1.0, FALSE);
      RECT dot = { (int) (cx - half), (int) (cy + s * 0.42),
                   (int) (cx + half + 1), (int) (cy + s * 0.42 + 2 * half + 1) };
      ly_round (c, dot, half, colour, 1.0);
      break;
    }
    case LY_GLYPH_LOCK: {
      arc_stroke (c, cx, cy - s * 0.18, s * 0.45, G_PI, G_PI * 2, half * 0.9, colour, 1.0);
      RECT body = { (int) lround (cx - s * 0.72), (int) lround (cy - s * 0.16),
                    (int) lround (cx + s * 0.72), (int) lround (cy + s * 0.82) };
      if (filled)
        ly_round (c, body, s * 0.22, colour, 1.0);
      else
        ly_round_ring (c, body, s * 0.22, half * 2, colour, 1.0);
      break;
    }
    case LY_GLYPH_GLOBE: {
      arc_stroke (c, cx, cy, s * 0.88, 0, G_PI * 2, half, colour, 1.0);
      Pt mid[2] = { { cx - s * 0.88, cy }, { cx + s * 0.88, cy } };
      polyline (c, mid, 2, half * 0.8, colour, 0.75, FALSE);
      /* Two meridians, drawn as arcs bulging either way. */
      arc_stroke (c, cx - s * 0.62, cy, s * 0.62, -G_PI / 2, G_PI / 2, half * 0.8,
                  colour, 0.75);
      arc_stroke (c, cx + s * 0.62, cy, s * 0.62, G_PI / 2, G_PI * 1.5, half * 0.8,
                  colour, 0.75);
      break;
    }
    case LY_GLYPH_STAR: {
      Pt p[10];
      for (int i = 0; i < 10; i++) {
        double a = -G_PI / 2 + i * G_PI / 5;
        double rad = (i % 2 == 0) ? s : s * 0.44;
        p[i].x = cx + cos (a) * rad;
        p[i].y = cy + sin (a) * rad;
      }
      if (filled)
        fill_polygon (c, p, 10, colour, 1.0);
      else
        polyline (c, p, 10, half * 0.9, colour, 1.0, TRUE);
      break;
    }
    case LY_GLYPH_SHIELD: {
      Pt p[6] = {
        { cx,            cy - s * 0.95 },
        { cx + s * 0.85, cy - s * 0.55 },
        { cx + s * 0.72, cy + s * 0.35 },
        { cx,            cy + s * 0.95 },
        { cx - s * 0.72, cy + s * 0.35 },
        { cx - s * 0.85, cy - s * 0.55 },
      };
      if (filled)
        fill_polygon (c, p, 6, colour, 1.0);
      else
        polyline (c, p, 6, half * 0.95, colour, 1.0, TRUE);
      break;
    }
  }
}

void
ly_switch (LyCanvas *c, RECT r, gboolean on, const LyTheme *theme)
{
  double h = r.bottom - r.top;
  ly_round (c, r, h / 2.0, on ? theme->accent : ly_mix (theme->surface_alt,
                                                        theme->line, 0.6),
            1.0);
  if (!on)
    ly_round_ring (c, r, h / 2.0, 1.0, theme->line, 0.9);

  double inset = MAX (2.0, h / 8.0);
  double d = h - inset * 2.0;
  double x = on ? r.right - inset - d : r.left + inset;
  RECT knob = { (int) lround (x), (int) lround (r.top + inset),
                (int) lround (x + d), (int) lround (r.top + inset + d) };
  ly_round_shadow (c, knob, d / 2.0, theme->shadow, 0.18, 1, 2);
  ly_round (c, knob, d / 2.0, on ? theme->accent_text : RGB (255, 255, 255), 1.0);
}
