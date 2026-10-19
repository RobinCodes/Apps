/* notice.c — see notice.h. */

#include "notice.h"
#include "ui.h"

#include <windowsx.h>
#include <string.h>

#define NOTICE_CLASS L"LyndonNoticeBar"

/* Design sizes at 96 dpi, the same card as login.c so the two are one thing
 * seen twice rather than two. */
#define CARD_W      460
#define CARD_R       14
#define CARD_H       44
#define PAD          12
#define GAP           8
#define ICON         16
#define BTN_H        28
#define BTN_R         9
#define BTN_PAD      14
#define CLOSE_W      26

typedef enum { BTN_ACTION, BTN_CLOSE, BTN_N } Button;

typedef struct {
  HWND      hwnd;
  HINSTANCE instance;

  char     *message;
  char     *button;

  /* Measured once: neither string changes while the bar is up. */
  int       action_w;

  int       hot;           /* button under the pointer, -1 for none */

  LyTheme   theme;
  LyFonts   fonts;
  int       dpi;

  LyNoticeActionFn action;
  gpointer         user_data;
} LyNoticeBar;

/* One at a time, like the login bar. */
static LyNoticeBar *the_bar;

static int
sc (LyNoticeBar *bar, int v)
{
  return ly_scale (bar->dpi, v);
}

/* ---------------------------------------------------------------- layout */

static void
measure (LyNoticeBar *bar)
{
  LyCanvas m = { .dc = GetDC (bar->hwnd) };
  bar->action_w = ly_text_width (&m, bar->button, bar->fonts.bold) + sc (bar, BTN_PAD * 2);
  ReleaseDC (bar->hwnd, m.dc);
}

static RECT
button_rect (LyNoticeBar *bar, int which)
{
  RECT c;
  GetClientRect (bar->hwnd, &c);

  int h = sc (bar, BTN_H);
  int top = (c.bottom - h) / 2;
  int right = c.right - sc (bar, PAD);

  RECT close = { right - sc (bar, CLOSE_W), top, right, top + h };
  right = close.left - sc (bar, GAP) / 2;
  RECT action = { right - bar->action_w, top, right, top + h };

  return which == BTN_CLOSE ? close : action;
}

static RECT
icon_rect (LyNoticeBar *bar)
{
  RECT c;
  GetClientRect (bar->hwnd, &c);
  int top = (c.bottom - sc (bar, ICON)) / 2;
  return (RECT) { sc (bar, PAD), top, sc (bar, PAD + ICON), top + sc (bar, ICON) };
}

static RECT
label_rect (LyNoticeBar *bar)
{
  RECT c;
  GetClientRect (bar->hwnd, &c);
  RECT last = button_rect (bar, BTN_ACTION);
  RECT r = { sc (bar, PAD + ICON + GAP), 0, last.left - sc (bar, GAP), c.bottom };
  if (r.right < r.left)
    r.right = r.left;
  return r;
}

static int
button_at (LyNoticeBar *bar, POINT pt)
{
  for (int i = 0; i < BTN_N; i++) {
    RECT r = button_rect (bar, i);
    if (PtInRect (&r, pt))
      return i;
  }
  return -1;
}

/* --------------------------------------------------------------- drawing */

static void
paint (LyNoticeBar *bar, LyCanvas *cv)
{
  RECT c;
  GetClientRect (bar->hwnd, &c);

  ly_round_shadow (cv, c, sc (bar, CARD_R), bar->theme.shadow, 0.30,
                   sc (bar, 1), sc (bar, 2));
  ly_round (cv, c, sc (bar, CARD_R), bar->theme.surface, 1.0);
  ly_round_ring (cv, c, sc (bar, CARD_R), 1.0, bar->theme.line, 1.0);

  ly_glyph (cv, icon_rect (bar), LY_GLYPH_DOWNLOAD, bar->theme.text_dim, FALSE);

  ly_text (cv, label_rect (bar), bar->message, bar->theme.text, bar->fonts.normal,
           DT_SINGLELINE | DT_VCENTER | DT_LEFT | DT_END_ELLIPSIS | DT_NOPREFIX);

  RECT action = button_rect (bar, BTN_ACTION);
  ly_round (cv, action, sc (bar, BTN_R), bar->theme.accent, 1.0);
  ly_text (cv, action, bar->button, bar->theme.accent_text, bar->fonts.bold,
           DT_SINGLELINE | DT_CENTER | DT_VCENTER | DT_NOPREFIX);

  RECT close = button_rect (bar, BTN_CLOSE);
  if (bar->hot == BTN_CLOSE)
    ly_round (cv, close, sc (bar, BTN_R), bar->theme.text, 0.07);
  RECT glyph = close;
  InflateRect (&glyph, -sc (bar, 8), -sc (bar, 8));
  ly_glyph (cv, glyph, LY_GLYPH_CLOSE, bar->theme.text_dim, FALSE);
}

/* ------------------------------------------------------------- behaviour */

static void
fire (LyNoticeBar *bar)
{
  LyNoticeActionFn action = bar->action;
  gpointer data = bar->user_data;

  /* Down before the callback: it restarts the browser, and this card has no
   * business still being on screen while that happens. */
  DestroyWindow (bar->hwnd);

  if (action != NULL)
    action (data);
}

static LRESULT CALLBACK
notice_proc (HWND hwnd, UINT msg, WPARAM wp, LPARAM lp)
{
  LyNoticeBar *bar = (LyNoticeBar *) GetWindowLongPtrW (hwnd, GWLP_USERDATA);

  switch (msg) {
    case WM_NCCREATE: {
      CREATESTRUCTW *cs = (CREATESTRUCTW *) lp;
      SetWindowLongPtrW (hwnd, GWLP_USERDATA, (LONG_PTR) cs->lpCreateParams);
      return DefWindowProcW (hwnd, msg, wp, lp);
    }

    case WM_PAINT: {
      PAINTSTRUCT ps;
      HDC dc = BeginPaint (hwnd, &ps);
      RECT c;
      GetClientRect (hwnd, &c);
      LyCanvas buffer;
      if (ly_canvas_begin (&buffer, dc, c.right, c.bottom)) {
        paint (bar, &buffer);
        ly_canvas_end (&buffer);
      } else {
        LyCanvas plain = { .dc = dc, .px = NULL, .width = c.right,
                           .height = c.bottom,
                           .clip = { 0, 0, c.right, c.bottom } };
        paint (bar, &plain);
      }
      EndPaint (hwnd, &ps);
      return 0;
    }

    case WM_ERASEBKGND:
      return 1;   /* painted whole, every time */

    case WM_MOUSEMOVE: {
      POINT pt = { GET_X_LPARAM (lp), GET_Y_LPARAM (lp) };
      int hot = button_at (bar, pt);
      if (hot != bar->hot) {
        bar->hot = hot;
        InvalidateRect (hwnd, NULL, FALSE);
        TRACKMOUSEEVENT track = { sizeof track, TME_LEAVE, hwnd, 0 };
        TrackMouseEvent (&track);
      }
      return 0;
    }

    case WM_MOUSELEAVE:
      bar->hot = -1;
      InvalidateRect (hwnd, NULL, FALSE);
      return 0;

    case WM_LBUTTONDOWN: {
      POINT pt = { GET_X_LPARAM (lp), GET_Y_LPARAM (lp) };
      switch (button_at (bar, pt)) {
        case BTN_ACTION: fire (bar);          break;
        case BTN_CLOSE:  ly_notice_dismiss (); break;
        default: break;
      }
      return 0;
    }

    case WM_DESTROY:
      if (the_bar == bar)
        the_bar = NULL;
      g_free (bar->message);
      g_free (bar->button);
      ly_fonts_free (&bar->fonts);
      g_free (bar);
      return 0;
  }
  return DefWindowProcW (hwnd, msg, wp, lp);
}

/* ------------------------------------------------------------------- API */

gboolean
ly_notice_register (HINSTANCE instance)
{
  WNDCLASSEXW wc = { 0 };
  wc.cbSize = sizeof wc;
  wc.lpfnWndProc = notice_proc;
  wc.hInstance = instance;
  wc.hCursor = LoadCursorW (NULL, IDC_ARROW);
  wc.lpszClassName = NOTICE_CLASS;
  return RegisterClassExW (&wc) != 0;
}

void
ly_notice_show (HWND owner, HINSTANCE instance, RECT anchor, int dpi,
                gboolean dark, const char *message, const char *button,
                LyNoticeActionFn action, gpointer user_data)
{
  if (message == NULL || button == NULL)
    return;

  ly_notice_dismiss ();

  LyNoticeBar *bar = g_new0 (LyNoticeBar, 1);
  bar->instance  = instance;
  bar->message   = g_strdup (message);
  bar->button    = g_strdup (button);
  bar->hot       = -1;
  bar->dpi       = dpi > 0 ? dpi : 96;
  bar->action    = action;
  bar->user_data = user_data;

  ly_theme_load (&bar->theme, dark);
  ly_fonts_make (&bar->fonts, bar->dpi);

  int w = ly_scale (bar->dpi, CARD_W);
  int room = anchor.right - anchor.left - ly_scale (bar->dpi, 16);
  if (w > room)
    w = room;
  int h = ly_scale (bar->dpi, CARD_H);

  int x = anchor.left + (anchor.right - anchor.left - w) / 2;
  int y = anchor.top + ly_scale (bar->dpi, 10);

  /* Nothing here takes typing, so the page keeps the keyboard. */
  bar->hwnd = CreateWindowExW (
      WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE, NOTICE_CLASS, L"", WS_POPUP,
      x, y, w, h, owner, NULL, instance, bar);
  if (bar->hwnd == NULL) {
    ly_fonts_free (&bar->fonts);
    g_free (bar->message);
    g_free (bar->button);
    g_free (bar);
    return;
  }
  the_bar = bar;
  measure (bar);
  ShowWindow (bar->hwnd, SW_SHOWNOACTIVATE);
}

void
ly_notice_dismiss (void)
{
  if (the_bar != NULL && the_bar->hwnd != NULL)
    DestroyWindow (the_bar->hwnd);
  the_bar = NULL;
}

void
ly_notice_reanchor (HWND owner, RECT anchor)
{
  LyNoticeBar *bar = the_bar;
  if (bar == NULL || bar->hwnd == NULL)
    return;
  if (GetWindow (bar->hwnd, GW_OWNER) != owner)
    return;

  RECT r;
  GetWindowRect (bar->hwnd, &r);
  int w = r.right - r.left;

  int x = anchor.left + (anchor.right - anchor.left - w) / 2;
  int y = anchor.top + ly_scale (bar->dpi, 10);
  SetWindowPos (bar->hwnd, NULL, x, y, 0, 0,
                SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE);
}
