"""The symbology round trip, run against a REAL PyQGIS — no stubs.

WHY THIS EXISTS. `test_tile_symbology.py` and its siblings replace the QGIS classes with recording
stubs, because QGIS is not importable on a normal CI runner. Those stubs share one base class that
hands every symbol layer a `setWidth`/`width` pair — so the fill branch passed a green test against
a method `QgsSimpleFillSymbolLayer` has never had, and every polygon layer arrived in QGIS unstyled
while CI reported the round trip working. A stub cannot be trusted to describe an API this wide.

So this file asserts against the real thing:

  * **the API audit** — every QGIS name the plugin calls exists, on this QGIS, with that spelling.
    That is what catches an enum QGIS moved into the `Qgis` namespace between 3.28 and 4.x, which
    otherwise presents as "the plugin silently does nothing".
  * **the round trip** — a real `QgsVectorLayer` per geometry, styled by `symbology.apply`, read
    back by `symbology.from_qgis`, and compared with `comparable_style`. Anything the writer sets
    and the reader cannot see is a style that would be lost on the user's next push.
  * **the tile path** — `apply_to_vector_tiles` builds REAL symbols (only the layer container is a
    stand-in, since a tile layer needs a served archive), then `style_from_vector_tiles` reads them
    back through the real renderer classes.

Run it:

    docker run --rm -v <plugin-dir>:/src -w /src \
        -e QT_QPA_PLATFORM=offscreen qgis/qgis:ltr \
        python3 scripts/test_real_qgis.py

`qgis/qgis:ltr` is 3.44; `qgis/qgis:4.2` is the Qt6 build. Both must pass, and CI runs both.
Exit code 0 = every check passed; 1 = at least one failed, each printed with what it expected.
"""
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.join(HERE, "..", "geodeploy_qgis")

# ── Real QGIS, headless ──────────────────────────────────────────────────────────────────────────
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from qgis.core import (QgsApplication, QgsFeature, QgsField, QgsGeometry, QgsPointXY,  # noqa: E402
                       QgsVectorLayer)

QgsApplication.setPrefixPath(os.environ.get("QGIS_PREFIX_PATH", "/usr"), True)
QGS = QgsApplication([], False)
QGS.initQgis()

# ── The module under test, loaded the way the stub harness loads it ──────────────────────────────
sys.path.insert(0, os.path.join(PLUGIN, "vendor"))
sys.path.insert(0, PLUGIN)
_src = open(os.path.join(PLUGIN, "symbology.py"), encoding="utf-8").read().splitlines()
_src = "\n".join(line for line in _src if not line.startswith("from .connection"))
symbology = type(sys)("symbology")
symbology.__dict__["__name__"] = "symbology"
exec(compile(_src, "symbology.py", "exec"), symbology.__dict__)  # noqa: S102
# `rules.py` imports this module by name; register it so it gets THIS copy, not a second one.
sys.modules["symbology"] = symbology

FAILURES = []
CHECKS = [0]


def check(name, condition, detail=""):
    CHECKS[0] += 1
    if condition:
        print("  ok   {0}".format(name))
    else:
        print("  FAIL {0}{1}".format(name, "  — " + detail if detail else ""))
        FAILURES.append(name)


def section(title):
    print("\n== {0} ==".format(title))


# ══ 1. API audit ═════════════════════════════════════════════════════════════════════════════════
# Every QGIS attribute the plugin reaches for, named here so a rename in any QGIS version fails
# LOUDLY on the version that renamed it rather than silently degrading on a user's machine.

def audit():
    section("API audit — the names the plugin calls exist on this QGIS")
    from qgis.core import (QgsSimpleFillSymbolLayer, QgsSimpleLineSymbolLayer,
                           QgsSimpleMarkerSymbolLayer, QgsSymbol, QgsUnitTypes, QgsWkbTypes)
    from qgis.PyQt.QtCore import Qt

    print("QGIS {0}".format(__import__("qgis.core", fromlist=["Qgis"]).Qgis.QGIS_VERSION))

    # The methods each symbol layer really has. This is the assertion the stubs could not make.
    for cls, present, absent in (
            (QgsSimpleFillSymbolLayer,
             ("setStrokeWidth", "strokeWidth", "setStrokeColor", "strokeColor",
              "setStrokeStyle", "strokeStyle", "setBrushStyle", "setStrokeWidthUnit"),
             ("setWidth", "width")),
            (QgsSimpleLineSymbolLayer,
             ("setWidth", "width", "setPenStyle", "penStyle", "setWidthUnit",
              "setPenCapStyle", "setPenJoinStyle", "setCustomDashVector", "setOffset"),
             ()),
            (QgsSimpleMarkerSymbolLayer,
             ("setShape", "shape", "setStrokeWidth", "strokeWidth", "setStrokeColor",
              "setStrokeStyle", "setSize", "setSizeUnit", "setAngle", "setOffset"),
             ()),
    ):
        for name in present:
            check("{0}.{1} exists".format(cls.__name__, name), hasattr(cls, name))
        for name in absent:
            # Not pedantry: the plugin calling one of these is the bug this file was written for.
            check("{0}.{1} does NOT exist".format(cls.__name__, name), not hasattr(cls, name),
                  "the plugin must not call it")

    # Enums, through the plugin's own resolver — the failure mode is a silent None on QGIS 4.
    enum = symbology.enum
    for owner, scope, name, label in (
            (QgsWkbTypes, "GeometryType", "PolygonGeometry", "QgsWkbTypes.PolygonGeometry"),
            (QgsWkbTypes, "GeometryType", "LineGeometry", "QgsWkbTypes.LineGeometry"),
            (QgsWkbTypes, "GeometryType", "PointGeometry", "QgsWkbTypes.PointGeometry"),
            (QgsUnitTypes, "RenderUnit", "RenderPoints", "QgsUnitTypes.RenderPoints"),
            (Qt, "PenStyle", "DashLine", "Qt.DashLine"),
            (Qt, "PenStyle", "DotLine", "Qt.DotLine"),
            (Qt, "PenStyle", "NoPen", "Qt.NoPen"),
    ):
        try:
            enum(owner, scope, name)
            check("enum {0}".format(label), True)
        except Exception as exc:            # noqa: BLE001
            check("enum {0}".format(label), False, "{0}: {1}".format(type(exc).__name__, exc))

    # Data-defined size rides on this one, and it moved to `Qgis.Property` in 3.36.
    try:
        from qgis.core import QgsSymbolLayer
        enum(QgsSymbolLayer, "Property", "PropertyStrokeWidth")
        check("enum QgsSymbolLayer.PropertyStrokeWidth", True)
    except Exception as exc:                # noqa: BLE001
        check("enum QgsSymbolLayer.PropertyStrokeWidth", False,
              "{0}: {1} — size-from-a-field will not travel".format(type(exc).__name__, exc))

    check("QgsSymbol.defaultSymbol(polygon) works",
          QgsSymbol.defaultSymbol(enum(QgsWkbTypes, "GeometryType", "PolygonGeometry")) is not None)


# ══ 2. Real feature layers ═══════════════════════════════════════════════════════════════════════

def make_layer(geometry, name="test"):
    """A real in-memory QgsVectorLayer with a numeric and a text field, and three features."""
    uri = "{0}?crs=EPSG:4326&field=pop:double&field=kind:string".format(geometry)
    layer = QgsVectorLayer(uri, name, "memory")
    assert layer.isValid(), "could not create a {0} memory layer".format(geometry)
    geoms = {
        "Point": ["POINT(0 0)", "POINT(1 1)", "POINT(2 2)"],
        "LineString": ["LINESTRING(0 0, 1 1)", "LINESTRING(1 1, 2 2)", "LINESTRING(2 2, 3 3)"],
        "Polygon": ["POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))",
                    "POLYGON((2 2, 3 2, 3 3, 2 3, 2 2))",
                    "POLYGON((4 4, 5 4, 5 5, 4 5, 4 4))"],
    }[geometry]
    provider = layer.dataProvider()
    for i, wkt in enumerate(geoms):
        f = QgsFeature(layer.fields())
        f.setGeometry(QgsGeometry.fromWkt(wkt))
        f.setAttributes([float(i * 100), ["a", "b", "c"][i]])
        provider.addFeature(f)
    layer.updateExtents()
    return layer


#: One style per geometry, using every key the vocabulary has for it. These are the values a user
#: would actually set, and every one of them must survive apply → read → compare.
STYLES = {
    "Point": {
        "single": {"color": "#ff0000", "radius": 8, "marker": "square",
                   "outline_color": "#000000", "outline_width": 0.25},
        "graduated": {"color_mode": "graduated", "color_field": "pop", "radius": 6,
                      "marker": "triangle",
                      "classes": [{"min": 0, "max": 100, "color": "#fee0d2"},
                                  {"min": 100, "max": 200, "color": "#fc9272"},
                                  {"min": 200, "max": None, "color": "#de2d26"}]},
        "categorized": {"color_mode": "categorized", "color_field": "kind", "radius": 7,
                        "marker": "star", "other_color": "#9ca3af",
                        "categories": [{"value": "a", "color": "#3b82f6"},
                                       {"value": "b", "color": "#ef4444"}]},
    },
    "LineString": {
        "single": {"color": "#0000ff", "line_width": 3, "lineType": "dashed"},
        "graduated": {"color_mode": "graduated", "color_field": "pop", "line_width": 2.5,
                      "lineType": "dotted",
                      "classes": [{"min": 0, "max": 150, "color": "#c6dbef"},
                                  {"min": 150, "max": None, "color": "#08519c"}]},
        "categorized": {"color_mode": "categorized", "color_field": "kind", "line_width": 4,
                        "lineType": "dashed",
                        "categories": [{"value": "a", "color": "#22c55e"},
                                       {"value": "b", "color": "#f59e0b"}]},
    },
    "Polygon": {
        "single": {"color": "#00ff00", "fill_opacity": 0.6, "outline_color": "#1d4ed8",
                   "outline_width": 2},
        "graduated": {"color_mode": "graduated", "color_field": "pop", "fill_opacity": 0.5,
                      "outline_color": "#333333", "outline_width": 1.5,
                      "classes": [{"min": 0, "max": 100, "color": "#e5f5e0"},
                                  {"min": 100, "max": None, "color": "#31a354"}]},
        "categorized": {"color_mode": "categorized", "color_field": "kind", "fill_opacity": 0.7,
                        "outline_color": "none",
                        "categories": [{"value": "a", "color": "#a855f7"},
                                       {"value": "b", "color": "#06b6d4"}]},
    },
}

GEOM_NAME = {"Point": "point", "LineString": "line", "Polygon": "polygon"}


def round_trip():
    section("Round trip — real QgsVectorLayer, apply → from_qgis → compare")
    for geometry, cases in STYLES.items():
        for mode, style in cases.items():
            label = "{0}/{1}".format(GEOM_NAME[geometry], mode)
            layer = make_layer(geometry)
            try:
                applied = symbology.apply(layer, style)
            except Exception as exc:        # noqa: BLE001
                check("{0}: apply".format(label), False,
                      "{0}: {1}".format(type(exc).__name__, exc))
                traceback.print_exc()
                continue
            check("{0}: apply set a renderer".format(label), bool(applied))
            try:
                read = symbology.from_qgis(layer)
            except Exception as exc:        # noqa: BLE001
                check("{0}: from_qgis".format(label), False,
                      "{0}: {1}".format(type(exc).__name__, exc))
                traceback.print_exc()
                continue
            check("{0}: from_qgis returned a style".format(label), bool(read),
                  "empty — nothing would be sent back to the instance")
            if not read:
                continue

            want = symbology.comparable_style(style, GEOM_NAME[geometry])
            got = symbology.comparable_style(symbology.merge_style(style, read),
                                             GEOM_NAME[geometry])
            differing = sorted(k for k in set(want) | set(got) if want.get(k) != got.get(k))
            check("{0}: survives unchanged".format(label), not differing,
                  "changed: " + ", ".join("{0} {1!r}→{2!r}".format(k, want.get(k), got.get(k))
                                          for k in differing))


def key_by_key():
    """Which INDIVIDUAL keys are lost, so a failure above names the property rather than the case.

    A round trip that changes one number is reported by `comparable_style` as one difference; this
    says which of the writer's keys the reader never saw at all, which is the actionable form.
    """
    section("Per-key read-back — what from_qgis can actually see")
    for geometry, cases in STYLES.items():
        style = cases["single"]
        layer = make_layer(geometry)
        try:
            symbology.apply(layer, style)
            read = symbology.from_qgis(layer)
        except Exception as exc:            # noqa: BLE001
            check("{0}: single-symbol read-back".format(GEOM_NAME[geometry]), False,
                  "{0}: {1}".format(type(exc).__name__, exc))
            continue
        for key, value in sorted(style.items()):
            if key in ("color_mode",):
                continue
            got = read.get(key)
            same = got is not None and (
                abs(float(got) - float(value)) < 0.06 if isinstance(value, (int, float))
                and isinstance(got, (int, float)) else str(got).lower() == str(value).lower())
            check("{0}: {1} reads back".format(GEOM_NAME[geometry], key), same,
                  "wrote {0!r}, read {1!r}".format(value, got))


# ══ 3. The vector-tile path ══════════════════════════════════════════════════════════════════════

class FakeTileLayer(object):
    """A stand-in for `QgsVectorTileLayer` — the only stub here, and only for the CONTAINER.

    A real tile layer needs a served archive, which a unit test has no business standing up. The
    renderer, the styles and every symbol inside them are the real QGIS classes, which is where the
    bugs live.
    """

    def __init__(self, geometry):
        self._renderer = None
        self._props = {"geodeploy/geometry": geometry}

    def setRenderer(self, r):
        self._renderer = r

    def renderer(self):
        return self._renderer

    def triggerRepaint(self):
        pass

    def customProperty(self, key, default=None):
        return self._props.get(key, default)

    def setCustomProperty(self, key, value):
        self._props[key] = value

    def name(self):
        return "tiles"


def tiles():
    section("Vector tiles — apply_to_vector_tiles → style_from_vector_tiles")
    for geometry, cases in STYLES.items():
        name = GEOM_NAME[geometry]
        for mode, style in cases.items():
            label = "tiles {0}/{1}".format(name, mode)
            layer = FakeTileLayer(name)
            row = {"geometry_type": geometry}
            try:
                ok = symbology.apply_to_vector_tiles(layer, row, "geodeploy", style)
            except Exception as exc:        # noqa: BLE001
                check("{0}: apply".format(label), False,
                      "{0}: {1}".format(type(exc).__name__, exc))
                continue
            check("{0}: styled".format(label), bool(ok),
                  "returned False — QGIS draws it in its own default colour")
            if not ok:
                continue
            try:
                read = symbology.style_from_vector_tiles(layer)
            except Exception as exc:        # noqa: BLE001
                check("{0}: read back".format(label), False,
                      "{0}: {1} — this ESCAPES from_qgis (only ImportError is caught)"
                      .format(type(exc).__name__, exc))
                continue
            check("{0}: read back a style".format(label), bool(read))
            if not read:
                continue
            want = symbology.comparable_style(style, name)
            got = symbology.comparable_style(symbology.merge_style(style, read), name)
            differing = sorted(k for k in set(want) | set(got) if want.get(k) != got.get(k))
            check("{0}: survives unchanged".format(label), not differing,
                  "changed: " + ", ".join("{0} {1!r}→{2!r}".format(k, want.get(k), got.get(k))
                                          for k in differing))


# ══ 4. Geometry the row does not name ════════════════════════════════════════════════════════════

def unknown_geometry():
    """A tile layer whose row carries no `geometry_type` styles EVERY geometry — so one broken
    branch takes the whole layer down. GeoParquet layers are the ones that hit this."""
    section("Tile layer with no recorded geometry")
    layer = FakeTileLayer("")
    try:
        ok = symbology.apply_to_vector_tiles(layer, {"geometry_type": ""}, "geodeploy",
                                             {"color": "#123456", "line_width": 2})
        check("unknown geometry: styled", bool(ok),
              "returned False — every geometry's symbol must build, or none of them draw")
    except Exception as exc:                # noqa: BLE001
        check("unknown geometry: styled", False, "{0}: {1}".format(type(exc).__name__, exc))


# ══ 5. Symbology QGIS authored, not GeoDeploy ════════════════════════════════════════════════════
# Everything above styles a layer with GeoDeploy's own vocabulary and reads it back, which proves
# the loop closes. It does NOT prove the reader copes with symbology a person built in QGIS — and
# on a real project it did not: four layers uploaded nothing at all, three uploaded only a colour.
# These are those cases, reduced to the smallest renderer that reproduces each.

def authored_in_qgis():
    section("Symbology authored in QGIS — the shapes a real project actually holds")
    from qgis.core import (QgsCategorizedSymbolRenderer, QgsFillSymbol, QgsLineSymbol,
                           QgsMarkerSymbol, QgsRendererCategory, QgsRuleBasedRenderer,
                           QgsSvgMarkerSymbolLayer, QgsSymbol)

    # RULE-BASED. What most real QGIS projects use, and what `renderer.symbols(None)` could not read
    # on QGIS 4 — the call raises TypeError there, which `from_qgis` turned into `{}`. A rule-based
    # layer must come back with SOMETHING: flattened is a documented degradation, silence is not.
    layer = make_layer("LineString")
    root = QgsRuleBasedRenderer.Rule(None)
    for i, (expr, colour, width) in enumerate(
            (('"pop" < 100', "#ff0000", 1.5), ('"pop" >= 100', "#0000ff", 4.0))):
        sym = QgsLineSymbol.createSimple({"color": colour, "width": str(width)})
        rule = QgsRuleBasedRenderer.Rule(sym)
        rule.setFilterExpression(expr)
        rule.setLabel("rule {0}".format(i))
        root.appendChild(rule)
    layer.setRenderer(QgsRuleBasedRenderer(root))
    try:
        read = symbology.from_qgis(layer)
    except Exception as exc:            # noqa: BLE001
        check("rule-based: read", False, "{0}: {1}".format(type(exc).__name__, exc))
        read = {}
    check("rule-based: sends something", bool(read),
          "empty — the layer would upload with no styling at all")
    check("rule-based: keeps a colour", bool(read.get("color")), repr(read))
    check("rule-based: keeps the line width", read.get("line_width") is not None, repr(read))

    # CATEGORIZED, WITH A SHAPE. The classes travelled and the dash did not, which is the
    # "correct categories but no dashed lines" report.
    layer = make_layer("LineString")
    cats = []
    for value, colour in (("a", "#22c55e"), ("b", "#f59e0b")):
        sym = QgsLineSymbol.createSimple({"color": colour, "width": "1.0"})
        sym.symbolLayer(0).setPenStyle(symbology.enum(
            __import__("qgis.PyQt.QtCore", fromlist=["Qt"]).Qt, "PenStyle", "DashLine"))
        cats.append(QgsRendererCategory(value, sym, str(value)))
    layer.setRenderer(QgsCategorizedSymbolRenderer("kind", cats))
    read = symbology.from_qgis(layer)
    check("categorized line: keeps the categories", len(read.get("categories") or []) == 2)
    check("categorized line: keeps the DASH", read.get("lineType") == "dashed",
          "got {0!r} — the classes travelled and the dash did not".format(read.get("lineType")))
    check("categorized line: keeps the width", read.get("line_width") is not None, repr(read))
    check("categorized line: sends no top-level colour", "color" not in read,
          "class 0's colour must not be reported as the layer's own")

    # A CATEGORIZED FILL keeps its opacity and outline for the same reason.
    layer = make_layer("Polygon")
    cats = []
    for value, colour in (("a", "#a855f7"), ("b", "#06b6d4")):
        sym = QgsFillSymbol.createSimple({"color": colour, "outline_color": "#333333",
                                          "outline_width": "0.8"})
        sym.setOpacity(0.6)
        cats.append(QgsRendererCategory(value, sym, str(value)))
    layer.setRenderer(QgsCategorizedSymbolRenderer("kind", cats))
    read = symbology.from_qgis(layer)
    check("categorized fill: keeps fill_opacity", read.get("fill_opacity") is not None, repr(read))
    check("categorized fill: keeps the outline", read.get("outline_color") not in (None, ""),
          repr(read))

    # AN SVG MARKER TRAVELS AS ITS PICTURE. It used to arrive as a coloured dot: not one of the
    # three Simple classes, so the reader returned the colour and nothing else.
    import glob
    from qgis.core import QgsSingleSymbolRenderer
    svgs = sorted(glob.glob("/usr/share/qgis/svg/**/*.svg", recursive=True))
    layer = make_layer("Point")
    sym = QgsMarkerSymbol()
    sym.changeSymbolLayer(0, QgsSvgMarkerSymbolLayer(svgs[0]))
    sym.setSize(8.0)
    layer.setRenderer(QgsSingleSymbolRenderer(sym))
    read = symbology.from_qgis(layer)
    check("svg marker: keeps a radius", read.get("radius"), repr(read.get("radius")))
    check("svg marker: the PICTURE travels",
          str(read.get("marker_image") or "").startswith("data:image/png;base64,"),
          "an SVG marker must not arrive as a coloured dot")

    # And the graduated case, so the shape half is covered for both classified modes.
    layer = make_layer("Point")
    symbology.apply(layer, dict(STYLES["Point"]["graduated"], marker="square", radius=9))
    read = symbology.from_qgis(layer)
    check("graduated point: keeps the marker shape", read.get("marker") == "square", repr(read))
    check("graduated point: keeps the radius", read.get("radius") is not None, repr(read))
    check("graduated point: sends no top-level colour", "color" not in read, repr(read))

    assert QgsSymbol is not None        # imported for the module's own sanity, not used directly


# ══ 6. Rule-based rendering, out and back ════════════════════════════════════════════════════════

def rule_based():
    section("Rule-based — QGIS rules → style.rules → QGIS rules")
    from qgis.core import QgsLineSymbol, QgsRuleBasedRenderer

    def build(layer, specs, nest=False):
        """A rule renderer from `(expression, colour, width, label, min_scale, max_scale)` tuples."""
        root = QgsRuleBasedRenderer.Rule(None)
        parent = root
        if nest:
            parent = QgsRuleBasedRenderer.Rule(None)
            parent.setFilterExpression('"pop" >= 0')
            root.appendChild(parent)
        for expr, colour, width, label, lo, hi in specs:
            sym = QgsLineSymbol.createSimple({"color": colour, "width": str(width)})
            rule = QgsRuleBasedRenderer.Rule(sym)
            if expr is None:
                rule.setIsElse(True)
            else:
                rule.setFilterExpression(expr)
            rule.setLabel(label)
            if lo:
                rule.setMinimumScale(lo)
            if hi:
                rule.setMaximumScale(hi)
            parent.appendChild(rule)
        layer.setRenderer(QgsRuleBasedRenderer(root))
        return layer

    # A plain two-rule renderer.
    layer = build(make_layer("LineString"), [
        ("\"kind\" = 'a'", "#ff0000", 1.5, "A", 0, 0),
        ("\"kind\" = 'b'", "#0000ff", 3.0, "B", 0, 0)])
    style = symbology.from_qgis(layer)
    got = style.get("rules") or []
    check("rules: both are carried", len(got) == 2, "got {0}".format(len(got)))
    check("rules: the filter is translated",
          got and got[0].get("filter") == ["==", ["get", "kind"], "a"], repr(got[:1]))
    check("rules: the QGIS source rides along",
          got and got[0].get("expression") == "\"kind\" = 'a'", repr(got[:1]))
    check("rules: each keeps its own symbol",
          [r["style"].get("color") for r in got] == ["#ff0000", "#0000ff"],
          repr([r["style"].get("color") for r in got]))
    check("rules: each keeps its own width",
          [r["style"].get("line_width") for r in got] == [2.0, 4.0],
          repr([r["style"].get("line_width") for r in got]))
    check("rules: the label travels", got and got[0].get("label") == "A", repr(got[:1]))

    # …and back into QGIS, then read again. The SECOND read is the real test: it proves the
    # renderer we built is one this module can read, which is what a round trip means.
    back = make_layer("LineString")
    check("rules: applied back to QGIS", symbology.apply(back, style))
    check("rules: QGIS got a rule renderer",
          type(back.renderer()).__name__ == "QgsRuleBasedRenderer",
          type(back.renderer()).__name__)
    again = (symbology.from_qgis(back) or {}).get("rules") or []
    check("rules: survive a full round trip",
          [(r.get("expression"), r["style"].get("color")) for r in again]
          == [(r.get("expression"), r["style"].get("color")) for r in got],
          repr(again))

    # An ELSE rule becomes NOT(the others).
    layer = build(make_layer("LineString"), [
("\"kind\" = 'a'", "#ff0000", 1.0, "A", 0, 0),
        (None, "#888888", 1.0, "Everything else", 0, 0)])
    got = (symbology.from_qgis(layer) or {}).get("rules") or []
    check("rules: an ELSE is carried", len(got) == 2, "got {0}".format(len(got)))
    check("rules: an ELSE is the negation of its siblings",
          len(got) == 2 and got[1].get("filter") == ["!", ["==", ["get", "kind"], "a"]],
          repr(got[1:] and got[1].get("filter")))

    # A scale range becomes a zoom range, and the two ends swap.
    layer = build(make_layer("LineString"), [
        ("\"kind\" = 'a'", "#ff0000", 1.0, "A", 100000, 1000)])
    got = (symbology.from_qgis(layer) or {}).get("rules") or []
    lo = got[0].get("minzoom") if got else None
    hi = got[0].get("maxzoom") if got else None
    check("rules: a scale range becomes a zoom range",
          lo is not None and hi is not None and 12 < lo < 13 and 18 < hi < 20,
          "minzoom={0} maxzoom={1}".format(lo, hi))

    # A nested rule inherits its parent's filter.
    layer = build(make_layer("LineString"), [
        ("\"kind\" = 'a'", "#ff0000", 1.0, "A", 0, 0)], nest=True)
    got = (symbology.from_qgis(layer) or {}).get("rules") or []
    check("rules: a nested rule ANDs its parent's filter",
          got and got[0].get("filter") == ["all", [">=", ["get", "pop"], 0],
                                           ["==", ["get", "kind"], "a"]],
          repr(got[:1]))

    # A filter outside the subset is SKIPPED, not drawn unfiltered.
    layer = build(make_layer("LineString"), [
("\"kind\" = 'a'", "#ff0000", 1.0, "A", 0, 0),
        ("intersects($geometry, @atlas_geometry)", "#00ff00", 1.0, "Atlas", 0, 0)])
    got = (symbology.from_qgis(layer) or {}).get("rules") or []
    check("rules: an untranslatable filter is dropped, not widened",
          len(got) == 1 and got[0].get("label") == "A", repr([r.get("label") for r in got]))


# ══ 7. 2.5D ══════════════════════════════════════════════════════════════════════════════════════

def two_and_a_half_d():
    section("2.5D — QGIS's pseudo-3D block → extrusion → back")
    try:
        from qgis.core import Qgs25DRenderer
    except ImportError:
        check("2.5D: available in this QGIS", False, "Qgs25DRenderer is missing")
        return

    layer = make_layer("Polygon")
    layer.setRenderer(Qgs25DRenderer.convertFromRenderer(layer.renderer()))
    renderer = layer.renderer()
    from qgis.PyQt.QtGui import QColor
    renderer.setRoofColor(QColor("#cc8844"))
    renderer.setWallColor(QColor("#886644"))
    renderer.setShadowColor(QColor("#222222"))
    renderer.setShadowEnabled(True)
    renderer.setShadowSpread(6.0)

    # Height and angle are PROJECT variables, not renderer properties — that is the whole quirk.
    from qgis.core import QgsExpressionContextUtils, QgsProject
    QgsExpressionContextUtils.setProjectVariable(QgsProject.instance(), "qgis_25d_height", 25.0)
    QgsExpressionContextUtils.setProjectVariable(QgsProject.instance(), "qgis_25d_angle", 55.0)

    style = symbology.from_qgis(layer)
    check("2.5D: sends something", bool(style),
          "empty — the layer would upload with no styling at all")
    ex = (style or {}).get("extrusion") or {}
    check("2.5D: becomes an extrusion", bool(ex.get("enabled")), repr(style))
    check("2.5D: the height comes from the project variable", ex.get("height") == 25.0,
          "got {0!r}".format(ex.get("height")))
    check("2.5D: the roof colour becomes the extrusion colour", ex.get("color") == "#cc8844",
          "got {0!r}".format(ex.get("color")))
    check("2.5D: the flat style is the roof colour too", (style or {}).get("color") == "#cc8844",
          "a 2D reader must still see a sensible polygon")

    block = ex.get("qgis25d") or {}
    check("2.5D: the angle is carried", block.get("angle") == 55.0, repr(block))
    check("2.5D: the wall colour is carried", block.get("wall_color") == "#886644", repr(block))
    check("2.5D: the shadow is carried",
          block.get("shadow_color") == "#222222" and block.get("shadow_spread") == 6.0,
          repr(block))

    # …and back. The renderer must be 2.5D again, not a plain fill or a real 3D one.
    back = make_layer("Polygon")
    check("2.5D: applied back to QGIS", symbology.apply(back, style))
    check("2.5D: QGIS got a 2.5D renderer again",
          type(back.renderer()).__name__ == "Qgs25DRenderer",
          type(back.renderer()).__name__)
    again = symbology.from_qgis(back) or {}
    ex2 = again.get("extrusion") or {}
    check("2.5D: survives a full round trip",
          ex2.get("color") == ex.get("color")
          and ex2.get("height") == ex.get("height")
          and (ex2.get("qgis25d") or {}).get("angle") == block.get("angle"),
          repr(ex2))

    # A plain extrusion must NOT become 2.5D — it is a real 3D layer, not a pseudo-3D one.
    plain = make_layer("Polygon")
    symbology.apply(plain, {"color": "#3b82f6",
                            "extrusion": {"enabled": True, "height": 12}})
    check("2.5D: a plain extrusion stays a plain renderer",
          type(plain.renderer()).__name__ != "Qgs25DRenderer",
          type(plain.renderer()).__name__)


# ══ 8. The layer-level and line/marker vocabulary ════════════════════════════════════════════════

def natives():
    section("Natives — things MapLibre draws and GeoDeploy had no word for")
    from qgis.core import QgsLineSymbol, QgsMarkerSymbol, QgsSingleSymbolRenderer

    # ── A custom dash pattern, cap and join, and a line offset ────────────────────────────────────
    layer = make_layer("LineString")
    written = {"color": "#ff0000", "line_width": 4,
               "dash_pattern": [3, 2, 1, 2], "line_cap": "round", "line_join": "miter",
               "line_offset": 2.5}
    symbology.apply(layer, written)
    read = symbology.from_qgis(layer)
    check("dash pattern round-trips", read.get("dash_pattern") == [3.0, 2.0, 1.0, 2.0],
          "got {0!r}".format(read.get("dash_pattern")))
    check("a custom pattern clears the preset name", read.get("lineType") == "solid",
          "got {0!r} — a preset would contradict the pattern".format(read.get("lineType")))
    check("line cap round-trips", read.get("line_cap") == "round", repr(read.get("line_cap")))
    check("line join round-trips", read.get("line_join") == "miter", repr(read.get("line_join")))
    check("line offset round-trips, sign included", read.get("line_offset") == 2.5,
          "got {0!r} — MapLibre offsets left where QGIS offsets right".format(
              read.get("line_offset")))

    # The pattern is in LINE-WIDTH multiples, so changing the width must not change its shape.
    wide = make_layer("LineString")
    symbology.apply(wide, dict(written, line_width=10))
    check("a dash pattern is width-independent",
          (symbology.from_qgis(wide) or {}).get("dash_pattern") == [3.0, 2.0, 1.0, 2.0],
          repr((symbology.from_qgis(wide) or {}).get("dash_pattern")))

    # ── Marker rotation, offset and opacity ──────────────────────────────────────────────────────
    layer = make_layer("Point")
    written = {"color": "#00ff00", "radius": 8, "marker": "triangle",
               "marker_rotation": 45, "marker_offset": [3, -2], "marker_opacity": 0.5}
    symbology.apply(layer, written)
    read = symbology.from_qgis(layer)
    check("marker rotation round-trips", read.get("marker_rotation") == 45.0,
          repr(read.get("marker_rotation")))
    check("marker offset round-trips", read.get("marker_offset") == [3.0, -2.0],
          repr(read.get("marker_offset")))
    check("marker opacity round-trips", read.get("marker_opacity") == 0.5,
          repr(read.get("marker_opacity")))
    plain = make_layer("Point")
    symbology.apply(plain, {"color": "#00ff00", "radius": 6})
    check("a fully opaque marker says nothing about opacity",
          "marker_opacity" not in (symbology.from_qgis(plain) or {}),
          "1.0 on every marker would be noise in every style")

    # ── The layer's own scale range ──────────────────────────────────────────────────────────────
    layer = make_layer("LineString")
    layer.setScaleBasedVisibility(True)
    layer.setMinimumScale(100000.0)     # the most zoomed-OUT limit, the larger denominator
    layer.setMaximumScale(1000.0)
    read = symbology.from_qgis(layer)
    lo, hi = read.get("minzoom"), read.get("maxzoom")
    check("a layer's scale range becomes a zoom range",
          lo is not None and hi is not None and 12 < lo < 13 and 18 < hi < 20,
          "minzoom={0} maxzoom={1}".format(lo, hi))
    back = make_layer("LineString")
    symbology.apply(back, read)
    check("and goes back as a scale range",
          back.hasScaleBasedVisibility()
          and abs(back.minimumScale() - 100000.0) < 500
          and abs(back.maximumScale() - 1000.0) < 50,
          "min={0} max={1} on={2}".format(back.minimumScale(), back.maximumScale(),
                                          back.hasScaleBasedVisibility()))

    # ── The subset string ────────────────────────────────────────────────────────────────────────
    layer = make_layer("LineString")
    layer.setSubsetString('"pop" > 50')
    read = symbology.from_qgis(layer)
    check("a subset string becomes a filter",
          read.get("filter") == [">", ["get", "pop"], 50], repr(read.get("filter")))
    check("and the QGIS source rides along",
          read.get("filter_expression") == '"pop" > 50', repr(read.get("filter_expression")))

    layer = make_layer("LineString")
    layer.setSubsetString("intersects($geometry, @atlas_geometry)")
    read = symbology.from_qgis(layer) or {}
    check("an untranslatable subset sends NO filter rather than a wrong one",
          "filter" not in read,
          "a broken filter would publish every feature — a different map from the one on screen")

    # ── "No symbols" ─────────────────────────────────────────────────────────────────────────────
    try:
        from qgis.core import QgsNullSymbolRenderer
        layer = make_layer("Polygon")
        layer.setRenderer(QgsNullSymbolRenderer())
        read = symbology.from_qgis(layer) or {}
        check("the null renderer is carried as no_symbol", read.get("no_symbol") is True,
              repr(read))
    except ImportError:                 # pragma: no cover
        check("QgsNullSymbolRenderer available", False, "missing from this QGIS")

    assert QgsLineSymbol and QgsMarkerSymbol and QgsSingleSymbolRenderer  # imported for clarity


# ══ 9. Labels ════════════════════════════════════════════════════════════════════════════════════

def labelling():
    section("Labels — QgsPalLayerSettings → style.labels → back")
    from qgis.core import (QgsPalLayerSettings, QgsTextBufferSettings, QgsTextFormat,
                           QgsVectorLayerSimpleLabeling)
    from qgis.PyQt.QtGui import QColor, QFont

    layer = make_layer("Point")
    settings = QgsPalLayerSettings()
    settings.fieldName = "kind"
    fmt = QgsTextFormat()
    font = QFont("Noto Sans")
    font.setBold(True)
    fmt.setFont(font)
    fmt.setSize(15.0)                       # points
    fmt.setColor(QColor("#204080"))
    buf = QgsTextBufferSettings()
    buf.setEnabled(True)
    buf.setSize(1.5)
    buf.setColor(QColor("#ffffff"))
    fmt.setBuffer(buf)
    settings.setFormat(fmt)
    settings.xOffset = 3.0
    settings.yOffset = -6.0
    settings.priority = 8
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)

    style = symbology.from_qgis(layer) or {}
    lab = style.get("labels") or {}
    check("labels: carried at all", bool(lab.get("enabled")), repr(style.get("labels")))
    check("labels: the field travels", lab.get("field") == "kind", repr(lab.get("field")))
    check("labels: the size is in CSS pixels", lab.get("size") == 20.0,
          "15pt should read back as 20px, got {0!r}".format(lab.get("size")))
    check("labels: the colour travels", lab.get("color") == "#204080", repr(lab.get("color")))
    check("labels: a bold font maps onto a stack we can serve",
          lab.get("font") == "Noto Sans Bold", repr(lab.get("font")))
    check("labels: the buffer becomes a halo",
          lab.get("halo_width") == 2.0 and lab.get("halo_color") == "#ffffff", repr(lab))
    check("labels: the offset travels", lab.get("offset") == [4.0, -8.0], repr(lab.get("offset")))
    check("labels: the priority travels", lab.get("priority") == 8, repr(lab.get("priority")))

    # …and back into QGIS.
    back = make_layer("Point")
    symbology.apply(back, style)
    check("labels: applied back to QGIS", back.labelsEnabled())
    again = (symbology.from_qgis(back) or {}).get("labels") or {}
    for key in ("field", "size", "color", "font", "halo_width", "offset", "priority"):
        check("labels: {0} survives a full round trip".format(key), again.get(key) == lab.get(key),
              "{0!r} -> {1!r}".format(lab.get(key), again.get(key)))

    # A FAMILY THE PORTAL CANNOT DRAW must still return to QGIS unchanged: the portal substitutes
    # because its glyph set is finite, but QGIS draws with real system fonts and has no such limit.
    layer = make_layer("Point")
    settings = QgsPalLayerSettings()
    settings.fieldName = "kind"
    fmt = QgsTextFormat()
    exotic = QFont("Helvetica Neue Condensed")
    exotic.setItalic(True)
    fmt.setFont(exotic)
    settings.setFormat(fmt)
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)
    lab = (symbology.from_qgis(layer) or {}).get("labels") or {}
    check("labels: an unservable font is substituted for the portal",
          lab.get("font") == "Noto Sans Italic", repr(lab.get("font")))
    check("labels: …and the original family is carried",
          (lab.get("qgis_font") or {}).get("family") == "Helvetica Neue Condensed",
          repr(lab.get("qgis_font")))
    back = make_layer("Point")
    symbology.apply(back, {"color": "#111", "labels": lab})
    got = back.labeling().settings().format().font()
    check("labels: …and QGIS gets its own typeface back, not the substitute",
          got.family() == "Helvetica Neue Condensed" and got.italic(),
          "{0!r} italic={1}".format(got.family(), got.italic()))

    # THE SUBSTITUTION PICKS THE RIGHT KIND OF FACE, and follows what the INSTANCE has installed
    # rather than what GeoDeploy happens to ship.
    try:
        import labels as _labels
    except ImportError:                 # pragma: no cover
        _labels = None
    if _labels is not None:
        _labels.set_available(["Noto Sans Regular", "Noto Sans Bold", "Noto Sans Italic",
                               "Noto Serif Regular", "Noto Serif Bold",
                               "Noto Sans Mono Regular"])
        cases = [("Times New Roman", False, False, "Noto Serif Regular"),
                 ("Georgia", True, False, "Noto Serif Bold"),
                 ("Courier New", False, False, "Noto Sans Mono Regular"),
                 ("Arial", True, False, "Noto Sans Bold"),
                 ("Helvetica Neue", False, True, "Noto Sans Italic"),
                 ("Noto Serif Bold", False, False, "Noto Serif Bold")]
        for family, bold, italic, expected in cases:
            got = _labels._fontstack(family, bold, italic, [])
            check("font: {0} -> {1}".format(family, expected), got == expected,
                  "got {0!r}".format(got))
        # …and with only the shipped three installed, a serif has nowhere better to go.
        _labels.set_available(list(_labels.LABEL_FONTS))
        check("font: a serif falls back to the sans when no serif is installed",
              _labels._fontstack("Times New Roman", False, False, []) == "Noto Sans Regular",
              _labels._fontstack("Times New Roman", False, False, []))

    # A label EXPRESSION goes through the translator, and the QGIS source rides along.
    layer = make_layer("Point")
    settings = QgsPalLayerSettings()
    settings.fieldName = "\"kind\" || ' (' || \"pop\" || ')'"
    settings.isExpression = True
    settings.setFormat(QgsTextFormat())
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)
    lab = (symbology.from_qgis(layer) or {}).get("labels") or {}
    check("labels: an expression is translated",
          lab.get("expression") == ["concat", ["get", "kind"], " (", ["get", "pop"], ")"],
          repr(lab.get("expression")))
    check("labels: the QGIS source rides along",
          lab.get("qgis_expression") == settings.fieldName, repr(lab.get("qgis_expression")))

    # An untranslatable label expression must send NO labels rather than label every feature with
    # the expression text.
    layer = make_layer("Point")
    settings = QgsPalLayerSettings()
    settings.fieldName = "geom_to_wkt($geometry)"
    settings.isExpression = True
    settings.setFormat(QgsTextFormat())
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)
    read = symbology.from_qgis(layer) or {}
    check("labels: an untranslatable expression sends none",
          not (read.get("labels") or {}).get("enabled"), repr(read.get("labels")))

    # Turning labels off in GeoDeploy must actually turn them off in QGIS.
    off = make_layer("Point")
    off.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    off.setLabelsEnabled(True)
    symbology.apply(off, {"color": "#123456"})
    check("labels: a style with none switches labelling off", not off.labelsEnabled(),
          "otherwise the two disagree and the next push argues about it")


# ══ 10. Markers that are pictures ════════════════════════════════════════════════════════════════

def marker_pictures():
    section("Marker pictures — a symbol we have no words for travels as its own image")
    import glob
    from qgis.core import (QgsEllipseSymbolLayer, QgsFontMarkerSymbolLayer, QgsMarkerSymbol,
                           QgsSimpleMarkerSymbolLayer, QgsSingleSymbolRenderer,
                           QgsSvgMarkerSymbolLayer)
    svgs = sorted(glob.glob("/usr/share/qgis/svg/**/*.svg", recursive=True))

    def rendered(symbol_layer, size=10.0):
        layer = make_layer("Point")
        sym = QgsMarkerSymbol()
        sym.changeSymbolLayer(0, symbol_layer)
        sym.setSize(size)
        layer.setRenderer(QgsSingleSymbolRenderer(sym))
        return symbology.from_qgis(layer) or {}

    for label, sl in (("svg", QgsSvgMarkerSymbolLayer(svgs[0])),
                      ("font", QgsFontMarkerSymbolLayer("Noto Sans", "A")),
                      ("ellipse", QgsEllipseSymbolLayer())):
        got = rendered(sl)
        uri = str(got.get("marker_image") or "")
        check("{0} marker: carries a picture".format(label),
              uri.startswith("data:image/png;base64,") and len(uri) > 200,
              "{0} bytes".format(len(uri)))

    # A PLAIN marker must NOT carry a picture — it is describable, a bitmap could not be recoloured
    # per class, and every style would pay a few KB for nothing.
    got = rendered(QgsSimpleMarkerSymbolLayer())
    check("a simple marker carries no picture", "marker_image" not in got, repr(list(got)))

    # …and a multi-layer marker symbol, which used to lose everything but its bottom layer.
    layer = make_layer("Point")
    sym = QgsMarkerSymbol()
    sym.changeSymbolLayer(0, QgsSimpleMarkerSymbolLayer())
    sym.appendSymbolLayer(QgsSvgMarkerSymbolLayer(svgs[0]))
    layer.setRenderer(QgsSingleSymbolRenderer(sym))
    got = symbology.from_qgis(layer) or {}
    check("a multi-layer marker carries a picture of the WHOLE symbol",
          str(got.get("marker_image") or "").startswith("data:image/png;base64,"),
          "only the bottom layer used to survive")

    got = rendered(QgsSvgMarkerSymbolLayer(svgs[0]))
    check("the picture survives a merge",
          symbology.merge_style({}, got).get("marker_image") == got.get("marker_image"))


# ══ 11. Markers along a line ═════════════════════════════════════════════════════════════════════

def line_decorations():
    section("Markers along a line — QGIS's marker line → symbol-placement: line")
    from qgis.core import (QgsLineSymbol, QgsMarkerLineSymbolLayer, QgsMarkerSymbol,
                           QgsSimpleLineSymbolLayer, QgsSimpleMarkerSymbolLayer,
                           QgsSingleSymbolRenderer)

    def with_markers(stroke_first):
        """A line symbol with ticks along it, optionally over a plain stroke — which is how QGIS
        builds a decorated line, and the case reading only symbolLayer(0) used to lose."""
        deco = QgsMarkerLineSymbolLayer()
        deco.setInterval(6.0)
        sub = QgsMarkerSymbol()
        sub.changeSymbolLayer(0, QgsSimpleMarkerSymbolLayer())
        sub.setSize(4.0)
        deco.setSubSymbol(sub)
        sym = QgsLineSymbol()
        if stroke_first:
            sym.changeSymbolLayer(0, QgsSimpleLineSymbolLayer())
            sym.appendSymbolLayer(deco)
        else:
            sym.changeSymbolLayer(0, deco)
        layer = make_layer("LineString")
        layer.setRenderer(QgsSingleSymbolRenderer(sym))
        return symbology.from_qgis(layer) or {}

    got = with_markers(stroke_first=True)
    block = got.get("line_marker") or {}
    check("a decorated line carries its markers",
          str(block.get("image") or "").startswith("data:image/png;base64,"),
          "a road with ticks used to arrive as a plain road")
    check("the interval travels", block.get("spacing") is not None, repr(block.get("spacing")))
    check("the stroke under it still travels", got.get("line_width") is not None,
          "reading only the decoration would lose the road itself")

    # …and when the decoration IS the first symbol layer, with no stroke under it.
    block = (with_markers(stroke_first=False).get("line_marker") or {})
    check("a bare marker line carries its markers too",
          str(block.get("image") or "").startswith("data:image/png;base64,"), repr(list(block)))

    # A plain line must NOT carry a decoration, or every line style would grow a bitmap.
    layer = make_layer("LineString")
    symbology.apply(layer, {"color": "#111", "line_width": 2})
    check("a plain line carries no decoration",
          "line_marker" not in (symbology.from_qgis(layer) or {}))


# ══ 12. Pattern fills ════════════════════════════════════════════════════════════════════════════

def fill_patterns():
    section("Pattern fills — the tile is REBUILT so it repeats, not photographed")
    import base64
    import glob
    from qgis.core import (QgsFillSymbol, QgsLinePatternFillSymbolLayer, QgsMarkerSymbol,
                           QgsPointPatternFillSymbolLayer, QgsRasterFillSymbolLayer,
                           QgsSimpleFillSymbolLayer, QgsSimpleMarkerSymbolLayer,
                           QgsSingleSymbolRenderer, QgsSVGFillSymbolLayer)
    from qgis.PyQt.QtCore import Qt
    svgs = sorted(glob.glob("/usr/share/qgis/svg/**/*.svg", recursive=True))

    def read(symbol_layer, stack_under=False):
        sym = QgsFillSymbol()
        if stack_under:
            sym.changeSymbolLayer(0, QgsSimpleFillSymbolLayer())
            sym.appendSymbolLayer(symbol_layer)
        else:
            sym.changeSymbolLayer(0, symbol_layer)
        layer = make_layer("Polygon")
        layer.setRenderer(QgsSingleSymbolRenderer(sym))
        return symbology.from_qgis(layer) or {}

    def tile_of(style):
        block = style.get("fill_pattern") or {}
        uri = str(block.get("image") or "")
        return block, uri

    # A Qt hatch on a plain fill — the commonest patterned polygon there is, and the case that
    # needs scanning past symbolLayer(0).
    hatch = QgsSimpleFillSymbolLayer()
    hatch.setBrushStyle(symbology.enum(Qt, "BrushStyle", "BDiagPattern"))
    block, uri = tile_of(read(hatch, stack_under=True))
    check("a hatch stacked on a fill is found", uri.startswith("data:image/png;base64,"),
          "reading only symbolLayer(0) sees the plain half")
    check("the hatch tile is a multiple of Qt's 8px period",
          block.get("width", 0) % 8 == 0,
          "got {0} — any other size shows a seam".format(block.get("width")))

    # A plain fill must NOT produce a tile.
    plain = QgsSimpleFillSymbolLayer()
    check("a plain fill has no tile", not (read(plain).get("fill_pattern")),
          "a solid colour is describable and needs no bitmap")

    # Line pattern: the tile side follows the angle, because only some angles close.
    for angle, expect_diagonal in ((0.0, False), (90.0, False), (45.0, True)):
        lp = QgsLinePatternFillSymbolLayer()
        lp.setDistance(4.0)
        lp.setLineAngle(angle)
        block, uri = tile_of(read(lp))
        check("line pattern at {0:g} deg makes a tile".format(angle),
              uri.startswith("data:image/png;base64,"), repr(list(block)))
        if block:
            square = block.get("width") == block.get("height")
            check("line pattern at {0:g} deg is square".format(angle), square,
                  "{0}x{1}".format(block.get("width"), block.get("height")))

    # An angle with no square tile is snapped rather than seamed.
    lp = QgsLinePatternFillSymbolLayer()
    lp.setDistance(4.0)
    lp.setLineAngle(30.0)
    block, uri = tile_of(read(lp))
    check("an unclosable angle still makes a tile", uri.startswith("data:image/png;base64,"),
          "a snapped angle beats a seam every tile")

    # Point pattern: the tile is exactly the grid spacing, so it closes by construction.
    pp = QgsPointPatternFillSymbolLayer()
    pp.setDistanceX(5.0)
    pp.setDistanceY(3.0)
    sub = QgsMarkerSymbol()
    sub.changeSymbolLayer(0, QgsSimpleMarkerSymbolLayer())
    sub.setSize(2.0)
    pp.setSubSymbol(sub)
    block, uri = tile_of(read(pp))
    check("point pattern makes a tile", uri.startswith("data:image/png;base64,"), repr(list(block)))
    check("its tile is NOT square, because the grid is not",
          block.get("width") != block.get("height"),
          "{0}x{1} — a square tile would change the spacing".format(
              block.get("width"), block.get("height")))

    # An SVG fill: the source image is the tile.
    sf = QgsSVGFillSymbolLayer(svgs[0], 6.0)
    block, uri = tile_of(read(sf))
    check("an SVG fill makes a tile", uri.startswith("data:image/png;base64,"), repr(list(block)))

    # Millimetres must not be read as pixels — that turns a 4 mm hatch into a 4 px block.
    lp = QgsLinePatternFillSymbolLayer()
    lp.setDistance(4.0)
    lp.setLineAngle(0.0)
    block, _ = tile_of(read(lp))
    check("a millimetre spacing becomes ~15px, not 4",
          block.get("width", 0) >= 12,
          "got {0} — reading mm as px makes a hatch a solid block".format(block.get("width")))

    # The tile really is a PNG.
    if uri:
        raw = base64.b64decode(uri.split(",", 1)[1])
        signature = bytes([137, 80, 78, 71, 13, 10, 26, 10])
        check("the tile is a real PNG", raw[:8] == signature, repr(raw[:8]))

    assert QgsRasterFillSymbolLayer  # imported for the reader's benefit


def main():
    audit()
    round_trip()
    key_by_key()
    tiles()
    unknown_geometry()
    authored_in_qgis()
    rule_based()
    two_and_a_half_d()
    natives()
    labelling()
    marker_pictures()
    line_decorations()
    fill_patterns()
    print("\n{0} checks, {1} failed".format(CHECKS[0], len(FAILURES)))
    for name in FAILURES:
        print("  - {0}".format(name))
    QGS.exitQgis()
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
