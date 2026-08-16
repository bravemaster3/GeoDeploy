#!/usr/bin/env python3
"""Load the plugin the way QGIS does, with QGIS stubbed out.

Importing every module is not enough, and shipping proved it: a patch that rewrote one method
deleted `upload_active` along with it, every module still imported cleanly, and the plugin died the
moment someone clicked its toolbar button —

    AttributeError: 'GeoDeployDock' object has no attribute 'upload_active'

because the name is only looked up when the dock is CONSTRUCTED. So this builds the dock, and then
checks that every method the UI wires a signal to actually exists. Both are cheap; neither needs
QGIS; both would have caught it.

Run by CI (`.github/workflows`) and by `scripts/build.py`-adjacent checks:

    python integrations/qgis-plugin/scripts/smoke.py
"""
import os
import re
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_DIR = os.path.dirname(HERE)
PACKAGE = os.path.join(PLUGIN_DIR, "geodeploy_qgis")


def _stub_qgis():
    """A `qgis` package with just enough shape to import and construct the dock."""
    qgis = types.ModuleType("qgis")
    core = types.ModuleType("qgis.core")
    pyqt = types.ModuleType("qgis.PyQt")
    for sub in ("QtCore", "QtGui", "QtWidgets"):
        setattr(pyqt, sub, types.ModuleType("qgis.PyQt." + sub))

    class _AnyMeta(type):
        """Class-level attribute access too — Qt enums are read off the CLASS
        (`QLineEdit.Password`, `QDialogButtonBox.Ok`), which an instance `__getattr__` never sees."""

        def __getattr__(cls, name):
            return _Any()

    class _Any(metaclass=_AnyMeta):
        """Accepts anything: construction, attributes, calls, and signal connections."""

        def __init__(self, *a, **k):
            pass

        def __getattr__(self, name):
            return _Any()

        def __call__(self, *a, **k):
            return _Any()

    for name in ("Qgis", "QgsApplication", "QgsProject", "QgsRasterLayer", "QgsVectorLayer",
                 "QgsCategorizedSymbolRenderer", "QgsGraduatedSymbolRenderer", "QgsRendererCategory",
                 "QgsRendererRange", "QgsSimpleFillSymbolLayer", "QgsSimpleLineSymbolLayer",
                 "QgsSimpleMarkerSymbolLayer", "QgsSymbol", "QgsSingleSymbolRenderer",
                 "QgsClassificationRange", "QgsMultiBandColorRenderer", "QgsSingleBandGrayRenderer",
                 "QgsSingleBandPseudoColorRenderer", "QgsHillshadeRenderer",
                 "QgsPalettedRasterRenderer", "QgsCoordinateTransformContext",
                 "QgsVectorFileWriter", "QgsNetworkAccessManager", "QgsProperty", "QgsSymbolLayer",
                 "QgsMessageLog"):
        setattr(core, name, _AnyMeta(name, (_Any,), {}))
    core.Qgis = type("Qgis", (), {"Info": 0, "Warning": 1, "Critical": 2, "Success": 3})

    pyqt.QtCore.Qt = type("Qt", (), {"RightDockWidgetArea": 2, "UserRole": 32, "DashLine": 2,
                                     "DotLine": 3, "NoPen": 0})
    pyqt.QtCore.QUrl = _AnyMeta("QUrl", (_Any,), {})
    pyqt.QtCore.pyqtSignal = lambda *a, **k: type("Signal", (), {"connect": lambda self, f: None,
                                                                 "emit": lambda self, *x: None})()
    pyqt.QtGui.QColor = _AnyMeta("QColor", (_Any,), {})
    pyqt.QtGui.QDesktopServices = _AnyMeta("QDesktopServices", (_Any,), {})
    for name in ("QAbstractItemView", "QAction", "QCheckBox", "QComboBox",
                 "QHBoxLayout", "QLabel", "QLineEdit", "QMessageBox", "QProgressBar", "QPushButton",
                 "QTreeWidget", "QTreeWidgetItem", "QVBoxLayout", "QWidget", "QDialog",
                 "QDialogButtonBox", "QTextEdit"):
        setattr(pyqt.QtWidgets, name, _AnyMeta(name, (_Any,), {}))

    # The classes the plugin SUBCLASSES must not answer to every attribute name. With the
    # permissive metaclass, `hasattr(GeoDeployDock, "anything")` was True through inheritance —
    # which silently defeated the missing-method check this script exists for. Verified by deleting
    # a method and watching it still pass. Instance access stays permissive (so `self.setWidget(…)`
    # works); CLASS access does not.
    class _Base:
        def __init__(self, *a, **k):
            pass

        def __getattr__(self, name):        # instances only — never the class
            return _Any()

    pyqt.QtWidgets.QDockWidget = type("QDockWidget", (_Base,), {})
    core.QgsTask = type("QgsTask", (_Base,), {"CanCancel": 1})

    qgis.core, qgis.PyQt = core, pyqt
    sys.modules.update({"qgis": qgis, "qgis.core": core, "qgis.PyQt": pyqt,
                        "qgis.PyQt.QtCore": pyqt.QtCore, "qgis.PyQt.QtGui": pyqt.QtGui,
                        "qgis.PyQt.QtWidgets": pyqt.QtWidgets})
    return _Any


def main() -> int:
    Any = _stub_qgis()
    sys.path.insert(0, PLUGIN_DIR)

    import importlib
    modules = ("connection", "sources", "symbology", "export", "portals", "diffdialog", "plugin")
    for name in modules:
        importlib.import_module("geodeploy_qgis." + name)
    print("imported {0} modules".format(len(modules)))

    from geodeploy_qgis import plugin as plugin_mod

    # 1. CONSTRUCT the dock. This is what QGIS does on the first click, and it is where a missing
    #    method surfaces — every `connect(self.x)` resolves `x` right here.
    plugin_mod.GeoDeployDock(Any())
    print("constructed GeoDeployDock")

    # 2. Belt and braces: every method the UI names must exist on the class, including any wired
    #    somewhere construction happens not to reach.
    source = open(os.path.join(PACKAGE, "plugin.py"), encoding="utf-8").read()
    wired = set(re.findall(r"connect\(self\.([a-zA-Z_]\w*)\)", source))
    # Both classes wire signals — the dock its buttons, the plugin its menu action — so a name is
    # satisfied by either. Checking only the dock reported `show` (which is the plugin's) missing.
    owners = (plugin_mod.GeoDeployDock, plugin_mod.GeoDeployPlugin)
    missing = sorted(n for n in wired if not any(hasattr(o, n) for o in owners))
    if missing:
        print("MISSING methods referenced by the UI: {0}".format(", ".join(missing)))
        return 1
    print("all {0} wired methods exist: {1}".format(len(wired), ", ".join(sorted(wired))))

    # 3. The plugin object QGIS instantiates, and the menu wiring it does at startup.
    plugin_mod.GeoDeployPlugin(Any()).initGui()
    print("initGui ran")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
