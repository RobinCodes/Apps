/* downloads.h — a small download list shared by every window. */
#pragma once

#include "lyndon.h"

G_BEGIN_DECLS

typedef struct _LyDownloads LyDownloads;

typedef struct {
  WebKitDownload *download;   /* NULL once WebKit has released it */
  char           *name;
  char           *path;
  char           *uri;
  double          progress;
  guint64         received;
  gboolean        finished;
  gboolean        failed;
  char           *error;
} LyDownloadItem;

typedef void (*LyDownloadsChangedFn) (LyDownloads *downloads, gpointer user_data);
/* Raised once per completed download so the window can show a toast. */
typedef void (*LyDownloadDoneFn)     (const LyDownloadItem *item, gpointer user_data);

LyDownloads *ly_downloads_new  (LyConfig *cfg, WebKitNetworkSession *session);
void         ly_downloads_free (LyDownloads *downloads);

GPtrArray *ly_downloads_items          (LyDownloads *downloads);  /* LyDownloadItem* */
guint      ly_downloads_active_count   (LyDownloads *downloads);
void       ly_downloads_clear_finished (LyDownloads *downloads);
void       ly_downloads_cancel         (LyDownloads *downloads, LyDownloadItem *item);

void ly_downloads_set_callbacks (LyDownloads          *downloads,
                                 LyDownloadsChangedFn  changed,
                                 LyDownloadDoneFn      done,
                                 gpointer              user_data);

G_END_DECLS
