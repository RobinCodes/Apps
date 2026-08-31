/* chrome.c — see chrome.h. */

#include "chrome.h"
#include "panel.h"
#include "prefs.h"

#include <dwmapi.h>
#include <windowsx.h>
#include <math.h>      /* the star in the bookmark button */
#include <shlwapi.h>
#include <string.h>

#define CLASS_NAME     L"LyndonWindow"
#define ID_ADDRESS     1001
#define TIMER_PROGRESS    1   /* the indeterminate load bar */

static guint window_count;
/* Every live window, so that the environment becoming ready can reach the
 * ones created before it did. Not owned: WM_DESTROY removes them. */
static GPtrArray *windows;
static gboolean env_ok;
static gboolean env_settled;
static char    *env_message;

/* ------------------------------------------------------------------ sizes */

/* Straight out of data/css/lyndon.css, so the two builds are the same shape
 * and not merely the same idea. Everything is at 96 dpi and scaled by sc(). */
#define TOOLBAR_H          42   /* headerbar min-height          */
#define TOOLBAR_H_COMPACT  36   /* .lyndon-compact               */
#define TAB_H              30   /* tabbox > tab min-height       */
#define TAB_R               9   /* tab and button border-radius  */
#define TAB_GAP             1   /* tab margin: 0 1px             */
#define TAB_PAD             6   /* tab padding: 0 6px            */
#define TAB_MAX_W         240
#define TAB_MIN_W          78
#define STRIP_SIDE          4   /* tabbar .box padding: 0 4px 3px */
#define STRIP_BOTTOM        3
#define BTN                30   /* image-button min-width/height  */
#define URL_H              32   /* .lyndon-url min-height         */
#define URL_R              16   /* .lyndon-url border-radius      */
#define PAD                 6   /* headerbar padding: 0 6px       */
#define BADGE_R             7   /* .lyndon-shield-count           */
#define PROGRESS_H          2   /* .lyndon-progress               */

/* ----------------------------------------------------------------- window */

typedef enum {
  HIT_NONE = 0,
  HIT_TAB,
  HIT_TAB_CLOSE,
  HIT_NEWTAB,
  HIT_BACK,
  HIT_FORWARD,
  HIT_RELOAD,
  HIT_HOME,
  HIT_ADDRESS,
  /* the right-hand end, in the order they are drawn from the edge inwards */
  HIT_MENU,
  HIT_LIBRARY,
  HIT_SHIELD,
  HIT_STAR,
} HitKind;

#define RIGHT_N 4
static const HitKind RIGHT_ORDER[RIGHT_N] = {
  HIT_MENU, HIT_LIBRARY, HIT_SHIELD, HIT_STAR
};
/* The shield is wider because the count sits beside it in a pill. */
static const int RIGHT_WIDTH[RIGHT_N] = { 32, 32, 52, 32 };

struct _LyWindow {
  HWND       hwnd;
  HWND       address;
  WNDPROC    address_proc;
  HINSTANCE  instance;

  LyConfig    *cfg;
  LyStore     *store;
  LyBlock     *block;
  LyDownloads *downloads;
  LyPasswords *passwords;
  LyPanel     *panel;
  gboolean     bookmarked;

  GPtrArray *tabs;          /* LyTab*            */
  int        active;
  gboolean   address_dirty; /* user is typing; do not overwrite */
  /* The entry is a real EDIT control, but only while it has the focus. The
   * rest of the time the URL is painted here instead, which is the only way
   * to dim the scheme and the path so the eye lands on the domain — an EDIT
   * has one colour for all of its text. */
  gboolean   editing_url;

  LyTheme    theme;
  LyFonts    fonts;
  int        dpi;

  HitKind    hot_kind;
  int        hot_tab;
  guint      progress_phase;  /* the indeterminate load bar */
  gboolean   progress_on;
  GPtrArray *pending;         /* char* */
};

static void layout (LyWindow *win);
static void redraw_chrome (LyWindow *win);
static void refresh_bookmark_state (LyWindow *win);
static void toggle_bookmark (LyWindow *win);
static void show_panel (LyWindow *win, LyPanelKind kind);
static void show_menu (LyWindow *win);
static void save_session (LyWindow *win);
static gboolean restore_session (LyWindow *win);
static void begin_url_edit (LyWindow *win);
static void end_url_edit (LyWindow *win);

static int
sc (LyWindow *win, int v)
{
  return ly_scale (win->dpi, v);
}

static LyTab *
active_tab (LyWindow *win)
{
  if (win->active < 0 || (guint) win->active >= win->tabs->len)
    return NULL;
  return g_ptr_array_index (win->tabs, win->active);
}

/* ---------------------------------------------------------------- geometry */

static int
toolbar_height (LyWindow *win)
{
  gboolean compact = win->cfg && win->cfg->compact_chrome;
  return sc (win, compact ? TOOLBAR_H_COMPACT : TOOLBAR_H);
}

/* The strip is hidden with a single tab unless the option says otherwise,
 * which is what .lyndon-chrome tabbar does on the other side. */
static gboolean
tabs_visible (LyWindow *win)
{
  if (win->tabs->len > 1)
    return TRUE;
  return win->cfg ? win->cfg->show_tab_bar_single : TRUE;
}

static int
strip_height (LyWindow *win)
{
  return tabs_visible (win) ? sc (win, TAB_H + STRIP_BOTTOM + 3) : 0;
}

static int
chrome_height (LyWindow *win)
{
  return strip_height (win) + toolbar_height (win);
}

static int
tab_width (LyWindow *win)
{
  RECT c;
  GetClientRect (win->hwnd, &c);
  int usable = c.right - sc (win, STRIP_SIDE * 2 + BTN);
  guint n = win->tabs->len ? win->tabs->len : 1;
  int w = usable / (int) n;
  return CLAMP (w, sc (win, TAB_MIN_W), sc (win, TAB_MAX_W));
}

static RECT
tab_rect (LyWindow *win, guint i)
{
  int w = tab_width (win);
  int x = sc (win, STRIP_SIDE) + (int) i * w;
  int top = sc (win, 3);
  RECT r = { x + sc (win, TAB_GAP), top,
             x + w - sc (win, TAB_GAP), top + sc (win, TAB_H) };
  return r;
}

static RECT
newtab_rect (LyWindow *win)
{
  int w = tab_width (win);
  int x = sc (win, STRIP_SIDE) + (int) win->tabs->len * w + sc (win, 2);
  int top = sc (win, 3);
  RECT r = { x, top, x + sc (win, BTN), top + sc (win, TAB_H) };
  return r;
}

static RECT
button_rect (LyWindow *win, int slot)
{
  int y = strip_height (win);
  int h = toolbar_height (win);
  int step = sc (win, BTN + 2);
  int box = sc (win, BTN);
  int left = sc (win, PAD) + slot * step;
  RECT r = { left, y + (h - box) / 2, left + box, y + (h + box) / 2 };
  return r;
}

static gboolean
has_home (LyWindow *win)
{
  return win->cfg && win->cfg->show_home_button;
}

static int
left_slots (LyWindow *win)
{
  return has_home (win) ? 4 : 3;
}

/* Slot 0 is nearest the right edge; each one is placed inside the last. */
static RECT
right_rect (LyWindow *win, int slot)
{
  RECT c;
  GetClientRect (win->hwnd, &c);
  int y = strip_height (win);
  int h = toolbar_height (win);
  int box = sc (win, BTN);

  int right = c.right - sc (win, PAD);
  for (int i = 0; i < slot && i < RIGHT_N; i++)
    right -= sc (win, RIGHT_WIDTH[i]);
  int width = sc (win, RIGHT_WIDTH[CLAMP (slot, 0, RIGHT_N - 1)]);

  RECT r = { right - width, y + (h - box) / 2, right, y + (h + box) / 2 };
  return r;
}

static int
right_edge (LyWindow *win)
{
  return right_rect (win, RIGHT_N - 1).left;
}

static RECT
address_rect (LyWindow *win)
{
  RECT last = button_rect (win, left_slots (win) - 1);
  int y = strip_height (win);
  int h = toolbar_height (win);
  int box = sc (win, URL_H);
  RECT r = { last.right + sc (win, 6), y + (h - box) / 2,
             right_edge (win) - sc (win, 6), y + (h + box) / 2 };
  if (r.right < r.left + sc (win, 80))
    r.right = r.left + sc (win, 80);
  return r;
}

/* Where the EDIT sits when the address bar is being typed into: inside the
 * pill, past the security icon. */
static RECT
address_text_rect (LyWindow *win)
{
  RECT a = address_rect (win);
  RECT r = { a.left + sc (win, 30), a.top + sc (win, 5),
             a.right - sc (win, 10), a.bottom - sc (win, 5) };
  return r;
}

static RECT
page_rect (LyWindow *win)
{
  RECT c;
  GetClientRect (win->hwnd, &c);
  RECT r = { 0, chrome_height (win), c.right, c.bottom };
  if (r.bottom < r.top)
    r.bottom = r.top;
  return r;
}

/* --------------------------------------------------------------- painting */

/* alpha(currentColor, x) from the stylesheet: the text colour laid over
 * whatever is behind it, which for the chrome is the header bar. */
static COLORREF
over_chrome (LyWindow *win, double amount)
{
  return ly_mix (win->theme.chrome, win->theme.text, amount);
}

static void
paint_tabs (LyWindow *win, LyCanvas *cv)
{
  if (!tabs_visible (win))
    return;

  for (guint i = 0; i < win->tabs->len; i++) {
    LyTab *tab = g_ptr_array_index (win->tabs, i);
    RECT r = tab_rect (win, i);
    if (r.right <= r.left)
      continue;

    gboolean selected = ((int) i == win->active);
    gboolean hot = (win->hot_kind == HIT_TAB && win->hot_tab == (int) i);
    double radius = sc (win, TAB_R);

    if (selected) {
      /* box-shadow: 0 1px 2px alpha(shade, .5) */
      ly_round_shadow (cv, r, radius, win->theme.shadow,
                       win->theme.dark ? 0.5 : 0.22, 1, 2);
      ly_round (cv, r, radius, win->theme.surface, 1.0);
    } else if (hot) {
      ly_round (cv, r, radius, over_chrome (win, 0.07), 1.0);
    }

    gboolean showing_close = (r.right - r.left) > sc (win, 96);
    RECT label = { r.left + sc (win, TAB_PAD + 4), r.top,
                   r.right - sc (win, showing_close ? 26 : TAB_PAD), r.bottom };

    const char *title = ly_tab_title (tab);
    if (title == NULL || *title == '\0')
      title = "New tab";

    ly_text (cv, label, title,
             selected ? win->theme.text : ly_mix (win->theme.chrome,
                                                  win->theme.text, 0.7),
             selected ? win->fonts.bold : win->fonts.normal,
             DT_SINGLELINE | DT_VCENTER | DT_END_ELLIPSIS | DT_NOPREFIX);

    if (showing_close) {
      RECT x = { r.right - sc (win, 25), r.top + sc (win, 4),
                 r.right - sc (win, 3), r.bottom - sc (win, 4) };
      gboolean close_hot = (win->hot_kind == HIT_TAB_CLOSE && win->hot_tab == (int) i);
      if (close_hot)
        ly_round (cv, x, sc (win, 6), over_chrome (win, 0.12), 1.0);
      ly_glyph (cv, x, LY_GLYPH_CLOSE,
                close_hot ? win->theme.text : win->theme.text_dim, FALSE);
    }
  }

  RECT nt = newtab_rect (win);
  if (win->hot_kind == HIT_NEWTAB)
    ly_round (cv, nt, sc (win, TAB_R), over_chrome (win, 0.09), 1.0);
  ly_glyph (cv, nt, LY_GLYPH_PLUS, win->theme.text_dim, FALSE);
}

/* An icon button with the hover and pressed fills the stylesheet gives them. */
static void
paint_button (LyWindow *win, LyCanvas *cv, RECT r, HitKind kind, LyGlyph glyph,
              gboolean enabled)
{
  if (enabled && win->hot_kind == kind)
    ly_round (cv, r, sc (win, TAB_R), over_chrome (win, 0.09), 1.0);

  COLORREF ink = enabled ? ly_mix (win->theme.chrome, win->theme.text, 0.78)
                         : ly_mix (win->theme.chrome, win->theme.text, 0.28);
  if (enabled && win->hot_kind == kind)
    ink = win->theme.text;
  ly_glyph (cv, r, glyph, ink, FALSE);
}

/* The security indicator and the URL, with everything but the registrable
 * domain dimmed — the domain is the only part that says where you are. */
static void
paint_address (LyWindow *win, LyCanvas *cv)
{
  RECT a = address_rect (win);
  LyTab *tab = active_tab (win);
  double radius = sc (win, URL_R);
  gboolean focused = win->editing_url;
  gboolean hot = (win->hot_kind == HIT_ADDRESS);

  if (focused) {
    /* box-shadow: 0 0 0 2px alpha(accent, .18) */
    RECT ring = { a.left - sc (win, 2), a.top - sc (win, 2),
                  a.right + sc (win, 2), a.bottom + sc (win, 2) };
    ly_round (cv, ring, radius + sc (win, 2), win->theme.accent, 0.18);
    ly_round (cv, a, radius, win->theme.field, 1.0);
    ly_round_ring (cv, a, radius, 1.0, win->theme.accent, 0.55);
  } else {
    double strength = hot ? 0.85 : 0.6;
    ly_round (cv, a, radius, ly_mix (win->theme.chrome, win->theme.field, strength),
              1.0);
    ly_round_ring (cv, a, radius, 1.0, win->theme.line, win->theme.dark ? 0.5 : 0.8);
  }

  /* The padlock. Colours from .security-secure / -insecure / -internal. */
  RECT icon = { a.left + sc (win, 7), a.top, a.left + sc (win, 27), a.bottom };
  const char *url = tab ? ly_tab_url (tab) : NULL;
  if (url && *url) {
    if (ly_uri_is_internal (url))
      ly_glyph (cv, icon, LY_GLYPH_GLOBE, ly_mix (win->theme.field,
                                                  win->theme.text, 0.5), FALSE);
    else if (ly_uri_is_secure (url))
      ly_glyph (cv, icon, LY_GLYPH_LOCK, win->theme.success, FALSE);
    else
      ly_glyph (cv, icon, LY_GLYPH_WARNING, win->theme.warning, FALSE);
  }

  if (focused)
    return;   /* the EDIT is drawing the text */

  RECT text = address_text_rect (win);
  if (url == NULL || *url == '\0') {
    ly_text (cv, text, "Search or enter an address", win->theme.text_dim,
             win->fonts.normal, DT_SINGLELINE | DT_VCENTER | DT_NOPREFIX);
    return;
  }

  g_autofree char *pretty = ly_pretty_uri (url);
  if (pretty == NULL)
    return;
  g_autofree char *host = ly_uri_host (url);

  /* Split at the end of the host, wherever that falls in the pretty form. */
  const char *split = NULL;
  if (host && *host) {
    const char *found = strstr (pretty, host);
    if (found)
      split = found + strlen (host);
  }

  if (split == NULL || ly_uri_is_internal (url)) {
    ly_text (cv, text, pretty, win->theme.text, win->fonts.normal,
             DT_SINGLELINE | DT_VCENTER | DT_NOPREFIX | DT_END_ELLIPSIS);
    return;
  }

  g_autofree char *head = g_strndup (pretty, (gsize) (split - pretty));
  int head_w = ly_text_width (cv, head, win->fonts.normal);

  ly_text (cv, text, head, win->theme.text, win->fonts.normal,
           DT_SINGLELINE | DT_VCENTER | DT_NOPREFIX);
  if (*split) {
    RECT tail = text;
    tail.left += head_w;
    ly_text (cv, tail, split, win->theme.text_dim, win->fonts.normal,
             DT_SINGLELINE | DT_VCENTER | DT_NOPREFIX | DT_END_ELLIPSIS);
  }
}

static void
paint_right (LyWindow *win, LyCanvas *cv)
{
  LyTab *tab = active_tab (win);
  gboolean blocking = tab && ly_tab_blocking (tab);
  guint active_downloads = win->downloads
    ? ly_downloads_active_count (win->downloads) : 0;

  for (int i = 0; i < RIGHT_N; i++) {
    HitKind kind = RIGHT_ORDER[i];
    RECT r = right_rect (win, i);
    gboolean hot = (win->hot_kind == kind);
    if (hot)
      ly_round (cv, r, sc (win, TAB_R), over_chrome (win, 0.09), 1.0);

    switch (kind) {
      case HIT_STAR:
        ly_glyph (cv, r, LY_GLYPH_STAR,
                  win->bookmarked ? win->theme.accent_fg
                                  : ly_mix (win->theme.chrome, win->theme.text, 0.7),
                  win->bookmarked);
        break;

      case HIT_SHIELD: {
        RECT icon = r;
        icon.right = r.left + sc (win, BTN);
        ly_glyph (cv, icon, LY_GLYPH_SHIELD,
                  blocking ? win->theme.accent_fg
                           : ly_mix (win->theme.chrome, win->theme.text, 0.4),
                  blocking);

        guint blocked = tab ? ly_tab_blocked (tab) : 0;
        if (blocking && blocked) {
          /* .lyndon-shield-count: a filled accent pill, not loose digits. */
          g_autofree char *n = blocked > 99 ? g_strdup ("99+")
                                            : g_strdup_printf ("%u", blocked);
          int w = ly_text_width (cv, n, win->fonts.tiny_bold) + sc (win, 10);
          RECT pill = { icon.right - sc (win, 4), r.top + sc (win, 5),
                        icon.right - sc (win, 4) + w, r.bottom - sc (win, 5) };
          ly_round (cv, pill, sc (win, BADGE_R), win->theme.accent, 1.0);
          ly_text (cv, pill, n, win->theme.accent_text, win->fonts.tiny_bold,
                   DT_SINGLELINE | DT_CENTER | DT_VCENTER | DT_NOPREFIX);
        }
        break;
      }

      case HIT_LIBRARY:
        ly_glyph (cv, r, LY_GLYPH_LIBRARY,
                  ly_mix (win->theme.chrome, win->theme.text, hot ? 1.0 : 0.78),
                  FALSE);
        if (active_downloads) {
          /* A dot, not a number: that something is running is the news. */
          RECT dot = { r.right - sc (win, 11), r.top + sc (win, 4),
                       r.right - sc (win, 5), r.top + sc (win, 10) };
          ly_round (cv, dot, sc (win, 3), win->theme.accent, 1.0);
        }
        break;

      default:
        ly_glyph (cv, r, LY_GLYPH_MENU,
                  ly_mix (win->theme.chrome, win->theme.text, hot ? 1.0 : 0.78),
                  FALSE);
        break;
    }
  }
}

/* A two-pixel bar along the bottom of the chrome. WebView2 reports no
 * fraction, so it is indeterminate: a bright segment sliding under a faint
 * track, which says "working" without claiming to know how far along. */
static void
paint_progress (LyWindow *win, LyCanvas *cv, RECT chrome)
{
  if (!win->progress_on)
    return;
  int h = sc (win, PROGRESS_H);
  RECT track = { 0, chrome.bottom - h, chrome.right, chrome.bottom };
  ly_fill_alpha (cv, track, win->theme.accent, 0.16);

  int width = chrome.right;
  int seg = MAX (sc (win, 120), width / 5);
  int span = width + seg;
  int x = (int) ((win->progress_phase % 100) * span / 100) - seg;

  for (int i = 0; i < seg; i++) {
    int px = x + i;
    if (px < 0 || px >= width)
      continue;
    /* Bright in the middle, feathered at both ends. */
    double t = (double) i / seg;
    double a = sin (t * G_PI);
    RECT col = { px, track.top, px + 1, track.bottom };
    ly_fill_alpha (cv, col, win->theme.accent, a);
  }
}

static void
paint (LyWindow *win, LyCanvas *cv)
{
  RECT c;
  GetClientRect (win->hwnd, &c);
  RECT chrome = { 0, 0, c.right, chrome_height (win) };

  ly_fill (cv, chrome, win->theme.chrome);

  paint_tabs (win, cv);
  paint_address (win, cv);

  LyTab *tab = active_tab (win);
  struct { HitKind kind; LyGlyph glyph; gboolean on; } left[] = {
    { HIT_BACK,    LY_GLYPH_BACK,    tab && ly_tab_can_back (tab)    },
    { HIT_FORWARD, LY_GLYPH_FORWARD, tab && ly_tab_can_forward (tab) },
    { HIT_RELOAD,  (tab && ly_tab_loading (tab)) ? LY_GLYPH_STOP : LY_GLYPH_RELOAD,
      tab != NULL },
    { HIT_HOME,    LY_GLYPH_HOME,    tab != NULL                     },
  };
  for (int i = 0; i < left_slots (win); i++)
    paint_button (win, cv, button_rect (win, i), left[i].kind, left[i].glyph,
                  left[i].on);

  paint_right (win, cv);

  /* border-bottom: 1px solid color-mix(borders 70%) */
  RECT edge = { 0, chrome.bottom - 1, c.right, chrome.bottom };
  ly_fill_alpha (cv, edge, win->theme.line, 0.7);

  paint_progress (win, cv, chrome);
}

/* ------------------------------------------------------------- hit testing */

static HitKind
hit_test (LyWindow *win, POINT p, int *index)
{
  *index = -1;

  if (p.y < strip_height (win)) {
    for (guint i = 0; i < win->tabs->len; i++) {
      RECT r = tab_rect (win, i);
      if (!PtInRect (&r, p))
        continue;
      *index = (int) i;
      RECT x = { r.right - sc (win, 25), r.top, r.right - sc (win, 3), r.bottom };
      return ((r.right - r.left) > sc (win, 96) && PtInRect (&x, p))
               ? HIT_TAB_CLOSE : HIT_TAB;
    }
    RECT nt = newtab_rect (win);
    if (PtInRect (&nt, p))
      return HIT_NEWTAB;
    return HIT_NONE;
  }

  for (int i = 0; i < left_slots (win); i++) {
    RECT r = button_rect (win, i);
    if (PtInRect (&r, p))
      return (HitKind) (HIT_BACK + i);
  }
  for (int i = 0; i < RIGHT_N; i++) {
    RECT r = right_rect (win, i);
    if (PtInRect (&r, p))
      return RIGHT_ORDER[i];
  }
  RECT a = address_rect (win);
  if (PtInRect (&a, p))
    return HIT_ADDRESS;
  return HIT_NONE;
}

/* ------------------------------------------------------------------- tabs */

static void
sync_address (LyWindow *win)
{
  /* Nothing to sync unless the entry is showing: the painted URL is read
   * straight from the tab. */
  LyTab *tab = active_tab (win);
  if (tab == NULL || !win->editing_url || win->address_dirty)
    return;
  const char *url = ly_tab_url (tab);
  g_autofree wchar_t *w =
      (wchar_t *) g_utf8_to_utf16 (url ? url : "", -1, NULL, NULL, NULL);
  SetWindowTextW (win->address, w ? w : L"");
}

static void
update_title (LyWindow *win)
{
  LyTab *tab = active_tab (win);
  const char *t = tab ? ly_tab_title (tab) : NULL;
  /* The start page is called Lyndon, and "Lyndon — Lyndon" is a bug report
   * waiting to be filed. */
  g_autofree char *full =
      (t == NULL || t[0] == 0 || g_strcmp0 (t, "Lyndon") == 0)
        ? g_strdup ("Lyndon")
        : g_strdup_printf ("%s — Lyndon", t);
  g_autofree wchar_t *w = (wchar_t *) g_utf8_to_utf16 (full, -1, NULL, NULL, NULL);
  SetWindowTextW (win->hwnd, w ? w : L"Lyndon");
}

/* The bar runs while anything is loading and stops when nothing is, so an
 * idle browser is not repainting thirty times a second for no reason. */
static void
sync_progress (LyWindow *win)
{
  gboolean loading = FALSE;
  for (guint i = 0; i < win->tabs->len; i++)
    if (ly_tab_loading (g_ptr_array_index (win->tabs, i))) {
      loading = TRUE;
      break;
    }
  if (loading == win->progress_on)
    return;

  win->progress_on = loading;
  if (loading) {
    win->progress_phase = 0;
    SetTimer (win->hwnd, TIMER_PROGRESS, 16, NULL);
  } else {
    KillTimer (win->hwnd, TIMER_PROGRESS);
  }
  redraw_chrome (win);
}

static void
on_tab_changed (LyTab *tab, gpointer data)
{
  LyWindow *win = data;
  sync_progress (win);
  if (tab == active_tab (win)) {
    sync_address (win);
    update_title (win);
  }
  /* A background tab still redraws: its title and spinner are in the strip. */
  redraw_chrome (win);
  if (tab == active_tab (win))
    refresh_bookmark_state (win);

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

/* ------------------------------------------------------------- bookmarks */

static void
refresh_bookmark_state (LyWindow *win)
{
  LyTab *tab = active_tab (win);
  gboolean now = FALSE;
  if (tab && win->store) {
    const char *url = ly_tab_url (tab);
    if (url && *url && !ly_uri_is_internal (url))
      now = ly_store_is_bookmarked (win->store, url);
  }
  if (now != win->bookmarked) {
    win->bookmarked = now;
    redraw_chrome (win);
  }
}

static void
toggle_bookmark (LyWindow *win)
{
  LyTab *tab = active_tab (win);
  if (tab == NULL || win->store == NULL)
    return;
  const char *url = ly_tab_url (tab);
  if (url == NULL || *url == 0 || ly_uri_is_internal (url))
    return;

  if (ly_store_is_bookmarked (win->store, url))
    ly_store_remove_bookmark (win->store, url);
  else
    ly_store_add_bookmark (win->store, url, ly_tab_title (tab));

  win->bookmarked = !win->bookmarked;
  redraw_chrome (win);
  if (ly_panel_is_open (win->panel))
    ly_panel_refresh (win->panel);
}

/* ----------------------------------------------------------------- panel */

static void
on_panel_open (const char *url, gboolean new_tab, gpointer data)
{
  LyWindow *win = data;
  if (new_tab) {
    ly_window_open_tab (win, url);
    return;
  }
  LyTab *tab = active_tab (win);
  if (tab)
    ly_tab_navigate (tab, url);
  else
    ly_window_open_tab (win, url);
}

static void
show_panel (LyWindow *win, LyPanelKind kind)
{
  RECT anchor = right_rect (win, 1);          /* under the library button */
  MapWindowPoints (win->hwnd, NULL, (POINT *) &anchor, 2);

  if (ly_panel_is_open (win->panel)) {
    ly_panel_close (win->panel);
    return;
  }
  win->panel = ly_panel_show (win->hwnd, win->instance, anchor, kind, win->dpi,
                              win->theme.dark, win->store, win->downloads,
                              on_panel_open, win);
}

/* --------------------------------------------------------------- the menu */

enum {
  MENU_NEW_TAB = 100,
  MENU_BOOKMARKS,
  MENU_HISTORY,
  MENU_DOWNLOADS,
  MENU_BOOKMARK_THIS,
  MENU_SETTINGS,
  MENU_ABOUT,
};

static void
show_menu (LyWindow *win)
{
  HMENU menu = CreatePopupMenu ();
  if (menu == NULL)
    return;

  AppendMenuW (menu, MF_STRING, MENU_NEW_TAB,       L"New tab\tCtrl+T");
  AppendMenuW (menu, MF_SEPARATOR, 0, NULL);
  AppendMenuW (menu, MF_STRING, MENU_BOOKMARK_THIS,
               win->bookmarked ? L"Remove bookmark" : L"Bookmark this page\tCtrl+D");
  AppendMenuW (menu, MF_STRING, MENU_BOOKMARKS,     L"Bookmarks");
  AppendMenuW (menu, MF_STRING, MENU_HISTORY,       L"History\tCtrl+H");
  AppendMenuW (menu, MF_STRING, MENU_DOWNLOADS,     L"Downloads\tCtrl+J");
  AppendMenuW (menu, MF_SEPARATOR, 0, NULL);
  AppendMenuW (menu, MF_STRING, MENU_SETTINGS,      L"Settings\tCtrl+,");
  AppendMenuW (menu, MF_STRING, MENU_ABOUT,         L"About Lyndon");

  RECT r = right_rect (win, 0);
  MapWindowPoints (win->hwnd, NULL, (POINT *) &r, 2);

  /* TPM_RETURNCMD: the command comes back here rather than as WM_COMMAND,
   * which keeps the menu handling in one place. */
  int choice = (int) TrackPopupMenu (menu, TPM_RIGHTALIGN | TPM_RETURNCMD | TPM_NONOTIFY,
                                     r.right, r.bottom, 0, win->hwnd, NULL);
  DestroyMenu (menu);

  switch (choice) {
    case MENU_NEW_TAB:       ly_window_open_tab (win, NULL); break;
    case MENU_BOOKMARK_THIS: toggle_bookmark (win); break;
    case MENU_BOOKMARKS:     show_panel (win, LY_PANEL_BOOKMARKS); break;
    case MENU_HISTORY:       show_panel (win, LY_PANEL_HISTORY); break;
    case MENU_DOWNLOADS:     show_panel (win, LY_PANEL_DOWNLOADS); break;
    case MENU_SETTINGS:
      ly_prefs_show (win->hwnd, win->instance, win->cfg, win->store, win->passwords);
      break;
    case MENU_ABOUT: {
      g_autofree char *runtime = ly_webview_runtime_version ();
      g_autofree char *text = g_strdup_printf (
          "Lyndon %s\n\nA small, fast, private browser.\n\n"
          "Pages are drawn by WebView2 %s.",
          LYNDON_VERSION, runtime ? runtime : "(unknown)");
      g_autofree wchar_t *w = (wchar_t *) g_utf8_to_utf16 (text, -1, NULL, NULL, NULL);
      MessageBoxW (win->hwnd, w, L"About Lyndon", MB_OK | MB_ICONINFORMATION);
      break;
    }
    default: break;
  }
}

/* ------------------------------------------------------------- downloads */

static void
on_downloads_changed (LyDownloads *downloads, gpointer data)
{
  LyWindow *win = data;
  redraw_chrome (win);
  if (ly_panel_is_open (win->panel))
    ly_panel_refresh (win->panel);
}

static void
on_download_done (const LyDownloadItem *item, gpointer data)
{
  LyWindow *win = data;
  redraw_chrome (win);
  if (ly_panel_is_open (win->panel))
    ly_panel_refresh (win->panel);
}

/* -------------------------------------------------------------- passwords */

static void
on_tab_login (LyTab *tab, const char *origin, const char *username,
              const char *password, gpointer data)
{
  LyWindow *win = data;
  if (win->passwords == NULL || origin == NULL)
    return;
  if (ly_passwords_is_blocked (win->passwords, origin))
    return;

  g_autofree char *text = g_strdup_printf (
      "Save the login for %s?\n\nUser: %s\n\n"
      "It goes into Windows Credential Manager, not into a file of Lyndon's.\n\n"
      "Choose No to skip it this time, or Cancel to never ask for this site.",
      origin, (username && *username) ? username : "(none)");
  g_autofree wchar_t *w = (wchar_t *) g_utf8_to_utf16 (text, -1, NULL, NULL, NULL);

  int answer = MessageBoxW (win->hwnd, w, L"Lyndon",
                            MB_YESNOCANCEL | MB_ICONQUESTION);
  if (answer == IDYES)
    ly_passwords_save (win->passwords, origin, username, password);
  else if (answer == IDCANCEL)
    ly_passwords_block (win->passwords, origin);
}

static LyPolicy
on_tab_permission (LyTab *tab, LyPermKind kind, const char *origin, gpointer data)
{
  LyWindow *win = data;
  if (win->cfg == NULL)
    return LY_POLICY_ASK;

  /* A per-site answer beats the global default, exactly as on Linux. */
  if (win->store && origin) {
    g_autofree char *host = ly_uri_host (origin);
    if (host) {
      int stored = ly_store_site_permission (win->store, host, (int) kind);
      if (stored >= 0)
        return (LyPolicy) stored;
    }
  }
  return win->cfg->perm[kind];
}

/* --------------------------------------------------------------- session */

/* Saved on close and restored on start, when the option is on. The rows are
 * the store's own session table, so the Linux build reads the same thing. */
static void
save_session (LyWindow *win)
{
  if (win->store == NULL || win->cfg == NULL || !win->cfg->restore_session)
    return;

  GPtrArray *rows = g_ptr_array_new_with_free_func ((GDestroyNotify) ly_store_row_free);
  for (guint i = 0; i < win->tabs->len; i++) {
    LyTab *tab = g_ptr_array_index (win->tabs, i);
    const char *url = ly_tab_url (tab);
    if (url == NULL || *url == 0 || ly_uri_is_internal (url))
      continue;
    LyStoreRow *row = g_new0 (LyStoreRow, 1);
    row->url = g_strdup (url);
    row->title = g_strdup (ly_tab_title (tab));
    row->window = 0;
    row->index = (int) i;
    g_ptr_array_add (rows, row);
  }
  ly_store_save_session (win->store, rows);
  g_ptr_array_unref (rows);
}

static gboolean
restore_session (LyWindow *win)
{
  if (win->store == NULL || win->cfg == NULL || !win->cfg->restore_session)
    return FALSE;

  GPtrArray *rows = ly_store_load_session (win->store);
  if (rows == NULL)
    return FALSE;

  guint opened = 0;
  for (guint i = 0; i < rows->len; i++) {
    LyStoreRow *row = g_ptr_array_index (rows, i);
    if (row->url && *row->url) {
      ly_window_open_tab (win, row->url);
      opened++;
    }
  }
  g_ptr_array_unref (rows);
  return opened > 0;
}

static void
select_tab (LyWindow *win, int index)
{
  if (index < 0 || (guint) index >= win->tabs->len)
    return;
  for (guint i = 0; i < win->tabs->len; i++)
    ly_tab_set_visible (g_ptr_array_index (win->tabs, i), (int) i == index);
  win->active = index;
  win->address_dirty = FALSE;
  end_url_edit (win);
  sync_address (win);
  update_title (win);
  /* The star belongs to the page, so it has to be asked again about this
   * one rather than left showing the last tab's answer. */
  refresh_bookmark_state (win);
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
    g_ptr_array_add (win->pending, g_strdup (target));
    return;
  }

  LyTab *tab = ly_tab_new (win->hwnd, win->cfg, win->block, win->downloads,
                           win->passwords, win->store, target);
  ly_tab_set_callbacks (tab, on_tab_changed, on_tab_new_window, win);
  ly_tab_set_accelerator_handler (tab, on_tab_accelerator);
  ly_tab_set_login_handler (tab, on_tab_login);
  ly_tab_set_permission_handler (tab, on_tab_permission);
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
  if (win->address) {
    RECT t = address_text_rect (win);
    MoveWindow (win->address, t.left, t.top, t.right - t.left, t.bottom - t.top,
                TRUE);
  }

  RECT page = page_rect (win);
  for (guint i = 0; i < win->tabs->len; i++)
    ly_tab_set_bounds (g_ptr_array_index (win->tabs, i), page);
}

static void
redraw_chrome (LyWindow *win)
{
  RECT c;
  GetClientRect (win->hwnd, &c);
  c.bottom = chrome_height (win);
  InvalidateRect (win->hwnd, &c, FALSE);
}

/* ----------------------------------------------------------- address bar */

/* The entry only exists while it is being typed into. Out of focus the URL is
 * painted instead, which is the only way to dim everything but the domain. */
static void
begin_url_edit (LyWindow *win)
{
  if (win->editing_url)
    return;
  LyTab *tab = active_tab (win);
  const char *url = tab ? ly_tab_url (tab) : "";
  g_autofree wchar_t *w =
    (wchar_t *) g_utf8_to_utf16 (url ? url : "", -1, NULL, NULL, NULL);

  win->editing_url = TRUE;
  SetWindowTextW (win->address, w ? w : L"");
  layout (win);
  ShowWindow (win->address, SW_SHOW);
  SetFocus (win->address);
  SendMessageW (win->address, EM_SETSEL, 0, -1);
  redraw_chrome (win);
}

static void
end_url_edit (LyWindow *win)
{
  if (!win->editing_url)
    return;
  win->editing_url = FALSE;
  win->address_dirty = FALSE;
  ShowWindow (win->address, SW_HIDE);
  redraw_chrome (win);
}

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
  end_url_edit (win);
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
        end_url_edit (win);
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

    case WM_KILLFOCUS:
      /* Clicking away puts the painted URL back, dimmed path and all. */
      end_url_edit (win);
      break;
  }
  return CallWindowProcW (win->address_proc, hwnd, msg, wp, lp);
}

/* ------------------------------------------------------------ window proc */



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
      ly_theme_load (&win->theme, ly_wants_dark (win->cfg));
      ly_fonts_make (&win->fonts, win->dpi);
      ly_apply_dark_titlebar (hwnd, win->theme.dark);

      win->address = CreateWindowExW (
          0, L"EDIT", L"",
          WS_CHILD | ES_AUTOHSCROLL | ES_LEFT,
          0, 0, 10, 10, hwnd, (HMENU) (UINT_PTR) ID_ADDRESS, win->instance, NULL);
      SendMessageW (win->address, WM_SETFONT, (WPARAM) win->fonts.normal, TRUE);
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
      ly_fonts_make (&win->fonts, win->dpi);
      SendMessageW (win->address, WM_SETFONT, (WPARAM) win->fonts.normal, TRUE);
      RECT *r = (RECT *) lp;
      SetWindowPos (hwnd, NULL, r->left, r->top, r->right - r->left,
                    r->bottom - r->top, SWP_NOZORDER | SWP_NOACTIVATE);
      layout (win);
      return 0;
    }

    case WM_SETTINGCHANGE: {
      gboolean dark = ly_wants_dark (win->cfg);
      if (dark != win->theme.dark) {
        ly_theme_load (&win->theme, dark);
        ly_apply_dark_titlebar (hwnd, dark);
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
      RECT c;
      GetClientRect (hwnd, &c);
      LyCanvas canvas;
      if (ly_canvas_begin (&canvas, dc, c.right, chrome_height (win))) {
        paint (win, &canvas);
        ly_canvas_end (&canvas);
      }
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

    case WM_TIMER:
      if (wp == TIMER_PROGRESS) {
        win->progress_phase++;
        RECT c;
        GetClientRect (hwnd, &c);
        RECT bar = { 0, chrome_height (win) - sc (win, PROGRESS_H) - 1,
                     c.right, chrome_height (win) };
        InvalidateRect (hwnd, &bar, FALSE);
      }
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
        case HIT_HOME:
          if (tab && win->cfg && win->cfg->homepage && *win->cfg->homepage)
            ly_tab_navigate (tab, win->cfg->homepage);
          break;
        case HIT_ADDRESS:  begin_url_edit (win); break;
        case HIT_STAR:     toggle_bookmark (win); break;
        case HIT_LIBRARY:  show_panel (win, LY_PANEL_BOOKMARKS); break;
        case HIT_MENU:     show_menu (win); break;
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
      KillTimer (hwnd, TIMER_PROGRESS);
      save_session (win);
      if (win->panel)
        ly_panel_close (win->panel);
      if (windows)
        g_ptr_array_remove_fast (windows, win);
      for (guint i = 0; i < win->tabs->len; i++)
        ly_tab_free (g_ptr_array_index (win->tabs, i));
      g_ptr_array_free (win->tabs, TRUE);
      ly_fonts_free (&win->fonts);
      g_ptr_array_free (win->pending, TRUE);
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
      case 'L': begin_url_edit (win); return TRUE;
      case 'R': if (tab) ly_tab_reload (tab); return TRUE;
      case 'D': toggle_bookmark (win); return TRUE;
      case 'H': show_panel (win, LY_PANEL_HISTORY); return TRUE;
      case 'J': show_panel (win, LY_PANEL_DOWNLOADS); return TRUE;
      case VK_OEM_COMMA:
        ly_prefs_show (win->hwnd, win->instance, win->cfg, win->store, win->passwords);
        return TRUE;
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
      begin_url_edit (win);
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
               LyBlock *block, LyDownloads *downloads, LyPasswords *passwords,
               const char *url)
{
  LyWindow *win = g_new0 (LyWindow, 1);
  win->instance = instance;
  win->cfg = cfg;
  win->store = store;
  win->block = block;
  win->downloads = downloads;
  win->passwords = passwords;
  win->tabs = g_ptr_array_new ();
  win->pending = g_ptr_array_new_with_free_func (g_free);
  win->active = -1;
  win->hot_tab = -1;
  win->dpi = 96;

  int w = 1200, h = 820;
  HWND hwnd = CreateWindowExW (
      0, CLASS_NAME, L"Lyndon", WS_OVERLAPPEDWINDOW,
      CW_USEDEFAULT, CW_USEDEFAULT, w, h, NULL, NULL, instance, win);
  if (hwnd == NULL) {
    g_ptr_array_free (win->tabs, TRUE);
    g_ptr_array_free (win->pending, TRUE);
    g_free (win);
    return NULL;
  }

  window_count++;
  if (windows == NULL)
    windows = g_ptr_array_new ();
  g_ptr_array_add (windows, win);

  if (downloads)
    ly_downloads_set_callbacks (downloads, on_downloads_changed,
                                on_download_done, win);

  ShowWindow (hwnd, SW_SHOWDEFAULT);
  UpdateWindow (hwnd);

  if (env_settled && !env_ok) {
    g_autofree wchar_t *m = (wchar_t *) g_utf8_to_utf16 (
        env_message ? env_message : "WebView2 is unavailable.", -1, NULL, NULL, NULL);
    MessageBoxW (hwnd, m, L"Lyndon", MB_OK | MB_ICONERROR);
    return win;
  }

  /* A URL on the command line wins over the saved session: it is what the
   * user asked for just now. */
  if (url && *url)
    ly_window_open_tab (win, url);
  else if (!restore_session (win))
    ly_window_open_tab (win, NULL);

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
    /* The window was asked for tabs before there was anything to put in
     * them. Now there is. Taken by steal so that opening them cannot see a
     * queue that is still being drained. */
    g_autoptr (GPtrArray) waiting = win->pending;
    win->pending = g_ptr_array_new_with_free_func (g_free);
    for (guint t = 0; t < waiting->len; t++)
      ly_window_open_tab (win, g_ptr_array_index (waiting, t));
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
