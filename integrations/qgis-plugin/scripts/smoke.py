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

    #: Qt getters that return a STRING. `_Any` accepting everything is what makes this stub small,
    #: but a method whose result is concatenated has to hand back the right TYPE or the stub fails
    #: where real Qt would not — which it did, on `button.toolTip() + "…"`. Named rather than
    #: guessed, so the stub stays honest about what it is pretending to be.
    _STRING_GETTERS = {"toolTip", "text", "name", "windowTitle", "styleSheet", "placeholderText",
                       "currentText", "objectName", "whatsThis", "statusTip"}
    #: …and the ones that return a LIST. Same reasoning: `selectedItems()` is empty on a fresh dock,
    #: and code that says `items[0] if items else None` is correct against Qt and crashed against a
    #: stub that answered with a truthy object.
    _LIST_GETTERS = {"selectedItems", "selectedLayers", "selectedNodes", "children", "styles",
                     "symbolLayers", "ranges", "categories", "classes"}

    class _Any(metaclass=_AnyMeta):
        """Accepts anything: construction, attributes, calls, and signal connections."""

        def __init__(self, *a, **k):
            pass

        def __getattr__(self, name):
            if name in _STRING_GETTERS:
                return lambda *a, **k: ""
            if name in _LIST_GETTERS:
                return lambda *a, **k: []
            return _Any()

        def __call__(self, *a, **k):
            return _Any()

    for name in ("Qgis", "QgsApplication", "QgsProject", "QgsRasterLayer", "QgsVectorLayer",
                 "QgsVectorTileLayer", "QgsDataSourceUri", "QgsUnitTypes",
                 "QgsVectorTileBasicRenderer", "QgsVectorTileBasicRendererStyle",
                 "QgsWkbTypes",
                 "QgsCoordinateReferenceSystem", "QgsCoordinateTransform",
                 "QgsRectangle",
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

    # FLAT spellings only, deliberately. Real Qt6 scopes these (`Qt.TextFormat.RichText`) and Qt5
    # does not, and `compat.enum` tries the scoped name before falling back to the flat one — so a
    # stub carrying only flat names exercises the fallback, and a name missing from BOTH raises
    # exactly as it would on a real build. That is what caught `Qt.RichText` here rather than in
    # somebody's QGIS 4.
    pyqt.QtCore.Qt = type("Qt", (), {"RightDockWidgetArea": 2, "UserRole": 32, "DashLine": 2,
                                     "DotLine": 3, "NoPen": 0, "RichText": 1})
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
    modules = ("connection", "sources", "symbology", "export", "portals", "diffdialog",
               "uploadpicker", "plugin")
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

    # 3. Every `self.something(...)` CALL, not only signal targets. `_open_portal` was deleted by
    #    the same patch that took `upload_active`, and survived the first version of this check
    #    because it is called from `add_selected` rather than wired to a button — so the plugin
    #    still crashed, just one click further in.
    called = set(re.findall(r"self\.(_?[a-z]\w*)\(", source))
    assigned = set(re.findall(r"self\.(\w+)\s*=", source))
    defined = set(re.findall(r"^    def (\w+)", source, re.M))
    # Methods the dock inherits from QDockWidget. The stub bases are deliberately NOT permissive at
    # class level (that is what made the first version of this check useless), so real Qt methods
    # have to be named. A short, explicit list beats a check that passes for everything.
    QT_INHERITED = {"setWidget", "show", "raise_", "hide", "setWindowTitle", "widget"}
    absent = sorted(c for c in called
                    if c not in defined and c not in assigned and c not in QT_INHERITED
                    and not hasattr(plugin_mod.GeoDeployDock, c)
                    and not hasattr(plugin_mod.GeoDeployPlugin, c))
    if absent:
        print("CALLED but never defined: {0}".format(", ".join(absent)))
        return 1
    print("all {0} self-calls resolve".format(len(called)))

    # 4. The plugin object QGIS instantiates, and the menu wiring it does at startup.
    plugin_mod.GeoDeployPlugin(Any()).initGui()
    print("initGui ran")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
