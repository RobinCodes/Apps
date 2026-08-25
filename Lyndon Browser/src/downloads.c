/* downloads.c — see downloads.h.
 *
 * Downloads are saved straight into the configured folder under a name that
 * never overwrites an existing file. There is no "where do you want this?"
 * dialog: WebKit needs the destination synchronously while deciding, and a
 * modal round trip there is the classic way to lose a download. */

#include "downloads.h"

#include <string.h>

struct _LyDownloads {
  LyConfig             *cfg;
  WebKitNetworkSession *session;
  GPtrArray            *items;
  LyDownloadsChangedFn  changed;
  LyDownloadDoneFn      done;
  gpointer              user_data;
};

static void
item_free (gpointer data)
{
  LyDownloadItem *item = data;
  g_clear_object (&item->download);
  g_free (item->name);
  g_free (item->path);
  g_free (item->uri);
  g_free (item->error);
  g_free (item);
}

static void
notify_changed (LyDownloads *downloads)
{
  if (downloads->changed != NULL)
    downloads->changed (downloads, downloads->user_data);
}

static LyDownloadItem *
find_item (LyDownloads *downloads, WebKitDownload *download)
{
  for (guint i = 0; i < downloads->items->len; i++) {
    LyDownloadItem *item = g_ptr_array_index (downloads->items, i);
    if (item->download == download)
      return item;
  }
  return NULL;
}

/* "report.pdf" -> "report (2).pdf" when the first two already exist. */
static char *
unique_path (const char *dir, const char *filename)
{
  g_autofree char *base = g_path_get_basename (filename);
  if (*base == '\0' || g_strcmp0 (base, ".") == 0 || g_strcmp0 (base, "/") == 0) {
    g_free (base);
    base = g_strdup ("download");
  }

  g_autofree char *candidate = g_build_filename (dir, base, NULL);
  if (!g_file_test (candidate, G_FILE_TEST_EXISTS))
    return g_steal_pointer (&candidate);

  const char *dot = strrchr (base, '.');
  g_autofree char *stem = dot && dot != base ? g_strndup (base, (size_t) (dot - base))
                                             : g_strdup (base);
  const char *ext = dot && dot != base ? dot : "";

  for (int n = 2; n < 1000; n++) {
    g_autofree char *name = g_strdup_printf ("%s (%d)%s", stem, n, ext);
    char *path = g_build_filename (dir, name, NULL);
    if (!g_file_test (path, G_FILE_TEST_EXISTS))
      return path;
    g_free (path);
  }
  return g_steal_pointer (&candidate);
}

/* ----------------------------------------------------------- callbacks */

static gboolean
on_decide_destination (WebKitDownload *download, const char *suggested, gpointer data)
{
  LyDownloads *downloads = data;
  LyDownloadItem *item = find_item (downloads, download);

  const char *dir = downloads->cfg->download_dir;
  if (dir == NULL || *dir == '\0')
    dir = g_get_user_special_dir (G_USER_DIRECTORY_DOWNLOAD) ?: g_get_home_dir ();
  g_mkdir_with_parents (dir, 0700);

  g_autofree char *path = unique_path (dir, suggested && *suggested ? suggested : "download");
  webkit_download_set_destination (download, path);

  if (item != NULL) {
    g_free (item->path);
    item->path = g_strdup (path);
    g_free (item->name);
    item->name = g_path_get_basename (path);
    notify_changed (downloads);
  }
  return TRUE;
}

static void
on_progress (GObject *object, GParamSpec *pspec, gpointer data)
{
  LyDownloads *downloads = data;
  LyDownloadItem *item = find_item (downloads, WEBKIT_DOWNLOAD (object));
  if (item == NULL)
    return;

  item->progress = webkit_download_get_estimated_progress (item->download);
  item->received = webkit_download_get_received_data_length (item->download);
  notify_changed (downloads);
}

static void
on_finished (WebKitDownload *download, gpointer data)
{
  LyDownloads *downloads = data;
  LyDownloadItem *item = find_item (downloads, download);
  if (item == NULL)
    return;

  /* "finished" also fires after "failed"; do not overwrite the failure. */
  if (!item->failed) {
    item->finished = TRUE;
    item->progress = 1.0;
    if (downloads->done != NULL)
      downloads->done (item, downloads->user_data);
  }
  notify_changed (downloads);
}

static void
on_failed (WebKitDownload *download, GError *error, gpointer data)
{
  LyDownloads *downloads = data;
  LyDownloadItem *item = find_item (downloads, download);
  if (item == NULL)
    return;

  item->failed   = TRUE;
  item->finished = TRUE;
  g_free (item->error);
  item->error = g_strdup (error ? error->message : "Download failed");
  notify_changed (downloads);
}

static void
on_download_started (WebKitNetworkSession *session, WebKitDownload *download, gpointer data)
{
  LyDownloads *downloads = data;

  LyDownloadItem *item = g_new0 (LyDownloadItem, 1);
  item->download = g_object_ref (download);

  WebKitURIRequest *request = webkit_download_get_request (download);
  const char *uri = request ? webkit_uri_request_get_uri (request) : NULL;
  item->uri  = g_strdup (uri ?: "");
  item->name = g_strdup ("Starting…");

  g_ptr_array_insert (downloads->items, 0, item);

  g_signal_connect (download, "decide-destination",
                    G_CALLBACK (on_decide_destination), downloads);
  g_signal_connect (download, "notify::estimated-progress",
                    G_CALLBACK (on_progress), downloads);
  g_signal_connect (download, "finished", G_CALLBACK (on_finished), downloads);
  g_signal_connect (download, "failed",   G_CALLBACK (on_failed), downloads);

  notify_changed (downloads);
}

/* ------------------------------------------------------------------ api */

LyDownloads *
ly_downloads_new (LyConfig *cfg, WebKitNetworkSession *session)
{
  LyDownloads *downloads = g_new0 (LyDownloads, 1);
  downloads->cfg     = cfg;
  downloads->session = session;
  downloads->items   = g_ptr_array_new_with_free_func (item_free);

  g_signal_connect (session, "download-started",
                    G_CALLBACK (on_download_started), downloads);
  return downloads;
}

void
ly_downloads_free (LyDownloads *downloads)
{
  if (downloads == NULL)
    return;
  g_signal_handlers_disconnect_by_data (downloads->session, downloads);
  g_clear_pointer (&downloads->items, g_ptr_array_unref);
  g_free (downloads);
}

GPtrArray *
ly_downloads_items (LyDownloads *downloads)
{
  return downloads->items;
}

guint
ly_downloads_active_count (LyDownloads *downloads)
{
  guint n = 0;
  for (guint i = 0; i < downloads->items->len; i++) {
    LyDownloadItem *item = g_ptr_array_index (downloads->items, i);
    if (!item->finished)
      n++;
  }
  return n;
}

void
ly_downloads_clear_finished (LyDownloads *downloads)
{
  for (guint i = downloads->items->len; i > 0; i--) {
    LyDownloadItem *item = g_ptr_array_index (downloads->items, i - 1);
    if (item->finished)
      g_ptr_array_remove_index (downloads->items, i - 1);
  }
  notify_changed (downloads);
}

void
ly_downloads_cancel (LyDownloads *downloads, LyDownloadItem *item)
{
  if (item == NULL || item->finished || item->download == NULL)
    return;
  webkit_download_cancel (item->download);
}

void
ly_downloads_set_callbacks (LyDownloads          *downloads,
                            LyDownloadsChangedFn  changed,
                            LyDownloadDoneFn      done,
                            gpointer              user_data)
{
  downloads->changed   = changed;
  downloads->done      = done;
  downloads->user_data = user_data;
}
