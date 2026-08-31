/* block.c — see block.h for why this exists at all. */

#include "block.h"
#include "abp.h"

#include <string.h>

/* Rules whose longest literal run is shorter than this cannot be indexed
 * usefully: the bucket would be hit by almost every URL. They go on the
 * always-check list instead, which is why that list stays short. */
#define MIN_KEYWORD 3

/* ------------------------------------------------------------------ rules */

typedef struct {
  GRegex   *re;
  guint32   kinds;        /* bit per LyResourceKind; 0 means any            */
  gint8     third_party;  /* -1 either, 1 third-party only, 0 first only    */
  char    **domains;      /* $domain=a.com|b.com — NULL means any           */
  char    **domains_not;  /* $domain=~a.com                                 */
} Rule;

static void
rule_free (Rule *r)
{
  if (r == NULL)
    return;
  if (r->re)
    g_regex_unref (r->re);
  g_strfreev (r->domains);
  g_strfreev (r->domains_not);
  g_free (r);
}

struct _LyBlock {
  /* "||host^" with no options: the overwhelming majority of every list. */
  GHashTable *hosts;             /* host -> present               */
  GHashTable *hosts_allow;

  /* Everything else, bucketed by one literal token of the pattern. */
  GHashTable *index;             /* keyword -> GPtrArray<Rule*>   */
  GHashTable *index_allow;
  GPtrArray  *loose;             /* no usable keyword             */
  GPtrArray  *loose_allow;

  GPtrArray  *cosmetic_generic;  /* char* selector                */
  GHashTable *cosmetic_domain;   /* host -> GPtrArray<char*>      */

  LyBlockStats stats;
};

/* ------------------------------------------------------------- small bits */

static gboolean
token_char (char c)
{
  return g_ascii_isalnum (c) || c == '%';
}

/* The longest literal run in a pattern, which is what the rule gets indexed
 * under. Wildcards, separators and punctuation all end a run, so whatever
 * comes back must appear verbatim in any URL the rule can match. */
static char *
keyword_of (const char *pattern)
{
  const char *best = NULL, *start = NULL;
  size_t best_len = 0, run = 0;

  for (const char *p = pattern; ; p++) {
    if (token_char (*p)) {
      if (run == 0)
        start = p;
      run++;
      continue;
    }
    if (run > best_len) {
      best_len = run;
      best = start;
    }
    run = 0;
    if (*p == '\0')
      break;
  }
  if (best_len < MIN_KEYWORD)
    return NULL;
  return g_ascii_strdown (best, (gssize) best_len);
}

static void
index_add (GHashTable *index, const char *keyword, Rule *rule)
{
  GPtrArray *bucket = g_hash_table_lookup (index, keyword);
  if (bucket == NULL) {
    bucket = g_ptr_array_new_with_free_func ((GDestroyNotify) rule_free);
    g_hash_table_insert (index, g_strdup (keyword), bucket);
  }
  g_ptr_array_add (bucket, rule);
}

/* ---------------------------------------------------------------- options */

static guint32
kind_bit (const char *name)
{
  static const struct { const char *name; LyResourceKind kind; } map[] = {
    { "script",         LY_RES_SCRIPT      },
    { "image",          LY_RES_IMAGE       },
    { "stylesheet",     LY_RES_STYLESHEET  },
    { "font",           LY_RES_FONT        },
    { "media",          LY_RES_MEDIA       },
    { "xmlhttprequest", LY_RES_XHR         },
    { "websocket",      LY_RES_WEBSOCKET   },
    { "subdocument",    LY_RES_SUBDOCUMENT },
    { "document",       LY_RES_DOCUMENT    },
    { "other",          LY_RES_OTHER       },
  };
  for (gsize i = 0; i < G_N_ELEMENTS (map); i++)
    if (g_strcmp0 (map[i].name, name) == 0)
      return 1u << map[i].kind;
  return 0;
}

/* Parse "$third-party,script,domain=a.com|~b.com" onto the rule.
 *
 * Returns FALSE for an option that is not implemented, and the caller then
 * drops the rule. That is deliberate: an unimplemented option always narrows
 * a rule, so honouring the pattern while ignoring the option would block more
 * than the list author asked for. */
static gboolean
parse_options (Rule *r, const char *opts)
{
  g_auto(GStrv) parts = g_strsplit (opts, ",", -1);
  GPtrArray *inc = g_ptr_array_new ();
  GPtrArray *exc = g_ptr_array_new ();
  gboolean ok = TRUE;

  for (guint i = 0; parts[i]; i++) {
    const char *o = parts[i];
    gboolean negate = (*o == '~');
    if (negate)
      o++;

    if (g_str_has_prefix (o, "domain=")) {
      g_auto(GStrv) doms = g_strsplit (o + 7, "|", -1);
      for (guint j = 0; doms[j]; j++) {
        if (doms[j][0] == '~')
          g_ptr_array_add (exc, g_ascii_strdown (doms[j] + 1, -1));
        else if (doms[j][0])
          g_ptr_array_add (inc, g_ascii_strdown (doms[j], -1));
      }
      continue;
    }
    if (g_strcmp0 (o, "third-party") == 0) { r->third_party = negate ? 0 : 1; continue; }
    if (g_strcmp0 (o, "first-party") == 0) { r->third_party = negate ? 1 : 0; continue; }

    guint32 bit = kind_bit (o);
    if (bit) {
      /* A negated type means everything but this one. Ignoring it leaves the
       * rule no wider than written, so it is safe to skip. */
      if (!negate)
        r->kinds |= bit;
      continue;
    }
    /* match-case, popup, csp, redirect, badfilter and the rest. */
    ok = FALSE;
    break;
  }

  if (ok) {
    if (inc->len) {
      g_ptr_array_add (inc, NULL);
      r->domains = (char **) g_ptr_array_free (inc, FALSE);
    } else {
      g_ptr_array_free (inc, TRUE);
    }
    if (exc->len) {
      g_ptr_array_add (exc, NULL);
      r->domains_not = (char **) g_ptr_array_free (exc, FALSE);
    } else {
      g_ptr_array_free (exc, TRUE);
    }
  } else {
    g_ptr_array_set_free_func (inc, g_free);
    g_ptr_array_set_free_func (exc, g_free);
    g_ptr_array_free (inc, TRUE);
    g_ptr_array_free (exc, TRUE);
  }
  return ok;
}

/* ------------------------------------------------------------------ hosts */

/* "||example.com^" and nothing else: the fast path. Returns the bare host, or
 * NULL when the pattern is anything more complicated than that. */
static char *
plain_domain_rule (const char *pattern)
{
  if (!g_str_has_prefix (pattern, "||"))
    return NULL;

  const char *p = pattern + 2;
  size_t len = strlen (p);
  if (len > 1 && p[len - 1] == '^')
    len--;
  else
    return NULL;

  for (size_t i = 0; i < len; i++)
    if (!(g_ascii_isalnum (p[i]) || p[i] == '.' || p[i] == '-'))
      return NULL;
  if (len == 0 || memchr (p, '.', len) == NULL)
    return NULL;
  return g_ascii_strdown (p, (gssize) len);
}

/* Does `host` equal an entry or sit beneath one? Walks labels right to left,
 * so ads.example.com matches an entry of example.com while notexample.com
 * does not — the whole-label step is what makes that true. */
static gboolean
host_in_table (GHashTable *table, const char *host)
{
  if (host == NULL || *host == '\0')
    return FALSE;
  for (const char *p = host; p && *p; ) {
    if (g_hash_table_contains (table, p))
      return TRUE;
    const char *dot = strchr (p, '.');
    p = dot ? dot + 1 : NULL;
  }
  return FALSE;
}

/* ----------------------------------------------------------------- adding */

static void
add_cosmetic (LyBlock *b, const char *line, const char *sep)
{
  const char *sel = sep + 2;
  g_autofree char *scope = g_strndup (line, (gsize) (sep - line));

  if (*sel == '\0')
    return;
  /* Procedural cosmetics (:has-text, :xpath, :matches-css) need an engine
   * evaluating them per element; a plain selector is all CSS can express. */
  if (strstr (sel, ":has-text") || strstr (sel, ":xpath") || strstr (sel, ":matches-"))
    return;

  b->stats.cosmetics++;
  if (*scope == '\0') {
    g_ptr_array_add (b->cosmetic_generic, g_strdup (sel));
    return;
  }

  g_auto(GStrv) doms = g_strsplit (scope, ",", -1);
  for (guint i = 0; doms[i]; i++) {
    if (doms[i][0] == '\0' || doms[i][0] == '~')
      continue;
    g_autofree char *host = g_ascii_strdown (doms[i], -1);
    GPtrArray *list = g_hash_table_lookup (b->cosmetic_domain, host);
    if (list == NULL) {
      list = g_ptr_array_new_with_free_func (g_free);
      g_hash_table_insert (b->cosmetic_domain, g_steal_pointer (&host), list);
    }
    g_ptr_array_add (list, g_strdup (sel));
  }
}

static void
add_line (LyBlock *b, const char *raw)
{
  g_autofree char *line = g_strdup (raw);
  g_strstrip (line);

  if (*line == '\0' || *line == '!' || *line == '[')
    return;

  /* Element hiding. #@# un-hides something a list hid; skipping it only
   * leaves an element visible, so it is the safe one to not implement. */
  if (strstr (line, "#@#") != NULL)
    return;
  const char *sep = strstr (line, "##");
  if (sep != NULL) {
    add_cosmetic (b, line, sep);
    return;
  }

  gboolean allow = g_str_has_prefix (line, "@@");
  const char *body = allow ? line + 2 : line;

  g_autofree char *pattern = NULL;
  Rule *r = g_new0 (Rule, 1);
  r->third_party = -1;

  const char *dollar = strrchr (body, '$');
  if (dollar && dollar != body) {
    pattern = g_strndup (body, (gsize) (dollar - body));
    if (!parse_options (r, dollar + 1)) {
      rule_free (r);
      b->stats.invalid++;
      return;
    }
  } else {
    pattern = g_strdup (body);
  }

  if (*pattern == '\0') {
    rule_free (r);
    b->stats.invalid++;
    return;
  }

  /* Fast path: a bare domain rule carrying no options at all. */
  if (r->kinds == 0 && r->third_party == -1 && r->domains == NULL && r->domains_not == NULL) {
    char *host = plain_domain_rule (pattern);
    if (host) {
      g_hash_table_add (allow ? b->hosts_allow : b->hosts, host);
      if (allow)
        b->stats.exceptions++;
      else
        b->stats.domains++;
      rule_free (r);
      return;
    }
  }

  g_autofree char *rx = ly_abp_pattern_to_regex (pattern);
  if (rx == NULL) {
    rule_free (r);
    b->stats.invalid++;
    return;
  }

  r->re = g_regex_new (rx, G_REGEX_CASELESS | G_REGEX_OPTIMIZE, 0, NULL);
  if (r->re == NULL) {
    rule_free (r);
    b->stats.invalid++;
    return;
  }

  if (allow)
    b->stats.exceptions++;
  else
    b->stats.patterns++;

  g_autofree char *kw = keyword_of (pattern);
  if (kw)
    index_add (allow ? b->index_allow : b->index, kw, r);
  else
    g_ptr_array_add (allow ? b->loose_allow : b->loose, r);
}

void
ly_block_add_text (LyBlock *b, const char *text)
{
  g_return_if_fail (b != NULL);
  if (text == NULL)
    return;
  g_auto(GStrv) lines = g_strsplit (text, "\n", -1);
  for (guint i = 0; lines[i]; i++)
    add_line (b, lines[i]);
}

gboolean
ly_block_add_file (LyBlock *b, const char *path, GError **error)
{
  g_autofree char *text = NULL;
  if (!g_file_get_contents (path, &text, NULL, error))
    return FALSE;
  ly_block_add_text (b, text);
  return TRUE;
}

void
ly_block_load_builtin (LyBlock *b, LyConfig *cfg, const char *data_dir)
{
  for (int c = 0; c < LY_CAT_N; c++) {
    if (cfg && !cfg->block_cat[c])
      continue;
    g_autofree char *name = g_strconcat (ly_cat_id ((LyBlockCat) c), ".txt", NULL);
    g_autofree char *full = g_build_filename (data_dir, "rules", name, NULL);
    g_autoptr(GError) error = NULL;
    if (!ly_block_add_file (b, full, &error))
      g_debug ("blocker: %s", error->message);
  }
}

/* --------------------------------------------------------------- matching */

static gboolean
domain_allowed (Rule *r, const char *page_host)
{
  if (r->domains_not && page_host)
    for (guint i = 0; r->domains_not[i]; i++)
      if (g_str_has_suffix (page_host, r->domains_not[i]))
        return FALSE;
  if (r->domains == NULL)
    return TRUE;
  if (page_host == NULL)
    return FALSE;
  for (guint i = 0; r->domains[i]; i++)
    if (g_str_has_suffix (page_host, r->domains[i]))
      return TRUE;
  return FALSE;
}

static gboolean
rule_matches (Rule *r, const char *url, const char *page_host,
              gboolean third_party, LyResourceKind kind)
{
  if (r->kinds && !(r->kinds & (1u << kind)))
    return FALSE;
  if (r->third_party == 1 && !third_party)
    return FALSE;
  if (r->third_party == 0 && third_party)
    return FALSE;
  if (!domain_allowed (r, page_host))
    return FALSE;
  return g_regex_match (r->re, url, 0, NULL);
}

/* Test the bucket for each token in the URL, then the unindexed leftovers. */
static gboolean
index_matches (GHashTable *index, GPtrArray *loose, const char *url_lower,
               const char *page_host, gboolean third_party, LyResourceKind kind)
{
  const char *p = url_lower;
  while (*p) {
    if (!token_char (*p)) {
      p++;
      continue;
    }
    const char *start = p;
    while (token_char (*p))
      p++;
    gsize len = (gsize) (p - start);
    if (len < MIN_KEYWORD)
      continue;

    g_autofree char *token = g_strndup (start, len);
    GPtrArray *bucket = g_hash_table_lookup (index, token);
    if (bucket == NULL)
      continue;
    for (guint i = 0; i < bucket->len; i++)
      if (rule_matches (g_ptr_array_index (bucket, i), url_lower, page_host, third_party, kind))
        return TRUE;
  }

  for (guint i = 0; i < loose->len; i++)
    if (rule_matches (g_ptr_array_index (loose, i), url_lower, page_host, third_party, kind))
      return TRUE;
  return FALSE;
}

gboolean
ly_block_should_block (LyBlock *b, const char *url, const char *page_url, LyResourceKind kind)
{
  g_return_val_if_fail (b != NULL, FALSE);
  if (url == NULL || *url == '\0')
    return FALSE;

  /* Only http(s) is ours to refuse. data:, blob: and about: are the page
   * talking to itself, and blocking those breaks more than it saves. */
  if (!g_str_has_prefix (url, "http://") && !g_str_has_prefix (url, "https://"))
    return FALSE;

  g_autofree char *url_lower = g_ascii_strdown (url, -1);
  g_autofree char *host = ly_uri_host (url);
  g_autofree char *page_host = page_url ? ly_uri_host (page_url) : NULL;

  gboolean third_party = FALSE;
  if (page_host && host) {
    g_autofree char *a = ly_uri_base_domain (url);
    g_autofree char *c = ly_uri_base_domain (page_url);
    third_party = (a && c && g_strcmp0 (a, c) != 0);
  }

  /* Exceptions win, and go first so an allowed request never pays for the
   * block tables. */
  if (host_in_table (b->hosts_allow, host))
    return FALSE;
  if (index_matches (b->index_allow, b->loose_allow, url_lower, page_host, third_party, kind))
    return FALSE;

  if (host_in_table (b->hosts, host)) {
    b->stats.blocked++;
    return TRUE;
  }
  if (index_matches (b->index, b->loose, url_lower, page_host, third_party, kind)) {
    b->stats.blocked++;
    return TRUE;
  }
  return FALSE;
}

char *
ly_block_cosmetic_css (LyBlock *b, const char *host)
{
  g_return_val_if_fail (b != NULL, NULL);

  GPtrArray *sel = g_ptr_array_new ();
  for (guint i = 0; i < b->cosmetic_generic->len; i++)
    g_ptr_array_add (sel, g_ptr_array_index (b->cosmetic_generic, i));

  for (const char *p = host; p && *p; ) {
    GPtrArray *list = g_hash_table_lookup (b->cosmetic_domain, p);
    if (list)
      for (guint i = 0; i < list->len; i++)
        g_ptr_array_add (sel, g_ptr_array_index (list, i));
    const char *dot = strchr (p, '.');
    p = dot ? dot + 1 : NULL;
  }

  if (sel->len == 0) {
    g_ptr_array_free (sel, TRUE);
    return NULL;
  }

  GString *css = g_string_new (NULL);
  for (guint i = 0; i < sel->len; i++) {
    if (i)
      g_string_append_c (css, ',');
    g_string_append (css, (const char *) g_ptr_array_index (sel, i));
  }
  g_string_append (css, "{display:none !important}");
  g_ptr_array_free (sel, TRUE);
  return g_string_free (css, FALSE);
}

LyResourceKind
ly_block_kind_from_context (int ctx)
{
  /* COREWEBVIEW2_WEB_RESOURCE_CONTEXT, in its declared order. */
  switch (ctx) {
    case 1:  return LY_RES_DOCUMENT;
    case 2:  return LY_RES_STYLESHEET;
    case 3:  return LY_RES_IMAGE;
    case 4:  return LY_RES_MEDIA;
    case 5:  return LY_RES_FONT;
    case 6:  return LY_RES_SCRIPT;
    case 7:  return LY_RES_XHR;
    case 8:  return LY_RES_XHR;          /* Fetch */
    case 9:  return LY_RES_SUBDOCUMENT;
    case 12: return LY_RES_WEBSOCKET;
    default: return LY_RES_OTHER;
  }
}

const LyBlockStats *
ly_block_stats (LyBlock *b)
{
  return &b->stats;
}

void
ly_block_reset_counters (LyBlock *b)
{
  b->stats.blocked = 0;
}

/* ------------------------------------------------------------- life cycle */

static void
bucket_free (gpointer p)
{
  g_ptr_array_free ((GPtrArray *) p, TRUE);
}

LyBlock *
ly_block_new (void)
{
  LyBlock *b = g_new0 (LyBlock, 1);
  b->hosts            = g_hash_table_new_full (g_str_hash, g_str_equal, g_free, NULL);
  b->hosts_allow      = g_hash_table_new_full (g_str_hash, g_str_equal, g_free, NULL);
  b->index            = g_hash_table_new_full (g_str_hash, g_str_equal, g_free, bucket_free);
  b->index_allow      = g_hash_table_new_full (g_str_hash, g_str_equal, g_free, bucket_free);
  b->loose            = g_ptr_array_new_with_free_func ((GDestroyNotify) rule_free);
  b->loose_allow      = g_ptr_array_new_with_free_func ((GDestroyNotify) rule_free);
  b->cosmetic_generic = g_ptr_array_new_with_free_func (g_free);
  b->cosmetic_domain  = g_hash_table_new_full (g_str_hash, g_str_equal, g_free, bucket_free);
  return b;
}

void
ly_block_free (LyBlock *b)
{
  if (b == NULL)
    return;
  g_hash_table_destroy (b->hosts);
  g_hash_table_destroy (b->hosts_allow);
  g_hash_table_destroy (b->index);
  g_hash_table_destroy (b->index_allow);
  g_ptr_array_free (b->loose, TRUE);
  g_ptr_array_free (b->loose_allow, TRUE);
  g_ptr_array_free (b->cosmetic_generic, TRUE);
  g_hash_table_destroy (b->cosmetic_domain);
  g_free (b);
}
