#!/usr/bin/env python3
"""Launcher for Mnemosyne.

Lives next to the mnemosyne/ package and puts its own directory on sys.path,
so the app runs from wherever this folder happens to sit — no install step, no
PYTHONPATH to set.

realpath rather than abspath, so this still finds the package when it is
reached through the ~/.local/bin symlink rather than by its own path.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

# Before the app itself is imported, so an update fetched last time is
# the code that runs this time.
from mnemosyne import updater  # noqa: E402

updater.apply()

# The command-line answers come before GTK is imported at all: `--due` is
# asked by other apps every time they draw a badge, and loading a toolkit to
# print two numbers would make that the slowest part of their startup.
from mnemosyne import cli  # noqa: E402

answered = cli.main(sys.argv[1:])
if answered is not None:
    sys.exit(answered)

from mnemosyne.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
