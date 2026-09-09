/* import.c — see import.h. */

#include "import.h"

#include <glib/gstdio.h>
#include <json-glib/json-glib.h>
#include <sqlite3.h>
#include <string.h>

/* Reading another browser's saved logins means unwrapping them with whatever
 * the platform keeps its secrets in: the Secret Service and OpenSSL here,
 * DPAPI and CNG on Windows. Only these includes and the two implementations
 * of ChromiumKeys below know the difference. */
#ifdef _WIN32
#include <windows.h>
#include <wincrypt.h>
#include <bcrypt.h>
#else
#include <libsecret/secret.h>
#include <openssl/evp.h>
#endif

/* Firefox is the one import that has to hand the work to the library that
 * owns the format, and NSS is optional on both platforms. */
#ifdef LYNDON_HAVE_NSS
#include <nss.h>
#include <pk11pub.h>
#include <pk11sdr.h>
#include <secitem.h>
#endif

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
add_chromium_root (GPtrArray *sources, const char *root, const char *label,
                   const char *const *secret_apps)
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
    g_autofree char *logins    = g_build_filename (profile, "Login Data", NULL);

    gboolean has_bookmarks = g_file_test (bookmarks, G_FILE_TEST_EXISTS);
    gboolean has_history   = g_file_test (history, G_FILE_TEST_EXISTS);
    gboolean has_passwords = g_file_test (logins, G_FILE_TEST_EXISTS);
    if (!has_bookmarks && !has_history && !has_passwords)
      continue;

    LyImportSource *source = g_new0 (LyImportSource, 1);
    source->kind          = LY_IMPORT_CHROMIUM;
    source->profile       = g_steal_pointer (&profile);
    source->has_bookmarks = has_bookmarks;
    source->has_history   = has_history;
    source->has_passwords = has_passwords;
    source->secret_apps   = secret_apps;
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
    g_autofree char *logins  = g_build_filename (profile, "logins.json", NULL);
    if (!g_file_test (places, G_FILE_TEST_EXISTS))
      continue;

    LyImportSource *source = g_new0 (LyImportSource, 1);
    source->kind          = LY_IMPORT_FIREFOX;
    source->profile       = g_steal_pointer (&profile);
    source->has_bookmarks = TRUE;
    source->has_history   = TRUE;
    source->has_passwords = g_file_test (logins, G_FILE_TEST_EXISTS);

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
  /* Chromium on Windows keeps its key in the profile root's Local State file
   * rather than in any keyring, so there is no application name to look up
   * and the last argument stays NULL throughout. */
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
      add_chromium_root (sources, root, chromium[i].label, NULL);
    }
  }
  if (roaming) {
    g_autofree char *ff = g_build_filename (roaming, "Mozilla", "Firefox", "Profiles", NULL);
    add_firefox_root (sources, ff);
  }
#else
  /* Each list is tried in order against the keyring; the first hit wins. The
   * spellings are what the browsers themselves register, and the trailing
   * "chromium" catches forks that never changed the default. */
  static const char *const CHROME[]   = { "chrome", "Chrome", NULL };
  static const char *const CHROMIUM[] = { "chromium", "Chromium", NULL };
  static const char *const BRAVE[]    = { "brave", "Brave", "chromium", NULL };
  static const char *const EDGE[]     = { "microsoft-edge", "Microsoft Edge",
                                          "edge", "chromium", NULL };
  static const char *const VIVALDI[]  = { "vivaldi", "Vivaldi", "chromium", NULL };
  static const char *const OPERA[]    = { "opera", "Opera", "chromium", NULL };

  const char *config = g_get_user_config_dir ();
  static const struct {
    const char *relative; const char *label; const char *const *secret_apps;
  } chromium[] = {
    { "google-chrome",               "Google Chrome",  CHROME   },
    { "chromium",                    "Chromium",       CHROMIUM },
    { "BraveSoftware/Brave-Browser", "Brave",          BRAVE    },
    { "microsoft-edge",              "Microsoft Edge", EDGE     },
    { "vivaldi",                     "Vivaldi",        VIVALDI  },
    { "opera",                       "Opera",          OPERA    },
  };
  for (gsize i = 0; i < G_N_ELEMENTS (chromium); i++) {
    g_autofree char *root = g_build_filename (config, chromium[i].relative, NULL);
    add_chromium_root (sources, root, chromium[i].label, chromium[i].secret_apps);
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

/* --------------------------------------------------- chromium passwords */

/* Chromium encrypts every stored password, and keeps the key wherever the
 * platform keeps secrets. Only two things differ between the two builds: how
 * that key is found, and which cipher wraps the value. The walk over the
 * profile's SQLite table further down is written once and is the same on
 * both — which is the point of splitting it this way rather than writing the
 * whole import twice.
 *
 * None of the parameters below are a choice. They are what Chromium's own
 * os_crypt does, and matching it exactly is the only way to read a profile's
 * logins without asking Chromium to do it. */

typedef struct _ChromiumKeys ChromiumKeys;

static ChromiumKeys *chromium_keys_open   (const LyImportSource *source);
static void          chromium_keys_free   (ChromiumKeys *keys);
/* TRUE when nothing was found to decrypt with, which is the difference
 * between "this profile has no logins" and "these logins are unreadable". */
static gboolean      chromium_keys_locked (const ChromiumKeys *keys);
static char         *chromium_decrypt     (const ChromiumKeys *keys,
                                           const guchar *blob, gsize length);

#ifdef _WIN32

/* Windows: a random AES-256 key lives in the profile root's Local State file,
 * wrapped with DPAPI so that only this Windows account can unwrap it. Values
 * are then a "v10" tag, a 12-byte nonce, the ciphertext and a 16-byte GCM
 * tag. Chrome 79 and earlier wrapped each value with DPAPI on its own. */

#define CHROMIUM_KEY_BYTES  32
#define CHROMIUM_GCM_NONCE  12
#define CHROMIUM_GCM_TAG    16

static const char CHROMIUM_LOCKED_MESSAGE[] =
  "This browser's passwords are locked: the key it encrypts them with "
  "belongs to the Windows account that saved them, and could not be "
  "unwrapped here. Export them from the browser as a CSV instead.";

struct _ChromiumKeys {
  guchar   key[CHROMIUM_KEY_BYTES];
  gboolean have_key;
};

/* BCRYPT_SUCCESS is spelled differently across SDK versions; the test itself
 * is part of the NTSTATUS contract and does not change. */
static inline gboolean
nt_ok (NTSTATUS status)
{
  return status >= 0;
}

static guchar *
dpapi_unprotect (const guchar *in, gsize in_length, gsize *out_length)
{
  DATA_BLOB input  = { (DWORD) in_length, (BYTE *) in };
  DATA_BLOB output = { 0, NULL };

  if (!CryptUnprotectData (&input, NULL, NULL, NULL, NULL, 0, &output))
    return NULL;

  guchar *bytes = g_malloc (output.cbData);
  memcpy (bytes, output.pbData, output.cbData);
  *out_length = output.cbData;

  SecureZeroMemory (output.pbData, output.cbData);
  LocalFree (output.pbData);
  return bytes;
}

static char *
aes_gcm_decrypt (const guchar *key, const guchar *nonce,
                 const guchar *cipher, gsize cipher_length, const guchar *tag)
{
  BCRYPT_ALG_HANDLE algorithm = NULL;
  BCRYPT_KEY_HANDLE handle = NULL;
  char *text = NULL;

  if (!nt_ok (BCryptOpenAlgorithmProvider (&algorithm, BCRYPT_AES_ALGORITHM, NULL, 0)))
    return NULL;

  if (!nt_ok (BCryptSetProperty (algorithm, BCRYPT_CHAINING_MODE,
                                 (PUCHAR) BCRYPT_CHAIN_MODE_GCM,
                                 sizeof BCRYPT_CHAIN_MODE_GCM, 0)) ||
      !nt_ok (BCryptGenerateSymmetricKey (algorithm, &handle, NULL, 0,
                                          (PUCHAR) key, CHROMIUM_KEY_BYTES, 0)))
    goto out;

  BCRYPT_AUTHENTICATED_CIPHER_MODE_INFO info;
  BCRYPT_INIT_AUTH_MODE_INFO (info);
  info.pbNonce = (PUCHAR) nonce;
  info.cbNonce = CHROMIUM_GCM_NONCE;
  info.pbTag   = (PUCHAR) tag;
  info.cbTag   = CHROMIUM_GCM_TAG;

  guchar *plain = g_malloc0 (cipher_length + 1);
  ULONG written = 0;

  /* The tag is checked here, which is also the only signal that the key was
   * the right one: a wrong key fails this step rather than returning junk. */
  if (nt_ok (BCryptDecrypt (handle, (PUCHAR) cipher, (ULONG) cipher_length,
                            &info, NULL, 0, plain, (ULONG) cipher_length,
                            &written, 0)) &&
      g_utf8_validate ((const char *) plain, written, NULL)) {
    plain[written] = '\0';
    text = (char *) plain;
  } else {
    memset (plain, 0, cipher_length + 1);
    g_free (plain);
  }

out:
  if (handle != NULL)
    BCryptDestroyKey (handle);
  BCryptCloseAlgorithmProvider (algorithm, 0);
  return text;
}

static ChromiumKeys *
chromium_keys_open (const LyImportSource *source)
{
  ChromiumKeys *keys = g_new0 (ChromiumKeys, 1);

  /* Local State sits beside the profile directories, not inside one. */
  g_autofree char *root = g_path_get_dirname (source->profile);
  g_autofree char *path = g_build_filename (root, "Local State", NULL);

  g_autoptr (JsonParser) parser = json_parser_new ();
  if (!json_parser_load_from_file (parser, path, NULL))
    return keys;

  JsonNode *node = json_parser_get_root (parser);
  if (node == NULL || !JSON_NODE_HOLDS_OBJECT (node))
    return keys;

  JsonObject *object = json_node_get_object (node);
  if (!json_object_has_member (object, "os_crypt"))
    return keys;

  JsonObject *os_crypt = json_object_get_object_member (object, "os_crypt");
  const char *encoded = os_crypt != NULL
    ? json_object_get_string_member_with_default (os_crypt, "encrypted_key", NULL)
    : NULL;
  if (encoded == NULL)
    return keys;

  gsize wrapped_length = 0;
  g_autofree guchar *wrapped = g_base64_decode (encoded, &wrapped_length);
  /* A "DPAPI" prefix marks the bytes that follow as CryptUnprotectData's. */
  if (wrapped == NULL || wrapped_length <= 5 || memcmp (wrapped, "DPAPI", 5) != 0)
    return keys;

  gsize plain_length = 0;
  g_autofree guchar *plain =
    dpapi_unprotect (wrapped + 5, wrapped_length - 5, &plain_length);
  if (plain == NULL)
    return keys;

  if (plain_length == CHROMIUM_KEY_BYTES) {
    memcpy (keys->key, plain, CHROMIUM_KEY_BYTES);
    keys->have_key = TRUE;
  }
  memset (plain, 0, plain_length);
  return keys;
}

static char *
chromium_decrypt (const ChromiumKeys *keys, const guchar *blob, gsize length)
{
  if (blob == NULL || length == 0)
    return NULL;

  if (length > 3 && (memcmp (blob, "v10", 3) == 0 || memcmp (blob, "v11", 3) == 0)) {
    gsize overhead = 3 + CHROMIUM_GCM_NONCE + CHROMIUM_GCM_TAG;
    if (!keys->have_key || length <= overhead)
      return NULL;

    const guchar *nonce  = blob + 3;
    const guchar *cipher = nonce + CHROMIUM_GCM_NONCE;
    gsize cipher_length  = length - overhead;
    return aes_gcm_decrypt (keys->key, nonce, cipher, cipher_length,
                            cipher + cipher_length);
  }

  /* "v20" is Chrome 127's app-bound wrapping: the key is held by an elevated
   * Windows service that hands it back only to Chrome itself, so those rows
   * genuinely cannot be read from here. They are counted as skipped. */
  if (length > 3 && memcmp (blob, "v20", 3) == 0)
    return NULL;

  gsize plain_length = 0;
  g_autofree guchar *plain = dpapi_unprotect (blob, length, &plain_length);
  if (plain == NULL)
    return NULL;

  char *text = plain_length > 0 &&
               g_utf8_validate ((const char *) plain, plain_length, NULL)
    ? g_strndup ((const char *) plain, plain_length)
    : NULL;
  memset (plain, 0, plain_length);
  return text;
}

static gboolean
chromium_keys_locked (const ChromiumKeys *keys)
{
  return !keys->have_key;
}

#else  /* !_WIN32 */

/* Linux: AES-128-CBC under a key derived from a passphrase Chromium keeps in
 * the same Secret Service Lyndon uses. Profiles set up without a keyring fall
 * back to a fixed passphrase, which is not a secret — it is written down in
 * Chromium's own source. */

#define CHROMIUM_SALT       "saltysalt"
#define CHROMIUM_ITERATIONS 1
#define CHROMIUM_KEY_BYTES  16

static const char CHROMIUM_LOCKED_MESSAGE[] =
  "This browser's passwords are locked: the key it encrypts them with is not "
  "in your keyring. Start the browser once to unlock it, or export a CSV "
  "from it.";

struct _ChromiumKeys {
  guchar   keyring[CHROMIUM_KEY_BYTES];   /* "v11" — from the Secret Service */
  guchar   fallback[CHROMIUM_KEY_BYTES];  /* "v10" — the fixed passphrase    */
  gboolean have_keyring;
};

static const SecretSchema *
chromium_schema (void)
{
  /* DONT_MATCH_NAME: the schema name changed between Chromium releases, and
   * the application attribute is what actually identifies the item. */
  static const SecretSchema schema = {
    "chrome_libsecret_os_crypt_password_v2", SECRET_SCHEMA_DONT_MATCH_NAME,
    {
      { "application", SECRET_SCHEMA_ATTRIBUTE_STRING },
      { "NULL", 0 },
    },
    0, NULL, NULL, NULL, NULL, NULL, NULL, NULL
  };
  return &schema;
}

static void
derive_chromium_key (const char *passphrase, guchar key[CHROMIUM_KEY_BYTES])
{
  PKCS5_PBKDF2_HMAC_SHA1 (passphrase, (int) strlen (passphrase),
                          (const guchar *) CHROMIUM_SALT, sizeof CHROMIUM_SALT - 1,
                          CHROMIUM_ITERATIONS, CHROMIUM_KEY_BYTES, key);
}

static ChromiumKeys *
chromium_keys_open (const LyImportSource *source)
{
  static const char *const FALLBACK[] = { "chromium", NULL };

  ChromiumKeys *keys = g_new0 (ChromiumKeys, 1);
  derive_chromium_key ("peanuts", keys->fallback);

  const char *const *apps = source->secret_apps ?: FALLBACK;
  for (guint i = 0; apps[i] != NULL; i++) {
    g_autoptr (GError) error = NULL;
    char *passphrase = secret_password_lookup_sync (chromium_schema (), NULL, &error,
                                                    "application", apps[i], NULL);
    if (passphrase == NULL)
      continue;

    derive_chromium_key (passphrase, keys->keyring);
    keys->have_keyring = TRUE;
    secret_password_free (passphrase);
    break;
  }
  return keys;
}

static char *
chromium_decrypt (const ChromiumKeys *keys, const guchar *blob, gsize length)
{
  /* Sixteen spaces. Chromium uses a fixed IV, which is weak, but it is not
   * Lyndon's decision to make when reading someone else's file. */
  static const guchar IV[16] = {
    ' ', ' ', ' ', ' ', ' ', ' ', ' ', ' ',
    ' ', ' ', ' ', ' ', ' ', ' ', ' ', ' ',
  };

  if (blob == NULL || length <= 3)
    return NULL;

  const guchar *key = NULL;
  if (memcmp (blob, "v11", 3) == 0)
    key = keys->have_keyring ? keys->keyring : NULL;
  else if (memcmp (blob, "v10", 3) == 0)
    key = keys->fallback;
  if (key == NULL)
    return NULL;

  const guchar *cipher = blob + 3;           /* past the "v10"/"v11" tag */
  int cipher_length = (int) (length - 3);
  if (cipher_length <= 0 || cipher_length % 16 != 0)
    return NULL;

  EVP_CIPHER_CTX *ctx = EVP_CIPHER_CTX_new ();
  if (ctx == NULL)
    return NULL;

  guchar *plain = g_malloc0 (cipher_length + 1);
  int written = 0, final = 0;
  gboolean ok =
    EVP_DecryptInit_ex (ctx, EVP_aes_128_cbc (), NULL, key, IV) == 1 &&
    EVP_DecryptUpdate (ctx, plain, &written, cipher, cipher_length) == 1 &&
    /* Padding is checked here, which is also the only signal that the key was
     * the right one: a wrong key fails this step rather than returning junk. */
    EVP_DecryptFinal_ex (ctx, plain + written, &final) == 1;

  EVP_CIPHER_CTX_free (ctx);

  if (!ok || !g_utf8_validate ((const char *) plain, written + final, NULL)) {
    memset (plain, 0, cipher_length + 1);
    g_free (plain);
    return NULL;
  }
  plain[written + final] = '\0';
  return (char *) plain;
}

static gboolean
chromium_keys_locked (const ChromiumKeys *keys)
{
  return !keys->have_keyring;
}

#endif /* _WIN32 */

static void
chromium_keys_free (ChromiumKeys *keys)
{
  if (keys == NULL)
    return;
  memset (keys, 0, sizeof *keys);
  g_free (keys);
}

static GPtrArray *
import_chromium_passwords (const LyImportSource *source, guint *skipped, GError **error)
{
  g_autofree char *path = g_build_filename (source->profile, "Login Data", NULL);
  if (!g_file_test (path, G_FILE_TEST_EXISTS))
    return ly_credentials_new ();

  g_autofree char *copy = copy_for_reading (path, error);
  if (copy == NULL)
    return NULL;

  ChromiumKeys *keys = chromium_keys_open (source);

  GPtrArray *credentials = ly_credentials_new ();
  guint dropped = 0;

  sqlite3 *db = open_copy_readonly (copy);
  if (db != NULL) {
    sqlite3_stmt *stmt = NULL;
    const char *sql =
      "SELECT origin_url, username_value, password_value FROM logins "
      "WHERE blacklisted_by_user = 0 AND length(password_value) > 0;";

    if (sqlite3_prepare_v2 (db, sql, -1, &stmt, NULL) == SQLITE_OK) {
      while (sqlite3_step (stmt) == SQLITE_ROW) {
        const unsigned char *url  = sqlite3_column_text (stmt, 0);
        const unsigned char *user = sqlite3_column_text (stmt, 1);
        const guchar *blob        = sqlite3_column_blob (stmt, 2);
        int blob_length           = sqlite3_column_bytes (stmt, 2);

        g_autofree char *origin =
          ly_passwords_normalise_origin (url ? (const char *) url : NULL);
        if (origin == NULL || blob == NULL || blob_length <= 0) {
          dropped++;
          continue;
        }

        g_autofree char *password = chromium_decrypt (keys, blob, (gsize) blob_length);
        /* A value with no version tag at all was never encrypted. */
        if (password == NULL && (blob_length <= 3 || blob[0] != 'v') &&
            g_utf8_validate ((const char *) blob, blob_length, NULL))
          password = g_strndup ((const char *) blob, blob_length);

        if (password == NULL || *password == '\0') {
          dropped++;
          continue;
        }
        g_ptr_array_add (credentials,
                         ly_credential_new (origin, user ? (const char *) user : "",
                                            password));
      }
      sqlite3_finalize (stmt);
    }
    sqlite3_close (db);
  }

  gboolean locked = chromium_keys_locked (keys);
  chromium_keys_free (keys);
  g_unlink (copy);

  if (credentials->len == 0 && dropped > 0 && locked) {
    g_set_error_literal (error, G_IO_ERROR, G_IO_ERROR_PERMISSION_DENIED,
                         CHROMIUM_LOCKED_MESSAGE);
    g_ptr_array_unref (credentials);
    return NULL;
  }

  if (skipped != NULL)
    *skipped = dropped;
  return credentials;
}

/* ---------------------------------------------------- firefox passwords */

/* Firefox keeps its logins in a JSON file and the key that opens them in an
 * NSS database, so this is the one import that has to hand the work to the
 * library that owns the format. */

#ifdef LYNDON_HAVE_NSS

static const char *const NSS_FILES[] = {
  "key4.db", "cert9.db", "pkcs11.txt",          /* current */
  "key3.db", "cert8.db", "secmod.db",           /* pre-58 profiles */
  NULL
};

static void
remove_directory (const char *path)
{
  g_autoptr (GDir) dir = g_dir_open (path, 0, NULL);
  if (dir != NULL) {
    const char *name;
    while ((name = g_dir_read_name (dir)) != NULL) {
      g_autofree char *child = g_build_filename (path, name, NULL);
      g_unlink (child);
    }
  }
  g_rmdir (path);
}

/* NSS wants a directory it can open as a database, and Firefox may be holding
 * one open, so it gets a copy of the handful of files it actually reads. */
static char *
copy_nss_database (const char *profile, gboolean *modern)
{
  g_autofree char *cache = ly_cache_dir ();
  char *dir = g_build_filename (cache, "import-nss", NULL);

  remove_directory (dir);
  if (g_mkdir_with_parents (dir, 0700) != 0) {
    g_free (dir);
    return NULL;
  }

  gboolean any = FALSE;
  for (guint i = 0; NSS_FILES[i] != NULL; i++) {
    g_autofree char *from = g_build_filename (profile, NSS_FILES[i], NULL);
    if (!g_file_test (from, G_FILE_TEST_EXISTS))
      continue;

    g_autofree char *to = g_build_filename (dir, NSS_FILES[i], NULL);
    g_autoptr (GFile) source = g_file_new_for_path (from);
    g_autoptr (GFile) target = g_file_new_for_path (to);

    if (g_file_copy (source, target, G_FILE_COPY_OVERWRITE, NULL, NULL, NULL, NULL)) {
      any = TRUE;
      if (g_strcmp0 (NSS_FILES[i], "key4.db") == 0)
        *modern = TRUE;
    }
  }

  if (!any) {
    remove_directory (dir);
    g_free (dir);
    return NULL;
  }
  return dir;
}

static char *
nss_decrypt (const char *base64)
{
  if (base64 == NULL || *base64 == '\0')
    return NULL;

  gsize length = 0;
  g_autofree guchar *raw = g_base64_decode (base64, &length);
  if (raw == NULL || length == 0)
    return NULL;

  SECItem in  = { siBuffer, raw, (unsigned int) length };
  SECItem out = { siBuffer, NULL, 0 };

  if (PK11SDR_Decrypt (&in, &out, NULL) != SECSuccess)
    return NULL;

  char *text = g_strndup ((const char *) out.data, out.len);
  SECITEM_ZfreeItem (&out, PR_FALSE);
  return text;
}

static GPtrArray *
import_firefox_passwords (const LyImportSource *source, guint *skipped, GError **error)
{
  g_autofree char *json_path = g_build_filename (source->profile, "logins.json", NULL);
  if (!g_file_test (json_path, G_FILE_TEST_EXISTS))
    return ly_credentials_new ();

  gboolean modern = FALSE;
  g_autofree char *dir = copy_nss_database (source->profile, &modern);
  if (dir == NULL) {
    g_set_error_literal (error, G_IO_ERROR, G_IO_ERROR_NOT_FOUND,
                         "This profile has no key database to unlock its logins.");
    return NULL;
  }

  g_autofree char *config = g_strdup_printf ("%s:%s", modern ? "sql" : "dbm", dir);

  NSSInitContext *nss = NSS_InitContext (config, "", "", "secmod.db", NULL,
                                         NSS_INIT_READONLY | NSS_INIT_FORCEOPEN |
                                         NSS_INIT_NOROOTINIT | NSS_INIT_OPTIMIZESPACE);
  if (nss == NULL) {
    remove_directory (dir);
    g_set_error_literal (error, G_IO_ERROR, G_IO_ERROR_FAILED,
                         "The profile's key database could not be opened.");
    return NULL;
  }

  GPtrArray *credentials = NULL;
  PK11SlotInfo *slot = PK11_GetInternalKeySlot ();

  if (slot == NULL) {
    g_set_error_literal (error, G_IO_ERROR, G_IO_ERROR_FAILED,
                         "The profile's key store is unreadable.");
  } else if (PK11_CheckUserPassword (slot, "") != SECSuccess) {
    /* Only an empty primary password can be tried without asking for one, and
     * asking is a whole dialog Lyndon does not have a place for yet. */
    g_set_error_literal (error, G_IO_ERROR, G_IO_ERROR_PERMISSION_DENIED,
                         "These logins are sealed with a Primary Password. "
                         "Export them from Firefox as a CSV instead.");
  } else if (PK11_Authenticate (slot, PR_TRUE, NULL) != SECSuccess) {
    g_set_error_literal (error, G_IO_ERROR, G_IO_ERROR_PERMISSION_DENIED,
                         "The profile's key store refused to unlock.");
  } else {
    g_autoptr (JsonParser) parser = json_parser_new ();
    guint dropped = 0;

    if (json_parser_load_from_file (parser, json_path, error)) {
      credentials = ly_credentials_new ();

      JsonNode *root = json_parser_get_root (parser);
      JsonObject *object = JSON_NODE_HOLDS_OBJECT (root) ? json_node_get_object (root) : NULL;
      JsonArray *logins = object != NULL && json_object_has_member (object, "logins")
        ? json_object_get_array_member (object, "logins") : NULL;

      guint count = logins != NULL ? json_array_get_length (logins) : 0;
      for (guint i = 0; i < count; i++) {
        JsonNode *node = json_array_get_element (logins, i);
        if (!JSON_NODE_HOLDS_OBJECT (node)) continue;
        JsonObject *login = json_node_get_object (node);

        const char *host =
          json_object_get_string_member_with_default (login, "hostname", NULL);
        g_autofree char *origin = ly_passwords_normalise_origin (host);

        g_autofree char *user = nss_decrypt (
          json_object_get_string_member_with_default (login, "encryptedUsername", NULL));
        g_autofree char *password = nss_decrypt (
          json_object_get_string_member_with_default (login, "encryptedPassword", NULL));

        if (origin == NULL || password == NULL || *password == '\0') {
          dropped++;
          continue;
        }
        g_ptr_array_add (credentials, ly_credential_new (origin, user ?: "", password));
      }
    }
    if (skipped != NULL)
      *skipped = dropped;
  }

  if (slot != NULL)
    PK11_FreeSlot (slot);
  NSS_ShutdownContext (nss);
  remove_directory (dir);

  return credentials;
}

#else  /* !LYNDON_HAVE_NSS */

static GPtrArray *
import_firefox_passwords (const LyImportSource *source, guint *skipped, GError **error)
{
  g_set_error_literal (error, G_IO_ERROR, G_IO_ERROR_NOT_SUPPORTED,
                       "This build has no NSS support, and Firefox logins cannot "
                       "be read without it. Export them from Firefox as a CSV "
                       "instead, or rebuild Lyndon with nss installed.");
  return NULL;
}

#endif /* LYNDON_HAVE_NSS */

GPtrArray *
ly_import_passwords (const LyImportSource *source, guint *skipped, GError **error)
{
  g_return_val_if_fail (source != NULL, NULL);

  if (skipped != NULL)
    *skipped = 0;

  return source->kind == LY_IMPORT_CHROMIUM
    ? import_chromium_passwords (source, skipped, error)
    : import_firefox_passwords (source, skipped, error);
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
