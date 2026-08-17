"""Prove a RASTER's symbology survives the trip to QGIS and back unchanged.

The complaint this exists to stop coming back: "I cannot classify a raster in QGIS". The reason was
never the symbology code — it was that the plugin only ever handed QGIS the SERVER-RENDERED TILES,
which reach it as one band of RGBA ("Singleband color data") with no bands to stretch and no classes
to build. Opening the GeoTIFF instead gives QGIS real values, and `raster_to_qgis` is what stops
that from being a downgrade: the pixels arrive with GeoDeploy's own colormap, stretch, band and
classification already applied.

That only counts if it goes BOTH WAYS, so almost everything here is a round trip: a style is applied
to a stand-in QGIS layer, read back with `raster_from_qgis`, and compared through `comparable_style`
— the same comparison `portals._style_differs` uses to decide whether a layer was restyled. A round
trip that reports a change nobody made is as broken as one that loses a colour.

QGIS is not importable here, so the renderer classes are stand-ins. They are written to be faithful
about TYPES and about whether something is a method — the lesson from `test_tile_symbology`, where a
stub that exposed `geometryType` as an attribute hid the very bug the file was written to pin.
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


class _QColor:
    """QColor as this code uses it: built from channels or from a hex string, read back as both.

    `name()` returns SIX digits, as Qt does — the alpha is only available through `alpha()`. Getting
    that wrong here would hide the difference between `#ff0000` and `#ff000080`, which is the whole
    of the transparency that a "no data" class depends on.
    """

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

    def __eq__(self, other):
        return (self._r, self._g, self._b, self._a) == (
            other.red(), other.green(), other.blue(), other.alpha())


class _Ramp:
    """A named ramp from the style library. Deterministic, and DIFFERENT per name — the plugin
    recognises a palette by the colours it produces, so two ramps that sampled alike would make the
    test pass against a reader that could not actually tell them apart."""

    def __init__(self, name):
        self.name = name
        self._inverted = False

    def color(self, fraction):
        f = max(0.0, min(1.0, float(fraction)))
        if self._inverted:
            f = 1.0 - f
        seed = sum(ord(c) for c in self.name) % 100
        return _QColor(int(seed + f * 100) % 256, int(255 * f), int(200 - f * 150))

    def clone(self):
        clone = _Ramp(self.name)
        clone._inverted = self._inverted
        return clone

    def invert(self):
        self._inverted = not self._inverted


class QgsGradientColorRamp:
    """The ramp built from stops when QGIS has no palette of that name (`terrain`)."""

    def __init__(self, c1, c2):
        self._stops = [(0.0, c1), (1.0, c2)]

    def setStops(self, stops):
        middle = [(s.offset, s.color) for s in stops]
        self._stops = [self._stops[0]] + sorted(middle) + [self._stops[-1]]

    def color(self, fraction):
        f = max(0.0, min(1.0, float(fraction)))
        for (p0, c0), (p1, c1) in zip(self._stops, self._stops[1:]):
            if p0 <= f <= p1:
                t = 0.0 if p1 == p0 else (f - p0) / (p1 - p0)
                return _QColor(int(c0.red() + (c1.red() - c0.red()) * t),
                               int(c0.green() + (c1.green() - c0.green()) * t),
                               int(c0.blue() + (c1.blue() - c0.blue()) * t))
        return self._stops[-1][1]

    def clone(self):
        clone = QgsGradientColorRamp(self._stops[0][1], self._stops[-1][1])
        clone._stops = list(self._stops)
        return clone


class QgsGradientStop:
    def __init__(self, offset, color):
        self.offset, self.color = offset, color


#: What QGIS ships. Deliberately WITHOUT `terrain` — QGIS genuinely has no ramp of that name, which
#: is why the plugin carries its stops, and a stub that pretended otherwise would never test that.
_RAMP_NAMES = ["Viridis", "Magma", "Plasma", "Inferno", "Cividis", "Greys", "Spectral",
               "RdYlGn", "RdBu", "Turbo"]


class QgsStyle(_Stub):
    @staticmethod
    def defaultStyle():
        return QgsStyle()

    def colorRampNames(self):
        return list(_RAMP_NAMES)

    def colorRamp(self, name):
        return _Ramp(name) if name in _RAMP_NAMES else None


class QgsContrastEnhancement:
    StretchToMinimumMaximum = 1

    def __init__(self, data_type=None):
        self.data_type = data_type
        self._min, self._max, self._algorithm = None, None, None

    def setContrastEnhancementAlgorithm(self, algorithm, generate=True):
        self._algorithm = algorithm

    def setMinimumValue(self, v):
        self._min = v

    def setMaximumValue(self, v):
        self._max = v

    def minimumValue(self):
        return self._min

    def maximumValue(self):
        return self._max


class QgsColorRampShader:
    Interpolated, Discrete, Exact = 0, 1, 2
    Continuous, EqualInterval, Quantile = 0, 1, 2

    class ColorRampItem:
        def __init__(self, value, color, label=""):
            self.value, self.color, self.label = value, color, label

    def __init__(self, minimum=0.0, maximum=1.0, ramp=None, type_=0, mode=0):
        self.minimum, self.maximum = minimum, maximum
        self._ramp = ramp
        self._items = []
        self._type, self._mode = None, None

    def setColorRampType(self, t):
        self._type = t

    def setClassificationMode(self, m):
        self._mode = m

    def setColorRampItemList(self, items):
        self._items = list(items)

    def colorRampItemList(self):
        return list(self._items)

    def setSourceColorRamp(self, ramp):
        self._ramp = ramp

    def sourceColorRamp(self):
        return self._ramp


class QgsRasterShader:
    def __init__(self):
        self._fn = None

    def setRasterShaderFunction(self, fn):
        self._fn = fn

    def rasterShaderFunction(self):
        return self._fn


class QgsSingleBandPseudoColorRenderer:
    def __init__(self, provider, band, shader):
        self._provider, self._band, self._shader = provider, band, shader
        self._min, self._max = None, None

    def band(self):
        return self._band

    def shader(self):
        return self._shader

    def setClassificationMin(self, v):
        self._min = v

    def setClassificationMax(self, v):
        self._max = v

    def classificationMin(self):
        return self._min

    def classificationMax(self):
        return self._max


class QgsPalettedRasterRenderer:
    class Class:
        def __init__(self, value, color, label=""):
            self.value, self.color, self.label = value, color, label

    def __init__(self, provider, band, classes):
        self._provider, self._band, self._classes = provider, band, list(classes)

    def band(self):
        return self._band

    def classes(self):
        return list(self._classes)


class QgsHillshadeRenderer:
    def __init__(self, provider, band, azimuth, altitude):
        self._provider, self._band = provider, band
        self._azimuth, self._altitude, self._z = azimuth, altitude, 1.0

    def band(self):
        return self._band

    def setZFactor(self, z):
        self._z = z

    def zFactor(self):
        return self._z

    def azimuth(self):
        return self._azimuth

    def altitude(self):
        return self._altitude


class QgsSingleBandGrayRenderer:
    def __init__(self, provider, band):
        self._provider, self._band, self._enhancement = provider, band, None

    def grayBand(self):
        return self._band

    def setContrastEnhancement(self, e):
        self._enhancement = e

    def contrastEnhancement(self):
        return self._enhancement


class QgsMultiBandColorRenderer:
    def __init__(self, provider, red, green, blue):
        self._provider = provider
        self._bands = [red, green, blue]
        self._enhancements = [None, None, None]

    def redBand(self):
        return self._bands[0]

    def greenBand(self):
        return self._bands[1]

    def blueBand(self):
        return self._bands[2]

    def setRedContrastEnhancement(self, e):
        self._enhancements[0] = e

    def setGreenContrastEnhancement(self, e):
        self._enhancements[1] = e

    def setBlueContrastEnhancement(self, e):
        self._enhancements[2] = e

    def redContrastEnhancement(self):
        return self._enhancements[0]


class QgsSingleBandColorDataRenderer:
    """What a server-rendered tile layer gets: colour, not values. Nothing to read back."""


for name in ("QgsCategorizedSymbolRenderer", "QgsGraduatedSymbolRenderer", "QgsRendererCategory",
             "QgsRendererRange", "QgsSingleSymbolRenderer", "QgsClassificationRange",
             "QgsSimpleFillSymbolLayer", "QgsSimpleLineSymbolLayer", "QgsSimpleMarkerSymbolLayer",
             "QgsSymbol", "QgsUnitTypes", "QgsVectorTileBasicRenderer",
             "QgsVectorTileBasicRendererStyle", "QgsWkbTypes", "QgsProperty", "QgsSymbolLayer"):
    setattr(core, name, type(name, (_Stub,), {}))
core.QgsUnitTypes.RenderPoints = 3

for cls in (QgsColorRampShader, QgsContrastEnhancement, QgsGradientColorRamp, QgsGradientStop,
            QgsHillshadeRenderer, QgsMultiBandColorRenderer, QgsPalettedRasterRenderer,
            QgsRasterShader, QgsSingleBandGrayRenderer, QgsSingleBandPseudoColorRenderer,
            QgsStyle):
    setattr(core, cls.__name__, cls)
QtCore.Qt = type("Qt", (), {"DashLine": 2, "DotLine": 3, "NoPen": 0})
QtGui.QColor = _QColor
qgis.core, qgis.PyQt = core, PyQt
PyQt.QtCore, PyQt.QtGui = QtCore, QtGui
sys.modules.update({"qgis": qgis, "qgis.core": core, "qgis.PyQt": PyQt,
                    "qgis.PyQt.QtCore": QtCore, "qgis.PyQt.QtGui": QtGui})

import os                                                                       # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "geodeploy_qgis", "vendor"))
sys.path.insert(0, os.path.join(HERE, "..", "geodeploy_qgis"))

lines = open(os.path.join(HERE, "..", "geodeploy_qgis", "symbology.py"),
             encoding="utf-8").read().splitlines()
src = "\n".join(l for l in lines if not l.startswith("from .connection"))
ns = {}
exec(compile(src, "symbology", "exec"), ns)                                     # noqa: S102
assert ns["QGIS"] is True, "the fake qgis.core was not picked up"
assert ns["QGIS_RASTER"] is True, "the raster half of the qgis.core import did not resolve"

raster_to_qgis = ns["raster_to_qgis"]
raster_from_qgis = ns["raster_from_qgis"]
raster_style_from_legend = ns["raster_style_from_legend"]
raster_style_of = ns["raster_style_of"]
comparable_style = ns["comparable_style"]
merge_style = ns["merge_style"]
qcolor = ns["_qcolor"]

import sources                                                                  # noqa: E402

#: Every colormap the instance publishes — `raster_from_qgis` will not claim a name the server does
#: not have, so the gate has to be realistic or the test proves nothing about the real path.
COLORMAPS = {"viridis", "plasma", "inferno", "magma", "cividis", "gray", "rdylgn", "rdbu",
             "spectral", "terrain"}


class _Provider:
    def __init__(self, band_count=1, lo=0.0, hi=100.0):
        self.band_count = band_count
        self._lo, self._hi = lo, hi

    def dataType(self, band):
        return 6                        # Float32, as far as anything here cares

    def bandStatistics(self, band):
        return type("Stats", (), {"minimumValue": self._lo, "maximumValue": self._hi})()


class RasterLayer:
    """A QgsRasterLayer as this code touches it. `providerType()` is a METHOD, as in QGIS — the
    plugin refuses to band-render anything that is not GDAL-backed, and an attribute here would let
    a WMS layer through the guard that exists to stop exactly that."""

    def __init__(self, provider_type="gdal", band_count=1):
        self._provider = _Provider(band_count)
        self._provider_type = provider_type
        self._renderer = None
        self._properties = {}
        self.repainted = False

    def dataProvider(self):
        return self._provider

    def providerType(self):
        return self._provider_type

    def setRenderer(self, r):
        self._renderer = r

    def renderer(self):
        return self._renderer

    def triggerRepaint(self):
        self.repainted = True

    def setCustomProperty(self, key, value):
        self._properties[key] = value

    def customProperty(self, key, default=None):
        return self._properties.get(key, default)


def round_trip(style, layer=None):
    """Apply `style` to a fresh layer and read it straight back."""
    layer = layer or RasterLayer()
    assert raster_to_qgis(layer, style), "raster_to_qgis refused {0!r}".format(style)
    assert layer.repainted, "the layer was never repainted, so nothing would redraw"
    return raster_from_qgis(layer, COLORMAPS), layer


def same(a, b, note=""):
    ca, cb = comparable_style(a), comparable_style(b)
    assert ca == cb, "{0}\n  applied: {1}\n  read   : {2}".format(note, ca, cb)


# ── the round trip, one style at a time ──────────────────────────────────────────────────────────
CASES = [
    ("named colormap", {"colormap": "viridis", "rescale": "0,100", "bidx": [1]}),
    ("reversed ramp", {"colormap": "viridis", "colormap_reverse": True, "rescale": "0,100"}),
    ("aliased name", {"colormap": "gray", "rescale": "-2,2.5", "bidx": [2]}),
    ("ramp QGIS lacks", {"colormap": "terrain", "rescale": "0,1000", "bidx": [1]}),
    ("classified", {"bidx": [1], "color_classes": [{"value": 1, "color": "#ff0000"},
                                                   {"value": 2, "color": "#00ff00"},
                                                   {"value": 3, "color": "#0000ff80"}]}),
    ("hillshade", {"algorithm": "hillshade", "zfactor": 5.0, "bidx": [1]}),
    ("hillshade, no z", {"algorithm": "hillshade", "bidx": [1]}),
    ("rgb composite", {"bidx": [1, 2, 3], "rescale": "0,255"}),
    ("stretch only", {"bidx": [1], "rescale": "0.5563,0.9477"}),
]
for label, style in CASES:
    read, _ = round_trip(style)
    same(style, read, "{0} did not survive the round trip".format(label))
print("round trip     -> {0} raster styles return unchanged".format(len(CASES)))

# The stretch is the part that decides whether non-8-bit data draws at all, so check the NUMBERS
# rather than trusting the comparison to have looked at them.
read, _ = round_trip({"colormap": "viridis", "rescale": "0.5563,0.9477"})
assert read["rescale"] == "0.5563,0.9477", read
assert read["colormap"] == "viridis", read
print("stretch        -> carried exactly, not re-derived")

# ── the parts a comparison could hide ────────────────────────────────────────────────────────────
layer = RasterLayer()
raster_to_qgis(layer, {"colormap": "magma", "rescale": "0,10", "bidx": [2]})
renderer = layer.renderer()
assert isinstance(renderer, QgsSingleBandPseudoColorRenderer), type(renderer)
assert renderer.band() == 2, "the band choice did not reach the renderer"
assert renderer.classificationMin() == 0.0 and renderer.classificationMax() == 10.0
items = renderer.shader().rasterShaderFunction().colorRampItemList()
assert len(items) == ns["_RAMP_STOPS"], len(items)
assert items[0].value == 0.0 and items[-1].value == 10.0, (items[0].value, items[-1].value)
# The shader gets a ramp of ITS OWN. Handing it the object this code is sampling would leave the
# loop above reading a ramp C++ had taken ownership of and freed.
shader_fn = renderer.shader().rasterShaderFunction()
assert shader_fn.sourceColorRamp() is not None, "QGIS's dialog needs the palette, not just stops"
print("pseudocolour   -> band, stretch and ramp items all land on the renderer")

layer = RasterLayer()
raster_to_qgis(layer, {"bidx": [1], "color_classes": [{"value": 7, "color": "#ff000080"}]})
cls = layer.renderer().classes()[0]
assert cls.value == 7, cls.value
# ALPHA LAST. Qt reads eight hex digits as #AARRGGBB, so a constructor left to its own conventions
# turns a half-transparent red into an opaque near-black — and a transparent "no data" class into a
# painted one.
assert (cls.color.red(), cls.color.alpha()) == (255, 128), (cls.color.red(), cls.color.alpha())
print("paletted       -> value, colour and ALPHA all reach the class")

# ── labels: what a legend actually prints ────────────────────────────────────────────────────────
labelled = {"bidx": [1], "color_classes": [
    {"value": 11, "color": "#419bdf", "label": "Water"},
    {"value": 21, "color": "#397d49", "label": "Trees"},
    {"value": 30, "color": "#88b053"}]}
read, layer = round_trip(labelled)
assert layer.renderer().classes()[0].label == "Water", "the label never reached QGIS"
# THE WAY BACK is what was broken: the reader built {value, color} only, so a push replaced the
# stored classes with unlabelled ones and every legend fell back to bare numbers.
assert read["color_classes"][0].get("label") == "Water", read["color_classes"]
assert read["color_classes"][1].get("label") == "Trees", read["color_classes"]
same(labelled, read, "a labelled classification did not survive the round trip")
# QGIS labels an unnamed class with its own value, so that must NOT come back as a label somebody
# chose — otherwise a raster whose classes were never named reports as edited on every push.
assert "label" not in read["color_classes"][2], read["color_classes"][2]
same({"color_classes": [{"value": 30, "color": "#88b053"}]},
     {"color_classes": [{"value": 30, "color": "#88b053", "label": "30"}]},
     "a label that is just the value is the same legend as no label")
# But a real one is a real difference: it is what the legend prints, and it is DATA, so its case is
# not folded either.
assert comparable_style({"color_classes": [{"value": 1, "color": "#fff", "label": "Water"}]}) != \
    comparable_style({"color_classes": [{"value": 1, "color": "#fff", "label": "Lake"}]})
assert comparable_style({"color_classes": [{"value": 1, "color": "#fff", "label": "Water"}]}) != \
    comparable_style({"color_classes": [{"value": 1, "color": "#fff", "label": "water"}]})
# And renaming one in QGIS travels back as the rename it is.
layer.renderer().classes()[1].label = "Forest"
assert raster_from_qgis(layer, COLORMAPS)["color_classes"][1]["label"] == "Forest"
print("class labels   -> reach QGIS, come back, and a rename registers as a change")

# A classified raster is matched on RAW pixel values, so the tile URL carries no stretch — and a
# stretch that is not drawn is not a difference. (Reported as "unique values renders only the red":
# rescale mapped a float mask of 0/1/2 to 0/127/255 and two of the three classes stopped matching.)
same({"color_classes": [{"value": 1, "color": "#ff0000"}], "rescale": "0,2"},
     {"color_classes": [{"value": 1, "color": "#ff0000"}]},
     "a stretch is not applied to a value-lookup palette, so it is not a visible difference")
assert "rescale" not in comparable_style({"color_classes": [{"value": 1, "color": "#ff0000"}],
                                          "rescale": "0,2"})
print("classified     -> the stretch is not drawn, so it is not compared")

layer = RasterLayer()
raster_to_qgis(layer, {"algorithm": "hillshade", "zfactor": 3.5, "bidx": [1]})
shade = layer.renderer()
assert (shade.azimuth(), shade.altitude()) == (315.0, 45.0), "the sun moved; GeoDeploy renders 315/45"
assert shade.zFactor() == 3.5
print("hillshade      -> GeoDeploy's own sun position, and the z factor")

# ── a reversed classification re-pairs the COLOURS, exactly as TiTiler does ───────────────────────
layer = RasterLayer()
classes = [{"value": 1, "color": "#ff0000"}, {"value": 2, "color": "#00ff00"},
           {"value": 3, "color": "#0000ff"}]
raster_to_qgis(layer, {"bidx": [1], "color_classes": classes, "colormap_reverse": True})
drawn = [(c.value, c.color.name()) for c in layer.renderer().classes()]
assert drawn == [(1, "#0000ff"), (2, "#00ff00"), (3, "#ff0000")], drawn
print("reversed classes -> values keep their places, the palette runs the other way")

# ── edits made in QGIS come back ─────────────────────────────────────────────────────────────────
layer = RasterLayer()
raster_to_qgis(layer, {"colormap": "viridis", "rescale": "0,100"})
layer.renderer().setClassificationMin(10.0)     # the user drags the stretch
layer.renderer().setClassificationMax(90.0)
read = raster_from_qgis(layer, COLORMAPS)
assert read["rescale"] == "10,90", read
assert read["colormap"] == "viridis", "restretching is not choosing a different palette"
print("restretched    -> the new stretch travels, the palette is not lost with it")

# Flipping the ramp in QGIS's dialog is a real edit, and it has to come back as one.
layer = RasterLayer()
raster_to_qgis(layer, {"colormap": "spectral", "rescale": "0,1"})
fn = layer.renderer().shader().rasterShaderFunction()
fn.setColorRampItemList(list(reversed(fn.colorRampItemList())))
read = raster_from_qgis(layer, COLORMAPS)
assert read.get("colormap") == "spectral" and read.get("colormap_reverse") is True, read
print("ramp flipped   -> comes back as the same palette, reversed")

# A DIFFERENT ramp is not the recorded one, and must not be reported as it. Losing the name here is
# correct: QGIS does not record what a gradient is called, and inventing one would publish colours
# nobody chose.
layer = RasterLayer()
raster_to_qgis(layer, {"colormap": "viridis", "rescale": "0,100"})
fn = layer.renderer().shader().rasterShaderFunction()
fn.setColorRampItemList([QgsColorRampShader.ColorRampItem(0.0, _QColor("#123456"), "0"),
                         QgsColorRampShader.ColorRampItem(100.0, _QColor("#654321"), "100")])
read = raster_from_qgis(layer, COLORMAPS)
assert "colormap" not in read, read
assert read["rescale"] == "0,100", "the stretch is still true and still worth sending"
print("ramp replaced  -> no stale name is claimed, the stretch still travels")

# ── server-rendered tiles are declined at both ends ──────────────────────────────────────────────
tiles = RasterLayer(provider_type="wms")
assert raster_to_qgis(tiles, {"colormap": "viridis", "rescale": "0,1"}) is False
assert tiles.renderer() is None, "a finished picture must not be re-coloured through a ramp"
picture = RasterLayer()
picture.setRenderer(QgsSingleBandColorDataRenderer())
assert raster_from_qgis(picture, COLORMAPS) == {}
print("tiles          -> not styled, and nothing invented on the way back")

# ── comparison: the spellings that are not differences, and the ones that are ────────────────────
same({"rescale": "0.0,2.0"}, {"rescale": [0, 2]}, "a stretch is a stretch however it is written")
same({"colormap": "Viridis"}, {"colormap": "viridis"}, "a palette name is not case")
same({"colormap": "viridis_r"}, {"colormap": "viridis", "colormap_reverse": True},
     "matplotlib's suffix and GeoDeploy's flag say the same thing")
same({"color_classes": [{"value": 1, "color": "#3B82F6"}]},
     {"color_classes": [{"value": 1, "color": "#3b82f6ff"}]}, "an opaque colour, written twice")
same({"algorithm": "hillshade", "bidx": [1], "colormap": "viridis"},
     {"algorithm": "hillshade", "bidx": [1]},
     "a hillshade ignores the colormap, so a stale one is not a visible difference")
assert comparable_style({"rescale": "0,2"}) != comparable_style({"rescale": "0,3"})
assert comparable_style({"colormap": "viridis"}) != comparable_style({"colormap": "magma"})
assert comparable_style({"colormap": "viridis"}) != comparable_style(
    {"colormap": "viridis", "colormap_reverse": True})
print("comparison     -> spelling folded, real differences kept")

# A raster style must not be compared through the VECTOR defaults — that is what filled a raster
# with a marker size and a fill opacity and made every one of them look edited.
assert "radius" not in comparable_style({"colormap": "viridis"})
assert "radius" in comparable_style({"color": "#ff0000"})
print("shapes         -> a raster is compared as a raster, a vector as a vector")

# ── merge: a raster read-back is the whole colouring ─────────────────────────────────────────────
merged = merge_style({"colormap": "viridis", "rescale": "0,10", "opacity": 0.5},
                     {"algorithm": "hillshade", "zfactor": 2.0, "bidx": [1]})
assert "colormap" not in merged, merged
assert merged["algorithm"] == "hillshade" and merged["opacity"] == 0.5, merged
merged = merge_style({"color_classes": [{"value": 1, "color": "#ff0000"}], "bidx": [1]},
                     {"colormap": "magma", "rescale": "0,5", "bidx": [1]})
assert "color_classes" not in merged, "an explicit mapping outranks a colormap and would still win"
assert merged["colormap"] == "magma", merged
# And nothing read means nothing changed — the rule that stops a push deleting a portal's styling.
assert merge_style({"colormap": "viridis"}, {}) == {"colormap": "viridis"}
print("merge          -> a new renderer replaces the old colouring, and keeps the rest")

# ── the two readers that feed the apply side ─────────────────────────────────────────────────────
legend = {"kind": "raster", "ramp": True, "colormap": "terrain", "colormap_reverse": True,
          "rescale": [264.9, 298.33], "bidx": [1], "entries": [], "algorithm": None}
assert raster_style_from_legend(legend) == {"colormap": "terrain", "colormap_reverse": True,
                                            "rescale": "264.9,298.33", "bidx": [1]}
classified = {"kind": "raster", "ramp": False, "colormap": None, "rescale": None,
              "entries": [{"value": 11, "color": "#419bdf", "label": "Water"},
                          {"value": 21, "color": "#397d49", "label": "Trees"}]}
assert raster_style_from_legend(classified)["color_classes"] == [
    {"value": 11, "color": "#419bdf"}, {"value": 21, "color": "#397d49"}]
print("legend         -> read as a raster style, not as a vector's single colour")

# A raster's stored default style is FLAT where a vector's nests; both shapes are read, and
# `opacity` — which is applied separately — is not smuggled in as part of the colouring.
assert raster_style_of({"opacity": 0.6, "colormap": "viridis", "rescale": "0,1"}) == {
    "colormap": "viridis", "rescale": "0,1"}
assert raster_style_of({"opacity": 0.6, "style": {"colormap": "magma"}}) == {"colormap": "magma"}
assert raster_style_of({"opacity": 0.6}) == {}
print("stored style   -> flat or nested, both read; opacity left out of the colouring")

# ── a portal bakes its raster styling into the TILE URL, and it has to be readable back out ──────
base = "https://x.org/raster/cog/tiles/WebMercatorQuad/{z}/{x}/{y}?url=s3://b/k.tif"
assert sources.raster_style_from_tile_url(base + "&colormap_name=terrain") == {"colormap": "terrain"}
assert sources.raster_style_from_tile_url(base + "&colormap_name=viridis_r") == {
    "colormap": "viridis", "colormap_reverse": True}
assert sources.raster_style_from_tile_url(base + "&rescale=264.9,298.33") == {
    "rescale": "264.9,298.33"}
assert sources.raster_style_from_tile_url(
    base + "&algorithm=hillshade&expression=b1*5.0") == {"algorithm": "hillshade", "zfactor": 5.0}
assert sources.raster_style_from_tile_url(base + "&bidx=1&bidx=2&bidx=3") == {"bidx": [1, 2, 3]}
explicit = json.dumps({"2": [255, 0, 0, 255], "1": [0, 0, 255, 128]}, separators=(",", ":"))
from urllib.parse import quote as _q                                            # noqa: E402
assert sources.raster_style_from_tile_url(
    base + "&colormap=" + _q(explicit, safe="")) == {
        "color_classes": [{"value": 1, "color": "#0000ff80"},
                          {"value": 2, "color": "#ff0000ff"}]}
assert sources.raster_style_from_tile_url("") == {}
assert sources.raster_style_from_tile_url(base) == {}
# CONTOURS ride in one JSON blob, and the interval lives nowhere else — a portal drawing contours
# every 10 m would otherwise reopen at the algorithm's own 35 m default.
contour_url = (base + "&algorithm=contours&algorithm_params=" +
               _q(json.dumps({"increment": 10.0, "thickness": 2, "minz": 182, "maxz": 316}),
                  safe=""))
assert sources.raster_style_from_tile_url(contour_url) == {
    "algorithm": "contours", "increment": 10.0, "thickness": 2, "minz": 182, "maxz": 316}
assert sources.raster_style_from_tile_url(base + "&algorithm=contours&algorithm_params=%7Bbroken") \
    == {"algorithm": "contours"}, "an unreadable blob must not cost the algorithm"
print("portal tiles   -> the baked colormap, stretch, bands and hillshade all read back")

# What a portal's tile URL says must survive being applied and read again — that is the whole path
# behind "reopen this portal's raster as its GeoTIFF and restyle it".
baked = sources.raster_style_from_tile_url(base + "&bidx=1&rescale=0,2500&colormap_name=terrain")
read, _ = round_trip(baked)
same(baked, read, "a portal's baked raster styling did not survive being reopened")
print("portal restyle -> baked styling opens in QGIS and reads back identical")

# ── the source picker offers the data surface, and says which it is ──────────────────────────────
raster_row = {"_base": "https://x.org", "uid": "abc", "layer_type": "raster",
              "links": [{"id": "wmts", "url": "https://x.org/api/data/raster/abc/wmts"}]}
offered = sources.alternatives(raster_row)
assert [s["kind"] for s in offered] == ["wmts", "cog"], offered
assert [s["is_data"] for s in offered] == [False, True]
assert all(s["label"] for s in offered), "every source needs a name a menu can show"
untiled = {"_base": "https://x.org", "uid": "def", "kind": "vector"}
only = sources.alternatives(untiled)
assert [s["kind"] for s in only] == ["oapif"] and only[0]["is_data"] is True, only
print("sources        -> both surfaces offered for a raster, one for an untiled vector")

# ── speed: a colormap with no stretch must not read the raster to find one ───────────────────────
class _CountingProvider(_Provider):
    """Counts `bandStatistics` calls. On a remote COG that call is range requests over the network,
    so a styling nicety that made one on every add would be a stall on every add."""

    def __init__(self):
        super().__init__()
        self.stats_calls = 0

    def bandStatistics(self, *args, **kwargs):
        self.stats_calls += 1
        return super().bandStatistics(args[0] if args else 1)


layer = RasterLayer()
layer._provider = _CountingProvider()
# QGIS has already worked a range out when it opened the raster; it is on the default renderer.
opened = QgsSingleBandGrayRenderer(layer.dataProvider(), 1)
enhancement = QgsContrastEnhancement()
enhancement.setMinimumValue(12.0)
enhancement.setMaximumValue(88.0)
opened.setContrastEnhancement(enhancement)
layer.setRenderer(opened)
assert raster_to_qgis(layer, {"colormap": "viridis"})
assert layer.dataProvider().stats_calls == 0, "the raster was read to find a range QGIS already had"
assert layer.renderer().classificationMin() == 12.0, layer.renderer().classificationMin()
assert layer.renderer().classificationMax() == 88.0
print("no stretch     -> QGIS's own range reused, the raster never read")

# ── forward compatibility: a raster property this plugin has never met ───────────────────────────
#
# Contour styling is planned — `algorithm: "contours"` with `increment` and `thickness` — and the
# question that decides whether it round-trips on the day it lands is what happens to a key nothing
# here recognises. It must SURVIVE (an allowlist would drop it, and the layer would lose it the
# first time anyone opened it in QGIS) and it must be COMPARED (dropping it from the comparison
# would make a real change to one invisible).
future = {"algorithm": "contours", "increment": 25, "thickness": 2, "rescale": "0,3000",
          "bidx": [1]}
assert raster_style_of(dict(future, opacity=0.7)) == future, raster_style_of(dict(future))
assert comparable_style(future)["increment"] == 25, comparable_style(future)
assert comparable_style(future) != comparable_style(dict(future, increment=50))
# An algorithm other than hillshade still takes a stretch — TiTiler only drops one for hillshade,
# because that comes back as finished relief. Getting this wrong would lose a contour range.
assert comparable_style(future)["rescale"] == "0,3000", comparable_style(future)
assert "rescale" not in comparable_style({"algorithm": "hillshade", "rescale": "0,3000"})
print("future keys    -> an unknown raster property survives and is still compared")

# ── an algorithm QGIS has NO RENDERER FOR must survive the trip ──────────────────────────────────
#
# `hillshade` becomes a QgsHillshadeRenderer. `contours` becomes nothing: QGIS makes contours with a
# processing algorithm that outputs a VECTOR layer, so the raster is drawn here with its stretch
# alone. Reading THAT back would report a plain stretch, and the merge — which treats a raster
# read-back as the whole colouring — would drop `algorithm` and turn a contour layer grey.
layer = RasterLayer()
assert raster_to_qgis(layer, future), "a contour raster should still draw, with its stretch"
read = raster_from_qgis(layer, COLORMAPS)
assert read.get("algorithm") == "contours", read
assert read.get("increment") == 25 and read.get("thickness") == 2, read
same(future, merge_style(future, read), "contour styling did not survive the round trip")
print("contours       -> drawn as a stretch, and its algorithm comes back intact")

# …but a real restyle in QGIS REPLACES it, and that has to travel as the replacement it is.
layer = RasterLayer()
raster_to_qgis(layer, future)
raster_to_qgis(layer, {"colormap": "viridis", "rescale": "0,3000"})   # the user picks a palette
read = raster_from_qgis(layer, COLORMAPS)
assert "algorithm" not in read, read
assert read.get("colormap") == "viridis", read
assert "algorithm" not in merge_style(future, read), merge_style(future, read)
print("contours off   -> choosing a renderer in QGIS replaces it, and the merge clears it")

# A hillshade is NOT recorded this way — it becomes a real renderer and reads back on its own, so
# recording it too would mean restoring a stale copy over a genuine edit.
layer = RasterLayer()
raster_to_qgis(layer, {"algorithm": "hillshade", "zfactor": 5.0, "bidx": [1]})
assert not layer.customProperty(ns["P_RASTER_ALGO"]), "hillshade needs no note; it has a renderer"
print("hillshade      -> left to its own renderer, not shadowed by a recorded copy")

# ── the raster styles that actually exist, taken off a live instance ─────────────────────────────
#
# Read from geodeploy-lite on 2026-08-17: every raster's stored `default_style`, exactly as the API
# returns it — nulls and all, because that is what a reader has to survive. Offline fixtures, but
# not invented ones.
LIVE_RASTERS = [
    ("Degfert_DEM", {"algorithm": None, "bidx": [1], "color_classes": None, "colormap": None,
                     "colormap_reverse": False, "opacity": 1.0, "zfactor": None,
                     "rescale": "182.789993,315.959992"}),
    # The raster from the report that started this: a reversed named ramp over a 0–2 stretch.
    ("FINAL_MICROTOPO3-SLU202237", {"algorithm": None, "bidx": None, "color_classes": None,
                                    "colormap": "plasma", "colormap_reverse": True,
                                    "opacity": 1.0, "rescale": "0.0,2.0", "zfactor": None}),
    ("rvi-2023-2024-9", {"algorithm": None, "bidx": None, "colormap": None,
                         "colormap_reverse": False, "opacity": 1.0, "rescale": "0.5563,0.9477"}),
    ("Degfert_DEM_restr", {"algorithm": "hillshade", "bidx": None, "colormap": None,
                           "colormap_reverse": False, "opacity": 1.0, "zfactor": 5.0,
                           "rescale": "264.9,298.33"}),
    ("RVI_2023_2024", {"algorithm": None, "bidx": None, "colormap": "viridis",
                       "colormap_reverse": False, "opacity": 1.0, "rescale": "0.5563,0.9477"}),
]
for name, stored in LIVE_RASTERS:
    # `opacity` is applied separately and the nulls mean "not set"; both are the reader's job.
    style = raster_style_of(stored)
    assert "opacity" not in style and None not in style.values(), style
    read, _ = round_trip(style)
    same(style, merge_style(style, read), "live raster {0!r} would report a phantom change"
         .format(name))
print("live rasters   -> {0} real styles round-trip with no phantom change"
      .format(len(LIVE_RASTERS)))

# The two Degfert_DEM copies differ in the LAST DIGIT of their stretch — 315.959992 against
# 315.959991 — which is real float noise off the same source raster. They must not compare equal,
# or a genuine restretch that small would be invisible.
assert comparable_style({"rescale": "182.789993,315.959992"}) != comparable_style(
    {"rescale": "182.789993,315.959991"})
print("float noise    -> a stretch differing in the last digit is still a difference")

print("\nALL RASTER SYMBOLOGY CASES PASS")
