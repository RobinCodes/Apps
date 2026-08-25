/* prefs.c — see prefs.h.
 *
 * Built in code rather than from a .ui file: the dialog is mostly a mechanical
 * mapping from configuration fields to rows, and expressing that as a few
 * helpers is shorter, and far easier to keep in step with the config struct,
 * than the equivalent XML.
 */

#include "prefs.h"
#include "blocker.h"
#include "passwords.h"
#include "import.h"

#include <string.h>

typedef struct {
  LyApp     *app;
  LyConfig  *cfg;
  AdwPreferencesDialog *dialog;
  AdwPreferencesGroup  *subs_group;
  GPtrArray            *subs_rows;   /* GtkWidget*, so they can be removed again */
  AdwPreferencesGroup  *login_group;
  GPtrArray            *login_rows;
  AdwPreferencesGroup  *keyword_group;
  GPtrArray            *keyword_rows;
} Prefs;

typedef struct {
  Prefs    *prefs;
  gboolean *flag;
  int      *choice;
  double   *number;
  int      *integer;
  char    **string;
} Binding;

static void refresh_subscriptions (Prefs *prefs);

/* GClosureNotify rather than a cast of g_free: the signatures genuinely
 * differ, and casting between them is undefined behaviour. */
static void
free_binding (gpointer data, GClosure *closure)
{
  g_free (data);
}

static void
free_prefs (gpointer data)
{
  Prefs *prefs = data;
  g_clear_pointer (&prefs->subs_rows, g_ptr_array_unref);
  g_clear_pointer (&prefs->login_rows, g_ptr_array_unref);
  g_clear_pointer (&prefs->keyword_rows, g_ptr_array_unref);
  g_free (prefs);
}

static void
changed (Prefs *prefs, gboolean rebuild_rules)
{
  ly_config_touch (prefs->cfg);
  if (rebuild_rules)
    ly_blocker_rebuild (ly_app_blocker (prefs->app));
  ly_app_refresh (prefs->app);
}

/* ----------------------------------------------------------- row makers */

static void
on_switch_changed (GObject *row, GParamSpec *pspec, gpointer data)
{
  Binding *b = data;
  *b->flag = adw_switch_row_get_active (ADW_SWITCH_ROW (row));
  changed (b->prefs, TRUE);
}

static GtkWidget *
bool_row (Prefs *prefs, AdwPreferencesGroup *group,
          const char *title, const char *subtitle, gboolean *field)
{
  GtkWidget *row = adw_switch_row_new ();
  adw_preferences_row_set_title (ADW_PREFERENCES_ROW (row), title);
  if (subtitle != NULL)
    adw_action_row_set_subtitle (ADW_ACTION_ROW (row), subtitle);
  adw_switch_row_set_active (ADW_SWITCH_ROW (row), *field);

  Binding *b = g_new0 (Binding, 1);
  b->prefs = prefs;
  b->flag  = field;
  g_signal_connect_data (row, "notify::active", G_CALLBACK (on_switch_changed),
                         b, free_binding, 0);

  adw_preferences_group_add (group, row);
  return row;
}

static void
on_combo_changed (GObject *row, GParamSpec *pspec, gpointer data)
{
  Binding *b = data;
  *b->choice = (int) adw_combo_row_get_selected (ADW_COMBO_ROW (row));
  changed (b->prefs, TRUE);
}

static GtkWidget *
enum_row (Prefs *prefs, AdwPreferencesGroup *group,
          const char *title, const char *subtitle,
          const char *const *labels, int *field)
{
  GtkWidget *row = adw_combo_row_new ();
  adw_preferences_row_set_title (ADW_PREFERENCES_ROW (row), title);
  if (subtitle != NULL)
    adw_action_row_set_subtitle (ADW_ACTION_ROW (row), subtitle);

  GtkStringList *model = gtk_string_list_new (labels);
  adw_combo_row_set_model (ADW_COMBO_ROW (row), G_LIST_MODEL (model));
  adw_combo_row_set_selected (ADW_COMBO_ROW (row), (guint) *field);

  Binding *b = g_new0 (Binding, 1);
  b->prefs  = prefs;
  b->choice = field;
  g_signal_connect_data (row, "notify::selected", G_CALLBACK (on_combo_changed),
                         b, free_binding, 0);

  adw_preferences_group_add (group, row);
  return row;
}

static void
on_spin_changed (GObject *row, GParamSpec *pspec, gpointer data)
{
  Binding *b = data;
  double value = adw_spin_row_get_value (ADW_SPIN_ROW (row));
  if (b->number != NULL)
    *b->number = value;
  else if (b->integer != NULL)
    *b->integer = (int) value;
  changed (b->prefs, FALSE);
}

static GtkWidget *
spin_row (Prefs *prefs, AdwPreferencesGroup *group,
          const char *title, const char *subtitle,
          double min, double max, double step,
          double *number, int *integer)
{
  GtkWidget *row = adw_spin_row_new_with_range (min, max, step);
  adw_preferences_row_set_title (ADW_PREFERENCES_ROW (row), title);
  if (subtitle != NULL)
    adw_action_row_set_subtitle (ADW_ACTION_ROW (row), subtitle);
  adw_spin_row_set_value (ADW_SPIN_ROW (row), number ? *number : (double) *integer);
  if (number != NULL)
    adw_spin_row_set_digits (ADW_SPIN_ROW (row), 2);

  Binding *b = g_new0 (Binding, 1);
  b->prefs   = prefs;
  b->number  = number;
  b->integer = integer;
  g_signal_connect_data (row, "notify::value", G_CALLBACK (on_spin_changed),
                         b, free_binding, 0);

  adw_preferences_group_add (group, row);
  return row;
}

static void
on_entry_changed (GObject *row, GParamSpec *pspec, gpointer data)
{
  Binding *b = data;
  g_free (*b->string);
  *b->string = g_strdup (gtk_editable_get_text (GTK_EDITABLE (row)));
  changed (b->prefs, FALSE);
}

static GtkWidget *
entry_row (Prefs *prefs, AdwPreferencesGroup *group,
           const char *title, char **field)
{
  GtkWidget *row = adw_entry_row_new ();
  adw_preferences_row_set_title (ADW_PREFERENCES_ROW (row), title);
  gtk_editable_set_text (GTK_EDITABLE (row), *field ?: "");

  Binding *b = g_new0 (Binding, 1);
  b->prefs  = prefs;
  b->string = field;
  g_signal_connect_data (row, "notify::text", G_CALLBACK (on_entry_changed),
                         b, free_binding, 0);

  adw_preferences_group_add (group, row);
  return row;
}

static GtkWidget *
button_row (AdwPreferencesGroup *group, const char *title, const char *subtitle,
            const char *button_label, GCallback callback, gpointer data)
{
  GtkWidget *row = adw_action_row_new ();
  adw_preferences_row_set_title (ADW_PREFERENCES_ROW (row), title);
  if (subtitle != NULL)
    adw_action_row_set_subtitle (ADW_ACTION_ROW (row), subtitle);

  GtkWidget *button = gtk_button_new_with_label (button_label);
  gtk_widget_set_valign (button, GTK_ALIGN_CENTER);
  g_signal_connect (button, "clicked", callback, data);
  adw_action_row_add_suffix (ADW_ACTION_ROW (row), button);
  adw_preferences_row_set_use_underline (ADW_PREFERENCES_ROW (row), FALSE);

  adw_preferences_group_add (group, row);
  return row;
}

static AdwPreferencesGroup *
add_group (AdwPreferencesPage *page, const char *title, const char *description)
{
  AdwPreferencesGroup *group = ADW_PREFERENCES_GROUP (adw_preferences_group_new ());
  adw_preferences_group_set_title (group, title);
  if (description != NULL)
    adw_preferences_group_set_description (group, description);
  adw_preferences_page_add (page, group);
  return group;
}

static AdwPreferencesPage *
add_page (Prefs *prefs, const char *title, const char *icon)
{
  AdwPreferencesPage *page = ADW_PREFERENCES_PAGE (adw_preferences_page_new ());
  adw_preferences_page_set_title (page, title);
  adw_preferences_page_set_icon_name (page, icon);
  adw_preferences_dialog_add (prefs->dialog, page);
  return page;
}

/* ------------------------------------------------------------ appearance */

static void
build_appearance (Prefs *prefs)
{
  AdwPreferencesPage *page = add_page (prefs, "Appearance", "applications-graphics-symbolic");

  AdwPreferencesGroup *theme = add_group (page, "Theme", NULL);
  static const char *const SCHEMES[] = { "Follow system", "Light", "Dark", NULL };
  enum_row (prefs, theme, "Colour scheme", NULL, SCHEMES, (int *) &prefs->cfg->scheme);

  static const char *const DARK_MODES[] = {
    "Off", "Smart — only sites without a dark theme", "Always", NULL
  };
  enum_row (prefs, theme, "Enforce dark mode on sites",
            "Smart measures what the page actually painted and leaves proper "
            "dark themes alone. Inversion can disturb fixed-position layouts.",
            DARK_MODES, (int *) &prefs->cfg->force_dark);

  AdwPreferencesGroup *effects = add_group (page, "Effects",
    "Each step down removes GPU work. Translucency needs a compositor; without "
    "one it degrades to a flat tint rather than breaking.");
  static const char *const FX[] = {
    "Full — translucent chrome, shadows, animation",
    "Reduced — opaque, shadows, animation",
    "Off — flat, no transitions",
    NULL
  };
  enum_row (prefs, effects, "Visual effects", NULL, FX, (int *) &prefs->cfg->effects);
  spin_row (prefs, effects, "Chrome opacity",
            "Only applies at the Full effect level.",
            0.35, 1.0, 0.01, &prefs->cfg->ui_opacity, NULL);

  AdwPreferencesGroup *layout = add_group (page, "Layout", NULL);
  bool_row (prefs, layout, "Compact toolbar",
            "Shorter header, more room for the page.", &prefs->cfg->compact_chrome);
  bool_row (prefs, layout, "Show the bookmarks bar",
            "A row of your bookmarks under the address bar.",
            &prefs->cfg->show_bookmarks_bar);
  bool_row (prefs, layout, "Always show the tab strip",
            "Off hides it whenever a single tab is open.",
            &prefs->cfg->show_tab_bar_single);
}

/* ------------------------------------------------------------------ web */

static void
build_web (Prefs *prefs)
{
  AdwPreferencesPage *page = add_page (prefs, "Web", "web-browser-symbolic");

  AdwPreferencesGroup *content = add_group (page, "Content", NULL);
  bool_row (prefs, content, "JavaScript", NULL, &prefs->cfg->javascript);
  bool_row (prefs, content, "Images", NULL, &prefs->cfg->images);
  bool_row (prefs, content, "WebGL",
            "A large fingerprinting surface. Off unless you need 3D content.",
            &prefs->cfg->webgl);
  bool_row (prefs, content, "WebRTC",
            "Can reveal local network addresses to any page that asks.",
            &prefs->cfg->webrtc);
  bool_row (prefs, content, "Web Audio", NULL, &prefs->cfg->webaudio);
  bool_row (prefs, content, "Autoplay media",
            "Off requires a click before video or audio starts.",
            &prefs->cfg->media_autoplay);

  AdwPreferencesGroup *perf = add_group (page, "Performance", NULL);
  static const char *const HW[] = { "Automatic", "Always", "Never", NULL };
  enum_row (prefs, perf, "Hardware acceleration",
            "Never falls back to CPU rendering — slower, but it uses no GPU memory.",
            HW, (int *) &prefs->cfg->hw_accel);
  bool_row (prefs, perf, "Smooth scrolling", NULL, &prefs->cfg->smooth_scrolling);
  bool_row (prefs, perf, "Keep pages in memory when navigating back",
            "Faster back and forward, at the cost of resident memory.",
            &prefs->cfg->page_cache);

  AdwPreferencesGroup *text = add_group (page, "Text", NULL);
  spin_row (prefs, text, "Default zoom", NULL, 0.3, 3.0, 0.05,
            &prefs->cfg->default_zoom, NULL);
  spin_row (prefs, text, "Minimum font size", "0 leaves the site's choice alone.",
            0, 32, 1, NULL, &prefs->cfg->minimum_font_size);

  AdwPreferencesGroup *start = add_group (page, "Startup and tabs", NULL);
  entry_row (prefs, start, "Home and new tab page", &prefs->cfg->homepage);
  bool_row (prefs, start, "Reopen tabs on launch", NULL, &prefs->cfg->restore_session);
  bool_row (prefs, start, "Show a home button", NULL, &prefs->cfg->show_home_button);
  bool_row (prefs, start, "Remember zoom per site", NULL, &prefs->cfg->per_site_zoom);

  AdwPreferencesGroup *tools = add_group (page, "Tools", NULL);
  bool_row (prefs, tools, "Developer tools",
            "Adds Inspect Element to the context menu.", &prefs->cfg->developer_tools);
  bool_row (prefs, tools, "Spell checking", NULL, &prefs->cfg->spell_check);
}

/* ---------------------------------------------------------------- import */

typedef struct {
  Prefs          *prefs;
  LyImportSource *source;   /* owned by the sources array below */
  GtkWidget      *button;
} ImportRow;

static void
on_import_clicked (GtkButton *button, gpointer data)
{
  ImportRow *row = data;
  Prefs *prefs = row->prefs;

  gtk_widget_set_sensitive (GTK_WIDGET (button), FALSE);
  gtk_button_set_label (button, "Importing…");

  LyImportResult result;
  gboolean ok = ly_import_run (ly_app_store (prefs->app), row->source,
                               row->source->has_bookmarks,
                               row->source->has_history,
                               &result);

  g_autofree char *message = NULL;
  if (!ok) {
    message = g_strdup_printf ("Import failed: %s", result.error ?: "unknown error");
    gtk_button_set_label (button, "Retry");
    gtk_widget_set_sensitive (GTK_WIDGET (button), TRUE);
  } else {
    message = g_strdup_printf ("Imported %u bookmark%s and %u page%s of history",
                               result.bookmarks, result.bookmarks == 1 ? "" : "s",
                               result.history,   result.history == 1 ? "" : "s");
    gtk_button_set_label (button, "Imported");
  }
  ly_import_result_clear (&result);

  adw_preferences_dialog_add_toast (prefs->dialog, adw_toast_new (message));
}

static void
on_import_clicked_free (gpointer data, GClosure *closure)
{
  g_free (data);
}

static void
on_import_dialog (GtkButton *button, gpointer data)
{
  Prefs *prefs = data;

  AdwDialog *dialog = adw_dialog_new ();
  adw_dialog_set_title (dialog, "Import");
  adw_dialog_set_content_width (dialog, 520);

  AdwPreferencesPage *page = ADW_PREFERENCES_PAGE (adw_preferences_page_new ());
  AdwPreferencesGroup *group = ADW_PREFERENCES_GROUP (adw_preferences_group_new ());
  adw_preferences_group_set_title (group, "Browsers found on this computer");
  adw_preferences_group_set_description (group,
    "Bookmarks and history are copied in, with their original visit counts and "
    "dates. Nothing is sent anywhere, and the other browser's files are only "
    "ever read from a temporary copy.");

  GPtrArray *sources = ly_import_sources ();
  /* Keep the array alive as long as the dialog: the rows point into it. */
  g_object_set_data_full (G_OBJECT (dialog), "lyndon-sources", sources,
                          (GDestroyNotify) g_ptr_array_unref);

  if (sources->len == 0) {
    GtkWidget *row = adw_action_row_new ();
    adw_preferences_row_set_title (ADW_PREFERENCES_ROW (row), "No other browsers found");
    adw_action_row_set_subtitle (ADW_ACTION_ROW (row),
      "Looked for Chrome, Chromium, Brave, Edge, Vivaldi and Firefox profiles.");
    adw_preferences_group_add (group, row);
  }

  for (guint i = 0; i < sources->len; i++) {
    LyImportSource *source = g_ptr_array_index (sources, i);

    GtkWidget *row = adw_action_row_new ();
    adw_preferences_row_set_title (ADW_PREFERENCES_ROW (row), source->label);

    g_autofree char *detail = g_strdup_printf ("%s%s%s",
      source->has_bookmarks ? "bookmarks" : "",
      (source->has_bookmarks && source->has_history) ? " and " : "",
      source->has_history ? "history" : "");
    adw_action_row_set_subtitle (ADW_ACTION_ROW (row), detail);

    GtkWidget *import = gtk_button_new_with_label ("Import");
    gtk_widget_set_valign (import, GTK_ALIGN_CENTER);
    gtk_widget_add_css_class (import, "suggested-action");

    ImportRow *context = g_new0 (ImportRow, 1);
    context->prefs  = prefs;
    context->source = source;
    context->button = import;
    g_signal_connect_data (import, "clicked", G_CALLBACK (on_import_clicked),
                           context, on_import_clicked_free, 0);

    adw_action_row_add_suffix (ADW_ACTION_ROW (row), import);
    adw_preferences_group_add (group, row);
  }

  adw_preferences_page_add (page, group);

  AdwToolbarView *toolbar = ADW_TOOLBAR_VIEW (adw_toolbar_view_new ());
  adw_toolbar_view_add_top_bar (toolbar, adw_header_bar_new ());
  adw_toolbar_view_set_content (toolbar, GTK_WIDGET (page));
  adw_dialog_set_child (dialog, GTK_WIDGET (toolbar));

  adw_dialog_present (dialog, GTK_WIDGET (prefs->dialog));
}


/* ------------------------------------------------------- search keywords */

static void refresh_keywords (Prefs *prefs);

static void
on_remove_keyword (GtkButton *button, gpointer data)
{
  Prefs *prefs = data;
  const char *entry = g_object_get_data (G_OBJECT (button), "lyndon-entry");

  for (guint i = 0; i < prefs->cfg->search_keywords->len; i++) {
    if (g_strcmp0 (g_ptr_array_index (prefs->cfg->search_keywords, i), entry) == 0) {
      g_ptr_array_remove_index (prefs->cfg->search_keywords, i);
      break;
    }
  }
  ly_config_touch (prefs->cfg);
  refresh_keywords (prefs);
}

static void
on_add_keyword (GtkWidget *row, gpointer data)
{
  Prefs *prefs = data;
  const char *text = gtk_editable_get_text (GTK_EDITABLE (row));

  const char *equals = text ? strchr (text, '=') : NULL;
  if (equals == NULL || equals == text || strstr (equals, "%s") == NULL) {
    adw_preferences_dialog_add_toast (prefs->dialog,
      adw_toast_new ("Use  keyword=https://example.com/search?q=%s"));
    return;
  }

  g_ptr_array_add (prefs->cfg->search_keywords, g_strdup (text));
  gtk_editable_set_text (GTK_EDITABLE (row), "");
  ly_config_touch (prefs->cfg);
  refresh_keywords (prefs);
}

static void
refresh_keywords (Prefs *prefs)
{
  if (prefs->keyword_group == NULL)
    return;

  for (guint i = 0; i < prefs->keyword_rows->len; i++)
    adw_preferences_group_remove (prefs->keyword_group,
                                  g_ptr_array_index (prefs->keyword_rows, i));
  g_ptr_array_set_size (prefs->keyword_rows, 0);

  for (guint i = 0; i < prefs->cfg->search_keywords->len; i++) {
    const char *entry = g_ptr_array_index (prefs->cfg->search_keywords, i);
    const char *equals = strchr (entry, '=');

    GtkWidget *row = adw_action_row_new ();
    g_autofree char *name = equals ? g_strndup (entry, (size_t) (equals - entry))
                                   : g_strdup (entry);
    adw_preferences_row_set_title (ADW_PREFERENCES_ROW (row), name);
    adw_action_row_set_subtitle (ADW_ACTION_ROW (row), equals ? equals + 1 : "");

    GtkWidget *remove = gtk_button_new_from_icon_name ("user-trash-symbolic");
    gtk_widget_add_css_class (remove, "flat");
    gtk_widget_set_valign (remove, GTK_ALIGN_CENTER);
    g_object_set_data_full (G_OBJECT (remove), "lyndon-entry", g_strdup (entry), g_free);
    g_signal_connect (remove, "clicked", G_CALLBACK (on_remove_keyword), prefs);
    adw_action_row_add_suffix (ADW_ACTION_ROW (row), remove);

    adw_preferences_group_add (prefs->keyword_group, row);
    g_ptr_array_add (prefs->keyword_rows, row);
  }

  GtkWidget *add = adw_entry_row_new ();
  adw_preferences_row_set_title (ADW_PREFERENCES_ROW (add),
                                 "keyword=https://example.com/?q=%s");
  g_signal_connect (add, "entry-activated", G_CALLBACK (on_add_keyword), prefs);
  adw_preferences_group_add (prefs->keyword_group, add);
  g_ptr_array_add (prefs->keyword_rows, add);
}

/* -------------------------------------------------------------- privacy */

static void
on_clear_confirmed (AdwAlertDialog *dialog, const char *response, gpointer data)
{
  Prefs *prefs = data;
  if (g_strcmp0 (response, "clear") != 0)
    return;

  ly_engine_clear_data (ly_app_engine (prefs->app), WEBKIT_WEBSITE_DATA_ALL, NULL, NULL);
  adw_preferences_dialog_add_toast (prefs->dialog,
                                    adw_toast_new ("Browsing data cleared"));
}

static void
on_clear_clicked (GtkButton *button, gpointer data)
{
  Prefs *prefs = data;
  AdwDialog *dialog = adw_alert_dialog_new ("Clear browsing data?", NULL);
  adw_alert_dialog_set_body (ADW_ALERT_DIALOG (dialog),
    "Removes cookies, caches, local storage, service workers and the tracking "
    "prevention database. Open pages are not reloaded.");
  adw_alert_dialog_add_responses (ADW_ALERT_DIALOG (dialog),
                                  "cancel", "Cancel", "clear", "Clear", NULL);
  adw_alert_dialog_set_response_appearance (ADW_ALERT_DIALOG (dialog), "clear",
                                            ADW_RESPONSE_DESTRUCTIVE);
  adw_alert_dialog_set_default_response (ADW_ALERT_DIALOG (dialog), "cancel");
  g_signal_connect (dialog, "response", G_CALLBACK (on_clear_confirmed), prefs);
  adw_dialog_present (dialog, GTK_WIDGET (prefs->dialog));
}

typedef struct { Prefs *prefs; const char *name, *url; } SearchChoice;

static const SearchChoice SEARCH_ENGINES[] = {
  { NULL, "DuckDuckGo", "https://duckduckgo.com/?q=%s" },
  { NULL, "Startpage",  "https://www.startpage.com/sp/search?query=%s" },
  { NULL, "Brave",      "https://search.brave.com/search?q=%s" },
  { NULL, "Mojeek",     "https://www.mojeek.com/search?q=%s" },
  { NULL, "Wikipedia",  "https://en.wikipedia.org/w/index.php?search=%s" },
  { NULL, "Google",     "https://www.google.com/search?q=%s" },
};

static void
on_search_engine_changed (GObject *row, GParamSpec *pspec, gpointer data)
{
  Prefs *prefs = data;
  guint index = adw_combo_row_get_selected (ADW_COMBO_ROW (row));

  if (index < G_N_ELEMENTS (SEARCH_ENGINES)) {
    g_free (prefs->cfg->search_name);
    g_free (prefs->cfg->search_url);
    prefs->cfg->search_name = g_strdup (SEARCH_ENGINES[index].name);
    prefs->cfg->search_url  = g_strdup (SEARCH_ENGINES[index].url);
    changed (prefs, FALSE);
  }
}

static void
build_privacy (Prefs *prefs)
{
  AdwPreferencesPage *page = add_page (prefs, "Privacy", "channel-secure-symbolic");

  AdwPreferencesGroup *cookies = add_group (page, "Cookies", NULL);
  static const char *const COOKIE_MODES[] = {
    "Block all", "First party only", "Allow all", NULL
  };
  enum_row (prefs, cookies, "Accept cookies",
            "First party only is the usual choice: sites keep working, "
            "cross-site tracking cookies do not.",
            COOKIE_MODES, (int *) &prefs->cfg->cookie_policy);
  bool_row (prefs, cookies, "Clear everything on exit", NULL, &prefs->cfg->clear_on_exit);

  AdwPreferencesGroup *tracking = add_group (page, "Tracking", NULL);
  bool_row (prefs, tracking, "Intelligent tracking prevention",
            "WebKit's own classifier: partitions and expires storage for "
            "domains it sees tracking across sites.", &prefs->cfg->itp);
  bool_row (prefs, tracking, "Send Global Privacy Control",
            "Declares an opt-out of sale and sharing. Legally binding in some "
            "jurisdictions, advisory elsewhere.", &prefs->cfg->gpc);
  bool_row (prefs, tracking, "Trim referrers",
            "Cross-site requests carry only the origin, never the path.",
            &prefs->cfg->trim_referrer);
  bool_row (prefs, tracking, "HTTPS-only",
            "Rewrites plain http:// to https:// in the network process, before "
            "any cleartext request leaves the machine.",
            &prefs->cfg->https_only);
  bool_row (prefs, tracking, "Resist fingerprinting",
            "Reports generic CPU and memory values, adds imperceptible noise to "
            "canvas readback, and masks the GPU model.",
            &prefs->cfg->fingerprint_defence);

  AdwPreferencesGroup *identity = add_group (page, "Identity", NULL);
  static const char *const UA_MODES[] = {
    "Lyndon (default)", "Generic Safari", "Custom", NULL
  };
  enum_row (prefs, identity, "User agent", NULL, UA_MODES, (int *) &prefs->cfg->ua_mode);
  entry_row (prefs, identity, "Custom user agent", &prefs->cfg->ua_custom);
  bool_row (prefs, identity, "Remember history",
            "Powers address-bar suggestions. Off stops recording entirely.",
            &prefs->cfg->remember_history);

  AdwPreferencesGroup *network = add_group (page, "Network", NULL);
  static const char *const PROXY_MODES[] = {
    "Use the system proxy", "No proxy", "Custom", NULL
  };
  enum_row (prefs, network, "Proxy", NULL, PROXY_MODES, (int *) &prefs->cfg->proxy_mode);
  entry_row (prefs, network, "Proxy URL (http://host:port)", &prefs->cfg->proxy_url);
  entry_row (prefs, network, "Languages sent to sites",
             &prefs->cfg->languages);

  AdwPreferencesGroup *search = add_group (page, "Search", NULL);
  GtkWidget *row = adw_combo_row_new ();
  adw_preferences_row_set_title (ADW_PREFERENCES_ROW (row), "Search engine");

  const char *names[G_N_ELEMENTS (SEARCH_ENGINES) + 1];
  guint selected = 0;
  for (guint i = 0; i < G_N_ELEMENTS (SEARCH_ENGINES); i++) {
    names[i] = SEARCH_ENGINES[i].name;
    if (g_strcmp0 (prefs->cfg->search_name, SEARCH_ENGINES[i].name) == 0)
      selected = i;
  }
  names[G_N_ELEMENTS (SEARCH_ENGINES)] = NULL;

  adw_combo_row_set_model (ADW_COMBO_ROW (row), G_LIST_MODEL (gtk_string_list_new (names)));
  adw_combo_row_set_selected (ADW_COMBO_ROW (row), selected);
  g_signal_connect (row, "notify::selected", G_CALLBACK (on_search_engine_changed), prefs);
  adw_preferences_group_add (search, row);
  entry_row (prefs, search, "Search URL (%s is the query)", &prefs->cfg->search_url);

  prefs->keyword_group = add_group (page, "Search keywords",
    "Type the keyword, a space, then your query — \u201cw lyndon word\u201d searches "
    "Wikipedia if you add  w=…  below.");
  refresh_keywords (prefs);

  AdwPreferencesGroup *data = add_group (page, "Stored data", NULL);
  button_row (data, "Import from another browser",
              "Bookmarks and history from Chrome, Firefox and friends.",
              "Import…", G_CALLBACK (on_import_dialog), prefs);
  button_row (data, "Clear browsing data",
              "Cookies, caches, storage and the tracking database.",
              "Clear…", G_CALLBACK (on_clear_clicked), prefs);
}


/* ------------------------------------------------------------- blocking */

static void
on_rebuild_clicked (GtkButton *button, gpointer data)
{
  Prefs *prefs = data;
  ly_blocker_rebuild (ly_app_blocker (prefs->app));
  adw_preferences_dialog_add_toast (prefs->dialog, adw_toast_new ("Rebuilding rules…"));
}

static void
on_update_clicked (GtkButton *button, gpointer data)
{
  Prefs *prefs = data;
  ly_blocker_update_subscriptions (ly_app_blocker (prefs->app), TRUE);
  adw_preferences_dialog_add_toast (prefs->dialog, adw_toast_new ("Fetching filter lists…"));
}

static void
on_edit_rules_clicked (GtkButton *button, gpointer data)
{
  g_autofree char *path = ly_blocker_custom_rules_path ();
  g_autofree char *uri = g_filename_to_uri (path, NULL, NULL);
  if (uri != NULL) {
    g_autoptr (GtkUriLauncher) launcher = gtk_uri_launcher_new (uri);
    gtk_uri_launcher_launch (launcher, NULL, NULL, NULL, NULL);
  }
}

static void
on_remove_subscription (GtkButton *button, gpointer data)
{
  Prefs *prefs = data;
  const char *url = g_object_get_data (G_OBJECT (button), "lyndon-url");

  for (guint i = 0; i < prefs->cfg->subscriptions->len; i++) {
    if (g_strcmp0 (g_ptr_array_index (prefs->cfg->subscriptions, i), url) == 0) {
      g_ptr_array_remove_index (prefs->cfg->subscriptions, i);
      break;
    }
  }
  changed (prefs, TRUE);
  refresh_subscriptions (prefs);
}

static void
on_add_subscription (GtkWidget *row, gpointer data)
{
  Prefs *prefs = data;
  const char *text = gtk_editable_get_text (GTK_EDITABLE (row));

  if (text == NULL || (!g_str_has_prefix (text, "https://") &&
                       !g_str_has_prefix (text, "http://"))) {
    adw_preferences_dialog_add_toast (prefs->dialog,
                                      adw_toast_new ("Enter a http(s) URL"));
    return;
  }

  for (guint i = 0; i < prefs->cfg->subscriptions->len; i++)
    if (g_strcmp0 (g_ptr_array_index (prefs->cfg->subscriptions, i), text) == 0)
      return;

  g_ptr_array_add (prefs->cfg->subscriptions, g_strdup (text));
  gtk_editable_set_text (GTK_EDITABLE (row), "");
  ly_config_touch (prefs->cfg);
  ly_blocker_update_subscriptions (ly_app_blocker (prefs->app), TRUE);
  refresh_subscriptions (prefs);
}

static void
refresh_subscriptions (Prefs *prefs)
{
  AdwPreferencesGroup *group = prefs->subs_group;
  if (group == NULL)
    return;

  /* Rebuild the whole group. Removal has to work from a list we kept
   * ourselves: an AdwPreferencesGroup's first child is its internal box, not a
   * row, so walking the widget tree finds nothing to remove. */
  for (guint i = 0; i < prefs->subs_rows->len; i++)
    adw_preferences_group_remove (group, g_ptr_array_index (prefs->subs_rows, i));
  g_ptr_array_set_size (prefs->subs_rows, 0);

  for (guint i = 0; i < prefs->cfg->subscriptions->len; i++) {
    const char *url = g_ptr_array_index (prefs->cfg->subscriptions, i);

    GtkWidget *row = adw_action_row_new ();
    adw_preferences_row_set_title (ADW_PREFERENCES_ROW (row), url);
    adw_preferences_row_set_title_selectable (ADW_PREFERENCES_ROW (row), TRUE);

    GtkWidget *remove = gtk_button_new_from_icon_name ("user-trash-symbolic");
    gtk_widget_add_css_class (remove, "flat");
    gtk_widget_set_valign (remove, GTK_ALIGN_CENTER);
    g_object_set_data_full (G_OBJECT (remove), "lyndon-url", g_strdup (url), g_free);
    g_signal_connect (remove, "clicked", G_CALLBACK (on_remove_subscription), prefs);
    adw_action_row_add_suffix (ADW_ACTION_ROW (row), remove);

    adw_preferences_group_add (group, row);
    g_ptr_array_add (prefs->subs_rows, row);
  }

  GtkWidget *add = adw_entry_row_new ();
  adw_preferences_row_set_title (ADW_PREFERENCES_ROW (add),
                                 "Add a list URL, then press Enter");
  g_signal_connect (add, "entry-activated", G_CALLBACK (on_add_subscription), prefs);
  adw_preferences_group_add (group, add);
  g_ptr_array_add (prefs->subs_rows, add);
}

static void
build_blocking (Prefs *prefs)
{
  AdwPreferencesPage *page = add_page (prefs, "Blocking", "security-high-symbolic");

  AdwPreferencesGroup *main = add_group (page, "Content blocking",
    "Rules are compiled once into WebKit's own filter engine and matched inside "
    "the network process — no extension, no per-request scripting.");
  bool_row (prefs, main, "Block content", NULL, &prefs->cfg->block_enabled);

  AdwPreferencesGroup *cats = add_group (page, "Categories", NULL);
  for (int i = 0; i < LY_CAT_N; i++)
    bool_row (prefs, cats, ly_cat_label (i), ly_cat_summary (i),
              &prefs->cfg->block_cat[i]);

  AdwPreferencesGroup *tune = add_group (page, "Behaviour", NULL);
  bool_row (prefs, tune, "Strip third-party cookies from embeds",
            "Keeps embedded players and widgets working while denying them a "
            "stable identifier.", &prefs->cfg->block_strict_third_party);
  bool_row (prefs, tune, "Hide leftover placeholders",
            "Element hiding for the gaps blocked ads leave behind. Costs one "
            "extra style pass per page.", &prefs->cfg->block_hide_placeholders);

  AdwPreferencesGroup *lists = add_group (page, "Filter lists",
    "Any Adblock Plus or hosts-format list. Cached locally and refreshed every "
    "few days.");
  prefs->subs_group = lists;
  refresh_subscriptions (prefs);

  AdwPreferencesGroup *actions = add_group (page, "Rules", NULL);
  button_row (actions, "Your own rules",
              "Opens custom-rules.txt in your text editor.",
              "Edit…", G_CALLBACK (on_edit_rules_clicked), prefs);
  button_row (actions, "Filter lists", "Fetch the newest copy of each list now.",
              "Update", G_CALLBACK (on_update_clicked), prefs);
  button_row (actions, "Compiled rules", ly_blocker_status_text (ly_app_blocker (prefs->app)),
              "Rebuild", G_CALLBACK (on_rebuild_clicked), prefs);
}


/* ------------------------------------------------------------- passwords */

/* The saved-login list arrives asynchronously from the keyring, and the
 * dialog may well be gone by then, so the continuation holds a weak reference
 * to the group widget rather than to Prefs. */
typedef struct {
  GWeakRef group_ref;
  Prefs   *prefs;
} LoginListRequest;

static void refresh_logins (Prefs *prefs);

static void
on_forget_login (GtkButton *button, gpointer data)
{
  Prefs *prefs = data;
  const char *origin   = g_object_get_data (G_OBJECT (button), "lyndon-origin");
  const char *username = g_object_get_data (G_OBJECT (button), "lyndon-username");

  ly_passwords_forget (ly_app_passwords (prefs->app), origin, username);
  adw_preferences_dialog_add_toast (prefs->dialog, adw_toast_new ("Password removed"));

  /* The keyring deletes asynchronously; re-reading immediately would race it. */
  g_timeout_add_once (350, (GSourceOnceFunc) refresh_logins, prefs);
}

static void
on_unblock_origin (GtkButton *button, gpointer data)
{
  Prefs *prefs = data;
  const char *origin = g_object_get_data (G_OBJECT (button), "lyndon-origin");
  ly_passwords_unblock (ly_app_passwords (prefs->app), origin);
  refresh_logins (prefs);
}

static void
on_logins_listed (GPtrArray *credentials, gpointer data)
{
  LoginListRequest *request = data;
  g_autoptr (GObject) group = g_weak_ref_get (&request->group_ref);

  if (group == NULL) {          /* dialog closed while the keyring answered */
    g_weak_ref_clear (&request->group_ref);
    g_free (request);
    return;
  }

  Prefs *prefs = request->prefs;
  LyPasswords *passwords = ly_app_passwords (prefs->app);

  for (guint i = 0; i < prefs->login_rows->len; i++)
    adw_preferences_group_remove (prefs->login_group,
                                  g_ptr_array_index (prefs->login_rows, i));
  g_ptr_array_set_size (prefs->login_rows, 0);

  for (guint i = 0; i < credentials->len; i++) {
    LyCredential *credential = g_ptr_array_index (credentials, i);

    GtkWidget *row = adw_action_row_new ();
    adw_preferences_row_set_title (ADW_PREFERENCES_ROW (row),
      (credential->username && *credential->username) ? credential->username
                                                      : "(no username)");
    adw_action_row_set_subtitle (ADW_ACTION_ROW (row), credential->origin);
    adw_preferences_row_set_title_selectable (ADW_PREFERENCES_ROW (row), TRUE);

    GtkWidget *forget = gtk_button_new_from_icon_name ("user-trash-symbolic");
    gtk_widget_add_css_class (forget, "flat");
    gtk_widget_set_valign (forget, GTK_ALIGN_CENTER);
    gtk_widget_set_tooltip_text (forget, "Remove from the keyring");
    g_object_set_data_full (G_OBJECT (forget), "lyndon-origin",
                            g_strdup (credential->origin), g_free);
    g_object_set_data_full (G_OBJECT (forget), "lyndon-username",
                            g_strdup (credential->username), g_free);
    g_signal_connect (forget, "clicked", G_CALLBACK (on_forget_login), prefs);
    adw_action_row_add_suffix (ADW_ACTION_ROW (row), forget);

    adw_preferences_group_add (prefs->login_group, row);
    g_ptr_array_add (prefs->login_rows, row);
  }

  /* Sites the user answered "Never" for belong in the same list: they are the
   * other half of what the password manager is doing on their behalf. */
  for (guint i = 0; i < prefs->cfg->password_never->len; i++) {
    const char *origin = g_ptr_array_index (prefs->cfg->password_never, i);

    GtkWidget *row = adw_action_row_new ();
    adw_preferences_row_set_title (ADW_PREFERENCES_ROW (row), origin);
    adw_action_row_set_subtitle (ADW_ACTION_ROW (row), "Never asked here");

    GtkWidget *undo = gtk_button_new_from_icon_name ("edit-undo-symbolic");
    gtk_widget_add_css_class (undo, "flat");
    gtk_widget_set_valign (undo, GTK_ALIGN_CENTER);
    gtk_widget_set_tooltip_text (undo, "Ask again on this site");
    g_object_set_data_full (G_OBJECT (undo), "lyndon-origin", g_strdup (origin), g_free);
    g_signal_connect (undo, "clicked", G_CALLBACK (on_unblock_origin), prefs);
    adw_action_row_add_suffix (ADW_ACTION_ROW (row), undo);

    adw_preferences_group_add (prefs->login_group, row);
    g_ptr_array_add (prefs->login_rows, row);
  }

  if (prefs->login_rows->len == 0) {
    GtkWidget *row = adw_action_row_new ();
    adw_preferences_row_set_title (ADW_PREFERENCES_ROW (row),
      ly_passwords_available (passwords) ? "No saved passwords"
                                         : "No keyring available");
    adw_action_row_set_subtitle (ADW_ACTION_ROW (row), ly_passwords_status (passwords));
    adw_preferences_group_add (prefs->login_group, row);
    g_ptr_array_add (prefs->login_rows, row);
  }

  g_weak_ref_clear (&request->group_ref);
  g_free (request);
}

static void
refresh_logins (Prefs *prefs)
{
  if (prefs->login_group == NULL)
    return;

  LoginListRequest *request = g_new0 (LoginListRequest, 1);
  g_weak_ref_init (&request->group_ref, prefs->login_group);
  request->prefs = prefs;

  ly_passwords_list (ly_app_passwords (prefs->app), on_logins_listed, request);
}

static void
build_passwords (Prefs *prefs)
{
  AdwPreferencesPage *page = add_page (prefs, "Passwords", "dialog-password-symbolic");
  LyPasswords *passwords = ly_app_passwords (prefs->app);

  AdwPreferencesGroup *main = add_group (page, "Password manager",
    "Logins are kept by your desktop keyring over the Secret Service API — the "
    "same store the rest of your desktop uses. Lyndon writes no password to "
    "its own files and rolls no crypto of its own.");

  bool_row (prefs, main, "Offer to save logins", NULL, &prefs->cfg->save_passwords);
  bool_row (prefs, main, "Fill saved logins automatically",
            "Only on an exact origin match, and never inside a third-party frame.",
            &prefs->cfg->password_autofill);

  GtkWidget *status = adw_action_row_new ();
  adw_preferences_row_set_title (ADW_PREFERENCES_ROW (status), "Keyring");
  adw_action_row_set_subtitle (ADW_ACTION_ROW (status), ly_passwords_status (passwords));
  GtkWidget *badge = gtk_image_new_from_icon_name (
    ly_passwords_available (passwords) ? "emblem-ok-symbolic" : "dialog-warning-symbolic");
  gtk_widget_set_valign (badge, GTK_ALIGN_CENTER);
  adw_action_row_add_suffix (ADW_ACTION_ROW (status), badge);
  adw_preferences_group_add (main, status);

  prefs->login_group = add_group (page, "Saved logins",
    "Passwords are never shown here; removing a row deletes it from the keyring.");
  refresh_logins (prefs);
}

/* ---------------------------------------------------------- permissions */

typedef struct { Prefs *prefs; int index; } PermBinding;

static void
on_permission_changed (GObject *row, GParamSpec *pspec, gpointer data)
{
  PermBinding *b = data;
  b->prefs->cfg->perm[b->index] = (LyPolicy) adw_combo_row_get_selected (ADW_COMBO_ROW (row));
  changed (b->prefs, FALSE);
}

static void
build_permissions (Prefs *prefs)
{
  AdwPreferencesPage *page = add_page (prefs, "Permissions", "dialog-question-symbolic");

  AdwPreferencesGroup *group = add_group (page, "Site permissions",
    "What a page gets when it asks. Ask shows a bar at the top of the page; "
    "Block answers no without interrupting you.");

  static const char *const POLICIES[] = { "Ask", "Allow", "Block", NULL };

  for (int i = 0; i < LY_PERM_N; i++) {
    GtkWidget *row = adw_combo_row_new ();
    adw_preferences_row_set_title (ADW_PREFERENCES_ROW (row), ly_perm_label (i));
    adw_combo_row_set_model (ADW_COMBO_ROW (row),
                             G_LIST_MODEL (gtk_string_list_new (POLICIES)));
    adw_combo_row_set_selected (ADW_COMBO_ROW (row), (guint) prefs->cfg->perm[i]);

    PermBinding *b = g_new0 (PermBinding, 1);
    b->prefs = prefs;
    b->index = i;
    g_signal_connect_data (row, "notify::selected", G_CALLBACK (on_permission_changed),
                           b, free_binding, 0);

    adw_preferences_group_add (group, row);
  }

  AdwPreferencesGroup *sites = add_group (page, "Site exceptions",
    "Sites where content blocking is paused. Use the shield in the address bar "
    "to add or remove one.");

  if (prefs->cfg->block_exceptions->len == 0) {
    GtkWidget *row = adw_action_row_new ();
    adw_preferences_row_set_title (ADW_PREFERENCES_ROW (row), "None");
    adw_preferences_group_add (sites, row);
  } else {
    for (guint i = 0; i < prefs->cfg->block_exceptions->len; i++) {
      GtkWidget *row = adw_action_row_new ();
      adw_preferences_row_set_title (ADW_PREFERENCES_ROW (row),
                                     g_ptr_array_index (prefs->cfg->block_exceptions, i));
      adw_preferences_group_add (sites, row);
    }
  }
}

/* ------------------------------------------------------------------ api */

void
ly_prefs_present (LyApp *app, GtkWidget *parent)
{
  Prefs *prefs = g_new0 (Prefs, 1);
  prefs->app = app;
  prefs->cfg = ly_app_config (app);
  prefs->subs_rows  = g_ptr_array_new ();
  prefs->login_rows   = g_ptr_array_new ();
  prefs->keyword_rows = g_ptr_array_new ();
  prefs->dialog = ADW_PREFERENCES_DIALOG (adw_preferences_dialog_new ());

  adw_dialog_set_title (ADW_DIALOG (prefs->dialog), "Preferences");
  adw_dialog_set_content_width (ADW_DIALOG (prefs->dialog), 640);
  adw_dialog_set_content_height (ADW_DIALOG (prefs->dialog), 680);

  build_appearance (prefs);
  build_web (prefs);
  build_privacy (prefs);
  build_passwords (prefs);
  build_blocking (prefs);
  build_permissions (prefs);

  g_object_set_data_full (G_OBJECT (prefs->dialog), "lyndon-prefs", prefs, free_prefs);
  adw_dialog_present (ADW_DIALOG (prefs->dialog), parent);
}
