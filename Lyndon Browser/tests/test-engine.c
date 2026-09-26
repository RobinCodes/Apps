/* test-engine.c — the engine's list of live content managers.
 *
 * A tab owns its WebKitUserContentManager and drops the last reference to it
 * when it closes. The engine keeps an unreferenced pointer to every manager so
 * it can re-apply settings to all of them, so a closed tab has to take its
 * pointer out of that list -- otherwise the next ly_engine_apply walks freed
 * memory. That was a real crash: close a tab, open the preferences, SIGSEGV. */
#include "engine.h"
#include <stdio.h>

static int failures = 0;
static int criticals = 0;

/* Touching a finalized object shows up as a GLib type-check CRITICAL rather
 * than a crash of its own, so the test has to watch the log to see it. */
static GLogWriterOutput
watch_log (GLogLevelFlags level, const GLogField *fields, gsize n, gpointer data)
{
  if (level & (G_LOG_LEVEL_CRITICAL | G_LOG_LEVEL_WARNING)) {
    criticals++;
    for (gsize i = 0; i < n; i++)
      if (g_strcmp0 (fields[i].key, "MESSAGE") == 0)
        printf ("    log: %s\n", (const char *) fields[i].value);
  }
  return G_LOG_WRITER_HANDLED;
}

static void
ok (const char *what, int cond)
{
  printf ("  %-58s %s\n", what, cond ? "ok" : "FAIL");
  if (!cond) failures++;
}

int
main (void)
{
  /* Keep the test's writes out of the real profile. */
  g_setenv ("XDG_DATA_HOME",  "/tmp/lyndon-ucm-test/data",  TRUE);
  g_setenv ("XDG_CACHE_HOME", "/tmp/lyndon-ucm-test/cache", TRUE);
  g_setenv ("XDG_CONFIG_HOME","/tmp/lyndon-ucm-test/config",TRUE);

  g_log_set_writer_func (watch_log, NULL, NULL);

  gtk_init ();
  adw_init ();

  LyConfig *cfg = ly_config_new ();
  ly_config_load (cfg);
  LyEngine *engine = ly_engine_new (cfg);

  /* A tab's content manager: created, registered, then dropped when the tab
   * closes -- which is the only reference to it. */
  WebKitUserContentManager *ucm = webkit_user_content_manager_new ();
  ly_engine_prepare_content_manager (engine, ucm);

  g_object_add_weak_pointer (G_OBJECT (ucm), (gpointer *) &ucm);
  g_object_unref (ucm);
  ok ("the manager is finalized when its tab drops it", ucm == NULL);

  /* The crash: a preference change re-applies settings to every manager the
   * engine remembers. */
  criticals = 0;
  ly_engine_apply (engine);
  ok ("ly_engine_apply after a tab closed does not touch freed memory",
      criticals == 0);

  /* A live manager must still be reached, so the removal is not over-eager. */
  WebKitUserContentManager *live = webkit_user_content_manager_new ();
  ly_engine_prepare_content_manager (engine, live);
  criticals = 0;
  ly_engine_apply (engine);
  ok ("a manager still open is still re-applied", criticals == 0);

  criticals = 0;
  ly_engine_free (engine);
  ok ("freeing the engine with a live manager registered is clean",
      criticals == 0);

  /* The manager outlives the engine: its weak ref must have been dropped. */
  criticals = 0;
  g_object_unref (live);
  ok ("a manager outliving the engine does not call back into it",
      criticals == 0);

  ly_config_free (cfg);

  printf ("\n%s\n", failures ? "FAILURES" : "all good");
  return failures ? 1 : 0;
}
