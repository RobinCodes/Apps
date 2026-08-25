/* store.h — history, bookmarks, per-site zoom and the saved session.
 *
 * One SQLite file, opened once, in WAL mode. SQLite is already resident
 * because WebKit links it, so this costs no new dependency at runtime — and it
 * gives ordered prefix queries for address-bar completion that a flat file
 * would need a hand-rolled index to match.
 */
#pragma once

#include "lyndon.h"

G_BEGIN_DECLS

typedef struct {
  char   *url;
  char   *title;
  int     visits;
  gint64  when;      /* unix seconds */
  int     window;    /* session rows only */
  int     index;     /* session rows only */
  gboolean bookmarked;
} LyStoreRow;

void ly_store_row_free (LyStoreRow *row);

LyStore *ly_store_new  (void);
void     ly_store_free (LyStore *store);

/* -- history ------------------------------------------------------------ */
void       ly_store_record_visit  (LyStore *store, const char *url, const char *title);
/* Titles arrive from the web process over IPC and can land after the load has
 * already finished, so they are recorded separately from the visit itself. */
void       ly_store_update_title  (LyStore *store, const char *url, const char *title);
/* Insert a visit with its real count and timestamp, for imports. Keeps the
 * larger visit count and the later date when the URL is already known, so
 * importing twice cannot inflate or rewind anything. */
void       ly_store_import_visit  (LyStore *store, const char *url, const char *title,
                                   int visits, gint64 when);
/* Ranked completions for the address bar: bookmarks first, then by how often
 * and how recently a page was visited. Caller frees with g_ptr_array_unref. */
GPtrArray *ly_store_complete      (LyStore *store, const char *text, guint limit);
GPtrArray *ly_store_recent        (LyStore *store, guint limit);
void       ly_store_forget_url    (LyStore *store, const char *url);
void       ly_store_clear_history (LyStore *store);
guint      ly_store_history_count (LyStore *store);

/* -- bookmarks ---------------------------------------------------------- */
gboolean   ly_store_is_bookmarked  (LyStore *store, const char *url);
void       ly_store_add_bookmark   (LyStore *store, const char *url, const char *title);
void       ly_store_remove_bookmark(LyStore *store, const char *url);
GPtrArray *ly_store_bookmarks      (LyStore *store, guint limit);

/* -- per-site zoom ------------------------------------------------------ */
double ly_store_zoom_for   (LyStore *store, const char *host, double fallback);
void   ly_store_set_zoom   (LyStore *store, const char *host, double level);
void   ly_store_clear_zoom (LyStore *store);

/* -- per-site permissions ----------------------------------------------- */
/* policy is an LyPolicy; -1 means "no override, use the global default". */
int  ly_store_site_permission     (LyStore *store, const char *host, int permission);
void ly_store_set_site_permission (LyStore *store, const char *host, int permission,
                                   int policy);
void ly_store_clear_site          (LyStore *store, const char *host);
guint ly_store_site_override_count (LyStore *store, const char *host);

/* -- session ------------------------------------------------------------ */
void       ly_store_save_session (LyStore *store, GPtrArray *rows);
GPtrArray *ly_store_load_session (LyStore *store);

G_END_DECLS
