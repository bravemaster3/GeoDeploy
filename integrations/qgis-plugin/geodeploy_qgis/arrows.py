"""QGIS's arrow line ⇄ GeoDeploy's `style.line_marker`, with the arrow's own parameters carried.

## What QGIS's arrow actually is

`QgsArrowSymbolLayer` is not a line with an arrowhead drawn on it. It is a POLYGON: QGIS builds a
tapered shaft from `arrowStartWidth` to `arrowWidth`, puts a triangular head of `headLength` ×
`headThickness` on the end, and fills the whole outline with a fill sub-symbol. That is why the
layer has a `QgsFillSymbol` under it and no stroke colour of its own — the "line colour" of an
arrow is the colour of the fill.

## Why the translation is an approximation, and which part is exact

MapLibre has no arrow primitive. What it does have is exactly the second half of the picture: a
`symbol` layer at `symbol-placement: line` with `icon-rotation-alignment: map` repeats an image down
a line, turned to follow it. So the head translates **exactly** — same triangle, same proportions,
rotated with the line — and it is drawn as a repeated icon over an ordinary line of the shaft's
width.

Two differences, both stated in the note the push reports rather than left to be discovered:

* **QGIS draws ONE arrow per feature** (or per segment, with `isRepeated()`); MapLibre repeats the
  head at a fixed spacing. Direction — the thing an arrow is for — survives; "exactly one arrowhead,
  at the end" does not, because MapLibre cannot place an icon at a line's end.
* **The shaft does not taper.** `line-width` is one number. A tapered shaft would need a polygon per
  feature, which is a geometry change, not a style.

The arrow's own numbers ride along in `line_marker.arrow` so the trip back to QGIS rebuilds a real
`QgsArrowSymbolLayer` rather than the marker line the image alone would suggest — the same trick
`extrusion.qgis25d` uses for the 2.5D renderer.

## Units

QGIS states these in millimetres by default. Everything here is in PIXELS, via `fills._px_of`,
because reading a millimetre as a pixel turns a 3 mm head into a 3 px one — a speck.
"""
from __future__ import annotations

try:                                    # a package, inside QGIS
    from . import fills, symbology
    from .compat import enum
except ImportError:                     # pragma: no cover - exec'd standalone by the test harness
    import fills
    import symbology
    from compat import enum

try:                                    # pragma: no cover - only present inside QGIS
    from qgis.core import QgsArrowSymbolLayer
    QGIS_ARROWS = True
except ImportError:                     # pragma: no cover
    QGIS_ARROWS = False

#: Pixels between repeated heads when QGIS has not implied one. Far enough apart to read as
#: direction markers rather than a dotted line, close enough that a short feature still gets one.
DEFAULT_SPACING_PX = 90.0

#: The head QGIS gives you when it declines to say. Its own defaults, in millimetres.
DEFAULT_HEAD_LENGTH_MM = 1.5
DEFAULT_HEAD_THICKNESS_MM = 1.5
DEFAULT_ARROW_WIDTH_MM = 1.0

#: A head is drawn into its own small canvas; this bounds it the way `fills` bounds a tile, because
#: the image rides in every published portal's style.json.
MAX_HEAD_PX = 64


def is_arrow(symbol_layer) -> bool:
    return bool(QGIS_ARROWS) and isinstance(symbol_layer, QgsArrowSymbolLayer)


def from_qgis(sl) -> dict:
    """`{line_width, color, line_marker}` for an arrow symbol layer, or `{}`.

    Returns the LINE's properties as well as the decoration, because an arrow layer carries both:
    its shaft is the line, and dropping the shaft width would draw a hairline under a large head.
    """
    if not is_arrow(sl):
        return {}
    try:
        colour = _fill_colour(sl)
        width = fills._px_of(sl, "arrowWidth") or (DEFAULT_ARROW_WIDTH_MM * fills.MM_TO_PX)
        head_len = fills._px_of(sl, "headLength") or (DEFAULT_HEAD_LENGTH_MM * fills.MM_TO_PX)
        head_thick = fills._px_of(sl, "headThickness") or (DEFAULT_HEAD_THICKNESS_MM * fills.MM_TO_PX)

        head_type = symbology._number(_call(sl, "headType"), 0)
        arrow_type = symbology._number(_call(sl, "arrowType"), 0)
        picture = head_image(head_len, head_thick, colour, double=_is_double(head_type),
                            reversed_=_is_reversed(head_type))
        if not picture:
            return {}

        marker = {"image": picture["image"], "width": picture["width"],
                  "height": picture["height"], "spacing": round(_spacing(sl, head_len), 2),
                  # Everything needed to rebuild the real symbol layer on the way back. Kept under
                  # its own key so a hand-authored line marker is never mistaken for an arrow.
                  "arrow": {"width": round(width, 2),
                            "start_width": round(fills._px_of(sl, "arrowStartWidth"), 2),
                            "head_length": round(head_len, 2),
                            "head_thickness": round(head_thick, 2),
                            "head_type": int(head_type), "arrow_type": int(arrow_type),
                            "curved": bool(_call(sl, "isCurved")),
                            "repeated": bool(_call(sl, "isRepeated"))}}
        out = {"line_marker": marker, "line_width": round(width, 2)}
        if colour:
            out["color"] = colour
        return out
    except Exception as exc:            # noqa: BLE001 - an arrow is never worth failing a style
        symbology._log("Could not read this arrow line ({0}: {1}); it will be drawn as a plain "
                       "line.".format(type(exc).__name__, exc))
        return {}


def head_image(length_px: float, thickness_px: float, colour: str,
               double: bool = False, reversed_: bool = False):
    """A filled triangle pointing along +x, as a PNG data URI.

    +x, not up: `symbol-placement: line` with `icon-rotation-alignment: map` gives an icon the
    line's own bearing at rotation 0, so a head drawn pointing right follows the line's direction.
    Drawing it pointing up would put every arrow across the line instead of along it.
    """
    try:
        from qgis.PyQt.QtCore import QPointF
        from qgis.PyQt.QtGui import QBrush, QColor, QPolygonF

        length = max(2.0, min(float(length_px), MAX_HEAD_PX))
        thickness = max(2.0, min(float(thickness_px), MAX_HEAD_PX))
        w = int(round(length * (2 if double else 1))) or 2
        h = int(round(thickness)) or 2
        image = fills._canvas(w, h)
        painter = fills._painter(image)
        try:
            painter.setPen(enum(__import__("qgis").PyQt.QtCore.Qt, "PenStyle", "NoPen"))
            painter.setBrush(QBrush(QColor(colour or symbology.DEFAULT_COLOR)))
            # One head fills the canvas; a double head is two, back to back, so the pair reads as
            # "both ways" rather than as one arrow twice the size.
            if double:
                painter.drawPolygon(QPolygonF([QPointF(0, h / 2.0), QPointF(length, 0),
                                               QPointF(length, h)]))
                painter.drawPolygon(QPolygonF([QPointF(w, h / 2.0), QPointF(length, 0),
                                               QPointF(length, h)]))
            elif reversed_:
                painter.drawPolygon(QPolygonF([QPointF(0, h / 2.0), QPointF(w, 0), QPointF(w, h)]))
            else:
                painter.drawPolygon(QPolygonF([QPointF(w, h / 2.0), QPointF(0, 0), QPointF(0, h)]))
        finally:
            painter.end()
        return fills._encode(image)
    except Exception:                   # noqa: BLE001  # nosec B110 - a head we cannot draw is not fatal
        return None


def carried(style) -> dict:
    """The `line_marker.arrow` block, or `{}`. Present means "this came from an arrow line"."""
    marker = (style or {}).get("line_marker") if isinstance(style, dict) else None
    block = marker.get("arrow") if isinstance(marker, dict) else None
    return block if isinstance(block, dict) else {}


def to_qgis(symbol, style) -> bool:
    """Replace `symbol`'s layers with a rebuilt arrow. True when done.

    Only for a style that CAME from an arrow line. A line marker authored in GeoDeploy stays a
    marker line in QGIS, because that is what it is — turning every decorated line into an arrow
    would be inventing a picture nobody asked for. Same rule as `qgis25d.to_qgis`.
    """
    block = carried(style)
    if not QGIS_ARROWS or not block or symbol is None:
        return False
    try:
        sl = QgsArrowSymbolLayer()
        px = fills.MM_TO_PX
        for setter, value in (("setArrowWidth", block.get("width")),
                              ("setArrowStartWidth", block.get("start_width")),
                              ("setHeadLength", block.get("head_length")),
                              ("setHeadThickness", block.get("head_thickness"))):
            number = symbology._number(value, None)
            fn = getattr(sl, setter, None)
            if number and callable(fn):
                fn(number / px)         # back to millimetres, the unit a fresh layer is stated in
        for setter, value in (("setHeadType", block.get("head_type")),
                              ("setArrowType", block.get("arrow_type"))):
            fn = getattr(sl, setter, None)
            if value is not None and callable(fn):
                try:
                    fn(int(value))
                except Exception:       # noqa: BLE001  # nosec B112 - an enum this build spells differently
                    continue
        for setter, value in (("setIsCurved", block.get("curved")),
                              ("setIsRepeated", block.get("repeated"))):
            fn = getattr(sl, setter, None)
            if value is not None and callable(fn):
                fn(bool(value))
        colour = (style or {}).get("color")
        if colour:
            _set_fill_colour(sl, colour)
        symbology._replace_symbol_layers(symbol, [sl])
        return True
    except Exception as exc:            # noqa: BLE001
        symbology._log("Could not rebuild the arrow line ({0}: {1}).".format(
            type(exc).__name__, exc))
        return False


# ── internals ────────────────────────────────────────────────────────────────────────────────────

def _call(obj, name, default=None):
    fn = getattr(obj, name, None)
    if not callable(fn):
        return default
    try:
        return fn()
    except Exception:                   # noqa: BLE001  # nosec B110 - a value we cannot read is a default
        return default


def _is_double(head_type) -> bool:
    return _head_name(head_type) == "HeadDouble"


def _is_reversed(head_type) -> bool:
    return _head_name(head_type) == "HeadReversed"


def _head_name(head_type) -> str:
    """The enum member's NAME, resolved against the class rather than assumed from its value.

    `HeadSingle`/`HeadReversed`/`HeadDouble` are 0/1/2 today, and comparing to literals would be a
    silent misdraw the day that order changes — a reversed arrow pointing upstream is not an error
    anyone would trace back to this line.
    """
    if not QGIS_ARROWS:
        return ""
    for name in ("HeadSingle", "HeadReversed", "HeadDouble"):
        try:
            if head_type == enum(QgsArrowSymbolLayer, "HeadType", name):
                return name
        except Exception:               # noqa: BLE001  # nosec B112 - a name this build does not have
            continue
    return ""


def _fill_colour(sl) -> str:
    """The arrow's colour, which lives on its FILL sub-symbol — an arrow is a filled polygon."""
    sub = _call(sl, "subSymbol")
    colour = _call(sub, "color") if sub is not None else None
    if colour is None:
        colour = _call(sl, "color")
    try:
        return symbology._hex(colour).lower() if colour is not None else ""
    except Exception:                   # noqa: BLE001  # nosec B110 - fall back to the style's own colour
        return ""


def _set_fill_colour(sl, colour) -> None:
    try:
        from qgis.PyQt.QtGui import QColor
        sub = _call(sl, "subSymbol")
        if sub is not None and hasattr(sub, "setColor"):
            sub.setColor(QColor(colour))
            if hasattr(sl, "setSubSymbol"):
                sl.setSubSymbol(sub)
    except Exception:                   # noqa: BLE001  # nosec B110 - a colour we cannot set is not fatal
        pass


def _spacing(sl, head_len: float) -> float:
    """How far apart to repeat the head.

    QGIS does not state a spacing — it draws one arrow — so this is chosen, not read. It scales with
    the head so a large arrow does not end up in a dense row of overlapping triangles, with a floor
    that keeps a short feature from getting none at all.
    """
    return max(DEFAULT_SPACING_PX, head_len * 6.0)
