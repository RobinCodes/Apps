/* blocker.c — see blocker.h. */

#include "blocker.h"

#include <libsoup/soup.h>
#include <string.h>

#define RESOURCE_PREFIX "/org/lyndon/Browser/rules/"
#define SUBSCRIPTION_MAX_AGE_DAYS 5
#define FILTER_RULE_LIMIT 60000

typedef struct {
  WebKitUserContentManager *ucm;   /* not owned; the tab owns it */
  guint                     refs;  /* tabs sharing this manager */
  gboolean                  active;
  gboolean                  installed;
} Attachment;

struct _LyBlocker {
  LyConfig                     *cfg;
  WebKitUserContentFilterStore *store;
  WebKitUserContentFilter      *filter;
  char                         *identifier;
  GPtrArray                    *attachments;   /* Attachment* */
  GCancellable                 *cancel;
  LyAbpStats                    stats;
  gboolean                      ready;
  gboolean                      building;
  gboolean                      rebuild_queued;
  char                         *status;
  guint                         pending_fetches;
  SoupSession                  *http;
  LyBlockerReadyFn              on_ready;
  gpointer                      on_ready_data;
};

/* -------------------------------------------------------------- utilities */

char *
ly_blocker_custom_rules_path (void)
{
  g_autofree char *dir = ly_config_dir ();
  g_mkdir_with_parents (dir, 0700);
  char *path = g_build_filename (dir, "custom-rules.txt", NULL);

  if (!g_file_test (path, G_FILE_TEST_EXISTS)) {
    const char *seed =
      "! Your own rules. Adblock Plus syntax; one rule per line.\n"
      "!\n"
      "!   ||tracker.example^              block a host everywhere\n"
      "!   ||ads.example.com^$third-party  block it only as a third party\n"
      "!   example.com##.promo             hide an element on one site\n"
      "!   @@||example.com/needed^         carve an exception out again\n"
      "!\n"
      "! Lines starting with ! are comments. Saved changes apply on Rebuild.\n";
    g_file_set_contents (path, seed, -1, NULL);
  }
  return path;
}

static char *
subscription_cache_path (const char *url)
{
  g_autofree char *cache = ly_cache_dir ();
  g_autofree char *lists = g_build_filename (cache, "lists", NULL);
  g_mkdir_with_parents (lists, 0700);

  g_autofree char *hash = g_compute_checksum_for_string (G_CHECKSUM_SHA256, url, -1);
  g_autofree char *name = g_strdup_printf ("%.20s.txt", hash);
  return g_build_filename (lists, name, NULL);
}

static void
set_status (LyBlocker *blocker, const char *fmt, ...) G_GNUC_PRINTF (2, 3);

static void
set_status (LyBlocker *blocker, const char *fmt, ...)
{
  va_list args;
  va_start (args, fmt);
  g_free (blocker->status);
  blocker->status = g_strdup_vprintf (fmt, args);
  va_end (args);
}

/* ------------------------------------------------------------ attachments */

static Attachment *
find_attachment (LyBlocker *blocker, WebKitUserContentManager *ucm)
{
  for (guint i = 0; i < blocker->attachments->len; i++) {
    Attachment *a = g_ptr_array_index (blocker->attachments, i);
    if (a->ucm == ucm)
      return a;
  }
  return NULL;
}

static void
sync_attachment (LyBlocker *blocker, Attachment *a)
{
  gboolean enabled = blocker->cfg->block_enabled || blocker->cfg->https_only;
  gboolean want = enabled && a->active && blocker->filter != NULL;

  if (want && !a->installed) {
    webkit_user_content_manager_add_filter (a->ucm, blocker->filter);
    a->installed = TRUE;
  } else if (!want && a->installed) {
    webkit_user_content_manager_remove_all_filters (a->ucm);
    a->installed = FALSE;
  }
}

static void
sync_all (LyBlocker *blocker)
{
  for (guint i = 0; i < blocker->attachments->len; i++)
    sync_attachment (blocker, g_ptr_array_index (blocker->attachments, i));
}

void
ly_blocker_attach (LyBlocker *blocker, WebKitUserContentManager *ucm)
{
  /* A view opened with related-view inherits its opener's content manager, so
   * two tabs can share one. Count them, or closing the first would strip
   * blocking from the second. */
  Attachment *existing = find_attachment (blocker, ucm);
  if (existing != NULL) {
    existing->refs++;
    return;
  }

  Attachment *a = g_new0 (Attachment, 1);
  a->ucm    = ucm;
  a->refs   = 1;
  a->active = TRUE;
  g_ptr_array_add (blocker->attachments, a);
  sync_attachment (blocker, a);
}

void
ly_blocker_detach (LyBlocker *blocker, WebKitUserContentManager *ucm)
{
  for (guint i = 0; i < blocker->attachments->len; i++) {
    Attachment *a = g_ptr_array_index (blocker->attachments, i);
    if (a->ucm == ucm) {
      if (--a->refs == 0)
        g_ptr_array_remove_index_fast (blocker->attachments, i);
      return;
    }
  }
}

void
ly_blocker_set_active (LyBlocker *blocker, WebKitUserContentManager *ucm, gboolean active)
{
  Attachment *a = find_attachment (blocker, ucm);
  if (a == NULL || a->active == active)
    return;
  a->active = active;
  sync_attachment (blocker, a);
}

/* -------------------------------------------------------------- gathering */

static void
append_resource (GString *out, const char *name)
{
  g_autofree char *path = g_strconcat (RESOURCE_PREFIX, name, ".txt", NULL);
  g_autoptr (GError) error = NULL;
  g_autoptr (GBytes) bytes =
    g_resources_lookup_data (path, G_RESOURCE_LOOKUP_FLAGS_NONE, &error);

  if (bytes == NULL) {
    g_warning ("built-in rule list %s missing: %s", name, error->message);
    return;
  }

  gsize len = 0;
  const char *data = g_bytes_get_data (bytes, &len);
  g_string_append_len (out, data, (gssize) len);
  g_string_append_c (out, '\n');
}

static void
append_file (GString *out, const char *path)
{
  g_autofree char *text = NULL;
  gsize len = 0;
  if (g_file_get_contents (path, &text, &len, NULL)) {
    g_string_append_len (out, text, (gssize) len);
    g_string_append_c (out, '\n');
  }
}

/* Collect every rule source the current configuration selects. */
static char *
gather_rules (LyBlocker *blocker)
{
  LyConfig *cfg = blocker->cfg;
  GString *out = g_string_sized_new (1 << 17);

  for (int i = 0; i < LY_CAT_N; i++)
    if (cfg->block_cat[i])
      append_resource (out, ly_cat_id (i));

  for (guint i = 0; i < cfg->subscriptions->len; i++) {
    const char *url = g_ptr_array_index (cfg->subscriptions, i);
    g_autofree char *cached = subscription_cache_path (url);
    append_file (out, cached);
  }

  g_autofree char *custom = ly_blocker_custom_rules_path ();
  append_file (out, custom);

  return g_string_free (out, FALSE);
}

/* ------------------------------------------------------------- compilation */

static void
finish_build (LyBlocker *blocker, WebKitUserContentFilter *filter, const char *error_text)
{
  blocker->building = FALSE;

  if (filter != NULL) {
    g_clear_pointer (&blocker->filter, webkit_user_content_filter_unref);
    blocker->filter = filter;   /* transfer */
    blocker->ready  = TRUE;

    /* Re-install everywhere: the old filter object is gone. */
    for (guint i = 0; i < blocker->attachments->len; i++) {
      Attachment *a = g_ptr_array_index (blocker->attachments, i);
      if (a->installed) {
        webkit_user_content_manager_remove_all_filters (a->ucm);
        a->installed = FALSE;
      }
    }
    sync_all (blocker);

    set_status (blocker, "%u rules active", ly_blocker_rule_count (blocker));
  } else {
    blocker->ready = FALSE;
    set_status (blocker, "%s", error_text ?: "rule compilation failed");
  }

  if (blocker->on_ready != NULL)
    blocker->on_ready (blocker, blocker->on_ready_data);

  if (blocker->rebuild_queued) {
    blocker->rebuild_queued = FALSE;
    ly_blocker_rebuild (blocker);
  }
}

static void
on_filter_saved (GObject *source, GAsyncResult *result, gpointer data)
{
  LyBlocker *blocker = data;
  g_autoptr (GError) error = NULL;

  WebKitUserContentFilter *filter =
    webkit_user_content_filter_store_save_finish (WEBKIT_USER_CONTENT_FILTER_STORE (source),
                                                 result, &error);

  if (filter == NULL && g_error_matches (error, G_IO_ERROR, G_IO_ERROR_CANCELLED))
    return;   /* blocker is going away */

  if (filter == NULL)
    g_warning ("content filter compilation failed: %s", error->message);

  finish_build (blocker, filter, filter ? NULL : error->message);
}

void
ly_blocker_rebuild (LyBlocker *blocker)
{
  if (blocker->building) {
    blocker->rebuild_queued = TRUE;
    return;
  }

  /* HTTPS-only is carried by the same filter list, so the list still has to
   * be built when blocking itself is switched off. */
  if (!blocker->cfg->block_enabled && !blocker->cfg->https_only) {
    g_clear_pointer (&blocker->filter, webkit_user_content_filter_unref);
    blocker->ready = FALSE;
    sync_all (blocker);
    set_status (blocker, "blocking is off");
    if (blocker->on_ready != NULL)
      blocker->on_ready (blocker, blocker->on_ready_data);
    return;
  }

  g_autofree char *rules = gather_rules (blocker);

  LyAbp *abp = ly_abp_new ();
  ly_abp_set_cosmetic (abp, blocker->cfg->block_hide_placeholders);
  ly_abp_set_limit (abp, FILTER_RULE_LIMIT);
  ly_abp_add_text (abp, rules);

  if (blocker->cfg->block_strict_third_party) {
    /* Hosts a page legitimately needs, but which must not carry a stable
     * cookie across sites. Blocking them outright breaks embeds; stripping
     * their cookies does not. */
    static const char *const COOKIE_STRIP[] = {
      "bing.com", "linkedin.com", "youtube.com", "youtube-nocookie.com",
      "vimeo.com", "twitch.tv", "disqus.com", "gravatar.com",
      "hubspot.com", "zendesk.com", "cloudflareinsights.com", "hcaptcha.com",
    };
    for (guint i = 0; i < G_N_ELEMENTS (COOKIE_STRIP); i++)
      ly_abp_add_cookie_block (abp, COOKIE_STRIP[i]);
  }

  if (blocker->cfg->https_only)
    ly_abp_add_https_upgrade (abp);

  LyAbpStats stats;
  char *json = ly_abp_finish (abp, &stats);
  ly_abp_free (abp);
  blocker->stats = stats;

  g_autofree char *hash =
    g_compute_checksum_for_string (G_CHECKSUM_SHA256, json, -1);
  g_autofree char *identifier = g_strdup_printf ("lyndon-%.16s", hash);

  /* Nothing changed: the compiled filter on hand is still correct. */
  if (blocker->filter != NULL && g_strcmp0 (blocker->identifier, identifier) == 0) {
    g_free (json);
    sync_all (blocker);
    return;
  }

  g_free (blocker->identifier);
  blocker->identifier = g_steal_pointer (&identifier);

  blocker->building = TRUE;
  set_status (blocker, "compiling %u rules…",
              stats.blocks + stats.cosmetics + stats.exceptions);

  gsize json_len = strlen (json);
  g_autoptr (GBytes) bytes = g_bytes_new_take (json, json_len);

  /* WebKit caches compiled filters on disk under their identifier, so an
   * unchanged rule set is loaded rather than recompiled on the next launch. */
  webkit_user_content_filter_store_save (blocker->store,
                                         blocker->identifier,
                                         bytes,
                                         blocker->cancel,
                                         on_filter_saved,
                                         blocker);
}

/* ----------------------------------------------------------- subscriptions */

typedef struct {
  LyBlocker *blocker;
  char      *url;
  char      *path;
} Fetch;

static void
fetch_free (Fetch *f)
{
  g_free (f->url);
  g_free (f->path);
  g_free (f);
}

static void
fetch_done (LyBlocker *blocker)
{
  if (blocker->pending_fetches > 0)
    blocker->pending_fetches--;
  if (blocker->pending_fetches == 0)
    ly_blocker_rebuild (blocker);
}

static void
on_list_fetched (GObject *source, GAsyncResult *result, gpointer data)
{
  Fetch *f = data;
  g_autoptr (GError) error = NULL;

  g_autoptr (GBytes) body =
    soup_session_send_and_read_finish (SOUP_SESSION (source), result, &error);

  if (body == NULL) {
    if (!g_error_matches (error, G_IO_ERROR, G_IO_ERROR_CANCELLED)) {
      g_message ("filter list %s could not be fetched: %s", f->url, error->message);
      fetch_done (f->blocker);
    }
    fetch_free (f);
    return;
  }

  gsize len = 0;
  const char *text = g_bytes_get_data (body, &len);

  /* A truncated or error-page response would silently gut the blocker. */
  if (len > 64 && (memchr (text, '\n', len) != NULL)) {
    g_autoptr (GError) write_error = NULL;
    if (!g_file_set_contents (f->path, text, (gssize) len, &write_error))
      g_message ("could not cache %s: %s", f->url, write_error->message);
  } else {
    g_message ("filter list %s looked empty, keeping the previous copy", f->url);
  }

  fetch_done (f->blocker);
  fetch_free (f);
}

void
ly_blocker_update_subscriptions (LyBlocker *blocker, gboolean force)
{
  LyConfig *cfg = blocker->cfg;
  if (cfg->subscriptions->len == 0)
    return;

  if (blocker->http == NULL) {
    blocker->http = soup_session_new_with_options (
      "user-agent", "Lyndon/" LYNDON_VERSION " (filter list updater)",
      "timeout",    (guint) 20,
      NULL);
  }

  g_autoptr (GDateTime) now = g_date_time_new_now_utc ();

  for (guint i = 0; i < cfg->subscriptions->len; i++) {
    const char *url = g_ptr_array_index (cfg->subscriptions, i);
    if (!g_str_has_prefix (url, "https://") && !g_str_has_prefix (url, "http://"))
      continue;

    g_autofree char *path = subscription_cache_path (url);

    if (!force && g_file_test (path, G_FILE_TEST_EXISTS)) {
      g_autoptr (GFile) file = g_file_new_for_path (path);
      g_autoptr (GFileInfo) info =
        g_file_query_info (file, G_FILE_ATTRIBUTE_TIME_MODIFIED,
                           G_FILE_QUERY_INFO_NONE, NULL, NULL);
      if (info != NULL) {
        g_autoptr (GDateTime) modified = g_file_info_get_modification_date_time (info);
        if (modified != NULL) {
          GTimeSpan age = g_date_time_difference (now, modified);
          if (age < (GTimeSpan) SUBSCRIPTION_MAX_AGE_DAYS * G_TIME_SPAN_DAY)
            continue;
        }
      }
    }

    g_autoptr (SoupMessage) msg = soup_message_new ("GET", url);
    if (msg == NULL)
      continue;

    Fetch *f = g_new0 (Fetch, 1);
    f->blocker = blocker;
    f->url     = g_strdup (url);
    f->path    = g_steal_pointer (&path);

    blocker->pending_fetches++;
    soup_session_send_and_read_async (blocker->http, msg, G_PRIORITY_LOW,
                                      blocker->cancel, on_list_fetched, f);
  }

  if (blocker->pending_fetches > 0)
    set_status (blocker, "updating %u filter list%s…",
                blocker->pending_fetches,
                blocker->pending_fetches == 1 ? "" : "s");
}

/* -------------------------------------------------------------- accessors */

gboolean
ly_blocker_ready (LyBlocker *blocker)
{
  return blocker->ready && blocker->filter != NULL;
}

const LyAbpStats *
ly_blocker_stats (LyBlocker *blocker)
{
  return &blocker->stats;
}

guint
ly_blocker_rule_count (LyBlocker *blocker)
{
  return blocker->stats.blocks + blocker->stats.cosmetics + blocker->stats.exceptions;
}

const char *
ly_blocker_status_text (LyBlocker *blocker)
{
  return blocker->status ?: "";
}

void
ly_blocker_set_ready_callback (LyBlocker *blocker, LyBlockerReadyFn fn, gpointer user_data)
{
  blocker->on_ready      = fn;
  blocker->on_ready_data = user_data;
}

/* ------------------------------------------------------------- lifecycle */

LyBlocker *
ly_blocker_new (LyConfig *cfg)
{
  LyBlocker *blocker = g_new0 (LyBlocker, 1);
  blocker->cfg         = cfg;
  blocker->attachments = g_ptr_array_new_with_free_func (g_free);
  blocker->cancel      = g_cancellable_new ();
  blocker->status      = g_strdup ("starting…");

  g_autofree char *cache = ly_cache_dir ();
  g_autofree char *filters = g_build_filename (cache, "filters", NULL);
  g_mkdir_with_parents (filters, 0700);
  blocker->store = webkit_user_content_filter_store_new (filters);

  return blocker;
}

void
ly_blocker_free (LyBlocker *blocker)
{
  if (blocker == NULL)
    return;

  g_cancellable_cancel (blocker->cancel);
  g_clear_object (&blocker->cancel);
  g_clear_object (&blocker->http);
  g_clear_object (&blocker->store);
  g_clear_pointer (&blocker->filter, webkit_user_content_filter_unref);
  g_clear_pointer (&blocker->attachments, g_ptr_array_unref);
  g_clear_pointer (&blocker->identifier, g_free);
  g_clear_pointer (&blocker->status, g_free);
  g_free (blocker);
}
