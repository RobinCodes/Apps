/* prefs.c — see prefs.h. */

#include "prefs.h"
#include "ui.h"
#include "import.h"
#include "pwfile.h"

#include <windowsx.h>
#include <shlobj.h>
#include <shellapi.h>   /* ShellExecuteW */
#include <commdlg.h>    /* GetOpenFileNameW, GetSaveFileNameW */
#include <string.h>
#include <wchar.h>

#define PREFS_CLASS  L"LyndonPrefs"
#define ID_EDIT      3001

/* Design sizes at 96 dpi. */
#define SIDEBAR_W    172
#define ROW_H         56
#define GROUP_H       38
#define PAD           16
#define SWITCH_W      44
#define SWITCH_H      24

/* ------------------------------------------------------------- row model */

typedef enum {
  ROW_TOGGLE,     /* gboolean          */
  ROW_CHOICE,     /* int, from choices */
  ROW_TEXT,       /* char*             */
  ROW_NUMBER,     /* double or int     */
  ROW_ACTION,     /* a button          */
  ROW_PAIR,       /* two buttons; the Linux rows with an edit and a bin */
  ROW_NOTE,       /* explanatory text  */
  ROW_GROUP,      /* a heading         */
} RowKind;

typedef struct _Page Page;
struct _LyPrefs;

typedef void (*RowAction) (struct _LyPrefs *prefs, int index);

typedef struct {
  RowKind      kind;
  const char  *title;
  const char  *subtitle;
  size_t       offset;        /* byte offset into LyConfig, or 0 */
  const char *const *choices; /* NULL-terminated, for ROW_CHOICE */
  double       min, max, step;
  gboolean     integer;       /* ROW_NUMBER writes an int, not a double */
  RowAction    action;
  int          tag;           /* free for the action to use */
  /* Not every setting can be honoured by WebView2; those say so rather than
   * pretending, and are drawn dimmed. */
  gboolean     linux_only;
  /* Most rows are a field of LyConfig, named by `offset`. A few are not — the
   * import switches and the login editor are the page's own state rather than
   * a setting — and those point straight at their own storage instead, and
   * are not written back to the config file. */
  void        *bind;
  gboolean     secret;        /* ROW_TEXT: shown and edited as dots */
  /* ROW_PAIR only: the second button, drawn to the left of the first. */
  const char  *second_label;
  RowAction    second;
  const char  *first_label;   /* ROW_ACTION and ROW_PAIR; "Go" when NULL */
} Row;

/* ------------------------------------------------------------ the choices */

static const char *const SCHEME[]   = { "Follow the system", "Light", "Dark", NULL };
static const char *const DARKNESS[] = { "Never", "Only pages that are light", "Always", NULL };
static const char *const COOKIES[]  = { "Block all", "Block third-party", "Allow all", NULL };
static const char *const POLICY[]   = { "Ask each time", "Always allow", "Always block", NULL };
static const char *const EFFECTS[]  = { "Full", "Reduced", "Off", NULL };
static const char *const HWACCEL[]  = { "Automatic", "Always", "Never", NULL };
static const char *const PROXY[]    = { "System", "None", "Custom", NULL };
static const char *const UAMODE[]   = { "Default", "Minimal", "Custom", NULL };

#define OFFSET(field) offsetof (LyConfig, field)

/* ------------------------------------------------------------- the pages */

static void action_clear_history (LyPrefs *p, int index);
static void action_clear_cookies (LyPrefs *p, int index);
static void action_choose_downloads (LyPrefs *p, int index);
static void action_import (LyPrefs *p, int index);
static void action_forget_password (LyPrefs *p, int index);
static void action_unblock_origin (LyPrefs *p, int index);
static void action_open_rules (LyPrefs *p, int index);
static void action_add_login (LyPrefs *p, int index);
static void action_edit_login (LyPrefs *p, int index);
static void action_editor_save (LyPrefs *p, int index);
static void action_editor_copy (LyPrefs *p, int index);
static void action_editor_cancel (LyPrefs *p, int index);
static void action_import_file (LyPrefs *p, int index);
static void action_export_file (LyPrefs *p, int index);
static void action_show_import (LyPrefs *p, int index);

static const Row APPEARANCE[] = {
  { ROW_GROUP,  "Window", NULL },
  { ROW_CHOICE, "Colour scheme", "Light, dark, or whatever Windows is doing",
    OFFSET (scheme), SCHEME },
  { ROW_CHOICE, "Visual effects", "Each step down removes GPU work",
    OFFSET (effects), EFFECTS },
  { ROW_TOGGLE, "Compact chrome", "A shorter toolbar and tab strip",
    OFFSET (compact_chrome) },
  { ROW_TOGGLE, "Keep the tab strip with one tab open", NULL,
    OFFSET (show_tab_bar_single) },
  { ROW_TOGGLE, "Show the bookmarks bar", NULL, OFFSET (show_bookmarks_bar) },
  { ROW_TOGGLE, "Show the home button", NULL, OFFSET (show_home_button) },

  { ROW_GROUP,  "Start-up", NULL },
  { ROW_TEXT,   "Homepage", "Where a new tab and the home button go",
    OFFSET (homepage) },
  { ROW_TOGGLE, "Reopen the last session", "Restore the tabs that were open on exit",
    OFFSET (restore_session) },
};

static const Row WEB[] = {
  { ROW_GROUP,  "Content", NULL },
  { ROW_TOGGLE, "JavaScript", NULL, OFFSET (javascript) },
  { ROW_CHOICE, "Force dark pages", "Only inverts pages that painted themselves light",
    OFFSET (force_dark), DARKNESS },
  { ROW_TOGGLE, "Autoplay media", "Applies to tabs opened after a restart",
    OFFSET (media_autoplay) },
  { ROW_TOGGLE, "Smooth scrolling", NULL, OFFSET (smooth_scrolling) },
  { ROW_TOGGLE, "Spell checking", NULL, OFFSET (spell_check) },

  { ROW_GROUP,  "Zoom and text", NULL },
  { ROW_NUMBER, "Default zoom", "Per cent", OFFSET (default_zoom),
    NULL, 50, 300, 10 },
  { ROW_TOGGLE, "Remember zoom per site", NULL, OFFSET (per_site_zoom) },
  { ROW_NUMBER, "Minimum font size", "Pixels; 0 leaves it to the page",
    OFFSET (minimum_font_size), NULL, 0, 32, 1, TRUE },

  { ROW_GROUP,  "Developer", NULL },
  { ROW_TOGGLE, "Developer tools", "F12, and Inspect in the context menu",
    OFFSET (developer_tools) },

  { ROW_GROUP,  "Not used by this build", NULL },
  { ROW_TOGGLE, "WebGL", "WebView2 has no switch for this", OFFSET (webgl),
    NULL, 0, 0, 0, FALSE, NULL, 0, TRUE },
  { ROW_TOGGLE, "WebRTC", "WebView2 has no switch for this", OFFSET (webrtc),
    NULL, 0, 0, 0, FALSE, NULL, 0, TRUE },
  { ROW_CHOICE, "Hardware acceleration", "Chosen by Edge, not by Lyndon",
    OFFSET (hw_accel), HWACCEL, 0, 0, 0, FALSE, NULL, 0, TRUE },
};

static const Row PRIVACY[] = {
  { ROW_GROUP,  "Tracking", NULL },
  { ROW_CHOICE, "Cookies", NULL, OFFSET (cookie_policy), COOKIES },
  { ROW_TOGGLE, "Global Privacy Control and Do Not Track", NULL, OFFSET (gpc) },
  { ROW_TOGGLE, "Trim the referrer", "Send the origin, not the whole URL",
    OFFSET (trim_referrer) },
  { ROW_TOGGLE, "Upgrade http:// to https://", NULL, OFFSET (https_only) },

  { ROW_GROUP,  "History", NULL },
  { ROW_TOGGLE, "Remember history", NULL, OFFSET (remember_history) },
  { ROW_TOGGLE, "Clear on exit", "Cookies and cache, every time Lyndon closes",
    OFFSET (clear_on_exit) },
  { ROW_ACTION, "Clear history now", NULL, 0, NULL, 0, 0, 0, FALSE,
    action_clear_history },
  { ROW_ACTION, "Clear cookies and cache now", "Takes effect on the next start",
    0, NULL, 0, 0, 0, FALSE, action_clear_cookies },

  { ROW_GROUP,  "Search", NULL },
  { ROW_TEXT,   "Search engine name", NULL, OFFSET (search_name) },
  { ROW_TEXT,   "Search URL", "%s is replaced with the query", OFFSET (search_url) },

  { ROW_GROUP,  "Downloads", NULL },
  { ROW_TEXT,   "Download folder", "Empty means the Windows Downloads folder",
    OFFSET (download_dir) },
  { ROW_ACTION, "Choose a folder…", NULL, 0, NULL, 0, 0, 0, FALSE,
    action_choose_downloads },

  { ROW_GROUP,  "Identity", NULL },
  { ROW_CHOICE, "User agent", NULL, OFFSET (ua_mode), UAMODE },
  { ROW_TEXT,   "Custom user agent", "Used when the mode above is Custom",
    OFFSET (ua_custom) },
  { ROW_TEXT,   "Languages", "Accept-Language, comma separated",
    OFFSET (languages) },

  { ROW_GROUP,  "Not used by this build", NULL },
  { ROW_CHOICE, "Proxy", "Edge follows the Windows proxy settings",
    OFFSET (proxy_mode), PROXY, 0, 0, 0, FALSE, NULL, 0, TRUE },
  { ROW_TOGGLE, "Fingerprint defence", "WebKit-specific", OFFSET (fingerprint_defence),
    NULL, 0, 0, 0, FALSE, NULL, 0, TRUE },
  { ROW_TOGGLE, "Intelligent tracking prevention", "WebKit-specific", OFFSET (itp),
    NULL, 0, 0, 0, FALSE, NULL, 0, TRUE },
};

static const Row BLOCKING[] = {
  { ROW_GROUP,  "Blocking", NULL },
  { ROW_TOGGLE, "Block ads and trackers", NULL, OFFSET (block_enabled) },
  { ROW_TOGGLE, "Hide the space they left", "Element hiding, applied as CSS",
    OFFSET (block_hide_placeholders) },
  { ROW_TOGGLE, "Drop third-party cookies on blocked hosts", NULL,
    OFFSET (block_strict_third_party) },

  { ROW_GROUP,  "Lists", NULL },
  /* The seven categories are generated: see build_blocking_page(). */
  { ROW_ACTION, "Edit my own rules…", "Adblock syntax, one rule per line",
    0, NULL, 0, 0, 0, FALSE, action_open_rules },
};

static const Row PASSWORDS_TOP[] = {
  { ROW_GROUP,  "Passwords", NULL },
  { ROW_TOGGLE, "Offer to save logins", NULL, OFFSET (save_passwords) },
  { ROW_TOGGLE, "Fill them in automatically", NULL, OFFSET (password_autofill) },
  { ROW_NOTE,   "Saved in Windows Credential Manager",
    "Lyndon stores no passwords of its own. They are encrypted for your "
    "Windows account and can be reviewed in Control Panel." },
};

/* Reads the CSV every other password manager exports, and .xlsx or .ods
 * spreadsheets directly — the same wording, and the same code, as the Linux
 * build's Import and export group. */
static const Row PASSWORDS_TRANSFER[] = {
  { ROW_GROUP,  "Import and export", NULL },
  { ROW_ACTION, "Import from a file", "CSV, TSV, Excel or OpenDocument.",
    0, NULL, 0, 0, 0, FALSE, action_import_file, 0, FALSE, NULL, FALSE,
    NULL, NULL, "Choose\u2026" },
  { ROW_ACTION, "Import from another browser",
    "Saved logins from Chrome, Firefox and friends, decrypted in place.",
    0, NULL, 0, 0, 0, FALSE, action_show_import, 0, FALSE, NULL, FALSE,
    NULL, NULL, "Browsers\u2026" },
  { ROW_ACTION, "Export saved logins",
    "Writes every password to an unencrypted CSV file.",
    0, NULL, 0, 0, 0, FALSE, action_export_file, 0, FALSE, NULL, FALSE,
    NULL, NULL, "Export\u2026" },
  { ROW_NOTE,   "A column naming the site and one naming the password",
    "Everything else is worked out from the header row, so an export from "
    "Chrome, Firefox, Bitwarden, KeePassXC or 1Password reads as it stands." },
};

/* ------------------------------------------------------------ the window */

typedef struct {
  const char *name;
  LyGlyph     glyph;
} Category;

static const Category CATEGORIES[] = {
  { "Appearance",  LY_GLYPH_HOME     },
  { "Web",         LY_GLYPH_RELOAD   },
  { "Privacy",     LY_GLYPH_LOCK     },
  { "Blocking",    LY_GLYPH_SHIELD   },
  { "Passwords",   LY_GLYPH_STAR     },
  { "Permissions", LY_GLYPH_CHECK    },
  { "Import",      LY_GLYPH_DOWNLOAD },
};
#define CATEGORY_N ((int) G_N_ELEMENTS (CATEGORIES))

struct _LyPrefs {
  HWND        hwnd;
  HWND        edit;            /* the in-place text editor, one at a time */
  WNDPROC     edit_proc;
  int         editing;         /* row index being edited, -1 for none */
  HINSTANCE   instance;

  LyConfig   *cfg;
  LyStore    *store;
  LyPasswords *passwords;

  int         category;
  int         scroll;
  int         hot;

  GArray     *rows;            /* Row, built per category */
  GPtrArray  *owned;           /* char* the built rows point at */
  GPtrArray  *credentials;     /* LyCredential* for the passwords page */
  GPtrArray  *sources;         /* LyImportSource* for the import page */

  /* Which of bookmarks, history and saved logins each source should bring
   * over: three flags per source, in that order. Page state rather than a
   * setting, so the switches bind straight to it. */
  gboolean   *import_want;

  /* While this is set the passwords page shows one login being edited
   * instead of the list — the Linux build's editor dialog, which has nowhere
   * to be here but does not need one. */
  gboolean    editing_login;
  char       *edit_old_origin;    /* NULL when adding rather than editing */
  char       *edit_old_username;
  char       *edit_origin;
  char       *edit_username;
  char       *edit_password;

  LyTheme     theme;
  LyFonts     fonts;
  int         dpi;
};

static LyPrefs *the_prefs;     /* one window, like the Linux build */

static void rebuild_rows (LyPrefs *p);
static void layout_edit (LyPrefs *p);
static void close_editor (LyPrefs *p);

static int
sc (LyPrefs *p, int v)
{
  return ly_scale (p->dpi, v);
}

/* A string owned by the page, freed when the page is rebuilt. */
static const char *
own (LyPrefs *p, char *s)
{
  g_ptr_array_add (p->owned, s);
  return s;
}

/* --------------------------------------------------------- config access */

static void *
slot_at (LyPrefs *p, const Row *row)
{
  return row->bind != NULL ? row->bind : (void *) ((char *) p->cfg + row->offset);
}

static gboolean *
bool_at (LyPrefs *p, const Row *row)
{
  return (gboolean *) slot_at (p, row);
}

static int *
int_at (LyPrefs *p, const Row *row)
{
  return (int *) slot_at (p, row);
}

static double *
double_at (LyPrefs *p, const Row *row)
{
  return (double *) slot_at (p, row);
}

static char **
string_at (LyPrefs *p, const Row *row)
{
  return (char **) slot_at (p, row);
}

/* A row that is not a setting has nothing to save and no watcher to tell. */
static void
touched (LyPrefs *p, const Row *row)
{
  if (row->bind == NULL)
    ly_config_touch (p->cfg);
}

/* ------------------------------------------------------- page construction */

static void
push (LyPrefs *p, Row row)
{
  g_array_append_val (p->rows, row);
}

static void
push_table (LyPrefs *p, const Row *table, gsize n)
{
  for (gsize i = 0; i < n; i++)
    push (p, table[i]);
}

static void
build_blocking_page (LyPrefs *p)
{
  push_table (p, BLOCKING, G_N_ELEMENTS (BLOCKING) - 1);

  for (int c = 0; c < LY_CAT_N; c++) {
    Row row = { 0 };
    row.kind = ROW_TOGGLE;
    row.title = ly_cat_label ((LyBlockCat) c);
    row.subtitle = ly_cat_summary ((LyBlockCat) c);
    row.offset = OFFSET (block_cat) + (size_t) c * sizeof (gboolean);
    push (p, row);
  }

  push (p, BLOCKING[G_N_ELEMENTS (BLOCKING) - 1]);

  if (p->cfg->block_exceptions->len) {
    Row group = { ROW_GROUP, "Sites with blocking switched off", NULL };
    push (p, group);
    for (guint i = 0; i < p->cfg->block_exceptions->len; i++) {
      Row row = { 0 };
      row.kind = ROW_ACTION;
      row.title = g_ptr_array_index (p->cfg->block_exceptions, i);
      row.subtitle = "Turn blocking back on here";
      row.action = action_unblock_origin;
      row.tag = (int) i;
      push (p, row);
    }
  }
}

static void
collect_credentials (GPtrArray *found, gpointer user_data)
{
  LyPrefs *p = user_data;
  for (guint i = 0; i < found->len; i++) {
    LyCredential *c = g_ptr_array_index (found, i);
    LyCredential *copy = g_new0 (LyCredential, 1);
    copy->origin = g_strdup (c->origin);
    copy->username = g_strdup (c->username);
    g_ptr_array_add (p->credentials, copy);
  }
}

/* The editor takes the whole page while it is open, the way the Linux build's
 * editor takes its own dialog. Three fields, and the same rule about what
 * autofill will match afterwards. */
static void
build_login_editor (LyPrefs *p)
{
  Row group = { ROW_GROUP,
                p->edit_old_origin ? "Edit login" : "Add a login", NULL };
  push (p, group);

  Row note = { ROW_NOTE, "Autofill matches the site exactly as written here",
               "Scheme and host, and a port only if the site uses one. A bare "
               "domain is filled in for you." };
  push (p, note);

  Row site = { 0 };
  site.kind = ROW_TEXT;
  site.title = "Site";
  site.bind = &p->edit_origin;
  push (p, site);

  Row user = { 0 };
  user.kind = ROW_TEXT;
  user.title = "Username";
  user.bind = &p->edit_username;
  push (p, user);

  Row pass = { 0 };
  pass.kind = ROW_TEXT;
  pass.title = "Password";
  pass.bind = &p->edit_password;
  pass.secret = TRUE;
  push (p, pass);

  Row copy = { 0 };
  copy.kind = ROW_ACTION;
  copy.title = "Copy the password";
  copy.subtitle = "Puts it on the clipboard, as it is written above.";
  copy.first_label = "Copy";
  copy.action = action_editor_copy;
  push (p, copy);

  Row save = { 0 };
  save.kind = ROW_PAIR;
  save.title = "Save this login";
  save.subtitle = "Stored in Windows Credential Manager.";
  save.first_label = "Save";
  save.action = action_editor_save;
  save.second_label = "Cancel";
  save.second = action_editor_cancel;
  push (p, save);
}

static void
build_passwords_page (LyPrefs *p)
{
  if (p->editing_login) {
    build_login_editor (p);
    return;
  }

  push_table (p, PASSWORDS_TOP, G_N_ELEMENTS (PASSWORDS_TOP));

  g_ptr_array_set_size (p->credentials, 0);
  ly_passwords_list (p->passwords, collect_credentials, p);

  Row group = { ROW_GROUP, "Saved logins", NULL };
  push (p, group);

  Row add = { 0 };
  add.kind = ROW_ACTION;
  add.title = "Add a login by hand";
  add.subtitle = "For a site Lyndon has not seen you sign in to.";
  add.first_label = "Add\u2026";
  add.action = action_add_login;
  push (p, add);

  if (p->credentials->len == 0) {
    Row none = { ROW_NOTE, "Nothing saved yet",
                 "Sign in somewhere and Lyndon will offer to remember it." };
    push (p, none);
  }
  for (guint i = 0; i < p->credentials->len; i++) {
    LyCredential *c = g_ptr_array_index (p->credentials, i);
    Row row = { 0 };
    row.kind = ROW_PAIR;
    row.title = own (p, g_strdup (c->origin));
    row.subtitle = own (p, g_strdup ((c->username && *c->username)
                                       ? c->username : "(no username)"));
    row.first_label = "Edit\u2026";
    row.action = action_edit_login;
    row.second_label = "Forget";
    row.second = action_forget_password;
    row.tag = (int) i;
    push (p, row);
  }

  push_table (p, PASSWORDS_TRANSFER, G_N_ELEMENTS (PASSWORDS_TRANSFER));

  if (p->cfg->password_never->len) {
    Row never = { ROW_GROUP, "Never asked on", NULL };
    push (p, never);
    for (guint i = 0; i < p->cfg->password_never->len; i++) {
      Row row = { 0 };
      row.kind = ROW_ACTION;
      row.title = g_ptr_array_index (p->cfg->password_never, i);
      row.subtitle = "Ask here again";
      row.action = action_unblock_origin;
      row.tag = -(int) i - 1;   /* negative: the password list, not blocking */
      push (p, row);
    }
  }
}

static void
build_permissions_page (LyPrefs *p)
{
  Row group = { ROW_GROUP, "What a site may ask for", NULL };
  push (p, group);
  for (int k = 0; k < LY_PERM_N; k++) {
    Row row = { 0 };
    row.kind = ROW_CHOICE;
    row.title = ly_perm_label ((LyPermKind) k);
    row.offset = OFFSET (perm) + (size_t) k * sizeof (LyPolicy);
    row.choices = POLICY;
    push (p, row);
  }
}

/* Three flags per source, in the order bookmarks, history, saved logins. */
#define WANT_BOOKMARKS 0
#define WANT_HISTORY   1
#define WANT_PASSWORDS 2

static gboolean *
want_slot (LyPrefs *p, guint source, int which)
{
  return &p->import_want[source * 3 + (guint) which];
}

static void
push_import_switch (LyPrefs *p, guint source, int which,
                    const char *title, const char *subtitle)
{
  Row row = { 0 };
  row.kind = ROW_TOGGLE;
  row.title = title;
  row.subtitle = subtitle;
  row.bind = want_slot (p, source, which);
  push (p, row);
}

static void
build_import_page (LyPrefs *p)
{
  Row group = { ROW_GROUP, "Browsers found on this computer", NULL };
  push (p, group);

  Row about = { ROW_NOTE, "Nothing is changed in the other browser",
                "Bookmarks and history are copied in with their original visit "
                "counts and dates, and saved logins are decrypted straight out "
                "of the other browser's own store. Every file is read from a "
                "temporary copy." };
  push (p, about);

  if (p->sources)
    g_ptr_array_unref (p->sources);
  p->sources = ly_import_sources ();

  g_clear_pointer (&p->import_want, g_free);
  p->import_want = g_new0 (gboolean, (p->sources->len ?: 1) * 3);

  if (p->sources->len == 0) {
    Row none = { ROW_NOTE, "No other browsers found",
                 "Lyndon looks for Chrome, Edge, Brave, Vivaldi, Opera and "
                 "Firefox profiles in the places their installers use." };
    push (p, none);
    return;
  }

  for (guint i = 0; i < p->sources->len; i++) {
    LyImportSource *s = g_ptr_array_index (p->sources, i);

    Row heading = { ROW_GROUP, s->label, NULL };
    push (p, heading);

    /* Everything the profile has, on by default, exactly as the expander
     * rows on the Linux side start switched on. */
    if (s->has_bookmarks) {
      *want_slot (p, i, WANT_BOOKMARKS) = TRUE;
      push_import_switch (p, i, WANT_BOOKMARKS, "Bookmarks", NULL);
    }
    if (s->has_history) {
      *want_slot (p, i, WANT_HISTORY) = TRUE;
      push_import_switch (p, i, WANT_HISTORY, "History", NULL);
    }
    if (s->has_passwords) {
      *want_slot (p, i, WANT_PASSWORDS) = TRUE;
      push_import_switch (p, i, WANT_PASSWORDS, "Saved logins",
        s->kind == LY_IMPORT_FIREFOX
          ? "Needs the profile to have no Primary Password set."
          : "Needs the key this browser left for your Windows account.");
    }

    Row go = { 0 };
    go.kind = ROW_ACTION;
    go.title = "Bring the selected items over";
    go.first_label = "Import";
    go.action = action_import;
    go.tag = (int) i;
    push (p, go);
  }
}

static void
rebuild_rows (LyPrefs *p)
{
  if (p->editing >= 0) {
    ShowWindow (p->edit, SW_HIDE);
    p->editing = -1;
  }
  g_array_set_size (p->rows, 0);
  g_ptr_array_set_size (p->owned, 0);

  switch (p->category) {
    case 0: push_table (p, APPEARANCE, G_N_ELEMENTS (APPEARANCE)); break;
    case 1: push_table (p, WEB, G_N_ELEMENTS (WEB)); break;
    case 2: push_table (p, PRIVACY, G_N_ELEMENTS (PRIVACY)); break;
    case 3: build_blocking_page (p); break;
    case 4: build_passwords_page (p); break;
    case 5: build_permissions_page (p); break;
    case 6: build_import_page (p); break;
    default: break;
  }
  p->scroll = 0;
  p->hot = -1;
  InvalidateRect (p->hwnd, NULL, FALSE);
}

/* -------------------------------------------------------------- geometry */

static RECT
sidebar_rect (LyPrefs *p)
{
  RECT c;
  GetClientRect (p->hwnd, &c);
  RECT r = { 0, 0, sc (p, SIDEBAR_W), c.bottom };
  return r;
}

static RECT
category_rect (LyPrefs *p, int index)
{
  RECT s = sidebar_rect (p);
  int h = sc (p, 40);
  RECT r = { s.left + sc (p, 6), sc (p, 12) + index * h,
             s.right - sc (p, 6), sc (p, 12) + index * h + h - sc (p, 2) };
  return r;
}

static RECT
content_rect (LyPrefs *p)
{
  RECT c;
  GetClientRect (p->hwnd, &c);
  RECT r = { sc (p, SIDEBAR_W), 0, c.right, c.bottom };
  return r;
}

static int
row_height (LyPrefs *p, const Row *row)
{
  switch (row->kind) {
    case ROW_GROUP: return sc (p, GROUP_H);
    case ROW_NOTE:  return sc (p, 64);
    default:        return sc (p, ROW_H);
  }
}

static RECT
row_rect (LyPrefs *p, guint index)
{
  RECT c = content_rect (p);
  int y = c.top + sc (p, PAD) - p->scroll;
  for (guint i = 0; i < index && i < p->rows->len; i++)
    y += row_height (p, &g_array_index (p->rows, Row, i));
  RECT r = { c.left + sc (p, PAD), y, c.right - sc (p, PAD), 0 };
  if (index < p->rows->len)
    r.bottom = r.top + row_height (p, &g_array_index (p->rows, Row, index));
  return r;
}

static int
content_height (LyPrefs *p)
{
  int h = sc (p, PAD) * 2;
  for (guint i = 0; i < p->rows->len; i++)
    h += row_height (p, &g_array_index (p->rows, Row, i));
  return h;
}

static int
max_scroll (LyPrefs *p)
{
  RECT c = content_rect (p);
  return MAX (0, content_height (p) - (c.bottom - c.top));
}

/* The control on the right of a row: the switch, the choice, the value. */
static RECT
control_rect (LyPrefs *p, RECT row, const Row *spec)
{
  /* Two buttons and the gap between them, when there are two. */
  int w = sc (p, spec->kind == ROW_TOGGLE ? SWITCH_W
                 : spec->kind == ROW_PAIR ? 186 : 150);
  int h = sc (p, spec->kind == ROW_TOGGLE ? SWITCH_H : 28);
  int cy = (row.top + row.bottom) / 2;
  RECT r = { row.right - sc (p, 12) - w, cy - h / 2, row.right - sc (p, 12), cy + h / 2 };
  return r;
}

/* The buttons inside a ROW_ACTION or ROW_PAIR control area, right to left.
 * `which` is 0 for the leading (rightmost) button, 1 for the second. */
static RECT
button_rect (LyPrefs *p, RECT ctl, int which)
{
  int w = sc (p, 90);
  RECT r = { ctl.right - w, ctl.top, ctl.right, ctl.bottom };
  if (which > 0) {
    r.right = r.left - sc (p, 6);
    r.left = r.right - w;
  }
  return r;
}

/* -------------------------------------------------------------- painting */

static void
paint (LyPrefs *p, LyCanvas *cv)
{
  RECT c;
  GetClientRect (p->hwnd, &c);
  ly_fill (cv, c, p->theme.bg);

  /* sidebar */
  RECT s = sidebar_rect (p);
  ly_fill (cv, s, p->theme.surface_alt);
  for (int i = 0; i < CATEGORY_N; i++) {
    RECT r = category_rect (p, i);
    gboolean active = (i == p->category);
    if (active)
      ly_round (cv, r, sc (p, 6), p->theme.accent, 1.0);

    RECT icon = { r.left + sc (p, 8), r.top + sc (p, 8),
                  r.left + sc (p, 30), r.bottom - sc (p, 8) };
    ly_glyph (cv, icon, CATEGORIES[i].glyph,
              active ? p->theme.accent_text : p->theme.text_dim, FALSE);

    RECT label = r;
    label.left += sc (p, 36);
    ly_text (cv, label, CATEGORIES[i].name,
             active ? p->theme.accent_text : p->theme.text,
             active ? p->fonts.bold : p->fonts.normal,
             DT_SINGLELINE | DT_VCENTER | DT_NOPREFIX);
  }

  /* content */
  RECT content = content_rect (p);
  RECT saved_clip = ly_canvas_clip (cv, content);

  for (guint i = 0; i < p->rows->len; i++) {
    const Row *spec = &g_array_index (p->rows, Row, i);
    RECT r = row_rect (p, i);
    if (r.bottom < content.top || r.top > content.bottom)
      continue;

    COLORREF ink = spec->linux_only ? p->theme.text_dim : p->theme.text;

    if (spec->kind == ROW_GROUP) {
      RECT t = r;
      t.top += sc (p, 12);
      ly_text (cv, t, spec->title, p->theme.text_dim, p->fonts.bold,
               DT_SINGLELINE | DT_BOTTOM | DT_NOPREFIX);
      continue;
    }

    gboolean hot = ((int) i == p->hot);
    ly_round (cv, r, sc (p, 8), hot ? p->theme.surface_alt : p->theme.surface, 1.0);

    RECT title = { r.left + sc (p, 14), r.top + sc (p, 9),
                   r.right - sc (p, 180), r.top + sc (p, 29) };
    if (spec->kind == ROW_NOTE || spec->kind == ROW_ACTION)
      title.right = r.right - sc (p, 14);
    else if (spec->kind == ROW_PAIR)
      title.right = r.right - sc (p, 210);
    ly_text (cv, title, spec->title, ink, p->fonts.normal,
             DT_SINGLELINE | DT_VCENTER | DT_END_ELLIPSIS | DT_NOPREFIX);

    if (spec->subtitle) {
      RECT sub = title;
      sub.top = r.top + sc (p, 28);
      sub.bottom = r.bottom - sc (p, 6);
      ly_text (cv, sub, spec->subtitle, p->theme.text_dim, p->fonts.small_,
               (spec->kind == ROW_NOTE ? DT_WORDBREAK : DT_SINGLELINE | DT_END_ELLIPSIS)
                 | DT_NOPREFIX);
    }

    RECT ctl = control_rect (p, r, spec);
    switch (spec->kind) {
      case ROW_TOGGLE:
        ly_switch (cv, ctl, *bool_at (p, spec), &p->theme);
        break;

      case ROW_CHOICE: {
        int value = *int_at (p, spec);
        int n = 0;
        while (spec->choices[n]) n++;
        const char *label = (value >= 0 && value < n) ? spec->choices[value] : "?";
        ly_round (cv, ctl, sc (p, 6), p->theme.field, 1.0);
        RECT t = ctl;
        t.left += sc (p, 10);
        t.right -= sc (p, 24);
        ly_text (cv, t, label, ink, p->fonts.small_,
                 DT_SINGLELINE | DT_VCENTER | DT_END_ELLIPSIS | DT_NOPREFIX);
        RECT chev = { ctl.right - sc (p, 22), ctl.top, ctl.right, ctl.bottom };
        ly_glyph (cv, chev, LY_GLYPH_CHEVRON_DOWN, p->theme.text_dim, FALSE);
        break;
      }

      case ROW_NUMBER: {
        double value = spec->integer ? (double) *int_at (p, spec)
                                     : *double_at (p, spec);
        if (!spec->integer && spec->max > 3.0)
          value *= 100.0;   /* zoom is stored as a factor, shown as per cent */
        g_autofree char *text = spec->integer
          ? g_strdup_printf ("%d", (int) value)
          : g_strdup_printf ("%.0f", value);
        ly_round (cv, ctl, sc (p, 6), p->theme.field, 1.0);
        RECT minus = { ctl.left, ctl.top, ctl.left + sc (p, 30), ctl.bottom };
        RECT plus  = { ctl.right - sc (p, 30), ctl.top, ctl.right, ctl.bottom };
        ly_text (cv, minus, "−", p->theme.text, p->fonts.normal,
                 DT_SINGLELINE | DT_CENTER | DT_VCENTER | DT_NOPREFIX);
        ly_text (cv, plus, "+", p->theme.text, p->fonts.normal,
                 DT_SINGLELINE | DT_CENTER | DT_VCENTER | DT_NOPREFIX);
        ly_text (cv, ctl, text, ink, p->fonts.small_,
                 DT_SINGLELINE | DT_CENTER | DT_VCENTER | DT_NOPREFIX);
        break;
      }

      case ROW_TEXT: {
        if ((int) i == p->editing)
          break;   /* the EDIT control is over it */
        char **value = string_at (p, spec);
        gboolean filled = value && *value && **value;
        ly_round (cv, ctl, sc (p, 6), p->theme.field, 1.0);
        RECT t = ctl;
        t.left += sc (p, 8);
        t.right -= sc (p, 8);

        /* A saved password is never printed into the window; the row says it
         * is there and the editor is the only place it is legible. */
        g_autofree char *dots = NULL;
        if (spec->secret && filled) {
          gsize n = MIN (g_utf8_strlen (*value, -1), 24);
          dots = g_strnfill (n * 3, ' ');
          for (gsize k = 0; k < n; k++)
            memcpy (dots + k * 3, "\u2022", 3);
        }
        const char *shown = dots != NULL ? dots : (filled ? *value : "—");
        ly_text (cv, t, shown, filled ? ink : p->theme.text_dim,
                 p->fonts.small_,
                 DT_SINGLELINE | DT_VCENTER | DT_END_ELLIPSIS | DT_NOPREFIX);
        break;
      }

      case ROW_ACTION: {
        RECT b = button_rect (p, ctl, 0);
        ly_round (cv, b, sc (p, 6), hot ? p->theme.accent : p->theme.field, 1.0);
        ly_text (cv, b, spec->first_label ?: "Go",
                 hot ? p->theme.accent_text : p->theme.text,
                 p->fonts.small_, DT_SINGLELINE | DT_CENTER | DT_VCENTER | DT_NOPREFIX);
        break;
      }

      case ROW_PAIR: {
        RECT first  = button_rect (p, ctl, 0);
        RECT second = button_rect (p, ctl, 1);
        ly_round (cv, first, sc (p, 6), hot ? p->theme.accent : p->theme.field, 1.0);
        ly_text (cv, first, spec->first_label ?: "Go",
                 hot ? p->theme.accent_text : p->theme.text, p->fonts.small_,
                 DT_SINGLELINE | DT_CENTER | DT_VCENTER | DT_NOPREFIX);
        ly_round (cv, second, sc (p, 6), p->theme.field, 1.0);
        ly_text (cv, second, spec->second_label ?: "", p->theme.text,
                 p->fonts.small_, DT_SINGLELINE | DT_CENTER | DT_VCENTER | DT_NOPREFIX);
        break;
      }

      default:
        break;
    }
  }

  ly_canvas_unclip (cv, saved_clip);

  int span = max_scroll (p);
  if (span > 0) {
    int visible = content.bottom - content.top;
    int thumb = MAX (sc (p, 30), visible * visible / MAX (1, content_height (p)));
    int y = content.top + (visible - thumb) * p->scroll / span;
    RECT bar = { c.right - sc (p, 6), y, c.right - sc (p, 3), y + thumb };
    ly_round (cv, bar, sc (p, 2), p->theme.line, 1.0);
  }
}

/* --------------------------------------------------------------- editing */

static void
commit_edit (LyPrefs *p)
{
  if (p->editing < 0)
    return;
  const Row *spec = &g_array_index (p->rows, Row, (guint) p->editing);
  wchar_t buf[2048];
  GetWindowTextW (p->edit, buf, G_N_ELEMENTS (buf));
  g_autofree char *text = g_utf16_to_utf8 ((const gunichar2 *) buf, -1, NULL, NULL, NULL);

  char **slot = string_at (p, spec);
  g_free (*slot);
  *slot = g_strdup (text ? text : "");
  touched (p, spec);

  ShowWindow (p->edit, SW_HIDE);
  p->editing = -1;
  InvalidateRect (p->hwnd, NULL, FALSE);
}

static LRESULT CALLBACK
edit_proc (HWND hwnd, UINT msg, WPARAM wp, LPARAM lp)
{
  LyPrefs *p = (LyPrefs *) GetWindowLongPtrW (hwnd, GWLP_USERDATA);
  if (msg == WM_KEYDOWN && wp == VK_RETURN) {
    commit_edit (p);
    return 0;
  }
  if (msg == WM_KEYDOWN && wp == VK_ESCAPE) {
    ShowWindow (p->edit, SW_HIDE);
    p->editing = -1;
    InvalidateRect (p->hwnd, NULL, FALSE);
    return 0;
  }
  if (msg == WM_CHAR && (wp == VK_RETURN || wp == VK_ESCAPE))
    return 0;
  if (msg == WM_KILLFOCUS) {
    commit_edit (p);
    return 0;
  }
  return CallWindowProcW (p->edit_proc, hwnd, msg, wp, lp);
}

static void
begin_edit (LyPrefs *p, guint index)
{
  const Row *spec = &g_array_index (p->rows, Row, index);
  RECT r = row_rect (p, index);
  RECT ctl = control_rect (p, r, spec);

  char **value = string_at (p, spec);
  g_autofree wchar_t *w =
    (wchar_t *) g_utf8_to_utf16 ((value && *value) ? *value : "", -1, NULL, NULL, NULL);
  SetWindowTextW (p->edit, w ? w : L"");

  p->editing = (int) index;
  SendMessageW (p->edit, EM_SETPASSWORDCHAR,
                spec->secret ? (WPARAM) L'\u2022' : 0, 0);
  MoveWindow (p->edit, ctl.left + sc (p, 4), ctl.top + sc (p, 3),
              (ctl.right - ctl.left) - sc (p, 8), (ctl.bottom - ctl.top) - sc (p, 6), TRUE);
  ShowWindow (p->edit, SW_SHOW);
  SetFocus (p->edit);
  SendMessageW (p->edit, EM_SETSEL, 0, -1);
}

static void
layout_edit (LyPrefs *p)
{
  if (p->editing >= 0)
    begin_edit (p, (guint) p->editing);
}

/* --------------------------------------------------------------- actions */

static void
action_clear_history (LyPrefs *p, int index)
{
  if (MessageBoxW (p->hwnd, L"Delete all browsing history?", L"Lyndon",
                   MB_OKCANCEL | MB_ICONWARNING) != IDOK)
    return;
  ly_store_clear_history (p->store);
  MessageBoxW (p->hwnd, L"History cleared.", L"Lyndon", MB_OK | MB_ICONINFORMATION);
}

static void
action_clear_cookies (LyPrefs *p, int index)
{
  /* The profile directory is Edge's, and it will not let go of it while the
   * browser is running. Marking it is honest and works; deleting it from
   * under a live WebView2 is not. */
  g_autofree char *data = ly_data_dir ();
  g_autofree char *stamp = g_build_filename (data, "clear-on-next-start", NULL);
  g_file_set_contents (stamp, "", 0, NULL);
  MessageBoxW (p->hwnd,
               L"Cookies and cache will be cleared the next time Lyndon starts.",
               L"Lyndon", MB_OK | MB_ICONINFORMATION);
}

static void
action_choose_downloads (LyPrefs *p, int index)
{
  BROWSEINFOW bi = { 0 };
  bi.hwndOwner = p->hwnd;
  bi.lpszTitle = L"Where should downloads go?";
  bi.ulFlags = BIF_RETURNONLYFSDIRS | BIF_NEWDIALOGSTYLE;
  LPITEMIDLIST id = SHBrowseForFolderW (&bi);
  if (id == NULL)
    return;
  wchar_t path[MAX_PATH];
  if (SHGetPathFromIDListW (id, path)) {
    g_autofree char *utf8 = g_utf16_to_utf8 ((const gunichar2 *) path, -1, NULL, NULL, NULL);
    g_free (p->cfg->download_dir);
    p->cfg->download_dir = g_strdup (utf8 ? utf8 : "");
    ly_config_touch (p->cfg);
    InvalidateRect (p->hwnd, NULL, FALSE);
  }
  CoTaskMemFree (id);
}

static void
action_open_rules (LyPrefs *p, int index)
{
  g_autofree char *dir = ly_config_dir ();
  g_autofree char *path = g_build_filename (dir, "rules.txt", NULL);
  if (!g_file_test (path, G_FILE_TEST_EXISTS)) {
    const char *seed =
      "! Your own blocking rules, in Adblock syntax.\n"
      "! One per line. Lyndon reads this at start-up.\n"
      "!\n"
      "! ||annoying.example^\n"
      "! example.com##.newsletter-popup\n";
    g_file_set_contents (path, seed, -1, NULL);
  }
  g_autofree wchar_t *w = (wchar_t *) g_utf8_to_utf16 (path, -1, NULL, NULL, NULL);
  if (w)
    ShellExecuteW (NULL, L"open", w, NULL, NULL, SW_SHOWNORMAL);
}

static void
action_forget_password (LyPrefs *p, int index)
{
  if (index < 0 || (guint) index >= p->credentials->len)
    return;
  LyCredential *c = g_ptr_array_index (p->credentials, (guint) index);
  ly_passwords_forget (p->passwords, c->origin, c->username);
  rebuild_rows (p);
}

static void
action_unblock_origin (LyPrefs *p, int index)
{
  if (index >= 0) {
    if ((guint) index < p->cfg->block_exceptions->len) {
      g_ptr_array_remove_index (p->cfg->block_exceptions, (guint) index);
      ly_config_touch (p->cfg);
    }
  } else {
    guint i = (guint) (-index - 1);
    if (i < p->cfg->password_never->len) {
      g_ptr_array_remove_index (p->cfg->password_never, i);
      ly_config_touch (p->cfg);
    }
  }
  rebuild_rows (p);
}

static void
report (LyPrefs *p, const char *text, gboolean ok)
{
  g_autofree wchar_t *w = (wchar_t *) g_utf8_to_utf16 (text, -1, NULL, NULL, NULL);
  MessageBoxW (p->hwnd, w ? w : L"", L"Lyndon",
               MB_OK | (ok ? MB_ICONINFORMATION : MB_ICONERROR));
}

/* Writes a batch into Credential Manager, one call each. An import is an
 * explicit, one-off action, and getting the count right in the message
 * afterwards matters more here than staying responsive for a second. */
static guint
store_credentials (LyPrefs *p, GPtrArray *credentials, GError **error)
{
  guint stored = 0;
  for (guint i = 0; i < credentials->len; i++) {
    LyCredential *c = g_ptr_array_index (credentials, i);
    if (!ly_passwords_save_sync (p->passwords, c->origin, c->username,
                                 c->password, error))
      break;      /* a store that refused one row will refuse the rest */
    stored++;
  }
  return stored;
}

static void
action_import (LyPrefs *p, int index)
{
  if (p->sources == NULL || index < 0 || (guint) index >= p->sources->len)
    return;
  const LyImportSource *source = g_ptr_array_index (p->sources, (guint) index);

  gboolean want_bookmarks = *want_slot (p, (guint) index, WANT_BOOKMARKS);
  gboolean want_history   = *want_slot (p, (guint) index, WANT_HISTORY);
  gboolean want_passwords = *want_slot (p, (guint) index, WANT_PASSWORDS);

  if (!want_bookmarks && !want_history && !want_passwords)
    return;

  GString *message = g_string_new (NULL);
  g_autofree char *problem = NULL;
  gboolean ok = TRUE;

  SetCursor (LoadCursorW (NULL, IDC_WAIT));

  if (want_bookmarks || want_history) {
    LyImportResult result = { 0 };
    ok = ly_import_run (p->store, source, want_bookmarks, want_history, &result);
    if (!ok) {
      problem = g_strdup (result.error ? result.error : "Unknown error.");
    } else {
      if (want_bookmarks)
        g_string_append_printf (message, "%s%u bookmark%s", message->len ? ", " : "",
                                result.bookmarks, result.bookmarks == 1 ? "" : "s");
      if (want_history)
        g_string_append_printf (message, "%s%u page%s of history",
                                message->len ? ", " : "",
                                result.history, result.history == 1 ? "" : "s");
    }
    ly_import_result_clear (&result);
  }

  if (want_passwords && ok) {
    guint skipped = 0;
    g_autoptr (GError) error = NULL;
    g_autoptr (GPtrArray) credentials =
      ly_import_passwords (source, &skipped, &error);

    if (credentials == NULL) {
      problem = g_strdup (error->message);
      ok = FALSE;
    } else {
      guint stored = store_credentials (p, credentials, &error);
      g_string_append_printf (message, "%s%u login%s", message->len ? ", " : "",
                              stored, stored == 1 ? "" : "s");
      if (skipped > 0)
        g_string_append_printf (message, " (%u could not be read)", skipped);
      if (stored < credentials->len && error != NULL) {
        problem = g_strdup (error->message);
        ok = FALSE;
      }
    }
  }

  SetCursor (LoadCursorW (NULL, IDC_ARROW));

  g_autofree char *text = ok
    ? g_strdup_printf ("Imported %s from %s.", message->str, source->label)
    : g_strdup_printf ("%s from %s.\n\n%s",
                       message->len > 0 ? "Only partly imported"
                                        : "Nothing could be imported",
                       source->label, problem ? problem : "Unknown error.");
  g_string_free (message, TRUE);

  report (p, text, ok);
  /* The page is not rebuilt: it holds the switches the user just set, and
   * losing them to a failed import would mean setting them again. The saved
   * logins list is rebuilt whenever the Passwords page is opened. */
}

/* ------------------------------------------------- password files */

/* GetOpenFileName wants the filter as double-NUL-terminated pairs, which no
 * string literal can carry, so it is assembled rather than written out. */
static wchar_t *
build_filter (gboolean csv_only)
{
  static const wchar_t *const WIDE[] = {
    L"Password exports and spreadsheets", L"*.csv;*.tsv;*.txt;*.xlsx;*.ods",
    L"All files", L"*.*", NULL
  };
  static const wchar_t *const NARROW[] = {
    L"CSV", L"*.csv", L"All files", L"*.*", NULL
  };
  const wchar_t *const *parts = csv_only ? NARROW : WIDE;

  gsize total = 1;   /* the final terminating NUL */
  for (gsize i = 0; parts[i] != NULL; i++)
    total += wcslen (parts[i]) + 1;

  wchar_t *filter = g_new0 (wchar_t, total);
  gsize at = 0;
  for (gsize i = 0; parts[i] != NULL; i++) {
    gsize n = wcslen (parts[i]);
    wmemcpy (filter + at, parts[i], n);
    at += n + 1;     /* g_new0 already left the NUL between them */
  }
  return filter;
}

static void
action_import_file (LyPrefs *p, int index)
{
  wchar_t path[MAX_PATH] = { 0 };
  g_autofree wchar_t *filter = build_filter (FALSE);

  OPENFILENAMEW ofn = { 0 };
  ofn.lStructSize = sizeof ofn;
  ofn.hwndOwner = p->hwnd;
  ofn.lpstrFilter = filter;
  ofn.lpstrFile = path;
  ofn.nMaxFile = G_N_ELEMENTS (path);
  ofn.lpstrTitle = L"Import passwords";
  ofn.Flags = OFN_FILEMUSTEXIST | OFN_PATHMUSTEXIST | OFN_NOCHANGEDIR;

  if (!GetOpenFileNameW (&ofn))
    return;

  g_autofree char *utf8 = g_utf16_to_utf8 ((const gunichar2 *) path, -1, NULL, NULL, NULL);
  if (utf8 == NULL)
    return;

  guint skipped = 0;
  g_autoptr (GError) error = NULL;
  SetCursor (LoadCursorW (NULL, IDC_WAIT));
  g_autoptr (GPtrArray) credentials = ly_pwfile_read (utf8, &skipped, &error);

  if (credentials == NULL) {
    SetCursor (LoadCursorW (NULL, IDC_ARROW));
    g_autofree char *text =
      g_strdup_printf ("Nothing could be imported.\n\n%s", error->message);
    report (p, text, FALSE);
    return;
  }

  guint stored = store_credentials (p, credentials, &error);
  SetCursor (LoadCursorW (NULL, IDC_ARROW));

  GString *message = g_string_new (NULL);
  g_string_append_printf (message, "Imported %u login%s", stored,
                          stored == 1 ? "" : "s");
  if (skipped > 0)
    g_string_append_printf (message, "; %u row%s had no site or password",
                            skipped, skipped == 1 ? "" : "s");
  g_string_append_c (message, '.');

  gboolean ok = !(stored < credentials->len && error != NULL);
  if (!ok)
    g_string_append_printf (message, "\n\n%s", error->message);

  report (p, message->str, ok);
  g_string_free (message, TRUE);
  rebuild_rows (p);
}

/* The listing deliberately never holds a password, so the export asks for
 * every secret at the moment it is about to write them out. */
static void
collect_for_export (GPtrArray *found, gpointer user_data)
{
  GPtrArray *out = user_data;
  for (guint i = 0; i < found->len; i++) {
    LyCredential *c = g_ptr_array_index (found, i);
    g_ptr_array_add (out, ly_credential_new (c->origin, c->username, c->password));
  }
}

static void
action_export_file (LyPrefs *p, int index)
{
  if (MessageBoxW (p->hwnd,
        L"Every password is written out as readable text, with no encryption "
        L"of any kind.\n\nAnything that can read the file can read your "
        L"logins \u2014 delete it once whatever needed it has finished.\n\n"
        L"Export saved passwords?",
        L"Lyndon", MB_OKCANCEL | MB_ICONWARNING) != IDOK)
    return;

  wchar_t path[MAX_PATH];
  wcscpy (path, L"lyndon-passwords.csv");
  g_autofree wchar_t *filter = build_filter (TRUE);

  OPENFILENAMEW ofn = { 0 };
  ofn.lStructSize = sizeof ofn;
  ofn.hwndOwner = p->hwnd;
  ofn.lpstrFilter = filter;
  ofn.lpstrFile = path;
  ofn.nMaxFile = G_N_ELEMENTS (path);
  ofn.lpstrTitle = L"Export passwords";
  ofn.lpstrDefExt = L"csv";
  ofn.Flags = OFN_OVERWRITEPROMPT | OFN_PATHMUSTEXIST | OFN_NOCHANGEDIR;

  if (!GetSaveFileNameW (&ofn))
    return;

  g_autofree char *utf8 = g_utf16_to_utf8 ((const gunichar2 *) path, -1, NULL, NULL, NULL);
  if (utf8 == NULL)
    return;

  g_autoptr (GPtrArray) credentials = ly_credentials_new ();
  ly_passwords_list (p->passwords, collect_for_export, credentials);

  g_autoptr (GError) error = NULL;
  if (!ly_pwfile_write_csv (utf8, credentials, &error)) {
    g_autofree char *text =
      g_strdup_printf ("The file could not be written.\n\n%s", error->message);
    report (p, text, FALSE);
    return;
  }

  g_autofree char *text =
    g_strdup_printf ("Exported %u login%s in plain text.",
                     credentials->len, credentials->len == 1 ? "" : "s");
  report (p, text, TRUE);
}

/* The Linux build offers the browser import from the passwords page as well
 * as from its own; here that page is a category, so this goes to it. */
static void
action_show_import (LyPrefs *p, int index)
{
  p->category = CATEGORY_N - 1;   /* Import, the last one */
  rebuild_rows (p);
}

/* -------------------------------------------------------- the login editor */

static void
open_editor (LyPrefs *p, const char *origin, const char *username,
             const char *password)
{
  close_editor (p);   /* wipes and frees whatever it was holding before */

  p->edit_old_origin   = g_strdup (origin);
  p->edit_old_username = g_strdup (username);
  p->edit_origin       = g_strdup (origin ?: "");
  p->edit_username     = g_strdup (username ?: "");
  p->edit_password     = g_strdup (password ?: "");
  p->editing_login     = TRUE;

  rebuild_rows (p);
}

static void
close_editor (LyPrefs *p)
{
  /* The password was legible on this page; it should not stay in the heap
   * once the page is gone. */
  if (p->edit_password != NULL)
    memset (p->edit_password, 0, strlen (p->edit_password));

  g_clear_pointer (&p->edit_old_origin, g_free);
  g_clear_pointer (&p->edit_old_username, g_free);
  g_clear_pointer (&p->edit_origin, g_free);
  g_clear_pointer (&p->edit_username, g_free);
  g_clear_pointer (&p->edit_password, g_free);
  p->editing_login = FALSE;
}

static void
action_add_login (LyPrefs *p, int index)
{
  open_editor (p, NULL, NULL, NULL);
}

/* The page keeps only what it draws — an origin and a username — so opening
 * the editor asks the store for that one row's secret at the moment it is
 * needed, rather than holding every password for as long as the window is
 * open. */
static void
action_edit_login (LyPrefs *p, int index)
{
  if (index < 0 || (guint) index >= p->credentials->len)
    return;
  LyCredential *row = g_ptr_array_index (p->credentials, (guint) index);

  g_autoptr (GPtrArray) found = ly_credentials_new ();
  ly_passwords_lookup (p->passwords, row->origin, collect_for_export, found);

  const char *password = NULL;
  for (guint i = 0; i < found->len; i++) {
    LyCredential *c = g_ptr_array_index (found, i);
    if (g_strcmp0 (c->username, row->username) == 0) {
      password = c->password;
      break;
    }
  }
  open_editor (p, row->origin, row->username, password);
}

/* The clipboard is the one place a saved password legitimately leaves the
 * store, and it is what the Linux editor's copy button does too. */
static void
action_editor_copy (LyPrefs *p, int index)
{
  commit_edit (p);
  if (p->edit_password == NULL || *p->edit_password == '\0')
    return;

  glong chars = 0;
  wchar_t *w = (wchar_t *) g_utf8_to_utf16 (p->edit_password, -1, NULL, &chars, NULL);
  if (w == NULL)
    return;

  gsize bytes = (gsize) (chars + 1) * sizeof (wchar_t);
  HGLOBAL handle = GlobalAlloc (GMEM_MOVEABLE, bytes);
  if (handle != NULL && OpenClipboard (p->hwnd)) {
    void *slot = GlobalLock (handle);
    if (slot != NULL) {
      memcpy (slot, w, bytes);
      GlobalUnlock (handle);
      EmptyClipboard ();
      /* The clipboard owns it from here; nothing is freed on this side. */
      if (SetClipboardData (CF_UNICODETEXT, handle) != NULL)
        handle = NULL;
    }
    CloseClipboard ();
  }
  if (handle != NULL)
    GlobalFree (handle);

  memset (w, 0, bytes);
  g_free (w);
}

static void
action_editor_cancel (LyPrefs *p, int index)
{
  close_editor (p);
  rebuild_rows (p);
}

static void
action_editor_save (LyPrefs *p, int index)
{
  commit_edit (p);   /* a field still being typed into counts */

  g_autofree char *origin = ly_passwords_normalise_origin (p->edit_origin);
  if (origin == NULL) {
    report (p, "That site is not a web address Lyndon can match a page "
               "against. A domain such as example.com is enough.", FALSE);
    return;
  }
  if (p->edit_password == NULL || *p->edit_password == '\0') {
    report (p, "A password is required.", FALSE);
    return;
  }

  g_autoptr (GError) error = NULL;
  gboolean ok = ly_passwords_update (p->passwords,
                                     p->edit_old_origin, p->edit_old_username,
                                     origin, p->edit_username,
                                     p->edit_password, &error);
  if (!ok) {
    g_autofree char *text =
      g_strdup_printf ("Could not save.\n\n%s", error->message);
    report (p, text, FALSE);
    return;
  }

  close_editor (p);
  rebuild_rows (p);
}

/* ---------------------------------------------------------------- input */

static int
row_at (LyPrefs *p, POINT pt)
{
  RECT c = content_rect (p);
  if (!PtInRect (&c, pt))
    return -1;
  for (guint i = 0; i < p->rows->len; i++) {
    const Row *spec = &g_array_index (p->rows, Row, i);
    if (spec->kind == ROW_GROUP)
      continue;
    RECT r = row_rect (p, i);
    if (PtInRect (&r, pt))
      return (int) i;
  }
  return -1;
}

static void
cycle_choice (LyPrefs *p, const Row *spec, gboolean backwards)
{
  int n = 0;
  while (spec->choices[n]) n++;
  int *slot = int_at (p, spec);
  *slot = (*slot + (backwards ? n - 1 : 1)) % n;
  touched (p, spec);
}

static void
click_row (LyPrefs *p, guint index, POINT pt)
{
  const Row *spec = &g_array_index (p->rows, Row, index);
  RECT r = row_rect (p, index);
  RECT ctl = control_rect (p, r, spec);

  switch (spec->kind) {
    case ROW_TOGGLE: {
      gboolean *slot = bool_at (p, spec);
      *slot = !*slot;
      touched (p, spec);
      break;
    }
    case ROW_CHOICE:
      cycle_choice (p, spec, (GetKeyState (VK_SHIFT) & 0x8000) != 0);
      break;

    case ROW_NUMBER: {
      gboolean minus = pt.x < ctl.left + sc (p, 30);
      gboolean plus  = pt.x > ctl.right - sc (p, 30);
      if (!minus && !plus)
        break;
      double delta = (minus ? -spec->step : spec->step);
      if (spec->integer) {
        int *slot = int_at (p, spec);
        *slot = (int) CLAMP (*slot + delta, spec->min, spec->max);
      } else {
        double *slot = double_at (p, spec);
        /* Zoom is a factor in the file and a percentage on screen. */
        double shown = CLAMP (*slot * 100.0 + delta, spec->min, spec->max);
        *slot = shown / 100.0;
      }
      ly_config_touch (p->cfg);
      break;
    }

    case ROW_TEXT:
      begin_edit (p, index);
      return;

    case ROW_ACTION:
      if (spec->action)
        spec->action (p, spec->tag);
      return;

    case ROW_PAIR: {
      RECT second = button_rect (p, ctl, 1);
      RowAction chosen = PtInRect (&second, pt) ? spec->second : spec->action;
      if (chosen)
        chosen (p, spec->tag);
      return;
    }

    default:
      return;
  }
  InvalidateRect (p->hwnd, NULL, FALSE);
}

/* ------------------------------------------------------------ window proc */

static LRESULT CALLBACK
prefs_proc (HWND hwnd, UINT msg, WPARAM wp, LPARAM lp)
{
  LyPrefs *p = (LyPrefs *) GetWindowLongPtrW (hwnd, GWLP_USERDATA);

  switch (msg) {
    case WM_NCCREATE: {
      CREATESTRUCTW *cs = (CREATESTRUCTW *) lp;
      SetWindowLongPtrW (hwnd, GWLP_USERDATA, (LONG_PTR) cs->lpCreateParams);
      return DefWindowProcW (hwnd, msg, wp, lp);
    }

    case WM_CREATE:
      p->hwnd = hwnd;
      p->dpi = (int) GetDpiForWindow (hwnd);
      if (p->dpi <= 0)
        p->dpi = 96;
      ly_theme_load (&p->theme, ly_wants_dark (p->cfg));
      ly_fonts_make (&p->fonts, p->dpi);
      ly_apply_dark_titlebar (hwnd, p->theme.dark);

      p->edit = CreateWindowExW (0, L"EDIT", L"",
                                 WS_CHILD | ES_AUTOHSCROLL | ES_LEFT,
                                 0, 0, 10, 10, hwnd,
                                 (HMENU) (UINT_PTR) ID_EDIT, p->instance, NULL);
      SendMessageW (p->edit, WM_SETFONT, (WPARAM) p->fonts.small_, TRUE);
      SetWindowLongPtrW (p->edit, GWLP_USERDATA, (LONG_PTR) p);
      p->edit_proc = (WNDPROC) SetWindowLongPtrW (p->edit, GWLP_WNDPROC,
                                                  (LONG_PTR) edit_proc);
      rebuild_rows (p);
      return 0;

    case WM_SIZE:
      layout_edit (p);
      InvalidateRect (hwnd, NULL, FALSE);
      return 0;

    case WM_DPICHANGED: {
      p->dpi = HIWORD (wp);
      ly_fonts_make (&p->fonts, p->dpi);
      SendMessageW (p->edit, WM_SETFONT, (WPARAM) p->fonts.small_, TRUE);
      RECT *r = (RECT *) lp;
      SetWindowPos (hwnd, NULL, r->left, r->top, r->right - r->left,
                    r->bottom - r->top, SWP_NOZORDER | SWP_NOACTIVATE);
      InvalidateRect (hwnd, NULL, TRUE);
      return 0;
    }

    case WM_SETTINGCHANGE: {
      gboolean dark = ly_wants_dark (p->cfg);
      if (dark != p->theme.dark) {
        ly_theme_load (&p->theme, dark);
        ly_apply_dark_titlebar (hwnd, dark);
        InvalidateRect (hwnd, NULL, TRUE);
      }
      return 0;
    }

    case WM_CTLCOLOREDIT: {
      HDC dc = (HDC) wp;
      SetTextColor (dc, p->theme.text);
      SetBkColor (dc, p->theme.field);
      static HBRUSH brush;
      if (brush)
        DeleteObject (brush);
      brush = CreateSolidBrush (p->theme.field);
      return (LRESULT) brush;
    }

    case WM_ERASEBKGND:
      return 1;

    case WM_PAINT: {
      PAINTSTRUCT ps;
      HDC dc = BeginPaint (hwnd, &ps);
      RECT c;
      GetClientRect (hwnd, &c);
      LyCanvas buffer;
      if (ly_canvas_begin (&buffer, dc, c.right, c.bottom)) {
        paint (p, &buffer);
        ly_canvas_end (&buffer);
      } else {
        /* No DIB: draw straight to the DC. The primitives fall back to GDI,
         * which is uglier but is better than not painting at all. */
        LyCanvas plain = { .dc = dc, .px = NULL, .width = c.right,
                           .height = c.bottom,
                           .clip = { 0, 0, c.right, c.bottom } };
        paint (p, &plain);
      }
      EndPaint (hwnd, &ps);
      return 0;
    }

    case WM_MOUSEMOVE: {
      POINT pt = { GET_X_LPARAM (lp), GET_Y_LPARAM (lp) };
      int row = row_at (p, pt);
      if (row != p->hot) {
        p->hot = row;
        InvalidateRect (hwnd, NULL, FALSE);
        TRACKMOUSEEVENT tme = { sizeof tme, TME_LEAVE, hwnd, 0 };
        TrackMouseEvent (&tme);
      }
      return 0;
    }

    case WM_MOUSELEAVE:
      p->hot = -1;
      InvalidateRect (hwnd, NULL, FALSE);
      return 0;

    case WM_MOUSEWHEEL: {
      p->scroll = CLAMP (p->scroll - GET_WHEEL_DELTA_WPARAM (wp) / 2, 0, max_scroll (p));
      layout_edit (p);
      InvalidateRect (hwnd, NULL, FALSE);
      return 0;
    }

    case WM_LBUTTONDOWN: {
      POINT pt = { GET_X_LPARAM (lp), GET_Y_LPARAM (lp) };
      RECT side = sidebar_rect (p);
      if (PtInRect (&side, pt)) {
        for (int i = 0; i < CATEGORY_N; i++) {
          RECT r = category_rect (p, i);
          if (PtInRect (&r, pt) && i != p->category) {
            p->category = i;
            rebuild_rows (p);
            break;
          }
        }
        return 0;
      }
      int row = row_at (p, pt);
      if (row >= 0)
        click_row (p, (guint) row, pt);
      return 0;
    }

    case WM_CLOSE:
      DestroyWindow (hwnd);
      return 0;

    case WM_DESTROY:
      close_editor (p);       /* wipes the password it was showing */
      g_array_free (p->rows, TRUE);
      g_ptr_array_free (p->owned, TRUE);
      g_ptr_array_free (p->credentials, TRUE);
      if (p->sources)
        g_ptr_array_unref (p->sources);
      g_free (p->import_want);
      ly_fonts_free (&p->fonts);
      if (the_prefs == p)
        the_prefs = NULL;
      g_free (p);
      return 0;
  }
  return DefWindowProcW (hwnd, msg, wp, lp);
}

/* ------------------------------------------------------------------- API */

gboolean
ly_prefs_register (HINSTANCE instance)
{
  WNDCLASSEXW wc = { 0 };
  wc.cbSize = sizeof wc;
  wc.style = CS_HREDRAW | CS_VREDRAW;
  wc.lpfnWndProc = prefs_proc;
  wc.hInstance = instance;
  wc.hCursor = LoadCursorW (NULL, IDC_ARROW);
  wc.lpszClassName = PREFS_CLASS;
  wc.hIcon = LoadIconW (instance, L"APPICON");
  wc.hIconSm = wc.hIcon;
  return RegisterClassExW (&wc) != 0;
}

gboolean
ly_prefs_is_open (void)
{
  return the_prefs != NULL;
}

void
ly_prefs_show (HWND owner, HINSTANCE instance, LyConfig *cfg,
               LyStore *store, LyPasswords *passwords)
{
  if (the_prefs) {
    SetForegroundWindow (the_prefs->hwnd);
    return;
  }

  LyPrefs *p = g_new0 (LyPrefs, 1);
  p->instance = instance;
  p->cfg = cfg;
  p->store = store;
  p->passwords = passwords;
  p->hot = -1;
  p->editing = -1;
  p->dpi = 96;
  p->rows = g_array_new (FALSE, TRUE, sizeof (Row));
  p->owned = g_ptr_array_new_with_free_func (g_free);
  p->credentials = g_ptr_array_new_with_free_func ((GDestroyNotify) ly_credential_free);

  the_prefs = p;
  HWND hwnd = CreateWindowExW (
      0, PREFS_CLASS, L"Lyndon Settings", WS_OVERLAPPEDWINDOW,
      CW_USEDEFAULT, CW_USEDEFAULT, 880, 660, owner, NULL, instance, p);
  if (hwnd == NULL) {
    the_prefs = NULL;
    g_array_free (p->rows, TRUE);
    g_ptr_array_free (p->owned, TRUE);
    g_ptr_array_free (p->credentials, TRUE);
    g_free (p);
    return;
  }
  ShowWindow (hwnd, SW_SHOW);
  UpdateWindow (hwnd);
}
