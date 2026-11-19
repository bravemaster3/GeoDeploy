"""GeoDeploy for QGIS.

QGIS loads a plugin by importing this package and calling `classFactory(iface)`, so this file stays
tiny: anything heavier is imported inside the factory, because an exception at import time disables
the plugin with a traceback the user cannot act on.
"""


def _vendor_dir():
    import os
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")


def _use_shipped_client():
    """Make `import geodeploy` mean THIS plugin's copy of the client, freshly imported.

    The client is vendored under the top-level name `geodeploy`. When QGIS reloads a plugin — an
    upgrade installed from a zip, or the Plugin Reloader — it drops the modules named after the
    PLUGIN (`geodeploy_qgis…`) and nothing else, so the PREVIOUS version's `geodeploy` stayed in
    `sys.modules` and the new plugin ran against it: 0.7.7 died on its first push with
    "module 'geodeploy.styles' has no attribute 'set_latitude'", as 0.7.x had on `on_throttled`
    and `enable_measures` before it. Making each new argument optional (`test_stale_client.py`)
    only ever covered the cases someone thought of; this removes the cause.

    A `geodeploy` pip-installed into QGIS's Python is displaced the same way: the plugin is tested
    against the client it ships, not whichever one happens to be on the path.

    Imports are inside: QGIS imports this module to FIND the plugin, and it stays import-free
    (`scripts/test_package.py`) so nothing at that point can disable the plugin.
    """
    import sys
    vendor = _vendor_dir()
    for name in [n for n in list(sys.modules) if n == "geodeploy" or n.startswith("geodeploy.")]:
        del sys.modules[name]
    while vendor in sys.path:
        sys.path.remove(vendor)
    sys.path.insert(0, vendor)


def classFactory(iface):        # noqa: N802 - the name QGIS looks for
    _use_shipped_client()
    from .plugin import GeoDeployPlugin
    return GeoDeployPlugin(iface)
