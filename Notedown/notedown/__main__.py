"""So `python3 -m notedown` works as well as the launcher script."""

import sys

from .app import main

if __name__ == "__main__":
    sys.exit(main())
