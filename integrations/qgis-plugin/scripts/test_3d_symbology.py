"""Prove 3D extrusion survives the trip to QGIS and back — for POLYGONS and for POINTS.

GeoDeploy draws 3D as MapLibre `fill-extrusion` from one style key:

    extrusion {enabled, field, height, scale, base, color, opacity, radius}

QGIS models the same thing as a second renderer beside the 2D one — `QgsPolygon3DSymbol` with an
extrusion height, or `QgsPoint3DSymbol` shaped as a cylinder, which is what the tile server's
`pillars` function builds a point into. Until now the plugin only PRESERVED the key through a push
(`merge_style`); nothing wrote it into QGIS and nothing read it back, so 3D could not be edited
there at all.

The hard case, and most of what this file is about: **QGIS cannot express every GeoDeploy
extrusion.** A cylinder has a fixed length, so a point whose height comes from a COLUMN has no
equivalent — and reading the symbol back naively would report a fixed height, which the merge would
then treat as "the user replaced the column", deleting it. So the applied spec is recorded on the
layer and returned unchanged while the symbol still matches it; only a real edit in QGIS is read as
one. These tests are what keep that true.
"""
import json
import sys
import types

qgis = types.ModuleType("qgis")
core = types.ModuleType("qgis.core")
three_d = types.ModuleType("qgis._3d")
PyQt = types.ModuleType("qgis.PyQt")
QtCore = types.ModuleType("qgis.PyQt.QtCore")
QtGui = types.ModuleType("qgis.PyQt.QtGui")


class _Stub:
    pass


class _QColor:
    def __init__(self, *args):
        if len(args) >= 3:
            self._r, self._g, self._b = int(args[0]), int(args[1]), int(args[2])
            self._a = int(args[3]) if len(args) > 3 else 255
        else:
            text = str(args[0] if args else "#000000").strip().lstrip("#")
            if len(text) == 3:
                text = "".join(c * 2 for c in text)
            parts = [int(text[i:i + 2], 16) for i in range(0, min(len(text), 8), 2)]
            while len(parts) < 3:
                parts.append(0)
            self._r, self._g, self._b = parts[0], parts[1], parts[2]
            self._a = parts[3] if len(parts) > 3 else 255

    def name(self):
        return "#{0:02x}{1:02x}{2:02x}".format(self._r, self._g, self._b)

    def red(self):
        return self._r

    def green(self):
        return self._g

    def blue(self):
        return self._b

    def alpha(self):
        return self._a


class QgsWkbTypes:
    PointGeometry, LineGeometry, PolygonGeometry = 0, 1, 2


# ── the 2D half, only as much as `from_qgis`'s single-symbol path touches ─────────────────────────
class _SymbolLayer:
    def __init__(self):
        self._width, self.stroke, self.pen = None, None, None
        self.stroke_width = None

    def setWidth(self, w):
        self._width = w

    def width(self):
        return self._width

    def setStrokeColor(self, c):
        self.stroke = c

    def strokeColor(self):
        return self.stroke if self.stroke is not None else _QColor("#000000")

    def setStrokeStyle(self, s):
        self.pen = s

    def strokeStyle(self):
        return self.pen if self.pen is not None else 1

    def setPenStyle(self, s):
        self.pen = s

    def penStyle(self):
        return self.pen if self.pen is not None else 1

    def setStrokeWidth(self, w):
        self.stroke_width = w

    def strokeWidth(self):
        return self.stroke_width

    def setDataDefinedProperty(self, key, prop):
        pass

    def dataDefinedProperty(self, key):
        return None


class QgsSimpleFillSymbolLayer(_SymbolLayer):
    pass


class QgsSimpleMarkerSymbolLayer(_SymbolLayer):
    def __init__(self):
        super().__init__()
        self._shape = None

    @staticmethod
    def decodeShape(name):
        return (name, True)

    @staticmethod
    def encodeShape(shape):
        return shape

    def setShape(self, shape):
        self._shape = shape

    def shape(self):
        return self._shape


class QgsSimpleLineSymbolLayer(_SymbolLayer):
    pass


_LAYER_FOR = {0: QgsSimpleMarkerSymbolLayer, 1: QgsSimpleLineSymbolLayer, 2: QgsSimpleFillSymbolLayer}


class _Symbol:
    def __init__(self, geometry_type):
        self.geometry_type = geometry_type
        self._color, self._size, self._opacity = _QColor("#000000"), None, 1.0
        self._layer = _LAYER_FOR[geometry_type]()
        self._data_defined = None

    def setColor(self, c):
        self._color = c if hasattr(c, "name") else _QColor(c)

    def color(self):
        return self._color

    def setSize(self, s):
        self._size = s

    def size(self):
        return self._size

    def setOpacity(self, o):
        self._opacity = o

    def opacity(self):
        return self._opacity

    def setDataDefinedSize(self, p):
        self._data_defined = p

    def dataDefinedSize(self):
        return self._data_defined

    def symbolLayerCount(self):
        return 1

    def symbolLayer(self, i):
        return self._layer


class QgsSymbol(_Stub):
    @staticmethod
    def defaultSymbol(geometry_type):
        return _Symbol(geometry_type)


class QgsSingleSymbolRenderer:
    def __init__(self, symbol):
        self._symbol = symbol

    def symbol(self):
        return self._symbol


class _Property:
    """QgsProperty as this code uses it. `isActive()` is a METHOD and matters: an inactive property
    is not driving anything, and treating one as active would read a stale expression as a live
    data-defined height."""

    def __init__(self, expression="", active=True):
        self._e, self._active = expression, active

    @staticmethod
    def fromExpression(expression):
        return _Property(expression)

    def expressionString(self):
        return self._e

    def isActive(self):
        return self._active


class _PropertyCollection:
    def __init__(self):
        self._by_key = {}

    def setProperty(self, key, prop):
        self._by_key[key] = prop

    def property(self, key):
        return self._by_key.get(key)


# ── the 3D half ──────────────────────────────────────────────────────────────────────────────────
class QgsAbstract3DSymbol:
    #: The OLD spelling, which QGIS 3.28 uses. The plugin also asks for the newer
    #: `Property.ExtrusionHeight`; leaving only one here would let a probe that finds neither pass.
    PropertyExtrusionHeight = 1
    PropertyHeight = 2

    def __init__(self):
        self._properties = _PropertyCollection()

    def dataDefinedProperties(self):
        return self._properties

    def setDataDefinedProperties(self, collection):
        self._properties = collection


class QgsPhongMaterialSettings:
    def __init__(self):
        self._diffuse, self._ambient = None, None

    def setDiffuse(self, c):
        self._diffuse = c

    def diffuse(self):
        return self._diffuse

    def setAmbient(self, c):
        self._ambient = c


class _Material3DSymbol(QgsAbstract3DSymbol):
    def __init__(self):
        super().__init__()
        self._material = None

    def setMaterialSettings(self, m):
        self._material = m

    def materialSettings(self):
        return self._material


class QgsPolygon3DSymbol(_Material3DSymbol):
    def __init__(self):
        super().__init__()
        self._extrusion, self._height = 0.0, 0.0

    def setExtrusionHeight(self, h):
        self._extrusion = h

    def extrusionHeight(self):
        return self._extrusion

    def setHeight(self, h):
        self._height = h

    def height(self):
        return self._height


class QgsPoint3DSymbol(_Material3DSymbol):
    Cylinder = 3

    def __init__(self):
        super().__init__()
        self._shape, self._shape_properties = None, {}

    def setShape(self, shape):
        self._shape = shape

    def shape(self):
        return self._shape

    def setShapeProperties(self, properties):
        self._shape_properties = dict(properties)

    def shapeProperties(self):
        return dict(self._shape_properties)


class QgsVectorLayer3DRenderer:
    def __init__(self, symbol=None):
        self._symbol = symbol

    def symbol(self):
        return self._symbol


for name in ("QgsCategorizedSymbolRenderer", "QgsGraduatedSymbolRenderer", "QgsRendererCategory",
             "QgsRendererRange", "QgsClassificationRange", "QgsUnitTypes",
             "QgsMultiBandColorRenderer", "QgsSingleBandGrayRenderer", "QgsHillshadeRenderer",
             "QgsSingleBandPseudoColorRenderer", "QgsPalettedRasterRenderer", "QgsSymbolLayer",
             "QgsVectorTileBasicRenderer", "QgsVectorTileBasicRendererStyle"):
    setattr(core, name, type(name, (_Stub,), {}))
core.QgsUnitTypes.RenderPoints = 3
core.QgsSymbolLayer.PropertyStrokeWidth = 42
core.QgsProperty = _Property
core.QgsSymbol = QgsSymbol
core.QgsSingleSymbolRenderer = QgsSingleSymbolRenderer
core.QgsSimpleFillSymbolLayer = QgsSimpleFillSymbolLayer
core.QgsSimpleLineSymbolLayer = QgsSimpleLineSymbolLayer
core.QgsSimpleMarkerSymbolLayer = QgsSimpleMarkerSymbolLayer
core.QgsWkbTypes = QgsWkbTypes
for cls in (QgsAbstract3DSymbol, QgsPhongMaterialSettings, QgsPoint3DSymbol, QgsPolygon3DSymbol,
            QgsVectorLayer3DRenderer):
    setattr(three_d, cls.__name__, cls)
QtCore.Qt = type("Qt", (), {"DashLine": 2, "DotLine": 3, "NoPen": 0})
QtGui.QColor = _QColor
qgis.core, qgis.PyQt = core, PyQt
PyQt.QtCore, PyQt.QtGui = QtCore, QtGui
sys.modules.update({"qgis": qgis, "qgis.core": core, "qgis._3d": three_d, "qgis.PyQt": PyQt,
                    "qgis.PyQt.QtCore": QtCore, "qgis.PyQt.QtGui": QtGui})

import os                                                                       # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "geodeploy_qgis", "vendor"))

lines = open(os.path.join(HERE, "..", "geodeploy_qgis", "symbology.py"),
             encoding="utf-8").read().splitlines()
src = "\n".join(l for l in lines if not l.startswith("from .connection"))
ns = {}
exec(compile(src, "symbology", "exec"), ns)                                     # noqa: S102
assert ns["QGIS"] is True, "the fake qgis.core was not picked up"

apply_3d = ns["apply_3d"]
extrusion_from_qgis = ns["extrusion_from_qgis"]
apply_to_qgis = ns["apply_to_qgis"]
from_qgis = ns["from_qgis"]
comparable_style = ns["comparable_style"]
merge_style = ns["merge_style"]
is_extruded = ns["is_extruded"]
P_EXTRUSION = ns["P_EXTRUSION"]

assert ns["_qgis3d"]("QgsPolygon3DSymbol") is QgsPolygon3DSymbol, "the fake qgis._3d was not found"
assert ns["_3d_property_key"]("extrusion") == QgsAbstract3DSymbol.PropertyExtrusionHeight
assert ns["_3d_property_key"]("height") == QgsAbstract3DSymbol.PropertyHeight
print("probes         -> the 3D classes and both enum spellings resolve")


class VectorLayer:
    """A QgsVectorLayer as the 3D path touches it. `geometryType()` is a METHOD, and the renderer3D
    accessors are the pair QGIS has — a stub that only stored the symbol would never exercise the
    "the user deleted the 3D renderer" branch."""

    def __init__(self, geometry=QgsWkbTypes.PolygonGeometry):
        self._geometry = geometry
        self._renderer, self._renderer3d = None, None
        self._properties = {}
        self.repainted = False

    def geometryType(self):
        return self._geometry

    def setRenderer(self, r):
        self._renderer = r

    def renderer(self):
        return self._renderer

    def setRenderer3D(self, r):
        self._renderer3d = r

    def renderer3D(self):
        return self._renderer3d

    def setCustomProperty(self, key, value):
        self._properties[key] = value

    def customProperty(self, key, default=None):
        return self._properties.get(key, default)

    def triggerRepaint(self):
        self.repainted = True


core.QgsVectorLayer = VectorLayer


def round_trip(style, geometry=QgsWkbTypes.PolygonGeometry):
    layer = VectorLayer(geometry)
    assert apply_3d(layer, style), "apply_3d refused {0!r}".format(style)
    return extrusion_from_qgis(layer), layer


def same(a, b, note="", geometry="polygon"):
    """Compared as `portals._style_differs` compares — WITH the geometry, because `outline_width`
    is a ratio of the radius on a point and a width in pixels on a polygon, so their defaults
    differ and a comparison that cannot tell them apart reports every polygon as restyled."""
    ca, cb = comparable_style(a, geometry), comparable_style(b, geometry)
    assert ca == cb, "{0}\n  applied: {1}\n  read   : {2}".format(note, ca, cb)


# ── polygons ─────────────────────────────────────────────────────────────────────────────────────
style = {"color": "#3b82f6",
         "extrusion": {"enabled": True, "height": 25.0, "color": "#ef4444", "base": 4.0}}
read, layer = round_trip(style)
same(style, {"extrusion": read}, "a fixed-height polygon extrusion did not survive")
symbol = layer.renderer3D().symbol()
assert isinstance(symbol, QgsPolygon3DSymbol), type(symbol)
assert symbol.extrusionHeight() == 25.0
assert symbol.height() == 4.0, "the base did not reach the symbol"
assert symbol.materialSettings().diffuse().name() == "#ef4444", "the 3D colour is its own"
print("polygon fixed  -> height, base and colour all land, and read back unchanged")

style = {"extrusion": {"enabled": True, "field": "levels", "scale": 3.0, "color": "#22c55e"}}
read, layer = round_trip(style)
same(style, {"extrusion": read}, "a column-driven polygon extrusion did not survive")
expression = layer.renderer3D().symbol().dataDefinedProperties().property(
    QgsAbstract3DSymbol.PropertyExtrusionHeight).expressionString()
assert expression == '"levels" * 3', expression
print("polygon field  -> a data-defined height, per feature, not one averaged block")

# A base driven by a COLUMN is a different property, and it must not be written as a number.
read, layer = round_trip({"extrusion": {"enabled": True, "height": 10, "base": "ground_z"}})
assert read.get("base") == "ground_z", read
base_expression = layer.renderer3D().symbol().dataDefinedProperties().property(
    QgsAbstract3DSymbol.PropertyHeight).expressionString()
assert base_expression == '"ground_z"', base_expression
print("polygon base   -> a column base rides the Height property, not the extrusion")

# Scale 1 needs no multiplication — and the expression has to read back as scale 1, not as 1.0
# written into the style where nothing had one.
read, _ = round_trip({"extrusion": {"enabled": True, "field": "h"}})
assert read["field"] == "h" and "scale" not in read, read
print("scale of 1     -> no multiplier written, and none invented on the way back")

# ── points: the case QGIS cannot express ─────────────────────────────────────────────────────────
pillars = {"extrusion": {"enabled": True, "field": "pop", "scale": 0.5, "radius": 120.0,
                         "color": "#a855f7", "opacity": 0.8}}
read, layer = round_trip(pillars, QgsWkbTypes.PointGeometry)
symbol = layer.renderer3D().symbol()
assert isinstance(symbol, QgsPoint3DSymbol), type(symbol)
assert symbol.shape() == QgsPoint3DSymbol.Cylinder, "pillars are cylinders; a box is a different map"
assert symbol.shapeProperties()["radius"] == 120.0, symbol.shapeProperties()
# THE POINT OF THE WHOLE MECHANISM: a cylinder has one length, so the column driving the height has
# no equivalent — and reading the symbol back naively would report a fixed height, which the merge
# would treat as "the user replaced the column". The recorded spec is what travels.
assert read["field"] == "pop" and read["scale"] == 0.5, read
assert read["opacity"] == 0.8, "opacity has no cylinder equivalent either, and must not be lost"
same(pillars, {"extrusion": read}, "a point pillar's column-driven height did not survive")
print("point pillars  -> cylinder + radius applied, and the column/opacity QGIS cannot hold survive")

read, layer = round_trip({"extrusion": {"enabled": True, "height": 300.0}},
                         QgsWkbTypes.PointGeometry)
assert layer.renderer3D().symbol().shapeProperties()["length"] == 300.0
assert layer.renderer3D().symbol().shapeProperties()["radius"] == 30.0, "QGIS needs SOME footprint"
# AND THE FALLBACK MUST NOT TRAVEL BACK. A style that names no radius is one the INSTANCE sizes,
# from the layer's own extent (`services/symbology.pillar_radius`) — 240 country centroids get a
# footprint scaled to the world, not the 30 m floor this end uses to draw something. Writing the
# fallback into the style would pin every such layer to bars a few thousandths of a pixel wide,
# which is indistinguishable from "3D is broken".
assert read["height"] == 300.0 and "radius" not in read, read
print("point fixed    -> length from the height; the local fallback footprint does not travel back")

# ── edits made in QGIS come back ─────────────────────────────────────────────────────────────────
_, layer = round_trip({"extrusion": {"enabled": True, "height": 25.0}})
layer.renderer3D().symbol().setExtrusionHeight(80.0)        # the user drags it taller
read = extrusion_from_qgis(layer)
assert read["height"] == 80.0, read
assert comparable_style({"extrusion": {"enabled": True, "height": 25.0}}) != comparable_style(
    {"extrusion": read}), "a real height change must not compare equal"
print("edited height  -> the new height travels, and registers as a change")

_, layer = round_trip({"extrusion": {"enabled": True, "field": "pop", "radius": 90.0}},
                      QgsWkbTypes.PointGeometry)
layer.renderer3D().symbol().setShapeProperties({"shape": "Cylinder", "radius": 250.0,
                                                "length": 40.0})
read = extrusion_from_qgis(layer)
assert read["radius"] == 250.0, read
# The COLUMN is still on the symbol — widening a pillar is not replacing what drives its height —
# so it is still reported, and `merge_style` then drops the fixed length a column overrides.
assert read["field"] == "pop", read
assert merge_style({"extrusion": {"enabled": True, "field": "pop", "radius": 90.0}},
                   {"extrusion": read})["extrusion"]["radius"] == 250.0
print("widened pillar -> the new footprint travels, and the column driving it is not lost")

# Now REMOVE what drives the height. That is the edit that must clear the column, and an INACTIVE
# property has to count as removed — QGIS leaves the expression text behind when you untick it, so
# reading the string without checking `isActive()` would resurrect a column the user turned off.
_, layer = round_trip({"extrusion": {"enabled": True, "field": "pop", "radius": 90.0}},
                      QgsWkbTypes.PointGeometry)
symbol = layer.renderer3D().symbol()
collection = _PropertyCollection()
collection.setProperty(QgsAbstract3DSymbol.PropertyExtrusionHeight, _Property('"pop"', active=False))
symbol.setDataDefinedProperties(collection)
symbol.setShapeProperties({"shape": "Cylinder", "radius": 90.0, "length": 55.0})
read = extrusion_from_qgis(layer)
assert "field" not in read, "an unticked expression is not a live column: {0}".format(read)
assert read["height"] == 55.0, read
merged = merge_style({"extrusion": {"enabled": True, "field": "pop", "scale": 2.0}},
                     {"extrusion": read})
assert "field" not in merged["extrusion"] and merged["extrusion"]["height"] == 55.0, merged
print("column removed -> read as a fixed height, and the merge drops the column with it")

# Switching 3D OFF in QGIS is an edit too, and it has to travel as one.
_, layer = round_trip({"extrusion": {"enabled": True, "height": 25.0}})
layer.setRenderer3D(None)
assert extrusion_from_qgis(layer) == {"enabled": False}
print("3D removed     -> reported as disabled, not as silence")

# A layer that never had 3D says NOTHING — silence, so a push cannot delete a portal's extrusion.
untouched = VectorLayer()
assert extrusion_from_qgis(untouched) is None
assert merge_style({"extrusion": {"enabled": True, "field": "pop"}}, {"color": "#ff0000"}) == {
    "extrusion": {"enabled": True, "field": "pop"}, "color": "#ff0000"}
print("never 3D       -> silence, so a push cannot delete a portal's extrusion")

# Turning it off in GeoDeploy and reopening has to actually turn it off in QGIS.
_, layer = round_trip({"extrusion": {"enabled": True, "height": 25.0}})
assert apply_3d(layer, {"extrusion": {"enabled": False, "height": 25.0}}) is False
assert layer.renderer3D() is None, "a disabled extrusion left a 3D renderer standing"
assert not layer.customProperty(P_EXTRUSION)
print("disabled       -> the 3D renderer is cleared, not left behind to disagree")

# ── merge: key by key, with the height SOURCE still switching cleanly ────────────────────────────
merged = merge_style({"extrusion": {"enabled": True, "field": "pop", "scale": 2.0,
                                    "opacity": 0.8, "radius": 100.0}},
                     {"extrusion": {"enabled": True, "height": 40.0, "radius": 100.0}})
assert "field" not in merged["extrusion"] and "scale" not in merged["extrusion"], merged
assert merged["extrusion"]["opacity"] == 0.8, "opacity QGIS never saw must not be dropped"
merged = merge_style({"extrusion": {"enabled": True, "height": 40.0, "opacity": 0.8}},
                     {"extrusion": {"enabled": True, "field": "pop", "scale": 2.0}})
assert "height" not in merged["extrusion"], "a column drives it now; the fixed height is not drawn"
merged = merge_style({"extrusion": {"enabled": True, "field": "pop"}, "color": "#111111"},
                     {"extrusion": {"enabled": False}})
assert merged["extrusion"] == {"enabled": False, "field": "pop"}, merged
print("merge          -> sub-keys survive, the height source switches, disabling keeps the field")

# ── comparison: what is not drawn is not a difference ────────────────────────────────────────────
same({}, {"extrusion": {"enabled": False, "field": "pop", "height": 9}},
     "an extrusion switched off draws exactly like none")
same({}, {"extrusion": {"enabled": True}}, "enabled with no height is drawn flat")
same({"extrusion": {"enabled": True, "field": "h", "height": 12}},
     {"extrusion": {"enabled": True, "field": "h", "height": 99}},
     "a column drives the height, so the fixed one beside it is not drawn")
same({"extrusion": {"enabled": True, "height": 5, "color": "#AABBCC"}},
     {"extrusion": {"enabled": True, "height": 5.0, "color": "#aabbcc"}}, "colour case, and 5 vs 5.0")
assert comparable_style({"extrusion": {"enabled": True, "height": 5}}) != comparable_style(
    {"extrusion": {"enabled": True, "height": 6}})
assert comparable_style({"extrusion": {"enabled": True, "field": "a"}}) != comparable_style(
    {"extrusion": {"enabled": True, "field": "b"}})
# A base field is DATA, like a category value — folding its case would mislabel the map.
assert comparable_style({"extrusion": {"enabled": True, "height": 1, "base": "Ground"}}) != \
    comparable_style({"extrusion": {"enabled": True, "height": 1, "base": "ground"}})
print("comparison     -> undrawn keys folded, real differences kept, field names left as data")

assert is_extruded({"extrusion": {"enabled": True, "height": 3}}) is True
assert is_extruded({"extrusion": {"enabled": True}}) is False
assert is_extruded({"extrusion": {"enabled": False, "height": 3}}) is False
print("is_extruded    -> agrees with services/symbology.is_extruded")

# ── the whole style, through the ordinary entry points ───────────────────────────────────────────
layer = VectorLayer(QgsWkbTypes.PolygonGeometry)
full = {"color_mode": "single", "color": "#3b82f6", "fill_opacity": 0.45,
        "outline_color": "#1d4ed8",
        "extrusion": {"enabled": True, "field": "levels", "scale": 3.0, "color": "#ef4444"}}
assert apply_to_qgis(layer, full), "apply_to_qgis refused the style"
assert layer.renderer3D() is not None, "2D styling applied but 3D was skipped"
read = from_qgis(layer)
same(full, read, "the full style did not round-trip through apply_to_qgis/from_qgis")
print("full style     -> 2D and 3D applied together, and read back together, unchanged")

# 3D is a SECOND renderer: a layer can be extruded and classified at once, and reading only the
# branch the 2D renderer matched is how half a style goes missing.
assert read["color_mode"] == "single" and read["extrusion"]["field"] == "levels"
assert json.loads(layer.customProperty(P_EXTRUSION))["field"] == "levels"
print("both renderers -> the 2D style and the extrusion travel in the same dict")

# ── the shapes that actually exist, taken off a live instance ────────────────────────────────────
#
# Read from geodeploy-lite on 2026-08-17 — every `extrusion` block stored on a layer or baked into a
# portal's layer_config. Kept here as fixtures rather than as a live check so the suite stays
# offline, but they are not invented: each one is a style somebody built and published, and two of
# them are cases no amount of reasoning produced.
LIVE = [
    # A polygon extruded by a column, which is what almost every real one is.
    ("21f_buildings", "polygon", {"enabled": True, "field": "area_in_meters", "scale": 10}),
    ("portal/layer 19", "polygon", {"enabled": True, "field": "area_in_meters", "scale": 2}),
    ("portal/layer 15", "polygon", {"enabled": True, "field": "Height", "scale": 10}),
    ("portal/layer 10", "polygon", {"enabled": True, "field": "SURF_PARC", "scale": 10}),
    # THE DEGENERATE ONE. `CO` is stored with the box ticked and no height at all — GeoDeploy draws
    # it flat (`services/symbology.is_extruded` wants a height too), so QGIS must draw it flat, and
    # a push must not "helpfully" turn it into a zero-height volume.
    ("CO", "polygon", {"enabled": True}),
    # A POINT PILLAR AT WORLD SCALE: country centroids extruded by longitude, with a 10 000 km
    # footprint. The radius is not a typo — it is what a layer spanning the globe needs to be
    # visible at all, and it is why the plugin must never overwrite a stored radius with its own
    # 30 m fallback.
    ("portal/layer 1", "point", {"enabled": True, "field": "longitude", "scale": 1000,
                                 "radius": 10000000}),
]
for name, geometry, extrusion in LIVE:
    style = {"color": "#3b82f6", "extrusion": extrusion}
    kind = QgsWkbTypes.PointGeometry if geometry == "point" else QgsWkbTypes.PolygonGeometry
    layer = VectorLayer(kind)
    apply_3d(layer, style)
    read = extrusion_from_qgis(layer)
    after = merge_style(style, {"extrusion": read} if read is not None else {})
    same(style, after, "live style {0!r} would report a phantom change".format(name))
    if not is_extruded(style):
        assert layer.renderer3D() is None, "{0} draws flat in GeoDeploy and must in QGIS".format(name)
    else:
        assert layer.renderer3D() is not None, "{0} is extruded and got no 3D symbol".format(name)
        if extrusion.get("radius"):
            assert layer.renderer3D().symbol().shapeProperties()["radius"] == extrusion["radius"], \
                "a stored footprint must not be replaced by the local fallback"
print("live styles    -> {0} real extrusions round-trip with no phantom change".format(len(LIVE)))

print("\nALL 3D SYMBOLOGY CASES PASS")
