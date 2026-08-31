/* panel.c — see panel.h. */

#include "panel.h"

#include <commctrl.h>   /* EM_SETCUEBANNER */
#include <windowsx.h>
#include <string.h>

#define PANEL_CLASS  L"LyndonPanel"
#define ID_FILTER    2001

/* Design sizes at 96 dpi. */
#define PANEL_W      460
#define PANEL_H      520
#define HEADER_H      40
#define FILTER_H      34
#define ROW_H         46
#define PAD           10

/* History can be enormous; the panel is a menu, not a database browser. */
#define MAX_ROWS     200

typedef struct {
  char    *title;
  char    *subtitle;
  char    *url;
  gboolean bookmarked;
  LyDownloadItem *download;   /* downloads section only */
} Row;

struct _LyPanel {
  HWND        hwnd;
  HWND        filter;
  WNDPROC     filter_proc;
  HINSTANCE   instance;

  LyPanelKind kind;
  LyStore    *store;
  LyDownloads *downloads;

  GPtrArray  *rows;           /* Row* */
  int         scroll;         /* pixels */
  int         hot;            /* row under the pointer, -1 for none */
  gboolean    hot_action;     /* pointer is on the row's trailing button */
  int         hot_section;    /* header section under the pointer */

  LyTheme     theme;
  LyFonts     fonts;
  int         dpi;

  LyPanelOpenFn on_open;
  gpointer      user_data;
};

static void rebuild (LyPanel *p);

static int
sc (LyPanel *p, int v)
{
  return ly_scale (p->dpi, v);
}

static void
row_free (Row *r)
{
  g_free (r->title);
  g_free (r->subtitle);
  g_free (r->url);
  g_free (r);
}

/* --------------------------------------------------------------- contents */

static char *
filter_text (LyPanel *p)
{
  wchar_t buf[256];
  GetWindowTextW (p->filter, buf, G_N_ELEMENTS (buf));
  return g_utf16_to_utf8 ((const gunichar2 *) buf, -1, NULL, NULL, NULL);
}

static void
add_store_rows (LyPanel *p, GPtrArray *store_rows)
{
  for (guint i = 0; i < store_rows->len && p->rows->len < MAX_ROWS; i++) {
    LyStoreRow *sr = g_ptr_array_index (store_rows, i);
    Row *r = g_new0 (Row, 1);
    r->url = g_strdup (sr->url);
    r->title = g_strdup ((sr->title && *sr->title) ? sr->title : sr->url);
    r->subtitle = ly_pretty_uri (sr->url);
    r->bookmarked = sr->bookmarked;
    g_ptr_array_add (p->rows, r);
  }
}

static void
rebuild (LyPanel *p)
{
  g_ptr_array_set_size (p->rows, 0);
  g_autofree char *query = filter_text (p);
  gboolean searching = (query && *query);

  switch (p->kind) {
    case LY_PANEL_BOOKMARKS: {
      GPtrArray *rows = searching
        ? ly_store_complete (p->store, query, MAX_ROWS)
        : ly_store_bookmarks (p->store, MAX_ROWS);
      if (rows) {
        if (searching) {
          /* complete() ranks bookmarks first but still returns history; the
             bookmarks section should only ever show bookmarks. */
          for (guint i = 0; i < rows->len; i++) {
            LyStoreRow *sr = g_ptr_array_index (rows, i);
            if (!sr->bookmarked)
              continue;
            Row *r = g_new0 (Row, 1);
            r->url = g_strdup (sr->url);
            r->title = g_strdup ((sr->title && *sr->title) ? sr->title : sr->url);
            r->subtitle = ly_pretty_uri (sr->url);
            r->bookmarked = TRUE;
            g_ptr_array_add (p->rows, r);
          }
        } else {
          add_store_rows (p, rows);
        }
        g_ptr_array_unref (rows);
      }
      break;
    }

    case LY_PANEL_HISTORY: {
      GPtrArray *rows = searching
        ? ly_store_complete (p->store, query, MAX_ROWS)
        : ly_store_recent (p->store, MAX_ROWS);
      if (rows) {
        add_store_rows (p, rows);
        g_ptr_array_unref (rows);
      }
      break;
    }

    case LY_PANEL_DOWNLOADS: {
      GPtrArray *items = ly_downloads_items (p->downloads);
      /* Newest first: the one just started is the one being looked for. */
      for (guint i = items->len; i > 0; i--) {
        LyDownloadItem *item = g_ptr_array_index (items, i - 1);
        Row *r = g_new0 (Row, 1);
        r->title = g_strdup (item->name);
        r->subtitle = ly_download_progress_text (item);
        r->url = g_strdup (item->path);
        r->download = item;
        g_ptr_array_add (p->rows, r);
      }
      break;
    }

    default:
      break;
  }

  p->scroll = 0;
  p->hot = -1;
  InvalidateRect (p->hwnd, NULL, FALSE);
}

/* -------------------------------------------------------------- geometry */

static RECT
header_rect (LyPanel *p)
{
  RECT c;
  GetClientRect (p->hwnd, &c);
  RECT r = { 0, 0, c.right, sc (p, HEADER_H) };
  return r;
}

static RECT
section_rect (LyPanel *p, int index)
{
  RECT h = header_rect (p);
  int w = h.right / LY_PANEL_N;
  RECT r = { index * w, 0, index * w + w, h.bottom };
  return r;
}

static gboolean
has_filter (LyPanel *p)
{
  return p->kind != LY_PANEL_DOWNLOADS;
}

static RECT
list_rect (LyPanel *p)
{
  RECT c;
  GetClientRect (p->hwnd, &c);
  int top = sc (p, HEADER_H) + (has_filter (p) ? sc (p, FILTER_H) : 0);
  RECT r = { 0, top, c.right, c.bottom };
  return r;
}

static int
content_height (LyPanel *p)
{
  return (int) p->rows->len * sc (p, ROW_H);
}

static int
max_scroll (LyPanel *p)
{
  RECT l = list_rect (p);
  int visible = l.bottom - l.top;
  return MAX (0, content_height (p) - visible);
}

static RECT
row_rect (LyPanel *p, guint index)
{
  RECT l = list_rect (p);
  int h = sc (p, ROW_H);
  RECT r = { l.left, l.top + (int) index * h - p->scroll, l.right, 0 };
  r.bottom = r.top + h;
  return r;
}

/* The trailing button: remove from history, un-bookmark, or open the folder
 * of a finished download. */
static RECT
action_rect (LyPanel *p, RECT row)
{
  RECT r = { row.right - sc (p, 38), row.top + sc (p, 8),
             row.right - sc (p, 8), row.bottom - sc (p, 8) };
  return r;
}

/* -------------------------------------------------------------- painting */

static const char *SECTION_NAMES[LY_PANEL_N] = { "Bookmarks", "History", "Downloads" };

static void
paint (LyPanel *p, LyCanvas *cv)
{
  RECT c;
  GetClientRect (p->hwnd, &c);
  ly_fill (cv, c, p->theme.surface);

  /* header */
  RECT h = header_rect (p);
  ly_fill (cv, h, p->theme.surface_alt);
  for (int i = 0; i < LY_PANEL_N; i++) {
    RECT s = section_rect (p, i);
    gboolean active = (i == (int) p->kind);
    if (active)
      ly_fill (cv, s, p->theme.surface);
    else if (i == p->hot_section)
      ly_fill (cv, s, p->theme.line);

    ly_text (cv, s, SECTION_NAMES[i], active ? p->theme.text : p->theme.text_dim,
             active ? p->fonts.bold : p->fonts.normal,
             DT_SINGLELINE | DT_CENTER | DT_VCENTER | DT_NOPREFIX);

    if (active) {
      RECT bar = { s.left + sc (p, 12), s.bottom - sc (p, 3),
                   s.right - sc (p, 12), s.bottom };
      ly_fill (cv, bar, p->theme.accent);
    }
  }
  RECT edge = { 0, h.bottom - 1, c.right, h.bottom };
  ly_fill (cv, edge, p->theme.line);

  /* list */
  RECT l = list_rect (p);
  RECT saved_clip = ly_canvas_clip (cv, l);

  if (p->rows->len == 0) {
    const char *empty =
      p->kind == LY_PANEL_DOWNLOADS ? "Nothing downloaded yet."
      : p->kind == LY_PANEL_BOOKMARKS ? "No bookmarks yet. Star a page to keep it."
      : "Nothing in history.";
    RECT box = l;
    box.top += sc (p, 40);
    ly_text (cv, box, empty, p->theme.text_dim, p->fonts.normal,
             DT_CENTER | DT_WORDBREAK | DT_NOPREFIX);
  }

  for (guint i = 0; i < p->rows->len; i++) {
    RECT r = row_rect (p, i);
    if (r.bottom < l.top || r.top > l.bottom)
      continue;   /* scrolled out of sight */

    Row *row = g_ptr_array_index (p->rows, i);
    gboolean hot = ((int) i == p->hot);
    if (hot)
      ly_fill (cv, r, p->theme.surface_alt);

    RECT icon = { r.left + sc (p, PAD), r.top + sc (p, 11),
                  r.left + sc (p, PAD + 22), r.top + sc (p, 33) };
    LyGlyph g = p->kind == LY_PANEL_DOWNLOADS ? LY_GLYPH_DOWNLOAD
              : p->kind == LY_PANEL_BOOKMARKS ? LY_GLYPH_STAR
              : LY_GLYPH_CLOCK;
    gboolean filled = (p->kind == LY_PANEL_BOOKMARKS) ||
                      (row->download && row->download->finished && !row->download->failed);
    COLORREF ink = p->theme.text_dim;
    if (row->download && row->download->failed)
      ink = p->theme.danger;
    else if (filled && p->kind == LY_PANEL_BOOKMARKS)
      ink = p->theme.accent;
    ly_glyph (cv, icon, g, ink, filled);

    int text_left = r.left + sc (p, PAD + 32);
    int text_right = r.right - sc (p, hot ? 44 : PAD);

    RECT title = { text_left, r.top + sc (p, 6), text_right, r.top + sc (p, 24) };
    ly_text (cv, title, row->title, p->theme.text, p->fonts.normal,
             DT_SINGLELINE | DT_VCENTER | DT_END_ELLIPSIS | DT_NOPREFIX);

    RECT sub = { text_left, r.top + sc (p, 24), text_right, r.bottom - sc (p, 4) };
    COLORREF sub_ink = (row->download && row->download->failed)
                         ? p->theme.danger : p->theme.text_dim;
    ly_text (cv, sub, row->subtitle, sub_ink, p->fonts.small_,
             DT_SINGLELINE | DT_VCENTER | DT_END_ELLIPSIS | DT_NOPREFIX);

    /* Progress under an unfinished download, so the row itself is the bar. */
    if (row->download && !row->download->finished && row->download->total > 0) {
      double fraction = (double) row->download->received / (double) row->download->total;
      fraction = CLAMP (fraction, 0.0, 1.0);
      RECT track = { text_left, r.bottom - sc (p, 6), text_right, r.bottom - sc (p, 4) };
      ly_fill (cv, track, p->theme.line);
      RECT done = track;
      done.right = track.left + (int) ((track.right - track.left) * fraction);
      ly_fill (cv, done, p->theme.accent);
    }

    if (hot) {
      RECT a = action_rect (p, r);
      if (p->hot_action)
        ly_fill (cv, a, p->theme.line);
      LyGlyph ag = p->kind == LY_PANEL_DOWNLOADS ? LY_GLYPH_MENU : LY_GLYPH_CLOSE;
      ly_glyph (cv, a, ag, p->theme.text_dim, FALSE);
    }
  }

  ly_canvas_unclip (cv, saved_clip);

  /* A scrollbar, only while there is something to scroll. */
  int span = max_scroll (p);
  if (span > 0) {
    int visible = l.bottom - l.top;
    int track_h = visible;
    int thumb_h = MAX (sc (p, 24), track_h * visible / MAX (1, content_height (p)));
    int y = l.top + (track_h - thumb_h) * p->scroll / span;
    RECT thumb = { c.right - sc (p, 5), y, c.right - sc (p, 2), y + thumb_h };
    ly_round (cv, thumb, sc (p, 2), p->theme.line, 1.0);
  }

  ly_round_ring (cv, c, 0, 1, p->theme.line, 1.0);
}

/* ---------------------------------------------------------------- actions */

static void
activate (LyPanel *p, guint index, gboolean new_tab)
{
  if (index >= p->rows->len)
    return;
  Row *row = g_ptr_array_index (p->rows, index);

  if (p->kind == LY_PANEL_DOWNLOADS) {
    if (row->download && row->download->finished && !row->download->failed)
      ly_downloads_open (row->download);
    return;
  }
  if (row->url && p->on_open) {
    /* Copy first: opening a tab rebuilds the list under us. */
    g_autofree char *url = g_strdup (row->url);
    LyPanelOpenFn fn = p->on_open;
    gpointer data = p->user_data;
    if (!new_tab)
      ShowWindow (p->hwnd, SW_HIDE);
    fn (url, new_tab, data);
  }
}

static void
row_action (LyPanel *p, guint index)
{
  if (index >= p->rows->len)
    return;
  Row *row = g_ptr_array_index (p->rows, index);

  switch (p->kind) {
    case LY_PANEL_BOOKMARKS:
      ly_store_remove_bookmark (p->store, row->url);
      break;
    case LY_PANEL_HISTORY:
      ly_store_forget_url (p->store, row->url);
      break;
    case LY_PANEL_DOWNLOADS:
      if (row->download == NULL)
        break;
      if (!row->download->finished)
        ly_downloads_cancel (p->downloads, row->download);
      else
        ly_downloads_show_in_folder (row->download);
      return;   /* the list is rebuilt by the downloads callback */
    default:
      break;
  }
  rebuild (p);
}

/* ------------------------------------------------------------ hit testing */

static int
row_at (LyPanel *p, POINT pt, gboolean *on_action)
{
  *on_action = FALSE;
  RECT l = list_rect (p);
  if (!PtInRect (&l, pt))
    return -1;
  for (guint i = 0; i < p->rows->len; i++) {
    RECT r = row_rect (p, i);
    if (!PtInRect (&r, pt))
      continue;
    RECT a = action_rect (p, r);
    *on_action = PtInRect (&a, pt);
    return (int) i;
  }
  return -1;
}

static int
section_at (LyPanel *p, POINT pt)
{
  RECT h = header_rect (p);
  if (!PtInRect (&h, pt))
    return -1;
  for (int i = 0; i < LY_PANEL_N; i++) {
    RECT s = section_rect (p, i);
    if (PtInRect (&s, pt))
      return i;
  }
  return -1;
}

/* ------------------------------------------------------------ filter box */

static LRESULT CALLBACK
filter_proc (HWND hwnd, UINT msg, WPARAM wp, LPARAM lp)
{
  LyPanel *p = (LyPanel *) GetWindowLongPtrW (hwnd, GWLP_USERDATA);

  if (msg == WM_KEYDOWN) {
    if (wp == VK_ESCAPE) {
      ly_panel_close (p);
      return 0;
    }
    if (wp == VK_RETURN) {
      activate (p, 0, FALSE);
      return 0;
    }
    if (wp == VK_DOWN && p->rows->len) {
      p->hot = MIN (p->hot + 1, (int) p->rows->len - 1);
      InvalidateRect (p->hwnd, NULL, FALSE);
      return 0;
    }
  }
  if (msg == WM_CHAR && wp == VK_RETURN)
    return 0;   /* no beep from a single-line EDIT */

  LRESULT out = CallWindowProcW (p->filter_proc, hwnd, msg, wp, lp);
  if (msg == WM_CHAR || msg == WM_KEYUP || msg == WM_PASTE)
    rebuild (p);
  return out;
}

/* ------------------------------------------------------------ window proc */

static LRESULT CALLBACK
panel_proc (HWND hwnd, UINT msg, WPARAM wp, LPARAM lp)
{
  LyPanel *p = (LyPanel *) GetWindowLongPtrW (hwnd, GWLP_USERDATA);

  switch (msg) {
    case WM_NCCREATE: {
      CREATESTRUCTW *cs = (CREATESTRUCTW *) lp;
      SetWindowLongPtrW (hwnd, GWLP_USERDATA, (LONG_PTR) cs->lpCreateParams);
      return DefWindowProcW (hwnd, msg, wp, lp);
    }

    case WM_ERASEBKGND:
      return 1;

    case WM_PAINT: {
      PAINTSTRUCT ps;
      HDC dc = BeginPaint (hwnd, &ps);
      RECT c;
      GetClientRect (hwnd, &c);
      LyCanvas buffer;
      if (ly_canvas_begin (&buffer, dc, c.right, c.bottom)) {
        paint (p, &buffer);
        ly_canvas_end (&buffer);
      } else {
        /* No DIB: draw straight to the DC. The primitives fall back to GDI,
         * which is uglier but is better than not painting at all. */
        LyCanvas plain = { .dc = dc, .px = NULL, .width = c.right,
                           .height = c.bottom,
                           .clip = { 0, 0, c.right, c.bottom } };
        paint (p, &plain);
      }
      EndPaint (hwnd, &ps);
      return 0;
    }

    case WM_MOUSEMOVE: {
      POINT pt = { GET_X_LPARAM (lp), GET_Y_LPARAM (lp) };
      gboolean on_action = FALSE;
      int row = row_at (p, pt, &on_action);
      int section = section_at (p, pt);
      if (row != p->hot || on_action != p->hot_action || section != p->hot_section) {
        p->hot = row;
        p->hot_action = on_action;
        p->hot_section = section;
        InvalidateRect (hwnd, NULL, FALSE);
        TRACKMOUSEEVENT tme = { sizeof tme, TME_LEAVE, hwnd, 0 };
        TrackMouseEvent (&tme);
      }
      return 0;
    }

    case WM_MOUSELEAVE:
      p->hot = -1;
      p->hot_section = -1;
      InvalidateRect (hwnd, NULL, FALSE);
      return 0;

    case WM_MOUSEWHEEL: {
      int delta = GET_WHEEL_DELTA_WPARAM (wp);
      p->scroll = CLAMP (p->scroll - delta / 2, 0, max_scroll (p));
      InvalidateRect (hwnd, NULL, FALSE);
      return 0;
    }

    case WM_LBUTTONDOWN: {
      POINT pt = { GET_X_LPARAM (lp), GET_Y_LPARAM (lp) };
      int section = section_at (p, pt);
      if (section >= 0) {
        if (section != (int) p->kind) {
          p->kind = (LyPanelKind) section;
          ShowWindow (p->filter, has_filter (p) ? SW_SHOW : SW_HIDE);
          SetWindowTextW (p->filter, L"");
          rebuild (p);
        }
        return 0;
      }
      gboolean on_action = FALSE;
      int row = row_at (p, pt, &on_action);
      if (row >= 0) {
        if (on_action)
          row_action (p, (guint) row);
        else
          activate (p, (guint) row, FALSE);
      }
      return 0;
    }

    case WM_MBUTTONDOWN: {
      POINT pt = { GET_X_LPARAM (lp), GET_Y_LPARAM (lp) };
      gboolean on_action = FALSE;
      int row = row_at (p, pt, &on_action);
      if (row >= 0 && !on_action)
        activate (p, (guint) row, TRUE);   /* middle click: new tab */
      return 0;
    }

    case WM_ACTIVATE:
      /* A menu that stays open after you click elsewhere is a window, and
       * this is meant to be a menu. */
      if (LOWORD (wp) == WA_INACTIVE)
        ShowWindow (hwnd, SW_HIDE);
      return 0;

    case WM_KEYDOWN:
      if (wp == VK_ESCAPE) {
        ShowWindow (hwnd, SW_HIDE);
        return 0;
      }
      break;

    case WM_DESTROY:
      g_ptr_array_free (p->rows, TRUE);
      ly_fonts_free (&p->fonts);
      g_free (p);
      return 0;
  }
  return DefWindowProcW (hwnd, msg, wp, lp);
}

/* ------------------------------------------------------------------- API */

gboolean
ly_panel_register (HINSTANCE instance)
{
  WNDCLASSEXW wc = { 0 };
  wc.cbSize = sizeof wc;
  wc.style = CS_DROPSHADOW;   /* it is a menu; give it a menu's shadow */
  wc.lpfnWndProc = panel_proc;
  wc.hInstance = instance;
  wc.hCursor = LoadCursorW (NULL, IDC_ARROW);
  wc.lpszClassName = PANEL_CLASS;
  return RegisterClassExW (&wc) != 0;
}

LyPanel *
ly_panel_show (HWND owner, HINSTANCE instance, RECT anchor, LyPanelKind kind,
               int dpi, gboolean dark, LyStore *store, LyDownloads *downloads,
               LyPanelOpenFn on_open, gpointer user_data)
{
  LyPanel *p = g_new0 (LyPanel, 1);
  p->instance = instance;
  p->kind = kind;
  p->store = store;
  p->downloads = downloads;
  p->on_open = on_open;
  p->user_data = user_data;
  p->rows = g_ptr_array_new_with_free_func ((GDestroyNotify) row_free);
  p->hot = -1;
  p->hot_section = -1;
  p->dpi = dpi > 0 ? dpi : 96;
  ly_theme_load (&p->theme, dark);
  ly_fonts_make (&p->fonts, p->dpi);

  int w = ly_scale (p->dpi, PANEL_W);
  int h = ly_scale (p->dpi, PANEL_H);

  /* Below the anchor, pulled left so it stays on the monitor. */
  int x = anchor.right - w;
  int y = anchor.bottom + ly_scale (p->dpi, 4);
  HMONITOR mon = MonitorFromRect (&anchor, MONITOR_DEFAULTTONEAREST);
  MONITORINFO mi = { sizeof mi };
  if (GetMonitorInfoW (mon, &mi)) {
    x = CLAMP (x, mi.rcWork.left, mi.rcWork.right - w);
    if (y + h > mi.rcWork.bottom)
      y = MAX (mi.rcWork.top, anchor.top - h - ly_scale (p->dpi, 4));
  }

  p->hwnd = CreateWindowExW (
      WS_EX_TOOLWINDOW, PANEL_CLASS, L"", WS_POPUP | WS_BORDER,
      x, y, w, h, owner, NULL, instance, p);
  if (p->hwnd == NULL) {
    g_ptr_array_free (p->rows, TRUE);
    ly_fonts_free (&p->fonts);
    g_free (p);
    return NULL;
  }

  RECT hd = header_rect (p);
  p->filter = CreateWindowExW (
      0, L"EDIT", L"", WS_CHILD | ES_AUTOHSCROLL | ES_LEFT,
      ly_scale (p->dpi, PAD), hd.bottom + ly_scale (p->dpi, 5),
      w - ly_scale (p->dpi, PAD * 2), ly_scale (p->dpi, FILTER_H - 10),
      p->hwnd, (HMENU) (UINT_PTR) ID_FILTER, instance, NULL);
  SendMessageW (p->filter, WM_SETFONT, (WPARAM) p->fonts.normal, TRUE);
  SendMessageW (p->filter, EM_SETCUEBANNER, TRUE, (LPARAM) L"Search");
  SetWindowLongPtrW (p->filter, GWLP_USERDATA, (LONG_PTR) p);
  p->filter_proc = (WNDPROC) SetWindowLongPtrW (p->filter, GWLP_WNDPROC,
                                                (LONG_PTR) filter_proc);
  ShowWindow (p->filter, has_filter (p) ? SW_SHOW : SW_HIDE);

  rebuild (p);
  ShowWindow (p->hwnd, SW_SHOWNA);
  SetForegroundWindow (p->hwnd);
  if (has_filter (p))
    SetFocus (p->filter);
  return p;
}

void
ly_panel_close (LyPanel *p)
{
  if (p && p->hwnd)
    ShowWindow (p->hwnd, SW_HIDE);
}

void
ly_panel_refresh (LyPanel *p)
{
  if (p && p->hwnd && IsWindowVisible (p->hwnd))
    rebuild (p);
}

gboolean
ly_panel_is_open (LyPanel *p)
{
  return p != NULL && p->hwnd != NULL && IsWindowVisible (p->hwnd);
}
