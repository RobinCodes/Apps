/* engine.h — the single shared WebKit context.
 *
 * One WebKitWebContext and one WebKitNetworkSession are shared by every tab in
 * every window, so WebKit runs exactly one network process for the whole
 * browser. Per-tab state is limited to the WebView and its content manager.
 */
#pragma once

#include "lyndon.h"

G_BEGIN_DECLS

typedef struct _LyEngine LyEngine;

LyEngine *ly_engine_new  (LyConfig *cfg);
void      ly_engine_free (LyEngine *engine);

WebKitWebContext     *ly_engine_context (LyEngine *engine);
WebKitNetworkSession *ly_engine_session (LyEngine *engine);
WebKitSettings       *ly_engine_settings (LyEngine *engine);

/* Push the current configuration into WebKit. Called whenever settings change;
 * everything here is a live property, so no tab needs reloading. */
void ly_engine_apply (LyEngine *engine);

/* Install the privacy and dark-mode user scripts on a tab's content manager.
 * Re-callable: it clears what it previously injected. */
void ly_engine_prepare_content_manager (LyEngine                 *engine,
                                        WebKitUserContentManager *ucm);

/* Colour the WebView's own canvas so page loads do not flash white in dark
 * mode, and re-run the injection for the current settings. */
void ly_engine_style_web_view (LyEngine *engine, WebKitWebView *view);

typedef void (*LyEngineClearedFn) (gpointer user_data);

void ly_engine_clear_data (LyEngine          *engine,
                           WebKitWebsiteDataTypes types,
                           LyEngineClearedFn  done,
                           gpointer           user_data);

/* Remove cookies, storage and caches belonging to one host (and its
 * subdomains), leaving every other site untouched. */
void ly_engine_clear_data_for_host (LyEngine *engine, const char *host);

/* True when the UI is currently rendering dark, taking "follow system" into
 * account. */
gboolean ly_engine_is_dark (void);

G_END_DECLS
