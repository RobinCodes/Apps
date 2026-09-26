/* main.c — start up, and the message loop.
 *
 * The order matters more than the length suggests. The window is created
 * before WebView2 is asked for an environment, so something is on screen in
 * the time the runtime takes to start; the environment then reports back
 * through the message loop, and the window fills its first tab. Asking for
 * the environment first would mean a second of nothing at all.
 */

#include "chrome.h"
#include "login.h"
#include "panel.h"
#include "prefs.h"

#include <commctrl.h>
#include <objbase.h>
#include <shellapi.h>
#include <shobjidl.h>

static void
on_environment (gboolean ok, const char *message, gpointer data)
{
  ly_window_environment_ready (ok, message);
}

/* What this start was asked to open, one entry per argument. A file becomes
 * its file:// URI here, the one place that knows what a relative path is
 * relative to; anything else stays as typed, for the address bar's rules. */
static GPtrArray *
requested (void)
{
  GPtrArray *wanted = g_ptr_array_new_with_free_func (g_free);
  int argc = 0;
  LPWSTR *argv = CommandLineToArgvW (GetCommandLineW (), &argc);
  for (int i = 1; argv && i < argc; i++) {
    char *arg = g_utf16_to_utf8 ((const gunichar2 *) argv[i], -1, NULL, NULL, NULL);
    /* The Linux build's --new-window and --private have nothing to do here,
     * and read as address-bar text they would be searched for. */
    if (arg == NULL || *arg == '\0' || g_str_has_prefix (arg, "--")) {
      g_free (arg);
      continue;
    }
    char *uri = ly_uri_from_path (arg, NULL);
    if (uri) {
      g_free (arg);
      arg = uri;
    }
    g_ptr_array_add (wanted, arg);
  }
  if (argv)
    LocalFree (argv);
  return wanted;
}

/* The data directory the app was installed with: rules/ and web/ live beside
 * the executable, the way they do under /usr/share on Linux. */
static char *
resource_dir (void)
{
  wchar_t exe[MAX_PATH];
  if (GetModuleFileNameW (NULL, exe, MAX_PATH) == 0)
    return g_strdup ("data");

  g_autofree char *path = g_utf16_to_utf8 ((const gunichar2 *) exe, -1, NULL, NULL, NULL);
  g_autofree char *dir = g_path_get_dirname (path);

  /* Installed: <dir>/data. Built in place: <dir>/data as well, because the
   * Makefile leaves lyndon.exe in the project root. */
  g_autofree char *candidate = g_build_filename (dir, "data", NULL);
  if (g_file_test (candidate, G_FILE_TEST_IS_DIR))
    return g_steal_pointer (&candidate);
  return g_steal_pointer (&dir);
}

int WINAPI
wWinMain (HINSTANCE instance, HINSTANCE prev, PWSTR cmdline, int show)
{
  /* APARTMENTTHREADED: WebView2 hosts a window, and windows belong to an STA. */
  HRESULT hr = CoInitializeEx (NULL, COINIT_APARTMENTTHREADED);
  if (FAILED (hr) && hr != RPC_E_CHANGED_MODE)
    return 1;

  /* Before any window exists, so every one of them carries it. The Start
   * Menu shortcut starts the updater rather than this executable, and the
   * shared ID is what lets the taskbar see the two as one app — the pinned
   * icon lights up rather than a second one appearing beside it. */
  SetCurrentProcessExplicitAppUserModelID (L"" LYNDON_APP_ID);

  /* One Lyndon at a time, the way GApplication makes it on Linux: a link
   * clicked while one is open becomes a tab in it, not a second browser. */
  g_autoptr (GPtrArray) wanted = requested ();
  if (ly_window_hand_over (wanted)) {
    CoUninitialize ();
    return 0;
  }

  /* Per-monitor v2, so the chrome redraws at the right size when the window
   * is dragged to a display with a different scale factor. */
  SetProcessDpiAwarenessContext (DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);

  INITCOMMONCONTROLSEX icc = { sizeof icc, ICC_STANDARD_CLASSES };
  InitCommonControlsEx (&icc);

  LyConfig *cfg = ly_config_new ();
  ly_config_load (cfg);

  LyStore *store = ly_store_new ();

  g_autofree char *data_dir = resource_dir ();
  LyBlock *block = ly_block_new ();
  ly_block_load_builtin (block, cfg, data_dir);

  /* The user's own rules, if they have made any. */
  g_autofree char *cfg_dir = ly_config_dir ();
  g_autofree char *custom = g_build_filename (cfg_dir, "rules.txt", NULL);
  if (g_file_test (custom, G_FILE_TEST_EXISTS))
    ly_block_add_file (block, custom, NULL);

  LyDownloads *downloads = ly_downloads_new (cfg);
  LyPasswords *passwords = ly_passwords_new (cfg);

  if (!ly_window_register (instance) || !ly_panel_register (instance) ||
      !ly_prefs_register (instance) || !ly_login_register (instance)) {
    MessageBoxW (NULL, L"Could not register the window classes.", L"Lyndon",
                 MB_OK | MB_ICONERROR);
    return 1;
  }

  /* What the command line asked for opens instead of the homepage, read the
   * way the address bar would read it: "example.com" is a site, and words
   * are a search. WebView2 itself takes nothing but a complete URL. */
  g_autoptr (GPtrArray) urls = g_ptr_array_new_with_free_func (g_free);
  for (guint i = 0; i < wanted->len; i++) {
    char *url = ly_config_resolve_input (cfg, g_ptr_array_index (wanted, i));
    if (url)
      g_ptr_array_add (urls, url);
  }

  LyWindow *win = ly_window_new (instance, cfg, store, block, downloads, passwords,
                                 urls->len ? g_ptr_array_index (urls, 0) : NULL);
  if (win == NULL)
    return 1;
  for (guint i = 1; i < urls->len; i++)
    ly_window_open_tab (win, g_ptr_array_index (urls, i));

  /* Cookies, cache and logins live here, shared by every tab and window. */
  g_autofree char *data_home = ly_data_dir ();
  g_autofree char *profile = g_build_filename (data_home, "webview2", NULL);
  g_mkdir_with_parents (profile, 0700);
  ly_webview_init (profile, data_dir, cfg, on_environment, NULL);

  MSG msg = { 0 };
  gboolean running = TRUE;

  while (running) {
    /* Everything under the toolkit is written against GLib, and the debounced
     * config save is a g_timeout_add_seconds on the default context. Nothing
     * would ever iterate that context here, so a plain GetMessageW loop means
     * settings are only written when ly_config_free() flushes them on the way
     * out — a crash would lose the lot, where the Linux build loses at most
     * two seconds. Draining the context each turn makes the shared code
     * behave the same on both. */
    while (g_main_context_iteration (NULL, FALSE))
      ;

    /* Wait for a Windows message, and wake anyway once a second so a GLib
     * timer with no message behind it still fires. Asking GLib how long it is
     * willing to wait means driving prepare/query/check by hand, which cannot
     * be mixed with the iteration above; one idle wake per second is the
     * cheaper mistake. */
    MsgWaitForMultipleObjectsEx (0, NULL, 1000, QS_ALLINPUT, MWMO_INPUTAVAILABLE);

    while (PeekMessageW (&msg, NULL, 0, 0, PM_REMOVE)) {
      if (msg.message == WM_QUIT) {
        running = FALSE;
        break;
      }
      LyWindow *target = ly_window_from_message (&msg);
      if (target && ly_window_handle_key (target, &msg))
        continue;
      TranslateMessage (&msg);
      DispatchMessageW (&msg);
    }
  }

  ly_webview_shutdown ();
  ly_passwords_free (passwords);
  ly_downloads_free (downloads);
  ly_store_free (store);
  ly_block_free (block);
  ly_config_free (cfg);
  CoUninitialize ();
  return (int) msg.wParam;
}
