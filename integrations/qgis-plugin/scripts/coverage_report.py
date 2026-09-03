"""Everything QGIS can draw, against what GeoDeploy carries — checked, not remembered.

## Why this is a script and not a table in a document

A hand-written list of "the symbology we support" is out of date the day QGIS ships a new symbol
layer, and nobody finds out. QGIS publishes its own inventory — `symbolLayerRegistry()`,
`rendererRegistry()`, and the data-defined property definitions on `QgsSymbolLayer` and
`QgsPalLayerSettings` — so this reads that inventory and joins it against the verdicts declared
below. **Anything QGIS offers that is not declared here fails the run**, which is what stops the
matrix silently rotting: a new QGIS version forces a decision rather than a gap.

## The four verdicts

Each is a promise about the ROUND TRIP, not a feature flag. The constraint that shapes all of them:
GeoDeploy renders with MapLibre and TiTiler, not with QGIS, so a symbol travels exactly when the web
renderer can express it — and where it cannot, the author should be told which of the other three
applies before they publish.

  EXACT    the web draws it natively; push, pull, push again is identical
  APPROX   the web draws something close and deliberately chosen; stable, but not what QGIS drew
  CARRIED  stored and handed back untouched, never rendered — QGIS to QGIS is lossless
  TODO     not carried at all today; the note says what it would take and which issue tracks it

Run it the way `test_real_qgis.py` runs:

    docker run --rm -v <plugin-dir>:/src -w /src -e QT_QPA_PLATFORM=offscreen \\
        qgis/qgis:ltr python3 scripts/coverage_report.py
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from qgis.core import (Qgis, QgsApplication, QgsPalLayerSettings,  # noqa: E402
                       QgsSymbolLayer)

EXACT, APPROX, CARRIED, TODO = "EXACT", "APPROX", "CARRIED", "TODO"

# ── Renderers ────────────────────────────────────────────────────────────────────────────────────
RENDERERS = {
    "singleSymbol":            (EXACT,  "the default path, both directions"),
    "categorizedSymbol":       (EXACT,  "classes and the symbol's shape both travel"),
    "graduatedSymbol":         (EXACT,  "classes and the symbol's shape both travel"),
    "RuleRenderer":            (EXACT,  "one render layer per rule, with its filter translated by "
                                        "`geodeploy.expressions`, its own symbol and its own zoom "
                                        "range. Nesting flattens with AND, ELSE becomes NOT(the "
                                        "siblings), and a filter outside the expression subset is "
                                        "DROPPED with a note rather than widened"),
    "heatmapRenderer":         (TODO,   "MapLibre has a native `heatmap` layer type - radius, "
                                        "weight, intensity and ramp all map over"),
    "pointCluster":            (TODO,   "portal_generator already clusters; wire the renderer to it"),
    "pointDisplacement":       (TODO,   "no web equivalent; cluster is the honest approximation"),
    "25dRenderer":             (APPROX, "becomes a real `fill-extrusion`, which is better than the "
                                        "pseudo-3D block but not the same picture: MapLibre has one "
                                        "colour plus a vertical gradient and NO shadow. Height and "
                                        "angle come from the project variables `@qgis_25d_height` / "
                                        "`@qgis_25d_angle` (the renderer has no accessors); the "
                                        "angle, wall and shadow ride along in `extrusion.qgis25d` "
                                        "so a round trip comes back as 2.5D, not as a plain "
                                        "extrusion. Height is in MAP UNITS, read as metres"),
    "invertedPolygonRenderer": (TODO,   "a server-derived mask source (the layer's union subtracted "
                                        "from the world), the device `services/pillars` already uses"),
    "mergedFeatureRenderer":   (TODO,   "draws as the underlying symbol; overlap differs"),
    "embeddedSymbol":          (TODO,   "per-feature symbols have no tile representation - carry only"),
    "nullSymbol":              (EXACT,  "carried as `no_symbol`; the style emits NO render layer, so the layer stays listed and identifiable and draws nothing"),
}

# ── Symbol layers, by registry name ──────────────────────────────────────────────────────────────
MARKERS = {
    "SimpleMarker":      (APPROX, "6 of QGIS's shapes; the rest need the full shape table"),
    "SvgMarker":         (EXACT,  "the SYMBOL is rendered to a PNG by the plugin and shipped as a "
                                  "data URI; MapLibre does not need to understand an icon, only to "
                                  "have its pixels"),
    "RasterMarker":      (EXACT,  "same - rendered, not translated"),
    "FontMarker":        (EXACT,  "same - the glyph is drawn by QGIS and the pixels travel"),
    "EllipseMarker":     (EXACT,  "same"),
    "FilledMarker":      (EXACT,  "same"),
    "AnimatedMarker":    (APPROX, "rendered, so the first frame travels; nothing animates"),
    "MaskMarker":        (TODO,   "a clipping mask; no MapLibre equivalent - carry"),
    "VectorField":       (APPROX, "rendered as a picture like any marker, so it draws - but the "
                                  "arrows cannot follow the data per feature"),
    "GeometryGenerator": (TODO,   "an arbitrary expression producing NEW geometry - not portable, "
                                  "carry the QML"),
}

LINES = {
    "SimpleLine":        (EXACT,  "colour, width, custom dash pattern (in line-width multiples, "
                                  "MapLibre's unit), cap, join and offset. A polygon OUTLINE gets "
                                  "the same vocabulary, because an outline is a line"),
    "MarkerLine":        (TODO,   "`symbol-placement: line` with `symbol-spacing` - this is how "
                                  "MapLibre draws ticks on a boundary and arrows on a river"),
    "HashLine":          (TODO,   "same placement trick with a short line icon"),
    "ArrowLine":         (TODO,   "same, with an arrow icon"),
    "InterpolatedLine":  (TODO,   "data-driven `line-width` exists; colour needs `line-gradient` "
                                  "with `lineMetrics`"),
    "Lineburst":         (TODO,   "a gradient across the line's width - approximate to flat"),
    "RasterLine":        (TODO,   "approximate to the dominant colour"),
    "FilledLine":        (TODO,   "a line drawn as a filled buffer - approximate to width"),
    "LinearReferencing": (TODO,   "chainage labels along a line; needs labels first (#98)"),
    "GeometryGenerator": (TODO,   "see the marker entry"),
}

FILLS = {
    "SimpleFill":        (APPROX, "colour, opacity and outline exact; the Qt BRUSH STYLE (hatch, "
                                  "cross, dense) is dropped - `fill-pattern` against a generated "
                                  "tileable canvas image"),
    "LinePatternFill":   (TODO,   "`fill-pattern`, angle and spacing encoded in the image id"),
    "PointPatternFill":  (TODO,   "`fill-pattern` from a generated image"),
    "RandomMarkerFill":  (TODO,   "approximate to the regular point pattern"),
    "SVGFill":           (TODO,   "asset upload, then `fill-pattern` (#101)"),
    "RasterFill":        (TODO,   "asset upload, then `fill-pattern` (#101)"),
    "CentroidFill":      (TODO,   "a `symbol` layer over a polygon source places at the centroid by "
                                  "default - nearly free"),
    "GradientFill":      (TODO,   "MapLibre has no fill gradient; flat fill at the ramp's midpoint, "
                                  "reported as approximated"),
    "ShapeburstFill":    (TODO,   "a distance transform inside each polygon - genuinely out of "
                                  "reach; carry the QML"),
    "GeometryGenerator": (TODO,   "see the marker entry"),
}

# ── Symbol-layer data-defined properties, grouped ────────────────────────────────────────────────
# The registry lists 74. Grouped because the verdict is per FAMILY, not per key - `offsetX` and
# `offsetY` are one decision. Every registry key must appear in exactly one group.
PROPERTY_GROUPS = [
    ("size / width", EXACT,
     "`icon-size` and `line-width`, data-driven already",
     ("size", "width", "height", "outlineWidth", "arrowWidth", "arrowStartWidth",
      "arrowHeadLength", "arrowHeadThickness")),
    ("colour", EXACT, "`icon-color` / `line-color` / `fill-color`, data-driven already",
     ("fillColor", "outlineColor", "color2", "lineStartColorValue", "lineEndColorValue")),
    ("opacity", EXACT, "`*-opacity`", ("alpha",)),
    ("rotation, offset, anchor", APPROX,
     "rotation and offset travel (`icon-rotate`, `icon-offset`), and so does a marker's own "
     "opacity; the ANCHOR point and offset-along-a-line do not yet",
     ("angle", "offset", "offsetX", "offsetY", "hAnchor", "vAnchor", "offsetAlongLine",
      "OffsetRotation")),
    ("expressions", APPROX,
     "a declared subset of QGIS expressions translates to MapLibre ones and back "
     "(`geodeploy.expressions`); anything outside it is refused BY NAME rather than approximated",
     ()),
    ("dash, cap, join", APPROX,
     "the custom dash pattern, cap and join all travel; the dash OFFSET, blank segments and end "
     "trims have no MapLibre equivalent",
     ("customDash", "dashPatternOffset", "capStyle", "joinStyle", "outlineStyle", "blankSegments",
      "trimStart", "trimEnd")),
    ("marker placement along a line", TODO,
     "`symbol-placement: line` + `symbol-spacing`",
     ("placement", "interval", "averageAngleLength", "skipMultiples", "showMarker",
      "extraItems", "lineClipping", "markerClipping", "clipPoints")),
    ("pattern fills", TODO, "generated tileable canvas images",
     ("lineAngle", "lineDistance", "distanceX", "distanceY", "displacementX", "displacementY",
      "fillStyle", "densityArea", "pointCount", "randomOffsetX", "randomOffsetY", "randomSeed")),
    ("gradient", APPROX, "no MapLibre fill gradient - flat fill at the midpoint",
     ("gradientMode", "gradientType", "gradientSpread", "gradientRef1X", "gradientRef1Y",
      "gradientRef2X", "gradientRef2Y", "gradientRef1Centroid", "gradientRef2Centroid")),
    ("shapeburst", CARRIED, "a distance transform; no web equivalent",
     ("shapeburstIgnoreRings", "shapeburstMaxDist", "shapeburstWholeShape")),
    ("external picture", TODO, "asset upload plus sprite generation (#101)",
     ("file", "name", "char", "fontFamily", "fontStyle", "preserveAspectRatio")),
    ("interpolated line", TODO, "`line-gradient` with `lineMetrics`",
     ("lineStartWidthValue", "lineEndWidthValue")),
    ("arrow shape", TODO, "an arrow icon along the line",
     ("arrowType", "arrowHeadType")),
    ("effects", CARRIED, "MapLibre exposes no blur or blend modes", ("blurRadius",)),
    ("enabled", EXACT, "an unchecked symbol layer is simply not emitted", ("enabled",)),
]

# ── Whole blocks tracked as one decision ─────────────────────────────────────────────────────────
BLOCKS = {
    "Labels (QgsPalLayerSettings)": (
        APPROX,
        "BUILT 2026-09-03, in the platform and the plugin at once - GeoDeploy had no labels at all "
        "before. `style.labels` becomes its own MapLibre `symbol` layer: text (a field or a "
        "translated expression), size, colour, halo from the buffer, offset, rotation, wrap, "
        "transform, letter spacing, allow-overlap, priority (inverted - QGIS ranks higher as more "
        "important, MapLibre places lower first) and the label's OWN zoom range. APPROX for two "
        "reasons: a FONT is mapped onto the stacks the glyph set can serve, because MapLibre draws "
        "NOTHING for one it lacks; and shadows, background shapes and callouts have no equivalent. "
        "Rule-based labelling takes the first rule and says so. The label FONT is the one "
        "approximation left, and it is bounded by what the instance has installed - see below"),
    "Fonts for labels": (
        APPROX,
        "Noto Sans Regular/Bold/Italic SHIP, served from `templates/shared/fonts/` by "
        "`/api/fonts/{fontstack}/{range}.pbf`; `GET /api/fonts` lists what is installed and the "
        "plugin asks on connect. A face is DISCOVERED, not declared - `scripts/build_glyphs.js` "
        "turns any TTF into a set and the route picks it up with no rebuild. A QGIS family with no "
        "match is mapped to the nearest installed face by kind (serif stays serif, mono stays "
        "mono) and weight/slant, the substitution is logged by name, and `text-font` is emitted as "
        "a STACK so an instance that later installs the face draws it without republishing. APPROX "
        "only because a web map can draw the faces it has and QGIS can draw the machine's - and "
        "QGIS gets its ORIGINAL family back either way, carried in `labels.qgis_font`"),
    "Paint effects (blur, glow, shadow)": (
        CARRIED, "MapLibre exposes none of them"),
    "Scale-dependent visibility (per rule)": (
        EXACT, "a rule's scale range becomes its layer's `minzoom`/`maxzoom`, clamped to MapLibre's "
               "0-24 - `styles.zoom_for_scale` does the conversion, and the two ends swap"),
    "Scale-dependent visibility (per layer)": (
        EXACT, "`minimumScale`/`maximumScale` become `minzoom`/`maxzoom` on every render layer the "
               "style emits, and go back as a scale range. The two ends swap - a denominator grows "
               "as a zoom shrinks"),
    "Layer subset string": (
        EXACT, "translated by `geodeploy.expressions` into `style.filter` and ANDed into every "
               "render layer; the QGIS source rides along in `filter_expression`. A subset OUTSIDE "
               "the expression subset sends no filter at all and says so - publishing every feature "
               "would be a different map from the one on screen"),
    "Multi-layer symbols": (
        APPROX, "a stacked MARKER travels as a picture of the whole symbol - a halo under a dot is "
                "two simple markers, and reading only symbolLayer(0) described half of it. Stacked "
                "LINE and FILL symbols are still read as their first layer"),
    "Blend modes": (CARRIED, "MapLibre GL JS does not expose them"),
    "Colour ramps at any class count": (
        EXACT, "ramps interpolate between their anchor stops, so N classes give N distinct colours. "
               "They used to snap to the nearest of seven stops - 8 classes in 7 colours, 12 in 7 - "
               "which is what the old 12-class cap was working around. Now 2-100, matching QGIS"),
    "Qualitative colours past twelve": (
        EXACT, "`category_color` hands out the twelve picked colours, then a golden-ratio hue wheel "
               "rather than cycling - deterministic, so a category keeps its colour when the data "
               "gains a value"),
    "3D (QgsAbstract3DSymbol)": (
        APPROX, "extrusion travels and is drawn by GeoDeploy but NOT by QGIS; units are metres here "
                "and map units there, with no conversion; roof/wall colours and edges approximate"),
}


def _fail(msg, bucket):
    bucket.append(msg)


def main():
    QgsApplication.setPrefixPath(os.environ.get("QGIS_PREFIX_PATH", "/usr"), True)
    app = QgsApplication([], False)
    app.initQgis()
    problems = []
    counts = {EXACT: 0, APPROX: 0, CARRIED: 0, TODO: 0}

    print("QGIS {0} - symbology coverage\n".format(Qgis.QGIS_VERSION))

    def table(title, declared, actual):
        print("=" * 96)
        print("{0}  ({1} in this QGIS)".format(title, len(actual)))
        print("=" * 96)
        for name in sorted(actual):
            if name not in declared:
                _fail("{0}: {1} is not declared in coverage_report.py".format(title, name),
                      problems)
                print("  {0:<20} {1:<8} ** UNDECLARED - classify it **".format(name, "?"))
                continue
            verdict, note = declared[name]
            counts[verdict] += 1
            print("  {0:<20} {1:<8} {2}".format(name, verdict, note))
        for name in sorted(set(declared) - set(actual)):
            print("  {0:<20} {1:<8} (not in this QGIS)".format(name, "-"))
        print()

    reg = QgsApplication.symbolLayerRegistry()
    table("FEATURE RENDERERS", RENDERERS,
          set(QgsApplication.rendererRegistry().renderersList()))
    for label, declared, stype in (("MARKER SYMBOL LAYERS", MARKERS, Qgis.SymbolType.Marker),
                                   ("LINE SYMBOL LAYERS", LINES, Qgis.SymbolType.Line),
                                   ("FILL SYMBOL LAYERS", FILLS, Qgis.SymbolType.Fill)):
        table(label, declared, set(reg.symbolLayersForType(stype)))

    # Data-defined properties: every registry key must land in exactly one group.
    print("=" * 96)
    print("SYMBOL-LAYER DATA-DEFINED PROPERTIES")
    print("=" * 96)
    actual = {d.name() for d in QgsSymbolLayer.propertyDefinitions().values()}
    seen = set()
    for title, verdict, note, keys in PROPERTY_GROUPS:
        here = sorted(set(keys) & actual)
        dupes = set(keys) & seen
        if dupes:
            _fail("properties declared twice: {0}".format(", ".join(sorted(dupes))), problems)
        seen |= set(keys)
        counts[verdict] += 1
        print("  {0:<28} {1:<8} {2}".format(title, verdict, note))
        print("  {0:<28} {1}".format("", ", ".join(here) or "(none in this QGIS)"))
    missing = sorted(actual - seen)
    if missing:
        _fail("{0} data-defined properties are not classified: {1}".format(
            len(missing), ", ".join(missing)), problems)
        print("\n  ** UNCLASSIFIED ({0}): {1}".format(len(missing), ", ".join(missing)))
    print("\n  {0} of {1} registry properties classified\n".format(
        len(actual & seen), len(actual)))

    print("=" * 96)
    print("WHOLE BLOCKS")
    print("=" * 96)
    for name in sorted(BLOCKS):
        verdict, note = BLOCKS[name]
        counts[verdict] += 1
        print("  {0:<36} {1:<8} {2}".format(name, verdict, note))
    print("\n  QGIS label properties in this build: {0}".format(
        len(QgsPalLayerSettings.propertyDefinitions())))

    print("\n" + "=" * 96)
    print("  ".join("{0}: {1}".format(k, counts[k]) for k in (EXACT, APPROX, CARRIED, TODO)))
    if problems:
        print("\n{0} problem(s):".format(len(problems)))
        for p in problems:
            print("  - {0}".format(p))
    app.exitQgis()
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
