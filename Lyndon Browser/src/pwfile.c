/* pwfile.c — see pwfile.h. */

#include "pwfile.h"

#include <string.h>
#include <zlib.h>

/* A spreadsheet of logins is a small file. These caps exist so that a wrong
 * file picked by mistake fails quickly instead of eating the machine. */
#define MAX_FILE_BYTES  (64u * 1024 * 1024)
#define MAX_ROWS        20000
#define MAX_COLUMNS     256

/* A table is rows of cells; every parser below produces one, and only the
 * column mapping at the bottom knows what the cells mean. */
typedef GPtrArray Table;   /* of GPtrArray of char* */

static Table *
table_new (void)
{
  return g_ptr_array_new_with_free_func ((GDestroyNotify) g_ptr_array_unref);
}

static GPtrArray *
row_new (void)
{
  return g_ptr_array_new_with_free_func (g_free);
}

static const char *
cell (GPtrArray *row, int index)
{
  if (index < 0 || (guint) index >= row->len)
    return "";
  return g_ptr_array_index (row, index) ?: "";
}

/* ------------------------------------------------------------------ csv */

/* RFC 4180 with the usual real-world slack: quotes anywhere in a field, "" for
 * a literal quote, newlines inside quotes, and CRLF or LF line endings. */
static Table *
parse_csv (const char *text, char delimiter)
{
  Table      *table = table_new ();
  GPtrArray  *row   = row_new ();
  GString    *field = g_string_new (NULL);
  gboolean    quoted = FALSE;
  gboolean    dirty  = FALSE;   /* something was written to this row */

  for (const char *p = text; ; p++) {
    char c = *p;

    if (quoted) {
      if (c == '\0') break;
      if (c == '"') {
        if (p[1] == '"') { g_string_append_c (field, '"'); p++; }
        else quoted = FALSE;
      } else {
        g_string_append_c (field, c);
      }
      continue;
    }

    if (c == '"' && field->len == 0) { quoted = TRUE; dirty = TRUE; continue; }

    if (c == delimiter) {
      g_ptr_array_add (row, g_strdup (field->str));
      g_string_truncate (field, 0);
      dirty = TRUE;
      continue;
    }

    if (c == '\r')
      continue;      /* CRLF, and a lone CR is not worth honouring */

    if (c == '\n' || c == '\0') {
      if (dirty || field->len > 0) {
        g_ptr_array_add (row, g_strdup (field->str));
        g_string_truncate (field, 0);
        if (table->len < MAX_ROWS)
          g_ptr_array_add (table, row);
        else
          g_ptr_array_unref (row);
        row = row_new ();
      }
      dirty = FALSE;
      if (c == '\0') break;
      continue;
    }

    g_string_append_c (field, c);
  }

  g_ptr_array_unref (row);
  g_string_free (field, TRUE);
  return table;
}

/* Semicolons are what a spreadsheet saves as "CSV" in most of Europe, and tabs
 * are what a copy-and-paste out of one produces. Counting outside quotes is
 * what stops a comma inside a note from voting. */
static char
sniff_delimiter (const char *text)
{
  static const char CANDIDATES[] = { ',', ';', '\t', '|' };
  int count[G_N_ELEMENTS (CANDIDATES)] = { 0 };
  gboolean quoted = FALSE;

  /* The header line decides; it is the row whose shape matters. */
  for (const char *p = text; *p != '\0' && *p != '\n'; p++) {
    if (*p == '"') { quoted = !quoted; continue; }
    if (quoted) continue;
    for (guint i = 0; i < G_N_ELEMENTS (CANDIDATES); i++)
      if (*p == CANDIDATES[i]) count[i]++;
  }

  guint best = 0;
  for (guint i = 1; i < G_N_ELEMENTS (CANDIDATES); i++)
    if (count[i] > count[best]) best = i;

  return count[best] > 0 ? CANDIDATES[best] : ',';
}

/* -------------------------------------------------------------- zip file */

/* Just enough of the format to pull a few named members out of an .xlsx or
 * .ods. Both are zip archives of XML, and reading them here means importing a
 * spreadsheet does not need a spreadsheet program installed. */

typedef struct {
  const guchar *bytes;
  gsize         size;
} Zip;

static guint32
u32 (const guchar *p) { return p[0] | (p[1] << 8) | (p[2] << 16) | ((guint32) p[3] << 24); }
static guint16
u16 (const guchar *p) { return p[0] | (p[1] << 8); }

/* Offset of the central directory, or -1. */
static gssize
zip_central_directory (const Zip *zip, guint16 *entries_out)
{
  if (zip->size < 22)
    return -1;

  /* The end-of-directory record is last, after a comment of up to 64 KiB. */
  gsize limit = MIN (zip->size, (gsize) 22 + 0xFFFF);
  for (gsize back = 22; back <= limit; back++) {
    const guchar *p = zip->bytes + zip->size - back;
    if (u32 (p) != 0x06054b50)
      continue;
    guint16 entries = u16 (p + 10);
    guint32 offset  = u32 (p + 16);
    if (offset == 0xFFFFFFFFu || entries == 0xFFFFu)
      return -1;             /* zip64; no spreadsheet of logins is that big */
    if (offset >= zip->size)
      return -1;
    if (entries_out != NULL)
      *entries_out = entries;
    return (gssize) offset;
  }
  return -1;
}

/* Called for each member name in turn. Returning TRUE picks that member;
 * returning FALSE throughout is how a caller walks the whole archive looking
 * for something it can only recognise by comparison. */
typedef gboolean (*ZipVisitFn) (const char *name, gpointer user_data);

static guchar *
inflate_raw (const guchar *in, gsize in_size, gsize expected)
{
  if (expected == 0 || expected > MAX_FILE_BYTES)
    return NULL;

  guchar *out = g_malloc (expected + 1);
  z_stream stream = { 0 };
  stream.next_in   = (Bytef *) in;
  stream.avail_in  = in_size;
  stream.next_out  = out;
  stream.avail_out = expected;

  /* Negative window bits: a zip member is a raw deflate stream with no
   * zlib header of its own. */
  if (inflateInit2 (&stream, -MAX_WBITS) != Z_OK) {
    g_free (out);
    return NULL;
  }
  int status = inflate (&stream, Z_FINISH);
  inflateEnd (&stream);

  if (status != Z_STREAM_END) {
    g_free (out);
    return NULL;
  }
  out[stream.total_out] = '\0';
  return out;
}

/* Decompress the first member whose name is accepted by match. Members here
 * are XML documents, so the result is NUL-terminated and its length is the
 * caller's business rather than this function's. */
static char *
zip_read (const Zip *zip, ZipVisitFn match, gpointer user_data)
{
  guint16 entries = 0;
  gssize  offset  = zip_central_directory (zip, &entries);
  if (offset < 0)
    return NULL;

  gsize at = (gsize) offset;
  for (guint16 i = 0; i < entries; i++) {
    if (at + 46 > zip->size || u32 (zip->bytes + at) != 0x02014b50)
      return NULL;

    const guchar *entry = zip->bytes + at;
    guint16 method     = u16 (entry + 10);
    guint32 packed     = u32 (entry + 20);
    guint32 plain      = u32 (entry + 24);
    guint16 name_len   = u16 (entry + 28);
    guint16 extra_len  = u16 (entry + 30);
    guint16 cmt_len    = u16 (entry + 32);
    guint32 local      = u32 (entry + 42);

    gsize next = at + 46 + name_len + extra_len + cmt_len;
    if (next > zip->size)
      return NULL;

    g_autofree char *name = g_strndup ((const char *) entry + 46, name_len);
    if (!match (name, user_data)) { at = next; continue; }

    /* The directory says where the member's own header is; the payload starts
     * past that header's own variable-length name and extra fields. */
    if ((gsize) local + 30 > zip->size || u32 (zip->bytes + local) != 0x04034b50)
      return NULL;
    const guchar *lh = zip->bytes + local;
    gsize data = (gsize) local + 30 + u16 (lh + 26) + u16 (lh + 28);
    if (data + packed > zip->size)
      return NULL;

    guchar *bytes = NULL;

    if (method == 0) {                      /* stored */
      if (packed > MAX_FILE_BYTES)
        return NULL;
      bytes = g_malloc (packed + 1);
      memcpy (bytes, zip->bytes + data, packed);
      bytes[packed] = '\0';
    } else if (method == 8) {
      bytes = inflate_raw (zip->bytes + data, packed, plain);
    }

    return (char *) bytes;
  }
  return NULL;
}

static gboolean
name_is (const char *name, gpointer user_data)
{
  return g_strcmp0 (name, user_data) == 0;
}

/* Which sheet is "the first" is recorded in the workbook relationships, but
 * every writer in practice names them sheet1.xml upward, and taking the
 * lowest-named worksheet gets the same answer without a second parse. */
typedef struct { char *best; } SheetPick;

static gboolean
pick_worksheet (const char *name, gpointer user_data)
{
  SheetPick *pick = user_data;
  if (!g_str_has_prefix (name, "xl/worksheets/") || !g_str_has_suffix (name, ".xml"))
    return FALSE;
  if (pick->best == NULL || strcmp (name, pick->best) < 0) {
    g_free (pick->best);
    pick->best = g_strdup (name);
  }
  return FALSE;   /* never stop: we want the smallest name, not the first */
}

/* ----------------------------------------------------------------- xlsx */

typedef struct {
  GPtrArray *shared;      /* char* */
  GString   *text;        /* the string being assembled */
  gboolean   in_si;
  gboolean   in_t;
} SharedParse;

static void
shared_start (GMarkupParseContext *ctx, const char *element,
              const char **names, const char **values,
              gpointer data, GError **error)
{
  SharedParse *parse = data;
  if (g_strcmp0 (element, "si") == 0) {
    parse->in_si = TRUE;
    g_string_truncate (parse->text, 0);
  } else if (g_strcmp0 (element, "t") == 0 && parse->in_si) {
    parse->in_t = TRUE;
  }
}

static void
shared_end (GMarkupParseContext *ctx, const char *element,
            gpointer data, GError **error)
{
  SharedParse *parse = data;
  if (g_strcmp0 (element, "t") == 0) {
    parse->in_t = FALSE;
  } else if (g_strcmp0 (element, "si") == 0) {
    parse->in_si = FALSE;
    g_ptr_array_add (parse->shared, g_strdup (parse->text->str));
  }
}

static void
shared_text (GMarkupParseContext *ctx, const char *text, gsize len,
             gpointer data, GError **error)
{
  SharedParse *parse = data;
  if (parse->in_t)
    g_string_append_len (parse->text, text, len);
}

static GPtrArray *
parse_shared_strings (const char *xml)
{
  SharedParse parse = {
    .shared = g_ptr_array_new_with_free_func (g_free),
    .text   = g_string_new (NULL),
  };
  static const GMarkupParser parser = {
    shared_start, shared_end, shared_text, NULL, NULL
  };

  GMarkupParseContext *ctx =
    g_markup_parse_context_new (&parser, G_MARKUP_TREAT_CDATA_AS_TEXT, &parse, NULL);
  g_markup_parse_context_parse (ctx, xml, -1, NULL);
  g_markup_parse_context_free (ctx);

  g_string_free (parse.text, TRUE);
  return parse.shared;
}

typedef struct {
  Table      *table;
  GPtrArray  *shared;
  GPtrArray  *row;
  GString    *value;
  int         column;      /* from the cell's A1 reference */
  gboolean    is_shared;
  gboolean    collecting;  /* inside <v> or an inline <t> */
} SheetParse;

/* "BC12" -> 54. Anything unparseable falls back to appending. */
static int
column_of (const char *reference)
{
  int index = 0;
  for (const char *p = reference; *p != '\0'; p++) {
    if (g_ascii_isdigit (*p)) break;
    if (!g_ascii_isalpha (*p)) return -1;
    index = index * 26 + (g_ascii_toupper (*p) - 'A' + 1);
  }
  return index > 0 ? index - 1 : -1;
}

static const char *
attribute (const char **names, const char **values, const char *want)
{
  for (guint i = 0; names[i] != NULL; i++)
    if (g_strcmp0 (names[i], want) == 0)
      return values[i];
  return NULL;
}

static void
sheet_start (GMarkupParseContext *ctx, const char *element,
             const char **names, const char **values,
             gpointer data, GError **error)
{
  SheetParse *parse = data;

  if (g_strcmp0 (element, "row") == 0) {
    parse->row = row_new ();
  } else if (g_strcmp0 (element, "c") == 0 && parse->row != NULL) {
    const char *type = attribute (names, values, "t");
    const char *ref  = attribute (names, values, "r");
    parse->is_shared = g_strcmp0 (type, "s") == 0;
    parse->column    = ref != NULL ? column_of (ref) : -1;
    g_string_truncate (parse->value, 0);
  } else if ((g_strcmp0 (element, "v") == 0 || g_strcmp0 (element, "t") == 0) &&
             parse->row != NULL) {
    parse->collecting = TRUE;
  }
}

static void
sheet_end (GMarkupParseContext *ctx, const char *element,
           gpointer data, GError **error)
{
  SheetParse *parse = data;

  if (g_strcmp0 (element, "v") == 0 || g_strcmp0 (element, "t") == 0) {
    parse->collecting = FALSE;
    return;
  }

  if (g_strcmp0 (element, "c") == 0 && parse->row != NULL) {
    const char *text = parse->value->str;
    g_autofree char *resolved = NULL;

    if (parse->is_shared) {
      gint64 index = g_ascii_strtoll (text, NULL, 10);
      resolved = (index >= 0 && index < parse->shared->len)
        ? g_strdup (g_ptr_array_index (parse->shared, index))
        : g_strdup ("");
    } else {
      resolved = g_strdup (text);
    }

    /* Empty cells are skipped entirely in the file, so the A1 reference is the
     * only thing that keeps the columns lined up. */
    int at = parse->column >= 0 ? parse->column : (int) parse->row->len;
    if (at < MAX_COLUMNS) {
      while ((int) parse->row->len < at)
        g_ptr_array_add (parse->row, g_strdup (""));
      if ((int) parse->row->len == at)
        g_ptr_array_add (parse->row, g_steal_pointer (&resolved));
    }
    return;
  }

  if (g_strcmp0 (element, "row") == 0 && parse->row != NULL) {
    if (parse->table->len < MAX_ROWS)
      g_ptr_array_add (parse->table, parse->row);
    else
      g_ptr_array_unref (parse->row);
    parse->row = NULL;
  }
}

static void
sheet_text (GMarkupParseContext *ctx, const char *text, gsize len,
            gpointer data, GError **error)
{
  SheetParse *parse = data;
  if (parse->collecting)
    g_string_append_len (parse->value, text, len);
}

static Table *
parse_xlsx (const Zip *zip)
{
  g_autofree char *shared_xml =
    zip_read (zip, name_is, (gpointer) "xl/sharedStrings.xml");

  SheetPick pick = { 0 };
  zip_read (zip, pick_worksheet, &pick);
  if (pick.best == NULL)
    return NULL;

  g_autofree char *sheet_name = pick.best;
  g_autofree char *sheet_xml  = zip_read (zip, name_is, sheet_name);
  if (sheet_xml == NULL)
    return NULL;

  SheetParse parse = {
    .table  = table_new (),
    .shared = shared_xml != NULL ? parse_shared_strings (shared_xml)
                                 : g_ptr_array_new_with_free_func (g_free),
    .value  = g_string_new (NULL),
    .column = -1,
  };
  static const GMarkupParser parser = { sheet_start, sheet_end, sheet_text, NULL, NULL };

  GMarkupParseContext *ctx =
    g_markup_parse_context_new (&parser, G_MARKUP_TREAT_CDATA_AS_TEXT, &parse, NULL);
  g_markup_parse_context_parse (ctx, sheet_xml, -1, NULL);
  g_markup_parse_context_free (ctx);

  g_clear_pointer (&parse.row, g_ptr_array_unref);
  g_ptr_array_unref (parse.shared);
  g_string_free (parse.value, TRUE);
  return parse.table;
}

/* ------------------------------------------------------------------ ods */

typedef struct {
  Table     *table;
  GPtrArray *row;
  GString   *value;
  int        repeat;
  gboolean   in_cell;
  gboolean   collecting;
  gboolean   done;        /* only the first table in the document */
} OdsParse;

static void
ods_start (GMarkupParseContext *ctx, const char *element,
           const char **names, const char **values,
           gpointer data, GError **error)
{
  OdsParse *parse = data;
  if (parse->done)
    return;

  if (g_strcmp0 (element, "table:table-row") == 0) {
    parse->row = row_new ();
  } else if (g_strcmp0 (element, "table:table-cell") == 0 && parse->row != NULL) {
    const char *repeat = attribute (names, values, "table:number-columns-repeated");
    parse->repeat  = repeat != NULL ? (int) g_ascii_strtoll (repeat, NULL, 10) : 1;
    parse->in_cell = TRUE;
    g_string_truncate (parse->value, 0);
  } else if (g_strcmp0 (element, "text:p") == 0 && parse->in_cell) {
    if (parse->value->len > 0)
      g_string_append_c (parse->value, '\n');
    parse->collecting = TRUE;
  }
}

static void
ods_end (GMarkupParseContext *ctx, const char *element, gpointer data, GError **error)
{
  OdsParse *parse = data;
  if (parse->done)
    return;

  if (g_strcmp0 (element, "text:p") == 0) {
    parse->collecting = FALSE;
  } else if (g_strcmp0 (element, "table:table-cell") == 0 && parse->row != NULL) {
    parse->in_cell = FALSE;
    /* A run of empty cells is written once with a repeat count, and that count
     * routinely runs to a thousand columns of nothing. */
    int repeat = CLAMP (parse->repeat, 1, MAX_COLUMNS);
    for (int i = 0; i < repeat && parse->row->len < MAX_COLUMNS; i++)
      g_ptr_array_add (parse->row, g_strdup (parse->value->str));
  } else if (g_strcmp0 (element, "table:table-row") == 0 && parse->row != NULL) {
    if (parse->table->len < MAX_ROWS)
      g_ptr_array_add (parse->table, parse->row);
    else
      g_ptr_array_unref (parse->row);
    parse->row = NULL;
  } else if (g_strcmp0 (element, "table:table") == 0 && parse->table->len > 0) {
    parse->done = TRUE;
  }
}

static void
ods_text (GMarkupParseContext *ctx, const char *text, gsize len,
          gpointer data, GError **error)
{
  OdsParse *parse = data;
  if (parse->collecting)
    g_string_append_len (parse->value, text, len);
}

static Table *
parse_ods (const Zip *zip)
{
  g_autofree char *xml = zip_read (zip, name_is, (gpointer) "content.xml");
  if (xml == NULL)
    return NULL;

  OdsParse parse = { .table = table_new (), .value = g_string_new (NULL) };
  static const GMarkupParser parser = { ods_start, ods_end, ods_text, NULL, NULL };

  GMarkupParseContext *ctx =
    g_markup_parse_context_new (&parser, G_MARKUP_TREAT_CDATA_AS_TEXT, &parse, NULL);
  g_markup_parse_context_parse (ctx, xml, -1, NULL);
  g_markup_parse_context_free (ctx);

  g_clear_pointer (&parse.row, g_ptr_array_unref);
  g_string_free (parse.value, TRUE);
  return parse.table;
}

/* ------------------------------------------------------- column mapping */

/* Headers differ in spacing, case and punctuation between every exporter, and
 * agree once all three are thrown away: "Login URI", "login_uri" and "url"
 * all reduce to something this table can match. */
static char *
normalise_header (const char *text)
{
  GString *out = g_string_new (NULL);
  for (const char *p = text; *p != '\0'; p++)
    if (g_ascii_isalnum (*p))
      g_string_append_c (out, g_ascii_tolower (*p));
  return g_string_free (out, FALSE);
}

typedef enum { COL_URL, COL_USER, COL_PASS, COL_NAME, COL_N } ColumnKind;

static const char *const COLUMN_NAMES[COL_N][10] = {
  [COL_URL]  = { "url", "urls", "uri", "loginuri", "loginurl", "weburl", "website",
                 "websiteurl", "hostname", NULL },
  [COL_USER] = { "username", "user", "userid", "loginusername", "loginname",
                 "login", "account", "accountname", "email", NULL },
  [COL_PASS] = { "password", "loginpassword", "passwd", "pass", "secret", NULL },
  [COL_NAME] = { "name", "title", "displayname", "itemname", "site", NULL },
};

/* Second-choice headers: matched only when nothing better was found, so that a
 * file with both "url" and "site" does not resolve the site column as the URL. */
static const char *const COLUMN_FALLBACKS[COL_N][6] = {
  [COL_URL]  = { "site", "host", "origin", "originurl", "address", NULL },
  [COL_USER] = { "emailaddress", "identity", "usernameoremail", NULL },
  [COL_PASS] = { NULL },
  [COL_NAME] = { NULL },
};

static gboolean
header_in (const char *header, const char *const *list)
{
  for (guint i = 0; list != NULL && list[i] != NULL; i++)
    if (g_strcmp0 (header, list[i]) == 0)
      return TRUE;
  return FALSE;
}

static void
map_columns (GPtrArray *header, int column[COL_N])
{
  for (int kind = 0; kind < COL_N; kind++)
    column[kind] = -1;

  for (guint i = 0; i < header->len && i < MAX_COLUMNS; i++) {
    g_autofree char *name = normalise_header (g_ptr_array_index (header, i));
    for (int kind = 0; kind < COL_N; kind++)
      if (column[kind] < 0 && header_in (name, COLUMN_NAMES[kind]))
        column[kind] = (int) i;
  }

  for (guint i = 0; i < header->len && i < MAX_COLUMNS; i++) {
    g_autofree char *name = normalise_header (g_ptr_array_index (header, i));
    for (int kind = 0; kind < COL_N; kind++)
      if (column[kind] < 0 && header_in (name, COLUMN_FALLBACKS[kind]))
        column[kind] = (int) i;
  }
}

/* Bitwarden puts every URI a login matches in one cell, newline separated. */
static char *
first_line (const char *text)
{
  const char *end = strpbrk (text ?: "", "\r\n");
  return end != NULL ? g_strndup (text, end - text) : g_strdup (text ?: "");
}

static GPtrArray *
table_to_credentials (Table *table, guint *skipped, GError **error)
{
  if (table == NULL || table->len < 2) {
    g_set_error_literal (error, G_IO_ERROR, G_IO_ERROR_INVALID_DATA,
                         "The file has no rows below its header.");
    return NULL;
  }

  GPtrArray *header = g_ptr_array_index (table, 0);
  int column[COL_N];
  map_columns (header, column);

  if (column[COL_PASS] < 0 || (column[COL_URL] < 0 && column[COL_NAME] < 0)) {
    GString *found = g_string_new (NULL);
    for (guint i = 0; i < header->len && i < 12; i++)
      g_string_append_printf (found, "%s%s", i > 0 ? ", " : "",
                              (const char *) g_ptr_array_index (header, i));
    g_set_error (error, G_IO_ERROR, G_IO_ERROR_INVALID_DATA,
                 "No password column in the first row. Lyndon needs a header "
                 "row naming at least a site and a password column; this file "
                 "starts with: %s", found->len > 0 ? found->str : "(nothing)");
    g_string_free (found, TRUE);
    return NULL;
  }

  GPtrArray *credentials = ly_credentials_new ();
  guint dropped = 0;

  for (guint i = 1; i < table->len; i++) {
    GPtrArray *row = g_ptr_array_index (table, i);

    const char *password = cell (row, column[COL_PASS]);
    if (*password == '\0') { dropped++; continue; }    /* a note or a card */

    g_autofree char *site = first_line (cell (row, column[COL_URL]));
    g_autofree char *origin = ly_passwords_normalise_origin (site);

    /* Managers that store an app rather than a site leave the URL empty and
     * put something host-shaped in the name. */
    if (origin == NULL && column[COL_NAME] >= 0)
      origin = ly_passwords_normalise_origin (cell (row, column[COL_NAME]));

    if (origin == NULL) { dropped++; continue; }

    g_ptr_array_add (credentials,
                     ly_credential_new (origin, cell (row, column[COL_USER]), password));
  }

  if (skipped != NULL)
    *skipped = dropped;
  return credentials;
}

/* ------------------------------------------------------------------ api */

GPtrArray *
ly_pwfile_read (const char *path, guint *skipped, GError **error)
{
  g_autofree char *contents = NULL;
  gsize length = 0;

  if (!g_file_get_contents (path, &contents, &length, error))
    return NULL;

  if (length > MAX_FILE_BYTES) {
    g_set_error_literal (error, G_IO_ERROR, G_IO_ERROR_INVALID_DATA,
                         "That file is far too large to be a password export.");
    return NULL;
  }

  Table *table = NULL;

  /* Both spreadsheet formats are zip archives; the extension only says which
   * member to look inside. */
  if (length > 4 && memcmp (contents, "PK\003\004", 4) == 0) {
    Zip zip = { .bytes = (const guchar *) contents, .size = length };
    g_autofree char *lower = g_ascii_strdown (path, -1);

    if (g_str_has_suffix (lower, ".ods"))
      table = parse_ods (&zip);
    else
      table = parse_xlsx (&zip);

    /* A misnamed file is common enough to be worth a second attempt. */
    if (table == NULL || table->len == 0) {
      g_clear_pointer (&table, g_ptr_array_unref);
      table = g_str_has_suffix (lower, ".ods") ? parse_xlsx (&zip) : parse_ods (&zip);
    }

    if (table == NULL) {
      g_set_error_literal (error, G_IO_ERROR, G_IO_ERROR_INVALID_DATA,
                           "That looks like a spreadsheet, but no sheet could be "
                           "read from it. Saving it again as CSV will work.");
      return NULL;
    }
  } else {
    if (!g_utf8_validate (contents, length, NULL)) {
      g_set_error_literal (error, G_IO_ERROR, G_IO_ERROR_INVALID_DATA,
                           "That file is not text, and not a spreadsheet either.");
      return NULL;
    }
    const char *text = contents;
    if (g_str_has_prefix (text, "\xEF\xBB\xBF"))    /* what Excel writes */
      text += 3;
    table = parse_csv (text, sniff_delimiter (text));
  }

  GPtrArray *credentials = table_to_credentials (table, skipped, error);
  g_ptr_array_unref (table);
  return credentials;
}

static void
append_csv_field (GString *out, const char *text, gboolean last)
{
  gboolean quote = strpbrk (text, ",\"\r\n") != NULL;

  if (quote) {
    g_string_append_c (out, '"');
    for (const char *p = text; *p != '\0'; p++) {
      if (*p == '"')
        g_string_append_c (out, '"');
      g_string_append_c (out, *p);
    }
    g_string_append_c (out, '"');
  } else {
    g_string_append (out, text);
  }
  g_string_append (out, last ? "\n" : ",");
}

gboolean
ly_pwfile_write_csv (const char *path, GPtrArray *credentials, GError **error)
{
  GString *out = g_string_new ("name,url,username,password,note\n");

  for (guint i = 0; i < credentials->len; i++) {
    LyCredential *credential = g_ptr_array_index (credentials, i);

    /* Chrome's own export puts the bare host in the name column, and that is
     * what its importer shows in the list afterwards. */
    g_autofree char *name = ly_uri_host (credential->origin);

    append_csv_field (out, name ?: credential->origin, FALSE);
    append_csv_field (out, credential->origin, FALSE);
    append_csv_field (out, credential->username ?: "", FALSE);
    append_csv_field (out, credential->password ?: "", FALSE);
    append_csv_field (out, "", TRUE);
  }

  /* 0600: this file is plaintext, and the file manager should be the only
   * thing between it and the user. */
  gboolean ok = g_file_set_contents_full (path, out->str, out->len,
                                          G_FILE_SET_CONTENTS_CONSISTENT, 0600, error);

  memset (out->str, 0, out->len);
  g_string_free (out, TRUE);
  return ok;
}
