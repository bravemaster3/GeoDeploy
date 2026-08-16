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
    try:
        from qgis.core import QgsProperty, QgsSymbolLayer
    except ImportError:                 # pragma: no cover
        return
    try:
        if isinstance(layer0, QgsSimpleMarkerSymbolLayer):
            expr = 'scale_linear("{0}", {1}, {2}, {3}, {4})'.format(
                field, in_lo, in_hi, out_lo * 2, out_hi * 2)
            symbol.setDataDefinedSize(QgsProperty.fromExpression(expr))
        elif isinstance(layer0, QgsSimpleLineSymbolLayer):
            expr = 'scale_linear("{0}", {1}, {2}, {3}, {4})'.format(
                field, in_lo, in_hi, out_lo / 4.0, out_hi / 4.0)
            layer0.setDataDefinedProperty(QgsSymbolLayer.PropertyStrokeWidth,
                                          QgsProperty.fromExpression(expr))
    except Exception as exc:            # noqa: BLE001 - a size must never stop a layer drawing
        _log("Could not apply size-by-field ({0}): {1}".format(field, exc))


def _symbol_for(qgis_layer, color: str | None, style: dict):
    """A single symbol of the right geometry kind, coloured and sized from `style`."""
    symbol = QgsSymbol.defaultSymbol(qgis_layer.geometryType())
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
        if style.get("radius"):
            # GeoDeploy's radius is a pixel radius; QGIS marker size is a diameter in mm-ish units.
            # Doubling keeps the relative sizes right, which is what a reader compares.
            symbol.setSize(float(style["radius"]) * 2)
        outline = style.get("outline_color")
        if outline and outline != "none":
            layer0.setStrokeColor(QColor(outline))
        elif outline == "none":
            layer0.setStrokeStyle(Qt.NoPen)
    elif isinstance(layer0, QgsSimpleLineSymbolLayer):
        if style.get("line_width"):
            layer0.setWidth(float(style["line_width"]) / 4.0)   # px → mm, roughly
        dash = (style.get("lineType") or "solid").lower()
        if dash == "dashed":
            layer0.setPenStyle(Qt.DashLine)
        elif dash == "dotted":
            layer0.setPenStyle(Qt.DotLine)
    elif isinstance(layer0, QgsSimpleFillSymbolLayer):
        outline = style.get("outline_color")
        if outline == "none":
            layer0.setStrokeStyle(Qt.NoPen)
        elif outline:
            layer0.setStrokeColor(QColor(outline))
        if style.get("fill_opacity") is not None:
            symbol.setOpacity(float(style["fill_opacity"]))
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


def _log(message: str) -> None:
    """Into QGIS's Log Messages panel, under our own tab — the place a user can be pointed to."""
    try:
        from qgis.core import Qgis, QgsMessageLog
        QgsMessageLog.logMessage(message, "GeoDeploy", Qgis.Warning)
    except Exception:                   # noqa: BLE001 - logging must never raise
        pass


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
            elif name:
                _log("QGIS ramp {0!r} has no colormap of that name on the instance — sending the "
                     "stretch without it.".format(name))
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


def from_qgis(qgis_layer) -> dict:
    """The GeoDeploy style dict for a QGIS layer's current renderer.

    Returns `{}` when there is nothing worth sending — an empty style means "use the default",
    which is better than uploading an approximation nobody asked for.
    """
    if not QGIS or qgis_layer is None:
        return {}
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
            style = {"color_mode": "single", "color": _hex(symbol.color())}
            layer0 = symbol.symbolLayer(0) if symbol.symbolLayerCount() else None
            if isinstance(layer0, QgsSimpleMarkerSymbolLayer):
                style["radius"] = round(float(symbol.size()) / 2.0, 2)
            elif isinstance(layer0, QgsSimpleLineSymbolLayer):
                style["line_width"] = round(float(layer0.width()) * 4.0, 2)
                pen = layer0.penStyle()
                style["lineType"] = ("dashed" if pen == Qt.DashLine
                                     else "dotted" if pen == Qt.DotLine else "solid")
            return style
    except Exception:                   # noqa: BLE001 - never block an upload over styling
        return {}
    return {}
