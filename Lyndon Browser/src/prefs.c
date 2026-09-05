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
#include "pwfile.h"

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

static void  refresh_subscriptions (Prefs *prefs);
static void  refresh_logins        (Prefs *prefs);
static void  report_problem        (Prefs *prefs, const char *title, const char *body);
static guint store_credentials     (Prefs *prefs, GPtrArray *credentials, GError **error);

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
  GtkWidget      *bookmarks;   /* NULL when this profile has none */
  GtkWidget      *history;
  GtkWidget      *passwords;
} ImportRow;

static gboolean
wants (GtkWidget *row)
{
  return row != NULL && adw_switch_row_get_active (ADW_SWITCH_ROW (row));
}

static void
on_import_clicked (GtkButton *button, gpointer data)
{
  ImportRow *row = data;
  Prefs *prefs = row->prefs;

  gboolean want_bookmarks = wants (row->bookmarks);
  gboolean want_history   = wants (row->history);
  gboolean want_passwords = wants (row->passwords);

  if (!want_bookmarks && !want_history && !want_passwords)
    return;

  gtk_widget_set_sensitive (GTK_WIDGET (button), FALSE);
  gtk_button_set_label (button, "Importing…");

  /* Everything below blocks, and the label is the only sign that anything is
   * happening, so let it get to the screen first. That re-enters the main
   * loop, where the dialog could be closed out from under us — and Prefs lives
   * and dies with it — so hold it open for the rest of this call. */
  g_autoptr (GtkWidget) hold = g_object_ref (GTK_WIDGET (prefs->dialog));
  while (g_main_context_iteration (NULL, FALSE))
    ;

  GString *message = g_string_new (NULL);
  g_autofree char *problem = NULL;
  gboolean ok = TRUE;

  if (want_bookmarks || want_history) {
    LyImportResult result;
    ok = ly_import_run (ly_app_store (prefs->app), row->source,
                        want_bookmarks, want_history, &result);
    if (!ok) {
      problem = g_strdup (result.error ?: "unknown error");
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
      ly_import_passwords (row->source, &skipped, &error);

    if (credentials == NULL) {
      problem = g_strdup (error->message);
      ok = FALSE;
    } else {
      guint stored = store_credentials (prefs, credentials, &error);
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

  if (!ok) {
    gtk_button_set_label (button, "Retry");
    gtk_widget_set_sensitive (GTK_WIDGET (button), TRUE);
    report_problem (prefs,
                    message->len > 0 ? "Partly imported" : "Nothing could be imported",
                    problem);
  } else {
    gtk_button_set_label (button, "Imported");
    g_autofree char *text = g_strdup_printf ("Imported %s", message->str);
    adw_preferences_dialog_add_toast (prefs->dialog, adw_toast_new (text));
  }

  g_string_free (message, TRUE);
  if (want_passwords)
    refresh_logins (prefs);
}

static void
on_import_clicked_free (gpointer data, GClosure *closure)
{
  g_free (data);
}

static GtkWidget *
import_switch (AdwExpanderRow *parent, const char *title, const char *subtitle)
{
  GtkWidget *row = adw_switch_row_new ();
  adw_preferences_row_set_title (ADW_PREFERENCES_ROW (row), title);
  if (subtitle != NULL)
    adw_action_row_set_subtitle (ADW_ACTION_ROW (row), subtitle);
  adw_switch_row_set_active (ADW_SWITCH_ROW (row), TRUE);
  adw_expander_row_add_row (parent, row);
  return row;
}

static void
on_import_dialog (GtkButton *button, gpointer data)
{
  Prefs *prefs = data;

  AdwDialog *dialog = adw_dialog_new ();
  adw_dialog_set_title (dialog, "Import");
  adw_dialog_set_content_width (dialog, 560);
  adw_dialog_set_content_height (dialog, 560);

  AdwPreferencesPage *page = ADW_PREFERENCES_PAGE (adw_preferences_page_new ());
  AdwPreferencesGroup *group = ADW_PREFERENCES_GROUP (adw_preferences_group_new ());
  adw_preferences_group_set_title (group, "Browsers found on this computer");
  adw_preferences_group_set_description (group,
    "Bookmarks and history are copied in with their original visit counts and "
    "dates, and saved logins are decrypted straight out of the other browser's "
    "own store. Nothing is sent anywhere, and every file is read from a "
    "temporary copy.");

  GPtrArray *sources = ly_import_sources ();
  /* Keep the array alive as long as the dialog: the rows point into it. */
  g_object_set_data_full (G_OBJECT (dialog), "lyndon-sources", sources,
                          (GDestroyNotify) g_ptr_array_unref);

  if (sources->len == 0) {
    GtkWidget *row = adw_action_row_new ();
    adw_preferences_row_set_title (ADW_PREFERENCES_ROW (row), "No other browsers found");
    adw_action_row_set_subtitle (ADW_ACTION_ROW (row),
      "Looked for Chrome, Chromium, Brave, Edge, Vivaldi, Opera and Firefox profiles.");
    adw_preferences_group_add (group, row);
  }

  for (guint i = 0; i < sources->len; i++) {
    LyImportSource *source = g_ptr_array_index (sources, i);

    GtkWidget *row = adw_expander_row_new ();
    adw_preferences_row_set_title (ADW_PREFERENCES_ROW (row), source->label);
    adw_action_row_set_subtitle (ADW_ACTION_ROW (row), "Choose what to bring over");
    adw_expander_row_set_expanded (ADW_EXPANDER_ROW (row), sources->len == 1);

    ImportRow *context = g_new0 (ImportRow, 1);
    context->prefs  = prefs;
    context->source = source;

    if (source->has_bookmarks)
      context->bookmarks = import_switch (ADW_EXPANDER_ROW (row), "Bookmarks", NULL);
    if (source->has_history)
      context->history = import_switch (ADW_EXPANDER_ROW (row), "History", NULL);
    if (source->has_passwords)
      context->passwords = import_switch (ADW_EXPANDER_ROW (row), "Saved logins",
        source->kind == LY_IMPORT_FIREFOX
          ? "Needs the profile to have no Primary Password set."
          : "Needs this browser's keyring entry, which it makes on first run.");

    GtkWidget *import = gtk_button_new_with_label ("Import");
    gtk_widget_set_valign (import, GTK_ALIGN_CENTER);
    gtk_widget_add_css_class (import, "suggested-action");
    context->button = import;

    g_signal_connect_data (import, "clicked", G_CALLBACK (on_import_clicked),
                           context, on_import_clicked_free, 0);

    adw_expander_row_add_suffix (ADW_EXPANDER_ROW (row), import);
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
              "Bookmarks, history and saved logins from Chrome, Firefox and friends.",
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

/* ------------------------------------------------------- editing a login */

/* Editing has to read a password back out of the keyring, and the preferences
 * dialog can be closed while that read is in flight; every continuation below
 * checks that it still has somewhere to put its answer. */
typedef struct {
  GWeakRef dialog_ref;
  Prefs   *prefs;
} PrefsHandle;

static PrefsHandle *
prefs_handle_new (Prefs *prefs)
{
  PrefsHandle *handle = g_new0 (PrefsHandle, 1);
  g_weak_ref_init (&handle->dialog_ref, prefs->dialog);
  handle->prefs = prefs;
  return handle;
}

static Prefs *
prefs_handle_live (PrefsHandle *handle)
{
  g_autoptr (GObject) dialog = g_weak_ref_get (&handle->dialog_ref);
  return dialog != NULL ? handle->prefs : NULL;
}

static void
prefs_handle_free (PrefsHandle *handle)
{
  g_weak_ref_clear (&handle->dialog_ref);
  g_free (handle);
}

typedef struct {
  Prefs     *prefs;
  AdwDialog *dialog;
  GtkWidget *origin_row;
  GtkWidget *username_row;
  GtkWidget *password_row;
  char      *old_origin;      /* NULL when adding a new login */
  char      *old_username;
} LoginEditor;

static void
login_editor_free (gpointer data)
{
  LoginEditor *editor = data;
  g_free (editor->old_origin);
  g_free (editor->old_username);
  g_free (editor);
}

static void
on_editor_cancel (GtkButton *button, gpointer data)
{
  LoginEditor *editor = data;
  adw_dialog_close (editor->dialog);
}

static void
on_editor_copy (GtkButton *button, gpointer data)
{
  LoginEditor *editor = data;
  const char *password = gtk_editable_get_text (GTK_EDITABLE (editor->password_row));

  gdk_clipboard_set_text (gtk_widget_get_clipboard (GTK_WIDGET (button)), password);
  adw_preferences_dialog_add_toast (editor->prefs->dialog,
                                    adw_toast_new ("Password copied"));
}

static void
on_editor_save (GtkButton *button, gpointer data)
{
  LoginEditor *editor = data;
  Prefs *prefs = editor->prefs;

  g_autofree char *origin = ly_passwords_normalise_origin (
    gtk_editable_get_text (GTK_EDITABLE (editor->origin_row)));
  const char *username = gtk_editable_get_text (GTK_EDITABLE (editor->username_row));
  const char *password = gtk_editable_get_text (GTK_EDITABLE (editor->password_row));

  /* Say which field is wrong on the field itself; a toast behind a dialog is
   * easy to miss. */
  gtk_widget_remove_css_class (editor->origin_row, "error");
  gtk_widget_remove_css_class (editor->password_row, "error");

  if (origin == NULL) {
    gtk_widget_add_css_class (editor->origin_row, "error");
    gtk_widget_grab_focus (editor->origin_row);
    return;
  }
  if (password == NULL || *password == '\0') {
    gtk_widget_add_css_class (editor->password_row, "error");
    gtk_widget_grab_focus (editor->password_row);
    return;
  }

  g_autoptr (GError) error = NULL;
  gboolean ok = ly_passwords_update (ly_app_passwords (prefs->app),
                                     editor->old_origin, editor->old_username,
                                     origin, username, password, &error);

  adw_dialog_close (editor->dialog);

  g_autofree char *message = ok
    ? g_strdup_printf ("Saved for %s", origin)
    : g_strdup_printf ("Could not save: %s", error->message);
  adw_preferences_dialog_add_toast (prefs->dialog, adw_toast_new (message));

  if (ok)
    g_timeout_add_once (350, (GSourceOnceFunc) refresh_logins, prefs);
}

static void
present_login_editor (Prefs *prefs, const char *origin,
                      const char *username, const char *password)
{
  LoginEditor *editor = g_new0 (LoginEditor, 1);
  editor->prefs        = prefs;
  editor->old_origin   = g_strdup (origin);
  editor->old_username = g_strdup (username);

  gboolean adding = origin == NULL;

  AdwDialog *dialog = adw_dialog_new ();
  editor->dialog = dialog;
  adw_dialog_set_title (dialog, adding ? "Add a login" : "Edit login");
  adw_dialog_set_content_width (dialog, 460);
  g_object_set_data_full (G_OBJECT (dialog), "lyndon-editor", editor, login_editor_free);

  AdwPreferencesPage  *page  = ADW_PREFERENCES_PAGE (adw_preferences_page_new ());
  AdwPreferencesGroup *group = ADW_PREFERENCES_GROUP (adw_preferences_group_new ());
  adw_preferences_group_set_description (group,
    "Autofill matches on the site exactly as written here — scheme and host, "
    "and a port only if the site uses one. A bare domain is filled in for you.");

  editor->origin_row = adw_entry_row_new ();
  adw_preferences_row_set_title (ADW_PREFERENCES_ROW (editor->origin_row), "Site");
  gtk_editable_set_text (GTK_EDITABLE (editor->origin_row), origin ?: "");
  adw_preferences_group_add (group, editor->origin_row);

  editor->username_row = adw_entry_row_new ();
  adw_preferences_row_set_title (ADW_PREFERENCES_ROW (editor->username_row), "Username");
  gtk_editable_set_text (GTK_EDITABLE (editor->username_row), username ?: "");
  adw_preferences_group_add (group, editor->username_row);

  editor->password_row = adw_password_entry_row_new ();
  adw_preferences_row_set_title (ADW_PREFERENCES_ROW (editor->password_row), "Password");
  gtk_editable_set_text (GTK_EDITABLE (editor->password_row), password ?: "");

  GtkWidget *copy = gtk_button_new_from_icon_name ("edit-copy-symbolic");
  gtk_widget_add_css_class (copy, "flat");
  gtk_widget_set_valign (copy, GTK_ALIGN_CENTER);
  gtk_widget_set_tooltip_text (copy, "Copy the password");
  g_signal_connect (copy, "clicked", G_CALLBACK (on_editor_copy), editor);
  adw_entry_row_add_suffix (ADW_ENTRY_ROW (editor->password_row), copy);
  adw_preferences_group_add (group, editor->password_row);

  adw_preferences_page_add (page, group);

  GtkWidget *cancel = gtk_button_new_with_label ("Cancel");
  g_signal_connect (cancel, "clicked", G_CALLBACK (on_editor_cancel), editor);

  GtkWidget *save = gtk_button_new_with_label ("Save");
  gtk_widget_add_css_class (save, "suggested-action");
  g_signal_connect (save, "clicked", G_CALLBACK (on_editor_save), editor);

  GtkWidget *header = adw_header_bar_new ();
  adw_header_bar_pack_start (ADW_HEADER_BAR (header), cancel);
  adw_header_bar_pack_end (ADW_HEADER_BAR (header), save);

  AdwToolbarView *toolbar = ADW_TOOLBAR_VIEW (adw_toolbar_view_new ());
  adw_toolbar_view_add_top_bar (toolbar, header);
  adw_toolbar_view_set_content (toolbar, GTK_WIDGET (page));
  adw_dialog_set_child (dialog, GTK_WIDGET (toolbar));

  adw_dialog_present (dialog, GTK_WIDGET (prefs->dialog));
  gtk_widget_grab_focus (adding ? editor->origin_row : editor->password_row);
}

typedef struct {
  PrefsHandle *handle;
  char        *origin;
  char        *username;
} EditRequest;

/* The list in the dialog deliberately never holds a password, so opening the
 * editor asks the keyring for that one row's secret at the moment it is
 * needed. */
static void
on_credentials_for_edit (GPtrArray *credentials, gpointer data)
{
  EditRequest *request = data;
  Prefs *prefs = prefs_handle_live (request->handle);

  if (prefs != NULL) {
    const char *password = NULL;
    for (guint i = 0; i < credentials->len; i++) {
      LyCredential *credential = g_ptr_array_index (credentials, i);
      if (g_strcmp0 (credential->username, request->username) == 0) {
        password = credential->password;
        break;
      }
    }
    present_login_editor (prefs, request->origin, request->username, password);
  }

  prefs_handle_free (request->handle);
  g_free (request->origin);
  g_free (request->username);
  g_free (request);
}

static void
on_edit_login (GtkButton *button, gpointer data)
{
  Prefs *prefs = data;

  EditRequest *request = g_new0 (EditRequest, 1);
  request->handle   = prefs_handle_new (prefs);
  request->origin   = g_strdup (g_object_get_data (G_OBJECT (button), "lyndon-origin"));
  request->username = g_strdup (g_object_get_data (G_OBJECT (button), "lyndon-username"));

  ly_passwords_lookup (ly_app_passwords (prefs->app), request->origin,
                       on_credentials_for_edit, request);
}

static void
on_add_login (GtkButton *button, gpointer data)
{
  present_login_editor ((Prefs *) data, NULL, NULL, NULL);
}

/* ------------------------------------------------------- import a file */

static void
add_password_filters (GtkFileDialog *dialog, gboolean csv_only)
{
  g_autoptr (GListStore) filters = g_list_store_new (GTK_TYPE_FILE_FILTER);

  GtkFileFilter *primary = gtk_file_filter_new ();
  gtk_file_filter_set_name (primary, csv_only ? "CSV" : "Password exports and spreadsheets");
  gtk_file_filter_add_pattern (primary, "*.csv");
  if (!csv_only) {
    gtk_file_filter_add_pattern (primary, "*.tsv");
    gtk_file_filter_add_pattern (primary, "*.txt");
    gtk_file_filter_add_pattern (primary, "*.xlsx");
    gtk_file_filter_add_pattern (primary, "*.ods");
  }
  g_list_store_append (filters, primary);

  GtkFileFilter *any = gtk_file_filter_new ();
  gtk_file_filter_set_name (any, "All files");
  gtk_file_filter_add_pattern (any, "*");
  g_list_store_append (filters, any);

  gtk_file_dialog_set_filters (dialog, G_LIST_MODEL (filters));
  gtk_file_dialog_set_default_filter (dialog, primary);
  g_object_unref (primary);
  g_object_unref (any);
}

static GtkWindow *
prefs_window (Prefs *prefs)
{
  GtkRoot *root = gtk_widget_get_root (GTK_WIDGET (prefs->dialog));
  return GTK_IS_WINDOW (root) ? GTK_WINDOW (root) : NULL;
}

static void
report_problem (Prefs *prefs, const char *title, const char *body)
{
  AdwDialog *dialog = adw_alert_dialog_new (title, body ?: "No further detail.");
  adw_alert_dialog_add_responses (ADW_ALERT_DIALOG (dialog), "ok", "OK", NULL);
  adw_alert_dialog_set_default_response (ADW_ALERT_DIALOG (dialog), "ok");
  adw_dialog_present (dialog, GTK_WIDGET (prefs->dialog));
}

/* Writes a batch into the keyring, one blocking round trip each. An import is
 * an explicit, one-off action, and getting the count right in the message
 * afterwards matters more here than staying responsive for a second. */
static guint
store_credentials (Prefs *prefs, GPtrArray *credentials, GError **error)
{
  LyPasswords *passwords = ly_app_passwords (prefs->app);
  guint stored = 0;

  for (guint i = 0; i < credentials->len; i++) {
    LyCredential *credential = g_ptr_array_index (credentials, i);
    if (!ly_passwords_save_sync (passwords, credential->origin, credential->username,
                                 credential->password, error))
      break;      /* a keyring that refused one row will refuse the rest */
    stored++;
  }
  return stored;
}

static void
on_import_file_ready (GObject *source, GAsyncResult *result, gpointer data)
{
  PrefsHandle *handle = data;
  Prefs *prefs = prefs_handle_live (handle);

  g_autoptr (GError) error = NULL;
  g_autoptr (GFile) file =
    gtk_file_dialog_open_finish (GTK_FILE_DIALOG (source), result, &error);

  if (prefs == NULL || file == NULL) {     /* dialog gone, or cancelled */
    prefs_handle_free (handle);
    return;
  }
  prefs_handle_free (handle);

  g_autofree char *path = g_file_get_path (file);
  guint skipped = 0;
  g_autoptr (GPtrArray) credentials = ly_pwfile_read (path, &skipped, &error);

  if (credentials == NULL) {
    report_problem (prefs, "Nothing could be imported", error->message);
    return;
  }

  guint stored = store_credentials (prefs, credentials, &error);

  GString *message = g_string_new (NULL);
  g_string_append_printf (message, "Imported %u login%s", stored, stored == 1 ? "" : "s");
  if (skipped > 0)
    g_string_append_printf (message, "; %u row%s had no site or password",
                            skipped, skipped == 1 ? "" : "s");

  if (stored < credentials->len && error != NULL)
    report_problem (prefs, message->str, error->message);
  else
    adw_preferences_dialog_add_toast (prefs->dialog, adw_toast_new (message->str));

  g_string_free (message, TRUE);
  refresh_logins (prefs);
}

static void
on_import_file_clicked (GtkButton *button, gpointer data)
{
  Prefs *prefs = data;

  GtkFileDialog *dialog = gtk_file_dialog_new ();
  gtk_file_dialog_set_title (dialog, "Import passwords");
  add_password_filters (dialog, FALSE);

  gtk_file_dialog_open (dialog, prefs_window (prefs), NULL,
                        on_import_file_ready, prefs_handle_new (prefs));
  g_object_unref (dialog);
}

/* ------------------------------------------------------------- exporting */

typedef struct {
  PrefsHandle *handle;
  char        *path;
} ExportRequest;

static void
on_credentials_for_export (GPtrArray *credentials, gpointer data)
{
  ExportRequest *request = data;
  Prefs *prefs = prefs_handle_live (request->handle);

  if (prefs != NULL) {
    g_autoptr (GError) error = NULL;
    if (!ly_pwfile_write_csv (request->path, credentials, &error)) {
      report_problem (prefs, "The file could not be written", error->message);
    } else {
      g_autofree char *message =
        g_strdup_printf ("Exported %u login%s in plain text",
                         credentials->len, credentials->len == 1 ? "" : "s");
      adw_preferences_dialog_add_toast (prefs->dialog, adw_toast_new (message));
    }
  }

  prefs_handle_free (request->handle);
  g_free (request->path);
  g_free (request);
}

static void
on_export_file_ready (GObject *source, GAsyncResult *result, gpointer data)
{
  PrefsHandle *handle = data;
  Prefs *prefs = prefs_handle_live (handle);

  g_autoptr (GFile) file =
    gtk_file_dialog_save_finish (GTK_FILE_DIALOG (source), result, NULL);

  if (prefs == NULL || file == NULL) {
    prefs_handle_free (handle);
    return;
  }

  ExportRequest *request = g_new0 (ExportRequest, 1);
  request->handle = handle;
  request->path   = g_file_get_path (file);

  ly_passwords_list (ly_app_passwords (prefs->app), on_credentials_for_export, request);
}

static void
on_export_confirmed (AdwAlertDialog *alert, const char *response, gpointer data)
{
  Prefs *prefs = data;
  if (g_strcmp0 (response, "export") != 0)
    return;

  GtkFileDialog *dialog = gtk_file_dialog_new ();
  gtk_file_dialog_set_title (dialog, "Export passwords");
  gtk_file_dialog_set_initial_name (dialog, "lyndon-passwords.csv");
  add_password_filters (dialog, TRUE);

  gtk_file_dialog_save (dialog, prefs_window (prefs), NULL,
                        on_export_file_ready, prefs_handle_new (prefs));
  g_object_unref (dialog);
}

static void
on_export_clicked (GtkButton *button, gpointer data)
{
  Prefs *prefs = data;

  AdwDialog *dialog = adw_alert_dialog_new ("Export saved passwords?", NULL);
  adw_alert_dialog_set_body (ADW_ALERT_DIALOG (dialog),
    "Every password is written out as readable text, with no encryption of any "
    "kind. Anything that can read the file can read your logins — delete it "
    "once whatever needed it has finished.");
  adw_alert_dialog_add_responses (ADW_ALERT_DIALOG (dialog),
                                  "cancel", "Cancel", "export", "Export", NULL);
  adw_alert_dialog_set_response_appearance (ADW_ALERT_DIALOG (dialog), "export",
                                            ADW_RESPONSE_DESTRUCTIVE);
  adw_alert_dialog_set_default_response (ADW_ALERT_DIALOG (dialog), "cancel");
  g_signal_connect (dialog, "response", G_CALLBACK (on_export_confirmed), prefs);

  adw_dialog_present (dialog, GTK_WIDGET (prefs->dialog));
}

/* ------------------------------------------------------------- the list */

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

    GtkWidget *edit = gtk_button_new_from_icon_name ("document-edit-symbolic");
    gtk_widget_add_css_class (edit, "flat");
    gtk_widget_set_valign (edit, GTK_ALIGN_CENTER);
    gtk_widget_set_tooltip_text (edit, "Change the site, username or password");
    g_object_set_data_full (G_OBJECT (edit), "lyndon-origin",
                            g_strdup (credential->origin), g_free);
    g_object_set_data_full (G_OBJECT (edit), "lyndon-username",
                            g_strdup (credential->username), g_free);
    g_signal_connect (edit, "clicked", G_CALLBACK (on_edit_login), prefs);
    adw_action_row_add_suffix (ADW_ACTION_ROW (row), edit);

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
    "Passwords are shown only inside the editor. Removing a row deletes it "
    "from the keyring.");

  GtkWidget *add = gtk_button_new_from_icon_name ("list-add-symbolic");
  gtk_widget_add_css_class (add, "flat");
  gtk_widget_set_tooltip_text (add, "Add a login by hand");
  g_signal_connect (add, "clicked", G_CALLBACK (on_add_login), prefs);
  adw_preferences_group_set_header_suffix (prefs->login_group, add);

  refresh_logins (prefs);

  AdwPreferencesGroup *transfer = add_group (page, "Import and export",
    "Reads the CSV every other password manager exports, and .xlsx or .ods "
    "spreadsheets directly. A column naming the site and one naming the "
    "password are all that is needed; the rest is worked out from the header.");

  button_row (transfer, "Import from a file",
              "CSV, TSV, Excel or OpenDocument.",
              "Choose…", G_CALLBACK (on_import_file_clicked), prefs);
  button_row (transfer, "Import from another browser",
              "Saved logins from Chrome, Firefox and friends, decrypted in place.",
              "Browsers…", G_CALLBACK (on_import_dialog), prefs);
  button_row (transfer, "Export saved logins",
              "Writes every password to an unencrypted CSV file.",
              "Export…", G_CALLBACK (on_export_clicked), prefs);
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
