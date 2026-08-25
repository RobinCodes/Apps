/* app.c — see app.h. */

#include "app.h"
#include "window.h"
#include "prefs.h"

#include <string.h>

struct _LyApp {
  AdwApplication parent_instance;

  LyConfig    *cfg;
  LyEngine    *engine;
  LyBlocker   *blocker;
  LyDownloads *downloads;
  LyStore     *store;
  LyPasswords *passwords;

  GtkCssProvider *base_css;
  GtkCssProvider *tuned_css;   /* regenerated whenever opacity changes */
};

G_DEFINE_FINAL_TYPE (LyApp, ly_app, ADW_TYPE_APPLICATION)

LyConfig    *ly_app_config    (LyApp *app) { return app->cfg; }
LyEngine    *ly_app_engine    (LyApp *app) { return app->engine; }
LyBlocker   *ly_app_blocker   (LyApp *app) { return app->blocker; }
LyDownloads *ly_app_downloads (LyApp *app) { return app->downloads; }
LyStore     *ly_app_store     (LyApp *app) { return app->store; }
LyPasswords *ly_app_passwords (LyApp *app) { return app->passwords; }

/* ------------------------------------------------------------- styling */

static void
apply_style (LyApp *app)
{
  AdwStyleManager *style = adw_style_manager_get_default ();
  AdwColorScheme scheme;
  switch (app->cfg->scheme) {
    case LY_SCHEME_LIGHT: scheme = ADW_COLOR_SCHEME_FORCE_LIGHT; break;
    case LY_SCHEME_DARK:  scheme = ADW_COLOR_SCHEME_FORCE_DARK;  break;
    default:              scheme = ADW_COLOR_SCHEME_DEFAULT;     break;
  }
  adw_style_manager_set_color_scheme (style, scheme);

  /* The one value that has to be generated rather than written by hand. */
  g_autofree char *css = g_strdup_printf (
    ".fx-full .lyndon-chrome { background-color: alpha(@headerbar_bg_color, %.3f); }\n"
    ".fx-full popover.lyndon-panel > contents { background-color: alpha(@popover_bg_color, %.3f); }\n",
    app->cfg->ui_opacity,
    CLAMP (app->cfg->ui_opacity + 0.14, 0.4, 1.0));

  gtk_css_provider_load_from_string (app->tuned_css, css);
}

/* ------------------------------------------------------------- actions */

static void
windows_present_all (LyApp *app)
{
  GList *windows = gtk_application_get_windows (GTK_APPLICATION (app));
  for (GList *l = windows; l != NULL; l = l->next)
    if (LY_IS_WINDOW (l->data))
      gtk_window_present (GTK_WINDOW (l->data));
}

static GtkWidget *
active_window_widget (LyApp *app)
{
  GtkWindow *window = gtk_application_get_active_window (GTK_APPLICATION (app));
  return window ? GTK_WIDGET (window) : NULL;
}

static void
action_new_window (GSimpleAction *action, GVariant *param, gpointer data)
{
  LyApp *app = data;
  LyWindow *window = ly_app_new_window (app);
  ly_window_open_tab (window, NULL, FALSE);
  gtk_window_present (GTK_WINDOW (window));
}


static void
on_cache_cleared (gpointer data)
{
  LyApp *app = data;
  GtkWindow *window = gtk_application_get_active_window (GTK_APPLICATION (app));
  if (LY_IS_WINDOW (window))
    ly_window_toast (LY_WINDOW (window), "Cache cleared");
}

static void
action_clear_cache (GSimpleAction *action, GVariant *param, gpointer data)
{
  LyApp *app = data;
  /* Caches only: no confirmation, because nothing here is possible to miss.
   * Cookies, storage and history need the fuller dialog. */
  ly_engine_clear_data (app->engine,
                        WEBKIT_WEBSITE_DATA_DISK_CACHE |
                        WEBKIT_WEBSITE_DATA_MEMORY_CACHE |
                        WEBKIT_WEBSITE_DATA_DOM_CACHE,
                        on_cache_cleared, app);
}

static void
action_private_window (GSimpleAction *action, GVariant *param, gpointer data)
{
  LyApp *app = data;
  LyWindow *window = ly_app_new_private_window (app);
  if (window == NULL)
    return;
  ly_window_open_tab (window, NULL, FALSE);
  gtk_window_present (GTK_WINDOW (window));
}

static void
action_preferences (GSimpleAction *action, GVariant *param, gpointer data)
{
  LyApp *app = data;
  ly_prefs_present (app, active_window_widget (app));
}

static void
action_about (GSimpleAction *action, GVariant *param, gpointer data)
{
  LyApp *app = data;

  AdwDialog *dialog = adw_about_dialog_new ();
  AdwAboutDialog *about = ADW_ABOUT_DIALOG (dialog);
  adw_about_dialog_set_application_name (about, "Lyndon");
  adw_about_dialog_set_application_icon (about, LYNDON_APP_ID);
  adw_about_dialog_set_version (about, LYNDON_VERSION);
  adw_about_dialog_set_developer_name (about, "The Lyndon project");
  adw_about_dialog_set_license_type (about, GTK_LICENSE_MIT_X11);
  adw_about_dialog_set_comments (about,
    "A small native browser built on GTK4 and WebKitGTK.\n\n"
    "Blocking runs inside WebKit's own content-filter engine, so rules are "
    "matched in the network process with no extension and no per-request "
    "scripting.");

  g_autofree char *rules =
    g_strdup_printf ("%u rules compiled", ly_blocker_rule_count (app->blocker));
  adw_about_dialog_set_debug_info (about, rules);

  adw_dialog_present (dialog, active_window_widget (app));
}

/* The clear dialog is granular because the categories have very different
 * costs: dropping caches is free, dropping cookies logs you out everywhere. */
typedef struct {
  LyApp     *app;
  GtkWidget *cache;
  GtkWidget *cookies;
  GtkWidget *history;
  GtkWidget *sites;
} ClearChoices;

static void
on_clear_response (AdwAlertDialog *dialog, const char *response, gpointer data)
{
  ClearChoices *choices = data;
  LyApp *app = choices->app;

  if (g_strcmp0 (response, "clear") != 0)
    return;

  WebKitWebsiteDataTypes types = 0;

  if (gtk_check_button_get_active (GTK_CHECK_BUTTON (choices->cache)))
    types |= WEBKIT_WEBSITE_DATA_DISK_CACHE |
             WEBKIT_WEBSITE_DATA_MEMORY_CACHE |
             WEBKIT_WEBSITE_DATA_DOM_CACHE;

  if (gtk_check_button_get_active (GTK_CHECK_BUTTON (choices->cookies)))
    types |= WEBKIT_WEBSITE_DATA_COOKIES |
             WEBKIT_WEBSITE_DATA_LOCAL_STORAGE |
             WEBKIT_WEBSITE_DATA_SESSION_STORAGE |
             WEBKIT_WEBSITE_DATA_INDEXEDDB_DATABASES |
             WEBKIT_WEBSITE_DATA_SERVICE_WORKER_REGISTRATIONS |
             WEBKIT_WEBSITE_DATA_OFFLINE_APPLICATION_CACHE |
             WEBKIT_WEBSITE_DATA_ITP |
             WEBKIT_WEBSITE_DATA_DEVICE_ID_HASH_SALT;

  if (types != 0)
    ly_engine_clear_data (app->engine, types, NULL, NULL);

  if (gtk_check_button_get_active (GTK_CHECK_BUTTON (choices->history)) &&
      app->store != NULL)
    ly_store_clear_history (app->store);

  if (gtk_check_button_get_active (GTK_CHECK_BUTTON (choices->sites)) &&
      app->store != NULL) {
    ly_store_clear_zoom (app->store);
    g_ptr_array_set_size (app->cfg->block_exceptions, 0);
    g_ptr_array_set_size (app->cfg->password_never, 0);
    ly_config_touch (app->cfg);
  }

  GtkWindow *window = gtk_application_get_active_window (GTK_APPLICATION (app));
  if (LY_IS_WINDOW (window))
    ly_window_toast (LY_WINDOW (window), "Browsing data cleared");
}

static GtkWidget *
clear_check (GtkWidget *box, const char *label, const char *detail, gboolean active)
{
  GtkWidget *check = gtk_check_button_new_with_label (label);
  gtk_check_button_set_active (GTK_CHECK_BUTTON (check), active);
  gtk_box_append (GTK_BOX (box), check);

  if (detail != NULL) {
    GtkWidget *note = gtk_label_new (detail);
    gtk_label_set_xalign (GTK_LABEL (note), 0.0f);
    gtk_widget_add_css_class (note, "caption");
    gtk_widget_add_css_class (note, "dim-label");
    gtk_widget_set_margin_start (note, 28);
    gtk_widget_set_margin_bottom (note, 4);
    gtk_box_append (GTK_BOX (box), note);
  }
  return check;
}

static void
action_clear_data (GSimpleAction *action, GVariant *param, gpointer data)
{
  LyApp *app = data;

  AdwDialog *dialog = adw_alert_dialog_new ("Clear browsing data?", NULL);

  GtkWidget *box = gtk_box_new (GTK_ORIENTATION_VERTICAL, 2);
  ClearChoices *choices = g_new0 (ClearChoices, 1);
  choices->app = app;

  choices->cache = clear_check (box, "Cache",
    "Images and scripts. Costs nothing but a slower next load.", TRUE);
  choices->cookies = clear_check (box, "Cookies and site data",
    "Signs you out of everything.", FALSE);
  choices->history = clear_check (box, "History",
    app->store != NULL
      ? "Also empties address-bar suggestions."
      : "History is unavailable.", FALSE);
  choices->sites = clear_check (box, "Site settings",
    "Per-site zoom, blocker exceptions and never-save-password sites.", FALSE);

  adw_alert_dialog_set_extra_child (ADW_ALERT_DIALOG (dialog), box);
  adw_alert_dialog_add_responses (ADW_ALERT_DIALOG (dialog),
                                  "cancel", "Cancel", "clear", "Clear", NULL);
  adw_alert_dialog_set_response_appearance (ADW_ALERT_DIALOG (dialog), "clear",
                                            ADW_RESPONSE_DESTRUCTIVE);
  adw_alert_dialog_set_default_response (ADW_ALERT_DIALOG (dialog), "cancel");

  g_object_set_data_full (G_OBJECT (dialog), "lyndon-clear", choices, g_free);
  g_signal_connect (dialog, "response", G_CALLBACK (on_clear_response), choices);
  adw_dialog_present (dialog, active_window_widget (app));
}

static void
add_shortcut_row (AdwPreferencesGroup *group, const char *keys, const char *what)
{
  GtkWidget *row = adw_action_row_new ();
  adw_preferences_row_set_title (ADW_PREFERENCES_ROW (row), what);

  GtkWidget *label = gtk_label_new (keys);
  gtk_widget_add_css_class (label, "dim-label");
  gtk_widget_add_css_class (label, "lyndon-monospace");
  gtk_widget_set_valign (label, GTK_ALIGN_CENTER);
  adw_action_row_add_suffix (ADW_ACTION_ROW (row), label);

  adw_preferences_group_add (group, row);
}

static void
action_shortcuts (GSimpleAction *action, GVariant *param, gpointer data)
{
  LyApp *app = data;

  AdwDialog *dialog = adw_dialog_new ();
  adw_dialog_set_title (dialog, "Keyboard Shortcuts");
  adw_dialog_set_content_width (dialog, 460);
  adw_dialog_set_content_height (dialog, 560);

  AdwPreferencesPage *page = ADW_PREFERENCES_PAGE (adw_preferences_page_new ());

  struct { const char *title; const char *keys[8][2]; } SECTIONS[] = {
    { "Tabs", {
      { "Ctrl T",        "New tab" },
      { "Ctrl W",        "Close tab" },
      { "Ctrl Tab",      "Next tab" },
      { "Ctrl Shift Tab","Previous tab" },
      { "Ctrl Shift O",  "Tab overview" },
      { "Ctrl N",        "New window" },
      { NULL, NULL } } },
    { "Navigation", {
      { "Ctrl L",        "Focus the address bar" },
      { "Alt ←",         "Back" },
      { "Alt →",         "Forward" },
      { "Ctrl R / F5",   "Reload" },
      { "Ctrl Shift R",  "Reload, ignoring cache" },
      { "Esc",           "Stop, or close the find bar" },
      { NULL, NULL } } },
    { "Page", {
      { "Ctrl F",        "Find in page" },
      { "Ctrl G",        "Next match" },
      { "Ctrl Shift G",  "Previous match" },
      { "Ctrl +",        "Zoom in" },
      { "Ctrl −",        "Zoom out" },
      { "Ctrl 0",        "Actual size" },
      { "Ctrl Shift P",  "Pause protection on this site" },
      { NULL, NULL } } },
  };

  for (guint s = 0; s < G_N_ELEMENTS (SECTIONS); s++) {
    AdwPreferencesGroup *group = ADW_PREFERENCES_GROUP (adw_preferences_group_new ());
    adw_preferences_group_set_title (group, SECTIONS[s].title);
    for (guint k = 0; SECTIONS[s].keys[k][0] != NULL; k++)
      add_shortcut_row (group, SECTIONS[s].keys[k][0], SECTIONS[s].keys[k][1]);
    adw_preferences_page_add (page, group);
  }

  AdwToolbarView *toolbar = ADW_TOOLBAR_VIEW (adw_toolbar_view_new ());
  adw_toolbar_view_add_top_bar (toolbar, adw_header_bar_new ());
  adw_toolbar_view_set_content (toolbar, GTK_WIDGET (page));
  adw_dialog_set_child (dialog, GTK_WIDGET (toolbar));

  adw_dialog_present (dialog, active_window_widget (app));
}

static void
action_quit (GSimpleAction *action, GVariant *param, gpointer data)
{
  g_application_quit (G_APPLICATION (data));
}

static const GActionEntry APP_ACTIONS[] = {
  { "new-window",     action_new_window },
  { "private-window", action_private_window },
  { "clear-cache",    action_clear_cache },
  { "preferences", action_preferences },
  { "about",       action_about },
  { "shortcuts",   action_shortcuts },
  { "clear-data",  action_clear_data },
  { "quit",        action_quit },
};

static const struct { const char *action; const char *accels[4]; } ACCELS[] = {
  { "win.new-tab",           { "<Control>t", NULL } },
  { "win.close-tab",         { "<Control>w", NULL } },
  { "win.focus-url",         { "<Control>l", "<Alt>d", "F6", NULL } },
  { "win.reload",            { "<Control>r", "F5", NULL } },
  { "win.reload-hard",       { "<Control><Shift>r", "<Control>F5", NULL } },
  { "win.back",              { "<Alt>Left", NULL } },
  { "win.forward",           { "<Alt>Right", NULL } },
  { "win.find",              { "<Control>f", NULL } },
  { "win.find-next",         { "<Control>g", NULL } },
  { "win.find-prev",         { "<Control><Shift>g", NULL } },
  { "win.escape",            { "Escape", NULL } },
  { "win.zoom-in",           { "<Control>plus", "<Control>equal", "<Control>KP_Add", NULL } },
  { "win.zoom-out",          { "<Control>minus", "<Control>KP_Subtract", NULL } },
  { "win.zoom-reset",        { "<Control>0", NULL } },
  { "win.tab-overview",      { "<Control><Shift>o", NULL } },
  { "win.next-tab",          { "<Control>Tab", "<Control>Page_Down", NULL } },
  { "win.prev-tab",          { "<Control><Shift>Tab", "<Control>Page_Up", NULL } },
  { "win.toggle-protection", { "<Control><Shift>p", NULL } },
  { "app.new-window",        { "<Control>n", NULL } },
  { "app.private-window",    { "<Control><Shift>n", NULL } },
  { "app.clear-cache",       { "<Control><Shift>BackSpace", "<Control><Shift>k", NULL } },
  { "win.print",             { "<Control>p", NULL } },
  { "win.fullscreen",        { "F11", NULL } },
  { "win.home",              { "<Alt>Home", NULL } },
  { "win.reopen-tab",        { "<Control><Shift>t", NULL } },
  { "win.duplicate-tab",     { "<Control><Shift>d", NULL } },
  { "win.view-source",       { "<Control>u", NULL } },
  { "win.save-page",         { "<Control>s", NULL } },
  { "win.caret-browsing",    { "F7", NULL } },
  { "win.site-info",         { "<Control>i", NULL } },
  /* Ctrl+1..9, the way every other browser numbers tabs. */
  { "win.select-tab(1)",     { "<Control>1", NULL } },
  { "win.select-tab(2)",     { "<Control>2", NULL } },
  { "win.select-tab(3)",     { "<Control>3", NULL } },
  { "win.select-tab(4)",     { "<Control>4", NULL } },
  { "win.select-tab(5)",     { "<Control>5", NULL } },
  { "win.select-tab(6)",     { "<Control>6", NULL } },
  { "win.select-tab(7)",     { "<Control>7", NULL } },
  { "win.select-tab(8)",     { "<Control>8", NULL } },
  { "win.select-tab(9)",     { "<Control>9", NULL } },
  { "win.bookmark",          { "<Control>d", NULL } },
  { "win.history",           { "<Control>h", NULL } },
  { "app.preferences",       { "<Control>comma", NULL } },
  { "app.clear-data",        { "<Control><Shift>Delete", NULL } },
  { "app.quit",              { "<Control>q", NULL } },
};

/* -------------------------------------------------------------- windows */

LyWindow *
ly_app_new_window (LyApp *app)
{
  return ly_window_new (app);
}

LyWindow *
ly_app_new_private_window (LyApp *app)
{
  return ly_window_new_private (app);
}

void
ly_app_save_session (LyApp *app)
{
  if (app->store == NULL || !app->cfg->restore_session)
    return;

  g_autoptr (GPtrArray) rows =
    g_ptr_array_new_with_free_func ((GDestroyNotify) ly_store_row_free);

  int index = 0;
  GList *windows = gtk_application_get_windows (GTK_APPLICATION (app));
  for (GList *l = windows; l != NULL; l = l->next) {
    if (!LY_IS_WINDOW (l->data))
      continue;
    ly_window_collect_session (LY_WINDOW (l->data), rows, index);
    index++;
  }

  ly_store_save_session (app->store, rows);
}

/* Returns TRUE when at least one tab came back. */
static gboolean
restore_session (LyApp *app)
{
  if (app->store == NULL || !app->cfg->restore_session)
    return FALSE;

  g_autoptr (GPtrArray) rows = ly_store_load_session (app->store);
  if (rows->len == 0)
    return FALSE;

  LyWindow *window = NULL;
  int current = -1;

  for (guint i = 0; i < rows->len; i++) {
    LyStoreRow *row = g_ptr_array_index (rows, i);

    /* Rows arrive ordered by window then index, so a change of window number
     * is the signal to open the next one. */
    if (window == NULL || row->window != current) {
      window = ly_app_new_window (app);
      current = row->window;
    }
    ly_window_restore_tab (window, row->url, row->title);
  }

  windows_present_all (app);
  return TRUE;
}

void
ly_app_refresh (LyApp *app)
{
  apply_style (app);
  ly_engine_apply (app->engine);

  GList *windows = gtk_application_get_windows (GTK_APPLICATION (app));
  for (GList *l = windows; l != NULL; l = l->next)
    if (LY_IS_WINDOW (l->data))
      ly_window_refresh (LY_WINDOW (l->data));
}

static void
on_blocker_ready (LyBlocker *blocker, gpointer data)
{
  LyApp *app = data;
  GList *windows = gtk_application_get_windows (GTK_APPLICATION (app));
  for (GList *l = windows; l != NULL; l = l->next)
    if (LY_IS_WINDOW (l->data))
      ly_window_refresh (LY_WINDOW (l->data));
}

static void
on_dark_changed (GObject *style, GParamSpec *pspec, gpointer data)
{
  /* Following the system theme means the page background has to follow too. */
  ly_app_refresh (LY_APP (data));
}

/* ------------------------------------------------------------ lifecycle */

static void
ly_app_startup (GApplication *application)
{
  LyApp *app = LY_APP (application);

  G_APPLICATION_CLASS (ly_app_parent_class)->startup (application);

  app->cfg = ly_config_new ();
  ly_config_load (app->cfg);

  app->engine    = ly_engine_new (app->cfg);
  app->blocker   = ly_blocker_new (app->cfg);
  app->downloads = ly_downloads_new (app->cfg, ly_engine_session (app->engine));
  app->store     = ly_store_new ();
  app->passwords = ly_passwords_new (app->cfg);

  ly_blocker_set_ready_callback (app->blocker, on_blocker_ready, app);

  GdkDisplay *display = gdk_display_get_default ();

  app->base_css = gtk_css_provider_new ();
  gtk_css_provider_load_from_resource (app->base_css, "/org/lyndon/Browser/css/lyndon.css");
  gtk_style_context_add_provider_for_display (display, GTK_STYLE_PROVIDER (app->base_css),
                                              GTK_STYLE_PROVIDER_PRIORITY_APPLICATION);

  app->tuned_css = gtk_css_provider_new ();
  gtk_style_context_add_provider_for_display (display, GTK_STYLE_PROVIDER (app->tuned_css),
                                              GTK_STYLE_PROVIDER_PRIORITY_APPLICATION + 1);

  g_action_map_add_action_entries (G_ACTION_MAP (app), APP_ACTIONS,
                                   G_N_ELEMENTS (APP_ACTIONS), app);

  for (guint i = 0; i < G_N_ELEMENTS (ACCELS); i++)
    gtk_application_set_accels_for_action (GTK_APPLICATION (app), ACCELS[i].action,
                                           ACCELS[i].accels);

  g_signal_connect (adw_style_manager_get_default (), "notify::dark",
                    G_CALLBACK (on_dark_changed), app);

  apply_style (app);

  /* Compile rules straight away so the first page load is already protected,
   * and refresh any subscribed lists that have gone stale. */
  ly_blocker_rebuild (app->blocker);
  ly_blocker_update_subscriptions (app->blocker, FALSE);
}

static void
ly_app_activate (GApplication *application)
{
  LyApp *app = LY_APP (application);

  GtkWindow *existing = gtk_application_get_active_window (GTK_APPLICATION (app));
  if (existing != NULL) {
    gtk_window_present (existing);
    return;
  }

  if (restore_session (app))
    return;

  LyWindow *window = ly_app_new_window (app);
  ly_window_open_tab (window, NULL, FALSE);
  gtk_window_present (GTK_WINDOW (window));
}

static void
ly_app_open (GApplication *application, GFile **files, int n_files, const char *hint)
{
  LyApp *app = LY_APP (application);

  GtkWindow *existing = gtk_application_get_active_window (GTK_APPLICATION (app));
  LyWindow *window = LY_IS_WINDOW (existing) ? LY_WINDOW (existing) : ly_app_new_window (app);

  for (int i = 0; i < n_files; i++) {
    g_autofree char *uri = g_file_get_uri (files[i]);
    ly_window_open_tab (window, uri, i > 0);
  }

  gtk_window_present (GTK_WINDOW (window));
}

/* Clearing on exit has to finish before the process goes away, so the call is
 * pumped on a nested loop with a ceiling rather than left to chance. */
static void
on_exit_cleared (gpointer data)
{
  g_main_loop_quit ((GMainLoop *) data);
}

static gboolean
exit_clear_timeout (gpointer data)
{
  g_main_loop_quit ((GMainLoop *) data);
  return G_SOURCE_REMOVE;
}

static void
ly_app_shutdown (GApplication *application)
{
  LyApp *app = LY_APP (application);

  ly_app_save_session (app);

  if (app->cfg != NULL && app->cfg->clear_on_exit && app->engine != NULL) {
    g_autoptr (GMainLoop) loop = g_main_loop_new (NULL, FALSE);
    guint timeout = g_timeout_add_seconds (3, exit_clear_timeout, loop);
    ly_engine_clear_data (app->engine, WEBKIT_WEBSITE_DATA_ALL, on_exit_cleared, loop);
    g_main_loop_run (loop);
    g_source_remove (timeout);
  }

  if (app->cfg != NULL)
    ly_config_save (app->cfg);

  g_clear_pointer (&app->passwords, ly_passwords_free);
  g_clear_pointer (&app->store, ly_store_free);
  g_clear_pointer (&app->downloads, ly_downloads_free);
  g_clear_pointer (&app->blocker, ly_blocker_free);
  g_clear_pointer (&app->engine, ly_engine_free);
  g_clear_pointer (&app->cfg, ly_config_free);
  g_clear_object (&app->base_css);
  g_clear_object (&app->tuned_css);

  G_APPLICATION_CLASS (ly_app_parent_class)->shutdown (application);
}

static void
ly_app_class_init (LyAppClass *klass)
{
  GApplicationClass *application_class = G_APPLICATION_CLASS (klass);
  application_class->startup  = ly_app_startup;
  application_class->activate = ly_app_activate;
  application_class->open     = ly_app_open;
  application_class->shutdown = ly_app_shutdown;
}

static void
ly_app_init (LyApp *app)
{
}

LyApp *
ly_app_new (void)
{
  return g_object_new (LY_TYPE_APP,
                       "application-id", LYNDON_APP_ID,
                       "flags", G_APPLICATION_HANDLES_OPEN,
                       NULL);
}
