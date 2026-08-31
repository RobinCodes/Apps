/* tab.c — see tab.h.
 *
 * WebView2 is COM, and every asynchronous call takes a handler object rather
 * than a function pointer. In C that means a struct whose first member is a
 * vtable pointer, plus the three IUnknown methods, per callback. The three
 * are identical every time, so HANDLER_IUNKNOWN writes them and each handler
 * below is just its Invoke.
 *
 * The loader DLL is opened by name instead of linked. The import library
 * Microsoft ships is MSVC-format, and GetProcAddress costs one call at
 * startup — which also gives somewhere sensible to say "the runtime is not
 * installed" rather than failing to start with no window and no message.
 */

#include "tab.h"
#include "scheme.h"
#include "ui.h"   /* ly_system_is_dark, for the start page */

#ifdef LY_DEBUG
# include <stdio.h>
# define TRACE(...) do { fprintf (stderr, "[lyndon] " __VA_ARGS__); fputc (10, stderr); fflush (stderr); } while (0)
#else
# define TRACE(...) do { } while (0)
#endif

#include <json-glib/json-glib.h>

#include <shlwapi.h>
#include <stdlib.h>
#include <string.h>

#include "WebView2.h"

/* ------------------------------------------------------------ conversions */

static wchar_t *
to_w (const char *utf8)
{
  if (utf8 == NULL)
    return NULL;
  return (wchar_t *) g_utf8_to_utf16 (utf8, -1, NULL, NULL, NULL);
}

static char *
from_w (const wchar_t *w)
{
  if (w == NULL)
    return NULL;
  return g_utf16_to_utf8 ((const gunichar2 *) w, -1, NULL, NULL, NULL);
}

/* COM hands back strings the caller must free with CoTaskMemFree. */
static char *
take_w (wchar_t *w)
{
  char *s = from_w (w);
  if (w)
    CoTaskMemFree (w);
  return s;
}

/* ------------------------------------------------------- IUnknown, once */

#define HANDLER_IUNKNOWN(Name, Iface)                                         \
  static HRESULT STDMETHODCALLTYPE                                            \
  Name##_QueryInterface (Iface *self, REFIID riid, void **ppv)                \
  {                                                                           \
    if (ppv == NULL)                                                          \
      return E_POINTER;                                                       \
    if (IsEqualIID (riid, &IID_IUnknown) || IsEqualIID (riid, &IID_##Iface)) { \
      *ppv = self;                                                            \
      InterlockedIncrement (&((Name *) self)->ref);                           \
      return S_OK;                                                            \
    }                                                                         \
    *ppv = NULL;                                                              \
    return E_NOINTERFACE;                                                     \
  }                                                                           \
  static ULONG STDMETHODCALLTYPE Name##_AddRef (Iface *self)                  \
  {                                                                           \
    return (ULONG) InterlockedIncrement (&((Name *) self)->ref);              \
  }                                                                           \
  static ULONG STDMETHODCALLTYPE Name##_Release (Iface *self)                 \
  {                                                                           \
    LONG left = InterlockedDecrement (&((Name *) self)->ref);                 \
    if (left == 0)                                                            \
      g_free (self);                                                          \
    return (ULONG) left;                                                      \
  }

/* Declare the struct, the IUnknown three, and the constructor. The Invoke is
 * written by hand under each, because only its signature differs. */
#define HANDLER_HEAD(Name, Iface)                                             \
  typedef struct {                                                            \
    Iface  iface;                                                             \
    LONG   ref;                                                               \
    gpointer data;                                                            \
  } Name;                                                                     \
  static HRESULT STDMETHODCALLTYPE Name##_Invoke                              \
    (Iface *self, HANDLER_ARGS_##Name);                                       \
  HANDLER_IUNKNOWN (Name, Iface)                                              \
  static Iface##Vtbl Name##_vtbl = {                                          \
    Name##_QueryInterface, Name##_AddRef, Name##_Release, Name##_Invoke        \
  };                                                                          \
  static Iface *                                                              \
  Name##_new (gpointer data)                                                  \
  {                                                                           \
    Name *h = g_new0 (Name, 1);                                               \
    h->iface.lpVtbl = &Name##_vtbl;                                           \
    h->ref = 1;                                                               \
    h->data = data;                                                           \
    return (Iface *) h;                                                       \
  }

/* --------------------------------------------------------- shared runtime */

typedef HRESULT (STDMETHODCALLTYPE *CreateEnvFn) (
    PCWSTR browserExecutableFolder, PCWSTR userDataFolder,
    ICoreWebView2EnvironmentOptions *options,
    ICoreWebView2CreateCoreWebView2EnvironmentCompletedHandler *handler);

typedef HRESULT (STDMETHODCALLTYPE *VersionFn) (
    PCWSTR browserExecutableFolder, LPWSTR *versionInfo);

static struct {
  HMODULE      loader;
  CreateEnvFn  create_env;
  VersionFn    version;
  ICoreWebView2Environment *env;
  LyEnvReadyFn ready_fn;
  gpointer     ready_data;
  char        *resources;   /* where data/web and data/rules live */
} rt;

static gboolean
load_loader (void)
{
  if (rt.loader)
    return TRUE;
  /* Next to the .exe first, so a portable copy works; then the PATH, where
   * the runtime installer puts it. */
  rt.loader = LoadLibraryW (L"WebView2Loader.dll");
  if (rt.loader == NULL)
    return FALSE;
  rt.create_env = (CreateEnvFn) (void *) GetProcAddress (
      rt.loader, "CreateCoreWebView2EnvironmentWithOptions");
  rt.version = (VersionFn) (void *) GetProcAddress (
      rt.loader, "GetAvailableCoreWebView2BrowserVersionString");
  return rt.create_env != NULL;
}

char *
ly_webview_runtime_version (void)
{
  if (!load_loader () || rt.version == NULL)
    return NULL;
  LPWSTR v = NULL;
  if (FAILED (rt.version (NULL, &v)) || v == NULL)
    return NULL;
  return take_w (v);
}

gboolean
ly_webview_ready (void)
{
  return rt.env != NULL;
}

/* -- environment created ------------------------------------------------- */

#define HANDLER_ARGS_EnvHandler HRESULT result, ICoreWebView2Environment *env
HANDLER_HEAD (EnvHandler, ICoreWebView2CreateCoreWebView2EnvironmentCompletedHandler)

static HRESULT STDMETHODCALLTYPE
EnvHandler_Invoke (ICoreWebView2CreateCoreWebView2EnvironmentCompletedHandler *self,
                   HRESULT result, ICoreWebView2Environment *env)
{
  if (FAILED (result) || env == NULL) {
    if (rt.ready_fn)
      rt.ready_fn (FALSE, "The WebView2 runtime could not be started.", rt.ready_data);
    return S_OK;
  }
  rt.env = env;
  ICoreWebView2Environment_AddRef (env);
  if (rt.ready_fn)
    rt.ready_fn (TRUE, NULL, rt.ready_data);
  return S_OK;
}

void
ly_webview_init (const char *profile_dir, const char *resource_dir,
                 LyConfig *cfg, LyEnvReadyFn ready, gpointer user_data)
{
  rt.ready_fn = ready;
  rt.ready_data = user_data;
  g_free (rt.resources);
  rt.resources = g_strdup (resource_dir);

  if (!load_loader ()) {
    if (ready)
      ready (FALSE,
             "WebView2Loader.dll was not found.\n\n"
             "Lyndon draws pages with WebView2, which ships with Microsoft "
             "Edge. Install the WebView2 Runtime from Microsoft and start "
             "Lyndon again.",
             user_data);
    return;
  }

  g_autofree wchar_t *dir = to_w (profile_dir);

  /* The options object is what registers the lyndon: scheme, so the same
   * homepage works on both builds. Without it the environment still starts;
   * only the start page would be unreachable. */
  g_autofree char *runtime = ly_webview_runtime_version ();
  ICoreWebView2EnvironmentOptions *options =
    ly_environment_options_new (cfg, runtime);
  TRACE ("options=%p runtime=%s resources=%s", (void *) options,
         runtime ? runtime : "(unknown)", rt.resources);
  HRESULT hr = rt.create_env (NULL, dir, options, EnvHandler_new (NULL));
  TRACE ("CreateCoreWebView2EnvironmentWithOptions -> 0x%08lx", (unsigned long) hr);
  if (FAILED (hr) && options) {
    TRACE ("options refused; retrying without the custom scheme");
    ICoreWebView2EnvironmentOptions_Release (options);
    options = NULL;
    hr = rt.create_env (NULL, dir, NULL, EnvHandler_new (NULL));
  }
  if (options)
    ICoreWebView2EnvironmentOptions_Release (options);

  if (FAILED (hr) && ready)
    ready (FALSE, "WebView2 refused to create a browser environment.", user_data);
}

void
ly_webview_shutdown (void)
{
  if (rt.env) {
    ICoreWebView2Environment_Release (rt.env);
    rt.env = NULL;
  }
}

/* -------------------------------------------------------------- the tab */

struct _LyTab {
  HWND parent;
  LyConfig    *cfg;
  LyBlock     *block;
  LyDownloads *downloads;
  LyPasswords *passwords;
  LyStore     *store;

  ICoreWebView2Controller *controller;
  ICoreWebView2           *view;

  char    *title;
  char    *url;
  char    *pending;        /* asked for before the controller existed */
  gboolean loading;
  gboolean can_back;
  gboolean can_forward;
  gboolean visible;
  gboolean blocking;
  guint    blocked;
  RECT     bounds;

  LyTabChangedFn    changed;
  LyTabNewWindowFn  new_window;
  LyTabAccelFn      accel;
  LyTabLoginFn      login;
  LyTabPermissionFn permission;
  gpointer          cb_data;
};

static void apply_settings (LyTab *tab);
static void inject_password_script (LyTab *tab);
static void inject_start_page_stats (LyTab *tab);

static void
tab_changed (LyTab *tab)
{
  if (tab->changed)
    tab->changed (tab, tab->cb_data);
}

/* -- events -------------------------------------------------------------- */

#define HANDLER_ARGS_NavStart ICoreWebView2 *sender, ICoreWebView2NavigationStartingEventArgs *args
HANDLER_HEAD (NavStart, ICoreWebView2NavigationStartingEventHandler)

static HRESULT STDMETHODCALLTYPE
NavStart_Invoke (ICoreWebView2NavigationStartingEventHandler *self,
                 ICoreWebView2 *sender, ICoreWebView2NavigationStartingEventArgs *args)
{
  LyTab *tab = ((NavStart *) self)->data;
  LPWSTR uri = NULL;
  if (SUCCEEDED (ICoreWebView2NavigationStartingEventArgs_get_Uri (args, &uri))) {
    g_free (tab->url);
    tab->url = take_w (uri);
  }
  tab->loading = TRUE;
  tab->blocked = 0;
  tab_changed (tab);
  return S_OK;
}

#define HANDLER_ARGS_NavDone ICoreWebView2 *sender, ICoreWebView2NavigationCompletedEventArgs *args
HANDLER_HEAD (NavDone, ICoreWebView2NavigationCompletedEventHandler)

static HRESULT STDMETHODCALLTYPE
NavDone_Invoke (ICoreWebView2NavigationCompletedEventHandler *self,
                ICoreWebView2 *sender, ICoreWebView2NavigationCompletedEventArgs *args)
{
  LyTab *tab = ((NavDone *) self)->data;
  tab->loading = FALSE;
  if (tab->url && g_str_has_prefix (tab->url, LY_SCHEME ":"))
    inject_start_page_stats (tab);
  tab_changed (tab);
  return S_OK;
}

#define HANDLER_ARGS_TitleChanged ICoreWebView2 *sender, IUnknown *args
HANDLER_HEAD (TitleChanged, ICoreWebView2DocumentTitleChangedEventHandler)

static HRESULT STDMETHODCALLTYPE
TitleChanged_Invoke (ICoreWebView2DocumentTitleChangedEventHandler *self,
                     ICoreWebView2 *sender, IUnknown *args)
{
  LyTab *tab = ((TitleChanged *) self)->data;
  LPWSTR t = NULL;
  if (SUCCEEDED (ICoreWebView2_get_DocumentTitle (sender, &t))) {
    g_free (tab->title);
    tab->title = take_w (t);
    tab_changed (tab);
  }
  return S_OK;
}

#define HANDLER_ARGS_SourceChanged ICoreWebView2 *sender, ICoreWebView2SourceChangedEventArgs *args
HANDLER_HEAD (SourceChanged, ICoreWebView2SourceChangedEventHandler)

static HRESULT STDMETHODCALLTYPE
SourceChanged_Invoke (ICoreWebView2SourceChangedEventHandler *self,
                      ICoreWebView2 *sender, ICoreWebView2SourceChangedEventArgs *args)
{
  LyTab *tab = ((SourceChanged *) self)->data;
  LPWSTR uri = NULL;
  if (SUCCEEDED (ICoreWebView2_get_Source (sender, &uri))) {
    g_free (tab->url);
    tab->url = take_w (uri);
    tab_changed (tab);
  }
  return S_OK;
}

#define HANDLER_ARGS_HistoryChanged ICoreWebView2 *sender, IUnknown *args
HANDLER_HEAD (HistoryChanged, ICoreWebView2HistoryChangedEventHandler)

static HRESULT STDMETHODCALLTYPE
HistoryChanged_Invoke (ICoreWebView2HistoryChangedEventHandler *self,
                       ICoreWebView2 *sender, IUnknown *args)
{
  LyTab *tab = ((HistoryChanged *) self)->data;
  BOOL b = FALSE, f = FALSE;
  ICoreWebView2_get_CanGoBack (sender, &b);
  ICoreWebView2_get_CanGoForward (sender, &f);
  tab->can_back = !!b;
  tab->can_forward = !!f;
  tab_changed (tab);
  return S_OK;
}

/* -- the pages Lyndon serves itself -------------------------------------- */

/* "lyndon:start" -> <resources>/web/start.html. Only names made of letters
 * are accepted, so nothing can walk out of the directory. */
static char *
internal_page_path (const char *uri)
{
  if (!g_str_has_prefix (uri, LY_SCHEME ":"))
    return NULL;
  const char *name = uri + strlen (LY_SCHEME ":");
  while (*name == '/')
    name++;

  const char *end = strpbrk (name, "?#");
  g_autofree char *bare = end ? g_strndup (name, (gsize) (end - name))
                              : g_strdup (name);
  if (*bare == '\0')
    bare = g_strdup ("start");
  for (const char *p = bare; *p; p++)
    if (!g_ascii_isalnum (*p) && *p != '-')
      return NULL;

  g_autofree char *file = g_strconcat (bare, ".html", NULL);
  return g_build_filename (rt.resources ? rt.resources : "data", "web", file, NULL);
}

/* The response for one of those pages, or NULL if there is no such page. */
static ICoreWebView2WebResourceResponse *
internal_page_response (const char *uri)
{
  if (rt.env == NULL)
    return NULL;
  g_autofree char *path = internal_page_path (uri);
  TRACE ("internal page for %s -> %s", uri, path ? path : "(not ours)");
  if (path == NULL)
    return NULL;

  g_autofree char *body = NULL;
  gsize length = 0;
  ICoreWebView2WebResourceResponse *res = NULL;

  if (!g_file_get_contents (path, &body, &length, NULL)) {
    ICoreWebView2Environment_CreateWebResourceResponse (
        rt.env, NULL, 404, L"Not Found",
        L"Content-Type: text/plain; charset=utf-8", &res);
    return res;
  }

  /* SHCreateMemStream copies, so the buffer can go out of scope. */
  IStream *stream = SHCreateMemStream ((const BYTE *) body, (UINT) length);
  ICoreWebView2Environment_CreateWebResourceResponse (
      rt.env, stream, 200, L"OK",
      L"Content-Type: text/html; charset=utf-8\r\n"
      L"Cache-Control: no-store", &res);
  if (stream)
    IStream_Release (stream);
  return res;
}

/* The three numbers on the start page, filled in exactly as src/tab.c does. */
static void
inject_start_page_stats (LyTab *tab)
{
  if (tab->view == NULL || tab->cfg == NULL)
    return;

  const char *scheme_name;
  if (tab->cfg->force_dark != LY_DARK_OFF)
    scheme_name = tab->cfg->force_dark == LY_DARK_ALWAYS ? "Dark (forced)"
                                                         : "Dark (smart)";
  else
    scheme_name = ly_wants_dark (tab->cfg) ? "Dark" : "Light";

  const char *cookies;
  switch (tab->cfg->cookie_policy) {
    case LY_COOKIES_NONE: cookies = "Blocked"; break;
    case LY_COOKIES_ALL:  cookies = "Allowed"; break;
    default:              cookies = "1st party"; break;
  }

  guint rules = tab->cfg->block_enabled ? ly_block_rule_count (tab->block) : 0;

  g_autofree char *js =
    g_strdup_printf ("if(window.lyndonStats)window.lyndonStats("
                     "{rules:%u,cookies:\"%s\",appearance:\"%s\"});",
                     rules, cookies, scheme_name);
  g_autofree wchar_t *w = to_w (js);
  if (w)
    ICoreWebView2_ExecuteScript (tab->view, w, NULL);
}

/* -- the blocker hook ---------------------------------------------------- */

#define HANDLER_ARGS_ResourceReq ICoreWebView2 *sender, ICoreWebView2WebResourceRequestedEventArgs *args
HANDLER_HEAD (ResourceReq, ICoreWebView2WebResourceRequestedEventHandler)

static HRESULT STDMETHODCALLTYPE
ResourceReq_Invoke (ICoreWebView2WebResourceRequestedEventHandler *self,
                    ICoreWebView2 *sender,
                    ICoreWebView2WebResourceRequestedEventArgs *args)
{
  LyTab *tab = ((ResourceReq *) self)->data;
  if (rt.env == NULL)
    return S_OK;

  ICoreWebView2WebResourceRequest *req = NULL;
  if (FAILED (ICoreWebView2WebResourceRequestedEventArgs_get_Request (args, &req)) || req == NULL)
    return S_OK;

  LPWSTR uri_w = NULL;
  ICoreWebView2WebResourceRequest_get_Uri (req, &uri_w);
  g_autofree char *uri = take_w (uri_w);

  /* Lyndon's own pages are answered from disk before anything else looks at
   * the request. */
  TRACE ("request: %s", uri ? uri : "(null)");
  ICoreWebView2WebResourceResponse *page = internal_page_response (uri);
  if (page) {
    ICoreWebView2WebResourceRequestedEventArgs_put_Response (args, page);
    ICoreWebView2WebResourceResponse_Release (page);
    ICoreWebView2WebResourceRequest_Release (req);
    return S_OK;
  }

  if (!tab->blocking || tab->block == NULL) {
    ICoreWebView2WebResourceRequest_Release (req);
    return S_OK;
  }

  COREWEBVIEW2_WEB_RESOURCE_CONTEXT ctx = COREWEBVIEW2_WEB_RESOURCE_CONTEXT_ALL;
  ICoreWebView2WebResourceRequestedEventArgs_get_ResourceContext (args, &ctx);

  gboolean blocked = ly_block_should_block (tab->block, uri, tab->url,
                                            ly_block_kind_from_context ((int) ctx));
  if (blocked) {
    /* An empty 403 rather than a failed request: the page sees a definite
     * answer immediately and does not sit waiting on a timeout. */
    ICoreWebView2WebResourceResponse *res = NULL;
    if (SUCCEEDED (ICoreWebView2Environment_CreateWebResourceResponse (
            rt.env, NULL, 403, L"Blocked by Lyndon", L"", &res)) && res) {
      ICoreWebView2WebResourceRequestedEventArgs_put_Response (args, res);
      ICoreWebView2WebResourceResponse_Release (res);
    }
    tab->blocked++;
    tab_changed (tab);
  }

  ICoreWebView2WebResourceRequest_Release (req);
  return S_OK;
}

/* -- element hiding ------------------------------------------------------ */

#define HANDLER_ARGS_ContentLoading ICoreWebView2 *sender, ICoreWebView2ContentLoadingEventArgs *args
HANDLER_HEAD (ContentLoading, ICoreWebView2ContentLoadingEventHandler)

static HRESULT STDMETHODCALLTYPE
ContentLoading_Invoke (ICoreWebView2ContentLoadingEventHandler *self,
                       ICoreWebView2 *sender, ICoreWebView2ContentLoadingEventArgs *args)
{
  LyTab *tab = ((ContentLoading *) self)->data;

  inject_password_script (tab);
  ly_tab_apply_config (tab);

  if (!tab->blocking || tab->block == NULL || tab->url == NULL)
    return S_OK;

  g_autofree char *host = ly_uri_host (tab->url);
  if (host == NULL)
    return S_OK;
  g_autofree char *css = ly_block_cosmetic_css (tab->block, host);
  if (css == NULL)
    return S_OK;

  /* A style element rather than insertAdjacentHTML: the rules land before
   * first paint and survive the page rewriting its own body. */
  g_autofree char *escaped = ly_escape_js_string (css);
  g_autofree char *js = g_strdup_printf (
      "(function(){var s=document.createElement('style');"
      "s.textContent=\"%s\";"
      "(document.head||document.documentElement).appendChild(s);})()", escaped);
  g_autofree wchar_t *w = to_w (js);
  ICoreWebView2_ExecuteScript (sender, w, NULL);
  return S_OK;
}

/* The login-form script, injected per navigation rather than registered once
 * with AddScriptToExecuteOnDocumentCreated: it wants a document to look at,
 * and document-created is too early for that. */
static void
inject_password_script (LyTab *tab)
{
  if (tab->view == NULL || tab->passwords == NULL || tab->cfg == NULL)
    return;
  if (!tab->cfg->save_passwords && !tab->cfg->password_autofill)
    return;
  g_autofree wchar_t *w = to_w (ly_passwords_user_script ());
  if (w)
    ICoreWebView2_ExecuteScript (tab->view, w, NULL);
}

/* -- links that want a window -------------------------------------------- */

#define HANDLER_ARGS_NewWindow ICoreWebView2 *sender, ICoreWebView2NewWindowRequestedEventArgs *args
HANDLER_HEAD (NewWindow, ICoreWebView2NewWindowRequestedEventHandler)

static HRESULT STDMETHODCALLTYPE
NewWindow_Invoke (ICoreWebView2NewWindowRequestedEventHandler *self,
                  ICoreWebView2 *sender, ICoreWebView2NewWindowRequestedEventArgs *args)
{
  LyTab *tab = ((NewWindow *) self)->data;
  LPWSTR uri = NULL;
  ICoreWebView2NewWindowRequestedEventArgs_get_Uri (args, &uri);
  g_autofree char *url = take_w (uri);

  /* Handled: WebView2 would otherwise open a bare popup window with no
   * chrome, which is not a tab and cannot be blocked or closed properly. */
  ICoreWebView2NewWindowRequestedEventArgs_put_Handled (args, TRUE);
  if (tab->new_window && url)
    tab->new_window (tab, url, tab->cb_data);
  return S_OK;
}

/* -- downloads ----------------------------------------------------------- */

/* The list entry a download operation belongs to. WebView2 gives each
 * operation its own event source, so the handler carries the item rather
 * than having to look it up. */
typedef struct {
  LyTab          *tab;
  LyDownloadItem *item;
  ICoreWebView2DownloadOperation *op;
} DownloadLink;

static void
download_sync (DownloadLink *link)
{
  if (link->item == NULL || link->op == NULL)
    return;

  /* The panel sets this when the user presses cancel; the engine object is
   * only reachable from here. */
  if (link->item->cancelled && !link->item->finished) {
    ICoreWebView2DownloadOperation_Cancel (link->op);
    return;
  }

  INT64 received = 0;
  ICoreWebView2DownloadOperation_get_BytesReceived (link->op, &received);
  ly_downloads_progress (link->tab->downloads, link->item, (guint64) received);
}

#define HANDLER_ARGS_DlBytes ICoreWebView2DownloadOperation *sender, IUnknown *args
HANDLER_HEAD (DlBytes, ICoreWebView2BytesReceivedChangedEventHandler)

static HRESULT STDMETHODCALLTYPE
DlBytes_Invoke (ICoreWebView2BytesReceivedChangedEventHandler *self,
                ICoreWebView2DownloadOperation *sender, IUnknown *args)
{
  download_sync (((DlBytes *) self)->data);
  return S_OK;
}

#define HANDLER_ARGS_DlState ICoreWebView2DownloadOperation *sender, IUnknown *args
HANDLER_HEAD (DlState, ICoreWebView2StateChangedEventHandler)

static HRESULT STDMETHODCALLTYPE
DlState_Invoke (ICoreWebView2StateChangedEventHandler *self,
                ICoreWebView2DownloadOperation *sender, IUnknown *args)
{
  DownloadLink *link = ((DlState *) self)->data;
  COREWEBVIEW2_DOWNLOAD_STATE state = COREWEBVIEW2_DOWNLOAD_STATE_IN_PROGRESS;
  ICoreWebView2DownloadOperation_get_State (sender, &state);

  if (state == COREWEBVIEW2_DOWNLOAD_STATE_IN_PROGRESS) {
    download_sync (link);
    return S_OK;
  }

  if (state == COREWEBVIEW2_DOWNLOAD_STATE_COMPLETED) {
    download_sync (link);
    ly_downloads_finish (link->tab->downloads, link->item, TRUE, NULL);
  } else {
    COREWEBVIEW2_DOWNLOAD_INTERRUPT_REASON why =
      COREWEBVIEW2_DOWNLOAD_INTERRUPT_REASON_NONE;
    ICoreWebView2DownloadOperation_get_InterruptReason (sender, &why);
    const char *text =
      (why == COREWEBVIEW2_DOWNLOAD_INTERRUPT_REASON_USER_CANCELED)
        ? "Cancelled" : "The download was interrupted.";
    ly_downloads_finish (link->tab->downloads, link->item, FALSE, text);
  }

  if (link->op) {
    ICoreWebView2DownloadOperation_Release (link->op);
    link->op = NULL;
  }
  return S_OK;
}

#define HANDLER_ARGS_DlStart ICoreWebView2 *sender, ICoreWebView2DownloadStartingEventArgs *args
HANDLER_HEAD (DlStart, ICoreWebView2DownloadStartingEventHandler)

static HRESULT STDMETHODCALLTYPE
DlStart_Invoke (ICoreWebView2DownloadStartingEventHandler *self,
                ICoreWebView2 *sender,
                ICoreWebView2DownloadStartingEventArgs *args)
{
  LyTab *tab = ((DlStart *) self)->data;
  if (tab->downloads == NULL)
    return S_OK;

  ICoreWebView2DownloadOperation *op = NULL;
  if (FAILED (ICoreWebView2DownloadStartingEventArgs_get_DownloadOperation (args, &op))
      || op == NULL)
    return S_OK;

  LPWSTR wsuggested = NULL;
  ICoreWebView2DownloadOperation_get_ResultFilePath (op, &wsuggested);
  g_autofree char *suggested = take_w (wsuggested);

  LPWSTR wuri = NULL;
  ICoreWebView2DownloadOperation_get_Uri (op, &wuri);
  g_autofree char *uri = take_w (wuri);

  INT64 total = 0;
  ICoreWebView2DownloadOperation_get_TotalBytesToReceive (op, &total);

  /* Lyndon picks the path, so the configured folder is honoured and an
   * existing file is never quietly overwritten. */
  g_autofree char *path = ly_downloads_target_path (tab->downloads, suggested);
  g_autofree wchar_t *wpath = to_w (path);
  if (wpath)
    ICoreWebView2DownloadStartingEventArgs_put_ResultFilePath (args, wpath);

  /* Handled, so Edge shows no download bubble of its own: the panel is
   * Lyndon's, and two download UIs would be one too many. */
  ICoreWebView2DownloadStartingEventArgs_put_Handled (args, TRUE);

  DownloadLink *link = g_new0 (DownloadLink, 1);
  link->tab = tab;
  link->op = op;
  ICoreWebView2DownloadOperation_AddRef (op);
  link->item = ly_downloads_begin (tab->downloads, uri, path,
                                   (guint64) MAX (total, 0), op);

  EventRegistrationToken tok;
  ICoreWebView2DownloadOperation_add_BytesReceivedChanged (op, DlBytes_new (link), &tok);
  ICoreWebView2DownloadOperation_add_StateChanged (op, DlState_new (link), &tok);

  ICoreWebView2DownloadOperation_Release (op);
  return S_OK;
}

/* -- permissions --------------------------------------------------------- */

static LyPermKind
perm_from_webview2 (int kind)
{
  /* COREWEBVIEW2_PERMISSION_KIND, in its declared order. The two lists do
   * not line up exactly; anything without a counterpart is treated as a
   * device-information request, which is the most conservative of ours. */
  switch (kind) {
    case 1:  return LY_PERM_MICROPHONE;
    case 2:  return LY_PERM_CAMERA;
    case 3:  return LY_PERM_GEOLOCATION;
    case 4:  return LY_PERM_NOTIFICATIONS;
    case 6:  return LY_PERM_CLIPBOARD;
    default: return LY_PERM_DEVICE_INFO;
  }
}

#define HANDLER_ARGS_PermReq ICoreWebView2 *sender, ICoreWebView2PermissionRequestedEventArgs *args
HANDLER_HEAD (PermReq, ICoreWebView2PermissionRequestedEventHandler)

static HRESULT STDMETHODCALLTYPE
PermReq_Invoke (ICoreWebView2PermissionRequestedEventHandler *self,
                ICoreWebView2 *sender,
                ICoreWebView2PermissionRequestedEventArgs *args)
{
  LyTab *tab = ((PermReq *) self)->data;

  COREWEBVIEW2_PERMISSION_KIND kind = COREWEBVIEW2_PERMISSION_KIND_UNKNOWN_PERMISSION;
  ICoreWebView2PermissionRequestedEventArgs_get_PermissionKind (args, &kind);
  LyPermKind mine = perm_from_webview2 ((int) kind);

  LPWSTR wuri = NULL;
  ICoreWebView2PermissionRequestedEventArgs_get_Uri (args, &wuri);
  g_autofree char *uri = take_w (wuri);

  LyPolicy policy = tab->cfg ? tab->cfg->perm[mine] : LY_POLICY_ASK;
  if (tab->permission)
    policy = tab->permission (tab, mine, uri, tab->cb_data);

  if (policy == LY_POLICY_ALLOW)
    ICoreWebView2PermissionRequestedEventArgs_put_State (
        args, COREWEBVIEW2_PERMISSION_STATE_ALLOW);
  else if (policy == LY_POLICY_DENY)
    ICoreWebView2PermissionRequestedEventArgs_put_State (
        args, COREWEBVIEW2_PERMISSION_STATE_DENY);
  /* ASK is left alone, and WebView2 puts up its own prompt. */
  return S_OK;
}

/* -- the password channel ------------------------------------------------ */

/* Fill the form if exactly one login is stored for this origin. Two would be
 * a choice, and a choice needs a prompt rather than a guess. */
static void
autofill_found (GPtrArray *found, gpointer user_data)
{
  LyTab *tab = user_data;
  if (found->len != 1)
    return;
  const LyCredential *c = g_ptr_array_index (found, 0);
  if (c->password)
    ly_tab_fill_login (tab, c->username, c->password);
}

#define HANDLER_ARGS_WebMsg ICoreWebView2 *sender, ICoreWebView2WebMessageReceivedEventArgs *args
HANDLER_HEAD (WebMsg, ICoreWebView2WebMessageReceivedEventHandler)

static HRESULT STDMETHODCALLTYPE
WebMsg_Invoke (ICoreWebView2WebMessageReceivedEventHandler *self,
               ICoreWebView2 *sender,
               ICoreWebView2WebMessageReceivedEventArgs *args)
{
  LyTab *tab = ((WebMsg *) self)->data;

  LPWSTR wjson = NULL;
  if (FAILED (ICoreWebView2WebMessageReceivedEventArgs_TryGetWebMessageAsString (args, &wjson))
      || wjson == NULL)
    return S_OK;
  g_autofree char *json = take_w (wjson);

  g_autoptr (JsonParser) parser = json_parser_new ();
  if (!json_parser_load_from_data (parser, json, -1, NULL))
    return S_OK;

  JsonNode *root = json_parser_get_root (parser);
  if (root == NULL || !JSON_NODE_HOLDS_OBJECT (root))
    return S_OK;
  JsonObject *envelope = json_node_get_object (root);

  /* Every page can post on this one channel, so the envelope has to say what
   * it is. Anything that does not name the password channel is a page
   * talking to itself and is none of our business. */
  if (g_strcmp0 (json_object_get_string_member_with_default (envelope, "channel", ""),
                 "lyndonPasswords") != 0)
    return S_OK;
  if (!json_object_has_member (envelope, "body"))
    return S_OK;

  JsonNode *body_node = json_object_get_member (envelope, "body");
  if (!JSON_NODE_HOLDS_OBJECT (body_node))
    return S_OK;
  JsonObject *body = json_node_get_object (body_node);

  const char *type = json_object_get_string_member_with_default (body, "type", "");
  const char *origin = json_object_get_string_member_with_default (body, "origin", "");

  if (g_strcmp0 (type, "submit") == 0) {
    if (tab->cfg && !tab->cfg->save_passwords)
      return S_OK;
    const char *user = json_object_get_string_member_with_default (body, "username", "");
    const char *pass = json_object_get_string_member_with_default (body, "password", "");
    if (*pass && tab->login)
      tab->login (tab, origin, user, pass, tab->cb_data);
    return S_OK;
  }

  if (g_strcmp0 (type, "forms") == 0) {
    if (tab->passwords == NULL || tab->cfg == NULL || !tab->cfg->password_autofill)
      return S_OK;
    if (json_object_get_int_member_with_default (body, "count", 0) < 1)
      return S_OK;
    ly_passwords_lookup (tab->passwords, origin, autofill_found, tab);
  }
  return S_OK;
}

/* -- keys pressed while the page has the focus --------------------------- */

#define HANDLER_ARGS_AccelKey ICoreWebView2Controller *sender, ICoreWebView2AcceleratorKeyPressedEventArgs *args
HANDLER_HEAD (AccelKey, ICoreWebView2AcceleratorKeyPressedEventHandler)

static HRESULT STDMETHODCALLTYPE
AccelKey_Invoke (ICoreWebView2AcceleratorKeyPressedEventHandler *self,
                 ICoreWebView2Controller *sender,
                 ICoreWebView2AcceleratorKeyPressedEventArgs *args)
{
  LyTab *tab = ((AccelKey *) self)->data;
  if (tab->accel == NULL)
    return S_OK;

  COREWEBVIEW2_KEY_EVENT_KIND kind = COREWEBVIEW2_KEY_EVENT_KIND_KEY_DOWN;
  ICoreWebView2AcceleratorKeyPressedEventArgs_get_KeyEventKind (args, &kind);
  if (kind != COREWEBVIEW2_KEY_EVENT_KIND_KEY_DOWN &&
      kind != COREWEBVIEW2_KEY_EVENT_KIND_SYSTEM_KEY_DOWN)
    return S_OK;

  UINT vkey = 0;
  ICoreWebView2AcceleratorKeyPressedEventArgs_get_VirtualKey (args, &vkey);

  if (tab->accel (tab, vkey, tab->cb_data)) {
    /* Handled means the page never sees it, which is what stops Ctrl+T from
     * also reaching a web app that binds it. */
    ICoreWebView2AcceleratorKeyPressedEventArgs_put_Handled (args, TRUE);
  }
  return S_OK;
}

/* -- settings ------------------------------------------------------------ */

static void
apply_settings (LyTab *tab)
{
  ICoreWebView2Settings *s = NULL;
  if (FAILED (ICoreWebView2_get_Settings (tab->view, &s)) || s == NULL)
    return;

  LyConfig *c = tab->cfg;
  ICoreWebView2Settings_put_IsScriptEnabled (s, c ? !!c->javascript : TRUE);
  ICoreWebView2Settings_put_AreDefaultScriptDialogsEnabled (s, TRUE);
  ICoreWebView2Settings_put_IsWebMessageEnabled (s, TRUE);
  ICoreWebView2Settings_put_AreDevToolsEnabled (s, c ? !!c->developer_tools : FALSE);
  ICoreWebView2Settings_put_AreDefaultContextMenusEnabled (s, TRUE);
  ICoreWebView2Settings_put_IsZoomControlEnabled (s, TRUE);
  ICoreWebView2Settings_put_IsStatusBarEnabled (s, TRUE);
  /* The browser-supplied error pages are more useful than a blank tab. */
  ICoreWebView2Settings_put_IsBuiltInErrorPageEnabled (s, TRUE);

  ICoreWebView2Settings_Release (s);

  /* Pages that honour prefers-color-scheme should follow the browser, not the
   * system, when the two disagree. The profile is on ICoreWebView2_13. */
  ICoreWebView2_13 *v13 = NULL;
  if (SUCCEEDED (ICoreWebView2_QueryInterface (tab->view, &IID_ICoreWebView2_13,
                                               (void **) &v13)) && v13) {
    ICoreWebView2Profile *profile = NULL;
    if (SUCCEEDED (ICoreWebView2_13_get_Profile (v13, &profile)) && profile) {
      COREWEBVIEW2_PREFERRED_COLOR_SCHEME want =
        (c == NULL || c->scheme == LY_SCHEME_SYSTEM)
          ? COREWEBVIEW2_PREFERRED_COLOR_SCHEME_AUTO
          : (c->scheme == LY_SCHEME_DARK
               ? COREWEBVIEW2_PREFERRED_COLOR_SCHEME_DARK
               : COREWEBVIEW2_PREFERRED_COLOR_SCHEME_LIGHT);
      ICoreWebView2Profile_put_PreferredColorScheme (profile, want);
      ICoreWebView2Profile_Release (profile);
    }
    ICoreWebView2_13_Release (v13);
  }
}

/* -- controller created -------------------------------------------------- */

static void
wire_events (LyTab *tab)
{
  EventRegistrationToken tok;
  ICoreWebView2_add_NavigationStarting (tab->view, NavStart_new (tab), &tok);
  ICoreWebView2_add_NavigationCompleted (tab->view, NavDone_new (tab), &tok);
  ICoreWebView2_add_DocumentTitleChanged (tab->view, TitleChanged_new (tab), &tok);
  ICoreWebView2_add_SourceChanged (tab->view, SourceChanged_new (tab), &tok);
  ICoreWebView2_add_HistoryChanged (tab->view, HistoryChanged_new (tab), &tok);
  ICoreWebView2_add_ContentLoading (tab->view, ContentLoading_new (tab), &tok);
  ICoreWebView2_add_NewWindowRequested (tab->view, NewWindow_new (tab), &tok);

  /* Every request, so the blocker sees sub-resources and not just documents.
   * The filter has to be added before the handler will fire. */
  ICoreWebView2_AddWebResourceRequestedFilter (tab->view, L"*",
                                               COREWEBVIEW2_WEB_RESOURCE_CONTEXT_ALL);
  /* "*" does not reach a custom scheme; it has to be named. */
  ICoreWebView2_AddWebResourceRequestedFilter (tab->view, L"" LY_SCHEME ":*",
                                               COREWEBVIEW2_WEB_RESOURCE_CONTEXT_ALL);
  ICoreWebView2_add_WebResourceRequested (tab->view, ResourceReq_new (tab), &tok);

  /* Downloads arrived in ICoreWebView2_4, so it is behind a QueryInterface
   * rather than on the base interface. An older runtime simply does not get
   * Lyndon-managed downloads, which is better than refusing to start. */
  ICoreWebView2_4 *v4 = NULL;
  if (SUCCEEDED (ICoreWebView2_QueryInterface (tab->view, &IID_ICoreWebView2_4,
                                               (void **) &v4)) && v4) {
    ICoreWebView2_4_add_DownloadStarting (v4, DlStart_new (tab), &tok);
    ICoreWebView2_4_Release (v4);
  }
  ICoreWebView2_add_PermissionRequested (tab->view, PermReq_new (tab), &tok);
  ICoreWebView2_add_WebMessageReceived (tab->view, WebMsg_new (tab), &tok);

  /* On the controller, not the view: this one is about the host window. */
  ICoreWebView2Controller_add_AcceleratorKeyPressed (tab->controller,
                                                     AccelKey_new (tab), &tok);
}

#define HANDLER_ARGS_CtrlHandler HRESULT result, ICoreWebView2Controller *controller
HANDLER_HEAD (CtrlHandler, ICoreWebView2CreateCoreWebView2ControllerCompletedHandler)

static HRESULT STDMETHODCALLTYPE
CtrlHandler_Invoke (ICoreWebView2CreateCoreWebView2ControllerCompletedHandler *self,
                    HRESULT result, ICoreWebView2Controller *controller)
{
  LyTab *tab = ((CtrlHandler *) self)->data;
  if (FAILED (result) || controller == NULL)
    return S_OK;

  tab->controller = controller;
  ICoreWebView2Controller_AddRef (controller);
  ICoreWebView2Controller_get_CoreWebView2 (controller, &tab->view);

  apply_settings (tab);
  wire_events (tab);

  ICoreWebView2Controller_put_Bounds (controller, tab->bounds);
  ICoreWebView2Controller_put_IsVisible (controller, tab->visible);

  if (tab->pending) {
    g_autofree wchar_t *w = to_w (tab->pending);
    ICoreWebView2_Navigate (tab->view, w);
    g_clear_pointer (&tab->pending, g_free);
  }
  tab_changed (tab);
  return S_OK;
}

/* ------------------------------------------------------------------- API */

LyTab *
ly_tab_new (HWND parent, LyConfig *cfg, LyBlock *block,
            LyDownloads *downloads, LyPasswords *passwords,
            LyStore *store, const char *url)
{
  LyTab *tab = g_new0 (LyTab, 1);
  tab->parent = parent;
  tab->cfg = cfg;
  tab->block = block;
  tab->downloads = downloads;
  tab->passwords = passwords;
  tab->store = store;
  tab->visible = TRUE;
  tab->blocking = cfg ? !!cfg->block_enabled : TRUE;
  tab->url = g_strdup (url ? url : "");
  tab->pending = g_strdup (url ? url : "about:blank");
  tab->title = g_strdup ("New tab");
  GetClientRect (parent, &tab->bounds);

  if (rt.env)
    ICoreWebView2Environment_CreateCoreWebView2Controller (rt.env, parent,
                                                           CtrlHandler_new (tab));
  return tab;
}

void
ly_tab_free (LyTab *tab)
{
  if (tab == NULL)
    return;
  if (tab->controller) {
    ICoreWebView2Controller_Close (tab->controller);
    ICoreWebView2Controller_Release (tab->controller);
  }
  if (tab->view)
    ICoreWebView2_Release (tab->view);
  g_free (tab->title);
  g_free (tab->url);
  g_free (tab->pending);
  g_free (tab);
}

void
ly_tab_set_callbacks (LyTab *tab, LyTabChangedFn changed,
                      LyTabNewWindowFn new_window, gpointer user_data)
{
  tab->changed = changed;
  tab->new_window = new_window;
  tab->cb_data = user_data;
}

void
ly_tab_set_accelerator_handler (LyTab *tab, LyTabAccelFn accel)
{
  tab->accel = accel;
}

void
ly_tab_set_login_handler (LyTab *tab, LyTabLoginFn login)
{
  tab->login = login;
}

void
ly_tab_set_permission_handler (LyTab *tab, LyTabPermissionFn permission)
{
  tab->permission = permission;
}

void
ly_tab_fill_login (LyTab *tab, const char *username, const char *password)
{
  if (tab->view == NULL || password == NULL)
    return;
  /* __lyndonFill is installed by the injected script and knows how to write
   * through the prototype setter, which is what makes React notice. */
  g_autofree char *u = ly_escape_js_string (username ? username : "");
  g_autofree char *p = ly_escape_js_string (password);
  g_autofree char *js =
    g_strdup_printf ("window.__lyndonFill&&window.__lyndonFill(\"%s\",\"%s\")", u, p);
  g_autofree wchar_t *w = to_w (js);
  if (w)
    ICoreWebView2_ExecuteScript (tab->view, w, NULL);
}

void
ly_tab_apply_config (LyTab *tab)
{
  if (tab->view == NULL)
    return;
  apply_settings (tab);

  /* Zoom: the per-site value if there is one and the option is on, and the
   * configured default otherwise. */
  double zoom = tab->cfg ? tab->cfg->default_zoom : 1.0;
  if (tab->cfg && tab->cfg->per_site_zoom && tab->store && tab->url) {
    g_autofree char *host = ly_uri_host (tab->url);
    if (host)
      zoom = ly_store_zoom_for (tab->store, host, zoom);
  }
  if (zoom > 0.1 && tab->controller)
    ICoreWebView2Controller_put_ZoomFactor (tab->controller, zoom);
}

void
ly_tab_navigate (LyTab *tab, const char *url)
{
  if (tab == NULL || url == NULL || *url == '\0')
    return;
  if (tab->view == NULL) {
    g_free (tab->pending);
    tab->pending = g_strdup (url);
    return;
  }
  g_autofree wchar_t *w = to_w (url);
  ICoreWebView2_Navigate (tab->view, w);
}

void ly_tab_back    (LyTab *t) { if (t->view) ICoreWebView2_GoBack (t->view); }
void ly_tab_forward (LyTab *t) { if (t->view) ICoreWebView2_GoForward (t->view); }
void ly_tab_reload  (LyTab *t) { if (t->view) ICoreWebView2_Reload (t->view); }
void ly_tab_stop    (LyTab *t) { if (t->view) ICoreWebView2_Stop (t->view); }

void
ly_tab_focus (LyTab *tab)
{
  if (tab->controller)
    ICoreWebView2Controller_MoveFocus (
        tab->controller, COREWEBVIEW2_MOVE_FOCUS_REASON_PROGRAMMATIC);
}

void
ly_tab_set_bounds (LyTab *tab, RECT bounds)
{
  tab->bounds = bounds;
  if (tab->controller)
    ICoreWebView2Controller_put_Bounds (tab->controller, bounds);
}

void
ly_tab_set_visible (LyTab *tab, gboolean visible)
{
  tab->visible = visible;
  if (tab->controller)
    ICoreWebView2Controller_put_IsVisible (tab->controller, visible);
}

void
ly_tab_set_zoom (LyTab *tab, double zoom)
{
  if (tab->controller)
    ICoreWebView2Controller_put_ZoomFactor (tab->controller, zoom);
}

double
ly_tab_zoom (LyTab *tab)
{
  double z = 1.0;
  if (tab->controller)
    ICoreWebView2Controller_get_ZoomFactor (tab->controller, &z);
  return z;
}

const char *ly_tab_title (LyTab *t) { return t->title ? t->title : ""; }
const char *ly_tab_url   (LyTab *t) { return t->url ? t->url : ""; }
gboolean ly_tab_loading  (LyTab *t) { return t->loading; }
gboolean ly_tab_can_back (LyTab *t) { return t->can_back; }
gboolean ly_tab_can_forward (LyTab *t) { return t->can_forward; }
guint    ly_tab_blocked  (LyTab *t) { return t->blocked; }

gboolean
ly_tab_secure (LyTab *tab)
{
  return tab->url && ly_uri_is_secure (tab->url);
}

void
ly_tab_set_blocking (LyTab *tab, gboolean enabled)
{
  tab->blocking = enabled;
  tab_changed (tab);
}

gboolean
ly_tab_blocking (LyTab *tab)
{
  return tab->blocking;
}
