/* tab.h — one WebView2 per tab.
 *
 * The Linux build puts a WebKitWebView inside a GtkWidget and lets GTK own
 * it. WebView2 is a COM object hosted in an HWND, created asynchronously in
 * two stages — first an environment shared by every tab, then a controller
 * per tab — so a tab exists and is shown before it can navigate anywhere.
 * ly_tab_navigate() before that point remembers the URL and replays it when
 * the controller arrives, which is why nothing else has to know about the
 * two stages.
 *
 * The environment is created once and shared. That is what makes cookies, the
 * cache and logged-in sessions common to all tabs, and it is also the reason
 * the profile directory is chosen here rather than per tab.
 */
#pragma once

#include "lyndon.h"
#include "block.h"
#include "downloads.h"
#include "passwords.h"
#include "store.h"

#include <windows.h>

G_BEGIN_DECLS

typedef struct _LyTab LyTab;

/* Called whenever anything the chrome draws has changed: title, URL, loading
 * state, back/forward availability, block count. */
typedef void (*LyTabChangedFn) (LyTab *tab, gpointer user_data);
/* A link that asked for a new window; the chrome decides to open a tab. */
typedef void (*LyTabNewWindowFn) (LyTab *source, const char *url, gpointer user_data);

/* A key pressed while the page had the focus. Return TRUE to consume it.
 *
 * This is not a nicety. WebView2 runs its own windows, and while one of them
 * has the focus the host's GetMessage loop never sees a keystroke at all —
 * Ctrl+T and Ctrl+L simply do nothing. WebView2 offers this event for exactly
 * that reason, and it is the only route by which the browser's own shortcuts
 * can work while a page is focused. */
typedef gboolean (*LyTabAccelFn) (LyTab *tab, guint vkey, gpointer user_data);

/* A login the page just submitted, for the window to offer to remember. The
 * tab does not save it itself: whether to ask, and what the answer was, is a
 * question for the window that has somewhere to put the question. */
typedef void (*LyTabLoginFn) (LyTab *tab, const char *origin, const char *username,
                              const char *password, gpointer user_data);

/* A site asking for the camera, the microphone, a location. Return an
 * LyPolicy; LY_POLICY_ASK leaves the decision to WebView2's own prompt. */
typedef LyPolicy (*LyTabPermissionFn) (LyTab *tab, LyPermKind kind,
                                       const char *origin, gpointer user_data);

/* Create the shared environment. `ready` fires on the UI thread once tabs can
 * be made; it fires with ok=FALSE when the WebView2 runtime is missing. */
typedef void (*LyEnvReadyFn) (gboolean ok, const char *message, gpointer user_data);
void ly_webview_init (const char *profile_dir, const char *resource_dir,
                      LyConfig *cfg, LyEnvReadyFn ready, gpointer user_data);
void ly_webview_shutdown (void);
gboolean ly_webview_ready (void);

/* The version of the installed runtime, or NULL. Free with g_free(). */
char *ly_webview_runtime_version (void);

LyTab *ly_tab_new  (HWND parent, LyConfig *cfg, LyBlock *block,
                    LyDownloads *downloads, LyPasswords *passwords,
                    LyStore *store, const char *url);
void   ly_tab_free (LyTab *tab);

void ly_tab_set_callbacks (LyTab *tab, LyTabChangedFn changed,
                           LyTabNewWindowFn new_window, gpointer user_data);
void ly_tab_set_accelerator_handler (LyTab *tab, LyTabAccelFn accel);
void ly_tab_set_login_handler (LyTab *tab, LyTabLoginFn login);
void ly_tab_set_permission_handler (LyTab *tab, LyTabPermissionFn permission);

/* Put a saved login into the form the page is showing. */
void ly_tab_fill_login (LyTab *tab, const char *username, const char *password);

/* Re-read the settings that WebView2 can be told about after creation. */
void ly_tab_apply_config (LyTab *tab);

void ly_tab_navigate (LyTab *tab, const char *url);
void ly_tab_back     (LyTab *tab);
void ly_tab_forward  (LyTab *tab);
void ly_tab_reload   (LyTab *tab);
void ly_tab_stop     (LyTab *tab);
void ly_tab_focus    (LyTab *tab);

/* Where the page is drawn, in client coordinates of the parent window. */
void ly_tab_set_bounds (LyTab *tab, RECT bounds);
void ly_tab_set_visible (LyTab *tab, gboolean visible);

void ly_tab_set_zoom (LyTab *tab, double zoom);
double ly_tab_zoom (LyTab *tab);

const char *ly_tab_title    (LyTab *tab);
const char *ly_tab_url      (LyTab *tab);
gboolean    ly_tab_loading  (LyTab *tab);
gboolean    ly_tab_can_back (LyTab *tab);
gboolean    ly_tab_can_forward (LyTab *tab);
gboolean    ly_tab_secure   (LyTab *tab);
guint       ly_tab_blocked  (LyTab *tab);

/* Blocking can be turned off for one tab, for a site that needs it. */
void     ly_tab_set_blocking (LyTab *tab, gboolean enabled);
gboolean ly_tab_blocking (LyTab *tab);

G_END_DECLS
