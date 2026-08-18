"""Prove the plugin reads enums in a way that survives QGIS 4 — and keep it that way.

QGIS 4 is Qt6, where an enum member is only reachable through its scope: `Qt.NoPen` becomes
`Qt.PenStyle.NoPen`, `Qgis.Warning` becomes `Qgis.MessageLevel.Warning`. The plugin must run on
both, because its declared floor is QGIS 3.28 (Qt5), so `compat.enum` asks for the scoped name and
falls back to the flat one.

Two things are tested, and the second is the one that matters over time:

1. the resolver works against a Qt5-shaped owner AND a Qt6-shaped one;
2. **no source file reaches for a flat enum member any more.** That is the regression guard: the
   fix was mechanical across 94 call sites, and a single `Qgis.Warning` typed later would be
   invisible until a user on QGIS 4 hit that code path.
"""
import ast
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.join(HERE, "..", "geodeploy_qgis")
sys.path.insert(0, PLUGIN)

from compat import enum                                                         # noqa: E402


# ── 1. the resolver ──────────────────────────────────────────────────────────────────────────────
class Qt5Style:
    """Qt5: the member sits directly on the class (and, in PyQt5, also under its scope)."""
    NoPen = 0


class Qt6Style:
    """Qt6: only the scope has it."""
    class PenStyle:
        NoPen = 0


class BothStyle:
    """PyQt5 in practice: both, and they must agree."""
    NoPen = 7

    class PenStyle:
        NoPen = 7


assert enum(Qt5Style, "PenStyle", "NoPen") == 0, "flat-only owner (Qt5) must still resolve"
assert enum(Qt6Style, "PenStyle", "NoPen") == 0, "scoped-only owner (Qt6) must resolve"
assert enum(BothStyle, "PenStyle", "NoPen") == 7, "when both exist, the scoped one is used"
try:
    enum(Qt5Style, "PenStyle", "Nonexistent")
    raise AssertionError("an unknown member must raise, not return None")
except AttributeError:
    pass
print("resolver           -> Qt5, Qt6 and both-styles all resolve; an unknown member still raises")


# ── the scope belongs to the class you are CALLING ───────────────────────────────────────────────
#
# Both of these were reported by the QGIS checker in a form that names the scope but not its owner,
# and guessing the owner is how you get an AttributeError at runtime instead of a clean start.
try:
    from PyQt5.QtCore import Qt as _Qt
    from PyQt5.QtWidgets import QDialogButtonBox as _BB, QLineEdit as _LE
except ImportError:                     # pragma: no cover - PyQt5 absent in this environment
    _Qt = _BB = _LE = None
if _Qt is not None:
    assert not hasattr(getattr(_Qt, "EchoMode", object()), "Password"),         "Qt.EchoMode.Password should NOT exist — EchoMode belongs to QLineEdit"
    assert enum(_LE, "EchoMode", "Password") == _LE.Password
    assert enum(_BB, "StandardButton", "Ok") == _BB.Ok
    print("scopes             -> resolved against the real owner (QLineEdit, QDialogButtonBox)")


# ── 2. nothing reaches for a flat enum any more ──────────────────────────────────────────────────
#
# The map is repeated here on purpose. If someone edits the one in the fixer and not this one, the
# test stops covering what it claims to — so this is the list the CHECK is written against, and a
# disagreement between the two is itself a signal.
FLAT = {
    "Qgis": ("Info", "Warning", "Critical", "Success", "NoLevel"),
    "Qt": ("NoPen", "DashLine", "DotLine", "SolidLine", "UserRole", "ItemIsUserCheckable",
           "ItemIsEnabled", "Checked", "Unchecked", "RightDockWidgetArea"),
    "QLineEdit": ("Password",),
    "QMessageBox": ("Ok", "Cancel"),
    # QDialogButtonBox, not QMessageBox. Missing from this map is exactly why the first pass left
    # six `QDialogButtonBox.Ok` accesses behind and the guard below said nothing: the check can only
    # catch what it has been told to look for, so an owner absent here is an owner unprotected.
    "QDialogButtonBox": ("Ok", "Cancel", "Apply", "Close", "Save", "Yes", "No"),
    "QDialog": ("Accepted", "Rejected"),
    "QAbstractItemView": ("SingleSelection", "NoSelection"),
    "QgsWkbTypes": ("PointGeometry", "LineGeometry", "PolygonGeometry"),
    "QgsTask": ("CanCancel",),
    "QgsUnitTypes": ("RenderPoints",),
    "QgsContrastEnhancement": ("StretchToMinimumMaximum",),
    "QgsColorRampShader": ("Interpolated", "Discrete", "Exact", "Continuous", "EqualInterval",
                           "Quantile"),
    "QgsRasterBandStats": ("Min", "Max"),
    "QgsSymbolLayer": ("PropertyStrokeWidth",),
    "QgsVectorFileWriter": ("NoError",),
}

offenders = []
for name in sorted(os.listdir(PLUGIN)):
    if not name.endswith(".py") or name == "compat.py":
        continue
    path = os.path.join(PLUGIN, name)
    tree = ast.parse(open(path, encoding="utf-8").read(), filename=name)
    # AST, not a regex: a regex cannot tell `Qgis.Warning` in code from the same words inside the
    # docstring that explains the migration, and compat.py is full of the latter.
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Name):
            continue
        if node.attr in FLAT.get(node.value.id, ()):
            offenders.append("{0}:{1}  {2}.{3}".format(name, node.lineno, node.value.id, node.attr))

assert not offenders, "flat enum access, which breaks on QGIS 4:\n  " + "\n  ".join(offenders)
print("sources            -> no flat enum access in any module")

# `exec_()` is gone from Qt6 as well.
for name in sorted(os.listdir(PLUGIN)):
    if name.endswith(".py"):
        body = open(os.path.join(PLUGIN, name), encoding="utf-8").read()
        assert ".exec_()" not in body, "{0} still calls exec_(), which Qt6 removed".format(name)
print("dialogs            -> exec(), not exec_()")

print("\nALL QT6 COMPATIBILITY CASES PASS")
