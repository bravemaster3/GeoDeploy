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
                           QgsSingleSymbolRenderer, QgsClassificationRange)
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
    except Exception:                   # noqa: BLE001 - a style must never stop a layer loading
        return False
    return False


# ── QGIS → GeoDeploy ─────────────────────────────────────────────────────────────────────────────

def _hex(color) -> str:
    return color.name() if hasattr(color, "name") else str(color)


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
