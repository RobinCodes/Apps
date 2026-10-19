#!/usr/bin/env python3
"""Launcher for Hermione.

Lives next to the hermione/ package and puts its own directory on sys.path,
so the app runs from wherever this folder happens to sit — no install step, no
PYTHONPATH to set.

realpath rather than abspath, so this still finds the package when it is
reached through the ~/.local/bin symlink rather than by its own path.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))


def run() -> int:
    # Before anything else is imported, so commits fetched last time are the
    # code that runs this time. Local git only — a fast-forward or nothing —
    # so it costs a few milliseconds and never the network.
    from hermione import updater

    updater.apply()

    # The chooser service is routed to before `hermione.app` is imported,
    # and that is load-bearing rather than tidy: importing the app imports
    # libadwaita, libadwaita reads the colour scheme from the settings
    # portal, and the portal is the process waiting for this one to take
    # its bus name. See hermione/portal.py.
    if "--portal" in sys.argv[1:]:
        from hermione.portal import main as serve_portal

        return serve_portal()

    from hermione.app import main

    return main()


if __name__ == "__main__":
    sys.exit(run())
