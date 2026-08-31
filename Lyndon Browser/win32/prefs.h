/* prefs.h — the settings window.
 *
 * The Linux build gets AdwPreferencesPage, AdwSwitchRow and AdwComboRow, and
 * writes about a thousand lines of building them. Here there is no row widget
 * to build, so the pages are a table instead: each row names its kind, its
 * title, and the field of LyConfig it is bound to, and one painter draws them
 * all. Adding a setting is a line in that table.
 *
 * A change writes straight into LyConfig and calls ly_config_touch(), which
 * is what the Linux rows do too — the debounced save and the watcher list are
 * already there and are not this window's business.
 */
#pragma once

#include "lyndon.h"
#include "store.h"
#include "passwords.h"

#include <windows.h>

G_BEGIN_DECLS

typedef struct _LyPrefs LyPrefs;

gboolean ly_prefs_register (HINSTANCE instance);

/* One settings window per application; a second call raises the first. */
void ly_prefs_show (HWND owner, HINSTANCE instance, LyConfig *cfg,
                    LyStore *store, LyPasswords *passwords);

/* True while the window exists, so the chrome can leave its button pressed. */
gboolean ly_prefs_is_open (void);

G_END_DECLS
