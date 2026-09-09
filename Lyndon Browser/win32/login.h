/* login.h — the "save this login?" bar.
 *
 * The Linux build puts a card over the page inside the tab's GtkOverlay, and
 * grows an entry box in it when the page gave up only half a login. WebView2
 * hosts its own child window over the whole page area and nothing can be
 * placed inside it, so the same card is a popup anchored under the toolbar
 * instead. Everything else is the same: the same three answers, the same
 * ninety-second life, and the same entry box for whichever half is missing.
 *
 * One bar at a time. A second offer replaces the first, because two cards
 * asking about two sites at once is a question nobody answers.
 */
#pragma once

#include "lyndon.h"
#include "tab.h"

#include <windows.h>

G_BEGIN_DECLS

typedef enum {
  LY_LOGIN_ANSWER_SAVE,
  LY_LOGIN_ANSWER_DISMISS,   /* "Not now", Escape, or the offer timing out */
  LY_LOGIN_ANSWER_NEVER,     /* never ask for this origin again */
} LyLoginAnswer;

/* `username` and `password` are what should actually be stored: whatever the
 * page captured, with anything the user typed into the bar in its place. Both
 * are NULL for anything but LY_LOGIN_ANSWER_SAVE. */
typedef void (*LyLoginDoneFn) (LyLoginAnswer answer, const char *origin,
                               const char *username, const char *password,
                               gpointer user_data);

gboolean ly_login_register (HINSTANCE instance);

/* `anchor` is where the page area begins, in screen coordinates; the bar is
 * centred across it and sits just below the top, where the Linux card does.
 * `guess` pre-fills the username box for a two-step login, or is NULL. */
void ly_login_offer (HWND owner, HINSTANCE instance, RECT anchor, int dpi,
                     gboolean dark, LyLoginKind kind, const char *origin,
                     const char *username, const char *password,
                     gboolean is_update, const char *guess,
                     LyLoginDoneFn done, gpointer user_data);

/* Take the bar down without answering. Called when the tab is switched away
 * from or the window closes. */
void ly_login_dismiss (void);

/* A page started loading. Signing in navigates, and taking the card down on
 * that navigation would mean never seeing it at all — so this only dismisses
 * an offer that has been up long enough for the load to be the user moving
 * on instead. The same five seconds src/tab.c allows. */
void ly_login_navigated (void);

/* Keep it over the page area when the window is moved or resized. */
void ly_login_reanchor (HWND owner, RECT anchor);

G_END_DECLS
