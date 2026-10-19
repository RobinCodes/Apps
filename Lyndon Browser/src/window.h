/* window.h — a browser window. */
#pragma once

#include "lyndon.h"
#include "app.h"
#include "tab.h"

G_BEGIN_DECLS

LyWindow *ly_window_new (LyApp *app);
/* Backed by an ephemeral WebKit session: no cookies, cache or history reach
 * disk, and the session is destroyed with the window. */
LyWindow *ly_window_new_private (LyApp *app);
gboolean  ly_window_is_private  (LyWindow *self);

/* Bank every tab's attention against the page it is showing. Called on the
 * way out, while the history store is still there to bank it in. */
void   ly_window_flush_attention (LyWindow *self);

/* Append this window's tabs to a session snapshot. */
void   ly_window_collect_session (LyWindow *self, GPtrArray *rows, int window_index);
LyTab *ly_window_restore_tab     (LyWindow *self, const char *url, const char *title);

LyTab *ly_window_open_tab (LyWindow *self, const char *uri, gboolean background);
LyTab *ly_window_active_tab (LyWindow *self);

void ly_window_toast (LyWindow *self, const char *text);

/* "A newer Lyndon is ready" with a Restart button, for as long as it takes
 * someone to answer it. Raised by LyApp when a build lands, and by a window
 * built while one is already waiting. */
void ly_window_offer_restart (LyWindow *self);

/* Re-read configuration: effect classes, tab policies, chrome density. */
void ly_window_refresh (LyWindow *self);

/* A site archive started, moved or stopped: re-read what the transfers button
 * should be saying. */
void ly_window_archives_changed (LyWindow *self);

/* Record this window's size and maximised state in the configuration, so the
 * next window opens the shape this one was left in. */
void ly_window_save_geometry (LyWindow *self);

G_END_DECLS
