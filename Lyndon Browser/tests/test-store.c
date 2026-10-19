/* test-store.c — how the address bar decides what you meant.
 *
 *   make check
 *
 * store.c is the half of completion that has an opinion: it decides which of
 * the pages matching what you typed is the one you were reaching for. That is
 * arithmetic over four columns, and arithmetic is exactly the kind of thing
 * that quietly stops being right. The cases below are the ones the ranking
 * was designed around — a site's front page against one very long page
 * inside it — plus the guards that keep inline completion from rewriting
 * what somebody is in the middle of typing.
 *
 * Only ly_data_dir is needed from the rest of the browser, and it is stubbed
 * here, so this links against store.c, util.c, glib and sqlite alone.
 */

#include "store.h"

#include <glib/gstdio.h>
#include <stdio.h>
#include <string.h>

static int failures = 0;
static int checks = 0;
static char *test_dir = NULL;

/* store.c asks where the profile lives; here it lives in a temp directory. */
char *
ly_data_dir (void)
{
  return g_strdup (test_dir);
}

static void
expect_str (char *got, const char *want, const char *what)
{
  checks++;
  if (g_strcmp0 (got, want) == 0) {
    printf ("  ok    %s\n", what);
  } else {
    printf ("  FAIL  %s (got \"%s\", wanted \"%s\")\n", what,
            got ? got : "(null)", want ? want : "(null)");
    failures++;
  }
  g_free (got);
}

static void
expect_int (gint64 got, gint64 want, const char *what)
{
  checks++;
  if (got == want) {
    printf ("  ok    %s\n", what);
  } else {
    printf ("  FAIL  %s (got %" G_GINT64_FORMAT ", wanted %" G_GINT64_FORMAT ")\n",
            what, got, want);
    failures++;
  }
}

/* ------------------------------------------------------------- fixtures */

#define DAY (24 * 60 * 60)

static gint64
days_ago (int days)
{
  return g_get_real_time () / G_USEC_PER_SEC - (gint64) days * DAY;
}

/* A page with a history: `visits` of them, `typed` of which began in the
 * address bar, and `per_visit` seconds of attention each time. */
static void
seed (LyStore *store, const char *url, const char *title,
      int visits, int typed, int per_visit, int age_days)
{
  ly_store_import_visit (store, url, title, visits, typed, days_ago (age_days));
  for (int i = 0; i < visits && per_visit > 0; i++)
    ly_store_add_dwell (store, url, per_visit);
}

/* The URL the completion list puts first for `text`. */
static char *
top_match (LyStore *store, const char *text)
{
  g_autoptr (GPtrArray) rows = ly_store_complete (store, text, 8);
  if (rows->len == 0)
    return NULL;
  return g_strdup (((LyStoreRow *) g_ptr_array_index (rows, 0))->url);
}

static guint
match_count (LyStore *store, const char *text)
{
  g_autoptr (GPtrArray) rows = ly_store_complete (store, text, 8);
  return rows->len;
}

/* Where `site` comes in the sweep's shortlist, or -1 when it is not on it. */
static int
sweep_rank (LyStore *store, int days, int min_visits, const char *site)
{
  g_autoptr (GPtrArray) hosts = ly_store_recent_hosts (store, days, min_visits, 32);
  for (guint i = 0; i < hosts->len; i++) {
    g_autofree char *base = ly_uri_base_domain (g_ptr_array_index (hosts, i));
    if (g_strcmp0 (base, site) == 0)
      return (int) i;
  }
  return -1;
}

static char *
sweep_entry (LyStore *store, int days, int min_visits, const char *site)
{
  g_autoptr (GPtrArray) hosts = ly_store_recent_hosts (store, days, min_visits, 32);
  for (guint i = 0; i < hosts->len; i++) {
    const char *url = g_ptr_array_index (hosts, i);
    g_autofree char *base = ly_uri_base_domain (url);
    if (g_strcmp0 (base, site) == 0)
      return g_strdup (url);
  }
  return NULL;
}

static gint64
dwell_of (LyStore *store, const char *url)
{
  g_autoptr (GPtrArray) rows = ly_store_recent (store, 500);
  for (guint i = 0; i < rows->len; i++) {
    LyStoreRow *row = g_ptr_array_index (rows, i);
    if (g_strcmp0 (row->url, url) == 0)
      return row->dwell;
  }
  return -1;
}

/* ----------------------------------------------------------------- main */

int
main (void)
{
  g_autofree char *tmp = g_dir_make_tmp ("lyndon-store-XXXXXX", NULL);
  g_assert (tmp != NULL);
  test_dir = tmp;

  LyStore *store = ly_store_new ();

  /* The case the ranking exists for: a site's front page, typed and visited
   * constantly but only glanced at, against one page inside it that was left
   * open for eighteen hours. */
  seed (store, "https://www.youtube.com/", "YouTube", 300, 40, 15, 0);
  ly_store_import_visit (store, "https://www.youtube.com/watch?v=jNQXAC9IVRw",
                         "Me at the zoo", 1, 0, days_ago (0));
  ly_store_add_dwell (store, "https://www.youtube.com/watch?v=jNQXAC9IVRw",
                      18 * 60 * 60);

  /* The same shape, but where the long page is also the one visited most —
   * an app-shaped site whose address changes as you use it. */
  seed (store, "https://discord.com/app", "Discord", 60, 25, 10, 0);
  seed (store, "https://discord.com/channels/123456/789012", "general", 200, 0, 90, 0);

  seed (store, "https://drive.google.com/", "My Drive", 80, 30, 60, 0);
  seed (store, "https://drive.google.com/drive/folders/deadbeef", "Taxes", 12, 0, 120, 2);

  /* Somewhere landed on once and never again. */
  ly_store_import_visit (store, "https://random-thing.example/oops", "Oops",
                         1, 0, days_ago (0));

  /* An underscore is a LIKE wildcard and a perfectly ordinary URL character. */
  seed (store, "https://en.wikipedia.org/wiki/Foo_Bar", "Foo Bar", 5, 0, 60, 1);
  seed (store, "https://example.org/FooXBar", "Foo X Bar", 5, 0, 60, 1);

  /* An old favourite, against a page on the same site seen twice yesterday:
   * fading is not the same as dropping out of reach. */
  seed (store, "https://oldsite.example/", "Old Site", 400, 0, 30, 400);
  seed (store, "https://oldsite.example/news", "Old Site News", 2, 0, 30, 1);

  /* The same page twice over, alike in everything but when. */
  seed (store, "https://fresh.example/kept", "Kept", 20, 0, 30, 1);
  seed (store, "https://fresh.example/dropped", "Dropped", 20, 0, 30, 300);

  printf ("ranking\n");

  expect_str (top_match (store, "youtube.com"), "https://www.youtube.com/",
              "eighteen hours on one video does not beat the front page");
  expect_str (top_match (store, "youtube"), "https://www.youtube.com/",
              "the same on a partial host");
  expect_str (top_match (store, "discord"), "https://discord.com/app",
              "a typed address beats the long-lived one the app rewrites it to");
  expect_str (top_match (store, "drive.goo"), "https://drive.google.com/",
              "the front page beats a folder read for twice as long");
  expect_str (top_match (store, "fresh.example"), "https://fresh.example/kept",
              "between two pages used alike, the one used lately wins");
  expect_str (top_match (store, "oldsite"), "https://oldsite.example/",
              "a year of decay does not put a favourite behind a page seen twice");

  printf ("matching\n");

  expect_int (match_count (store, "Foo_Bar"), 1,
              "an underscore in the search text is a literal underscore");
  expect_str (top_match (store, "Foo_Bar"), "https://en.wikipedia.org/wiki/Foo_Bar",
              "and it finds the page that actually has one");
  expect_int (match_count (store, "nothing-here-at-all"), 0,
              "no match is no rows");

  printf ("dwell\n");

  expect_int (dwell_of (store, "https://www.youtube.com/watch?v=jNQXAC9IVRw"), 90,
              "eighteen hours banks as the per-visit cap");
  expect_int (dwell_of (store, "https://www.youtube.com/"), 300 * 15,
              "ordinary attention banks in full");
  expect_int (dwell_of (store, "https://nowhere.example/"), -1,
              "a page that was never recorded gains no row from being looked at");

  printf ("inline completion\n");

  expect_str (ly_store_autocomplete (store, "drive.go"), "drive.google.com",
              "typing finishes into the form a person types");
  expect_str (ly_store_autocomplete (store, "you"), "youtube.com",
              "a leading www. is not something anyone types");
  expect_str (ly_store_autocomplete (store, "https://dri"), "https://drive.google.com/",
              "typing the scheme keeps the scheme");
  expect_str (ly_store_autocomplete (store, "random-t"), NULL,
              "one accidental visit is not confident enough to rewrite typing");
  expect_str (ly_store_autocomplete (store, "channels/123"), NULL,
              "completion never starts from the middle of a URL");
  expect_str (ly_store_autocomplete (store, "drive.google.com"), NULL,
              "nothing left to add is no completion");
  expect_str (ly_store_autocomplete (store, ""), NULL,
              "empty typing completes to nothing");

  printf ("typed navigation\n");

  /* Two pages identical but for how they were reached. */
  ly_store_record_visit (store, "https://twin.example/typed", "Twin", TRUE);
  ly_store_record_visit (store, "https://twin.example/typed", "Twin", TRUE);
  ly_store_record_visit (store, "https://twin.example/linked", "Twin", FALSE);
  ly_store_record_visit (store, "https://twin.example/linked", "Twin", FALSE);
  expect_str (top_match (store, "twin.example"), "https://twin.example/typed",
              "having been typed is what breaks the tie");

  printf ("the offline sweep's shortlist\n");

  /* A site read a lot this week, one read a lot but long ago, and one barely
   * touched: only the first is a site somebody is still reading. */
  seed (store, "https://docs.sweep.example/a", "A", 9, 0, 30, 1);
  seed (store, "https://docs.sweep.example/b", "B", 5, 0, 30, 2);
  seed (store, "https://old.example/x",        "X", 40, 0, 30, 90);
  seed (store, "https://once.example/y",       "Y", 1, 0, 30, 1);

  expect_int (sweep_rank (store, 7, 3, "sweep.example") >= 0, TRUE,
              "a site read this week is on the list");
  expect_int (sweep_rank (store, 7, 3, "old.example"), -1,
              "a site not read since the spring is not");
  expect_int (sweep_rank (store, 7, 3, "once.example"), -1,
              "nor is one opened a single time");
  expect_int (sweep_rank (store, 120, 3, "old.example") >= 0, TRUE,
              "a wider window reaches back far enough for it");

  /* Pages add up per site, and the crawl should start from the busiest. */
  expect_str (sweep_entry (store, 7, 14, "sweep.example"),
              "https://docs.sweep.example/a",
              "a site's pages count together, and its busiest one is the way in");
  expect_int (sweep_rank (store, 7, 15, "sweep.example"), -1,
              "one more visit than the site has is one too many");

  /* -- bookmarks, and renaming one ------------------------------------- */
  printf ("\nbookmarks\n");

  ly_store_add_bookmark (store, "https://news.example/world", "World news");
  expect_int (ly_store_is_bookmarked (store, "https://news.example/world"), TRUE,
              "a bookmark is saved");
  expect_str (ly_store_bookmark_title (store, "https://news.example/world"),
              "World news", "with the label it was given");

  expect_int (ly_store_rename_bookmark (store, "https://news.example/world",
                                        "The news"), TRUE, "renaming one works");
  expect_str (ly_store_bookmark_title (store, "https://news.example/world"),
              "The news", "and the new label is what is stored");
  expect_int (ly_store_is_bookmarked (store, "https://news.example/world"), TRUE,
              "a rename does not unbookmark it");

  expect_int (ly_store_rename_bookmark (store, "https://news.example/world",
                                        "   Trimmed   "), TRUE,
              "surrounding space is not a reason to refuse a name");
  expect_str (ly_store_bookmark_title (store, "https://news.example/world"),
              "Trimmed", "and is not kept either");

  expect_int (ly_store_rename_bookmark (store, "https://news.example/world", "   "),
              FALSE, "a name that is only space is refused");
  expect_str (ly_store_bookmark_title (store, "https://news.example/world"),
              "Trimmed", "and changes nothing");
  expect_int (ly_store_rename_bookmark (store, "https://news.example/world", NULL),
              FALSE, "so is no name at all");

  expect_int (ly_store_rename_bookmark (store, "https://not-saved.example/", "Hello"),
              FALSE, "renaming something that is not bookmarked reports so");
  expect_str (ly_store_bookmark_title (store, "https://not-saved.example/"), NULL,
              "and it still has no label, because it is still not a bookmark");

  /* The page's own title is written over a bookmark that never had one. A
   * name somebody chose is not a bookmark that never had one. */
  ly_store_update_title (store, "https://news.example/world", "World News — Today");
  expect_str (ly_store_bookmark_title (store, "https://news.example/world"),
              "Trimmed", "a visit does not overwrite a name that was chosen");

  ly_store_add_bookmark (store, "https://blank.example/", NULL);
  ly_store_update_title (store, "https://blank.example/", "Blank Example");
  expect_str (ly_store_bookmark_title (store, "https://blank.example/"),
              "Blank Example",
              "but it does fill in one that was saved before its title arrived");

  ly_store_free (store);

  /* The store keeps exactly one file, in WAL mode, so clean up its company. */
  const char *leftovers[] = { "browsing.db", "browsing.db-wal", "browsing.db-shm" };
  for (guint i = 0; i < G_N_ELEMENTS (leftovers); i++) {
    g_autofree char *path = g_build_filename (tmp, leftovers[i], NULL);
    g_unlink (path);
  }
  g_rmdir (tmp);

  printf ("\n%d checks, %d failures\n", checks, failures);
  return failures == 0 ? 0 : 1;
}
