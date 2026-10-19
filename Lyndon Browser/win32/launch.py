"""Shim: the launcher moved to ../lyndon.py when it stopped being Windows-only.

Shortcuts made by an older windows\\install.ps1 still point here, and a
shortcut someone pinned is not something to break over a file move.
"""

import os
import runpy
import sys

sys.argv[0] = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "lyndon.py")
runpy.run_path(sys.argv[0], run_name="__main__")
