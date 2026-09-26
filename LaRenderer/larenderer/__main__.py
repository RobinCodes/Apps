"""So `python3 -m larenderer` works as well as the launcher script."""

import sys

from . import updater

updater.apply()

from .app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
