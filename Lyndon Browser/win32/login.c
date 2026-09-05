/* login.c — see login.h. */

#include "login.h"
#include "ui.h"

#include <windowsx.h>
#include <string.h>

#define LOGIN_CLASS  L"LyndonLoginBar"
#define ID_USER      4001
#define ID_PASS      4002
#define TIMER_EXPIRE    1

/* An offer nobody answered should not still be sitting there an hour later,
 * over a page that has nothing to do with it. The same ninety seconds the
 * Linux bar gives itself. */
#define OFFER_MS     (90 * 1000)

/* Design sizes at 96 dpi, from .lyndon-permission and the buttons in it. */
#define CARD_W       500
#define CARD_R        14   /* .lyndon-permission border-radius */
#define TOP_H         44
#define FIELDS_H      44
#define FIELD_H       30
#define FIELD_R       10
#define PAD           12
#define GAP            8
#define ICON          16
#define BTN_H         28
#define BTN_R          9
#define BTN_PAD       14

typedef enum { BTN_NEVER, BTN_NOT_NOW, BTN_SAVE, BTN_N } Button;

static const char *const BUTTON_LABELS[BTN_N] = { "Never", "Not now", "Save" };

typedef struct {
  HWND      hwnd;
  HWND      user;          /* real EDIT controls: text editing is not worth */
  HWND      pass;          /* reimplementing, and prefs.c does the same     */
  WNDPROC   user_proc;
  WNDPROC   pass_proc;
  HINSTANCE instance;
  HBRUSH    field_brush;

  char     *origin;
  char     *username;      /* what the page captured */
  char     *password;      /* NULL when it is the user who must supply it */
  char     *label;

  gboolean  ask_user;
  gboolean  ask_pass;
  gboolean  can_save;

  /* Measured once: the labels never change, and a layout function that grabs
   * a DC to ask how wide they are would do it on every repaint. */
  int       button_w[BTN_N];

  int       hot;           /* button under the pointer, -1 for none */
  gint64    shown_at;

  LyTheme   theme;
  LyFonts   fonts;
  int       dpi;

  LyLoginDoneFn done;
  gpointer      user_data;
} LyLoginBar;

/* One at a time, like the preferences window. */
static LyLoginBar *the_bar;

static void answer (LyLoginBar *bar, LyLoginAnswer decision);

static int
sc (LyLoginBar *bar, int v)
{
  return ly_scale (bar->dpi, v);
}

/* ---------------------------------------------------------------- layout */

static void
measure_buttons (LyLoginBar *bar)
{
  LyCanvas measure = { .dc = GetDC (bar->hwnd) };
  for (int i = 0; i < BTN_N; i++) {
    int text = ly_text_width (&measure, BUTTON_LABELS[i],
                              i == BTN_SAVE ? bar->fonts.bold : bar->fonts.normal);
    bar->button_w[i] = text + sc (bar, BTN_PAD * 2);
  }
  ReleaseDC (bar->hwnd, measure.dc);
}

static RECT
button_rect (LyLoginBar *bar, int which)
{
  RECT c;
  GetClientRect (bar->hwnd, &c);

  /* Right to left, in the order they are drawn: Save is the rightmost. */
  int right = c.right - sc (bar, PAD);
  int h = sc (bar, BTN_H);
  int top = (sc (bar, TOP_H) - h) / 2;
  RECT r = { 0, 0, 0, 0 };

  for (int i = BTN_N - 1; i >= 0; i--) {
    RECT here = { right - bar->button_w[i], top, right, top + h };
    if (i == which)
      r = here;
    right = here.left - sc (bar, GAP) / 2;
  }
  return r;
}

static RECT
label_rect (LyLoginBar *bar)
{
  /* Everything between the icon and the leftmost button. */
  RECT last = button_rect (bar, BTN_NEVER);
  RECT r = { sc (bar, PAD + ICON + GAP), 0, last.left - sc (bar, GAP), sc (bar, TOP_H) };
  if (r.right < r.left)
    r.right = r.left;
  return r;
}

static RECT
icon_rect (LyLoginBar *bar)
{
  int top = (sc (bar, TOP_H) - sc (bar, ICON)) / 2;
  RECT r = { sc (bar, PAD), top, sc (bar, PAD + ICON), top + sc (bar, ICON) };
  return r;
}

/* The two boxes share the second row; with only one of them the box takes the
 * whole width, which is what the Linux bar's hexpand does. */
static void
field_rects (LyLoginBar *bar, RECT *user, RECT *pass)
{
  RECT c;
  GetClientRect (bar->hwnd, &c);

  int top = sc (bar, TOP_H) + (sc (bar, FIELDS_H) - sc (bar, FIELD_H)) / 2;
  int left = sc (bar, PAD);
  int right = c.right - sc (bar, PAD);
  RECT empty = { 0, 0, 0, 0 };

  *user = empty;
  *pass = empty;

  if (bar->ask_user && bar->ask_pass) {
    int middle = (left + right) / 2;
    RECT u = { left, top, middle - sc (bar, GAP) / 2, top + sc (bar, FIELD_H) };
    RECT p = { middle + sc (bar, GAP) / 2, top, right, top + sc (bar, FIELD_H) };
    *user = u;
    *pass = p;
  } else if (bar->ask_user) {
    RECT u = { left, top, right, top + sc (bar, FIELD_H) };
    *user = u;
  } else if (bar->ask_pass) {
    RECT p = { left, top, right, top + sc (bar, FIELD_H) };
    *pass = p;
  }
}

/* The EDIT sits inside the painted box with a little breathing room, so the
 * rounded field is drawn around it rather than under its own square edge. */
static RECT
inset (LyLoginBar *bar, RECT r)
{
  RECT in = { r.left + sc (bar, 8), r.top + sc (bar, 5),
              r.right - sc (bar, 8), r.bottom - sc (bar, 5) };
  return in;
}

static void
place_fields (LyLoginBar *bar)
{
  RECT user, pass;
  field_rects (bar, &user, &pass);

  if (bar->ask_user) {
    RECT in = inset (bar, user);
    SetWindowPos (bar->user, NULL, in.left, in.top, in.right - in.left,
                  in.bottom - in.top, SWP_NOZORDER);
  }
  if (bar->ask_pass) {
    RECT in = inset (bar, pass);
    SetWindowPos (bar->pass, NULL, in.left, in.top, in.right - in.left,
                  in.bottom - in.top, SWP_NOZORDER);
  }
  ShowWindow (bar->user, bar->ask_user ? SW_SHOW : SW_HIDE);
  ShowWindow (bar->pass, bar->ask_pass ? SW_SHOW : SW_HIDE);
}

/* --------------------------------------------------------------- drawing */

static void
paint (LyLoginBar *bar, LyCanvas *cv)
{
  RECT c;
  GetClientRect (bar->hwnd, &c);

  /* The card. Painted rather than left to the window background so that the
   * corners are the same 14px the stylesheet asks for. */
  ly_round_shadow (cv, c, sc (bar, CARD_R), bar->theme.shadow, 0.30,
                   sc (bar, 1), sc (bar, 2));
  ly_round (cv, c, sc (bar, CARD_R), bar->theme.surface, 1.0);
  ly_round_ring (cv, c, sc (bar, CARD_R), 1.0, bar->theme.line, 1.0);

  ly_glyph (cv, icon_rect (bar), LY_GLYPH_LOCK, bar->theme.text_dim, FALSE);

  ly_text (cv, label_rect (bar), bar->label, bar->theme.text, bar->fonts.normal,
           DT_SINGLELINE | DT_VCENTER | DT_LEFT | DT_END_ELLIPSIS | DT_NOPREFIX);

  for (int i = 0; i < BTN_N; i++) {
    RECT r = button_rect (bar, i);
    gboolean suggested = (i == BTN_SAVE);
    gboolean live = !suggested || bar->can_save;

    if (suggested) {
      ly_round (cv, r, sc (bar, BTN_R), bar->theme.accent, live ? 1.0 : 0.35);
    } else if (i == bar->hot) {
      /* alpha(currentColor, .07), the same hover fill as everything else. */
      ly_round (cv, r, sc (bar, BTN_R), bar->theme.text, 0.07);
    }

    COLORREF ink = suggested ? bar->theme.accent_text : bar->theme.text;
    ly_text (cv, r, BUTTON_LABELS[i], ink,
             suggested ? bar->fonts.bold : bar->fonts.normal,
             DT_SINGLELINE | DT_CENTER | DT_VCENTER | DT_NOPREFIX);
  }

  if (bar->ask_user || bar->ask_pass) {
    RECT user, pass;
    field_rects (bar, &user, &pass);
    if (bar->ask_user)
      ly_round (cv, user, sc (bar, FIELD_R), bar->theme.field, 1.0);
    if (bar->ask_pass)
      ly_round (cv, pass, sc (bar, FIELD_R), bar->theme.field, 1.0);
  }
}

/* --------------------------------------------------------------- answers */

static char *
field_text (HWND edit)
{
  int chars = GetWindowTextLengthW (edit);
  if (chars <= 0)
    return g_strdup ("");

  wchar_t *w = g_new0 (wchar_t, (gsize) chars + 1);
  GetWindowTextW (edit, w, chars + 1);
  char *text = g_utf16_to_utf8 ((const gunichar2 *) w, -1, NULL, NULL, NULL);

  /* Anything typed into the box is a password too. */
  memset (w, 0, (gsize) (chars + 1) * sizeof (wchar_t));
  g_free (w);
  return text ?: g_strdup ("");
}

/* Nothing to save until the box the user was asked to fill has something in
 * it; the other cases keep Save live from the moment the bar appears. */
static void
refresh_can_save (LyLoginBar *bar)
{
  gboolean was = bar->can_save;
  bar->can_save = !bar->ask_pass || GetWindowTextLengthW (bar->pass) > 0;
  if (was != bar->can_save)
    InvalidateRect (bar->hwnd, NULL, FALSE);
}

static void
answer (LyLoginBar *bar, LyLoginAnswer decision)
{
  LyLoginDoneFn done = bar->done;
  gpointer data = bar->user_data;

  g_autofree char *typed_user = NULL;
  g_autofree char *typed_pass = NULL;

  const char *username = bar->username;
  const char *password = bar->password;

  if (decision == LY_LOGIN_ANSWER_SAVE) {
    if (bar->ask_user) {
      typed_user = field_text (bar->user);
      username = typed_user;
    }
    if (bar->ask_pass) {
      typed_pass = field_text (bar->pass);
      password = typed_pass;
    }
    if (password == NULL || *password == '\0')
      return;    /* nothing to save; leave the bar up */
  }

  /* Taken down before the callback: saving can put a dialog on screen, and
   * this bar has no business still being there behind it. */
  g_autofree char *origin = g_strdup (bar->origin);
  g_autofree char *out_user = g_strdup (username ?: "");
  g_autofree char *out_pass = g_strdup (password ?: "");

  DestroyWindow (bar->hwnd);

  if (done != NULL)
    done (decision, origin,
          decision == LY_LOGIN_ANSWER_SAVE ? out_user : NULL,
          decision == LY_LOGIN_ANSWER_SAVE ? out_pass : NULL,
          data);

  if (out_pass != NULL)
    memset (out_pass, 0, strlen (out_pass));
  if (typed_pass != NULL)
    memset (typed_pass, 0, strlen (typed_pass));
}

/* ------------------------------------------------------------ hit testing */

static int
button_at (LyLoginBar *bar, POINT pt)
{
  for (int i = 0; i < BTN_N; i++) {
    RECT r = button_rect (bar, i);
    if (PtInRect (&r, pt))
      return i;
  }
  return -1;
}

/* ------------------------------------------------------------ the fields */

static LRESULT CALLBACK
field_proc (HWND hwnd, UINT msg, WPARAM wp, LPARAM lp)
{
  LyLoginBar *bar = (LyLoginBar *) GetWindowLongPtrW (hwnd, GWLP_USERDATA);
  WNDPROC original = (hwnd == bar->user) ? bar->user_proc : bar->pass_proc;

  if (msg == WM_KEYDOWN) {
    if (wp == VK_ESCAPE) {
      answer (bar, LY_LOGIN_ANSWER_DISMISS);
      return 0;
    }
    if (wp == VK_RETURN) {
      if (bar->can_save)
        answer (bar, LY_LOGIN_ANSWER_SAVE);
      return 0;
    }
  }
  if (msg == WM_CHAR && wp == VK_RETURN)
    return 0;   /* no beep from a single-line EDIT */

  LRESULT out = CallWindowProcW (original, hwnd, msg, wp, lp);
  if (msg == WM_CHAR || msg == WM_KEYUP || msg == WM_PASTE)
    refresh_can_save (bar);
  return out;
}

/* ----------------------------------------------------------- window proc */

static LRESULT CALLBACK
login_proc (HWND hwnd, UINT msg, WPARAM wp, LPARAM lp)
{
  LyLoginBar *bar = (LyLoginBar *) GetWindowLongPtrW (hwnd, GWLP_USERDATA);

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
        case BTN_NEVER:   answer (bar, LY_LOGIN_ANSWER_NEVER);   break;
        case BTN_NOT_NOW: answer (bar, LY_LOGIN_ANSWER_DISMISS); break;
        case BTN_SAVE:
          if (bar->can_save)
            answer (bar, LY_LOGIN_ANSWER_SAVE);
          break;
        default: break;
      }
      return 0;
    }

    case WM_KEYDOWN:
      if (wp == VK_ESCAPE) {
        answer (bar, LY_LOGIN_ANSWER_DISMISS);
        return 0;
      }
      if (wp == VK_RETURN && bar->can_save) {
        answer (bar, LY_LOGIN_ANSWER_SAVE);
        return 0;
      }
      break;

    case WM_TIMER:
      if (wp == TIMER_EXPIRE) {
        answer (bar, LY_LOGIN_ANSWER_DISMISS);
        return 0;
      }
      break;

    case WM_CTLCOLOREDIT: {
      HDC dc = (HDC) wp;
      SetTextColor (dc, bar->theme.text);
      SetBkColor (dc, bar->theme.field);
      return (LRESULT) bar->field_brush;
    }

    case WM_DESTROY: {
      KillTimer (hwnd, TIMER_EXPIRE);
      if (the_bar == bar)
        the_bar = NULL;

      /* Anything typed into the boxes is a password too, and the controls
       * would otherwise carry it into their own destruction. */
      if (bar->pass)
        SetWindowTextW (bar->pass, L"");
      if (bar->user)
        SetWindowTextW (bar->user, L"");

      /* The children are destroyed after this message, and their subclass
       * reads through `bar`. Put their own procedures back before anything
       * here is freed. */
      if (bar->user && bar->user_proc) {
        SetWindowLongPtrW (bar->user, GWLP_WNDPROC, (LONG_PTR) bar->user_proc);
        SetWindowLongPtrW (bar->user, GWLP_USERDATA, 0);
      }
      if (bar->pass && bar->pass_proc) {
        SetWindowLongPtrW (bar->pass, GWLP_WNDPROC, (LONG_PTR) bar->pass_proc);
        SetWindowLongPtrW (bar->pass, GWLP_USERDATA, 0);
      }

      /* The captured password must not outlive the offer. */
      if (bar->password != NULL)
        memset (bar->password, 0, strlen (bar->password));

      g_free (bar->origin);
      g_free (bar->username);
      g_free (bar->password);
      g_free (bar->label);
      if (bar->field_brush)
        DeleteObject (bar->field_brush);
      ly_fonts_free (&bar->fonts);
      g_free (bar);
      return 0;
    }
  }
  return DefWindowProcW (hwnd, msg, wp, lp);
}

/* ------------------------------------------------------------------- API */

gboolean
ly_login_register (HINSTANCE instance)
{
  WNDCLASSEXW wc = { 0 };
  wc.cbSize = sizeof wc;
  wc.lpfnWndProc = login_proc;
  wc.hInstance = instance;
  wc.hCursor = LoadCursorW (NULL, IDC_ARROW);
  wc.lpszClassName = LOGIN_CLASS;
  return RegisterClassExW (&wc) != 0;
}

/* The same sentences the Linux bar shows, for the same three cases. */
static char *
offer_label (LyLoginKind kind, const char *origin, const char *username,
             gboolean is_update)
{
  g_autofree char *host = ly_uri_host (origin);
  const char *where = (host && *host) ? host : origin;

  if (kind == LY_LOGIN_NO_PASSWORD)
    return g_strdup_printf ("Signed in to %s as %s — type the password to save it:",
                            where, username ?: "");
  if (kind == LY_LOGIN_NO_USERNAME)
    return g_strdup_printf ("Save the password for %s?", where);

  return is_update ? g_strdup_printf ("Update the saved password for %s?", username)
                   : g_strdup_printf ("Save the password for %s?", username);
}

void
ly_login_offer (HWND owner, HINSTANCE instance, RECT anchor, int dpi,
                gboolean dark, LyLoginKind kind, const char *origin,
                const char *username, const char *password,
                gboolean is_update, const char *guess,
                LyLoginDoneFn done, gpointer user_data)
{
  if (origin == NULL)
    return;

  /* A second offer replaces the first rather than stacking on it. */
  ly_login_dismiss ();

  LyLoginBar *bar = g_new0 (LyLoginBar, 1);
  bar->instance = instance;
  bar->origin = g_strdup (origin);
  bar->username = g_strdup (username ?: "");
  bar->password = password != NULL ? g_strdup (password) : NULL;
  bar->label = offer_label (kind, origin, username, is_update);
  bar->ask_user = (kind == LY_LOGIN_NO_USERNAME);
  bar->ask_pass = (kind == LY_LOGIN_NO_PASSWORD);
  bar->can_save = !bar->ask_pass;
  bar->hot = -1;
  bar->shown_at = g_get_monotonic_time ();
  bar->dpi = dpi > 0 ? dpi : 96;
  bar->done = done;
  bar->user_data = user_data;

  ly_theme_load (&bar->theme, dark);
  ly_fonts_make (&bar->fonts, bar->dpi);
  bar->field_brush = CreateSolidBrush (bar->theme.field);

  int w = ly_scale (bar->dpi, CARD_W);
  if (w > anchor.right - anchor.left - ly_scale (bar->dpi, 16))
    w = anchor.right - anchor.left - ly_scale (bar->dpi, 16);

  gboolean has_fields = bar->ask_user || bar->ask_pass;
  int h = ly_scale (bar->dpi, TOP_H) +
          (has_fields ? ly_scale (bar->dpi, FIELDS_H) : 0);

  /* Centred across the page area and just below its top, where the card in
   * the GtkOverlay sits. */
  int x = anchor.left + (anchor.right - anchor.left - w) / 2;
  int y = anchor.top + ly_scale (bar->dpi, 10);

  /* WS_EX_NOACTIVATE when there is nothing to type: the buttons are still
   * clickable and the page keeps the focus, which is how the Linux card
   * behaves. When the bar is asking for a username or a password it has to
   * be able to receive keys, and a window that cannot be activated cannot —
   * so that case takes the focus, which is also what its sentence invites. */
  DWORD ex = WS_EX_TOOLWINDOW | (has_fields ? 0u : (DWORD) WS_EX_NOACTIVATE);

  bar->hwnd = CreateWindowExW (
      ex, LOGIN_CLASS, L"", WS_POPUP,
      x, y, w, h, owner, NULL, instance, bar);
  if (bar->hwnd == NULL) {
    if (bar->field_brush)
      DeleteObject (bar->field_brush);
    ly_fonts_free (&bar->fonts);
    g_free (bar->origin);
    g_free (bar->username);
    g_free (bar->password);
    g_free (bar->label);
    g_free (bar);
    return;
  }
  the_bar = bar;
  measure_buttons (bar);

  bar->user = CreateWindowExW (0, L"EDIT", L"",
                               WS_CHILD | ES_AUTOHSCROLL | ES_LEFT,
                               0, 0, 10, 10, bar->hwnd,
                               (HMENU) (UINT_PTR) ID_USER, instance, NULL);
  bar->pass = CreateWindowExW (0, L"EDIT", L"",
                               WS_CHILD | ES_AUTOHSCROLL | ES_LEFT | ES_PASSWORD,
                               0, 0, 10, 10, bar->hwnd,
                               (HMENU) (UINT_PTR) ID_PASS, instance, NULL);

  SendMessageW (bar->user, WM_SETFONT, (WPARAM) bar->fonts.normal, TRUE);
  SendMessageW (bar->pass, WM_SETFONT, (WPARAM) bar->fonts.normal, TRUE);
  SetWindowLongPtrW (bar->user, GWLP_USERDATA, (LONG_PTR) bar);
  SetWindowLongPtrW (bar->pass, GWLP_USERDATA, (LONG_PTR) bar);
  bar->user_proc = (WNDPROC) SetWindowLongPtrW (bar->user, GWLP_WNDPROC,
                                                (LONG_PTR) field_proc);
  bar->pass_proc = (WNDPROC) SetWindowLongPtrW (bar->pass, GWLP_WNDPROC,
                                                (LONG_PTR) field_proc);

  if (bar->ask_user && guess != NULL && *guess != '\0') {
    g_autofree wchar_t *w16 = (wchar_t *) g_utf8_to_utf16 (guess, -1, NULL, NULL, NULL);
    if (w16)
      SetWindowTextW (bar->user, w16);
  }

  place_fields (bar);

  if (has_fields) {
    ShowWindow (bar->hwnd, SW_SHOW);
    SetFocus (bar->ask_pass ? bar->pass : bar->user);
  } else {
    ShowWindow (bar->hwnd, SW_SHOWNA);
  }

  SetTimer (bar->hwnd, TIMER_EXPIRE, OFFER_MS, NULL);
}

void
ly_login_dismiss (void)
{
  if (the_bar != NULL && the_bar->hwnd != NULL)
    DestroyWindow (the_bar->hwnd);
  the_bar = NULL;
}

void
ly_login_navigated (void)
{
  if (the_bar == NULL)
    return;
  if (g_get_monotonic_time () - the_bar->shown_at > 5 * G_USEC_PER_SEC)
    ly_login_dismiss ();
}

void
ly_login_reanchor (HWND owner, RECT anchor)
{
  LyLoginBar *bar = the_bar;
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
