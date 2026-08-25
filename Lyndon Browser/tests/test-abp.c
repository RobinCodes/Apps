/* test-abp.c — unit tests for the filter-list compiler.
 * Build with `make check`. */

#include "abp.h"

/* Mirrors SEP_CLASS in abp.c. */
#define SEP_CLASS_TEST "[^-_.%a-zA-Z0-9]"

#include <stdio.h>
#include <string.h>

static int failures = 0;
static int checks   = 0;

static void
expect_regex (const char *pattern, const char *want)
{
  checks++;
  char *got = ly_abp_pattern_to_regex (pattern);
  const char *shown = got ? got : "(null)";
  const char *expected = want ? want : "(null)";
  if (g_strcmp0 (got, want) != 0) {
    failures++;
    printf ("  FAIL  %-34s -> %s\n        expected            %s\n",
            pattern, shown, expected);
  } else {
    printf ("  ok    %-34s -> %s\n", pattern, shown);
  }
  g_free (got);
}

static void
expect_contains (const char *label, const char *haystack, const char *needle)
{
  checks++;
  if (strstr (haystack, needle) == NULL) {
    failures++;
    printf ("  FAIL  %s\n        missing: %s\n        in: %.200s\n",
            label, needle, haystack);
  } else {
    printf ("  ok    %s\n", label);
  }
}

static void
expect_absent (const char *label, const char *haystack, const char *needle)
{
  checks++;
  if (strstr (haystack, needle) != NULL) {
    failures++;
    printf ("  FAIL  %s\n        unexpectedly present: %s\n", label, needle);
  } else {
    printf ("  ok    %s\n", label);
  }
}

static void
expect_uint (const char *label, guint got, guint want)
{
  checks++;
  if (got != want) {
    failures++;
    printf ("  FAIL  %s: got %u, expected %u\n", label, got, want);
  } else {
    printf ("  ok    %s = %u\n", label, got);
  }
}

static char *
compile (const char *text, LyAbpStats *stats)
{
  LyAbp *abp = ly_abp_new ();
  ly_abp_add_text (abp, text);
  char *json = ly_abp_finish (abp, stats);
  ly_abp_free (abp);
  return json;
}

int
main (void)
{
  printf ("\n-- pattern translation --\n");
  expect_regex ("||doubleclick.net^",
                "^https?://([^/]+\\.)?doubleclick\\.net" SEP_CLASS_TEST);
  expect_regex ("||example.com/ads/*",
                "^https?://([^/]+\\.)?example\\.com/ads/.*");
  expect_regex ("|http://tracker.io/",  "^http://tracker\\.io/");
  expect_regex ("/banner/*.gif",        "/banner/.*\\.gif");
  expect_regex ("swf|",                 "swf$");
  expect_regex ("/^https?:\\/\\/ads\\./", "^https?:\\/\\/ads\\.");
  /* Unsupported regex constructs must be refused, not passed through. */
  expect_regex ("/(?=evil)/",           NULL);
  expect_regex ("*",                    NULL);

  printf ("\n-- options --\n");
  LyAbpStats s;
  char *json = compile ("||ads.example.com^$third-party,script,image", &s);
  expect_contains ("third-party load type", json, "\"load-type\":[\"third-party\"]");
  expect_contains ("script resource type",  json, "\"script\"");
  expect_contains ("image resource type",   json, "\"image\"");
  expect_uint     ("blocks", s.blocks, 1);
  g_free (json);

  json = compile ("||cdn.example.com^$domain=news.com|~vip.news.com", &s);
  expect_contains ("if-domain carries the * prefix", json, "\"if-domain\":[\"*news.com\"]");
  expect_absent   ("unless-domain dropped alongside if-domain", json, "unless-domain");
  g_free (json);

  json = compile ("||tracker.io^$xmlhttprequest,websocket", &s);
  expect_contains ("xhr and websocket both fold to raw", json, "\"raw\"");
  g_free (json);

  printf ("\n-- unsupported rules are dropped, not mistranslated --\n");
  json = compile ("||x.com^$redirect=noop.js\n"
                  "||y.com^$removeparam=fbclid\n"
                  "||z.com^$csp=script-src none\n"
                  "||w.com^$badfilter\n", &s);
  expect_uint ("nothing emitted", s.blocks, 0);
  expect_uint ("all four skipped", s.skipped, 4);
  expect_contains ("empty list is still valid JSON", json, "[]");
  g_free (json);

  printf ("\n-- ordering: blocks, then cosmetics, then exceptions --\n");
  json = compile ("||ads.example.com^\n"
                  "example.com##.promo-banner\n"
                  "@@||ads.example.com/allowed^\n", &s);
  {
    const char *block  = strstr (json, "\"block\"");
    const char *hide   = strstr (json, "css-display-none");
    const char *ignore = strstr (json, "ignore-previous-rules");
    checks++;
    if (block && hide && ignore && block < hide && hide < ignore) {
      printf ("  ok    block < css-display-none < ignore-previous-rules\n");
    } else {
      failures++;
      printf ("  FAIL  rule sections are out of order\n");
    }
  }
  expect_uint ("blocks",     s.blocks, 1);
  expect_uint ("cosmetics",  s.cosmetics, 1);
  expect_uint ("exceptions", s.exceptions, 1);
  g_free (json);

  printf ("\n-- generic cosmetics collapse into one rule --\n");
  json = compile ("##.ad-slot\n##.sponsored\n##div[id^=\"google_ads\"]\n", &s);
  expect_uint     ("counted individually", s.cosmetics, 3);
  expect_contains ("merged selector list", json, ".ad-slot,.sponsored");
  {
    /* One combined rule means exactly one css-display-none action. */
    int n = 0;
    for (const char *p = json; (p = strstr (p, "css-display-none")) != NULL; p += 4)
      n++;
    expect_uint ("emitted as a single rule", (guint) n, 1);
  }
  g_free (json);

  printf ("\n-- hosts files are accepted too --\n");
  json = compile ("# comment\n"
                  "0.0.0.0 analytics.example\n"
                  "127.0.0.1 localhost\n"
                  "0.0.0.0 beacon.example # inline note\n", &s);
  expect_uint     ("two hosts converted", s.blocks, 2);
  expect_contains ("analytics.example", json, "analytics\\\\.example");
  expect_absent   ("localhost is not blocked", json, "localhost");
  g_free (json);

  printf ("\n-- cookie stripping --\n");
  {
    LyAbp *abp = ly_abp_new ();
    ly_abp_add_cookie_block (abp, "tracker.example");
    char *out = ly_abp_finish (abp, &s);
    expect_contains ("block-cookies action", out, "\"block-cookies\"");
    expect_contains ("third-party only", out, "\"load-type\":[\"third-party\"]");
    g_free (out);
    ly_abp_free (abp);
  }

  printf ("\n-- json escaping --\n");
  json = compile ("example.com##a[href=\"/ad\"]", &s);
  expect_contains ("quotes escaped", json, "a[href=\\\"/ad\\\"]");
  g_free (json);

  printf ("\n%s  %d checks, %d failures\n\n",
          failures == 0 ? "PASS" : "FAIL", checks, failures);
  return failures == 0 ? 0 : 1;
}
