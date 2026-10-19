/* test-archive.c — the parts of the site archiver that do not need a network.
 *
 * Where a URL lands on disk, what a link between two of those places looks
 * like, and which sites the allow and block lists let through. Those three
 * decide whether a mirror browses or falls apart, and all three are pure
 * functions of their arguments.
 */

#include "archive.h"

#include <string.h>

static int failures;

static void
check (gboolean ok, const char *what)
{
  if (!ok) {
    g_printerr ("  FAIL  %s\n", what);
    failures++;
  }
}

/* Takes the string it is given: everything under test returns a fresh one. */
static void
check_str (char *got_owned, const char *want, const char *what)
{
  g_autofree char *got = got_owned;

  if (g_strcmp0 (got, want) != 0) {
    g_printerr ("  FAIL  %s\n        got  %s\n        want %s\n",
                what, got ?: "(null)", want ?: "(null)");
    failures++;
  }
}

/* ------------------------------------------------------------- mapping */

static void
test_local_path (void)
{
  const char *root = "/m/example.com";

  check_str (ly_archive_local_path (root, "example.com", "https://example.com/", NULL),
             "/m/example.com/index.html", "a bare origin is index.html");
  check_str (ly_archive_local_path (root, "example.com", "https://example.com/docs/", NULL),
             "/m/example.com/docs/index.html", "a directory URL is index.html");
  check_str (ly_archive_local_path (root, "example.com", "https://example.com/about", ".html"),
             "/m/example.com/about.html", "an extensionless page gains .html");
  check_str (ly_archive_local_path (root, "example.com", "https://example.com/a/b.html", ".html"),
             "/m/example.com/a/b.html", "a page that already says .html keeps it");

  /* /about and /about/team can both exist, and must not fight over a name. */
  check_str (ly_archive_local_path (root, "example.com", "https://example.com/about/team", ".html"),
             "/m/example.com/about/team.html",
             "a page under a page of the same name gets its own file");

  check_str (ly_archive_local_path (root, "example.com", "https://example.com/s/app.js", ".js"),
             "/m/example.com/s/app.js", "a resource keeps its own name");

  /* A query makes a different document of the same path. */
  g_autofree char *one =
    ly_archive_local_path (root, "example.com", "https://example.com/p.css?v=1", ".css");
  g_autofree char *two =
    ly_archive_local_path (root, "example.com", "https://example.com/p.css?v=2", ".css");
  check (g_strcmp0 (one, two) != 0, "two queries are two files");
  check (g_str_has_suffix (one, ".css"), "a query keeps the extension last");

  /* Anything not on the site's own host is visibly not. */
  check_str (ly_archive_local_path (root, "example.com", "https://cdn.other.net/x/f.woff2", NULL),
             "/m/example.com/_hosts/cdn.other.net/x/f.woff2",
             "another host goes under _hosts");

  /* Nothing may escape the root, however the URL is spelled. */
  g_autofree char *escape =
    ly_archive_local_path (root, "example.com", "https://example.com/%2e%2e/%2e%2e/etc/passwd",
                           ".html");
  check (escape != NULL && strstr (escape, "..") == NULL,
         "an encoded .. cannot climb out of the mirror");
  check (escape != NULL && g_str_has_prefix (escape, root),
         "every path stays inside the root");

  g_autofree char *backslash =
    ly_archive_local_path (root, "example.com", "https://example.com/a%5Cb.html", ".html");
  check (backslash != NULL && strchr (backslash + strlen (root), '\\') == NULL,
         "a backslash in a name cannot become a separator");

  /* Schemes with nothing to mirror. */
  check (ly_archive_local_path (root, "example.com", "mailto:a@b.c", NULL) == NULL,
         "mailto has no local path");
  check (ly_archive_local_path (root, "example.com", "data:text/plain,hi", NULL) == NULL,
         "a data URI has no local path");

  /* A name no filesystem would take, cut but still usable. */
  g_autofree char *long_name = g_strnfill (400, 'a');
  g_autofree char *long_url = g_strdup_printf ("https://example.com/%s.html", long_name);
  g_autofree char *cut = ly_archive_local_path (root, "example.com", long_url, ".html");
  g_autofree char *base = g_path_get_basename (cut);
  check (strlen (base) <= 96, "an absurd name is cut to something writable");
  check (g_str_has_suffix (base, ".html"), "and keeps its extension");
}

static void
test_relative (void)
{
  check_str (ly_archive_relative ("/m/s/index.html", "/m/s/about.html"),
             "about.html", "a sibling is a bare name");
  check_str (ly_archive_relative ("/m/s/index.html", "/m/s/a/b.html"),
             "a/b.html", "a child is a path down");
  check_str (ly_archive_relative ("/m/s/a/b/c.html", "/m/s/style.css"),
             "../../style.css", "a parent is a path up");
  check_str (ly_archive_relative ("/m/s/a.html", "/m/s/a.html"),
             "a.html", "a document can link to itself");
  check_str (ly_archive_relative ("/m/s/index.html", "/m/s/_hosts/cdn.net/f.css"),
             "_hosts/cdn.net/f.css", "a borrowed host is reachable");
  check_str (ly_archive_relative ("/m/s/index.html", "/m/s/a b.html"),
             "a%20b.html", "a space in a name is escaped");
}

/* -------------------------------------------------------------- policy */

static void
test_host_lists (void)
{
  g_autoptr (LyConfig) cfg = ly_config_new ();

  check (ly_archive_host_allowed (cfg, "example.com"),
         "an empty allow list allows anything");
  check (!ly_archive_host_allowed (cfg, ""), "a nameless host is not a site");
  check (!ly_archive_host_allowed (cfg, NULL), "nor is nothing at all");

  g_ptr_array_add (cfg->offline_block, g_strdup ("bank.example"));
  check (!ly_archive_host_allowed (cfg, "bank.example"), "a blocked host is blocked");
  check (!ly_archive_host_allowed (cfg, "secure.bank.example"),
         "and so is anything under it");
  check (ly_archive_host_allowed (cfg, "notbank.example"),
         "but not a host that merely ends the same way");
  check (ly_archive_host_allowed (cfg, "example.com"), "and nothing else is touched");

  g_ptr_array_add (cfg->offline_allow, g_strdup ("docs.gnome.org"));
  check (ly_archive_host_allowed (cfg, "docs.gnome.org"),
         "a non-empty allow list lets its own through");
  check (!ly_archive_host_allowed (cfg, "example.com"),
         "and shuts out everything else");

  /* Block wins: it is the list you reach for when something must not happen. */
  g_ptr_array_add (cfg->offline_allow, g_strdup ("bank.example"));
  check (!ly_archive_host_allowed (cfg, "bank.example"),
         "blocking beats allowing for the same host");
}

static void
test_roots (void)
{
  g_autoptr (LyConfig) cfg = ly_config_new ();
  g_free (cfg->offline_dir);
  cfg->offline_dir = g_strdup ("/sites");

  g_autofree char *saved = ly_archive_root_for (cfg, LY_ARCHIVE_SAVED, "www.example.com");
  check (g_strcmp0 (saved, "/sites/Saved/example.com") == 0,
         "a saved site drops www. and lands under Saved");

  g_autofree char *automatic =
    ly_archive_root_for (cfg, LY_ARCHIVE_AUTOMATIC, "example.com");
  check (g_strcmp0 (automatic, "/sites/Automatic/example.com") == 0,
         "a swept site lands under Automatic");

  /* The two must never be the same folder, or the sweep would overwrite what
   * someone asked to keep. */
  check (g_strcmp0 (saved, automatic) != 0, "the two kinds stay apart");

  /* A host is one path component, whatever characters it arrived with. */
  g_autofree char *odd = ly_archive_root_for (cfg, LY_ARCHIVE_SAVED, "a/../b");
  check (g_str_has_prefix (odd, "/sites/Saved/") &&
         strchr (odd + strlen ("/sites/Saved/"), '/') == NULL,
         "a host cannot smuggle a path into the folder name");
}

int
main (void)
{
  /* ly_config_new reads only the environment, and nothing here writes. */
  test_local_path ();
  test_relative ();
  test_host_lists ();
  test_roots ();

  if (failures > 0) {
    g_printerr ("test-archive: %d failure%s\n", failures, failures == 1 ? "" : "s");
    return 1;
  }
  g_print ("test-archive: ok\n");
  return 0;
}
