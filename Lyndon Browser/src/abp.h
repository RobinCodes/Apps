/* abp.h — Adblock-syntax filter lists to WebKit content-blocker JSON.
 *
 * WebKit compiles content-blocker JSON down to a DFA in the network process, so
 * matching costs no JavaScript and no per-request IPC to the UI. The price is a
 * restricted rule language: this compiler translates the subset of Adblock Plus
 * syntax that maps cleanly, and deliberately drops anything that does not —
 * dropping a rule only under-blocks, whereas mistranslating one can break pages
 * or, worse, make WebKit reject the entire list.
 */
#pragma once

#include <glib.h>

G_BEGIN_DECLS

typedef struct _LyAbp LyAbp;

typedef struct {
  guint blocks;      /* network block rules emitted           */
  guint cosmetics;   /* element-hiding selectors emitted      */
  guint exceptions;  /* @@ allow rules emitted                */
  guint skipped;     /* lines understood but not expressible  */
  guint invalid;     /* lines we could not parse at all       */
} LyAbpStats;

LyAbp *ly_abp_new          (void);
void   ly_abp_free         (LyAbp *abp);

/* Cosmetic filtering is opt-in: it costs a style recalculation on every page. */
void   ly_abp_set_cosmetic (LyAbp *abp, gboolean enabled);
/* Hard ceiling on emitted rules; WebKit slows down badly past ~100k. */
void   ly_abp_set_limit    (LyAbp *abp, guint max_rules);

void   ly_abp_add_line     (LyAbp *abp, const char *line);
void   ly_abp_add_text     (LyAbp *abp, const char *text);

/* Emit rules that strip cookies from third-party requests to the given hosts. */
void   ly_abp_add_cookie_block (LyAbp *abp, const char *domain);

/* Rewrite plain-http navigations to https. WebKit does the upgrade in the
 * network process, before the cleartext request is ever sent. */
void   ly_abp_add_https_upgrade (LyAbp *abp);

/* Returns a complete JSON array. Free with g_free(). */
char  *ly_abp_finish       (LyAbp *abp, LyAbpStats *stats);

/* Exposed for tests. Returns NULL when the pattern cannot be expressed. */
char  *ly_abp_pattern_to_regex (const char *pattern);

G_END_DECLS
