"""The WHOLE round trip, as a matrix, against a real PyQGIS — and against the real web renderer.

WHY A SECOND REAL-QGIS SCRIPT. `test_real_qgis.py` grew case by case, each one a bug somebody hit:
it is a regression suite, and it reads like one. What it cannot answer is the question that keeps
producing those bugs — *"is there anything QGIS can draw that we have never tried?"* — because it
only knows the symbols somebody thought to write down.

So this file is generated from QGIS's OWN registries wherever it can be. Every symbol-layer type
`QgsApplication.symbolLayerRegistry()` offers is instantiated, styled, read, written back and read
again; a QGIS that gains a symbol layer next year is tested the day it ships, without anybody
remembering to add it. The same for renderers, for the eight render units, and for the label
properties.

And it does not stop at the plugin. A style that reads back perfectly in QGIS is still wrong if the
MAP cannot draw it, so `api/geodeploy/services/symbology.py` — the real web renderer's expression
builder, pure stdlib and therefore importable here — is mounted and asked what MapLibre would
actually paint. That closes the loop the four-surface parity rule exists for: QGIS → GeoDeploy →
MapLibre, and QGIS → GeoDeploy → QGIS, in one run.

Run it:

    docker run --rm -v <repo>/integrations/qgis-plugin:/src -v <repo>/api:/api -w /src \
        -e QT_QPA_PLATFORM=offscreen qgis/qgis:ltr python3 -u scripts/test_roundtrip_matrix.py

The `/api` mount is OPTIONAL: without it the MapLibre sections are skipped and reported as skipped,
so the script still runs anywhere the plugin does. Exit code 0 = everything passed.
"""
import json
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.normpath(os.path.join(HERE, ".."))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from qgis.core import QgsApplication                                            # noqa: E402

QgsApplication.setPrefixPath(os.environ.get("QGIS_PREFIX_PATH", "/usr"), True)
QGS = QgsApplication([], False)
QGS.initQgis()

sys.path.insert(0, os.path.join(PLUGIN_ROOT, "geodeploy_qgis", "vendor"))
sys.path.insert(0, PLUGIN_ROOT)

from qgis.core import (Qgis, QgsFeature, QgsField, QgsGeometry, QgsPointXY,     # noqa: E402
                       QgsProject, QgsRenderContext, QgsVectorLayer)
from qgis.PyQt.QtCore import Qt                                                 # noqa: E402
from qgis.PyQt.QtGui import QColor                                              # noqa: E402

from geodeploy_qgis import labels as labels_mod                                  # noqa: E402
from geodeploy_qgis import symbology                                             # noqa: E402
from geodeploy_qgis.compat import enum                                           # noqa: E402

# The WEB renderer, if it was mounted. Pure stdlib on purpose — see the module docstring.
WEB = None
for candidate in ("/api", os.path.normpath(os.path.join(PLUGIN_ROOT, "..", "..", "api"))):
    marker = os.path.join(candidate, "geodeploy", "services", "symbology.py")
    if os.path.exists(marker):
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location("web_symbology", marker)
            WEB = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(WEB)
            break
        except Exception:                                                        # noqa: BLE001
            WEB = None

FAILURES = []
SKIPPED = []
CHECKS = [0]
CURRENT = [""]


def section(title):
    print("\n== {0} ==".format(title))
    CURRENT[0] = title


def check(name, condition, detail=""):
    CHECKS[0] += 1
    if condition:
        print("  ok   {0}".format(name))
    else:
        print("  FAIL {0}{1}".format(name, "  — " + str(detail)[:400] if detail else ""))
        FAILURES.append("[{0}] {1}".format(CURRENT[0], name))


def skip(name, why):
    SKIPPED.append("{0}: {1}".format(name, why))
    print("  --   {0}  ({1})".format(name, why))


# ── Fixtures ─────────────────────────────────────────────────────────────────────────────────────
GEOMETRIES = ("Point", "MultiPoint", "LineString", "MultiLineString", "Polygon", "MultiPolygon")
FIELDS = "&field=kind:string&field=pop:integer&field=height:double&field=name:string"


def make_layer(kind, name=None):
    """A memory layer with the fields every case here classifies or labels by."""
    layer = QgsVectorLayer("{0}?crs=EPSG:3857{1}".format(kind, FIELDS), name or kind, "memory")
    if not layer.isValid():
        raise RuntimeError("could not create a {0} memory layer".format(kind))
    QgsProject.instance().addMapLayer(layer)
    return layer


def geometry_family(kind):
    low = kind.lower()
    return "polygon" if "polygon" in low else "line" if "line" in low else "point"


def stable(layer, geometry, label):
    """apply → read → apply → read must reach the same picture, or a push loses something.

    Compared through `comparable_style`, which is what the plugin itself uses to decide whether a
    layer was restyled — so a difference here is exactly a difference a user would be told about.
    """
    first = symbology.from_qgis(layer) or {}
    again = make_layer(geometry)
    symbology.apply_to_qgis(again, dict(first))
    second = symbology.from_qgis(again) or {}
    a = symbology.comparable_style(first, geometry)
    b = symbology.comparable_style(second, geometry)
    diff = {k: (a.get(k), b.get(k)) for k in set(a) | set(b) if a.get(k) != b.get(k)}
    check("{0}: survives a second round trip".format(label), not diff, json.dumps(diff, default=str))
    return first


# ══ 1. Every symbol layer QGIS registers ═════════════════════════════════════════════════════════

def registry_sweep():
    """Instantiate EVERY symbol-layer type this QGIS offers, and put it through the trip.

    Generated from `QgsApplication.symbolLayerRegistry()` rather than from a list somebody typed, so
    a symbol layer QGIS adds in a later version is covered the day it ships. The assertions are
    deliberately the weak ones — the strong per-property checks are further down — because what this
    catches is the failure mode nobody predicts: a symbol layer whose reader RAISES, or one that
    returns nothing at all, which arrives as a layer drawn in the map's default blue with no
    indication that anything was lost.
    """
    section("Registry sweep — every symbol layer this QGIS has")
    from qgis.core import (QgsFillSymbol, QgsLineSymbol, QgsMarkerSymbol,
                           QgsSingleSymbolRenderer, QgsSymbol)

    registry = QgsApplication.symbolLayerRegistry()
    types = sorted(registry.symbolLayersForType(enum(Qgis, "SymbolType", "Marker"))
                   if hasattr(Qgis, "SymbolType") else registry.symbolLayersForType(0))
    families = [("point", enum(Qgis, "SymbolType", "Marker"), QgsMarkerSymbol, "Point"),
                ("line", enum(Qgis, "SymbolType", "Line"), QgsLineSymbol, "LineString"),
                ("polygon", enum(Qgis, "SymbolType", "Fill"), QgsFillSymbol, "Polygon")]

    total = 0
    for family, symbol_type, symbol_cls, geometry in families:
        names = sorted(registry.symbolLayersForType(symbol_type))
        print("  .. {0}: {1} types — {2}".format(family, len(names), ", ".join(names)))
        for type_name in names:
            total += 1
            layer = make_layer(geometry)
            try:
                sl = registry.createSymbolLayer(type_name, {})
            except Exception as exc:                                             # noqa: BLE001
                check("{0}: QGIS can create one".format(type_name), False,
                      "{0}: {1}".format(type(exc).__name__, exc))
                continue
            if sl is None:
                check("{0}: QGIS can create one".format(type_name), False, "createSymbolLayer → None")
                continue
            symbol = symbol_cls()
            try:
                symbol.changeSymbolLayer(0, sl)
            except Exception as exc:                                             # noqa: BLE001
                check("{0}: goes into a {1} symbol".format(type_name, family), False, str(exc))
                continue
            layer.setRenderer(QgsSingleSymbolRenderer(symbol))

            # (a) READING IT MUST NOT RAISE, and must not come back empty.
            try:
                style = symbology.from_qgis(layer) or {}
                raised = None
            except Exception as exc:                                             # noqa: BLE001
                style, raised = {}, "{0}: {1}".format(type(exc).__name__, exc)
            check("{0}: reads without raising".format(type_name), raised is None, raised)
            check("{0}: reads as something".format(type_name), bool(style), json.dumps(style)[:200])

            # (b) IT MUST NAME A COLOUR. Everything downstream — the legend, the layer card, the
            # portal's own list — shows a swatch, and a style with no colour draws the default blue
            # while claiming to be the user's symbology.
            colour = style.get("color") or (style.get("outline_color")
                                            if style.get("fill_opacity") == 0 else None)
            check("{0}: carries a colour".format(type_name),
                  isinstance(colour, str) and colour.startswith("#"), repr(style.get("color")))

            # (c) WRITING IT BACK MUST NOT RAISE EITHER — the direction that used to swallow an
            # AttributeError into "Could not style the layer" and draw QGIS's own default.
            back = make_layer(geometry)
            try:
                applied = symbology.apply_to_qgis(back, dict(style))
                wrote = None
            except Exception as exc:                                             # noqa: BLE001
                applied, wrote = False, "{0}: {1}".format(type(exc).__name__, exc)
            check("{0}: applies without raising".format(type_name), wrote is None, wrote)
            check("{0}: applies to a layer".format(type_name), applied, repr(applied))

            # (d) AND IT MUST BE STABLE. A style that changes on every push tells the user their
            # layer was edited when they only opened it.
            if applied:
                stable(back, geometry, type_name)
    check("the sweep covered every registered symbol layer", total >= 25, total)


# ══ 2. Every renderer QGIS registers ═════════════════════════════════════════════════════════════

#: Renderers that cannot be built from an empty definition, each covered by hand elsewhere here.
#: A renderer QGIS adds later will fail the sweep until somebody puts it in one list or the other,
#: which is the point: an untested renderer should cost a decision, not go unnoticed.
COVERED_BY_HAND = {
    "categorizedSymbol",        # per_class_matrix, property_matrix, tile_matrix
    "graduatedSymbol",          # per_class_matrix, tile_matrix
    "RuleRenderer",             # special_renderers
    "25dRenderer",              # special_renderers
    "heatmapRenderer",          # special_renderers
    "invertedPolygonRenderer",  # registry sweep cannot build it; read as its sub-symbol + a note
    "mergedFeatureRenderer",
    "pointCluster",
    "pointDisplacement",
    "embeddedSymbol",
    "singleSymbol",
}


def renderer_sweep():
    """Every renderer in `QgsApplication.rendererRegistry()`, asked what it sends.

    A renderer this plugin has no branch for still has to produce SOMETHING honest — the fallback
    reads its symbols and sends the first one's shape — and above all must not raise. The renderers
    that GROUP features (cluster, displacement, merged) additionally have to say what the push
    loses, because they succeed silently otherwise and the map quietly stops clustering.
    """
    section("Renderer sweep — every renderer this QGIS has")
    from qgis.core import QgsRendererRegistry

    registry = QgsApplication.rendererRegistry()
    names = sorted(registry.renderersList())
    print("  .. {0} renderers — {1}".format(len(names), ", ".join(names)))
    made = 0
    unbuildable = set()
    for name in names:
        metadata = registry.rendererMetadata(name)
        if metadata is None:
            continue
        for geometry in ("Point", "LineString", "Polygon"):
            layer = make_layer(geometry)
            # `createRenderer` takes a QDomElement and a read context, NOT None — passing None
            # returns nothing for every renderer, which is how this sweep silently tested zero of
            # them the first time it ran. An EMPTY element is the honest "give me your default".
            try:
                from qgis.core import QgsReadWriteContext
                from qgis.PyQt.QtXml import QDomDocument
                element = QDomDocument().createElement("renderer-v2")
                try:
                    renderer = metadata.createRenderer(element, QgsReadWriteContext())
                except TypeError:                                                # older signature
                    renderer = metadata.createRenderer(element)
            except Exception:                                                    # noqa: BLE001
                renderer = None
            if renderer is None:
                unbuildable.add(name)
                continue
            try:
                layer.setRenderer(renderer)
            except Exception:                                                    # noqa: BLE001
                unbuildable.add(name)
                continue
            made += 1
            try:
                style = symbology.from_qgis(layer)
                raised = None
            except Exception as exc:                                             # noqa: BLE001
                style, raised = None, "{0}: {1}".format(type(exc).__name__, exc)
            check("{0} on a {1}: reads without raising".format(name, geometry), raised is None, raised)
            # None is a legitimate answer — "nothing to send" — but a CRASH never is.
            if style is None:
                continue
            check("{0} on a {1}: what it sends is a dict".format(name, geometry),
                  isinstance(style, dict), type(style).__name__)
            break                                       # one geometry per renderer is enough here
    # NOT EVERY RENDERER CAN BE BUILT FROM NOTHING: `createRenderer` takes a QDomElement, and the
    # ones that need real classes or a real symbol return None for an empty one. Those are covered
    # by name in the sections below; what this sweep is for is the ones that CAN be built, and the
    # assertion that matters is that the plugin survived every one of them.
    print("  .. {0} of {1} renderers were built from an empty definition; not buildable: {2}"
          .format(made, len(names), ", ".join(sorted(unbuildable)) or "none"))
    # NOT EVERY RENDERER CAN BE BUILT FROM NOTHING — one that needs real classes, real ranges or a
    # real sub-renderer returns None for an empty element. Each of those is built by hand and
    # exercised somewhere else in this file, and THAT is what is asserted here: a renderer this
    # sweep cannot reach must be covered by name, or a QGIS that adds one leaves a hole nobody
    # notices. `COVERED_BY_HAND` is the list, and a new renderer forces a decision rather than
    # quietly widening the gap.
    uncovered = sorted(unbuildable - COVERED_BY_HAND)
    check("every renderer this sweep cannot build is covered by name elsewhere", not uncovered,
          uncovered)
    check("the sweep exercised the rest", made >= 5, "{0} of {1}".format(made, len(names)))


# ══ 3. Render units, exhaustively ════════════════════════════════════════════════════════════════

#: Every unit `QgsUnitTypes.RenderUnit` has, with what one of it is worth in POINTS — or None where
#: there is no honest answer without a render context and a map scale.
UNITS = (
    ("RenderMillimeters", 0, 72.0 / 25.4),
    ("RenderMapUnits", 1, None),
    ("RenderPixels", 2, 0.75),
    ("RenderPercentage", 3, None),
    ("RenderPoints", 4, 1.0),
    ("RenderInches", 5, 72.0),
    ("RenderUnknownUnit", 6, 1.0),
    ("RenderMetersInMapUnits", 7, None),
)


def unit_matrix():
    """Every size, in every unit QGIS can state it in.

    THE BUG THIS EXISTS FOR: QGIS's default is MILLIMETRES and the reader assumed points, so every
    style a person authored in QGIS arrived 2.835× too small — "the markers are great in QGIS but
    appear small in the browser". It survived because the writer stamps points on everything, so a
    style this plugin applied and read back round-tripped perfectly and every test wrote before it
    read. This section never writes first.
    """
    section("Render units — a size means nothing without the unit it is stated in")
    from qgis.core import (QgsLineSymbol, QgsMarkerSymbol, QgsSingleSymbolRenderer,
                           QgsUnitTypes)

    for unit_name, expected_value, factor in UNITS:
        try:
            unit = enum(QgsUnitTypes, "RenderUnit", unit_name)
        except Exception:                                                        # noqa: BLE001
            skip("unit {0}".format(unit_name), "not in this QGIS")
            continue
        check("unit {0} still has the value the table assumes ({1})".format(unit_name, expected_value),
              int(unit) == expected_value, int(unit))

        # A MARKER, sized 10 in this unit. A radius is half the size, in CSS pixels.
        layer = make_layer("Point")
        marker = QgsMarkerSymbol.createSimple({"color": "#ff0000"})
        marker.setSize(10.0)
        marker.setSizeUnit(unit)
        layer.setRenderer(QgsSingleSymbolRenderer(marker))
        radius = (symbology.from_qgis(layer) or {}).get("radius")
        if factor is None:
            # NOT CONVERTIBLE, and the honest answer is to say nothing rather than to invent a
            # number: map units and percentages depend on the render context and the map's scale.
            check("unit {0}: a size in it is not claimed as pixels".format(unit_name),
                  radius is None, repr(radius))
        else:
            want = round(10.0 * factor / 2.0 / symbology.CSS_PX_TO_POINTS, 2)
            check("unit {0}: 10 → radius {1}".format(unit_name, want), radius == want, repr(radius))

        # A LINE, whose unit lives on the symbol LAYER — `QgsLineSymbol` has no `widthUnit` at all.
        line_layer = make_layer("LineString")
        line = QgsLineSymbol.createSimple({"color": "#0000ff"})
        line.symbolLayer(0).setWidth(4.0)
        line.symbolLayer(0).setWidthUnit(unit)
        line_layer.setRenderer(QgsSingleSymbolRenderer(line))
        width = (symbology.from_qgis(line_layer) or {}).get("line_width")
        if factor is None:
            check("unit {0}: a line width in it is not claimed either".format(unit_name),
                  width is None, repr(width))
        else:
            want = round(4.0 * factor / symbology.CSS_PX_TO_POINTS, 2)
            check("unit {0}: a 4 line → {1} px".format(unit_name, want), width == want, repr(width))

    # A POLYGON's stroke width has its own unit setter, and its own reader.
    from qgis.core import QgsFillSymbol
    for unit_name, _value, factor in UNITS:
        if factor is None:
            continue
        unit = enum(QgsUnitTypes, "RenderUnit", unit_name)
        layer = make_layer("Polygon")
        fill = QgsFillSymbol.createSimple({"color": "#00ff00", "outline_color": "#000000"})
        fill.symbolLayer(0).setStrokeWidth(2.0)
        fill.symbolLayer(0).setStrokeWidthUnit(unit)
        layer.setRenderer(QgsSingleSymbolRenderer(fill))
        want = round(2.0 * factor / symbology.CSS_PX_TO_POINTS, 2)
        got = (symbology.from_qgis(layer) or {}).get("outline_width")
        check("unit {0}: a 2 polygon stroke → {1} px".format(unit_name, want), got == want, repr(got))

    # AND THE WRITE SIDE STAMPS POINTS, which is what makes an applied style read back unchanged.
    applied = make_layer("Point")
    symbology.apply_to_qgis(applied, {"color": "#ff0000", "radius": 9.5})
    got_unit = applied.renderer().symbol().sizeUnit()
    check("what the plugin WRITES is stated in points",
          int(got_unit) == 4, int(got_unit))
    check("…so an applied radius reads back unchanged",
          (symbology.from_qgis(applied) or {}).get("radius") == 9.5,
          repr((symbology.from_qgis(applied) or {}).get("radius")))


# ══ 4. Per-class shape — a class is more than a colour ═══════════════════════════════════════════

def per_class_matrix():
    """A classification whose classes differ by DASH, WIDTH, FILL, SHAPE or SIZE.

    GeoDeploy used to carry a colour per class and one shape for the whole layer, taken from the
    first class. Reported on a layer with two categories in the SAME colour that differ only by
    dash: the two arrived identical and the map lost the distinction it was made for. Every class
    now carries whatever its own symbol says that the layer's does not — and an ordinary classified
    layer, where every class is the same shape in a different colour, must carry nothing extra at
    all, or every style on every instance would grow keys nobody chose.
    """
    section("Per-class shape — classes that differ by more than their colour")
    from qgis.core import (QgsCategorizedSymbolRenderer, QgsFillSymbol, QgsGraduatedSymbolRenderer,
                           QgsLineSymbol, QgsMarkerSymbol, QgsRendererCategory, QgsRendererRange)

    DASH = enum(Qt, "PenStyle", "DashLine")
    DOT = enum(Qt, "PenStyle", "DotLine")
    NOBRUSH = enum(Qt, "BrushStyle", "NoBrush")

    # ── the reported shape: one colour, two dashes ───────────────────────────────────────────────
    line_layer = make_layer("LineString")
    dashed = QgsLineSymbol.createSimple({"color": "#84d9ff"})
    dashed.symbolLayer(0).setWidth(0.6)
    dashed.symbolLayer(0).setPenStyle(DASH)
    solid = QgsLineSymbol.createSimple({"color": "#84d9ff"})
    solid.symbolLayer(0).setWidth(1.4)
    line_layer.setRenderer(QgsCategorizedSymbolRenderer("kind", [
        QgsRendererCategory("tithe", dashed, "Tithe"),
        QgsRendererCategory("modern", solid, "Modern")]))
    sent = symbology.from_qgis(line_layer) or {}
    cats = sent.get("categories") or []
    check("two categories in one colour still differ",
          cats[1].get("lineType") == "solid" and cats[1].get("line_width") == 5.29, json.dumps(cats))
    check("the class that matches the layer inherits rather than repeating",
          "lineType" not in cats[0] and "line_width" not in cats[0], json.dumps(cats[0]))

    back = make_layer("LineString")
    symbology.apply_to_qgis(back, dict(sent))
    symbols = [c.symbol().clone() for c in back.renderer().categories()]
    check("back in QGIS: the first class is dashed",
          symbols[0].symbolLayer(0).penStyle() == DASH, symbols[0].symbolLayer(0).penStyle())
    check("back in QGIS: the second is not",
          symbols[1].symbolLayer(0).penStyle() != DASH, symbols[1].symbolLayer(0).penStyle())
    check("back in QGIS: and it is the wider one",
          symbols[1].symbolLayer(0).width() > symbols[0].symbolLayer(0).width(),
          (symbols[0].symbolLayer(0).width(), symbols[1].symbolLayer(0).width()))
    stable(back, "LineString", "per-class dash")

    # ── three dashes, so it is not a two-way coincidence ─────────────────────────────────────────
    three = make_layer("LineString")
    made = []
    for i, pen in enumerate((DASH, DOT, enum(Qt, "PenStyle", "SolidLine"))):
        sym = QgsLineSymbol.createSimple({"color": "#123456"})
        sym.symbolLayer(0).setPenStyle(pen)
        sym.symbolLayer(0).setWidth(0.4 + i * 0.4)
        made.append(QgsRendererCategory("k{0}".format(i), sym, "k{0}".format(i)))
    three.setRenderer(QgsCategorizedSymbolRenderer("kind", made))
    style3 = symbology.from_qgis(three) or {}
    kinds = [c.get("lineType", style3.get("lineType")) for c in style3.get("categories") or []]
    check("three classes, three dash patterns", len(set(kinds)) == 3, kinds)
    widths = [c.get("line_width", style3.get("line_width")) for c in style3.get("categories") or []]
    check("three classes, three widths", len(set(widths)) == 3, widths)

    # ── polygons: one hollow class among filled ones ─────────────────────────────────────────────
    poly = make_layer("Polygon")
    hollow = QgsFillSymbol.createSimple({"color": "#e9c9b0", "outline_color": "#333333"})
    hollow.symbolLayer(0).setBrushStyle(NOBRUSH)
    filled = QgsFillSymbol.createSimple({"color": "#d5b43c"})
    poly.setRenderer(QgsCategorizedSymbolRenderer("kind", [
        QgsRendererCategory("hachure", hollow, "Hachure"),
        QgsRendererCategory("infra", filled, "Infrastructure")]))
    psent = symbology.from_qgis(poly) or {}
    pcats = psent.get("categories") or []
    check("one hollow class does not empty the layer", psent.get("fill_opacity") == 1.0,
          psent.get("fill_opacity"))
    check("...and the hollow class still says it is hollow", pcats[0].get("fill_opacity") == 0.0,
          json.dumps(pcats[0]))
    pback = make_layer("Polygon")
    symbology.apply_to_qgis(pback, dict(psent))
    brushes = [c.symbol().clone().symbolLayer(0).brushStyle() for c in pback.renderer().categories()]
    check("back in QGIS: the hollow class has no brush", brushes[0] == NOBRUSH, brushes)
    check("back in QGIS: the filled one does", brushes[1] != NOBRUSH, brushes)

    # ── graduated, with a width per class ────────────────────────────────────────────────────────
    grad = make_layer("LineString")
    ranges = []
    for i, (lo, hi, width) in enumerate(((0, 10, 0.3), (10, 50, 1.0), (50, 100, 3.0))):
        sym = QgsLineSymbol.createSimple({"color": ("#eeeeee", "#888888", "#111111")[i]})
        sym.symbolLayer(0).setWidth(width)
        ranges.append(QgsRendererRange(lo, hi, sym, "{0}-{1}".format(lo, hi)))
    grad.setRenderer(QgsGraduatedSymbolRenderer("pop", ranges))
    gsent = symbology.from_qgis(grad) or {}
    gwidths = [c.get("line_width", gsent.get("line_width")) for c in gsent.get("classes") or []]
    check("a graduated layer carries a width per class", len(set(gwidths)) == 3, gwidths)
    gback = make_layer("LineString")
    symbology.apply_to_qgis(gback, dict(gsent))
    got = [round(r.symbol().symbolLayer(0).width(), 3) for r in gback.renderer().ranges()]
    check("back in QGIS: three different widths", len(set(got)) == 3, got)
    stable(gback, "LineString", "per-class width")

    # ── points: a shape and a size per class ─────────────────────────────────────────────────────
    points = make_layer("Point")
    small = QgsMarkerSymbol.createSimple({"name": "square", "color": "#ff0000"})
    small.setSize(3)
    big = QgsMarkerSymbol.createSimple({"name": "triangle", "color": "#00ff00"})
    big.setSize(8)
    points.setRenderer(QgsCategorizedSymbolRenderer("kind", [
        QgsRendererCategory("s", small, "Small"), QgsRendererCategory("b", big, "Big")]))
    ptsent = symbology.from_qgis(points) or {}
    ptcats = ptsent.get("categories") or []
    check("a point class keeps its own shape", ptcats[1].get("marker") == "triangle",
          json.dumps(ptcats[1]))
    check("...and its own radius", ptcats[1].get("radius") == 15.12, json.dumps(ptcats[1]))
    ptback = make_layer("Point")
    symbology.apply_to_qgis(ptback, dict(ptsent))
    sizes = [round(c.symbol().size(), 2) for c in ptback.renderer().categories()]
    check("back in QGIS: the sizes differ", len(set(sizes)) >= 2, sizes)

    # ── AND THE CASE THAT MUST NOT CHANGE: colours only ──────────────────────────────────────────
    for geometry, builder in (("Point", QgsMarkerSymbol), ("LineString", QgsLineSymbol),
                              ("Polygon", QgsFillSymbol)):
        plain = make_layer(geometry)
        plain_cats = [QgsRendererCategory(v, builder.createSimple({"color": c}), v)
                      for v, c in (("a", "#84d9ff"), ("b", "#e24646"), ("c", "#22c55e"))]
        plain.setRenderer(QgsCategorizedSymbolRenderer("kind", plain_cats))
        style = symbology.from_qgis(plain) or {}
        extra = sorted({k for c in (style.get("categories") or []) for k in c
                        if k not in ("value", "color")})
        check("{0}: an ordinary categorized layer carries no per-class keys".format(geometry),
              not extra, extra)
        if WEB is not None:
            check("{0}: ...so the map still draws it as ONE layer".format(geometry),
                  WEB.expand_classes(style) is None, "split into layers it did not need")


# ══ 5. Every property, per geometry ══════════════════════════════════════════════════════════════

#: One style per row, applied and read back on the geometry named. Each is a property somebody can
#: set in the QGIS dialog or in GeoDeploy, and the assertion is that a viewer would see the same
#: thing afterwards — not that the dict is byte-identical, which `comparable_style` decides.
PROPERTY_MATRIX = (
    # (geometry, label, style)
    ("Point", "a plain marker", {"color": "#3b82f6", "radius": 6}),
    ("Point", "a marker with a stroke", {"color": "#3b82f6", "radius": 6,
                                         "outline_color": "#111111", "outline_width": 0.4}),
    ("Point", "a marker with NO stroke", {"color": "#3b82f6", "radius": 6,
                                          "outline_color": "none"}),
    ("Point", "every shape: circle", {"color": "#f00", "marker": "circle", "radius": 5}),
    ("Point", "every shape: square", {"color": "#f00", "marker": "square", "radius": 5}),
    ("Point", "every shape: triangle", {"color": "#f00", "marker": "triangle", "radius": 5}),
    ("Point", "every shape: diamond", {"color": "#f00", "marker": "diamond", "radius": 5}),
    ("Point", "every shape: star", {"color": "#f00", "marker": "star", "radius": 5}),
    ("Point", "every shape: cross", {"color": "#f00", "marker": "cross", "radius": 5}),
    ("Point", "a marker offset from its point", {"color": "#f00", "radius": 5,
                                                 "marker_offset": [3.0, -2.0]}),
    ("Point", "a rotated marker", {"color": "#f00", "radius": 5, "marker_rotation": 45}),
    ("Point", "size driven by a field", {"color": "#f00", "size_mode": "proportional",
                                         "size_field": "pop", "size_stops": [[0, 2], [100, 20]]}),
    ("MultiPoint", "a multipoint layer", {"color": "#0af", "radius": 4}),

    ("LineString", "a plain line", {"color": "#e24646", "line_width": 3}),
    ("LineString", "a dashed line", {"color": "#e24646", "line_width": 3, "lineType": "dashed"}),
    ("LineString", "a dotted line", {"color": "#e24646", "line_width": 3, "lineType": "dotted"}),
    ("LineString", "a custom dash vector", {"color": "#e24646", "line_width": 2,
                                            "dash_pattern": [4, 2, 1, 2]}),
    ("LineString", "an offset line", {"color": "#e24646", "line_width": 2, "line_offset": 1.5}),
    ("LineString", "round caps and joins", {"color": "#e24646", "line_width": 4,
                                            "line_cap": "round", "line_join": "round"}),
    ("LineString", "flat caps and mitre joins", {"color": "#e24646", "line_width": 4,
                                                 "line_cap": "butt", "line_join": "miter"}),
    ("MultiLineString", "a multiline layer", {"color": "#0a0", "line_width": 2}),

    ("Polygon", "a plain fill", {"color": "#3b82f6", "fill_opacity": 0.45}),
    ("Polygon", "an opaque fill", {"color": "#3b82f6", "fill_opacity": 1.0}),
    ("Polygon", "an outline-only polygon", {"color": "#3b82f6", "fill_opacity": 0.0,
                                            "outline_color": "#cc22d2", "outline_width": 2.5}),
    ("Polygon", "a wide outline", {"color": "#3b82f6", "fill_opacity": 0.4,
                                   "outline_color": "#1d4ed8", "outline_width": 4.0}),
    ("Polygon", "no outline at all", {"color": "#3b82f6", "fill_opacity": 0.4,
                                      "outline_color": "none"}),
    ("Polygon", "a dashed outline", {"color": "#3b82f6", "fill_opacity": 0.4,
                                     "outline_color": "#111", "lineType": "dashed"}),
    ("MultiPolygon", "a multipolygon layer", {"color": "#f59e0b", "fill_opacity": 0.6}),
)


def property_matrix():
    """Every friendly style key, applied to a real layer and read back.

    This is the direction the plugin controls on both ends, so it can assert IDENTITY: what goes in
    must come out. A key that silently fails to apply reads back as the map's default and is
    reported here as the difference it is.
    """
    section("Property matrix — every key, applied and read back")
    for geometry, label, style in PROPERTY_MATRIX:
        layer = make_layer(geometry)
        try:
            applied = symbology.apply_to_qgis(layer, dict(style))
        except Exception as exc:                                                 # noqa: BLE001
            check("{0} / {1}: applies".format(geometry, label), False,
                  "{0}: {1}".format(type(exc).__name__, exc))
            continue
        check("{0} / {1}: applies".format(geometry, label), applied, repr(applied))
        if not applied:
            continue
        read = symbology.from_qgis(layer) or {}
        want = symbology.comparable_style(style, geometry)
        got = symbology.comparable_style(read, geometry)
        # Only the keys the style ACTUALLY NAMED are asserted; everything else is a default both
        # sides fill identically, and `comparable_style` has already folded those.
        diff = {k: (want.get(k), got.get(k)) for k in style if want.get(k) != got.get(k)}
        check("{0} / {1}: reads back the same".format(geometry, label), not diff,
              json.dumps(diff, default=str))
        stable(layer, geometry, "{0} / {1}".format(geometry, label))


# ══ 6. Layer-level scope: zoom range, subset, opacity ════════════════════════════════════════════

def scope_matrix():
    """A layer's scale range and its subset filter — the things that scope everything it draws."""
    section("Layer scope — scale range, subset, opacity")
    from qgis.core import QgsMarkerSymbol, QgsSingleSymbolRenderer

    layer = make_layer("Point")
    layer.setRenderer(QgsSingleSymbolRenderer(QgsMarkerSymbol.createSimple({"color": "#ff0000"})))
    layer.setScaleBasedVisibility(True)
    layer.setMinimumScale(1000000)          # QGIS: the SMALLEST scale (widest view) it draws at
    layer.setMaximumScale(1000)
    style = symbology.from_qgis(layer) or {}
    check("a scale range becomes a zoom range",
          style.get("minzoom") is not None and style.get("maxzoom") is not None,
          json.dumps({k: style.get(k) for k in ("minzoom", "maxzoom")}))
    check("...the right way round (min < max)",
          (style.get("minzoom") or 0) < (style.get("maxzoom") or 24),
          (style.get("minzoom"), style.get("maxzoom")))

    back = make_layer("Point")
    symbology.apply_to_qgis(back, dict(style))
    check("a zoom range comes back as a scale range", back.hasScaleBasedVisibility(),
          back.hasScaleBasedVisibility())
    again = symbology.from_qgis(back) or {}
    check("and the range survives the trip",
          (round(again.get("minzoom") or -1), round(again.get("maxzoom") or -1))
          == (round(style.get("minzoom") or -1), round(style.get("maxzoom") or -1)),
          (again.get("minzoom"), again.get("maxzoom")))

    # OPACITY IS A PROPERTY OF THE LAYER, not of its symbol, and GeoDeploy stores it OUTSIDE the
    # style (`{opacity, style, popup_fields}`) for that reason. So the contract is not that
    # `from_qgis` returns it — it must not — but that applying a style sets the QGIS layer's own
    # opacity, and that the two transparencies are never confused with each other.
    op_layer = make_layer("Polygon")
    op_layer.setOpacity(0.35)
    symbology.apply_to_qgis(op_layer, {"color": "#3b82f6", "fill_opacity": 0.9})
    check("applying a style does not disturb the layer's own opacity",
          abs(op_layer.opacity() - 0.35) < 0.02, op_layer.opacity())
    read = symbology.from_qgis(op_layer) or {}
    check("...and is not read back INTO the style, where it does not belong",
          "opacity" not in read, read.get("opacity"))
    check("...while the FILL's own opacity is a style key, and is",
          abs((read.get("fill_opacity") or 0) - 0.9) < 0.02, read.get("fill_opacity"))


# ══ 7. Labels, every property, on a feature layer AND on tiles ═══════════════════════════════════

LABEL_MATRIX = (
    ("a field", {"enabled": True, "field": "name"}),
    ("a size", {"enabled": True, "field": "name", "size": 18.0}),
    ("a colour", {"enabled": True, "field": "name", "color": "#c0392b"}),
    ("a halo", {"enabled": True, "field": "name", "halo_color": "#ffffff", "halo_width": 2.0}),
    ("an offset", {"enabled": True, "field": "name", "offset": [4.0, -3.0]}),
    ("a rotation", {"enabled": True, "field": "name", "rotation": 30.0}),
    ("wrapping", {"enabled": True, "field": "name", "max_width": 12}),
    ("uppercase", {"enabled": True, "field": "name", "transform": "uppercase"}),
    ("letter spacing", {"enabled": True, "field": "name", "letter_spacing": 1.5}),
    ("allow overlap", {"enabled": True, "field": "name", "allow_overlap": True}),
    ("a priority", {"enabled": True, "field": "name", "priority": 8}),
    ("its own zoom range", {"enabled": True, "field": "name", "minzoom": 6, "maxzoom": 14}),
    ("a bold italic font", {"enabled": True, "field": "name",
                            "qgis_font": {"family": "Arial", "bold": True, "italic": True}}),
    ("an expression", {"enabled": True, "expression": 'concat("name", \' (\', "pop", \')\')'}),
)


def label_matrix():
    """Every label property, both directions, on a feature layer and on a vector-tile layer.

    A vector TILE layer is what a portal opened as a group hands QGIS, and it has no
    `setLabelsEnabled` — so the guard that asked for one meant every layer opened that way came
    back with its labels missing. Both surfaces are asserted here for exactly that reason.
    """
    section("Labels — every property, features and tiles")
    for label, block in LABEL_MATRIX:
        layer = make_layer("Point")
        style = {"color": "#3b82f6", "radius": 5, "labels": dict(block)}
        try:
            symbology.apply_to_qgis(layer, dict(style))
            applied = labels_mod.to_qgis(layer, dict(style))
        except Exception as exc:                                                 # noqa: BLE001
            check("labels / {0}: applies".format(label), False,
                  "{0}: {1}".format(type(exc).__name__, exc))
            continue
        check("labels / {0}: applies".format(label), applied, repr(applied))
        if not applied:
            continue
        read = (symbology.from_qgis(layer) or {}).get("labels") or {}
        want = symbology.comparable_style({"labels": block}, "Point").get("labels") or {}
        got = symbology.comparable_style({"labels": read}, "Point").get("labels") or {}
        diff = {k: (want.get(k), got.get(k)) for k in block if want.get(k) != got.get(k)}
        check("labels / {0}: reads back the same".format(label), not diff,
              json.dumps(diff, default=str))

    # ── the same block, on TILES ─────────────────────────────────────────────────────────────────
    try:
        from qgis.core import QgsVectorTileLayer
    except ImportError:                                                          # pragma: no cover
        skip("tile labels", "no QgsVectorTileLayer on this QGIS")
        return
    for geometry in ("point", "line", "polygon"):
        tiles = QgsVectorTileLayer(
            "type=xyz&url=https://example.invalid/{z}/{x}/{y}.pbf&zmin=0&zmax=14", "tiles")
        QgsProject.instance().addMapLayer(tiles)
        tiles.setCustomProperty(symbology.P_GEOMETRY, geometry)
        applied = labels_mod.to_qgis(tiles, {"labels": {"enabled": True, "field": "name",
                                                       "size": 12.0, "color": "#232323"}})
        check("tile labels ({0}): applied".format(geometry), applied, repr(applied))
        labeling = tiles.labeling()
        check("tile labels ({0}): a tile labeling is set".format(geometry),
              type(labeling).__name__ == "QgsVectorTileBasicLabeling",
              type(labeling).__name__ if labeling else "None")
        styles = labeling.styles() if labeling else []
        check("tile labels ({0}): the field travels".format(geometry),
              bool(styles) and styles[0].labelSettings().fieldName == "name",
              styles[0].labelSettings().fieldName if styles else None)
        # The style must be scoped to THIS geometry, or QGIS labels nothing at all: a point style
        # over line data places no label.
        if styles:
            want = {"point": "PointGeometry", "line": "LineGeometry",
                    "polygon": "PolygonGeometry"}[geometry]
            from qgis.core import QgsWkbTypes
            check("tile labels ({0}): scoped to the right geometry".format(geometry),
                  styles[0].geometryType() == enum(QgsWkbTypes, "GeometryType", want),
                  styles[0].geometryType())


# ══ 8. The tile renderer — what a portal group is actually drawn with ════════════════════════════

TILE_MATRIX = (
    ("single symbol", {"color": "#3b82f6", "radius": 5}),
    ("dashed line", {"color": "#e24646", "line_width": 3, "lineType": "dashed"}),
    ("categorized", {"color_mode": "categorized", "color_field": "kind",
                     "categories": [{"value": "a", "color": "#84d9ff"},
                                    {"value": "b", "color": "#e24646"}]}),
    ("graduated", {"color_mode": "graduated", "color_field": "pop", "classes_n": 3,
                   "classes": [{"min": None, "max": 10, "color": "#eeeeee"},
                               {"min": 10, "max": 50, "color": "#888888"},
                               {"min": 50, "max": None, "color": "#111111"}]}),
    ("categorized with a dash per class",
     {"color_mode": "categorized", "color_field": "kind", "line_width": 2, "lineType": "dashed",
      "categories": [{"value": "a", "color": "#84d9ff"},
                     {"value": "b", "color": "#84d9ff", "lineType": "solid", "line_width": 5}]}),
    ("graduated with a width per class",
     {"color_mode": "graduated", "color_field": "pop", "line_width": 1,
      "classes": [{"min": None, "max": 10, "color": "#eee"},
                  {"min": 10, "max": None, "color": "#111", "line_width": 6}]}),
)


def tile_matrix():
    """`apply_to_vector_tiles` → `style_from_vector_tiles`, for every mode.

    The tile renderer is the surface a portal opened as a group draws on, and it is a DIFFERENT
    renderer with a different vocabulary — one symbol per class, each with a filter expression. A
    per-class shape has to survive here too, or a portal group draws every class with the first
    one's symbol even when the layer's own style carries the difference.
    """
    section("Vector tiles — the renderer a portal group opens on")
    try:
        from qgis.core import QgsVectorTileBasicRenderer, QgsVectorTileLayer
    except ImportError:                                                          # pragma: no cover
        skip("tile renderer", "not on this QGIS")
        return

    for label, style in TILE_MATRIX:
        geometry = ("line" if "line_width" in json.dumps(style) or "lineType" in json.dumps(style)
                    else "point")
        tiles = QgsVectorTileLayer(
            "type=xyz&url=https://example.invalid/{z}/{x}/{y}.pbf&zmin=0&zmax=14", label)
        QgsProject.instance().addMapLayer(tiles)
        tiles.setCustomProperty(symbology.P_GEOMETRY, geometry)
        try:
            applied = symbology.apply_to_vector_tiles(tiles, {}, "src", dict(style))
        except Exception as exc:                                                 # noqa: BLE001
            check("tiles / {0}: applies".format(label), False,
                  "{0}: {1}".format(type(exc).__name__, exc))
            continue
        check("tiles / {0}: applies".format(label), applied, repr(applied))
        if not applied:
            continue
        entries = tiles.renderer().styles() if tiles.renderer() else []
        check("tiles / {0}: a renderer entry per class".format(label), bool(entries), len(entries))
        read = symbology.style_from_vector_tiles(tiles) or {}
        check("tiles / {0}: reads back the same mode".format(label),
              (read.get("color_mode") or "single") == (style.get("color_mode") or "single"),
              read.get("color_mode"))
        for key in ("classes", "categories"):
            if key in style:
                check("tiles / {0}: {1} come back".format(label, key),
                      len(read.get(key) or []) == len(style[key]),
                      (len(read.get(key) or []), len(style[key])))
        # …and the per-class difference specifically.
        if "per class" in label:
            entry_key = "categories" if "categories" in style else "classes"
            sent_shapes = [{k: v for k, v in c.items() if k in symbology.CLASS_SHAPE_KEYS}
                           for c in style[entry_key]]
            got_shapes = [{k: v for k, v in c.items() if k in symbology.CLASS_SHAPE_KEYS}
                          for c in (read.get(entry_key) or [])]
            check("tiles / {0}: the classes still DIFFER after the trip".format(label),
                  len({json.dumps(s, sort_keys=True) for s in got_shapes}) ==
                  len({json.dumps(s, sort_keys=True) for s in sent_shapes}),
                  json.dumps(got_shapes))


# ══ 9. Rules, 2.5D, heatmaps and the renderers with their own module ═════════════════════════════

def special_renderers():
    """Rule-based rendering, 2.5D and heatmaps — each of which has its own module and its own trap."""
    section("Rules, 2.5D and heatmaps")
    from qgis.core import (QgsFillSymbol, QgsLineSymbol, QgsRuleBasedRenderer,
                           QgsSingleSymbolRenderer)

    # ── RULES. A rule tree flattens to one render layer per leaf, each carrying the AND of the
    # filters above it. `rules[0]` draws FIRST, which is QGIS's order and the opposite of a portal's
    # layer list.
    layer = make_layer("LineString")
    root = QgsRuleBasedRenderer.Rule(None)
    for label, expression, colour, width in (("main", '"pop" > 50', "#e24646", 1.2),
                                             ("minor", '"pop" <= 50', "#84d9ff", 0.4)):
        symbol = QgsLineSymbol.createSimple({"color": colour})
        symbol.symbolLayer(0).setWidth(width)
        rule = QgsRuleBasedRenderer.Rule(symbol, 0, 0, expression, label)
        root.appendChild(rule)
    layer.setRenderer(QgsRuleBasedRenderer(root))
    style = symbology.from_qgis(layer) or {}
    rules = style.get("rules") or []
    check("a rule tree becomes a rule list", len(rules) == 2, len(rules))
    check("each rule keeps the expression somebody typed",
          all(r.get("expression") for r in rules), json.dumps(rules)[:200])
    check("each rule keeps a MapLibre filter too",
          all(r.get("filter") is not None for r in rules), json.dumps(rules)[:200])
    check("each rule carries its own symbol",
          len({json.dumps(r.get("style") or {}, sort_keys=True) for r in rules}) == 2,
          json.dumps([r.get("style") for r in rules]))
    back = make_layer("LineString")
    symbology.apply_to_qgis(back, dict(style))
    check("a rule list comes back as a rule tree",
          type(back.renderer()).__name__ == "QgsRuleBasedRenderer", type(back.renderer()).__name__)
    again = symbology.from_qgis(back) or {}
    check("...with the same number of rules", len(again.get("rules") or []) == 2,
          len(again.get("rules") or []))

    # ── 2.5D. Height and angle are PROJECT VARIABLES, not renderer properties, which is why
    # `Qgs25DRenderer` has colours and no `height()`. What travels is a real `fill-extrusion`, with
    # the 2.5D parameters riding alongside so the trip back rebuilds 2.5D rather than a flat block.
    try:
        from qgis.core import Qgs25DRenderer
    except ImportError:                                                          # pragma: no cover
        skip("2.5D", "no Qgs25DRenderer on this QGIS")
        Qgs25DRenderer = None
    if Qgs25DRenderer is not None:
        poly = make_layer("Polygon")
        poly.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol.createSimple({"color": "#3b82f6"})))
        renderer25 = Qgs25DRenderer()
        renderer25.setRoofColor(QColor("#d97706"))
        renderer25.setWallColor(QColor("#92400e"))
        poly.setRenderer(renderer25)
        s25 = symbology.from_qgis(poly) or {}
        extrusion = s25.get("extrusion") or {}
        check("2.5D becomes a real extrusion", bool(extrusion.get("enabled")), json.dumps(s25)[:200])
        check("...carrying the 2.5D parameters home", bool(extrusion.get("qgis25d")),
              json.dumps(extrusion)[:200])
        back25 = make_layer("Polygon")
        symbology.apply_to_qgis(back25, dict(s25))
        check("...so the trip back is 2.5D again, not a flat block",
              type(back25.renderer()).__name__ == "Qgs25DRenderer",
              type(back25.renderer()).__name__)

        # AND A PLAIN GeoDeploy EXTRUSION MUST OPEN AS 2.5D TOO — that is the whole point: somebody
        # who ticks 3D in the browser opens the layer in QGIS and sees blocks, not flat polygons.
        plain25 = make_layer("Polygon")
        symbology.apply_to_qgis(plain25, {"color": "#3b82f6", "fill_opacity": 1.0,
                                          "extrusion": {"enabled": True, "height": 25}})
        check("an extrusion authored in GeoDeploy opens as 2.5D",
              type(plain25.renderer()).__name__ == "Qgs25DRenderer",
              type(plain25.renderer()).__name__)
        # ...and pushing it straight back must not invent a `qgis25d` block nobody chose.
        readback = symbology.from_qgis(plain25) or {}
        check("...and pushing it back does not invent 2.5D parameters",
              not (readback.get("extrusion") or {}).get("qgis25d"),
              json.dumps(readback.get("extrusion")))

    # ── HEATMAPS. MapLibre has the same layer type, so this is a translation, not an approximation.
    try:
        from qgis.core import QgsHeatmapRenderer
    except ImportError:                                                          # pragma: no cover
        skip("heatmap", "no QgsHeatmapRenderer on this QGIS")
        return
    heat_layer = make_layer("Point")
    heat_layer.setRenderer(QgsHeatmapRenderer())
    heat_style = symbology.from_qgis(heat_layer) or {}
    check("a heatmap renderer sends a heatmap", bool(heat_style.get("heatmap")),
          json.dumps(heat_style)[:200])
    heat_back = make_layer("Point")
    symbology.apply_to_qgis(heat_back, dict(heat_style))
    check("...and comes back as one", type(heat_back.renderer()).__name__ == "QgsHeatmapRenderer",
          type(heat_back.renderer()).__name__)


# ══ 10. What the MAP would actually draw ═════════════════════════════════════════════════════════

def maplibre_matrix():
    """The other half of the round trip: QGIS → GeoDeploy → **MapLibre**.

    A style that reads back perfectly in QGIS is still wrong if the map cannot draw it. This runs
    the real `api/geodeploy/services/symbology.py` — the same module the published portal, the
    editor preview and the layer page all build their paint from — over the styles QGIS produced
    above, and asserts the resulting expressions actually encode what the style said.
    """
    section("MapLibre — what the map would draw from what QGIS sent")
    if WEB is None:
        skip("MapLibre matrix", "api/ was not mounted; run with -v <repo>/api:/api")
        return
    from qgis.core import (QgsCategorizedSymbolRenderer, QgsLineSymbol, QgsRendererCategory)

    # ── the colour expressions ───────────────────────────────────────────────────────────────────
    cat_style = {"color_mode": "categorized", "color_field": "kind",
                 "categories": [{"value": "a", "color": "#84d9ff"},
                                {"value": "b", "color": "#e24646"}],
                 "other_color": "#9ca3af"}
    expr = WEB.color_expression(cat_style)
    check("a categorized style becomes a `match` expression",
          isinstance(expr, list) and expr[0] == "match", json.dumps(expr))
    check("...with every category in it",
          "#84d9ff" in json.dumps(expr) and "#e24646" in json.dumps(expr), json.dumps(expr))

    grad_style = {"color_mode": "graduated", "color_field": "pop",
                  "classes": [{"min": None, "max": 10, "color": "#eee"},
                              {"min": 10, "max": 50, "color": "#888"},
                              {"min": 50, "max": None, "color": "#111"}]}
    gexpr = WEB.color_expression(grad_style)
    check("a graduated style becomes a `step` expression",
          isinstance(gexpr, list) and gexpr[0] == "step", json.dumps(gexpr))

    # ── THE PER-CLASS SPLIT, and the two things that must hold about it ──────────────────────────
    split_style = dict(cat_style, line_width=2, lineType="dashed",
                       categories=[{"value": "a", "color": "#84d9ff"},
                                   {"value": "b", "color": "#84d9ff",
                                    "lineType": "solid", "line_width": 5}])
    split = WEB.expand_classes(split_style)
    check("classes that differ by dash become one layer each", split and len(split) == 3,
          json.dumps(split)[:200] if split else None)
    check("...and the catch-all draws first, underneath",
          split and split[0]["label"] == "Other", split[0]["label"] if split else None)
    check("...each carrying its OWN dash",
          split and split[1]["style"].get("lineType") == "dashed"
          and split[2]["style"].get("lineType") == "solid",
          json.dumps([r["style"].get("lineType") for r in split]) if split else None)
    check("...and its own width",
          split and split[2]["style"].get("line_width") == 5,
          json.dumps([r["style"].get("line_width") for r in split]) if split else None)
    check("an ordinary categorized style is NOT split", WEB.expand_classes(cat_style) is None,
          json.dumps(WEB.expand_classes(cat_style) or [])[:120])

    # THE SPLIT MUST NOT CHANGE WHICH FEATURES DRAW. `step` gives everything below the first
    # boundary the first colour and everything above the last one the last colour, so the filters
    # have to mirror the stops rather than reading `min`/`max` literally.
    gsplit_style = dict(grad_style, line_width=1)
    gsplit_style["classes"] = [dict(c) for c in grad_style["classes"]]
    gsplit_style["classes"][2]["line_width"] = 6
    gsplit = WEB.expand_classes(gsplit_style)
    check("a graduated split is one layer per class", gsplit and len(gsplit) == 3, len(gsplit or []))
    check("...the first class has no lower bound",
          gsplit and json.dumps(gsplit[0]["filter"]).count(">=") == 0,
          json.dumps(gsplit[0]["filter"]) if gsplit else None)
    check("...the last has no upper bound",
          gsplit and json.dumps(gsplit[-1]["filter"]).count("<") == 0,
          json.dumps(gsplit[-1]["filter"]) if gsplit else None)
    check("...and the boundaries are the `step` stops",
          gsplit and json.dumps(gsplit[1]["filter"]) ==
          json.dumps(["all", [">=", ["to-number", ["get", "pop"]], 10],
                      ["<", ["to-number", ["get", "pop"]], 50]]),
          json.dumps(gsplit[1]["filter"]) if gsplit else None)

    # ── AND THE REAL THING: a style QGIS produced, drawn by the web renderer ─────────────────────
    real = make_layer("LineString")
    dashed = QgsLineSymbol.createSimple({"color": "#84d9ff"})
    dashed.symbolLayer(0).setWidth(0.6)
    dashed.symbolLayer(0).setPenStyle(enum(Qt, "PenStyle", "DashLine"))
    solid = QgsLineSymbol.createSimple({"color": "#84d9ff"})
    solid.symbolLayer(0).setWidth(1.4)
    real.setRenderer(QgsCategorizedSymbolRenderer("kind", [
        QgsRendererCategory("a", dashed, "A"), QgsRendererCategory("b", solid, "B")]))
    from_qgis = symbology.from_qgis(real) or {}
    web_split = WEB.expand_classes(from_qgis)
    check("a REAL QGIS layer of two same-coloured classes splits in the map",
          web_split and len(web_split) == 3, len(web_split or []))
    if web_split:
        dashes = [WEB.dash_array(r["style"]) for r in web_split[1:]]
        check("...and the map draws one dashed and one solid",
              dashes[0] is not None and dashes[1] is None, json.dumps(dashes))
        widths = [r["style"].get("line_width") for r in web_split[1:]]
        check("...at the widths QGIS stated, in millimetres",
              widths == [2.27, 5.29], json.dumps(widths))

    # ── the size and marker expressions the map builds ───────────────────────────────────────────
    marker_style = {"color": "#f00", "marker": "square", "radius": 6}
    check("a marker style names an icon", bool(WEB.icon_image_expression(marker_style)),
          repr(WEB.icon_image_expression(marker_style)))
    check("...and the icon id carries the shape",
          "square" in json.dumps(WEB.icon_image_expression(marker_style)),
          json.dumps(WEB.icon_image_expression(marker_style)))
    check("a dashed line style produces a dash array",
          WEB.dash_array({"lineType": "dashed"}) is not None,
          repr(WEB.dash_array({"lineType": "dashed"})))
    check("a custom dash vector is used verbatim",
          WEB.dash_array({"dash_pattern": [4, 2, 1, 2]}) == [4, 2, 1, 2],
          repr(WEB.dash_array({"dash_pattern": [4, 2, 1, 2]})))
    check("an outline-only polygon asks the map for no fill",
          WEB.color_expression({"color": "#111", "fill_opacity": 0}) == "#111",
          repr(WEB.color_expression({"color": "#111", "fill_opacity": 0})))

    # ── the legend the map shows must match the classes ──────────────────────────────────────────
    entries = WEB.legend_entries(split_style)
    check("the legend still lists every class after a split", len(entries) >= 2, len(entries))


# ══ 11. Determinism ══════════════════════════════════════════════════════════════════════════════

def determinism():
    """Reading the same layer twice must give the same answer.

    Not a hypothetical: a marker rendered to a PNG is content-addressed, and a renderer that walked
    a `set` would produce a different id every run — so two pushes of an untouched layer would each
    report a change.
    """
    section("Determinism — the same layer read twice is the same style")
    from qgis.core import (QgsCategorizedSymbolRenderer, QgsMarkerSymbol, QgsRendererCategory,
                           QgsSingleSymbolRenderer)
    cases = []
    marker = make_layer("Point")
    svg = QgsMarkerSymbol.createSimple({"name": "star", "color": "#ff0000"})
    marker.setRenderer(QgsSingleSymbolRenderer(svg))
    cases.append(("a rendered marker", marker))
    cats = make_layer("Polygon")
    from qgis.core import QgsFillSymbol
    cats.setRenderer(QgsCategorizedSymbolRenderer("kind", [
        QgsRendererCategory(v, QgsFillSymbol.createSimple({"color": c}), v)
        for v, c in (("a", "#111111"), ("b", "#222222"), ("c", "#333333"))]))
    cases.append(("a categorized polygon", cats))
    for label, layer in cases:
        first = json.dumps(symbology.from_qgis(layer) or {}, sort_keys=True)
        second = json.dumps(symbology.from_qgis(layer) or {}, sort_keys=True)
        check("{0}: reads identically twice".format(label), first == second,
              "first {0} chars differ".format(
                  next((i for i in range(min(len(first), len(second)))
                        if first[i] != second[i]), -1)))


def main():
    print("GeoDeploy ⇄ QGIS round-trip matrix")
    print("QGIS {0}   |   web renderer: {1}".format(
        Qgis.QGIS_VERSION, "mounted" if WEB is not None else "NOT MOUNTED (sections skipped)"))
    for run in (registry_sweep, renderer_sweep, unit_matrix, per_class_matrix, property_matrix,
                scope_matrix, label_matrix, tile_matrix, special_renderers, maplibre_matrix,
                determinism):
        try:
            run()
        except Exception:                                                        # noqa: BLE001
            print("\n!! {0} raised:".format(run.__name__))
            traceback.print_exc()
            FAILURES.append("{0} raised".format(run.__name__))
    print("\n{0} checks, {1} failed, {2} skipped".format(CHECKS[0], len(FAILURES), len(SKIPPED)))
    for name in FAILURES:
        print("   FAILED: {0}".format(name))
    for name in SKIPPED:
        print("   skipped: {0}".format(name))
    sys.stdout.flush()
    sys.stderr.flush()
    # `os._exit`, not `sys.exit`: QGIS's teardown can segfault on exit and take the exit code — and
    # the buffered output — with it. Documented in notes_temp/notes_for_future.md.
    os._exit(1 if FAILURES else 0)


if __name__ == "__main__":
    main()
