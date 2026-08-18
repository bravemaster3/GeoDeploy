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
import json
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
        self._width = None
        self.stroke = None
        self.stroke_width = None
        self.pen = None
        self.data_defined = None

    def setWidth(self, w):
        self._width = w

    def width(self):                    # a METHOD in QGIS
        return self._width

    def setStrokeWidth(self, w):
        # A marker's OUTLINE width — a different setter from setWidth, which is a line's own width.
        # Missing it here is how the first run of this test passed while the stroke went unset.
        self.stroke_width = w

    def strokeWidth(self):
        # And the GETTER, which QGIS has too. Without it the reader found no width to read and the
        # round trip lost `outline_width` while the test looked like the code was at fault.
        return self.stroke_width

    def setStrokeColor(self, c):
        self.stroke = c

    def strokeColor(self):
        # A GETTER, as in QGIS. Its absence made `_stroke_of` fall to its except branch and return
        # "", which reads as "no outline stated" — so a stroke colour looked lost in the round trip.
        return self.stroke if self.stroke is not None else _QColor("#000000")

    def setStrokeStyle(self, s):
        self.pen = s

    def strokeStyle(self):
        return self.pen if self.pen is not None else 1   # 1 = Qt.SolidLine

    def setPenStyle(self, s):
        self.pen = s

    def penStyle(self):
        return self.pen if self.pen is not None else 1   # 1 = Qt.SolidLine

    def setDataDefinedProperty(self, key, prop):
        self.data_defined = prop

    def dataDefinedProperty(self, key):
        return self.data_defined


class QgsSimpleMarkerSymbolLayer(_SymbolLayer):
    def __init__(self):
        super().__init__()
        self._shape = None

    @staticmethod
    def decodeShape(name):
        return (name, True)

    @staticmethod
    def encodeShape(shape):
        # The inverse, which QGIS also has. Missing it, `_shape_name` returned None and the marker
        # SHAPE quietly failed to round-trip.
        return shape

    def setShape(self, shape):
        self._shape = shape

    def shape(self):
        return self._shape


class QgsSimpleLineSymbolLayer(_SymbolLayer):
    pass


class QgsSimpleFillSymbolLayer(_SymbolLayer):
    pass


_LAYER_FOR = {0: QgsSimpleMarkerSymbolLayer, 1: QgsSimpleLineSymbolLayer,
              2: QgsSimpleFillSymbolLayer}


class _Symbol:
    def __init__(self, geometry_type):
        self.geometry_type = geometry_type
        self._color = _QColor("#000000")
        self._size = None
        self._opacity = 1.0
        self._data_defined = None
        self._layer = _LAYER_FOR[geometry_type]()

    def setColor(self, c):
        self._color = c if hasattr(c, "name") else _QColor(c)

    # A METHOD, as in QGIS — the read-back path calls `symbol.color().name()`, so a plain string
    # attribute here would have made the round-trip test pass against an API that does not exist.
    def color(self):
        return self._color

    def setSize(self, s):
        self._size = s

    def size(self):                     # a METHOD in QGIS, like color()
        return self._size

    def setOpacity(self, o):
        self._opacity = o

    def opacity(self):
        return self._opacity

    def setDataDefinedSize(self, p):
        self._data_defined = p

    def dataDefinedSize(self):
        # QGIS returns a QgsProperty here; `_size_from_qgis` reads its expression back to recover
        # size-by-field. Without the getter the round trip silently dropped it.
        return self._data_defined

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
        self._symbol = None
        self.filter = ""
        self.enabled = False

    def setEnabled(self, v):
        self.enabled = v

    def setFilterExpression(self, e):
        self.filter = e

    def filterExpression(self):
        return self.filter

    # A METHOD in QGIS. Exposed only as an attribute here at first, which made the reader's
    # geometry filter — `if not hasattr(e, "geometryType")` — match every entry and hide the very
    # bug this file was written to pin.
    def geometryType(self):
        return self.geometry_type

    def isEnabled(self):
        return self.enabled

    def symbol(self):
        return self._symbol

    def setSymbol(self, s):             # noqa: F811 - replaces the simple setter above
        self._symbol = s


class QgsVectorTileBasicRenderer(_Stub):
    def __init__(self):
        self._styles = []

    def setStyles(self, styles):
        self._styles = styles

    # QGIS exposes the list as a METHOD; the read-back path calls it, so the stub must too or the
    # round-trip test would pass against an API that does not exist.
    def styles(self):
        return self._styles


for name in ("QgsCategorizedSymbolRenderer", "QgsGraduatedSymbolRenderer", "QgsRendererCategory",
             "QgsRendererRange", "QgsSingleSymbolRenderer", "QgsClassificationRange",
             "QgsMultiBandColorRenderer", "QgsSingleBandGrayRenderer", "QgsHillshadeRenderer",
             "QgsSingleBandPseudoColorRenderer", "QgsPalettedRasterRenderer", "QgsUnitTypes"):
    setattr(core, name, type(name, (_Stub,), {}))
core.QgsUnitTypes.RenderPoints = 3


class _Property:
    """QgsProperty, as far as this needs it: an expression in, the same expression out."""

    def __init__(self, expression=""):
        self._e = expression

    @staticmethod
    def fromExpression(expression):
        return _Property(expression)

    def expressionString(self):
        return self._e


core.QgsProperty = _Property
core.QgsSymbolLayer = type("QgsSymbolLayer", (), {"PropertyStrokeWidth": 42})
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
# …and the package itself, so the module under test can import its own `compat` helper.
sys.path.insert(0, os.path.join(HERE, "..", "geodeploy_qgis"))

lines = open(os.path.join(HERE, "..", "geodeploy_qgis", "symbology.py"),
             encoding="utf-8").read().splitlines()
src = "\n".join(l for l in lines if not l.startswith("from .connection"))
# A real module always has __name__, and the module under test now uses it: its
# `from .compat import enum` needs a package context to fail as an ImportError so the
# standalone fallback can take over. Without it Python raises KeyError instead.
ns = {"__name__": "symbology"}
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
    return layer.renderer.styles()


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
assert [s.symbol().color().name() for s in out] == ["#fee5d9", "#fb6a4a", "#a50f15"]
assert all(s.geometry_type == QgsWkbTypes.PolygonGeometry for s in out)
assert all(s.source_layer == "geodeploy" and s.enabled for s in out)
# An open LOWER edge must not become `>= None` — that class has to keep drawing everything below it.
assert out[0].filter == '"pop" < 100', out[0].filter
assert out[1].filter == '"pop" >= 100 AND "pop" < 1000', out[1].filter
# The TOP break is inclusive, or the single largest value in the layer matches nothing and vanishes.
assert out[2].filter == '"pop" >= 1000 AND "pop" <= 9000', out[2].filter
# The outline travelled through the shared symbol builder, not a tile-only copy of it.
assert out[0].symbol().symbolLayer(0).stroke.name() == "#333333"
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
assert out[0].name == "other" and out[0].filter == "" and out[0].symbol().color().name() == "#cccccc"
assert [s.filter for s in out[1:]] == ['"surface" = \'paved\'',
                                       '"surface" = \'unpaved\'',
                                       '"surface" = \'O\'\'Hare\''], [s.filter for s in out[1:]]
assert all(s.geometry_type == QgsWkbTypes.LineGeometry for s in out)
# A quote in a value must be ESCAPED, not concatenated into a broken expression.
assert out[3].filter.count("''") == 1
assert out[1].symbol().symbolLayer(0).width() == 2 * 0.75, "line width lost the CSS-px→pt conversion"
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
assert out[0].symbol().color().name() == "#3b82f6"
assert out[0].symbol().size() == 5 * 2 * 0.75, ("a point with no radius must take the PORTAL's default "
                                            "(circle-radius 5), not QGIS's number under our unit")
stroke = out[0].symbol().symbolLayer(0)
assert stroke.stroke.name() == "#ffffff", "the map draws circle-stroke-color #ffffff"
# The outline width is a RATIO OF THE RADIUS, not a pixel width: `markerImage` draws
# `lineWidth = radius * ratio`, default 0.28. A flat 1 px — what this used to assert — is ratio 0.2
# at the default radius, so the plugin drew a thinner ring than the portal.
assert stroke.stroke_width == 5 * 0.28 * 0.75, stroke.stroke_width
print("styleless point -> portal defaults: radius 5, white ring at 0.28 x radius")

# An explicit outline still wins over the default.
out = styles_for({"geometry_type": "point"}, {"color": "#3b82f6", "outline_color": "#000000"})
assert out[0].symbol().symbolLayer(0).stroke.name() == "#000000"
# …and "none" still means none.
out = styles_for({"geometry_type": "point"}, {"color": "#3b82f6", "outline_color": "none"})
assert out[0].symbol().symbolLayer(0).pen == 0, "outline_color 'none' must set NoPen"
print("point outline   -> explicit colour wins, 'none' means none")


# ── single symbol, and the degrade paths ─────────────────────────────────────────────────────
out = styles_for({"geometry_type": "Point"}, {"color": "#3388ff", "radius": 6, "marker": "square"})
assert len(out) == 1 and out[0].filter == ""
assert out[0].symbol().color().name() == "#3388ff"
assert out[0].symbol().size() == 6 * 2 * 0.75, "point radius lost the diameter/points conversion"
print("single symbol  -> one unfiltered style, radius converted")

# A graduated style with NO classes must still draw — in its base colour, not refuse.
out = styles_for({"geometry_type": "Polygon"},
                 {"color_mode": "graduated", "color_field": "pop", "classes": [],
                  "color": "#654321"})
assert len(out) == 1 and out[0].symbol().color().name() == "#654321"
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
assert any(s.filter == '"k" = \'a\'' for s in layer.renderer.styles())
print("row default    -> read from default_style when no style is passed")

print("\nALL VECTOR-TILE SYMBOLOGY CASES PASS")


# ── ROUND TRIP: apply a style to tiles, read it back, and get the same style ──────────────────
# The bug this catches is the one that made "restyle a portal group and push it" do nothing at all:
# `from_qgis` reads FEATURE renderers, and a portal's vector layers are TILE layers, so it returned
# {} and the push sent no style. Reading the tile renderer back is the fix; a round trip is the only
# test that proves the two directions actually agree, and it immediately caught two conversions that
# did not invert (a radius of 5 came back 3.75, a line width of 2 came back 6).
style_from_vector_tiles = ns["style_from_vector_tiles"]


class Renderer:
    def __init__(self, styles):
        self._s = styles

    def styles(self):
        return self._s


class TileLayerWith:
    def __init__(self, styles, geometry=None):
        self._r = Renderer(styles)
        self._props = {ns["P_GEOMETRY"]: geometry or ""}

    def renderer(self):
        return self._r

    def customProperty(self, key, default=None):
        return self._props.get(key, default)


def round_trip(row, style):
    applied = styles_for(row, style)
    # The plugin records the geometry on the layer when it builds one; the reader needs it to know
    # which renderer entry is the layer's. See P_GEOMETRY.
    return style_from_vector_tiles(TileLayerWith(applied, row.get("geometry_type")))


out = round_trip({"geometry_type": "point"}, {"color": "#3388ff", "radius": 6, "marker": "square"})
assert out["color_mode"] == "single" and out["color"] == "#3388ff", out
assert out["radius"] == 6, ("a radius must survive the trip, not shrink by CSS_PX_TO_POINTS", out)
print("round trip point     ->", json.dumps(out))

out = round_trip({"geometry_type": "LineString"},
                 {"color": "#d1ba23", "line_width": 2, "lineType": "dashed"})
assert out["color"] == "#d1ba23" and out["line_width"] == 2, out
assert out["lineType"] == "dashed", out
print("round trip line      ->", json.dumps(out))

out = round_trip({"geometry_type": "MultiPolygon"},
                 {"color_mode": "graduated", "color_field": "pop",
                  "classes": [{"min": None, "max": 100, "color": "#fee5d9"},
                              {"min": 100, "max": 1000, "color": "#fb6a4a"},
                              {"min": 1000, "max": 9000, "color": "#a50f15"}]})
assert out["color_mode"] == "graduated" and out["color_field"] == "pop", out
assert [c["color"] for c in out["classes"]] == ["#fee5d9", "#fb6a4a", "#a50f15"], out
assert out["classes"][0]["min"] is None, "an open lower edge must come back open"
assert out["classes"][2]["max"] == 9000, out
print("round trip graduated ->", len(out["classes"]), "classes, edges intact")

out = round_trip({"geometry_type": "LineString"},
                 {"color_mode": "categorized", "color_field": "surface",
                  "categories": [{"value": "paved", "color": "#1f78b4"},
                                 {"value": "O'Hare", "color": "#33a02c"}],
                  "other_color": "#cccccc"})
assert out["color_mode"] == "categorized" and out["color_field"] == "surface", out
assert [c["value"] for c in out["categories"]] == ["paved", "O'Hare"], out
assert out["other_color"] == "#cccccc", out
print("round trip category  -> values and the escaped quote survive")

out = round_trip({"geometry_type": "Point"},
                 {"color_mode": "categorized", "color_field": "zone",
                  "categories": [{"value": 1, "color": "#f00000"},
                                 {"value": 2, "color": "#00f000"}]})
assert [c["value"] for c in out["categories"]] == [1, 2], ("numbers must not come back as text", out)
print("round trip numeric   -> stays numeric")

# A filter nobody here wrote must NOT be half-parsed into a wrong classification.
hand = styles_for({"geometry_type": "Polygon"}, {"color": "#123456"})
hand[0].setFilterExpression('"a" > 1 OR "b" < 2')
out = style_from_vector_tiles(TileLayerWith(hand))
assert out["color_mode"] == "single" and out["color"] == "#123456", out
print("foreign filter       -> degrades to one symbol, not a wrong class")

# Two DIFFERENT fields is not a classification this wrote either. Needs two real categories: with
# only one, repointing it is indistinguishable from a legitimate classification on that other field,
# which is why the first version of this case failed — the premise was wrong, not the reader.
two = styles_for({"geometry_type": "Polygon"},
                 {"color_mode": "categorized", "color_field": "k",
                  "categories": [{"value": "a", "color": "#111111"},
                                 {"value": "b", "color": "#222222"}]})
assert len(two) == 3, ("other + two categories", [e.name for e in two])
two[-1].setFilterExpression('"other" = \'z\'')
out = style_from_vector_tiles(TileLayerWith(two))
assert out["color_mode"] == "single", out
print("two fields           -> degrades to one symbol")

# A classification on ONE field with several categories is of course kept.
ok = styles_for({"geometry_type": "Polygon"},
                {"color_mode": "categorized", "color_field": "k",
                 "categories": [{"value": "a", "color": "#111111"},
                                {"value": "b", "color": "#222222"}]})
out = style_from_vector_tiles(TileLayerWith(ok))
assert out["color_mode"] == "categorized" and len(out["categories"]) == 2, out
print("two categories       -> kept")

# Nothing to read is {} — "use the default" — never a half-built style.
assert style_from_vector_tiles(TileLayerWith([])) == {}
assert style_from_vector_tiles(None) == {}
print("nothing to read      -> {}")

print("\nALL ROUND-TRIP CASES PASS")


# ── THE REPORTED BUG: an edit to a POINT layer read back as the polygon entry ──────────────────
# QGIS's own vector-tile symbology editor keeps one UNFILTERED style per geometry type. De-duplicating
# on the filter alone therefore kept only the FIRST — so a user who changed the point marker had the
# POLYGON colour read back: a colour they never touched, equal to the old default, so "push group to
# portal" saw no change and published nothing, and "Save styling to GeoDeploy" appeared to do nothing.
def qgis_editor_styles(colours):
    """What QGIS leaves behind after editing a vector tile layer: one style per geometry, no filters."""
    out = []
    for geom, colour in ((QgsWkbTypes.PolygonGeometry, colours[0]),
                         (QgsWkbTypes.LineGeometry, colours[1]),
                         (QgsWkbTypes.PointGeometry, colours[2])):
        entry = QgsVectorTileBasicRendererStyle("s", "", geom)
        symbol = QgsSymbol.defaultSymbol(geom)
        symbol.setColor(_QColor(colour))
        entry.setSymbol(symbol)
        entry.setEnabled(True)
        entry.setFilterExpression("")
        out.append(entry)
    return out


edited = qgis_editor_styles(["#aaaaaa", "#bbbbbb", "#ff0000"])
out = style_from_vector_tiles(TileLayerWith(edited, "point"))
assert out["color"] == "#ff0000", ("the POINT entry is the layer's; #aaaaaa is the polygon one", out)
assert "fill_opacity" not in out, ("a point style must not pick up a fill's opacity", out)
print("qgis-edited point  -> reads the point entry, not the first one")

out = style_from_vector_tiles(TileLayerWith(edited, "MultiPolygon"))
assert out["color"] == "#aaaaaa", out
out = style_from_vector_tiles(TileLayerWith(edited, "LineString"))
assert out["color"] == "#bbbbbb", out
print("qgis-edited others -> line and polygon read their own entries")

# No geometry recorded and the entries disagree: guessing would send a colour nobody picked, so it
# sends nothing — which `plan_push` turns into "keep whatever the portal already has".
assert style_from_vector_tiles(TileLayerWith(edited, None)) == {}
print("geometry unknown   -> {} rather than a guess")

# One unfiltered entry and no recorded geometry is unambiguous, so it still reads.
one = qgis_editor_styles(["#aaaaaa", "#bbbbbb", "#ff0000"])[2:]
assert style_from_vector_tiles(TileLayerWith(one, None))["color"] == "#ff0000"
print("single entry       -> still read without a recorded geometry")

# A classified POINT layer edited in QGIS keeps its filters; the geometry filter must not drop them.
mixed = qgis_editor_styles(["#aaaaaa", "#bbbbbb", "#ff0000"])
for value, colour in (("a", "#111111"), ("b", "#222222")):
    e = QgsVectorTileBasicRendererStyle("c", "", QgsWkbTypes.PointGeometry)
    sym = QgsSymbol.defaultSymbol(QgsWkbTypes.PointGeometry)
    sym.setColor(_QColor(colour))
    e.setSymbol(sym); e.setEnabled(True); e.setFilterExpression('"k" = \'%s\'' % value)
    mixed.append(e)
out = style_from_vector_tiles(TileLayerWith(mixed, "point"))
assert out["color_mode"] == "categorized" and len(out["categories"]) == 2, out
assert out["other_color"] == "#ff0000", ("the unfiltered POINT entry is the catch-all", out)
print("classified point   -> categories kept, point catch-all used")

print("\nALL GEOMETRY-SELECTION CASES PASS")


# ── DETECTION: what counts as a change, and what must not ──────────────────────────────────────
# Two complaints, opposite in direction, one root cause — a style read out of QGIS is always COMPLETE
# while a stored one holds only what somebody chose:
#   * "I only change the stroke colour and it doesn't detect the change"  → the reader ignored strokes.
#   * "I changed only one style, but it says 3 were restyled"             → filled-in defaults counted.
comparable_style = ns["comparable_style"]


def differs(before, after, geometry=None):
    """`portals._style_differs`, in the one part that matters here.

    The GEOMETRY is not optional in the real call and is not optional here: `outline_width` is a
    ratio of the radius on a point and a width in pixels on a polygon, so a comparison that does not
    know which would report every polygon as restyled (and it did, until this argument existed).
    """
    return comparable_style(before, geometry) != comparable_style(after, geometry)


# A portal's stored style, and the same style after a round trip through QGIS. Nothing was edited, so
# nothing may be reported.
stored = {"color": "#10b981", "fill_opacity": 0.45, "outline_color": "#1d4ed8"}
readback = round_trip({"geometry_type": "MultiPolygon"}, stored)
assert not differs(stored, readback, "MultiPolygon"), (stored, readback)
print("untouched polygon  -> no change reported")

for geom, stored in (("point", {"color": "#ef4444", "radius": 1.0}),
                     ("LineString", {"color": "#3b82f6", "line_width": 1.04}),
                     ("point", {"color": "#3b82f6"}),                 # nothing but a colour
                     ("MultiPolygon", {"color": "#e5b636"})):
    readback = round_trip({"geometry_type": geom}, stored)
    assert not differs(stored, readback, geom), (geom, stored, readback)
print("untouched, 4 shapes-> no change reported")

# A classified layer, likewise.
stored = {"color_mode": "categorized", "color_field": "Type",
          "categories": [{"value": "Autochamber", "color": "#8c4ee3"},
                         {"value": "Manual chamber", "color": "#6cefa2"}],
          "other_color": "#eea26b"}
readback = round_trip({"geometry_type": "point"}, stored)
assert not differs(stored, readback), (stored, readback)
# The category VALUES must survive as written — they are data, not colours.
assert [c["value"] for c in readback["categories"]] == ["Autochamber", "Manual chamber"], readback
print("untouched category -> no change reported, values kept verbatim")

# ── …and every real edit MUST be reported ─────────────────────────────────────────────────────
base_point = {"color": "#3b82f6", "radius": 5, "outline_color": "#ffffff", "marker": "circle"}
edits = [
    ("fill colour",   dict(base_point, color="#ff0000")),
    ("stroke colour", dict(base_point, outline_color="#000000")),
    ("stroke removed", dict(base_point, outline_color="none")),
    ("radius",        dict(base_point, radius=9)),
    ("marker shape",  dict(base_point, marker="square")),
]
for what, edited in edits:
    assert differs(base_point, edited), ("an edit to the " + what + " must be reported", edited)
print("point edits        ->", ", ".join(w for w, _ in edits), "all detected")

base_line = {"color": "#3b82f6", "line_width": 2, "lineType": "solid"}
for what, edited in (("colour", dict(base_line, color="#00ff00")),
                     ("width", dict(base_line, line_width=6)),
                     ("dash", dict(base_line, lineType="dashed"))):
    assert differs(base_line, edited), what
print("line edits         -> colour, width, dash all detected")

base_fill = {"color": "#10b981", "fill_opacity": 0.45, "outline_color": "#1d4ed8"}
for what, edited in (("colour", dict(base_fill, color="#123456")),
                     ("opacity", dict(base_fill, fill_opacity=0.9)),
                     ("outline", dict(base_fill, outline_color="#ff0000")),
                     ("no outline", dict(base_fill, outline_color="none"))):
    assert differs(base_fill, edited), what
print("polygon edits      -> colour, opacity, outline, no-outline all detected")

# A change to a CLASS's colour, or to the field, or a class added: all real.
g = {"color_mode": "graduated", "color_field": "pop",
     "classes": [{"min": None, "max": 100, "color": "#fee5d9"},
                 {"min": 100, "max": None, "color": "#a50f15"}]}
assert differs(g, {**g, "color_field": "area"})
assert differs(g, {**g, "classes": [dict(g["classes"][0], color="#000000"), g["classes"][1]]})
assert differs(g, {**g, "classes": g["classes"] + [{"min": 900, "max": None, "color": "#111111"}]})
print("class edits        -> field, class colour, extra class all detected")

# Cosmetic-only differences must NOT be reported: case, shorthand-vs-long, an explicitly stated
# default, or a key written as null.
assert not differs({"color": "#3B82F6"}, {"color": "#3b82f6"})
assert not differs({"color": "#3b82f6"}, {"color": "#3b82f6", "radius": 5})
assert not differs({"color": "#3b82f6"}, {"color": "#3b82f6", "lineType": "solid"})
assert not differs({"color": "#3b82f6"}, {"color": "#3b82f6", "outline_color": None})
assert not differs({"color": "#3b82f6", "outline_color": "#ffffff"},
                   {"color": "#3b82f6", "outline_color": "#1d4ed8"})   # both mean "the default"
print("cosmetic only      -> not reported")

print("\nALL CHANGE-DETECTION CASES PASS")


# ── EVERY property at once: written, read back, and compared ───────────────────────────────────
# Asked for directly: "can we make sure that all these work at once?" So this walks the whole
# supported set rather than the one that last broke.
merge_style = ns["merge_style"]

# The outline WIDTH travels. Reported as "stroke color now works well, but stroke width doesn't seem
# to be saved" — it was neither applied nor read, and the map stores it as a ratio of the radius.
out = round_trip({"geometry_type": "point"},
                 {"color": "#3b82f6", "radius": 10, "outline_color": "#000000",
                  "outline_width": 0.6})
assert out["outline_width"] == 0.6, out
assert out["radius"] == 10 and out["outline_color"] == "#000000", out
print("outline width      -> survives the round trip as a ratio")

base = {"color": "#3b82f6", "radius": 5, "outline_color": "#000000", "outline_width": 0.28}
assert differs(base, dict(base, outline_width=0.6)), "a width change must register"
assert not differs(base, dict(base))
print("outline width      -> a change to it is detected")

# SIZE BY A FIELD: the writer emits a scale_linear expression, so it has to read back out of one.
sized = {"color": "#ef4444", "radius": 5, "size_mode": "proportional",
         "size_field": "longitude", "size_stops": [[-176.2, 1], [178, 5]]}
out = round_trip({"geometry_type": "point"}, sized)
assert out.get("size_mode") == "proportional" and out.get("size_field") == "longitude", out
assert out["size_stops"][0][1] == 1 and out["size_stops"][1][1] == 5, out
assert not differs(sized, out), (sized, out)
print("size by field      -> read back, and an untouched layer is not reported")
assert differs(sized, dict(sized, size_field="latitude"))
assert differs(sized, dict(sized, size_stops=[[-176.2, 2], [178, 9]]))
assert differs(sized, {k: v for k, v in sized.items() if k != "size_mode"})
print("size by field      -> field, stops and switching it off all detected")

# MERGE, not replace: a push must not delete what QGIS cannot draw.
stored = {"color": "#10b981", "fill_opacity": 0.45,
          "extrusion": {"height_field": "h", "radius": 30},          # 3D — QGIS draws it flat
          "maplibre": {"layers": [{"type": "fill", "paint": {"fill-color": "#123"}}]},
          "popup_fields": ["name"]}
merged = merge_style(stored, {"color": "#ff0000", "fill_opacity": 0.9, "color_mode": "single"})
assert merged["color"] == "#ff0000" and merged["fill_opacity"] == 0.9, merged
assert merged["extrusion"] == stored["extrusion"], "3D must survive a restyle from QGIS"
assert merged["maplibre"] == stored["maplibre"], "imported raw paint must survive"
assert merged["popup_fields"] == ["name"], "popup fields must survive"
print("merge              -> keeps extrusion, raw paint and popup fields")

# …but a change of MODE clears the previous mode's leftovers, which nothing would draw.
was_graduated = {"color_mode": "graduated", "color_field": "pop",
                 "classes": [{"min": None, "max": 10, "color": "#111111"}], "classes_n": 1}
merged = merge_style(was_graduated, {"color_mode": "single", "color": "#ff0000"})
assert "classes" not in merged and "color_field" not in merged, merged
print("merge              -> a mode change drops the old mode's keys")

# Switching size-by-field off clears the field and stops.
merged = merge_style(sized, {"size_mode": "fixed", "color": "#ef4444"})
assert "size_field" not in merged and "size_stops" not in merged, merged
print("merge              -> fixed size drops the field and stops")

# An empty read-back changes nothing at all — the raster case, one level down.
assert merge_style(stored, {}) == stored
assert merge_style(stored, None) == stored
print("merge              -> nothing read means nothing changed")

# Finally: EVERY supported visual property, one layer at a time, round-tripped and compared.
cases = [
    ("point",       {"color": "#3b82f6", "radius": 7, "marker": "square",
                     "outline_color": "#112233", "outline_width": 0.4}),
    ("point",       {"color": "#3b82f6", "radius": 4, "outline_color": "none"}),
    ("LineString",  {"color": "#d1ba23", "line_width": 3, "lineType": "dotted"}),
    ("MultiPolygon", {"color": "#10b981", "fill_opacity": 0.7, "outline_color": "#1d4ed8"}),
    ("MultiPolygon", {"color": "#10b981", "outline_color": "none"}),
    ("point",       {"color_mode": "categorized", "color_field": "k", "radius": 6,
                     "categories": [{"value": "a", "color": "#111111"},
                                    {"value": 2, "color": "#222222"}],
                     "other_color": "#999999"}),
    ("LineString",  {"color_mode": "graduated", "color_field": "pop", "line_width": 4,
                     "classes": [{"min": None, "max": 10, "color": "#fee5d9"},
                                 {"min": 10, "max": 90, "color": "#a50f15"}]}),
]
for geom, style in cases:
    readback = round_trip({"geometry_type": geom}, style)
    # The GEOMETRY is part of the comparison — `outline_width` is a ratio on a point and pixels on
    # a polygon, and their defaults differ.
    assert not differs(style, readback, geom), (geom, style, readback)
print("all properties     ->", len(cases), "styles round-trip with no phantom change")

# ── a polygon's outline WIDTH ────────────────────────────────────────────────────────────────────
#
# It used to be dropped on the floor, and honestly so: a MapLibre `fill` strokes its own edge at a
# fixed hairline, so there was no width for GeoDeploy to draw. There is now — a `line` layer beside
# the fill — so the number has to survive the trip like every other one.
poly = {"color": "#e5b636", "outline_color": "#1d4ed8", "outline_width": 4}
back = round_trip({"geometry_type": "MultiPolygon"}, poly)
assert back["outline_width"] == 4, back
assert not differs(poly, back, "MultiPolygon"), back
print("polygon outline    -> a 4 px border survives the round trip")

# A change to it registers…
assert differs(poly, dict(poly, outline_width=8), "MultiPolygon"), "a wider border is a real edit"
# …and 1 px is the default a fill's own edge draws, so stating it is not an edit.
assert not differs({"color": "#e5b636"}, {"color": "#e5b636", "outline_width": 1}, "MultiPolygon")
# THE SAME KEY MEANS SOMETHING ELSE ON A POINT: there it is a RATIO of the radius, and 1 is a solid
# ring rather than a hairline — so the two must not be folded together.
assert differs({"color": "#e5b636"}, {"color": "#e5b636", "outline_width": 1}, "point")
print("outline width      -> px on a polygon, a ratio on a point, and the defaults do not collide")

print("\nALL FULL-FIDELITY CASES PASS")
