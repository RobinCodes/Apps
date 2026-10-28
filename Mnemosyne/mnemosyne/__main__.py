"""So `python3 -m mnemosyne` works as well as the launcher script."""

import sys

from . import updater

updater.apply()

from . import cli  # noqa: E402

answered = cli.main(sys.argv[1:])
if answered is not None:
    sys.exit(answered)

from .app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
