/* config.c — plain-file configuration.
 *
 * Everything lives in ~/.config/lyndon/config.ini. A GKeyFile parse at startup
 * is measured in microseconds, which is why this is not GSettings: no schema
 * compilation, no dconf daemon, no D-Bus round trip on a cold start.
 */

#include "lyndon.h"

#include <string.h>

typedef struct {
  guint             id;
  LyConfigChangedFn fn;
  gpointer          data;
} LyConfigWatch;

/* ---------------------------------------------------------------- tables */

static const struct { const char *id, *label; } PERMS[LY_PERM_N] = {
  [LY_PERM_GEOLOCATION]     = { "geolocation",     "Location" },
  [LY_PERM_CAMERA]          = { "camera",          "Camera" },
  [LY_PERM_MICROPHONE]      = { "microphone",      "Microphone" },
  [LY_PERM_DISPLAY_CAPTURE] = { "display-capture", "Screen sharing" },
  [LY_PERM_NOTIFICATIONS]   = { "notifications",   "Notifications" },
  [LY_PERM_CLIPBOARD]       = { "clipboard",       "Clipboard" },
  [LY_PERM_POINTER_LOCK]    = { "pointer-lock",    "Pointer lock" },
  [LY_PERM_DRM]             = { "drm",             "Protected content (DRM)" },
  [LY_PERM_DEVICE_INFO]     = { "device-info",     "Media device names" },
  [LY_PERM_XR]              = { "xr",              "VR / AR devices" },
};

static const struct { const char *id, *label, *summary; } CATS[LY_CAT_N] = {
  [LY_CAT_ADS] = { "ads", "Advertising",
    "Display, video and native ad networks" },
  [LY_CAT_TRACKERS] = { "trackers", "Trackers",
    "Cross-site beacons, pixels and fingerprinting scripts" },
  [LY_CAT_ANALYTICS] = { "analytics", "Analytics",
    "Page-view, session-replay and heat-map collectors" },
  [LY_CAT_SOCIAL] = { "social", "Social widgets",
    "Like buttons and embeds that phone home on every page" },
  [LY_CAT_ANNOYANCES] = { "annoyances", "Annoyances",
    "Newsletter interstitials, push nags and paywall overlays" },
  [LY_CAT_COOKIE_NOTICES] = { "cookie-notices", "Cookie notices",
    "Consent banners and the CMP scripts behind them" },
  [LY_CAT_CRYPTOMINING] = { "cryptomining", "Cryptomining",
    "In-page miners that burn your CPU" },
};

const char *ly_perm_id     (LyPermKind k) { return PERMS[k].id; }
const char *ly_perm_label  (LyPermKind k) { return PERMS[k].label; }
const char *ly_cat_id      (LyBlockCat c) { return CATS[c].id; }
const char *ly_cat_label   (LyBlockCat c) { return CATS[c].label; }
const char *ly_cat_summary (LyBlockCat c) { return CATS[c].summary; }

/* ------------------------------------------------------------------ dirs */

char *
ly_config_dir (void)
{
  return g_build_filename (g_get_user_config_dir (), "lyndon", NULL);
}

char *
ly_data_dir (void)
{
  return g_build_filename (g_get_user_data_dir (), "lyndon", NULL);
}

char *
ly_cache_dir (void)
{
  return g_build_filename (g_get_user_cache_dir (), "lyndon", NULL);
}

/* ------------------------------------------------------------ enum <-> id */

static int
enum_from_id (const char *value, const char *const *ids, int n, int fallback)
{
  if (value == NULL)
    return fallback;
  for (int i = 0; i < n; i++)
    if (g_ascii_strcasecmp (value, ids[i]) == 0)
      return i;
  return fallback;
}

static const char *const SCHEME_IDS[] = { "system", "light", "dark" };
static const char *const DARK_IDS[]   = { "off", "smart", "always" };
static const char *const COOKIE_IDS[] = { "none", "no-third-party", "all" };
static const char *const POLICY_IDS[] = { "ask", "allow", "deny" };
static const char *const HW_IDS[]     = { "auto", "always", "never" };
static const char *const UA_IDS[]     = { "default", "minimal", "custom" };
static const char *const PROXY_IDS[]  = { "system", "none", "custom" };
static const char *const FX_IDS[]     = { "full", "reduced", "off" };

/* ----------------------------------------------------------- constructor */

LyConfig *
ly_config_new (void)
{
  LyConfig *cfg = g_new0 (LyConfig, 1);

  cfg->scheme              = LY_SCHEME_SYSTEM;
  cfg->effects             = LY_FX_FULL;
  cfg->compact_chrome      = TRUE;
  cfg->show_tab_bar_single = FALSE;
  cfg->show_bookmarks_bar  = FALSE;
  cfg->ui_opacity          = 0.82;

  cfg->force_dark       = LY_DARK_OFF;
  cfg->javascript       = TRUE;
  cfg->images           = TRUE;
  cfg->webgl            = FALSE;   /* a large fingerprinting surface  */
  cfg->webrtc           = FALSE;   /* leaks local addresses           */
  cfg->webaudio         = TRUE;
  cfg->media_autoplay   = FALSE;
  cfg->smooth_scrolling = TRUE;
  cfg->page_cache       = TRUE;
  cfg->developer_tools  = TRUE;
  cfg->spell_check      = FALSE;
  cfg->hw_accel         = LY_HW_AUTO;
  cfg->default_zoom     = 1.0;
  cfg->minimum_font_size = 0;

  cfg->cookie_policy         = LY_COOKIES_NO_THIRD_PARTY;
  cfg->clear_on_exit         = FALSE;
  cfg->itp                   = TRUE;
  cfg->gpc                   = TRUE;
  cfg->trim_referrer         = TRUE;
  cfg->fingerprint_defence   = TRUE;
  cfg->https_only            = FALSE;
  cfg->proxy_mode            = LY_PROXY_SYSTEM;
  cfg->proxy_url             = g_strdup ("");
  cfg->languages             = g_strdup ("");
  cfg->search_keywords       = g_ptr_array_new_with_free_func (g_free);
  cfg->save_passwords        = TRUE;
  cfg->password_autofill     = TRUE;
  cfg->password_never        = g_ptr_array_new_with_free_func (g_free);
  cfg->remember_history      = TRUE;
  cfg->ua_mode               = LY_UA_DEFAULT;
  cfg->ua_custom             = g_strdup ("");
  cfg->search_name           = g_strdup ("DuckDuckGo");
  cfg->search_url            = g_strdup ("https://duckduckgo.com/?q=%s");

  cfg->block_enabled = TRUE;
  for (int i = 0; i < LY_CAT_N; i++)
    cfg->block_cat[i] = TRUE;
  cfg->block_cat[LY_CAT_ANNOYANCES] = FALSE;  /* most likely to break sites */
  cfg->block_strict_third_party     = TRUE;
  cfg->block_hide_placeholders      = TRUE;
  cfg->block_exceptions = g_ptr_array_new_with_free_func (g_free);
  cfg->subscriptions    = g_ptr_array_new_with_free_func (g_free);

  for (int i = 0; i < LY_PERM_N; i++)
    cfg->perm[i] = LY_POLICY_ASK;
  cfg->perm[LY_PERM_NOTIFICATIONS] = LY_POLICY_DENY;
  cfg->perm[LY_PERM_DEVICE_INFO]   = LY_POLICY_DENY;
  cfg->perm[LY_PERM_DRM]           = LY_POLICY_DENY;
  cfg->perm[LY_PERM_XR]            = LY_POLICY_DENY;

  cfg->restore_session  = TRUE;
  cfg->show_home_button = FALSE;
  cfg->per_site_zoom    = TRUE;

  cfg->download_dir = g_strdup (g_get_user_special_dir (G_USER_DIRECTORY_DOWNLOAD)
                                  ?: g_get_home_dir ());

  cfg->homepage        = g_strdup ("lyndon:start");

  cfg->watchers = g_ptr_array_new_with_free_func (g_free);

  g_autofree char *dir = ly_config_dir ();
  cfg->path = g_build_filename (dir, "config.ini", NULL);

  return cfg;
}

void
ly_config_free (LyConfig *cfg)
{
  if (cfg == NULL)
    return;

  if (cfg->save_source != 0) {
    g_source_remove (cfg->save_source);
    cfg->save_source = 0;
    ly_config_save (cfg);
  }

  g_clear_pointer (&cfg->ua_custom, g_free);
  g_clear_pointer (&cfg->search_name, g_free);
  g_clear_pointer (&cfg->search_url, g_free);
  g_clear_pointer (&cfg->download_dir, g_free);
  g_clear_pointer (&cfg->homepage, g_free);
  g_clear_pointer (&cfg->path, g_free);
  g_clear_pointer (&cfg->block_exceptions, g_ptr_array_unref);
  g_clear_pointer (&cfg->password_never, g_ptr_array_unref);
  g_clear_pointer (&cfg->search_keywords, g_ptr_array_unref);
  g_clear_pointer (&cfg->proxy_url, g_free);
  g_clear_pointer (&cfg->languages, g_free);
  g_clear_pointer (&cfg->subscriptions, g_ptr_array_unref);
  g_clear_pointer (&cfg->watchers, g_ptr_array_unref);
  g_free (cfg);
}

/* ---------------------------------------------------------------- loading */

static void
load_string_list (GKeyFile *kf, const char *group, const char *key, GPtrArray *dest)
{
  gsize n = 0;
  g_auto (GStrv) values = g_key_file_get_string_list (kf, group, key, &n, NULL);
  if (values == NULL)
    return;

  g_ptr_array_set_size (dest, 0);
  for (gsize i = 0; i < n; i++) {
    g_autofree char *trimmed = g_strdup (values[i]);
    g_strstrip (trimmed);
    if (*trimmed != '\0')
      g_ptr_array_add (dest, g_steal_pointer (&trimmed));
  }
}

#define GET_BOOL(group, key, field) G_STMT_START {                       \
    GError *e = NULL;                                                    \
    gboolean v = g_key_file_get_boolean (kf, group, key, &e);            \
    if (e == NULL) cfg->field = v; else g_clear_error (&e);              \
  } G_STMT_END

#define GET_INT(group, key, field) G_STMT_START {                        \
    GError *e = NULL;                                                    \
    int v = g_key_file_get_integer (kf, group, key, &e);                 \
    if (e == NULL) cfg->field = v; else g_clear_error (&e);              \
  } G_STMT_END

#define GET_DOUBLE(group, key, field) G_STMT_START {                     \
    GError *e = NULL;                                                    \
    double v = g_key_file_get_double (kf, group, key, &e);               \
    if (e == NULL) cfg->field = v; else g_clear_error (&e);              \
  } G_STMT_END

#define GET_STR(group, key, field) G_STMT_START {                        \
    char *v = g_key_file_get_string (kf, group, key, NULL);              \
    if (v != NULL) { g_free (cfg->field); cfg->field = v; }              \
  } G_STMT_END

#define GET_ENUM(group, key, field, ids, type) G_STMT_START {            \
    g_autofree char *v = g_key_file_get_string (kf, group, key, NULL);   \
    cfg->field = (type) enum_from_id (v, ids, G_N_ELEMENTS (ids),        \
                                      (int) cfg->field);                 \
  } G_STMT_END

void
ly_config_load (LyConfig *cfg)
{
  g_autoptr (GKeyFile) kf = g_key_file_new ();

  if (!g_key_file_load_from_file (kf, cfg->path, G_KEY_FILE_NONE, NULL))
    return;   /* first run — defaults stand */

  GET_ENUM   ("appearance", "color-scheme",  scheme,  SCHEME_IDS, LyColorScheme);
  GET_ENUM   ("appearance", "effects",       effects, FX_IDS,     LyEffects);
  GET_BOOL   ("appearance", "compact-chrome", compact_chrome);
  GET_BOOL   ("appearance", "tab-bar-when-single", show_tab_bar_single);
  GET_BOOL   ("appearance", "bookmarks-bar",       show_bookmarks_bar);
  GET_DOUBLE ("appearance", "opacity",       ui_opacity);

  GET_ENUM   ("web", "force-dark",         force_dark, DARK_IDS, LyForceDark);
  GET_BOOL   ("web", "javascript",         javascript);
  GET_BOOL   ("web", "images",             images);
  GET_BOOL   ("web", "webgl",              webgl);
  GET_BOOL   ("web", "webrtc",             webrtc);
  GET_BOOL   ("web", "webaudio",           webaudio);
  GET_BOOL   ("web", "media-autoplay",     media_autoplay);
  GET_BOOL   ("web", "smooth-scrolling",   smooth_scrolling);
  GET_BOOL   ("web", "page-cache",         page_cache);
  GET_BOOL   ("web", "developer-tools",    developer_tools);
  GET_BOOL   ("web", "spell-check",        spell_check);
  GET_ENUM   ("web", "hardware-acceleration", hw_accel, HW_IDS, LyHwAccel);
  GET_DOUBLE ("web", "default-zoom",       default_zoom);
  GET_INT    ("web", "minimum-font-size",  minimum_font_size);

  GET_ENUM ("privacy", "cookies", cookie_policy, COOKIE_IDS, LyCookiePolicy);
  GET_BOOL ("privacy", "clear-on-exit",       clear_on_exit);
  GET_BOOL ("privacy", "tracking-prevention", itp);
  GET_BOOL ("privacy", "global-privacy-control", gpc);
  GET_BOOL ("privacy", "trim-referrer",       trim_referrer);
  GET_BOOL ("privacy", "fingerprint-defence", fingerprint_defence);
  GET_BOOL ("privacy", "https-only",          https_only);
  GET_ENUM ("privacy", "proxy", proxy_mode, PROXY_IDS, LyProxyMode);
  GET_STR  ("privacy", "proxy-url",  proxy_url);
  GET_STR  ("privacy", "languages",  languages);
  load_string_list (kf, "privacy", "search-keywords", cfg->search_keywords);
  GET_BOOL ("privacy", "save-passwords",      save_passwords);
  GET_BOOL ("privacy", "password-autofill",   password_autofill);
  GET_BOOL ("privacy", "remember-history",    remember_history);
  load_string_list (kf, "privacy", "password-never", cfg->password_never);
  GET_ENUM ("privacy", "user-agent", ua_mode, UA_IDS, LyUaMode);
  GET_STR  ("privacy", "user-agent-string", ua_custom);
  GET_STR  ("privacy", "search-name", search_name);
  GET_STR  ("privacy", "search-url",  search_url);

  GET_BOOL ("blocker", "enabled",            block_enabled);
  GET_BOOL ("blocker", "strict-third-party", block_strict_third_party);
  GET_BOOL ("blocker", "hide-placeholders",  block_hide_placeholders);
  for (int i = 0; i < LY_CAT_N; i++) {
    GError *e = NULL;
    gboolean v = g_key_file_get_boolean (kf, "blocker", CATS[i].id, &e);
    if (e == NULL) cfg->block_cat[i] = v; else g_clear_error (&e);
  }
  load_string_list (kf, "blocker", "exceptions",    cfg->block_exceptions);
  load_string_list (kf, "blocker", "subscriptions", cfg->subscriptions);

  for (int i = 0; i < LY_PERM_N; i++) {
    g_autofree char *v = g_key_file_get_string (kf, "permissions", PERMS[i].id, NULL);
    cfg->perm[i] = (LyPolicy) enum_from_id (v, POLICY_IDS, G_N_ELEMENTS (POLICY_IDS),
                                            (int) cfg->perm[i]);
  }

  GET_STR  ("downloads", "directory", download_dir);

  GET_BOOL ("session", "restore",          restore_session);
  GET_BOOL ("session", "show-home-button", show_home_button);
  GET_BOOL ("session", "per-site-zoom",    per_site_zoom);
  GET_STR  ("session", "homepage", homepage);

  /* Clamp anything a hand-edited file could have put out of range. */
  cfg->ui_opacity   = CLAMP (cfg->ui_opacity, 0.35, 1.0);
  cfg->default_zoom = CLAMP (cfg->default_zoom, 0.3, 5.0);
  cfg->minimum_font_size = CLAMP (cfg->minimum_font_size, 0, 32);
}

/* ---------------------------------------------------------------- saving */

void
ly_config_save (LyConfig *cfg)
{
  g_autoptr (GKeyFile) kf = g_key_file_new ();

  g_key_file_set_string  (kf, "appearance", "color-scheme", SCHEME_IDS[cfg->scheme]);
  g_key_file_set_string  (kf, "appearance", "effects",      FX_IDS[cfg->effects]);
  g_key_file_set_boolean (kf, "appearance", "compact-chrome", cfg->compact_chrome);
  g_key_file_set_boolean (kf, "appearance", "tab-bar-when-single", cfg->show_tab_bar_single);
  g_key_file_set_boolean (kf, "appearance", "bookmarks-bar",       cfg->show_bookmarks_bar);
  g_key_file_set_double  (kf, "appearance", "opacity",      cfg->ui_opacity);

  g_key_file_set_string  (kf, "web", "force-dark",       DARK_IDS[cfg->force_dark]);
  g_key_file_set_boolean (kf, "web", "javascript",       cfg->javascript);
  g_key_file_set_boolean (kf, "web", "images",           cfg->images);
  g_key_file_set_boolean (kf, "web", "webgl",            cfg->webgl);
  g_key_file_set_boolean (kf, "web", "webrtc",           cfg->webrtc);
  g_key_file_set_boolean (kf, "web", "webaudio",         cfg->webaudio);
  g_key_file_set_boolean (kf, "web", "media-autoplay",   cfg->media_autoplay);
  g_key_file_set_boolean (kf, "web", "smooth-scrolling", cfg->smooth_scrolling);
  g_key_file_set_boolean (kf, "web", "page-cache",       cfg->page_cache);
  g_key_file_set_boolean (kf, "web", "developer-tools",  cfg->developer_tools);
  g_key_file_set_boolean (kf, "web", "spell-check",      cfg->spell_check);
  g_key_file_set_string  (kf, "web", "hardware-acceleration", HW_IDS[cfg->hw_accel]);
  g_key_file_set_double  (kf, "web", "default-zoom",     cfg->default_zoom);
  g_key_file_set_integer (kf, "web", "minimum-font-size", cfg->minimum_font_size);

  g_key_file_set_string  (kf, "privacy", "cookies", COOKIE_IDS[cfg->cookie_policy]);
  g_key_file_set_boolean (kf, "privacy", "clear-on-exit",       cfg->clear_on_exit);
  g_key_file_set_boolean (kf, "privacy", "tracking-prevention", cfg->itp);
  g_key_file_set_boolean (kf, "privacy", "global-privacy-control", cfg->gpc);
  g_key_file_set_boolean (kf, "privacy", "trim-referrer",       cfg->trim_referrer);
  g_key_file_set_boolean (kf, "privacy", "fingerprint-defence", cfg->fingerprint_defence);
  g_key_file_set_boolean (kf, "privacy", "https-only",          cfg->https_only);
  g_key_file_set_string  (kf, "privacy", "proxy", PROXY_IDS[cfg->proxy_mode]);
  g_key_file_set_string  (kf, "privacy", "proxy-url", cfg->proxy_url ?: "");
  g_key_file_set_string  (kf, "privacy", "languages", cfg->languages ?: "");
  g_key_file_set_string_list (kf, "privacy", "search-keywords",
                              (const char * const *) cfg->search_keywords->pdata,
                              cfg->search_keywords->len);
  g_key_file_set_boolean (kf, "privacy", "save-passwords",      cfg->save_passwords);
  g_key_file_set_boolean (kf, "privacy", "password-autofill",   cfg->password_autofill);
  g_key_file_set_boolean (kf, "privacy", "remember-history",    cfg->remember_history);
  g_key_file_set_string_list (kf, "privacy", "password-never",
                              (const char * const *) cfg->password_never->pdata,
                              cfg->password_never->len);
  g_key_file_set_string  (kf, "privacy", "user-agent", UA_IDS[cfg->ua_mode]);
  g_key_file_set_string  (kf, "privacy", "user-agent-string", cfg->ua_custom ?: "");
  g_key_file_set_string  (kf, "privacy", "search-name", cfg->search_name ?: "");
  g_key_file_set_string  (kf, "privacy", "search-url",  cfg->search_url ?: "");

  g_key_file_set_boolean (kf, "blocker", "enabled",            cfg->block_enabled);
  g_key_file_set_boolean (kf, "blocker", "strict-third-party", cfg->block_strict_third_party);
  g_key_file_set_boolean (kf, "blocker", "hide-placeholders",  cfg->block_hide_placeholders);
  for (int i = 0; i < LY_CAT_N; i++)
    g_key_file_set_boolean (kf, "blocker", CATS[i].id, cfg->block_cat[i]);
  g_key_file_set_string_list (kf, "blocker", "exceptions",
                              (const char * const *) cfg->block_exceptions->pdata,
                              cfg->block_exceptions->len);
  g_key_file_set_string_list (kf, "blocker", "subscriptions",
                              (const char * const *) cfg->subscriptions->pdata,
                              cfg->subscriptions->len);

  for (int i = 0; i < LY_PERM_N; i++)
    g_key_file_set_string (kf, "permissions", PERMS[i].id, POLICY_IDS[cfg->perm[i]]);

  g_key_file_set_string  (kf, "downloads", "directory", cfg->download_dir ?: "");

  g_key_file_set_boolean (kf, "session", "restore",          cfg->restore_session);
  g_key_file_set_boolean (kf, "session", "show-home-button", cfg->show_home_button);
  g_key_file_set_boolean (kf, "session", "per-site-zoom",    cfg->per_site_zoom);
  g_key_file_set_string  (kf, "session", "homepage", cfg->homepage ?: "");

  g_autofree char *dir = g_path_get_dirname (cfg->path);
  g_mkdir_with_parents (dir, 0700);

  g_autoptr (GError) error = NULL;
  if (!g_key_file_save_to_file (kf, cfg->path, &error))
    g_warning ("could not write %s: %s", cfg->path, error->message);
}

static gboolean
save_timeout (gpointer data)
{
  LyConfig *cfg = data;
  cfg->save_source = 0;
  ly_config_save (cfg);
  return G_SOURCE_REMOVE;
}

void
ly_config_queue_save (LyConfig *cfg)
{
  if (cfg->save_source != 0)
    g_source_remove (cfg->save_source);
  cfg->save_source = g_timeout_add_seconds (2, save_timeout, cfg);
}

/* --------------------------------------------------------------- watchers */

void
ly_config_touch (LyConfig *cfg)
{
  /* Copy first: a watcher is allowed to add or drop watchers while running. */
  guint n = cfg->watchers->len;
  g_autofree LyConfigWatch *snapshot = g_new0 (LyConfigWatch, n ? n : 1);
  for (guint i = 0; i < n; i++)
    snapshot[i] = *(LyConfigWatch *) g_ptr_array_index (cfg->watchers, i);

  for (guint i = 0; i < n; i++)
    snapshot[i].fn (cfg, snapshot[i].data);

  ly_config_queue_save (cfg);
}

guint
ly_config_watch (LyConfig *cfg, LyConfigChangedFn fn, gpointer data)
{
  static guint next_id = 1;
  LyConfigWatch *w = g_new0 (LyConfigWatch, 1);
  w->id   = next_id++;
  w->fn   = fn;
  w->data = data;
  g_ptr_array_add (cfg->watchers, w);
  return w->id;
}

void
ly_config_unwatch (LyConfig *cfg, guint id)
{
  for (guint i = 0; i < cfg->watchers->len; i++) {
    LyConfigWatch *w = g_ptr_array_index (cfg->watchers, i);
    if (w->id == id) {
      g_ptr_array_remove_index_fast (cfg->watchers, i);
      return;
    }
  }
}

/* -------------------------------------------------------- search keywords */

char *
ly_config_resolve_input (LyConfig *cfg, const char *text)
{
  if (text == NULL)
    return NULL;

  g_autofree char *trimmed = g_strdup (text);
  g_strstrip (trimmed);

  /* "kw rest of the query" — the space is what distinguishes a keyword from
   * someone typing a hostname that happens to start with the same letters. */
  const char *space = strchr (trimmed, ' ');
  if (space != NULL && space != trimmed) {
    g_autofree char *keyword = g_strndup (trimmed, (size_t) (space - trimmed));
    const char *rest = space + 1;

    for (guint i = 0; i < cfg->search_keywords->len; i++) {
      const char *entry = g_ptr_array_index (cfg->search_keywords, i);
      const char *equals = strchr (entry, '=');
      if (equals == NULL)
        continue;

      g_autofree char *name = g_strndup (entry, (size_t) (equals - entry));
      g_strstrip (name);
      if (g_ascii_strcasecmp (name, keyword) != 0)
        continue;

      return ly_normalise_input (rest, equals + 1);
    }
  }

  return ly_normalise_input (trimmed, cfg->search_url);
}

/* ------------------------------------------------------------- exceptions */

gboolean
ly_config_host_excepted (LyConfig *cfg, const char *host)
{
  if (host == NULL)
    return FALSE;

  for (guint i = 0; i < cfg->block_exceptions->len; i++) {
    const char *entry = g_ptr_array_index (cfg->block_exceptions, i);
    if (g_ascii_strcasecmp (entry, host) == 0)
      return TRUE;
    /* An exception for example.com also covers cdn.example.com. */
    size_t elen = strlen (entry), hlen = strlen (host);
    if (hlen > elen + 1 &&
        host[hlen - elen - 1] == '.' &&
        g_ascii_strcasecmp (host + hlen - elen, entry) == 0)
      return TRUE;
  }
  return FALSE;
}

void
ly_config_set_host_except (LyConfig *cfg, const char *host, gboolean excepted)
{
  if (host == NULL || *host == '\0')
    return;

  for (guint i = 0; i < cfg->block_exceptions->len; i++) {
    if (g_ascii_strcasecmp (g_ptr_array_index (cfg->block_exceptions, i), host) == 0) {
      if (!excepted)
        g_ptr_array_remove_index (cfg->block_exceptions, i);
      return;
    }
  }
  if (excepted)
    g_ptr_array_add (cfg->block_exceptions, g_strdup (host));
}
