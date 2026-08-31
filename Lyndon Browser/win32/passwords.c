/* passwords.c — the Windows half of src/passwords.h.
 *
 * The Linux build puts secrets in the Secret Service through libsecret, on
 * the principle that the browser should never invent its own crypto and never
 * write a password to its own files. Windows has exactly that store already:
 * Credential Manager encrypts per user, unlocks at logon, and is manageable
 * from the Control Panel by someone who has never heard of Lyndon. So the
 * principle is unchanged and only the call is different.
 *
 * One thing does differ in shape. libsecret is asynchronous, so the header
 * asks for callbacks; the Credential Manager calls are synchronous. The
 * callbacks are therefore invoked before the function returns. That is within
 * the contract — a caller may not assume the callback is deferred — and it
 * keeps the two implementations behind the same header.
 */

#include "passwords.h"
#include "password-script.h"

#include <windows.h>
#include <wincred.h>
#include <string.h>

/* Everything Lyndon stores is prefixed, so enumeration can ask for only ours
 * and a user reading Credential Manager can see where it came from. */
#define TARGET_PREFIX "Lyndon:"

struct _LyPasswords {
  LyConfig *cfg;
};

/* ------------------------------------------------------------ conversions */

static wchar_t *
to_w (const char *utf8)
{
  return utf8 ? (wchar_t *) g_utf8_to_utf16 (utf8, -1, NULL, NULL, NULL) : NULL;
}

static char *
from_w (const wchar_t *w)
{
  return w ? g_utf16_to_utf8 ((const gunichar2 *) w, -1, NULL, NULL, NULL) : NULL;
}

/* "Lyndon:https://example.com|alice". The origin cannot contain a bar and a
 * username may, so the split is taken from the first one after the prefix. */
static char *
target_for (const char *origin, const char *username)
{
  return g_strdup_printf (TARGET_PREFIX "%s|%s", origin, username ? username : "");
}

static gboolean
split_target (const char *target, char **origin, char **username)
{
  if (!g_str_has_prefix (target, TARGET_PREFIX))
    return FALSE;
  const char *rest = target + strlen (TARGET_PREFIX);
  const char *bar = strchr (rest, '|');
  if (bar == NULL)
    return FALSE;
  *origin = g_strndup (rest, (gsize) (bar - rest));
  *username = g_strdup (bar + 1);
  return TRUE;
}

void
ly_credential_free (LyCredential *c)
{
  if (c == NULL)
    return;
  g_free (c->origin);
  g_free (c->username);
  if (c->password) {
    /* Not merely freed: the plaintext should not outlive the struct in a
     * page of heap that something else may later read. */
    memset (c->password, 0, strlen (c->password));
    g_free (c->password);
  }
  g_free (c);
}

/* ------------------------------------------------------------- lifecycle */

LyPasswords *
ly_passwords_new (LyConfig *cfg)
{
  LyPasswords *p = g_new0 (LyPasswords, 1);
  p->cfg = cfg;
  return p;
}

void
ly_passwords_free (LyPasswords *p)
{
  g_free (p);
}

gboolean
ly_passwords_available (LyPasswords *p)
{
  /* Credential Manager is part of Windows; there is no service to be missing
   * and nothing to connect to. */
  return TRUE;
}

const char *
ly_passwords_status (LyPasswords *p)
{
  return "Saved in Windows Credential Manager.";
}

const char *
ly_passwords_user_script (void)
{
  return LY_PASSWORD_SCRIPT;
}

/* ------------------------------------------------------------- retrieval */

static LyCredential *
credential_from (const CREDENTIALW *cred, gboolean with_password)
{
  g_autofree char *target = from_w (cred->TargetName);
  if (target == NULL)
    return NULL;

  char *origin = NULL, *username = NULL;
  if (!split_target (target, &origin, &username))
    return NULL;

  LyCredential *out = g_new0 (LyCredential, 1);
  out->origin = origin;
  out->username = username;

  if (with_password && cred->CredentialBlob && cred->CredentialBlobSize > 0) {
    /* The blob is UTF-16 with no terminator, so it is length-delimited. */
    gsize chars = cred->CredentialBlobSize / sizeof (wchar_t);
    out->password = g_utf16_to_utf8 ((const gunichar2 *) cred->CredentialBlob,
                                     (glong) chars, NULL, NULL, NULL);
  }
  return out;
}

static GPtrArray *
enumerate (const char *only_origin, gboolean with_password)
{
  GPtrArray *found =
    g_ptr_array_new_with_free_func ((GDestroyNotify) ly_credential_free);

  DWORD count = 0;
  PCREDENTIALW *creds = NULL;
  g_autofree wchar_t *filter = to_w (TARGET_PREFIX "*");
  if (filter == NULL || !CredEnumerateW (filter, 0, &count, &creds))
    return found;   /* nothing stored yet is not an error */

  for (DWORD i = 0; i < count; i++) {
    LyCredential *c = credential_from (creds[i], with_password);
    if (c == NULL)
      continue;
    if (only_origin && g_strcmp0 (c->origin, only_origin) != 0) {
      ly_credential_free (c);
      continue;
    }
    g_ptr_array_add (found, c);
  }
  CredFree (creds);
  return found;
}

void
ly_passwords_lookup (LyPasswords *p, const char *origin,
                     LyCredentialsFn callback, gpointer user_data)
{
  GPtrArray *found = enumerate (origin, TRUE);
  callback (found, user_data);
  g_ptr_array_unref (found);
}

void
ly_passwords_list (LyPasswords *p, LyCredentialsFn callback, gpointer user_data)
{
  /* The listing is for a settings page, which shows origins and usernames and
   * has no business holding every password in memory to do it. */
  GPtrArray *found = enumerate (NULL, FALSE);
  callback (found, user_data);
  g_ptr_array_unref (found);
}

/* --------------------------------------------------------------- writing */

void
ly_passwords_save (LyPasswords *p, const char *origin,
                   const char *username, const char *password)
{
  if (origin == NULL || password == NULL || *password == '\0')
    return;
  if (ly_passwords_is_blocked (p, origin))
    return;

  g_autofree char *target = target_for (origin, username);
  g_autofree wchar_t *wtarget = to_w (target);
  g_autofree wchar_t *wuser = to_w (username ? username : "");
  glong pw_chars = 0;
  wchar_t *wpass = (wchar_t *) g_utf8_to_utf16 (password, -1, NULL, &pw_chars, NULL);
  if (wtarget == NULL || wpass == NULL) {
    g_free (wpass);
    return;
  }

  CREDENTIALW cred = { 0 };
  cred.Type = CRED_TYPE_GENERIC;
  cred.TargetName = wtarget;
  cred.UserName = wuser;
  cred.CredentialBlob = (LPBYTE) wpass;
  cred.CredentialBlobSize = (DWORD) (pw_chars * sizeof (wchar_t));
  cred.Persist = CRED_PERSIST_LOCAL_MACHINE;
  cred.Comment = L"Saved by the Lyndon browser";

  if (!CredWriteW (&cred, 0))
    g_debug ("passwords: CredWrite failed (%lu)", GetLastError ());

  /* Wipe the plaintext copy rather than leaving it in freed heap. */
  memset (wpass, 0, (size_t) pw_chars * sizeof (wchar_t));
  g_free (wpass);
}

void
ly_passwords_forget (LyPasswords *p, const char *origin, const char *username)
{
  g_autofree char *target = target_for (origin, username);
  g_autofree wchar_t *wtarget = to_w (target);
  if (wtarget)
    CredDeleteW (wtarget, CRED_TYPE_GENERIC, 0);
}

/* -------------------------------------------------------------- blocking */

/* Identical to the Linux implementation, and deliberately so: "never for this
 * site" is a preference, not a secret, and belongs in the config file where
 * the user can see and edit it. */

gboolean
ly_passwords_is_blocked (LyPasswords *p, const char *origin)
{
  if (origin == NULL)
    return FALSE;
  for (guint i = 0; i < p->cfg->password_never->len; i++)
    if (g_strcmp0 (g_ptr_array_index (p->cfg->password_never, i), origin) == 0)
      return TRUE;
  return FALSE;
}

void
ly_passwords_block (LyPasswords *p, const char *origin)
{
  if (origin == NULL || ly_passwords_is_blocked (p, origin))
    return;
  g_ptr_array_add (p->cfg->password_never, g_strdup (origin));
  ly_config_touch (p->cfg);
}

void
ly_passwords_unblock (LyPasswords *p, const char *origin)
{
  for (guint i = 0; i < p->cfg->password_never->len; i++) {
    if (g_strcmp0 (g_ptr_array_index (p->cfg->password_never, i), origin) == 0) {
      g_ptr_array_remove_index (p->cfg->password_never, i);
      ly_config_touch (p->cfg);
      return;
    }
  }
}
