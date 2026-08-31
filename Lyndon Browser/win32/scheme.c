/* scheme.c — see scheme.h. */

#include "scheme.h"

#include <string.h>

#include "WebView2.h"

/* COM in C, with four interfaces on one object. None of
 * ICoreWebView2EnvironmentOptions2/3/4 derives from the first — they all
 * derive from IUnknown — so the object carries a vtable pointer per interface
 * and each method walks back to the object by subtracting its own offset. */

typedef struct {
  ICoreWebView2CustomSchemeRegistration iface;
  LONG     ref;
  wchar_t *name;
  BOOL     secure;
  BOOL     authority;
} Registration;

typedef struct {
  ICoreWebView2EnvironmentOptions  v1;
  ICoreWebView2EnvironmentOptions2 v2;
  ICoreWebView2EnvironmentOptions3 v3;
  ICoreWebView2EnvironmentOptions4 v4;
  LONG ref;

  wchar_t *browser_arguments;
  wchar_t *language;
  wchar_t *target_version;
  BOOL     single_sign_on;
  BOOL     exclusive_folder;
  BOOL     crash_reporting;

  Registration *scheme;
} Options;

#define FROM_V1(p) ((Options *) (p))
#define FROM_V2(p) ((Options *) ((char *) (p) - offsetof (Options, v2)))
#define FROM_V3(p) ((Options *) ((char *) (p) - offsetof (Options, v3)))
#define FROM_V4(p) ((Options *) ((char *) (p) - offsetof (Options, v4)))

/* ---------------------------------------------------------------- helpers */

/* COM strings out of a method are the caller's to free with CoTaskMemFree. */
static wchar_t *
task_dup (const wchar_t *s)
{
  if (s == NULL)
    s = L"";
  size_t bytes = (wcslen (s) + 1) * sizeof (wchar_t);
  wchar_t *out = CoTaskMemAlloc (bytes);
  if (out)
    memcpy (out, s, bytes);
  return out;
}

static wchar_t *
wcs_dup (const wchar_t *s)
{
  if (s == NULL)
    return NULL;
  size_t bytes = (wcslen (s) + 1) * sizeof (wchar_t);
  wchar_t *out = g_malloc (bytes);
  memcpy (out, s, bytes);
  return out;
}

static void
replace (wchar_t **slot, const wchar_t *value)
{
  g_free (*slot);
  *slot = wcs_dup (value);
}

/* ------------------------------------------------- scheme registration -- */

static HRESULT STDMETHODCALLTYPE
reg_QueryInterface (ICoreWebView2CustomSchemeRegistration *self, REFIID riid,
                    void **ppv)
{
  if (ppv == NULL)
    return E_POINTER;
  if (IsEqualIID (riid, &IID_IUnknown) ||
      IsEqualIID (riid, &IID_ICoreWebView2CustomSchemeRegistration)) {
    *ppv = self;
    InterlockedIncrement (&((Registration *) self)->ref);
    return S_OK;
  }
  *ppv = NULL;
  return E_NOINTERFACE;
}

static ULONG STDMETHODCALLTYPE
reg_AddRef (ICoreWebView2CustomSchemeRegistration *self)
{
  return (ULONG) InterlockedIncrement (&((Registration *) self)->ref);
}

static ULONG STDMETHODCALLTYPE
reg_Release (ICoreWebView2CustomSchemeRegistration *self)
{
  Registration *r = (Registration *) self;
  LONG left = InterlockedDecrement (&r->ref);
  if (left == 0) {
    g_free (r->name);
    g_free (r);
  }
  return (ULONG) left;
}

static HRESULT STDMETHODCALLTYPE
reg_get_SchemeName (ICoreWebView2CustomSchemeRegistration *self, LPWSTR *value)
{
  if (value == NULL)
    return E_POINTER;
  *value = task_dup (((Registration *) self)->name);
  return *value ? S_OK : E_OUTOFMEMORY;
}

static HRESULT STDMETHODCALLTYPE
reg_get_TreatAsSecure (ICoreWebView2CustomSchemeRegistration *self, BOOL *value)
{
  if (value == NULL)
    return E_POINTER;
  *value = ((Registration *) self)->secure;
  return S_OK;
}

static HRESULT STDMETHODCALLTYPE
reg_put_TreatAsSecure (ICoreWebView2CustomSchemeRegistration *self, BOOL value)
{
  ((Registration *) self)->secure = value;
  return S_OK;
}

static HRESULT STDMETHODCALLTYPE
reg_GetAllowedOrigins (ICoreWebView2CustomSchemeRegistration *self,
                       UINT32 *count, LPWSTR **origins)
{
  /* None: nothing on the web may fetch lyndon: URLs, which is the point of
   * the scheme being display-isolated on the other build too. */
  if (count)
    *count = 0;
  if (origins)
    *origins = NULL;
  return S_OK;
}

static HRESULT STDMETHODCALLTYPE
reg_SetAllowedOrigins (ICoreWebView2CustomSchemeRegistration *self,
                       UINT32 count, LPCWSTR *origins)
{
  return S_OK;   /* accepted and ignored; there are none */
}

static HRESULT STDMETHODCALLTYPE
reg_get_HasAuthorityComponent (ICoreWebView2CustomSchemeRegistration *self,
                               BOOL *value)
{
  if (value == NULL)
    return E_POINTER;
  *value = ((Registration *) self)->authority;
  return S_OK;
}

static HRESULT STDMETHODCALLTYPE
reg_put_HasAuthorityComponent (ICoreWebView2CustomSchemeRegistration *self,
                               BOOL value)
{
  ((Registration *) self)->authority = value;
  return S_OK;
}

static ICoreWebView2CustomSchemeRegistrationVtbl reg_vtbl = {
  reg_QueryInterface,
  reg_AddRef,
  reg_Release,
  reg_get_SchemeName,
  reg_get_TreatAsSecure,
  reg_put_TreatAsSecure,
  reg_GetAllowedOrigins,
  reg_SetAllowedOrigins,
  reg_get_HasAuthorityComponent,
  reg_put_HasAuthorityComponent,
};

static Registration *
registration_new (const wchar_t *name)
{
  Registration *r = g_new0 (Registration, 1);
  r->iface.lpVtbl = &reg_vtbl;
  r->ref = 1;
  r->name = wcs_dup (name);
  /* Secure, so the start page is not treated as a mixed-content risk and can
   * use the same APIs an https page can. "lyndon:start" has no //host, so it
   * has no authority component. */
  r->secure = TRUE;
  r->authority = FALSE;
  return r;
}

/* ------------------------------------------------------- IUnknown, shared */

static void
options_destroy (Options *o)
{
  g_free (o->browser_arguments);
  g_free (o->language);
  g_free (o->target_version);
  if (o->scheme)
    reg_Release (&o->scheme->iface);
  g_free (o);
}

static HRESULT
options_qi (Options *o, REFIID riid, void **ppv)
{
  if (ppv == NULL)
    return E_POINTER;
  if (IsEqualIID (riid, &IID_IUnknown) ||
      IsEqualIID (riid, &IID_ICoreWebView2EnvironmentOptions))
    *ppv = &o->v1;
  else if (IsEqualIID (riid, &IID_ICoreWebView2EnvironmentOptions2))
    *ppv = &o->v2;
  else if (IsEqualIID (riid, &IID_ICoreWebView2EnvironmentOptions3))
    *ppv = &o->v3;
  else if (IsEqualIID (riid, &IID_ICoreWebView2EnvironmentOptions4))
    *ppv = &o->v4;
  else {
    *ppv = NULL;
    return E_NOINTERFACE;
  }
  InterlockedIncrement (&o->ref);
  return S_OK;
}

static ULONG
options_addref (Options *o)
{
  return (ULONG) InterlockedIncrement (&o->ref);
}

static ULONG
options_release (Options *o)
{
  LONG left = InterlockedDecrement (&o->ref);
  if (left == 0)
    options_destroy (o);
  return (ULONG) left;
}

#define UNKNOWN_FOR(prefix, Iface, FROM)                                      \
  static HRESULT STDMETHODCALLTYPE                                            \
  prefix##_QueryInterface (Iface *self, REFIID riid, void **ppv)              \
  { return options_qi (FROM (self), riid, ppv); }                             \
  static ULONG STDMETHODCALLTYPE prefix##_AddRef (Iface *self)                \
  { return options_addref (FROM (self)); }                                    \
  static ULONG STDMETHODCALLTYPE prefix##_Release (Iface *self)               \
  { return options_release (FROM (self)); }

UNKNOWN_FOR (o1, ICoreWebView2EnvironmentOptions,  FROM_V1)
UNKNOWN_FOR (o2, ICoreWebView2EnvironmentOptions2, FROM_V2)
UNKNOWN_FOR (o3, ICoreWebView2EnvironmentOptions3, FROM_V3)
UNKNOWN_FOR (o4, ICoreWebView2EnvironmentOptions4, FROM_V4)

/* ------------------------------------------------------------ options v1 */

static HRESULT STDMETHODCALLTYPE
o1_get_AdditionalBrowserArguments (ICoreWebView2EnvironmentOptions *self, LPWSTR *v)
{
  if (v == NULL) return E_POINTER;
  *v = task_dup (FROM_V1 (self)->browser_arguments);
  return *v ? S_OK : E_OUTOFMEMORY;
}

static HRESULT STDMETHODCALLTYPE
o1_put_AdditionalBrowserArguments (ICoreWebView2EnvironmentOptions *self, LPCWSTR v)
{
  replace (&FROM_V1 (self)->browser_arguments, v);
  return S_OK;
}

static HRESULT STDMETHODCALLTYPE
o1_get_Language (ICoreWebView2EnvironmentOptions *self, LPWSTR *v)
{
  if (v == NULL) return E_POINTER;
  *v = task_dup (FROM_V1 (self)->language);
  return *v ? S_OK : E_OUTOFMEMORY;
}

static HRESULT STDMETHODCALLTYPE
o1_put_Language (ICoreWebView2EnvironmentOptions *self, LPCWSTR v)
{
  replace (&FROM_V1 (self)->language, v);
  return S_OK;
}

static HRESULT STDMETHODCALLTYPE
o1_get_TargetCompatibleBrowserVersion (ICoreWebView2EnvironmentOptions *self, LPWSTR *v)
{
  if (v == NULL) return E_POINTER;
  *v = task_dup (FROM_V1 (self)->target_version);
  return *v ? S_OK : E_OUTOFMEMORY;
}

static HRESULT STDMETHODCALLTYPE
o1_put_TargetCompatibleBrowserVersion (ICoreWebView2EnvironmentOptions *self, LPCWSTR v)
{
  replace (&FROM_V1 (self)->target_version, v);
  return S_OK;
}

static HRESULT STDMETHODCALLTYPE
o1_get_AllowSingleSignOnUsingOSPrimaryAccount (ICoreWebView2EnvironmentOptions *self,
                                               BOOL *v)
{
  if (v == NULL) return E_POINTER;
  *v = FROM_V1 (self)->single_sign_on;
  return S_OK;
}

static HRESULT STDMETHODCALLTYPE
o1_put_AllowSingleSignOnUsingOSPrimaryAccount (ICoreWebView2EnvironmentOptions *self,
                                               BOOL v)
{
  FROM_V1 (self)->single_sign_on = v;
  return S_OK;
}

static ICoreWebView2EnvironmentOptionsVtbl o1_vtbl = {
  o1_QueryInterface, o1_AddRef, o1_Release,
  o1_get_AdditionalBrowserArguments, o1_put_AdditionalBrowserArguments,
  o1_get_Language, o1_put_Language,
  o1_get_TargetCompatibleBrowserVersion, o1_put_TargetCompatibleBrowserVersion,
  o1_get_AllowSingleSignOnUsingOSPrimaryAccount,
  o1_put_AllowSingleSignOnUsingOSPrimaryAccount,
};

/* ----------------------------------------------------------- options 2, 3 */

static HRESULT STDMETHODCALLTYPE
o2_get_ExclusiveUserDataFolderAccess (ICoreWebView2EnvironmentOptions2 *self, BOOL *v)
{
  if (v == NULL) return E_POINTER;
  *v = FROM_V2 (self)->exclusive_folder;
  return S_OK;
}

static HRESULT STDMETHODCALLTYPE
o2_put_ExclusiveUserDataFolderAccess (ICoreWebView2EnvironmentOptions2 *self, BOOL v)
{
  FROM_V2 (self)->exclusive_folder = v;
  return S_OK;
}

static ICoreWebView2EnvironmentOptions2Vtbl o2_vtbl = {
  o2_QueryInterface, o2_AddRef, o2_Release,
  o2_get_ExclusiveUserDataFolderAccess, o2_put_ExclusiveUserDataFolderAccess,
};

static HRESULT STDMETHODCALLTYPE
o3_get_IsCustomCrashReportingEnabled (ICoreWebView2EnvironmentOptions3 *self, BOOL *v)
{
  if (v == NULL) return E_POINTER;
  *v = FROM_V3 (self)->crash_reporting;
  return S_OK;
}

static HRESULT STDMETHODCALLTYPE
o3_put_IsCustomCrashReportingEnabled (ICoreWebView2EnvironmentOptions3 *self, BOOL v)
{
  FROM_V3 (self)->crash_reporting = v;
  return S_OK;
}

static ICoreWebView2EnvironmentOptions3Vtbl o3_vtbl = {
  o3_QueryInterface, o3_AddRef, o3_Release,
  o3_get_IsCustomCrashReportingEnabled, o3_put_IsCustomCrashReportingEnabled,
};

/* -------------------------------------------------- options 4: the scheme */

static HRESULT STDMETHODCALLTYPE
o4_GetCustomSchemeRegistrations (ICoreWebView2EnvironmentOptions4 *self,
                                 UINT32 *count,
                                 ICoreWebView2CustomSchemeRegistration ***list)
{
  Options *o = FROM_V4 (self);
  if (count == NULL || list == NULL)
    return E_POINTER;
  if (o->scheme == NULL) {
    *count = 0;
    *list = NULL;
    return S_OK;
  }
  ICoreWebView2CustomSchemeRegistration **out =
    CoTaskMemAlloc (sizeof *out);
  if (out == NULL)
    return E_OUTOFMEMORY;
  out[0] = &o->scheme->iface;
  reg_AddRef (out[0]);
  *count = 1;
  *list = out;
  return S_OK;
}

static HRESULT STDMETHODCALLTYPE
o4_SetCustomSchemeRegistrations (ICoreWebView2EnvironmentOptions4 *self,
                                 UINT32 count,
                                 ICoreWebView2CustomSchemeRegistration **list)
{
  /* Lyndon builds its own list and nothing else sets it. */
  return S_OK;
}

static ICoreWebView2EnvironmentOptions4Vtbl o4_vtbl = {
  o4_QueryInterface, o4_AddRef, o4_Release,
  o4_GetCustomSchemeRegistrations, o4_SetCustomSchemeRegistrations,
};

/* ------------------------------------------------------------------- API */

/* Chromium switches for the settings WebView2 has no property for. These are
 * read once, at environment creation, which is why the preferences window
 * says a restart is needed for them. */
static char *
browser_arguments (LyConfig *cfg)
{
  GString *args = g_string_new (NULL);

  if (cfg == NULL)
    return g_string_free (args, FALSE);

  if (!cfg->media_autoplay)
    g_string_append (args, " --autoplay-policy=user-gesture-required");
  if (cfg->force_dark != LY_DARK_OFF)
    g_string_append (args, " --enable-features=WebContentsForceDark");
  if (cfg->cookie_policy == LY_COOKIES_NO_THIRD_PARTY)
    g_string_append (args, " --test-third-party-cookie-phaseout");
  if (!cfg->smooth_scrolling)
    g_string_append (args, " --disable-smooth-scrolling");

  return g_string_free (args, FALSE);
}

void *
ly_environment_options_new (LyConfig *cfg, const char *runtime_version)
{
  Options *o = g_new0 (Options, 1);
  o->v1.lpVtbl = &o1_vtbl;
  o->v2.lpVtbl = &o2_vtbl;
  o->v3.lpVtbl = &o3_vtbl;
  o->v4.lpVtbl = &o4_vtbl;
  o->ref = 1;
  o->scheme = registration_new (L"" LY_SCHEME);

  /* Never newer than what is installed. */
  o->target_version = runtime_version && *runtime_version
    ? (wchar_t *) g_utf8_to_utf16 (runtime_version, -1, NULL, NULL, NULL)
    : wcs_dup (L"86.0.616.0");   /* the first stable WebView2 */

  g_autofree char *args = browser_arguments (cfg);
  if (args && *args)
    o->browser_arguments = (wchar_t *) g_utf8_to_utf16 (args, -1, NULL, NULL, NULL);

  if (cfg && cfg->languages && *cfg->languages) {
    /* WebView2 takes one BCP-47 tag, not the whole Accept-Language list. */
    g_autofree char *first = g_strdup (cfg->languages);
    char *comma = strchr (first, ',');
    if (comma)
      *comma = '\0';
    g_strstrip (first);
    if (*first)
      o->language = (wchar_t *) g_utf8_to_utf16 (first, -1, NULL, NULL, NULL);
  }

  return &o->v1;
}
