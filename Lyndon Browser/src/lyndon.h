/* lyndon.h — shared types for the Lyndon browser.
 *
 * Lyndon is deliberately small: one process for the UI, one shared WebKit
 * network session, and a plain C configuration struct instead of GSettings so
 * that startup costs nothing but a single GKeyFile parse.
 *
 * The toolkit is behind _WIN32 because there is no WebKitGTK for Windows —
 * the port there is Win32 and WebView2, in win32/. Everything below this
 * include block is the same on both, which is what lets config.c, util.c,
 * store.c, abp.c and import.c compile unchanged for either build.
 */
#pragma once

#ifdef _WIN32
# include <glib.h>
# include <gio/gio.h>
#else
# include <adwaita.h>
# include <gtk/gtk.h>
# include <webkit/webkit.h>
#endif

/* The house palettes, generated from house/theme.py by house/sync.py. The
 * same eight themes the Python apps here use, so that Porcelain in the
 * browser is the same Porcelain as in the terminal and the file manager. */
#include "house-themes.h"

G_BEGIN_DECLS

/* Whichever of the generated themes `key` names, or NULL for one this build
 * does not know. Defined in config.c. */
const LyHouseTheme *ly_house_theme (const char *key);

/* ------------------------------------------------------------------ enums */

typedef enum {
  LY_SCHEME_SYSTEM = 0,
  LY_SCHEME_LIGHT,
  LY_SCHEME_DARK,
} LyColorScheme;

/* How hard we push dark mode onto page content. SMART only inverts pages that
 * actually painted themselves light, so sites with a real dark theme are left
 * alone. */
typedef enum {
  LY_DARK_OFF = 0,
  LY_DARK_SMART,
  LY_DARK_ALWAYS,
} LyForceDark;

typedef enum {
  LY_COOKIES_NONE = 0,
  LY_COOKIES_NO_THIRD_PARTY,
  LY_COOKIES_ALL,
} LyCookiePolicy;

typedef enum {
  LY_POLICY_ASK = 0,
  LY_POLICY_ALLOW,
  LY_POLICY_DENY,
} LyPolicy;

typedef enum {
  LY_HW_AUTO = 0,
  LY_HW_ALWAYS,
  LY_HW_NEVER,
} LyHwAccel;

typedef enum {
  LY_PROXY_SYSTEM = 0,
  LY_PROXY_NONE,
  LY_PROXY_CUSTOM,
} LyProxyMode;

typedef enum {
  LY_UA_DEFAULT = 0,
  LY_UA_MINIMAL,   /* trimmed, low-entropy UA string */
  LY_UA_CUSTOM,
} LyUaMode;

/* Visual effect budget. Each step down removes GPU work. */
typedef enum {
  LY_FX_FULL = 0,   /* translucency + shadows + animation */
  LY_FX_REDUCED,    /* opaque, shadows, animation */
  LY_FX_OFF,        /* opaque, flat, no transitions */
} LyEffects;

/* What the built-in start page draws. The artwork below is orthogonal to this:
 * BLANK with an artwork chosen is a wallpaper and nothing else. */
typedef enum {
  LY_START_FULL = 0,   /* wordmark, tagline, settings summary, keyboard hint */
  LY_START_MINIMAL,    /* the word "Lyndon", nothing else                    */
  LY_START_BLANK,      /* no content at all                                  */
} LyStartPage;

/* Built-in start-page backgrounds. Every one is drawn from CSS gradients, or
 * from the app glyph the page already carries, so the whole set adds no
 * bitmap to the binary and nothing to decode at load. */
typedef enum {
  LY_ART_NONE = 0,
  LY_ART_AURORA,
  LY_ART_MERIDIAN,
  LY_ART_DUNE,
  LY_ART_LATTICE,
  LY_ART_EMBER,
  LY_ART_MONOLITH,
  LY_ART_N,
} LyStartArt;

typedef enum {
  LY_PERM_GEOLOCATION = 0,
  LY_PERM_CAMERA,
  LY_PERM_MICROPHONE,
  LY_PERM_DISPLAY_CAPTURE,
  LY_PERM_NOTIFICATIONS,
  LY_PERM_CLIPBOARD,
  LY_PERM_POINTER_LOCK,
  LY_PERM_DRM,
  LY_PERM_DEVICE_INFO,
  LY_PERM_XR,
  LY_PERM_N,
} LyPermKind;

typedef enum {
  LY_CAT_ADS = 0,
  LY_CAT_TRACKERS,
  LY_CAT_ANALYTICS,
  LY_CAT_SOCIAL,
  LY_CAT_ANNOYANCES,
  LY_CAT_COOKIE_NOTICES,
  LY_CAT_CRYPTOMINING,
  LY_CAT_N,
} LyBlockCat;

const char *ly_perm_id     (LyPermKind kind);
const char *ly_perm_label  (LyPermKind kind);
const char *ly_cat_id      (LyBlockCat cat);
const char *ly_cat_label   (LyBlockCat cat);
const char *ly_cat_summary (LyBlockCat cat);
const char *ly_start_page_id (LyStartPage page);
const char *ly_start_art_id  (LyStartArt art);

/* ----------------------------------------------------------------- config */

typedef struct _LyConfig LyConfig;

struct _LyConfig {
  /* appearance */
  LyColorScheme scheme;
  LyEffects     effects;
  gboolean      compact_chrome;
  gboolean      show_tab_bar_single;   /* keep the tab strip with one tab open */
  gboolean      show_bookmarks_bar;
  double        ui_opacity;            /* 0.55 – 1.0, only used when FX_FULL   */

  /* web content */
  LyForceDark   force_dark;
  gboolean      javascript;
  gboolean      images;
  gboolean      webgl;
  gboolean      webrtc;
  gboolean      webaudio;
  gboolean      media_autoplay;
  gboolean      smooth_scrolling;
  gboolean      page_cache;
  /* One web process per site rather than per tab. WebKit spawns a whole
   * WebCore and JavaScriptCore for every view otherwise, which is where a
   * browser with a few tabs open spends most of its memory. */
  gboolean      share_site_process;
  int           unload_after;          /* minutes idle before a background tab
                                        * is dropped; 0 keeps them all loaded */
  gboolean      developer_tools;
  gboolean      spell_check;
  LyHwAccel     hw_accel;
  double        default_zoom;
  int           minimum_font_size;

  /* privacy */
  LyCookiePolicy cookie_policy;
  gboolean       clear_on_exit;
  gboolean       itp;                  /* intelligent tracking prevention     */
  gboolean       gpc;                  /* Global Privacy Control + DNT        */
  gboolean       trim_referrer;
  gboolean       fingerprint_defence;
  gboolean       https_only;          /* upgrade http:// to https://        */
  LyProxyMode    proxy_mode;
  char          *proxy_url;           /* e.g. http://127.0.0.1:8080         */
  char          *languages;           /* Accept-Language, comma separated   */
  GPtrArray     *search_keywords;     /* char* "w=https://…?q=%s"           */
  gboolean       save_passwords;    /* offer to save form logins       */
  gboolean       password_autofill;
  gboolean       password_suggest;    /* offer a generated one on sign-up */
  /* Off until it is asked for: checking a password against a breach corpus
   * means reaching a service that is not ours. Five characters of a hash is
   * all that goes, and nothing goes at all until this is on. */
  gboolean       password_leak_check;
  GPtrArray     *password_never;     /* char* origin — never ask here    */
  gboolean       remember_history;
  LyUaMode       ua_mode;
  char          *ua_custom;
  char          *search_name;
  char          *search_url;           /* contains %s */

  /* blocker */
  gboolean  block_enabled;
  gboolean  block_cat[LY_CAT_N];
  gboolean  block_strict_third_party;  /* also drop cookies for 3rd parties   */
  gboolean  block_hide_placeholders;   /* cosmetic filtering                  */
  GPtrArray *block_exceptions;         /* char* host — blocker off for these  */
  GPtrArray *subscriptions;            /* char* URL of extra filter lists     */

  /* the house style — see house-themes.h. `scheme` above decides which of
   * the two is in force: FORCE_LIGHT and FORCE_DARK pick one outright, and
   * SYSTEM follows the desktop between them. */
  char     *dark_theme;
  char     *light_theme;

  /* permissions */
  LyPolicy perm[LY_PERM_N];

  /* downloads */
  char     *download_dir;

  /* offline archives — whole sites kept on disk, see archive.h */
  gboolean   offline_enabled;
  char      *offline_dir;            /* default ~/Documents/Websites       */
  gboolean   offline_ask_where;      /* choose a folder on every save      */
  gboolean   offline_automatic;      /* sweep recently-read sites          */
  int        offline_days;           /* "read in the last X days"          */
  int        offline_min_visits;     /* before a site counts as one of yours */
  GPtrArray *offline_allow;          /* char* host — empty means "any"     */
  GPtrArray *offline_block;          /* char* host — never these           */
  int        offline_max_pages;
  int        offline_max_depth;
  int        offline_max_mb;

  /* session and startup */
  gboolean  restore_session;
  gboolean  show_home_button;
  gboolean  per_site_zoom;
  char     *homepage;
  LyStartPage start_page;          /* what lyndon:start draws            */
  LyStartArt  start_art;           /* and what it draws it on            */
  /* Shape of the last window to be closed, so the next one comes back the
   * same size instead of the built-in default. */
  int       window_width;
  int       window_height;
  gboolean  window_maximized;

  /* -- runtime only -------------------------------------------------- */
  char     *path;
  guint     save_source;
  GPtrArray *watchers;                 /* LyConfigWatch* */
};

typedef void (*LyConfigChangedFn) (LyConfig *cfg, gpointer user_data);

LyConfig *ly_config_new         (void);
void      ly_config_free        (LyConfig *cfg);
void      ly_config_load        (LyConfig *cfg);
void      ly_config_save        (LyConfig *cfg);
void      ly_config_queue_save  (LyConfig *cfg);
/* Notify watchers that something changed and schedule a debounced save. */
void      ly_config_touch       (LyConfig *cfg);
guint     ly_config_watch       (LyConfig *cfg, LyConfigChangedFn fn, gpointer data);
void      ly_config_unwatch     (LyConfig *cfg, guint id);

G_DEFINE_AUTOPTR_CLEANUP_FUNC (LyConfig, ly_config_free)

gboolean  ly_config_host_excepted   (LyConfig *cfg, const char *host);
void      ly_config_set_host_except (LyConfig *cfg, const char *host, gboolean excepted);

char     *ly_config_dir  (void);
char     *ly_data_dir    (void);
char     *ly_cache_dir   (void);

/* ------------------------------------------------------------------- util */

char     *ly_normalise_input (const char *text, const char *search_url);
/* Like ly_normalise_input, but first honours a "keyword rest of query"
 * shortcut from [privacy] search-keywords. */
char     *ly_config_resolve_input (LyConfig *cfg, const char *text);
/* A command-line argument that names a file, as the file:// URI to load it
 * from; NULL when it names none, and is an address or a search instead.
 * Explorer opens a page by handing over "C:\…\page.html", which is neither —
 * the GTK build never sees one, as GApplication turns arguments into GFiles.
 * `cwd` is what a relative path is relative to; NULL is this process's. */
char     *ly_uri_from_path   (const char *arg, const char *cwd);
gboolean  ly_looks_like_url  (const char *text);
char     *ly_uri_host        (const char *uri);
char     *ly_uri_base_domain (const char *uri);   /* eTLD+1, approximated     */
char     *ly_pretty_uri      (const char *uri);
gboolean  ly_uri_is_secure   (const char *uri);
gboolean  ly_uri_is_internal (const char *uri);
char     *ly_format_size     (guint64 bytes);
char     *ly_escape_js_string(const char *s);

/* --------------------------------------------------------------- forwards */

typedef struct _LyBlocker LyBlocker;
typedef struct _LyStore   LyStore;

#ifdef _WIN32
/* No GObjects in the Windows build: the window and the tab are plain structs
 * around an HWND and an ICoreWebView2. See win32/chrome.h and win32/tab.h. */
typedef struct _LyWindow LyWindow;
typedef struct _LyTab    LyTab;
#else
# define LY_TYPE_APP    (ly_app_get_type ())
# define LY_TYPE_WINDOW (ly_window_get_type ())
# define LY_TYPE_TAB    (ly_tab_get_type ())

G_DECLARE_FINAL_TYPE (LyApp,    ly_app,    LY, APP,    AdwApplication)
G_DECLARE_FINAL_TYPE (LyWindow, ly_window, LY, WINDOW, AdwApplicationWindow)
G_DECLARE_FINAL_TYPE (LyTab,    ly_tab,    LY, TAB,    GtkWidget)
#endif

G_END_DECLS
