/* archive.h — offline copies of whole sites.
 *
 * This is a crawler, not a page saver: "Save Page As" already exists and
 * writes one MHTML file through WebKit. What this does instead is walk a site
 * over plain HTTP with libsoup, keep every page and every subresource it needs
 * to render, and rewrite the links between them so the result browses offline
 * from a folder of ordinary files.
 *
 * It fetches on its own, outside WebKit, and therefore outside your cookies:
 * what lands on disk is the logged-out view of a site. That is a deliberate
 * limit, not an oversight — mirroring a logged-in session would write other
 * people's private pages into a folder with no access control on it.
 */
#pragma once

#include "lyndon.h"
#include "engine.h"
#include "store.h"

G_BEGIN_DECLS

typedef enum {
  LY_ARCHIVE_SAVED = 0,   /* <dir>/Saved     — someone asked for this one */
  LY_ARCHIVE_AUTOMATIC,   /* <dir>/Automatic — the sweep took it          */
} LyArchiveKind;

typedef struct _LyArchive LyArchive;

/* The public half of a crawl: enough to draw a row for it and no more. */
typedef struct {
  char          *host;
  char          *start;      /* the URL the crawl began from     */
  char          *root;       /* the directory it is written to   */
  LyArchiveKind  kind;
  guint          pages;      /* HTML documents written           */
  guint          files;      /* files written in total           */
  guint          queued;     /* still to fetch                   */
  guint64        bytes;
  gboolean       rewriting;  /* fetching done, relinking         */
  gboolean       finished;
  gboolean       cancelled;
  gboolean       failed;
  char          *error;
} LyArchiveJob;

typedef void (*LyArchiveChangedFn) (LyArchive *archive, gpointer user_data);
/* Raised once per crawl that stops, however it stopped. */
typedef void (*LyArchiveDoneFn)    (const LyArchiveJob *job, gpointer user_data);

LyArchive *ly_archive_new  (LyConfig *cfg, LyEngine *engine);
void       ly_archive_free (LyArchive *archive);

void ly_archive_set_callbacks (LyArchive          *archive,
                               LyArchiveChangedFn  changed,
                               LyArchiveDoneFn     done,
                               gpointer            user_data);

/* Mirror the site `url` belongs to. `dest` overrides where it lands, which is
 * what the "ask every time" path passes; NULL means the configured folder.
 * FALSE with `reason` set when the site is not one we may take — the feature
 * is off, the host is blocked, or a crawl of it is already running. */
gboolean ly_archive_start (LyArchive *archive, const char *url,
                           LyArchiveKind kind, const char *dest, char **reason);

GPtrArray *ly_archive_jobs   (LyArchive *archive);   /* LyArchiveJob* */
guint      ly_archive_active (LyArchive *archive);
void       ly_archive_cancel (LyArchive *archive, LyArchiveJob *job);
void       ly_archive_clear_finished (LyArchive *archive);

/* One pass of the automatic sweep: every site in history from the last
 * configured number of days that is eligible and not already mirrored
 * recently. Does nothing at all unless both the feature and the sweep are on. */
void ly_archive_sweep (LyArchive *archive, LyStore *store);

/* ------------------------------------------------------------- policy --- */
/* Public because this is the part worth testing without a network. */

/* Where mirrors live: [offline] directory, or ~/Documents/Websites. */
char    *ly_archive_base_dir     (LyConfig *cfg);
char    *ly_archive_root_for     (LyConfig *cfg, LyArchiveKind kind, const char *host);
/* An empty allow list means "anything not blocked". Matching is on the host
 * and any parent of it, so "example.com" covers "docs.example.com". */
gboolean ly_archive_host_allowed (LyConfig *cfg, const char *host);

/* ------------------------------------------------------------ mapping --- */

/* Where `url` lands inside a mirror rooted at `root` whose main host is
 * `primary`. `ext` is the extension content sniffing wants to give it, or
 * NULL to take whatever the URL already says. NULL for a URL no mirror can
 * hold — anything that is not http(s), or that carries no host. */
char *ly_archive_local_path (const char *root, const char *primary,
                             const char *url, const char *ext);
/* `to` written as a relative link from the document saved at `from`. Both are
 * local paths; the result is what goes in the rewritten href. */
char *ly_archive_relative   (const char *from, const char *to);

G_END_DECLS
