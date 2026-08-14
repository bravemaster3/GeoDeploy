"""GeoDeploy for QGIS.

QGIS loads a plugin by importing this package and calling `classFactory(iface)`, so this file stays
tiny: anything heavier is imported inside the factory, because an exception at import time disables
the plugin with a traceback the user cannot act on.
"""


def classFactory(iface):        # noqa: N802 - the name QGIS looks for
    from .plugin import GeoDeployPlugin
    return GeoDeployPlugin(iface)
