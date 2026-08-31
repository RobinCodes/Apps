/* downloads.c — see downloads.h. */

#include "downloads.h"

#include <windows.h>
#include <shlobj.h>
#include <shellapi.h>
#include <string.h>

struct _LyDownloads {
  LyConfig  *cfg;
  GPtrArray *items;            /* LyDownloadItem*, newest last */

  LyDownloadsChangedFn changed;
  LyDownloadDoneFn     done;
  gpointer             user_data;
};

static void
item_free (LyDownloadItem *item)
{
  if (item == NULL)
    return;
  g_free (item->name);
  g_free (item->path);
  g_free (item->uri);
  g_free (item->error);
  g_free (item);
}

static void
notify (LyDownloads *d)
{
  if (d->changed)
    d->changed (d, d->user_data);
}

/* ------------------------------------------------------------ life cycle */

LyDownloads *
ly_downloads_new (LyConfig *cfg)
{
  LyDownloads *d = g_new0 (LyDownloads, 1);
  d->cfg = cfg;
  d->items = g_ptr_array_new_with_free_func ((GDestroyNotify) item_free);
  return d;
}

void
ly_downloads_free (LyDownloads *d)
{
  if (d == NULL)
    return;
  g_ptr_array_free (d->items, TRUE);
  g_free (d);
}

void
ly_downloads_set_callbacks (LyDownloads *d, LyDownloadsChangedFn changed,
                            LyDownloadDoneFn done, gpointer user_data)
{
  d->changed = changed;
  d->done = done;
  d->user_data = user_data;
}

/* ------------------------------------------------------- where files land */

char *
ly_downloads_target_path (LyDownloads *d, const char *suggested)
{
  const char *dir = (d->cfg && d->cfg->download_dir && *d->cfg->download_dir)
                      ? d->cfg->download_dir : NULL;
  g_autofree char *fallback = NULL;
  if (dir == NULL) {
    /* KNOWNFOLDERID for Downloads, which is not always under the profile —
     * people move it to another drive and Windows remembers. */
    PWSTR w = NULL;
    if (SUCCEEDED (SHGetKnownFolderPath (&FOLDERID_Downloads, 0, NULL, &w)) && w) {
      fallback = g_utf16_to_utf8 ((const gunichar2 *) w, -1, NULL, NULL, NULL);
      CoTaskMemFree (w);
    }
    if (fallback == NULL)
      fallback = g_build_filename (g_get_home_dir (), "Downloads", NULL);
    dir = fallback;
  }
  g_mkdir_with_parents (dir, 0700);

  g_autofree char *base = g_path_get_basename (suggested && *suggested ? suggested : "download");
  g_autofree char *candidate = g_build_filename (dir, base, NULL);
  if (!g_file_test (candidate, G_FILE_TEST_EXISTS))
    return g_steal_pointer (&candidate);

  /* "report.pdf" -> "report (2).pdf". Never overwrite: a download the user
   * did not ask to replace is not the browser's to destroy. */
  const char *dot = strrchr (base, '.');
  g_autofree char *stem = dot ? g_strndup (base, (gsize) (dot - base)) : g_strdup (base);
  const char *ext = dot ? dot : "";

  for (int n = 2; n < 1000; n++) {
    g_autofree char *name = g_strdup_printf ("%s (%d)%s", stem, n, ext);
    char *path = g_build_filename (dir, name, NULL);
    if (!g_file_test (path, G_FILE_TEST_EXISTS))
      return path;
    g_free (path);
  }
  return g_steal_pointer (&candidate);
}

/* --------------------------------------------------- what the engine says */

LyDownloadItem *
ly_downloads_begin (LyDownloads *d, const char *uri, const char *path,
                    guint64 total, gpointer engine)
{
  LyDownloadItem *item = g_new0 (LyDownloadItem, 1);
  item->uri    = g_strdup (uri);
  item->path   = g_strdup (path);
  item->name   = g_path_get_basename (path ? path : "download");
  item->total  = total;
  item->engine = engine;
  item->when   = g_get_real_time () / G_USEC_PER_SEC;
  g_ptr_array_add (d->items, item);
  notify (d);
  return item;
}

void
ly_downloads_progress (LyDownloads *d, LyDownloadItem *item, guint64 received)
{
  if (item == NULL || item->finished)
    return;
  item->received = received;
  notify (d);
}

void
ly_downloads_finish (LyDownloads *d, LyDownloadItem *item, gboolean ok, const char *error)
{
  if (item == NULL || item->finished)
    return;
  item->finished = TRUE;
  item->failed = !ok;
  item->engine = NULL;
  if (!ok) {
    g_free (item->error);
    item->error = g_strdup (error ? error : "The download did not finish.");
  } else if (item->total == 0) {
    item->total = item->received;
  }
  notify (d);
  if (ok && d->done)
    d->done (item, d->user_data);
}

/* ------------------------------------------------------------ the listing */

GPtrArray *
ly_downloads_items (LyDownloads *d)
{
  return d->items;
}

guint
ly_downloads_active_count (LyDownloads *d)
{
  guint n = 0;
  for (guint i = 0; i < d->items->len; i++) {
    const LyDownloadItem *item = g_ptr_array_index (d->items, i);
    if (!item->finished)
      n++;
  }
  return n;
}

void
ly_downloads_clear_finished (LyDownloads *d)
{
  for (guint i = d->items->len; i > 0; i--) {
    LyDownloadItem *item = g_ptr_array_index (d->items, i - 1);
    if (item->finished)
      g_ptr_array_remove_index (d->items, i - 1);
  }
  notify (d);
}

void
ly_downloads_remove (LyDownloads *d, LyDownloadItem *item)
{
  g_ptr_array_remove (d->items, item);
  notify (d);
}

void
ly_downloads_cancel (LyDownloads *d, LyDownloadItem *item)
{
  if (item == NULL || item->finished)
    return;
  /* The engine object is owned by tab.c, which is watching this flag; setting
   * it here keeps COM out of the list. */
  item->cancelled = TRUE;
  notify (d);
}

/* ------------------------------------------------------------- the shell */

void
ly_downloads_open (const LyDownloadItem *item)
{
  if (item == NULL || item->path == NULL || !item->finished || item->failed)
    return;
  g_autofree wchar_t *w = (wchar_t *) g_utf8_to_utf16 (item->path, -1, NULL, NULL, NULL);
  if (w)
    ShellExecuteW (NULL, L"open", w, NULL, NULL, SW_SHOWNORMAL);
}

void
ly_downloads_show_in_folder (const LyDownloadItem *item)
{
  if (item == NULL || item->path == NULL)
    return;
  /* /select, opens Explorer with the file highlighted rather than just the
   * folder, which is what "show in folder" means everywhere else. */
  g_autofree char *arg = g_strdup_printf ("/select,\"%s\"", item->path);
  g_autofree wchar_t *w = (wchar_t *) g_utf8_to_utf16 (arg, -1, NULL, NULL, NULL);
  if (w)
    ShellExecuteW (NULL, L"open", L"explorer.exe", w, NULL, SW_SHOWNORMAL);
}

char *
ly_download_progress_text (const LyDownloadItem *item)
{
  if (item->failed)
    return g_strdup (item->error ? item->error : "Failed");
  if (item->cancelled && !item->finished)
    return g_strdup ("Cancelling…");

  g_autofree char *got = ly_format_size (item->received);
  if (item->finished)
    return g_strdup (got);
  if (item->total > 0) {
    g_autofree char *all = ly_format_size (item->total);
    return g_strdup_printf ("%s of %s", got, all);
  }
  return g_strdup_printf ("%s so far", got);
}
