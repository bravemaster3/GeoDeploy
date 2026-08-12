"""`python -m geodeploy` — the same entry point as the `geodeploy` console script.

Worth having: inside QGIS's bundled Python, or a virtualenv whose `Scripts/` is not on PATH, the
module form is the one that works.
"""
from __future__ import annotations

import sys

from .cli.main import main

if __name__ == "__main__":
    sys.exit(main())
