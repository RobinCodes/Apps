"""So `python3 -m hermione` works as well as the launcher script."""

import sys

from . import updater

updater.apply()

if __name__ == "__main__":
    # `--portal` before `.app`, which would import libadwaita — see the
    # note in hermione/portal.py about what that costs here.
    if "--portal" in sys.argv[1:]:
        from .portal import main
    else:
        from .app import main
    sys.exit(main())
