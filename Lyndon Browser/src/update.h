/* update.h — noticing that a newer Lyndon has been built, and taking it.
 *
 * None of the work is here. Lyndon is started by lyndon.py, which fetches
 * behind the running browser and, when the source has moved on, builds the
 * new version into dist-next/. All this does is watch the one file the
 * launcher writes when that build lands, say so, and — if the answer is yes
 * — ask the launcher to bring the new one up once this process is gone.
 *
 * Nothing here reaches the network, and nothing here runs git: a browser is
 * the wrong process to be doing either from. Started any other way than
 * through the launcher — `make run`, or the binary by hand — ly_update_new
 * answers NULL and the browser has no update machinery in it at all.
 */
#pragma once

#include "lyndon.h"

G_BEGIN_DECLS

typedef struct _LyUpdate LyUpdate;

typedef void (*LyUpdateReadyFn) (LyUpdate *update, gpointer user_data);

/* NULL unless the launcher started us and said where to watch. */
LyUpdate *ly_update_new  (void);
void      ly_update_free (LyUpdate *update);

/* Raised once, when a build becomes available while the browser is running.
 * A build that was already waiting at startup never raises it — there is no
 * window to put the offer in yet — so a window asks ly_update_ready as it is
 * built instead, and both paths end in the same offer. */
void ly_update_set_ready_callback (LyUpdate        *update,
                                   LyUpdateReadyFn  ready,
                                   gpointer         user_data);

gboolean ly_update_ready (LyUpdate *update);

/* Start the launcher again, told to wait for this process to exit before it
 * swaps the new build in and runs it. The caller quits straight afterwards —
 * through the application's own quit, so everything that happens on the way
 * out still happens. FALSE, with `error` set, when the launcher could not be
 * started, in which case nothing has been promised and quitting would only
 * close the browser. */
gboolean ly_update_restart (LyUpdate *update, GError **error);

G_END_DECLS
