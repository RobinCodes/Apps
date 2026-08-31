/* panel.h — the drop-down that holds bookmarks, history and downloads.
 *
 * libadwaita would give each of these a dialog and a ListBox. Three popup
 * windows would be three lots of scrolling, hover and keyboard handling, so
 * there is one: a popup with three sections along the top, an owner-drawn
 * list under it, and a filter box for the two that are worth searching.
 *
 * It closes when it loses the focus, which is what makes it feel like a menu
 * rather than a window the user has to tidy away.
 */
#pragma once

#include "lyndon.h"
#include "store.h"
#include "downloads.h"
#include "ui.h"

#include <windows.h>

G_BEGIN_DECLS

typedef struct _LyPanel LyPanel;

typedef enum {
  LY_PANEL_BOOKMARKS = 0,
  LY_PANEL_HISTORY,
  LY_PANEL_DOWNLOADS,
  LY_PANEL_N,
} LyPanelKind;

/* The panel never navigates anything itself; it says what was chosen and the
 * window decides whether that means this tab or a new one. */
typedef void (*LyPanelOpenFn) (const char *url, gboolean new_tab, gpointer user_data);

gboolean ly_panel_register (HINSTANCE instance);

/* Opens below `anchor` (in screen coordinates), or moves an already-open
 * panel to the requested section. */
LyPanel *ly_panel_show (HWND owner, HINSTANCE instance, RECT anchor,
                        LyPanelKind kind, int dpi, gboolean dark,
                        LyStore *store, LyDownloads *downloads,
                        LyPanelOpenFn on_open, gpointer user_data);

void ly_panel_close   (LyPanel *panel);
void ly_panel_refresh (LyPanel *panel);
/* The window's own panel pointer, so it can tell whether one is open. */
gboolean ly_panel_is_open (LyPanel *panel);

G_END_DECLS
