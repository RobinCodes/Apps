/* main.c — entry point. */

#include "app.h"

#include <locale.h>

int
main (int argc, char **argv)
{
  setlocale (LC_ALL, "");

  /* Bare --version without spinning up a display connection. */
  for (int i = 1; i < argc; i++) {
    if (g_strcmp0 (argv[i], "--version") == 0 || g_strcmp0 (argv[i], "-v") == 0) {
      g_print ("lyndon %s\n", LYNDON_VERSION);
      return 0;
    }
    if (g_strcmp0 (argv[i], "--help") == 0 || g_strcmp0 (argv[i], "-h") == 0) {
      g_print ("Usage: lyndon [URL…]\n\n"
               "  -v, --version   print the version and exit\n"
               "  -h, --help      show this message\n\n"
               "Settings live in ~/.config/lyndon/config.ini\n");
      return 0;
    }
  }

  /* WebKitGTK 2.52 aborts the web process from inside its own accessibility
   * code when a selection is extended across the last rendered line of a
   * virtualised editor. Any editor that recycles line elements as you scroll
   * hits that shape: in Overleaf, Ctrl+A in the LaTeX pane kills the tab every
   * single time and the document comes back as a blank "page crashed" screen.
   * The library exposes no setting for this, so the only lever is to point the
   * web process at an accessibility bus that is not there, which stops it
   * building the tree the crash walks.
   *
   * The cost is real: screen readers see no page content while this is in
   * force. Set LYNDON_ACCESSIBILITY=1 to keep the tree and take the crash, and
   * drop this whole block once WebKit stops aborting.
   */
  if (g_getenv ("LYNDON_ACCESSIBILITY") == NULL &&
      g_getenv ("WEBKIT_A11Y_BUS_ADDRESS") == NULL)
    g_setenv ("WEBKIT_A11Y_BUS_ADDRESS", "unix:path=/nonexistent", TRUE);

  g_set_application_name ("Lyndon");

  g_autoptr (LyApp) app = ly_app_new ();
  return g_application_run (G_APPLICATION (app), argc, argv);
}
