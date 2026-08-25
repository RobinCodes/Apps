/* window.c — see window.h. */

#include "window.h"
#include "prefs.h"

#include <math.h>
#include <string.h>

struct _LyWindow {
  AdwApplicationWindow parent_instance;

  LyApp       *app;
  LyConfig    *cfg;
  LyEngine    *engine;
  LyBlocker   *blocker;
  LyDownloads *downloads;
  LyStore     *store;
  LyPasswords *passwords;

  /* Owned only by private windows; NULL means the shared persistent session. */
  WebKitNetworkSession *private_session;

  AdwTabView     *tabs;
  AdwTabBar      *tab_bar;
  AdwTabOverview *overview;
  AdwToolbarView *toolbar;
  AdwToastOverlay *toasts;
  GtkWidget      *chrome;          /* box holding header + tab bar */

  /* header */
  GtkWidget *back, *forward, *reload;
  GtkWidget *url_box, *url_text, *security_icon, *security_button;
  GtkWidget *shield_button, *shield_icon;
  GtkWidget *star_button, *star_icon;
  GtkWidget *home_button;
  GtkWidget *zoom_button;
  GtkWidget *bookmarks_bar;
  GtkWidget *downloads_button;
  GtkWidget *progress;

  /* address-bar completion */
  GtkWidget *suggestions;       /* GtkPopover, deliberately not autohiding */
  GtkWidget *suggestion_list;   /* GtkListBox */
  GPtrArray *suggestion_rows;   /* LyStoreRow*, parallel to the list */
  guint      suggest_source;

  GPtrArray *closed_tabs;       /* char* URL, most recently closed last */

  /* The page the tab context menu was opened on; NULL when it is closed, in
   * which case the tab actions fall back to the selected page. */
  AdwTabPage *menu_page;

  /* find */
  GtkWidget *find_revealer, *find_entry, *find_count;

  /* status */
  GtkWidget *status_label, *status_revealer;

  gboolean url_focused;
  guint    blocker_watch;
};

G_DEFINE_FINAL_TYPE (LyWindow, ly_window, ADW_TYPE_APPLICATION_WINDOW)

static void sync_chrome (LyWindow *self);
static void tab_changed (LyTab *tab, gpointer data);
static void hide_suggestions (LyWindow *self);
static void open_suggestion  (LyWindow *self, int index);

/* Everything a new tab needs from this window, including the ephemeral
 * session when this is a private window. */
static LyTabContext
tab_context (LyWindow *self)
{
  return (LyTabContext) {
    .engine    = self->engine,
    .blocker   = self->blocker,
    .cfg       = self->cfg,
    .store     = self->store,
    .passwords = self->passwords,
    .session   = self->private_session,
  };
}

gboolean
ly_window_is_private (LyWindow *self)
{
  return self->private_session != NULL;
}

/* ------------------------------------------------------------- helpers */

static LyTab *
tab_from_page (AdwTabPage *page)
{
  if (page == NULL)
    return NULL;
  GtkWidget *child = adw_tab_page_get_child (page);
  return LY_IS_TAB (child) ? LY_TAB (child) : NULL;
}

LyTab *
ly_window_active_tab (LyWindow *self)
{
  return tab_from_page (adw_tab_view_get_selected_page (self->tabs));
}

void
ly_window_toast (LyWindow *self, const char *text)
{
  adw_toast_overlay_add_toast (self->toasts, adw_toast_new (text));
}

/* ------------------------------------------------------------ url field */

static void
set_url_text (LyWindow *self, const char *text)
{
  gtk_editable_set_text (GTK_EDITABLE (self->url_text), text ?: "");
}

static void
update_security_indicator (LyWindow *self, LyTab *tab)
{
  GtkImage *icon = GTK_IMAGE (self->security_icon);
  const char *uri = tab ? ly_tab_uri (tab) : NULL;

  gtk_widget_remove_css_class (self->security_icon, "security-secure");
  gtk_widget_remove_css_class (self->security_icon, "security-insecure");
  gtk_widget_remove_css_class (self->security_icon, "security-internal");

  if (uri == NULL || *uri == '\0' || ly_uri_is_internal (uri)) {
    gtk_image_set_from_icon_name (icon, "system-search-symbolic");
    gtk_widget_add_css_class (self->security_icon, "security-internal");
    gtk_widget_set_tooltip_text (self->security_button, "Site information");
  } else if (ly_uri_is_secure (uri)) {
    gtk_image_set_from_icon_name (icon, "channel-secure-symbolic");
    gtk_widget_add_css_class (self->security_icon, "security-secure");
    gtk_widget_set_tooltip_text (self->security_button, "Encrypted connection — click for details");
  } else {
    gtk_image_set_from_icon_name (icon, "channel-insecure-symbolic");
    gtk_widget_add_css_class (self->security_icon, "security-insecure");
    gtk_widget_set_tooltip_text (self->security_button,
                                 "Not encrypted — anything you type here is sent in the clear");
  }
}

static void
on_url_activate (GtkText *text, gpointer data)
{
  LyWindow *self = data;
  LyTab *tab = ly_window_active_tab (self);
  if (tab == NULL)
    tab = ly_window_open_tab (self, NULL, FALSE);

  GtkListBoxRow *selected = self->suggestion_list != NULL
    ? gtk_list_box_get_selected_row (GTK_LIST_BOX (self->suggestion_list)) : NULL;

  if (selected != NULL && gtk_widget_get_visible (self->suggestions)) {
    open_suggestion (self, gtk_list_box_row_get_index (selected));
    return;
  }

  hide_suggestions (self);
  const char *input = gtk_editable_get_text (GTK_EDITABLE (self->url_text));
  ly_tab_load_input (tab, input);
  gtk_widget_grab_focus (GTK_WIDGET (ly_tab_web_view (tab)));
}

static void
on_url_focus_enter (GtkEventControllerFocus *controller, gpointer data)
{
  LyWindow *self = data;
  self->url_focused = TRUE;

  /* Show the real URL while editing; the pretty form hides information the
   * user may specifically be looking for. */
  LyTab *tab = ly_window_active_tab (self);
  const char *uri = tab ? ly_tab_uri (tab) : NULL;
  if (uri != NULL && !ly_uri_is_internal (uri))
    set_url_text (self, uri);

  gtk_editable_select_region (GTK_EDITABLE (self->url_text), 0, -1);
}

static void
on_url_focus_leave (GtkEventControllerFocus *controller, gpointer data)
{
  LyWindow *self = data;
  self->url_focused = FALSE;
  hide_suggestions (self);
  sync_chrome (self);
}


/* --------------------------------------------------- address completion */

static void
hide_suggestions (LyWindow *self)
{
  if (self->suggest_source != 0) {
    g_source_remove (self->suggest_source);
    self->suggest_source = 0;
  }
  if (self->suggestions != NULL)
    gtk_popover_popdown (GTK_POPOVER (self->suggestions));
}

static void
open_suggestion (LyWindow *self, int index)
{
  if (index < 0 || index >= (int) self->suggestion_rows->len)
    return;

  LyStoreRow *entry = g_ptr_array_index (self->suggestion_rows, index);
  g_autofree char *url = g_strdup (entry->url);

  hide_suggestions (self);

  LyTab *tab = ly_window_active_tab (self);
  if (tab == NULL)
    tab = ly_window_open_tab (self, NULL, FALSE);
  ly_tab_load (tab, url);
  gtk_widget_grab_focus (GTK_WIDGET (ly_tab_web_view (tab)));
}

static void
on_suggestion_activated (GtkListBox *box, GtkListBoxRow *row, gpointer data)
{
  open_suggestion ((LyWindow *) data, gtk_list_box_row_get_index (row));
}

static GtkWidget *
build_suggestion_row (LyStoreRow *entry)
{
  GtkWidget *box = gtk_box_new (GTK_ORIENTATION_HORIZONTAL, 10);
  gtk_widget_set_margin_start (box, 8);
  gtk_widget_set_margin_end (box, 8);
  gtk_widget_set_margin_top (box, 5);
  gtk_widget_set_margin_bottom (box, 5);

  GtkWidget *icon = gtk_image_new_from_icon_name (
    entry->bookmarked ? "starred-symbolic" : "document-open-recent-symbolic");
  gtk_widget_add_css_class (icon, entry->bookmarked ? "accent" : "lyndon-dim");
  gtk_box_append (GTK_BOX (box), icon);

  GtkWidget *text = gtk_box_new (GTK_ORIENTATION_VERTICAL, 0);
  gtk_widget_set_hexpand (text, TRUE);

  const char *title = (entry->title && *entry->title) ? entry->title : entry->url;
  GtkWidget *title_label = gtk_label_new (title);
  gtk_label_set_ellipsize (GTK_LABEL (title_label), PANGO_ELLIPSIZE_END);
  gtk_label_set_xalign (GTK_LABEL (title_label), 0.0f);
  gtk_box_append (GTK_BOX (text), title_label);

  g_autofree char *pretty = ly_pretty_uri (entry->url);
  GtkWidget *url_label = gtk_label_new (pretty);
  gtk_label_set_ellipsize (GTK_LABEL (url_label), PANGO_ELLIPSIZE_MIDDLE);
  gtk_label_set_xalign (GTK_LABEL (url_label), 0.0f);
  gtk_widget_add_css_class (url_label, "caption");
  gtk_widget_add_css_class (url_label, "lyndon-dim");
  gtk_box_append (GTK_BOX (text), url_label);

  gtk_box_append (GTK_BOX (box), text);
  return box;
}

static gboolean
refresh_suggestions (gpointer data)
{
  LyWindow *self = data;
  self->suggest_source = 0;

  const char *text = gtk_editable_get_text (GTK_EDITABLE (self->url_text));

  /* One character matches almost everything; it is noise, not help. */
  if (self->store == NULL || text == NULL || g_utf8_strlen (text, -1) < 2) {
    hide_suggestions (self);
    return G_SOURCE_REMOVE;
  }

  GtkWidget *child;
  while ((child = gtk_widget_get_first_child (self->suggestion_list)) != NULL)
    gtk_list_box_remove (GTK_LIST_BOX (self->suggestion_list), child);
  g_ptr_array_set_size (self->suggestion_rows, 0);

  g_autoptr (GPtrArray) matches = ly_store_complete (self->store, text, 8);
  if (matches->len == 0) {
    gtk_popover_popdown (GTK_POPOVER (self->suggestions));
    return G_SOURCE_REMOVE;
  }

  for (guint i = 0; i < matches->len; i++) {
    LyStoreRow *entry = g_ptr_array_index (matches, i);
    gtk_list_box_append (GTK_LIST_BOX (self->suggestion_list), build_suggestion_row (entry));
    /* Steal the row so the popover can outlive the query result. */
    g_ptr_array_add (self->suggestion_rows, entry);
    g_ptr_array_index (matches, i) = NULL;
  }
  /* The array's free func would otherwise free the NULLs we left behind. */
  g_ptr_array_set_size (matches, 0);

  gtk_list_box_unselect_all (GTK_LIST_BOX (self->suggestion_list));
  gtk_widget_set_size_request (self->suggestions,
                               gtk_widget_get_width (self->url_box), -1);
  gtk_popover_popup (GTK_POPOVER (self->suggestions));
  return G_SOURCE_REMOVE;
}

static void
on_url_text_changed (GtkEditable *editable, gpointer data)
{
  LyWindow *self = data;
  if (!self->url_focused)
    return;
  if (self->suggest_source != 0)
    g_source_remove (self->suggest_source);
  self->suggest_source = g_timeout_add (110, refresh_suggestions, self);
}

static gboolean
on_url_key_pressed (GtkEventControllerKey *controller, guint keyval, guint keycode,
                    GdkModifierType state, gpointer data)
{
  LyWindow *self = data;

  if (keyval == GDK_KEY_Escape) {
    hide_suggestions (self);
    return GDK_EVENT_PROPAGATE;   /* Escape still stops the page */
  }

  if (!gtk_widget_get_visible (self->suggestions) || self->suggestion_rows->len == 0)
    return GDK_EVENT_PROPAGATE;

  int count = (int) self->suggestion_rows->len;
  GtkListBox *box = GTK_LIST_BOX (self->suggestion_list);
  GtkListBoxRow *selected = gtk_list_box_get_selected_row (box);
  int index = selected ? gtk_list_box_row_get_index (selected) : -1;

  if (keyval == GDK_KEY_Down)
    index = (index + 1 >= count) ? 0 : index + 1;
  else if (keyval == GDK_KEY_Up)
    index = (index <= 0) ? count - 1 : index - 1;
  else
    return GDK_EVENT_PROPAGATE;

  GtkListBoxRow *row = gtk_list_box_get_row_at_index (box, index);
  if (row != NULL)
    gtk_list_box_select_row (box, row);
  return GDK_EVENT_STOP;
}

/* --------------------------------------------------------------- star */

static void
update_star (LyWindow *self, LyTab *tab)
{
  const char *uri = tab ? ly_tab_uri (tab) : NULL;
  gboolean can_mark = uri != NULL && *uri != '\0' && !ly_uri_is_internal (uri);
  gboolean marked = can_mark && self->store != NULL &&
                    ly_store_is_bookmarked (self->store, uri);

  gtk_widget_set_sensitive (self->star_button, can_mark);
  gtk_image_set_from_icon_name (GTK_IMAGE (self->star_icon),
                                marked ? "starred-symbolic" : "non-starred-symbolic");
  if (marked)
    gtk_widget_add_css_class (self->star_button, "accent");
  else
    gtk_widget_remove_css_class (self->star_button, "accent");
  gtk_widget_set_tooltip_text (self->star_button,
                               marked ? "Remove bookmark" : "Bookmark this page");
}

/* ------------------------------------------------------------- shield */

static void
on_shield_toggled (GtkButton *button, gpointer data)
{
  LyWindow *self = data;
  LyTab *tab = ly_window_active_tab (self);
  if (tab == NULL)
    return;

  g_autofree char *host = ly_tab_host (tab);
  gboolean was_on = ly_tab_protection_on (tab);
  ly_tab_toggle_protection (tab);

  g_autofree char *message =
    g_strdup_printf (was_on ? "Protection paused on %s" : "Protection resumed on %s",
                     host ?: "this site");
  ly_window_toast (self, message);
}

static void
update_shield (LyWindow *self, LyTab *tab)
{
  gboolean on = tab != NULL && ly_tab_protection_on (tab);
  gboolean globally_off = !self->cfg->block_enabled;

  gtk_widget_remove_css_class (self->shield_button, "active");
  gtk_widget_remove_css_class (self->shield_button, "paused");
  gtk_widget_remove_css_class (self->shield_button, "inactive");

  if (globally_off) {
    gtk_image_set_from_icon_name (GTK_IMAGE (self->shield_icon), "security-low-symbolic");
    gtk_widget_add_css_class (self->shield_button, "inactive");
    gtk_widget_set_tooltip_text (self->shield_button, "Blocking is off for all sites");
  } else if (on) {
    gtk_image_set_from_icon_name (GTK_IMAGE (self->shield_icon), "security-high-symbolic");
    gtk_widget_add_css_class (self->shield_button, "active");
    g_autofree char *tip =
      g_strdup_printf ("Protection on — %u rules active\nClick to pause on this site",
                       ly_blocker_rule_count (self->blocker));
    gtk_widget_set_tooltip_text (self->shield_button, tip);
  } else {
    gtk_image_set_from_icon_name (GTK_IMAGE (self->shield_icon), "security-medium-symbolic");
    gtk_widget_add_css_class (self->shield_button, "paused");
    gtk_widget_set_tooltip_text (self->shield_button,
                                 "Protection paused on this site\nClick to resume");
  }
}

/* ------------------------------------------------------------ downloads */

static void
on_download_cancel (GtkButton *button, gpointer data)
{
  LyWindow *self = data;
  LyDownloadItem *item = g_object_get_data (G_OBJECT (button), "lyndon-item");
  ly_downloads_cancel (self->downloads, item);
}

static void
build_downloads_popover (LyWindow *self, GtkWidget *popover)
{
  GtkWidget *box = gtk_box_new (GTK_ORIENTATION_VERTICAL, 2);

  GtkWidget *title = gtk_label_new ("Downloads");
  gtk_widget_add_css_class (title, "lyndon-panel-title");
  gtk_widget_set_halign (title, GTK_ALIGN_START);
  gtk_box_append (GTK_BOX (box), title);

  GPtrArray *items = ly_downloads_items (self->downloads);

  if (items->len == 0) {
    GtkWidget *empty = gtk_label_new ("Nothing downloaded yet");
    gtk_widget_add_css_class (empty, "lyndon-dim");
    gtk_widget_set_margin_top (empty, 12);
    gtk_widget_set_margin_bottom (empty, 12);
    gtk_box_append (GTK_BOX (box), empty);
  }

  for (guint i = 0; i < items->len && i < 12; i++) {
    LyDownloadItem *item = g_ptr_array_index (items, i);

    GtkWidget *row = gtk_box_new (GTK_ORIENTATION_VERTICAL, 2);
    gtk_widget_add_css_class (row, "lyndon-download-row");

    GtkWidget *name = gtk_label_new (item->name);
    gtk_label_set_ellipsize (GTK_LABEL (name), PANGO_ELLIPSIZE_MIDDLE);
    gtk_label_set_xalign (GTK_LABEL (name), 0.0f);
    gtk_box_append (GTK_BOX (row), name);

    g_autofree char *size = ly_format_size (item->received);
    g_autofree char *detail =
      item->failed   ? g_strdup_printf ("Failed — %s", item->error ?: "unknown error")
    : item->finished ? g_strdup_printf ("Complete · %s", size)
                     : g_strdup_printf ("%.0f%% · %s", item->progress * 100.0, size);

    GtkWidget *sub = gtk_label_new (detail);
    gtk_widget_add_css_class (sub, "lyndon-dim");
    gtk_label_set_xalign (GTK_LABEL (sub), 0.0f);
    gtk_widget_add_css_class (sub, "caption");
    gtk_box_append (GTK_BOX (row), sub);

    if (!item->finished) {
      GtkWidget *line = gtk_box_new (GTK_ORIENTATION_HORIZONTAL, 6);

      GtkWidget *bar = gtk_progress_bar_new ();
      gtk_progress_bar_set_fraction (GTK_PROGRESS_BAR (bar), item->progress);
      gtk_widget_set_valign (bar, GTK_ALIGN_CENTER);
      gtk_widget_set_hexpand (bar, TRUE);
      gtk_box_append (GTK_BOX (line), bar);

      GtkWidget *cancel = gtk_button_new_from_icon_name ("window-close-symbolic");
      gtk_widget_add_css_class (cancel, "flat");
      gtk_widget_set_tooltip_text (cancel, "Cancel");
      g_object_set_data (G_OBJECT (cancel), "lyndon-item", item);
      g_signal_connect (cancel, "clicked", G_CALLBACK (on_download_cancel), self);
      gtk_box_append (GTK_BOX (line), cancel);

      gtk_box_append (GTK_BOX (row), line);
    }

    gtk_box_append (GTK_BOX (box), row);
  }

  if (items->len > 0) {
    GtkWidget *clear = gtk_button_new_with_label ("Clear finished");
    gtk_widget_add_css_class (clear, "flat");
    gtk_widget_set_margin_top (clear, 4);
    g_signal_connect_swapped (clear, "clicked",
                              G_CALLBACK (ly_downloads_clear_finished), self->downloads);
    gtk_box_append (GTK_BOX (box), clear);
  }

  gtk_popover_set_child (GTK_POPOVER (popover), box);
}

static void
on_downloads_changed (LyDownloads *downloads, gpointer data)
{
  LyWindow *self = data;
  guint active = ly_downloads_active_count (downloads);
  gtk_widget_set_visible (self->downloads_button,
                          ly_downloads_items (downloads)->len > 0);
  gtk_widget_set_tooltip_text (self->downloads_button,
                               active > 0 ? "Downloading…" : "Downloads");
}

/* --------------------------------------------------------------- shield
 * panel: a compact summary of exactly what is being enforced right now. */

static void
add_summary_row (GtkWidget *box, const char *label, const char *value)
{
  GtkWidget *row = gtk_box_new (GTK_ORIENTATION_HORIZONTAL, 12);
  gtk_widget_set_margin_start (row, 8);
  gtk_widget_set_margin_end (row, 8);
  gtk_widget_set_margin_top (row, 3);
  gtk_widget_set_margin_bottom (row, 3);

  GtkWidget *key = gtk_label_new (label);
  gtk_label_set_xalign (GTK_LABEL (key), 0.0f);
  gtk_widget_set_hexpand (key, TRUE);
  gtk_widget_add_css_class (key, "lyndon-dim");
  gtk_box_append (GTK_BOX (row), key);

  GtkWidget *val = gtk_label_new (value);
  gtk_widget_add_css_class (val, "lyndon-stat-value");
  gtk_box_append (GTK_BOX (row), val);

  gtk_box_append (GTK_BOX (box), row);
}

static void
build_shield_popover (LyWindow *self, GtkWidget *popover)
{
  LyTab *tab = ly_window_active_tab (self);
  g_autofree char *host = tab ? ly_tab_host (tab) : NULL;

  GtkWidget *box = gtk_box_new (GTK_ORIENTATION_VERTICAL, 2);

  GtkWidget *title = gtk_label_new (host ?: "This page");
  gtk_widget_add_css_class (title, "lyndon-panel-title");
  gtk_label_set_ellipsize (GTK_LABEL (title), PANGO_ELLIPSIZE_MIDDLE);
  gtk_widget_set_halign (title, GTK_ALIGN_START);
  gtk_box_append (GTK_BOX (box), title);

  const char *cookies;
  switch (self->cfg->cookie_policy) {
    case LY_COOKIES_NONE: cookies = "All blocked"; break;
    case LY_COOKIES_ALL:  cookies = "All allowed"; break;
    default:              cookies = "First party only"; break;
  }

  g_autofree char *rules = g_strdup_printf ("%u", ly_blocker_rule_count (self->blocker));

  guint on = 0;
  for (int i = 0; i < LY_CAT_N; i++)
    if (self->cfg->block_cat[i])
      on++;
  g_autofree char *cats = g_strdup_printf ("%u of %d", on, LY_CAT_N);

  add_summary_row (box, "Rules active",  self->cfg->block_enabled ? rules : "—");
  add_summary_row (box, "Categories",    cats);
  add_summary_row (box, "Cookies",       cookies);
  add_summary_row (box, "Tracking prevention", self->cfg->itp ? "On" : "Off");

  gtk_box_append (GTK_BOX (box), gtk_separator_new (GTK_ORIENTATION_HORIZONTAL));

  if (host != NULL) {
    gboolean on_here = ly_tab_protection_on (tab);
    GtkWidget *toggle = gtk_button_new_with_label (
      on_here ? "Pause protection on this site" : "Resume protection on this site");
    gtk_widget_add_css_class (toggle, "flat");
    gtk_widget_set_margin_top (toggle, 4);
    g_signal_connect (toggle, "clicked", G_CALLBACK (on_shield_toggled), self);
    g_signal_connect_swapped (toggle, "clicked", G_CALLBACK (gtk_popover_popdown), popover);
    gtk_box_append (GTK_BOX (box), toggle);
  }

  GtkWidget *status = gtk_label_new (ly_blocker_status_text (self->blocker));
  gtk_widget_add_css_class (status, "lyndon-dim");
  gtk_widget_add_css_class (status, "caption");
  gtk_widget_set_margin_top (status, 4);
  gtk_widget_set_margin_bottom (status, 2);
  gtk_box_append (GTK_BOX (box), status);

  GtkWidget *settings = gtk_button_new_with_label ("Blocking settings…");
  gtk_widget_add_css_class (settings, "flat");
  gtk_actionable_set_action_name (GTK_ACTIONABLE (settings), "app.preferences");
  g_signal_connect_swapped (settings, "clicked", G_CALLBACK (gtk_popover_popdown), popover);
  gtk_box_append (GTK_BOX (box), settings);

  gtk_popover_set_child (GTK_POPOVER (popover), box);
}

/* Both panels are rebuilt each time they open: their contents depend on the
 * page that happens to be in front, so caching them would only mean
 * invalidating them from six other places. */
static void
on_shield_popover_show (GtkPopover *popover, gpointer data)
{
  build_shield_popover (LY_WINDOW (data), GTK_WIDGET (popover));
}

static void
on_downloads_popover_show (GtkPopover *popover, gpointer data)
{
  build_downloads_popover (LY_WINDOW (data), GTK_WIDGET (popover));
}


/* ------------------------------------------------- bookmarks and history */

/* One dialog serves both lists: they differ only in where the rows come from
 * and what the destructive button does. */
typedef struct {
  LyWindow *window;
  gboolean  bookmarks;
  GtkWidget *list;
  AdwDialog *dialog;
} Library;

static void library_populate (Library *library);

static void
on_library_row_activated (GtkListBox *box, GtkListBoxRow *row, gpointer data)
{
  Library *library = data;
  const char *url = g_object_get_data (G_OBJECT (row), "lyndon-url");
  if (url == NULL)
    return;

  g_autofree char *copy = g_strdup (url);
  adw_dialog_close (library->dialog);
  ly_window_open_tab (library->window, copy, FALSE);
}

static void
on_library_remove (GtkButton *button, gpointer data)
{
  Library *library = data;
  const char *url = g_object_get_data (G_OBJECT (button), "lyndon-url");
  if (url == NULL)
    return;

  if (library->bookmarks)
    ly_store_remove_bookmark (library->window->store, url);
  else
    ly_store_forget_url (library->window->store, url);

  library_populate (library);
  sync_chrome (library->window);
}

static void
on_library_clear (GtkButton *button, gpointer data)
{
  Library *library = data;
  if (!library->bookmarks)
    ly_store_clear_history (library->window->store);
  library_populate (library);
}

static void
library_populate (Library *library)
{
  GtkWidget *child;
  while ((child = gtk_widget_get_first_child (library->list)) != NULL)
    gtk_list_box_remove (GTK_LIST_BOX (library->list), child);

  g_autoptr (GPtrArray) rows = library->bookmarks
    ? ly_store_bookmarks (library->window->store, 500)
    : ly_store_recent (library->window->store, 500);

  if (rows->len == 0) {
    GtkWidget *empty = adw_action_row_new ();
    adw_preferences_row_set_title (ADW_PREFERENCES_ROW (empty),
                                   library->bookmarks ? "No bookmarks yet"
                                                      : "No history");
    gtk_list_box_append (GTK_LIST_BOX (library->list), empty);
    return;
  }

  for (guint i = 0; i < rows->len; i++) {
    LyStoreRow *entry = g_ptr_array_index (rows, i);

    GtkWidget *row = adw_action_row_new ();
    const char *title = (entry->title && *entry->title) ? entry->title : entry->url;
    adw_preferences_row_set_title (ADW_PREFERENCES_ROW (row), title);
    adw_preferences_row_set_use_markup (ADW_PREFERENCES_ROW (row), FALSE);

    g_autofree char *pretty = ly_pretty_uri (entry->url);
    adw_action_row_set_subtitle (ADW_ACTION_ROW (row), pretty);
    gtk_list_box_row_set_activatable (GTK_LIST_BOX_ROW (row), TRUE);
    g_object_set_data_full (G_OBJECT (row), "lyndon-url", g_strdup (entry->url), g_free);

    GtkWidget *remove = gtk_button_new_from_icon_name ("user-trash-symbolic");
    gtk_widget_add_css_class (remove, "flat");
    gtk_widget_set_valign (remove, GTK_ALIGN_CENTER);
    gtk_widget_set_tooltip_text (remove, library->bookmarks ? "Remove bookmark" : "Forget");
    g_object_set_data_full (G_OBJECT (remove), "lyndon-url", g_strdup (entry->url), g_free);
    g_signal_connect (remove, "clicked", G_CALLBACK (on_library_remove), library);
    adw_action_row_add_suffix (ADW_ACTION_ROW (row), remove);

    gtk_list_box_append (GTK_LIST_BOX (library->list), row);
  }
}

static void
present_library (LyWindow *self, gboolean bookmarks)
{
  Library *library = g_new0 (Library, 1);
  library->window    = self;
  library->bookmarks = bookmarks;

  AdwDialog *dialog = adw_dialog_new ();
  library->dialog = dialog;
  adw_dialog_set_title (dialog, bookmarks ? "Bookmarks" : "History");
  adw_dialog_set_content_width (dialog, 560);
  adw_dialog_set_content_height (dialog, 620);
  g_object_set_data_full (G_OBJECT (dialog), "lyndon-library", library, g_free);

  library->list = gtk_list_box_new ();
  gtk_list_box_set_selection_mode (GTK_LIST_BOX (library->list), GTK_SELECTION_NONE);
  gtk_widget_add_css_class (library->list, "boxed-list");
  gtk_widget_set_margin_start (library->list, 12);
  gtk_widget_set_margin_end (library->list, 12);
  gtk_widget_set_margin_top (library->list, 12);
  gtk_widget_set_margin_bottom (library->list, 12);
  g_signal_connect (library->list, "row-activated",
                    G_CALLBACK (on_library_row_activated), library);

  GtkWidget *scroller = gtk_scrolled_window_new ();
  gtk_scrolled_window_set_child (GTK_SCROLLED_WINDOW (scroller), library->list);
  gtk_widget_set_vexpand (scroller, TRUE);

  GtkWidget *header = adw_header_bar_new ();
  if (!bookmarks) {
    GtkWidget *clear = gtk_button_new_with_label ("Clear all");
    gtk_widget_add_css_class (clear, "destructive-action");
    g_signal_connect (clear, "clicked", G_CALLBACK (on_library_clear), library);
    adw_header_bar_pack_end (ADW_HEADER_BAR (header), clear);
  }

  AdwToolbarView *toolbar = ADW_TOOLBAR_VIEW (adw_toolbar_view_new ());
  adw_toolbar_view_add_top_bar (toolbar, header);
  adw_toolbar_view_set_content (toolbar, scroller);
  adw_dialog_set_child (dialog, GTK_WIDGET (toolbar));

  library_populate (library);
  adw_dialog_present (dialog, GTK_WIDGET (self));
}


/* ------------------------------------------------------------- site panel */

typedef struct {
  LyWindow *window;
  int       permission;
} PermChoice;

static void build_site_panel (LyWindow *self, GtkWidget *popover);

static void
free_perm_choice (gpointer data, GClosure *closure)
{
  g_free (data);
}

static void
on_site_permission_changed (GObject *row, GParamSpec *pspec, gpointer data)
{
  PermChoice *choice = data;
  LyWindow *self = choice->window;
  LyTab *tab = ly_window_active_tab (self);
  if (tab == NULL || self->store == NULL)
    return;

  g_autofree char *host = ly_tab_host (tab);
  /* Index 0 is "use the default", so the stored policy is one less. */
  int selected = (int) adw_combo_row_get_selected (ADW_COMBO_ROW (row));
  ly_store_set_site_permission (self->store, host, choice->permission, selected - 1);
}

static void
on_clear_site_data (GtkButton *button, gpointer data)
{
  LyWindow *self = data;
  LyTab *tab = ly_window_active_tab (self);
  if (tab == NULL)
    return;

  g_autofree char *host = ly_tab_host (tab);
  if (host == NULL)
    return;

  if (self->store != NULL)
    ly_store_clear_site (self->store, host);

  /* Cookies and storage for one origin, rather than the whole profile. */
  ly_engine_clear_data_for_host (self->engine, host);

  g_autofree char *message = g_strdup_printf ("Cleared data for %s", host);
  ly_window_toast (self, message);
  sync_chrome (self);
}

static void
on_site_panel_show (GtkPopover *popover, gpointer data)
{
  build_site_panel (LY_WINDOW (data), GTK_WIDGET (popover));
}

static void
build_site_panel (LyWindow *self, GtkWidget *popover)
{
  LyTab *tab = ly_window_active_tab (self);
  g_autofree char *host = tab ? ly_tab_host (tab) : NULL;
  const char *uri = tab ? ly_tab_uri (tab) : NULL;

  GtkWidget *box = gtk_box_new (GTK_ORIENTATION_VERTICAL, 2);

  GtkWidget *title = gtk_label_new (host ?: "This page");
  gtk_widget_add_css_class (title, "lyndon-panel-title");
  gtk_label_set_ellipsize (GTK_LABEL (title), PANGO_ELLIPSIZE_MIDDLE);
  gtk_widget_set_halign (title, GTK_ALIGN_START);
  gtk_box_append (GTK_BOX (box), title);

  /* -- connection -------------------------------------------------- */
  GTlsCertificate *certificate = NULL;
  GTlsCertificateFlags errors = 0;
  gboolean tls = tab != NULL && ly_tab_tls_info (tab, &certificate, &errors);

  if (uri != NULL && ly_uri_is_internal (uri)) {
    add_summary_row (box, "Connection", "Internal page");
  } else if (tls && errors == 0) {
    add_summary_row (box, "Connection", "Encrypted");
    if (certificate != NULL) {
      g_autofree char *issuer = g_tls_certificate_get_issuer_name (certificate);
      if (issuer != NULL)
        add_summary_row (box, "Certificate from", issuer);

      g_autoptr (GDateTime) expiry = g_tls_certificate_get_not_valid_after (certificate);
      if (expiry != NULL) {
        g_autofree char *when = g_date_time_format (expiry, "%e %b %Y");
        add_summary_row (box, "Valid until", g_strstrip (when));
      }
    }
  } else if (tls) {
    add_summary_row (box, "Connection", "Certificate problem");
  } else {
    add_summary_row (box, "Connection", "Not encrypted");
  }

  if (tab != NULL)
    add_summary_row (box, "Blocking", ly_tab_protection_on (tab) ? "On" : "Paused");

  /* -- per-site permissions ---------------------------------------- */
  if (host != NULL && self->store != NULL) {
    gtk_box_append (GTK_BOX (box), gtk_separator_new (GTK_ORIENTATION_HORIZONTAL));

    GtkWidget *heading = gtk_label_new ("Permissions here");
    gtk_widget_add_css_class (heading, "lyndon-panel-title");
    gtk_widget_set_halign (heading, GTK_ALIGN_START);
    gtk_box_append (GTK_BOX (box), heading);

    GtkWidget *list = gtk_list_box_new ();
    gtk_widget_add_css_class (list, "boxed-list");
    gtk_list_box_set_selection_mode (GTK_LIST_BOX (list), GTK_SELECTION_NONE);

    /* Only the ones people actually decide per site. The full set stays in
     * Preferences. */
    static const int SHOWN[] = {
      LY_PERM_GEOLOCATION, LY_PERM_CAMERA, LY_PERM_MICROPHONE,
      LY_PERM_NOTIFICATIONS, LY_PERM_CLIPBOARD,
    };
    static const char *const CHOICES[] = { "Default", "Ask", "Allow", "Block", NULL };

    for (guint i = 0; i < G_N_ELEMENTS (SHOWN); i++) {
      int permission = SHOWN[i];

      GtkWidget *row = adw_combo_row_new ();
      adw_preferences_row_set_title (ADW_PREFERENCES_ROW (row), ly_perm_label (permission));
      adw_combo_row_set_model (ADW_COMBO_ROW (row),
                               G_LIST_MODEL (gtk_string_list_new (CHOICES)));

      int override = ly_store_site_permission (self->store, host, permission);
      adw_combo_row_set_selected (ADW_COMBO_ROW (row),
                                  override < 0 ? 0 : (guint) override + 1);

      PermChoice *choice = g_new0 (PermChoice, 1);
      choice->window     = self;
      choice->permission = permission;
      g_signal_connect_data (row, "notify::selected",
                             G_CALLBACK (on_site_permission_changed),
                             choice, (GClosureNotify) free_perm_choice, 0);

      gtk_list_box_append (GTK_LIST_BOX (list), row);
    }
    gtk_box_append (GTK_BOX (box), list);

    GtkWidget *clear = gtk_button_new_with_label ("Clear data for this site");
    gtk_widget_add_css_class (clear, "flat");
    gtk_widget_set_margin_top (clear, 6);
    g_signal_connect (clear, "clicked", G_CALLBACK (on_clear_site_data), self);
    g_signal_connect_swapped (clear, "clicked", G_CALLBACK (gtk_popover_popdown), popover);
    gtk_box_append (GTK_BOX (box), clear);
  }

  gtk_popover_set_child (GTK_POPOVER (popover), box);
}


/* ------------------------------------------------------------- zoom chip */

static void
update_zoom_chip (LyWindow *self, LyTab *tab)
{
  double zoom = tab ? ly_tab_zoom (tab) : 1.0;
  /* Only worth screen space when it differs from what the user asked for. */
  gboolean show = tab != NULL && ABS (zoom - self->cfg->default_zoom) > 0.01;

  gtk_widget_set_visible (self->zoom_button, show);
  if (!show)
    return;

  g_autofree char *label = g_strdup_printf ("%.0f%%", zoom * 100.0);
  gtk_button_set_label (GTK_BUTTON (self->zoom_button), label);
  gtk_widget_set_tooltip_text (self->zoom_button, "Reset zoom");
}

/* -------------------------------------------------------- bookmarks bar */

static void
on_bookmark_bar_clicked (GtkButton *button, gpointer data)
{
  LyWindow *self = data;
  const char *uri = g_object_get_data (G_OBJECT (button), "lyndon-url");
  LyTab *tab = ly_window_active_tab (self);

  if (tab != NULL)
    ly_tab_load (tab, uri);
  else
    ly_window_open_tab (self, uri, FALSE);
}

static void
refresh_bookmarks_bar (LyWindow *self)
{
  if (self->bookmarks_bar == NULL)
    return;

  gboolean show = self->cfg->show_bookmarks_bar && self->store != NULL;
  gtk_widget_set_visible (self->bookmarks_bar, show);
  if (!show)
    return;

  GtkWidget *child;
  while ((child = gtk_widget_get_first_child (self->bookmarks_bar)) != NULL)
    gtk_box_remove (GTK_BOX (self->bookmarks_bar), child);

  g_autoptr (GPtrArray) marks = ly_store_bookmarks (self->store, 30);

  if (marks->len == 0) {
    GtkWidget *hint = gtk_label_new ("Bookmarks you add appear here");
    gtk_widget_add_css_class (hint, "lyndon-dim");
    gtk_widget_add_css_class (hint, "caption");
    gtk_box_append (GTK_BOX (self->bookmarks_bar), hint);
    return;
  }

  for (guint i = 0; i < marks->len; i++) {
    LyStoreRow *mark = g_ptr_array_index (marks, i);

    const char *label = (mark->title && *mark->title) ? mark->title : mark->url;
    GtkWidget *button = gtk_button_new_with_label (label);
    gtk_widget_add_css_class (button, "flat");
    gtk_widget_add_css_class (button, "lyndon-bookmark");
    gtk_widget_set_tooltip_text (button, mark->url);

    GtkWidget *text = gtk_button_get_child (GTK_BUTTON (button));
    if (GTK_IS_LABEL (text)) {
      gtk_label_set_ellipsize (GTK_LABEL (text), PANGO_ELLIPSIZE_END);
      gtk_label_set_max_width_chars (GTK_LABEL (text), 22);
    }

    g_object_set_data_full (G_OBJECT (button), "lyndon-url",
                            g_strdup (mark->url), g_free);
    g_signal_connect (button, "clicked", G_CALLBACK (on_bookmark_bar_clicked), self);
    gtk_box_append (GTK_BOX (self->bookmarks_bar), button);
  }
}

/* ------------------------------------------------------------ paste and go */

static void
on_pasted_text (GObject *source, GAsyncResult *result, gpointer data)
{
  LyWindow *self = data;
  g_autoptr (GError) error = NULL;
  g_autofree char *text = gdk_clipboard_read_text_finish (GDK_CLIPBOARD (source),
                                                          result, &error);
  if (text == NULL || *text == '\0')
    return;

  LyTab *tab = ly_window_active_tab (self);
  if (tab == NULL)
    tab = ly_window_open_tab (self, NULL, FALSE);
  ly_tab_load_input (tab, text);
}

/* Middle-click on the address bar is paste-and-go everywhere on Linux. */
static void
on_url_middle_click (GtkGestureClick *gesture, int n_press, double x, double y,
                     gpointer data)
{
  LyWindow *self = data;
  GdkClipboard *primary =
    gdk_display_get_primary_clipboard (gtk_widget_get_display (GTK_WIDGET (self)));
  gdk_clipboard_read_text_async (primary, NULL, on_pasted_text, self);
  gtk_gesture_set_state (GTK_GESTURE (gesture), GTK_EVENT_SEQUENCE_CLAIMED);
}

/* ------------------------------------------------------------ chrome sync */

static void
sync_chrome (LyWindow *self)
{
  LyTab *tab = ly_window_active_tab (self);

  gtk_widget_set_sensitive (self->back,    tab && ly_tab_can_go_back (tab));
  gtk_widget_set_sensitive (self->forward, tab && ly_tab_can_go_forward (tab));

  gboolean loading = tab && ly_tab_is_loading (tab);
  gtk_button_set_icon_name (GTK_BUTTON (self->reload),
                            loading ? "process-stop-symbolic" : "view-refresh-symbolic");
  gtk_widget_set_tooltip_text (self->reload, loading ? "Stop" : "Reload");

  if (loading) {
    gtk_widget_set_visible (self->progress, TRUE);
    gtk_progress_bar_set_fraction (GTK_PROGRESS_BAR (self->progress),
                                   tab ? ly_tab_progress (tab) : 0.0);
  } else {
    gtk_widget_set_visible (self->progress, FALSE);
  }

  if (!self->url_focused) {
    const char *uri = tab ? ly_tab_uri (tab) : NULL;
    if (uri == NULL || *uri == '\0' || ly_uri_is_internal (uri))
      set_url_text (self, "");
    else {
      g_autofree char *pretty = ly_pretty_uri (uri);
      set_url_text (self, pretty);
    }
  }

  update_security_indicator (self, tab);
  update_shield (self, tab);
  update_star (self, tab);
  update_zoom_chip (self, tab);
}

static void
sync_tab_page (LyWindow *self, LyTab *tab)
{
  AdwTabPage *page = adw_tab_view_get_page (self->tabs, GTK_WIDGET (tab));
  if (page == NULL)
    return;

  adw_tab_page_set_title (page, ly_tab_title (tab));
  adw_tab_page_set_tooltip (page, ly_tab_uri (tab));
  adw_tab_page_set_loading (page, ly_tab_is_loading (tab));

  GdkTexture *favicon = ly_tab_favicon (tab);
  adw_tab_page_set_icon (page, favicon ? G_ICON (favicon) : NULL);

  /* The indicator shows one thing at a time. Audio wins: it is the one a
   * person needs to act on immediately. */
  if (ly_tab_is_muted (tab) || ly_tab_is_playing_audio (tab)) {
    gboolean muted = ly_tab_is_muted (tab);
    g_autoptr (GIcon) icon = g_themed_icon_new (
      muted ? "audio-volume-muted-symbolic" : "audio-volume-high-symbolic");
    adw_tab_page_set_indicator_icon (page, icon);
    adw_tab_page_set_indicator_activatable (page, TRUE);
    adw_tab_page_set_indicator_tooltip (page, muted ? "Unmute this tab"
                                                    : "Mute this tab");
  } else if (self->cfg->block_enabled && !ly_tab_protection_on (tab)) {
    g_autoptr (GIcon) icon = g_themed_icon_new ("security-medium-symbolic");
    adw_tab_page_set_indicator_icon (page, icon);
    adw_tab_page_set_indicator_activatable (page, FALSE);
    adw_tab_page_set_indicator_tooltip (page, "Protection paused on this site");
  } else {
    adw_tab_page_set_indicator_icon (page, NULL);
  }
}

/* Clicking the speaker on a tab mutes it without switching to it. */
static void
on_indicator_activated (AdwTabView *view, AdwTabPage *page, gpointer data)
{
  LyWindow *self = data;
  LyTab *tab = tab_from_page (page);
  if (tab == NULL)
    return;
  ly_tab_set_muted (tab, !ly_tab_is_muted (tab));
  sync_tab_page (self, tab);
}

static void
tab_changed (LyTab *tab, gpointer data)
{
  LyWindow *self = data;
  sync_tab_page (self, tab);
  if (ly_window_active_tab (self) == tab)
    sync_chrome (self);
}

static void
tab_status (LyTab *tab, const char *hovered_uri, gpointer data)
{
  LyWindow *self = data;
  gboolean show = hovered_uri != NULL && *hovered_uri != '\0';

  if (show) {
    g_autofree char *pretty = ly_pretty_uri (hovered_uri);
    gtk_label_set_text (GTK_LABEL (self->status_label), pretty);
  }
  gtk_revealer_set_reveal_child (GTK_REVEALER (self->status_revealer), show);
}

static void
tab_found (LyTab *tab, guint matches, gpointer data)
{
  LyWindow *self = data;
  if (matches == 0) {
    gtk_label_set_text (GTK_LABEL (self->find_count), "No matches");
    gtk_widget_add_css_class (self->find_entry, "error");
  } else {
    g_autofree char *text = g_strdup_printf ("%u match%s", matches, matches == 1 ? "" : "es");
    gtk_label_set_text (GTK_LABEL (self->find_count), text);
    gtk_widget_remove_css_class (self->find_entry, "error");
  }
}

static void tab_close_requested (LyTab *tab, gpointer data);

static void
tab_open_uri (LyTab *tab, const char *uri, gboolean background, gpointer data)
{
  ly_window_open_tab (LY_WINDOW (data), uri, background);
}

static void
tab_search_for (LyTab *tab, const char *text, gpointer data)
{
  LyWindow *self = data;
  g_autofree char *uri = ly_normalise_input (text, self->cfg->search_url);

  /* A selection is a search phrase even when it happens to look like a URL —
   * that is what the menu item promised. */
  if (uri == NULL)
    return;
  ly_window_open_tab (self, uri, FALSE);
}

static const LyTabDelegate TAB_DELEGATE = {
  .changed     = tab_changed,
  .status      = tab_status,
  .create_view = NULL,   /* filled in per window below */
  .close       = tab_close_requested,
  .found       = tab_found,
  .open_uri    = tab_open_uri,
  .search_for  = tab_search_for,
};

static void
tab_close_requested (LyTab *tab, gpointer data)
{
  LyWindow *self = data;
  AdwTabPage *page = adw_tab_view_get_page (self->tabs, GTK_WIDGET (tab));
  if (page != NULL)
    adw_tab_view_close_page (self->tabs, page);
}

static WebKitWebView *
tab_create_view (LyTab *opener, gpointer data)
{
  LyWindow *self = data;

  /* related-view keeps the new page in the opener's web process, which is what
   * makes window.opener and named-window targeting work. */
  WebKitWebView *view = WEBKIT_WEB_VIEW (
    g_object_new (WEBKIT_TYPE_WEB_VIEW,
                  "related-view", ly_tab_web_view (opener),
                  NULL));

  LyTabContext context = tab_context (self);
  LyTab *tab = ly_tab_new_for_view (&context, view);

  LyTabDelegate delegate = TAB_DELEGATE;
  delegate.create_view = tab_create_view;
  ly_tab_set_delegate (tab, &delegate, self);

  AdwTabPage *page = adw_tab_view_add_page (self->tabs, GTK_WIDGET (tab),
                                            adw_tab_view_get_selected_page (self->tabs));
  adw_tab_page_set_title (page, "New Tab");
  adw_tab_view_set_selected_page (self->tabs, page);

  return view;
}

LyTab *
ly_window_open_tab (LyWindow *self, const char *uri, gboolean background)
{
  LyTabContext context = tab_context (self);
  LyTab *tab = ly_tab_new (&context);

  LyTabDelegate delegate = TAB_DELEGATE;
  delegate.create_view = tab_create_view;
  ly_tab_set_delegate (tab, &delegate, self);

  AdwTabPage *page = adw_tab_view_append (self->tabs, GTK_WIDGET (tab));
  adw_tab_page_set_title (page, "New Tab");

  if (!background)
    adw_tab_view_set_selected_page (self->tabs, page);

  ly_tab_load (tab, uri && *uri ? uri : self->cfg->homepage);

  if (!background && (uri == NULL || *uri == '\0'))
    gtk_widget_grab_focus (self->url_text);

  return tab;
}

/* ------------------------------------------------------------- tab view */

static void
on_selected_page_changed (GObject *object, GParamSpec *pspec, gpointer data)
{
  LyWindow *self = data;
  self->url_focused = FALSE;
  sync_chrome (self);

  LyTab *tab = ly_window_active_tab (self);
  if (tab != NULL)
    sync_tab_page (self, tab);
}

/* Remember what was closed so Ctrl+Shift+T can bring it back. Private windows
 * deliberately remember nothing. */
static gboolean
on_close_page (AdwTabView *view, AdwTabPage *page, gpointer data)
{
  LyWindow *self = data;
  LyTab *tab = tab_from_page (page);

  if (tab != NULL && !ly_window_is_private (self)) {
    const char *uri = ly_tab_uri (tab);
    if (uri != NULL && *uri != '\0' && !ly_uri_is_internal (uri)) {
      g_ptr_array_add (self->closed_tabs, g_strdup (uri));
      if (self->closed_tabs->len > 25)
        g_ptr_array_remove_index (self->closed_tabs, 0);
    }
  }
  return GDK_EVENT_PROPAGATE;   /* let the default close proceed */
}

static void
on_pages_changed (GObject *object, GParamSpec *pspec, gpointer data)
{
  LyWindow *self = data;
  if (adw_tab_view_get_n_pages (self->tabs) == 0)
    gtk_window_close (GTK_WINDOW (self));
}

static AdwTabView *
on_create_window (AdwTabView *view, gpointer data)
{
  /* Dragging a tab out of the strip makes a new window. */
  LyWindow *self = data;
  LyWindow *fresh = ly_app_new_window (self->app);
  gtk_window_present (GTK_WINDOW (fresh));
  return fresh->tabs;
}

static AdwTabPage *
on_overview_create_tab (AdwTabOverview *overview, gpointer data)
{
  LyWindow *self = data;
  LyTab *tab = ly_window_open_tab (self, NULL, FALSE);
  return adw_tab_view_get_page (self->tabs, GTK_WIDGET (tab));
}

/* ---------------------------------------------------------------- find */

static void
find_show (LyWindow *self, gboolean show)
{
  gtk_revealer_set_reveal_child (GTK_REVEALER (self->find_revealer), show);
  if (show) {
    gtk_widget_grab_focus (self->find_entry);
    gtk_editable_select_region (GTK_EDITABLE (self->find_entry), 0, -1);
  } else {
    LyTab *tab = ly_window_active_tab (self);
    if (tab != NULL) {
      ly_tab_find_close (tab);
      gtk_widget_grab_focus (GTK_WIDGET (ly_tab_web_view (tab)));
    }
    gtk_label_set_text (GTK_LABEL (self->find_count), "");
  }
}

static void
on_find_changed (GtkEditable *entry, gpointer data)
{
  LyWindow *self = data;
  LyTab *tab = ly_window_active_tab (self);
  if (tab == NULL)
    return;

  const char *text = gtk_editable_get_text (entry);
  if (*text == '\0') {
    ly_tab_find_close (tab);
    gtk_label_set_text (GTK_LABEL (self->find_count), "");
    gtk_widget_remove_css_class (self->find_entry, "error");
    return;
  }
  ly_tab_find (tab, text, FALSE);
}

/* -------------------------------------------------------------- actions */

#define TAB_ACTION(name, body)                                          \
  static void name (GSimpleAction *action, GVariant *param, gpointer data) { \
    LyWindow *self = data;                                              \
    LyTab *tab = ly_window_active_tab (self);                           \
    if (tab == NULL) return;                                            \
    body;                                                               \
  }

TAB_ACTION (action_back,        ly_tab_go_back (tab))
TAB_ACTION (action_forward,     ly_tab_go_forward (tab))
TAB_ACTION (action_reload_hard, ly_tab_reload (tab, TRUE))
TAB_ACTION (action_zoom_in,     ly_tab_set_zoom (tab, ly_tab_zoom (tab) * 1.1))
TAB_ACTION (action_zoom_out,    ly_tab_set_zoom (tab, ly_tab_zoom (tab) / 1.1))
TAB_ACTION (action_zoom_reset,  ly_tab_set_zoom (tab, self->cfg->default_zoom))
TAB_ACTION (action_toggle_protection, ly_tab_toggle_protection (tab);
                                      sync_chrome (self))

static void
action_reload (GSimpleAction *action, GVariant *param, gpointer data)
{
  LyWindow *self = data;
  LyTab *tab = ly_window_active_tab (self);
  if (tab == NULL)
    return;
  if (ly_tab_is_loading (tab))
    ly_tab_stop (tab);
  else
    ly_tab_reload (tab, FALSE);
}

static void
action_new_tab (GSimpleAction *action, GVariant *param, gpointer data)
{
  ly_window_open_tab (LY_WINDOW (data), NULL, FALSE);
}

static void
action_close_tab (GSimpleAction *action, GVariant *param, gpointer data)
{
  LyWindow *self = data;
  AdwTabPage *page = adw_tab_view_get_selected_page (self->tabs);
  if (page != NULL)
    adw_tab_view_close_page (self->tabs, page);
}

static void
action_focus_url (GSimpleAction *action, GVariant *param, gpointer data)
{
  LyWindow *self = data;
  gtk_widget_grab_focus (self->url_text);
}

static void
action_find (GSimpleAction *action, GVariant *param, gpointer data)
{
  find_show (LY_WINDOW (data), TRUE);
}

static void
action_find_next (GSimpleAction *action, GVariant *param, gpointer data)
{
  LyWindow *self = data;
  LyTab *tab = ly_window_active_tab (self);
  if (tab != NULL)
    ly_tab_find_next (tab);
}

static void
action_find_prev (GSimpleAction *action, GVariant *param, gpointer data)
{
  LyWindow *self = data;
  LyTab *tab = ly_window_active_tab (self);
  if (tab != NULL)
    ly_tab_find_prev (tab);
}

static void
action_escape (GSimpleAction *action, GVariant *param, gpointer data)
{
  LyWindow *self = data;
  if (gtk_revealer_get_reveal_child (GTK_REVEALER (self->find_revealer))) {
    find_show (self, FALSE);
    return;
  }
  LyTab *tab = ly_window_active_tab (self);
  if (tab != NULL && ly_tab_is_loading (tab))
    ly_tab_stop (tab);
}

static void
action_tab_overview (GSimpleAction *action, GVariant *param, gpointer data)
{
  LyWindow *self = data;
  adw_tab_overview_set_open (self->overview,
                             !adw_tab_overview_get_open (self->overview));
}

static void
action_next_tab (GSimpleAction *action, GVariant *param, gpointer data)
{
  LyWindow *self = data;
  adw_tab_view_select_next_page (self->tabs);
}

static void
action_prev_tab (GSimpleAction *action, GVariant *param, gpointer data)
{
  LyWindow *self = data;
  adw_tab_view_select_previous_page (self->tabs);
}


static void
action_print (GSimpleAction *action, GVariant *param, gpointer data)
{
  LyWindow *self = data;
  LyTab *tab = ly_window_active_tab (self);
  if (tab != NULL)
    ly_tab_print (tab);
}

static void
action_fullscreen (GSimpleAction *action, GVariant *param, gpointer data)
{
  GtkWindow *window = GTK_WINDOW (data);
  if (gtk_window_is_fullscreen (window))
    gtk_window_unfullscreen (window);
  else
    gtk_window_fullscreen (window);
}

static void
action_home (GSimpleAction *action, GVariant *param, gpointer data)
{
  LyWindow *self = data;
  LyTab *tab = ly_window_active_tab (self);
  if (tab != NULL)
    ly_tab_load (tab, self->cfg->homepage);
}

static void
action_bookmark (GSimpleAction *action, GVariant *param, gpointer data)
{
  LyWindow *self = data;
  LyTab *tab = ly_window_active_tab (self);
  if (tab == NULL || self->store == NULL)
    return;

  const char *uri = ly_tab_uri (tab);
  if (uri == NULL || *uri == '\0' || ly_uri_is_internal (uri))
    return;

  if (ly_store_is_bookmarked (self->store, uri)) {
    ly_store_remove_bookmark (self->store, uri);
    ly_window_toast (self, "Bookmark removed");
  } else {
    ly_store_add_bookmark (self->store, uri, ly_tab_title (tab));
    ly_window_toast (self, "Bookmarked");
  }
  update_star (self, tab);
  refresh_bookmarks_bar (self);
}

static void
action_bookmarks (GSimpleAction *action, GVariant *param, gpointer data)
{
  present_library (LY_WINDOW (data), TRUE);
}

static void
action_history (GSimpleAction *action, GVariant *param, gpointer data)
{
  present_library (LY_WINDOW (data), FALSE);
}

static void
action_reopen_tab (GSimpleAction *action, GVariant *param, gpointer data)
{
  LyWindow *self = data;
  if (self->closed_tabs->len == 0) {
    ly_window_toast (self, "No recently closed tabs");
    return;
  }

  guint last = self->closed_tabs->len - 1;
  g_autofree char *url = g_strdup (g_ptr_array_index (self->closed_tabs, last));
  g_ptr_array_remove_index (self->closed_tabs, last);
  ly_window_open_tab (self, url, FALSE);
}


/* ---------------------------------------------------------- tab actions */

/* setup-menu hands us the right-clicked page and then NULL again when the
 * menu closes, which is the only way to know which tab a menu item means. */
static void
on_setup_menu (AdwTabView *view, AdwTabPage *page, gpointer data)
{
  LyWindow *self = data;
  self->menu_page = page;
}

static AdwTabPage *
target_page (LyWindow *self)
{
  return self->menu_page ?: adw_tab_view_get_selected_page (self->tabs);
}

static void
action_duplicate_tab (GSimpleAction *action, GVariant *param, gpointer data)
{
  LyWindow *self = data;
  LyTab *tab = tab_from_page (target_page (self));
  if (tab == NULL)
    return;
  ly_window_open_tab (self, ly_tab_uri (tab), FALSE);
}

static void
action_pin_tab (GSimpleAction *action, GVariant *param, gpointer data)
{
  LyWindow *self = data;
  AdwTabPage *page = target_page (self);
  if (page != NULL)
    adw_tab_view_set_page_pinned (self->tabs, page, !adw_tab_page_get_pinned (page));
}

static void
action_mute_tab (GSimpleAction *action, GVariant *param, gpointer data)
{
  LyWindow *self = data;
  LyTab *tab = tab_from_page (target_page (self));
  if (tab == NULL)
    return;
  ly_tab_set_muted (tab, !ly_tab_is_muted (tab));
  sync_tab_page (self, tab);
}

static void
action_close_other_tabs (GSimpleAction *action, GVariant *param, gpointer data)
{
  LyWindow *self = data;
  AdwTabPage *page = target_page (self);
  if (page != NULL)
    adw_tab_view_close_other_pages (self->tabs, page);
}

static void
action_close_tabs_right (GSimpleAction *action, GVariant *param, gpointer data)
{
  LyWindow *self = data;
  AdwTabPage *page = target_page (self);
  if (page != NULL)
    adw_tab_view_close_pages_after (self->tabs, page);
}

/* Ctrl+1..8 pick that tab; Ctrl+9 is always the last one, as everywhere else. */
static void
action_select_tab (GSimpleAction *action, GVariant *param, gpointer data)
{
  LyWindow *self = data;
  int wanted = param ? g_variant_get_int32 (param) : 1;
  int count  = adw_tab_view_get_n_pages (self->tabs);
  if (count == 0)
    return;

  int index = (wanted >= 9) ? count - 1 : CLAMP (wanted - 1, 0, count - 1);
  AdwTabPage *page = adw_tab_view_get_nth_page (self->tabs, index);
  if (page != NULL)
    adw_tab_view_set_selected_page (self->tabs, page);
}

static GMenu *
build_tab_menu (void)
{
  GMenu *menu = g_menu_new ();

  GMenu *first = g_menu_new ();
  g_menu_append (first, "Duplicate",   "win.duplicate-tab");
  g_menu_append (first, "Pin / Unpin", "win.pin-tab");
  g_menu_append (first, "Mute / Unmute", "win.mute-tab");
  g_menu_append_section (menu, NULL, G_MENU_MODEL (first));
  g_object_unref (first);

  GMenu *closing = g_menu_new ();
  g_menu_append (closing, "Close Other Tabs",   "win.close-other-tabs");
  g_menu_append (closing, "Close Tabs to the Right", "win.close-tabs-right");
  g_menu_append (closing, "Close",              "win.close-tab");
  g_menu_append_section (menu, NULL, G_MENU_MODEL (closing));
  g_object_unref (closing);

  return menu;
}


/* ------------------------------------------------------ source and saving */

static void
on_source_fetched (const char *source, const char *uri, gpointer data)
{
  LyWindow *self = data;

  if (source == NULL) {
    ly_window_toast (self, "Could not read the page source");
    return;
  }

  g_autofree char *escaped = g_markup_escape_text (source, -1);
  g_autofree char *shown   = ly_pretty_uri (uri);
  g_autofree char *safe    = g_markup_escape_text (shown, -1);

  g_autofree char *html = g_strdup_printf (
    "<!doctype html><meta charset=utf-8><title>Source of %s</title>"
    "<style>"
    ":root{color-scheme:light dark}"
    "body{margin:0;font:13px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace}"
    "header{position:sticky;top:0;padding:.6rem 1rem;font:600 12px system-ui;"
    "background:Canvas;border-bottom:1px solid color-mix(in srgb,CanvasText 15%%,transparent)}"
    "pre{margin:0;padding:1rem;white-space:pre-wrap;word-break:break-word;"
    "tab-size:2}"
    "</style>"
    "<header>Source of %s</header><pre>%s</pre>",
    safe, safe, escaped);

  LyTab *tab = ly_window_open_tab (self, NULL, FALSE);
  ly_tab_load_html (tab, html, NULL);
}

static void
action_view_source (GSimpleAction *action, GVariant *param, gpointer data)
{
  LyWindow *self = data;
  LyTab *tab = ly_window_active_tab (self);
  if (tab != NULL)
    ly_tab_fetch_source (tab, on_source_fetched, self);
}

static void
action_save_page (GSimpleAction *action, GVariant *param, gpointer data)
{
  LyWindow *self = data;
  LyTab *tab = ly_window_active_tab (self);
  if (tab != NULL)
    ly_tab_save_page (tab);
}

/* Firefox binds page info to Ctrl+I; having it on the keyboard also means the
 * padlock panel is reachable without a mouse. */
static void
action_site_info (GSimpleAction *action, GVariant *param, gpointer data)
{
  LyWindow *self = data;
  if (self->security_button != NULL)
    gtk_menu_button_popup (GTK_MENU_BUTTON (self->security_button));
}

static void
action_caret_browsing (GSimpleAction *action, GVariant *param, gpointer data)
{
  LyWindow *self = data;
  LyTab *tab = ly_window_active_tab (self);
  if (tab == NULL)
    return;

  gboolean on = !ly_tab_caret_browsing (tab);
  ly_tab_set_caret_browsing (tab, on);
  ly_window_toast (self, on ? "Caret browsing on" : "Caret browsing off");
}

static const GActionEntry WINDOW_ACTIONS[] = {
  { "new-tab",           action_new_tab },
  { "close-tab",         action_close_tab },
  { "back",              action_back },
  { "forward",           action_forward },
  { "reload",            action_reload },
  { "reload-hard",       action_reload_hard },
  { "focus-url",         action_focus_url },
  { "find",              action_find },
  { "find-next",         action_find_next },
  { "find-prev",         action_find_prev },
  { "escape",            action_escape },
  { "zoom-in",           action_zoom_in },
  { "zoom-out",          action_zoom_out },
  { "zoom-reset",        action_zoom_reset },
  { "toggle-protection", action_toggle_protection },
  { "tab-overview",      action_tab_overview },
  { "next-tab",          action_next_tab },
  { "prev-tab",          action_prev_tab },
  { "print",             action_print },
  { "fullscreen",        action_fullscreen },
  { "home",              action_home },
  { "bookmark",          action_bookmark },
  { "bookmarks",         action_bookmarks },
  { "history",           action_history },
  { "reopen-tab",        action_reopen_tab },
  { "duplicate-tab",     action_duplicate_tab },
  { "pin-tab",           action_pin_tab },
  { "mute-tab",          action_mute_tab },
  { "close-other-tabs",  action_close_other_tabs },
  { "close-tabs-right",  action_close_tabs_right },
  { "select-tab",        action_select_tab, "i" },
  { "view-source",       action_view_source },
  { "save-page",         action_save_page },
  { "caret-browsing",    action_caret_browsing },
  { "site-info",         action_site_info },
};

/* ------------------------------------------------------------ building */

static GtkWidget *
icon_button (const char *icon_name, const char *action, const char *tooltip)
{
  GtkWidget *button = gtk_button_new_from_icon_name (icon_name);
  gtk_widget_add_css_class (button, "image-button");
  gtk_widget_add_css_class (button, "flat");
  gtk_widget_set_tooltip_text (button, tooltip);
  if (action != NULL)
    gtk_actionable_set_action_name (GTK_ACTIONABLE (button), action);
  return button;
}

static GtkWidget *
build_url_bar (LyWindow *self)
{
  GtkWidget *box = gtk_box_new (GTK_ORIENTATION_HORIZONTAL, 4);
  gtk_widget_add_css_class (box, "lyndon-url");
  gtk_widget_set_hexpand (box, TRUE);
  gtk_widget_set_valign (box, GTK_ALIGN_CENTER);
  gtk_widget_set_size_request (box, 320, -1);
  self->url_box = box;

  /* The padlock is a button: everything a person wants to know about the
   * connection and this site's permissions lives behind it. */
  self->security_button = gtk_menu_button_new ();
  gtk_widget_add_css_class (self->security_button, "lyndon-shield");
  gtk_widget_add_css_class (self->security_button, "flat");
  self->security_icon = gtk_image_new_from_icon_name ("system-search-symbolic");
  gtk_menu_button_set_child (GTK_MENU_BUTTON (self->security_button), self->security_icon);

  GtkWidget *site_popover = gtk_popover_new ();
  gtk_widget_add_css_class (site_popover, "lyndon-panel");
  gtk_menu_button_set_popover (GTK_MENU_BUTTON (self->security_button), site_popover);
  g_signal_connect (site_popover, "show", G_CALLBACK (on_site_panel_show), self);
  gtk_box_append (GTK_BOX (box), self->security_button);

  self->url_text = gtk_text_new ();
  gtk_widget_set_hexpand (self->url_text, TRUE);
  gtk_text_set_placeholder_text (GTK_TEXT (self->url_text),
                                 "Search or enter address");
  g_signal_connect (self->url_text, "activate", G_CALLBACK (on_url_activate), self);
  g_signal_connect (self->url_text, "changed", G_CALLBACK (on_url_text_changed), self);

  GtkEventController *keys = gtk_event_controller_key_new ();
  g_signal_connect (keys, "key-pressed", G_CALLBACK (on_url_key_pressed), self);
  gtk_widget_add_controller (self->url_text, keys);

  GtkEventController *focus = gtk_event_controller_focus_new ();
  g_signal_connect (focus, "enter", G_CALLBACK (on_url_focus_enter), self);
  g_signal_connect (focus, "leave", G_CALLBACK (on_url_focus_leave), self);
  gtk_widget_add_controller (self->url_text, focus);

  gtk_box_append (GTK_BOX (box), self->url_text);

  self->zoom_button = gtk_button_new_with_label ("100%");
  gtk_widget_add_css_class (self->zoom_button, "lyndon-zoom-chip");
  gtk_widget_add_css_class (self->zoom_button, "flat");
  gtk_widget_set_visible (self->zoom_button, FALSE);
  gtk_actionable_set_action_name (GTK_ACTIONABLE (self->zoom_button), "win.zoom-reset");
  gtk_box_append (GTK_BOX (box), self->zoom_button);

  self->star_button = gtk_button_new ();
  gtk_widget_add_css_class (self->star_button, "lyndon-shield");
  gtk_widget_add_css_class (self->star_button, "flat");
  self->star_icon = gtk_image_new_from_icon_name ("non-starred-symbolic");
  gtk_button_set_child (GTK_BUTTON (self->star_button), self->star_icon);
  gtk_actionable_set_action_name (GTK_ACTIONABLE (self->star_button), "win.bookmark");
  gtk_box_append (GTK_BOX (box), self->star_button);

  self->shield_button = gtk_menu_button_new ();
  gtk_widget_add_css_class (self->shield_button, "lyndon-shield");
  gtk_widget_add_css_class (self->shield_button, "flat");
  self->shield_icon = gtk_image_new_from_icon_name ("security-high-symbolic");
  gtk_menu_button_set_child (GTK_MENU_BUTTON (self->shield_button), self->shield_icon);

  GtkWidget *shield_popover = gtk_popover_new ();
  gtk_widget_add_css_class (shield_popover, "lyndon-panel");
  gtk_menu_button_set_popover (GTK_MENU_BUTTON (self->shield_button), shield_popover);
  g_signal_connect (shield_popover, "show", G_CALLBACK (on_shield_popover_show), self);
  gtk_box_append (GTK_BOX (box), self->shield_button);

  /* Non-autohiding: the address bar must keep focus so typing continues to
   * narrow the list instead of the popover swallowing the keystrokes. */
  GtkGesture *paste = gtk_gesture_click_new ();
  gtk_gesture_single_set_button (GTK_GESTURE_SINGLE (paste), GDK_BUTTON_MIDDLE);
  g_signal_connect (paste, "pressed", G_CALLBACK (on_url_middle_click), self);
  gtk_widget_add_controller (box, GTK_EVENT_CONTROLLER (paste));

  self->suggestions = gtk_popover_new ();
  gtk_popover_set_autohide (GTK_POPOVER (self->suggestions), FALSE);
  gtk_popover_set_has_arrow (GTK_POPOVER (self->suggestions), FALSE);
  gtk_popover_set_position (GTK_POPOVER (self->suggestions), GTK_POS_BOTTOM);
  gtk_widget_add_css_class (self->suggestions, "lyndon-panel");
  gtk_widget_set_parent (self->suggestions, box);

  self->suggestion_list = gtk_list_box_new ();
  gtk_list_box_set_selection_mode (GTK_LIST_BOX (self->suggestion_list),
                                   GTK_SELECTION_SINGLE);
  gtk_widget_add_css_class (self->suggestion_list, "navigation-sidebar");
  g_signal_connect (self->suggestion_list, "row-activated",
                    G_CALLBACK (on_suggestion_activated), self);
  gtk_popover_set_child (GTK_POPOVER (self->suggestions), self->suggestion_list);

  return box;
}

static GMenu *
build_main_menu (void)
{
  GMenu *menu = g_menu_new ();

  GMenu *tabs = g_menu_new ();
  g_menu_append (tabs, "New Tab",            "win.new-tab");
  g_menu_append (tabs, "New Window",         "app.new-window");
  g_menu_append (tabs, "New Private Window", "app.private-window");
  g_menu_append (tabs, "Reopen Closed Tab",  "win.reopen-tab");
  g_menu_append_section (menu, NULL, G_MENU_MODEL (tabs));
  g_object_unref (tabs);

  GMenu *library = g_menu_new ();
  g_menu_append (library, "Bookmark This Page", "win.bookmark");
  g_menu_append (library, "Bookmarks",          "win.bookmarks");
  g_menu_append (library, "History",            "win.history");
  g_menu_append_section (menu, NULL, G_MENU_MODEL (library));
  g_object_unref (library);

  GMenu *zoom = g_menu_new ();
  g_menu_append (zoom, "Zoom In",     "win.zoom-in");
  g_menu_append (zoom, "Zoom Out",    "win.zoom-out");
  g_menu_append (zoom, "Actual Size", "win.zoom-reset");
  g_menu_append_section (menu, NULL, G_MENU_MODEL (zoom));
  g_object_unref (zoom);

  GMenu *page = g_menu_new ();
  g_menu_append (page, "Find in Page…", "win.find");
  g_menu_append (page, "Print…",        "win.print");
  g_menu_append (page, "Save Page As…", "win.save-page");
  g_menu_append (page, "View Page Source", "win.view-source");
  g_menu_append (page, "Site Information",  "win.site-info");
  g_menu_append (page, "Full Screen",   "win.fullscreen");
  g_menu_append (page, "Pause Protection on This Site", "win.toggle-protection");
  g_menu_append_section (menu, NULL, G_MENU_MODEL (page));
  g_object_unref (page);

  GMenu *app = g_menu_new ();
  g_menu_append (app, "Clear Cache",          "app.clear-cache");
  g_menu_append (app, "Clear Browsing Data…", "app.clear-data");
  g_menu_append (app, "Preferences",          "app.preferences");
  g_menu_append (app, "Keyboard Shortcuts",   "app.shortcuts");
  g_menu_append (app, "About Lyndon",         "app.about");
  g_menu_append_section (menu, NULL, G_MENU_MODEL (app));
  g_object_unref (app);

  return menu;
}

static GtkWidget *
build_find_bar (LyWindow *self)
{
  GtkWidget *bar = gtk_box_new (GTK_ORIENTATION_HORIZONTAL, 6);
  gtk_widget_add_css_class (bar, "lyndon-findbar");

  self->find_entry = gtk_search_entry_new ();
  gtk_widget_set_hexpand (self->find_entry, TRUE);
  g_signal_connect (self->find_entry, "search-changed", G_CALLBACK (on_find_changed), self);
  g_signal_connect_swapped (self->find_entry, "activate",
                            G_CALLBACK (action_find_next), NULL);
  gtk_box_append (GTK_BOX (bar), self->find_entry);

  self->find_count = gtk_label_new ("");
  gtk_widget_add_css_class (self->find_count, "lyndon-find-count");
  gtk_box_append (GTK_BOX (bar), self->find_count);

  gtk_box_append (GTK_BOX (bar), icon_button ("go-up-symbolic",   "win.find-prev", "Previous match"));
  gtk_box_append (GTK_BOX (bar), icon_button ("go-down-symbolic", "win.find-next", "Next match"));
  gtk_box_append (GTK_BOX (bar), icon_button ("window-close-symbolic", "win.escape", "Close"));

  self->find_revealer = gtk_revealer_new ();
  gtk_revealer_set_child (GTK_REVEALER (self->find_revealer), bar);
  gtk_revealer_set_transition_type (GTK_REVEALER (self->find_revealer),
                                    GTK_REVEALER_TRANSITION_TYPE_SLIDE_UP);
  return self->find_revealer;
}

static void
on_blocker_ready (LyBlocker *blocker, gpointer data)
{
  LyWindow *self = data;
  sync_chrome (self);
}

static void
ly_window_dispose (GObject *object)
{
  LyWindow *self = LY_WINDOW (object);

  if (self->downloads != NULL)
    ly_downloads_set_callbacks (self->downloads, NULL, NULL, NULL);
  self->downloads = NULL;

  if (self->suggest_source != 0) {
    g_source_remove (self->suggest_source);
    self->suggest_source = 0;
  }
  /* The completion popover is parented to the address bar, so it has to be
   * unparented explicitly rather than left to the container. */
  g_clear_pointer (&self->suggestions, gtk_widget_unparent);
  g_clear_pointer (&self->suggestion_rows, g_ptr_array_unref);
  g_clear_pointer (&self->closed_tabs, g_ptr_array_unref);
  g_clear_object (&self->private_session);

  G_OBJECT_CLASS (ly_window_parent_class)->dispose (object);
}

static void
ly_window_class_init (LyWindowClass *klass)
{
  G_OBJECT_CLASS (klass)->dispose = ly_window_dispose;
}

static void
ly_window_init (LyWindow *self)
{
}

/* ---------------------------------------------------------------- public */

static LyWindow *
window_create (LyApp *app, WebKitNetworkSession *session)
{
  LyWindow *self = g_object_new (LY_TYPE_WINDOW,
                                 "application", app,
                                 "default-width", 1180,
                                 "default-height", 760,
                                 "title", session != NULL ? "Lyndon — Private" : "Lyndon",
                                 NULL);

  /* Set before anything builds a tab: tab_context() reads it. */
  self->private_session = session;

  self->app       = app;
  self->cfg       = ly_app_config (app);
  self->engine    = ly_app_engine (app);
  self->blocker   = ly_app_blocker (app);
  self->downloads = ly_app_downloads (app);
  self->store     = ly_app_store (app);
  self->passwords = ly_app_passwords (app);

  self->suggestion_rows = g_ptr_array_new_with_free_func ((GDestroyNotify) ly_store_row_free);
  self->closed_tabs     = g_ptr_array_new_with_free_func (g_free);

  gtk_widget_add_css_class (GTK_WIDGET (self), "lyndon");
  if (session != NULL)
    gtk_widget_add_css_class (GTK_WIDGET (self), "lyndon-private");

  g_action_map_add_action_entries (G_ACTION_MAP (self), WINDOW_ACTIONS,
                                   G_N_ELEMENTS (WINDOW_ACTIONS), self);

  /* -- tabs ---------------------------------------------------------- */
  self->tabs = ADW_TAB_VIEW (adw_tab_view_new ());
  g_signal_connect (self->tabs, "notify::selected-page",
                    G_CALLBACK (on_selected_page_changed), self);
  g_signal_connect (self->tabs, "notify::n-pages",
                    G_CALLBACK (on_pages_changed), self);
  g_signal_connect (self->tabs, "create-window", G_CALLBACK (on_create_window), self);
  g_signal_connect (self->tabs, "close-page", G_CALLBACK (on_close_page), self);
  g_signal_connect (self->tabs, "setup-menu", G_CALLBACK (on_setup_menu), self);
  g_signal_connect (self->tabs, "indicator-activated",
                    G_CALLBACK (on_indicator_activated), self);

  g_autoptr (GMenu) tab_menu = build_tab_menu ();
  adw_tab_view_set_menu_model (self->tabs, G_MENU_MODEL (tab_menu));

  self->tab_bar = ADW_TAB_BAR (adw_tab_bar_new ());
  adw_tab_bar_set_view (self->tab_bar, self->tabs);
  adw_tab_bar_set_autohide (self->tab_bar, !self->cfg->show_tab_bar_single);
  refresh_bookmarks_bar (self);
  if (self->home_button != NULL)
    gtk_widget_set_visible (self->home_button, self->cfg->show_home_button);

  /* -- header -------------------------------------------------------- */
  GtkWidget *header = adw_header_bar_new ();
  /* LOOSE lets the address bar use the space the buttons do not, instead of
   * being sized to match them symmetrically. */
  adw_header_bar_set_centering_policy (ADW_HEADER_BAR (header),
                                       ADW_CENTERING_POLICY_LOOSE);

  GtkWidget *nav = gtk_box_new (GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_add_css_class (nav, "linked");
  self->back    = icon_button ("go-previous-symbolic", "win.back",    "Back");
  self->forward = icon_button ("go-next-symbolic",     "win.forward", "Forward");
  self->reload  = icon_button ("view-refresh-symbolic", "win.reload", "Reload");
  gtk_box_append (GTK_BOX (nav), self->back);
  gtk_box_append (GTK_BOX (nav), self->forward);
  gtk_box_append (GTK_BOX (nav), self->reload);
  adw_header_bar_pack_start (ADW_HEADER_BAR (header), nav);

  adw_header_bar_set_title_widget (ADW_HEADER_BAR (header), build_url_bar (self));

  GtkWidget *menu_button = gtk_menu_button_new ();
  gtk_menu_button_set_icon_name (GTK_MENU_BUTTON (menu_button), "open-menu-symbolic");
  gtk_widget_add_css_class (menu_button, "image-button");
  gtk_widget_set_tooltip_text (menu_button, "Main menu");
  g_autoptr (GMenu) menu_model = build_main_menu ();
  gtk_menu_button_set_menu_model (GTK_MENU_BUTTON (menu_button), G_MENU_MODEL (menu_model));
  adw_header_bar_pack_end (ADW_HEADER_BAR (header), menu_button);

  GtkWidget *overview_button = icon_button ("view-grid-symbolic", "win.tab-overview",
                                            "All tabs");
  adw_header_bar_pack_end (ADW_HEADER_BAR (header), overview_button);

  self->downloads_button = gtk_menu_button_new ();
  gtk_menu_button_set_icon_name (GTK_MENU_BUTTON (self->downloads_button),
                                 "folder-download-symbolic");
  gtk_widget_add_css_class (self->downloads_button, "image-button");
  gtk_widget_set_visible (self->downloads_button, FALSE);
  GtkWidget *downloads_popover = gtk_popover_new ();
  gtk_widget_add_css_class (downloads_popover, "lyndon-panel");
  gtk_menu_button_set_popover (GTK_MENU_BUTTON (self->downloads_button), downloads_popover);
  g_signal_connect (downloads_popover, "show",
                    G_CALLBACK (on_downloads_popover_show), self);
  adw_header_bar_pack_end (ADW_HEADER_BAR (header), self->downloads_button);

  self->home_button = icon_button ("go-home-symbolic", "win.home", "Home");
  gtk_widget_set_visible (self->home_button, self->cfg->show_home_button);
  adw_header_bar_pack_start (ADW_HEADER_BAR (header), self->home_button);

  GtkWidget *new_tab = icon_button ("tab-new-symbolic", "win.new-tab", "New tab");
  adw_header_bar_pack_start (ADW_HEADER_BAR (header), new_tab);

  /* -- progress ------------------------------------------------------ */
  self->progress = gtk_progress_bar_new ();
  gtk_widget_add_css_class (self->progress, "lyndon-progress");
  gtk_widget_set_visible (self->progress, FALSE);
  gtk_widget_set_valign (self->progress, GTK_ALIGN_START);

  /* -- assembly ------------------------------------------------------ */
  self->chrome = gtk_box_new (GTK_ORIENTATION_VERTICAL, 0);
  gtk_widget_add_css_class (self->chrome, "lyndon-chrome");
  gtk_box_append (GTK_BOX (self->chrome), header);

  self->bookmarks_bar = gtk_box_new (GTK_ORIENTATION_HORIZONTAL, 2);
  gtk_widget_add_css_class (self->bookmarks_bar, "lyndon-bookmarks-bar");
  gtk_widget_set_visible (self->bookmarks_bar, FALSE);
  gtk_box_append (GTK_BOX (self->chrome), self->bookmarks_bar);

  gtk_box_append (GTK_BOX (self->chrome), GTK_WIDGET (self->tab_bar));
  gtk_box_append (GTK_BOX (self->chrome), self->progress);

  /* The hovered-link readout floats over the content instead of taking a
   * permanent row of its own. */
  self->status_label = gtk_label_new ("");
  gtk_label_set_ellipsize (GTK_LABEL (self->status_label), PANGO_ELLIPSIZE_MIDDLE);
  gtk_widget_add_css_class (self->status_label, "caption");
  gtk_widget_set_margin_start (self->status_label, 8);
  gtk_widget_set_margin_end (self->status_label, 8);
  gtk_widget_set_margin_top (self->status_label, 2);
  gtk_widget_set_margin_bottom (self->status_label, 2);

  GtkWidget *status_frame = gtk_box_new (GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_widget_add_css_class (status_frame, "osd");
  gtk_widget_add_css_class (status_frame, "lyndon-status");
  gtk_box_append (GTK_BOX (status_frame), self->status_label);

  self->status_revealer = gtk_revealer_new ();
  gtk_revealer_set_child (GTK_REVEALER (self->status_revealer), status_frame);
  gtk_revealer_set_transition_type (GTK_REVEALER (self->status_revealer),
                                    GTK_REVEALER_TRANSITION_TYPE_CROSSFADE);
  gtk_widget_set_halign (self->status_revealer, GTK_ALIGN_START);
  gtk_widget_set_valign (self->status_revealer, GTK_ALIGN_END);
  gtk_widget_set_can_target (self->status_revealer, FALSE);

  GtkWidget *content_overlay = gtk_overlay_new ();
  gtk_overlay_set_child (GTK_OVERLAY (content_overlay), GTK_WIDGET (self->tabs));
  gtk_overlay_add_overlay (GTK_OVERLAY (content_overlay), self->status_revealer);

  self->toolbar = ADW_TOOLBAR_VIEW (adw_toolbar_view_new ());
  adw_toolbar_view_add_top_bar (self->toolbar, self->chrome);
  adw_toolbar_view_set_content (self->toolbar, content_overlay);
  adw_toolbar_view_add_bottom_bar (self->toolbar, build_find_bar (self));
  adw_toolbar_view_set_top_bar_style (self->toolbar, ADW_TOOLBAR_FLAT);

  self->toasts = ADW_TOAST_OVERLAY (adw_toast_overlay_new ());
  adw_toast_overlay_set_child (self->toasts, GTK_WIDGET (self->toolbar));

  self->overview = ADW_TAB_OVERVIEW (adw_tab_overview_new ());
  adw_tab_overview_set_view (self->overview, self->tabs);
  adw_tab_overview_set_child (self->overview, GTK_WIDGET (self->toasts));
  adw_tab_overview_set_enable_new_tab (self->overview, TRUE);
  g_signal_connect (self->overview, "create-tab",
                    G_CALLBACK (on_overview_create_tab), self);

  adw_application_window_set_content (ADW_APPLICATION_WINDOW (self),
                                      GTK_WIDGET (self->overview));

  ly_downloads_set_callbacks (self->downloads, on_downloads_changed, NULL, self);
  ly_blocker_set_ready_callback (self->blocker, on_blocker_ready, self);

  ly_window_refresh (self);
  return self;
}

LyWindow *
ly_window_new (LyApp *app)
{
  return window_create (app, NULL);
}

LyWindow *
ly_window_new_private (LyApp *app)
{
  /* An ephemeral session keeps cookies, cache and storage in memory only, and
   * takes them down with the window. Nothing here is a UI convention — it is
   * the actual storage boundary. */
  WebKitNetworkSession *session = webkit_network_session_new_ephemeral ();
  if (session == NULL)
    return NULL;

  LyConfig *cfg = ly_app_config (app);
  WebKitCookieManager *cookies = webkit_network_session_get_cookie_manager (session);
  webkit_cookie_manager_set_accept_policy (
    cookies, cfg->cookie_policy == LY_COOKIES_ALL
               ? WEBKIT_COOKIE_POLICY_ACCEPT_ALWAYS
               : WEBKIT_COOKIE_POLICY_ACCEPT_NO_THIRD_PARTY);
  webkit_network_session_set_itp_enabled (session, TRUE);
  webkit_network_session_set_persistent_credential_storage_enabled (session, FALSE);

  return window_create (app, session);
}

void
ly_window_collect_session (LyWindow *self, GPtrArray *rows, int window_index)
{
  if (ly_window_is_private (self))
    return;   /* the whole point is that it leaves nothing behind */

  int pages = adw_tab_view_get_n_pages (self->tabs);
  for (int i = 0; i < pages; i++) {
    LyTab *tab = tab_from_page (adw_tab_view_get_nth_page (self->tabs, i));
    if (tab == NULL)
      continue;

    const char *uri = ly_tab_uri (tab);
    if (uri == NULL || *uri == '\0')
      continue;

    LyStoreRow *row = g_new0 (LyStoreRow, 1);
    row->window = window_index;
    row->index  = i;
    row->url    = g_strdup (uri);
    row->title  = g_strdup (ly_tab_title (tab));
    g_ptr_array_add (rows, row);
  }
}

LyTab *
ly_window_restore_tab (LyWindow *self, const char *url, const char *title)
{
  LyTab *tab = ly_window_open_tab (self, url, TRUE);

  /* Show the remembered title straight away so a restored window reads
   * correctly before any of its tabs have finished loading. */
  AdwTabPage *page = adw_tab_view_get_page (self->tabs, GTK_WIDGET (tab));
  if (page != NULL && title != NULL && *title != '\0')
    adw_tab_page_set_title (page, title);

  return tab;
}

void
ly_window_refresh (LyWindow *self)
{
  GtkWidget *widget = GTK_WIDGET (self);

  static const char *FX_CLASSES[] = { "fx-full", "fx-reduced", "fx-off" };
  for (guint i = 0; i < G_N_ELEMENTS (FX_CLASSES); i++)
    gtk_widget_remove_css_class (widget, FX_CLASSES[i]);
  gtk_widget_add_css_class (widget, FX_CLASSES[self->cfg->effects]);

  if (self->cfg->compact_chrome)
    gtk_widget_add_css_class (widget, "lyndon-compact");
  else
    gtk_widget_remove_css_class (widget, "lyndon-compact");

  adw_tab_bar_set_autohide (self->tab_bar, !self->cfg->show_tab_bar_single);
  refresh_bookmarks_bar (self);
  if (self->home_button != NULL)
    gtk_widget_set_visible (self->home_button, self->cfg->show_home_button);

  for (int i = 0; i < adw_tab_view_get_n_pages (self->tabs); i++) {
    LyTab *tab = tab_from_page (adw_tab_view_get_nth_page (self->tabs, i));
    if (tab != NULL) {
      ly_tab_refresh_policy (tab);
      sync_tab_page (self, tab);
    }
  }

  sync_chrome (self);
}
