/* store.c — see store.h. */

#include "store.h"

#include <glib/gstdio.h>
#include <sqlite3.h>
#include <string.h>

struct _LyStore {
  sqlite3 *db;
  gboolean recording;   /* mirrors config: remember history */
};

void
ly_store_row_free (LyStoreRow *row)
{
  if (row == NULL)
    return;
  g_free (row->url);
  g_free (row->title);
  g_free (row);
}

/* --------------------------------------------------------------- helpers */

static gboolean
exec (LyStore *store, const char *sql)
{
  char *error = NULL;
  if (sqlite3_exec (store->db, sql, NULL, NULL, &error) != SQLITE_OK) {
    g_warning ("store: %s", error ? error : "unknown error");
    sqlite3_free (error);
    return FALSE;
  }
  return TRUE;
}

static sqlite3_stmt *
prepare (LyStore *store, const char *sql)
{
  sqlite3_stmt *stmt = NULL;
  if (sqlite3_prepare_v2 (store->db, sql, -1, &stmt, NULL) != SQLITE_OK) {
    g_warning ("store: %s", sqlite3_errmsg (store->db));
    return NULL;
  }
  return stmt;
}

static const char *
column_text (sqlite3_stmt *stmt, int index)
{
  const unsigned char *text = sqlite3_column_text (stmt, index);
  return text ? (const char *) text : "";
}

/* Pages that would only be noise in history or completion. */
static gboolean
url_is_recordable (const char *url)
{
  if (url == NULL || *url == '\0')
    return FALSE;
  if (g_str_has_prefix (url, "lyndon:") || g_str_has_prefix (url, "about:") ||
      g_str_has_prefix (url, "data:")   || g_str_has_prefix (url, "blob:"))
    return FALSE;
  return TRUE;
}

/* ------------------------------------------------------------ lifecycle */

LyStore *
ly_store_new (void)
{
  LyStore *store = g_new0 (LyStore, 1);
  store->recording = TRUE;

  g_autofree char *dir = ly_data_dir ();
  g_mkdir_with_parents (dir, 0700);
  g_autofree char *path = g_build_filename (dir, "browsing.db", NULL);

  if (sqlite3_open (path, &store->db) != SQLITE_OK) {
    g_warning ("store: cannot open %s: %s", path, sqlite3_errmsg (store->db));
    sqlite3_close (store->db);
    store->db = NULL;
    return store;
  }

  g_chmod (path, 0600);

  /* WAL keeps a page load from ever blocking on a checkpoint; NORMAL sync is
   * the right trade for data we can afford to lose the tail of. */
  exec (store,
    "PRAGMA journal_mode=WAL;"
    "PRAGMA synchronous=NORMAL;"
    "PRAGMA foreign_keys=ON;"
    "CREATE TABLE IF NOT EXISTS history ("
    "  url TEXT PRIMARY KEY, title TEXT, visits INTEGER DEFAULT 0,"
    "  last_visit INTEGER DEFAULT 0);"
    "CREATE INDEX IF NOT EXISTS history_recent ON history(last_visit DESC);"
    "CREATE TABLE IF NOT EXISTS bookmarks ("
    "  url TEXT PRIMARY KEY, title TEXT, added INTEGER DEFAULT 0);"
    "CREATE TABLE IF NOT EXISTS zoom ("
    "  host TEXT PRIMARY KEY, level REAL);"
    "CREATE TABLE IF NOT EXISTS site_perm ("
    "  host TEXT NOT NULL, perm INTEGER NOT NULL, policy INTEGER NOT NULL,"
    "  PRIMARY KEY(host, perm));"
    "CREATE TABLE IF NOT EXISTS session ("
    "  slot INTEGER PRIMARY KEY AUTOINCREMENT, win INTEGER, idx INTEGER,"
    "  url TEXT, title TEXT);");

  return store;
}

void
ly_store_free (LyStore *store)
{
  if (store == NULL)
    return;
  if (store->db != NULL) {
    exec (store, "PRAGMA optimize;");
    sqlite3_close (store->db);
  }
  g_free (store);
}

/* ---------------------------------------------------------------- history */

void
ly_store_record_visit (LyStore *store, const char *url, const char *title)
{
  if (store->db == NULL || !store->recording || !url_is_recordable (url))
    return;

  sqlite3_stmt *stmt = prepare (store,
    "INSERT INTO history(url, title, visits, last_visit) VALUES(?1, ?2, 1, ?3) "
    "ON CONFLICT(url) DO UPDATE SET "
    "  visits = visits + 1, last_visit = ?3,"
    /* Keep the old title if this visit has not produced one yet. */
    "  title = CASE WHEN ?2 <> '' THEN ?2 ELSE title END;");
  if (stmt == NULL)
    return;

  sqlite3_bind_text  (stmt, 1, url, -1, SQLITE_TRANSIENT);
  sqlite3_bind_text  (stmt, 2, title ?: "", -1, SQLITE_TRANSIENT);
  sqlite3_bind_int64 (stmt, 3, g_get_real_time () / G_USEC_PER_SEC);
  sqlite3_step (stmt);
  sqlite3_finalize (stmt);
}

void
ly_store_import_visit (LyStore *store, const char *url, const char *title,
                       int visits, gint64 when)
{
  if (store->db == NULL || !url_is_recordable (url))
    return;

  gint64 now = g_get_real_time () / G_USEC_PER_SEC;
  /* Guard against nonsense timestamps from a foreign profile. */
  if (when <= 0 || when > now)
    when = now;
  if (visits < 1)
    visits = 1;

  sqlite3_stmt *stmt = prepare (store,
    "INSERT INTO history(url, title, visits, last_visit) VALUES(?1, ?2, ?3, ?4) "
    "ON CONFLICT(url) DO UPDATE SET "
    "  visits = MAX(visits, ?3),"
    "  last_visit = MAX(last_visit, ?4),"
    "  title = CASE WHEN ?2 <> '' THEN ?2 ELSE title END;");
  if (stmt == NULL)
    return;

  sqlite3_bind_text  (stmt, 1, url, -1, SQLITE_TRANSIENT);
  sqlite3_bind_text  (stmt, 2, title ?: "", -1, SQLITE_TRANSIENT);
  sqlite3_bind_int   (stmt, 3, visits);
  sqlite3_bind_int64 (stmt, 4, when);
  sqlite3_step (stmt);
  sqlite3_finalize (stmt);
}

void
ly_store_update_title (LyStore *store, const char *url, const char *title)
{
  if (store->db == NULL || !store->recording || !url_is_recordable (url) ||
      title == NULL || *title == '\0')
    return;

  sqlite3_stmt *stmt = prepare (store,
    "UPDATE history SET title = ?2 WHERE url = ?1;");
  if (stmt == NULL)
    return;

  sqlite3_bind_text (stmt, 1, url, -1, SQLITE_TRANSIENT);
  sqlite3_bind_text (stmt, 2, title, -1, SQLITE_TRANSIENT);
  sqlite3_step (stmt);
  sqlite3_finalize (stmt);

  /* Keep a bookmark's label in step with the page's own title when the
   * bookmark was made before the title arrived. */
  sqlite3_stmt *mark = prepare (store,
    "UPDATE bookmarks SET title = ?2 WHERE url = ?1 AND (title IS NULL OR title = '' OR title = ?1);");
  if (mark != NULL) {
    sqlite3_bind_text (mark, 1, url, -1, SQLITE_TRANSIENT);
    sqlite3_bind_text (mark, 2, title, -1, SQLITE_TRANSIENT);
    sqlite3_step (mark);
    sqlite3_finalize (mark);
  }
}

static GPtrArray *
rows_from (sqlite3_stmt *stmt, gboolean has_bookmark_column)
{
  GPtrArray *rows = g_ptr_array_new_with_free_func ((GDestroyNotify) ly_store_row_free);

  while (sqlite3_step (stmt) == SQLITE_ROW) {
    LyStoreRow *row = g_new0 (LyStoreRow, 1);
    row->url    = g_strdup (column_text (stmt, 0));
    row->title  = g_strdup (column_text (stmt, 1));
    row->visits = sqlite3_column_int (stmt, 2);
    row->when   = sqlite3_column_int64 (stmt, 3);
    if (has_bookmark_column)
      row->bookmarked = sqlite3_column_int (stmt, 4) != 0;
    g_ptr_array_add (rows, row);
  }
  sqlite3_finalize (stmt);
  return rows;
}

GPtrArray *
ly_store_complete (LyStore *store, const char *text, guint limit)
{
  if (store->db == NULL || text == NULL || *text == '\0')
    return g_ptr_array_new_with_free_func ((GDestroyNotify) ly_store_row_free);

  /* Match anywhere in the URL or the title. LIKE with a leading wildcard
   * cannot use the index, but the table is small enough that it does not
   * matter, and matching mid-URL is what makes completion feel useful. */
  g_autofree char *escaped = g_strdup (text);
  for (char *p = escaped; *p != '\0'; p++)
    if (*p == '%' || *p == '_')
      *p = ' ';   /* neutralise LIKE wildcards typed by the user */
  g_autofree char *pattern = g_strdup_printf ("%%%s%%", escaped);

  sqlite3_stmt *stmt = prepare (store,
    "SELECT h.url, h.title, h.visits, h.last_visit,"
    "       EXISTS(SELECT 1 FROM bookmarks b WHERE b.url = h.url) AS marked "
    "FROM history h "
    "WHERE h.url LIKE ?1 OR h.title LIKE ?1 "
    /* Bookmarks first, then frequency, then recency. */
    "ORDER BY marked DESC, h.visits DESC, h.last_visit DESC "
    "LIMIT ?2;");
  if (stmt == NULL)
    return g_ptr_array_new_with_free_func ((GDestroyNotify) ly_store_row_free);

  sqlite3_bind_text (stmt, 1, pattern, -1, SQLITE_TRANSIENT);
  sqlite3_bind_int  (stmt, 2, (int) limit);
  return rows_from (stmt, TRUE);
}

GPtrArray *
ly_store_recent (LyStore *store, guint limit)
{
  if (store->db == NULL)
    return g_ptr_array_new_with_free_func ((GDestroyNotify) ly_store_row_free);

  sqlite3_stmt *stmt = prepare (store,
    "SELECT url, title, visits, last_visit, 0 FROM history "
    "ORDER BY last_visit DESC LIMIT ?1;");
  if (stmt == NULL)
    return g_ptr_array_new_with_free_func ((GDestroyNotify) ly_store_row_free);

  sqlite3_bind_int (stmt, 1, (int) limit);
  return rows_from (stmt, FALSE);
}

void
ly_store_forget_url (LyStore *store, const char *url)
{
  if (store->db == NULL || url == NULL)
    return;
  sqlite3_stmt *stmt = prepare (store, "DELETE FROM history WHERE url = ?1;");
  if (stmt == NULL)
    return;
  sqlite3_bind_text (stmt, 1, url, -1, SQLITE_TRANSIENT);
  sqlite3_step (stmt);
  sqlite3_finalize (stmt);
}

void
ly_store_clear_history (LyStore *store)
{
  if (store->db == NULL)
    return;
  exec (store, "DELETE FROM history; VACUUM;");
}

guint
ly_store_history_count (LyStore *store)
{
  if (store->db == NULL)
    return 0;
  sqlite3_stmt *stmt = prepare (store, "SELECT COUNT(*) FROM history;");
  if (stmt == NULL)
    return 0;
  guint count = (sqlite3_step (stmt) == SQLITE_ROW) ? (guint) sqlite3_column_int (stmt, 0) : 0;
  sqlite3_finalize (stmt);
  return count;
}

/* -------------------------------------------------------------- bookmarks */

gboolean
ly_store_is_bookmarked (LyStore *store, const char *url)
{
  if (store->db == NULL || url == NULL || *url == '\0')
    return FALSE;

  sqlite3_stmt *stmt = prepare (store, "SELECT 1 FROM bookmarks WHERE url = ?1;");
  if (stmt == NULL)
    return FALSE;
  sqlite3_bind_text (stmt, 1, url, -1, SQLITE_TRANSIENT);
  gboolean found = sqlite3_step (stmt) == SQLITE_ROW;
  sqlite3_finalize (stmt);
  return found;
}

void
ly_store_add_bookmark (LyStore *store, const char *url, const char *title)
{
  if (store->db == NULL || !url_is_recordable (url))
    return;

  sqlite3_stmt *stmt = prepare (store,
    "INSERT INTO bookmarks(url, title, added) VALUES(?1, ?2, ?3) "
    "ON CONFLICT(url) DO UPDATE SET title = ?2;");
  if (stmt == NULL)
    return;
  sqlite3_bind_text  (stmt, 1, url, -1, SQLITE_TRANSIENT);
  sqlite3_bind_text  (stmt, 2, title ?: url, -1, SQLITE_TRANSIENT);
  sqlite3_bind_int64 (stmt, 3, g_get_real_time () / G_USEC_PER_SEC);
  sqlite3_step (stmt);
  sqlite3_finalize (stmt);
}

void
ly_store_remove_bookmark (LyStore *store, const char *url)
{
  if (store->db == NULL || url == NULL)
    return;
  sqlite3_stmt *stmt = prepare (store, "DELETE FROM bookmarks WHERE url = ?1;");
  if (stmt == NULL)
    return;
  sqlite3_bind_text (stmt, 1, url, -1, SQLITE_TRANSIENT);
  sqlite3_step (stmt);
  sqlite3_finalize (stmt);
}

GPtrArray *
ly_store_bookmarks (LyStore *store, guint limit)
{
  if (store->db == NULL)
    return g_ptr_array_new_with_free_func ((GDestroyNotify) ly_store_row_free);

  sqlite3_stmt *stmt = prepare (store,
    "SELECT url, title, 0, added, 1 FROM bookmarks ORDER BY added DESC LIMIT ?1;");
  if (stmt == NULL)
    return g_ptr_array_new_with_free_func ((GDestroyNotify) ly_store_row_free);

  sqlite3_bind_int (stmt, 1, (int) limit);
  return rows_from (stmt, TRUE);
}

/* ------------------------------------------------------------------ zoom */

double
ly_store_zoom_for (LyStore *store, const char *host, double fallback)
{
  if (store->db == NULL || host == NULL || *host == '\0')
    return fallback;

  sqlite3_stmt *stmt = prepare (store, "SELECT level FROM zoom WHERE host = ?1;");
  if (stmt == NULL)
    return fallback;
  sqlite3_bind_text (stmt, 1, host, -1, SQLITE_TRANSIENT);

  double level = fallback;
  if (sqlite3_step (stmt) == SQLITE_ROW)
    level = sqlite3_column_double (stmt, 0);
  sqlite3_finalize (stmt);
  return level;
}

void
ly_store_set_zoom (LyStore *store, const char *host, double level)
{
  if (store->db == NULL || host == NULL || *host == '\0')
    return;

  /* A site back at the default needs no row of its own. */
  sqlite3_stmt *stmt = prepare (store,
    "INSERT INTO zoom(host, level) VALUES(?1, ?2) "
    "ON CONFLICT(host) DO UPDATE SET level = ?2;");
  if (stmt == NULL)
    return;
  sqlite3_bind_text   (stmt, 1, host, -1, SQLITE_TRANSIENT);
  sqlite3_bind_double (stmt, 2, level);
  sqlite3_step (stmt);
  sqlite3_finalize (stmt);
}

void
ly_store_clear_zoom (LyStore *store)
{
  if (store->db != NULL)
    exec (store, "DELETE FROM zoom;");
}

/* ----------------------------------------------------- site permissions */

int
ly_store_site_permission (LyStore *store, const char *host, int permission)
{
  if (store->db == NULL || host == NULL || *host == '\0')
    return -1;

  sqlite3_stmt *stmt = prepare (store,
    "SELECT policy FROM site_perm WHERE host = ?1 AND perm = ?2;");
  if (stmt == NULL)
    return -1;

  sqlite3_bind_text (stmt, 1, host, -1, SQLITE_TRANSIENT);
  sqlite3_bind_int  (stmt, 2, permission);

  int policy = -1;
  if (sqlite3_step (stmt) == SQLITE_ROW)
    policy = sqlite3_column_int (stmt, 0);
  sqlite3_finalize (stmt);
  return policy;
}

void
ly_store_set_site_permission (LyStore *store, const char *host, int permission, int policy)
{
  if (store->db == NULL || host == NULL || *host == '\0')
    return;

  /* A policy of -1 means "stop overriding", which is a delete, not a row. */
  if (policy < 0) {
    sqlite3_stmt *stmt = prepare (store,
      "DELETE FROM site_perm WHERE host = ?1 AND perm = ?2;");
    if (stmt == NULL)
      return;
    sqlite3_bind_text (stmt, 1, host, -1, SQLITE_TRANSIENT);
    sqlite3_bind_int  (stmt, 2, permission);
    sqlite3_step (stmt);
    sqlite3_finalize (stmt);
    return;
  }

  sqlite3_stmt *stmt = prepare (store,
    "INSERT INTO site_perm(host, perm, policy) VALUES(?1, ?2, ?3) "
    "ON CONFLICT(host, perm) DO UPDATE SET policy = ?3;");
  if (stmt == NULL)
    return;
  sqlite3_bind_text (stmt, 1, host, -1, SQLITE_TRANSIENT);
  sqlite3_bind_int  (stmt, 2, permission);
  sqlite3_bind_int  (stmt, 3, policy);
  sqlite3_step (stmt);
  sqlite3_finalize (stmt);
}

guint
ly_store_site_override_count (LyStore *store, const char *host)
{
  if (store->db == NULL || host == NULL)
    return 0;

  sqlite3_stmt *stmt = prepare (store,
    "SELECT COUNT(*) FROM site_perm WHERE host = ?1;");
  if (stmt == NULL)
    return 0;
  sqlite3_bind_text (stmt, 1, host, -1, SQLITE_TRANSIENT);
  guint n = (sqlite3_step (stmt) == SQLITE_ROW) ? (guint) sqlite3_column_int (stmt, 0) : 0;
  sqlite3_finalize (stmt);
  return n;
}

void
ly_store_clear_site (LyStore *store, const char *host)
{
  if (store->db == NULL || host == NULL || *host == '\0')
    return;

  sqlite3_stmt *perms = prepare (store, "DELETE FROM site_perm WHERE host = ?1;");
  if (perms != NULL) {
    sqlite3_bind_text (perms, 1, host, -1, SQLITE_TRANSIENT);
    sqlite3_step (perms);
    sqlite3_finalize (perms);
  }

  sqlite3_stmt *zoom = prepare (store, "DELETE FROM zoom WHERE host = ?1;");
  if (zoom != NULL) {
    sqlite3_bind_text (zoom, 1, host, -1, SQLITE_TRANSIENT);
    sqlite3_step (zoom);
    sqlite3_finalize (zoom);
  }

  /* History entries for the host go too: "clear this site" should not leave
   * the site's pages sitting in address-bar suggestions. */
  sqlite3_stmt *hist = prepare (store,
    "DELETE FROM history WHERE url LIKE 'http://' || ?1 || '/%' "
    "   OR url LIKE 'https://' || ?1 || '/%'"
    "   OR url LIKE 'http://' || ?1 || ':%'"
    "   OR url LIKE 'https://' || ?1 || ':%';");
  if (hist != NULL) {
    sqlite3_bind_text (hist, 1, host, -1, SQLITE_TRANSIENT);
    sqlite3_step (hist);
    sqlite3_finalize (hist);
  }
}

/* --------------------------------------------------------------- session */

void
ly_store_save_session (LyStore *store, GPtrArray *rows)
{
  if (store->db == NULL)
    return;

  exec (store, "BEGIN IMMEDIATE; DELETE FROM session;");

  sqlite3_stmt *stmt = prepare (store,
    "INSERT INTO session(win, idx, url, title) VALUES(?1, ?2, ?3, ?4);");
  if (stmt != NULL) {
    for (guint i = 0; i < rows->len; i++) {
      LyStoreRow *row = g_ptr_array_index (rows, i);
      if (!url_is_recordable (row->url))
        continue;
      sqlite3_bind_int  (stmt, 1, row->window);
      sqlite3_bind_int  (stmt, 2, row->index);
      sqlite3_bind_text (stmt, 3, row->url, -1, SQLITE_TRANSIENT);
      sqlite3_bind_text (stmt, 4, row->title ?: "", -1, SQLITE_TRANSIENT);
      sqlite3_step (stmt);
      sqlite3_reset (stmt);
    }
    sqlite3_finalize (stmt);
  }
  exec (store, "COMMIT;");
}

GPtrArray *
ly_store_load_session (LyStore *store)
{
  GPtrArray *rows = g_ptr_array_new_with_free_func ((GDestroyNotify) ly_store_row_free);
  if (store->db == NULL)
    return rows;

  sqlite3_stmt *stmt = prepare (store,
    "SELECT win, idx, url, title FROM session ORDER BY win, idx;");
  if (stmt == NULL)
    return rows;

  while (sqlite3_step (stmt) == SQLITE_ROW) {
    LyStoreRow *row = g_new0 (LyStoreRow, 1);
    row->window = sqlite3_column_int (stmt, 0);
    row->index  = sqlite3_column_int (stmt, 1);
    row->url    = g_strdup (column_text (stmt, 2));
    row->title  = g_strdup (column_text (stmt, 3));
    g_ptr_array_add (rows, row);
  }
  sqlite3_finalize (stmt);
  return rows;
}
