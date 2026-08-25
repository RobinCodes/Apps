/* passwords.c — see passwords.h. */

#include "passwords.h"

#include <libsecret/secret.h>
#include <string.h>

struct _LyPasswords {
  LyConfig     *cfg;
  SecretService *service;
  GCancellable *cancel;
  char         *status;
};

void
ly_credential_free (LyCredential *credential)
{
  if (credential == NULL)
    return;
  g_free (credential->origin);
  g_free (credential->username);
  if (credential->password != NULL) {
    /* Do not leave the plaintext lying in freed heap. */
    memset (credential->password, 0, strlen (credential->password));
    g_free (credential->password);
  }
  g_free (credential);
}

/* ---------------------------------------------------------------- schema */

static const SecretSchema *
login_schema (void)
{
  static const SecretSchema schema = {
    "org.lyndon.Browser.Login", SECRET_SCHEMA_NONE,
    {
      { "origin",   SECRET_SCHEMA_ATTRIBUTE_STRING },
      { "username", SECRET_SCHEMA_ATTRIBUTE_STRING },
      { "NULL",     0 },
    },
    /* Reserved fields. */
    0, NULL, NULL, NULL, NULL, NULL, NULL, NULL
  };
  return &schema;
}

/* ------------------------------------------------------------ the script */

/* Runs at document-end in the top frame only. Never in subframes: a
 * third-party iframe that could read a filled password would defeat the
 * point of having a password manager at all. */
static const char *PASSWORD_SCRIPT =
"(function(){'use strict';"
"if(window.__lyndonPw)return;"
"Object.defineProperty(window,'__lyndonPw',{value:1});"
"var H=window.webkit&&window.webkit.messageHandlers&&"
"      window.webkit.messageHandlers.lyndonPasswords;"
"if(!H)return;"

"var vis=function(e){return !!(e.offsetWidth||e.offsetHeight||e.getClientRects().length);};"
"var pws=function(){return [].slice.call("
"  document.querySelectorAll('input[type=password]')).filter(vis);};"

/* The username is almost always the nearest preceding text-ish input in the
 * same form; a few sites put it after, so fall back to looking forward. */
"var userFor=function(p){"
" var scope=p.form||document;"
" var ins=[].slice.call(scope.querySelectorAll('input'));"
" var at=ins.indexOf(p),ok=['text','email','tel',''];"
" for(var i=at-1;i>=0;i--){if(ok.indexOf((ins[i].type||'').toLowerCase())>=0&&vis(ins[i]))return ins[i];}"
" for(var j=at+1;j<ins.length;j++){if(ok.indexOf((ins[j].type||'').toLowerCase())>=0&&vis(ins[j]))return ins[j];}"
" return null;};"

/* Frameworks track their own state, so writing .value directly is invisible to
 * them. Going through the prototype setter and firing the events is what makes
 * React and friends notice the fill. */
"var setVal=function(e,v){"
" try{var d=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value');"
" if(d&&d.set)d.set.call(e,v);else e.value=v;}catch(_){e.value=v;}"
" e.dispatchEvent(new Event('input',{bubbles:true}));"
" e.dispatchEvent(new Event('change',{bubbles:true}));};"

"window.__lyndonFill=function(u,p){"
" var f=pws();if(!f.length)return false;"
" var pw=f[0],uf=userFor(pw);"
" if(uf&&u)setVal(uf,u);"
" setVal(pw,p);"
" return true;};"

"var last='';"
"var report=function(){"
" var n=pws().length;var key=n+'|'+location.origin;"
" if(key===last)return;last=key;"
" H.postMessage({type:'forms',count:n,origin:location.origin});};"

"var capture=function(){"
" var f=pws();if(!f.length)return;"
" var pw=f[0];if(!pw.value)return;"
" var uf=userFor(pw);"
" H.postMessage({type:'submit',origin:location.origin,"
"                username:uf?uf.value:'',password:pw.value});};"

"document.addEventListener('submit',capture,true);"
/* Single-page logins frequently never fire a submit event. */
"document.addEventListener('click',function(e){"
" var t=e.target;if(!t||!t.closest)return;"
" if(t.closest('button,input[type=submit],input[type=button],[role=button]'))"
"   setTimeout(capture,0);},true);"
"document.addEventListener('keydown',function(e){"
" if(e.key==='Enter')setTimeout(capture,0);},true);"

"report();"
/* Debounced: login forms often appear well after first paint, but a
 * mutation-per-message would flood the UI process. */
"var t=null;"
"try{new MutationObserver(function(){"
" if(t)return;t=setTimeout(function(){t=null;report();},400);"
"}).observe(document.documentElement,{childList:true,subtree:true});}catch(_){}"
"})();";

const char *
ly_passwords_user_script (void)
{
  return PASSWORD_SCRIPT;
}

/* ------------------------------------------------------------- lifecycle */

LyPasswords *
ly_passwords_new (LyConfig *cfg)
{
  LyPasswords *passwords = g_new0 (LyPasswords, 1);
  passwords->cfg    = cfg;
  passwords->cancel = g_cancellable_new ();

  g_autoptr (GError) error = NULL;
  /* Synchronous on purpose, and cheap: this is a D-Bus name lookup at
   * startup, and every later call needs to know whether it has a service. */
  passwords->service = secret_service_get_sync (SECRET_SERVICE_NONE, NULL, &error);

  if (passwords->service == NULL) {
    passwords->status = g_strdup_printf ("No keyring available (%s)",
                                         error ? error->message : "not running");
    g_message ("passwords: %s", passwords->status);
  } else {
    passwords->status = g_strdup ("Stored in your login keyring");
  }

  return passwords;
}

void
ly_passwords_free (LyPasswords *passwords)
{
  if (passwords == NULL)
    return;
  g_cancellable_cancel (passwords->cancel);
  g_clear_object (&passwords->cancel);
  g_clear_object (&passwords->service);
  g_clear_pointer (&passwords->status, g_free);
  g_free (passwords);
}

gboolean
ly_passwords_available (LyPasswords *passwords)
{
  return passwords->service != NULL;
}

const char *
ly_passwords_status (LyPasswords *passwords)
{
  return passwords->status ?: "";
}

/* ---------------------------------------------------------------- lookup */

typedef struct {
  LyPasswords    *passwords;
  LyCredentialsFn callback;
  gpointer        user_data;
} Query;

static GPtrArray *
credentials_from_results (GList *results)
{
  GPtrArray *credentials =
    g_ptr_array_new_with_free_func ((GDestroyNotify) ly_credential_free);

  for (GList *l = results; l != NULL; l = l->next) {
    SecretRetrievable *item = l->data;

    g_autoptr (GHashTable) attributes = secret_retrievable_get_attributes (item);
    if (attributes == NULL)
      continue;

    LyCredential *credential = g_new0 (LyCredential, 1);
    credential->origin   = g_strdup (g_hash_table_lookup (attributes, "origin") ?: "");
    credential->username = g_strdup (g_hash_table_lookup (attributes, "username") ?: "");

    /* SECRET_SEARCH_LOAD_SECRETS was requested, so this is already cached
     * locally and does not go back over D-Bus. */
    SecretValue *value = secret_retrievable_retrieve_secret_sync (item, NULL, NULL);
    if (value != NULL) {
      credential->password = g_strdup (secret_value_get_text (value));
      secret_value_unref (value);
    }

    g_ptr_array_add (credentials, credential);
  }
  return credentials;
}

static void
on_search_done (GObject *source, GAsyncResult *result, gpointer data)
{
  Query *query = data;
  g_autoptr (GError) error = NULL;

  GList *results = secret_password_search_finish (result, &error);

  if (error != NULL && !g_error_matches (error, G_IO_ERROR, G_IO_ERROR_CANCELLED))
    g_message ("passwords: search failed: %s", error->message);

  g_autoptr (GPtrArray) credentials = credentials_from_results (results);
  g_list_free_full (results, g_object_unref);

  if (query->callback != NULL)
    query->callback (credentials, query->user_data);

  g_free (query);
}

static void
search (LyPasswords *passwords, const char *origin,
        LyCredentialsFn callback, gpointer user_data)
{
  if (!ly_passwords_available (passwords)) {
    g_autoptr (GPtrArray) empty =
      g_ptr_array_new_with_free_func ((GDestroyNotify) ly_credential_free);
    if (callback != NULL)
      callback (empty, user_data);
    return;
  }

  Query *query = g_new0 (Query, 1);
  query->passwords = passwords;
  query->callback  = callback;
  query->user_data = user_data;

  SecretSearchFlags flags = SECRET_SEARCH_ALL | SECRET_SEARCH_UNLOCK |
                            SECRET_SEARCH_LOAD_SECRETS;

  if (origin != NULL)
    secret_password_search (login_schema (), flags, passwords->cancel,
                            on_search_done, query,
                            "origin", origin, NULL);
  else
    secret_password_search (login_schema (), flags, passwords->cancel,
                            on_search_done, query, NULL);
}

void
ly_passwords_lookup (LyPasswords *passwords, const char *origin,
                     LyCredentialsFn callback, gpointer user_data)
{
  search (passwords, origin, callback, user_data);
}

void
ly_passwords_list (LyPasswords *passwords, LyCredentialsFn callback, gpointer user_data)
{
  search (passwords, NULL, callback, user_data);
}

/* ------------------------------------------------------------ store/forget */

static void
on_stored (GObject *source, GAsyncResult *result, gpointer data)
{
  g_autoptr (GError) error = NULL;
  if (!secret_password_store_finish (result, &error) &&
      !g_error_matches (error, G_IO_ERROR, G_IO_ERROR_CANCELLED))
    g_warning ("passwords: could not store: %s", error->message);
}

void
ly_passwords_save (LyPasswords *passwords, const char *origin,
                   const char *username, const char *password)
{
  if (!ly_passwords_available (passwords) || origin == NULL || password == NULL)
    return;

  g_autofree char *label =
    g_strdup_printf ("%s — %s", origin, (username && *username) ? username : "login");

  secret_password_store (login_schema (), SECRET_COLLECTION_DEFAULT, label, password,
                         passwords->cancel, on_stored, NULL,
                         "origin",   origin,
                         "username", username ?: "",
                         NULL);
}

static void
on_cleared (GObject *source, GAsyncResult *result, gpointer data)
{
  g_autoptr (GError) error = NULL;
  if (!secret_password_clear_finish (result, &error) &&
      !g_error_matches (error, G_IO_ERROR, G_IO_ERROR_CANCELLED))
    g_warning ("passwords: could not remove: %s", error->message);
}

void
ly_passwords_forget (LyPasswords *passwords, const char *origin, const char *username)
{
  if (!ly_passwords_available (passwords) || origin == NULL)
    return;

  secret_password_clear (login_schema (), passwords->cancel, on_cleared, NULL,
                         "origin",   origin,
                         "username", username ?: "",
                         NULL);
}

/* ---------------------------------------------------------- never-ask list */

gboolean
ly_passwords_is_blocked (LyPasswords *passwords, const char *origin)
{
  if (origin == NULL)
    return FALSE;
  for (guint i = 0; i < passwords->cfg->password_never->len; i++)
    if (g_strcmp0 (g_ptr_array_index (passwords->cfg->password_never, i), origin) == 0)
      return TRUE;
  return FALSE;
}

void
ly_passwords_block (LyPasswords *passwords, const char *origin)
{
  if (origin == NULL || ly_passwords_is_blocked (passwords, origin))
    return;
  g_ptr_array_add (passwords->cfg->password_never, g_strdup (origin));
  ly_config_touch (passwords->cfg);
}

void
ly_passwords_unblock (LyPasswords *passwords, const char *origin)
{
  for (guint i = 0; i < passwords->cfg->password_never->len; i++) {
    if (g_strcmp0 (g_ptr_array_index (passwords->cfg->password_never, i), origin) == 0) {
      g_ptr_array_remove_index (passwords->cfg->password_never, i);
      ly_config_touch (passwords->cfg);
      return;
    }
  }
}
