/* import.c — see import.h. */

#include "import.h"

#include <glib/gstdio.h>
#include <json-glib/json-glib.h>
#include <sqlite3.h>
#include <string.h>

/* Chromium timestamps count microseconds from 1601-01-01. */
#define CHROMIUM_EPOCH_OFFSET G_GINT64_CONSTANT (11644473600)

void
ly_import_source_free (LyImportSource *source)
{
  if (source == NULL)
    return;
  g_free (source->label);
  g_free (source->profile);
  g_free (source);
}

void
ly_import_result_clear (LyImportResult *result)
{
  if (result != NULL)
    g_clear_pointer (&result->error, g_free);
}

/* ------------------------------------------------------------ discovery */

static void
add_chromium_root (GPtrArray *sources, const char *root, const char *label)
{
  if (root == NULL || !g_file_test (root, G_FILE_TEST_IS_DIR))
    return;

  g_autoptr (GDir) dir = g_dir_open (root, 0, NULL);
  if (dir == NULL)
    return;

  const char *name;
  while ((name = g_dir_read_name (dir)) != NULL) {
    /* Chromium calls them "Default", "Profile 1", "Profile 2"… */
    if (g_strcmp0 (name, "Default") != 0 && !g_str_has_prefix (name, "Profile "))
      continue;

    g_autofree char *profile = g_build_filename (root, name, NULL);
    g_autofree char *bookmarks = g_build_filename (profile, "Bookmarks", NULL);
    g_autofree char *history   = g_build_filename (profile, "History", NULL);

    gboolean has_bookmarks = g_file_test (bookmarks, G_FILE_TEST_EXISTS);
    gboolean has_history   = g_file_test (history, G_FILE_TEST_EXISTS);
    if (!has_bookmarks && !has_history)
      continue;

    LyImportSource *source = g_new0 (LyImportSource, 1);
    source->kind          = LY_IMPORT_CHROMIUM;
    source->profile       = g_steal_pointer (&profile);
    source->has_bookmarks = has_bookmarks;
    source->has_history   = has_history;
    source->label = g_strcmp0 (name, "Default") == 0
      ? g_strdup (label)
      : g_strdup_printf ("%s — %s", label, name);
    g_ptr_array_add (sources, source);
  }
}

static void
add_firefox_root (GPtrArray *sources, const char *root)
{
  if (root == NULL || !g_file_test (root, G_FILE_TEST_IS_DIR))
    return;

  g_autoptr (GDir) dir = g_dir_open (root, 0, NULL);
  if (dir == NULL)
    return;

  const char *name;
  while ((name = g_dir_read_name (dir)) != NULL) {
    g_autofree char *profile = g_build_filename (root, name, NULL);
    g_autofree char *places  = g_build_filename (profile, "places.sqlite", NULL);
    if (!g_file_test (places, G_FILE_TEST_EXISTS))
      continue;

    LyImportSource *source = g_new0 (LyImportSource, 1);
    source->kind          = LY_IMPORT_FIREFOX;
    source->profile       = g_steal_pointer (&profile);
    source->has_bookmarks = TRUE;
    source->has_history   = TRUE;

    /* Profile directories look like "8f3k2l1p.default-release". */
    const char *dot = strchr (name, '.');
    source->label = dot != NULL ? g_strdup_printf ("Firefox — %s", dot + 1)
                                : g_strdup_printf ("Firefox — %s", name);
    g_ptr_array_add (sources, source);
  }
}

/* The same browsers, in the two places the two platforms put them.
 *
 * Chromium keeps <root>/Default and <root>/Profile N either way; what differs
 * is the root. Linux uses the XDG config directory; Windows uses
 * %LOCALAPPDATA% with a vendor/product/User Data tree, and Firefox is under
 * %APPDATA% rather than a dotfile. Everything below the root is identical,
 * which is why only this function is conditional. */
GPtrArray *
ly_import_sources (void)
{
  GPtrArray *sources =
    g_ptr_array_new_with_free_func ((GDestroyNotify) ly_import_source_free);

#ifdef _WIN32
  const char *local = g_getenv ("LOCALAPPDATA");
  const char *roaming = g_getenv ("APPDATA");

  static const struct { const char *relative; const char *label; } chromium[] = {
    { "Google/Chrome/User Data",              "Google Chrome"  },
    { "Google/Chrome Beta/User Data",         "Chrome Beta"    },
    { "Chromium/User Data",                   "Chromium"       },
    { "BraveSoftware/Brave-Browser/User Data", "Brave"         },
    { "Microsoft/Edge/User Data",             "Microsoft Edge" },
    { "Vivaldi/User Data",                    "Vivaldi"        },
    { "Opera Software/Opera Stable",          "Opera"          },
  };
  if (local) {
    for (gsize i = 0; i < G_N_ELEMENTS (chromium); i++) {
      g_autofree char *root = g_build_filename (local, chromium[i].relative, NULL);
      add_chromium_root (sources, root, chromium[i].label);
    }
  }
  if (roaming) {
    g_autofree char *ff = g_build_filename (roaming, "Mozilla", "Firefox", "Profiles", NULL);
    add_firefox_root (sources, ff);
  }
#else
  const char *config = g_get_user_config_dir ();
  static const struct { const char *relative; const char *label; } chromium[] = {
    { "google-chrome",                 "Google Chrome"  },
    { "chromium",                      "Chromium"       },
    { "BraveSoftware/Brave-Browser",   "Brave"          },
    { "microsoft-edge",                "Microsoft Edge" },
    { "vivaldi",                       "Vivaldi"        },
  };
  for (gsize i = 0; i < G_N_ELEMENTS (chromium); i++) {
    g_autofree char *root = g_build_filename (config, chromium[i].relative, NULL);
    add_chromium_root (sources, root, chromium[i].label);
  }
  g_autofree char *ff = g_build_filename (g_get_home_dir (), ".mozilla", "firefox", NULL);
  add_firefox_root (sources, ff);
#endif

  return sources;
}

/* --------------------------------------------------------------- helpers */

/* Both browsers keep an exclusive lock while running, so every read goes
 * through a private copy that is deleted afterwards. */
static char *
copy_for_reading (const char *path, GError **error)
{
  g_autofree char *cache = ly_cache_dir ();
  g_mkdir_with_parents (cache, 0700);

  g_autofree char *base = g_path_get_basename (path);
  g_autofree char *name = g_strdup_printf ("import-%s.tmp", base);
  char *target = g_build_filename (cache, name, NULL);

  g_autoptr (GFile) from = g_file_new_for_path (path);
  g_autoptr (GFile) to   = g_file_new_for_path (target);

  if (!g_file_copy (from, to, G_FILE_COPY_OVERWRITE, NULL, NULL, NULL, error)) {
    g_free (target);
    return NULL;
  }
  return target;
}

/* SQLite may still consider a copied database "hot" if the source had a WAL
 * we did not take. Read-only + immutable avoids trying to recover it. */
static sqlite3 *
open_copy_readonly (const char *path)
{
  g_autofree char *uri = g_strdup_printf ("file:%s?immutable=1", path);
  sqlite3 *db = NULL;
  if (sqlite3_open_v2 (uri, &db, SQLITE_OPEN_READONLY | SQLITE_OPEN_URI, NULL) != SQLITE_OK) {
    sqlite3_close (db);
    return NULL;
  }
  return db;
}

/* ------------------------------------------------------ chromium bookmarks */

static guint
walk_chromium_node (LyStore *store, JsonObject *node)
{
  if (node == NULL)
    return 0;

  const char *type = json_object_get_string_member_with_default (node, "type", "");

  if (g_strcmp0 (type, "url") == 0) {
    const char *url   = json_object_get_string_member_with_default (node, "url", NULL);
    const char *title = json_object_get_string_member_with_default (node, "name", NULL);
    if (url == NULL || *url == '\0')
      return 0;
    ly_store_add_bookmark (store, url, title);
    return 1;
  }

  if (!json_object_has_member (node, "children"))
    return 0;

  JsonArray *children = json_object_get_array_member (node, "children");
  if (children == NULL)
    return 0;

  guint count = 0;
  guint n = json_array_get_length (children);
  for (guint i = 0; i < n; i++) {
    JsonNode *child = json_array_get_element (children, i);
    if (JSON_NODE_HOLDS_OBJECT (child))
      count += walk_chromium_node (store, json_node_get_object (child));
  }
  return count;
}

static guint
import_chromium_bookmarks (LyStore *store, const char *profile, GError **error)
{
  g_autofree char *path = g_build_filename (profile, "Bookmarks", NULL);
  if (!g_file_test (path, G_FILE_TEST_EXISTS))
    return 0;

  g_autoptr (JsonParser) parser = json_parser_new ();
  if (!json_parser_load_from_file (parser, path, error))
    return 0;

  JsonNode *root_node = json_parser_get_root (parser);
  if (root_node == NULL || !JSON_NODE_HOLDS_OBJECT (root_node))
    return 0;

  JsonObject *root = json_node_get_object (root_node);
  if (!json_object_has_member (root, "roots"))
    return 0;

  JsonObject *roots = json_object_get_object_member (root, "roots");
  if (roots == NULL)
    return 0;

  guint count = 0;
  g_autoptr (GList) members = json_object_get_members (roots);
  for (GList *l = members; l != NULL; l = l->next) {
    JsonNode *node = json_object_get_member (roots, l->data);
    if (JSON_NODE_HOLDS_OBJECT (node))
      count += walk_chromium_node (store, json_node_get_object (node));
  }
  return count;
}

/* -------------------------------------------------------------- histories */

/* Both browsers store microseconds; they differ only in the epoch. Carrying
 * the real counts and dates across is what keeps imported history ranking
 * sensibly in the address bar instead of all looking equally fresh. */
static guint
import_rows (LyStore *store, sqlite3 *db, const char *sql, gboolean chromium_epoch)
{
  sqlite3_stmt *stmt = NULL;
  if (sqlite3_prepare_v2 (db, sql, -1, &stmt, NULL) != SQLITE_OK)
    return 0;

  guint count = 0;
  while (sqlite3_step (stmt) == SQLITE_ROW) {
    const unsigned char *url   = sqlite3_column_text (stmt, 0);
    const unsigned char *title = sqlite3_column_text (stmt, 1);
    if (url == NULL || *url == '\0')
      continue;

    int    visits = sqlite3_column_int (stmt, 2);
    gint64 stamp  = sqlite3_column_int64 (stmt, 3);
    gint64 when   = stamp > 0 ? stamp / 1000000 : 0;
    if (chromium_epoch && when > 0)
      when -= CHROMIUM_EPOCH_OFFSET;

    ly_store_import_visit (store, (const char *) url,
                           title ? (const char *) title : "", visits, when);
    count++;
  }
  sqlite3_finalize (stmt);
  return count;
}

static guint
import_chromium_history (LyStore *store, const char *profile, GError **error)
{
  g_autofree char *path = g_build_filename (profile, "History", NULL);
  if (!g_file_test (path, G_FILE_TEST_EXISTS))
    return 0;

  g_autofree char *copy = copy_for_reading (path, error);
  if (copy == NULL)
    return 0;

  guint count = 0;
  sqlite3 *db = open_copy_readonly (copy);
  if (db != NULL) {
    count = import_rows (store, db,
      "SELECT url, title, visit_count, last_visit_time FROM urls "
      "WHERE url LIKE 'http%' "
      "ORDER BY visit_count DESC, last_visit_time DESC LIMIT 5000;", TRUE);
    sqlite3_close (db);
  }
  g_unlink (copy);
  return count;
}

static guint
import_firefox_history (LyStore *store, const char *profile, GError **error)
{
  g_autofree char *path = g_build_filename (profile, "places.sqlite", NULL);
  g_autofree char *copy = copy_for_reading (path, error);
  if (copy == NULL)
    return 0;

  guint count = 0;
  sqlite3 *db = open_copy_readonly (copy);
  if (db != NULL) {
    count = import_rows (store, db,
      "SELECT url, title, visit_count, last_visit_date FROM moz_places "
      "WHERE url LIKE 'http%' AND hidden = 0 "
      "ORDER BY visit_count DESC, last_visit_date DESC LIMIT 5000;", FALSE);
    sqlite3_close (db);
  }
  g_unlink (copy);
  return count;
}

static guint
import_firefox_bookmarks (LyStore *store, const char *profile, GError **error)
{
  g_autofree char *path = g_build_filename (profile, "places.sqlite", NULL);
  g_autofree char *copy = copy_for_reading (path, error);
  if (copy == NULL)
    return 0;

  guint count = 0;
  sqlite3 *db = open_copy_readonly (copy);
  if (db != NULL) {
    sqlite3_stmt *stmt = NULL;
    /* type 1 is a bookmark; folders and separators are types 2 and 3. */
    const char *sql =
      "SELECT p.url, COALESCE(b.title, p.title) FROM moz_bookmarks b "
      "JOIN moz_places p ON p.id = b.fk "
      "WHERE b.type = 1 AND p.url LIKE 'http%';";
    if (sqlite3_prepare_v2 (db, sql, -1, &stmt, NULL) == SQLITE_OK) {
      while (sqlite3_step (stmt) == SQLITE_ROW) {
        const unsigned char *url   = sqlite3_column_text (stmt, 0);
        const unsigned char *title = sqlite3_column_text (stmt, 1);
        if (url == NULL || *url == '\0')
          continue;
        ly_store_add_bookmark (store, (const char *) url,
                               title ? (const char *) title : (const char *) url);
        count++;
      }
      sqlite3_finalize (stmt);
    }
    sqlite3_close (db);
  }
  g_unlink (copy);
  return count;
}

/* ------------------------------------------------------------------- run */

gboolean
ly_import_run (LyStore              *store,
               const LyImportSource *source,
               gboolean              bookmarks,
               gboolean              history,
               LyImportResult       *result)
{
  g_return_val_if_fail (store != NULL && source != NULL && result != NULL, FALSE);

  memset (result, 0, sizeof *result);
  g_autoptr (GError) error = NULL;

  if (bookmarks) {
    result->bookmarks = (source->kind == LY_IMPORT_CHROMIUM)
      ? import_chromium_bookmarks (store, source->profile, &error)
      : import_firefox_bookmarks (store, source->profile, &error);
  }

  if (history && error == NULL) {
    result->history = (source->kind == LY_IMPORT_CHROMIUM)
      ? import_chromium_history (store, source->profile, &error)
      : import_firefox_history (store, source->profile, &error);
  }

  if (error != NULL) {
    result->error = g_strdup (error->message);
    return FALSE;
  }
  return TRUE;
}
