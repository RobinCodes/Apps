/* passwords.h — form login capture, autofill and storage.
 *
 * Secrets go to the Secret Service (GNOME Keyring, KWallet, KeePassXC — any
 * org.freedesktop.secrets provider) via libsecret, and on Windows to
 * Credential Manager. Lyndon never invents its own crypto and never writes a
 * password to its own files: both stores already solve at-rest encryption and
 * unlock-on-login, and each is the only one the rest of that desktop can also
 * manage.
 *
 * src/passwords.c and win32/passwords.c are the two implementations;
 * src/credentials.c holds the parts that are neither — the struct and the
 * origin rule — so that both builds match a saved login the same way.
 */
#pragma once

#include "lyndon.h"

G_BEGIN_DECLS

typedef struct _LyPasswords LyPasswords;

typedef struct {
  char *origin;     /* scheme://host[:port] — the autofill matching key */
  char *username;
  char *password;   /* NULL only when the store could not give it up */
} LyCredential;

LyCredential *ly_credential_new  (const char *origin, const char *username,
                                  const char *password);
void          ly_credential_free (LyCredential *credential);

/* Free func for a GPtrArray of LyCredential*. */
GPtrArray *ly_credentials_new (void);

/* "example.com", "https://Example.com:443/login?x=1" and "HTTPS://example.com"
 * all normalise to "https://example.com" — the same shape the page script
 * reports, so hand-entered and imported rows match on autofill. NULL when
 * there is no usable host in the text. */
char *ly_passwords_normalise_origin (const char *text);

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
/* Every credential stored, secrets included: exporting needs them, and the
 * settings page simply does not copy what it does not draw. */
void ly_passwords_list   (LyPasswords *passwords,
                          LyCredentialsFn callback, gpointer user_data);

void ly_passwords_save   (LyPasswords *passwords, const char *origin,
                          const char *username, const char *password);
void ly_passwords_forget (LyPasswords *passwords, const char *origin,
                          const char *username);

/* Blocking variants. Editing and importing both need to know whether the write
 * landed before they report anything back, and importing needs the writes to
 * stay in order, so those paths pay one D-Bus round trip per entry. */
gboolean ly_passwords_save_sync (LyPasswords *passwords, const char *origin,
                                 const char *username, const char *password,
                                 GError **error);

/* Changing the origin or the username changes the identity of the keyring
 * item, so an edit is a store followed by a delete of the row it replaced —
 * in that order, so a failure loses nothing. */
gboolean ly_passwords_update (LyPasswords *passwords,
                              const char *old_origin, const char *old_username,
                              const char *origin, const char *username,
                              const char *password, GError **error);

/* Origins the user said "never" for. */
gboolean ly_passwords_is_blocked (LyPasswords *passwords, const char *origin);
void     ly_passwords_block      (LyPasswords *passwords, const char *origin);
void     ly_passwords_unblock    (LyPasswords *passwords, const char *origin);

G_END_DECLS
