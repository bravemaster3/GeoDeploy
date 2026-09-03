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

#: Every key a RASTER style is made of — the twin of `services/titiler.STYLE_KEYS` and of
#: `ui/src/lib/mapStyle.RASTER_STYLE_KEYS`. `opacity` is not one of them: the map applies it, the
#: tile server does not. Named once because the same list, written out by hand, had already fallen
#: behind in four UI components, seven API call sites and this CLI — and a key that is merely
#: FORGOTTEN does not fail. It quietly serves the layer in a style nobody chose.
RASTER_STYLE_KEYS = ("colormap", "colormap_reverse", "rescale", "algorithm", "zfactor", "bidx",
                     "color_classes", "increment", "thickness", "minz", "maxz")

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
    # Contours: spacing, line width, and the range its background relief is coloured over.
    ("increment", "increment"),
    ("thickness", "thickness"),
    ("minz", "minz"),
    ("maxz", "maxz"),
    ("bidx", "bidx"),
    ("other_color", "other_color"),
    ("color_field", "color_field"),
    ("color_mode", "color_mode"),
    ("color_ramp", "color_ramp"),
    ("color_ramp_reverse", "color_ramp_reverse"),
    ("classes_n", "classes_n"),
    ("size_field", "size_field"),
    ("size_mode", "size_mode"),
    # 2026-09-03: things MapLibre draws natively that GeoDeploy had no word for until the QGIS
    # round trip needed them. Each is an EXACT round trip rather than an approximation.
    ("line_cap", "line_cap"),
    ("line_join", "line_join"),
    ("line_offset", "line_offset"),
    ("marker_rotation", "marker_rotation"),
    ("marker_opacity", "marker_opacity"),
    ("min_zoom", "minzoom"),
    ("max_zoom", "maxzoom"),
)

#: The fontstacks a portal can serve — the twin of `services/symbology.LABEL_FONTS`. A stack the
#: glyph source lacks renders as NOTHING at all, so this list is deliberately short.
LABEL_FONTS = ("Noto Sans Regular", "Noto Sans Bold", "Noto Sans Italic")

LINE_CAPS = ("butt", "round", "square")
LINE_JOINS = ("bevel", "round", "miter")


def parse_number_list(raw: Any, label: str, length: Optional[int] = None) -> List[float]:
    """`"3,2,1,2"` or a real list → `[3.0, 2.0, 1.0, 2.0]`. Used by dash patterns and offsets."""
    if isinstance(raw, str):
        raw = [part for part in raw.replace(" ", "").split(",") if part]
    if not isinstance(raw, (list, tuple)):
        raise ValidationError(400, "{0} must be a comma-separated list of numbers.".format(label))
    out = []
    for part in raw:
        try:
            out.append(float(part))
        except (TypeError, ValueError):
            raise ValidationError(400, "{0}: {1!r} is not a number.".format(label, part))
    if length is not None and len(out) != length:
        raise ValidationError(400, "{0} needs exactly {1} numbers.".format(label, length))
    return out


#: The scale denominator one Web Mercator tile pixel covers at zoom 0, at the equator and 96 dpi.
#: MapLibre, QGIS and every slippy-map tool agree on this number; it is what makes a QGIS scale
#: threshold and a MapLibre zoom threshold the same statement about the map.
SCALE_AT_ZOOM_0 = 559082264.0287178


def zoom_for_scale(denominator: Any) -> Optional[float]:
    """A QGIS scale DENOMINATOR as a MapLibre zoom level, or None for "no limit".

    QGIS states visibility as a scale range and MapLibre as a zoom range, and the two run in
    OPPOSITE directions: a denominator gets larger as you zoom out, a zoom level smaller. So a
    layer's `minimumScale` (its most-zoomed-OUT limit, the larger denominator) becomes `minzoom`,
    and `maximumScale` becomes `maxzoom` — which reads backwards and is right.

    0 is QGIS's "unset" for both ends, and it must not become zoom 29: a rule with no lower limit
    would then be invisible everywhere.
    """
    try:
        value = float(denominator)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    import math
    return round(math.log(SCALE_AT_ZOOM_0 / value, 2), 3)


def scale_for_zoom(zoom: Any) -> Optional[float]:
    """The inverse, for writing a MapLibre zoom back into a QGIS scale threshold."""
    try:
        value = float(zoom)
    except (TypeError, ValueError):
        return None
    return SCALE_AT_ZOOM_0 / (2.0 ** value)


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
    for key, allowed, flag in (("line_cap", LINE_CAPS, "--line-cap"),
                               ("line_join", LINE_JOINS, "--line-join")):
        value = style.get(key)
        if value is not None and str(value).lower() not in allowed:
            # MapLibre rejects the WHOLE style over an invalid enum, so this must fail here rather
            # than produce a portal that renders nothing.
            raise ValidationError(400, "{0} must be one of: {1}.".format(flag, ", ".join(allowed)))

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

    dash = kw.get("dash_pattern")
    if dash is not None:
        # In MULTIPLES OF THE LINE WIDTH, which is MapLibre's unit — a pattern in pixels would
        # change shape every time somebody changed the width.
        pattern = parse_number_list(dash, "--dash-pattern")
        if len(pattern) < 2:
            raise ValidationError(400, "--dash-pattern needs at least a dash and a gap.")
        style["dash_pattern"] = pattern
    if kw.get("no_dash_pattern"):
        style.pop("dash_pattern", None)
    offset = kw.get("marker_offset")
    if offset is not None:
        style["marker_offset"] = parse_number_list(offset, "--marker-offset", length=2)
    if kw.get("no_symbol") is not None:
        style["no_symbol"] = bool(kw["no_symbol"])
        if not style["no_symbol"]:
            style.pop("no_symbol", None)

    labels = kw.get("labels")
    if labels:
        # MERGED, not replaced: `--label-size 14` alone must not wipe the colour and halo somebody
        # already set, exactly as `--color` does not reset the marker shape.
        merged = dict(style.get("labels") or {})
        merged.update({k: v for k, v in labels.items() if v is not None})
        # Naming any label property turns labelling ON — asking for a label size on an unlabelled
        # layer and getting nothing would be a puzzle, not a safeguard.
        merged["enabled"] = True
        style["labels"] = merged
    if kw.get("clear_labels"):
        style.pop("labels", None)

    rules = kw.get("rules")
    if rules is not None:
        style["rules"] = parse_rules(rules)

    if kw.get("clear_classification"):
        for key in ("color_mode", "color_field", "classes", "categories", "other_color"):
            style.pop(key, None)
    if kw.get("clear_rules"):
        # Separate from `clear_classification` because they are different modes, not two spellings
        # of one: dropping the rules of a rule-based layer leaves the single symbol underneath,
        # which is the layer's own shape and the sensible thing to fall back to.
        style.pop("rules", None)
    return style


def parse_rules(raw: Any) -> List[Dict[str, Any]]:
    """Validate a rule list far enough that a broken one fails HERE rather than on a published map.

    Deliberately shallow: the FILTER is not re-checked, because it was produced by
    `expressions.to_maplibre` and re-deriving it in a second place is exactly the drift this package
    avoids elsewhere. What is checked is the shape a renderer will index into.
    """
    if isinstance(raw, str):
        import json
        try:
            raw = json.loads(raw)
        except ValueError as exc:
            raise ValidationError(400, "Rules must be JSON: {0}".format(exc))
    if not isinstance(raw, list):
        raise ValidationError(400, "Rules must be a list of rule objects.")
    out: List[Dict[str, Any]] = []
    for i, rule in enumerate(raw):
        if not isinstance(rule, dict):
            raise ValidationError(400, "Rule {0} is not an object.".format(i + 1))
        entry: Dict[str, Any] = {"label": str(rule.get("label") or "")}
        if rule.get("filter") is not None:
            entry["filter"] = rule["filter"]
        if rule.get("expression"):
            entry["expression"] = str(rule["expression"])
        inner = rule.get("style")
        if inner is not None and not isinstance(inner, dict):
            raise ValidationError(400, "Rule {0}'s style must be an object.".format(i + 1))
        entry["style"] = dict(inner or {})
        for key in ("minzoom", "maxzoom"):
            if rule.get(key) is None:
                continue
            try:
                entry[key] = float(rule[key])
            except (TypeError, ValueError):
                raise ValidationError(400, "Rule {0}'s {1} must be a number.".format(i + 1, key))
        out.append(entry)
    return out


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
             reverse: bool = False,
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
    stats = client.vector.field_stats(layer_ref, field, classes=classes, method=method, ramp=ramp,
                                      reverse=reverse)
    suggestion = (stats or {}).get("suggestion") or {}
    kind = (stats or {}).get("kind")
    resolved = mode or suggestion.get("color_mode") or (
        "graduated" if kind == "numeric" else "categorized")

    style = dict(base or {})
    style["color_field"] = field
    style["color_mode"] = resolved
    style["color_ramp"] = ramp
    # Recorded so the direction survives a re-classify: the class COLOURS are stored per class, so
    # without this a later change of method or class count would silently un-reverse the ramp.
    style["color_ramp_reverse"] = bool(reverse)
    # What was ASKED for, which is not always what came back — repeated values collapse a break, and
    # some columns yield one class however many you request. The editor shows this number in its
    # Classes box; storing it here means a CLI-classified layer opens in the browser showing the
    # count you asked for rather than the count you got.
    style["classes_n"] = int(classes)
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
    value = style.get("fill_opacity")
    if value is not None and not 0 <= float(value) <= 1:
        raise ValidationError(400, "fill_opacity is a fraction between 0 and 1.")
    # `outline_width` MEANS TWO THINGS and this validator cannot see the geometry: on a POINT it is
    # a fraction of the radius (0-1), on a POLYGON a width in CSS pixels. Refusing anything above 1
    # made a polygon border impossible to set from here at all — "outline_width is a fraction
    # between 0 and 1" for a perfectly ordinary 3 px edge. The renderers clamp for their own
    # geometry (a marker ratio is capped at 1, a polygon width at 40), so the check here is only a
    # sanity bound on a number that has no single unit.
    value = style.get("outline_width")
    if value is not None and not 0 <= float(value) <= 40:
        raise ValidationError(
            400, "outline_width is a fraction of the radius on points (0-1) and a width in pixels "
                 "on polygons (up to 40).")


class Style(object):
    """A stored style, READ. The reverse direction of `build_style`.

    `build_style` exists so a caller can assemble the vocabulary; nothing existed to take it apart
    again, so anything rendering a GeoDeploy layer somewhere else — the QGIS plugin first — had to
    reach into the dict and re-decide what `color_mode: "graduated"` implies, which keys are
    authoritative, and what a missing one defaults to. That is a second definition of the
    vocabulary, in a project that already keeps three surfaces in step by hand.

    So this is a READER, not a schema: it never rejects, never fills in a server default it cannot
    know, and keeps `.raw` so a caller can reach anything not modelled here. `to_dict()` returns the
    original dict, which is what makes build → parse → build lossless.
    """

    __slots__ = ("raw",)

    def __init__(self, style: Optional[Dict[str, Any]] = None):
        self.raw = dict(style or {})

    # -- what kind of symbology is this ------------------------------------------------------------

    @property
    def mode(self) -> str:
        """`single` | `graduated` | `categorized` | `rules` — never None, so a caller can switch.

        RULES OUTRANK `color_mode`. A rule-based style also carries the first rule's shape at the
        top level, `color_mode: "single"` included, so that a renderer knowing nothing about rules
        still draws something recognisable. Reading that first would report a rule-based layer as a
        plain one — and then a push would flatten it.
        """
        if self.raw.get("rules"):
            return "rules"
        return self.raw.get("color_mode") or "single"

    @property
    def rules(self) -> List[Dict[str, Any]]:
        """The rule list, or `[]`. Each entry is `{label, expression, filter, style, minzoom,
        maxzoom}` — see the QGIS plugin's `rules.py` for what each means and who writes it."""
        raw = self.raw.get("rules")
        return [r for r in raw if isinstance(r, dict)] if isinstance(raw, list) else []

    @property
    def field(self) -> Optional[str]:
        """The attribute the colours are driven by, or None for a single symbol."""
        return self.raw.get("color_field") if self.mode != "single" else None

    @property
    def is_data_driven(self) -> bool:
        return bool(self.field) and self.mode in ("graduated", "categorized")

    # -- the pieces a renderer needs ---------------------------------------------------------------

    @property
    def color(self) -> Optional[str]:
        return self.raw.get("color")

    @property
    def classes(self) -> List[Dict[str, Any]]:
        """`[{min, max, color}]` for a graduated style. `min`/`max` may be None at the ends — that
        is an OPEN bucket ("< 10", "≥ 90"), not missing data."""
        return [c for c in (self.raw.get("classes") or []) if isinstance(c, dict)]

    @property
    def categories(self) -> List[Dict[str, Any]]:
        """`[{value, color}]` for a categorized style. Anything not listed takes `other_color`."""
        return [c for c in (self.raw.get("categories") or []) if isinstance(c, dict)]

    @property
    def other_color(self) -> Optional[str]:
        return self.raw.get("other_color")

    @property
    def requested_classes(self) -> Optional[int]:
        """How many classes were ASKED for. `len(style.classes)` is how many came back, and the two
        differ whenever repeated values collapse a break — so a UI that shows the second number as
        the input fights the user (issue #10)."""
        value = self.raw.get("classes_n")
        return int(value) if isinstance(value, (int, float)) else None

    @property
    def ramp(self) -> Optional[str]:
        """The ramp the classes were generated from, when one was recorded. The class colours are
        stored per class, so this is provenance — not what a renderer should read to draw."""
        return self.raw.get("color_ramp")

    @property
    def ramp_reversed(self) -> bool:
        return bool(self.raw.get("color_ramp_reverse"))

    @property
    def size(self) -> Optional[Dict[str, Any]]:
        """`{"field": …, "stops": [[value, size], …]}` when size is data-driven, else None.

        Both halves are required: a field with no stops cannot be interpolated, and stops with no
        field have nothing to read.
        """
        field, stops = self.raw.get("size_field"), self.raw.get("size_stops")
        if not field or not stops:
            return None
        return {"field": field, "stops": [list(s) for s in stops if len(s) == 2]}

    @property
    def extrusion(self) -> Optional[Dict[str, Any]]:
        """The 3D block when it is switched ON. `{"enabled": False}` reads as None: a renderer that
        checks truthiness on the dict alone would extrude a layer the author turned off."""
        ext = self.raw.get("extrusion")
        if isinstance(ext, dict) and ext.get("enabled"):
            return dict(ext)
        return None

    # -- raster ------------------------------------------------------------------------------------

    @property
    def colormap(self) -> Optional[str]:
        return self.raw.get("colormap")

    @property
    def rescale(self) -> Optional[List[float]]:
        """The stretch as numbers. Stored as TiTiler wants it — the string "min,max" — so a caller
        that wants to compute with it should not have to parse it again."""
        value = self.raw.get("rescale")
        if isinstance(value, str):
            try:
                return [float(v) for v in value.split(",")]
            except ValueError:
                return None
        if isinstance(value, (list, tuple)) and len(value) == 2:
            try:
                return [float(v) for v in value]
            except (TypeError, ValueError):
                return None
        return None

    # -- output --------------------------------------------------------------------------------------

    def legend(self) -> List[Dict[str, Any]]:
        """Swatches and labels, WITHOUT asking the server — mirrors `symbology.legend_entries`.

        Prefer `client.vector.legend(ref)`: the server's answer is the one the portal drew. This is
        for a caller that already holds a style dict (a portal's layer_config, an unsaved edit in a
        dialog) and cannot make a request per keystroke. The two agree; `test_styles_jobs` pins the
        labels against the server's format.
        """
        if self.mode == "rules":
            # One entry per rule, in the order they draw. A rule's label is what its author wrote in
            # QGIS's rule editor, so it is already legend text; a rule with none falls back to its
            # expression, which is at least a description of what it selects.
            return [{"color": (r.get("style") or {}).get("color") or self.color,
                     "label": str(r.get("label") or r.get("expression") or "rule")}
                    for r in self.rules]
        if self.mode == "graduated":
            out = []
            for c in self.classes:
                lo, hi = c.get("min"), c.get("max")
                if lo is None and hi is None:
                    label = "all"
                elif lo is None:
                    label = "< {0}".format(_legend_num(hi))
                elif hi is None:
                    label = "≥ {0}".format(_legend_num(lo))
                else:
                    label = "{0} – {1}".format(_legend_num(lo), _legend_num(hi))
                out.append({"color": c.get("color"), "label": label})
            return out
        if self.mode == "categorized":
            out = [{"color": c.get("color"), "label": str(c.get("value"))} for c in self.categories]
            if out:
                out.append({"color": self.other_color or "#9ca3af", "label": "Other"})
            return out
        return []

    def to_dict(self) -> Dict[str, Any]:
        """The style as stored — unchanged, so `build_style(**…)` round-trips through this."""
        return dict(self.raw)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<Style {0}>".format(describe(self.raw))


def parse(style: Optional[Dict[str, Any]]) -> Style:
    """Read a stored style dict. Accepts a layer's `default_style` wrapper or the inner style."""
    if isinstance(style, dict) and "style" in style and isinstance(style["style"], dict):
        # A layer record carries {opacity, style: {...}, popup_fields}; a portal layer_config the
        # same. Taking either means a caller never has to remember which one they are holding.
        return Style(style["style"])
    return Style(style)


#: The package-level name. `styles.parse` reads well inside this module; at the top level, next to
#: `Client`, a bare `parse` says nothing about what it parses.
parse_style = parse


def _legend_num(value: Any) -> str:
    """Legend numbers, formatted as `symbology._num` does — an integer stays an integer."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number == int(number) and abs(number) < 1e15:
        return str(int(number))
    return "{0:,.2f}".format(number).rstrip("0").rstrip(".")


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
