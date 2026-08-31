/* chrome.c — see chrome.h. */

#include "chrome.h"

#include <dwmapi.h>
#include <windowsx.h>
#include <shlwapi.h>
#include <string.h>

#define CLASS_NAME     L"LyndonWindow"
#define ID_ADDRESS     1001

/* Design sizes at 96 dpi; everything is scaled by the window's own DPI. */
#define TABBAR_H        36
#define TOOLBAR_H       44
#define TAB_MAX_W      220
#define TAB_MIN_W       64
#define BTN_W           34
#define PAD              8

static guint window_count;
/* Every live window, so that the environment becoming ready can reach the
 * ones created before it did. Not owned: WM_DESTROY removes them. */
static GPtrArray *windows;
static gboolean env_ok;
static gboolean env_settled;
static char    *env_message;

/* ------------------------------------------------------------------ theme */

typedef struct {
  COLORREF bg;        /* the chrome behind the tabs      */
  COLORREF tab;       /* the selected tab and toolbar    */
  COLORREF tab_idle;
  COLORREF text;
  COLORREF text_dim;
  COLORREF line;
  COLORREF field;     /* the address bar                 */
  COLORREF accent;
} Theme;

static gboolean
system_is_dark (void)
{
  DWORD value = 1, size = sizeof value;
  if (RegGetValueW (HKEY_CURRENT_USER,
                    L"Software\\Microsoft\\Windows\\CurrentVersion\\Themes\\Personalize",
                    L"AppsUseLightTheme", RRF_RT_REG_DWORD, NULL, &value, &size) != ERROR_SUCCESS)
    return FALSE;
  return value == 0;
}

static void
theme_for (Theme *t, gboolean dark)
{
  if (dark) {
    t->bg       = RGB (0x1c, 0x1c, 0x20);
    t->tab      = RGB (0x2b, 0x2b, 0x31);
    t->tab_idle = RGB (0x22, 0x22, 0x27);
    t->text     = RGB (0xe8, 0xe8, 0xec);
    t->text_dim = RGB (0x9a, 0x9a, 0xa4);
    t->line     = RGB (0x38, 0x38, 0x40);
    t->field    = RGB (0x35, 0x35, 0x3d);
    t->accent   = RGB (0x62, 0xa0, 0xea);
  } else {
    t->bg       = RGB (0xdf, 0xe1, 0xe6);
    t->tab      = RGB (0xf8, 0xf9, 0xfb);
    t->tab_idle = RGB (0xe9, 0xea, 0xee);
    t->text     = RGB (0x1b, 0x1d, 0x22);
    t->text_dim = RGB (0x60, 0x65, 0x70);
    t->line     = RGB (0xc6, 0xc9, 0xd0);
    t->field    = RGB (0xff, 0xff, 0xff);
    t->accent   = RGB (0x1c, 0x71, 0xd8);
  }
}

/* ----------------------------------------------------------------- window */

typedef enum {
  HIT_NONE = 0,
  HIT_TAB,
  HIT_TAB_CLOSE,
  HIT_NEWTAB,
  HIT_BACK,
  HIT_FORWARD,
  HIT_RELOAD,
  HIT_SHIELD,
} HitKind;

struct _LyWindow {
  HWND       hwnd;
  HWND       address;
  WNDPROC    address_proc;
  HINSTANCE  instance;

  LyConfig  *cfg;
  LyStore   *store;
  LyBlock   *block;

  GPtrArray *tabs;          /* LyTab*            */
  int        active;
  gboolean   address_dirty; /* user is typing; do not overwrite */

  Theme      theme;
  gboolean   dark;
  HFONT      font;
  HFONT      font_small;
  int        dpi;

  HitKind    hot_kind;
  int        hot_tab;
  char      *pending_url;   /* wanted a tab before the runtime was up */
};

static void layout (LyWindow *win);
static void redraw_chrome (LyWindow *win);

static int
sc (LyWindow *win, int v)
{
  return MulDiv (v, win->dpi, 96);
}

static LyTab *
active_tab (LyWindow *win)
{
  if (win->active < 0 || (guint) win->active >= win->tabs->len)
    return NULL;
  return g_ptr_array_index (win->tabs, win->active);
}

/* ------------------------------------------------------------ geometry */

static RECT
tab_area (LyWindow *win)
{
  RECT c;
  GetClientRect (win->hwnd, &c);
  RECT r = { 0, 0, c.right, sc (win, TABBAR_H) };
  return r;
}

static int
tab_width (LyWindow *win)
{
  RECT bar = tab_area (win);
  int usable = bar.right - sc (win, BTN_W) - sc (win, PAD);
  guint n = win->tabs->len ? win->tabs->len : 1;
  int w = usable / (int) n;
  return CLAMP (w, sc (win, TAB_MIN_W), sc (win, TAB_MAX_W));
}

static RECT
tab_rect (LyWindow *win, guint i)
{
  int w = tab_width (win);
  RECT r = { (int) i * w, 0, (int) i * w + w, sc (win, TABBAR_H) };
  return r;
}

static RECT
newtab_rect (LyWindow *win)
{
  int w = tab_width (win);
  int x = (int) win->tabs->len * w + sc (win, 4);
  RECT r = { x, sc (win, 6), x + sc (win, 24), sc (win, TABBAR_H) - sc (win, 6) };
  return r;
}

static RECT
button_rect (LyWindow *win, int slot)
{
  int y = sc (win, TABBAR_H);
  int h = sc (win, TOOLBAR_H);
  int w = sc (win, BTN_W);
  RECT r = { sc (win, PAD) + slot * w, y + sc (win, 6),
             sc (win, PAD) + slot * w + w, y + h - sc (win, 6) };
  return r;
}

static RECT
shield_rect (LyWindow *win)
{
  RECT c;
  GetClientRect (win->hwnd, &c);
  int y = sc (win, TABBAR_H);
  int h = sc (win, TOOLBAR_H);
  RECT r = { c.right - sc (win, PAD) - sc (win, 44), y + sc (win, 6),
             c.right - sc (win, PAD), y + h - sc (win, 6) };
  return r;
}

static RECT
address_rect (LyWindow *win)
{
  RECT c;
  GetClientRect (win->hwnd, &c);
  RECT last = button_rect (win, 3);
  RECT sh = shield_rect (win);
  int y = sc (win, TABBAR_H);
  int h = sc (win, TOOLBAR_H);
  RECT r = { last.right + sc (win, 4), y + sc (win, 7),
             sh.left - sc (win, 8), y + h - sc (win, 7) };
  return r;
}

static RECT
page_rect (LyWindow *win)
{
  RECT c;
  GetClientRect (win->hwnd, &c);
  RECT r = { 0, sc (win, TABBAR_H) + sc (win, TOOLBAR_H), c.right, c.bottom };
  if (r.bottom < r.top)
    r.bottom = r.top;
  return r;
}

/* -------------------------------------------------------------- painting */

static void
fill (HDC dc, RECT r, COLORREF colour)
{
  HBRUSH b = CreateSolidBrush (colour);
  FillRect (dc, &r, b);
  DeleteObject (b);
}

static void
draw_text_in (HDC dc, RECT r, const char *utf8, COLORREF colour, HFONT font, UINT flags)
{
  if (utf8 == NULL)
    return;
  g_autofree wchar_t *w = (wchar_t *) g_utf8_to_utf16 (utf8, -1, NULL, NULL, NULL);
  if (w == NULL)
    return;
  HFONT old = SelectObject (dc, font);
  SetTextColor (dc, colour);
  SetBkMode (dc, TRANSPARENT);
  DrawTextW (dc, w, -1, &r, flags);
  SelectObject (dc, old);
}

/* The toolbar glyphs. Drawn with lines rather than a font so they do not
 * depend on Segoe UI Symbol being present and cannot fall back to tofu. */
static void
draw_glyph (HDC dc, RECT r, HitKind kind, COLORREF colour, gboolean on)
{
  int cx = (r.left + r.right) / 2;
  int cy = (r.top + r.bottom) / 2;
  int s  = (r.bottom - r.top) / 5;
  if (s < 3)
    s = 3;

  HPEN pen = CreatePen (PS_SOLID, MAX (1, s / 3), colour);
  HPEN old = SelectObject (dc, pen);

  switch (kind) {
    case HIT_BACK:
      MoveToEx (dc, cx + s / 2, cy - s, NULL);
      LineTo (dc, cx - s / 2, cy);
      LineTo (dc, cx + s / 2, cy + s);
      break;
    case HIT_FORWARD:
      MoveToEx (dc, cx - s / 2, cy - s, NULL);
      LineTo (dc, cx + s / 2, cy);
      LineTo (dc, cx - s / 2, cy + s);
      break;
    case HIT_RELOAD: {
      /* An open circle with a tick out of it reads as "reload" at this size
         far better than a closed ring does. */
      Arc (dc, cx - s, cy - s, cx + s, cy + s,
           cx + s, cy - s / 2, cx - s / 2, cy - s);
      MoveToEx (dc, cx + s, cy - s, NULL);
      LineTo (dc, cx + s, cy - s / 4);
      LineTo (dc, cx + s / 3, cy - s / 4);
      break;
    }
    case HIT_NEWTAB:
      MoveToEx (dc, cx - s, cy, NULL);
      LineTo (dc, cx + s, cy);
      MoveToEx (dc, cx, cy - s, NULL);
      LineTo (dc, cx, cy + s);
      break;
    case HIT_TAB_CLOSE:
      MoveToEx (dc, cx - s / 2, cy - s / 2, NULL);
      LineTo (dc, cx + s / 2 + 1, cy + s / 2 + 1);
      MoveToEx (dc, cx + s / 2, cy - s / 2, NULL);
      LineTo (dc, cx - s / 2 - 1, cy + s / 2 + 1);
      break;
    case HIT_SHIELD: {
      /* A shield outline; filled when blocking is on for this tab. */
      POINT p[5] = {
        { cx,     cy - s     }, { cx + s, cy - s / 2 }, { cx + s / 2, cy + s },
        { cx - s / 2, cy + s }, { cx - s, cy - s / 2 },
      };
      if (on) {
        HBRUSH br = CreateSolidBrush (colour);
        HBRUSH ob = SelectObject (dc, br);
        Polygon (dc, p, 5);
        SelectObject (dc, ob);
        DeleteObject (br);
      } else {
        Polyline (dc, p, 5);
        LineTo (dc, p[0].x, p[0].y);
      }
      break;
    }
    default:
      break;
  }

  SelectObject (dc, old);
  DeleteObject (pen);
}

static void
paint (LyWindow *win, HDC dc)
{
  Theme *t = &win->theme;
  RECT c;
  GetClientRect (win->hwnd, &c);

  /* tab strip background */
  RECT bar = tab_area (win);
  fill (dc, bar, t->bg);

  /* toolbar background */
  RECT tool = { 0, sc (win, TABBAR_H), c.right, sc (win, TABBAR_H) + sc (win, TOOLBAR_H) };
  fill (dc, tool, t->tab);
  RECT edge = { 0, tool.bottom - 1, c.right, tool.bottom };
  fill (dc, edge, t->line);

  /* tabs */
  for (guint i = 0; i < win->tabs->len; i++) {
    LyTab *tab = g_ptr_array_index (win->tabs, i);
    RECT r = tab_rect (win, i);
    gboolean sel = ((int) i == win->active);

    RECT inner = r;
    inner.left += sc (win, 2);
    inner.right -= sc (win, 2);
    inner.top += sc (win, 4);
    fill (dc, inner, sel ? t->tab : t->tab_idle);

    RECT label = inner;
    label.left += sc (win, 10);
    label.right -= sc (win, 24);
    const char *title = ly_tab_title (tab);
    if (ly_tab_loading (tab))
      title = "Loading…";
    draw_text_in (dc, label, title, sel ? t->text : t->text_dim,
                  win->font_small, DT_SINGLELINE | DT_VCENTER | DT_END_ELLIPSIS | DT_NOPREFIX);

    if (inner.right - inner.left > sc (win, 90)) {
      RECT x = { inner.right - sc (win, 22), inner.top + sc (win, 4),
                 inner.right - sc (win, 4), inner.bottom - sc (win, 4) };
      gboolean hot = (win->hot_kind == HIT_TAB_CLOSE && win->hot_tab == (int) i);
      if (hot)
        fill (dc, x, t->line);
      draw_glyph (dc, x, HIT_TAB_CLOSE, sel ? t->text : t->text_dim, FALSE);
    }
  }

  /* new tab */
  RECT nt = newtab_rect (win);
  if (win->hot_kind == HIT_NEWTAB)
    fill (dc, nt, t->tab_idle);
  draw_glyph (dc, nt, HIT_NEWTAB, t->text_dim, FALSE);

  /* navigation buttons */
  LyTab *tab = active_tab (win);
  struct { HitKind kind; gboolean on; } btns[] = {
    { HIT_BACK,    tab && ly_tab_can_back (tab) },
    { HIT_FORWARD, tab && ly_tab_can_forward (tab) },
    { HIT_RELOAD,  tab != NULL },
  };
  for (int i = 0; i < 3; i++) {
    RECT r = button_rect (win, i);
    if (win->hot_kind == btns[i].kind && btns[i].on)
      fill (dc, r, t->tab_idle);
    draw_glyph (dc, r, btns[i].kind, btns[i].on ? t->text : t->line, FALSE);
  }

  /* the shield, with what it has stopped on this page */
  RECT sh = shield_rect (win);
  gboolean blocking = tab && ly_tab_blocking (tab);
  RECT icon = sh;
  icon.right = sh.left + sc (win, 22);
  draw_glyph (dc, icon, HIT_SHIELD, blocking ? t->accent : t->text_dim, blocking);
  if (tab && blocking) {
    RECT num = sh;
    num.left = icon.right;
    g_autofree char *n = g_strdup_printf ("%u", ly_tab_blocked (tab));
    draw_text_in (dc, num, n, t->text_dim, win->font_small,
                  DT_SINGLELINE | DT_VCENTER | DT_LEFT | DT_NOPREFIX);
  }
}

/* ------------------------------------------------------------- hit testing */

static HitKind
hit_test (LyWindow *win, POINT p, int *index)
{
  *index = -1;

  if (p.y < sc (win, TABBAR_H)) {
    for (guint i = 0; i < win->tabs->len; i++) {
      RECT r = tab_rect (win, i);
      if (!PtInRect (&r, p))
        continue;
      *index = (int) i;
      RECT x = { r.right - sc (win, 26), r.top, r.right - sc (win, 2), r.bottom };
      return PtInRect (&x, p) ? HIT_TAB_CLOSE : HIT_TAB;
    }
    RECT nt = newtab_rect (win);
    if (PtInRect (&nt, p))
      return HIT_NEWTAB;
    return HIT_NONE;
  }

  for (int i = 0; i < 3; i++) {
    RECT r = button_rect (win, i);
    if (PtInRect (&r, p))
      return (HitKind) (HIT_BACK + i);
  }
  RECT sh = shield_rect (win);
  if (PtInRect (&sh, p))
    return HIT_SHIELD;
  return HIT_NONE;
}

/* ------------------------------------------------------------------- tabs */

static void
sync_address (LyWindow *win)
{
  LyTab *tab = active_tab (win);
  if (tab == NULL || win->address_dirty)
    return;
  if (GetFocus () == win->address)
    return;
  g_autofree char *pretty = ly_pretty_uri (ly_tab_url (tab));
  g_autofree wchar_t *w =
      (wchar_t *) g_utf8_to_utf16 (pretty ? pretty : "", -1, NULL, NULL, NULL);
  SetWindowTextW (win->address, w ? w : L"");
}

static void
update_title (LyWindow *win)
{
  LyTab *tab = active_tab (win);
  const char *t = tab ? ly_tab_title (tab) : NULL;
  g_autofree char *full =
      g_strdup_printf ("%s%sLyndon", (t && *t) ? t : "", (t && *t) ? " — " : "");
  g_autofree wchar_t *w = (wchar_t *) g_utf8_to_utf16 (full, -1, NULL, NULL, NULL);
  SetWindowTextW (win->hwnd, w ? w : L"Lyndon");
}

static void
on_tab_changed (LyTab *tab, gpointer data)
{
  LyWindow *win = data;
  if (tab == active_tab (win)) {
    sync_address (win);
    update_title (win);
  }
  /* A background tab still redraws: its title and spinner are in the strip. */
  redraw_chrome (win);

  /* Record the visit once the load has finished and a title exists. */
  if (!ly_tab_loading (tab) && win->store && win->cfg && win->cfg->remember_history) {
    const char *url = ly_tab_url (tab);
    if (url && *url && !ly_uri_is_internal (url)) {
      ly_store_record_visit (win->store, url, ly_tab_title (tab));
      ly_store_update_title (win->store, url, ly_tab_title (tab));
    }
  }
}

static void on_tab_new_window (LyTab *source, const char *url, gpointer data);
static gboolean on_tab_accelerator (LyTab *tab, guint vkey, gpointer data);

static void
select_tab (LyWindow *win, int index)
{
  if (index < 0 || (guint) index >= win->tabs->len)
    return;
  for (guint i = 0; i < win->tabs->len; i++)
    ly_tab_set_visible (g_ptr_array_index (win->tabs, i), (int) i == index);
  win->active = index;
  win->address_dirty = FALSE;
  sync_address (win);
  update_title (win);
  layout (win);
  redraw_chrome (win);
}

void
ly_window_open_tab (LyWindow *win, const char *url)
{
  const char *target = url;
  if (target == NULL || *target == '\0')
    target = (win->cfg && win->cfg->homepage && *win->cfg->homepage)
                 ? win->cfg->homepage : "about:blank";

  if (!ly_webview_ready ()) {
    g_free (win->pending_url);
    win->pending_url = g_strdup (target);
    return;
  }

  LyTab *tab = ly_tab_new (win->hwnd, win->cfg, win->block, target);
  ly_tab_set_callbacks (tab, on_tab_changed, on_tab_new_window, win);
  ly_tab_set_accelerator_handler (tab, on_tab_accelerator);
  g_ptr_array_add (win->tabs, tab);
  select_tab (win, (int) win->tabs->len - 1);
}

static void
on_tab_new_window (LyTab *source, const char *url, gpointer data)
{
  ly_window_open_tab ((LyWindow *) data, url);
}

static void
close_tab (LyWindow *win, int index)
{
  if (index < 0 || (guint) index >= win->tabs->len)
    return;
  LyTab *tab = g_ptr_array_index (win->tabs, index);
  g_ptr_array_remove_index (win->tabs, index);
  ly_tab_free (tab);

  if (win->tabs->len == 0) {
    DestroyWindow (win->hwnd);
    return;
  }
  select_tab (win, MIN (index, (int) win->tabs->len - 1));
}

/* ----------------------------------------------------------------- layout */

static void
layout (LyWindow *win)
{
  RECT a = address_rect (win);
  if (win->address)
    MoveWindow (win->address, a.left + sc (win, 8), a.top + sc (win, 4),
                (a.right - a.left) - sc (win, 16), (a.bottom - a.top) - sc (win, 8), TRUE);

  RECT page = page_rect (win);
  for (guint i = 0; i < win->tabs->len; i++)
    ly_tab_set_bounds (g_ptr_array_index (win->tabs, i), page);
}

static void
redraw_chrome (LyWindow *win)
{
  RECT c;
  GetClientRect (win->hwnd, &c);
  c.bottom = sc (win, TABBAR_H) + sc (win, TOOLBAR_H);
  InvalidateRect (win->hwnd, &c, FALSE);
}

/* ----------------------------------------------------------- address bar */

static void
go (LyWindow *win)
{
  wchar_t buf[4096];
  GetWindowTextW (win->address, buf, G_N_ELEMENTS (buf));
  g_autofree char *text = g_utf16_to_utf8 ((const gunichar2 *) buf, -1, NULL, NULL, NULL);
  if (text == NULL || *text == '\0')
    return;

  g_autofree char *url = win->cfg ? ly_config_resolve_input (win->cfg, text)
                                  : ly_normalise_input (text, NULL);
  if (url == NULL)
    return;

  LyTab *tab = active_tab (win);
  if (tab == NULL) {
    ly_window_open_tab (win, url);
    return;
  }
  win->address_dirty = FALSE;
  ly_tab_navigate (tab, url);
  ly_tab_focus (tab);
}

static LRESULT CALLBACK
address_proc (HWND hwnd, UINT msg, WPARAM wp, LPARAM lp)
{
  LyWindow *win = (LyWindow *) GetWindowLongPtrW (hwnd, GWLP_USERDATA);

  switch (msg) {
    case WM_CHAR:
      /* Enter would otherwise beep: a single-line EDIT has nowhere to put it. */
      if (wp == VK_RETURN)
        return 0;
      if (wp == VK_ESCAPE) {
        win->address_dirty = FALSE;
        sync_address (win);
        LyTab *t = active_tab (win);
        if (t)
          ly_tab_focus (t);
        return 0;
      }
      win->address_dirty = TRUE;
      break;

    case WM_KEYDOWN:
      if (wp == VK_RETURN) {
        go (win);
        return 0;
      }
      break;

    case WM_SETFOCUS:
      /* Select all on focus, the way every browser does. */
      PostMessageW (hwnd, EM_SETSEL, 0, -1);
      break;
  }
  return CallWindowProcW (win->address_proc, hwnd, msg, wp, lp);
}

/* ------------------------------------------------------------ window proc */

static void
apply_dark_titlebar (HWND hwnd, gboolean dark)
{
  /* DWMWA_USE_IMMERSIVE_DARK_MODE. 20 on current Windows 10 and 11, 19 on
   * the 1809-to-1903 builds; setting both is what everyone ends up doing. */
  BOOL on = dark;
  DwmSetWindowAttribute (hwnd, 20, &on, sizeof on);
  DwmSetWindowAttribute (hwnd, 19, &on, sizeof on);
}

static void
make_fonts (LyWindow *win)
{
  if (win->font)
    DeleteObject (win->font);
  if (win->font_small)
    DeleteObject (win->font_small);
  win->font = CreateFontW (-sc (win, 15), 0, 0, 0, FW_NORMAL, 0, 0, 0,
                           DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS,
                           CLEARTYPE_QUALITY, VARIABLE_PITCH, L"Segoe UI");
  win->font_small = CreateFontW (-sc (win, 13), 0, 0, 0, FW_NORMAL, 0, 0, 0,
                                 DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS,
                                 CLEARTYPE_QUALITY, VARIABLE_PITCH, L"Segoe UI");
}

static LRESULT CALLBACK
window_proc (HWND hwnd, UINT msg, WPARAM wp, LPARAM lp)
{
  LyWindow *win = (LyWindow *) GetWindowLongPtrW (hwnd, GWLP_USERDATA);

  switch (msg) {
    case WM_NCCREATE: {
      CREATESTRUCTW *cs = (CREATESTRUCTW *) lp;
      SetWindowLongPtrW (hwnd, GWLP_USERDATA, (LONG_PTR) cs->lpCreateParams);
      return DefWindowProcW (hwnd, msg, wp, lp);
    }

    case WM_CREATE: {
      win->hwnd = hwnd;
      win->dpi = (int) GetDpiForWindow (hwnd);
      if (win->dpi <= 0)
        win->dpi = 96;
      win->dark = system_is_dark ();
      theme_for (&win->theme, win->dark);
      make_fonts (win);
      apply_dark_titlebar (hwnd, win->dark);

      win->address = CreateWindowExW (
          0, L"EDIT", L"",
          WS_CHILD | WS_VISIBLE | ES_AUTOHSCROLL | ES_LEFT,
          0, 0, 10, 10, hwnd, (HMENU) (UINT_PTR) ID_ADDRESS, win->instance, NULL);
      SendMessageW (win->address, WM_SETFONT, (WPARAM) win->font, TRUE);
      SetWindowLongPtrW (win->address, GWLP_USERDATA, (LONG_PTR) win);
      win->address_proc = (WNDPROC) SetWindowLongPtrW (
          win->address, GWLP_WNDPROC, (LONG_PTR) address_proc);
      layout (win);
      return 0;
    }

    case WM_SIZE:
      layout (win);
      return 0;

    case WM_DPICHANGED: {
      win->dpi = HIWORD (wp);
      make_fonts (win);
      SendMessageW (win->address, WM_SETFONT, (WPARAM) win->font, TRUE);
      RECT *r = (RECT *) lp;
      SetWindowPos (hwnd, NULL, r->left, r->top, r->right - r->left,
                    r->bottom - r->top, SWP_NOZORDER | SWP_NOACTIVATE);
      layout (win);
      return 0;
    }

    case WM_SETTINGCHANGE: {
      gboolean dark = system_is_dark ();
      if (dark != win->dark) {
        win->dark = dark;
        theme_for (&win->theme, dark);
        apply_dark_titlebar (hwnd, dark);
        InvalidateRect (hwnd, NULL, TRUE);
      }
      return 0;
    }

    case WM_CTLCOLOREDIT: {
      HDC dc = (HDC) wp;
      SetTextColor (dc, win->theme.text);
      SetBkColor (dc, win->theme.field);
      static HBRUSH brush;
      if (brush)
        DeleteObject (brush);
      brush = CreateSolidBrush (win->theme.field);
      return (LRESULT) brush;
    }

    case WM_ERASEBKGND:
      return 1;   /* WM_PAINT covers all of it */

    case WM_PAINT: {
      PAINTSTRUCT ps;
      HDC dc = BeginPaint (hwnd, &ps);
      /* Double buffered: the strip repaints on every title change and would
         otherwise flicker over the page. */
      RECT c;
      GetClientRect (hwnd, &c);
      int h = sc (win, TABBAR_H) + sc (win, TOOLBAR_H);
      HDC mem = CreateCompatibleDC (dc);
      HBITMAP bmp = CreateCompatibleBitmap (dc, c.right, h);
      HBITMAP old = SelectObject (mem, bmp);
      paint (win, mem);
      BitBlt (dc, 0, 0, c.right, h, mem, 0, 0, SRCCOPY);
      SelectObject (mem, old);
      DeleteObject (bmp);
      DeleteDC (mem);
      EndPaint (hwnd, &ps);
      return 0;
    }

    case WM_MOUSEMOVE: {
      POINT p = { GET_X_LPARAM (lp), GET_Y_LPARAM (lp) };
      int index;
      HitKind k = hit_test (win, p, &index);
      if (k != win->hot_kind || index != win->hot_tab) {
        win->hot_kind = k;
        win->hot_tab = index;
        redraw_chrome (win);
        TRACKMOUSEEVENT tme = { sizeof tme, TME_LEAVE, hwnd, 0 };
        TrackMouseEvent (&tme);
      }
      return 0;
    }

    case WM_MOUSELEAVE:
      win->hot_kind = HIT_NONE;
      win->hot_tab = -1;
      redraw_chrome (win);
      return 0;

    case WM_LBUTTONDOWN: {
      POINT p = { GET_X_LPARAM (lp), GET_Y_LPARAM (lp) };
      int index;
      LyTab *tab = active_tab (win);
      switch (hit_test (win, p, &index)) {
        case HIT_TAB:        select_tab (win, index); break;
        case HIT_TAB_CLOSE:  close_tab (win, index); break;
        case HIT_NEWTAB:     ly_window_open_tab (win, NULL); break;
        case HIT_BACK:       if (tab) ly_tab_back (tab); break;
        case HIT_FORWARD:    if (tab) ly_tab_forward (tab); break;
        case HIT_RELOAD:     if (tab) ly_tab_reload (tab); break;
        case HIT_SHIELD:
          if (tab) {
            ly_tab_set_blocking (tab, !ly_tab_blocking (tab));
            ly_tab_reload (tab);
          }
          break;
        default: break;
      }
      return 0;
    }

    case WM_MBUTTONDOWN: {
      POINT p = { GET_X_LPARAM (lp), GET_Y_LPARAM (lp) };
      int index;
      if (hit_test (win, p, &index) == HIT_TAB || index >= 0)
        close_tab (win, index);
      return 0;
    }

    case WM_SETFOCUS: {
      LyTab *tab = active_tab (win);
      if (tab)
        ly_tab_focus (tab);
      return 0;
    }

    case WM_CLOSE:
      DestroyWindow (hwnd);
      return 0;

    case WM_DESTROY: {
      if (windows)
        g_ptr_array_remove_fast (windows, win);
      for (guint i = 0; i < win->tabs->len; i++)
        ly_tab_free (g_ptr_array_index (win->tabs, i));
      g_ptr_array_free (win->tabs, TRUE);
      if (win->font)
        DeleteObject (win->font);
      if (win->font_small)
        DeleteObject (win->font_small);
      g_free (win->pending_url);
      g_free (win);
      if (--window_count == 0)
        PostQuitMessage (0);
      return 0;
    }
  }
  return DefWindowProcW (hwnd, msg, wp, lp);
}

/* --------------------------------------------------------------- keyboard */

/* The browser's own shortcuts, from whichever of the two places the key
 * arrived. When the chrome has the focus that is the message loop; when a
 * page has it, the message loop never sees the key at all and WebView2's
 * AcceleratorKeyPressed is the only route. Both end up here so the two can
 * never drift apart. */
static gboolean
handle_vkey (LyWindow *win, guint vkey)
{
  gboolean ctrl = (GetKeyState (VK_CONTROL) & 0x8000) != 0;
  gboolean shift = (GetKeyState (VK_SHIFT) & 0x8000) != 0;
  LyTab *tab = active_tab (win);

  if (ctrl) {
    switch (vkey) {
      case 'T': ly_window_open_tab (win, NULL); return TRUE;
      case 'W': close_tab (win, win->active); return TRUE;
      case 'L': SetFocus (win->address); return TRUE;
      case 'R': if (tab) ly_tab_reload (tab); return TRUE;
      case VK_TAB: {
        int n = (int) win->tabs->len;
        if (n > 1)
          select_tab (win, shift ? (win->active + n - 1) % n : (win->active + 1) % n);
        return TRUE;
      }
      default:
        if (vkey >= '1' && vkey <= '9') {
          int want = (int) vkey - '1';
          if (want < (int) win->tabs->len)
            select_tab (win, want);
          return TRUE;
        }
    }
    return FALSE;
  }

  switch (vkey) {
    case VK_F5:
      if (tab)
        ly_tab_reload (tab);
      return TRUE;
    case VK_F6:
      SetFocus (win->address);
      return TRUE;
    case VK_BROWSER_BACK:
      if (tab)
        ly_tab_back (tab);
      return TRUE;
    case VK_BROWSER_FORWARD:
      if (tab)
        ly_tab_forward (tab);
      return TRUE;
  }
  return FALSE;
}

/* Offered by the message loop, for when the chrome rather than a page has
 * the focus. */
gboolean
ly_window_handle_key (LyWindow *win, MSG *msg)
{
  if (msg->message != WM_KEYDOWN && msg->message != WM_SYSKEYDOWN)
    return FALSE;
  /* The address bar owns its own text editing; only leave it by shortcut. */
  if (msg->hwnd == win->address) {
    gboolean ctrl = (GetKeyState (VK_CONTROL) & 0x8000) != 0;
    if (!ctrl && msg->wParam != VK_F5 && msg->wParam != VK_F6)
      return FALSE;
    if (ctrl && (msg->wParam == 'A' || msg->wParam == 'C' ||
                 msg->wParam == 'V' || msg->wParam == 'X' || msg->wParam == 'Z'))
      return FALSE;
  }
  return handle_vkey (win, (guint) msg->wParam);
}

static gboolean
on_tab_accelerator (LyTab *tab, guint vkey, gpointer data)
{
  return handle_vkey ((LyWindow *) data, vkey);
}

/* ------------------------------------------------------------------- API */

gboolean
ly_window_register (HINSTANCE instance)
{
  WNDCLASSEXW wc = { 0 };
  wc.cbSize = sizeof wc;
  wc.style = CS_HREDRAW | CS_VREDRAW;
  wc.lpfnWndProc = window_proc;
  wc.hInstance = instance;
  wc.hCursor = LoadCursorW (NULL, IDC_ARROW);
  wc.hbrBackground = NULL;
  wc.lpszClassName = CLASS_NAME;
  wc.hIcon = LoadIconW (instance, L"APPICON");
  wc.hIconSm = wc.hIcon;
  return RegisterClassExW (&wc) != 0;
}

LyWindow *
ly_window_new (HINSTANCE instance, LyConfig *cfg, LyStore *store,
               LyBlock *block, const char *url)
{
  LyWindow *win = g_new0 (LyWindow, 1);
  win->instance = instance;
  win->cfg = cfg;
  win->store = store;
  win->block = block;
  win->tabs = g_ptr_array_new ();
  win->active = -1;
  win->hot_tab = -1;
  win->dpi = 96;

  int w = 1200, h = 820;
  HWND hwnd = CreateWindowExW (
      0, CLASS_NAME, L"Lyndon", WS_OVERLAPPEDWINDOW,
      CW_USEDEFAULT, CW_USEDEFAULT, w, h, NULL, NULL, instance, win);
  if (hwnd == NULL) {
    g_ptr_array_free (win->tabs, TRUE);
    g_free (win);
    return NULL;
  }

  window_count++;
  if (windows == NULL)
    windows = g_ptr_array_new ();
  g_ptr_array_add (windows, win);

  ShowWindow (hwnd, SW_SHOWDEFAULT);
  UpdateWindow (hwnd);

  if (env_settled && !env_ok) {
    g_autofree wchar_t *m = (wchar_t *) g_utf8_to_utf16 (
        env_message ? env_message : "WebView2 is unavailable.", -1, NULL, NULL, NULL);
    MessageBoxW (hwnd, m, L"Lyndon", MB_OK | MB_ICONERROR);
  } else {
    ly_window_open_tab (win, url);
  }
  return win;
}

HWND
ly_window_hwnd (LyWindow *win)
{
  return win->hwnd;
}

guint
ly_window_count (void)
{
  return window_count;
}

void
ly_window_environment_ready (gboolean ok, const char *message)
{
  env_ok = ok;
  env_settled = TRUE;
  g_free (env_message);
  env_message = g_strdup (message);

  if (windows == NULL)
    return;

  for (guint i = 0; i < windows->len; i++) {
    LyWindow *win = g_ptr_array_index (windows, i);
    if (!ok) {
      g_autofree wchar_t *m = (wchar_t *) g_utf8_to_utf16 (
          message ? message : "WebView2 is unavailable.", -1, NULL, NULL, NULL);
      MessageBoxW (win->hwnd, m, L"Lyndon", MB_OK | MB_ICONERROR);
      continue;
    }
    /* The window was asked for a tab before there was anything to put in
     * one. Now there is. */
    g_autofree char *url = g_steal_pointer (&win->pending_url);
    if (url)
      ly_window_open_tab (win, url);
  }
}

LyWindow *
ly_window_from_message (MSG *msg)
{
  if (windows == NULL)
    return NULL;
  for (HWND h = msg->hwnd; h; h = GetParent (h)) {
    for (guint i = 0; i < windows->len; i++) {
      LyWindow *win = g_ptr_array_index (windows, i);
      if (win->hwnd == h)
        return win;
    }
  }
  /* WebView2 puts its own top-level-looking children in the tree; fall back
   * to the foreground window when the walk finds nothing. */
  HWND active = GetActiveWindow ();
  for (guint i = 0; i < windows->len; i++) {
    LyWindow *win = g_ptr_array_index (windows, i);
    if (win->hwnd == active)
      return win;
  }
  return NULL;
}
