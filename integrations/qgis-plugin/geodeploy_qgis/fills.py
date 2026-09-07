"""QGIS pattern fills ⇄ GeoDeploy's `style.fill_pattern`.

## The problem, and why it is not the marker problem again

A marker is drawn once per feature, so shipping a picture of it is enough. A FILL is drawn by
*repeating* a tile across a polygon, and MapLibre's `fill-pattern` repeats the image it is given —
so the image has to **tile seamlessly**. A rendered preview of a hatch does not: it is one square
patch, and butting copies of it together shows a seam every few pixels, which reads as a rendering
fault rather than a pattern.

So this module does not photograph the fill. It rebuilds the TILE, from the parameters QGIS states:
the spacing, the angle, the line width, the marker, the source image. That is what makes the result
repeat cleanly instead of merely looking right once.

## What each fill needs for its tile to close

* **Qt brush styles** (hatch, cross, dense) — Qt's own patterns have an 8-pixel period, so any tile
  whose side is a multiple of 8 closes. Painted with `QBrush(colour, style)`, which is exactly what
  QGIS draws with.
* **Line pattern** — a tile closes only at angles where the pattern's period is commensurate with a
  square. 0° and 90° close at the spacing itself; 45° and 135° close at `spacing × √2`. Any other
  angle has no square tile at all, so it is SNAPPED to the nearest of those four and said so — a
  visible 5° error beats a seam every tile.
* **Point pattern** — closes at exactly `distanceX × distanceY`. The marker is drawn at the centre
  and again at all eight neighbouring offsets, so one overhanging the edge reappears on the far
  side instead of being clipped away.
* **SVG and raster fills** — the source image IS the tile; QGIS repeats it at `patternWidth` and so
  does this.
* **Random marker fill** has no period at all. It is approximated as a regular point pattern at the
  same density, which is a different picture and is reported as one.

## Units

QGIS states these sizes in millimetres by default, sometimes in points or pixels. A tile is in
PIXELS, so every value goes through `_to_px` with its own unit — reading a millimetre as a pixel
makes a 2 mm hatch into a 2 px one, which is a solid block.
"""
from __future__ import annotations

import base64
import math

try:                                    # a package, inside QGIS
    from . import symbology
except ImportError:                     # pragma: no cover - exec'd standalone by the test harness
    import symbology

try:                                    # pragma: no cover - only present inside QGIS
    from qgis.core import (QgsLinePatternFillSymbolLayer, QgsPointPatternFillSymbolLayer,
                           QgsRandomMarkerFillSymbolLayer, QgsRasterFillSymbolLayer,
                           QgsSimpleFillSymbolLayer, QgsSVGFillSymbolLayer)
    QGIS_FILLS = True
except ImportError:                     # pragma: no cover
    QGIS_FILLS = False

#: One millimetre in CSS pixels at 96 dpi — the ratio every browser and every slippy map uses, and
#: the one QGIS assumes when it renders to screen.
MM_TO_PX = 96.0 / 25.4

#: A tile larger than this is not worth carrying: it rides in every published portal's style.json.
MAX_TILE_PX = 512
MIN_TILE_PX = 4

#: Qt's hatch patterns repeat every 8 pixels, so a tile whose side is a multiple of 8 closes.
BRUSH_TILE_PX = 16


def has_pattern(style) -> bool:
    block = (style or {}).get("fill_pattern")
    return bool(isinstance(block, dict) and block.get("image"))


# ── The tile ─────────────────────────────────────────────────────────────────────────────────────

def from_qgis(symbol):
    """`({"fill_pattern": {...}}, notes)` for a fill symbol that patterns, else `({}, notes)`.

    Scanned across every symbol layer, like a line's decoration: a patterned polygon is often a
    simple fill with a hatch stacked on it, and reading only `symbolLayer(0)` sees the plain half.
    """
    notes = []
    if not QGIS_FILLS or symbol is None:
        return {}, notes
    try:
        from qgis.core import QgsFillSymbol
        if not isinstance(symbol, QgsFillSymbol):
            return {}, notes
        for i in range(symbol.symbolLayerCount()):
            block, note = _tile_for(symbol.symbolLayer(i))
            if note:
                notes.append(note)
            if block and "__centroid__" in block:
                return {"centroid_marker": block["__centroid__"]}, notes
            if block:
                return {"fill_pattern": block}, notes
    except Exception as exc:            # noqa: BLE001 - a pattern is never worth failing a style
        symbology._log("Could not read this fill's pattern ({0}: {1}).".format(
            type(exc).__name__, exc))
    return {}, notes


def _tile_for(sl):
    """`(block, note)` for one symbol layer — `(None, None)` when it is not a pattern."""
    if isinstance(sl, QgsSimpleFillSymbolLayer):
        return _brush_tile(sl)
    if isinstance(sl, QgsLinePatternFillSymbolLayer):
        return _line_tile(sl)
    if isinstance(sl, QgsPointPatternFillSymbolLayer):
        return _point_tile(sl)
    if isinstance(sl, QgsRandomMarkerFillSymbolLayer):
        return _random_tile(sl)
    if _is_centroid(sl):
        # NOT a tile: a centroid fill draws ONE marker per polygon, at its centre. MapLibre places a
        # symbol layer's icons at a polygon's label point by default, so it is a marker, not a
        # pattern — returned under its own key so the caller can tell the two apart.
        marker = _centroid_marker(sl)
        return ({"__centroid__": marker} if marker else None), None
    if isinstance(sl, QgsSVGFillSymbolLayer):
        return _image_tile(sl, sl.svgFilePath(), _px_of(sl, "patternWidth"), svg=True)
    if isinstance(sl, QgsRasterFillSymbolLayer):
        return _image_tile(sl, sl.imageFilePath(), _px_of(sl, "width"), svg=False)
    return None, None


def _is_centroid(sl) -> bool:
    try:
        from qgis.core import QgsCentroidFillSymbolLayer
        return isinstance(sl, QgsCentroidFillSymbolLayer)
    except ImportError:                 # pragma: no cover
        return False


def _centroid_marker(sl):
    """The marker a centroid fill puts at each polygon's centre, as a picture."""
    from qgis.PyQt.QtCore import QSize
    sub = getattr(sl, "subSymbol", lambda: None)()
    if sub is None:
        return None
    try:
        image = sub.asImage(QSize(48, 48))
    except Exception:                   # noqa: BLE001  # nosec B110 - intentional: a sub-symbol we cannot draw is not a marker
        return None
    block = _encode(image)
    return block


def _brush_tile(sl):
    """A Qt hatch, cross or dense pattern, painted the way QGIS paints it."""
    from qgis.PyQt.QtCore import Qt
    from qgis.PyQt.QtGui import QBrush
    style = sl.brushStyle()
    solid = symbology.enum(Qt, "BrushStyle", "SolidPattern")
    none_ = symbology.enum(Qt, "BrushStyle", "NoBrush")
    if style in (solid, none_):
        return None, None               # a plain fill is describable; it needs no tile
    image = _canvas(BRUSH_TILE_PX)
    painter = _painter(image)
    try:
        painter.fillRect(0, 0, BRUSH_TILE_PX, BRUSH_TILE_PX, QBrush(sl.color(), style))
    finally:
        painter.end()
    return _encode(image), None


def _line_tile(sl):
    """Hatch lines at a spacing and an angle.

    THE ANGLE IS SNAPPED, and this is the one real approximation in the module. A square tile can
    only close where the pattern's period divides it: at 0 and 90 degrees that is the spacing, at 45
    and 135 it is spacing x root two. At 30 degrees there is no square tile, so the choice is a
    5-degree error or a seam every tile — and a seam is a rendering fault, where a slightly wrong
    angle is a slightly wrong hatch.
    """
    from qgis.PyQt.QtGui import QPen
    from qgis.PyQt.QtCore import Qt

    spacing = _px_of(sl, "distance") or 8.0
    width = max(1.0, _px_of(sl, "lineWidth") or 1.0)
    raw = float(sl.lineAngle() or 0.0) % 180.0
    angle = min((0.0, 45.0, 90.0, 135.0), key=lambda a: abs(a - raw))
    note = None
    if abs(angle - raw) > 0.5:
        note = ("its hatch angle of {0:g} degrees was drawn at {1:g}: a repeating tile only closes "
                "at 0, 45, 90 and 135, and a seam every tile is worse than a few degrees"
                .format(raw, angle))

    side = spacing if angle in (0.0, 90.0) else spacing * math.sqrt(2.0)
    side = int(max(MIN_TILE_PX, min(MAX_TILE_PX, round(side))))
    image = _canvas(side)
    painter = _painter(image)
    try:
        pen = QPen(sl.color())
        pen.setWidthF(width)
        pen.setCapStyle(symbology.enum(Qt, "PenCapStyle", "FlatCap"))
        painter.setPen(pen)
        if angle == 0.0:
            painter.drawLine(-1, 0, side + 1, 0)            # and its wrap at y = side
            painter.drawLine(-1, side, side + 1, side)
        elif angle == 90.0:
            painter.drawLine(0, -1, 0, side + 1)
            painter.drawLine(side, -1, side, side + 1)
        else:
            # Three passes so the diagonals continue across every edge rather than stopping at it.
            for k in (-1, 0, 1):
                off = k * side
                if angle == 45.0:
                    painter.drawLine(off - 1, side + 1, off + side + 1, -1)
                else:
                    painter.drawLine(off - 1, -1, off + side + 1, side + 1)
    finally:
        painter.end()
    return _encode(image), note


def _point_tile(sl):
    """Markers on a grid. Closes at exactly distanceX x distanceY."""
    w = int(max(MIN_TILE_PX, min(MAX_TILE_PX, round(_px_of(sl, "distanceX") or 12))))
    h = int(max(MIN_TILE_PX, min(MAX_TILE_PX, round(_px_of(sl, "distanceY") or 12))))
    return _marker_grid(sl, w, h), None


def _random_tile(sl):
    """A scattered fill, drawn as a REGULAR one at the same density.

    Randomness has no period, so there is no tile that reproduces it. A grid at the same density is
    a different picture — evenly spaced where the author asked for scattered — and saying so is the
    honest half of the translation.
    """
    count = float(getattr(sl, "pointCount", lambda: 0)() or 0)
    area = _px_of(sl, "densityArea") or 0
    side = 12.0
    if count > 0 and area > 0:
        # `densityArea` is an AREA in the layer's unit; the pixel conversion is linear, so the side
        # of the equivalent square is the square root of the converted area over the count.
        side = math.sqrt(max(1.0, (area * MM_TO_PX) / count))
    side = int(max(MIN_TILE_PX, min(MAX_TILE_PX, round(side))))
    return _marker_grid(sl, side, side), (
        "its randomly scattered fill was drawn as an evenly spaced one at the same density: a "
        "random pattern has no repeating tile")


def _marker_grid(sl, w: int, h: int):
    """One marker centred in a w x h tile, repeated at every neighbouring offset so an overhang
    reappears on the opposite edge instead of being clipped."""
    from qgis.PyQt.QtCore import QSize
    sub = getattr(sl, "subSymbol", lambda: None)()
    if sub is None:
        return None
    size = int(max(4, min(min(w, h) * 2, 96)))
    try:
        marker = sub.asImage(QSize(size, size))
    except Exception:                   # noqa: BLE001  # nosec B110 - intentional: a sub-symbol we cannot draw is simply not a pattern
        return None
    if marker is None or marker.isNull():
        return None
    image = _canvas(w, h)
    painter = _painter(image)
    try:
        x = (w - marker.width()) / 2.0
        y = (h - marker.height()) / 2.0
        for dx in (-w, 0, w):
            for dy in (-h, 0, h):
                painter.drawImage(int(round(x + dx)), int(round(y + dy)), marker)
    finally:
        painter.end()
    return _encode(image)



def _embedded_tile(payload: str):
    """`({image, width, height}, None)` for a `base64:` image the symbol carries inline."""
    import base64
    try:
        raw = base64.b64decode(payload)
    except Exception:                   # noqa: BLE001  # nosec B110
        return None, None
    try:
        from qgis.PyQt.QtGui import QImage
        image = QImage.fromData(raw)
        if image.isNull():
            return None, None
        return ({"image": "data:image/png;base64," + payload,
                 "width": image.width(), "height": image.height()}, None)
    except Exception:                   # noqa: BLE001  # nosec B110
        return None, None


def _image_tile(sl, path: str, width_px: float, svg: bool):
    """An SVG or raster fill: the source image IS the tile, at the width QGIS repeats it.

    `path` may be a FILE or a `base64:` blob. The plugin writes patterns embedded now — a file path
    is read lazily by QGIS and silently draws nothing when it cannot be read — so reading one back
    has to handle both, or a pattern this plugin applied could not be read out again.
    """
    if not path:
        return None, None
    if str(path).startswith("base64:"):
        return _embedded_tile(str(path)[len("base64:"):])
    side = int(max(MIN_TILE_PX, min(MAX_TILE_PX, round(width_px or 24))))
    image = _canvas(side)
    painter = _painter(image)
    try:
        if svg:
            from qgis.PyQt.QtCore import QRectF
            from qgis.PyQt.QtSvg import QSvgRenderer
            renderer = QSvgRenderer(path)
            if not renderer.isValid():
                return None, None
            renderer.render(painter, QRectF(0, 0, side, side))
        else:
            from qgis.PyQt.QtCore import Qt as _Qt
            from qgis.PyQt.QtGui import QImage
            source = QImage(path)
            if source.isNull():
                return None, None
            scaled = source.scaled(side, side,
                                   symbology.enum(_Qt, "AspectRatioMode", "IgnoreAspectRatio"),
                                   symbology.enum(_Qt, "TransformationMode", "SmoothTransformation"))
            painter.drawImage(0, 0, scaled)
    finally:
        painter.end()
    return _encode(image), None


# ── Plumbing ─────────────────────────────────────────────────────────────────────────────────────

def _canvas(w: int, h: int | None = None):
    """A transparent ARGB image. Transparent, not white: a hatch shows the fill beneath it."""
    from qgis.PyQt.QtGui import QImage
    image = QImage(int(w), int(h if h is not None else w),
                   symbology.enum(QImage, "Format", "Format_ARGB32_Premultiplied"))
    image.fill(0)
    return image


def _painter(image):
    from qgis.PyQt.QtGui import QPainter
    painter = QPainter(image)
    try:
        painter.setRenderHint(symbology.enum(QPainter, "RenderHint", "Antialiasing"), True)
    except Exception:                   # noqa: BLE001  # nosec B110 - intentional: antialiasing is cosmetic
        pass
    return painter


def _px_of(sl, getter: str) -> float:
    """One of a symbol layer's sizes, in PIXELS, honouring the unit it is stated in.

    Reading a millimetre as a pixel turns a 2 mm hatch into a 2 px one — a solid block — which is
    why every value goes through here rather than being used as it comes.
    """
    fn = getattr(sl, getter, None)
    if not callable(fn):
        return 0.0
    value = symbology._number(fn(), 0.0)
    if not value:
        return 0.0
    unit = None
    for name in (getter + "Unit", "distanceUnit", "sizeUnit"):
        u = getattr(sl, name, None)
        if callable(u):
            try:
                unit = u()
                break
            except Exception:           # noqa: BLE001  # nosec B112 - intentional: try the next spelling
                continue
    return value * _factor(unit)


def _factor(unit) -> float:
    """Pixels per unit. Millimetres are QGIS's default and the one that matters."""
    try:
        from qgis.core import QgsUnitTypes
        if unit == symbology.enum(QgsUnitTypes, "RenderUnit", "RenderPixels"):
            return 1.0
        if unit == symbology.enum(QgsUnitTypes, "RenderUnit", "RenderPoints"):
            return 1.0 / symbology.CSS_PX_TO_POINTS
    except Exception:                   # noqa: BLE001  # nosec B110 - intentional: fall through to millimetres
        pass
    return MM_TO_PX


def _encode(image):
    """A QImage as a PNG data URI, or None when it is too large to carry."""
    if image is None or image.isNull():
        return None
    try:
        from qgis.PyQt.QtCore import QBuffer, QByteArray, QIODevice
        data = QByteArray()
        buf = QBuffer(data)
        mode = getattr(QIODevice, "OpenModeFlag", QIODevice)
        buf.open(getattr(mode, "WriteOnly"))
        if not image.save(buf, "PNG"):
            return None
        raw = bytes(data)
        if len(raw) > symbology.MAX_PICTURE_BYTES:
            return None
        return {"image": "data:image/png;base64," + base64.b64encode(raw).decode("ascii"),
                "width": image.width(), "height": image.height()}
    except Exception:                   # noqa: BLE001  # nosec B110 - intentional: a pattern is optional
        return None


# ── GeoDeploy → QGIS ─────────────────────────────────────────────────────────────────────────────

#: The browser's hatch presets, as the ANGLES QGIS draws them at. These are the four angles at which
#: a square tile closes, which is why the presets offer exactly these — see `_line_tile`.
#:
#: A LIST, not a single angle, because `cross` is two hatches crossing. It was missing from the
#: first version of this table, so a cross-hatch fell through to the raster path and came back as a
#: picture — right on screen, wrong in kind: a picture cannot be recoloured or classified.
_HATCH_ANGLES = {"horizontal": [0.0], "vertical": [90.0], "forward": [45.0], "back": [135.0],
                 "cross": [0.0, 90.0]}


def to_qgis(symbol, style) -> bool:
    """Rebuild a pattern fill on `symbol` from `style.fill_pattern`. True when done.

    THE HALF THAT WAS MISSING. `from_qgis` has read patterns out of QGIS since this module existed,
    but nothing put one back — so a layer whose GeoDeploy style is a hatch opened in QGIS as a plain
    fill, with the hatch simply gone. Reported from testing exactly that way: "a layer that is
    supposed to have hashed polygons opened with colors instead".

    TWO ROUTES, because the tile can come from two places:

    * A hatch made in GeoDeploy's browser carries its preset NAME (`fill_pattern.hatch`). That
      rebuilds as a real `QgsLinePatternFillSymbolLayer` at the matching angle — a native QGIS hatch
      the author can then edit, recolour and classify, which a picture is not.
    * Anything else — an SVG fill, a raster fill, a Qt brush, a tile from another tool — is PIXELS,
      and the honest reconstruction is those pixels: a `QgsRasterFillSymbolLayer` over the image.
    """
    block = (style or {}).get("fill_pattern")
    if not QGIS_FILLS or symbol is None or not isinstance(block, dict):
        return False
    uri = block.get("image")
    if not isinstance(uri, str) or not uri.startswith("data:image/"):
        return False
    try:
        from qgis.core import QgsFillSymbol
        if not isinstance(symbol, QgsFillSymbol):
            return False
        layers = (_hatch_layers(block, style) if block.get("hatch") in _HATCH_ANGLES
                  else [_raster_layer(uri, block)])
        layers = [one for one in layers if one is not None]
        if not layers:
            return False
        # UNDER the pattern, the plain fill the style also describes: QGIS draws a pattern over
        # whatever is beneath it, and a hatch on nothing is a hatch on the basemap. This is the same
        # stack `from_qgis` reads back — a simple fill with the pattern on top of it.
        symbol.changeSymbolLayer(0, _plain_fill(style))
        for i in range(symbol.symbolLayerCount() - 1, 0, -1):
            symbol.deleteSymbolLayer(i)
        for one in layers:
            symbol.appendSymbolLayer(one)
        return True
    except Exception as exc:            # noqa: BLE001 - a pattern is never worth failing a style
        symbology._log("Could not rebuild this fill's pattern ({0}: {1}).".format(
            type(exc).__name__, exc))
        return False



def decorate(symbol, style) -> bool:
    """ADD the style's pattern / centre marker on top of a symbol that is already built and coloured.

    The difference from `to_qgis` is the whole point. `to_qgis` REPLACES a symbol's layers, which is
    right for a single symbol and wrong for a classified one: applied per class it threw away the
    class colour, and applied once to the layer it threw away the classification itself — a
    graduated, hatched polygon arrived in QGIS as one flat hatched colour.

    This appends instead, so the fill underneath keeps whatever colour it was given — the class's
    colour for a graduated layer, the layer's for a plain one — and one code path serves both.
    """
    if not QGIS_FILLS or symbol is None:
        return False
    try:
        from qgis.core import QgsFillSymbol
        if not isinstance(symbol, QgsFillSymbol):
            return False
    except Exception:                   # noqa: BLE001  # nosec B110
        return False
    added = False
    block = (style or {}).get("fill_pattern")
    if isinstance(block, dict) and str(block.get("image", "")).startswith("data:image/"):
        layers = (_hatch_layers(block, style) if block.get("hatch") in _HATCH_ANGLES
                  else [_raster_layer(block["image"], block)])
        for one in layers:
            if one is not None:
                symbol.appendSymbolLayer(one)
                added = True
    centre = (style or {}).get("centroid_marker")
    if isinstance(centre, dict) and str(centre.get("image", "")).startswith("data:image/"):
        marker = raster_marker(centre.get("image"))
        if marker is not None:
            try:
                from qgis.core import QgsCentroidFillSymbolLayer, QgsMarkerSymbol
                sub = QgsMarkerSymbol()
                sub.changeSymbolLayer(0, marker)
                fill = QgsCentroidFillSymbolLayer()
                fill.setSubSymbol(sub)
                symbol.appendSymbolLayer(fill)
                added = True
            except Exception:           # noqa: BLE001  # nosec B110
                pass
    return added

def _plain_fill(style):
    """The flat fill that sits under a pattern, in the style's own colour and opacity."""
    from qgis.core import QgsSimpleFillSymbolLayer
    from qgis.PyQt.QtGui import QColor
    fill = QgsSimpleFillSymbolLayer()
    colour = QColor(style.get("color") or symbology.DEFAULT_COLOR)
    opacity = symbology._number(style.get("fill_opacity"), None)
    if opacity is not None:
        colour.setAlphaF(max(0.0, min(1.0, opacity)))
    fill.setColor(colour)
    outline = style.get("outline_color")
    if outline and outline != "none":
        fill.setStrokeColor(QColor(outline))
    elif outline == "none":
        from qgis.PyQt.QtCore import Qt
        fill.setStrokeStyle(symbology.enum(Qt, "PenStyle", "NoPen"))
    return fill


def _hatch_layers(block, style):
    """Native QGIS hatches for a preset made in the browser — two of them for a cross."""
    return [_hatch_layer(block, style, angle) for angle in _HATCH_ANGLES[block["hatch"]]]


def _hatch_layer(block, style, angle):
    """One native QGIS hatch at one angle."""
    from qgis.core import QgsLinePatternFillSymbolLayer
    from qgis.PyQt.QtGui import QColor
    hatch = QgsLinePatternFillSymbolLayer()
    hatch.setLineAngle(angle)
    # The browser draws its tiles at 12px with a 1.5px stroke and lines every half-tile. Converted
    # back to millimetres so QGIS's own units mean what they say — a distance left in pixels here
    # would render at a quarter of its size on a printed map.
    hatch.setDistance(round((block.get("height") or 12) / 2.0 / MM_TO_PX, 3))
    hatch.setLineWidth(round(1.5 / MM_TO_PX, 3))
    hatch.setColor(QColor(style.get("color") or symbology.DEFAULT_COLOR))
    return hatch


def _raster_layer(uri, block):
    """A `QgsRasterFillSymbolLayer` over the tile's pixels, written to a file QGIS can read.

    QGIS wants a PATH, not bytes, and it reads that path lazily — every repaint — so the file has to
    outlive this call. Written into QGIS's own profile directory under a CONTENT hash: the same tile
    always lands on the same path, so opening one layer twice writes one file, and two layers
    sharing a pattern share it.
    """
    from qgis.core import QgsRasterFillSymbolLayer
    path = image_source(uri) or picture_file(uri)
    if not path:
        return None
    layer = QgsRasterFillSymbolLayer()
    layer.setImageFilePath(path)
    width = block.get("width")
    if width:
        layer.setWidth(round(float(width) / MM_TO_PX, 3))
    return layer


def _pattern_dir() -> str:
    """Where rebuilt pattern images live. Inside QGIS's own profile, so they survive a restart and
    are removed with the profile rather than accumulating in a temp directory nobody cleans."""
    import os
    try:
        from qgis.core import QgsApplication
        base = QgsApplication.qgisSettingsDirPath()
    except Exception:                   # noqa: BLE001 - fall back to a temp directory
        base = ""
    if not base:
        import tempfile
        base = tempfile.gettempdir()
    out = os.path.join(base, "geodeploy_patterns")
    os.makedirs(out, exist_ok=True)
    return out


# ── Pictures back INTO QGIS ──────────────────────────────────────────────────────────────────────
#
# THE SAME BUG THREE MORE TIMES. `fill_pattern` could be read out of QGIS and never put back, so a
# hatched layer opened as a plain colour. `marker_image`, `line_marker` and `centroid_marker` were
# in exactly the same state — every one of them written by `from_qgis` and read by nothing — and a
# systematic GeoDeploy → QGIS → GeoDeploy test over the whole vocabulary is what found them, rather
# than three more bug reports.
#
# All three are PIXELS, so all three rebuild the same way: write the data URI to a file QGIS can
# read, and point a raster symbol layer at it.


def picture_of(style, key):
    """The `data:` URI a style carries under `key`, or "". `marker_image` is a bare string; the
    others are blocks with the image inside, and callers should not have to know which."""
    value = (style or {}).get(key)
    if isinstance(value, dict):
        value = value.get("image")
    return value if isinstance(value, str) and value.startswith("data:image/") else ""


def image_source(uri: str) -> str:
    """A picture in the form QGIS's raster symbol layers take, or "".

    `base64:` EMBEDDING RATHER THAN A FILE, verified accepted by
    `QgsRasterFillSymbolLayer.setImageFilePath`. Writing a file worked here and did not on a
    reporter's Windows QGIS — the hatch was in the symbol and nothing drew, which is what a path
    QGIS cannot read looks like, because it reads that path lazily on every repaint and has nowhere
    to complain to.

    Embedding removes the filesystem from the question: no profile directory to differ, no
    permissions, no separators, and the symbol stays self-contained if the project is saved or moved
    to another machine. `picture_file` remains for a QGIS too old to accept the prefix.
    """
    if not isinstance(uri, str) or not uri.startswith("data:image/"):
        return ""
    payload = uri.partition(",")[2]
    return ("base64:" + payload) if payload else ""


def picture_file(uri: str):
    """A `data:image/…` URI written to a file QGIS can read, or None.

    QGIS wants a PATH and reads it lazily — every repaint — so the file must outlive this call.
    Named by a CONTENT hash in QGIS's own profile directory: the same picture always lands on the
    same path, so opening a layer twice writes one file and two layers sharing a marker share it.
    """
    import base64
    import hashlib
    import os

    if not isinstance(uri, str) or not uri.startswith("data:image/"):
        return None
    head, _, payload = uri.partition(",")
    if not payload:
        return None
    try:
        raw = base64.b64decode(payload)
    except Exception:                   # noqa: BLE001  # nosec B110 - a URI we cannot decode
        return None
    ext = ".png" if "png" in head else (".jpg" if "jpeg" in head else ".img")
    digest = hashlib.sha1(raw).hexdigest()[:16]     # nosec B324 - a filename, not a credential
    path = os.path.join(_pattern_dir(), "gd-pic-{0}{1}".format(digest, ext))
    if not os.path.exists(path):
        try:
            with open(path, "wb") as fh:
                fh.write(raw)
        except OSError:
            return None
    return path


def raster_marker(uri: str, size_px=None):
    """A `QgsRasterMarkerSymbolLayer` over a picture, or None.

    THE SIZE COMES FROM THE STYLE, NOT FROM THE BITMAP. `size_px` is the marker's diameter in CSS
    pixels — the same number the map draws it at — so the picture's own resolution is irrelevant
    here, and tying the two together is how a marker could change size when only the rendering
    resolution changed. `symbology._rendered_at` states the other half of the contract.
    """
    # The file is the fallback here rather than the first choice, for the same reason as the fill.
    path = image_source(uri) or picture_file(uri)
    if not path:
        return None
    try:
        from qgis.core import QgsRasterMarkerSymbolLayer
        marker = QgsRasterMarkerSymbolLayer(path)
        if size_px:
            marker.setSize(round(float(size_px) / MM_TO_PX, 3))
        return marker
    except Exception as exc:            # noqa: BLE001 - a marker is never worth failing a style
        symbology._log("Could not rebuild this marker picture ({0}: {1}).".format(
            type(exc).__name__, exc))
        return None


def marker_to_qgis(symbol, style) -> bool:
    """Replace a marker symbol's layers with the picture the style carries. True when done."""
    uri = (style or {}).get("marker_image")
    if symbol is None or not isinstance(uri, str):
        return False
    marker = raster_marker(uri, symbology._number(style.get("radius"), 5) * 2)
    if marker is None:
        return False
    return _only_layer(symbol, marker)


def line_marker_to_qgis(symbol, style) -> bool:
    """Put the style's repeated marker back along the line, over the stroke it already has."""
    block = (style or {}).get("line_marker")
    if symbol is None or not isinstance(block, dict):
        return False
    # SIZED FROM THE BLOCK, not left at the raster marker's default — see the note beside `size`
    # in `_line_decoration_symbol`.
    marker = raster_marker(block.get("image"), symbology._number(block.get("size"), None))
    if marker is None:
        return False
    try:
        from qgis.core import QgsMarkerLineSymbolLayer, QgsMarkerSymbol
        sub = QgsMarkerSymbol()
        sub.changeSymbolLayer(0, marker)
        deco = QgsMarkerLineSymbolLayer()
        deco.setSubSymbol(sub)          # a NEW symbol, so nothing borrowed is handed back
        spacing = symbology._number(block.get("spacing"), None)
        if spacing:
            # `symbol-spacing` is pixels; QGIS's interval is in the layer's own unit (points).
            deco.setInterval(round(spacing * symbology.CSS_PX_TO_POINTS, 2))
        # APPENDED, not replacing: a decorated line is a stroke WITH markers on it, and dropping the
        # stroke would lose the road under the ticks — the same mistake `_line_decoration_symbol`
        # exists to avoid when reading.
        symbol.appendSymbolLayer(deco)
        # …unless there is no stroke. A line of markers has a width of ZERO, and leaving the
        # invisible `Simple Line` standing puts a layer in the user's symbology dialog that their
        # original never had — which is exactly how the returned symbol was reported: "Marker Line"
        # plus a "Simple Line" nobody drew. Removed back to front so the indices stay valid.
        if not symbology._number(style.get("line_width"), 1):
            for index in range(symbol.symbolLayerCount() - 2, -1, -1):
                layer = symbol.symbolLayer(index)
                if type(layer).__name__ == "QgsSimpleLineSymbolLayer":
                    symbol.deleteSymbolLayer(index)
        return True
    except Exception as exc:            # noqa: BLE001
        symbology._log("Could not rebuild the markers along this line ({0}: {1}).".format(
            type(exc).__name__, exc))
        return False


def centroid_to_qgis(symbol, style) -> bool:
    """Put the style's centre marker back as a QGIS centroid fill, over the plain fill."""
    block = (style or {}).get("centroid_marker")
    if symbol is None or not isinstance(block, dict):
        return False
    marker = raster_marker(block.get("image"))
    if marker is None:
        return False
    try:
        from qgis.core import QgsCentroidFillSymbolLayer, QgsMarkerSymbol
        sub = QgsMarkerSymbol()
        sub.changeSymbolLayer(0, marker)
        centroid = QgsCentroidFillSymbolLayer()
        centroid.setSubSymbol(sub)
        symbol.changeSymbolLayer(0, _plain_fill(style))
        for i in range(symbol.symbolLayerCount() - 1, 0, -1):
            symbol.deleteSymbolLayer(i)
        symbol.appendSymbolLayer(centroid)
        return True
    except Exception as exc:            # noqa: BLE001
        symbology._log("Could not rebuild this centre marker ({0}: {1}).".format(
            type(exc).__name__, exc))
        return False


def _only_layer(symbol, layer) -> bool:
    """Make `layer` the symbol's single layer. A QgsSymbol must always hold at least one, so this
    changes the first and deletes the rest rather than clearing and refilling."""
    if symbol.symbolLayerCount():
        symbol.changeSymbolLayer(0, layer)
        for i in range(symbol.symbolLayerCount() - 1, 0, -1):
            symbol.deleteSymbolLayer(i)
    else:                               # pragma: no cover - a symbol with no layers is not a thing
        symbol.appendSymbolLayer(layer)
    return True
