/* import.h — pull bookmarks, history and saved logins out of other browsers.
 *
 * Read-only, and always from a copy: Chromium and Firefox both hold an
 * exclusive lock on their SQLite files while running, and the last thing an
 * import should do is disturb the browser it is reading from.
 */
#pragma once

#include "lyndon.h"
#include "passwords.h"
#include "store.h"

G_BEGIN_DECLS

typedef enum {
  LY_IMPORT_CHROMIUM,   /* Chrome, Chromium, Brave, Edge, Vivaldi */
  LY_IMPORT_FIREFOX,
} LyImportKind;

typedef struct {
  char        *label;     /* "Google Chrome" */
  char        *profile;   /* absolute path to the profile directory */
  LyImportKind kind;
  gboolean     has_bookmarks;
  gboolean     has_history;
  gboolean     has_passwords;
  /* Which keyring item holds this browser's password-encryption key. Chromium
   * forks each pick their own name for it, and guessing wrong is the whole
   * difference between reading the logins and not. */
  const char *const *secret_apps;
} LyImportSource;

void ly_import_source_free (LyImportSource *source);

/* Every browser profile found on this machine. Never empty-checked for you. */
GPtrArray *ly_import_sources (void);

typedef struct {
  guint bookmarks;
  guint history;
  char *error;          /* NULL on success */
} LyImportResult;

void ly_import_result_clear (LyImportResult *result);

gboolean ly_import_run (LyStore              *store,
                        const LyImportSource *source,
                        gboolean              bookmarks,
                        gboolean              history,
                        LyImportResult       *result);

/* Saved logins from another browser, decrypted, ready to be stored. Nothing is
 * written to the keyring here — the caller decides that.
 *
 * skipped counts entries the browser held but would not give up: Chromium rows
 * whose key is not in this session's keyring, and anything the decryption
 * rejected. A non-zero count with a non-empty result is normal and worth
 * telling the user about; it is not an error.
 */
GPtrArray *ly_import_passwords (const LyImportSource *source,
                                guint                *skipped,
                                GError              **error);

G_END_DECLS
