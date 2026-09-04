"""QGIS's 2.5D renderer ⇄ GeoDeploy's extrusion.

## What 2.5D actually is, which is not what it looks like

`Qgs25DRenderer` is not a 3D renderer. It draws a flat map with a pseudo-perspective block under
each polygon: a shadow, walls raked off at an angle, and a roof. Underneath it is one ordinary
symbol whose three symbol layers are a simple fill (the shadow) and two **geometry generators** that
extrude the outline:

    order_parts(extrude(segments_to_lines(@geometry),
                        cos(radians(@qgis_25d_angle)) * @qgis_25d_height,
                        sin(radians(@qgis_25d_angle)) * @qgis_25d_height), …)

Two consequences, both of which shape everything below:

* **The height and the angle are PROJECT variables**, `@qgis_25d_height` and `@qgis_25d_angle`, not
  properties of the renderer — which is why `Qgs25DRenderer` exposes colours and shadow settings and
  no `height()` at all. They are read from and written to the project's expression scope.
* **The geometry generators cannot travel.** They produce new geometry from an expression, which no
  vector tile can carry. What CAN travel is the thing they are imitating: a real extrusion, which
  GeoDeploy already renders with `fill-extrusion`.

## So the mapping is to extrusion, and it is an approximation stated as one

A MapLibre `fill-extrusion` is *better* than 2.5D — it is genuinely three-dimensional and the
viewer can orbit it — but it is not the same picture, and two of QGIS's four colours have nowhere to
go: MapLibre's extrusion has one colour plus an optional vertical gradient, and no shadow at all.
So the roof colour becomes the extrusion colour, and the rest rides along in `extrusion.qgis25d`
untouched, so that a layer opened from GeoDeploy in QGIS comes back as 2.5D rather than as a plain
extrusion somebody has to rebuild.

**Units.** QGIS's 2.5D height is in the project's MAP UNITS; GeoDeploy's extrusion is in metres.
Those agree in a projected CRS and do not in a geographic one, which is the same gap the true-3D
path has and is tracked with it.
"""
from __future__ import annotations

import re as _re

try:                                    # a package, inside QGIS
    from . import symbology
except ImportError:                     # pragma: no cover - exec'd standalone by the test harness
    import symbology

try:                                    # pragma: no cover - only present inside QGIS
    from qgis.core import Qgs25DRenderer
    QGIS_25D = True
except ImportError:                     # pragma: no cover
    QGIS_25D = False

#: QGIS's own names for the two project variables the geometry generators read.
HEIGHT_VARIABLE = "qgis_25d_height"
ANGLE_VARIABLE = "qgis_25d_angle"

#: What QGIS uses when a project has never had a 2.5D layer in it.
DEFAULT_HEIGHT = 10.0
DEFAULT_ANGLE = 70.0


def is_25d(renderer) -> bool:
    return bool(QGIS_25D and renderer is not None and isinstance(renderer, Qgs25DRenderer))


def carried(style) -> dict:
    """The `extrusion.qgis25d` block, or `{}`. Present means "this came from a 2.5D renderer"."""
    extrusion = (style or {}).get("extrusion")
    block = extrusion.get("qgis25d") if isinstance(extrusion, dict) else None
    return block if isinstance(block, dict) else {}


# ── QGIS → GeoDeploy ─────────────────────────────────────────────────────────────────────────────

def from_qgis(qgis_layer, renderer):
    """`(style, notes)` for a 2.5D layer — a normal fill style plus an `extrusion` block."""
    notes = []
    if not is_25d(renderer):
        return None, notes

    height, height_field, height_scale, height_expr = _height_of()
    angle = _project_variable(ANGLE_VARIABLE, DEFAULT_ANGLE)

    roof = _hex_of(renderer, "roofColor") or symbology.DEFAULT_COLOR
    wall = _hex_of(renderer, "wallColor")
    shadow = _hex_of(renderer, "shadowColor")

    block = {"angle": angle}
    if wall:
        block["wall_color"] = wall
    if shadow:
        block["shadow_color"] = shadow
    for name, key in (("shadowSpread", "shadow_spread"),
                      ("shadowEnabled", "shadow_enabled"),
                      ("wallShadingEnabled", "wall_shading")):
        value = _call(renderer, name)
        if value is not None:
            block[key] = value

    extrusion = {"enabled": True, "color": roof, "qgis25d": block}
    # A DATA-DEFINED HEIGHT becomes a FIELD, which is what GeoDeploy's extrusion already speaks —
    # see `_height_of`. A plain number stays a number, and the two are mutually exclusive: a fixed
    # height left beside a field would be a value the renderers ignore and a reader would not.
    if height_field:
        extrusion["field"] = height_field
        if height_scale not in (None, 1, 1.0):
            extrusion["scale"] = height_scale
    elif height:
        extrusion["height"] = height

    # THE FLAT STYLE UNDERNEATH matters as much as the extrusion: the roof colour is what a viewer
    # sees from directly above, and it is what a 2D legend swatch should show. A reader that knows
    # nothing about extrusion still gets a sensible polygon.
    style = {"color_mode": "single", "color": roof, "fill_opacity": 1.0,
             "outline_color": wall or symbology.DEFAULT_FILL_OUTLINE,
             "extrusion": extrusion}

    notes.append("2.5D is drawn as a real 3D extrusion on the web, which is not the same picture: "
                 "its shadow and its viewing angle have no MapLibre equivalent and are stored "
                 "rather than drawn. Height {0} is in the project's MAP UNITS; GeoDeploy reads it "
                 "as metres, which agree only in a projected CRS.".format(height_field or height))
    if height_expr:
        # Said out loud rather than left as a silent fallback: the layer WILL draw, at a height
        # nobody chose, and the only clue would be that the buildings look wrong.
        notes.append("The 2.5D height is the expression `{0}`, which GeoDeploy's extrusion cannot "
                     "express — it reads a column, optionally scaled. The layer is pushed at a "
                     "fixed height of {1} instead; set a height field on it in GeoDeploy to drive "
                     "it from the data.".format(height_expr, height))
    return style, notes



#: `levels`, `"levels"`, `levels * 3`, `3 * levels` — the shapes a 2.5D height expression takes when
#: it is driven by a column. Anything more elaborate is a real expression and is reported, not
#: guessed at.
_FIELD_ONLY = _re.compile(r'^\s*"?([A-Za-z_][\w ]*?)"?\s*$')
_FIELD_TIMES = _re.compile(r'^\s*"?([A-Za-z_][\w ]*?)"?\s*\*\s*([0-9]*\.?[0-9]+)\s*$')
_TIMES_FIELD = _re.compile(r'^\s*([0-9]*\.?[0-9]+)\s*\*\s*"?([A-Za-z_][\w ]*?)"?\s*$')


def _height_of():
    """`(height, field, scale, unsupported_expression)` for the 2.5D height.

    QGIS's 2.5D dialog accepts an EXPRESSION for the height, not only a number, and stores whatever
    was typed in the project variable as a string. Reading that with `float()` raises, so every
    data-driven 2.5D layer silently arrived at the default height — the buildings drew, at a height
    nobody chose, with no error to trace. Verified against real QGIS: setting the variable to
    `levels`, `"levels"` or `levels * 3` stores the string unchanged, and `Qgs25DRenderer` has no
    height method at all, so the variable really is the only place to look.

    A bare column name, a quoted one, and a column times a constant become GeoDeploy's own
    `field`/`scale`, which is exactly the same picture. Anything richer is NOT guessed at: it falls
    back to the default height and is REPORTED, because a wrong height that draws is worse than one
    that says why it could not.
    """
    raw = _raw_variable(HEIGHT_VARIABLE)
    if raw in (None, ""):
        return DEFAULT_HEIGHT, None, None, None
    try:
        return float(raw), None, None, None
    except (TypeError, ValueError):
        pass
    text = str(raw).strip()
    match = _FIELD_ONLY.match(text)
    if match:
        return DEFAULT_HEIGHT, match.group(1).strip(), None, None
    match = _FIELD_TIMES.match(text)
    if match:
        return DEFAULT_HEIGHT, match.group(1).strip(), float(match.group(2)), None
    match = _TIMES_FIELD.match(text)
    if match:
        return DEFAULT_HEIGHT, match.group(2).strip(), float(match.group(1)), None
    return DEFAULT_HEIGHT, None, None, text


def _raw_variable(name):
    """The project variable UNCONVERTED — `_project_variable` coerces to float, which is the whole
    problem this exists to see around."""
    try:
        from qgis.core import QgsExpressionContextUtils, QgsProject
        return QgsExpressionContextUtils.projectScope(QgsProject.instance()).variable(name)
    except Exception:                   # noqa: BLE001  # nosec B110 - a missing variable is not an error
        return None

def _project_variable(name: str, fallback: float) -> float:
    """A project-scope expression variable as a float. 2.5D keeps its height and angle here."""
    try:
        from qgis.core import QgsExpressionContextUtils, QgsProject
        scope = QgsExpressionContextUtils.projectScope(QgsProject.instance())
        raw = scope.variable(name)
        return float(raw) if raw not in (None, "") else fallback
    except Exception:                   # noqa: BLE001 - a missing variable is not an error
        return fallback


def _call(renderer, name):
    fn = getattr(renderer, name, None)
    if not callable(fn):
        return None
    try:
        return fn()
    except Exception:                   # noqa: BLE001
        return None


def _hex_of(renderer, name):
    value = _call(renderer, name)
    try:
        return symbology._hex(value) if value is not None else None
    except Exception:                   # noqa: BLE001
        return None


# ── GeoDeploy → QGIS ─────────────────────────────────────────────────────────────────────────────

def to_qgis(qgis_layer, style) -> bool:
    """Rebuild a 2.5D renderer from a style that carries one. True when set.

    Only for a style whose `extrusion` carries `qgis25d` — i.e. one that CAME from 2.5D. A plain
    extrusion authored in GeoDeploy stays a 3D renderer in QGIS, because that is what it is; turning
    every extrusion into a pseudo-3D block would be inventing a picture nobody asked for.
    """
    block = carried(style)
    if not QGIS_25D or not block or qgis_layer is None:
        return False
    try:
        from qgis.PyQt.QtGui import QColor
        extrusion = (style or {}).get("extrusion") or {}

        # The variables FIRST: the geometry generators the renderer builds read them, so a renderer
        # installed before they are set draws at whatever the last project used.
        _set_project_variable(HEIGHT_VARIABLE, extrusion.get("height") or DEFAULT_HEIGHT)
        _set_project_variable(ANGLE_VARIABLE, block.get("angle") or DEFAULT_ANGLE)

        renderer = Qgs25DRenderer.convertFromRenderer(qgis_layer.renderer())
        if renderer is None:
            return False
        roof = extrusion.get("color") or (style or {}).get("color")
        for setter, value in (("setRoofColor", roof),
                              ("setWallColor", block.get("wall_color")),
                              ("setShadowColor", block.get("shadow_color"))):
            if value:
                fn = getattr(renderer, setter, None)
                if callable(fn):
                    fn(QColor(value))
        for setter, value in (("setShadowSpread", block.get("shadow_spread")),
                              ("setShadowEnabled", block.get("shadow_enabled")),
                              ("setWallShadingEnabled", block.get("wall_shading"))):
            if value is None:
                continue
            fn = getattr(renderer, setter, None)
            if callable(fn):
                fn(value)
        qgis_layer.setRenderer(renderer)
        qgis_layer.triggerRepaint()
        return True
    except Exception as exc:            # noqa: BLE001 - a style must never stop a layer loading
        symbology._log("Could not rebuild the 2.5D renderer: {0}: {1}".format(
            type(exc).__name__, exc))
        return False


def _set_project_variable(name: str, value) -> None:
    try:
        from qgis.core import QgsExpressionContextUtils, QgsProject
        QgsExpressionContextUtils.setProjectVariable(QgsProject.instance(), name, float(value))
    except Exception:                   # noqa: BLE001  # nosec B110 - a variable we cannot set is not fatal
        pass
