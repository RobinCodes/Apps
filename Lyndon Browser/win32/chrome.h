/* chrome.h — the window around the pages.
 *
 * The Linux build gets its tab strip, header bar and entry from libadwaita.
 * There is no libadwaita here, so the chrome is drawn: a tab strip and a
 * toolbar painted in WM_PAINT, hit-tested in WM_LBUTTONDOWN, over a real
 * EDIT control for the address bar because text editing is not worth
 * reimplementing.
 *
 * One window owns its tabs and lends each one the same LyBlock and LyStore.
 */
#pragma once

#include "lyndon.h"
#include "block.h"
#include "store.h"
#include "tab.h"

#include <windows.h>

G_BEGIN_DECLS

/* Register the window class. Once, before the first ly_window_new(). */
gboolean ly_window_register (HINSTANCE instance);

LyWindow *ly_window_new (HINSTANCE instance, LyConfig *cfg, LyStore *store,
                         LyBlock *block, const char *url);

/* Open another tab and select it. NULL url means the configured homepage. */
void ly_window_open_tab (LyWindow *win, const char *url);

HWND ly_window_hwnd (LyWindow *win);

/* Windows still open. main() quits when this reaches zero. */
guint ly_window_count (void);

/* Offered every keystroke before TranslateMessage. The WebView2 child window
 * has the focus most of the time and swallows the accelerators the browser
 * itself needs, so they are claimed here first. Returns TRUE when handled. */
gboolean ly_window_handle_key (LyWindow *win, MSG *msg);

/* The window a message belongs to, or NULL. The message loop needs this to
 * offer keys to the right window. */
LyWindow *ly_window_from_message (MSG *msg);

/* Called when the shared WebView2 environment finishes starting, so windows
 * created before it was ready can build the tabs they were asked for. */
void ly_window_environment_ready (gboolean ok, const char *message);

G_END_DECLS
