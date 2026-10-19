/* archive.c — see archive.h.
 *
 * The shape of a crawl:
 *
 *   start()  →  robots.txt  →  the entry page  →  pump()
 *                                                   ↓
 *                              queue ⇄ up to LY_PARALLEL fetches in flight
 *                                                   ↓
 *                                            everything written out
 *                                                   ↓
 *                                relink()  — a second pass over the saved
 *                                            HTML and CSS, turning the URLs
 *                                            that got mirrored into relative
 *                                            paths and leaving the rest alone
 *
 * The second pass exists because the first one cannot know what will be in
 * the mirror: a link written while the crawl is still running would have to
 * guess whether its target ever arrives, and the limits below mean plenty of
 * them do not. Relinking from the finished set is the only way to end up with
 * a folder where every local link resolves and every other link still works.
 */

#include "archive.h"

#include <glib/gstdio.h>
#include <libsoup/soup.h>
#include <string.h>

/* How hard this is allowed to lean on a site. Four at a time with a pause
 * between dispatches is roughly what a person clicking through pages would
 * do, which is the bar a tool fetching on their behalf should stay under. */
#define LY_PARALLEL        4
#define LY_DISPATCH_MS     120
#define LY_TIMEOUT_S       25
/* One response we will not hold in memory, whatever the limits say. */
#define LY_MAX_RESPONSE    (32u * 1024 * 1024)
/* Resources are not counted against the page budget, so they get a ceiling of
 * their own or one page of thumbnails could stand in for a whole crawl. */
#define LY_FILES_PER_PAGE  25
#define LY_SEGMENT_MAX     96

/* ------------------------------------------------------------------ types */

typedef enum {
  KIND_HTML,
  KIND_CSS,
  KIND_OTHER,
} BodyKind;

typedef enum {
  REF_PAGE,       /* a document worth following                */
  REF_RESOURCE,   /* something the document needs to render    */
} RefKind;

/* Returns the text to write in place of `url`, or NULL to leave it alone.
 * The scanner frees what it is given. */
typedef char *(*RefFn) (const char *url, RefKind kind, gpointer data);

typedef struct {
  char     *url;
  int       depth;
  gboolean  page;
  gboolean  entry;   /* the one page whose failure fails the whole crawl */
} Task;

typedef struct {
  char     *path;    /* where it landed on disk */
  char     *url;     /* what it was fetched as  */
  BodyKind  kind;
} Document;

typedef struct {
  gboolean  allow;
  char     *pattern;
} RobotRule;

typedef struct _Run Run;

/* A fetch outlives the crawl that started it when the browser is closed in
 * the middle of one, and libsoup will still call back into a callback that
 * has nowhere left to go. The anchor is refcounted alongside the run and is
 * how a late callback finds out there is nothing to come back to. */
typedef struct {
  Run *run;
} Anchor;

struct _Run {
  Anchor       *anchor;
  LyArchive    *archive;
  LyArchiveJob *job;

  char       *primary;       /* host the mirror is rooted on   */
  char       *base_domain;   /* how far "the same site" reaches */
  char       *root;
  char       *entry_path;    /* local path of the first page    */

  GQueue     *queue;         /* Task*                           */
  GHashTable *seen;          /* url → NULL                      */
  GHashTable *saved;         /* url → local path (owned)        */
  GHashTable *written;       /* local path → NULL, counted once */
  GPtrArray  *documents;     /* Document*, for the relink pass  */

  GPtrArray  *robots;        /* RobotRule*                      */
  guint       crawl_delay;   /* ms, from robots.txt             */

  GPtrArray    *fetches;     /* Fetch*, borrowed: the live requests */
  guint         inflight;
  guint         pump_source;
  guint         relink_source;
  guint         relink_index;
  GCancellable *cancel;
  gboolean      stopping;
};

struct _LyArchive {
  LyConfig    *cfg;
  LyEngine    *engine;
  SoupSession *http;

  GPtrArray *jobs;   /* LyArchiveJob*, oldest first */
  GPtrArray *runs;   /* Run*, the live ones         */

  LyArchiveChangedFn changed;
  LyArchiveDoneFn    done;
  gpointer           user_data;
};

static void pump         (Run *run);
static void schedule_pump(Run *run);
static void finish_run   (Run *run, const char *error);

/* --------------------------------------------------------------- policy */

char *
ly_archive_base_dir (LyConfig *cfg)
{
  if (cfg->offline_dir != NULL && *cfg->offline_dir != '\0')
    return g_strdup (cfg->offline_dir);

  const char *documents = g_get_user_special_dir (G_USER_DIRECTORY_DOCUMENTS);
  return g_build_filename (documents ?: g_get_home_dir (), "Websites", NULL);
}

/* Lower-cased, with a leading "www." taken off, so that the folder for a site
 * is the same whichever of its names someone happened to be on. */
static char *
folder_name_for_host (const char *host)
{
  g_autofree char *lower = g_ascii_strdown (host ?: "site", -1);
  const char *p = lower;
  if (g_str_has_prefix (p, "www."))
    p += 4;
  if (*p == '\0')
    p = "site";

  GString *out = g_string_sized_new (strlen (p));
  for (; *p != '\0'; p++)
    g_string_append_c (out, g_ascii_isalnum (*p) || *p == '.' || *p == '-' ? *p : '_');
  return g_string_free (out, FALSE);
}

char *
ly_archive_root_for (LyConfig *cfg, LyArchiveKind kind, const char *host)
{
  g_autofree char *base = ly_archive_base_dir (cfg);
  g_autofree char *folder = folder_name_for_host (host);
  return g_build_filename (base,
                           kind == LY_ARCHIVE_AUTOMATIC ? "Automatic" : "Saved",
                           folder, NULL);
}

/* "example.com" in a list covers "docs.example.com", the way every other
 * per-site list in this browser reads. */
static gboolean
host_in_list (GPtrArray *list, const char *host)
{
  if (list == NULL || host == NULL)
    return FALSE;

  for (guint i = 0; i < list->len; i++) {
    const char *entry = g_ptr_array_index (list, i);
    if (entry == NULL || *entry == '\0')
      continue;
    if (g_ascii_strcasecmp (entry, host) == 0)
      return TRUE;

    size_t elen = strlen (entry), hlen = strlen (host);
    if (hlen > elen + 1 &&
        host[hlen - elen - 1] == '.' &&
        g_ascii_strcasecmp (host + hlen - elen, entry) == 0)
      return TRUE;
  }
  return FALSE;
}

gboolean
ly_archive_host_allowed (LyConfig *cfg, const char *host)
{
  if (host == NULL || *host == '\0')
    return FALSE;
  if (host_in_list (cfg->offline_block, host))
    return FALSE;
  if (cfg->offline_allow != NULL && cfg->offline_allow->len > 0)
    return host_in_list (cfg->offline_allow, host);
  return TRUE;
}

/* ---------------------------------------------------------- path mapping */

static const struct { const char *type, *ext; } TYPE_EXT[] = {
  { "text/html",                ".html" },
  { "application/xhtml+xml",    ".html" },
  { "text/css",                 ".css"  },
  { "text/javascript",          ".js"   },
  { "application/javascript",   ".js"   },
  { "application/x-javascript", ".js"   },
  { "application/json",         ".json" },
  { "image/png",                ".png"  },
  { "image/jpeg",               ".jpg"  },
  { "image/gif",                ".gif"  },
  { "image/webp",               ".webp" },
  { "image/avif",               ".avif" },
  { "image/svg+xml",            ".svg"  },
  { "image/x-icon",             ".ico"  },
  { "image/vnd.microsoft.icon", ".ico"  },
  { "font/woff",                ".woff" },
  { "font/woff2",               ".woff2"},
  { "font/ttf",                 ".ttf"  },
  { "font/otf",                 ".otf"  },
  { "application/pdf",          ".pdf"  },
  { "text/plain",               ".txt"  },
  { "application/xml",          ".xml"  },
  { "text/xml",                 ".xml"  },
};

static const char *
ext_for_type (const char *type)
{
  if (type == NULL)
    return NULL;
  for (guint i = 0; i < G_N_ELEMENTS (TYPE_EXT); i++)
    if (g_ascii_strcasecmp (type, TYPE_EXT[i].type) == 0)
      return TYPE_EXT[i].ext;
  return NULL;
}

static BodyKind
kind_for_type (const char *type)
{
  if (type == NULL)
    return KIND_OTHER;
  if (g_ascii_strcasecmp (type, "text/html") == 0 ||
      g_ascii_strcasecmp (type, "application/xhtml+xml") == 0)
    return KIND_HTML;
  if (g_ascii_strcasecmp (type, "text/css") == 0)
    return KIND_CSS;
  return KIND_OTHER;
}

/* Eight hex digits of the string, to keep two URLs that differ only in their
 * query — or in a part of a name too long to keep — out of each other's way. */
static char *
short_hash (const char *text)
{
  g_autofree char *full = g_compute_checksum_for_string (G_CHECKSUM_SHA256, text, -1);
  return g_strndup (full, 8);
}

/* One path component, made safe for every filesystem this browser runs on. */
static char *
clean_segment (const char *segment)
{
  g_autofree char *decoded = g_uri_unescape_string (segment, NULL);
  const char *s = decoded ?: segment;

  GString *out = g_string_sized_new (strlen (s));
  for (const guchar *p = (const guchar *) s; *p != '\0'; p++) {
    if (*p < 0x20 || strchr ("/\\<>:\"|?*", (char) *p) != NULL)
      g_string_append_c (out, '_');
    else
      g_string_append_c (out, (char) *p);
  }

  /* Windows refuses both, and a leading dot would hide the file everywhere
   * else. */
  while (out->len > 0 && (out->str[out->len - 1] == '.' || out->str[out->len - 1] == ' '))
    g_string_truncate (out, out->len - 1);
  while (out->len > 0 && out->str[0] == ' ')
    g_string_erase (out, 0, 1);

  if (out->len == 0 || g_strcmp0 (out->str, ".") == 0 || g_strcmp0 (out->str, "..") == 0)
    g_string_assign (out, "_");

  return g_string_free (out, FALSE);
}

/* Keep the extension when a name has to be cut: it is what decides whether
 * the file opens at all. */
static char *
shorten (const char *name)
{
  if (strlen (name) <= LY_SEGMENT_MAX)
    return g_strdup (name);

  const char *dot = strrchr (name, '.');
  g_autofree char *ext = (dot != NULL && strlen (dot) <= 8) ? g_strdup (dot) : g_strdup ("");
  g_autofree char *hash = short_hash (name);
  g_autofree char *head = g_strndup (name, LY_SEGMENT_MAX - 10 - strlen (ext));
  return g_strconcat (head, "~", hash, ext, NULL);
}

static char *
with_extension (const char *name, const char *ext)
{
  if (ext == NULL || *ext == '\0')
    return g_strdup (name);

  size_t nlen = strlen (name), elen = strlen (ext);
  if (nlen >= elen && g_ascii_strcasecmp (name + nlen - elen, ext) == 0)
    return g_strdup (name);
  return g_strconcat (name, ext, NULL);
}

char *
ly_archive_local_path (const char *root, const char *primary,
                       const char *url, const char *ext)
{
  if (root == NULL || url == NULL)
    return NULL;

  g_autoptr (GUri) uri = g_uri_parse (url, G_URI_FLAGS_ENCODED, NULL);
  if (uri == NULL)
    return NULL;

  const char *scheme = g_uri_get_scheme (uri);
  if (g_strcmp0 (scheme, "http") != 0 && g_strcmp0 (scheme, "https") != 0)
    return NULL;

  const char *host = g_uri_get_host (uri);
  if (host == NULL || *host == '\0')
    return NULL;

  /* The site's own host sits at the root of the mirror; anything else it
   * borrows from — a CDN, a font host — goes somewhere clearly not it. */
  g_autofree char *dir = NULL;
  if (primary != NULL && g_ascii_strcasecmp (host, primary) == 0) {
    dir = g_strdup (root);
  } else {
    g_autofree char *host_dir = clean_segment (host);
    dir = g_build_filename (root, "_hosts", host_dir, NULL);
  }

  const char *path = g_uri_get_path (uri);
  if (path == NULL)
    path = "/";

  g_auto (GStrv) parts = g_strsplit (path, "/", -1);
  guint n = g_strv_length (parts);

  /* Everything but the last component is a directory. */
  g_autoptr (GPtrArray) segments = g_ptr_array_new_with_free_func (g_free);
  for (guint i = 0; i + 1 < n; i++) {
    if (parts[i][0] == '\0')
      continue;
    g_autofree char *cleaned = clean_segment (parts[i]);
    g_ptr_array_add (segments, shorten (cleaned));
  }

  const char *last = n > 0 ? parts[n - 1] : "";
  g_autofree char *name = NULL;
  if (*last == '\0') {
    name = g_strdup ("index.html");     /* a directory URL */
  } else {
    g_autofree char *cleaned = clean_segment (last);
    name = with_extension (cleaned, ext);
  }

  const char *query = g_uri_get_query (uri);
  if (query != NULL && *query != '\0') {
    g_autofree char *hash = short_hash (query);
    const char *dot = strrchr (name, '.');
    g_autofree char *stamped = NULL;
    if (dot != NULL && dot != name)
      stamped = g_strdup_printf ("%.*s@%s%s", (int) (dot - name), name, hash, dot);
    else
      stamped = g_strdup_printf ("%s@%s", name, hash);
    g_free (name);
    name = g_steal_pointer (&stamped);
  }

  g_autofree char *final_name = shorten (name);
  g_ptr_array_add (segments, g_strdup (final_name));

  GString *out = g_string_new (dir);
  for (guint i = 0; i < segments->len; i++) {
    g_string_append_c (out, G_DIR_SEPARATOR);
    g_string_append (out, g_ptr_array_index (segments, i));
  }
  return g_string_free (out, FALSE);
}

char *
ly_archive_relative (const char *from, const char *to)
{
  if (from == NULL || to == NULL)
    return NULL;

  g_auto (GStrv) a = g_strsplit (from, G_DIR_SEPARATOR_S, -1);
  g_auto (GStrv) b = g_strsplit (to,   G_DIR_SEPARATOR_S, -1);
  guint na = g_strv_length (a), nb = g_strv_length (b);
  if (na == 0 || nb == 0)
    return NULL;

  /* The last component of each is a file name, not a directory. */
  guint common = 0;
  while (common + 1 < na && common + 1 < nb && g_strcmp0 (a[common], b[common]) == 0)
    common++;

  GString *out = g_string_new (NULL);
  for (guint i = common; i + 1 < na; i++)
    g_string_append (out, "../");
  for (guint i = common; i < nb; i++) {
    g_autofree char *escaped = g_uri_escape_string (b[i], NULL, FALSE);
    g_string_append (out, escaped);
    if (i + 1 < nb)
      g_string_append_c (out, '/');
  }

  if (out->len == 0)
    g_string_assign (out, "./");
  return g_string_free (out, FALSE);
}

/* ------------------------------------------------------------- scanning */

static gboolean
is_space (char c)
{
  return c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '\f';
}

/* Schemes and forms that can never name something to mirror. */
static gboolean
unfetchable (const char *url)
{
  if (url == NULL)
    return TRUE;

  const char *p = url;
  while (is_space (*p))
    p++;
  if (*p == '\0' || *p == '#')
    return TRUE;

  static const char *const SKIP[] = {
    "data:", "javascript:", "mailto:", "tel:", "blob:", "about:", "#", NULL
  };
  for (int i = 0; SKIP[i] != NULL; i++)
    if (g_ascii_strncasecmp (p, SKIP[i], strlen (SKIP[i])) == 0)
      return TRUE;
  return FALSE;
}

/* ------ CSS */

static void
css_scan (const char *css, gsize len, RefFn fn, gpointer data, GString *out)
{
  gsize i = 0;
  while (i < len) {
    /* Comments can hide anything, including an unbalanced quote. */
    if (i + 1 < len && css[i] == '/' && css[i + 1] == '*') {
      gsize end = i + 2;
      while (end + 1 < len && !(css[end] == '*' && css[end + 1] == '/'))
        end++;
      end = MIN (end + 2, len);
      if (out != NULL)
        g_string_append_len (out, css + i, end - i);
      i = end;
      continue;
    }

    /* "myurl(" is not a url(), so the character before one has to be
       something a token cannot continue through. */
    gboolean boundary = i == 0 || (!g_ascii_isalnum (css[i - 1]) && css[i - 1] != '-');
    gboolean is_url    = boundary && (i + 4 <= len) &&
                         g_ascii_strncasecmp (css + i, "url(", 4) == 0;
    gboolean is_import = (i + 7 <= len) && g_ascii_strncasecmp (css + i, "@import", 7) == 0;

    if (!is_url && !is_import) {
      if (out != NULL)
        g_string_append_c (out, css[i]);
      i++;
      continue;
    }

    gsize p = i + (is_url ? 4 : 7);
    if (is_import) {
      while (p < len && is_space (css[p]))
        p++;
      if (p + 4 <= len && g_ascii_strncasecmp (css + p, "url(", 4) == 0)
        p += 4;
      else if (p >= len || (css[p] != '"' && css[p] != '\''))
        {   /* @import with neither a string nor a url() — nothing to take */
          if (out != NULL)
            g_string_append_len (out, css + i, p - i);
          i = p;
          continue;
        }
    }

    gsize value_start = p;
    while (value_start < len && is_space (css[value_start]))
      value_start++;

    char quote = '\0';
    if (value_start < len && (css[value_start] == '"' || css[value_start] == '\'')) {
      quote = css[value_start];
      value_start++;
    }

    gsize value_end = value_start;
    if (quote != '\0')
      while (value_end < len && css[value_end] != quote)
        value_end++;
    else
      while (value_end < len && css[value_end] != ')' && !is_space (css[value_end]))
        value_end++;

    g_autofree char *value = g_strndup (css + value_start, value_end - value_start);
    g_autofree char *replacement = unfetchable (value)
      ? NULL : fn (value, REF_RESOURCE, data);

    if (out != NULL) {
      g_string_append_len (out, css + i, (gssize) (value_start - i));
      g_string_append (out, replacement ?: value);
    }
    i = value_end;   /* the closing quote or ')' is emitted by the next turn */
  }
}

/* ------ HTML */

/* Which attributes name something, and whether what they name is a page. */
static int
ref_kind_for (const char *tag, const char *attr)
{
  struct { const char *tag, *attr; int kind; } TABLE[] = {
    { "a",      "href",       REF_PAGE     },
    { "area",   "href",       REF_PAGE     },
    { "link",   "href",       REF_RESOURCE },
    { "use",    "href",       REF_RESOURCE },
    { "image",  "href",       REF_RESOURCE },
    { "img",    "src",        REF_RESOURCE },
    { "script", "src",        REF_RESOURCE },
    { "iframe", "src",        REF_RESOURCE },
    { "frame",  "src",        REF_RESOURCE },
    { "embed",  "src",        REF_RESOURCE },
    { "source", "src",        REF_RESOURCE },
    { "video",  "src",        REF_RESOURCE },
    { "audio",  "src",        REF_RESOURCE },
    { "track",  "src",        REF_RESOURCE },
    { "input",  "src",        REF_RESOURCE },
    { "video",  "poster",     REF_RESOURCE },
    { "object", "data",       REF_RESOURCE },
    { "body",   "background", REF_RESOURCE },
    { "td",     "background", REF_RESOURCE },
    { "table",  "background", REF_RESOURCE },
  };
  for (guint i = 0; i < G_N_ELEMENTS (TABLE); i++)
    if (g_strcmp0 (tag, TABLE[i].tag) == 0 && g_strcmp0 (attr, TABLE[i].attr) == 0)
      return TABLE[i].kind;
  return -1;
}

static gboolean
is_srcset (const char *tag, const char *attr)
{
  return g_strcmp0 (attr, "srcset") == 0 &&
         (g_strcmp0 (tag, "img") == 0 || g_strcmp0 (tag, "source") == 0);
}

/* "a.png 1x, b.png 2x" — the URL is a run of non-space characters, and a
 * trailing comma on it means the entry carried no descriptor. */
static char *
srcset_transform (const char *value, RefFn fn, gpointer data)
{
  GString *out = g_string_sized_new (strlen (value) + 32);
  const char *p = value;
  gboolean first = TRUE;

  while (*p != '\0') {
    while (is_space (*p) || *p == ',')
      p++;
    if (*p == '\0')
      break;

    const char *start = p;
    while (*p != '\0' && !is_space (*p))
      p++;

    gsize ulen = (gsize) (p - start);
    while (ulen > 0 && start[ulen - 1] == ',')
      ulen--;
    if (ulen == 0)
      continue;
    g_autofree char *url = g_strndup (start, ulen);

    const char *desc_start = p;
    while (*p != '\0' && *p != ',')
      p++;
    g_autofree char *descriptor = g_strndup (desc_start, (gsize) (p - desc_start));
    g_strstrip (descriptor);

    g_autofree char *replacement = unfetchable (url) ? NULL : fn (url, REF_RESOURCE, data);

    if (!first)
      g_string_append (out, ", ");
    first = FALSE;
    g_string_append (out, replacement ?: url);
    if (*descriptor != '\0')
      g_string_append_printf (out, " %s", descriptor);
  }

  return g_string_free (out, FALSE);
}

/* A <base href> changes what every relative URL in the document means, so
 * both passes have to know about it before they read anything else. */
static char *
find_base_href (const char *html, gsize len)
{
  gsize window = MIN (len, 16u * 1024);
  for (gsize i = 0; i + 5 < window; i++) {
    if (html[i] != '<' || g_ascii_strncasecmp (html + i + 1, "base", 4) != 0)
      continue;
    if (!is_space (html[i + 5]) && html[i + 5] != '>')
      continue;

    gsize end = i;
    while (end < window && html[end] != '>')
      end++;

    g_autofree char *tag = g_strndup (html + i, end - i);
    const char *href = NULL;
    for (const char *p = tag; (p = strchr (p, 'h')) != NULL; p++) {
      if (g_ascii_strncasecmp (p, "href", 4) != 0)
        continue;
      const char *q = p + 4;
      while (is_space (*q))
        q++;
      if (*q != '=')
        continue;
      q++;
      while (is_space (*q))
        q++;
      href = q;
      break;
    }
    if (href == NULL)
      continue;

    char quote = (*href == '"' || *href == '\'') ? *href : '\0';
    if (quote != '\0')
      href++;
    const char *stop = href;
    while (*stop != '\0' && (quote != '\0' ? *stop != quote : !is_space (*stop)))
      stop++;

    g_autofree char *value = g_strndup (href, (gsize) (stop - href));
    g_strstrip (value);
    if (*value != '\0')
      return g_steal_pointer (&value);
  }
  return NULL;
}

/* A tolerant one-pass tag scanner. It does not build a tree and does not need
 * to: every reference in HTML is an attribute value or a stylesheet, and both
 * can be found by reading forwards. Anything it does not understand is copied
 * through untouched, which is the behaviour that matters — a document this
 * cannot parse must come out of the mirror exactly as it went in. */
static void
html_scan (const char *html, gsize len, RefFn fn, gpointer data, GString *out)
{
  gsize i = 0;

#define EMIT(from, count) G_STMT_START {                 \
    if (out != NULL) g_string_append_len (out, (from), (gssize) (count)); \
  } G_STMT_END

  while (i < len) {
    if (html[i] != '<') {
      EMIT (html + i, 1);
      i++;
      continue;
    }

    /* Comments, doctypes and closing tags carry nothing we want. */
    if (i + 3 < len && strncmp (html + i, "<!--", 4) == 0) {
      gsize end = i + 4;
      while (end + 2 < len && strncmp (html + end, "-->", 3) != 0)
        end++;
      end = MIN (end + 3, len);
      EMIT (html + i, end - i);
      i = end;
      continue;
    }
    if (i + 1 < len && (html[i + 1] == '!' || html[i + 1] == '?' || html[i + 1] == '/')) {
      gsize end = i;
      while (end < len && html[end] != '>')
        end++;
      end = MIN (end + 1, len);
      EMIT (html + i, end - i);
      i = end;
      continue;
    }

    gsize name_start = i + 1, name_end = name_start;
    while (name_end < len && (g_ascii_isalnum (html[name_end]) || html[name_end] == '-'))
      name_end++;
    if (name_end == name_start) {
      EMIT (html + i, 1);
      i++;
      continue;
    }

    g_autofree char *tag = g_ascii_strdown (html + name_start, (gssize) (name_end - name_start));
    EMIT (html + i, name_end - i);

    gsize p = name_end;
    while (p < len && html[p] != '>') {
      if (is_space (html[p])) {
        EMIT (html + p, 1);
        p++;
        continue;
      }
      if (html[p] == '/') {
        EMIT (html + p, 1);
        p++;
        continue;
      }

      gsize attr_start = p;
      while (p < len && !is_space (html[p]) && html[p] != '=' && html[p] != '>' &&
             html[p] != '/')
        p++;
      g_autofree char *attr = g_ascii_strdown (html + attr_start, (gssize) (p - attr_start));
      EMIT (html + attr_start, p - attr_start);

      gsize gap = p;
      while (p < len && is_space (html[p]))
        p++;
      if (p >= len || html[p] != '=') {
        EMIT (html + gap, p - gap);
        continue;   /* a bare attribute */
      }
      EMIT (html + gap, p - gap);
      EMIT (html + p, 1);   /* the '=' */
      p++;

      gsize before_value = p;
      while (p < len && is_space (html[p]))
        p++;
      EMIT (html + before_value, p - before_value);

      char quote = (p < len && (html[p] == '"' || html[p] == '\'')) ? html[p] : '\0';
      if (quote != '\0') {
        EMIT (html + p, 1);
        p++;
      }

      gsize value_start = p;
      if (quote != '\0')
        while (p < len && html[p] != quote)
          p++;
      else
        while (p < len && !is_space (html[p]) && html[p] != '>')
          p++;

      g_autofree char *value = g_strndup (html + value_start, p - value_start);
      g_autofree char *replacement = NULL;

      if (g_strcmp0 (tag, "base") == 0 && g_strcmp0 (attr, "href") == 0) {
        /* Resolution happens against the document's own location once the
         * links around it are relative, which is what "." says. */
        replacement = g_strdup (".");
      } else if (g_strcmp0 (attr, "style") == 0) {
        GString *css = g_string_sized_new (strlen (value) + 16);
        css_scan (value, strlen (value), fn, data, css);
        replacement = g_string_free (css, FALSE);
      } else if (is_srcset (tag, attr)) {
        replacement = srcset_transform (value, fn, data);
      } else {
        int kind = ref_kind_for (tag, attr);
        if (kind >= 0 && !unfetchable (value))
          replacement = fn (value, (RefKind) kind, data);
      }

      if (out != NULL)
        g_string_append (out, replacement ?: value);

      if (quote != '\0' && p < len) {
        EMIT (html + p, 1);
        p++;
      }
    }

    if (p < len) {
      EMIT (html + p, 1);   /* the '>' */
      p++;
    }
    i = p;

    /* Raw-text elements end at their own closing tag, and only <style> has
     * anything inside worth looking at. */
    if (g_strcmp0 (tag, "script") == 0 || g_strcmp0 (tag, "style") == 0 ||
        g_strcmp0 (tag, "textarea") == 0 || g_strcmp0 (tag, "title") == 0) {
      g_autofree char *closing = g_strconcat ("</", tag, NULL);
      gsize close_len = strlen (closing);
      gsize end = i;
      while (end + close_len <= len &&
             g_ascii_strncasecmp (html + end, closing, close_len) != 0)
        end++;
      if (end + close_len > len)
        end = len;

      if (g_strcmp0 (tag, "style") == 0)
        css_scan (html + i, end - i, fn, data, out);
      else
        EMIT (html + i, end - i);
      i = end;
    }
  }
#undef EMIT
}

/* ----------------------------------------------------------- robots.txt */

static void
robot_rule_free (gpointer data)
{
  RobotRule *rule = data;
  g_free (rule->pattern);
  g_free (rule);
}

/* A robots.txt path is a prefix, with '*' for any run and a trailing '$' to
 * demand the path end there. "Disallow: /secret/" therefore covers
 * /secret/anything, which is the whole point of writing it. */
static gboolean
robot_match (const char *pattern, const char *path)
{
  if (*pattern == '\0')
    return FALSE;

  const char *p = pattern, *s = path;
  const char *star = NULL, *mark = NULL;

  while (*s != '\0') {
    if (*p == '$' && p[1] == '\0')
      return FALSE;        /* anchored at an end the path has not reached */
    if (*p == '\0')
      return TRUE;         /* the pattern ran out, and a prefix is a match */
    if (*p == '*') {
      star = ++p;
      mark = s;
      continue;
    }
    if (*p == *s) {
      p++;
      s++;
      continue;
    }
    if (star == NULL)
      return FALSE;
    p = star;
    s = ++mark;
  }

  while (*p == '*')
    p++;
  if (*p == '$' && p[1] == '\0')
    return TRUE;
  return *p == '\0';
}

static void
parse_robots (Run *run, const char *text, gsize len)
{
  g_autofree char *copy = g_strndup (text, len);
  g_auto (GStrv) lines = g_strsplit (copy, "\n", -1);

  gboolean applies = FALSE;
  gboolean in_group = FALSE;   /* the previous line named a user agent */

  for (guint i = 0; lines[i] != NULL; i++) {
    char *line = lines[i];
    char *hash = strchr (line, '#');
    if (hash != NULL)
      *hash = '\0';
    g_strstrip (line);
    if (*line == '\0')
      continue;

    char *colon = strchr (line, ':');
    if (colon == NULL)
      continue;
    *colon = '\0';
    g_autofree char *field = g_ascii_strdown (g_strstrip (line), -1);
    char *value = g_strstrip (colon + 1);

    if (g_strcmp0 (field, "user-agent") == 0) {
      if (!in_group) {   /* a new block of agents starts a new group */
        applies = FALSE;
        in_group = TRUE;
      }
      if (g_strcmp0 (value, "*") == 0 || g_ascii_strcasecmp (value, "lyndon") == 0)
        applies = TRUE;
      continue;
    }

    in_group = FALSE;
    if (!applies)
      continue;

    if (g_strcmp0 (field, "crawl-delay") == 0) {
      double seconds = g_ascii_strtod (value, NULL);
      run->crawl_delay = (guint) CLAMP (seconds * 1000.0, 0.0, 5000.0);
      continue;
    }
    if (g_strcmp0 (field, "disallow") != 0 && g_strcmp0 (field, "allow") != 0)
      continue;

    RobotRule *rule = g_new0 (RobotRule, 1);
    rule->allow   = g_strcmp0 (field, "allow") == 0;
    rule->pattern = g_strdup (value);
    /* "Disallow:" with nothing after it means the opposite of a rule. */
    if (!rule->allow && *value == '\0') {
      robot_rule_free (rule);
      continue;
    }
    g_ptr_array_add (run->robots, rule);
  }
}

/* Longest match wins, and Allow beats Disallow at the same length — the rule
 * every crawler settled on once Google wrote it down. */
static gboolean
robots_allow (Run *run, const char *url)
{
  if (run->robots->len == 0)
    return TRUE;

  g_autoptr (GUri) uri = g_uri_parse (url, G_URI_FLAGS_ENCODED, NULL);
  if (uri == NULL)
    return TRUE;

  const char *path = g_uri_get_path (uri);
  const char *query = g_uri_get_query (uri);
  g_autofree char *full = query != NULL && *query != '\0'
    ? g_strdup_printf ("%s?%s", path ?: "/", query)
    : g_strdup (path ?: "/");

  gsize best = 0;
  gboolean allowed = TRUE;

  for (guint i = 0; i < run->robots->len; i++) {
    RobotRule *rule = g_ptr_array_index (run->robots, i);
    if (!robot_match (rule->pattern, full))
      continue;
    gsize length = strlen (rule->pattern);
    if (length > best || (length == best && rule->allow)) {
      best = length;
      allowed = rule->allow;
    }
  }
  return allowed;
}

/* --------------------------------------------------------------- the run */

static void
task_free (gpointer data)
{
  Task *task = data;
  g_free (task->url);
  g_free (task);
}

static void
document_free (gpointer data)
{
  Document *doc = data;
  g_free (doc->path);
  g_free (doc->url);
  g_free (doc);
}

static void
job_free (gpointer data)
{
  LyArchiveJob *job = data;
  g_free (job->host);
  g_free (job->start);
  g_free (job->root);
  g_free (job->error);
  g_free (job);
}

static void
run_free (Run *run)
{
  if (run->pump_source != 0)
    g_source_remove (run->pump_source);
  if (run->relink_source != 0)
    g_source_remove (run->relink_source);

  run->anchor->run = NULL;
  g_rc_box_release (run->anchor);

  g_clear_pointer (&run->fetches, g_ptr_array_unref);
  g_clear_object (&run->cancel);
  g_clear_pointer (&run->queue, g_queue_free);
  g_clear_pointer (&run->seen, g_hash_table_unref);
  g_clear_pointer (&run->saved, g_hash_table_unref);
  g_clear_pointer (&run->written, g_hash_table_unref);
  g_clear_pointer (&run->documents, g_ptr_array_unref);
  g_clear_pointer (&run->robots, g_ptr_array_unref);
  g_free (run->primary);
  g_free (run->base_domain);
  g_free (run->root);
  g_free (run->entry_path);
  g_free (run);
}

static void
notify (LyArchive *archive)
{
  if (archive->changed != NULL)
    archive->changed (archive, archive->user_data);
}

/* Absolute, fragment-free, and with the default port off, so that two spellings
 * of one address are one entry in `seen`. */
static char *
normalise (const char *base, const char *reference)
{
  g_autofree char *absolute = base != NULL
    ? g_uri_resolve_relative (base, reference, G_URI_FLAGS_ENCODED, NULL)
    : g_strdup (reference);
  if (absolute == NULL)
    return NULL;

  g_autoptr (GUri) uri = g_uri_parse (absolute, G_URI_FLAGS_ENCODED, NULL);
  if (uri == NULL)
    return NULL;

  const char *scheme = g_uri_get_scheme (uri);
  if (g_strcmp0 (scheme, "http") != 0 && g_strcmp0 (scheme, "https") != 0)
    return NULL;
  if (g_uri_get_host (uri) == NULL)
    return NULL;

  int port = g_uri_get_port (uri);
  if ((g_strcmp0 (scheme, "http") == 0 && port == 80) ||
      (g_strcmp0 (scheme, "https") == 0 && port == 443))
    port = -1;

  const char *path = g_uri_get_path (uri);
  g_autoptr (GUri) clean =
    g_uri_build (G_URI_FLAGS_ENCODED, scheme, NULL, g_uri_get_host (uri), port,
                 (path != NULL && *path != '\0') ? path : "/",
                 g_uri_get_query (uri), NULL);
  return g_uri_to_string (clean);
}

static gboolean
same_site (Run *run, const char *url)
{
  g_autofree char *base = ly_uri_base_domain (url);
  return base != NULL && g_ascii_strcasecmp (base, run->base_domain) == 0;
}

static guint
file_budget (Run *run)
{
  return (guint) run->archive->cfg->offline_max_pages * LY_FILES_PER_PAGE + 100;
}

static gboolean
over_budget (Run *run)
{
  LyConfig *cfg = run->archive->cfg;
  return run->job->bytes >= (guint64) cfg->offline_max_mb * 1024 * 1024 ||
         run->job->files >= file_budget (run);
}

static void
enqueue (Run *run, const char *url, int depth, gboolean page)
{
  LyConfig *cfg = run->archive->cfg;

  if (run->stopping || url == NULL)
    return;
  if (g_hash_table_contains (run->seen, url))
    return;

  if (page) {
    if (depth > cfg->offline_max_depth)
      return;
    if (!same_site (run, url))
      return;
    if (!robots_allow (run, url))
      return;
    /* Pages already queued count against the budget: a crawl that discovers
     * ten thousand links should stop discovering, not stop fetching. */
    if (run->job->pages + run->job->queued >= (guint) cfg->offline_max_pages)
      return;
  } else if (over_budget (run)) {
    return;
  }

  g_hash_table_add (run->seen, g_strdup (url));

  Task *task = g_new0 (Task, 1);
  task->url   = g_strdup (url);
  task->depth = depth;
  task->page  = page;
  g_queue_push_tail (run->queue, task);

  if (page)
    run->job->queued++;
}

/* Record a saved file under both the URL asked for and the URL that answered,
 * so a link to either side of a redirect relinks to the same place. */
static void
remember_saved (Run *run, const char *requested, const char *final_url, const char *path)
{
  g_hash_table_insert (run->saved, g_strdup (requested), g_strdup (path));
  if (final_url != NULL && g_strcmp0 (final_url, requested) != 0)
    g_hash_table_insert (run->saved, g_strdup (final_url), g_strdup (path));
}

/* Write bytes where the mapping says, or somewhere flat when the mapping runs
 * into a file where it wanted a directory — /a serving a page and /a/b serving
 * another is perfectly legal and cannot both be honoured literally. */
static char *
write_body (Run *run, const char *path, const char *url, GBytes *body)
{
  gsize len = 0;
  const char *data = g_bytes_get_data (body, &len);

  g_autofree char *dir = g_path_get_dirname (path);
  g_autoptr (GError) error = NULL;

  if (g_mkdir_with_parents (dir, 0755) == 0 &&
      g_file_set_contents (path, data, (gssize) len, &error))
    return g_strdup (path);

  g_clear_error (&error);

  g_autofree char *hash = short_hash (url);
  g_autofree char *base = g_path_get_basename (path);
  g_autofree char *name = g_strdup_printf ("%s-%s", hash, base);
  g_autofree char *flat_dir = g_build_filename (run->root, "_files", NULL);
  g_autofree char *flat = g_build_filename (flat_dir, name, NULL);

  if (g_mkdir_with_parents (flat_dir, 0755) != 0 ||
      !g_file_set_contents (flat, data, (gssize) len, &error)) {
    g_message ("archive: could not write %s: %s", flat,
               error ? error->message : "unknown error");
    return NULL;
  }
  return g_steal_pointer (&flat);
}

/* ------ fetching */

typedef struct {
  Anchor       *anchor;   /* the run, while it still exists */
  Task         *task;
  SoupMessage  *message;
  GCancellable *cancel;   /* this request's own, for the size guard */
} Fetch;

static void
fetch_free (Fetch *fetch)
{
  Run *run = fetch->anchor->run;
  if (run != NULL)
    g_ptr_array_remove_fast (run->fetches, fetch);

  g_rc_box_release (fetch->anchor);
  task_free (fetch->task);
  g_clear_object (&fetch->message);
  g_clear_object (&fetch->cancel);
  g_free (fetch);
}

/* Every live request, for the two cases that end a crawl early. */
static void
cancel_fetches (Run *run)
{
  for (guint i = 0; i < run->fetches->len; i++) {
    Fetch *fetch = g_ptr_array_index (run->fetches, i);
    g_cancellable_cancel (fetch->cancel);
  }
}

/* Discovery: every reference in a document, queued and left in place. The
 * rewriting happens later, from the set that actually arrived. */
typedef struct {
  Run        *run;
  const char *base;
  int         depth;
} Discovery;

static char *
on_reference (const char *url, RefKind kind, gpointer data)
{
  Discovery *d = data;
  g_autofree char *absolute = normalise (d->base, url);
  if (absolute == NULL)
    return NULL;

  if (kind == REF_PAGE)
    enqueue (d->run, absolute, d->depth + 1, TRUE);
  else
    enqueue (d->run, absolute, d->depth, FALSE);
  return NULL;
}

static void
on_fetched (GObject *source, GAsyncResult *result, gpointer data)
{
  Fetch *fetch = data;
  Run   *run   = fetch->anchor->run;
  g_autoptr (GError) error = NULL;

  g_autoptr (GBytes) body =
    soup_session_send_and_read_finish (SOUP_SESSION (source), result, &error);

  if (run == NULL) {       /* the browser closed while this was in flight */
    fetch_free (fetch);
    return;
  }

  run->inflight--;
  gboolean was_entry = fetch->task->entry;

  if (run->stopping) {
    fetch_free (fetch);
    if (run->inflight == 0)
      finish_run (run, NULL);
    return;
  }

  if (fetch->task->page && run->job->queued > 0)
    run->job->queued--;

  if (body == NULL ||
      !SOUP_STATUS_IS_SUCCESSFUL (soup_message_get_status (fetch->message))) {
    /* One page that will not load is not a failed archive — but the page the
     * crawl was started from is the whole of it. */
    guint status = soup_message_get_status (fetch->message);
    fetch_free (fetch);
    if (was_entry) {
      g_autofree char *why = body == NULL
        ? g_strdup_printf ("that page could not be fetched: %s",
                           error ? error->message : "no response")
        : g_strdup_printf ("that page answered %u", status);
      finish_run (run, why);
    } else {
      schedule_pump (run);
    }
    return;
  }

  SoupMessageHeaders *headers = soup_message_get_response_headers (fetch->message);
  const char *content_type = soup_message_headers_get_content_type (headers, NULL);
  BodyKind kind = kind_for_type (content_type);

  g_autofree char *final_url = NULL;
  GUri *final_uri = soup_message_get_uri (fetch->message);
  if (final_uri != NULL) {
    g_autofree char *text = g_uri_to_string (final_uri);
    final_url = normalise (NULL, text);
  }
  const char *document_url = final_url ?: fetch->task->url;

  gsize len = 0;
  const char *data_bytes = g_bytes_get_data (body, &len);

  g_autofree char *path = ly_archive_local_path (run->root, run->primary,
                                                 document_url,
                                                 ext_for_type (content_type));
  g_autofree char *written =
    path != NULL ? write_body (run, path, document_url, body) : NULL;

  if (written == NULL) {
    fetch_free (fetch);
    if (was_entry)
      finish_run (run, "that page could not be written to disk");
    else
      schedule_pump (run);
    return;
  }

  remember_saved (run, fetch->task->url, final_url, written);

  /* "/docs/" and "/docs/index.html" are two URLs for one document, and the
   * mapping says so by giving them one path. Count it once, and relink it
   * once, however many ways the site spells it. */
  gboolean first_time = !g_hash_table_contains (run->written, written);
  if (first_time) {
    g_hash_table_add (run->written, g_strdup (written));
    run->job->files++;
    run->job->bytes += len;
    if (kind == KIND_HTML)
      run->job->pages++;
  }

  if (run->entry_path == NULL)
    run->entry_path = g_strdup (written);

  if (first_time && (kind == KIND_HTML || kind == KIND_CSS)) {
    Document *doc = g_new0 (Document, 1);
    doc->path = g_strdup (written);
    doc->url  = g_strdup (document_url);
    doc->kind = kind;
    g_ptr_array_add (run->documents, doc);
  }

  /* Follow what the document points at. A redirect moves the base, so
   * discovery resolves against the URL that answered, not the one asked. */
  if (kind == KIND_HTML || kind == KIND_CSS) {
    g_autofree char *base_href = kind == KIND_HTML ? find_base_href (data_bytes, len) : NULL;
    g_autofree char *base = NULL;
    if (base_href != NULL)
      base = g_uri_resolve_relative (document_url, base_href, G_URI_FLAGS_ENCODED, NULL);

    Discovery d = { run, base ?: document_url, fetch->task->depth };
    if (kind == KIND_HTML)
      html_scan (data_bytes, len, on_reference, &d, NULL);
    else
      css_scan (data_bytes, len, on_reference, &d, NULL);
  }

  notify (run->archive);
  schedule_pump (run);
  fetch_free (fetch);
}

/* Stop a response we do not want before its body comes down the wire. */
static void
on_got_headers (SoupMessage *message, gpointer data)
{
  Fetch *fetch = data;
  SoupMessageHeaders *headers = soup_message_get_response_headers (message);

  goffset length = soup_message_headers_get_content_length (headers);
  const char *type = soup_message_headers_get_content_type (headers, NULL);

  gboolean too_big = length > 0 && (guint64) length > LY_MAX_RESPONSE;
  gboolean streaming = type != NULL &&
                       (g_str_has_prefix (type, "video/") || g_str_has_prefix (type, "audio/")) &&
                       (length <= 0 || (guint64) length > 8u * 1024 * 1024);

  if (too_big || streaming)
    g_cancellable_cancel (fetch->cancel);
}

static void
dispatch (Run *run, Task *task)
{
  g_autoptr (SoupMessage) message = soup_message_new ("GET", task->url);
  if (message == NULL) {
    task_free (task);
    return;
  }

  Fetch *fetch = g_new0 (Fetch, 1);
  fetch->anchor  = g_rc_box_acquire (run->anchor);
  fetch->task    = task;
  fetch->message = g_object_ref (message);
  fetch->cancel  = g_cancellable_new ();
  g_ptr_array_add (run->fetches, fetch);

  g_signal_connect (message, "got-headers", G_CALLBACK (on_got_headers), fetch);

  run->inflight++;
  soup_session_send_and_read_async (run->archive->http, message, G_PRIORITY_LOW,
                                    fetch->cancel, on_fetched, fetch);
}

static gboolean
on_pump (gpointer data)
{
  Run *run = data;
  run->pump_source = 0;
  pump (run);
  return G_SOURCE_REMOVE;
}

static void
schedule_pump (Run *run)
{
  if (run->pump_source != 0 || run->stopping)
    return;
  run->pump_source = g_timeout_add (MAX (LY_DISPATCH_MS, run->crawl_delay),
                                    on_pump, run);
}

static void
pump (Run *run)
{
  if (run->stopping)
    return;

  while (run->inflight < LY_PARALLEL) {
    Task *task = g_queue_pop_head (run->queue);
    if (task == NULL)
      break;

    /* The budget can run out between queueing and dispatch. */
    if (over_budget (run) && !task->page) {
      task_free (task);
      continue;
    }
    dispatch (run, task);
  }

  if (run->inflight == 0 && g_queue_is_empty (run->queue))
    finish_run (run, NULL);
}

/* ------ relinking */

typedef struct {
  Run        *run;
  const char *from;    /* local path of the document being rewritten */
  const char *base;    /* URL it was fetched from                    */
} Relink;

static char *
on_relink (const char *url, RefKind kind, gpointer data)
{
  Relink *r = data;

  /* Keep the fragment: it is the one part of a link a mirror can honour
   * without having fetched anything. */
  const char *hash = strchr (url, '#');
  g_autofree char *bare = hash != NULL ? g_strndup (url, (gsize) (hash - url))
                                       : g_strdup (url);
  if (*bare == '\0')
    return NULL;   /* a pure fragment already points at this document */

  g_autofree char *absolute = normalise (r->base, bare);
  if (absolute == NULL)
    return NULL;

  const char *target = g_hash_table_lookup (r->run->saved, absolute);
  if (target == NULL) {
    /* Not in the mirror: make it absolute so it still works online. */
    return hash != NULL ? g_strconcat (absolute, hash, NULL) : g_steal_pointer (&absolute);
  }

  g_autofree char *relative = ly_archive_relative (r->from, target);
  if (relative == NULL)
    return NULL;
  return hash != NULL ? g_strconcat (relative, hash, NULL) : g_steal_pointer (&relative);
}

static void
relink_document (Run *run, Document *doc)
{
  g_autofree char *contents = NULL;
  gsize len = 0;
  if (!g_file_get_contents (doc->path, &contents, &len, NULL))
    return;

  g_autofree char *base_href = doc->kind == KIND_HTML ? find_base_href (contents, len) : NULL;
  g_autofree char *base = NULL;
  if (base_href != NULL)
    base = g_uri_resolve_relative (doc->url, base_href, G_URI_FLAGS_ENCODED, NULL);

  Relink r = { run, doc->path, base ?: doc->url };
  GString *out = g_string_sized_new (len + len / 8);

  if (doc->kind == KIND_HTML)
    html_scan (contents, len, on_relink, &r, out);
  else
    css_scan (contents, len, on_relink, &r, out);

  g_file_set_contents (doc->path, out->str, (gssize) out->len, NULL);
  g_string_free (out, TRUE);
}

/* The entry page is rarely <root>/index.html — it is whatever URL the crawl
 * started from. One redirect at the top of the folder saves everyone from
 * having to know that. */
static void
write_entry (Run *run)
{
  if (run->entry_path == NULL)
    return;

  g_autofree char *index = g_build_filename (run->root, "index.html", NULL);
  if (g_strcmp0 (index, run->entry_path) == 0)
    return;

  g_autofree char *relative = ly_archive_relative (index, run->entry_path);
  if (relative == NULL)
    return;

  g_autofree char *escaped = g_markup_escape_text (relative, -1);
  g_autofree char *host = g_markup_escape_text (run->job->host, -1);
  g_autofree char *html = g_strdup_printf (
    "<!doctype html>\n<meta charset=\"utf-8\">\n"
    "<title>%s — offline copy</title>\n"
    "<meta http-equiv=\"refresh\" content=\"0; url=%s\">\n"
    "<p>Offline copy of %s. <a href=\"%s\">Open it</a>.</p>\n",
    host, escaped, host, escaped);

  g_file_set_contents (index, html, -1, NULL);
}

static void
write_manifest (Run *run)
{
  LyArchiveJob *job = run->job;

  g_autoptr (GKeyFile) kf = g_key_file_new ();
  g_key_file_set_string  (kf, "archive", "source", job->start);
  g_key_file_set_string  (kf, "archive", "host",   job->host);
  g_key_file_set_string  (kf, "archive", "kind",
                          job->kind == LY_ARCHIVE_AUTOMATIC ? "automatic" : "saved");
  g_key_file_set_integer (kf, "archive", "pages",  (int) job->pages);
  g_key_file_set_integer (kf, "archive", "files",  (int) job->files);
  g_key_file_set_uint64  (kf, "archive", "bytes",  job->bytes);
  g_key_file_set_boolean (kf, "archive", "complete", !job->cancelled && !job->failed);

  g_autoptr (GDateTime) now = g_date_time_new_now_local ();
  g_autofree char *stamp = g_date_time_format_iso8601 (now);
  g_key_file_set_string (kf, "archive", "created", stamp ?: "");

  if (run->entry_path != NULL) {
    g_autofree char *index = g_build_filename (run->root, "index.html", NULL);
    g_autofree char *entry = ly_archive_relative (index, run->entry_path);
    g_key_file_set_string (kf, "archive", "entry", entry ?: "index.html");
  }

  g_autofree char *path = g_build_filename (run->root, "lyndon-archive.ini", NULL);
  g_key_file_save_to_file (kf, path, NULL);
}

static void run_retire (Run *run);

static gboolean
on_relink_step (gpointer data)
{
  Run *run = data;

  /* A few at a time: this is disk work on the main loop, and a mirror of a
   * few hundred pages would be a visible stall if it ran in one go. */
  for (int i = 0; i < 8 && run->relink_index < run->documents->len; i++, run->relink_index++)
    relink_document (run, g_ptr_array_index (run->documents, run->relink_index));

  if (run->relink_index < run->documents->len) {
    notify (run->archive);
    return G_SOURCE_CONTINUE;
  }

  run->relink_source = 0;
  write_entry (run);
  write_manifest (run);

  run->job->rewriting = FALSE;
  run->job->finished  = TRUE;
  run_retire (run);
  return G_SOURCE_REMOVE;
}

static void
run_retire (Run *run)
{
  LyArchive *archive = run->archive;
  LyArchiveJob *job = run->job;

  g_ptr_array_remove (archive->runs, run);
  notify (archive);
  if (archive->done != NULL)
    archive->done (job, archive->user_data);
  run_free (run);
}

static void
finish_run (Run *run, const char *error)
{
  if (run->job->finished || run->relink_source != 0)
    return;

  if (error != NULL) {
    run->job->failed = TRUE;
    g_free (run->job->error);
    run->job->error = g_strdup (error);
  }

  run->stopping = TRUE;
  if (run->pump_source != 0) {
    g_source_remove (run->pump_source);
    run->pump_source = 0;
  }

  /* Anything still in flight has to land before the files can be relinked.
   * On an error there is nothing to wait for, so cut it short. */
  if (run->inflight > 0) {
    if (run->job->failed)
      cancel_fetches (run);
    return;
  }

  if (run->job->failed || run->documents->len == 0) {
    run->job->finished = TRUE;
    if (run->job->failed && run->job->files == 0)
      g_rmdir (run->root);   /* only succeeds while it is empty, which is the point */
    else
      write_manifest (run);
    run_retire (run);
    return;
  }

  run->job->rewriting = TRUE;
  run->relink_index = 0;
  run->relink_source = g_idle_add (on_relink_step, run);
  notify (run->archive);
}

/* ------ the two fetches that come before the crawl */

/* The entry page goes through the ordinary path; it is only marked so that
 * its failure can be reported as the crawl's failure rather than swallowed. */
static void
start_entry (Run *run, const char *url)
{
  if (!robots_allow (run, url)) {
    finish_run (run, "robots.txt asks crawlers to stay off this page");
    return;
  }

  Task *task = g_new0 (Task, 1);
  task->url   = g_strdup (url);
  task->depth = 0;
  task->page  = TRUE;
  task->entry = TRUE;
  g_hash_table_add (run->seen, g_strdup (url));
  g_queue_push_tail (run->queue, task);
  run->job->queued++;

  pump (run);
}

typedef struct {
  Anchor *anchor;
  char   *url;
} RobotsFetch;

static void
on_robots_fetched (GObject *source, GAsyncResult *result, gpointer data)
{
  RobotsFetch *rf = data;
  Run *run = rf->anchor->run;
  g_autoptr (GError) error = NULL;

  g_autoptr (GBytes) body =
    soup_session_send_and_read_finish (SOUP_SESSION (source), result, &error);

  if (run != NULL && !run->stopping) {
    /* A site without a robots.txt answers 404, which is not a rule file. */
    if (body != NULL) {
      gsize len = 0;
      const char *text = g_bytes_get_data (body, &len);
      parse_robots (run, text, MIN (len, 512u * 1024));
    }
    start_entry (run, rf->url);
  } else if (run != NULL) {
    finish_run (run, NULL);
  }

  g_rc_box_release (rf->anchor);
  g_free (rf->url);
  g_free (rf);
}

/* ------------------------------------------------------------------- api */

LyArchive *
ly_archive_new (LyConfig *cfg, LyEngine *engine)
{
  LyArchive *archive = g_new0 (LyArchive, 1);
  archive->cfg    = cfg;
  archive->engine = engine;
  archive->jobs   = g_ptr_array_new_with_free_func (job_free);
  archive->runs   = g_ptr_array_new ();
  return archive;
}

void
ly_archive_free (LyArchive *archive)
{
  if (archive == NULL)
    return;

  while (archive->runs->len > 0) {
    Run *run = g_ptr_array_index (archive->runs, archive->runs->len - 1);
    run->stopping = TRUE;
    g_cancellable_cancel (run->cancel);
    cancel_fetches (run);
    g_ptr_array_remove_index (archive->runs, archive->runs->len - 1);
    run_free (run);
  }

  g_clear_pointer (&archive->runs, g_ptr_array_unref);
  g_clear_pointer (&archive->jobs, g_ptr_array_unref);
  g_clear_object (&archive->http);
  g_free (archive);
}

void
ly_archive_set_callbacks (LyArchive *archive, LyArchiveChangedFn changed,
                          LyArchiveDoneFn done, gpointer user_data)
{
  archive->changed   = changed;
  archive->done      = done;
  archive->user_data = user_data;
}

GPtrArray *
ly_archive_jobs (LyArchive *archive)
{
  return archive->jobs;
}

guint
ly_archive_active (LyArchive *archive)
{
  return archive->runs->len;
}

void
ly_archive_cancel (LyArchive *archive, LyArchiveJob *job)
{
  for (guint i = 0; i < archive->runs->len; i++) {
    Run *run = g_ptr_array_index (archive->runs, i);
    if (run->job != job)
      continue;
    job->cancelled = TRUE;
    run->stopping = TRUE;
    g_cancellable_cancel (run->cancel);
    cancel_fetches (run);
    if (run->inflight == 0)
      finish_run (run, NULL);
    return;
  }
}

void
ly_archive_clear_finished (LyArchive *archive)
{
  for (guint i = archive->jobs->len; i > 0; i--) {
    LyArchiveJob *job = g_ptr_array_index (archive->jobs, i - 1);
    if (job->finished)
      g_ptr_array_remove_index (archive->jobs, i - 1);
  }
  notify (archive);
}

/* The browser's own user agent: this is fetching on the user's behalf, and a
 * string nobody has heard of is turned away by more sites than it should be. */
static void
ensure_session (LyArchive *archive)
{
  const char *agent = "Lyndon/" LYNDON_VERSION;
  if (archive->engine != NULL) {
    const char *from_engine =
      webkit_settings_get_user_agent (ly_engine_settings (archive->engine));
    if (from_engine != NULL && *from_engine != '\0')
      agent = from_engine;
  }

  if (archive->http == NULL)
    archive->http = soup_session_new_with_options ("timeout", (guint) LY_TIMEOUT_S,
                                                   "max-conns-per-host", LY_PARALLEL,
                                                   NULL);
  soup_session_set_user_agent (archive->http, agent);
}

gboolean
ly_archive_start (LyArchive *archive, const char *url, LyArchiveKind kind,
                  const char *dest, char **reason)
{
#define REFUSE(text) G_STMT_START {           \
    if (reason != NULL) *reason = g_strdup (text); \
    return FALSE;                             \
  } G_STMT_END

  LyConfig *cfg = archive->cfg;

  if (!cfg->offline_enabled)
    REFUSE ("Offline archiving is switched off in Preferences");

  g_autofree char *start = normalise (NULL, url);
  if (start == NULL)
    REFUSE ("Only http and https pages can be archived");

  g_autofree char *host = ly_uri_host (start);
  if (host == NULL)
    REFUSE ("That address has no site to archive");

  if (host_in_list (cfg->offline_block, host))
    REFUSE ("This site is on the never-archive list");
  if (!ly_archive_host_allowed (cfg, host))
    REFUSE ("This site is not on the archive list");

  for (guint i = 0; i < archive->runs->len; i++) {
    Run *other = g_ptr_array_index (archive->runs, i);
    if (g_ascii_strcasecmp (other->job->host, host) == 0)
      REFUSE ("That site is already being archived");
  }

  ensure_session (archive);

  LyArchiveJob *job = g_new0 (LyArchiveJob, 1);
  job->host  = g_strdup (host);
  job->start = g_strdup (start);
  job->kind  = kind;
  /* A folder chosen by hand is where the mirror goes *into*, not what it is
   * called: two sites picked into the same folder must not land on top of
   * each other. */
  g_autofree char *folder = folder_name_for_host (host);
  job->root  = dest != NULL ? g_build_filename (dest, folder, NULL)
                            : ly_archive_root_for (cfg, kind, host);
  g_ptr_array_add (archive->jobs, job);

  Run *run = g_new0 (Run, 1);
  run->anchor      = g_rc_box_new0 (Anchor);
  run->anchor->run = run;
  run->fetches     = g_ptr_array_new ();
  run->archive     = archive;
  run->job         = job;
  run->primary     = g_strdup (host);
  run->base_domain = ly_uri_base_domain (start) ?: g_strdup (host);
  run->root        = g_strdup (job->root);
  run->queue       = g_queue_new ();
  run->seen        = g_hash_table_new_full (g_str_hash, g_str_equal, g_free, NULL);
  run->saved       = g_hash_table_new_full (g_str_hash, g_str_equal, g_free, g_free);
  run->written     = g_hash_table_new_full (g_str_hash, g_str_equal, g_free, NULL);
  run->documents   = g_ptr_array_new_with_free_func (document_free);
  run->robots      = g_ptr_array_new_with_free_func (robot_rule_free);
  run->cancel      = g_cancellable_new ();
  g_ptr_array_add (archive->runs, run);

  if (g_mkdir_with_parents (run->root, 0755) != 0) {
    finish_run (run, "that folder could not be created");
    if (reason != NULL)
      *reason = g_strdup ("That folder could not be created");
    return FALSE;
  }

  /* robots.txt first, always, and from the host the crawl is rooted on. */
  g_autoptr (GUri) uri = g_uri_parse (start, G_URI_FLAGS_ENCODED, NULL);
  int port = g_uri_get_port (uri);
  g_autofree char *robots_url = port > 0
    ? g_strdup_printf ("%s://%s:%d/robots.txt", g_uri_get_scheme (uri), host, port)
    : g_strdup_printf ("%s://%s/robots.txt", g_uri_get_scheme (uri), host);

  g_autoptr (SoupMessage) message = soup_message_new ("GET", robots_url);
  if (message == NULL) {
    start_entry (run, start);
  } else {
    RobotsFetch *rf = g_new0 (RobotsFetch, 1);
    rf->anchor = g_rc_box_acquire (run->anchor);
    rf->url    = g_strdup (start);
    soup_session_send_and_read_async (archive->http, message, G_PRIORITY_LOW,
                                      run->cancel, on_robots_fetched, rf);
  }

  notify (archive);
  return TRUE;
#undef REFUSE
}

/* ----------------------------------------------------------------- sweep */

/* How old a mirror has to be before the sweep refreshes it: the same window
 * the sweep uses to decide a site is one you are still reading. */
static gboolean
mirror_is_fresh (LyConfig *cfg, const char *root)
{
  g_autofree char *path = g_build_filename (root, "lyndon-archive.ini", NULL);
  g_autoptr (GFile) file = g_file_new_for_path (path);
  g_autoptr (GFileInfo) info =
    g_file_query_info (file, G_FILE_ATTRIBUTE_TIME_MODIFIED,
                       G_FILE_QUERY_INFO_NONE, NULL, NULL);
  if (info == NULL)
    return FALSE;

  g_autoptr (GDateTime) modified = g_file_info_get_modification_date_time (info);
  if (modified == NULL)
    return FALSE;

  g_autoptr (GDateTime) now = g_date_time_new_now_utc ();
  GTimeSpan age = g_date_time_difference (now, modified);
  return age < (GTimeSpan) cfg->offline_days * G_TIME_SPAN_DAY;
}

void
ly_archive_sweep (LyArchive *archive, LyStore *store)
{
  LyConfig *cfg = archive->cfg;

  if (!cfg->offline_enabled || !cfg->offline_automatic || store == NULL)
    return;

  g_autoptr (GPtrArray) hosts =
    ly_store_recent_hosts (store, cfg->offline_days, cfg->offline_min_visits, 64);

  for (guint i = 0; i < hosts->len; i++) {
    const char *url = g_ptr_array_index (hosts, i);

    /* One site per pass. The sweep runs often enough that working through a
     * backlog takes hours rather than saturating a connection for them. */
    if (archive->runs->len > 0)
      return;

    g_autofree char *host = ly_uri_host (url);
    if (host == NULL || !ly_archive_host_allowed (cfg, host))
      continue;

    g_autofree char *root = ly_archive_root_for (cfg, LY_ARCHIVE_AUTOMATIC, host);
    if (mirror_is_fresh (cfg, root))
      continue;

    ly_archive_start (archive, url, LY_ARCHIVE_AUTOMATIC, NULL, NULL);
  }
}
