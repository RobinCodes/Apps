/* test-util.c — the URL handling both builds share.
 *
 *   make check                      (Linux)
 *   make -f win32/Makefile check    (Windows)
 *
 * util.c is compiled into the GTK build and the Win32 one alike, and it is
 * what decides whether what you typed is an address or a search, which site
 * a request belongs to, and what the address bar shows. None of that touches
 * a toolkit, so it can be tested on either platform — and it is the piece
 * most worth testing, because getting it wrong sends someone's typing to a
 * search engine, or their cookies to the wrong first party.
 */

#include "lyndon.h"

#include <stdio.h>
#include <string.h>

static int failures = 0;
static int checks = 0;

static void
expect_str (char *got, const char *want, const char *what)
{
  checks++;
  const char *shown = got ? got : "(null)";
  if ((got == NULL && want == NULL) ||
      (got != NULL && want != NULL && strcmp (got, want) == 0)) {
    printf ("  ok    %s\n", what);
  } else {
    printf ("  FAIL  %s (got \"%s\", wanted \"%s\")\n", what, shown,
            want ? want : "(null)");
    failures++;
  }
  g_free (got);
}

static void
expect_bool (gboolean got, gboolean want, const char *what)
{
  checks++;
  if (got == want) {
    printf ("  ok    %s\n", what);
  } else {
    printf ("  FAIL  %s (got %s, wanted %s)\n", what,
            got ? "true" : "false", want ? "true" : "false");
    failures++;
  }
}

int
main (void)
{
  printf ("\naddress or search\n");
  expect_bool (ly_looks_like_url ("https://example.com"), TRUE, "an explicit scheme");
  expect_bool (ly_looks_like_url ("example.com"), TRUE, "a bare host with a TLD");
  expect_bool (ly_looks_like_url ("example.com/path?q=1"), TRUE, "a host with a path");
  expect_bool (ly_looks_like_url ("localhost:8080"), TRUE, "localhost and a port");
  expect_bool (ly_looks_like_url ("127.0.0.1:5000"), TRUE, "a loopback address");
  /* The distinction the whole function exists for. */
  expect_bool (ly_looks_like_url ("how do I make bread"), FALSE, "a phrase with spaces");
  expect_bool (ly_looks_like_url ("foo/bar"), FALSE, "a path with no dot before it");
  expect_bool (ly_looks_like_url ("a.b"), FALSE, "a one-letter TLD is not one");
  expect_bool (ly_looks_like_url (""), FALSE, "an empty string");
  expect_bool (ly_looks_like_url (NULL), FALSE, "no string at all");

  printf ("\nwhat gets loaded\n");
  expect_str (ly_normalise_input ("example.com", NULL),
              "https://example.com", "a bare host gains https");
  expect_str (ly_normalise_input ("  https://example.com  ", NULL),
              "https://example.com", "surrounding space is trimmed");
  expect_str (ly_normalise_input ("hello world", NULL),
              "https://duckduckgo.com/?q=hello%20world",
              "a phrase becomes a search");
  expect_str (ly_normalise_input ("hello", "https://example.org/find?s=%s"),
              "https://example.org/find?s=hello",
              "the configured search engine is used");
  /* A template with no %s in it cannot carry the query, so the default has to
   * take over rather than silently searching for nothing. */
  expect_str (ly_normalise_input ("hello", "https://example.org/broken"),
              "https://duckduckgo.com/?q=hello",
              "a search template with no %s falls back");
  expect_str (ly_normalise_input ("   ", NULL), NULL, "blank input loads nothing");

  printf ("\nwhich site a request belongs to\n");
  expect_str (ly_uri_host ("https://www.example.com/a/b"), "www.example.com", "the host");
  expect_str (ly_uri_host ("not a uri at all"), NULL, "nothing from a non-URI");
  expect_str (ly_uri_base_domain ("https://www.example.com/x"), "example.com",
              "www is not a site of its own");
  expect_str (ly_uri_base_domain ("https://example.com"), "example.com",
              "a two-label host is already the site");
  /* The reason the multi-TLD table exists: without it every .co.uk site would
   * be first-party to every other one. */
  expect_str (ly_uri_base_domain ("https://news.bbc.co.uk/story"), "bbc.co.uk",
              "co.uk is a suffix, not a site");
  expect_str (ly_uri_base_domain ("https://a.b.example.co.uk/"), "example.co.uk",
              "and deeper subdomains still land on it");
  expect_str (ly_uri_base_domain ("http://127.0.0.1:8080/x"), "127.0.0.1",
              "an IP literal is left alone");

  printf ("\nwhat the address bar says\n");
  expect_bool (ly_uri_is_secure ("https://example.com"), TRUE, "https is secure");
  expect_bool (ly_uri_is_secure ("http://example.com"), FALSE, "http is not");
  expect_bool (ly_uri_is_secure ("lyndon:settings"), TRUE, "an internal page is");
  expect_bool (ly_uri_is_internal ("lyndon:settings"), TRUE, "and is internal");
  expect_bool (ly_uri_is_internal ("https://example.com"), FALSE, "a real site is not");
  expect_str (ly_pretty_uri ("https://www.example.com/"), "example.com",
              "scheme, www and a lone trailing slash all go");
  expect_str (ly_pretty_uri ("https://example.com/a/b"), "example.com/a/b",
              "a real path is kept, slashes and all");
  expect_str (ly_pretty_uri ("https://example.com/a%20b"), "example.com/a b",
              "percent escapes are read back");
  expect_str (ly_pretty_uri ("lyndon:settings"), "lyndon:settings",
              "an internal page is shown as it is");
  expect_str (ly_pretty_uri (""), "", "and nothing stays nothing");

  printf ("\nsizes\n");
  expect_str (ly_format_size (0), "0 B", "zero");
  expect_str (ly_format_size (512), "512 B", "bytes have no decimal");
  expect_str (ly_format_size (1024), "1.0 kB", "a kilobyte does");
  expect_str (ly_format_size (1536), "1.5 kB", "and a half");
  expect_str (ly_format_size (1048576), "1.0 MB", "a megabyte");

  printf ("\nescaping a string into JavaScript\n");
  /* These land inside a double-quoted JS string literal in tab.c, filling a
   * login form. Anything that can close that literal early is the whole
   * password manager handing the page a script. */
  expect_str (ly_escape_js_string ("plain"), "plain", "ordinary text is untouched");
  expect_str (ly_escape_js_string ("a\"b"), "a\\\"b", "a double quote cannot close it");
  expect_str (ly_escape_js_string ("a\\b"), "a\\\\b", "nor can a backslash escape the closer");
  expect_str (ly_escape_js_string ("a'b"), "a\\'b", "a single quote is escaped too");
  expect_str (ly_escape_js_string ("a\nb"), "a\\nb", "a newline cannot break the line");
  expect_str (ly_escape_js_string ("</script>"), "\\x3c/script\\x3e",
              "and it cannot end the script element");
  expect_str (ly_escape_js_string (NULL), "", "no string is an empty one");

  printf ("\n%d checks, %d failed\n", checks, failures);
  if (failures == 0)
    printf ("all %d passed\n", checks);
  return failures == 0 ? 0 : 1;
}
