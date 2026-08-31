/* prefs.c — see prefs.h. */

#include "prefs.h"
#include "ui.h"
#include "import.h"

#include <windowsx.h>
#include <shlobj.h>
#include <shellapi.h>   /* ShellExecuteW */
#include <string.h>

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

  LyTheme     theme;
  LyFonts     fonts;
  int         dpi;
};

static LyPrefs *the_prefs;     /* one window, like the Linux build */

static void rebuild_rows (LyPrefs *p);
static void layout_edit (LyPrefs *p);

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

static gboolean *
bool_at (LyPrefs *p, const Row *row)
{
  return (gboolean *) ((char *) p->cfg + row->offset);
}

static int *
int_at (LyPrefs *p, const Row *row)
{
  return (int *) ((char *) p->cfg + row->offset);
}

static double *
double_at (LyPrefs *p, const Row *row)
{
  return (double *) ((char *) p->cfg + row->offset);
}

static char **
string_at (LyPrefs *p, const Row *row)
{
  return (char **) ((char *) p->cfg + row->offset);
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

static void
build_passwords_page (LyPrefs *p)
{
  push_table (p, PASSWORDS_TOP, G_N_ELEMENTS (PASSWORDS_TOP));

  g_ptr_array_set_size (p->credentials, 0);
  ly_passwords_list (p->passwords, collect_credentials, p);

  Row group = { ROW_GROUP, "Saved logins", NULL };
  push (p, group);

  if (p->credentials->len == 0) {
    Row none = { ROW_NOTE, "Nothing saved yet",
                 "Sign in somewhere and Lyndon will offer to remember it." };
    push (p, none);
  }
  for (guint i = 0; i < p->credentials->len; i++) {
    LyCredential *c = g_ptr_array_index (p->credentials, i);
    Row row = { 0 };
    row.kind = ROW_ACTION;
    row.title = own (p, g_strdup (c->origin));
    row.subtitle = own (p, g_strdup_printf ("%s — remove",
                                            (c->username && *c->username)
                                              ? c->username : "(no username)"));
    row.action = action_forget_password;
    row.tag = (int) i;
    push (p, row);
  }

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

static void
build_import_page (LyPrefs *p)
{
  Row group = { ROW_GROUP, "Bring things over", NULL };
  push (p, group);

  if (p->sources)
    g_ptr_array_unref (p->sources);
  p->sources = ly_import_sources ();

  if (p->sources->len == 0) {
    Row none = { ROW_NOTE, "No other browsers found",
                 "Lyndon looks for Chrome, Edge, Brave, Vivaldi, Opera and "
                 "Firefox profiles in the places their installers use." };
    push (p, none);
    return;
  }

  for (guint i = 0; i < p->sources->len; i++) {
    LyImportSource *s = g_ptr_array_index (p->sources, i);
    Row row = { 0 };
    row.kind = ROW_ACTION;
    row.title = s->label;
    row.subtitle = own (p, g_strdup_printf ("Import %s%s%s",
                        s->has_bookmarks ? "bookmarks" : "",
                        (s->has_bookmarks && s->has_history) ? " and " : "",
                        s->has_history ? "history" : ""));
    row.action = action_import;
    row.tag = (int) i;
    push (p, row);
  }

  Row note = { ROW_NOTE, "Nothing is changed in the other browser",
               "Both keep their files locked while running, so Lyndon reads "
               "from a private copy and deletes it afterwards." };
  push (p, note);
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
  int w = sc (p, spec->kind == ROW_TOGGLE ? SWITCH_W : 150);
  int h = sc (p, spec->kind == ROW_TOGGLE ? SWITCH_H : 28);
  int cy = (row.top + row.bottom) / 2;
  RECT r = { row.right - sc (p, 12) - w, cy - h / 2, row.right - sc (p, 12), cy + h / 2 };
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
        ly_round (cv, ctl, sc (p, 6), p->theme.field, 1.0);
        RECT t = ctl;
        t.left += sc (p, 8);
        t.right -= sc (p, 8);
        const char *shown = (value && *value && **value) ? *value : "—";
        ly_text (cv, t, shown, (value && *value && **value) ? ink : p->theme.text_dim,
                 p->fonts.small_,
                 DT_SINGLELINE | DT_VCENTER | DT_END_ELLIPSIS | DT_NOPREFIX);
        break;
      }

      case ROW_ACTION: {
        RECT b = ctl;
        b.left = ctl.right - sc (p, 90);
        ly_round (cv, b, sc (p, 6), hot ? p->theme.accent : p->theme.field, 1.0);
        ly_text (cv, b, "Go", hot ? p->theme.accent_text : p->theme.text,
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
  ly_config_touch (p->cfg);

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
action_import (LyPrefs *p, int index)
{
  if (p->sources == NULL || index < 0 || (guint) index >= p->sources->len)
    return;
  const LyImportSource *source = g_ptr_array_index (p->sources, (guint) index);

  LyImportResult result = { 0 };
  SetCursor (LoadCursorW (NULL, IDC_WAIT));
  gboolean ok = ly_import_run (p->store, source, source->has_bookmarks,
                               source->has_history, &result);
  SetCursor (LoadCursorW (NULL, IDC_ARROW));

  g_autofree char *message = ok
    ? g_strdup_printf ("Imported %u bookmark%s and %u page%s of history from %s.",
                       result.bookmarks, result.bookmarks == 1 ? "" : "s",
                       result.history, result.history == 1 ? "" : "s",
                       source->label)
    : g_strdup_printf ("Could not import from %s.\n\n%s", source->label,
                       result.error ? result.error : "Unknown error.");
  g_autofree wchar_t *w = (wchar_t *) g_utf8_to_utf16 (message, -1, NULL, NULL, NULL);
  MessageBoxW (p->hwnd, w, L"Lyndon",
               MB_OK | (ok ? MB_ICONINFORMATION : MB_ICONERROR));
  ly_import_result_clear (&result);
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
  ly_config_touch (p->cfg);
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
      ly_config_touch (p->cfg);
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
      g_array_free (p->rows, TRUE);
      g_ptr_array_free (p->owned, TRUE);
      g_ptr_array_free (p->credentials, TRUE);
      if (p->sources)
        g_ptr_array_unref (p->sources);
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
