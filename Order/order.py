#!/usr/bin/env python3
"""The Order's command line. See `order --help`, SYSTEM.md §2–§3 and README.md."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from order.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
