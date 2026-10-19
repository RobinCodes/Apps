/* test-pwcheck.c — the password checkup's arithmetic.
 *
 *   make check
 *
 * Three things here are worth a test and cannot be had from running the
 * browser: the strength estimate's ordering, the generator's guarantees, and
 * the parse of what the breach service answers with — which is the one piece
 * of this that reads something off the network and the one place a mistake
 * would quietly report every password as safe.
 */

#include "pwcheck.h"

#include <stdio.h>
#include <string.h>

static int failures = 0;
static int checks = 0;

static void
expect (gboolean got, const char *what)
{
  checks++;
  if (got) {
    printf ("  ok    %s\n", what);
  } else {
    printf ("  FAIL  %s\n", what);
    failures++;
  }
}

static void
expect_uint (guint got, guint want, const char *what)
{
  checks++;
  if (got == want) {
    printf ("  ok    %s\n", what);
  } else {
    printf ("  FAIL  %s (got %u, wanted %u)\n", what, got, want);
    failures++;
  }
}

static LyCredential *
login (const char *origin, const char *username, const char *password)
{
  return ly_credential_new (origin, username, password);
}

static LyPwFinding *
finding_for (GPtrArray *findings, const char *origin)
{
  for (guint i = 0; i < findings->len; i++) {
    LyPwFinding *finding = g_ptr_array_index (findings, i);
    if (g_strcmp0 (finding->origin, origin) == 0)
      return finding;
  }
  return NULL;
}

int
main (void)
{
  printf ("strength\n");
  expect_uint ((guint) ly_pw_strength ("password"), 0, "the word itself scores nothing");
  expect_uint ((guint) ly_pw_strength ("Password1"), 0, "nor does it with the capital and the digit sites ask for");
  expect_uint ((guint) ly_pw_strength ("123456"), 0, "nor the most common password there is");
  expect_uint ((guint) ly_pw_strength ("aaaaaaaaaaaaaaaa"), 0, "one character sixteen times is not sixteen characters");
  expect_uint ((guint) ly_pw_strength ("abcdefghijkl"), 0, "nor is walking the alphabet");
  expect_uint ((guint) ly_pw_strength ("987654321"), 0, "backwards through the digits is the same walk");
  expect (ly_pw_strength ("k4Rm9x") > 0, "a short random password beats a long pattern");
  expect (ly_pw_strength ("Tr0ub4dour&3xyz") >= 3, "mixed and long scores well");
  expect (ly_pw_strength ("correcthorsebatterystaple") >= 3, "so does a long passphrase");
  expect_uint ((guint) ly_pw_strength (""), 0, "nothing scores nothing");
  expect_uint ((guint) ly_pw_strength (NULL), 0, "and so does no password at all");

  printf ("\nweakness, which is what Checkup lists\n");
  expect (ly_pw_is_weak ("hunter2"), "under eight characters is weak whatever is in it");
  expect (ly_pw_is_weak ("qwertyuiop"), "a keyboard row is weak at any length");
  expect (!ly_pw_is_weak ("Tr0ub4dour&3xyz"), "a real password is not");
  expect (ly_pw_is_weak (NULL), "and no password is as weak as it gets");

  printf ("\nthe generator\n");
  gboolean lengths = TRUE, classes = TRUE, distinct = TRUE;
  g_autofree char *first = NULL;
  for (int i = 0; i < 200; i++) {
    g_autofree char *password = ly_pw_generate ();
    if (password == NULL) {
      printf ("  skip  no random source on this machine\n");
      lengths = classes = distinct = FALSE;
      break;
    }
    if (strlen (password) != 20)
      lengths = FALSE;

    gboolean lower = FALSE, upper = FALSE, digit = FALSE, other = FALSE;
    for (const char *c = password; *c != '\0'; c++) {
      if (g_ascii_islower (*c))      lower = TRUE;
      else if (g_ascii_isupper (*c)) upper = TRUE;
      else if (g_ascii_isdigit (*c)) digit = TRUE;
      else                           other = TRUE;
    }
    if (!(lower && upper && digit && other))
      classes = FALSE;
    if (ly_pw_is_weak (password))
      distinct = FALSE;

    if (first == NULL)
      first = g_strdup (password);
    else if (g_strcmp0 (first, password) == 0)
      distinct = FALSE;          /* twice the same in 200 is not random */
  }
  expect (lengths, "every generated password is twenty characters");
  expect (classes, "and has a lower, an upper, a digit and a symbol in it");
  expect (distinct, "and is strong, and not the same one every time");

  printf ("\nreuse, counted per site\n");
  {
    g_autoptr (GPtrArray) logins = ly_credentials_new ();
    g_ptr_array_add (logins, login ("https://a.example", "me", "Tr0ub4dour&3xyz"));
    g_ptr_array_add (logins, login ("https://b.example", "me", "Tr0ub4dour&3xyz"));
    g_ptr_array_add (logins, login ("https://c.example", "me", "Zx8!kQ2@wN4%vB7"));

    g_autoptr (GPtrArray) findings = ly_pwcheck_local (logins);
    LyPwFinding *shared = finding_for (findings, "https://a.example");
    expect (shared != NULL, "a password on two sites is reported");
    if (shared != NULL) {
      expect ((shared->issues & LY_PW_REUSED) != 0, "and it is reported as reuse");
      expect_uint (shared->shared_with, 1, "shared with exactly one other site");
    }
    expect (finding_for (findings, "https://c.example") == NULL,
            "a strong password used once is not reported at all");
  }
  {
    /* Two accounts of one person on one site. Chrome does not call this
     * reuse, and neither should this: there is nothing to act on. */
    g_autoptr (GPtrArray) logins = ly_credentials_new ();
    g_ptr_array_add (logins, login ("https://mail.example.com", "me", "Zx8!kQ2@wN4%vB7"));
    g_ptr_array_add (logins, login ("https://www.example.com", "work", "Zx8!kQ2@wN4%vB7"));

    g_autoptr (GPtrArray) findings = ly_pwcheck_local (logins);
    expect_uint (findings->len, 0, "two accounts on one site sharing a password is not reuse");
  }
  {
    g_autoptr (GPtrArray) logins = ly_credentials_new ();
    g_ptr_array_add (logins, login ("https://a.example", "me", "hunter2"));

    g_autoptr (GPtrArray) findings = ly_pwcheck_local (logins);
    expect_uint (findings->len, 1, "a weak password used once is still reported");
    if (findings->len == 1)
      expect ((((LyPwFinding *) g_ptr_array_index (findings, 0))->issues & LY_PW_WEAK) != 0,
              "as weak");
  }

  printf ("\nreading the breach service's answer\n");
  /* Real shape: the other 35 characters of the SHA-1, a colon, and how many
   * times it has been seen. CRLF, because that is what comes back. */
  const char *body =
    "0018A45C4D1DEF81644B54AB7F969B88D65:1\r\n"
    "00D4F6E8FA6EECAD2A3AA415EEC418D38EC:2\r\n"
    "011053FD0102E94D6AE2F8B83D76FAF94F6:0\r\n";
  expect_uint (ly_pwcheck_count_in_range (body, "0018A45C4D1DEF81644B54AB7F969B88D65"), 1,
               "the first line is found");
  expect_uint (ly_pwcheck_count_in_range (body, "00D4F6E8FA6EECAD2A3AA415EEC418D38EC"), 2,
               "and so is a later one, with its count");
  expect_uint (ly_pwcheck_count_in_range (body, "0018a45c4d1def81644b54ab7f969b88d65"), 1,
               "the comparison ignores case");
  expect_uint (ly_pwcheck_count_in_range (body, "011053FD0102E94D6AE2F8B83D76FAF94F6"), 0,
               "a padding entry's zero count reads as not breached");
  expect_uint (ly_pwcheck_count_in_range (body, "FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF"), 0,
               "a hash that is not there is not there");
  expect_uint (ly_pwcheck_count_in_range (body, "0018A45C4D1DEF81644B54AB7F969B88D"), 0,
               "a prefix of a line is not a match");
  expect_uint (ly_pwcheck_count_in_range ("", "0018A45C4D1DEF81644B54AB7F969B88D65"), 0,
               "an empty answer breaches nothing");
  expect_uint (ly_pwcheck_count_in_range (NULL, "ABC"), 0, "nor does no answer");
  expect_uint (ly_pwcheck_count_in_range (body, NULL), 0, "nor no hash to look for");
  expect_uint (ly_pwcheck_count_in_range ("0018A45C4D1DEF81644B54AB7F969B88D65:7", "0018A45C4D1DEF81644B54AB7F969B88D65"),
               7, "a single line with no newline at the end still parses");

  printf ("\n%d checks, %d failed\n", checks, failures);
  if (failures == 0)
    printf ("all %d passed\n", checks);
  return failures == 0 ? 0 : 1;
}
