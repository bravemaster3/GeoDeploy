"""Symbology — the style dict a layer config carries, built from plain arguments.

The vocabulary is `api/geodeploy/services/symbology.py`'s, exactly: single-symbol keys (`color`,
`fill_opacity`, `outline_color`, `line_width`, `radius`, `marker`, `lineType`) plus the v1.1
data-driven ones (`color_mode`/`color_field`/`classes`/`categories`/`other_color`,
`size_mode`/`size_field`/`size_stops`, `extrusion`). This module only ASSEMBLES that dict.

**The classification maths is not reimplemented here, on purpose.** Breaks come from
`GET /data/vector/{ref}/field-stats`, which computes them with the same module the editor preview
and the published portal use. A second quantile implementation in the client would eventually
disagree with the server's, and the disagreement would first be visible as a published map whose
legend does not match its colours — the exact failure `services/symbology.py` was extracted to
prevent.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

from .errors import ValidationError

#: Named ramps the server knows (`symbology.RAMPS`). Listed so the CLI can reject a typo up front
#: instead of silently falling back to viridis on the server.
RAMPS = ("viridis", "magma", "blues", "reds", "greens", "oranges", "rdbu", "brbg", "spectral")

MARKERS = ("circle", "square", "triangle", "diamond", "star", "cross")
LINE_TYPES = ("solid", "dashed", "dotted")
CLASSIFY_METHODS = ("quantile", "equal", "jenks")
COLOR_MODES = ("single", "graduated", "categorized")

#: `outline_color="none"` means draw NO outline — a sentinel, not an empty string, because "" is
#: what an uninitialised colour input produces and would silently turn outlines off.
NO_OUTLINE = "none"

#: CLI/keyword name → the key the style dict actually uses. Only `line_type` differs, and it
#: differs because the stored key is camelCase (`lineType`) for historical reasons.
_SIMPLE_KEYS = (
    ("color", "color"),
    ("fill_opacity", "fill_opacity"),
    ("outline_color", "outline_color"),
    ("outline_width", "outline_width"),
    ("line_width", "line_width"),
    ("radius", "radius"),
    ("marker", "marker"),
    ("line_type", "lineType"),
    ("colormap", "colormap"),
    ("rescale", "rescale"),
    ("algorithm", "algorithm"),
    ("zfactor", "zfactor"),
    ("bidx", "bidx"),
    ("other_color", "other_color"),
    ("color_field", "color_field"),
    ("color_mode", "color_mode"),
    ("size_field", "size_field"),
    ("size_mode", "size_mode"),
)


def build_style(base: Optional[Dict[str, Any]] = None, **kw: Any) -> Dict[str, Any]:
    """Merge only the styling arguments that were actually given onto `base`.

    "Only what was given" is the whole contract: `geodeploy portals style … --color red` must not
    reset the marker shape and the classes someone spent ten minutes on, so an argument left at
    None is absent, not a default.
    """
    style = dict(base or {})
    for arg, key in _SIMPLE_KEYS:
        value = kw.get(arg)
        if value is not None:
            style[key] = value

    _validate(style)

    stops = kw.get("size_stops")
    if stops is not None:
        style["size_stops"] = parse_size_stops(stops)
        style.setdefault("size_mode", "proportional")
    if style.get("size_mode") == "proportional" and not style.get("size_field"):
        raise ValidationError(400, "Proportional size needs a field (--size-field).")

    classes = kw.get("classes")
    if classes is not None:
        style["classes"] = classes
        style["color_mode"] = "graduated"
    categories = kw.get("categories")
    if categories is not None:
        style["categories"] = categories
        style["color_mode"] = "categorized"

    extrusion = build_extrusion(style.get("extrusion"), **kw)
    if extrusion is not None:
        style["extrusion"] = extrusion

    if kw.get("clear_classification"):
        for key in ("color_mode", "color_field", "classes", "categories", "other_color"):
            style.pop(key, None)
    return style


def build_extrusion(base: Optional[Dict[str, Any]] = None, **kw: Any) -> Optional[Dict[str, Any]]:
    """The 3D block, or None when no 3D argument was given.

    Points get a footprint `radius` in METRES (a point has no area, so extruding one needs a width
    that polygons get for free); left unset, the server derives it from the layer's own extent —
    which is why this does not invent a default: a fixed 30 m bar is invisible on a world map.
    """
    fields = (("extrude", "enabled"), ("extrude_field", "field"), ("extrude_scale", "scale"),
              ("extrude_base", "base"), ("extrude_color", "color"),
              ("extrude_opacity", "opacity"), ("extrude_radius", "radius"))
    given = {key: kw.get(arg) for arg, key in fields if kw.get(arg) is not None}
    if not given:
        return None
    out = dict(base or {})
    out.update(given)
    if out.get("enabled") and not (out.get("field") or out.get("height")):
        raise ValidationError(400, "3D extrusion needs a numeric field (--extrude-field).")
    return out


def parse_size_stops(raw: Any) -> List[List[float]]:
    """`"0:2,100:12"` (or an already-parsed list) → `[[0, 2], [100, 12]]`, ascending.

    Two stops minimum, because `interpolate` between one point is not an interpolation — the server
    silently falls back to the fixed radius, which looks like the flag did nothing.
    """
    if isinstance(raw, (list, tuple)):
        pairs = [list(p) for p in raw]
    else:
        pairs = []
        for chunk in str(raw).split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            if ":" not in chunk:
                raise ValidationError(400, "Size stops look like value:size — got {0!r}.".format(chunk))
            value, _, size = chunk.partition(":")
            try:
                pairs.append([float(value), float(size)])
            except ValueError:
                raise ValidationError(400, "Size stop {0!r} is not numeric.".format(chunk))
    if len(pairs) < 2:
        raise ValidationError(400, "Give at least two size stops, e.g. 0:2,1000:12.")
    return sorted(pairs, key=lambda p: p[0])


def parse_classes(raw: Any) -> List[Dict[str, Any]]:
    """`"0-10:#fee,10-50:#f00"` → the `classes` list. `*` is an open edge (`<10`, `≥50`)."""
    if isinstance(raw, list):
        return raw
    out = []  # type: List[Dict[str, Any]]
    for chunk in str(raw).split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        span, _, color = chunk.rpartition(":")
        if not span or not color:
            raise ValidationError(400, "Classes look like min-max:#colour — got {0!r}.".format(chunk))
        lo, _, hi = span.partition("-")
        out.append({"min": _edge(lo), "max": _edge(hi), "color": color.strip()})
    if not out:
        raise ValidationError(400, "No classes parsed.")
    return out


def parse_categories(raw: Any) -> List[Dict[str, Any]]:
    """`"forest:#2c7,water:#39f"` → the `categories` list."""
    if isinstance(raw, list):
        return raw
    out = []  # type: List[Dict[str, Any]]
    for chunk in str(raw).split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        value, _, color = chunk.rpartition(":")
        if not value or not color:
            raise ValidationError(400, "Categories look like value:#colour — got {0!r}.".format(chunk))
        out.append({"value": value.strip(), "color": color.strip()})
    if not out:
        raise ValidationError(400, "No categories parsed.")
    return out


def _edge(text: str) -> Optional[float]:
    text = (text or "").strip()
    if text in ("", "*", "inf", "-inf", "none"):
        return None
    try:
        return float(text)
    except ValueError:
        raise ValidationError(400, "{0!r} is not a number.".format(text))


def classify(client: Any, layer_ref: Any, field: str, mode: Optional[str] = None,
             classes: int = 5, method: str = "quantile", ramp: str = "viridis",
             base: Optional[Dict[str, Any]] = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Ask the server to classify `field`, and fold the answer into a style dict.

    Returns `(style, stats)` — the stats carry the distribution the server saw, which is what makes
    "why are all my features one colour" answerable (a column that is 90 % one value collapses its
    quantile breaks, and the server drops the empty classes rather than publish a legend the map
    does not match).
    """
    if method not in CLASSIFY_METHODS:
        raise ValidationError(400, "Classification method must be one of {0}.".format(
            ", ".join(CLASSIFY_METHODS)))
    if ramp not in RAMPS:
        raise ValidationError(400, "Unknown colour ramp {0!r}. Known: {1}".format(
            ramp, ", ".join(RAMPS)))
    stats = client.vector.field_stats(layer_ref, field, classes=classes, method=method, ramp=ramp)
    suggestion = (stats or {}).get("suggestion") or {}
    kind = (stats or {}).get("kind")
    resolved = mode or suggestion.get("color_mode") or (
        "graduated" if kind == "numeric" else "categorized")

    style = dict(base or {})
    style["color_field"] = field
    style["color_mode"] = resolved
    if resolved == "graduated":
        found = suggestion.get("classes") or []
        if not found:
            raise ValidationError(
                400, "{0!r} produced no classes — it is {1}, and graduated colouring needs a "
                     "numeric column.".format(field, kind or "not numeric"))
        style["classes"] = found
        style.pop("categories", None)
    else:
        found = suggestion.get("categories") or []
        if not found:
            raise ValidationError(400, "{0!r} produced no categories.".format(field))
        style["categories"] = found
        style.pop("classes", None)
    return style, stats


def _validate(style: Dict[str, Any]) -> None:
    marker = style.get("marker")
    if marker is not None and marker not in MARKERS:
        raise ValidationError(400, "Marker must be one of {0}.".format(", ".join(MARKERS)))
    line_type = style.get("lineType")
    if line_type is not None and line_type not in LINE_TYPES:
        raise ValidationError(400, "Line type must be one of {0}.".format(", ".join(LINE_TYPES)))
    mode = style.get("color_mode")
    if mode is not None and mode not in COLOR_MODES:
        raise ValidationError(400, "Colour mode must be one of {0}.".format(", ".join(COLOR_MODES)))
    for key in ("fill_opacity", "outline_width"):
        value = style.get(key)
        if value is not None and not 0 <= float(value) <= 1:
            raise ValidationError(400, "{0} is a fraction between 0 and 1.".format(key))


def describe(style: Dict[str, Any]) -> str:
    """A one-line human summary of a style — what a table cell shows."""
    if not style:
        return "default"
    mode = style.get("color_mode") or "single"
    if mode == "graduated" and style.get("color_field"):
        return "graduated on {0} ({1} classes)".format(style["color_field"],
                                                       len(style.get("classes") or []))
    if mode == "categorized" and style.get("color_field"):
        return "categorized on {0} ({1} values)".format(style["color_field"],
                                                        len(style.get("categories") or []))
    bits = []  # type: List[str]
    for key in ("color", "marker", "radius", "line_width", "colormap", "rescale"):
        if style.get(key) is not None:
            bits.append("{0}={1}".format(key, style[key]))
    if (style.get("extrusion") or {}).get("enabled"):
        bits.append("3D")
    return ", ".join(bits) or "default"


def merge_iterable(pairs: Iterable[Tuple[str, Any]]) -> Dict[str, Any]:  # pragma: no cover - util
    return {k: v for k, v in pairs if v is not None}
