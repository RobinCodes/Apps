/* credentials.c — the parts of passwords.h that are not a keyring.
 *
 * A credential is a struct and an origin is a string, and neither has
 * anything to do with the Secret Service or with Windows Credential Manager.
 * They live here so that both builds share one copy: the origin is the key
 * autofill matches on, and two implementations of that rule would eventually
 * be two rules — a saved login that the site it came from no longer matches.
 *
 * src/passwords.c and win32/passwords.c implement the rest of the header,
 * each against its own platform's store.
 */

#include "passwords.h"

#include <string.h>

LyCredential *
ly_credential_new (const char *origin, const char *username, const char *password)
{
  LyCredential *credential = g_new0 (LyCredential, 1);
  credential->origin   = g_strdup (origin ?: "");
  credential->username = g_strdup (username ?: "");
  credential->password = password != NULL ? g_strdup (password) : NULL;
  return credential;
}

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

GPtrArray *
ly_credentials_new (void)
{
  return g_ptr_array_new_with_free_func ((GDestroyNotify) ly_credential_free);
}

/* ------------------------------------------------------------- origins */

/* The autofill key is whatever the page script reports, which is exactly
 * JavaScript's location.origin: lower-cased scheme and host, and a port only
 * when it is not the scheme's default. Everything that can put a row in the
 * store — capture, hand editing, import — goes through here, so a row typed
 * as "Example.com" still matches the site that produced it.  */
char *
ly_passwords_normalise_origin (const char *text)
{
  if (text == NULL)
    return NULL;

  g_autofree char *trimmed = g_strdup (text);
  g_strstrip (trimmed);
  if (*trimmed == '\0')
    return NULL;

  /* Bare hosts are far more likely to be typed than written out in full, and
   * a login form served over plain http is the rare case now. */
  gboolean had_scheme = strstr (trimmed, "://") != NULL;
  g_autofree char *full = had_scheme
    ? g_strdup (trimmed)
    : g_strdup_printf ("https://%s", trimmed);

  g_autoptr (GUri) uri = g_uri_parse (full, G_URI_FLAGS_NONE, NULL);
  if (uri == NULL)
    return NULL;

  const char *scheme = g_uri_get_scheme (uri);
  const char *host   = g_uri_get_host (uri);
  if (scheme == NULL || host == NULL || *host == '\0')
    return NULL;

  /* Without a scheme to go on, a single bare word is far more likely to be a
   * label than a host — import files are full of "Netflix" and "Work laptop",
   * and turning those into origins would fill the store with rows that can
   * never match a page. Anything written out in full is taken at its word. */
  if (!had_scheme && strchr (host, '.') == NULL &&
      g_ascii_strcasecmp (host, "localhost") != 0)
    return NULL;

  g_autofree char *lower_scheme = g_ascii_strdown (scheme, -1);
  g_autofree char *lower_host   = g_ascii_strdown (host, -1);

  int port = g_uri_get_port (uri);
  gboolean default_port =
    port < 0 ||
    (port == 443 && g_strcmp0 (lower_scheme, "https") == 0) ||
    (port == 80  && g_strcmp0 (lower_scheme, "http") == 0);

  if (default_port)
    return g_strdup_printf ("%s://%s", lower_scheme, lower_host);
  return g_strdup_printf ("%s://%s:%d", lower_scheme, lower_host, port);
}
