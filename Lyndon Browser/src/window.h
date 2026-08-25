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

/* Append this window's tabs to a session snapshot. */
void   ly_window_collect_session (LyWindow *self, GPtrArray *rows, int window_index);
LyTab *ly_window_restore_tab     (LyWindow *self, const char *url, const char *title);

LyTab *ly_window_open_tab (LyWindow *self, const char *uri, gboolean background);
LyTab *ly_window_active_tab (LyWindow *self);

void ly_window_toast (LyWindow *self, const char *text);

/* Re-read configuration: effect classes, tab policies, chrome density. */
void ly_window_refresh (LyWindow *self);

G_END_DECLS
