/* engine.c — see engine.h. */

#include "engine.h"

#include <string.h>

struct _LyEngine {
  LyConfig             *cfg;
  WebKitWebContext     *context;
  WebKitNetworkSession *session;
  WebKitSettings       *settings;
  GPtrArray            *managers;   /* WebKitUserContentManager*, weak */
};

/* A trimmed user agent: still a truthful WebKit/GTK string, but without the
 * build numbers that make one install distinguishable from another. */
#define UA_MINIMAL \
  "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/605.1.15 (KHTML, like Gecko) " \
  "Version/17.0 Safari/605.1.15"

/* ------------------------------------------------------------ dark helper */

gboolean
ly_engine_is_dark (void)
{
  AdwStyleManager *mgr = adw_style_manager_get_default ();
  return adw_style_manager_get_dark (mgr);
}

/* ------------------------------------------------- internal lyndon: pages */

static void
serve_resource (WebKitURISchemeRequest *request, const char *resource, const char *mime)
{
  g_autoptr (GError) error = NULL;
  g_autoptr (GBytes) bytes =
    g_resources_lookup_data (resource, G_RESOURCE_LOOKUP_FLAGS_NONE, &error);

  if (bytes == NULL) {
    webkit_uri_scheme_request_finish_error (request, error);
    return;
  }

  g_autoptr (GInputStream) stream = g_memory_input_stream_new_from_bytes (bytes);
  webkit_uri_scheme_request_finish (request, stream,
                                    (gint64) g_bytes_get_size (bytes), mime);
}

static void
on_lyndon_scheme (WebKitURISchemeRequest *request, gpointer user_data)
{
  const char *path = webkit_uri_scheme_request_get_path (request);

  if (path == NULL || *path == '\0' || g_strcmp0 (path, "start") == 0 ||
      g_strcmp0 (path, "newtab") == 0 || g_strcmp0 (path, "home") == 0) {
    serve_resource (request, "/org/lyndon/Browser/web/start.html", "text/html");
    return;
  }

  g_autoptr (GError) error =
    g_error_new (G_IO_ERROR, G_IO_ERROR_NOT_FOUND, "No such page: lyndon:%s", path);
  webkit_uri_scheme_request_finish_error (request, error);
}

/* --------------------------------------------------------- user scripts */

static char *
build_privacy_script (LyConfig *cfg)
{
  GString *js = g_string_sized_new (2048);

  g_string_append (js,
    "(function(){'use strict';"
    "if(window.__lyndon)return;"
    "Object.defineProperty(window,'__lyndon',{value:1});"
    "var def=function(o,k,v){try{Object.defineProperty(o,k,"
    "{get:function(){return v;},configurable:true});}catch(e){}};");

  if (cfg->gpc) {
    /* Both are legally meaningful signals in some jurisdictions, and both are
     * read off the prototype by the scripts that care. */
    g_string_append (js,
      "def(Navigator.prototype,'globalPrivacyControl',true);"
      "def(Navigator.prototype,'doNotTrack','1');");
  }

  if (cfg->trim_referrer) {
    /* strict-origin keeps the origin for analytics that legitimately need it
     * while dropping the path, which is where the interesting data lives. */
    g_string_append (js,
      "try{var m=document.createElement('meta');m.name='referrer';"
      "m.content='strict-origin';"
      "(document.head||document.documentElement).appendChild(m);}catch(e){}");
  }

  if (cfg->fingerprint_defence) {
    g_string_append (js,
      /* Two of the highest-entropy, lowest-value values a page can read. */
      "def(Navigator.prototype,'hardwareConcurrency',4);"
      "def(Navigator.prototype,'deviceMemory',8);"

      /* Canvas readback noise. The rendering is untouched; only the pixels a
       * script pulls back out are perturbed, by at most one level per channel,
       * which is invisible but destroys a stable fingerprint. */
      "var seed=Math.floor(Math.random()*2147483647)||1;"
      "var rnd=function(){seed=(seed*48271)%2147483647;return seed/2147483647;};"
      "var smudge=function(d){for(var i=0;i<d.length;i+=4){"
      "if(rnd()<0.05){d[i]=d[i]^1;d[i+1]=d[i+1]^1;d[i+2]=d[i+2]^1;}}};"
      "try{"
      "var gid=CanvasRenderingContext2D.prototype.getImageData;"
      "CanvasRenderingContext2D.prototype.getImageData=function(){"
      "var r=gid.apply(this,arguments);smudge(r.data);return r;};"
      "var tdu=HTMLCanvasElement.prototype.toDataURL;"
      "HTMLCanvasElement.prototype.toDataURL=function(){"
      "try{var c=this.getContext('2d');if(c){var w=this.width,h=this.height;"
      "if(w&&h&&w*h<4194304){var im=gid.call(c,0,0,w,h);smudge(im.data);"
      "c.putImageData(im,0,0);}}}catch(e){}"
      "return tdu.apply(this,arguments);};"
      "}catch(e){}"

      /* WebGL vendor strings are the single most identifying pair a GPU
       * exposes; report a generic one. */
      "try{var gp=WebGLRenderingContext.prototype.getParameter;"
      "var mask=function(p){if(p===37445)return'Mozilla';"
      "if(p===37446)return'Mozilla';return null;};"
      "WebGLRenderingContext.prototype.getParameter=function(p){"
      "var m=mask(p);return m!==null?m:gp.call(this,p);};"
      "}catch(e){}");
  }

  g_string_append (js, "})();");
  return g_string_free (js, FALSE);
}

/* Decides, once the page has painted, whether it already looks dark. Only
 * installed in SMART mode. */
static const char *DARK_PROBE_JS =
  "(function(){'use strict';"
  "if(window.__lyndonDark)return;window.__lyndonDark=1;"
  "var lum=function(c){var m=/rgba?\\(([^)]+)\\)/.exec(c||'');if(!m)return null;"
  "var p=m[1].split(',').map(parseFloat);"
  "if(p.length>3&&p[3]===0)return null;"
  "return (0.2126*p[0]+0.7152*p[1]+0.0722*p[2])/255;};"
  "var decide=function(){try{"
  "var de=document.documentElement;if(!de)return;"
  "var l=null;"
  "if(document.body)l=lum(getComputedStyle(document.body).backgroundColor);"
  "if(l===null)l=lum(getComputedStyle(de).backgroundColor);"
  "if(l===null)l=1;"                    /* nothing painted means white */
  "if(l>0.5)de.setAttribute('data-lyndon-dark','invert');"
  "else de.removeAttribute('data-lyndon-dark');"
  "}catch(e){}};"
  "decide();"
  /* Sites that paint their theme from JavaScript need a second look. */
  "setTimeout(decide,250);setTimeout(decide,1200);"
  "})();";

static char *
load_resource_text (const char *path)
{
  g_autoptr (GBytes) bytes =
    g_resources_lookup_data (path, G_RESOURCE_LOOKUP_FLAGS_NONE, NULL);
  if (bytes == NULL)
    return g_strdup ("");

  gsize len = 0;
  const char *data = g_bytes_get_data (bytes, &len);
  return g_strndup (data, len);
}

void
ly_engine_prepare_content_manager (LyEngine *engine, WebKitUserContentManager *ucm)
{
  LyConfig *cfg = engine->cfg;

  webkit_user_content_manager_remove_all_scripts (ucm);
  webkit_user_content_manager_remove_all_style_sheets (ucm);

  if (!g_ptr_array_find (engine->managers, ucm, NULL))
    g_ptr_array_add (engine->managers, ucm);

  /* -- privacy shims, before any page script runs -------------------- */
  if (cfg->gpc || cfg->trim_referrer || cfg->fingerprint_defence) {
    g_autofree char *source = build_privacy_script (cfg);
    WebKitUserScript *script =
      webkit_user_script_new (source,
                              WEBKIT_USER_CONTENT_INJECT_ALL_FRAMES,
                              WEBKIT_USER_SCRIPT_INJECT_AT_DOCUMENT_START,
                              NULL, NULL);
    webkit_user_content_manager_add_script (ucm, script);
    webkit_user_script_unref (script);
  }

  /* -- forced dark mode ---------------------------------------------- */
  if (cfg->force_dark != LY_DARK_OFF) {
    g_autofree char *css = load_resource_text ("/org/lyndon/Browser/web/force-dark.css");

    /* In ALWAYS mode the gate attribute is set for every page up front, so the
     * same stylesheet serves both modes. */
    if (cfg->force_dark == LY_DARK_ALWAYS) {
      static const char *FORCE_JS =
        "(function(){var d=document.documentElement;"
        "if(d)d.setAttribute('data-lyndon-dark','invert');})();";
      WebKitUserScript *script =
        webkit_user_script_new (FORCE_JS,
                                WEBKIT_USER_CONTENT_INJECT_TOP_FRAME,
                                WEBKIT_USER_SCRIPT_INJECT_AT_DOCUMENT_START,
                                NULL, NULL);
      webkit_user_content_manager_add_script (ucm, script);
      webkit_user_script_unref (script);
    } else {
      WebKitUserScript *script =
        webkit_user_script_new (DARK_PROBE_JS,
                                WEBKIT_USER_CONTENT_INJECT_TOP_FRAME,
                                WEBKIT_USER_SCRIPT_INJECT_AT_DOCUMENT_END,
                                NULL, NULL);
      webkit_user_content_manager_add_script (ucm, script);
      webkit_user_script_unref (script);
    }

    WebKitUserStyleSheet *sheet =
      webkit_user_style_sheet_new (css,
                                   WEBKIT_USER_CONTENT_INJECT_TOP_FRAME,
                                   WEBKIT_USER_STYLE_LEVEL_USER,
                                   NULL, NULL);
    webkit_user_content_manager_add_style_sheet (ucm, sheet);
    webkit_user_style_sheet_unref (sheet);
  }
}

/* ------------------------------------------------------------- settings */

static void
apply_settings (LyEngine *engine)
{
  LyConfig       *cfg = engine->cfg;
  WebKitSettings *s   = engine->settings;

  webkit_settings_set_enable_javascript (s, cfg->javascript);
  webkit_settings_set_auto_load_images (s, cfg->images);
  webkit_settings_set_enable_webgl (s, cfg->webgl);
  webkit_settings_set_enable_webrtc (s, cfg->webrtc);
  webkit_settings_set_enable_webaudio (s, cfg->webaudio);
  webkit_settings_set_enable_smooth_scrolling (s, cfg->smooth_scrolling);
  webkit_settings_set_enable_page_cache (s, cfg->page_cache);
  webkit_settings_set_enable_developer_extras (s, cfg->developer_tools);
  webkit_settings_set_minimum_font_size (s, (guint) cfg->minimum_font_size);

  /* Autoplay: requiring a gesture is both a privacy and a battery decision. */
  webkit_settings_set_media_playback_requires_user_gesture (s, !cfg->media_autoplay);
  webkit_settings_set_media_playback_allows_inline (s, TRUE);

  /* Hardening that costs nothing on well-behaved sites. */
  /* Hyperlink auditing (<a ping>) needs no setting: WebKit stopped sending
   * those requests altogether, and the property is deprecated. */
  webkit_settings_set_allow_file_access_from_file_urls (s, FALSE);
  webkit_settings_set_allow_universal_access_from_file_urls (s, FALSE);
  webkit_settings_set_allow_top_navigation_to_data_urls (s, FALSE);
  webkit_settings_set_javascript_can_access_clipboard (s, FALSE);
  webkit_settings_set_javascript_can_open_windows_automatically (s, FALSE);
  webkit_settings_set_allow_modal_dialogs (s, FALSE);
  webkit_settings_set_disable_web_security (s, FALSE);
  webkit_settings_set_enable_encrypted_media (s, cfg->perm[LY_PERM_DRM] == LY_POLICY_ALLOW);
  webkit_settings_set_enable_media_capabilities (s, FALSE);
  webkit_settings_set_enable_mock_capture_devices (s, FALSE);

  switch (cfg->hw_accel) {
    case LY_HW_NEVER:
      webkit_settings_set_hardware_acceleration_policy (
        s, WEBKIT_HARDWARE_ACCELERATION_POLICY_NEVER);
      break;
    case LY_HW_ALWAYS:
    case LY_HW_AUTO:
    default:
      webkit_settings_set_hardware_acceleration_policy (
        s, WEBKIT_HARDWARE_ACCELERATION_POLICY_ALWAYS);
      break;
  }

  switch (cfg->ua_mode) {
    case LY_UA_MINIMAL:
      webkit_settings_set_user_agent (s, UA_MINIMAL);
      break;
    case LY_UA_CUSTOM:
      if (cfg->ua_custom != NULL && *cfg->ua_custom != '\0') {
        webkit_settings_set_user_agent (s, cfg->ua_custom);
        break;
      }
      G_GNUC_FALLTHROUGH;
    case LY_UA_DEFAULT:
    default:
      webkit_settings_set_user_agent_with_application_details (s, "Lyndon", LYNDON_VERSION);
      break;
  }
}

static void
apply_network (LyEngine *engine)
{
  LyConfig             *cfg     = engine->cfg;
  WebKitNetworkSession *session = engine->session;
  WebKitCookieManager  *cookies = webkit_network_session_get_cookie_manager (session);

  WebKitCookieAcceptPolicy policy;
  switch (cfg->cookie_policy) {
    case LY_COOKIES_NONE:  policy = WEBKIT_COOKIE_POLICY_ACCEPT_NEVER; break;
    case LY_COOKIES_ALL:   policy = WEBKIT_COOKIE_POLICY_ACCEPT_ALWAYS; break;
    case LY_COOKIES_NO_THIRD_PARTY:
    default:               policy = WEBKIT_COOKIE_POLICY_ACCEPT_NO_THIRD_PARTY; break;
  }
  webkit_cookie_manager_set_accept_policy (cookies, policy);

  webkit_network_session_set_itp_enabled (session, cfg->itp);
  webkit_network_session_set_persistent_credential_storage_enabled (session,
                                                                    cfg->save_passwords);
  webkit_network_session_set_tls_errors_policy (session, WEBKIT_TLS_ERRORS_POLICY_FAIL);

  WebKitWebsiteDataManager *data = webkit_network_session_get_website_data_manager (session);
  webkit_website_data_manager_set_favicons_enabled (data, TRUE);

  switch (cfg->proxy_mode) {
    case LY_PROXY_NONE:
      webkit_network_session_set_proxy_settings (session,
        WEBKIT_NETWORK_PROXY_MODE_NO_PROXY, NULL);
      break;
    case LY_PROXY_CUSTOM:
      if (cfg->proxy_url != NULL && *cfg->proxy_url != '\0') {
        WebKitNetworkProxySettings *proxy =
          webkit_network_proxy_settings_new (cfg->proxy_url, NULL);
        webkit_network_session_set_proxy_settings (session,
          WEBKIT_NETWORK_PROXY_MODE_CUSTOM, proxy);
        webkit_network_proxy_settings_free (proxy);
        break;
      }
      G_GNUC_FALLTHROUGH;
    case LY_PROXY_SYSTEM:
    default:
      webkit_network_session_set_proxy_settings (session,
        WEBKIT_NETWORK_PROXY_MODE_DEFAULT, NULL);
      break;
  }
}

void
ly_engine_apply (LyEngine *engine)
{
  apply_settings (engine);
  apply_network (engine);

  webkit_web_context_set_spell_checking_enabled (engine->context, engine->cfg->spell_check);

  /* Accept-Language is a real fingerprinting input, so an empty setting means
   * "say nothing extra" rather than "send the whole system locale list". */
  if (engine->cfg->languages != NULL && *engine->cfg->languages != '\0') {
    g_auto (GStrv) languages = g_strsplit_set (engine->cfg->languages, ", ", -1);
    guint n = 0;
    for (guint i = 0; languages[i] != NULL; i++)
      if (*languages[i] != '\0')
        languages[n++] = languages[i];
    languages[n] = NULL;
    webkit_web_context_set_preferred_languages (engine->context,
                                                (const char * const *) languages);
  }

  /* Reduce WebKit's own caching when the user has asked for a lean profile. */
  webkit_web_context_set_cache_model (engine->context,
                                      engine->cfg->page_cache
                                        ? WEBKIT_CACHE_MODEL_WEB_BROWSER
                                        : WEBKIT_CACHE_MODEL_DOCUMENT_VIEWER);

  for (guint i = 0; i < engine->managers->len; i++)
    ly_engine_prepare_content_manager (engine, g_ptr_array_index (engine->managers, i));
}

void
ly_engine_style_web_view (LyEngine *engine, WebKitWebView *view)
{
  /* Painting the view in the theme colour is what stops the white flash
   * between navigations in dark mode. */
  gboolean dark = ly_engine_is_dark () || engine->cfg->force_dark == LY_DARK_ALWAYS;
  GdkRGBA colour = dark ? (GdkRGBA) { 0.067f, 0.067f, 0.078f, 1.0f }
                        : (GdkRGBA) { 1.0f,   1.0f,   1.0f,   1.0f };
  webkit_web_view_set_background_color (view, &colour);
}

/* ----------------------------------------------------------- clear data */

typedef struct {
  LyEngineClearedFn done;
  gpointer          data;
} ClearCall;

static void
on_cleared (GObject *source, GAsyncResult *result, gpointer user_data)
{
  ClearCall *call = user_data;
  g_autoptr (GError) error = NULL;

  if (!webkit_website_data_manager_clear_finish (WEBKIT_WEBSITE_DATA_MANAGER (source),
                                                 result, &error))
    g_warning ("clearing website data failed: %s", error->message);

  if (call->done != NULL)
    call->done (call->data);
  g_free (call);
}

void
ly_engine_clear_data (LyEngine               *engine,
                      WebKitWebsiteDataTypes  types,
                      LyEngineClearedFn       done,
                      gpointer                user_data)
{
  WebKitWebsiteDataManager *data =
    webkit_network_session_get_website_data_manager (engine->session);

  ClearCall *call = g_new0 (ClearCall, 1);
  call->done = done;
  call->data = user_data;

  webkit_website_data_manager_clear (data, types, 0, NULL, on_cleared, call);
}

/* WebKit indexes website data by registrable domain, so a name matches when it
 * is the host itself or a parent of it. */
static gboolean
data_belongs_to_host (const char *name, const char *host)
{
  if (name == NULL || *name == '\0')
    return FALSE;
  if (g_ascii_strcasecmp (name, host) == 0)
    return TRUE;

  size_t nlen = strlen (name), hlen = strlen (host);
  if (hlen > nlen + 1 && host[hlen - nlen - 1] == '.' &&
      g_ascii_strcasecmp (host + hlen - nlen, name) == 0)
    return TRUE;
  return FALSE;
}

static void
on_host_data_removed (GObject *source, GAsyncResult *result, gpointer data)
{
  g_autoptr (GError) error = NULL;
  if (!webkit_website_data_manager_remove_finish (WEBKIT_WEBSITE_DATA_MANAGER (source),
                                                  result, &error))
    g_message ("clearing site data failed: %s", error->message);
}

static void
on_host_data_fetched (GObject *source, GAsyncResult *result, gpointer data)
{
  g_autofree char *host = data;
  WebKitWebsiteDataManager *manager = WEBKIT_WEBSITE_DATA_MANAGER (source);
  g_autoptr (GError) error = NULL;

  GList *all = webkit_website_data_manager_fetch_finish (manager, result, &error);
  if (all == NULL)
    return;

  GList *matching = NULL;
  for (GList *l = all; l != NULL; l = l->next) {
    WebKitWebsiteData *entry = l->data;
    if (data_belongs_to_host (webkit_website_data_get_name (entry), host))
      matching = g_list_prepend (matching, webkit_website_data_ref (entry));
  }

  if (matching != NULL)
    webkit_website_data_manager_remove (manager, WEBKIT_WEBSITE_DATA_ALL, matching,
                                        NULL, on_host_data_removed, NULL);

  g_list_free_full (matching, (GDestroyNotify) webkit_website_data_unref);
  g_list_free_full (all, (GDestroyNotify) webkit_website_data_unref);
}

void
ly_engine_clear_data_for_host (LyEngine *engine, const char *host)
{
  if (host == NULL || *host == '\0')
    return;

  WebKitWebsiteDataManager *manager =
    webkit_network_session_get_website_data_manager (engine->session);

  webkit_website_data_manager_fetch (manager, WEBKIT_WEBSITE_DATA_ALL, NULL,
                                     on_host_data_fetched, g_strdup (host));
}

/* ------------------------------------------------------------ lifecycle */

LyEngine *
ly_engine_new (LyConfig *cfg)
{
  LyEngine *engine = g_new0 (LyEngine, 1);
  engine->cfg      = cfg;
  engine->managers = g_ptr_array_new ();

  g_autofree char *data_dir  = ly_data_dir ();
  g_autofree char *cache_dir = ly_cache_dir ();
  g_mkdir_with_parents (data_dir, 0700);
  g_mkdir_with_parents (cache_dir, 0700);

  engine->session = webkit_network_session_new (data_dir, cache_dir);
  engine->context = webkit_web_context_new ();

  WebKitCookieManager *cookies = webkit_network_session_get_cookie_manager (engine->session);
  g_autofree char *cookie_file = g_build_filename (data_dir, "cookies.sqlite", NULL);
  webkit_cookie_manager_set_persistent_storage (cookies, cookie_file,
                                                WEBKIT_COOKIE_PERSISTENT_STORAGE_SQLITE);

  engine->settings = webkit_settings_new ();

  /* Internal pages get a scheme of their own so they cannot be reached, or
   * scripted, from the open web. */
  webkit_web_context_register_uri_scheme (engine->context, "lyndon",
                                          on_lyndon_scheme, engine, NULL);
  WebKitSecurityManager *security = webkit_web_context_get_security_manager (engine->context);
  webkit_security_manager_register_uri_scheme_as_secure (security, "lyndon");
  webkit_security_manager_register_uri_scheme_as_display_isolated (security, "lyndon");

  ly_engine_apply (engine);
  return engine;
}

void
ly_engine_free (LyEngine *engine)
{
  if (engine == NULL)
    return;
  g_clear_pointer (&engine->managers, g_ptr_array_unref);
  g_clear_object (&engine->settings);
  g_clear_object (&engine->context);
  g_clear_object (&engine->session);
  g_free (engine);
}

WebKitWebContext     *ly_engine_context  (LyEngine *e) { return e->context; }
WebKitNetworkSession *ly_engine_session  (LyEngine *e) { return e->session; }
WebKitSettings       *ly_engine_settings (LyEngine *e) { return e->settings; }
