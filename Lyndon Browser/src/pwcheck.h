/* pwcheck.h — what Chrome's password manager calls Checkup, and the generator
 * that fixes what it finds.
 *
 * Three questions about a saved login, in increasing order of what they cost:
 *
 *   weak      — length, variety and the list of passwords everybody uses.
 *               Arithmetic on a string; no store, no network.
 *   reused    — one password serving several sites, which turns any one of
 *               those sites' breaches into all of them. Needs the whole set
 *               of saved logins, and nothing else.
 *   leaked    — the password appears in a known breach corpus. This is the
 *               only one that reaches the network, and the only one that is
 *               off until somebody turns it on.
 *
 * The leak check sends the first five characters of the password's SHA-1 and
 * nothing else: the service answers with every hash it holds under that
 * prefix — tens of thousands of them — and the comparison happens here. That
 * is the same k-anonymity shape Chrome's own checkup uses, and it is the
 * reason this can be done against a service that is not ours without handing
 * it the password, the username or the site.
 *
 * Nothing here is in the Windows build: the front end there has its own
 * preferences and never calls any of it. The analysis would port unchanged;
 * the libsoup fetch and libcrypto's random bytes would not.
 */
#pragma once

#include "lyndon.h"
#include "passwords.h"

G_BEGIN_DECLS

typedef enum {
  LY_PW_FINE   = 0,
  LY_PW_WEAK   = 1 << 0,
  LY_PW_REUSED = 1 << 1,
  LY_PW_LEAKED = 1 << 2,
} LyPwIssues;

/* 0 (guessed immediately) to 4 (strong). */
int         ly_pw_strength       (const char *password);
const char *ly_pw_strength_label (int score);
/* Short, or one of the passwords that turn up at the top of every breach:
 * Chrome's definition, and the one the Checkup list draws from. */
gboolean    ly_pw_is_weak        (const char *password);

/* 20 characters, mixed case, digits and the punctuation that no site rejects,
 * from the system's cryptographic random source, with at least one of each
 * class present. NULL only when that source could not be read, in which case
 * offering a password at all would be worse than not offering one. */
char *ly_pw_generate (void);

/* One saved login's verdict. No password: a finding can be kept, drawn and
 * sorted without a secret in it. */
typedef struct {
  char  *origin;
  char  *username;
  guint  issues;         /* LyPwIssues */
  int    strength;
  guint  shared_with;    /* other saved sites using this same password */
  guint  leaked_count;   /* appearances in the corpus; 0 when unchecked */
} LyPwFinding;

void ly_pw_finding_free (LyPwFinding *finding);

/* A finding per credential that has something wrong with it, worst first.
 * Caller frees with g_ptr_array_unref. */
GPtrArray *ly_pwcheck_local (GPtrArray *credentials);

/* Parses one `range` response: lines of `SUFFIX:COUNT`, and answers how many
 * times `suffix` appears in it. Separated out because it is the part worth
 * testing without a network. */
guint ly_pwcheck_count_in_range (const char *body, const char *suffix);

/* The leak check. `credentials` is needed again because `findings` holds no
 * secrets; rows that turn out to be in the corpus gain LY_PW_LEAKED and a
 * count. A credential with no finding of its own yet gains one.
 *
 * `findings` is handed back to the callback — the same array, extended in
 * place — or NULL with `error` set when nothing could be checked at all. One
 * prefix that fails is not a failure: it leaves those rows unchecked and the
 * rest still answered, which is better than refusing to answer anything. */
typedef void (*LyPwCheckFn) (GPtrArray *findings, const char *error,
                             gpointer user_data);

void ly_pwcheck_breaches (GPtrArray    *credentials,
                          GPtrArray    *findings,
                          GCancellable *cancel,
                          LyPwCheckFn   done,
                          gpointer      user_data);

G_END_DECLS
