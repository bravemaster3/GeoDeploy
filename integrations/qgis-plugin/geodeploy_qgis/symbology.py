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

try:                                    # pragma: no cover - only present inside QGIS
    from qgis.core import (QgsCategorizedSymbolRenderer, QgsGraduatedSymbolRenderer,
                           QgsRendererCategory, QgsRendererRange, QgsSimpleFillSymbolLayer,
                           QgsSimpleLineSymbolLayer, QgsSimpleMarkerSymbolLayer, QgsSymbol,
                           QgsSingleSymbolRenderer, QgsClassificationRange,
                           QgsMultiBandColorRenderer, QgsSingleBandGrayRenderer,
                           QgsSingleBandPseudoColorRenderer, QgsHillshadeRenderer,
                           QgsPalettedRasterRenderer)
    from qgis.PyQt.QtCore import Qt
    from qgis.PyQt.QtGui import QColor
    QGIS = True
except ImportError:                     # importable outside QGIS so the module can be unit-tested
    QGIS = False


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
            layer0.setDataDefinedProperty(QgsSymbolLayer.PropertyStrokeWidth,
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
    """Set a marker's outline width, whatever this QGIS calls the setter."""
    for name in ("setStrokeWidth", "setOutlineWidth"):
        fn = getattr(layer0, name, None)
        if callable(fn):
            try:
                fn(width)
                return
            except Exception:           # noqa: BLE001 - an outline is not worth failing the style
                pass


def _use_points(symbol, layer0) -> None:
    """Measure this symbol in POINTS, so it matches the size GeoDeploy draws.

    Millimetres (the QGIS default) made a radius of 5 into a 10 mm marker — roughly four times too
    big. Render pixels fixed that but went too far the other way on a scaled display, because they
    are device pixels while GeoDeploy's are CSS pixels. Points are the unit that means the same
    thing on both sides.
    """
    try:
        from qgis.core import QgsUnitTypes
        pt = QgsUnitTypes.RenderPoints
    except Exception:                   # noqa: BLE001 - very old QGIS: leave the default
        return
    for target, setter in ((symbol, "setSizeUnit"), (layer0, "setSizeUnit"),
                           (symbol, "setWidthUnit"), (layer0, "setWidthUnit")):
        fn = getattr(target, setter, None)
        if callable(fn):
            try:
                fn(pt)
            except Exception:           # noqa: BLE001 - not every symbol layer has both
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
            except Exception:           # noqa: BLE001 - shape names shift between QGIS versions
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
            layer0.setStrokeStyle(Qt.NoPen)
        else:
            # A WHITE ring by default, because that is what the map draws — and because QGIS's own
            # default is a dark grey outline, which on a small marker covers the fill completely
            # and turns every point black whatever colour the style asked for.
            layer0.setStrokeColor(QColor(outline or DEFAULT_MARKER_OUTLINE))
            # radius (CSS px) x ratio = the stroke in CSS px, then into points like every other size.
            _set_stroke_width(layer0, _outline_px(style) * CSS_PX_TO_POINTS)
    elif isinstance(layer0, QgsSimpleLineSymbolLayer):
        _use_points(symbol, layer0)
        # Always set, defaulted to the map's `line-width: 2` — QGIS's own default is 0.26 mm, a
        # hairline, so an unstyled line came out far thinner than the portal draws it.
        layer0.setWidth(float(style.get("line_width") or DEFAULT_LINE_WIDTH) * CSS_PX_TO_POINTS)
        dash = (style.get("lineType") or "solid").lower()
        if dash == "dashed":
            layer0.setPenStyle(Qt.DashLine)
        elif dash == "dotted":
            layer0.setPenStyle(Qt.DotLine)
    elif isinstance(layer0, QgsSimpleFillSymbolLayer):
        outline = style.get("outline_color")
        if outline == "none":
            layer0.setStrokeStyle(Qt.NoPen)
        else:
            # The map's default outline, not QGIS's — `fill-outline-color: #1d4ed8` in
            # portal_generator when the style names none.
            layer0.setStrokeColor(QColor(outline or DEFAULT_FILL_OUTLINE))
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


def _log(message: str, level: str = "warning") -> None:
    """Into QGIS's Log Messages panel, under our own tab — the place a user can be pointed to.

    `level="info"` is for things that are EXPLANATIONS rather than faults. Three identical WARNINGs
    for three rasters that simply cannot carry band styling read as three failures; they are one fact
    about how a portal draws a raster, and the dialog already says what to do about it.
    """
    try:
        from qgis.core import Qgis, QgsMessageLog
        QgsMessageLog.logMessage(message, "GeoDeploy",
                                 Qgis.Info if level == "info" else Qgis.Warning)
    except Exception:                   # noqa: BLE001 - logging must never raise
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
}


def comparable_style(style: dict | None) -> dict:
    """A style reduced to what a viewer would SEE, for comparing two of them.

    WHY A STORED STYLE AND A READ-BACK ONE CANNOT BE COMPARED DIRECTLY. QGIS has no concept of "unset":
    `_symbol_of` fills every gap with the map's own default — radius 5, a white marker stroke, 45% fill
    — so reading a symbol back always returns a COMPLETE style, while the stored one usually holds only
    the few keys somebody actually chose. Compared raw, opening a portal and pushing it straight back
    reported every layer as restyled; reported as "I changed only one style, but it says 3 were
    restyled". Filling both sides from the same table is what makes the comparison mean "looks
    different" instead of "is written differently".

    Keys that do not apply to a geometry are harmless: both sides get them identically.
    """
    merged = dict(_STYLE_DEFAULTS)
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
    if fresh.get("color_mode") and fresh["color_mode"] != base.get("color_mode"):
        for key in ("classes", "classes_n", "categories", "other_color", "color_field"):
            base.pop(key, None)
    if fresh.get("size_mode") == "fixed" or (
            "size_mode" in fresh and fresh["size_mode"] != "proportional"):
        for key in ("size_field", "size_stops"):
            base.pop(key, None)
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
        from qgis.core import QgsVectorTileLayer
        is_tiles = isinstance(qgis_layer, QgsVectorTileLayer)
    except ImportError:                 # pragma: no cover - older QGIS has no vector tiles
        is_tiles = False
    if is_tiles:
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
        geometry_types = [QgsWkbTypes.PolygonGeometry]
    elif "line" in geom:
        geometry_types = [QgsWkbTypes.LineGeometry]
    elif "point" in geom:
        geometry_types = [QgsWkbTypes.PointGeometry]
    else:
        geometry_types = [QgsWkbTypes.PolygonGeometry, QgsWkbTypes.LineGeometry,
                          QgsWkbTypes.PointGeometry]
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
            symbol = _symbol_of(geometry_type, colour, style)
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
            return style

        if isinstance(renderer, QgsSingleBandGrayRenderer):
            band = renderer.grayBand()
            if isinstance(band, int) and band > 0:
                style["bidx"] = [band]
            lo, hi = _enhancement_range(renderer.contrastEnhancement())
            if lo is not None:
                style["rescale"] = "{0},{1}".format(_trim(lo), _trim(hi))
            return style

        if isinstance(renderer, QgsSingleBandPseudoColorRenderer):
            band = renderer.band()
            if isinstance(band, int) and band > 0:
                style["bidx"] = [band]
            lo, hi = renderer.classificationMin(), renderer.classificationMax()
            if _finite(lo) and _finite(hi) and hi > lo:
                style["rescale"] = "{0},{1}".format(_trim(lo), _trim(hi))
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
            return style

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
            return style

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
                classes.append({"value": value,
                                "color": "#{0:02x}{1:02x}{2:02x}{3:02x}".format(
                                    colour.red(), colour.green(), colour.blue(), colour.alpha())})
            if len(classes) > MAX_COLOR_CLASSES:
                # Truncating a classification would silently mis-colour part of the map, so refuse
                # the colours and keep the band: a grey raster is obviously unstyled, where a
                # half-coloured one looks finished and is wrong.
                _log("This raster has {0} classes; GeoDeploy carries at most {1} because the "
                     "mapping travels in every tile request. The colours were not sent — the band "
                     "was.".format(len(classes), MAX_COLOR_CLASSES))
                return style
            if classes:
                style["color_classes"] = classes
            else:
                _log("This paletted raster exposed no readable classes; only its band was sent.")
            return style

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
        return style
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
            wanted = QgsWkbTypes.PolygonGeometry
        elif "line" in recorded:
            wanted = QgsWkbTypes.LineGeometry
        elif "point" in recorded:
            wanted = QgsWkbTypes.PointGeometry
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
    renderer = qgis_layer.renderer() if hasattr(qgis_layer, "renderer") else None
    if renderer is None:
        return {}

    try:
        if isinstance(renderer, QgsGraduatedSymbolRenderer):
            classes = []
            for rng in renderer.ranges():
                lo, hi = rng.lowerValue(), rng.upperValue()
                classes.append({
                    # ±inf becomes None: GeoDeploy's open edge, so data added later still draws.
                    "min": None if lo == float("-inf") else lo,
                    "max": None if hi == float("inf") else hi,
                    "color": _hex(rng.symbol().color()),
                })
            if classes:
                return {"color_mode": "graduated", "color_field": renderer.classAttribute(),
                        "classes": classes, "classes_n": len(classes)}

        if isinstance(renderer, QgsCategorizedSymbolRenderer):
            categories, other = [], None
            for cat in renderer.categories():
                value = cat.value()
                if value in (None, ""):
                    other = _hex(cat.symbol().color())     # QGIS's catch-all is GeoDeploy's "other"
                    continue
                categories.append({"value": value, "color": _hex(cat.symbol().color())})
            if categories:
                style = {"color_mode": "categorized", "color_field": renderer.classAttribute(),
                         "categories": categories}
                if other:
                    style["other_color"] = other
                return style

        symbol = renderer.symbol() if isinstance(renderer, QgsSingleSymbolRenderer) else None
        if symbol is None and hasattr(renderer, "symbols"):
            symbols = renderer.symbols(None)
            symbol = symbols[0] if symbols else None
        if symbol is not None:
            return dict({"color_mode": "single"}, **_style_from_symbol(symbol))
    except Exception:                   # noqa: BLE001 - never block an upload over styling
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
        stroke_pt = None
        for getter in ("strokeWidth", "outlineWidth"):
            fn = getattr(layer0, getter, None)
            if callable(fn):
                stroke_pt = _number(fn(), None)
                if stroke_pt is not None:
                    break
        radius = style.get("radius") or DEFAULT_POINT_RADIUS
        if stroke_pt is not None and radius:
            ratio = stroke_pt / CSS_PX_TO_POINTS / float(radius)
            style["outline_width"] = round(max(0.0, min(1.0, ratio)), 3)
    elif isinstance(layer0, QgsSimpleLineSymbolLayer):
        width = number(layer0.width)
        if width is not None:
            style["line_width"] = round(width / CSS_PX_TO_POINTS, 2)
        pen = layer0.penStyle()
        style["lineType"] = ("dashed" if pen == Qt.DashLine
                             else "dotted" if pen == Qt.DotLine else "solid")
    elif isinstance(layer0, QgsSimpleFillSymbolLayer):
        opacity = number(symbol.opacity)
        if opacity is not None:
            style["fill_opacity"] = round(opacity, 3)
        style["outline_color"] = _stroke_of(layer0)
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
                (layer0, "dataDefinedProperty", QgsSymbolLayer.PropertyStrokeWidth)):
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


def _stroke_of(layer0) -> str:
    """A symbol layer's outline as GeoDeploy states it: a colour, or `"none"` for no outline."""
    try:
        if layer0.strokeStyle() == Qt.NoPen:
            return "none"
    except Exception:                   # noqa: BLE001 - not every symbol layer has one
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
