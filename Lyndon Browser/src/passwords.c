/* passwords.c — see passwords.h. */

#include "passwords.h"
#include "password-script.h"

#include <libsecret/secret.h>
#include <string.h>

struct _LyPasswords {
  LyConfig     *cfg;
  SecretService *service;
  GCancellable *cancel;
  char         *status;
};

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

/* A note's item, keyed exactly as its login is. See passwords.h for why it is
 * not simply another attribute on the login itself. */
static const SecretSchema *
note_schema (void)
{
  static const SecretSchema schema = {
    "org.lyndon.Browser.LoginNote", SECRET_SCHEMA_NONE,
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

/* In password-script.h, shared verbatim with the Windows build: only the
 * line that posts a message back to the browser differs between the two. */

const char *
ly_passwords_user_script (void)
{
  return LY_PASSWORD_SCRIPT;
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

/* -- the same-domain fallback ------------------------------------------- */

typedef struct {
  LyPasswords    *passwords;
  char           *origin;
  char           *domain;
  LyCredentialsFn callback;
  gpointer        user_data;
} Fallback;

static void
fallback_free (Fallback *fallback)
{
  g_free (fallback->origin);
  g_free (fallback->domain);
  g_free (fallback);
}

static void
on_domain_search_done (GObject *source, GAsyncResult *result, gpointer data)
{
  Fallback *fallback = data;
  g_autoptr (GError) error = NULL;

  GList *results = secret_password_search_finish (result, &error);
  if (error != NULL && !g_error_matches (error, G_IO_ERROR, G_IO_ERROR_CANCELLED))
    g_message ("passwords: domain search failed: %s", error->message);

  g_autoptr (GPtrArray) credentials = ly_credentials_new ();

  for (GList *l = results; l != NULL; l = l->next) {
    SecretRetrievable *item = l->data;

    g_autoptr (GHashTable) attributes = secret_retrievable_get_attributes (item);
    if (attributes == NULL)
      continue;

    const char *origin = g_hash_table_lookup (attributes, "origin");
    if (origin == NULL || g_strcmp0 (origin, fallback->origin) == 0)
      continue;

    g_autofree char *domain = ly_uri_base_domain (origin);
    if (g_strcmp0 (domain, fallback->domain) != 0)
      continue;

    /* A login saved over https does not get filled into a page served over
     * http: that is the one direction of this fallback that would hand a
     * password to whoever is between. */
    if (g_str_has_prefix (fallback->origin, "http://") &&
        !g_str_has_prefix (origin, "http://"))
      continue;

    LyCredential *credential = g_new0 (LyCredential, 1);
    credential->origin   = g_strdup (origin);
    credential->username = g_strdup (g_hash_table_lookup (attributes, "username") ?: "");

    /* Secrets were deliberately not loaded for the whole store — this path
     * sees every saved login — so the few that matched are fetched one at a
     * time. Synchronous, and on a path that only runs when the site had no
     * login of its own: a page with an exact match never reaches here. */
    SecretValue *value = secret_retrievable_retrieve_secret_sync (item, NULL, NULL);
    if (value != NULL) {
      credential->password = g_strdup (secret_value_get_text (value));
      secret_value_unref (value);
    }
    g_ptr_array_add (credentials, credential);
  }
  g_list_free_full (results, g_object_unref);

  if (fallback->callback != NULL)
    fallback->callback (credentials, fallback->user_data);
  fallback_free (fallback);
}

static void
on_exact_lookup_done (GPtrArray *credentials, gpointer data)
{
  Fallback *fallback = data;

  /* An empty answer because there is no keyring is not an empty answer about
   * this origin, and searching the store again would only ask the same
   * absent service a second question. */
  if (credentials->len > 0 || fallback->domain == NULL ||
      !ly_passwords_available (fallback->passwords)) {
    if (fallback->callback != NULL)
      fallback->callback (credentials, fallback->user_data);
    fallback_free (fallback);
    return;
  }

  /* Attributes only: what comes back is every saved login, and loading every
   * password to look at its origin would be both slow and more than this
   * question needs. */
  secret_password_search (login_schema (),
                          SECRET_SEARCH_ALL | SECRET_SEARCH_UNLOCK,
                          fallback->passwords->cancel,
                          on_domain_search_done, fallback, NULL);
}

void
ly_passwords_lookup_matching (LyPasswords *passwords, const char *origin,
                              LyCredentialsFn callback, gpointer user_data)
{
  if (origin == NULL || *origin == '\0') {
    ly_passwords_lookup (passwords, origin, callback, user_data);
    return;
  }

  Fallback *fallback = g_new0 (Fallback, 1);
  fallback->passwords = passwords;
  fallback->origin    = g_strdup (origin);
  fallback->domain    = ly_uri_base_domain (origin);
  fallback->callback  = callback;
  fallback->user_data = user_data;

  /* A bare host is its own registrable domain, and then the fallback would
   * match everything under it only by matching itself: nothing to gain, and
   * an extra search of the whole store to find that out. */
  g_autofree char *host = ly_uri_host (origin);
  if (g_strcmp0 (host, fallback->domain) == 0)
    g_clear_pointer (&fallback->domain, g_free);

  search (passwords, origin, on_exact_lookup_done, fallback);
}

/* ------------------------------------------------------------ store/forget */

static char *
login_label (const char *origin, const char *username)
{
  return g_strdup_printf ("%s \u2014 %s", origin,
                          (username && *username) ? username : "login");
}


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

  g_autofree char *label = login_label (origin, username);

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

/* -------------------------------------------------------- edit and import */

gboolean
ly_passwords_save_sync (LyPasswords *passwords, const char *origin,
                        const char *username, const char *password,
                        GError **error)
{
  if (!ly_passwords_available (passwords)) {
    g_set_error_literal (error, G_IO_ERROR, G_IO_ERROR_NOT_SUPPORTED,
                         "No keyring is available to store passwords in");
    return FALSE;
  }
  if (origin == NULL || *origin == '\0' || password == NULL) {
    g_set_error_literal (error, G_IO_ERROR, G_IO_ERROR_INVALID_ARGUMENT,
                         "A site and a password are both required");
    return FALSE;
  }

  g_autofree char *label = login_label (origin, username);

  /* The attributes are the identity of the item, so this replaces a row with
   * the same origin and username rather than adding a second one. */
  return secret_password_store_sync (login_schema (), SECRET_COLLECTION_DEFAULT,
                                     label, password, NULL, error,
                                     "origin",   origin,
                                     "username", username ?: "",
                                     NULL);
}

gboolean
ly_passwords_update (LyPasswords *passwords,
                     const char *old_origin, const char *old_username,
                     const char *origin, const char *username,
                     const char *password, GError **error)
{
  if (!ly_passwords_save_sync (passwords, origin, username, password, error))
    return FALSE;

  gboolean same = g_strcmp0 (old_origin, origin) == 0 &&
                  g_strcmp0 (old_username ?: "", username ?: "") == 0;
  if (same || old_origin == NULL)
    return TRUE;

  /* The replacement is safely stored, so losing this delete costs a duplicate
   * row rather than the login itself. */
  g_autoptr (GError) local = NULL;
  if (!secret_password_clear_sync (login_schema (), NULL, &local,
                                   "origin",   old_origin,
                                   "username", old_username ?: "",
                                   NULL) && local != NULL)
    g_warning ("passwords: could not remove the row it replaced: %s", local->message);

  return TRUE;
}

/* ------------------------------------------------------------------- notes */

char *
ly_passwords_note (LyPasswords *passwords, const char *origin, const char *username)
{
  if (!ly_passwords_available (passwords) || origin == NULL)
    return NULL;

  g_autoptr (GError) error = NULL;
  char *note = secret_password_lookup_sync (note_schema (), NULL, &error,
                                            "origin",   origin,
                                            "username", username ?: "",
                                            NULL);
  if (error != NULL)
    g_message ("passwords: could not read the note: %s", error->message);
  return note;
}

gboolean
ly_passwords_set_note (LyPasswords *passwords, const char *origin,
                       const char *username, const char *note, GError **error)
{
  if (!ly_passwords_available (passwords)) {
    g_set_error_literal (error, G_IO_ERROR, G_IO_ERROR_NOT_SUPPORTED,
                         "No keyring is available to store notes in");
    return FALSE;
  }
  if (origin == NULL || *origin == '\0') {
    g_set_error_literal (error, G_IO_ERROR, G_IO_ERROR_INVALID_ARGUMENT,
                         "A site is required");
    return FALSE;
  }

  if (note == NULL || *note == '\0') {
    /* Nothing stored for an empty note: a blank item would be a row in the
     * keyring that says nothing, and the desktop's own password manager would
     * draw it beside the login as if it did. */
    g_autoptr (GError) local = NULL;
    secret_password_clear_sync (note_schema (), NULL, &local,
                                "origin",   origin,
                                "username", username ?: "",
                                NULL);
    return TRUE;
  }

  g_autofree char *label = g_strdup_printf ("%s \u2014 note", origin);
  return secret_password_store_sync (note_schema (), SECRET_COLLECTION_DEFAULT,
                                     label, note, NULL, error,
                                     "origin",   origin,
                                     "username", username ?: "",
                                     NULL);
}

void
ly_passwords_move_note (LyPasswords *passwords,
                        const char *old_origin, const char *old_username,
                        const char *origin, const char *username)
{
  if (old_origin == NULL ||
      (g_strcmp0 (old_origin, origin) == 0 &&
       g_strcmp0 (old_username ?: "", username ?: "") == 0))
    return;

  g_autofree char *note = ly_passwords_note (passwords, old_origin, old_username);
  if (note == NULL)
    return;

  g_autoptr (GError) error = NULL;
  if (!ly_passwords_set_note (passwords, origin, username, note, &error)) {
    g_warning ("passwords: could not move the note: %s", error->message);
    return;                      /* leave it where it still is */
  }
  ly_passwords_set_note (passwords, old_origin, old_username, NULL, NULL);
  memset (note, 0, strlen (note));
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
