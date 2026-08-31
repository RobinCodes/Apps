/* test-block.c — the matcher against the lists the app actually ships.
 *
 *   make -f win32/Makefile check
 *
 * The Linux build tests its rule compiler (tests/test-abp.c) because WebKit
 * does the matching. Here the matching is ours, so this tests that instead:
 * that the shipped lists block what they are for, that a normal page is left
 * alone, and that the label-walking cannot be fooled by a lookalike host.
 */

#include "block.h"

#include <stdio.h>
#include <string.h>

static int failures = 0;
static int checks = 0;

static void
expect (gboolean got, gboolean want, const char *what)
{
  checks++;
  if (got == want) {
    printf ("  ok    %s\n", what);
  } else {
    printf ("  FAIL  %s (got %s, wanted %s)\n", what,
            got ? "blocked" : "allowed", want ? "blocked" : "allowed");
    failures++;
  }
}

static void
check (LyBlock *b, const char *url, const char *page, LyResourceKind kind,
       gboolean want, const char *what)
{
  expect (ly_block_should_block (b, url, page, kind), want, what);
}

int
main (int argc, char **argv)
{
  const char *data = (argc > 1) ? argv[1] : "data";

  LyBlock *b = ly_block_new ();
  ly_block_load_builtin (b, NULL, data);

  const LyBlockStats *st = ly_block_stats (b);
  printf ("loaded: %u domain rules, %u patterns, %u exceptions, "
          "%u cosmetic, %u unparsed\n\n",
          st->domains, st->patterns, st->exceptions, st->cosmetics, st->invalid);

  if (st->domains == 0) {
    printf ("no rules loaded — is the data directory right? (%s)\n", data);
    return 1;
  }

  const char *page = "https://news.example.org/story";

  printf ("things the lists exist to stop\n");
  check (b, "https://doubleclick.net/pixel.gif", page, LY_RES_IMAGE, TRUE,
         "doubleclick.net");
  check (b, "https://ads.doubleclick.net/x.js", page, LY_RES_SCRIPT, TRUE,
         "a subdomain of a blocked host");
  check (b, "https://googlesyndication.com/pagead/js/adsbygoogle.js", page,
         LY_RES_SCRIPT, TRUE, "googlesyndication.com");
  check (b, "https://cdn.cookielaw.org/consent.js", page, LY_RES_SCRIPT, TRUE,
         "a consent management platform");

  printf ("\nthings it must not stop\n");
  check (b, "https://news.example.org/style.css", page, LY_RES_STYLESHEET, FALSE,
         "the page's own stylesheet");
  check (b, "https://en.wikipedia.org/wiki/Advertising", page, LY_RES_DOCUMENT,
         FALSE, "an article that merely mentions advertising");
  check (b, "https://notdoubleclick.net/a.js", page, LY_RES_SCRIPT, FALSE,
         "a lookalike host, not a subdomain");
  check (b, "https://mydoubleclick.net.example.com/a.js", page, LY_RES_SCRIPT,
         FALSE, "a blocked name buried mid-host");
  check (b, "data:text/html,hello", page, LY_RES_DOCUMENT, FALSE,
         "a data: URL the page made itself");

  printf ("\nrules added by hand\n");
  ly_block_add_text (b,
                     "||tracker.test^\n"
                     "/banner-ad.\n"
                     "@@||tracker.test/allowed.js\n"
                     "||thirdonly.test^$third-party\n"
                     "example.net##.promo-box\n");
  check (b, "https://tracker.test/beacon", page, LY_RES_XHR, TRUE,
         "a hand-added domain rule");
  check (b, "https://cdn.example.com/img/banner-ad.png", page, LY_RES_IMAGE,
         TRUE, "a substring pattern");
  check (b, "https://tracker.test/allowed.js", page, LY_RES_SCRIPT, FALSE,
         "an @@ exception beats the block");
  check (b, "https://thirdonly.test/x.js", page, LY_RES_SCRIPT, TRUE,
         "$third-party on a third-party request");
  check (b, "https://thirdonly.test/x.js", "https://thirdonly.test/page",
         LY_RES_SCRIPT, FALSE, "$third-party on a first-party request");

  printf ("\ncosmetic filtering\n");
  {
    char *css = ly_block_cosmetic_css (b, "www.example.net");
    checks++;
    if (css && strstr (css, ".promo-box")) {
      printf ("  ok    a domain selector reaches a subdomain\n");
    } else {
      printf ("  FAIL  a domain selector reaches a subdomain (got %s)\n",
              css ? css : "nothing");
      failures++;
    }
    g_free (css);

    css = ly_block_cosmetic_css (b, "unrelated.test");
    checks++;
    if (css == NULL || strstr (css, ".promo-box") == NULL) {
      printf ("  ok    and not an unrelated host\n");
    } else {
      printf ("  FAIL  and not an unrelated host (got %s)\n", css);
      failures++;
    }
    g_free (css);
  }

  printf ("\nblocked %llu requests over %d checks\n",
          (unsigned long long) st->blocked, checks);
  ly_block_free (b);

  if (failures) {
    printf ("\n%d of %d failed\n", failures, checks);
    return 1;
  }
  printf ("all %d passed\n", checks);
  return 0;
}
