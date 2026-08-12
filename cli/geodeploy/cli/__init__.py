"""The `geodeploy` command-line interface.

Everything user-facing lives under here — argument parsing, tables, progress bars, exit codes. The
package above it (`geodeploy.client` and friends) is a library and never prints or exits, which is
what lets the QGIS plugin import it without dragging a CLI along.
"""
from __future__ import annotations
