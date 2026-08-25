/* passwords.h — form login capture, autofill and storage.
 *
 * Secrets go to the Secret Service (GNOME Keyring, KWallet, KeePassXC — any
 * org.freedesktop.secrets provider) via libsecret. Lyndon never invents its
 * own crypto and never writes a password to its own files: the keyring already
 * solves at-rest encryption and unlock-on-login, and it is the only store the
 * rest of the desktop can also manage.
 */
#pragma once

#include "lyndon.h"

G_BEGIN_DECLS

typedef struct _LyPasswords LyPasswords;

typedef struct {
  char *origin;     /* scheme://host[:port] — the autofill matching key */
  char *username;
  char *password;   /* NULL when only the listing was requested */
} LyCredential;

void ly_credential_free (LyCredential *credential);

LyPasswords *ly_passwords_new  (LyConfig *cfg);
void         ly_passwords_free (LyPasswords *passwords);

/* False when no Secret Service is reachable; the UI degrades to explaining
 * that rather than silently dropping logins. */
gboolean     ly_passwords_available (LyPasswords *passwords);
const char  *ly_passwords_status    (LyPasswords *passwords);

/* The script injected into pages to find, fill and capture login forms. */
const char *ly_passwords_user_script (void);

typedef void (*LyCredentialsFn) (GPtrArray *credentials, gpointer user_data);

/* Every credential stored for this exact origin. */
void ly_passwords_lookup (LyPasswords *passwords, const char *origin,
                          LyCredentialsFn callback, gpointer user_data);
void ly_passwords_list   (LyPasswords *passwords,
                          LyCredentialsFn callback, gpointer user_data);

void ly_passwords_save   (LyPasswords *passwords, const char *origin,
                          const char *username, const char *password);
void ly_passwords_forget (LyPasswords *passwords, const char *origin,
                          const char *username);

/* Origins the user said "never" for. */
gboolean ly_passwords_is_blocked (LyPasswords *passwords, const char *origin);
void     ly_passwords_block      (LyPasswords *passwords, const char *origin);
void     ly_passwords_unblock    (LyPasswords *passwords, const char *origin);

G_END_DECLS
