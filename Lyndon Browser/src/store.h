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
  int     typed;     /* visits that began with someone typing the address */
  gint64  dwell;     /* seconds of attention, capped per visit — see store.c */
  gint64  when;      /* unix seconds */
  int     window;    /* session rows only */
  int     index;     /* session rows only */
  gboolean bookmarked;
  double  score;     /* completion rank; only ly_store_complete sets it */
} LyStoreRow;

void ly_store_row_free (LyStoreRow *row);

LyStore *ly_store_new  (void);
void     ly_store_free (LyStore *store);

/* -- history ------------------------------------------------------------ */
/* `typed` means the visit was committed through the address bar — typed, or
 * pasted into it and gone to — rather than followed from a link. It is the
 * strongest thing the ranking knows, because it is the same question the
 * address bar is asking. */
void       ly_store_record_visit  (LyStore *store, const char *url, const char *title,
                                   gboolean typed);
/* Titles arrive from the web process over IPC and can land after the load has
 * already finished, so they are recorded separately from the visit itself. */
void       ly_store_update_title  (LyStore *store, const char *url, const char *title);
/* Insert a visit with its real count and timestamp, for imports. Keeps the
 * larger visit count and the later date when the URL is already known, so
 * importing twice cannot inflate or rewind anything. */
void       ly_store_import_visit  (LyStore *store, const char *url, const char *title,
                                   int visits, int typed, gint64 when);
/* Bank attention against a page: seconds it was the one on screen in a window
 * that had focus. Capped on the way in, and ignored for a URL that is not
 * already in history, so a page nobody chose to record earns nothing. */
void       ly_store_add_dwell     (LyStore *store, const char *url, int seconds);
/* Ranked completions for the address bar. Caller frees with g_ptr_array_unref.
 * The ranking is described at length in store.c. */
GPtrArray *ly_store_complete      (LyStore *store, const char *text, guint limit);
/* The one address worth finishing `text` into, in the form a person types it
 * — no scheme, no "www.", no bare trailing slash — or NULL when nothing is a
 * confident enough answer to rewrite what someone is in the middle of typing.
 * The result always starts with `text` (ignoring ASCII case) and is always
 * longer. Anchored and cheap: meant to be called on every keystroke. */
char      *ly_store_autocomplete  (LyStore *store, const char *text);
GPtrArray *ly_store_recent        (LyStore *store, guint limit);
/* One URL per site read in the last `days` days and visited at least
 * `min_visits` times across its pages, busiest first: what the offline sweep
 * means by "a site you are still reading". The URL is that site's most-read
 * page, which is where a crawl of it should start. Caller frees the array of
 * char* with g_ptr_array_unref. */
GPtrArray *ly_store_recent_hosts  (LyStore *store, int days, int min_visits,
                                   guint limit);
void       ly_store_forget_url    (LyStore *store, const char *url);
void       ly_store_clear_history (LyStore *store);
guint      ly_store_history_count (LyStore *store);

/* -- bookmarks ---------------------------------------------------------- */
gboolean   ly_store_is_bookmarked  (LyStore *store, const char *url);
void       ly_store_add_bookmark   (LyStore *store, const char *url, const char *title);
void       ly_store_remove_bookmark(LyStore *store, const char *url);
/* The label, and only the label: a bookmark is identified by its URL, so
 * renaming one never moves it. An empty or blank name is refused rather than
 * stored, because a row with no label draws as its own URL and the rename
 * would look like it had been ignored. TRUE when a row was changed. */
gboolean   ly_store_rename_bookmark(LyStore *store, const char *url, const char *title);
/* The label stored for `url`, or NULL when it is not bookmarked. The rename
 * dialog opens on this rather than on whatever the row happens to be drawing,
 * which for a bookmark with no label of its own is the URL. */
char      *ly_store_bookmark_title (LyStore *store, const char *url);
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
