/* app.h — application object: owns everything shared between windows. */
#pragma once

#include "lyndon.h"
#include "engine.h"
#include "blocker.h"
#include "downloads.h"
#include "archive.h"
#include "store.h"
#include "update.h"
#include "passwords.h"

G_BEGIN_DECLS

LyApp *ly_app_new (void);

LyConfig    *ly_app_config    (LyApp *app);
LyEngine    *ly_app_engine    (LyApp *app);
LyBlocker   *ly_app_blocker   (LyApp *app);
LyDownloads *ly_app_downloads (LyApp *app);
LyArchive   *ly_app_archive   (LyApp *app);
LyUpdate    *ly_app_update    (LyApp *app);
LyStore     *ly_app_store     (LyApp *app);
LyPasswords *ly_app_passwords (LyApp *app);

LyWindow *ly_app_new_window (LyApp *app);
/* A window backed by an ephemeral session: nothing it does touches disk. */
LyWindow *ly_app_new_private_window (LyApp *app);

/* Write every open tab to the session store, for restore on next launch.
 * `closing` is a window on its way out, whose tabs are counted and then
 * banked so that later saves -- including the one from a window that closes
 * after it -- still remember them. NULL when nothing is closing. */
void ly_app_save_session (LyApp *app, LyWindow *closing);

/* Drop the tabs banked from windows already closed this run. */
void ly_app_forget_closed_session (LyApp *app);

/* Re-apply configuration everywhere: engine settings, blocker rules, chrome
 * styling and every live tab. */
void ly_app_refresh (LyApp *app);

G_END_DECLS
