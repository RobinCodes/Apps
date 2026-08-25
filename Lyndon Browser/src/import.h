/* import.h — pull bookmarks and history out of other browsers.
 *
 * Read-only, and always from a copy: Chromium and Firefox both hold an
 * exclusive lock on their SQLite files while running, and the last thing an
 * import should do is disturb the browser it is reading from.
 */
#pragma once

#include "lyndon.h"
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

G_END_DECLS
