/* abp.c — Adblock Plus syntax to WebKit content-blocker JSON. See abp.h. */

#include "abp.h"

#include <string.h>

/* WebKit's separator class for the ABP '^' token: anything that is not a
 * legal character inside a hostname or path segment. */
#define SEP_CLASS "[^-_.%a-zA-Z0-9]"

/* The resource types WebKit has accepted since content blockers shipped. Newer
 * names (fetch, websocket, ping) exist but an unknown type makes WebKit reject
 * the whole list, so everything folds into this conservative set. */
static const char *const ALL_TYPES[] = {
  "document", "image", "style-sheet", "script", "font", "raw", "svg-document",
  "media", "popup",
};
#define N_TYPES G_N_ELEMENTS (ALL_TYPES)

struct _LyAbp {
  GString  *blocks;
  GString  *cosmetics;
  GString  *exceptions;
  GString  *generic_selectors;   /* merged into one rule to keep the list small */
  guint     generic_count;
  gboolean  cosmetic_enabled;
  guint     limit;
  LyAbpStats stats;
};

/* ------------------------------------------------------------------- json */

static void
json_escape_into (GString *out, const char *s)
{
  for (const char *p = s; *p != '\0'; p++) {
    switch (*p) {
      case '"':  g_string_append (out, "\\\""); break;
      case '\\': g_string_append (out, "\\\\"); break;
      case '\n': g_string_append (out, "\\n");  break;
      case '\r': g_string_append (out, "\\r");  break;
      case '\t': g_string_append (out, "\\t");  break;
      default:
        if ((guchar) *p < 0x20)
          g_string_append_printf (out, "\\u%04x", (guchar) *p);
        else
          g_string_append_c (out, *p);
    }
  }
}

static void
append_json_string (GString *out, const char *s)
{
  g_string_append_c (out, '"');
  json_escape_into (out, s);
  g_string_append_c (out, '"');
}

static void
append_json_array (GString *out, GPtrArray *items)
{
  g_string_append_c (out, '[');
  for (guint i = 0; i < items->len; i++) {
    if (i > 0)
      g_string_append_c (out, ',');
    append_json_string (out, g_ptr_array_index (items, i));
  }
  g_string_append_c (out, ']');
}

/* ---------------------------------------------------------------- pattern */

static gboolean
regex_is_safe (const char *re)
{
  /* WebKit's engine has no lookaround, no backreferences and no bounded
   * repeats. Anything using them would be rejected at compile time. */
  if (strstr (re, "(?") || strstr (re, "\\b") || strstr (re, "\\B") ||
      strstr (re, "\\1") || strstr (re, "{"))
    return FALSE;
  return TRUE;
}

char *
ly_abp_pattern_to_regex (const char *pattern)
{
  if (pattern == NULL || *pattern == '\0')
    return NULL;

  size_t len = strlen (pattern);

  /* A literal /regex/ rule is passed through if WebKit can digest it. */
  if (len > 2 && pattern[0] == '/' && pattern[len - 1] == '/') {
    g_autofree char *inner = g_strndup (pattern + 1, len - 2);
    return regex_is_safe (inner) ? g_steal_pointer (&inner) : NULL;
  }

  g_autofree char *lower = g_ascii_strdown (pattern, -1);
  const char *p = lower;
  GString *out = g_string_sized_new (len * 2);

  if (g_str_has_prefix (p, "||")) {
    /* Domain anchor: the host, or any subdomain of it. The trailing dot in the
     * optional group is what keeps ||example.com from matching notexample.com. */
    g_string_append (out, "^https?://([^/]+\\.)?");
    p += 2;
  } else if (p[0] == '|') {
    g_string_append_c (out, '^');
    p += 1;
  }

  /* A trailing '|' anchors the end of the URL. */
  size_t rest = strlen (p);
  gboolean anchor_end = (rest > 0 && p[rest - 1] == '|');
  if (anchor_end)
    rest--;

  for (size_t i = 0; i < rest; i++) {
    char c = p[i];
    switch (c) {
      case '*':
        /* Collapse runs of wildcards; ".*.*" only makes the DFA bigger. */
        while (i + 1 < rest && p[i + 1] == '*')
          i++;
        g_string_append (out, ".*");
        break;
      case '^':
        g_string_append (out, SEP_CLASS);
        break;
      case '.': case '+': case '?': case '(': case ')':
      case '[': case ']': case '{': case '}': case '$':
      case '\\': case '|':
        g_string_append_c (out, '\\');
        g_string_append_c (out, c);
        break;
      default:
        g_string_append_c (out, c);
    }
  }

  if (anchor_end)
    g_string_append_c (out, '$');

  if (out->len == 0) {
    g_string_free (out, TRUE);
    return NULL;
  }

  /* A bare ".*" matches everything and would be a very expensive no-op. */
  if (g_strcmp0 (out->str, ".*") == 0) {
    g_string_free (out, TRUE);
    return NULL;
  }

  return g_string_free (out, FALSE);
}

/* ---------------------------------------------------------------- options */

typedef struct {
  gboolean   valid;
  gboolean   unsupported;
  gboolean   third_party;
  gboolean   first_party;
  gboolean   match_case;
  gboolean   has_types;
  gboolean   types[N_TYPES];
  GPtrArray *if_domain;
  GPtrArray *unless_domain;
} Options;

static int
type_index (const char *name)
{
  /* ABP type names on the left, WebKit resource types on the right. */
  static const struct { const char *abp, *webkit; } MAP[] = {
    { "script",           "script"       },
    { "image",            "image"        },
    { "stylesheet",       "style-sheet"  },
    { "css",              "style-sheet"  },
    { "font",             "font"         },
    { "media",            "media"        },
    { "object",           "media"        },
    { "object-subrequest","raw"          },
    { "xmlhttprequest",   "raw"          },
    { "xhr",              "raw"          },
    { "websocket",        "raw"          },
    { "ping",             "raw"          },
    { "beacon",           "raw"          },
    { "other",            "raw"          },
    { "subdocument",      "document"     },
    { "frame",            "document"     },
    { "document",         "document"     },
    { "popup",            "popup"        },
  };

  for (guint i = 0; i < G_N_ELEMENTS (MAP); i++) {
    if (g_strcmp0 (name, MAP[i].abp) == 0) {
      for (guint t = 0; t < N_TYPES; t++)
        if (g_strcmp0 (MAP[i].webkit, ALL_TYPES[t]) == 0)
          return (int) t;
    }
  }
  return -1;
}

static void
options_init (Options *o)
{
  memset (o, 0, sizeof *o);
  o->valid         = TRUE;
  o->if_domain     = g_ptr_array_new_with_free_func (g_free);
  o->unless_domain = g_ptr_array_new_with_free_func (g_free);
}

static void
options_clear (Options *o)
{
  g_clear_pointer (&o->if_domain, g_ptr_array_unref);
  g_clear_pointer (&o->unless_domain, g_ptr_array_unref);
}

static void
parse_domain_option (Options *o, const char *value)
{
  g_auto (GStrv) domains = g_strsplit (value, "|", -1);
  for (int i = 0; domains[i] != NULL; i++) {
    const char *d = domains[i];
    gboolean negated = (*d == '~');
    if (negated)
      d++;
    if (*d == '\0')
      continue;

    g_autofree char *lower = g_ascii_strdown (d, -1);
    /* WebKit's '*' prefix means "this domain and every subdomain of it". */
    g_ptr_array_add (negated ? o->unless_domain : o->if_domain,
                     g_strdup_printf ("*%s", lower));
  }
}

static void
parse_options (Options *o, const char *text)
{
  g_auto (GStrv) parts = g_strsplit (text, ",", -1);
  gboolean negated_types[N_TYPES] = { FALSE };
  gboolean saw_negated_type = FALSE;

  for (int i = 0; parts[i] != NULL && o->valid; i++) {
    g_autofree char *opt = g_strdup (parts[i]);
    g_strstrip (opt);
    if (*opt == '\0')
      continue;

    char *eq = strchr (opt, '=');
    if (eq != NULL) {
      *eq = '\0';
      const char *value = eq + 1;
      if (g_strcmp0 (opt, "domain") == 0 || g_strcmp0 (opt, "from") == 0) {
        parse_domain_option (o, value);
        continue;
      }
      /* csp=, redirect=, removeparam=, rewrite=, header=, method=… are all
       * request-rewriting features WebKit has no equivalent for. */
      o->unsupported = TRUE;
      continue;
    }

    gboolean negated = (*opt == '~');
    const char *name = negated ? opt + 1 : opt;

    if (g_strcmp0 (name, "third-party") == 0 || g_strcmp0 (name, "3p") == 0) {
      if (negated) o->first_party = TRUE; else o->third_party = TRUE;
      continue;
    }
    if (g_strcmp0 (name, "first-party") == 0 || g_strcmp0 (name, "1p") == 0) {
      if (negated) o->third_party = TRUE; else o->first_party = TRUE;
      continue;
    }
    if (g_strcmp0 (name, "match-case") == 0) { o->match_case = TRUE; continue; }
    if (g_strcmp0 (name, "important") == 0)  { continue; }  /* no priorities */
    if (g_strcmp0 (name, "all") == 0)        { continue; }  /* every type    */
    if (g_strcmp0 (name, "badfilter") == 0)  { o->valid = FALSE; continue; }

    int t = type_index (name);
    if (t >= 0) {
      if (negated) {
        negated_types[t] = TRUE;
        saw_negated_type = TRUE;
      } else {
        o->types[t]   = TRUE;
        o->has_types  = TRUE;
      }
      continue;
    }

    /* Unknown option: refuse the rule rather than guess at its meaning. */
    o->unsupported = TRUE;
  }

  if (saw_negated_type && !o->has_types) {
    for (guint t = 0; t < N_TYPES; t++)
      o->types[t] = !negated_types[t];
    o->has_types = TRUE;
  }
}

/* ------------------------------------------------------------- emitting */

static void
append_trigger (GString *out, const char *url_filter, const Options *o)
{
  g_string_append (out, "\"trigger\":{\"url-filter\":");
  append_json_string (out, url_filter);

  if (o->match_case)
    g_string_append (out, ",\"url-filter-is-case-sensitive\":true");

  if (o->has_types) {
    g_string_append (out, ",\"resource-type\":[");
    gboolean first = TRUE;
    for (guint t = 0; t < N_TYPES; t++) {
      if (!o->types[t])
        continue;
      if (!first)
        g_string_append_c (out, ',');
      append_json_string (out, ALL_TYPES[t]);
      first = FALSE;
    }
    g_string_append_c (out, ']');
  }

  if (o->third_party && !o->first_party)
    g_string_append (out, ",\"load-type\":[\"third-party\"]");
  else if (o->first_party && !o->third_party)
    g_string_append (out, ",\"load-type\":[\"first-party\"]");

  /* WebKit rejects a trigger carrying both, so if-domain wins. */
  if (o->if_domain->len > 0) {
    g_string_append (out, ",\"if-domain\":");
    append_json_array (out, o->if_domain);
  } else if (o->unless_domain->len > 0) {
    g_string_append (out, ",\"unless-domain\":");
    append_json_array (out, o->unless_domain);
  }

  g_string_append_c (out, '}');
}

static void
emit_rule (GString *dest, const char *url_filter, const Options *o, const char *action)
{
  if (dest->len > 0)
    g_string_append_c (dest, ',');
  g_string_append_c (dest, '{');
  append_trigger (dest, url_filter, o);
  g_string_append_printf (dest, ",\"action\":{\"type\":\"%s\"}}", action);
}

/* ------------------------------------------------------------- lifecycle */

LyAbp *
ly_abp_new (void)
{
  LyAbp *abp = g_new0 (LyAbp, 1);
  abp->blocks            = g_string_sized_new (1 << 16);
  abp->cosmetics         = g_string_sized_new (1 << 12);
  abp->exceptions        = g_string_sized_new (1 << 12);
  abp->generic_selectors = g_string_sized_new (1 << 12);
  abp->cosmetic_enabled  = TRUE;
  abp->limit             = 60000;
  return abp;
}

void
ly_abp_free (LyAbp *abp)
{
  if (abp == NULL)
    return;
  g_string_free (abp->blocks, TRUE);
  g_string_free (abp->cosmetics, TRUE);
  g_string_free (abp->exceptions, TRUE);
  g_string_free (abp->generic_selectors, TRUE);
  g_free (abp);
}

void ly_abp_set_cosmetic (LyAbp *abp, gboolean enabled) { abp->cosmetic_enabled = enabled; }
void ly_abp_set_limit    (LyAbp *abp, guint max_rules)  { abp->limit = max_rules; }

static guint
emitted (const LyAbp *abp)
{
  return abp->stats.blocks + abp->stats.cosmetics + abp->stats.exceptions;
}

/* --------------------------------------------------------------- cosmetic */

static void
handle_cosmetic (LyAbp *abp, const char *line, const char *sep)
{
  /* Only plain '##' hiding is portable. '#@#' (un-hide), '#?#' (:has and
   * friends) and '#$#' (scriptlets) all need a JS engine we deliberately do
   * not run on every page. */
  if (sep[1] == '@' || sep[1] == '?' || sep[1] == '$' || sep[1] == '%') {
    abp->stats.skipped++;
    return;
  }

  const char *selector = sep + 2;
  if (*selector == '\0' || strchr (selector, '{') != NULL) {
    abp->stats.skipped++;
    return;
  }

  if (!abp->cosmetic_enabled || emitted (abp) >= abp->limit) {
    abp->stats.skipped++;
    return;
  }

  g_autofree char *domain_part = g_strndup (line, (size_t) (sep - line));
  g_strstrip (domain_part);

  if (*domain_part == '\0') {
    /* Generic rule: accumulate selectors and emit a single combined rule. */
    if (abp->generic_selectors->len > 0)
      g_string_append_c (abp->generic_selectors, ',');
    g_string_append (abp->generic_selectors, selector);
    abp->generic_count++;
    return;
  }

  Options o;
  options_init (&o);
  parse_domain_option (&o, domain_part);

  if (o.if_domain->len == 0) {
    options_clear (&o);
    abp->stats.skipped++;
    return;
  }

  GString *dest = abp->cosmetics;
  if (dest->len > 0)
    g_string_append_c (dest, ',');
  g_string_append_c (dest, '{');
  append_trigger (dest, ".*", &o);
  g_string_append (dest, ",\"action\":{\"type\":\"css-display-none\",\"selector\":");
  append_json_string (dest, selector);
  g_string_append (dest, "}}");

  abp->stats.cosmetics++;
  options_clear (&o);
}

/* ------------------------------------------------------------------ lines */

void
ly_abp_add_line (LyAbp *abp, const char *raw)
{
  if (raw == NULL)
    return;

  g_autofree char *line = g_strdup (raw);
  g_strstrip (line);

  if (*line == '\0' || *line == '!' || *line == '[' || *line == '#') {
    /* '#' alone starts a hosts-file comment; '##' is cosmetic syntax and is
     * caught below because it never reaches here with a leading '#'. */
    if (line[0] == '#' && line[1] == '#')
      ; /* fall through to cosmetic handling */
    else
      return;
  }

  /* hosts-file lines: "0.0.0.0 tracker.example" becomes "||tracker.example^". */
  if (g_str_has_prefix (line, "0.0.0.0 ") || g_str_has_prefix (line, "127.0.0.1 ") ||
      g_str_has_prefix (line, "::1 ")) {
    g_auto (GStrv) fields = g_strsplit_set (line, " \t", -1);
    for (int i = 1; fields[i] != NULL; i++) {
      if (*fields[i] == '\0')
        continue;
      if (*fields[i] == '#')
        break;
      if (g_strcmp0 (fields[i], "localhost") == 0 ||
          g_strcmp0 (fields[i], "localhost.localdomain") == 0)
        break;
      g_autofree char *rule = g_strdup_printf ("||%s^", fields[i]);
      ly_abp_add_line (abp, rule);
      break;
    }
    return;
  }

  const char *sep = strstr (line, "##");
  if (sep == NULL) {
    const char *alt = strstr (line, "#@#");
    if (alt == NULL) alt = strstr (line, "#?#");
    if (alt == NULL) alt = strstr (line, "#$#");
    if (alt != NULL) { abp->stats.skipped++; return; }
  }
  if (sep != NULL) {
    handle_cosmetic (abp, line, sep);
    return;
  }

  gboolean is_exception = g_str_has_prefix (line, "@@");
  const char *body = is_exception ? line + 2 : line;

  /* Split off the option list at the last '$' that is not inside a /regex/. */
  g_autofree char *pattern = NULL;
  g_autofree char *option_text = NULL;
  {
    size_t blen = strlen (body);
    gboolean in_regex = (blen > 1 && body[0] == '/');
    const char *dollar = NULL;
    for (size_t i = 0; i < blen; i++) {
      if (in_regex && body[i] == '/' && i > 0)
        in_regex = FALSE;
      else if (body[i] == '$' && !in_regex)
        { dollar = body + i; break; }
    }
    if (dollar != NULL) {
      pattern     = g_strndup (body, (size_t) (dollar - body));
      option_text = g_strdup (dollar + 1);
    } else {
      pattern = g_strdup (body);
    }
  }

  Options o;
  options_init (&o);
  if (option_text != NULL)
    parse_options (&o, option_text);

  if (!o.valid || o.unsupported) {
    options_clear (&o);
    abp->stats.skipped++;
    return;
  }

  g_autofree char *url_filter = ly_abp_pattern_to_regex (pattern);
  if (url_filter == NULL) {
    options_clear (&o);
    abp->stats.invalid++;
    return;
  }

  if (emitted (abp) >= abp->limit) {
    options_clear (&o);
    abp->stats.skipped++;
    return;
  }

  if (is_exception) {
    emit_rule (abp->exceptions, url_filter, &o, "ignore-previous-rules");
    abp->stats.exceptions++;
  } else {
    emit_rule (abp->blocks, url_filter, &o, "block");
    abp->stats.blocks++;
  }

  options_clear (&o);
}

void
ly_abp_add_text (LyAbp *abp, const char *text)
{
  if (text == NULL)
    return;

  const char *start = text;
  for (const char *p = text; ; p++) {
    if (*p == '\n' || *p == '\0') {
      if (p > start) {
        g_autofree char *line = g_strndup (start, (size_t) (p - start));
        ly_abp_add_line (abp, line);
      }
      if (*p == '\0')
        break;
      start = p + 1;
    }
  }
}

void
ly_abp_add_cookie_block (LyAbp *abp, const char *domain)
{
  if (domain == NULL || *domain == '\0' || emitted (abp) >= abp->limit)
    return;

  g_autofree char *pattern = g_strdup_printf ("||%s^", domain);
  g_autofree char *url_filter = ly_abp_pattern_to_regex (pattern);
  if (url_filter == NULL)
    return;

  Options o;
  options_init (&o);
  o.third_party = TRUE;
  emit_rule (abp->blocks, url_filter, &o, "block-cookies");
  abp->stats.blocks++;
  options_clear (&o);
}

void
ly_abp_add_https_upgrade (LyAbp *abp)
{
  if (emitted (abp) >= abp->limit)
    return;

  Options o;
  options_init (&o);

  /* Loopback and .local names have no certificate to upgrade to, so a blanket
   * rule would simply break local development. Real browsers carve out the
   * same set. */
  static const char *const EXEMPT[] = {
    "localhost", "*localhost", "*.local", "*.localhost", "*.test", "*.internal",
  };
  for (guint i = 0; i < G_N_ELEMENTS (EXEMPT); i++)
    g_ptr_array_add (o.unless_domain, g_strdup (EXEMPT[i]));

  /* Documents and subresources both; leaving subresources on http would keep
   * the page mixed-content anyway. */
  emit_rule (abp->blocks, "^http://", &o, "make-https");
  abp->stats.blocks++;
  options_clear (&o);
}

/* ----------------------------------------------------------------- finish */

char *
ly_abp_finish (LyAbp *abp, LyAbpStats *stats)
{
  /* One combined rule for every generic hiding selector: WebKit is happy with
   * a long selector list and it keeps the compiled DFA far smaller than one
   * rule per selector would. */
  if (abp->generic_selectors->len > 0) {
    if (abp->cosmetics->len > 0)
      g_string_append_c (abp->cosmetics, ',');
    g_string_append (abp->cosmetics,
                     "{\"trigger\":{\"url-filter\":\".*\"},"
                     "\"action\":{\"type\":\"css-display-none\",\"selector\":");
    append_json_string (abp->cosmetics, abp->generic_selectors->str);
    g_string_append (abp->cosmetics, "}}");
    abp->stats.cosmetics += abp->generic_count;
  }

  GString *out = g_string_sized_new (abp->blocks->len + abp->cosmetics->len +
                                     abp->exceptions->len + 64);
  g_string_append_c (out, '[');

  /* Order matters: WebKit applies rules in sequence and ignore-previous-rules
   * only cancels what came before it. */
  const GString *sections[] = { abp->blocks, abp->cosmetics, abp->exceptions };
  gboolean any = FALSE;
  for (guint i = 0; i < G_N_ELEMENTS (sections); i++) {
    if (sections[i]->len == 0)
      continue;
    if (any)
      g_string_append_c (out, ',');
    g_string_append_len (out, sections[i]->str, (gssize) sections[i]->len);
    any = TRUE;
  }
  g_string_append_c (out, ']');

  if (stats != NULL)
    *stats = abp->stats;

  return g_string_free (out, FALSE);
}
