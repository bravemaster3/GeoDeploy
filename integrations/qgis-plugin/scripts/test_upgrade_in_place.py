"""Installing a new version over an old one in a RUNNING QGIS must work without a restart.

WHAT HAPPENED. Plugin 0.7.7 installed over 0.7.6 failed on its first push with

    AttributeError: module 'geodeploy.styles' has no attribute 'set_latitude'

When QGIS reloads a plugin (the installer does, after unpacking a zip), it unloads the modules its
import hook RECORDED as the plugin's — those named `geodeploy_qgis…` — and nothing else. The client
the plugin bundles is imported as top-level `geodeploy`, so the previous version's copy stayed in
`sys.modules`, and the new plugin ran against it. 0.7.6 had hit the same thing with
`enable_measures`. `geodeploy_qgis.classFactory` now replaces whatever `geodeploy` is in memory.

THIS RUNS THE REAL SEQUENCE, through QGIS's own loader (`qgis.utils.loadPlugin`, whose hook decides
what a reload unloads): load the plugin, put an OLDER client in memory — the shape 0.7.6 left
behind — reload the plugin as the installer does, and read a layer's style.

    docker run --rm -e QT_QPA_PLATFORM=offscreen -v <repo>/integrations/qgis-plugin:/src -w /src \\
        qgis/qgis:ltr python3 -u scripts/test_upgrade_in_place.py
"""
import faulthandler
import os
import sys
import tempfile
import types

faulthandler.enable()

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.dirname(HERE)            # holds geodeploy_qgis/

from qgis.core import QgsApplication, QgsVectorLayer  # noqa: E402

QgsApplication.setPrefixPath("/usr", True)
APP = QgsApplication([], False)
APP.initQgis()

import qgis.utils  # noqa: E402

FAILURES = []
CHECKS = [0]


def check(name, condition, detail=""):
    CHECKS[0] += 1
    print("  {0}  {1}".format("ok  " if condition else "FAIL", name))
    if not condition:
        FAILURES.append(name)
        if detail != "":
            print("        -> {0}".format(str(detail)[:400]))


def load():
    """What QGIS does to start the plugin: its own loader, then the factory."""
    qgis.utils.updateAvailablePlugins()
    assert qgis.utils.loadPlugin("geodeploy_qgis"), "QGIS could not load the plugin"
    sys.modules["geodeploy_qgis"].classFactory(None)
    __import__("geodeploy_qgis.symbology")
    return sys.modules["geodeploy_qgis.symbology"]


def main():
    qgis.utils.plugin_paths = [PLUGIN_ROOT]
    sys.path.insert(0, PLUGIN_ROOT)
    layer = QgsVectorLayer("Point?crs=EPSG:4326&field=kind:string", "p", "memory")

    print("A plugin reload leaves the bundled client alone — the cause")
    symbology = load()
    symbology.from_qgis(layer)
    recorded = qgis.utils._plugin_modules.get("geodeploy_qgis", set())
    check("QGIS's reload list holds the plugin's modules but NOT `geodeploy`",
          "geodeploy_qgis.symbology" in recorded and "geodeploy" not in recorded, sorted(recorded)[:6])

    print("\nAn older client in memory, then the plugin reloaded over it")
    # The shape 0.7.6 left behind: a `geodeploy.styles` without what the new plugin calls.
    stale = types.ModuleType("geodeploy")
    stale.__path__ = []
    stale_styles = types.ModuleType("geodeploy.styles")
    stale_styles.zoom_for_scale = lambda *a, **k: 0
    stale_styles.scale_for_zoom = lambda *a, **k: 0
    for name in [n for n in list(sys.modules) if n == "geodeploy" or n.startswith("geodeploy.")]:
        del sys.modules[name]
    sys.modules["geodeploy"] = stale
    sys.modules["geodeploy.styles"] = stale_styles

    qgis.utils._unloadPluginModules("geodeploy_qgis")      # exactly what a reload does
    try:
        symbology = load()
        symbology.from_qgis(layer)
        raised = None
    except Exception as exc:            # noqa: BLE001
        raised = "{0}: {1}".format(type(exc).__name__, exc)
    check("reading a style after the reload works", raised is None, raised)
    styles = sys.modules.get("geodeploy.styles")
    vendor = os.path.realpath(os.path.join(PLUGIN_ROOT, "geodeploy_qgis", "vendor"))
    check("...against the client the plugin SHIPS",
          styles is not None and os.path.realpath(getattr(styles, "__file__", "") or "").startswith(vendor),
          getattr(styles, "__file__", None))

    print("\n{0} checks, {1} failed".format(CHECKS[0], len(FAILURES)))
    for name in FAILURES:
        print("  FAILED: " + name)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)
