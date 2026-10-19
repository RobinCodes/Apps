/* notice.h — "a newer Lyndon is ready", and the button that takes it.
 *
 * The Linux build has AdwToastOverlay and gets this for one call. There is no
 * such thing here, so this is the login bar with the entry boxes taken out: a
 * popup card anchored under the toolbar, a sentence, one suggested button and
 * a way to dismiss it. It is deliberately the same card, in the same place,
 * because a second visual language for a second kind of notice is how a
 * browser ends up looking like four browsers.
 *
 * It does not time out. An offer that scrolls away unread is the same as
 * never having made it, which is the same reason the Linux toast is raised
 * with a timeout of zero.
 */
#pragma once

#include "lyndon.h"

#include <windows.h>

G_BEGIN_DECLS

typedef void (*LyNoticeActionFn) (gpointer user_data);

gboolean ly_notice_register (HINSTANCE instance);

/* `anchor` is the page area in screen coordinates, as for the login bar.
 * `action` runs when the button is pressed; dismissing calls nothing. */
void ly_notice_show (HWND owner, HINSTANCE instance, RECT anchor, int dpi,
                     gboolean dark, const char *message, const char *button,
                     LyNoticeActionFn action, gpointer user_data);

void ly_notice_dismiss  (void);
/* Keep it over the page area when the window is moved or resized. */
void ly_notice_reanchor (HWND owner, RECT anchor);

G_END_DECLS
