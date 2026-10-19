/* update.c — see update.h. */

#include "update.h"

/* Shared by both builds: nothing here is a toolkit, only GLib, GIO and the
 * one call that says what this process's id is. */
#ifdef _WIN32
# include <process.h>
# define LY_GETPID() ((long) _getpid ())
#else
# include <unistd.h>
# define LY_GETPID() ((long) getpid ())
#endif

struct _LyUpdate {
  char          *stamp;      /* the file the launcher writes when a build lands */
  char          *launcher;   /* lyndon.py, for the restart                      */
  char          *python;     /* the interpreter it was started with             */
  GFileMonitor  *monitor;
  guint          poll;       /* only when the monitor could not be made     */
  gboolean       ready;
  gboolean       told;       /* the callback fires once, not once per write     */

  LyUpdateReadyFn ready_fn;
  gpointer        ready_data;
};

static void
announce (LyUpdate *update)
{
  if (update->told || !update->ready || update->ready_fn == NULL)
    return;
  update->told = TRUE;
  update->ready_fn (update, update->ready_data);
}

static void
on_stamp_changed (GFileMonitor *monitor, GFile *file, GFile *other,
                  GFileMonitorEvent event, gpointer data)
{
  LyUpdate *update = data;

  /* The launcher writes the file when a build is waiting and removes it once
   * that build has been swapped in, so its existence is the whole signal. */
  update->ready = g_file_query_exists (file, NULL);
  if (update->ready)
    announce (update);
}

static gboolean
on_poll (gpointer data)
{
  LyUpdate *update = data;
  if (!update->ready && g_file_test (update->stamp, G_FILE_TEST_EXISTS)) {
    update->ready = TRUE;
    announce (update);
  }
  return G_SOURCE_CONTINUE;
}

LyUpdate *
ly_update_new (void)
{
  const char *stamp = g_getenv ("LYNDON_UPDATE_STAMP");
  if (stamp == NULL || *stamp == '\0')
    return NULL;

  LyUpdate *update = g_new0 (LyUpdate, 1);
  update->stamp    = g_strdup (stamp);
  update->launcher = g_strdup (g_getenv ("LYNDON_LAUNCHER") ?: "");
  update->python   = g_strdup (g_getenv ("LYNDON_PYTHON") ?: "");

  g_autoptr (GFile) file = g_file_new_for_path (update->stamp);
  g_autoptr (GError) error = NULL;

  /* Watching a file that is not there yet is the ordinary case — GIO watches
   * the directory for us and reports the create. */
  update->monitor = g_file_monitor_file (file, G_FILE_MONITOR_NONE, NULL, &error);
  if (update->monitor != NULL) {
    g_signal_connect (update->monitor, "changed", G_CALLBACK (on_stamp_changed), update);
  } else {
    /* Not every GIO backend can watch a file. Looking once a minute costs a
     * stat and is the difference between the offer arriving and not. */
    g_message ("updates: watching %s by polling: %s", update->stamp, error->message);
    update->poll = g_timeout_add_seconds (60, on_poll, update);
  }

  /* A build that finished after the last run, and was not swapped in because
   * the browser never closed. */
  update->ready = g_file_query_exists (file, NULL);
  return update;
}

void
ly_update_free (LyUpdate *update)
{
  if (update == NULL)
    return;
  if (update->poll != 0)
    g_source_remove (update->poll);
  if (update->monitor != NULL) {
    g_file_monitor_cancel (update->monitor);
    g_object_unref (update->monitor);
  }
  g_free (update->stamp);
  g_free (update->launcher);
  g_free (update->python);
  g_free (update);
}

void
ly_update_set_ready_callback (LyUpdate *update, LyUpdateReadyFn ready, gpointer data)
{
  if (update == NULL)
    return;
  update->ready_fn   = ready;
  update->ready_data = data;
}

gboolean
ly_update_ready (LyUpdate *update)
{
  return update != NULL && update->ready;
}

#ifndef _WIN32
/* Between fork and exec. A session of its own, so the launcher is not taken
 * down with the terminal or the session this browser was started from. */
static void
detach (gpointer data)
{
  setsid ();
}
#endif

gboolean
ly_update_restart (LyUpdate *update, GError **error)
{
  if (update == NULL || *update->launcher == '\0') {
    g_set_error_literal (error, G_SPAWN_ERROR, G_SPAWN_ERROR_FAILED,
                         "this Lyndon was not started by its launcher");
    return FALSE;
  }

  g_autofree char *pid = g_strdup_printf ("%ld", LY_GETPID ());
  const char *python = *update->python != '\0' ? update->python : "python3";
  char *argv[] = { (char *) python, update->launcher, (char *) "--after", pid, NULL };

  /* The launcher waits for this process before it swaps the build in, so it
   * has to outlive us: its own session, and none of our descriptors. */
#ifdef _WIN32
  /* Windows has no fork, so no child setup to run between it and the exec;
   * a process started here is already independent of this one. */
  GSpawnChildSetupFunc setup = NULL;
#else
  GSpawnChildSetupFunc setup = detach;
#endif

  return g_spawn_async (NULL, argv, NULL,
                        G_SPAWN_SEARCH_PATH |
                        G_SPAWN_STDOUT_TO_DEV_NULL | G_SPAWN_STDERR_TO_DEV_NULL,
                        setup, NULL, NULL, error);
}
