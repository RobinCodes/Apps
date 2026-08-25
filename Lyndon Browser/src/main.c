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

  g_set_application_name ("Lyndon");

  g_autoptr (LyApp) app = ly_app_new ();
  return g_application_run (G_APPLICATION (app), argc, argv);
}
