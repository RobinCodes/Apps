/* tab.c — see tab.h. */

#include "tab.h"

#include <string.h>

struct _LyTab {
  GtkWidget parent_instance;

  LyTabContext ctx;
  LyEngine    *engine;     /* aliases of ctx, for brevity below */
  LyBlocker   *blocker;
  LyConfig    *cfg;
  LyStore     *store;
  LyPasswords *passwords;

  GtkWidget *overlay;
  GtkWidget *permission_bar;
  GtkWidget *permission_label;

  /* password save prompt */
  GtkWidget *password_bar;
  GtkWidget *password_label;
  char      *pending_origin;
  char      *pending_username;
  char      *pending_password;

  WebKitWebView            *view;
  WebKitUserContentManager *ucm;
  WebKitFindController     *finder;

  char     *host;            /* host of the committed document */
  gboolean  protection_on;
  guint     find_matches;
  gboolean  private_mode;
  gboolean  zoom_restored;
  char     *selection;   /* cached page selection, for the context menu */

  LyTabDelegate delegate;
  gpointer      delegate_data;

  WebKitPermissionRequest *pending_permission;
};

G_DEFINE_FINAL_TYPE (LyTab, ly_tab, GTK_TYPE_WIDGET)

static void update_protection (LyTab *tab);

static void
notify_changed (LyTab *tab)
{
  if (tab->delegate.changed != NULL)
    tab->delegate.changed (tab, tab->delegate_data);
}

/* ------------------------------------------------------------ start page */

static void
inject_start_page_stats (LyTab *tab)
{
  const char *scheme_name;
  if (tab->cfg->force_dark != LY_DARK_OFF)
    scheme_name = tab->cfg->force_dark == LY_DARK_ALWAYS ? "Dark (forced)" : "Dark (smart)";
  else
    scheme_name = ly_engine_is_dark () ? "Dark" : "Light";

  const char *cookies;
  switch (tab->cfg->cookie_policy) {
    case LY_COOKIES_NONE: cookies = "Blocked"; break;
    case LY_COOKIES_ALL:  cookies = "Allowed"; break;
    default:              cookies = "1st party"; break;
  }

  guint rules = tab->cfg->block_enabled ? ly_blocker_rule_count (tab->blocker) : 0;

  g_autofree char *js =
    g_strdup_printf ("if(window.lyndonStats)window.lyndonStats("
                     "{rules:%u,cookies:\"%s\",appearance:\"%s\"});",
                     rules, cookies, scheme_name);

  webkit_web_view_evaluate_javascript (tab->view, js, -1, NULL, NULL, NULL, NULL, NULL);
}

/* ----------------------------------------------------------- permissions */

static int
permission_kind (WebKitPermissionRequest *request)
{
  if (WEBKIT_IS_GEOLOCATION_PERMISSION_REQUEST (request))   return LY_PERM_GEOLOCATION;
  if (WEBKIT_IS_NOTIFICATION_PERMISSION_REQUEST (request))  return LY_PERM_NOTIFICATIONS;
  if (WEBKIT_IS_CLIPBOARD_PERMISSION_REQUEST (request))     return LY_PERM_CLIPBOARD;
  if (WEBKIT_IS_POINTER_LOCK_PERMISSION_REQUEST (request))  return LY_PERM_POINTER_LOCK;
  if (WEBKIT_IS_DEVICE_INFO_PERMISSION_REQUEST (request))   return LY_PERM_DEVICE_INFO;
  if (WEBKIT_IS_MEDIA_KEY_SYSTEM_PERMISSION_REQUEST (request)) return LY_PERM_DRM;
  if (WEBKIT_IS_XR_PERMISSION_REQUEST (request))            return LY_PERM_XR;

  if (WEBKIT_IS_USER_MEDIA_PERMISSION_REQUEST (request)) {
    WebKitUserMediaPermissionRequest *media = WEBKIT_USER_MEDIA_PERMISSION_REQUEST (request);
    gboolean audio = FALSE, video = FALSE, display = FALSE;
    g_object_get (media, "is-for-audio-device", &audio,
                         "is-for-video-device", &video,
                         "is-for-display-device", &display, NULL);
    if (display) return LY_PERM_DISPLAY_CAPTURE;
    if (video)   return LY_PERM_CAMERA;
    if (audio)   return LY_PERM_MICROPHONE;
    return LY_PERM_CAMERA;
  }
  return -1;
}

static void
dismiss_permission_bar (LyTab *tab)
{
  gtk_widget_set_visible (tab->permission_bar, FALSE);
  g_clear_object (&tab->pending_permission);
}

static void
on_permission_allow (GtkButton *button, gpointer data)
{
  LyTab *tab = data;
  if (tab->pending_permission != NULL)
    webkit_permission_request_allow (tab->pending_permission);
  dismiss_permission_bar (tab);
}

static void
on_permission_deny (GtkButton *button, gpointer data)
{
  LyTab *tab = data;
  if (tab->pending_permission != NULL)
    webkit_permission_request_deny (tab->pending_permission);
  dismiss_permission_bar (tab);
}

static gboolean
on_permission_request (WebKitWebView *view, WebKitPermissionRequest *request, gpointer data)
{
  LyTab *tab = data;
  int kind = permission_kind (request);

  if (kind < 0)
    return FALSE;   /* unknown request type — let WebKit's default stand */

  LyPolicy policy = tab->cfg->perm[kind];

  /* A per-site answer, if the user gave one, beats the global default. */
  if (tab->store != NULL) {
    g_autofree char *host = ly_tab_host (tab);
    int override = ly_store_site_permission (tab->store, host, kind);
    if (override >= 0)
      policy = (LyPolicy) override;
  }

  if (policy == LY_POLICY_ALLOW) {
    webkit_permission_request_allow (request);
    return TRUE;
  }
  if (policy == LY_POLICY_DENY) {
    webkit_permission_request_deny (request);
    return TRUE;
  }

  /* Ask. One prompt at a time: a page that fires three at once gets the first
   * answered and the rest denied rather than stacking banners. */
  if (tab->pending_permission != NULL) {
    webkit_permission_request_deny (request);
    return TRUE;
  }

  tab->pending_permission = g_object_ref (request);

  g_autofree char *host = ly_tab_host (tab);
  g_autofree char *text =
    g_strdup_printf ("%s wants access to: %s",
                     host ?: "This page", ly_perm_label (kind));
  gtk_label_set_text (GTK_LABEL (tab->permission_label), text);
  gtk_widget_set_visible (tab->permission_bar, TRUE);
  return TRUE;
}


/* ------------------------------------------------------------- passwords */

static void
clear_pending_login (LyTab *tab)
{
  g_clear_pointer (&tab->pending_origin, g_free);
  g_clear_pointer (&tab->pending_username, g_free);
  if (tab->pending_password != NULL) {
    memset (tab->pending_password, 0, strlen (tab->pending_password));
    g_clear_pointer (&tab->pending_password, g_free);
  }
  if (tab->password_bar != NULL)
    gtk_widget_set_visible (tab->password_bar, FALSE);
}

static void
on_password_save (GtkButton *button, gpointer data)
{
  LyTab *tab = data;
  ly_passwords_save (tab->passwords, tab->pending_origin,
                     tab->pending_username, tab->pending_password);
  clear_pending_login (tab);
}

static void
on_password_never (GtkButton *button, gpointer data)
{
  LyTab *tab = data;
  ly_passwords_block (tab->passwords, tab->pending_origin);
  clear_pending_login (tab);
}

static void
on_password_dismiss (GtkButton *button, gpointer data)
{
  clear_pending_login ((LyTab *) data);
}

static GtkWidget *
build_password_bar (LyTab *tab)
{
  GtkWidget *bar = gtk_box_new (GTK_ORIENTATION_HORIZONTAL, 8);
  gtk_widget_add_css_class (bar, "lyndon-permission");
  gtk_widget_add_css_class (bar, "card");
  gtk_widget_set_halign (bar, GTK_ALIGN_CENTER);
  gtk_widget_set_valign (bar, GTK_ALIGN_START);
  gtk_widget_set_visible (bar, FALSE);
  gtk_widget_set_margin_top (bar, 10);

  GtkWidget *icon = gtk_image_new_from_icon_name ("dialog-password-symbolic");
  gtk_widget_set_margin_start (icon, 12);
  gtk_box_append (GTK_BOX (bar), icon);

  tab->password_label = gtk_label_new ("");
  gtk_label_set_ellipsize (GTK_LABEL (tab->password_label), PANGO_ELLIPSIZE_MIDDLE);
  gtk_widget_set_margin_top (tab->password_label, 8);
  gtk_widget_set_margin_bottom (tab->password_label, 8);
  gtk_box_append (GTK_BOX (bar), tab->password_label);

  GtkWidget *never = gtk_button_new_with_label ("Never");
  gtk_widget_add_css_class (never, "flat");
  g_signal_connect (never, "clicked", G_CALLBACK (on_password_never), tab);
  gtk_box_append (GTK_BOX (bar), never);

  GtkWidget *not_now = gtk_button_new_with_label ("Not now");
  gtk_widget_add_css_class (not_now, "flat");
  g_signal_connect (not_now, "clicked", G_CALLBACK (on_password_dismiss), tab);
  gtk_box_append (GTK_BOX (bar), not_now);

  GtkWidget *save = gtk_button_new_with_label ("Save");
  gtk_widget_add_css_class (save, "suggested-action");
  gtk_widget_set_margin_end (save, 8);
  gtk_widget_set_margin_top (save, 6);
  gtk_widget_set_margin_bottom (save, 6);
  g_signal_connect (save, "clicked", G_CALLBACK (on_password_save), tab);
  gtk_box_append (GTK_BOX (bar), save);

  return bar;
}

/* Async work outlives the tab that started it, so every continuation reaches
 * back through a weak reference rather than a raw pointer. */
typedef struct {
  GWeakRef  tab_ref;
  char     *origin;
  char     *username;
  char     *password;
} LoginWork;

static void
login_work_free (LoginWork *work)
{
  g_weak_ref_clear (&work->tab_ref);
  g_free (work->origin);
  g_free (work->username);
  if (work->password != NULL) {
    memset (work->password, 0, strlen (work->password));
    g_free (work->password);
  }
  g_free (work);
}

static void
on_credentials_for_fill (GPtrArray *credentials, gpointer data)
{
  LoginWork *work = data;
  g_autoptr (GObject) object = g_weak_ref_get (&work->tab_ref);

  if (object != NULL && credentials->len > 0) {
    LyTab *tab = LY_TAB (object);
    /* More than one account saved: fill the first and leave switching to the
     * site's own account picker rather than inventing a second UI. */
    LyCredential *credential = g_ptr_array_index (credentials, 0);

    g_autofree char *user = ly_escape_js_string (credential->username);
    g_autofree char *pass = ly_escape_js_string (credential->password);
    g_autofree char *js = g_strdup_printf (
      "if(window.__lyndonFill)window.__lyndonFill(\"%s\",\"%s\");", user, pass);

    webkit_web_view_evaluate_javascript (tab->view, js, -1, NULL, NULL, NULL, NULL, NULL);
  }
  login_work_free (work);
}

static void
on_credentials_for_save (GPtrArray *credentials, gpointer data)
{
  LoginWork *work = data;
  g_autoptr (GObject) object = g_weak_ref_get (&work->tab_ref);

  if (object == NULL) {
    login_work_free (work);
    return;
  }
  LyTab *tab = LY_TAB (object);

  /* Nothing to ask about if this exact pair is already stored. */
  gboolean is_update = FALSE;
  for (guint i = 0; i < credentials->len; i++) {
    LyCredential *credential = g_ptr_array_index (credentials, i);
    if (g_strcmp0 (credential->username, work->username) != 0)
      continue;
    if (g_strcmp0 (credential->password, work->password) == 0) {
      login_work_free (work);
      return;
    }
    is_update = TRUE;
  }

  clear_pending_login (tab);
  tab->pending_origin   = g_strdup (work->origin);
  tab->pending_username = g_strdup (work->username);
  tab->pending_password = g_strdup (work->password);

  const char *who = (work->username && *work->username) ? work->username : "this login";
  g_autofree char *text =
    is_update ? g_strdup_printf ("Update the saved password for %s?", who)
              : g_strdup_printf ("Save the password for %s?", who);

  gtk_label_set_text (GTK_LABEL (tab->password_label), text);
  gtk_widget_set_visible (tab->password_bar, TRUE);

  login_work_free (work);
}

static char *
js_string_property (JSCValue *object, const char *name)
{
  g_autoptr (JSCValue) value = jsc_value_object_get_property (object, name);
  if (value == NULL || jsc_value_is_undefined (value) || jsc_value_is_null (value))
    return NULL;
  return jsc_value_to_string (value);
}

static void
on_password_message (WebKitUserContentManager *ucm, JSCValue *value, gpointer data)
{
  LyTab *tab = data;

  if (value == NULL || !jsc_value_is_object (value))
    return;
  if (!tab->cfg->save_passwords || tab->private_mode)
    return;

  g_autofree char *type = js_string_property (value, "type");
  g_autofree char *origin = js_string_property (value, "origin");
  if (type == NULL || origin == NULL || *origin == '\0' ||
      g_strcmp0 (origin, "null") == 0)
    return;

  if (g_strcmp0 (type, "forms") == 0) {
    if (!tab->cfg->password_autofill)
      return;

    g_autoptr (JSCValue) count = jsc_value_object_get_property (value, "count");
    if (count == NULL || jsc_value_to_int32 (count) <= 0)
      return;

    LoginWork *work = g_new0 (LoginWork, 1);
    g_weak_ref_init (&work->tab_ref, tab);
    work->origin = g_strdup (origin);
    ly_passwords_lookup (tab->passwords, origin, on_credentials_for_fill, work);
    return;
  }

  if (g_strcmp0 (type, "submit") == 0) {
    if (ly_passwords_is_blocked (tab->passwords, origin))
      return;

    g_autofree char *username = js_string_property (value, "username");
    g_autofree char *password = js_string_property (value, "password");
    if (password == NULL || *password == '\0')
      return;

    LoginWork *work = g_new0 (LoginWork, 1);
    g_weak_ref_init (&work->tab_ref, tab);
    work->origin   = g_strdup (origin);
    work->username = g_strdup (username ?: "");
    work->password = g_strdup (password);
    ly_passwords_lookup (tab->passwords, origin, on_credentials_for_save, work);
  }
}


/* ---------------------------------------------------------- context menu */

/* The hit-test result cannot tell us the selected text, and the menu has to be
 * built synchronously, so the selection is mirrored up as it changes. */
static const char *SELECTION_SCRIPT =
"(function(){'use strict';"
"if(window.__lyndonSel)return;"
"Object.defineProperty(window,'__lyndonSel',{value:1});"
"var H=window.webkit&&window.webkit.messageHandlers&&"
"      window.webkit.messageHandlers.lyndonSelection;"
"if(!H)return;"
"var t=null,last='';"
"document.addEventListener('selectionchange',function(){"
" if(t)return;"
" t=setTimeout(function(){t=null;"
"  var s='';try{s=String(window.getSelection()).trim();}catch(e){}"
"  if(s.length>300)s=s.slice(0,300);"
"  if(s===last)return;last=s;"
"  H.postMessage({text:s});},250);},true);"
"})();";

static void
on_selection_message (WebKitUserContentManager *ucm, JSCValue *value, gpointer data)
{
  LyTab *tab = data;
  if (value == NULL || !jsc_value_is_object (value))
    return;

  g_clear_pointer (&tab->selection, g_free);
  tab->selection = js_string_property (value, "text");
}

typedef struct {
  LyTab    *tab;
  char     *uri;
  gboolean  background;
} MenuTarget;

static void
menu_target_free (gpointer data, GClosure *closure)
{
  MenuTarget *target = data;
  g_free (target->uri);
  g_free (target);
}

static void
on_menu_open_uri (GSimpleAction *action, GVariant *param, gpointer data)
{
  MenuTarget *target = data;
  if (target->tab->delegate.open_uri != NULL)
    target->tab->delegate.open_uri (target->tab, target->uri, target->background,
                                    target->tab->delegate_data);
}

static void
on_menu_search (GSimpleAction *action, GVariant *param, gpointer data)
{
  MenuTarget *target = data;
  if (target->tab->delegate.search_for != NULL)
    target->tab->delegate.search_for (target->tab, target->uri,
                                      target->tab->delegate_data);
}

static void
on_menu_view_source (GSimpleAction *action, GVariant *param, gpointer data)
{
  MenuTarget *target = data;
  GtkWidget *widget = GTK_WIDGET (target->tab);
  gtk_widget_activate_action (widget, "win.view-source", NULL);
}

/* Builds one menu item backed by a throwaway action carrying its own data. */
static WebKitContextMenuItem *
menu_item (LyTab *tab, const char *name, const char *label, const char *payload,
           gboolean background, GCallback handler)
{
  MenuTarget *target = g_new0 (MenuTarget, 1);
  target->tab        = tab;
  target->uri        = g_strdup (payload);
  target->background = background;

  GSimpleAction *action = g_simple_action_new (name, NULL);
  g_signal_connect_data (action, "activate", handler, target, menu_target_free, 0);

  WebKitContextMenuItem *item =
    webkit_context_menu_item_new_from_gaction (G_ACTION (action), label, NULL);
  g_object_unref (action);
  return item;
}

static gboolean
on_context_menu (WebKitWebView *view, WebKitContextMenu *menu,
                 WebKitHitTestResult *hit, gpointer data)
{
  LyTab *tab = data;

  if (webkit_hit_test_result_context_is_link (hit)) {
    const char *uri = webkit_hit_test_result_get_link_uri (hit);
    if (uri != NULL) {
      /* WebKit's own "new window" action opens a tab here, so relabel it
       * rather than leaving the menu lying about what it does. */
      webkit_context_menu_prepend (menu, webkit_context_menu_item_new_separator ());
      webkit_context_menu_prepend (menu,
        menu_item (tab, "open-bg", "Open Link in Background Tab", uri, TRUE,
                   G_CALLBACK (on_menu_open_uri)));
      webkit_context_menu_prepend (menu,
        menu_item (tab, "open-fg", "Open Link in New Tab", uri, FALSE,
                   G_CALLBACK (on_menu_open_uri)));
    }
  }

  if (webkit_hit_test_result_context_is_image (hit)) {
    const char *uri = webkit_hit_test_result_get_image_uri (hit);
    if (uri != NULL)
      webkit_context_menu_append (menu,
        menu_item (tab, "open-image", "Open Image in New Tab", uri, FALSE,
                   G_CALLBACK (on_menu_open_uri)));
  }

  if (tab->selection != NULL && *tab->selection != '\0') {
    g_autofree char *shown = g_strdup (tab->selection);
    if (g_utf8_strlen (shown, -1) > 24) {
      char *cut = g_utf8_offset_to_pointer (shown, 24);
      *cut = '\0';
      char *ellipsis = g_strconcat (shown, "…", NULL);
      g_free (shown);
      shown = ellipsis;
    }
    g_autofree char *label = g_strdup_printf ("Search %s for “%s”",
                                              tab->cfg->search_name ?: "the web", shown);
    webkit_context_menu_append (menu, webkit_context_menu_item_new_separator ());
    webkit_context_menu_append (menu,
      menu_item (tab, "search-sel", label, tab->selection, FALSE,
                 G_CALLBACK (on_menu_search)));
  }

  webkit_context_menu_append (menu, webkit_context_menu_item_new_separator ());
  webkit_context_menu_append (menu,
    menu_item (tab, "view-source", "View Page Source", NULL, FALSE,
               G_CALLBACK (on_menu_view_source)));

  return FALSE;   /* show the menu we just amended */
}

/* -------------------------------------------------------- source and save */

typedef struct {
  LyTabSourceFn callback;
  gpointer      user_data;
  char         *uri;
} SourceRequest;

static void
on_source_ready (GObject *source, GAsyncResult *result, gpointer data)
{
  SourceRequest *request = data;
  g_autoptr (GError) error = NULL;
  gsize length = 0;

  guchar *bytes = webkit_web_resource_get_data_finish (WEBKIT_WEB_RESOURCE (source),
                                                       result, &length, &error);
  if (bytes == NULL) {
    request->callback (NULL, request->uri, request->user_data);
  } else {
    g_autofree char *text = g_strndup ((const char *) bytes, length);
    g_free (bytes);
    request->callback (text, request->uri, request->user_data);
  }

  g_free (request->uri);
  g_free (request);
}

void
ly_tab_fetch_source (LyTab *tab, LyTabSourceFn callback, gpointer user_data)
{
  WebKitWebResource *resource = webkit_web_view_get_main_resource (tab->view);
  if (resource == NULL) {
    callback (NULL, ly_tab_uri (tab), user_data);
    return;
  }

  SourceRequest *request = g_new0 (SourceRequest, 1);
  request->callback  = callback;
  request->user_data = user_data;
  request->uri       = g_strdup (ly_tab_uri (tab));

  webkit_web_resource_get_data (resource, NULL, on_source_ready, request);
}

static void
on_page_saved (GObject *source, GAsyncResult *result, gpointer data)
{
  g_autoptr (GError) error = NULL;
  if (!webkit_web_view_save_to_file_finish (WEBKIT_WEB_VIEW (source), result, &error) &&
      !g_error_matches (error, G_IO_ERROR, G_IO_ERROR_CANCELLED))
    g_message ("save page failed: %s", error->message);
}

static void
on_save_location_chosen (GObject *source, GAsyncResult *result, gpointer data)
{
  LyTab *tab = data;
  g_autoptr (GError) error = NULL;
  g_autoptr (GFile) file = gtk_file_dialog_save_finish (GTK_FILE_DIALOG (source),
                                                        result, &error);
  if (file == NULL)
    return;   /* cancelled */

  /* MHTML is the only complete-page format WebKit writes, and it keeps the
   * page in one file instead of a folder of assets. */
  webkit_web_view_save_to_file (tab->view, file, WEBKIT_SAVE_MODE_MHTML,
                                NULL, on_page_saved, NULL);
}

void
ly_tab_save_page (LyTab *tab)
{
  g_autoptr (GtkFileDialog) dialog = gtk_file_dialog_new ();
  gtk_file_dialog_set_title (dialog, "Save Page");

  g_autofree char *host = ly_tab_host (tab);
  g_autofree char *name = g_strdup_printf ("%s.mhtml", host ?: "page");
  for (char *p = name; *p != '\0'; p++)
    if (*p == '/' || *p == '\\')
      *p = '-';
  gtk_file_dialog_set_initial_name (dialog, name);

  GtkRoot *root = gtk_widget_get_root (GTK_WIDGET (tab));
  gtk_file_dialog_save (dialog, GTK_IS_WINDOW (root) ? GTK_WINDOW (root) : NULL,
                        NULL, on_save_location_chosen, tab);
}

void
ly_tab_load_html (LyTab *tab, const char *html, const char *base_uri)
{
  webkit_web_view_load_html (tab->view, html, base_uri);
}

void
ly_tab_set_caret_browsing (LyTab *tab, gboolean enabled)
{
  webkit_settings_set_enable_caret_browsing (webkit_web_view_get_settings (tab->view),
                                             enabled);
}

gboolean
ly_tab_caret_browsing (LyTab *tab)
{
  return webkit_settings_get_enable_caret_browsing (webkit_web_view_get_settings (tab->view));
}

gboolean
ly_tab_tls_info (LyTab *tab, GTlsCertificate **certificate, GTlsCertificateFlags *errors)
{
  return webkit_web_view_get_tls_info (tab->view, certificate, errors);
}

/* ------------------------------------------------------------- policies */

static gboolean
on_decide_policy (WebKitWebView           *view,
                  WebKitPolicyDecision    *decision,
                  WebKitPolicyDecisionType type,
                  gpointer                 data)
{
  LyTab *tab = data;

  switch (type) {
    case WEBKIT_POLICY_DECISION_TYPE_NEW_WINDOW_ACTION: {
      /* target=_blank and window.open both land here. Turn them into tabs. */
      WebKitNavigationPolicyDecision *nav = WEBKIT_NAVIGATION_POLICY_DECISION (decision);
      WebKitNavigationAction *action = webkit_navigation_policy_decision_get_navigation_action (nav);
      WebKitURIRequest *request = webkit_navigation_action_get_request (action);
      const char *uri = webkit_uri_request_get_uri (request);

      if (uri != NULL && tab->delegate.create_view != NULL) {
        WebKitWebView *fresh = tab->delegate.create_view (tab, tab->delegate_data);
        if (fresh != NULL)
          webkit_web_view_load_uri (fresh, uri);
      }
      webkit_policy_decision_ignore (decision);
      return TRUE;
    }

    case WEBKIT_POLICY_DECISION_TYPE_NAVIGATION_ACTION: {
      WebKitNavigationPolicyDecision *nav = WEBKIT_NAVIGATION_POLICY_DECISION (decision);
      WebKitNavigationAction *action = webkit_navigation_policy_decision_get_navigation_action (nav);

      guint button = webkit_navigation_action_get_mouse_button (action);
      guint mods   = webkit_navigation_action_get_modifiers (action);

      /* Middle-click, or ctrl-click, opens in a background tab. */
      if ((button == 2 || (button == 1 && (mods & GDK_CONTROL_MASK))) &&
          tab->delegate.create_view != NULL) {
        WebKitURIRequest *request = webkit_navigation_action_get_request (action);
        const char *uri = webkit_uri_request_get_uri (request);
        if (uri != NULL) {
          WebKitWebView *fresh = tab->delegate.create_view (tab, tab->delegate_data);
          if (fresh != NULL) {
            webkit_web_view_load_uri (fresh, uri);
            webkit_policy_decision_ignore (decision);
            return TRUE;
          }
        }
      }
      break;
    }

    case WEBKIT_POLICY_DECISION_TYPE_RESPONSE: {
      WebKitResponsePolicyDecision *response = WEBKIT_RESPONSE_POLICY_DECISION (decision);
      if (!webkit_response_policy_decision_is_mime_type_supported (response)) {
        webkit_policy_decision_download (decision);
        return TRUE;
      }
      break;
    }

    default:
      break;
  }
  return FALSE;
}

static WebKitWebView *
on_create (WebKitWebView *view, WebKitNavigationAction *action, gpointer data)
{
  LyTab *tab = data;
  if (tab->delegate.create_view == NULL)
    return NULL;
  return tab->delegate.create_view (tab, tab->delegate_data);
}

static void
on_close (WebKitWebView *view, gpointer data)
{
  LyTab *tab = data;
  if (tab->delegate.close != NULL)
    tab->delegate.close (tab, tab->delegate_data);
}

/* ----------------------------------------------------------- load cycle */

static void
update_protection (LyTab *tab)
{
  g_autofree char *host = ly_tab_host (tab);
  gboolean excepted = ly_config_host_excepted (tab->cfg, host);
  tab->protection_on = tab->cfg->block_enabled && !excepted;
  ly_blocker_set_active (tab->blocker, tab->ucm, !excepted);
}

static void
on_load_changed (WebKitWebView *view, WebKitLoadEvent event, gpointer data)
{
  LyTab *tab = data;

  if (event == WEBKIT_LOAD_STARTED) {
    dismiss_permission_bar (tab);
    clear_pending_login (tab);
  } else if (event == WEBKIT_LOAD_COMMITTED) {
    g_free (tab->host);
    tab->host = ly_uri_host (webkit_web_view_get_uri (view));
    update_protection (tab);

    /* Apply the site's remembered zoom before it paints, so the page does not
     * visibly jump a moment after it appears. */
    if (tab->cfg->per_site_zoom && tab->store != NULL && tab->host != NULL) {
      double level = ly_store_zoom_for (tab->store, tab->host, tab->cfg->default_zoom);
      tab->zoom_restored = TRUE;
      webkit_web_view_set_zoom_level (tab->view, level);
    }
  } else if (event == WEBKIT_LOAD_FINISHED) {
    const char *uri = webkit_web_view_get_uri (view);
    if (uri != NULL && g_str_has_prefix (uri, "lyndon:"))
      inject_start_page_stats (tab);

    /* Private tabs leave no trace in history by construction. */
    if (!tab->private_mode && tab->cfg->remember_history && tab->store != NULL)
      ly_store_record_visit (tab->store, uri, webkit_web_view_get_title (view));
  }

  notify_changed (tab);
}

static gboolean
on_load_failed (WebKitWebView *view, WebKitLoadEvent event,
                const char *uri, GError *error, gpointer data)
{
  /* A cancelled load is usually the user hitting stop or a redirect racing
   * ahead; showing an error page for it would be noise. */
  if (g_error_matches (error, WEBKIT_NETWORK_ERROR, WEBKIT_NETWORK_ERROR_CANCELLED) ||
      g_error_matches (error, WEBKIT_POLICY_ERROR,
                       WEBKIT_POLICY_ERROR_FRAME_LOAD_INTERRUPTED_BY_POLICY_CHANGE))
    return FALSE;

  g_autofree char *pretty = ly_pretty_uri (uri);
  g_autofree char *safe_host = g_markup_escape_text (pretty, -1);
  g_autofree char *safe_msg  = g_markup_escape_text (error->message, -1);

  g_autofree char *html = g_strdup_printf (
    "<!doctype html><meta charset=utf-8>"
    "<meta name=viewport content='width=device-width,initial-scale=1'>"
    "<style>"
    ":root{color-scheme:light dark;--fg:#16161a;--muted:#6b6b78;--bg:#fbfbfd}"
    "@media(prefers-color-scheme:dark){:root{--fg:#eceef4;--muted:#9a9aa8;--bg:#111114}}"
    "body{margin:0;height:100vh;display:flex;align-items:center;justify-content:center;"
    "background:var(--bg);color:var(--fg);"
    "font:15px/1.6 system-ui,Cantarell,sans-serif}"
    "main{max-width:30rem;padding:2rem;text-align:center}"
    "h1{font-size:1.15rem;font-weight:600;margin:0 0 .5rem;letter-spacing:-.01em}"
    "p{color:var(--muted);margin:.35rem 0}"
    "code{font-size:.85rem;opacity:.75}"
    "</style>"
    "<main><h1>Cannot reach %s</h1>"
    "<p>%s</p>"
    "<p><code>%s</code></p></main>",
    safe_host, safe_msg, safe_host);

  webkit_web_view_load_alternate_html (view, html, uri, NULL);
  return TRUE;
}

static gboolean
on_web_process_terminated (WebKitWebView *view, WebKitWebProcessTerminationReason reason,
                           gpointer data)
{
  const char *why;
  switch (reason) {
    case WEBKIT_WEB_PROCESS_CRASHED:              why = "The page crashed."; break;
    case WEBKIT_WEB_PROCESS_EXCEEDED_MEMORY_LIMIT: why = "The page ran out of memory."; break;
    default:                                      why = "The page was stopped."; break;
  }

  g_autofree char *html = g_strdup_printf (
    "<!doctype html><meta charset=utf-8><style>"
    ":root{color-scheme:light dark}"
    "body{margin:0;height:100vh;display:flex;align-items:center;justify-content:center;"
    "font:15px/1.6 system-ui,Cantarell,sans-serif}"
    "main{text-align:center;opacity:.7}</style>"
    "<main><p>%s</p><p style='font-size:.85rem'>Reload to try again.</p></main>", why);

  webkit_web_view_load_html (view, html, webkit_web_view_get_uri (view));
  return TRUE;
}

/* WebKit only raises this once the site holds the notification permission, so
 * the policy decision has already happened by the time we get here. */
static gboolean
on_show_notification (WebKitWebView *view, WebKitNotification *notification, gpointer data)
{
  LyTab *tab = data;
  GApplication *application = g_application_get_default ();
  if (application == NULL)
    return FALSE;

  const char *title = webkit_notification_get_title (notification);
  GNotification *desktop = g_notification_new (title ?: "Notification");

  const char *body = webkit_notification_get_body (notification);
  if (body != NULL && *body != '\0')
    g_notification_set_body (desktop, body);

  g_autofree char *host = ly_tab_host (tab);
  if (host != NULL) {
    g_autofree char *label = g_strdup_printf ("%s — %s", host, title ?: "");
    g_notification_set_title (desktop, label);
  }

  g_autofree char *id = g_strdup_printf ("lyndon-web-%" G_GUINT64_FORMAT,
                                         (guint64) webkit_notification_get_id (notification));
  g_application_send_notification (application, id, desktop);
  g_object_unref (desktop);

  return TRUE;   /* handled; WebKit must not draw its own */
}

static void
on_mouse_target_changed (WebKitWebView *view, WebKitHitTestResult *hit,
                         guint modifiers, gpointer data)
{
  LyTab *tab = data;
  if (tab->delegate.status == NULL)
    return;

  const char *uri = NULL;
  if (webkit_hit_test_result_context_is_link (hit))
    uri = webkit_hit_test_result_get_link_uri (hit);

  tab->delegate.status (tab, uri, tab->delegate_data);
}

static void
on_property_changed (GObject *object, GParamSpec *pspec, gpointer data)
{
  LyTab *tab = data;

  if (g_strcmp0 (g_param_spec_get_name (pspec), "title") == 0 &&
      !tab->private_mode && tab->cfg->remember_history && tab->store != NULL)
    ly_store_update_title (tab->store, webkit_web_view_get_uri (tab->view),
                           webkit_web_view_get_title (tab->view));

  notify_changed (tab);
}

static void
on_found (WebKitFindController *finder, guint count, gpointer data)
{
  LyTab *tab = data;
  tab->find_matches = count;
  if (tab->delegate.found != NULL)
    tab->delegate.found (tab, count, tab->delegate_data);
}

static void
on_not_found (WebKitFindController *finder, gpointer data)
{
  LyTab *tab = data;
  tab->find_matches = 0;
  if (tab->delegate.found != NULL)
    tab->delegate.found (tab, 0, tab->delegate_data);
}

/* --------------------------------------------------------------- widget */

static GtkWidget *
build_permission_bar (LyTab *tab)
{
  GtkWidget *bar = gtk_box_new (GTK_ORIENTATION_HORIZONTAL, 8);
  gtk_widget_add_css_class (bar, "lyndon-permission");
  gtk_widget_add_css_class (bar, "card");
  gtk_widget_set_halign (bar, GTK_ALIGN_CENTER);
  gtk_widget_set_valign (bar, GTK_ALIGN_START);
  gtk_widget_set_visible (bar, FALSE);
  gtk_widget_set_margin_top (bar, 10);

  GtkWidget *icon = gtk_image_new_from_icon_name ("dialog-question-symbolic");
  gtk_widget_set_margin_start (icon, 12);
  gtk_box_append (GTK_BOX (bar), icon);

  tab->permission_label = gtk_label_new ("");
  gtk_label_set_ellipsize (GTK_LABEL (tab->permission_label), PANGO_ELLIPSIZE_MIDDLE);
  gtk_widget_set_margin_top (tab->permission_label, 8);
  gtk_widget_set_margin_bottom (tab->permission_label, 8);
  gtk_box_append (GTK_BOX (bar), tab->permission_label);

  GtkWidget *block = gtk_button_new_with_label ("Block");
  gtk_widget_add_css_class (block, "flat");
  g_signal_connect (block, "clicked", G_CALLBACK (on_permission_deny), tab);
  gtk_box_append (GTK_BOX (bar), block);

  GtkWidget *allow = gtk_button_new_with_label ("Allow");
  gtk_widget_add_css_class (allow, "suggested-action");
  gtk_widget_set_margin_end (allow, 8);
  gtk_widget_set_margin_top (allow, 6);
  gtk_widget_set_margin_bottom (allow, 6);
  g_signal_connect (allow, "clicked", G_CALLBACK (on_permission_allow), tab);
  gtk_box_append (GTK_BOX (bar), allow);

  return bar;
}

static void
ly_tab_dispose (GObject *object)
{
  LyTab *tab = LY_TAB (object);

  if (tab->ucm != NULL && tab->blocker != NULL)
    ly_blocker_detach (tab->blocker, tab->ucm);

  g_clear_object (&tab->pending_permission);
  clear_pending_login (tab);
  g_clear_pointer (&tab->selection, g_free);
  g_clear_pointer (&tab->host, g_free);
  g_clear_pointer (&tab->overlay, gtk_widget_unparent);
  g_clear_object (&tab->ucm);

  G_OBJECT_CLASS (ly_tab_parent_class)->dispose (object);
}

static void
ly_tab_class_init (LyTabClass *klass)
{
  G_OBJECT_CLASS (klass)->dispose = ly_tab_dispose;
  gtk_widget_class_set_layout_manager_type (GTK_WIDGET_CLASS (klass), GTK_TYPE_BIN_LAYOUT);
}

static void
ly_tab_init (LyTab *tab)
{
  tab->protection_on = TRUE;
}

/* ---------------------------------------------------------- construction */

/* The engine clears all scripts when it re-applies settings, so the password
 * script has to be re-added on the same path rather than installed once. */
static void
install_scripts (LyTab *tab)
{
  ly_engine_prepare_content_manager (tab->engine, tab->ucm);

  /* Selection mirroring is independent of the password manager: the context
   * menu needs it whether or not logins are being saved. */
  WebKitUserScript *selection =
    webkit_user_script_new (SELECTION_SCRIPT,
                            WEBKIT_USER_CONTENT_INJECT_TOP_FRAME,
                            WEBKIT_USER_SCRIPT_INJECT_AT_DOCUMENT_END,
                            NULL, NULL);
  webkit_user_content_manager_add_script (tab->ucm, selection);
  webkit_user_script_unref (selection);

  if (tab->passwords == NULL || !ly_passwords_available (tab->passwords) ||
      !tab->cfg->save_passwords)
    return;

  WebKitUserScript *script =
    webkit_user_script_new (ly_passwords_user_script (),
                            WEBKIT_USER_CONTENT_INJECT_TOP_FRAME,
                            WEBKIT_USER_SCRIPT_INJECT_AT_DOCUMENT_END,
                            NULL, NULL);
  webkit_user_content_manager_add_script (tab->ucm, script);
  webkit_user_script_unref (script);
}

static void
wire_view (LyTab *tab)
{
  GtkWidget *view_widget = GTK_WIDGET (tab->view);
  gtk_widget_set_hexpand (view_widget, TRUE);
  gtk_widget_set_vexpand (view_widget, TRUE);

  tab->overlay = gtk_overlay_new ();
  gtk_widget_add_css_class (tab->overlay, "lyndon-content");
  gtk_overlay_set_child (GTK_OVERLAY (tab->overlay), view_widget);

  tab->permission_bar = build_permission_bar (tab);
  gtk_overlay_add_overlay (GTK_OVERLAY (tab->overlay), tab->permission_bar);

  tab->password_bar = build_password_bar (tab);
  gtk_overlay_add_overlay (GTK_OVERLAY (tab->overlay), tab->password_bar);

  gtk_widget_set_parent (tab->overlay, GTK_WIDGET (tab));

  ly_engine_style_web_view (tab->engine, tab->view);
  webkit_web_view_set_settings (tab->view, ly_engine_settings (tab->engine));
  webkit_web_view_set_zoom_level (tab->view, tab->cfg->default_zoom);

  install_scripts (tab);
  ly_blocker_attach (tab->blocker, tab->ucm);

  /* Registering the handler twice on one manager fails, so it happens here in
   * construction and never in the refresh path. */
  if (tab->passwords != NULL && ly_passwords_available (tab->passwords)) {
    webkit_user_content_manager_register_script_message_handler (tab->ucm,
                                                                 "lyndonPasswords",
                                                                 NULL);
    g_signal_connect (tab->ucm, "script-message-received::lyndonPasswords",
                      G_CALLBACK (on_password_message), tab);
  }

  g_signal_connect (tab->view, "load-changed",   G_CALLBACK (on_load_changed), tab);
  g_signal_connect (tab->view, "load-failed",    G_CALLBACK (on_load_failed), tab);
  g_signal_connect (tab->view, "decide-policy",  G_CALLBACK (on_decide_policy), tab);
  g_signal_connect (tab->view, "create",         G_CALLBACK (on_create), tab);
  g_signal_connect (tab->view, "close",          G_CALLBACK (on_close), tab);
  g_signal_connect (tab->view, "permission-request",
                    G_CALLBACK (on_permission_request), tab);
  g_signal_connect (tab->view, "web-process-terminated",
                    G_CALLBACK (on_web_process_terminated), tab);
  g_signal_connect (tab->view, "mouse-target-changed",
                    G_CALLBACK (on_mouse_target_changed), tab);
  g_signal_connect (tab->view, "context-menu", G_CALLBACK (on_context_menu), tab);
  g_signal_connect (tab->view, "show-notification",
                    G_CALLBACK (on_show_notification), tab);

  webkit_user_content_manager_register_script_message_handler (tab->ucm,
                                                               "lyndonSelection", NULL);
  g_signal_connect (tab->ucm, "script-message-received::lyndonSelection",
                    G_CALLBACK (on_selection_message), tab);

  const char *watched[] = { "notify::title", "notify::uri", "notify::favicon",
                            "notify::is-loading", "notify::estimated-load-progress",
                            "notify::is-playing-audio", "notify::is-muted" };
  for (guint i = 0; i < G_N_ELEMENTS (watched); i++)
    g_signal_connect (tab->view, watched[i], G_CALLBACK (on_property_changed), tab);

  tab->finder = webkit_web_view_get_find_controller (tab->view);
  g_signal_connect (tab->finder, "found-text",     G_CALLBACK (on_found), tab);
  g_signal_connect (tab->finder, "failed-to-find-text", G_CALLBACK (on_not_found), tab);
}

static void
apply_context (LyTab *tab, const LyTabContext *context)
{
  tab->ctx       = *context;
  tab->engine    = context->engine;
  tab->blocker   = context->blocker;
  tab->cfg       = context->cfg;
  tab->store     = context->store;
  tab->passwords = context->passwords;
  tab->private_mode = context->session != NULL &&
                      webkit_network_session_is_ephemeral (context->session);
}

LyTab *
ly_tab_new (const LyTabContext *context)
{
  LyTab *tab = g_object_new (LY_TYPE_TAB, NULL);
  apply_context (tab, context);

  WebKitNetworkSession *session = context->session ?: ly_engine_session (tab->engine);

  tab->ucm = webkit_user_content_manager_new ();
  tab->view = WEBKIT_WEB_VIEW (
    g_object_new (WEBKIT_TYPE_WEB_VIEW,
                  "web-context",          ly_engine_context (tab->engine),
                  "network-session",      session,
                  "user-content-manager", tab->ucm,
                  "settings",             ly_engine_settings (tab->engine),
                  NULL));

  wire_view (tab);
  return tab;
}

LyTab *
ly_tab_new_for_view (const LyTabContext *context, WebKitWebView *view)
{
  LyTab *tab = g_object_new (LY_TYPE_TAB, NULL);
  apply_context (tab, context);
  tab->view = view;
  tab->ucm  = g_object_ref (webkit_web_view_get_user_content_manager (view));

  /* A view made with related-view inherits its opener's session, so private
   * stays private no matter what the caller passed. */
  WebKitNetworkSession *inherited = webkit_web_view_get_network_session (view);
  if (inherited != NULL)
    tab->private_mode = webkit_network_session_is_ephemeral (inherited);

  wire_view (tab);
  return tab;
}

void
ly_tab_set_delegate (LyTab *tab, const LyTabDelegate *delegate, gpointer data)
{
  tab->delegate      = *delegate;
  tab->delegate_data = data;
}

/* ------------------------------------------------------------ accessors */

WebKitWebView *ly_tab_web_view (LyTab *tab) { return tab->view; }

void
ly_tab_load (LyTab *tab, const char *uri)
{
  if (uri != NULL && *uri != '\0')
    webkit_web_view_load_uri (tab->view, uri);
}

void
ly_tab_load_input (LyTab *tab, const char *text)
{
  g_autofree char *uri = ly_config_resolve_input (tab->cfg, text);
  ly_tab_load (tab, uri);
}

const char *
ly_tab_uri (LyTab *tab)
{
  return webkit_web_view_get_uri (tab->view);
}

const char *
ly_tab_title (LyTab *tab)
{
  const char *title = webkit_web_view_get_title (tab->view);
  if (title != NULL && *title != '\0')
    return title;

  const char *uri = ly_tab_uri (tab);
  if (uri == NULL || *uri == '\0')
    return "New Tab";
  if (g_str_has_prefix (uri, "lyndon:"))
    return "New Tab";
  return uri;
}

GdkTexture *ly_tab_favicon    (LyTab *tab) { return webkit_web_view_get_favicon (tab->view); }
gboolean    ly_tab_is_loading (LyTab *tab) { return webkit_web_view_is_loading (tab->view); }
double      ly_tab_progress   (LyTab *tab) { return webkit_web_view_get_estimated_load_progress (tab->view); }
gboolean    ly_tab_can_go_back    (LyTab *t) { return webkit_web_view_can_go_back (t->view); }
gboolean    ly_tab_can_go_forward (LyTab *t) { return webkit_web_view_can_go_forward (t->view); }

void ly_tab_go_back    (LyTab *t) { webkit_web_view_go_back (t->view); }
void ly_tab_go_forward (LyTab *t) { webkit_web_view_go_forward (t->view); }
void ly_tab_stop       (LyTab *t) { webkit_web_view_stop_loading (t->view); }

void
ly_tab_reload (LyTab *tab, gboolean bypass_cache)
{
  if (bypass_cache)
    webkit_web_view_reload_bypass_cache (tab->view);
  else
    webkit_web_view_reload (tab->view);
}

void
ly_tab_set_zoom (LyTab *tab, double zoom)
{
  double level = CLAMP (zoom, 0.25, 5.0);
  webkit_web_view_set_zoom_level (tab->view, level);

  if (tab->cfg->per_site_zoom && tab->store != NULL && !tab->private_mode) {
    g_autofree char *host = ly_tab_host (tab);
    if (host != NULL)
      ly_store_set_zoom (tab->store, host, level);
  }
}

double ly_tab_zoom (LyTab *tab) { return webkit_web_view_get_zoom_level (tab->view); }

gboolean ly_tab_is_private (LyTab *tab) { return tab->private_mode; }

gboolean
ly_tab_is_playing_audio (LyTab *tab)
{
  return webkit_web_view_is_playing_audio (tab->view);
}

gboolean ly_tab_is_muted  (LyTab *tab)                { return webkit_web_view_get_is_muted (tab->view); }
void     ly_tab_set_muted (LyTab *tab, gboolean muted) { webkit_web_view_set_is_muted (tab->view, muted); }

void
ly_tab_print (LyTab *tab)
{
  WebKitPrintOperation *print = webkit_print_operation_new (tab->view);
  GtkRoot *root = gtk_widget_get_root (GTK_WIDGET (tab));

  /* run_dialog owns the operation from here: it unrefs when the dialog
   * finishes, whether the user prints or cancels. */
  webkit_print_operation_run_dialog (print, GTK_IS_WINDOW (root) ? GTK_WINDOW (root) : NULL);
  g_object_unref (print);
}

char *
ly_tab_host (LyTab *tab)
{
  if (tab->host != NULL)
    return g_strdup (tab->host);
  return ly_uri_host (ly_tab_uri (tab));
}

gboolean
ly_tab_protection_on (LyTab *tab)
{
  return tab->protection_on;
}

void
ly_tab_toggle_protection (LyTab *tab)
{
  g_autofree char *host = ly_tab_host (tab);
  if (host == NULL)
    return;

  gboolean excepted = ly_config_host_excepted (tab->cfg, host);
  ly_config_set_host_except (tab->cfg, host, !excepted);
  ly_config_touch (tab->cfg);

  update_protection (tab);
  ly_tab_reload (tab, FALSE);
  notify_changed (tab);
}

void
ly_tab_refresh_policy (LyTab *tab)
{
  install_scripts (tab);
  ly_engine_style_web_view (tab->engine, tab->view);
  update_protection (tab);
}

/* ----------------------------------------------------------------- find */

void
ly_tab_find (LyTab *tab, const char *text, gboolean backwards)
{
  if (text == NULL || *text == '\0') {
    webkit_find_controller_search_finish (tab->finder);
    return;
  }

  guint32 options = WEBKIT_FIND_OPTIONS_CASE_INSENSITIVE |
                    WEBKIT_FIND_OPTIONS_WRAP_AROUND;
  if (backwards)
    options |= WEBKIT_FIND_OPTIONS_BACKWARDS;

  webkit_find_controller_search (tab->finder, text, options, G_MAXUINT);
  webkit_find_controller_count_matches (tab->finder, text,
                                        WEBKIT_FIND_OPTIONS_CASE_INSENSITIVE, G_MAXUINT);
}

void ly_tab_find_next  (LyTab *t) { webkit_find_controller_search_next (t->finder); }
void ly_tab_find_prev  (LyTab *t) { webkit_find_controller_search_previous (t->finder); }
void ly_tab_find_close (LyTab *t) { webkit_find_controller_search_finish (t->finder); }
