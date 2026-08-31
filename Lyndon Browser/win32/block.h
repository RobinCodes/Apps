/* block.h — request matching for the Windows build.
 *
 * The Linux build hands its rules to WebKit, which compiles them into a DFA
 * and matches them in the network process; nothing in Lyndon ever sees a
 * request. WebView2 has no equivalent — it offers a callback per request and
 * nothing else — so the matcher that WebKit provided has to exist here.
 *
 * It is built for the shape real filter lists actually have. Nearly every
 * rule is "||host^", a plain domain block, so those go in a hash table keyed
 * by host and are answered by walking the request's labels right to left:
 * four lookups for a four-label host, no regex, no scanning. What is left —
 * substring patterns, wildcards, anchors — is indexed by the longest literal
 * run in the pattern, so a request only tests the handful of rules that share
 * a token with its URL rather than the whole list.
 *
 * Patterns are translated by ly_abp_pattern_to_regex() from abp.c, the same
 * function the Linux build uses to produce its WebKit JSON. The two builds
 * therefore agree about what a rule means, which is the point of reusing it.
 */
#pragma once

#include "lyndon.h"

G_BEGIN_DECLS

typedef struct _LyBlock LyBlock;

/* What the request is for. Filter options like $script and $image select on
 * this; WebView2 reports it as a COREWEBVIEW2_WEB_RESOURCE_CONTEXT. */
typedef enum {
  LY_RES_OTHER = 0,
  LY_RES_DOCUMENT,
  LY_RES_SUBDOCUMENT,
  LY_RES_SCRIPT,
  LY_RES_IMAGE,
  LY_RES_STYLESHEET,
  LY_RES_FONT,
  LY_RES_MEDIA,
  LY_RES_XHR,
  LY_RES_WEBSOCKET,
} LyResourceKind;

typedef struct {
  guint domains;      /* "||host^" rules in the host table        */
  guint patterns;     /* everything else, in the keyword index    */
  guint exceptions;   /* @@ rules                                  */
  guint cosmetics;    /* ## selectors                              */
  guint invalid;      /* lines that could not be parsed            */
  guint64 blocked;    /* requests refused since start              */
} LyBlockStats;

LyBlock *ly_block_new   (void);
void     ly_block_free  (LyBlock *b);

/* Load one list. Category order does not matter; rules accumulate. */
void     ly_block_add_text (LyBlock *b, const char *text);
gboolean ly_block_add_file (LyBlock *b, const char *path, GError **error);

/* Load the built-in lists the app ships, honouring cfg->block_cat[]. */
void     ly_block_load_builtin (LyBlock *b, LyConfig *cfg, const char *data_dir);

/* The question WebView2 asks, once per request.
 * `url` is the request; `page_url` is the document it belongs to, used for
 * third-party tests; NULL page_url means "treat as first party". */
gboolean ly_block_should_block (LyBlock        *b,
                                const char     *url,
                                const char     *page_url,
                                LyResourceKind  kind);

/* CSS for element hiding on this host: the generic selectors plus any that
 * name it. Returns NULL when there is nothing to hide. Free with g_free(). */
char    *ly_block_cosmetic_css (LyBlock *b, const char *host);

const LyBlockStats *ly_block_stats (LyBlock *b);
void                ly_block_reset_counters (LyBlock *b);

/* Map WebView2's resource context onto LyResourceKind. Declared here so the
 * tab code does not need to know the enum's shape. */
LyResourceKind ly_block_kind_from_context (int webview2_context);

G_END_DECLS
