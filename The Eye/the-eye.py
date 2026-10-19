#!/usr/bin/env python3
"""Launcher for The Eye.

Lives next to the theeye/ package and puts its own directory on sys.path, so
the app runs from wherever this folder happens to sit — no install step, no
PYTHONPATH to set.

realpath rather than abspath, so this still finds the package when it is
reached through a ~/.local/bin symlink rather than by its own path.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

# Before the app itself is imported, so an update fetched last time is
# the code that runs this time.
from theeye import updater  # noqa: E402

updater.apply()

from theeye.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
