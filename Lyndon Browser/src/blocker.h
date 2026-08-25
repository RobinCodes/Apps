/* blocker.h — content blocking built on WebKit's native filter engine.
 *
 * Rules are compiled once into a WebKitUserContentFilter, which WebKit stores
 * as a bytecode DFA and evaluates inside the network process. Matching a
 * request therefore costs no JavaScript, no IPC to the UI process, and no
 * per-request allocation — the reason this blocker is cheaper than any
 * extension-based one.
 */
#pragma once

#include "lyndon.h"
#include "abp.h"

G_BEGIN_DECLS

typedef void (*LyBlockerReadyFn) (LyBlocker *blocker, gpointer user_data);

LyBlocker *ly_blocker_new    (LyConfig *cfg);
void       ly_blocker_free   (LyBlocker *blocker);

/* Register a per-tab content manager. Tabs get their own manager so a site
 * exception can be honoured by simply not installing the filter on that tab. */
void       ly_blocker_attach (LyBlocker *blocker, WebKitUserContentManager *ucm);
void       ly_blocker_detach (LyBlocker *blocker, WebKitUserContentManager *ucm);

/* Turn blocking on or off for one tab, e.g. when it navigates to an excepted
 * host. Cheap: it adds or removes an already-compiled filter. */
void       ly_blocker_set_active (LyBlocker                *blocker,
                                  WebKitUserContentManager *ucm,
                                  gboolean                  active);

/* Recompile from the current configuration. Safe to call repeatedly; results
 * are cached on disk by content hash so a toggle-and-untoggle is free. */
void       ly_blocker_rebuild (LyBlocker *blocker);

/* Fetch or refresh subscribed filter lists, then rebuild. */
void       ly_blocker_update_subscriptions (LyBlocker *blocker, gboolean force);

gboolean          ly_blocker_ready         (LyBlocker *blocker);
const LyAbpStats *ly_blocker_stats         (LyBlocker *blocker);
guint             ly_blocker_rule_count    (LyBlocker *blocker);
const char       *ly_blocker_status_text   (LyBlocker *blocker);

void       ly_blocker_set_ready_callback (LyBlocker        *blocker,
                                          LyBlockerReadyFn  fn,
                                          gpointer          user_data);

/* Path of the user's own rules file, created on first access. */
char      *ly_blocker_custom_rules_path (void);

G_END_DECLS
