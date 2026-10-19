/* store.c — see store.h. */

#include "store.h"

#include <glib/gstdio.h>
#include <math.h>
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

/* LIKE has two wildcards of its own, and a URL is full of underscores. Escape
 * them rather than blanking them, so that searching for "Foo_Bar" finds the
 * page called Foo_Bar. Every LIKE below pairs this with ESCAPE '\\'. */
static char *
like_escape (const char *text)
{
  GString *out = g_string_sized_new (strlen (text) + 8);
  for (const char *p = text; *p != '\0'; p++) {
    if (*p == '%' || *p == '_' || *p == '\\')
      g_string_append_c (out, '\\');
    g_string_append_c (out, *p);
  }
  return g_string_free (out, FALSE);
}

/* Where the part of a URL a person would actually type begins: past the
 * scheme, past a leading "www.". */
static const char *
typed_start (const char *url)
{
  const char *p = url;
  if (g_str_has_prefix (p, "https://"))
    p += 8;
  else if (g_str_has_prefix (p, "http://"))
    p += 7;
  if (g_str_has_prefix (p, "www."))
    p += 4;
  return p;
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

/* Add a counter column to an existing table if it is not there yet. */
static void
ensure_column (LyStore *store, const char *table, const char *column)
{
  g_autofree char *pragma = g_strdup_printf ("PRAGMA table_info(%s);", table);
  sqlite3_stmt *stmt = prepare (store, pragma);
  if (stmt == NULL)
    return;

  gboolean found = FALSE;
  while (!found && sqlite3_step (stmt) == SQLITE_ROW)
    found = g_strcmp0 (column_text (stmt, 1), column) == 0;
  sqlite3_finalize (stmt);

  if (!found) {
    g_autofree char *sql = g_strdup_printf (
      "ALTER TABLE %s ADD COLUMN %s INTEGER DEFAULT 0;", table, column);
    exec (store, sql);
  }
}

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
    "  typed INTEGER DEFAULT 0, dwell INTEGER DEFAULT 0,"
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

  /* Profiles written before the address bar learned to rank on these. ADD
   * COLUMN only rewrites the schema, so this costs nothing on a large
   * history; asking first keeps it from warning once per launch forever. */
  ensure_column (store, "history", "typed");
  ensure_column (store, "history", "dwell");

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

/* ------------------------------------------------------------ ranking
 *
 * What the address bar is really asking is "which page did you mean", and
 * three things answer it, in this order.
 *
 *   typed   You got here by typing the address. Nothing else says "I navigate
 *           here by name" as directly, and it is the one signal that survives
 *           a site you otherwise only ever reach by clicking.
 *   visits  How often, on a log scale: the hundredth visit says far less
 *           about a page than the second one did.
 *   dwell   Whether you read the page or bounced straight back off it.
 *
 * Dwell is the one that has to be handled carefully, and it is why this is
 * not simply "sort by time spent". Left as a total, an eighteen-hour video
 * outranks the site's own front page — which is exactly backwards. The video
 * is one page that happened to stay open; the front page is where you
 * actually go. Discord and every other app-shaped site have the same shape:
 * one long-lived URL deep inside, against a short one you type.
 *
 * So dwell is capped per visit as it is banked (ly_store_add_dwell) and read
 * back here as an average rather than a sum. That turns it into a quality
 * flag with a ceiling — it can lift a page from "bounced off" to "read" and
 * no further — instead of a quantity a single long session wins outright.
 * The eighteen-hour video ends up with exactly the same engagement as a page
 * you read for a minute, and then loses on the signals that should decide it:
 * it was visited once and never typed.
 */

#define DWELL_CAP_SECONDS  90     /* per visit, applied when banking        */
#define DWELL_ENGAGED      40.0   /* average dwell that counts as "read"    */
#define TYPED_WEIGHT       3.0    /* a typed visit against an ordinary one  */
#define RECENCY_HALF_LIFE  21.0   /* days for the recency term to halve     */
#define CANDIDATE_LIMIT    400    /* rows scored per query                  */

void
ly_store_record_visit (LyStore *store, const char *url, const char *title,
                       gboolean typed)
{
  if (store->db == NULL || !store->recording || !url_is_recordable (url))
    return;

  sqlite3_stmt *stmt = prepare (store,
    "INSERT INTO history(url, title, visits, typed, dwell, last_visit) "
    "VALUES(?1, ?2, 1, ?4, 0, ?3) "
    "ON CONFLICT(url) DO UPDATE SET "
    "  visits = visits + 1, typed = typed + excluded.typed, last_visit = ?3,"
    /* Keep the old title if this visit has not produced one yet. */
    "  title = CASE WHEN ?2 <> '' THEN ?2 ELSE title END;");
  if (stmt == NULL)
    return;

  sqlite3_bind_text  (stmt, 1, url, -1, SQLITE_TRANSIENT);
  sqlite3_bind_text  (stmt, 2, title ?: "", -1, SQLITE_TRANSIENT);
  sqlite3_bind_int64 (stmt, 3, g_get_real_time () / G_USEC_PER_SEC);
  sqlite3_bind_int   (stmt, 4, typed ? 1 : 0);
  sqlite3_step (stmt);
  sqlite3_finalize (stmt);
}

void
ly_store_add_dwell (LyStore *store, const char *url, int seconds)
{
  if (store->db == NULL || !store->recording || !url_is_recordable (url) ||
      seconds <= 0)
    return;

  /* Capped once, here, on the way in. What is stored is therefore a total of
   * *useful* attention, and nothing downstream has to keep remembering that
   * a tab left open overnight is not a night's worth of interest. */
  if (seconds > DWELL_CAP_SECONDS)
    seconds = DWELL_CAP_SECONDS;

  /* Deliberately an UPDATE and not an upsert: a URL with no history row is
   * one we chose not to record — a private tab, or a fragment some single
   * page app pushed into the address bar — and it should not gain one by
   * having been looked at. */
  sqlite3_stmt *stmt = prepare (store,
    "UPDATE history SET dwell = dwell + ?2 WHERE url = ?1;");
  if (stmt == NULL)
    return;

  sqlite3_bind_text (stmt, 1, url, -1, SQLITE_TRANSIENT);
  sqlite3_bind_int  (stmt, 2, seconds);
  sqlite3_step (stmt);
  sqlite3_finalize (stmt);
}

void
ly_store_import_visit (LyStore *store, const char *url, const char *title,
                       int visits, int typed, gint64 when)
{
  if (store->db == NULL || !url_is_recordable (url))
    return;

  gint64 now = g_get_real_time () / G_USEC_PER_SEC;
  /* Guard against nonsense timestamps from a foreign profile. */
  if (when <= 0 || when > now)
    when = now;
  if (visits < 1)
    visits = 1;
  if (typed < 0)
    typed = 0;
  /* Chromium counts a typed visit inside visit_count; Firefox does not always
   * agree with itself either. A typed count above the visit count would give
   * an imported page a rank no page of our own could reach. */
  if (typed > visits)
    typed = visits;

  sqlite3_stmt *stmt = prepare (store,
    "INSERT INTO history(url, title, visits, typed, dwell, last_visit) "
    "VALUES(?1, ?2, ?3, ?5, 0, ?4) "
    "ON CONFLICT(url) DO UPDATE SET "
    "  visits = MAX(visits, ?3),"
    "  typed = MAX(typed, ?5),"
    "  last_visit = MAX(last_visit, ?4),"
    "  title = CASE WHEN ?2 <> '' THEN ?2 ELSE title END;");
  if (stmt == NULL)
    return;

  sqlite3_bind_text  (stmt, 1, url, -1, SQLITE_TRANSIENT);
  sqlite3_bind_text  (stmt, 2, title ?: "", -1, SQLITE_TRANSIENT);
  sqlite3_bind_int   (stmt, 3, visits);
  sqlite3_bind_int64 (stmt, 4, when);
  sqlite3_bind_int   (stmt, 5, typed);
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

/* The seven columns every history query below selects, in order, so that one
 * reader can serve all of them. */
#define HISTORY_COLUMNS \
  "h.url, h.title, h.visits, h.typed, h.dwell, h.last_visit, " \
  "EXISTS(SELECT 1 FROM bookmarks b WHERE b.url = h.url) AS marked"

static GPtrArray *
rows_from (sqlite3_stmt *stmt)
{
  GPtrArray *rows = g_ptr_array_new_with_free_func ((GDestroyNotify) ly_store_row_free);

  while (sqlite3_step (stmt) == SQLITE_ROW) {
    LyStoreRow *row = g_new0 (LyStoreRow, 1);
    row->url    = g_strdup (column_text (stmt, 0));
    row->title  = g_strdup (column_text (stmt, 1));
    row->visits = sqlite3_column_int (stmt, 2);
    row->typed  = sqlite3_column_int (stmt, 3);
    row->dwell  = sqlite3_column_int64 (stmt, 4);
    row->when   = sqlite3_column_int64 (stmt, 5);
    row->bookmarked = sqlite3_column_int (stmt, 6) != 0;
    g_ptr_array_add (rows, row);
  }
  sqlite3_finalize (stmt);
  return rows;
}

/* How well the typing lines up with the URL. A person typing into the address
 * bar is aiming at the start of a name, not at the middle of a query string. */
typedef enum {
  MATCH_ANYWHERE = 0,   /* somewhere in the URL, or only in the title */
  MATCH_BOUNDARY,       /* at the start of some nameable part of the URL */
  MATCH_PREFIX,         /* the URL, as a person would type it, starts here */
} MatchKind;

static const double MATCH_WEIGHT[] = {
  [MATCH_ANYWHERE] = 1.0,
  [MATCH_BOUNDARY] = 1.8,
  [MATCH_PREFIX]   = 4.0,
};

static MatchKind
match_kind (const char *url, const char *text)
{
  size_t len = strlen (text);

  if (g_ascii_strncasecmp (typed_start (url), text, len) == 0 ||
      g_ascii_strncasecmp (url, text, len) == 0)
    return MATCH_PREFIX;

  /* Half-remembered typing aims at the start of a host label or a path
   * segment — "wiki" for en.wikipedia.org, "issues" for a forge's path. */
  for (const char *p = url; *p != '\0'; p++) {
    if (strchr ("./-_?&=#:", *p) != NULL &&
        g_ascii_strncasecmp (p + 1, text, len) == 0)
      return MATCH_BOUNDARY;
  }

  return MATCH_ANYWHERE;
}

/* A site's front page is what short typing nearly always means. */
static gboolean
url_is_root (const char *url)
{
  const char *slash = strchr (typed_start (url), '/');
  return slash == NULL || slash[1] == '\0';
}

static double
score_row (const LyStoreRow *row, gint64 now, MatchKind kind)
{
  double visits = MAX (row->visits, 1);
  double weight = log2 (1.0 + visits) + TYPED_WEIGHT * log2 (1.0 + row->typed);

  /* Engagement is a nudge and never a verdict: a tenth either way. A page
   * with no dwell recorded at all — everything from before this was measured,
   * and everything imported from another browser — is no opinion rather than
   * a bad one, so it sits at the neutral middle instead of being read as a
   * page somebody bounced off. */
  double engagement = 1.0;
  if (row->dwell > 0)
    engagement = 0.9 + 0.2 * MIN ((double) row->dwell / visits / DWELL_ENGAGED, 1.0);

  /* Halves every three weeks, onto a floor: an old favourite fades behind
   * what you use now without ever dropping out of reach. */
  double days = MAX (now - row->when, 0) / 86400.0;
  double recency = 0.2 + 0.8 * pow (0.5, days / RECENCY_HALF_LIFE);

  double score = weight * engagement * recency * MATCH_WEIGHT[kind];

  if (url_is_root (row->url))
    score *= 1.3;
  /* Bookmarking is the most deliberate thing anyone does to a page, but it is
   * a thumb on the scale rather than a separate shelf above everything else:
   * a bookmark saved once and never opened should not outrank the address you
   * type every morning. */
  if (row->bookmarked)
    score *= 2.0;

  return score;
}

static int
by_score (gconstpointer a, gconstpointer b)
{
  const LyStoreRow *first  = *(LyStoreRow *const *) a;
  const LyStoreRow *second = *(LyStoreRow *const *) b;

  if (first->score < second->score)
    return 1;
  if (first->score > second->score)
    return -1;
  /* Stable enough to stop the list reshuffling under the cursor. */
  return g_strcmp0 (first->url, second->url);
}

/* Score every row against `text`, best first, and keep the top `limit`. */
static void
rank_rows (GPtrArray *rows, const char *text, guint limit)
{
  gint64 now = g_get_real_time () / G_USEC_PER_SEC;

  for (guint i = 0; i < rows->len; i++) {
    LyStoreRow *row = g_ptr_array_index (rows, i);
    row->score = score_row (row, now, match_kind (row->url, text));
  }

  g_ptr_array_sort (rows, by_score);
  if (rows->len > limit)
    g_ptr_array_set_size (rows, (int) limit);
}

GPtrArray *
ly_store_complete (LyStore *store, const char *text, guint limit)
{
  if (store->db == NULL || text == NULL || *text == '\0')
    return g_ptr_array_new_with_free_func ((GDestroyNotify) ly_store_row_free);

  /* Match anywhere in the URL or the title. LIKE with a leading wildcard
   * cannot use the index, but the table is small enough that it does not
   * matter, and matching mid-URL is what makes completion feel useful. */
  g_autofree char *escaped = like_escape (text);
  g_autofree char *pattern = g_strdup_printf ("%%%s%%", escaped);

  /* The SQL only filters, and its ORDER BY is a coarse pre-rank whose whole
   * job is to decide what survives CANDIDATE_LIMIT on a very large history.
   * The real ranking happens in rank_rows, where the arithmetic lives. */
  sqlite3_stmt *stmt = prepare (store,
    "SELECT " HISTORY_COLUMNS " FROM history h "
    "WHERE h.url LIKE ?1 ESCAPE '\\' OR h.title LIKE ?1 ESCAPE '\\' "
    "ORDER BY h.typed * 4 + h.visits DESC, h.last_visit DESC "
    "LIMIT ?2;");
  if (stmt == NULL)
    return g_ptr_array_new_with_free_func ((GDestroyNotify) ly_store_row_free);

  sqlite3_bind_text (stmt, 1, pattern, -1, SQLITE_TRANSIENT);
  sqlite3_bind_int  (stmt, 2, CANDIDATE_LIMIT);

  GPtrArray *rows = rows_from (stmt);
  rank_rows (rows, text, limit);
  return rows;
}

/* The address bar's own form of a URL: what is left once the parts nobody
 * types are gone — no scheme, no "www.", no bare trailing slash. */
static char *
typed_form (const char *url)
{
  char *out = g_strdup (typed_start (url));
  size_t len = strlen (out);

  /* "example.com/" is just "example.com" to anyone typing it. */
  if (len > 1 && out[len - 1] == '/' && strchr (out, '/') == out + len - 1)
    out[len - 1] = '\0';

  return out;
}

char *
ly_store_autocomplete (LyStore *store, const char *text)
{
  if (store->db == NULL || text == NULL || *text == '\0')
    return NULL;

  g_autofree char *escaped = like_escape (text);
  /* Anchored: the five places a typed address can begin. Unlike the popover's
   * query this can never match mid-URL, because finishing someone's sentence
   * from the middle of a query string is not help. */
  g_autofree char *bare  = g_strdup_printf ("%s%%", escaped);
  g_autofree char *https = g_strdup_printf ("https://%s%%", escaped);
  g_autofree char *wwws  = g_strdup_printf ("https://www.%s%%", escaped);
  g_autofree char *http  = g_strdup_printf ("http://%s%%", escaped);
  g_autofree char *wwwp  = g_strdup_printf ("http://www.%s%%", escaped);

  sqlite3_stmt *stmt = prepare (store,
    "SELECT " HISTORY_COLUMNS " FROM history h "
    "WHERE h.url LIKE ?1 ESCAPE '\\' OR h.url LIKE ?2 ESCAPE '\\' "
    "   OR h.url LIKE ?3 ESCAPE '\\' OR h.url LIKE ?4 ESCAPE '\\' "
    "   OR h.url LIKE ?5 ESCAPE '\\' "
    "ORDER BY h.typed * 4 + h.visits DESC, h.last_visit DESC "
    "LIMIT ?6;");
  if (stmt == NULL)
    return NULL;

  sqlite3_bind_text (stmt, 1, bare,  -1, SQLITE_TRANSIENT);
  sqlite3_bind_text (stmt, 2, https, -1, SQLITE_TRANSIENT);
  sqlite3_bind_text (stmt, 3, wwws,  -1, SQLITE_TRANSIENT);
  sqlite3_bind_text (stmt, 4, http,  -1, SQLITE_TRANSIENT);
  sqlite3_bind_text (stmt, 5, wwwp,  -1, SQLITE_TRANSIENT);
  sqlite3_bind_int  (stmt, 6, CANDIDATE_LIMIT);

  g_autoptr (GPtrArray) rows = rows_from (stmt);
  rank_rows (rows, text, CANDIDATE_LIMIT);

  size_t len = strlen (text);

  /* Somebody who has typed a whole address they already know is finished
   * typing. Extending it into a page deeper inside the same site would mean
   * Enter went somewhere they never asked for — the one thing this must
   * never do — so this looks at every candidate before any of them is let
   * near the end of the line. */
  for (guint i = 0; i < rows->len; i++) {
    const LyStoreRow *row = g_ptr_array_index (rows, i);
    g_autofree char *form = typed_form (row->url);

    if (g_ascii_strcasecmp (form, text) == 0 ||
        g_ascii_strcasecmp (row->url, text) == 0)
      return NULL;
  }

  for (guint i = 0; i < rows->len; i++) {
    const LyStoreRow *row = g_ptr_array_index (rows, i);

    /* Rewriting what somebody is in the middle of typing is only welcome when
     * we are sure. A page seen once, never typed and never bookmarked is a
     * page they might have landed on by accident, and quietly sending them
     * back to it is worse than offering nothing. */
    if (row->typed < 1 && row->visits < 2 && !row->bookmarked)
      continue;

    /* Someone who typed the scheme meant the scheme. */
    if (g_ascii_strncasecmp (row->url, text, len) == 0) {
      if (strlen (row->url) > len)
        return g_strdup (row->url);
      continue;
    }

    g_autofree char *form = typed_form (row->url);
    if (g_ascii_strncasecmp (form, text, len) == 0 && strlen (form) > len)
      return g_steal_pointer (&form);
  }
  return NULL;
}

GPtrArray *
ly_store_recent (LyStore *store, guint limit)
{
  if (store->db == NULL)
    return g_ptr_array_new_with_free_func ((GDestroyNotify) ly_store_row_free);

  sqlite3_stmt *stmt = prepare (store,
    "SELECT " HISTORY_COLUMNS " FROM history h "
    "ORDER BY h.last_visit DESC LIMIT ?1;");
  if (stmt == NULL)
    return g_ptr_array_new_with_free_func ((GDestroyNotify) ly_store_row_free);

  sqlite3_bind_int (stmt, 1, (int) limit);
  return rows_from (stmt);
}

/* Grouping is done here rather than in SQL: the registrable domain of a URL
 * is ly_uri_base_domain's answer, and teaching SQLite the public suffix list
 * is not a trade worth making for a query that runs a few times an hour. */
GPtrArray *
ly_store_recent_hosts (LyStore *store, int days, int min_visits, guint limit)
{
  GPtrArray *out = g_ptr_array_new_with_free_func (g_free);
  if (store->db == NULL || limit == 0)
    return out;

  sqlite3_stmt *stmt = prepare (store,
    "SELECT url, visits FROM history "
    "WHERE last_visit >= ?1 ORDER BY visits DESC, last_visit DESC LIMIT 4000;");
  if (stmt == NULL)
    return out;

  gint64 cutoff = g_get_real_time () / G_USEC_PER_SEC - (gint64) days * 24 * 60 * 60;
  sqlite3_bind_int64 (stmt, 1, cutoff);

  /* site → visits so far; the first URL seen for a site is its busiest, which
   * is the one the ORDER BY above already put first. */
  g_autoptr (GHashTable) totals =
    g_hash_table_new_full (g_str_hash, g_str_equal, g_free, NULL);
  g_autoptr (GPtrArray) order = g_ptr_array_new_with_free_func (g_free);
  g_autoptr (GHashTable) best =
    g_hash_table_new_full (g_str_hash, g_str_equal, g_free, g_free);

  while (sqlite3_step (stmt) == SQLITE_ROW) {
    const char *url = column_text (stmt, 0);
    g_autofree char *site = ly_uri_base_domain (url);
    if (site == NULL)
      continue;

    gpointer seen = NULL;
    gboolean known = g_hash_table_lookup_extended (totals, site, NULL, &seen);
    if (!known) {
      g_ptr_array_add (order, g_strdup (site));
      g_hash_table_insert (best, g_strdup (site), g_strdup (url));
    }
    g_hash_table_insert (totals, g_strdup (site),
                         GINT_TO_POINTER (GPOINTER_TO_INT (seen) +
                                          sqlite3_column_int (stmt, 1)));
  }
  sqlite3_finalize (stmt);

  for (guint i = 0; i < order->len && out->len < limit; i++) {
    const char *site = g_ptr_array_index (order, i);
    if (GPOINTER_TO_INT (g_hash_table_lookup (totals, site)) < min_visits)
      continue;
    g_ptr_array_add (out, g_strdup (g_hash_table_lookup (best, site)));
  }
  return out;
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

gboolean
ly_store_rename_bookmark (LyStore *store, const char *url, const char *title)
{
  if (store->db == NULL || url == NULL || *url == '\0' || title == NULL)
    return FALSE;

  g_autofree char *trimmed = g_strdup (title);
  g_strstrip (trimmed);
  if (*trimmed == '\0')
    return FALSE;

  sqlite3_stmt *stmt = prepare (store,
    "UPDATE bookmarks SET title = ?2 WHERE url = ?1;");
  if (stmt == NULL)
    return FALSE;
  sqlite3_bind_text (stmt, 1, url, -1, SQLITE_TRANSIENT);
  sqlite3_bind_text (stmt, 2, trimmed, -1, SQLITE_TRANSIENT);
  sqlite3_step (stmt);
  sqlite3_finalize (stmt);

  return sqlite3_changes (store->db) > 0;
}

char *
ly_store_bookmark_title (LyStore *store, const char *url)
{
  if (store->db == NULL || url == NULL || *url == '\0')
    return NULL;

  sqlite3_stmt *stmt = prepare (store, "SELECT title FROM bookmarks WHERE url = ?1;");
  if (stmt == NULL)
    return NULL;
  sqlite3_bind_text (stmt, 1, url, -1, SQLITE_TRANSIENT);

  char *title = NULL;
  if (sqlite3_step (stmt) == SQLITE_ROW) {
    const unsigned char *text = sqlite3_column_text (stmt, 0);
    title = g_strdup (text != NULL ? (const char *) text : "");
  }
  sqlite3_finalize (stmt);
  return title;
}

GPtrArray *
ly_store_bookmarks (LyStore *store, guint limit)
{
  if (store->db == NULL)
    return g_ptr_array_new_with_free_func ((GDestroyNotify) ly_store_row_free);

  sqlite3_stmt *stmt = prepare (store,
    "SELECT url, title, 0, 0, 0, added, 1 FROM bookmarks "
    "ORDER BY added DESC LIMIT ?1;");
  if (stmt == NULL)
    return g_ptr_array_new_with_free_func ((GDestroyNotify) ly_store_row_free);

  sqlite3_bind_int (stmt, 1, (int) limit);
  return rows_from (stmt);
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
