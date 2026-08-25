/* tab.h — one browsing tab: a WebView plus the policy that surrounds it. */
#pragma once

#include "lyndon.h"
#include "engine.h"
#include "blocker.h"
#include "store.h"
#include "passwords.h"

G_BEGIN_DECLS

/* The window supplies these rather than connecting to signals: a plain vtable
 * keeps the ownership rules obvious, and `create_view` has to return a value,
 * which GSignal makes needlessly awkward in C. */
typedef struct {
  void           (*changed)      (LyTab *tab, gpointer data);
  void           (*status)       (LyTab *tab, const char *hovered_uri, gpointer data);
  WebKitWebView *(*create_view)  (LyTab *tab, gpointer data);
  void           (*close)        (LyTab *tab, gpointer data);
  void           (*found)        (LyTab *tab, guint matches, gpointer data);
  /* Context-menu requests that need a window: open this URI in a new tab, or
   * run this text as a search. */
  void           (*open_uri)     (LyTab *tab, const char *uri, gboolean background,
                                  gpointer data);
  void           (*search_for)   (LyTab *tab, const char *text, gpointer data);
} LyTabDelegate;

/* Everything a tab needs from its window, bundled so the constructor does not
 * grow a parameter per feature. */
typedef struct {
  LyEngine    *engine;
  LyBlocker   *blocker;
  LyConfig    *cfg;
  LyStore     *store;
  LyPasswords *passwords;
  /* NULL uses the shared persistent session; a private window passes its own
   * ephemeral one, which is what keeps its cookies and cache off disk. */
  WebKitNetworkSession *session;
} LyTabContext;

LyTab *ly_tab_new (const LyTabContext *context);

/* Wrap a WebView WebKit created for us (window.open, target=_blank), so the
 * opener relationship and its session storage survive. */
LyTab *ly_tab_new_for_view (const LyTabContext *context, WebKitWebView *view);

gboolean ly_tab_is_private (LyTab *tab);
gboolean ly_tab_is_playing_audio (LyTab *tab);
gboolean ly_tab_is_muted   (LyTab *tab);
void     ly_tab_set_muted  (LyTab *tab, gboolean muted);
void     ly_tab_print      (LyTab *tab);
void     ly_tab_save_page  (LyTab *tab);
void     ly_tab_load_html  (LyTab *tab, const char *html, const char *base_uri);
void     ly_tab_set_caret_browsing (LyTab *tab, gboolean enabled);
gboolean ly_tab_caret_browsing     (LyTab *tab);

/* Certificate for the committed document, or FALSE when the page is not TLS.
 * The certificate is owned by the view; do not unref it. */
gboolean ly_tab_tls_info (LyTab *tab, GTlsCertificate **certificate,
                          GTlsCertificateFlags *errors);

/* The page's HTML as the server sent it; NULL on failure. */
typedef void (*LyTabSourceFn) (const char *source, const char *uri, gpointer user_data);
void ly_tab_fetch_source (LyTab *tab, LyTabSourceFn callback, gpointer user_data);

void ly_tab_set_delegate (LyTab *tab, const LyTabDelegate *delegate, gpointer data);

WebKitWebView *ly_tab_web_view (LyTab *tab);

void        ly_tab_load        (LyTab *tab, const char *uri);
void        ly_tab_load_input  (LyTab *tab, const char *text);
const char *ly_tab_uri         (LyTab *tab);
const char *ly_tab_title       (LyTab *tab);
GdkTexture *ly_tab_favicon     (LyTab *tab);
gboolean    ly_tab_is_loading  (LyTab *tab);
double      ly_tab_progress    (LyTab *tab);
gboolean    ly_tab_can_go_back    (LyTab *tab);
gboolean    ly_tab_can_go_forward (LyTab *tab);

void ly_tab_go_back    (LyTab *tab);
void ly_tab_go_forward (LyTab *tab);
void ly_tab_reload     (LyTab *tab, gboolean bypass_cache);
void ly_tab_stop       (LyTab *tab);

void   ly_tab_set_zoom (LyTab *tab, double zoom);
double ly_tab_zoom     (LyTab *tab);

/* Blocking state for the host currently loaded. */
gboolean ly_tab_protection_on   (LyTab *tab);
void     ly_tab_toggle_protection (LyTab *tab);
char    *ly_tab_host            (LyTab *tab);

/* Re-read configuration that affects this tab (scripts, filters, zoom). */
void ly_tab_refresh_policy (LyTab *tab);

void ly_tab_find       (LyTab *tab, const char *text, gboolean backwards);
void ly_tab_find_next  (LyTab *tab);
void ly_tab_find_prev  (LyTab *tab);
void ly_tab_find_close (LyTab *tab);

G_END_DECLS
