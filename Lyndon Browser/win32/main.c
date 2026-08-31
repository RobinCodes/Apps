/* main.c — start up, and the message loop.
 *
 * The order matters more than the length suggests. The window is created
 * before WebView2 is asked for an environment, so something is on screen in
 * the time the runtime takes to start; the environment then reports back
 * through the message loop, and the window fills its first tab. Asking for
 * the environment first would mean a second of nothing at all.
 */

#include "chrome.h"

#include <commctrl.h>
#include <objbase.h>
#include <shellapi.h>

static void
on_environment (gboolean ok, const char *message, gpointer data)
{
  ly_window_environment_ready (ok, message);
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

  if (!ly_window_register (instance)) {
    MessageBoxW (NULL, L"Could not register the window class.", L"Lyndon",
                 MB_OK | MB_ICONERROR);
    return 1;
  }

  /* A URL on the command line opens instead of the homepage. */
  g_autofree char *first = NULL;
  int argc = 0;
  LPWSTR *argv = CommandLineToArgvW (GetCommandLineW (), &argc);
  if (argv) {
    if (argc > 1)
      first = g_utf16_to_utf8 ((const gunichar2 *) argv[1], -1, NULL, NULL, NULL);
    LocalFree (argv);
  }

  LyWindow *win = ly_window_new (instance, cfg, store, block, first);
  if (win == NULL)
    return 1;

  /* Cookies, cache and logins live here, shared by every tab and window. */
  g_autofree char *data_home = ly_data_dir ();
  g_autofree char *profile = g_build_filename (data_home, "webview2", NULL);
  g_mkdir_with_parents (profile, 0700);
  ly_webview_init (profile, on_environment, NULL);

  MSG msg;
  while (GetMessageW (&msg, NULL, 0, 0) > 0) {
    LyWindow *target = ly_window_from_message (&msg);
    if (target && ly_window_handle_key (target, &msg))
      continue;
    TranslateMessage (&msg);
    DispatchMessageW (&msg);
  }

  ly_webview_shutdown ();
  ly_store_free (store);
  ly_block_free (block);
  ly_config_free (cfg);
  CoUninitialize ();
  return (int) msg.wParam;
}
