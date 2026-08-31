/* downloads.h — the download list, shared by every window.
 *
 * The Linux header hands out a WebKitDownload and lets callers talk to it.
 * Nothing like that appears here: WebView2's download object is COM and lives
 * in tab.c, which reports progress in through these three calls. Everything
 * else — the list, the counts, the panel that draws it — then needs to know
 * nothing about the engine, which is the only reason the panel code is the
 * same shape as the Linux one.
 */
#pragma once

#include "lyndon.h"

G_BEGIN_DECLS

typedef struct _LyDownloads LyDownloads;

typedef struct {
  char    *name;
  char    *path;
  char    *uri;
  guint64  received;
  guint64  total;        /* 0 when the server never said */
  gboolean finished;
  gboolean failed;
  gboolean cancelled;
  char    *error;
  gint64   when;         /* unix seconds, for ordering */
  gpointer engine;       /* the ICoreWebView2DownloadOperation, opaque here */
} LyDownloadItem;

typedef void (*LyDownloadsChangedFn) (LyDownloads *downloads, gpointer user_data);
/* Once per completed download, so a window can say so. */
typedef void (*LyDownloadDoneFn) (const LyDownloadItem *item, gpointer user_data);

LyDownloads *ly_downloads_new  (LyConfig *cfg);
void         ly_downloads_free (LyDownloads *downloads);

void ly_downloads_set_callbacks (LyDownloads          *downloads,
                                 LyDownloadsChangedFn  changed,
                                 LyDownloadDoneFn      done,
                                 gpointer              user_data);

/* -- what the engine reports -------------------------------------------- */

LyDownloadItem *ly_downloads_begin    (LyDownloads *downloads, const char *uri,
                                       const char *path, guint64 total,
                                       gpointer engine);
void            ly_downloads_progress (LyDownloads *downloads, LyDownloadItem *item,
                                       guint64 received);
void            ly_downloads_finish   (LyDownloads *downloads, LyDownloadItem *item,
                                       gboolean ok, const char *error);

/* -- what the UI asks --------------------------------------------------- */

GPtrArray *ly_downloads_items          (LyDownloads *downloads);  /* LyDownloadItem* */
guint      ly_downloads_active_count   (LyDownloads *downloads);
void       ly_downloads_clear_finished (LyDownloads *downloads);
void       ly_downloads_remove         (LyDownloads *downloads, LyDownloadItem *item);
/* Marks the item cancelled; tab.c watches for that and tells the engine. */
void       ly_downloads_cancel         (LyDownloads *downloads, LyDownloadItem *item);
void       ly_downloads_open           (const LyDownloadItem *item);
void       ly_downloads_show_in_folder (const LyDownloadItem *item);

/* Where a download should go, honouring cfg->download_dir and never
 * overwriting: "report.pdf" becomes "report (2).pdf". Free with g_free(). */
char *ly_downloads_target_path (LyDownloads *downloads, const char *suggested);

/* "1.2 MB of 4.0 MB" or "1.2 MB". Free with g_free(). */
char *ly_download_progress_text (const LyDownloadItem *item);

G_END_DECLS
