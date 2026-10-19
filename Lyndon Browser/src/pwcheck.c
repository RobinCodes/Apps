/* pwcheck.c — see pwcheck.h. */

#include "pwcheck.h"

#include <libsoup/soup.h>
#include <openssl/rand.h>
#include <math.h>
#include <string.h>

/* ----------------------------------------------------------- the obvious */

/* Not a dictionary — a dictionary belongs in a file, and a password manager
 * that needs one has already lost. These are the passwords that sit at the
 * top of every breach summary, plus the shapes people reach for when a site
 * demands a capital and a digit. Anything here scores zero whatever its
 * length, because length bought it nothing. */
static const char *const COMMON[] = {
  "123456", "password", "123456789", "12345678", "12345", "qwerty",
  "1234567", "111111", "123123", "abc123", "1234567890", "1234",
  "password1", "iloveyou", "000000", "qwerty123", "1q2w3e", "aa12345678",
  "admin", "qwertyuiop", "654321", "555555", "lovely", "7777777",
  "888888", "princess", "dragon", "password123", "123qwe", "monkey",
  "letmein", "login", "welcome", "football", "baseball", "sunshine",
  "master", "shadow", "superman", "michael", "jennifer", "trustno1",
  "whatever", "freedom", "starwars", "computer", "qazwsx", "zxcvbnm",
  "asdfgh", "asdfghjkl", "1qaz2wsx", "q1w2e3r4", "passw0rd", "p@ssw0rd",
  "Password1", "Password123", "Welcome1", "Admin123", "letmein1",
  "secret", "changeme", "default", "guest", "test", "test123", "temp",
  "abcd1234", "a1b2c3d4", "11111111", "22222222", "00000000", "121212",
  "112233", "123321", "696969", "batman", "soccer", "hunter", "ranger",
  "buster", "jordan", "harley", "robert", "matthew", "daniel", "andrew",
  "joshua", "amanda", "ashley", "nicole", "chelsea", "hello", "charlie",
  "donald", "flower", "cookie", "pepper", "summer", "winter", "spring",
  "autumn", "orange", "purple", "yellow", "silver", "golden", "qwe123",
  "123abc", "1q2w3e4r", "1q2w3e4r5t", "zaq12wsx", "asd123", "google",
  "facebook", "internet", "liverpool", "arsenal", "chelsea1", "london",
  NULL
};

static gboolean
is_common (const char *password)
{
  for (int i = 0; COMMON[i] != NULL; i++)
    if (g_ascii_strcasecmp (password, COMMON[i]) == 0)
      return TRUE;
  return FALSE;
}

/* A single character repeated, or one step at a time through the alphabet,
 * the digits or a keyboard row. "aaaaaaaaaaaa" and "abcdefghijkl" are long
 * and varied enough to fool the arithmetic below, and are not passwords. */
static const char *const RUNS[] = {
  "abcdefghijklmnopqrstuvwxyz", "0123456789",
  "qwertyuiop", "asdfghjkl", "zxcvbnm", "qwertzuiop", "azertyuiop",
  NULL
};

static gboolean
is_a_run (const char *password)
{
  gsize len = strlen (password);
  if (len < 4)
    return FALSE;

  g_autofree char *lower = g_ascii_strdown (password, -1);

  gboolean same = TRUE;
  for (gsize i = 1; i < len && same; i++)
    if (lower[i] != lower[0])
      same = FALSE;
  if (same)
    return TRUE;

  for (int r = 0; RUNS[r] != NULL; r++) {
    if (strstr (RUNS[r], lower) != NULL)
      return TRUE;
    /* Backwards too: "987654321", "fedcba". */
    g_autofree char *back = g_strreverse (g_strdup (RUNS[r]));
    if (strstr (back, lower) != NULL)
      return TRUE;
  }
  return FALSE;
}

/* ------------------------------------------------------------- strength */

int
ly_pw_strength (const char *password)
{
  if (password == NULL || *password == '\0')
    return 0;

  gsize len = strlen (password);
  if (is_common (password) || is_a_run (password))
    return 0;

  gboolean lower = FALSE, upper = FALSE, digit = FALSE, other = FALSE;
  for (gsize i = 0; i < len; i++) {
    guchar c = (guchar) password[i];
    if (g_ascii_islower (c))      lower = TRUE;
    else if (g_ascii_isupper (c)) upper = TRUE;
    else if (g_ascii_isdigit (c)) digit = TRUE;
    else                          other = TRUE;
  }

  /* How large a set a brute force would have to walk, from what the password
   * actually uses. Generous — it assumes the attacker knows no better — but
   * it is the same generosity for every row, which is what matters for
   * sorting one against another. */
  int pool = (lower ? 26 : 0) + (upper ? 26 : 0) + (digit ? 10 : 0) +
             (other ? 33 : 0);
  if (pool < 2)
    pool = 2;

  /* Characters that only repeat something already in the password are worth
   * less than new ones. Counting distinct characters is a crude stand-in for
   * the pattern analysis a real estimator does, and it is enough to keep
   * "abababababab" below "k4Rm9x". */
  gboolean seen[256] = { FALSE };
  gsize distinct = 0;
  for (gsize i = 0; i < len; i++) {
    guchar c = (guchar) password[i];
    if (!seen[c]) { seen[c] = TRUE; distinct++; }
  }
  double effective = (double) distinct + ((double) (len - distinct) * 0.4);

  double bits = effective * log2 ((double) pool);

  if (len < 8)   return bits < 28 ? 0 : 1;
  if (bits < 30) return 0;
  if (bits < 42) return 1;
  if (bits < 60) return 2;
  if (bits < 80) return 3;
  return 4;
}

const char *
ly_pw_strength_label (int score)
{
  switch (score) {
    case 0:  return "Very weak";
    case 1:  return "Weak";
    case 2:  return "Fair";
    case 3:  return "Strong";
    default: return "Very strong";
  }
}

gboolean
ly_pw_is_weak (const char *password)
{
  if (password == NULL || *password == '\0')
    return TRUE;
  /* Chrome's rule: short, or one everybody uses. Both land on 0 or 1 here. */
  return strlen (password) < 8 || ly_pw_strength (password) <= 1;
}

/* ------------------------------------------------------------ generation */

#define LY_PW_GENERATED_LEN 20

static const char LOWER[]  = "abcdefghijkmnopqrstuvwxyz";   /* no l */
static const char UPPER[]  = "ABCDEFGHJKLMNPQRSTUVWXYZ";    /* no I, O */
static const char DIGITS[] = "23456789";                    /* no 0, 1 */
static const char SYMS[]   = "!@#$%^&*-_=+?";

/* An index in [0, n), without the bias that taking a byte modulo n would
 * introduce: anything landing in the short final stretch is thrown away and
 * a new byte asked for. */
static gboolean
pick (gsize n, gsize *out)
{
  if (n == 0 || n > 256)
    return FALSE;

  gsize limit = 256 - (256 % n);
  for (int tries = 0; tries < 64; tries++) {
    guint8 byte;
    if (RAND_bytes (&byte, 1) != 1)
      return FALSE;
    if ((gsize) byte < limit) {
      *out = (gsize) byte % n;
      return TRUE;
    }
  }
  return FALSE;
}

char *
ly_pw_generate (void)
{
  g_autofree char *all = g_strconcat (LOWER, UPPER, DIGITS, SYMS, NULL);
  const char *classes[] = { LOWER, UPPER, DIGITS, SYMS };

  g_autofree char *out = g_malloc0 (LY_PW_GENERATED_LEN + 1);

  /* One of each class first, so the result always satisfies the rule sites
   * state and Chrome's generator also guarantees. */
  gsize at = 0;
  for (guint c = 0; c < G_N_ELEMENTS (classes); c++, at++) {
    gsize index;
    if (!pick (strlen (classes[c]), &index))
      return NULL;
    out[at] = classes[c][index];
  }

  for (; at < LY_PW_GENERATED_LEN; at++) {
    gsize index;
    if (!pick (strlen (all), &index))
      return NULL;
    out[at] = all[index];
  }

  /* Otherwise every password would start lower, upper, digit, symbol. */
  for (gsize i = LY_PW_GENERATED_LEN - 1; i > 0; i--) {
    gsize j;
    if (!pick (i + 1, &j))
      return NULL;
    char swap = out[i];
    out[i] = out[j];
    out[j] = swap;
  }

  return g_steal_pointer (&out);
}

/* -------------------------------------------------------------- findings */

void
ly_pw_finding_free (LyPwFinding *finding)
{
  if (finding == NULL)
    return;
  g_free (finding->origin);
  g_free (finding->username);
  g_free (finding);
}

static int
worst_first (gconstpointer a, gconstpointer b)
{
  const LyPwFinding *x = *(const LyPwFinding * const *) a;
  const LyPwFinding *y = *(const LyPwFinding * const *) b;

  /* Breached before reused before merely weak: the order they have to be
   * dealt with in, which is the order they should be read in. */
  int rank_x = (x->issues & LY_PW_LEAKED) ? 0 : (x->issues & LY_PW_REUSED) ? 1 : 2;
  int rank_y = (y->issues & LY_PW_LEAKED) ? 0 : (y->issues & LY_PW_REUSED) ? 1 : 2;
  if (rank_x != rank_y)
    return rank_x - rank_y;

  if (x->leaked_count != y->leaked_count)
    return x->leaked_count > y->leaked_count ? -1 : 1;
  if (x->shared_with != y->shared_with)
    return x->shared_with > y->shared_with ? -1 : 1;
  if (x->strength != y->strength)
    return x->strength - y->strength;
  return g_strcmp0 (x->origin, y->origin);
}

/* Reuse is counted per site, not per row: the same password under two
 * usernames on one site is one account with a spare, not two sites sharing a
 * secret, and calling that reuse would report something nobody can act on. */
static char *
site_of (const char *origin)
{
  char *base = ly_uri_base_domain (origin);
  return base != NULL ? base : g_strdup (origin ?: "");
}

GPtrArray *
ly_pwcheck_local (GPtrArray *credentials)
{
  GPtrArray *findings =
    g_ptr_array_new_with_free_func ((GDestroyNotify) ly_pw_finding_free);
  if (credentials == NULL)
    return findings;

  /* password -> set of sites using it */
  g_autoptr (GHashTable) sites =
    g_hash_table_new_full (g_str_hash, g_str_equal, g_free,
                           (GDestroyNotify) g_hash_table_unref);

  for (guint i = 0; i < credentials->len; i++) {
    LyCredential *credential = g_ptr_array_index (credentials, i);
    if (credential->password == NULL || *credential->password == '\0')
      continue;

    GHashTable *set = g_hash_table_lookup (sites, credential->password);
    if (set == NULL) {
      set = g_hash_table_new_full (g_str_hash, g_str_equal, g_free, NULL);
      g_hash_table_insert (sites, g_strdup (credential->password), set);
    }
    g_hash_table_add (set, site_of (credential->origin));
  }

  for (guint i = 0; i < credentials->len; i++) {
    LyCredential *credential = g_ptr_array_index (credentials, i);
    if (credential->password == NULL || *credential->password == '\0')
      continue;

    GHashTable *set = g_hash_table_lookup (sites, credential->password);
    guint shared = set != NULL ? g_hash_table_size (set) - 1 : 0;

    guint issues = 0;
    if (ly_pw_is_weak (credential->password))
      issues |= LY_PW_WEAK;
    if (shared > 0)
      issues |= LY_PW_REUSED;
    if (issues == 0)
      continue;

    LyPwFinding *finding = g_new0 (LyPwFinding, 1);
    finding->origin      = g_strdup (credential->origin);
    finding->username    = g_strdup (credential->username);
    finding->issues      = issues;
    finding->strength    = ly_pw_strength (credential->password);
    finding->shared_with = shared;
    g_ptr_array_add (findings, finding);
  }

  g_ptr_array_sort (findings, worst_first);
  return findings;
}

/* --------------------------------------------------------- the leak check */

guint
ly_pwcheck_count_in_range (const char *body, const char *suffix)
{
  if (body == NULL || suffix == NULL || *suffix == '\0')
    return 0;

  gsize len = strlen (suffix);

  for (const char *line = body; line != NULL && *line != '\0'; ) {
    const char *end = strpbrk (line, "\r\n");
    gsize length = end != NULL ? (gsize) (end - line) : strlen (line);

    if (length > len && line[len] == ':' &&
        g_ascii_strncasecmp (line, suffix, len) == 0) {
      /* Padding entries are real-looking hashes with a count of zero, which
       * reads as "not in the corpus" without any special case. */
      guint64 count = g_ascii_strtoull (line + len + 1, NULL, 10);
      return count > G_MAXUINT ? G_MAXUINT : (guint) count;
    }

    if (end == NULL)
      break;
    line = end + strspn (end, "\r\n");
  }
  return 0;
}

#define LY_RANGE_URL "https://api.pwnedpasswords.com/range/"
/* Enough for anybody's keyring, and a stop for a store that has somehow come
 * to hold thousands: one request per distinct prefix, run one at a time. */
#define LY_MAX_PREFIXES 400

typedef struct {
  char        *suffix;     /* the other 35 characters of the SHA-1 */
  LyPwFinding *finding;    /* borrowed from the findings array      */
} Row;

static void
row_free (Row *row)
{
  g_free (row->suffix);
  g_free (row);
}

typedef struct {
  GPtrArray    *findings;   /* owned until the callback has had it */
  GPtrArray    *prefixes;   /* char*, in request order             */
  GHashTable   *rows;       /* char* prefix -> GPtrArray of Row*   */
  guint         at;         /* index into prefixes                 */
  guint         failed;     /* prefixes that could not be fetched  */
  SoupSession  *http;
  GCancellable *cancel;
  LyPwCheckFn   done;
  gpointer      user_data;
} Check;

static void check_next (Check *check);

static void
check_finish (Check *check, const char *error)
{
  if (check->findings != NULL)
    g_ptr_array_sort (check->findings, worst_first);

  if (check->done != NULL)
    check->done (error != NULL ? NULL : check->findings, error, check->user_data);

  g_clear_pointer (&check->findings, g_ptr_array_unref);
  g_clear_pointer (&check->prefixes, g_ptr_array_unref);
  g_clear_pointer (&check->rows, g_hash_table_unref);
  g_clear_object (&check->http);
  g_clear_object (&check->cancel);
  g_free (check);
}

static void
on_range_done (GObject *source, GAsyncResult *result, gpointer data)
{
  Check *check = data;
  g_autoptr (GError) error = NULL;

  g_autoptr (GBytes) body =
    soup_session_send_and_read_finish (SOUP_SESSION (source), result, &error);

  if (g_error_matches (error, G_IO_ERROR, G_IO_ERROR_CANCELLED)) {
    check_finish (check, NULL);
    return;
  }

  const char *prefix = g_ptr_array_index (check->prefixes, check->at);
  GPtrArray *rows = g_hash_table_lookup (check->rows, prefix);

  if (body == NULL) {
    /* Those rows stay unchecked, which is honest; the rest of the run still
     * has something to say. */
    check->failed++;
    g_message ("password checkup: %s: %s", prefix,
               error != NULL ? error->message : "no answer");
  } else if (rows != NULL) {
    gsize size = 0;
    const char *text = g_bytes_get_data (body, &size);
    /* The answer is hex and colons; a NUL in it would be the parser's
     * problem, so it is copied into a string rather than read in place. */
    g_autofree char *answer = g_strndup (text ?: "", size);

    for (guint i = 0; i < rows->len; i++) {
      Row *row = g_ptr_array_index (rows, i);
      guint count = ly_pwcheck_count_in_range (answer, row->suffix);
      if (count == 0)
        continue;
      row->finding->issues |= LY_PW_LEAKED;
      row->finding->leaked_count = count;
    }
  }

  check->at++;
  check_next (check);
}

static void
check_next (Check *check)
{
  if (g_cancellable_is_cancelled (check->cancel)) {
    check_finish (check, NULL);
    return;
  }

  if (check->at >= check->prefixes->len) {
    if (check->failed > 0 && check->failed == check->prefixes->len)
      check_finish (check, "Could not reach the breach database.");
    else
      check_finish (check, NULL);
    return;
  }

  const char *prefix = g_ptr_array_index (check->prefixes, check->at);
  g_autofree char *url = g_strconcat (LY_RANGE_URL, prefix, NULL);

  g_autoptr (SoupMessage) message = soup_message_new ("GET", url);
  if (message == NULL) {
    check->failed++;
    check->at++;
    check_next (check);
    return;
  }

  /* Padding makes every answer about the same size, so the length of what
   * comes back says nothing about how many hashes share this prefix. */
  soup_message_headers_append (soup_message_get_request_headers (message),
                               "Add-Padding", "true");

  soup_session_send_and_read_async (check->http, message, G_PRIORITY_LOW,
                                    check->cancel, on_range_done, check);
}

static char *
finding_key (const char *origin, const char *username)
{
  return g_strdup_printf ("%s\n%s", origin ?: "", username ?: "");
}

void
ly_pwcheck_breaches (GPtrArray *credentials, GPtrArray *findings,
                     GCancellable *cancel, LyPwCheckFn done, gpointer user_data)
{
  g_return_if_fail (findings != NULL);

  Check *check = g_new0 (Check, 1);
  check->findings  = g_ptr_array_ref (findings);
  check->prefixes  = g_ptr_array_new_with_free_func (g_free);
  check->rows      = g_hash_table_new_full (g_str_hash, g_str_equal, g_free,
                                            (GDestroyNotify) g_ptr_array_unref);
  check->cancel    = cancel != NULL ? g_object_ref (cancel) : g_cancellable_new ();
  check->done      = done;
  check->user_data = user_data;

  /* So a credential that turns out to be breached but was otherwise fine can
   * be given a finding rather than dropped. */
  g_autoptr (GHashTable) index =
    g_hash_table_new_full (g_str_hash, g_str_equal, g_free, NULL);
  for (guint i = 0; i < findings->len; i++) {
    LyPwFinding *finding = g_ptr_array_index (findings, i);
    g_hash_table_insert (index, finding_key (finding->origin, finding->username),
                         finding);
  }

  /* One request per distinct prefix, however many logins share it. */
  for (guint i = 0; credentials != NULL && i < credentials->len; i++) {
    LyCredential *credential = g_ptr_array_index (credentials, i);
    if (credential->password == NULL || *credential->password == '\0')
      continue;

    g_autofree char *hash =
      g_compute_checksum_for_string (G_CHECKSUM_SHA1, credential->password, -1);
    if (hash == NULL || strlen (hash) != 40)
      continue;
    g_autofree char *upper = g_ascii_strup (hash, -1);

    g_autofree char *prefix = g_strndup (upper, 5);
    GPtrArray *rows = g_hash_table_lookup (check->rows, prefix);
    if (rows == NULL) {
      if (check->prefixes->len >= LY_MAX_PREFIXES)
        continue;
      rows = g_ptr_array_new_with_free_func ((GDestroyNotify) row_free);
      g_hash_table_insert (check->rows, g_strdup (prefix), rows);
      g_ptr_array_add (check->prefixes, g_strdup (prefix));
    }

    g_autofree char *key = finding_key (credential->origin, credential->username);
    LyPwFinding *finding = g_hash_table_lookup (index, key);
    if (finding == NULL) {
      finding           = g_new0 (LyPwFinding, 1);
      finding->origin   = g_strdup (credential->origin);
      finding->username = g_strdup (credential->username);
      finding->strength = ly_pw_strength (credential->password);
      g_ptr_array_add (findings, finding);
      g_hash_table_insert (index, g_steal_pointer (&key), finding);
    }

    Row *row = g_new0 (Row, 1);
    row->suffix  = g_strdup (upper + 5);
    row->finding = finding;
    g_ptr_array_add (rows, row);
  }

  if (check->prefixes->len == 0) {
    check_finish (check, NULL);
    return;
  }

  check->http = soup_session_new_with_options (
    "timeout",      (guint) 20,
    "user-agent",   "Lyndon/" LYNDON_VERSION " (password checkup)",
    "max-conns",    (guint) 2,
    NULL);

  check_next (check);
}
