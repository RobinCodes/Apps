/* util.c — URL handling and small formatting helpers. */

#include "lyndon.h"

#include <string.h>

/* Schemes we hand to WebKit untouched. */
static const char *const KNOWN_SCHEMES[] = {
  "http://", "https://", "file://", "about:", "data:", "blob:",
  "ftp://", "lyndon:", "webkit:", "javascript:", NULL
};

/* Second-level registry suffixes that are not registrable on their own. This
 * is a pragmatic subset of the public suffix list — enough to get first-party
 * grouping right for the domains people actually browse, without carrying a
 * 250 kB table around. */
static const char *const MULTI_TLDS[] = {
  "co.uk", "org.uk", "me.uk", "ac.uk", "gov.uk", "net.uk", "sch.uk",
  "com.au", "net.au", "org.au", "edu.au", "gov.au", "id.au",
  "co.nz", "net.nz", "org.nz", "govt.nz", "ac.nz",
  "co.jp", "or.jp", "ne.jp", "ac.jp", "go.jp",
  "com.br", "net.br", "org.br", "gov.br",
  "com.cn", "net.cn", "org.cn", "gov.cn", "edu.cn",
  "co.in", "net.in", "org.in", "gov.in", "ac.in",
  "com.mx", "com.ar", "com.tr", "com.sg", "com.hk", "com.tw",
  "co.za", "co.kr", "co.il", "co.id", "co.th",
  "com.pl", "com.ua", "com.ru", "com.es", "com.pt", "com.gr",
  NULL
};

gboolean
ly_looks_like_url (const char *text)
{
  if (text == NULL || *text == '\0')
    return FALSE;

  for (int i = 0; KNOWN_SCHEMES[i] != NULL; i++)
    if (g_str_has_prefix (text, KNOWN_SCHEMES[i]))
      return TRUE;

  /* Anything with whitespace is a search phrase, not an address. */
  if (strpbrk (text, " \t\n") != NULL)
    return FALSE;

  if (g_str_has_prefix (text, "localhost") || g_str_has_prefix (text, "127.0.0.1"))
    return TRUE;

  /* host[:port][/path] — needs a dot before the first slash and a plausible
   * TLD after it, so that "foo/bar" stays a search but "example.com/x" does not. */
  const char *slash = strchr (text, '/');
  size_t authority_len = slash ? (size_t) (slash - text) : strlen (text);
  if (authority_len == 0)
    return FALSE;

  g_autofree char *authority = g_strndup (text, authority_len);
  char *colon = strchr (authority, ':');
  if (colon != NULL)
    *colon = '\0';

  const char *dot = strrchr (authority, '.');
  if (dot == NULL || dot == authority || dot[1] == '\0')
    return FALSE;

  for (const char *p = dot + 1; *p != '\0'; p++)
    if (!g_ascii_isalnum (*p) && *p != '-')
      return FALSE;

  return strlen (dot + 1) >= 2;
}

char *
ly_normalise_input (const char *text, const char *search_url)
{
  if (text == NULL)
    return NULL;

  g_autofree char *trimmed = g_strdup (text);
  g_strstrip (trimmed);
  if (*trimmed == '\0')
    return NULL;

  for (int i = 0; KNOWN_SCHEMES[i] != NULL; i++)
    if (g_str_has_prefix (trimmed, KNOWN_SCHEMES[i]))
      return g_steal_pointer (&trimmed);

  if (ly_looks_like_url (trimmed))
    return g_strconcat ("https://", trimmed, NULL);

  /* Fall through to a search. */
  g_autofree char *escaped = g_uri_escape_string (trimmed, NULL, FALSE);
  const char *tmpl = (search_url && strstr (search_url, "%s"))
                       ? search_url
                       : "https://duckduckgo.com/?q=%s";

  GString *out = g_string_new (NULL);
  for (const char *p = tmpl; *p != '\0'; p++) {
    if (p[0] == '%' && p[1] == 's') {
      g_string_append (out, escaped);
      p++;
    } else {
      g_string_append_c (out, *p);
    }
  }
  return g_string_free (out, FALSE);
}

char *
ly_uri_host (const char *uri)
{
  if (uri == NULL || *uri == '\0')
    return NULL;

  g_autoptr (GUri) parsed = g_uri_parse (uri, G_URI_FLAGS_NONE, NULL);
  if (parsed == NULL)
    return NULL;

  const char *host = g_uri_get_host (parsed);
  if (host == NULL || *host == '\0')
    return NULL;

  return g_strdup (host);
}

char *
ly_uri_base_domain (const char *uri)
{
  g_autofree char *host = ly_uri_host (uri);
  if (host == NULL)
    return NULL;

  /* Leave IP literals alone. */
  if (g_hostname_is_ip_address (host))
    return g_steal_pointer (&host);

  g_auto (GStrv) parts = g_strsplit (host, ".", -1);
  guint n = g_strv_length (parts);
  if (n < 3)
    return g_steal_pointer (&host);

  g_autofree char *last_two = g_strdup_printf ("%s.%s", parts[n - 2], parts[n - 1]);
  for (int i = 0; MULTI_TLDS[i] != NULL; i++) {
    if (g_ascii_strcasecmp (last_two, MULTI_TLDS[i]) == 0) {
      if (n < 4)
        return g_steal_pointer (&host);
      return g_strdup_printf ("%s.%s.%s", parts[n - 3], parts[n - 2], parts[n - 1]);
    }
  }
  return g_strdup_printf ("%s.%s", parts[n - 2], parts[n - 1]);
}

gboolean
ly_uri_is_secure (const char *uri)
{
  return uri != NULL &&
         (g_str_has_prefix (uri, "https://") ||
          g_str_has_prefix (uri, "file://")  ||
          g_str_has_prefix (uri, "lyndon:")  ||
          g_str_has_prefix (uri, "about:"));
}

gboolean
ly_uri_is_internal (const char *uri)
{
  return uri != NULL &&
         (g_str_has_prefix (uri, "lyndon:") || g_str_has_prefix (uri, "about:"));
}

char *
ly_pretty_uri (const char *uri)
{
  if (uri == NULL || *uri == '\0')
    return g_strdup ("");

  if (ly_uri_is_internal (uri))
    return g_strdup (uri);

  const char *p = uri;
  if (g_str_has_prefix (p, "https://"))
    p += 8;
  else if (g_str_has_prefix (p, "http://"))
    p += 7;

  if (g_str_has_prefix (p, "www."))
    p += 4;

  g_autofree char *out = g_uri_unescape_string (p, NULL);
  char *result = out ? g_steal_pointer (&out) : g_strdup (p);

  /* Drop a lone trailing slash: "example.com/" reads better as "example.com". */
  size_t len = strlen (result);
  if (len > 1 && result[len - 1] == '/' && strchr (result, '/') == result + len - 1)
    result[len - 1] = '\0';

  return result;
}

char *
ly_format_size (guint64 bytes)
{
  const char *units[] = { "B", "kB", "MB", "GB", "TB" };
  double value = (double) bytes;
  int unit = 0;

  while (value >= 1024.0 && unit < (int) G_N_ELEMENTS (units) - 1) {
    value /= 1024.0;
    unit++;
  }
  if (unit == 0)
    return g_strdup_printf ("%.0f %s", value, units[unit]);
  return g_strdup_printf ("%.1f %s", value, units[unit]);
}

char *
ly_escape_js_string (const char *s)
{
  if (s == NULL)
    return g_strdup ("");

  GString *out = g_string_sized_new (strlen (s) + 16);
  for (const char *p = s; *p != '\0'; p++) {
    switch (*p) {
      case '\\': g_string_append (out, "\\\\"); break;
      case '"':  g_string_append (out, "\\\""); break;
      case '\'': g_string_append (out, "\\'");  break;
      case '\n': g_string_append (out, "\\n");  break;
      case '\r': g_string_append (out, "\\r");  break;
      case '\t': g_string_append (out, "\\t");  break;
      case '<':  g_string_append (out, "\\x3c"); break;
      case '>':  g_string_append (out, "\\x3e"); break;
      default:   g_string_append_c (out, *p);   break;
    }
  }
  return g_string_free (out, FALSE);
}
