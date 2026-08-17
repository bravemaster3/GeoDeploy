"""Prove `apply_to_vector_tiles` really CLASSIFIES, with stand-ins for the QGIS tile classes.

The complaint this exists to stop coming back: a layer added as vector tiles arrived in the right
colour and nothing else — every feature drawn the same, whatever the portal showed. QGIS renders
tiles through `QgsVectorTileBasicRenderer`, which takes a list of styles each carrying its own
FILTER EXPRESSION, so the graduated/categorized breaks do translate; the earlier code simply did not
translate them.

QGIS is not importable here, so the renderer classes are replaced by recording stubs and the test
asserts on what would have been handed to QGIS: one style per class, the right colour, and a filter
that selects exactly that class — including the two edge cases that silently lose features, an open
outer bound and an inclusive top break.
"""
import sys
import types

qgis = types.ModuleType("qgis")
core = types.ModuleType("qgis.core")
PyQt = types.ModuleType("qgis.PyQt")
QtCore = types.ModuleType("qgis.PyQt.QtCore")
QtGui = types.ModuleType("qgis.PyQt.QtGui")


class _Stub:
    pass


class QgsWkbTypes:
    PointGeometry, LineGeometry, PolygonGeometry = 0, 1, 2


class _QColor:
    def __init__(self, name="#000000"):
        self._name = name

    def name(self):
        return self._name


class _SymbolLayer:
    """The base every stub symbol layer shares.

    Three SUBCLASSES below, one per geometry, because `_symbol_of` branches on `isinstance` — a
    single do-everything stub would satisfy every assertion here while the real code took none of
    those branches, which is the way a test like this quietly stops testing anything.
    """

    def __init__(self):
        self.width = None
        self.stroke = None
        self.stroke_width = None
        self.pen = None

    def setWidth(self, w):
        self.width = w

    def setStrokeWidth(self, w):
        # A marker's OUTLINE width — a different setter from setWidth, which is a line's own width.
        # Missing it here is how the first run of this test passed while the stroke went unset.
        self.stroke_width = w

    def setStrokeColor(self, c):
        self.stroke = c

    def setStrokeStyle(self, s):
        self.pen = s

    def setPenStyle(self, s):
        self.pen = s

    def setDataDefinedProperty(self, key, prop):
        self.data_defined = prop


class QgsSimpleMarkerSymbolLayer(_SymbolLayer):
    def __init__(self):
        super().__init__()
        self.shape = None

    @staticmethod
    def decodeShape(name):
        return (name, True)

    def setShape(self, shape):
        self.shape = shape


class QgsSimpleLineSymbolLayer(_SymbolLayer):
    pass


class QgsSimpleFillSymbolLayer(_SymbolLayer):
    pass


_LAYER_FOR = {0: QgsSimpleMarkerSymbolLayer, 1: QgsSimpleLineSymbolLayer,
              2: QgsSimpleFillSymbolLayer}


class _Symbol:
    def __init__(self, geometry_type):
        self.geometry_type = geometry_type
        self.color = None
        self.size = None
        self.opacity = None
        self._layer = _LAYER_FOR[geometry_type]()

    def setColor(self, c):
        self.color = c.name() if hasattr(c, "name") else c

    def setSize(self, s):
        self.size = s

    def setOpacity(self, o):
        self.opacity = o

    def setDataDefinedSize(self, p):
        self.data_defined = p

    def symbolLayerCount(self):
        return 1

    def symbolLayer(self, i):
        return self._layer


class QgsSymbol(_Stub):
    @staticmethod
    def defaultSymbol(geometry_type):
        return _Symbol(geometry_type)


class QgsVectorTileBasicRendererStyle(_Stub):
    def __init__(self, name, source_layer, geometry_type):
        self.name = name
        self.source_layer = source_layer
        self.geometry_type = geometry_type
        self.symbol = None
        self.filter = ""
        self.enabled = False

    def setSymbol(self, s):
        self.symbol = s

    def setEnabled(self, v):
        self.enabled = v

    def setFilterExpression(self, e):
        self.filter = e


class QgsVectorTileBasicRenderer(_Stub):
    def __init__(self):
        self.styles = []

    def setStyles(self, styles):
        self.styles = styles


for name in ("QgsCategorizedSymbolRenderer", "QgsGraduatedSymbolRenderer", "QgsRendererCategory",
             "QgsRendererRange", "QgsSingleSymbolRenderer", "QgsClassificationRange",
             "QgsMultiBandColorRenderer", "QgsSingleBandGrayRenderer", "QgsHillshadeRenderer",
             "QgsSingleBandPseudoColorRenderer", "QgsPalettedRasterRenderer", "QgsUnitTypes"):
    setattr(core, name, type(name, (_Stub,), {}))
core.QgsUnitTypes.RenderPoints = 3
core.QgsSymbol = QgsSymbol
core.QgsSimpleMarkerSymbolLayer = QgsSimpleMarkerSymbolLayer
core.QgsSimpleLineSymbolLayer = QgsSimpleLineSymbolLayer
core.QgsSimpleFillSymbolLayer = QgsSimpleFillSymbolLayer
core.QgsWkbTypes = QgsWkbTypes
core.QgsVectorTileBasicRenderer = QgsVectorTileBasicRenderer
core.QgsVectorTileBasicRendererStyle = QgsVectorTileBasicRendererStyle
QtCore.Qt = type("Qt", (), {"DashLine": 2, "DotLine": 3, "NoPen": 0})
QtGui.QColor = _QColor
qgis.core, qgis.PyQt = core, PyQt
PyQt.QtCore, PyQt.QtGui = QtCore, QtGui
sys.modules.update({"qgis": qgis, "qgis.core": core, "qgis.PyQt": PyQt,
                    "qgis.PyQt.QtCore": QtCore, "qgis.PyQt.QtGui": QtGui})

import os                                                                       # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "geodeploy_qgis", "vendor"))

lines = open(os.path.join(HERE, "..", "geodeploy_qgis", "symbology.py"),
             encoding="utf-8").read().splitlines()
src = "\n".join(l for l in lines if not l.startswith("from .connection"))
ns = {}
exec(compile(src, "symbology", "exec"), ns)                                     # noqa: S102
apply_to_vector_tiles = ns["apply_to_vector_tiles"]
assert ns["QGIS"] is True, "the fake qgis.core was not picked up"


class TileLayer:
    def __init__(self):
        self.renderer = None
        self.repainted = False

    def setRenderer(self, r):
        self.renderer = r

    def triggerRepaint(self):
        self.repainted = True


def styles_for(row, style, source_layer="geodeploy"):
    layer = TileLayer()
    ok = apply_to_vector_tiles(layer, row, source_layer, style)
    assert ok, "apply_to_vector_tiles refused the style"
    assert layer.repainted, "the layer was never repainted, so nothing would redraw"
    return layer.renderer.styles


# ── graduated ────────────────────────────────────────────────────────────────────────────────
row = {"geometry_type": "MultiPolygon"}
style = {
    "color_mode": "graduated",
    "color_field": "pop",
    "classes": [{"min": None, "max": 100, "color": "#fee5d9"},
                {"min": 100, "max": 1000, "color": "#fb6a4a"},
                {"min": 1000, "max": 9000, "color": "#a50f15"}],
    "outline_color": "#333333",
}
out = styles_for(row, style)
assert len(out) == 3, out
assert [s.symbol.color for s in out] == ["#fee5d9", "#fb6a4a", "#a50f15"]
assert all(s.geometry_type == QgsWkbTypes.PolygonGeometry for s in out)
assert all(s.source_layer == "geodeploy" and s.enabled for s in out)
# An open LOWER edge must not become `>= None` — that class has to keep drawing everything below it.
assert out[0].filter == '"pop" < 100', out[0].filter
assert out[1].filter == '"pop" >= 100 AND "pop" < 1000', out[1].filter
# The TOP break is inclusive, or the single largest value in the layer matches nothing and vanishes.
assert out[2].filter == '"pop" >= 1000 AND "pop" <= 9000', out[2].filter
# The outline travelled through the shared symbol builder, not a tile-only copy of it.
assert out[0].symbol.symbolLayer(0).stroke.name() == "#333333"
print("graduated      -> 3 filtered styles, open low edge and inclusive top break")

# ── categorized ──────────────────────────────────────────────────────────────────────────────
row = {"geometry_type": "LineString"}
style = {
    "color_mode": "categorized",
    "color_field": "surface",
    "categories": [{"value": "paved", "color": "#1f78b4"},
                   {"value": "unpaved", "color": "#b15928"},
                   {"value": "O'Hare", "color": "#33a02c"}],
    "other_color": "#cccccc",
    "line_width": 2,
}
out = styles_for(row, style)
# "Other" first so the named categories draw ON TOP of it, matching the map's fallback colour.
assert out[0].name == "other" and out[0].filter == "" and out[0].symbol.color == "#cccccc"
assert [s.filter for s in out[1:]] == ['"surface" = \'paved\'',
                                       '"surface" = \'unpaved\'',
                                       '"surface" = \'O\'\'Hare\''], [s.filter for s in out[1:]]
assert all(s.geometry_type == QgsWkbTypes.LineGeometry for s in out)
# A quote in a value must be ESCAPED, not concatenated into a broken expression.
assert out[3].filter.count("''") == 1
assert out[1].symbol.symbolLayer(0).width == 2 * 0.75, "line width lost the CSS-px→pt conversion"
print("categorized    -> other + 3 filtered styles, quote escaped, width converted")

# Numeric category values must NOT be quoted, or the filter never matches an integer column.
out = styles_for({"geometry_type": "Point"},
                 {"color_mode": "categorized", "color_field": "zone",
                  "categories": [{"value": 1, "color": "#f00"}, {"value": 2, "color": "#0f0"}]})
assert out[1].filter == '"zone" = 1', out[1].filter
print("numeric values -> unquoted literals")

# ── the defaults a styleless point layer gets ────────────────────────────────────────────────
# THE BUG THIS PINS: a real layer's style is often just {"color": "#3b82f6"} — no radius. QGIS's
# default marker is 2.0 in MILLIMETRES, and `_use_points` switches the unit to points without
# touching the number, so the same 2.0 became 0.7 mm: dots too small to see. QGIS's default outline
# is dark grey, which at that size covers the fill entirely and turns every point black whatever
# colour was asked for. Both symptoms, one screenshot, one cause.
out = styles_for({"geometry_type": "point"}, {"color": "#3b82f6"})
assert out[0].symbol.color == "#3b82f6"
assert out[0].symbol.size == 5 * 2 * 0.75, ("a point with no radius must take the PORTAL's default "
                                            "(circle-radius 5), not QGIS's number under our unit")
stroke = out[0].symbol.symbolLayer(0)
assert stroke.stroke.name() == "#ffffff", "the map draws circle-stroke-color #ffffff"
assert stroke.stroke_width == 1 * 0.75, "…at circle-stroke-width 1"
print("styleless point -> portal defaults: radius 5, white 1px stroke")

# An explicit outline still wins over the default.
out = styles_for({"geometry_type": "point"}, {"color": "#3b82f6", "outline_color": "#000000"})
assert out[0].symbol.symbolLayer(0).stroke.name() == "#000000"
# …and "none" still means none.
out = styles_for({"geometry_type": "point"}, {"color": "#3b82f6", "outline_color": "none"})
assert out[0].symbol.symbolLayer(0).pen == 0, "outline_color 'none' must set NoPen"
print("point outline   -> explicit colour wins, 'none' means none")


# ── single symbol, and the degrade paths ─────────────────────────────────────────────────────
out = styles_for({"geometry_type": "Point"}, {"color": "#3388ff", "radius": 6, "marker": "square"})
assert len(out) == 1 and out[0].filter == ""
assert out[0].symbol.color == "#3388ff"
assert out[0].symbol.size == 6 * 2 * 0.75, "point radius lost the diameter/points conversion"
print("single symbol  -> one unfiltered style, radius converted")

# A graduated style with NO classes must still draw — in its base colour, not refuse.
out = styles_for({"geometry_type": "Polygon"},
                 {"color_mode": "graduated", "color_field": "pop", "classes": [],
                  "color": "#654321"})
assert len(out) == 1 and out[0].symbol.color == "#654321"
print("no classes     -> falls back to one symbol")

# ── the geometry type is a BINDING, not a hint ────────────────────────────────────────────────
# A tile renderer style only draws features of its own geometry type, and QGIS is literal about it:
# a point style over line data draws a marker at every vertex (a road network as a carpet of dots),
# and a fill style over point data draws nothing. Defaulting the unknown case to "point" produced
# both. Unknown now means "style every type" — a tile layer can legitimately hold more than one.
out = styles_for({"geometry_type": None}, {"color": "#123456"})
assert sorted(s.geometry_type for s in out) == [QgsWkbTypes.PointGeometry,
                                                QgsWkbTypes.LineGeometry,
                                                QgsWkbTypes.PolygonGeometry], [s.geometry_type for s in out]
assert len({s.name for s in out}) == 3, "each entry needs its own name"
print("unknown geom   -> one style per geometry type, so nothing is mis-drawn")

# A geometry that IS known binds to exactly that type and no other.
for name, expected in (("MultiPolygon", QgsWkbTypes.PolygonGeometry),
                       ("LineString", QgsWkbTypes.LineGeometry),
                       ("point", QgsWkbTypes.PointGeometry),
                       ("MULTIPOINT", QgsWkbTypes.PointGeometry)):
    out = styles_for({"geometry_type": name}, {"color": "#123456"})
    assert [s.geometry_type for s in out] == [expected], (name, [s.geometry_type for s in out])
print("known geom     -> bound to exactly that type")

# Classified + unknown geometry: every class still gets every type, and the filters survive.
out = styles_for({"geometry_type": ""},
                 {"color_mode": "categorized", "color_field": "k",
                  "categories": [{"value": "a", "color": "#111111"},
                                 {"value": "b", "color": "#222222"}]})
assert len(out) == 9, len(out)          # (2 categories + other) x 3 geometry types
assert sum(1 for s in out if s.filter == '"k" = ' + "'a'") == 3
print("classified     -> classes x geometry types, filters intact")

# The row's OWN stored style is used when the caller passes none — the add path relies on this.
layer = TileLayer()
assert apply_to_vector_tiles(
    layer, {"geometry_type": "Polygon",
            "default_style": {"style": {"color_mode": "categorized", "color_field": "k",
                                        "categories": [{"value": "a", "color": "#abcdef"}]}}},
    "geodeploy")
assert any(s.filter == '"k" = \'a\'' for s in layer.renderer.styles)
print("row default    -> read from default_style when no style is passed")

print("\nALL VECTOR-TILE SYMBOLOGY CASES PASS")
