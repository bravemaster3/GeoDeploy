"""GeoDeploy symbology ⇄ QGIS renderers.

Both directions, because a style that only travels one way is a export button, not interchange:

* **In** — a layer added from an instance arrives looking like the portal: same classes, same
  colours, same breaks. The class list comes from the layer's stored style, which is the same list
  `services/symbology.py` turns into MapLibre expressions, so the two cannot disagree about which
  feature is which colour.
* **Out** — a layer uploaded from QGIS takes its styling with it, so the portal shows what the
  author was looking at instead of a default blue.

**Nothing here reimplements classification.** Breaks are read from the style on the way in, and read
from the QGIS renderer on the way out; when the plugin needs NEW breaks it asks the instance
(`/field-stats`), exactly as the CLI does. Two implementations of quantile would eventually disagree
and the disagreement would first appear as a published map whose legend does not match its colours.

Every conversion degrades rather than fails: an unmappable renderer becomes a single symbol with the
right colour, because a layer drawn plainly is recoverable and a layer that refuses to load is not.
"""
from __future__ import annotations

import re

from .connection import GeoDeployError  # noqa: F401  (re-exported for callers)
try:                                    # a package, inside QGIS
    from .compat import enum
except ImportError:                     # pragma: no cover - exec'd standalone by the test harness
    from compat import enum

try:                                    # pragma: no cover - only present inside QGIS
    from qgis.core import (QgsCategorizedSymbolRenderer, QgsGraduatedSymbolRenderer,
                           QgsRendererCategory, QgsRendererRange, QgsSimpleFillSymbolLayer,
                           QgsSimpleLineSymbolLayer, QgsSimpleMarkerSymbolLayer, QgsSymbol,
                           QgsSingleSymbolRenderer,
                           QgsMultiBandColorRenderer, QgsSingleBandGrayRenderer,
                           QgsSingleBandPseudoColorRenderer, QgsHillshadeRenderer,
                           QgsPalettedRasterRenderer)
    from qgis.PyQt.QtCore import Qt
    from qgis.PyQt.QtGui import QColor
    QGIS = True
except ImportError:                     # importable outside QGIS so the module can be unit-tested
    QGIS = False

try:                                    # pragma: no cover - only present inside QGIS
    # THE RASTER HALF, IMPORTED SEPARATELY AND DELIBERATELY. A QGIS build missing one of these must
    # cost the raster path only: folding them into the block above would turn a missing shader class
    # into "no styling at all", which is how one narrow gap becomes every layer arriving plain.
    from qgis.core import (QgsColorRampShader, QgsContrastEnhancement, QgsGradientColorRamp,
                           QgsGradientStop, QgsRasterShader, QgsStyle)
    QGIS_RASTER = True
except ImportError:                     # pragma: no cover
    QGIS_RASTER = False


# ── GeoDeploy → QGIS ─────────────────────────────────────────────────────────────────────────────

#: GeoDeploy marker names → the Qt shapes QGIS draws. A shape it does not have degrades to a circle
#: rather than refusing the style.
_MARKERS = {
    "circle": "circle", "square": "square", "triangle": "triangle",
    "diamond": "diamond", "star": "star", "cross": "cross",
}


def _size_stops(style: dict):
    """`(field, in_min, in_max, out_min, out_max)` for a proportional size, or None."""
    if (style.get("size_mode") or "fixed") != "proportional":
        return None
    field = (style.get("size_field") or "").strip()
    stops = [s for s in (style.get("size_stops") or [])
             if isinstance(s, (list, tuple)) and len(s) == 2]
    if not field or len(stops) < 2:
        return None
    ordered = sorted(stops, key=lambda s: s[0])
    lo, hi = ordered[0], ordered[-1]
    if hi[0] == lo[0]:
        return None                     # a zero-width input range divides by zero in scale_linear
    return (field, lo[0], hi[0], lo[1], hi[1])


def _apply_data_defined_size(symbol, layer0, style: dict) -> None:
    """Drive marker size / line width from a field, the way the portal does.

    GeoDeploy interpolates linearly between two stops, so `scale_linear` is the exact equivalent —
    not an approximation. The unit conversions are the same ones the fixed sizes use: a marker's
    QGIS size is a DIAMETER against GeoDeploy's radius, and a line's width is in mm against pixels.
    """
    spec = _size_stops(style)
    if spec is None:
        return
    field, in_lo, in_hi, out_lo, out_hi = spec
    # The stops are pixel sizes like every other size here, so the symbol must already be measured
    # in pixels (`_use_pixels`, called by `_symbol_for` before this).
    try:
        from qgis.core import QgsProperty, QgsSymbolLayer
    except ImportError:                 # pragma: no cover
        return
    try:
        if isinstance(layer0, QgsSimpleMarkerSymbolLayer):
            expr = 'scale_linear("{0}", {1}, {2}, {3}, {4})'.format(
                field, in_lo, in_hi, out_lo * 2 * CSS_PX_TO_POINTS, out_hi * 2 * CSS_PX_TO_POINTS)
            symbol.setDataDefinedSize(QgsProperty.fromExpression(expr))
        elif isinstance(layer0, QgsSimpleLineSymbolLayer):
            expr = 'scale_linear("{0}", {1}, {2}, {3}, {4})'.format(
                field, in_lo, in_hi, out_lo * CSS_PX_TO_POINTS, out_hi * CSS_PX_TO_POINTS)
            layer0.setDataDefinedProperty(enum(QgsSymbolLayer, "Property", "PropertyStrokeWidth"),
                                          QgsProperty.fromExpression(expr))
    except Exception as exc:            # noqa: BLE001 - a size must never stop a layer drawing
        _log("Could not apply size-by-field ({0}): {1}".format(field, exc))


#: GeoDeploy's sizes are CSS pixels — what MapLibre draws with, and device-INDEPENDENT (1/96 inch).
#: QGIS's "pixels" are RENDER pixels, which on a scaled or high-DPI display are physically smaller,
#: so the same number came out too small. Points are device-independent too (1/72 inch), so one CSS
#: pixel is 0.75 pt and the symbol keeps its size on screen wherever it is drawn.
CSS_PX_TO_POINTS = 0.75

#: What the map draws a point as when its style says nothing: `circle-radius: 5` with a white 1 px
#: stroke. Kept in step with `ui/src/lib/mapStyle.js`, `services/portal_generator.py` and
#: `templates/shared/portal.js` — the same three surfaces the project's parity rule names. They are
#: the defaults a layer with no saved style is ALREADY being drawn with in a browser, so matching
#: them is what makes QGIS and the portal show the same map.
DEFAULT_POINT_RADIUS = 5
#: A marker's outline width is a RATIO OF ITS RADIUS, not a pixel width: `lib/markerImage.js` draws
#: `lineWidth = radius * ratio`, and `services/symbology.marker_outline` says the same. 0.28 is what
#: both use when a style omits it. The plugin used to write a flat 1 px — ratio 0.2 at the default
#: radius, so a slightly thinner ring than the portal draws, and a width the user could not change
#: because it was never read back either.
DEFAULT_MARKER_OUTLINE_RATIO = 0.28
DEFAULT_MARKER_OUTLINE = "#ffffff"
#: And the polygon ones. `fill-opacity: opacity * style.get("fill_opacity", 0.45)` in
#: portal_generator — so a polygon with no stated opacity is drawn at 45% on every portal there is.
DEFAULT_FILL_OPACITY = 0.45
DEFAULT_FILL_OUTLINE = "#1d4ed8"
#: A line with no stated width is 2 CSS px on the map.
DEFAULT_LINE_WIDTH = 2
#: A POLYGON's outline with no stated width is 1 CSS px — what a MapLibre `fill` draws on its own
#: edge, and therefore what every polygon published before outlines had a width looked like.
#: Mirrors `services/symbology.POLYGON_OUTLINE_WIDTH`.
DEFAULT_POLYGON_OUTLINE = 1
#: And the colour every renderer falls back to — `symbology.DEFAULT_COLOR` on the instance.
DEFAULT_COLOR = "#3b82f6"
#: The catch-all colour of a categorized layer — `symbology.DEFAULT_OTHER_COLOR` on the instance.
DEFAULT_OTHER_COLOR = "#9ca3af"
#: The footprint of an extruded POINT when the style names none, in metres. Mirrors
#: `services/symbology.DEFAULT_PILLAR_RADIUS_M` and `services/pillars.DEFAULT_RADIUS_M`. The
#: instance derives a better one from the layer's own extent when it has the bbox to do it with;
#: this is only the floor, for a style that reaches QGIS without one.
DEFAULT_PILLAR_RADIUS_M = 30.0


def _number(value, default):
    """A finite float, or `default`. Styles arrive from JSON, a dialog and a QGIS getter."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if out == out and out not in (float("inf"), float("-inf")) else default


def _outline_px(style: dict) -> float:
    """A marker's outline width in CSS pixels: `radius * outline_width`, the ratio the map uses."""
    ratio = max(0.0, min(1.0, _number(style.get("outline_width"), DEFAULT_MARKER_OUTLINE_RATIO)))
    return _number(style.get("radius"), DEFAULT_POINT_RADIUS) * ratio


def _set_stroke_width(layer0, width: float) -> None:
    """Set a symbol layer's OUTLINE width, whatever this QGIS calls the setter.

    Used by markers AND fills, and that is the point. A fill's outline setter is `setStrokeWidth`;
    `setWidth` belongs to `QgsSimpleLineSymbolLayer` and has never existed on a fill. Calling it
    raised `AttributeError` inside every polygon style — swallowed by `apply_to_vector_tiles` into
    "Could not style the vector tiles" (QGIS then drew its own default colour) and by `from_qgis`
    into `{}` (a polygon uploaded with no styling at all). One name, resolved in one place, so the
    two branches cannot disagree about it again.
    """
    for name in ("setStrokeWidth", "setOutlineWidth"):
        fn = getattr(layer0, name, None)
        if callable(fn):
            try:
                fn(width)
                return
            except Exception:           # noqa: BLE001 - an outline is not worth failing the style  # nosec B110 - intentional: a cosmetic failure must not take down the layer
                pass


def _stroke_width_of(layer0):
    """A symbol layer's outline width in POINTS, or None. The inverse of `_set_stroke_width`."""
    for name in ("strokeWidth", "outlineWidth"):
        fn = getattr(layer0, name, None)
        if callable(fn):
            value = _number(fn(), None)
            if value is not None:
                return value
    return None


def _use_points(symbol, layer0) -> None:
    """Measure this symbol in POINTS, so it matches the size GeoDeploy draws.

    Millimetres (the QGIS default) made a radius of 5 into a 10 mm marker — roughly four times too
    big. Render pixels fixed that but went too far the other way on a scaled display, because they
    are device pixels while GeoDeploy's are CSS pixels. Points are the unit that means the same
    thing on both sides.
    """
    try:
        from qgis.core import QgsUnitTypes
        pt = enum(QgsUnitTypes, "RenderUnit", "RenderPoints")
    except Exception:                   # noqa: BLE001 - very old QGIS: leave the default
        return
    # `setStrokeWidthUnit` is here for the same reason `_set_stroke_width` exists: a FILL measures
    # its outline through a differently-named setter, so probing only `setWidthUnit` left every
    # polygon outline in millimetres against a number that means CSS pixels — a hairline asked for
    # as 1 px arriving nearly four times too wide.
    for target, setter in ((symbol, "setSizeUnit"), (layer0, "setSizeUnit"),
                           (symbol, "setWidthUnit"), (layer0, "setWidthUnit"),
                           (layer0, "setStrokeWidthUnit")):
        fn = getattr(target, setter, None)
        if callable(fn):
            try:
                fn(pt)
            except Exception:           # noqa: BLE001 - not every symbol layer has both  # nosec B110 - intentional: a cosmetic failure must not take down the layer
                pass


#: What a MapLibre line does when a style says nothing — `line-cap: butt`, `line-join: miter`. QGIS
#: defaults to square and bevel instead, which is why these are stated rather than left alone.
DEFAULT_LINE_CAP = "butt"
DEFAULT_LINE_JOIN = "miter"

#: GeoDeploy's cap and join names are MapLibre's, which are also SVG's. Qt spells them differently.
_CAP_STYLES = {"butt": "FlatCap", "round": "RoundCap", "square": "SquareCap"}
_JOIN_STYLES = {"bevel": "BevelJoin", "round": "RoundJoin", "miter": "MiterJoin"}


def _apply_line_decoration(layer0, style: dict) -> None:
    """Dash pattern, cap and join onto a simple line symbol layer.

    A `dash_pattern` is stored in MULTIPLES OF THE LINE WIDTH — MapLibre's unit — and QGIS wants
    absolute lengths in the symbol layer's own unit, so it is multiplied back up by the width. That
    is the whole reason the two can round-trip: a pattern in pixels would change shape the moment
    somebody changed the width, on either side.
    """
    pattern = style.get("dash_pattern")
    if isinstance(pattern, (list, tuple)) and len(pattern) >= 2:
        try:
            width = _number(style.get("line_width"), DEFAULT_LINE_WIDTH) * CSS_PX_TO_POINTS
            vector = [float(v) * width for v in pattern]
            if hasattr(layer0, "setUseCustomDashPattern"):
                layer0.setUseCustomDashPattern(True)
            if hasattr(layer0, "setCustomDashVector"):
                layer0.setCustomDashVector(vector)
            _set_dash_unit(layer0)
        except Exception as exc:        # noqa: BLE001 - a dash is not worth failing the symbol
            _log("Could not apply the custom dash pattern: {0}".format(exc))
    else:
        dash = (style.get("lineType") or "solid").lower()
        if dash == "dashed":
            layer0.setPenStyle(enum(Qt, "PenStyle", "DashLine"))
        elif dash == "dotted":
            layer0.setPenStyle(enum(Qt, "PenStyle", "DotLine"))

    # ALWAYS SET, and defaulted to MAPLIBRE's — the same rule as the point radius and the fill
    # opacity above. QGIS's own defaults are `square` and `bevel` where a map draws `butt` and
    # `miter`, so leaving them alone meant a plain line ended and cornered differently in QGIS than
    # in the portal, and reading them back reported an edit nobody made.
    for key, table, setter, fallback in (
            ("line_cap", _CAP_STYLES, "setPenCapStyle", DEFAULT_LINE_CAP),
            ("line_join", _JOIN_STYLES, "setPenJoinStyle", DEFAULT_LINE_JOIN)):
        name = table.get(str(style.get(key) or fallback).lower())
        fn = getattr(layer0, setter, None)
        if name and callable(fn):
            try:
                fn(enum(Qt, "PenCapStyle" if key == "line_cap" else "PenJoinStyle", name))
            except Exception:           # noqa: BLE001  # nosec B110 - cosmetic, never fatal
                pass

    offset = _number(style.get("line_offset"), None)
    fn = getattr(layer0, "setOffset", None)
    if offset is not None and callable(fn):
        try:
            # SIGN FLIP. MapLibre offsets to the LEFT of the direction of travel for a positive
            # value and QGIS to the right, so the same number would draw the line on the other side
            # of the road. Flipped in both directions, here and in the reader.
            fn(-offset * CSS_PX_TO_POINTS)
            _set_offset_unit(layer0)
        except Exception:               # noqa: BLE001  # nosec B110 - cosmetic, never fatal
            pass


def _set_dash_unit(layer0) -> None:
    """Measure a custom dash in POINTS, like every other size here."""
    _set_unit(layer0, "setCustomDashPatternUnit")


def _set_offset_unit(layer0) -> None:
    _set_unit(layer0, "setOffsetUnit")


def _set_unit(layer0, setter: str) -> None:
    try:
        from qgis.core import QgsUnitTypes
        fn = getattr(layer0, setter, None)
        if callable(fn):
            fn(enum(QgsUnitTypes, "RenderUnit", "RenderPoints"))
    except Exception:                   # noqa: BLE001  # nosec B110 - very old QGIS: leave the default
        pass


def _apply_marker_placement(symbol, layer0, style: dict) -> None:
    """Rotation, offset and per-symbol opacity onto a marker.

    All three are things MapLibre draws natively (`icon-rotate`, `icon-offset`, `icon-opacity`) and
    GeoDeploy simply had no word for, so each is an exact round trip rather than an approximation.
    """
    rotation = _number(style.get("marker_rotation"), None)
    if rotation is not None:
        for target in (symbol, layer0):
            fn = getattr(target, "setAngle", None)
            if callable(fn):
                try:
                    fn(rotation)
                    break
                except Exception:       # noqa: BLE001  # nosec B110
                    pass
    offset = style.get("marker_offset")
    if isinstance(offset, (list, tuple)) and len(offset) == 2:
        fn = getattr(layer0, "setOffset", None)
        if callable(fn):
            try:
                from qgis.PyQt.QtCore import QPointF
                fn(QPointF(_number(offset[0], 0.0) * CSS_PX_TO_POINTS,
                           _number(offset[1], 0.0) * CSS_PX_TO_POINTS))
                _set_offset_unit(layer0)
            except Exception:           # noqa: BLE001  # nosec B110
                pass
    own = _number(style.get("marker_opacity"), None)
    if own is not None:
        fn = getattr(symbol, "setOpacity", None)
        if callable(fn):
            try:
                fn(max(0.0, min(1.0, own)))
            except Exception:           # noqa: BLE001  # nosec B110
                pass


def _symbol_for(qgis_layer, color: str | None, style: dict):
    """A single symbol matching a LAYER's geometry kind, coloured and sized from `style`."""
    return _symbol_of(qgis_layer.geometryType(), color, style)


def _symbol_of(geometry_type, color: str | None, style: dict):
    """A single symbol of the given geometry kind, coloured and sized from `style`.

    Split out from `_symbol_for` for the vector-TILE renderer, which has no layer to ask: it takes
    a geometry type directly. Everything below — marker shape, point radius, line width and dash,
    fill opacity, data-defined size — has to behave identically for tiles and for features, so
    there is one body and two ways in rather than two bodies that drift.
    """
    symbol = QgsSymbol.defaultSymbol(geometry_type)
    if symbol is None:
        return None
    if color:
        symbol.setColor(QColor(color))
    layer0 = symbol.symbolLayer(0) if symbol.symbolLayerCount() else None
    if layer0 is None:
        return symbol

    # Size FROM A FIELD, which is independent of colour: a layer can be graduated by one column
    # and sized by another, and applying only the colours dropped half the symbology on the floor.
    # A fixed size is set below; this overrides it with an expression when the style has one.
    _apply_data_defined_size(symbol, layer0, style)

    if isinstance(layer0, QgsSimpleMarkerSymbolLayer):
        shape = _MARKERS.get((style.get("marker") or "circle").lower())
        if shape:
            try:
                layer0.setShape(QgsSimpleMarkerSymbolLayer.decodeShape(shape)[0])
            except Exception:           # noqa: BLE001 - shape names shift between QGIS versions  # nosec B110 - intentional: a cosmetic failure must not take down the layer
                pass
        _use_points(symbol, layer0)
        # ALWAYS set the size — never leave QGIS's default number standing under a unit we just
        # changed. QGIS's default marker is 2.0 in MILLIMETRES; switching the unit to points turns
        # the same 2.0 into 0.7 mm, a third of the size, which is how a styleless point layer came
        # out as dots too small to see next to the same layer in a browser. The fallback is the
        # portal's own default (`circle-radius: 5` in mapStyle.js, portal_generator and portal.js
        # alike), so a layer with no saved radius matches the web instead of matching nothing.
        #
        # GeoDeploy's radius is in CSS PIXELS and QGIS's size is a DIAMETER, hence the doubling.
        symbol.setSize(float(style.get("radius") or DEFAULT_POINT_RADIUS) * 2 * CSS_PX_TO_POINTS)
        outline = style.get("outline_color")
        if outline == "none":
            layer0.setStrokeStyle(enum(Qt, "PenStyle", "NoPen"))
        else:
            # A WHITE ring by default, because that is what the map draws — and because QGIS's own
            # default is a dark grey outline, which on a small marker covers the fill completely
            # and turns every point black whatever colour the style asked for.
            layer0.setStrokeColor(QColor(outline or DEFAULT_MARKER_OUTLINE))
            # radius (CSS px) x ratio = the stroke in CSS px, then into points like every other size.
            _set_stroke_width(layer0, _outline_px(style) * CSS_PX_TO_POINTS)
        _apply_marker_placement(symbol, layer0, style)
    elif isinstance(layer0, QgsSimpleLineSymbolLayer):
        _use_points(symbol, layer0)
        # Always set, defaulted to the map's `line-width: 2` — QGIS's own default is 0.26 mm, a
        # hairline, so an unstyled line came out far thinner than the portal draws it.
        layer0.setWidth(float(style.get("line_width") or DEFAULT_LINE_WIDTH) * CSS_PX_TO_POINTS)
        _apply_line_decoration(layer0, style)
    elif isinstance(layer0, QgsSimpleFillSymbolLayer):
        outline = style.get("outline_color")
        if outline == "none":
            layer0.setStrokeStyle(enum(Qt, "PenStyle", "NoPen"))
        else:
            # The map's default outline, not QGIS's — `fill-outline-color: #1d4ed8` in
            # portal_generator when the style names none.
            layer0.setStrokeColor(QColor(outline or DEFAULT_FILL_OUTLINE))
            # AND ITS WIDTH. On a POLYGON `outline_width` is a width in CSS pixels — the same unit a
            # line uses, because it is one — where on a marker the same key is a RATIO of the
            # radius. The two are never read by the same branch. 1 px is what a MapLibre fill's own
            # edge draws, so an unset width is a hairline here too.
            _use_points(symbol, layer0)
            _set_stroke_width(layer0,
                              _number(style.get("outline_width"), DEFAULT_POLYGON_OUTLINE)
                              * CSS_PX_TO_POINTS)
        # ALWAYS, and defaulted — the same mistake the point radius made. A polygon style rarely
        # carries `fill_opacity`, and the map fills the gap with 0.45: every portal draws polygons
        # translucent. Applying it only when present meant QGIS drew them SOLID, so a layer that is
        # a soft wash in the browser arrived as a flat block of colour hiding everything under it.
        symbol.setOpacity(float(style.get("fill_opacity", DEFAULT_FILL_OPACITY)))
    return symbol


def _label(lo, hi) -> str:
    """The same wording the portal legend uses, so the two read alike."""
    def num(v):
        f = float(v)
        return str(int(f)) if f == int(f) and abs(f) < 1e15 else f"{f:,.2f}".rstrip("0").rstrip(".")
    if lo is None and hi is None:
        return "all"
    if lo is None:
        return f"< {num(hi)}"
    if hi is None:
        return f"≥ {num(lo)}"
    return f"{num(lo)} – {num(hi)}"


def style_from_legend(legend: dict) -> dict:
    """Rebuild a style dict from the PUBLIC legend endpoint.

    Anonymous browsing is the plugin's headline promise, and styling it used to be impossible: the
    style lives on `default_style`, which only the authenticated layer endpoints return, so every
    public layer arrived in QGIS unstyled. `/legend` is public and is what the portal itself draws
    from, so it is the right source — and since it now carries the raw `min`/`max`/`value` next to
    each formatted label, nothing has to be parsed back out of a display string.
    """
    if not legend:
        return {}
    entries = legend.get("entries") or []
    mode = legend.get("color_mode") or "single"
    style = {}
    size = legend.get("size") or {}
    if size.get("field") and size.get("stops"):
        style["size_mode"] = "proportional"
        style["size_field"] = size["field"]
        style["size_stops"] = size["stops"]

    if mode == "graduated":
        classes = [{"min": e.get("min"), "max": e.get("max"), "color": e.get("color")}
                   for e in entries if not e.get("other")]
        if classes:
            style.update(color_mode="graduated", color_field=legend.get("field"), classes=classes)
            return style
    elif mode == "categorized":
        cats = [{"value": e.get("value"), "color": e.get("color")}
                for e in entries if not e.get("other")]
        other = next((e for e in entries if e.get("other")), None)
        if cats:
            style.update(color_mode="categorized", color_field=legend.get("field"),
                         categories=cats)
            if other:
                style["other_color"] = other.get("color")
            return style

    # Single symbol — the legend carries one swatch built from the layer's own colour.
    if entries:
        style["color"] = entries[0].get("color")
    return style


def raster_style_from_legend(legend: dict) -> dict:
    """A RASTER style from the public legend endpoint — the raster twin of `style_from_legend`.

    The two legends answer different questions and have different shapes, and for a long time only
    the vector one had a reader: a raster's legend was handed to `style_from_legend`, which looked
    for `entries[0].color` and returned `{"color": "#…"}` — a VECTOR key, meaningless to a raster
    renderer. So a public raster (whose listing row carries no `default_style`) had no way to arrive
    coloured, and the colours it did carry described nothing.

    `rescale` comes back from the API as a PAIR OF NUMBERS and is stored as the string TiTiler
    wants; that conversion happens here so that everything downstream sees one spelling.
    """
    if not legend:
        return {}
    style = {}
    colormap = (legend.get("colormap") or "").strip()
    if colormap:
        style["colormap"] = colormap
        if legend.get("colormap_reverse"):
            style["colormap_reverse"] = True
    classes = legend.get("color_classes")
    if not classes and not legend.get("ramp", True):
        # A classified raster reports its classes as legend ENTRIES; `color_classes` is the same
        # list under its own name, and older instances send only the entries.
        classes = [{"value": e.get("value"), "color": e.get("color")}
                   for e in (legend.get("entries") or [])
                   if e.get("value") is not None and e.get("color")]
    if classes:
        style["color_classes"] = [c for c in classes if isinstance(c, dict)]
    rescale = _rescale_text(legend.get("rescale"))
    if rescale:
        style["rescale"] = rescale
    bands = [int(b) for b in (legend.get("bidx") or []) if isinstance(b, (int, float))]
    if bands:
        style["bidx"] = bands
    algorithm = (legend.get("algorithm") or "").strip()
    if algorithm:
        style["algorithm"] = algorithm
        zfactor = legend.get("zfactor")
        if _finite(zfactor) and float(zfactor) > 0:
            style["zfactor"] = float(zfactor)
    return style


def raster_style_of(stored) -> dict:
    """The raster style inside a stored `default_style`, whichever shape it was written in.

    A vector's default style NESTS the visual part — `{opacity, style: {...}, popup_fields}` — while
    a raster's is written FLAT: `{opacity, colormap, rescale, …}`. The API's own legend route carries
    the same warning, having been written against the vector shape and therefore reporting every
    field as null on a live instance. Reading only `["style"]` here had the same effect one layer
    up: a raster with a stored colormap looked like a raster with no style at all.

    Both shapes are read, nested first, and the envelope keys are removed — `opacity`, which is
    applied separately and is not part of the colouring, plus the two a nested style wraps itself in.

    EVERYTHING ELSE IS KEPT, including keys this plugin has never heard of. An allowlist would have
    been tidier and would silently drop the next raster property GeoDeploy grows — contour
    `increment` and `thickness` are already planned — so the layer would lose it the first time
    anybody opened it in QGIS. A key we cannot translate still travels, untouched.
    """
    if not isinstance(stored, dict):
        return {}
    inner = stored.get("style")
    source = inner if isinstance(inner, dict) and inner else stored
    return {k: v for k, v in source.items()
            if v is not None and k not in ("opacity", "style", "popup_fields")}


def _rescale_text(rescale) -> str | None:
    """A stretch in any of its spellings, as the one `"min,max"` string that is stored.

    It arrives as `[0.0, 2.0]` from the legend route, as `"0,2"` from a stored style, and as two
    numbers from a QGIS renderer. Comparing those as written reports a change nobody made, which is
    exactly what `comparable_style` exists to prevent — so there is one canonical form and this is
    where everything is put into it.
    """
    if rescale is None:
        return None
    if isinstance(rescale, str):
        parts = [p.strip() for p in rescale.split(",")]
    elif isinstance(rescale, (list, tuple)):
        parts = list(rescale)
    else:
        return None
    if len(parts) != 2:
        return None
    try:
        lo, hi = float(parts[0]), float(parts[1])
    except (TypeError, ValueError):
        return None
    if not (_finite(lo) and _finite(hi)) or hi <= lo:
        return None
    return "{0},{1}".format(_trim(lo), _trim(hi))


def _log(message: str, level: str = "warning") -> None:
    """Into QGIS's Log Messages panel, under our own tab — the place a user can be pointed to.

    `level="info"` is for things that are EXPLANATIONS rather than faults. Three identical WARNINGs
    for three rasters that simply cannot carry band styling read as three failures; they are one fact
    about how a portal draws a raster, and the dialog already says what to do about it.
    """
    try:
        from qgis.core import Qgis, QgsMessageLog
        QgsMessageLog.logMessage(message, "GeoDeploy",
                                 enum(Qgis, "MessageLevel", "Info") if level == "info" else enum(Qgis, "MessageLevel", "Warning"))
    except Exception:                   # noqa: BLE001 - logging must never raise  # nosec B110 - intentional: a cosmetic failure must not take down the layer
        pass


#: Where a vector-tile layer remembers the name of the layer INSIDE its tiles. The tile renderer
#: needs it for every style it builds, and only the code that read the TileJSON knows it — so it is
#: written once, on the layer, rather than threaded through every caller that might restyle later.
P_SOURCE_LAYER = "geodeploy/source_layer"

#: And the layer's GEOMETRY, recorded for the way back out. A tile layer cannot be asked what it
#: holds, and the answer decides which renderer entry is the user's: QGIS's own vector-tile editor
#: keeps one UNFILTERED style per geometry type, so reading "the first one" read a polygon symbol on
#: a point layer — the wrong colour, identical to the old default, so an edit registered as no change.
P_GEOMETRY = "geodeploy/geometry"

#: And a raster's COLORMAP NAME, recorded when one is applied — because QGIS does not keep it.
#: `QgsColorRampShader` holds a ramp OBJECT, and only a ColorBrewer or cpt-city ramp can be asked
#: what scheme it came from; the matplotlib ramps (viridis, magma, plasma, …) are plain gradients
#: with no name at all. So a raster styled `colormap: "viridis"` used to come back with no colormap
#: and the layer silently lost its palette on every push, keeping only the stretch.
#:
#: The name alone would be a lie the moment somebody chose a different ramp in QGIS, so the ramp's
#: COLOURS are recorded beside it and the name is only believed while they still match — forwards,
#: or exactly reversed, which is how flipping the ramp in QGIS travels back as `colormap_reverse`.
P_COLORMAP = "geodeploy/colormap"
P_COLORMAP_SIG = "geodeploy/colormap_stops"

#: And the raster styling QGIS HAS NO RENDERER FOR. `hillshade` maps onto `QgsHillshadeRenderer`;
#: `contours` maps onto nothing — QGIS makes contours with a processing algorithm that outputs a
#: VECTOR layer, not with a raster renderer — and the same will be true of the next server-side
#: algorithm. Such a raster is drawn here with its stretch alone, which is honest, but reading THAT
#: back would report a plain stretch and the merge would then delete the algorithm: opening a
#: contour layer in QGIS and pushing it back would silently turn it into a grey raster.
#:
#: So the untranslatable part is recorded, with a signature of what QGIS was actually given, and
#: handed back unchanged while that still matches. A user who genuinely restyles the layer — picks
#: a palette, classifies it — changes the renderer, the signature stops matching, and the edit is
#: reported as the real edit it is. Third use of the same device (`P_COLORMAP`, `P_EXTRUSION`),
#: which is what it looks like when a lossy container is being used honestly.
P_RASTER_ALGO = "geodeploy/raster_algorithm"
P_RASTER_ALGO_SIG = "geodeploy/raster_algorithm_sig"

#: The keys that belong to a server-side algorithm rather than to a renderer QGIS can build.
_ALGORITHM_KEYS = ("algorithm", "increment", "thickness", "minz", "maxz", "zfactor")


#: Every visual key, with the value the MAP supplies when a style omits it. Used only to COMPARE two
#: styles — see `comparable_style`.
_STYLE_DEFAULTS = {
    "color_mode": "single",
    "color": "#3b82f6",
    "radius": DEFAULT_POINT_RADIUS,
    "marker": "circle",
    "line_width": DEFAULT_LINE_WIDTH,
    "lineType": "solid",
    "fill_opacity": DEFAULT_FILL_OPACITY,
    "outline_color": "",                # geometry-dependent, so normalised below rather than here
    "outline_width": DEFAULT_MARKER_OUTLINE_RATIO,
    "size_mode": "fixed",
    # The catch-all colour a categorized layer gets whether or not anybody chose one: `_symbol_of`
    # writes QGIS's "Other" category in it and the reader reports it, so a categorized layer opened
    # and pushed straight back read as edited when the stored style simply had not named it.
    "other_color": DEFAULT_OTHER_COLOR,
    # The map's defaults, not QGIS's — `_apply_line_decoration` writes these when a style is silent,
    # so a line read back always names them and an unstated one must compare equal.
    "line_cap": DEFAULT_LINE_CAP,
    "line_join": DEFAULT_LINE_JOIN,
}


def comparable_style(style: dict | None, geometry: str | None = None) -> dict:
    """A style reduced to what a viewer would SEE, for comparing two of them.

    WHY A STORED STYLE AND A READ-BACK ONE CANNOT BE COMPARED DIRECTLY. QGIS has no concept of "unset":
    `_symbol_of` fills every gap with the map's own default — radius 5, a white marker stroke, 45% fill
    — so reading a symbol back always returns a COMPLETE style, while the stored one usually holds only
    the few keys somebody actually chose. Compared raw, opening a portal and pushing it straight back
    reported every layer as restyled; reported as "I changed only one style, but it says 3 were
    restyled". Filling both sides from the same table is what makes the comparison mean "looks
    different" instead of "is written differently".

    Keys that do not apply to a geometry are harmless: both sides get them identically.

    A RASTER style is a different shape and gets its own treatment — filling it with a vector's
    defaults would compare a colormap against a marker size.
    """
    if _is_raster_style(style):
        return _comparable_raster(style)
    merged = dict(_STYLE_DEFAULTS)
    # `outline_width` MEANS TWO THINGS, and the default differs with them: on a POINT it is a ratio
    # of the marker radius (0.28), on a POLYGON a width in CSS pixels (1, what a fill's own edge
    # draws). Without the geometry the two cannot be told apart, and a polygon read back at its
    # 1 px default would compare against a marker's 0.28 and report every polygon as restyled.
    # Folding them into one token instead would be wrong the other way: a marker ratio of 1 is a
    # solid RING, a real symbol somebody chose, and hiding that change would be worse.
    # `in`, not `startswith`: GeoDeploy's geometry strings are "Polygon" AND "MultiPolygon", and
    # the rest of the codebase tests them the same way (`"polygon" in geom` in portal_generator and
    # mapStyle.js). Anchoring the match silently missed every multipart layer.
    if "polygon" in str(geometry or "").lower():
        merged["outline_width"] = DEFAULT_POLYGON_OUTLINE
    merged.update({k: v for k, v in (style or {}).items() if v is not None})
    # An outline is stated as a colour or the word "none", and the DEFAULT differs by geometry — white
    # on a marker, #1d4ed8 on a fill. Either default reads as "the outline nobody chose", so both
    # collapse to one token rather than being compared as colours.
    if merged.get("outline_color") in ("", None, "#ffffff", DEFAULT_FILL_OUTLINE):
        merged["outline_color"] = "default"
    # A layer sized by a FIELD: the field and the two stops are what a viewer sees, and a stop read
    # back out of an expression carries float noise, so they are rounded like every other number.
    if merged.get("size_mode") != "proportional":
        merged.pop("size_field", None)
        merged.pop("size_stops", None)
    elif isinstance(merged.get("size_stops"), list):
        merged["size_stops"] = [[round(_number(a, 0), 3), round(_number(b, 0), 2)]
                                for a, b in (pair for pair in merged["size_stops"]
                                             if isinstance(pair, (list, tuple)) and len(pair) == 2)]
    for key in ("radius", "line_width", "fill_opacity", "outline_width"):
        try:
            merged[key] = round(float(merged[key]), 3)
        except (TypeError, ValueError):
            merged[key] = None
    # Colours differ only by case or shorthand surprisingly often (#FFF vs #ffffff); comparing them
    # as written would report a change nobody made.
    for key in ("color", "outline_color", "other_color"):
        if isinstance(merged.get(key), str):
            merged[key] = merged[key].strip().lower()
    # The class lists carry their own colours, and those matter — keep them, case-folding ONLY the
    # colour. A category's `value` is DATA: "Autochamber" and "autochamber" are different categories,
    # and folding them here would hide a real change and mislabel the map.
    for key in ("classes", "categories"):
        if isinstance(merged.get(key), list):
            merged[key] = [_comparable_class(item) for item in merged[key]
                           if isinstance(item, dict)]
    # DERIVED, not chosen: `classes_n` is `len(classes)`, and only one side bothers to write it.
    merged.pop("classes_n", None)
    # 3D, reduced to what is actually drawn — an extrusion switched off is the same map as none.
    extrusion = _comparable_extrusion(merged.pop("extrusion", None))
    if extrusion:
        merged["extrusion"] = extrusion
    # A CLASSIFIED layer's single colour is not drawn — the classes are. Keeping it in the comparison
    # made an untouched categorized layer look edited, because the read-back fills it from the
    # catch-all entry while the stored style never had one.
    if merged.get("color_mode") in ("graduated", "categorized"):
        merged.pop("color", None)
    # An outline that is switched OFF has no width to compare — and QGIS reports whatever width the
    # pen had before it was disabled, which is not a difference a viewer can see.
    if merged.get("outline_color") == "none":
        merged.pop("outline_width", None)
    return merged


#: Every key a RASTER style is made of. A style holding any of them is a raster's; none of them
#: appears in a vector style, so the two shapes can never be mistaken for one another.
#:
#: THIS LIST IS ALSO WHAT A RASTER READ-BACK CLEARS (see `merge_style`): QGIS shows one renderer at
#: a time, so a raster that came back as a colormap is no longer a hillshade. A key OUTSIDE the list
#: survives a push untouched, which is the safe default for anything this plugin has not met — so
#: when contour styling lands (`algorithm: "contours"` with `increment` and `thickness`), adding
#: those two names here is what makes switching AWAY from contours clear them too. Until then they
#: would linger harmlessly: TiTiler ignores them without the algorithm that reads them.
_RASTER_KEYS = ("colormap", "colormap_reverse", "rescale", "bidx", "color_classes",
                "algorithm", "zfactor", "increment", "thickness", "minz", "maxz")

#: TiTiler's own contour defaults, mirrored from `services/titiler`. Needed here only so that an
#: absent interval and an explicitly written 35 compare as the same map.
CONTOUR_INCREMENT = 35.0
CONTOUR_THICKNESS = 1


def _is_raster_style(style) -> bool:
    return isinstance(style, dict) and any(k in style for k in _RASTER_KEYS)


def _hex_rgba(color):
    """`#RGB` / `#RRGGBB` / `#RRGGBBAA` folded to one lower-case 8-digit spelling.

    An opaque colour is written both ways depending on who wrote it — QGIS reads back
    `#3b82f6ff`, a person types `#3B82F6` — and comparing those as strings reports an edit nobody
    made, on every class, on every push.
    """
    if not isinstance(color, str):
        return color
    text = color.strip().lower().lstrip("#")
    if len(text) == 3:
        text = "".join(c * 2 for c in text)
    if len(text) == 6:
        text += "ff"
    return "#" + text if len(text) == 8 else color.strip().lower()


def _comparable_raster(style: dict | None) -> dict:
    """A RASTER style reduced to what a viewer would see — the raster half of `comparable_style`.

    Two rules do the work, and both come from `services/titiler.get_tile_url`, which is what
    actually draws the thing: a key the tile URL would IGNORE cannot be a visible difference, and
    the several spellings of one value (a stretch as a string or a pair, a colour with or without
    its alpha, a palette named forwards or with matplotlib's `_r`) are all folded into one.

    Without this, opening a portal's raster and pushing it straight back reported it as restyled —
    the same phantom-edit problem the vector side already solved, one shape along.
    """
    style = style or {}
    # ANYTHING THIS DOES NOT KNOW ABOUT IS CARRIED THROUGH, not dropped. A key it cannot classify
    # might well be drawn — contour `increment` and `thickness` are already planned — and dropping
    # it here would make a real change to one invisible to every comparison in the plugin.
    out = {k: v for k, v in style.items() if k not in _RASTER_KEYS and v is not None}
    bands = _bands_of(style)
    if bands and bands != [1]:
        # BAND 1 IS WHAT "NO BAND" MEANS. QGIS has no concept of unset here either: a renderer is
        # always ON a band, so a style that named none reads back as `bidx: [1]` and every raster
        # opened from a portal reported itself as restyled. The two draw the same picture — TiTiler
        # given no band renders the first — so they are not a difference a viewer can see.
        # (A raster with more than three bands defaults to an RGB composite instead, and that case
        # states its bands explicitly at both ends, so it is unaffected.)
        out["bidx"] = bands
    algorithm = (style.get("algorithm") or "").strip().lower()
    if algorithm:
        # ANY algorithm replaces the colouring — `get_tile_url` skips the colormap entirely when one
        # is set — but only a HILLSHADE drops the stretch, because it comes back as finished 0–255
        # relief and stretching that saturates every pixel to one value. Stated as two separate
        # rules rather than one, so the contours algorithm (which does take a stretch) is right the
        # day it arrives instead of quietly losing its range.
        out["algorithm"] = algorithm
        if algorithm == "hillshade":
            try:
                out["zfactor"] = round(float(style.get("zfactor") or 1.0), 6)
            except (TypeError, ValueError):
                out["zfactor"] = 1.0
            return out
        rescale = _rescale_text(style.get("rescale"))
        if rescale:
            out["rescale"] = rescale
        # THE ALGORITHM'S OWN PARAMETERS. `_RASTER_KEYS` holds them so a change of algorithm clears
        # them, which also meant they were filtered out of the comparison above — so changing a
        # contour interval from 5 m to 25 m, which is the most visible edit a contour map has, was
        # reported as "unchanged". Defaulted, because an absent value draws as TiTiler's default and
        # `5` written explicitly is the same map as nothing written at all.
        for key, default in (("increment", CONTOUR_INCREMENT), ("thickness", CONTOUR_THICKNESS)):
            value = _number(style.get(key), default)
            out[key] = round(value, 6) if key == "increment" else int(value)
        for key in ("minz", "maxz"):
            if style.get(key) is not None:
                out[key] = round(_number(style.get(key), 0.0), 6)
        return out
    rescale = _rescale_text(style.get("rescale"))
    if rescale:
        out["rescale"] = rescale
    if len(bands) == 3:
        return out                      # an RGB composite; a colormap is ignored for one
    classes = [c for c in (style.get("color_classes") or []) if isinstance(c, dict)]
    if classes:
        # An explicit colour per value beats a named ramp in the tile URL, so the ramp is not drawn
        # and a stale one left beside it is not a difference. NEITHER IS THE STRETCH: the mapping is
        # matched on raw pixel values and `get_tile_url` therefore omits `rescale` entirely — a
        # stretch would remap the very values the classes are keyed on, which is what once left a
        # three-class mask drawing only one of them.
        out.pop("rescale", None)
        out["color_classes"] = [_comparable_raster_class(c) for c in classes]
        if style.get("colormap_reverse"):
            out["colormap_reverse"] = True
        return out
    name, reverse = _colormap_of(style)
    if name:
        out["colormap"] = name.lower()
        if reverse:
            out["colormap_reverse"] = True
    return out


def _comparable_raster_class(item: dict) -> dict:
    """One class of a classified raster, reduced to what a viewer would see.

    The LABEL is what a legend prints, so it is compared — but QGIS labels a class with its own
    value when nothing else is given, and a raster whose classes were never named would otherwise
    come back carrying `label: "3"` where the stored style had none and report as edited. Absent and
    "the value as text" are the same legend, so they fold together.

    The text itself is DATA, like a category value: "Water" and "water" are different labels and
    case-folding them would hide a real edit and mislabel the map.
    """
    out = {"value": item.get("value"), "color": _hex_rgba(item.get("color"))}
    label = item.get("label")
    label = "" if label is None else str(label).strip()
    if label and label != str(item.get("value")):
        out["label"] = label
    return out


def _comparable_extrusion(ex):
    """A 3D block reduced to what a viewer would see, or None when nothing is extruded.

    The two rules are the same ones the 2D side uses, applied to `services/symbology.is_extruded`:
    an extrusion that is switched OFF draws exactly like no extrusion at all, and a height driven by
    a COLUMN means the fixed `height` beside it is not drawn — keeping either in the comparison
    would report an edit that changes nothing on the map.
    """
    if not isinstance(ex, dict) or not ex.get("enabled"):
        return None
    if not (ex.get("field") or ex.get("height")):
        return None                     # enabled with no height is drawn flat — the same as off
    out = {"enabled": True}
    field = str(ex.get("field") or "").strip()
    if field:
        out["field"] = field
        out["scale"] = round(_number(ex.get("scale"), 1.0), 6)
    else:
        out["height"] = round(_number(ex.get("height"), 0.0), 6)
    base = ex.get("base")
    if isinstance(base, str) and base.strip():
        out["base"] = base.strip()      # a FIELD name, which is data and not case-folded
    elif _number(base, 0.0):
        out["base"] = round(_number(base, 0.0), 6)
    if ex.get("color"):
        out["color"] = str(ex["color"]).strip().lower()
    opacity = round(_number(ex.get("opacity"), 1.0), 3)
    if opacity != 1.0:
        out["opacity"] = opacity
    if ex.get("radius") not in (None, ""):
        # POINTS only: the footprint the tile server buffers a point into. A polygon has area
        # already, so nothing writes one for it and nothing reads one back.
        out["radius"] = round(_number(ex.get("radius"), DEFAULT_PILLAR_RADIUS_M), 6)
    return out


def _comparable_class(item: dict) -> dict:
    """One class or category, with its colour case-folded and its BOUNDS as floats.

    A break written as `10` by a dialog and read back as `10.0` from QGIS is the same break; compared
    as written it is a change. The category `value` is left exactly as it is — it is data, and "10"
    and 10 really are different categories.
    """
    out = {}
    for key, value in item.items():
        if key == "color" and isinstance(value, str):
            out[key] = value.strip().lower()
        elif key in ("min", "max") and value is not None:
            out[key] = _number(value, value)
        else:
            out[key] = value
    return out


def merge_style(stored: dict | None, read_back: dict | None) -> dict:
    """`read_back` laid over `stored`, so properties QGIS cannot express are not DELETED by a push.

    A GeoDeploy style holds more than QGIS can draw: 3D extrusion, raw MapLibre paint from a GeoLibre
    import, popup fields. Replacing the whole style with what QGIS could read would silently drop all
    of it — the same mistake as pushing `{}` over a raster's colormap, one level down. So a push
    UPDATES the visual keys it understands and leaves the rest alone.

    A change of `color_mode` still has to clear the previous mode's leftovers, or a layer switched
    from graduated to single would keep a `classes` list that nothing draws and everything compares.
    """
    base = dict(stored or {})
    fresh = {k: v for k, v in (read_back or {}).items() if v is not None}
    if not fresh:
        return base
    if _is_raster_style(fresh):
        # A RASTER READ-BACK IS THE WHOLE COLOURING, not an update to part of it. QGIS shows one
        # renderer at a time, so a raster that came back as a hillshade is not also a colormap, and
        # a paletted one is not also a stretch — leaving either behind would publish a raster that
        # does not look like the QGIS the user was looking at when they pushed it. Keys outside this
        # list (opacity, anything a future GeoDeploy adds) are still left alone.
        for key in _RASTER_KEYS:
            if key not in fresh:
                base.pop(key, None)
        base.update(fresh)
        return base
    if fresh.get("color_mode") and fresh["color_mode"] != base.get("color_mode"):
        for key in ("classes", "classes_n", "categories", "other_color", "color_field"):
            base.pop(key, None)
    if fresh.get("size_mode") == "fixed" or (
            "size_mode" in fresh and fresh["size_mode"] != "proportional"):
        for key in ("size_field", "size_stops"):
            base.pop(key, None)
    if isinstance(fresh.get("extrusion"), dict):
        # EXTRUSION MERGES KEY BY KEY, not wholesale. QGIS reads back only the part of a 3D block it
        # can hold — a cylinder has a length, not a column — so replacing the whole dict with what
        # was read would delete the field, the scale and the opacity that QGIS never saw. The same
        # reasoning as `merge_style` itself, one level down.
        merged_ex = dict(base.get("extrusion") or {})
        fresh_ex = {k: v for k, v in fresh["extrusion"].items() if v is not None}
        # …but a change of height SOURCE still has to clear the other one, or a layer switched from
        # a column to a fixed height keeps the column, and `extrusion_paint` prefers the column.
        if fresh_ex.get("field"):
            merged_ex.pop("height", None)
        elif "height" in fresh_ex:
            merged_ex.pop("field", None)
            merged_ex.pop("scale", None)
        merged_ex.update(fresh_ex)
        fresh = dict(fresh, extrusion=merged_ex)
    base.update(fresh)
    return base


def apply(qgis_layer, style: dict, row: dict | None = None) -> bool:
    """Style ANY GeoDeploy layer — feature or vector tile — the way GeoDeploy draws it.

    The one entry point every caller should use. A vector-TILE layer needs a completely different
    renderer from a feature layer, and callers kept forgetting: the portal-group path handed tile
    layers to `apply_to_qgis`, which silently did nothing, so a portal opened as a group came in
    unstyled while the same layer added on its own came in styled. Deciding here, once, from the
    layer's actual type is the only version of this that cannot rot.
    """
    if not QGIS or not style:
        return False
    try:
        from qgis.core import QgsRasterLayer, QgsVectorTileLayer
        is_tiles = isinstance(qgis_layer, QgsVectorTileLayer)
        is_raster = isinstance(qgis_layer, QgsRasterLayer)
    except ImportError:                 # pragma: no cover - older QGIS has no vector tiles
        is_tiles, is_raster = False, False
    if is_raster:
        # A RASTER IS A THIRD RENDERER, not a variant of the vector one. It arrives here because the
        # caller should not have to know: the same "add this layer with its styling" call covers a
        # GeoTIFF, a feature layer and a tile pyramid, and every time that decision was left to a
        # caller one of them was forgotten. Server-rendered raster TILES have nothing to style — QGIS
        # holds them as colour — and `raster_to_qgis` declines them rather than pretending.
        return raster_to_qgis(qgis_layer, style)
    if is_tiles:
        # 3D CANNOT BE DRAWN ON TILES, and silence about that is how it reads as broken: the layer
        # arrives with the right colours, QGIS's 3D view shows it flat, and nothing anywhere says
        # why. A `QgsVectorLayer3DRenderer` needs a FEATURE layer — the extrusion is not lost (it
        # is still stored, and a push from here will not remove it), it simply has no renderer to
        # live on until the layer is opened from its data.
        if is_extruded(style):
            _log("{0} is drawn from vector tiles, which QGIS cannot extrude — the 3D view will "
                 "show it flat. Open it from its DATA to see and edit the 3D: pick “Editable — "
                 "each layer from its data” in Source before opening the portal, or select the "
                 "layer and use “Restyle this layer…”. Its 3D styling is unchanged either way."
                 .format(qgis_layer.name() if hasattr(qgis_layer, "name") else "This layer"),
                 level="info")
        source_layer = qgis_layer.customProperty(P_SOURCE_LAYER) or None
        return apply_to_vector_tiles(qgis_layer, row or {}, source_layer, style)
    return apply_to_qgis(qgis_layer, style)


def apply_to_qgis(qgis_layer, style: dict) -> bool:
    """Render `qgis_layer` the way GeoDeploy renders it. True when a renderer was set.

    `style` is the inner style dict (what `geodeploy.styles.parse` reads).
    """
    if not QGIS or not style:
        return False
    from geodeploy import parse_style
    model = parse_style(style)

    # 3D FIRST, and separately: it is a second renderer hung beside the 2D one, not a variant of it,
    # so a layer that is both extruded and graduated needs both set. Doing it here rather than in
    # every caller is the same reasoning as `apply` dispatching on layer type — every path that
    # styles a feature layer gets it without having to remember.
    apply_3d(qgis_layer, style)

    # The LAYER's own scale range, restored before any renderer: it is a property of the layer, not
    # of its symbology, so it applies whatever branch below ends up drawing it. The SUBSET string is
    # deliberately NOT restored — it changes which features the layer HAS, not how they are drawn,
    # and silently filtering somebody's data on open is a different thing from styling it.
    _apply_layer_scope(qgis_layer, style)

    # LABELS, and a style with none turns labelling OFF rather than leaving it standing — switching
    # labels off in GeoDeploy and reopening has to actually switch them off. Same rule as `apply_3d`.
    try:
        try:                            # a package, inside QGIS
            from . import labels as _labels
        except ImportError:             # exec'd standalone by the test harness
            import labels as _labels
        _labels.to_qgis(qgis_layer, style)
    except ImportError:                 # pragma: no cover - labels.py is optional
        pass

    # 2.5D BEFORE EVERYTHING: it replaces the whole renderer, and it is only ever attempted for a
    # style that CAME from 2.5D (`extrusion.qgis25d`). A plain extrusion authored in GeoDeploy stays
    # a real 3D renderer here, because that is what it is.
    try:
        try:                            # a package, inside QGIS
            from . import qgis25d as _25d
        except ImportError:             # exec'd standalone by the test harness
            import qgis25d as _25d
        if _25d.carried(style):
            # The flat symbol first: `convertFromRenderer` wraps whatever the layer is wearing, so
            # the roof and walls inherit the colours set here.
            symbol = _symbol_for(qgis_layer, style.get("color"), style)
            if symbol is not None:
                qgis_layer.setRenderer(QgsSingleSymbolRenderer(symbol))
            if _25d.to_qgis(qgis_layer, style):
                return True
    except ImportError:                 # pragma: no cover - qgis25d.py is optional
        pass

    # RULES BEFORE CLASSES. A style carrying `rules` is rule-based, and its `color_mode` is only the
    # fallback shape for viewers that know nothing about rules — reading that first would draw a
    # rule-based layer as one flat symbol and then report it as edited on the way back.
    try:
        try:                            # a package, inside QGIS
            from . import rules as _rules
        except ImportError:             # exec'd standalone by the test harness
            import rules as _rules
        if _rules.is_rule_based(style) and _rules.to_qgis(qgis_layer, style):
            return True
    except ImportError:                 # pragma: no cover - rules.py is optional
        pass

    try:
        if model.mode == "graduated" and model.field and model.classes:
            ranges = []
            for cls in model.classes:
                symbol = _symbol_for(qgis_layer, cls.get("color"), style)
                if symbol is None:
                    continue
                # Open outer edges in GeoDeploy mean "everything below/above". QGIS wants numbers,
                # so they become ±inf — which keeps features outside the sampled range DRAWN, the
                # behaviour the open edges exist for.
                lo = float(cls["min"]) if cls.get("min") is not None else float("-inf")
                hi = float(cls["max"]) if cls.get("max") is not None else float("inf")
                ranges.append(QgsRendererRange(lo, hi, symbol, _label(cls.get("min"), cls.get("max"))))
            if ranges:
                qgis_layer.setRenderer(QgsGraduatedSymbolRenderer(model.field, ranges))
                qgis_layer.triggerRepaint()
                return True

        if model.mode == "categorized" and model.field and model.categories:
            cats = []
            for cat in model.categories:
                symbol = _symbol_for(qgis_layer, cat.get("color"), style)
                if symbol is not None:
                    cats.append(QgsRendererCategory(cat.get("value"), symbol, str(cat.get("value"))))
            if cats:
                other = _symbol_for(qgis_layer, model.other_color or "#9ca3af", style)
                if other is not None:
                    cats.append(QgsRendererCategory(None, other, "Other"))
                qgis_layer.setRenderer(QgsCategorizedSymbolRenderer(model.field, cats))
                qgis_layer.triggerRepaint()
                return True

        symbol = _symbol_for(qgis_layer, model.color, style)
        if symbol is not None:
            qgis_layer.setRenderer(QgsSingleSymbolRenderer(symbol))
            qgis_layer.triggerRepaint()
            return True
    except Exception as exc:            # noqa: BLE001 - a style must never stop a layer loading
        # Never stop the layer, but never disappear either. Swallowed silently, a failure here is
        # indistinguishable from a layer that simply has no style — which is exactly how "no saved
        # symbology ever displays" arrived with nothing in the console to act on.
        _log("Could not apply the saved style: {0}: {1}".format(type(exc).__name__, exc))
        return False
    _log("The saved style produced no renderer (mode={0!r}, field={1!r}).".format(
        model.mode, model.field))
    return False


#: How many colour stops a continuous ramp is written with. Enough that an interpolated shader is
#: visually smooth, few enough that the list stays readable in QGIS's own dialog.
_RAMP_STOPS = 32

#: GeoDeploy/TiTiler colormap names → the ramp QGIS ships under a different spelling. The two
#: catalogues are both matplotlib + ColorBrewer underneath, so most names match once case is
#: ignored; these are the ones that genuinely differ.
_COLORMAP_ALIASES = {
    "gray": "Greys", "grey": "Greys",
    "rdylgn": "RdYlGn", "rdylbu": "RdYlBu", "rdbu": "RdBu", "rdgy": "RdGy",
    "brbg": "BrBG", "piyg": "PiYG", "prgn": "PRGn", "puor": "PuOr",
    "ylgn": "YlGn", "ylgnbu": "YlGnBu", "ylorbr": "YlOrBr", "ylorrd": "YlOrRd",
    "bugn": "BuGn", "bupu": "BuPu", "gnbu": "GnBu", "orrd": "OrRd",
    "pubu": "PuBu", "pubugn": "PuBuGn", "purd": "PuRd",
}

#: Ramps GeoDeploy has and QGIS does not ship at all, as `(position, #rrggbb)` stops. `terrain` is
#: matplotlib's, which is where TiTiler's comes from — so a DEM styled `terrain` in the portal opens
#: in QGIS as the same colours rather than falling back to grey.
_BUILTIN_RAMPS = {
    "terrain": [(0.00, "#333399"), (0.15, "#0099ff"), (0.25, "#00cc66"),
                (0.50, "#ffff99"), (0.75, "#805c54"), (1.00, "#ffffff")],
}


def _qcolor(value):
    """A QColor from `#rgb`, `#rrggbb` or `#rrggbbaa`. None when it is not a colour.

    Qt CANNOT be handed the 8-digit form directly: `QColor("#rrggbbaa")` reads eight hex digits as
    **#AARRGGBB**, so a half-transparent red arrives as an opaque near-black — and GeoDeploy writes
    alpha LAST (see `services/titiler._rgba`). The channels are therefore split here rather than
    left to a constructor whose convention is the opposite of ours.
    """
    if not QGIS or not isinstance(value, str):
        return None
    text = value.strip().lstrip("#")
    if len(text) == 3:
        text = "".join(c * 2 for c in text)
    if len(text) not in (6, 8):
        return None
    try:
        parts = [int(text[i:i + 2], 16) for i in range(0, len(text), 2)]
    except ValueError:
        return None
    return QColor(parts[0], parts[1], parts[2], parts[3] if len(parts) == 4 else 255)


def _rescale_pair(style: dict):
    """`(min, max)` floats for the stretch, or `(None, None)`."""
    text = _rescale_text((style or {}).get("rescale"))
    if not text:
        return (None, None)
    lo, hi = text.split(",")
    return (float(lo), float(hi))


def _bands_of(style: dict) -> list:
    return [int(b) for b in ((style or {}).get("bidx") or [])
            if isinstance(b, (int, float)) and int(b) > 0]


def _colormap_of(style: dict):
    """`(name, reverse)` for a style's colormap, with matplotlib's `_r` suffix unpacked.

    GeoDeploy stores the palette a person chose and its direction as two separate facts — that is
    what keeps "Viridis" recognisable in the UI after somebody flips it. A name that arrived with
    the suffix already on it (from an import, or from a hand-written style) is normalised to the
    same two facts here, so nothing downstream has to look for the suffix a second time.
    """
    name = ((style or {}).get("colormap") or "").strip()
    reverse = bool((style or {}).get("colormap_reverse"))
    if name.lower().endswith("_r"):
        name, reverse = name[:-2], not reverse
    return (name, reverse)


def _ramp_for(name: str):
    """`(ramp, stops)` for a GeoDeploy colormap name — the QGIS ramp and the colours it produces.

    Looked up in QGIS's own style library first, so a user who then opens the ramp dialog sees the
    palette they know by name rather than an anonymous gradient. `stops` is always the FORWARD
    colours, whatever direction the style asks for: it is the evidence for the name, and a reversed
    ramp has to be recognisable as the same palette running backwards rather than as a different
    one — that is what lets flipping the ramp in QGIS travel back as `colormap_reverse`.
    """
    if not (QGIS and QGIS_RASTER) or not name:
        return (None, [])
    wanted = name.strip().lower()
    ramp = None
    try:
        style_db = QgsStyle.defaultStyle()
        names = list(style_db.colorRampNames() or [])
        match = _COLORMAP_ALIASES.get(wanted)
        if match not in names:
            match = next((n for n in names if n.lower() == wanted), match)
        if match in names:
            ramp = style_db.colorRamp(match)
    except Exception as exc:            # noqa: BLE001 - a style library we cannot read is not fatal
        _log("Could not read QGIS's colour ramps ({0}); building {1!r} from its own stops."
             .format(exc, name), level="info")
    if ramp is None and wanted in _BUILTIN_RAMPS:
        ramp = _gradient_ramp(_BUILTIN_RAMPS[wanted])
    if ramp is None:
        _log("QGIS has no colour ramp called {0!r}, so this raster keeps its stretch and is drawn "
             "with the default ramp. Its colours in GeoDeploy are unchanged.".format(name))
        return (None, [])
    return (ramp, _ramp_colors(ramp))


def _gradient_ramp(stops):
    """A `QgsGradientColorRamp` from `[(position, "#rrggbb")]`."""
    colors = [(pos, _qcolor(hexcode)) for pos, hexcode in stops]
    colors = [(pos, c) for pos, c in colors if c is not None]
    if len(colors) < 2:
        return None
    ramp = QgsGradientColorRamp(colors[0][1], colors[-1][1])
    middle = [QgsGradientStop(pos, c) for pos, c in colors[1:-1]]
    if middle:
        ramp.setStops(middle)
    return ramp


def _ramp_colors(ramp) -> list:
    """The ramp sampled at `_RAMP_STOPS` even positions, as `#rrggbb` strings."""
    out = []
    for i in range(_RAMP_STOPS):
        try:
            colour = ramp.color(i / float(_RAMP_STOPS - 1))
        except Exception:               # noqa: BLE001 - an unsamplable ramp has no signature
            return []
        out.append(colour.name().lower())
    return out


def raster_to_qgis(qgis_layer, style: dict) -> bool:
    """Render a RASTER the way GeoDeploy renders it. True when a renderer was set.

    THE MISSING HALF. `raster_from_qgis` has read QGIS raster renderers for a while, but nothing
    wrote them, so the only way to restyle a raster was to open the GeoTIFF — which arrived in
    QGIS's own grey default, with GeoDeploy's colours nowhere in it. "Prefer the real data" was
    therefore a TRADE: values or appearance, never both, and restyling meant starting over. With
    this, the COG opens looking like the portal AND with its real bands, which is the only version
    of the round trip that is worth calling one.

    The renderer is chosen in the same ORDER `services/titiler.get_tile_url` chooses one, because
    the two have to agree about which key wins when a style carries several: hillshade first, then
    an explicit colour-per-value, then an RGB composite, then a named ramp, then a plain stretch.
    Disagreeing here would mean QGIS showing a colormap the portal ignores.
    """
    if not (QGIS and QGIS_RASTER) or not style or qgis_layer is None:
        return False
    provider = qgis_layer.dataProvider() if hasattr(qgis_layer, "dataProvider") else None
    if provider is None:
        return False
    # SERVER-RENDERED TILES ARE ALREADY COLOURED, and colouring them again is worse than doing
    # nothing: WMTS/XYZ tiles reach QGIS as RGBA, so a band renderer laid over them would draw the
    # red channel of a finished picture through a colour ramp. The GDAL provider is the one holding
    # real values, and that is the only one there is anything to style.
    try:
        if (qgis_layer.providerType() or "").lower() != "gdal":
            return False
    except Exception:                   # noqa: BLE001 - a layer that cannot say is left alone
        return False
    bands = _bands_of(style)
    band = bands[0] if bands else 1
    lo, hi = _rescale_pair(style)
    colormap, reverse = _colormap_of(style)
    try:
        renderer, colormap_sig = None, None
        if (style.get("algorithm") or "").strip() == "hillshade":
            # Azimuth and altitude are GeoDeploy's fixed 315/45 — `raster_from_qgis` says so on the
            # way out, and writing anything else here would make a pushed hillshade drift on every
            # round trip.
            renderer = QgsHillshadeRenderer(provider, band, 315.0, 45.0)
            try:
                z = float(style.get("zfactor") or 1.0)
                if _finite(z) and z > 0:
                    renderer.setZFactor(z)
            except (TypeError, ValueError):
                pass

        elif style.get("color_classes"):
            classes = _paletted_classes(style.get("color_classes"), reverse)
            if classes:
                renderer = QgsPalettedRasterRenderer(provider, band, classes)

        elif len(bands) == 3:
            renderer = QgsMultiBandColorRenderer(provider, bands[0], bands[1], bands[2])
            if lo is not None:
                for setter in ("setRedContrastEnhancement", "setGreenContrastEnhancement",
                               "setBlueContrastEnhancement"):
                    # A FRESH ENHANCEMENT PER BAND. QGIS takes ownership of the object it is given,
                    # so handing the same one to all three is a double-free waiting to happen.
                    getattr(renderer, setter)(_enhancement(provider, bands[0], lo, hi))

        elif colormap:
            ramp, colormap_sig = _ramp_for(colormap)
            if ramp is not None:
                low, high = (lo, hi) if lo is not None else _default_range(qgis_layer, band)
                renderer = _pseudocolor(provider, band, ramp, low, high, reverse)

        if renderer is None and (lo is not None or bands):
            # NO COLOURING TO APPLY, BUT A STRETCH WORTH KEEPING. Non-8-bit data drawn against
            # QGIS's guess is the difference between a visible raster and a black rectangle, and the
            # band choice is the difference between the layer's data and some other band of it.
            renderer = QgsSingleBandGrayRenderer(provider, band)
            if lo is not None:
                renderer.setContrastEnhancement(_enhancement(provider, band, lo, hi))
        if renderer is None:
            return False

        qgis_layer.setRenderer(renderer)
        # Recorded AFTER the renderer is in place: the name is only meaningful next to the colours
        # it produced, so the two are written together or not at all.
        _record_colormap(qgis_layer, colormap if colormap_sig else None, colormap_sig)
        # And the server-side algorithm QGIS could not build a renderer for — see `P_RASTER_ALGO`.
        # Hillshade is excluded because it DID become a renderer and reads back on its own.
        untranslatable = {k: style[k] for k in _ALGORITHM_KEYS
                          if style.get(k) is not None and algorithm_of(style) != "hillshade"}
        _record_algorithm(qgis_layer, untranslatable if algorithm_of(style) not in ("", "hillshade")
                          else None, _algorithm_signature(renderer))
        qgis_layer.triggerRepaint()
        return True
    except Exception as exc:            # noqa: BLE001 - a style must never stop a layer loading
        _log("Could not apply the saved raster style: {0}: {1}".format(type(exc).__name__, exc))
        return False


def _paletted_classes(color_classes, reverse: bool = False):
    """`[QgsPalettedRasterRenderer.Class]` from GeoDeploy's colour-per-value list.

    Reversal re-pairs the COLOURS with the values in the opposite order, exactly as
    `services/titiler._explicit_colormap` does — the values keep their places and the palette runs
    the other way. Doing it differently here would draw a classified raster in QGIS with class 3's
    colour on class 7.
    """
    entries = [c for c in (color_classes or []) if isinstance(c, dict)]
    colours = [c.get("color") for c in entries]
    if reverse:
        colours = colours[::-1]
    classes = []
    for entry, colour in zip(entries, colours):
        qcolor = _qcolor(colour)
        try:
            value = int(entry.get("value"))
        except (TypeError, ValueError):
            continue
        if qcolor is None:
            continue
        classes.append(QgsPalettedRasterRenderer.Class(
            value, qcolor, str(entry.get("label") or value)))
    return classes


def _enhancement(provider, band, lo, hi):
    """A min/max stretch QGIS will apply to `band`."""
    enhancement = QgsContrastEnhancement(provider.dataType(band))
    enhancement.setContrastEnhancementAlgorithm(
        enum(QgsContrastEnhancement, "ContrastEnhancementAlgorithm", "StretchToMinimumMaximum"), True)
    enhancement.setMinimumValue(lo)
    enhancement.setMaximumValue(hi)
    return enhancement


def _pseudocolor(provider, band, ramp, lo, hi, reverse: bool):
    """A single-band pseudocolour renderer over `ramp`, stretched to `lo`–`hi`.

    The colour items are written out here rather than left to `classifyColorRamp`, whose argument
    list has changed across QGIS versions — and because the items ARE the round trip: they are what
    `raster_from_qgis` reads back to recognise the ramp.
    """
    if lo is None or hi is None:
        lo, hi = 0.0, 1.0               # the caller resolves the real range; see `_default_range`
    # THE RAMP IS NOT HANDED TO THE SHADER'S CONSTRUCTOR, deliberately. `QgsColorRampShader` takes
    # OWNERSHIP of a ramp given that way, and `setSourceColorRamp` below deletes whatever it is
    # already holding — so passing the same object to both would leave this function sampling a ramp
    # C++ had just freed. It is sampled here, and the shader is given a clone of its own.
    shader_fn = QgsColorRampShader(lo, hi)
    for setter, value in (("setColorRampType", enum(QgsColorRampShader, "Type", "Interpolated")),
                          ("setClassificationMode", enum(QgsColorRampShader, "ClassificationMode", "Continuous"))):
        if hasattr(shader_fn, setter):
            getattr(shader_fn, setter)(value)
    span = (hi - lo) or 1.0
    items = []
    for i in range(_RAMP_STOPS):
        fraction = i / float(_RAMP_STOPS - 1)
        colour = ramp.color(1.0 - fraction if reverse else fraction)
        value = lo + fraction * span
        items.append(QgsColorRampShader.ColorRampItem(value, colour, _trim(value)))
    shader_fn.setColorRampItemList(items)
    # The SOURCE ramp as well as the items, so QGIS's dialog offers the palette itself — a user who
    # opens Symbology sees "Viridis" and can re-classify from it, not a list of frozen stops.
    if hasattr(shader_fn, "setSourceColorRamp"):
        try:
            source = ramp.clone() if hasattr(ramp, "clone") else ramp
            if reverse and hasattr(source, "invert"):
                source.invert()
            shader_fn.setSourceColorRamp(source)
        except Exception:               # noqa: BLE001 - the items already carry the colours  # nosec B110 - intentional: a cosmetic failure must not take down the layer
            pass
    shader = QgsRasterShader()
    shader.setRasterShaderFunction(shader_fn)
    renderer = QgsSingleBandPseudoColorRenderer(provider, band, shader)
    for setter, value in (("setClassificationMin", lo), ("setClassificationMax", hi)):
        if hasattr(renderer, setter):
            getattr(renderer, setter)(value)
    return renderer


def _default_range(qgis_layer, band):
    """`(min, max)` for a colormap whose style carries no stretch — WITHOUT reading the raster.

    A colormap needs a range to spread itself over, and the obvious way to get one is to ask the
    provider for band statistics. That is also the one thing in this file that could make adding a
    layer SLOW: statistics on a remote COG mean range requests over the network, and on a large one
    they are not quick — a styling nicety would have become a stall on every add.

    QGIS has already done the work. Opening a raster builds a default renderer with a contrast
    enhancement over its own sampled range, so the answer is sitting on the layer, free. Only if
    that is somehow absent is the provider asked, and then over a bounded SAMPLE rather than the
    whole raster.
    """
    existing = qgis_layer.renderer() if hasattr(qgis_layer, "renderer") else None
    if existing is not None:
        getter = getattr(existing, "contrastEnhancement", None)
        if callable(getter):
            lo, hi = _enhancement_range(getter())
            if lo is not None:
                return (float(lo), float(hi))
        lo = getattr(existing, "classificationMin", None)
        hi = getattr(existing, "classificationMax", None)
        if callable(lo) and callable(hi):
            lo, hi = lo(), hi()
            if _finite(lo) and _finite(hi) and hi > lo:
                return (float(lo), float(hi))
    try:
        provider = qgis_layer.dataProvider()
        # SAMPLED, not exhaustive: 250k pixels is what QGIS's own renderer uses to decide a stretch,
        # and it is bounded whatever the raster's size.
        from qgis.core import QgsRasterBandStats, QgsRectangle
        stats = provider.bandStatistics(band, enum(QgsRasterBandStats, "Stats", "Min") | enum(QgsRasterBandStats, "Stats", "Max"),
                                        QgsRectangle(), 250000)
        if _finite(stats.minimumValue) and stats.maximumValue > stats.minimumValue:
            return (float(stats.minimumValue), float(stats.maximumValue))
    except Exception:                   # noqa: BLE001 - a range we cannot find is not an error  # nosec B110 - intentional: a cosmetic failure must not take down the layer
        pass
    return (0.0, 1.0)


def algorithm_of(style) -> str:
    """A style's server-side algorithm, lower-cased. `""` when it has none."""
    return ((style or {}).get("algorithm") or "").strip().lower()


def _algorithm_signature(renderer) -> str:
    """What QGIS was given for a style it could not really draw — the renderer type and its band."""
    if renderer is None:
        return ""
    band = None
    for getter in ("band", "grayBand"):
        fn = getattr(renderer, getter, None)
        if callable(fn):
            try:
                band = fn()
                break
            except Exception:           # noqa: BLE001 - try the other spelling  # nosec B112 - intentional: try the other spelling, do not fail the read
                continue
    return "{0}:{1}".format(type(renderer).__name__, band)


def _record_algorithm(qgis_layer, keys, signature) -> None:
    """Remember a server-side algorithm QGIS has no renderer for — see `P_RASTER_ALGO`."""
    if not hasattr(qgis_layer, "setCustomProperty"):
        return
    try:
        import json
        if keys:
            qgis_layer.setCustomProperty(P_RASTER_ALGO, json.dumps(keys, sort_keys=True, default=str))
            qgis_layer.setCustomProperty(P_RASTER_ALGO_SIG, signature or "")
        else:
            qgis_layer.setCustomProperty(P_RASTER_ALGO, "")
            qgis_layer.setCustomProperty(P_RASTER_ALGO_SIG, "")
    except Exception:                   # noqa: BLE001 - a note we cannot store is not an error  # nosec B110 - intentional: a cosmetic failure must not take down the layer
        pass


def _recorded_algorithm(qgis_layer, renderer) -> dict:
    """The recorded algorithm keys, if QGIS is still showing what it was given. `{}` otherwise."""
    if not hasattr(qgis_layer, "customProperty"):
        return {}
    try:
        import json
        recorded = qgis_layer.customProperty(P_RASTER_ALGO) or ""
        signature = qgis_layer.customProperty(P_RASTER_ALGO_SIG) or ""
        if not recorded:
            return {}
        if _algorithm_signature(renderer) != signature:
            # The user built a real renderer over it — a palette, a classification. That REPLACES
            # the algorithm, and saying so is the point of comparing rather than always restoring.
            _log("This raster's {0} styling was replaced by the renderer you chose in QGIS, so it "
                 "will no longer be drawn that way.".format(
                     json.loads(recorded).get("algorithm", "server-side")), level="info")
            return {}
        keys = json.loads(recorded)
        return keys if isinstance(keys, dict) else {}
    except Exception:                   # noqa: BLE001 - an unreadable note is simply absent
        return {}


def _record_colormap(qgis_layer, name, stops) -> None:
    """Remember which named colormap produced the current colours — see `P_COLORMAP`."""
    if not hasattr(qgis_layer, "setCustomProperty"):
        return
    try:
        if name and stops:
            qgis_layer.setCustomProperty(P_COLORMAP, str(name))
            qgis_layer.setCustomProperty(P_COLORMAP_SIG, ",".join(stops))
        else:
            # A renderer that is not a named ramp must not leave a stale name behind for the reader
            # to believe.
            qgis_layer.setCustomProperty(P_COLORMAP, "")
            qgis_layer.setCustomProperty(P_COLORMAP_SIG, "")
    except Exception:                   # noqa: BLE001 - a note we cannot store is not an error  # nosec B110 - intentional: a cosmetic failure must not take down the layer
        pass


# ── 3D: extrusion, both directions ───────────────────────────────────────────────────────────────
#
# GeoDeploy draws 3D as MapLibre `fill-extrusion`, from one style key:
#
#     extrusion {enabled, field, height, scale, base, color, opacity, radius}
#
# `field` × `scale` is the height when a column drives it, `height` when a number does. `base` is a
# number or another field. `radius` is METRES and applies to POINTS only: a point has no area, so
# `services/pillars` buffers it into a footprint server-side and the tiles that reach a viewer hold
# POLYGONS. That is why a 3D point layer's tiles say "polygon" while its source says "point".
#
# QGIS models the same thing as a 3D RENDERER hung beside the 2D one — `QgsPolygon3DSymbol` with an
# extrusion height, or `QgsPoint3DSymbol` shaped as a cylinder — which is why 3D needs a FEATURE
# layer: a vector-tile layer has no such renderer to read, and that is the honest reason the restyle
# path exists.
#
# UNITS ARE NOT CONVERTED, deliberately. GeoDeploy's heights and radii are metres; QGIS 3D measures
# in the project's map units. Those agree exactly in a projected CRS in metres and do not in a
# geographic one — and converting would need the project CRS, would be lossy in both directions, and
# would mean the number a user typed is not the number that comes back. So the number travels
# unchanged and the mismatch is stated rather than papered over.

#: The 3D classes are not in one module across versions — `qgis._3d` for most of 3.x, with parts
#: migrating to `qgis.core`. Probed rather than imported, so a QGIS that keeps them elsewhere loses
#: 3D and nothing else.
_3D_MODULES = ("qgis._3d", "qgis.core")
_3D_CACHE: dict = {}


def _qgis3d(name):
    """A 3D class by name, from wherever this QGIS keeps it. None when it has none."""
    if name in _3D_CACHE:
        return _3D_CACHE[name]
    found = None
    for module in _3D_MODULES:
        try:
            import importlib
            found = getattr(importlib.import_module(module), name, None)
        except ImportError:             # pragma: no cover - a QGIS built without 3D
            found = None
        if found is not None:
            break
    _3D_CACHE[name] = found
    return found


def _3d_enum(class_name, *candidates):
    """An enum member spelled any of `candidates`, on the class or on its nested `Property`/`Shape`.

    QGIS moved these twice: `QgsAbstract3DSymbol.PropertyExtrusionHeight` became
    `QgsAbstract3DSymbol.Property.ExtrusionHeight`, and `QgsPoint3DSymbol.Cylinder` became
    `Qgis.Point3DShape.Cylinder`. Both spellings are asked for rather than one being assumed,
    because guessing wrong here does not fail loudly — it silently applies no 3D at all.
    """
    holders = [_qgis3d(class_name)]
    holders += [getattr(holders[0], attr, None) for attr in ("Property", "Shape")
                if holders[0] is not None]
    for holder in holders:
        if holder is None:
            continue
        for candidate in candidates:
            value = getattr(holder, candidate, None)
            if value is not None:
                return value
    return None


#: Where a layer remembers the extrusion it was GIVEN, and what that looked like once QGIS held it.
#: Same device as `P_COLORMAP`, for the same reason: QGIS cannot express every GeoDeploy extrusion —
#: a point's height driven by a column has no equivalent in a cylinder's fixed length — so reading
#: the symbol back would report a fixed height and the merge would then DELETE the column. The
#: recorded spec is returned unchanged while the symbol still matches it, and only a real edit in
#: QGIS is read as one.
P_EXTRUSION = "geodeploy/extrusion"
P_EXTRUSION_SIG = "geodeploy/extrusion_sig"

#: `"field" * 2.5` — the height expression written for a column-driven extrusion, read back.
_HEIGHT_EXPRESSION = re.compile(
    r'^\s*"(?P<field>[^"]+)"\s*(?:\*\s*(?P<scale>-?[\d.eE+]+)\s*)?$')


def _extrusion_of(style: dict) -> dict:
    """The extrusion block of a style, as a dict. Empty when there is none."""
    ex = (style or {}).get("extrusion")
    return dict(ex) if isinstance(ex, dict) else {}


def is_extruded(style: dict) -> bool:
    """Whether this style asks for 3D — the same test `services/symbology.is_extruded` makes.

    Enabled ALONE is not enough: a layer with the box ticked and no height set draws flat, and
    treating it as 3D here would put a zero-height symbol on a layer the map draws in 2D.
    """
    ex = _extrusion_of(style)
    return bool(ex.get("enabled")) and bool(ex.get("field") or ex.get("height"))


def _height_expression(ex: dict):
    """`("field" * scale)` for a column-driven height, or None."""
    field = str(ex.get("field") or "").strip()
    if not field:
        return None
    scale = _number(ex.get("scale"), 1.0) or 1.0
    return '"{0}"'.format(field) if scale == 1.0 else '"{0}" * {1:g}'.format(field, scale)


def _set_data_defined(symbol, key, expression) -> bool:
    """Drive one 3D symbol property from an expression. False when this QGIS cannot."""
    if key is None:
        return False
    try:
        from qgis.core import QgsProperty
        properties = symbol.dataDefinedProperties()
        properties.setProperty(key, QgsProperty.fromExpression(expression))
        symbol.setDataDefinedProperties(properties)
        return True
    except Exception:                   # noqa: BLE001 - 3D is a bonus, never a blocker
        return False


def _data_defined_expression(symbol, key):
    """The expression driving a 3D symbol property, or None."""
    if key is None:
        return None
    try:
        prop = symbol.dataDefinedProperties().property(key)
        if prop is None or not prop.isActive():
            return None
        return prop.expressionString() or None
    except Exception:                   # noqa: BLE001 - a property we cannot read is absent
        return None


def _set_material(symbol, colour: str) -> None:
    """Colour a 3D symbol. QGIS renamed the setter, so both names are tried."""
    settings_cls = _qgis3d("QgsPhongMaterialSettings")
    qcolor = _qcolor(colour)
    if settings_cls is None or qcolor is None:
        return
    try:
        material = settings_cls()
        material.setDiffuse(qcolor)
        # An unlit 3D volume reads as a silhouette; QGIS's own default ambient is nearly black, so
        # a dark ambient under a bright diffuse turns every extrusion into a shadow of itself.
        if hasattr(material, "setAmbient"):
            material.setAmbient(QColor(int(qcolor.red() * 0.35), int(qcolor.green() * 0.35),
                                       int(qcolor.blue() * 0.35)))
        for setter in ("setMaterialSettings", "setMaterial"):
            fn = getattr(symbol, setter, None)
            if callable(fn):
                fn(material)
                return
    except Exception:                   # noqa: BLE001 - a colour is not worth losing the 3D over  # nosec B110 - intentional: a cosmetic failure must not take down the layer
        pass


def _material_color(symbol):
    """A 3D symbol's diffuse colour as `#rrggbb`, or None."""
    for getter in ("materialSettings", "material"):
        fn = getattr(symbol, getter, None)
        if not callable(fn):
            continue
        try:
            material = fn()
            diffuse = material.diffuse() if hasattr(material, "diffuse") else None
            if diffuse is not None:
                return diffuse.name().lower()
        except Exception:               # noqa: BLE001 - try the other spelling  # nosec B112 - intentional: try the other spelling, do not fail the read
            continue
    return None


def apply_3d(qgis_layer, style: dict, geometry: str | None = None) -> bool:
    """Give a FEATURE layer the 3D symbol its GeoDeploy style describes. True when one was set.

    Called from `apply_to_qgis`, so every path that styles a feature layer gets 3D without having
    to remember to ask — the same reasoning as `apply` dispatching on layer type.

    A style with no extrusion CLEARS any 3D renderer this plugin set, rather than leaving one
    standing: turning 3D off in GeoDeploy and reopening the layer has to actually turn it off, or
    the two disagree and the next push argues about which is right.
    """
    if not QGIS or qgis_layer is None:
        return False
    setter = getattr(qgis_layer, "setRenderer3D", None)
    if not callable(setter):
        return False
    ex = _extrusion_of(style)
    if not is_extruded(style):
        try:
            if qgis_layer.customProperty(P_EXTRUSION):
                setter(None)            # ours to clear; a renderer we never set is left alone
            _record_extrusion(qgis_layer, None, None)
        except Exception:               # noqa: BLE001 - never fail a style over the 3D it lacks  # nosec B110 - intentional: a cosmetic failure must not take down the layer
            pass
        return False

    renderer_cls = _qgis3d("QgsVectorLayer3DRenderer")
    if renderer_cls is None:
        _log("This QGIS has no 3D support, so the layer's extrusion was not applied — it is still "
             "stored, and pushing from here will not remove it.", level="info")
        return False

    kind = (geometry or _geometry_name(qgis_layer) or "polygon").lower()
    symbol = _point_3d(ex, style) if kind.startswith("point") else _polygon_3d(ex, style)
    if symbol is None:
        return False
    try:
        qgis_layer.setRenderer3D(renderer_cls(symbol))
    except Exception as exc:            # noqa: BLE001 - 3D must never stop a layer loading
        _log("Could not apply the 3D extrusion: {0}: {1}".format(type(exc).__name__, exc))
        return False
    # Recorded together: the spec asked for, and what QGIS actually ended up holding.
    _record_extrusion(qgis_layer, ex, _read_3d_symbol(symbol, kind))
    return True


def _polygon_3d(ex: dict, style: dict):
    """A `QgsPolygon3DSymbol` for an extruded polygon layer."""
    cls = _qgis3d("QgsPolygon3DSymbol")
    if cls is None:
        return None
    symbol = cls()
    # The fixed height either way: it is what the layer draws at when no column drives it, and the
    # fallback QGIS falls back TO when a data-defined expression cannot be evaluated for a feature.
    symbol.setExtrusionHeight(_number(ex.get("height"), 0.0))
    expression = _height_expression(ex)
    if expression and not _set_data_defined(symbol, _3d_property_key("extrusion"), expression):
        # DATA-DEFINED IS WHAT MAKES IT THE SAME MAP: MapLibre reads the column per feature, and one
        # averaged height would draw a city of identical blocks.
        _log("This QGIS cannot drive an extrusion height from a field, so {0!r} was drawn at a flat "
             "height. The field is still stored, and still drawn in GeoDeploy."
             .format(ex.get("field")))
    base = ex.get("base")
    if isinstance(base, str) and base.strip():
        _set_data_defined(symbol, _3d_property_key("height"),
                          _height_expression({"field": base, "scale": ex.get("scale")}))
    elif hasattr(symbol, "setHeight"):
        symbol.setHeight(_number(base, 0.0))
    _set_material(symbol, ex.get("color") or style.get("color") or DEFAULT_COLOR)
    return symbol


def _point_3d(ex: dict, style: dict):
    """A `QgsPoint3DSymbol` — a cylinder, matching the pillars the tile server generates.

    A CYLINDER because that is what `services/pillars` builds: the point is buffered into a round
    footprint and extruded, so a box here would be a different map. `radius` is the footprint the
    style names, in metres, and `length` the height.
    """
    cls = _qgis3d("QgsPoint3DSymbol")
    if cls is None:
        return None
    symbol = cls()
    shape = _3d_enum("QgsPoint3DSymbol", "Cylinder") or _3d_enum("Qgis", "Point3DShape")
    if shape is None:
        shape = _3d_enum("Qgis", "Cylinder")
    try:
        if shape is not None and hasattr(symbol, "setShape"):
            symbol.setShape(shape)
    except Exception:                   # noqa: BLE001 - the default shape still draws something  # nosec B110 - intentional: a cosmetic failure must not take down the layer
        pass
    height = _number(ex.get("height"), 0.0)
    expression = _height_expression(ex)
    if expression:
        # A cylinder's LENGTH is a shape property, not a data-defined one in most QGIS builds, so a
        # column-driven pillar cannot be drawn per-feature here. It is attempted anyway (newer
        # builds accept it) and the recorded spec is what travels back, so the column is never lost
        # to this limitation — see `P_EXTRUSION`.
        _set_data_defined(symbol, _3d_property_key("extrusion"), expression)
    if hasattr(symbol, "setShapeProperties"):
        try:
            symbol.setShapeProperties({"shape": "Cylinder",
                                       "radius": _number(ex.get("radius"), DEFAULT_PILLAR_RADIUS_M),
                                       "length": height})
        except Exception:               # noqa: BLE001 - shape properties differ between builds  # nosec B110 - intentional: a cosmetic failure must not take down the layer
            pass
    _set_material(symbol, ex.get("color") or style.get("color") or DEFAULT_COLOR)
    return symbol


def _3d_property_key(which: str):
    if which == "extrusion":
        return _3d_enum("QgsAbstract3DSymbol", "ExtrusionHeight", "PropertyExtrusionHeight")
    return _3d_enum("QgsAbstract3DSymbol", "Height", "PropertyHeight")


def _read_3d_symbol(symbol, kind: str) -> dict:
    """What a 3D symbol is CURRENTLY drawing, as GeoDeploy extrusion keys.

    One function, used twice: to record what QGIS ended up holding when a style was applied, and to
    read it again later. Using the same reader for both is what makes the comparison meaningful — a
    separate "signature" routine would eventually disagree with the reader and every layer would
    look edited.
    """
    out: dict = {}
    if symbol is None:
        return out
    expression = _data_defined_expression(symbol, _3d_property_key("extrusion"))
    match = _HEIGHT_EXPRESSION.match(expression or "")
    if match:
        out["field"] = match.group("field")
        scale = _number(match.group("scale"), 1.0)
        if scale != 1.0:
            out["scale"] = round(scale, 6)
    if kind.startswith("point"):
        properties = {}
        getter = getattr(symbol, "shapeProperties", None)
        if callable(getter):
            try:
                properties = dict(getter() or {})
            except Exception:           # noqa: BLE001 - unreadable shape properties are absent
                properties = {}
        height = _number(properties.get("length"), None)
        radius = _number(properties.get("radius"), None)
        if height is not None:
            out["height"] = round(height, 6)
        if radius is not None:
            out["radius"] = round(radius, 6)
    else:
        height = _number(getattr(symbol, "extrusionHeight", lambda: None)(), None)
        if height is not None:
            out["height"] = round(height, 6)
        base_expression = _data_defined_expression(symbol, _3d_property_key("height"))
        base_match = _HEIGHT_EXPRESSION.match(base_expression or "")
        if base_match:
            out["base"] = base_match.group("field")
        else:
            base = _number(getattr(symbol, "height", lambda: None)(), None)
            if base:
                out["base"] = round(base, 6)
    colour = _material_color(symbol)
    if colour:
        out["color"] = colour
    return out


def _record_extrusion(qgis_layer, spec, applied) -> None:
    """Remember the extrusion a layer was given, and how QGIS ended up holding it."""
    if not hasattr(qgis_layer, "setCustomProperty"):
        return
    try:
        import json
        if spec:
            qgis_layer.setCustomProperty(P_EXTRUSION, json.dumps(spec, sort_keys=True, default=str))
            qgis_layer.setCustomProperty(P_EXTRUSION_SIG,
                                         json.dumps(applied or {}, sort_keys=True, default=str))
        else:
            qgis_layer.setCustomProperty(P_EXTRUSION, "")
            qgis_layer.setCustomProperty(P_EXTRUSION_SIG, "")
    except Exception:                   # noqa: BLE001 - a note we cannot store is not an error  # nosec B110 - intentional: a cosmetic failure must not take down the layer
        pass


def extrusion_from_qgis(qgis_layer, geometry: str | None = None):
    """The `extrusion` block for a layer's current 3D renderer, or None when there is nothing to say.

    THREE ANSWERS, AND THE DIFFERENCE MATTERS:

    * **None** — this layer cannot carry the question. A vector-TILE layer has no 3D renderer, and
      a QGIS without 3D has none either; reporting "no extrusion" for those would DELETE a portal's
      3D on the next push, which is the same mistake as pushing an empty raster style over a
      colormap.
    * **`{"enabled": False}`** — a feature layer whose 3D was switched off in QGIS. That is a real
      edit and must travel.
    * **the block** — what it is drawing now, or the spec it was given if that is still what it
      holds. The recorded spec wins whenever the symbol still matches it, because QGIS cannot
      express every GeoDeploy extrusion and reading it back would quietly flatten a column-driven
      point pillar into a fixed height.
    """
    if not QGIS or qgis_layer is None:
        return None
    getter = getattr(qgis_layer, "renderer3D", None)
    if not callable(getter):
        return None                     # not a layer that can hold 3D — say nothing about it
    try:
        renderer = getter()
    except Exception:                   # noqa: BLE001 - a renderer we cannot read is unknown
        return None
    symbol = None
    if renderer is not None:
        symbol_getter = getattr(renderer, "symbol", None)
        symbol = symbol_getter() if callable(symbol_getter) else None
    if symbol is None:
        # Nothing 3D on the layer. Only worth reporting when this plugin PUT something there — for
        # any other layer, silence is the truthful answer rather than "the user removed it".
        try:
            if qgis_layer.customProperty(P_EXTRUSION):
                return {"enabled": False}
        except Exception:               # noqa: BLE001  # nosec B110 - intentional: a cosmetic failure must not take down the layer
            pass
        return None

    kind = (geometry or _geometry_name(qgis_layer) or "polygon").lower()
    current = _read_3d_symbol(symbol, kind)
    try:
        import json
        recorded = qgis_layer.customProperty(P_EXTRUSION) or ""
        signature = qgis_layer.customProperty(P_EXTRUSION_SIG) or ""
        if recorded and signature and json.dumps(current, sort_keys=True, default=str) == signature:
            spec = json.loads(recorded)
            if isinstance(spec, dict):
                return dict(spec, enabled=True)
    except Exception:                   # noqa: BLE001 - fall through to what QGIS is showing  # nosec B110 - intentional: a cosmetic failure must not take down the layer
        pass
    if not current:
        return None
    return dict(current, enabled=True)


def _geometry_name(qgis_layer):
    """"point" / "line" / "polygon" for a feature layer, or None.

    A vector-tile layer records its geometry on the layer (`P_GEOMETRY`) because it cannot be asked;
    a feature layer can be, and `geometryType()` is the enum QGIS answers with.
    """
    recorded = None
    try:
        recorded = qgis_layer.customProperty(P_GEOMETRY) or None
    except Exception:                   # noqa: BLE001
        recorded = None
    if recorded:
        return str(recorded).lower()
    try:
        from qgis.core import QgsWkbTypes
        return {enum(QgsWkbTypes, "GeometryType", "PointGeometry"): "point", enum(QgsWkbTypes, "GeometryType", "LineGeometry"): "line",
                enum(QgsWkbTypes, "GeometryType", "PolygonGeometry"): "polygon"}.get(qgis_layer.geometryType())
    except Exception:                   # noqa: BLE001 - not a feature layer
        return None


# ── QGIS → GeoDeploy ─────────────────────────────────────────────────────────────────────────────

def _hex(color) -> str:
    return color.name() if hasattr(color, "name") else str(color)


#: Must match `services/titiler.MAX_COLOR_CLASSES`. The mapping rides in the URL of every tile
#: request, so the ceiling is set by what a proxy accepts, not by taste — 128 classes is ~5 kB,
#: against nginx's default 8 kB request line.
MAX_COLOR_CLASSES = 128


def apply_to_vector_tiles(tile_layer, row: dict, source_layer: str | None,
                          style: dict | None = None) -> bool:
    """Draw a vector TILE layer with the layer's real symbology — classes and all.

    QGIS renders tiles through `QgsVectorTileBasicRenderer`, which takes a LIST of styles, each with
    its own filter expression. That is the same shape MapLibre's `step`/`match` expressions have, so
    a graduated or categorized layer translates directly: one style per class, filtered to that
    class's values. The earlier version only set a base colour and told the user to give up speed to
    get their symbology back — a bad trade, and an unnecessary one.

    Falls back to a single style when there is nothing to classify, and returns False only when even
    that is impossible.
    """
    if not QGIS:
        return False
    try:
        from qgis.core import (QgsVectorTileBasicRenderer, QgsVectorTileBasicRendererStyle,
                               QgsWkbTypes)
    except ImportError:                 # pragma: no cover - older QGIS
        return False

    style = style if style is not None else (
        (row.get("default_style") or {}).get("style")
        if isinstance(row.get("default_style"), dict) else {}) or {}

    # WHICH GEOMETRY, and never a guess. A tile renderer's style is bound to ONE geometry type, and
    # QGIS honours that binding literally: give a line layer a point style and it draws a marker at
    # every vertex — a road network arriving as a carpet of dots — and give a point layer a fill
    # style and it draws nothing at all. Defaulting the unknown case to "point" is what produced
    # both. When the geometry is genuinely unknown, every type gets a style instead, so whatever the
    # tiles hold is drawn with a symbol that suits it; a tile layer may legitimately carry more than
    # one geometry anyway.
    geom = (row.get("geometry_type") or "").lower()
    if "polygon" in geom:
        geometry_types = [enum(QgsWkbTypes, "GeometryType", "PolygonGeometry")]
    elif "line" in geom:
        geometry_types = [enum(QgsWkbTypes, "GeometryType", "LineGeometry")]
    elif "point" in geom:
        geometry_types = [enum(QgsWkbTypes, "GeometryType", "PointGeometry")]
    else:
        geometry_types = [enum(QgsWkbTypes, "GeometryType", "PolygonGeometry"), enum(QgsWkbTypes, "GeometryType", "LineGeometry"),
                          enum(QgsWkbTypes, "GeometryType", "PointGeometry")]
        if geom:
            _log("Unrecognised geometry {0!r}; styling every geometry type.".format(geom))

    model = None
    try:
        from geodeploy import parse_style
        model = parse_style(style) if style else None
    except Exception:                   # noqa: BLE001 - fall through to a single symbol
        model = None

    def _style(name, colour, expression):
        """One renderer entry per geometry type this layer may hold — usually exactly one."""
        out = []
        for geometry_type in geometry_types:
            # THE SAME symbol builder the feature path uses — marker shape, radius, line width,
            # dash, fill opacity and data-defined size all included. A second implementation here
            # is how the two surfaces would start drawing the same layer differently.
            #
            # PER GEOMETRY, and that matters when the layer's geometry is unknown: a row with no
            # `geometry_type` (GeoParquet layers are the ones that hit this) asks for all three,
            # and one that cannot be built used to abort the whole renderer through the outer
            # handler — so a line layer whose geometry the instance did not report arrived with no
            # styling because the POLYGON symbol failed. One geometry's failure now costs that
            # geometry only, and says so.
            try:
                symbol = _symbol_of(geometry_type, colour, style)
            except Exception as exc:    # noqa: BLE001 - the other geometries must still draw
                _log("Could not build the {0} symbol for this tile layer ({1}: {2}); its other "
                     "geometries are still styled.".format(geometry_type, type(exc).__name__, exc))
                continue
            if symbol is None:
                continue
            entry = QgsVectorTileBasicRendererStyle(
                name if len(geometry_types) == 1 else "{0}-{1}".format(name, geometry_type),
                source_layer or "", geometry_type)
            entry.setSymbol(symbol)
            entry.setEnabled(True)
            if expression:
                entry.setFilterExpression(expression)
            out.append(entry)
        return out

    styles = []
    try:
        if model is not None and model.mode == "graduated" and model.field and model.classes:
            for i, cls in enumerate(model.classes):
                # Open edges mean "everything below/above", exactly as they do on the map.
                lo, hi = cls.get("min"), cls.get("max")
                field = '"{0}"'.format(model.field)
                parts = []
                if lo is not None:
                    parts.append("{0} >= {1}".format(field, lo))
                if hi is not None:
                    # The LAST class keeps its upper bound inclusive, or the largest value in the
                    # data falls through every filter and disappears.
                    op = "<=" if i == len(model.classes) - 1 else "<"
                    parts.append("{0} {1} {2}".format(field, op, hi))
                styles.extend(_style("class-{0}".format(i), cls.get("color"),
                                     " AND ".join(parts) or None))
        elif model is not None and model.mode == "categorized" and model.field and model.categories:
            for i, cat in enumerate(model.categories):
                value = cat.get("value")
                literal = ("'" + str(value).replace("'", "''") + "'"
                           if not isinstance(value, (int, float)) else str(value))
                styles.extend(_style("cat-{0}".format(i), cat.get("color"),
                                     '"{0}" = {1}'.format(model.field, literal)))
            # Everything not listed, drawn in the same "other" colour the map uses. First in the
            # list = drawn underneath the named categories.
            styles[:0] = _style("other", model.other_color or "#9ca3af", None)
        if not styles:
            styles.extend(_style("geodeploy", (style or {}).get("color") or "#3b82f6", None))
        if not styles:
            return False

        renderer = QgsVectorTileBasicRenderer()
        renderer.setStyles(styles)
        tile_layer.setRenderer(renderer)
        tile_layer.triggerRepaint()
        return True
    except Exception as exc:            # noqa: BLE001 - never stop a layer loading over a style
        _log("Could not style the vector tiles: {0}".format(exc))
        return False


def raster_from_qgis(qgis_layer, colormaps=None) -> dict:
    """A GeoDeploy RASTER default style from a QGIS raster renderer.

    A different shape entirely from the vector one — `{colormap, rescale, bidx}`, not classes — so
    it is a separate function rather than a branch inside `from_qgis`. Sending the vector shape for
    a raster is what made "Send its styling too" silently do nothing for GeoTIFFs.

    What travels, in order of how faithfully it survives:

    * **`rescale`** — exact, and the part that matters most. Non-8-bit data renders BLACK on a tile
      server that assumes 0–255, so the min/max stretch is the difference between a visible layer
      and a black rectangle. QGIS knows the numbers; there is no reason to make the server guess
      them again.
    * **`bidx`** — exact. Which band, or which three for an RGB composite.
    * **`colormap`** — best effort. QGIS colour ramps and TiTiler colormaps are different
      catalogues that happen to share many names (viridis, magma, terrain, …), so the name is sent
      ONLY when the server confirms it has one by that name. `colormaps` is that list; without it
      no colormap is claimed, because a wrong one is worse than the default.
    """
    if not QGIS or qgis_layer is None:
        return {}
    renderer = qgis_layer.renderer() if hasattr(qgis_layer, "renderer") else None
    # A PICTURE, NOT A RASTER. A portal's raster arrives as server-rendered tiles, which QGIS models
    # as one band of RGBA — "Singleband color data" in the Symbology tab, with no render type to
    # choose and nothing to stretch. There is genuinely no styling to read back, so say so plainly:
    # silence here reads as "the push lost my changes", and pushing the empty result would REPLACE
    # the portal's colormap with nothing (see portals.plan_push, which now refuses to).
    if type(renderer).__name__ == "QgsSingleBandColorDataRenderer":
        _log("This raster is drawn from server-rendered tiles, so QGIS holds it as colour rather "
             "than values — QGIS shows 'Singleband color data' and offers nothing to change, and "
             "there is no band styling to read back. To restyle it, tick 'Prefer the real data over "
             "the styled view' and add it again: that opens the GeoTIFF, with its real bands.",
             level="info")
        return {}
    if renderer is None:
        return {}

    def with_algorithm(read: dict) -> dict:
        """`read`, plus any server-side algorithm QGIS has no renderer for — see `P_RASTER_ALGO`.

        Applied to every return path below, because the algorithm is orthogonal to the renderer
        that was built: a contour layer is drawn here as a plain stretch, and returning only that
        stretch would let the merge clear `algorithm` and turn the layer grey.
        """
        recorded = _recorded_algorithm(qgis_layer, renderer)
        return dict(read, **recorded) if recorded else read

    style = {}
    try:
        if isinstance(renderer, QgsMultiBandColorRenderer):
            bands = [renderer.redBand(), renderer.greenBand(), renderer.blueBand()]
            if all(isinstance(b, int) and b > 0 for b in bands):
                style["bidx"] = bands
            # One stretch for the composite: TiTiler takes a single rescale, and the red band's is
            # the closest honest single answer when the three differ.
            lo, hi = _enhancement_range(renderer.redContrastEnhancement())
            if lo is not None:
                style["rescale"] = "{0},{1}".format(_trim(lo), _trim(hi))
            return with_algorithm(style)

        if isinstance(renderer, QgsSingleBandGrayRenderer):
            band = renderer.grayBand()
            if isinstance(band, int) and band > 0:
                style["bidx"] = [band]
            lo, hi = _enhancement_range(renderer.contrastEnhancement())
            if lo is not None:
                style["rescale"] = "{0},{1}".format(_trim(lo), _trim(hi))
            return with_algorithm(style)

        if isinstance(renderer, QgsSingleBandPseudoColorRenderer):
            band = renderer.band()
            if isinstance(band, int) and band > 0:
                style["bidx"] = [band]
            lo, hi = renderer.classificationMin(), renderer.classificationMax()
            if _finite(lo) and _finite(hi) and hi > lo:
                style["rescale"] = "{0},{1}".format(_trim(lo), _trim(hi))
            # THE NAME WE APPLIED FIRST, while the colours on screen still back it up. It is the
            # instance's own spelling — GeoDeploy calls a grey ramp `gray` where QGIS calls it
            # `Greys` — so believing it keeps a round trip byte-identical where reading the ramp
            # object would rename the palette on every pass.
            recorded, recorded_reverse = _recorded_colormap(qgis_layer, renderer)
            if recorded:
                style["colormap"] = recorded
                if recorded_reverse:
                    style["colormap_reverse"] = True
                return with_algorithm(style)
            name, inverted = _ramp_name(renderer)
            if name and colormaps and name in colormaps:
                style["colormap"] = name
                if inverted:
                    # QGIS inverts a ramp in place; GeoDeploy keeps the palette's name and a
                    # separate flag, so the two agree about WHICH ramp while disagreeing about
                    # its direction only where that is recorded.
                    style["colormap_reverse"] = True
            else:
                # NO NAME TO SEND. A ramp built in the QGIS dialog is a plain gradient with no
                # scheme name at all — which is most of them — so "send the name" quietly sent
                # nothing and the raster arrived looking unstyled despite the styling being right
                # there. The shader knows its actual colour stops; send those instead.
                classes = _shader_classes(renderer)
                if classes:
                    style["color_classes"] = classes
                elif name:
                    _log("QGIS ramp {0!r} has no colormap of that name on the instance, and its "
                         "stops are not per-value — only the stretch was sent.".format(name))
                else:
                    _log("This raster's colour ramp has no name and no per-value stops, so only "
                         "the stretch travelled. GeoDeploy carries either a NAMED palette or a "
                         "colour per value; a custom continuous gradient is neither.")
            return with_algorithm(style)

        if isinstance(renderer, QgsHillshadeRenderer):
            # Exactly representable: GeoDeploy asks TiTiler for a hillshade of the same band, and
            # `zfactor` is the same vertical exaggeration QGIS calls Z factor. Azimuth and altitude
            # have no equivalent in the raster style, so a non-default sun position is announced
            # rather than dropped in silence.
            band = renderer.band()
            if isinstance(band, int) and band > 0:
                style["bidx"] = [band]
            style["algorithm"] = "hillshade"
            try:
                z = float(renderer.zFactor())
                if _finite(z) and z > 0:
                    style["zfactor"] = z
            except (TypeError, ValueError):
                pass
            try:
                az, alt = float(renderer.azimuth()), float(renderer.altitude())
                if abs(az - 315.0) > 0.5 or abs(alt - 45.0) > 0.5:
                    _log("This hillshade uses azimuth {0:g}/altitude {1:g}; GeoDeploy renders the "
                         "standard 315/45, so the shading will differ.".format(az, alt))
            except (TypeError, ValueError, AttributeError):
                pass
            return with_algorithm(style)

        if isinstance(renderer, QgsPalettedRasterRenderer):
            # A colour per pixel VALUE — land cover, soil types, any classification. A named
            # colormap cannot express this (interpolating between class 3 and class 4 is
            # meaningless), so GeoDeploy carries the mapping itself and TiTiler renders from it.
            try:
                band = renderer.band()
                if isinstance(band, int) and band > 0:
                    style["bidx"] = [band]
            except (TypeError, AttributeError):
                pass
            classes = []
            for cls in (renderer.classes() or []):
                try:
                    value = int(cls.value)
                except (TypeError, ValueError, AttributeError):
                    continue
                colour = getattr(cls, "color", None)
                if colour is None:
                    continue
                # Alpha travels: "no data" in a classification is usually a transparent class, and
                # dropping that would paint it over everything underneath.
                entry = {"value": value,
                         "color": "#{0:02x}{1:02x}{2:02x}{3:02x}".format(
                             colour.red(), colour.green(), colour.blue(), colour.alpha())}
                # AND THE LABEL. "Water" and "Trees" are the whole point of a classification, and
                # dropping them here meant a push replaced the stored classes with unlabelled ones —
                # the legend on the layer page and in every portal fell back to bare numbers. QGIS
                # labels a class with its own value when nothing else is given, so that case is
                # treated as no label rather than travelling as one.
                label = getattr(cls, "label", None)
                label = "" if label is None else str(label).strip()
                if label and label != str(value):
                    entry["label"] = label
                classes.append(entry)
            if len(classes) > MAX_COLOR_CLASSES:
                # Truncating a classification would silently mis-colour part of the map, so refuse
                # the colours and keep the band: a grey raster is obviously unstyled, where a
                # half-coloured one looks finished and is wrong.
                _log("This raster has {0} classes; GeoDeploy carries at most {1} because the "
                     "mapping travels in every tile request. The colours were not sent — the band "
                     "was.".format(len(classes), MAX_COLOR_CLASSES))
                return with_algorithm(style)
            if classes:
                style["color_classes"] = classes
            else:
                _log("This paletted raster exposed no readable classes; only its band was sent.")
            return with_algorithm(style)

        # Anything else — a renderer from a plugin, or a QGIS class we have not met. The band and the
        # stretch are still worth having even when the colouring cannot travel: the stretch is what
        # keeps non-8-bit data from rendering as a black rectangle, and it is the part users notice.
        bands = renderer.usesBands() if hasattr(renderer, "usesBands") else []
        bands = [b for b in (bands or []) if isinstance(b, int) and b > 0]
        if bands:
            style["bidx"] = bands[:3] if len(bands) >= 3 else bands[:1]
        lo, hi = (None, None)
        if hasattr(renderer, "contrastEnhancement"):
            lo, hi = _enhancement_range(renderer.contrastEnhancement())
        if lo is not None:
            style["rescale"] = "{0},{1}".format(_trim(lo), _trim(hi))
        # Name the class. "No GeoDeploy equivalent" without saying what it saw is the kind of
        # message that costs a round trip to diagnose.
        _log("Raster renderer {0} is not translatable{1}.".format(
            type(renderer).__name__,
            " — sent its bands and stretch" if style else ", and exposed no bands or stretch"))
        return with_algorithm(style)
    except Exception as exc:            # noqa: BLE001 - never block an upload over styling
        _log("Could not read this raster's symbology ({0}): {1}: {2}".format(
            type(renderer).__name__, type(exc).__name__, exc))
        return {}
    return style


def _shader_classes(renderer):
    """`[{value, color}]` from a pseudocolour shader's own stops, or None.

    QGIS lists the ramp as value/colour pairs whatever the ramp is made of, which is exactly the
    shape GeoDeploy stores. It only carries integer values, so a classification (land cover, soil,
    a DISCRETE or EXACT ramp) travels intact while a continuous float gradient cannot — that one is
    reported rather than rounded into something the map never showed.
    """
    try:
        shader = renderer.shader()
        fn = shader.rasterShaderFunction() if shader else None
        items = fn.colorRampItemList() if fn and hasattr(fn, "colorRampItemList") else []
    except Exception:                   # noqa: BLE001 - a shader we cannot read is not an error
        return None
    if not items:
        return None

    classes = []
    for item in items:
        value, colour = getattr(item, "value", None), getattr(item, "color", None)
        if value is None or colour is None or not _finite(value):
            continue
        # Integral only: TiTiler maps a colour to a pixel VALUE, so 3.7 has nothing to key on.
        if float(value) != int(float(value)):
            return None
        classes.append({"value": int(float(value)),
                        "color": "#{0:02x}{1:02x}{2:02x}{3:02x}".format(
                            colour.red(), colour.green(), colour.blue(), colour.alpha())})
    if not classes:
        return None
    if len(classes) > MAX_COLOR_CLASSES:
        _log("This ramp has {0} stops; GeoDeploy carries at most {1}, so only the stretch was "
             "sent.".format(len(classes), MAX_COLOR_CLASSES))
        return None
    return classes


def _finite(v) -> bool:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return False
    return f == f and f not in (float("inf"), float("-inf"))


def _trim(v) -> str:
    """A stretch is a URL parameter — keep it short and free of float noise."""
    f = float(v)
    return str(int(f)) if f == int(f) else repr(round(f, 6))


def _enhancement_range(enhancement):
    """`(min, max)` from a contrast enhancement, or `(None, None)` when it has no usable one."""
    if enhancement is None:
        return (None, None)
    lo, hi = enhancement.minimumValue(), enhancement.maximumValue()
    if _finite(lo) and _finite(hi) and hi > lo:
        return (lo, hi)
    return (None, None)


def _ramp_name(renderer):
    """`(name, inverted)` for the renderer's colour ramp — lower-cased, or `(None, False)`.

    QGIS reverses a ramp by INVERTING it in place, so the direction is a property of the ramp
    object rather than part of its name. GeoDeploy keeps the two apart, which is what lets the
    palette a user chose stay recognisable in the UI after they flip it.
    """
    try:
        shader = renderer.shader()
        fn = shader.rasterShaderFunction() if shader else None
        ramp = fn.sourceColorRamp() if fn and hasattr(fn, "sourceColorRamp") else None
        # `type()` is the ramp KIND ("gradient", "cpt-city", …); cpt-city ramps carry a real name.
        name = getattr(ramp, "schemeName", None)
        name = name() if callable(name) else None
        inverted = False
        for attr in ("isInverted", "inverted"):
            probe = getattr(ramp, attr, None)
            if callable(probe):
                inverted = bool(probe())
                break
        return ((name or "").strip().lower() or None, inverted)
    except Exception:                   # noqa: BLE001 - a missing ramp is not an error
        return (None, False)


def _shader_signature(renderer) -> list:
    """The colours a pseudocolour shader is currently drawing, lower-cased `#rrggbb`.

    Deliberately the COLOURS and not the values: a user who restretches a raster in QGIS keeps the
    same palette, and a palette that stopped being recognised every time somebody moved a slider
    would be worse than useless.
    """
    try:
        shader = renderer.shader()
        fn = shader.rasterShaderFunction() if shader else None
        items = fn.colorRampItemList() if fn and hasattr(fn, "colorRampItemList") else []
    except Exception:                   # noqa: BLE001 - a shader we cannot read has no signature
        return []
    out = []
    for item in items:
        colour = getattr(item, "color", None)
        if colour is None:
            return []
        out.append(colour.name().lower())
    return out


def _recorded_colormap(qgis_layer, renderer):
    """`(name, reverse)` for the colormap this plugin applied, if it is still on screen.

    QGIS keeps a ramp OBJECT, not the name of the palette it came from — see `P_COLORMAP` — so
    without this a raster styled `viridis` in GeoDeploy came back from QGIS with no colormap at all
    and the push kept only its stretch. The recorded name is believed exactly as long as the
    colours still match it, forwards or exactly backwards; anything else means the user chose a
    different ramp, and the honest answer is then "no name", not the last one we happen to remember.
    """
    if not hasattr(qgis_layer, "customProperty"):
        return (None, False)
    try:
        name = str(qgis_layer.customProperty(P_COLORMAP) or "").strip()
        recorded = [s for s in str(qgis_layer.customProperty(P_COLORMAP_SIG) or "").split(",") if s]
    except Exception:                   # noqa: BLE001 - an unreadable note is simply absent
        return (None, False)
    if not name or not recorded:
        return (None, False)
    current = _shader_signature(renderer)
    if not current:
        return (None, False)
    if current == recorded:
        return (name, False)
    if current == recorded[::-1]:
        # The same palette, flipped in QGIS's dialog. That is a real edit and it travels.
        return (name, True)
    _log("This raster's colour ramp is no longer the {0!r} it was opened with, and QGIS does not "
         "record what a gradient is called — so its stretch travelled but its colours did not. "
         "Pick a ramp GeoDeploy also has, or classify it to send exact colours.".format(name),
         level="info")
    return (None, False)


#: Filters `apply_to_vector_tiles` writes, read back. `"field" = 'value'` is a category; a pair of
#: comparisons on one field is a class. Anchored so a hand-written filter this cannot understand
#: falls through to a single symbol rather than being half-parsed into a wrong class.
_CAT_FILTER = re.compile(
    r"""^"(?P<field>[^"]+)"\s*=\s*(?P<value>'(?:[^']|'')*'|-?[0-9.]+)$""")
_RANGE_PART = re.compile(
    r"""^"(?P<field>[^"]+)"\s*(?P<op><=|<|>=|>)\s*(?P<value>-?[0-9.eE+]+)$""")


def _unquote(literal: str):
    """A filter literal back to a Python value, undoing the doubled quotes an escape needed."""
    if literal.startswith("'") and literal.endswith("'"):
        return literal[1:-1].replace("''", "'")
    try:
        number = float(literal)
    except ValueError:
        return literal
    return int(number) if number == int(number) else number


def _parse_range(expression: str):
    """`("field", lo, hi)` for a class filter, or None. Open edges come back as None."""
    parts = [p.strip() for p in re.split(r"\s+AND\s+", expression.strip(), flags=re.IGNORECASE)]
    field, lo, hi = None, None, None
    for part in parts:
        m = _RANGE_PART.match(part)
        if not m:
            return None
        if field is not None and m.group("field") != field:
            return None                 # two fields is not a class this plugin wrote
        field = m.group("field")
        value = float(m.group("value"))
        if m.group("op") in (">=", ">"):
            lo = value
        else:
            hi = value
    if field is None or (lo is None and hi is None):
        return None
    return field, lo, hi


def style_from_vector_tiles(tile_layer) -> dict:
    """The GeoDeploy style for a vector-TILE layer — the inverse of `apply_to_vector_tiles`.

    WHY THIS IS NEEDED AT ALL. A portal's vector layers open as tiles, because that is what draws
    fast and matches the portal. QGIS renders those through `QgsVectorTileBasicRenderer`, which is
    not a feature renderer — so `from_qgis`, which reads `QgsSingleSymbolRenderer` and friends, found
    nothing and returned `{}`. Restyling a layer in a portal group and pushing it therefore sent no
    style at all: the change looked applied in QGIS and never arrived.

    Each renderer entry carries a symbol and a FILTER, which is exactly how the classes were written
    out, so they read back the same way: `"k" = 'a'` is a category, `"pop" >= 100 AND "pop" < 1000`
    is a class. Anything this cannot recognise degrades to a single symbol in the first colour rather
    than being guessed at — an approximation of somebody's classification is worse than none.
    """
    if not QGIS or tile_layer is None:
        return {}
    renderer = tile_layer.renderer() if hasattr(tile_layer, "renderer") else None
    entries = list(renderer.styles()) if hasattr(renderer, "styles") else []
    if not entries:
        return {}

    # ONLY THE ENTRIES FOR THIS LAYER'S GEOMETRY.
    #
    # QGIS's own vector-tile symbology editor keeps one style per geometry type — Polygons, Lines,
    # Points — all UNFILTERED. Reading them all and keeping the first meant a POINT layer's colour
    # was read off the polygon entry: a colour the user had not touched, equal to the old default, so
    # editing a point layer and pushing it registered as "no change" and published nothing. The
    # geometry is recorded on the layer when the plugin builds it, precisely because the layer itself
    # cannot be asked.
    wanted = None
    try:
        recorded = (tile_layer.customProperty(P_GEOMETRY) or "")
        recorded = str(recorded).lower()
        from qgis.core import QgsWkbTypes
        if "polygon" in recorded:
            wanted = enum(QgsWkbTypes, "GeometryType", "PolygonGeometry")
        elif "line" in recorded:
            wanted = enum(QgsWkbTypes, "GeometryType", "LineGeometry")
        elif "point" in recorded:
            wanted = enum(QgsWkbTypes, "GeometryType", "PointGeometry")
    except Exception:                   # noqa: BLE001 - fall through to every entry
        wanted = None

    enabled = [e for e in entries if not hasattr(e, "isEnabled") or e.isEnabled()]
    if wanted is not None:
        matching = [e for e in enabled
                    if not hasattr(e, "geometryType") or e.geometryType() == wanted]
        if matching:
            enabled = matching
    elif len({e.geometryType() for e in enabled if hasattr(e, "geometryType")}) > 1:
        # No geometry recorded and the entries disagree: any choice would be a guess, and a guess
        # here silently sends somebody a colour they never picked. Say so and send nothing, which
        # `plan_push` turns into "keep what the portal already has".
        _log("This tile layer has styles for several geometry types and no recorded geometry, so "
             "which one is the layer's cannot be told — leaving its saved style alone.")
        return {}

    # One class may have been written once per geometry type (see `apply_to_vector_tiles`), so within
    # one geometry the FILTER identifies a class. Keep the first of each.
    seen, unique = set(), []
    for entry in enabled:
        expression = (entry.filterExpression() or "").strip()
        if expression in seen:
            continue
        seen.add(expression)
        unique.append((expression, entry))
    if not unique:
        return {}

    visual = _style_from_symbol(unique[0][1].symbol()) if unique[0][1].symbol() else {}

    categories, classes, field, other = [], [], None, None
    for expression, entry in unique:
        symbol = entry.symbol()
        colour = _hex(symbol.color()) if symbol is not None else None
        if not expression:
            other = colour              # the unfiltered entry is the catch-all
            continue
        cat = _CAT_FILTER.match(expression)
        if cat:
            if field not in (None, cat.group("field")):
                return dict(visual, color_mode="single")
            field = cat.group("field")
            categories.append({"value": _unquote(cat.group("value")), "color": colour})
            continue
        rng = _parse_range(expression)
        if rng:
            if field not in (None, rng[0]):
                return dict(visual, color_mode="single")
            field = rng[0]
            classes.append({"min": rng[1], "max": rng[2], "color": colour})
            continue
        # A filter nobody here wrote: do not pretend to understand it.
        return dict(visual, color_mode="single")

    # For a CLASSIFIED layer the top-level colour means nothing — the classes carry the colours, and
    # `visual` took its `color` from whichever entry happened to be first (the catch-all, or class 0).
    # Reporting that as the layer's colour is how an untouched categorized layer read as edited.
    shape_only = {k: v for k, v in visual.items() if k != "color"}
    if classes and not categories:
        classes.sort(key=lambda c: (c["min"] is not None, c["min"] if c["min"] is not None else 0))
        return dict(shape_only, color_mode="graduated", color_field=field,
                    classes=classes, classes_n=len(classes))
    if categories and not classes:
        style = dict(shape_only, color_mode="categorized", color_field=field,
                     categories=categories)
        if other:
            style["other_color"] = other
        return style
    return dict(visual, color_mode="single")


def _symbols_of(renderer) -> list:
    """Every symbol a renderer holds, however this QGIS wants to be asked. Never raises.

    `renderer.symbols(None)` — the one call this used to make — **raises `TypeError` on QGIS 4**,
    where the argument is typed `QgsRenderContext` and `None` no longer coerces to one. That
    exception was caught by `from_qgis`'s blanket handler and turned into `{}`, so on QGIS 4 every
    renderer outside the single/graduated/categorized trio uploaded **no styling at all**. Measured
    on a real project: four of sixteen layers, all of them rule-based, silently sent nothing.

    A real context works on 3 and 4 alike, so it is tried first. Rule-based renderers are also
    walked through their rule tree, because `symbols()` on some builds returns the rules' symbols
    and on others does not, and "the first rule's symbol" is a far better approximation than
    nothing at all.
    """
    if renderer is None:
        return []
    attempts = []
    try:
        from qgis.core import QgsRenderContext
        attempts.append(lambda: renderer.symbols(QgsRenderContext()))
    except ImportError:                 # pragma: no cover - very old QGIS
        pass
    attempts.append(lambda: renderer.symbols(None))
    attempts.append(lambda: [renderer.symbol()])
    for attempt in attempts:
        try:
            found = attempt()
        except Exception:               # noqa: BLE001 - try the next spelling  # nosec B112 - intentional: each attempt is one QGIS version's API
            continue
        found = [s for s in (found or []) if s is not None]
        if found:
            return found
    try:                                # a rule-based renderer keeps its symbols in the rules
        from qgis.core import QgsRuleBasedRenderer
        if isinstance(renderer, QgsRuleBasedRenderer):
            # CLONED, not borrowed: a rule's symbol belongs to the rule, and the rule list here is
            # a temporary. Returning the borrowed pointer would hand the caller memory that is
            # freed before it is read — the crash the graduated branch documents.
            return [r.symbol().clone() for r in renderer.rootRule().children()
                    if r.symbol() is not None]
    except Exception:                   # noqa: BLE001  # nosec B110 - a missing class is not an error here
        pass
    return []


def from_qgis(qgis_layer) -> dict:
    """The GeoDeploy style dict for a QGIS layer's current renderer.

    Returns `{}` when there is nothing worth sending — an empty style means "use the default",
    which is better than uploading an approximation nobody asked for.
    """
    if not QGIS or qgis_layer is None:
        return {}
    # A VECTOR-TILE LAYER IS NOT A FEATURE LAYER, and this is where forgetting that cost a whole
    # feature: everything below reads feature renderers, so a portal group's layers — which are all
    # tiles — returned {} and their restyling never reached the instance.
    try:
        from qgis.core import QgsVectorTileLayer
        if isinstance(qgis_layer, QgsVectorTileLayer):
            return style_from_vector_tiles(qgis_layer)
    except ImportError:                 # pragma: no cover - older QGIS has no vector tiles
        pass
    except Exception as exc:            # noqa: BLE001 - a tile read must degrade, never escape
        # ONLY `ImportError` used to be caught here, so anything the tile reader raised travelled
        # out of `from_qgis` and up through `save_style` / `upload_active`, which have no handler
        # of their own: one unreadable symbol took down the whole action with a traceback instead
        # of saving the styles it could read. Reading a style is never worth that.
        _log("Could not read the tile layer's styling: {0}: {1}".format(type(exc).__name__, exc))
        return {}
    renderer = qgis_layer.renderer() if hasattr(qgis_layer, "renderer") else None
    if renderer is None:
        return {}

    def with_scope(style: dict) -> dict:
        """The LAYER's own scale range and subset string, which belong to no single symbol.

        Both apply to everything the layer draws, so they sit at the top of the style and the
        renderers apply them to every render layer they emit (`portal_generator._scoped`). Read here
        rather than in each branch because they are true whatever the renderer is.
        """
        scoped = dict(style, **_layer_scope_of(qgis_layer))
        # LABELS TOO. They hang off the layer beside the renderer, so they are true whatever the
        # renderer is — including "No symbols", which is how a layer kept for its labels is drawn.
        try:
            try:                        # a package, inside QGIS
                from . import labels as _labels
            except ImportError:         # exec'd standalone by the test harness
                import labels as _labels
            block, notes = _labels.from_qgis(qgis_layer)
            for note in notes:
                _log("{0}: {1}".format(
                    qgis_layer.name() if hasattr(qgis_layer, "name") else "This layer", note),
                    level="info")
            if block:
                scoped["labels"] = block
        except ImportError:             # pragma: no cover - labels.py is optional
            pass
        except Exception as exc:        # noqa: BLE001 - a label must not cost the symbology
            _log("Could not read the labels ({0}: {1}).".format(type(exc).__name__, exc))
        return scoped

    def with_3d(style: dict) -> dict:
        """The 2D style plus whatever the layer's 3D renderer says — see `extrusion_from_qgis`.

        Added to every return path rather than to one of them, because a layer can be extruded AND
        graduated: 3D is a second renderer, and reading only the one the first branch happened to
        match is how half a style goes missing.
        """
        extrusion = extrusion_from_qgis(qgis_layer)
        return with_scope(dict(style, extrusion=extrusion) if extrusion is not None else style)

    def shape_of(symbol) -> dict:
        """The non-colour half of a symbol: width, dash, radius, marker, fill opacity, outline.

        A CLASSIFIED LAYER HAS A SHAPE TOO, and this is what the two class branches below used to
        throw away. They returned the classes and nothing else, so a categorized layer of DASHED
        lines uploaded with its categories intact and no `lineType` at all — reported exactly that
        way: "it appears with correct categories but doesn't have dashed lines". The same loss took
        line width, marker shape, point radius, fill opacity and the outline with it.

        The colour is dropped because the CLASSES carry the colours; keeping class 0's would report
        it as the layer's own and make an untouched layer read as edited. `style_from_vector_tiles`
        already did exactly this — the tile reader was more faithful than the feature reader.
        """
        if symbol is None:
            return {}
        try:
            return {k: v for k, v in _style_from_symbol(symbol).items() if k != "color"}
        except Exception as exc:        # noqa: BLE001 - the classes matter more than the shape
            _log("Could not read this layer's symbol shape ({0}: {1}); its classes still travel."
                 .format(type(exc).__name__, exc))
            return {}

    try:
        if isinstance(renderer, QgsGraduatedSymbolRenderer):
            classes, shape = [], {}
            for rng in renderer.ranges():
                lo, hi = rng.lowerValue(), rng.upperValue()
                # READ IT HERE, INSIDE THE LOOP. `QgsRendererRange.symbol()` hands back a pointer
                # BORROWED from the range, and `ranges()` returns temporaries — so keeping the
                # symbol to read after the loop leaves Python holding a pointer into freed memory,
                # and QGIS segfaults the moment it is touched. Found exactly that way: a hard crash
                # with no traceback, on the first graduated layer.
                shape = shape or shape_of(rng.symbol())
                classes.append({
                    # ±inf becomes None: GeoDeploy's open edge, so data added later still draws.
                    "min": None if lo == float("-inf") else lo,
                    "max": None if hi == float("inf") else hi,
                    "color": _hex(rng.symbol().color()),
                })
            if classes:
                return with_3d(dict(shape,
                                    color_mode="graduated",
                                    color_field=renderer.classAttribute(),
                                    classes=classes, classes_n=len(classes)))

        if isinstance(renderer, QgsCategorizedSymbolRenderer):
            categories, other, shape = [], None, {}
            for cat in renderer.categories():
                value = cat.value()
                if value in (None, ""):
                    other = _hex(cat.symbol().color())     # QGIS's catch-all is GeoDeploy's "other"
                    continue
                # Inside the loop, for the reason spelled out in the graduated branch above: a
                # category's symbol is borrowed from a temporary and must not outlive it.
                shape = shape or shape_of(cat.symbol())
                categories.append({"value": value, "color": _hex(cat.symbol().color())})
            if categories:
                style = dict(shape, color_mode="categorized",
                             color_field=renderer.classAttribute(), categories=categories)
                if other:
                    style["other_color"] = other
                return with_3d(style)

        # "NO SYMBOLS" is a renderer, not an empty style: the layer stays in the tree, listed and
        # identifiable, and draws nothing. Kept for its popups or (one day) its labels alone.
        if type(renderer).__name__ == "QgsNullSymbolRenderer":
            return with_scope({"no_symbol": True})

        # 2.5D. A renderer of its own, and it must be recognised BEFORE the generic fallback: its
        # symbol is a shadow fill plus two geometry generators, so flattening it would send a
        # meaningless dark grey and lose the extrusion entirely. See `qgis25d.py`.
        try:
            try:                        # a package, inside QGIS
                from . import qgis25d as _25d
            except ImportError:         # exec'd standalone by the test harness
                import qgis25d as _25d
            if _25d.is_25d(renderer):
                built, notes = _25d.from_qgis(qgis_layer, renderer)
                for note in notes:
                    _log("{0}: {1}".format(
                        qgis_layer.name() if hasattr(qgis_layer, "name") else "This layer", note),
                        level="info")
                if built:
                    return built
        except ImportError:             # pragma: no cover - qgis25d.py is optional
            pass
        except Exception as exc:        # noqa: BLE001 - fall through to the generic paths
            _log("Could not read the 2.5D renderer ({0}: {1}).".format(type(exc).__name__, exc))

        # RULE-BASED, CARRIED AS RULES. Every rule becomes one entry in `style.rules` with its own
        # filter, symbol and scale range — see `rules.py`. This is the renderer most real QGIS
        # projects use, so flattening it to the first rule's colour (which is all this could do
        # before there was an expression translator) threw away most of what those projects say.
        try:
            try:                        # a package, inside QGIS
                from . import rules as _rules
            except ImportError:         # exec'd standalone by the test harness
                import rules as _rules
            if _rules.QGIS_RULES and isinstance(renderer, _rules.QgsRuleBasedRenderer):
                carried, notes = _rules.from_qgis(qgis_layer, renderer)
                for note in notes:
                    _log("{0}: {1}".format(
                        qgis_layer.name() if hasattr(qgis_layer, "name") else "This layer", note))
                if carried:
                    # The first rule's shape doubles as the layer's own, so a viewer that knows
                    # nothing about rules still draws something recognisable rather than a default.
                    base = dict(carried[0].get("style") or {})
                    return with_3d(dict(base, color_mode="single", rules=carried))
        except ImportError:             # pragma: no cover - rules.py is optional
            pass
        except Exception as exc:        # noqa: BLE001 - fall through to the single symbol below
            _log("Could not read the rules ({0}: {1}); sending a single symbol instead."
                 .format(type(exc).__name__, exc))

        # EVERY OTHER RENDERER, flattened to one symbol. Flattening loses what makes the renderer
        # what it is, so say so rather than letting the author discover it on the published map.
        symbol = renderer.symbol() if isinstance(renderer, QgsSingleSymbolRenderer) else None
        if symbol is None:
            symbols = _symbols_of(renderer)
            symbol = symbols[0] if symbols else None
            if symbol is not None:
                _log("{0} is drawn with {1}, which GeoDeploy has no equivalent for — it was sent "
                     "as a single symbol in the first rule's style. Its colours and widths travel; "
                     "the rules do not."
                     .format(qgis_layer.name() if hasattr(qgis_layer, "name") else "This layer",
                             type(renderer).__name__), level="info")
        if symbol is not None:
            return with_3d(dict({"color_mode": "single"}, **_style_from_symbol(symbol)))
    except Exception as exc:            # noqa: BLE001 - never block an upload over styling
        _log("Could not read this layer's styling: {0}: {1}".format(type(exc).__name__, exc))
        return {}
    return {}


def _style_from_symbol(symbol) -> dict:
    """Colour, size and dash from ONE QGIS symbol — the inverse of `_symbol_of`.

    THE CONVERSIONS HAVE TO INVERT, and two of them did not. `_symbol_of` writes a marker's size as
    `radius * 2 * CSS_PX_TO_POINTS` and a line's width as `line_width * CSS_PX_TO_POINTS`; this read
    them back as `size / 2` and `width * 4`, left over from when sizes were in millimetres. A radius
    of 5 round-tripped to 3.75 and a line width of 2 to 6 — so simply opening a layer and pushing it
    back changed how it draws. Dividing by the same constant the writer multiplies by is the whole
    fix, and `test_tile_symbology` now round-trips to keep it that way.
    """
    style = {"color": _hex(symbol.color())}
    layer0 = symbol.symbolLayer(0) if symbol.symbolLayerCount() else None

    def number(getter):
        """A finite float from a Qt getter, or None. A missing size must not cost the COLOUR too —
        losing a whole style over one unreadable number is the wrong trade, and it is the kind of
        thing that differs between QGIS builds."""
        try:
            value = float(getter())
        except (TypeError, ValueError):
            return None
        return value if value == value and value not in (float("inf"), float("-inf")) else None

    # SIZE COMES FROM THE SYMBOL, NOT THE SYMBOL LAYER, and that is what rescues the symbols QGIS
    # draws with something other than the three Simple classes. An SVG marker, a raster marker, a
    # font marker, a marker line, an arrow — none of them match the `isinstance` branches below, so
    # a layer wearing one returned its COLOUR AND NOTHING ELSE ("it exported the layer data, but it
    # didn't really export styles — maybe just the colour"). But `QgsMarkerSymbol.size()` and
    # `QgsLineSymbol.width()` are properties of the SYMBOL, so they can be read whatever is inside
    # it. The picture is still an approximation — a circle of the right size where QGIS drew an
    # icon — and that is the honest degradation this module promises, rather than silence.
    try:
        from qgis.core import QgsLineSymbol, QgsMarkerSymbol
        if isinstance(symbol, QgsMarkerSymbol):
            size = number(symbol.size)
            if size is not None:
                style["radius"] = round(size / 2.0 / CSS_PX_TO_POINTS, 2)
        elif isinstance(symbol, QgsLineSymbol):
            width = number(symbol.width)
            if width is not None:
                style["line_width"] = round(width / CSS_PX_TO_POINTS, 2)
    except ImportError:                 # pragma: no cover - the branches below still run
        pass

    # A DECORATED LINE whose first symbol layer is the decoration itself — ticks with no stroke
    # under them — never reaches the simple-line branch below, so the markers are read here.
    if layer0 is not None and not isinstance(layer0, QgsSimpleLineSymbolLayer):
        style.update(_line_decoration_symbol(symbol))

    # A MARKER WE HAVE NO WORDS FOR TRAVELS AS ITS PICTURE. Anything but a SINGLE plain simple
    # marker — an SVG, a raster or font marker, an ellipse, a filled marker, or several layers
    # stacked — used to arrive as a coloured dot. Rendering the symbol is the honest translation,
    # and one branch covers every kind of marker QGIS has, including ones it gains later.
    #
    # The layer COUNT matters as much as the class: a halo under a dot is two simple markers, and
    # `symbolLayer(0)` describes half of it. Reading only the first layer is exactly the loss this
    # closes, so more than one layer means the flat style cannot say what the symbol is either.
    stacked = layer0 is not None and getattr(symbol, "symbolLayerCount", lambda: 1)() > 1
    if layer0 is not None and (stacked or not isinstance(layer0, QgsSimpleMarkerSymbolLayer)):
        picture = _marker_picture(symbol)
        if picture:
            style["marker_image"] = picture

    if isinstance(layer0, QgsSimpleMarkerSymbolLayer):
        size = number(symbol.size)
        if size is not None:
            style["radius"] = round(size / 2.0 / CSS_PX_TO_POINTS, 2)
        # THE OUTLINE, which this used to ignore entirely — so changing only a marker's stroke
        # produced a byte-identical style and the push reported "unchanged". Reported exactly that
        # way: "when I only change the symbol fill it detects the change; when I only change the
        # stroke colour it doesn't."
        style["outline_color"] = _stroke_of(layer0)
        shape = _shape_name(layer0)
        if shape:
            style["marker"] = shape
        # …and the outline WIDTH, back out of pixels into the ratio the map stores. Reported as
        # "stroke color now works well, but stroke width doesn't seem to be saved" — it was never
        # read, and never applied either.
        stroke_pt = _stroke_width_of(layer0)
        radius = style.get("radius") or DEFAULT_POINT_RADIUS
        if stroke_pt is not None and radius:
            ratio = stroke_pt / CSS_PX_TO_POINTS / float(radius)
            style["outline_width"] = round(max(0.0, min(1.0, ratio)), 3)
        style.update(_marker_placement_of(symbol, layer0))
    elif isinstance(layer0, QgsSimpleLineSymbolLayer):
        width = number(layer0.width)
        if width is not None:
            style["line_width"] = round(width / CSS_PX_TO_POINTS, 2)
        pen = layer0.penStyle()
        style["lineType"] = ("dashed" if pen == enum(Qt, "PenStyle", "DashLine")
                             else "dotted" if pen == enum(Qt, "PenStyle", "DotLine") else "solid")
        style.update(_line_decoration_of(layer0, style.get("line_width")))
        style.update(_line_decoration_symbol(symbol))
    elif isinstance(layer0, QgsSimpleFillSymbolLayer) or _is_fill(symbol):
        opacity = number(symbol.opacity)
        if opacity is not None:
            style["fill_opacity"] = round(opacity, 3)
        if isinstance(layer0, QgsSimpleFillSymbolLayer):
            style["outline_color"] = _stroke_of(layer0)
        # The border WIDTH, which used to be dropped because GeoDeploy could not draw one: a
        # MapLibre fill strokes its own edge at a fixed hairline. It draws one now (a `line` layer
        # beside the fill), so the number is worth carrying — divided by the same constant the
        # writer multiplies by, like every other size here. Read through `_stroke_width_of` for the
        # same reason the writer sets it through `_set_stroke_width`: `layer0.width` is a LINE's
        # method, and reaching for it here raised `AttributeError` before `from_qgis` had a style to
        # return — so a single-symbol polygon uploaded with no styling at all.
        width = _stroke_width_of(layer0) if isinstance(layer0, QgsSimpleFillSymbolLayer) else None
        if width is not None:
            style["outline_width"] = round(width / CSS_PX_TO_POINTS, 2)
        # A PATTERNED FILL — hatch, cross, dense, line, point, SVG or raster — travels as a tile
        # that repeats seamlessly. Scanned across every symbol layer, because a patterned polygon
        # is usually a plain fill with a hatch stacked on it. See `fills.py` for why the tile is
        # REBUILT rather than photographed.
        style.update(_fill_pattern_of(symbol))
    style.update(_size_from_qgis(symbol, layer0))
    return style


#: The expression `_apply_data_defined_size` writes, read back. Same shape, so the same five numbers
#: come out — otherwise a layer sized BY A FIELD lost that on the way home, and the comparison then
#: reported it as an edit nobody made.
_SCALE_LINEAR = re.compile(
    r'^\s*scale_linear\(\s*"(?P<field>[^"]+)"\s*,\s*(?P<in_lo>-?[\d.eE+]+)\s*,'
    r'\s*(?P<in_hi>-?[\d.eE+]+)\s*,\s*(?P<out_lo>-?[\d.eE+]+)\s*,'
    r'\s*(?P<out_hi>-?[\d.eE+]+)\s*\)\s*$')


def _size_from_qgis(symbol, layer0) -> dict:
    """`{size_mode, size_field, size_stops}` when the symbol is sized by a field, else `{}`."""
    expression = None
    try:
        from qgis.core import QgsSymbolLayer
        for holder, getter, key in (
                (symbol, "dataDefinedSize", None),
                (layer0, "dataDefinedProperty", enum(QgsSymbolLayer, "Property", "PropertyStrokeWidth"))):
            fn = getattr(holder, getter, None)
            if not callable(fn):
                continue
            prop = fn() if key is None else fn(key)
            text = getattr(prop, "expressionString", lambda: "")()
            if text:
                expression = str(text)
                break
    except Exception:                   # noqa: BLE001 - a size is never worth failing a read
        return {}
    if not expression:
        return {}
    m = _SCALE_LINEAR.match(expression)
    if not m:
        return {}
    scale = 2 * CSS_PX_TO_POINTS if isinstance(layer0, QgsSimpleMarkerSymbolLayer) else CSS_PX_TO_POINTS
    return {
        "size_mode": "proportional",
        "size_field": m.group("field"),
        "size_stops": [[round(float(m.group("in_lo")), 6), round(float(m.group("out_lo")) / scale, 3)],
                       [round(float(m.group("in_hi")), 6), round(float(m.group("out_hi")) / scale, 3)]],
    }


#: The inverse of `_CAP_STYLES` / `_JOIN_STYLES`, resolved lazily because the Qt enum values are
#: only comparable once Qt is importable.
def _name_for(value, table, scope):
    for name, qt_name in table.items():
        try:
            if value == enum(Qt, scope, qt_name):
                return name
        except Exception:               # noqa: BLE001  # nosec B112 - try the next spelling
            continue
    return None


def _apply_layer_scope(qgis_layer, style: dict) -> None:
    """A style's zoom range back onto a QGIS layer as a scale range, and the two ends swap.

    Setting only one end still turns scale visibility ON, which is what QGIS does too; the unset end
    stays 0, its own "no limit".
    """
    lo, hi = style.get("minzoom"), style.get("maxzoom")
    if lo is None and hi is None:
        return
    try:
        from geodeploy.styles import scale_for_zoom
        if hasattr(qgis_layer, "setMinimumScale") and lo is not None:
            qgis_layer.setMinimumScale(scale_for_zoom(lo))
        if hasattr(qgis_layer, "setMaximumScale") and hi is not None:
            qgis_layer.setMaximumScale(scale_for_zoom(hi))
        if hasattr(qgis_layer, "setScaleBasedVisibility"):
            qgis_layer.setScaleBasedVisibility(True)
    except Exception as exc:            # noqa: BLE001 - a scale range must not stop a layer loading
        _log("Could not apply the layer's scale range: {0}".format(exc))


def _layer_scope_of(qgis_layer) -> dict:
    """`minzoom` / `maxzoom` / `filter` / `filter_expression` for a whole QGIS layer.

    Two things QGIS keeps on the LAYER rather than in its symbology, and both are exact round trips:

    * **Scale-based visibility.** `minimumScale` is the most-zoomed-OUT limit, the LARGER scale
      denominator, and becomes `minzoom` — which reads backwards and is right, because a
      denominator grows as a zoom level shrinks. `styles.zoom_for_scale` does the conversion.
    * **The subset string**, which is a filter expression over the layer's attributes and becomes a
      MapLibre `filter` through the same translator the rules use. The QGIS source rides along in
      `filter_expression` so a round trip hands back the text the author typed.
    """
    out = {}
    try:
        from geodeploy.styles import zoom_for_scale
        if getattr(qgis_layer, "hasScaleBasedVisibility", lambda: False)():
            lo = zoom_for_scale(qgis_layer.minimumScale())
            hi = zoom_for_scale(qgis_layer.maximumScale())
            if lo is not None:
                out["minzoom"] = lo
            if hi is not None:
                out["maxzoom"] = hi
    except Exception:                   # noqa: BLE001 - a scale range is never worth failing a read  # nosec B110 - intentional: a scale range is never worth failing a read
        pass

    try:
        subset = (qgis_layer.subsetString() or "").strip()             if hasattr(qgis_layer, "subsetString") else ""
    except Exception:                   # noqa: BLE001
        subset = ""
    if subset:
        from geodeploy import expressions
        node, reason = expressions.try_maplibre(subset)
        if node is None:
            # NOT sending a broken filter is the whole point: a layer whose subset cannot travel
            # would otherwise publish every feature, which is a different map from the one on
            # screen. Say so instead.
            _log("{0}: its filter ({1}) {2}, so the published layer will show ALL its features. "
                 "Simplify the filter, or filter the data before uploading it.".format(
                     qgis_layer.name() if hasattr(qgis_layer, "name") else "This layer",
                     subset, reason))
        else:
            out["filter"] = node
            out["filter_expression"] = subset
    return out


def _line_decoration_of(layer0, line_width) -> dict:
    """`dash_pattern`, `line_cap`, `line_join`, `line_offset` from a simple line symbol layer.

    The dash comes back in MULTIPLES OF THE LINE WIDTH, dividing by the same width the writer
    multiplied by — the unit MapLibre uses, and the reason the pattern keeps its shape when somebody
    changes the width on either side.
    """
    out = {}
    try:
        uses_custom = getattr(layer0, "useCustomDashPattern", None)
        vector = getattr(layer0, "customDashVector", None)
        if callable(uses_custom) and uses_custom() and callable(vector):
            width = _number(line_width, DEFAULT_LINE_WIDTH) * CSS_PX_TO_POINTS
            raw = [float(v) for v in (vector() or [])]
            if width and len(raw) >= 2:
                out["dash_pattern"] = [round(v / width, 4) for v in raw]
                # A custom pattern is the real dash; the preset name would contradict it.
                out["lineType"] = "solid"
    except Exception:                   # noqa: BLE001 - a dash is never worth failing the read  # nosec B110 - intentional: a dash is never worth failing the read
        pass

    for getter, table, scope, key in (("penCapStyle", _CAP_STYLES, "PenCapStyle", "line_cap"),
                                      ("penJoinStyle", _JOIN_STYLES, "PenJoinStyle", "line_join")):
        fn = getattr(layer0, getter, None)
        if not callable(fn):
            continue
        try:
            name = _name_for(fn(), table, scope)
        except Exception:               # noqa: BLE001
            name = None
        if name:
            out[key] = name

    fn = getattr(layer0, "offset", None)
    if callable(fn):
        value = _number(fn(), None)
        if value:
            # The sign flip the writer applies, undone — see `_apply_line_decoration`.
            out["line_offset"] = round(-value / CSS_PX_TO_POINTS, 3)
    return out


def _marker_placement_of(symbol, layer0) -> dict:
    """`marker_rotation`, `marker_offset`, `marker_opacity` from a marker symbol."""
    out = {}
    for target in (symbol, layer0):
        fn = getattr(target, "angle", None)
        if callable(fn):
            value = _number(fn(), None)
            if value:
                out["marker_rotation"] = round(value % 360, 3)
            break
    fn = getattr(layer0, "offset", None)
    if callable(fn):
        try:
            point = fn()
            x, y = _number(point.x(), 0.0), _number(point.y(), 0.0)
            if x or y:
                out["marker_offset"] = [round(x / CSS_PX_TO_POINTS, 3),
                                        round(y / CSS_PX_TO_POINTS, 3)]
        except Exception:               # noqa: BLE001 - not every build returns a QPointF  # nosec B110 - intentional: not every QGIS build returns a QPointF here
            pass
    fn = getattr(symbol, "opacity", None)
    if callable(fn):
        value = _number(fn(), None)
        # 1.0 is the default and saying so on every marker would be noise in every style.
        if value is not None and value < 1.0:
            out["marker_opacity"] = round(value, 3)
    return out


#: How large a rendered marker bitmap may get before it is not worth carrying. A style is JSON in a
#: database row and in every published portal's style.json, so a 300 KB icon would be paid for on
#: every page load. Real SVG and font markers land far under this; a photograph used as a marker
#: does not, and should be told about rather than silently shipped.
MAX_PICTURE_BYTES = 96 * 1024

#: The bitmap is rendered at this multiple of the marker's on-screen size, so it stays crisp on a
#: high-DPI display. The runtime registers it with `pixelRatio: 2` to match.
PICTURE_SCALE = 2

#: And never smaller than this, so a tiny marker still yields a usable image.
MIN_PICTURE_PX = 32
MAX_PICTURE_PX = 256


def _marker_picture(symbol) -> str | None:
    """A marker symbol rendered to a PNG data URI, or None.

    WHY RENDER THE SYMBOL RATHER THAN TRANSLATE IT. QGIS draws markers ten different ways — an SVG
    from disk, a raster image, a font glyph, an ellipse, a filled shape, several layers stacked —
    and a web map can express almost none of them. Every one of those used to arrive as a coloured
    dot: "your airport icon became a circle".

    But MapLibre does not need to UNDERSTAND an icon, only to have its pixels. QGIS will happily
    rasterise any symbol it can draw, so the honest translation of "a symbol we have no words for"
    is the picture of it. One code path covers SVG, raster, font, ellipse, filled and multi-layer
    markers, including ones QGIS gains later.

    What it cannot do is vary per feature: a bitmap cannot be recoloured per class the way a
    generated shape can, and a data-defined size scales it rather than redrawing it.
    """
    if not QGIS or symbol is None:
        return None
    try:
        from qgis.core import QgsMarkerSymbol
        if not isinstance(symbol, QgsMarkerSymbol):
            return None
        from qgis.PyQt.QtCore import QBuffer, QByteArray, QIODevice, QSize

        size = _number(getattr(symbol, "size", lambda: None)(), 0) or 0
        px = int(max(MIN_PICTURE_PX, min(MAX_PICTURE_PX,
                                         round(size / CSS_PX_TO_POINTS * PICTURE_SCALE * 2))))
        image = symbol.asImage(QSize(px, px))
        if image is None or image.isNull():
            return None

        data = QByteArray()
        buf = QBuffer(data)
        mode = getattr(QIODevice, "OpenModeFlag", QIODevice)
        buf.open(getattr(mode, "WriteOnly"))
        if not image.save(buf, "PNG"):
            return None
        raw = bytes(data)
        if len(raw) > MAX_PICTURE_BYTES:
            _log("This layer's marker renders to {0} KB, which is too large to carry in a style; "
                 "it will be drawn as a plain marker instead. A simpler symbol, or a smaller "
                 "image, would travel.".format(len(raw) // 1024))
            return None
        import base64
        return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")
    except Exception as exc:            # noqa: BLE001 - a picture is never worth failing the style
        _log("Could not render this layer's marker to an image ({0}: {1}); it will be drawn as a "
             "plain marker.".format(type(exc).__name__, exc))
        return None


def _line_decoration_symbol(symbol) -> dict:
    """`line_marker` for a line symbol carrying markers along it, or `{}`.

    QGIS's marker line and hashed line repeat a symbol at an interval down a line — arrows on a
    river, ticks on a boundary, chevrons on a one-way street. MapLibre draws exactly this with a
    `symbol` layer at `symbol-placement: line`, so it is an honest translation rather than an
    approximation: the same picture at the same spacing, rotated to the line.

    SCANNED ACROSS EVERY SYMBOL LAYER, not just the first, because a decorated line is nearly always
    TWO layers — a simple line for the stroke and a marker line on top of it. Reading only
    `symbolLayer(0)` is what made "a road with ticks" arrive as a plain road.
    """
    if not QGIS or symbol is None:
        return {}
    try:
        from qgis.core import QgsLineSymbol
        if not isinstance(symbol, QgsLineSymbol):
            return {}
        from qgis.core import QgsMarkerLineSymbolLayer
        try:                            # QGIS 3.24+; a build without it just skips hashed lines
            from qgis.core import QgsHashedLineSymbolLayer
            decorators = (QgsMarkerLineSymbolLayer, QgsHashedLineSymbolLayer)
        except ImportError:             # pragma: no cover
            decorators = (QgsMarkerLineSymbolLayer,)

        for i in range(symbol.symbolLayerCount()):
            sl = symbol.symbolLayer(i)
            if not isinstance(sl, decorators):
                continue
            sub = getattr(sl, "subSymbol", lambda: None)()
            if sub is None:
                continue
            picture = _marker_picture(sub) if hasattr(sub, "size") else _symbol_picture(sub)
            if not picture:
                continue
            out = {"image": picture}
            interval = _number(getattr(sl, "interval", lambda: None)(), None)
            if interval:
                # QGIS states the interval in the symbol layer's own unit (points by default);
                # `symbol-spacing` is in pixels, the unit every other size here is stated in.
                out["spacing"] = round(interval / CSS_PX_TO_POINTS, 2)
            return {"line_marker": out}
    except Exception as exc:            # noqa: BLE001 - a decoration is never worth failing a style
        _log("Could not read this line's markers ({0}: {1}).".format(type(exc).__name__, exc))
    return {}


def _symbol_picture(symbol) -> str | None:
    """Any symbol rendered to a PNG data URI — the general form of `_marker_picture`.

    A hashed line's sub-symbol is a LINE symbol, not a marker, so it has no `size()` to scale by; it
    is rendered at a fixed tick size instead. Same contract otherwise: the pixels travel because
    MapLibre needs pixels, not a description.
    """
    if not QGIS or symbol is None:
        return None
    try:
        from qgis.PyQt.QtCore import QBuffer, QByteArray, QIODevice, QSize
        image = symbol.asImage(QSize(MIN_PICTURE_PX, MIN_PICTURE_PX))
        if image is None or image.isNull():
            return None
        data = QByteArray()
        buf = QBuffer(data)
        mode = getattr(QIODevice, "OpenModeFlag", QIODevice)
        buf.open(getattr(mode, "WriteOnly"))
        if not image.save(buf, "PNG"):
            return None
        raw = bytes(data)
        if len(raw) > MAX_PICTURE_BYTES:
            return None
        import base64
        return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")
    except Exception:                   # noqa: BLE001  # nosec B110 - intentional: a decoration is optional
        return None


def _is_fill(symbol) -> bool:
    """True for a fill symbol, whatever its first layer happens to be — a polygon patterned with a
    hatch has a symbol layer this module has no branch for, and it is still a fill."""
    try:
        from qgis.core import QgsFillSymbol
        return isinstance(symbol, QgsFillSymbol)
    except ImportError:                 # pragma: no cover
        return False


def _fill_pattern_of(symbol) -> dict:
    """`{"fill_pattern": {...}}` for a patterned fill, else `{}`."""
    try:
        try:                            # a package, inside QGIS
            from . import fills as _fills
        except ImportError:             # exec'd standalone by the test harness
            import fills as _fills
        block, notes = _fills.from_qgis(symbol)
        for note in notes:
            _log("This layer's fill: {0}".format(note), level="info")
        return block
    except ImportError:                 # pragma: no cover - fills.py is optional
        return {}
    except Exception as exc:            # noqa: BLE001 - a pattern is never worth failing a style
        _log("Could not read the fill pattern ({0}: {1}).".format(type(exc).__name__, exc))
        return {}


def _stroke_of(layer0) -> str:
    """A symbol layer's outline as GeoDeploy states it: a colour, or `"none"` for no outline."""
    try:
        if layer0.strokeStyle() == enum(Qt, "PenStyle", "NoPen"):
            return "none"
    except Exception:                   # noqa: BLE001 - not every symbol layer has one  # nosec B110 - intentional: a cosmetic failure must not take down the layer
        pass
    try:
        return _hex(layer0.strokeColor())
    except Exception:                   # noqa: BLE001
        return ""


def _shape_name(layer0):
    """A marker's shape as one of GeoDeploy's names, or None when it is something else entirely.

    Read back so changing the SHAPE registers as a change too. QGIS knows far more shapes than
    GeoDeploy draws; one it cannot express is left out rather than forced to the nearest match, which
    would quietly rewrite the user's symbol.
    """
    try:
        name = QgsSimpleMarkerSymbolLayer.encodeShape(layer0.shape())
    except Exception:                   # noqa: BLE001 - encodeShape moved between QGIS versions
        return None
    name = str(name).strip().lower()
    return name if name in _MARKERS else None
