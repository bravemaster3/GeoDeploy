"""Data-driven symbology: a layer's friendly style keys → MapLibre expressions.

## Why this is its own module

A layer's colour used to be one string. It is now potentially a function of a feature property, and
that function has to be computed **identically in four places** (CLAUDE.md's parity rule):

  * `services/portal_generator.generate_style` — the published portal's MapLibre style,
  * `templates/shared/portal.js` — the live runtime (legend, symbology popover, marker icons),
  * `ui/src/views/PortalEditor.vue::buildPreviewStyle` — the editor preview,
  * `ui/src/components/portal/LayerPanel.vue` — the swatch drawn next to the layer name.

Three of those are JavaScript, so this module is deliberately **pure and small**: no database, no
ORM, no I/O. `ui/src/lib/symbology.js` is its line-by-line twin, and `tests/test_symbology.py`
pins the expressions both must produce. Keeping the classification *maths* here (rather than in each
renderer) is what stops the editor preview and the published portal disagreeing about which class a
feature falls in — a disagreement nobody would notice until a map was already published.

## The vocabulary

`layer_config.style` keeps every existing single-symbol key untouched (`color`, `fill_opacity`,
`outline_color`, `line_width`, `radius`, `marker`, `lineType`), so a portal styled before this
existed renders byte-identically. Data-driven styling is ADDITIVE:

    color_mode      "single" (default) | "graduated" | "categorized"
    color_field     the feature property to read
    color_ramp      a named ramp from RAMPS (used to (re)generate classes/categories)
    classes         [{"min": n, "max": n, "color": "#rrggbb"}, …]   graduated, ascending
    categories      [{"value": v, "color": "#rrggbb"}, …]           categorized
    other_color     colour for values matching no category (default #9ca3af)

    size_mode       "fixed" (default) | "proportional"
    size_field      the feature property to read
    size_stops      [[value, size], …] ascending — radius (points) or width (lines)

    extrusion       {"enabled": bool, "field": str, "scale": n, "base": n|str,
                     "color": "#rrggbb"|None, "opacity": n}

`color_mode` is separate from `size_mode` on purpose: colouring by one field while sizing by another
is a normal thing to want (population by colour, area by radius), and a single "mode" would forbid it.

## Why `step` and `match` rather than `interpolate`

Graduated symbology means CLASSES — a legend with five labelled buckets — so the expression must be
`step`, which is piecewise-constant. `interpolate` would produce a continuous gradient whose colours
do not appear in the legend at all. Proportional SIZE is the opposite case (a continuous quantity
with no legend to contradict), so it uses `interpolate`.
"""
from __future__ import annotations

import json

# ── Colour ramps ─────────────────────────────────────────────────────────────────────────────────
# Sampled at 7 stops and interpolated to whatever class count is asked for, so a ramp definition
# does not have to be repeated per class count. Perceptually-uniform ramps (viridis, magma) are
# first because they are the honest default for a quantity: equal steps in value look like equal
# steps in colour, which is exactly what a graduated legend claims.
RAMPS: dict[str, list[str]] = {
    "viridis": ["#440154", "#46327e", "#365c8d", "#277f8e", "#1fa187", "#4ac16d", "#fde725"],
    "magma":   ["#000004", "#3b0f70", "#8c2981", "#de4968", "#fe9f6d", "#fecf92", "#fcfdbf"],
    "blues":   ["#f7fbff", "#deebf7", "#c6dbef", "#9ecae1", "#6baed6", "#3182bd", "#08519c"],
    "reds":    ["#fff5f0", "#fee0d2", "#fcbba1", "#fc9272", "#fb6a4a", "#de2d26", "#a50f15"],
    "greens":  ["#f7fcf5", "#e5f5e0", "#c7e9c0", "#a1d99b", "#74c476", "#31a354", "#006d2c"],
    "oranges": ["#fff5eb", "#fee6ce", "#fdd0a2", "#fdae6b", "#fd8d3c", "#e6550d", "#a63603"],
    # Diverging — for values with a meaningful midpoint (change, anomaly, gain/loss).
    "rdbu":    ["#b2182b", "#ef8a62", "#fddbc7", "#f7f7f7", "#d1e5f0", "#67a9cf", "#2166ac"],
    "brbg":    ["#8c510a", "#d8b365", "#f6e8c3", "#f5f5f5", "#c7eae5", "#5ab4ac", "#01665e"],
    "spectral": ["#d53e4f", "#fc8d59", "#fee08b", "#ffffbf", "#e6f598", "#99d594", "#3288bd"],
}

#: Qualitative — for CATEGORIES, where the values have no order. A sequential ramp used for
#: categories implies a ranking that is not there, which is the most common misleading map there is.
CATEGORY_COLORS: list[str] = [
    "#3b82f6", "#ef4444", "#22c55e", "#f59e0b", "#a855f7", "#06b6d4",
    "#ec4899", "#84cc16", "#f97316", "#6366f1", "#14b8a6", "#eab308",
]

DEFAULT_COLOR = "#3b82f6"
DEFAULT_OTHER_COLOR = "#9ca3af"

#: The reciprocal golden ratio. Stepping a hue wheel by it spreads any number of categories about as
#: far apart as they can be, and — unlike a random palette — the Nth colour is the same every time,
#: which is what stops a category changing colour when the data gains a value.
_GOLDEN = 0.6180339887498949


def category_color(index: int) -> str:
    """The colour for the `index`-th category, for as many categories as a layer has.

    The twelve hand-picked colours come first because they are the most distinguishable set and
    cover the common cases. Past those, cycling would draw two categories identically — which QGIS
    never does, since its random ramp keeps generating — so the wheel takes over: the hue advances
    by the golden ratio and the lightness and saturation alternate, giving neighbours in the legend
    a difference in value as well as in hue.
    """
    if index < len(CATEGORY_COLORS):
        return CATEGORY_COLORS[index]
    n = index - len(CATEGORY_COLORS)
    hue = (n * _GOLDEN) % 1.0
    sat = 0.58 + 0.16 * (n % 2)
    light = 0.42 + 0.14 * ((n // 2) % 3)
    return _hsl_hex(hue, sat, light)


def _hsl_hex(h: float, s: float, ll: float) -> str:
    """HSL (0-1) to `#rrggbb`. Written to be transliterated: the twin in `ui/src/lib/symbology.js`
    is the same arithmetic, and `int(x + 0.5)` is half-up in both languages where round() is not."""
    def channel(p, q, t):
        t = t % 1.0
        if t < 1 / 6:
            return p + (q - p) * 6 * t
        if t < 1 / 2:
            return q
        if t < 2 / 3:
            return p + (q - p) * (2 / 3 - t) * 6
        return p
    q = ll * (1 + s) if ll < 0.5 else ll + s - ll * s
    p = 2 * ll - q
    return "#{0:02x}{1:02x}{2:02x}".format(
        int(channel(p, q, h + 1 / 3) * 255 + 0.5),
        int(channel(p, q, h) * 255 + 0.5),
        int(channel(p, q, h - 1 / 3) * 255 + 0.5))


def ramp_colors(name: str, count: int, reverse: bool = False) -> list[str]:
    """`count` colours sampled evenly from a named ramp, optionally end-to-end reversed.

    INTERPOLATED between the anchor stops, not snapped to the nearest one. Snapping was the earlier
    behaviour and it had a hard ceiling nobody had written down: the ramps are defined by seven
    stops, so **eight classes produced seven colours and twelve produced seven** — two classes
    drawn identically, in a legend that says they are different. That is why the class count was
    capped at twelve, and the cap was treating the symptom.
    Interpolating between adjacent stops of an already perceptually-tuned ramp stays close enough to
    the uniform path to be the right trade — and it is what QGIS and matplotlib do with the same
    ramps, so a GeoDeploy legend and a QGIS one now agree at every class count.

    `reverse` is a FLAG rather than a second table of reversed ramps: which end means "high" is a
    cartographic choice (a dark basemap often wants the light end for low values), and doubling nine
    ramps into eighteen would turn the picker into a wall. Reversing the sampled OUTPUT rather than
    the stop list keeps the sampling positions identical, so a reversed ramp is exactly the same
    colours in the opposite order — not a differently-sampled one.
    """
    stops = RAMPS.get(name) or RAMPS["viridis"]
    if count <= 0:
        return []
    if count == 1:
        return [stops[len(stops) // 2]]
    out = []
    for i in range(count):
        pos = i * (len(stops) - 1) / (count - 1)
        lo = int(pos)
        hi = min(lo + 1, len(stops) - 1)
        out.append(_blend(stops[lo], stops[hi], pos - lo))
    return out[::-1] if reverse else out


def _blend(a: str, b: str, t: float) -> str:
    """`a` and `b` mixed in RGB, `t` from 0 to 1, as `#rrggbb`.

    `int(x + 0.5)`, NOT `round()`. Python's round() is round-half-to-EVEN and JavaScript's
    Math.round() is round-half-UP, so the twin in `ui/src/lib/symbology.js` would land on a
    different byte wherever a channel came out on .5. Half-up is expressible identically in both
    languages, so the two cannot drift.
    """
    if t <= 0:
        return a
    if t >= 1:
        return b
    ar, ag, ab = int(a[1:3], 16), int(a[3:5], 16), int(a[5:7], 16)
    br, bg, bb = int(b[1:3], 16), int(b[3:5], 16), int(b[5:7], 16)
    return "#{0:02x}{1:02x}{2:02x}".format(
        int(ar + (br - ar) * t + 0.5),
        int(ag + (bg - ag) * t + 0.5),
        int(ab + (bb - ab) * t + 0.5))


# ── Classification ───────────────────────────────────────────────────────────────────────────────

def classify(values: list[float], method: str, count: int) -> list[float]:
    """Class BREAKS (the inner boundaries) for `count` classes over sorted numeric `values`.

    Returns `count - 1` numbers: with breaks `[b0, b1]` the classes are `(-∞, b0)`, `[b0, b1)`,
    `[b1, ∞)`. Inner boundaries only, deliberately — the outer edges are open so a feature outside
    the sampled range still draws, instead of silently falling out of every class. A layer gains
    rows after it is styled; a closed range would make the newest data invisible, which is the worst
    possible failure mode for a map that looks fine.

    Methods:
      * `quantile` — equal COUNT per class. The safe default: it always produces a readable map,
        including for the skewed distributions most spatial data has.
      * `equal` — equal VALUE width. Honest about magnitude, but one outlier can leave every other
        feature in the first class.
      * `jenks` — natural breaks, approximated by 1-D k-means (Fisher-style). Fits the data's own
        clustering; more expensive, and unstable for tiny samples.
    """
    vals = sorted(float(v) for v in values if v is not None)
    if len(vals) < 2 or count < 2:
        return []
    count = min(count, len(set(vals)))
    if count < 2:
        return []

    if method == "equal":
        lo, hi = vals[0], vals[-1]
        if hi <= lo:
            return []
        step = (hi - lo) / count
        return _usable([round(lo + step * (i + 1), 6) for i in range(count - 1)], lo, hi)

    if method == "jenks":
        return _usable(_kmeans_breaks(vals, count), vals[0], vals[-1])

    # quantile (default)
    breaks = []
    for i in range(1, count):
        idx = int(round(i * len(vals) / count))
        idx = max(1, min(idx, len(vals) - 1))
        breaks.append(vals[idx])
    # Ties collapse classes: with many repeated values two breaks can land on the same number, which
    # MapLibre rejects (`step` requires strictly ascending stops). Dedupe rather than error — fewer
    # classes than asked for is a correct map; a rejected style is a blank one.
    return _usable(_dedupe_ascending(breaks), vals[0], vals[-1])


def _kmeans_breaks(vals: list[float], count: int) -> list[float]:
    """1-D k-means (Lloyd's) seeded with quantiles, then breaks at cluster midpoints.

    Real Jenks optimises variance with dynamic programming at O(n²k), which is unusable on the
    100k-value samples we take. k-means on sorted 1-D data converges in a few passes and lands on
    the same obvious groupings for the class counts a legend can show.
    """
    centers = [vals[min(int(round(i * len(vals) / count)), len(vals) - 1)] for i in range(count)]
    for _ in range(20):
        groups: list[list[float]] = [[] for _ in range(count)]
        for v in vals:
            best = min(range(count), key=lambda c: abs(v - centers[c]))
            groups[best].append(v)
        moved = False
        for i, g in enumerate(groups):
            if g:
                mean = sum(g) / len(g)
                if abs(mean - centers[i]) > 1e-9:
                    centers[i], moved = mean, True
        if not moved:
            break
    centers = sorted(centers)
    breaks = [round((centers[i] + centers[i + 1]) / 2, 6) for i in range(len(centers) - 1)]
    return _dedupe_ascending(breaks)


def _dedupe_ascending(breaks: list[float]) -> list[float]:
    out: list[float] = []
    for b in breaks:
        if not out or b > out[-1]:
            out.append(b)
    return out


def _usable(breaks: list[float], lo: float, hi: float) -> list[float]:
    """Drop breaks that cannot separate anything, given the OPEN outer edges.

    A break `b` splits into `< b` and `≥ b`. So `b <= lo` leaves the lowest class empty and `b > hi`
    leaves the highest one empty — an empty class is a legend entry for a colour that appears
    nowhere on the map, which reads as "my data is missing".

    This is not hypothetical: a column that is 90% one value (a default, a flag, an unfilled field)
    makes every quantile break land on that value, i.e. on the minimum. Better to return fewer
    classes and let the legend say so than to publish a legend the map does not match.
    """
    return [b for b in breaks if lo < b <= hi]


def build_classes(values: list[float], method: str, count: int, ramp: str,
                  reverse: bool = False) -> list[dict]:
    """Breaks + ramp → the `classes` list the style stores and the legend reads.

    `min` of the first class and `max` of the last are `None`, matching the open outer edges
    `classify` produces — the legend renders them as "< b0" and "≥ bn".
    """
    breaks = classify(values, method, count)
    if not breaks:
        vals = [v for v in values if v is not None]
        if not vals:
            return []
        return [{"min": None, "max": None, "color": ramp_colors(ramp, 1, reverse)[0]}]
    colors = ramp_colors(ramp, len(breaks) + 1, reverse)
    edges = [None, *breaks, None]
    return [{"min": edges[i], "max": edges[i + 1], "color": colors[i]}
            for i in range(len(breaks) + 1)]


def build_categories(values: list, ramp: str | None = None,
                     reverse: bool = False) -> list[dict]:
    """Distinct values → categories, in the order given (the stats endpoint returns them by
    frequency, so the commonest gets the first, most distinguishable colour)."""
    out = []
    for i, v in enumerate(values):
        if ramp and ramp in RAMPS:
            colors = ramp_colors(ramp, max(len(values), 1), reverse)
            color = colors[i] if i < len(colors) else DEFAULT_OTHER_COLOR
        else:
            color = category_color(i)
        out.append({"value": v, "color": color})
    return out


# ── Expressions ──────────────────────────────────────────────────────────────────────────────────

def color_expression(style: dict):
    """The MapLibre value for a colour paint property: a string, or a data-driven expression.

    Falls back to the plain colour whenever the configuration is incomplete — a half-configured mode
    (field chosen, classes not yet computed) must render the layer, not blank it. Styling is edited
    live, so every intermediate state is something a user is looking at.
    """
    base = style.get("color") or DEFAULT_COLOR
    mode = style.get("color_mode") or "single"
    field = (style.get("color_field") or "").strip()
    if mode == "single" or not field:
        return base

    if mode == "graduated":
        classes = [c for c in (style.get("classes") or []) if c.get("color")]
        if not classes:
            return base
        # ["step", ["to-number", ["get", f]], c0, b0, c1, b1, c2, …]
        expr: list = ["step", ["to-number", ["get", field]], classes[0]["color"]]
        for c in classes[1:]:
            lo = c.get("min")
            if lo is None:
                continue
            expr.extend([lo, c["color"]])
        # A single class means no boundary was emitted; `step` needs at least one stop.
        return base if len(expr) < 5 else expr

    if mode == "categorized":
        cats = [c for c in (style.get("categories") or []) if c.get("color")]
        if not cats:
            return base
        # ["match", ["to-string", ["get", f]], "a", c0, "b", c1, …, other]
        expr: list = ["match", ["to-string", ["get", field]]]
        for c in cats:
            # match labels must be strings here because the input is coerced with to-string;
            # a numeric category would otherwise never equal its own label.
            expr.extend([str(c.get("value")), c["color"]])
        expr.append(style.get("other_color") or DEFAULT_OTHER_COLOR)
        return expr if len(expr) > 3 else base

    return base


def size_expression(style: dict, fallback: float):
    """Radius (points) or width (lines): a number, or an `interpolate` over `size_field`.

    Continuous rather than classed — see the module docstring. `linear` because the size legend, if
    one is shown, is a straight line between the two ends; an exponential base would make the middle
    of that legend a lie.
    """
    if (style.get("size_mode") or "fixed") != "proportional":
        return fallback
    field = (style.get("size_field") or "").strip()
    stops = [s for s in (style.get("size_stops") or []) if isinstance(s, (list, tuple)) and len(s) == 2]
    if not field or len(stops) < 2:
        return fallback
    ordered = sorted(stops, key=lambda s: s[0])
    expr: list = ["interpolate", ["linear"], ["to-number", ["get", field]]]
    last = None
    for value, size in ordered:
        if last is not None and value <= last:
            continue      # MapLibre requires strictly ascending stop inputs
        expr.extend([value, size])
        last = value
    return expr if len(expr) >= 7 else fallback


def extrusion_paint(style: dict, opacity: float) -> dict:
    """`fill-extrusion` paint for attribute-driven 3D.

    Height comes from a numeric property, optionally scaled — the same shape as GeoLibre's
    `extrusionHeightProperty` × `extrusionHeightScale`, so an imported project and a
    GeoDeploy-authored one produce the same map.

    `["to-number", ["get", f], 0]` with an explicit fallback: a feature missing the property, or
    holding a non-numeric one, becomes height 0 rather than making MapLibre discard the whole
    expression — one bad row must not flatten the layer.
    """
    field = (style.get("extrusion") or {}).get("field") or ""
    ex = style.get("extrusion") or {}
    scale = ex.get("scale", 1) or 1
    if field:
        height: object = ["*", ["to-number", ["get", str(field)], 0], scale]
    else:
        height = ex.get("height", 0) or 0
    base = ex.get("base", 0) or 0
    return {
        "fill-extrusion-color": ex.get("color") or color_expression(style),
        "fill-extrusion-height": height,
        "fill-extrusion-base": (["*", ["to-number", ["get", str(base)], 0], scale]
                                if isinstance(base, str) and base else base),
        "fill-extrusion-opacity": opacity * float(ex.get("opacity", 1) or 1),
        # A flat wash of one colour reads as a shapeless blob at any pitch; the vertical gradient is
        # what makes individual volumes legible.
        "fill-extrusion-vertical-gradient": True,
    }


#: Outline colour meaning NO OUTLINE. A distinct sentinel rather than an empty string, because the
#: style dict travels through JSON, a saved portal, and three renderers — and "" is what an
#: uninitialised colour input produces, which would silently turn outlines off for someone who never
#: asked. `None`/absent still means "use the default", so existing portals are untouched.
NO_OUTLINE = "none"


def outline_color(style: dict, default: str = "#1d4ed8"):
    """The outline colour, or None when the layer asks for no outline at all.

    Callers must distinguish the two: MapLibre has no "transparent" outline keyword, so an absent
    outline is expressed by omitting the paint property (fills) or by a zero width (points), not by
    passing a colour.
    """
    raw = style.get("outline_color", default)
    if raw in (NO_OUTLINE, "", False):
        return None
    return raw if raw is not None else default


def is_data_driven(style: dict) -> bool:
    """True when colour or size varies per feature."""
    mode = style.get("color_mode") or "single"
    if mode == "graduated" and (style.get("color_field") and style.get("classes")):
        return True
    if mode == "categorized" and (style.get("color_field") and style.get("categories")):
        return True
    return ((style.get("size_mode") or "fixed") == "proportional"
            and bool(style.get("size_field")) and len(style.get("size_stops") or []) >= 2)


# ── Point markers ────────────────────────────────────────────────────────────────────────────────
# Points draw as a `symbol` layer with a runtime-generated canvas icon, which is what lets them have
# SHAPES (circle/square/triangle/diamond/star/cross) on any basemap.
#
# One bitmap cannot be recoloured per feature — but `icon-image` is itself DATA-DRIVEN in MapLibre.
# So a classified point layer emits one image PER CLASS and selects between them with the same
# `step`/`match` structure the colour uses. Shapes and per-feature colour, both, with no compromise:
#
#   * an SDF icon (`{sdf: true}` + `icon-color`) was the other candidate. Rejected: MapLibre reads
#     the alpha channel as a DISTANCE FIELD, so a plain shape mask renders with mushy edges and an
#     unpredictable halo, and generating a true distance transform per marker is real work for a
#     worse-looking result than simply drawing the shape in the right colour.
#   * switching to a `circle` layer was the first implementation. Rejected on sight: losing the
#     marker shape the moment you classify is not a trade anyone asked for.
#
# The image ID CARRIES its parameters (`gd-pt-<shape>-<hex>-<size>`) so any renderer can generate a
# missing one from the id alone. That matters now that a layer needs N images rather than one — the
# runtime's `styleimagemissing` handler can no longer look them up from a single layer metadata blob.

#: A marker's outline, when the style does not say otherwise. White at 28% of the radius is what
#: every marker drew before outlines were configurable, so leaving these alone reproduces it exactly.
DEFAULT_MARKER_OUTLINE = "#ffffff"


def marker_outline(style: dict) -> tuple[str | None, float]:
    """`(colour, width_ratio)` for a point marker's outline — colour None means draw none.

    The width is a RATIO of the marker radius, not pixels: a 3 px ring around a 4 px dot and around
    a 20 px dot are different symbols, and someone resizing a layer expects the outline to keep its
    proportion. The old hard-coded stroke was `radius * 0.28`, which is the default here.

    A wide outline on a small marker is how you draw a RING — the fill disappears behind it — which
    is a symbol people ask for by name, so the ratio is deliberately allowed up to 1.
    """
    raw = style.get("outline_color", DEFAULT_MARKER_OUTLINE)
    color = None if raw in (NO_OUTLINE, "", False) else (raw or DEFAULT_MARKER_OUTLINE)
    try:
        ratio = float(style.get("outline_width", 0.28))
    except (TypeError, ValueError):
        ratio = 0.28
    return color, min(max(ratio, 0.0), 1.0)


#: What a POLYGON's outline is worth in CSS pixels when nothing says otherwise.
#:
#: 1 is not an arbitrary default — it is what a MapLibre `fill` layer already draws. A fill strokes
#: its own edge at a fixed hairline and `fill-outline-color` has no width, so every polygon ever
#: published here has a 1 px outline. Anything wider needs a real `line` layer beside the fill, and
#: keeping the default at 1 means those portals keep rendering exactly as they did.
POLYGON_OUTLINE_WIDTH = 1


def outline_width_px(style: dict, default: float = POLYGON_OUTLINE_WIDTH) -> float:
    """A POLYGON's outline width, in CSS pixels.

    `outline_width` means different things on different geometries, which is worth stating plainly
    because one key carries both: on a POINT it is a RATIO of the marker radius (a ring around a
    4 px dot and around a 20 px dot should stay in proportion — see `marker_outline`), and on a
    POLYGON it is a width in pixels, like a line's. They are never read by the same code path.
    """
    try:
        width = float(style.get("outline_width", default))
    except (TypeError, ValueError):
        return float(default)
    if width != width or width in (float("inf"), float("-inf")):
        return float(default)
    # Capped where a "border" stops being one. Nothing rejects a wider value; it simply stops being
    # a sensible thing to draw over the data the polygon is describing.
    return min(max(width, 0.0), 40.0)


def needs_outline_layer(style: dict) -> bool:
    """Whether a polygon needs its own `line` layer, or a plain `fill` already draws it right.

    ONLY WHEN IT IS WIDER THAN THE HAIRLINE A FILL DRAWS ANYWAY. Emitting the extra layer
    unconditionally would add one to every polygon in every portal that exists, for a picture
    nobody asked to change — and a `line` layer's antialiasing is not pixel-identical to a fill's
    own edge, so it would be a visible change too.
    """
    if outline_color(style) is None:
        return False                     # no outline at all; nothing to widen
    return outline_width_px(style) > POLYGON_OUTLINE_WIDTH


def _px(value, default=5):
    try:
        n = round(float(value if value is not None else default), 2)
    except (TypeError, ValueError):
        n = default
    return int(n) if n == int(n) else n


def marker_image_id(shape: str, color: str, size, outline: str | None = DEFAULT_MARKER_OUTLINE,
                    outline_width: float = 0.28) -> str:
    """The canonical id for a marker bitmap. Every parameter that changes the PIXELS is in the id.

    That is the whole contract: a renderer seeing an unknown id can draw it from the id alone, and
    two layers wanting the same marker share one image. Adding outline colour and width to the
    drawing therefore has to add them here — otherwise a red-ringed marker and a white-ringed one
    would collide on the same id, and whichever was created first would win for both.
    """
    hexish = str(color or DEFAULT_COLOR).lstrip("#").lower() or "3b82f6"
    ohex = "none" if not outline else str(outline).lstrip("#").lower()
    ow = _px(outline_width, 0.28)
    return f"gd-pt-{(shape or 'circle')}-{hexish}-{_px(size)}-{ohex}-{ow}"


def picture_id(data_uri: str) -> str:
    """The id for a marker PICTURE — a bitmap rendered from a symbol GeoDeploy cannot describe.

    CONTENT-ADDRESSED, like `marker_image_id`, and for the same reason: fifty layers using the same
    airport icon should register one image, not fifty. FNV-1a because it has to produce the identical
    id in `ui/src/lib/symbology.js` — a hash whose implementations disagreed would have the editor
    preview asking for an image the published portal never registers.
    """
    h = 0x811c9dc5
    for ch in (data_uri or ""):
        h = ((h ^ (ord(ch) & 0xFF)) * 0x01000193) & 0xFFFFFFFF
    return "gd-img-{0:08x}".format(h)


def marker_picture(style: dict):
    """The marker bitmap this style carries, or None.

    A `marker_image` is what the QGIS plugin sends when the author's symbol is one GeoDeploy has no
    words for — an SVG marker, a raster marker, a font marker, a filled or ellipse marker, or a
    multi-layer symbol. Rather than approximate it as a coloured dot, the plugin renders the SYMBOL
    ITSELF to a PNG and ships the pixels, so the portal draws the picture the author drew.
    """
    uri = style.get("marker_image")
    return uri if isinstance(uri, str) and uri.startswith("data:image/") else None


def icon_image_expression(style: dict):
    """`icon-image` for a point layer: one id, or a data-driven choice between per-class ids.

    Mirrors `color_expression` stop for stop — same field, same breaks, same order — so the icon a
    feature gets is the class its colour would have been. Anything else would be a second
    classification that could disagree with the first.
    """
    # A PICTURE WINS OVER A SHAPE. When the plugin has sent the rendered symbol, that bitmap is the
    # marker — and it is ONE image for every feature, because a raster icon cannot be recoloured per
    # class the way a generated shape can. A classified layer keeps its classification everywhere
    # else (a line's colour, a polygon's fill); only the icon stops varying.
    picture = marker_picture(style)
    if picture:
        return picture_id(picture)

    shape = style.get("marker") or "circle"
    size = style.get("radius", 5)
    ol, ow = marker_outline(style)
    base = marker_image_id(shape, style.get("color") or DEFAULT_COLOR, size, ol, ow)
    mode = style.get("color_mode") or "single"
    field = (style.get("color_field") or "").strip()
    if mode == "single" or not field:
        return base

    if mode == "graduated":
        classes = [c for c in (style.get("classes") or []) if c.get("color")]
        if not classes:
            return base
        expr: list = ["step", ["to-number", ["get", field]],
                      marker_image_id(shape, classes[0]["color"], size, ol, ow)]
        for c in classes[1:]:
            if c.get("min") is None:
                continue
            expr.extend([c["min"], marker_image_id(shape, c["color"], size, ol, ow)])
        return base if len(expr) < 5 else expr

    if mode == "categorized":
        cats = [c for c in (style.get("categories") or []) if c.get("color")]
        if not cats:
            return base
        expr: list = ["match", ["to-string", ["get", field]]]
        for c in cats:
            expr.extend([str(c.get("value")), marker_image_id(shape, c["color"], size, ol, ow)])
        expr.append(marker_image_id(shape, style.get("other_color") or DEFAULT_OTHER_COLOR,
                                    size, ol, ow))
        return expr if len(expr) > 3 else base

    return base


def marker_images(style: dict) -> list[dict]:
    """Every marker bitmap this style needs: `[{id, shape, color, size}]`.

    Baked into the published style so the runtime can create them all up front rather than
    discovering them one `styleimagemissing` event at a time — with a classified layer, that would
    otherwise be a visible pop-in of markers as each class first appears on screen.
    """
    picture = marker_picture(style)
    if picture:
        # One entry, carrying the PIXELS. The runtime registers it from the data URI instead of
        # drawing a shape — see `setMarkerImage` in templates/shared/portal.js.
        return [{"id": picture_id(picture), "image": picture}]

    shape = style.get("marker") or "circle"
    size = style.get("radius", 5)
    ol, ow = marker_outline(style)
    colors = [style.get("color") or DEFAULT_COLOR]
    mode = style.get("color_mode") or "single"
    if mode == "graduated" and style.get("color_field"):
        colors += [c["color"] for c in (style.get("classes") or []) if c.get("color")]
    elif mode == "categorized" and style.get("color_field"):
        colors += [c["color"] for c in (style.get("categories") or []) if c.get("color")]
        colors.append(style.get("other_color") or DEFAULT_OTHER_COLOR)

    seen, out = set(), []
    for c in colors:
        iid = marker_image_id(shape, c, size, ol, ow)
        if iid in seen:
            continue
        seen.add(iid)
        out.append({"id": iid, "shape": shape, "color": c, "size": size,
                    "outline": ol, "outline_width": ow})
    return out


def icon_size_expression(style: dict):
    """`icon-size`, a MULTIPLIER of the bitmap's natural size.

    The bitmap is drawn at the layer's base radius, so a fixed-size layer is exactly 1. Proportional
    size divides the per-feature size by that base — arithmetic MapLibre can do inside the
    expression, which keeps one classification rather than pre-computing scaled stops.
    """
    if (style.get("size_mode") or "fixed") != "proportional":
        return 1
    base = float(style.get("radius", 5) or 5)
    expr = size_expression(style, None)
    if expr is None:
        return 1
    return ["/", expr, base]


# ── Line decoration, marker placement, and layer scope ───────────────────────────────────────────
# Added 2026-09-03 for the QGIS round trip. Every one of these is something MapLibre draws natively
# and GeoDeploy simply had no word for, so each is an EXACT round trip rather than an approximation.
# The twins are `ui/src/lib/symbology.js` (editor preview) and `portal_generator` (published style).

#: `lineType` as a dash array, in MULTIPLES OF THE LINE WIDTH — which is the unit MapLibre's
#: `line-dasharray` uses, and the reason a dash pattern is stored that way rather than in pixels: a
#: pattern in absolute units would change shape every time somebody changed the width.
LINE_TYPE_DASHES = {"dashed": [2, 1.5], "dotted": [0.4, 1.8]}

_LINE_CAPS = ("butt", "round", "square")
_LINE_JOINS = ("bevel", "round", "miter")


def dash_array(style: dict):
    """The `line-dasharray` for a style, or None for a solid line.

    An explicit `dash_pattern` wins over `lineType`: the named types are the two presets a web map
    always had, and a pattern read out of QGIS is the real thing the author drew.
    """
    pattern = style.get("dash_pattern")
    if isinstance(pattern, (list, tuple)) and len(pattern) >= 2:
        out = []
        for value in pattern:
            try:
                out.append(round(float(value), 4))
            except (TypeError, ValueError):
                return None
        # MapLibre wants pairs; an odd-length pattern repeats inverted, which is not what QGIS drew.
        return out if len(out) % 2 == 0 else out + [out[-1]]
    return LINE_TYPE_DASHES.get(style.get("lineType"))


def line_layout(style: dict) -> dict:
    """`line-cap` / `line-join` — LAYOUT properties, not paint, which is why they are their own dict."""
    out = {}
    cap = str(style.get("line_cap") or "").lower()
    join = str(style.get("line_join") or "").lower()
    if cap in _LINE_CAPS:
        out["line-cap"] = cap
    if join in _LINE_JOINS:
        out["line-join"] = join
    return out


def line_offset(style: dict):
    """`line-offset` in pixels, or None. Positive is to the LEFT of the direction of travel in
    MapLibre and to the right in QGIS, so the sign is flipped where it is read, not here."""
    value = style.get("line_offset")
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return round(value, 3) if value else None


def marker_layout(style: dict) -> dict:
    """`icon-rotate` and `icon-offset` for a point layer.

    `icon-offset` is in units of the ICON's own size in MapLibre, and GeoDeploy states it in CSS
    pixels like every other size, so it is divided by the generated bitmap's size at render time —
    see `icon_size_expression` for why the bitmap is drawn at a fixed size and scaled.
    """
    out = {}
    rotation = style.get("marker_rotation")
    try:
        rotation = float(rotation)
    except (TypeError, ValueError):
        rotation = None
    if rotation:
        out["icon-rotate"] = round(rotation % 360, 3)
    offset = style.get("marker_offset")
    if isinstance(offset, (list, tuple)) and len(offset) == 2:
        try:
            out["icon-offset"] = [round(float(offset[0]), 3), round(float(offset[1]), 3)]
        except (TypeError, ValueError):
            pass
    return out


def marker_opacity(style: dict, opacity: float) -> float:
    """The layer's opacity times the marker's own, which QGIS keeps separately."""
    own = style.get("marker_opacity")
    try:
        own = float(own)
    except (TypeError, ValueError):
        return opacity
    return round(opacity * max(0.0, min(1.0, own)), 4)


def layer_scope(style: dict) -> dict:
    """`minzoom` / `maxzoom` / `filter` that belong to the LAYER rather than to one render layer.

    A QGIS layer carries a scale range and a subset string, and both apply to everything it draws —
    a polygon's fill AND its outline, every rule of a rule-based renderer. So they are applied by
    the caller to every render layer a style emits, not baked into one of them.

    The zoom range is clamped to MapLibre's own 0-24: QGIS stores scale thresholds far outside it,
    and one out-of-range number makes MapLibre reject the WHOLE style rather than ignore it. The
    twin of this clamp is `_apply_rule_scope`, which does the same for a rule.
    """
    out = {}
    for key in ("minzoom", "maxzoom"):
        value = style.get(key)
        if value is None:
            continue
        try:
            value = max(0.0, min(24.0, float(value)))
        except (TypeError, ValueError):
            continue
        if (key == "minzoom" and value > 0) or (key == "maxzoom" and value < 24):
            out[key] = round(value, 3)
    if style.get("filter") is not None:
        out["filter"] = style["filter"]
    return out


def combined_filter(a, b):
    """Two MapLibre filters ANDed, flattened. Either may be None."""
    if a is None:
        return b
    if b is None:
        return a
    parts = []
    for node in (a, b):
        if isinstance(node, list) and node and node[0] == "all":
            parts.extend(node[1:])
        else:
            parts.append(node)
    return ["all"] + parts


def draws_nothing(style: dict) -> bool:
    """QGIS's "No symbols" renderer: the layer is listed and legible, and draws nothing."""
    return bool(style.get("no_symbol"))


# ── Labels ───────────────────────────────────────────────────────────────────────────────────────
# `style.labels` is its own block rather than more top-level keys, because a label is a second thing
# drawn for the same feature: it has its own colour, its own size, its own zoom range, and it is
# emitted as its own MapLibre `symbol` layer so it can sit above every geometry on the map.
#
#     labels = {"enabled": bool,
#               "field": "name",                    the common case — one attribute
#               "expression": <maplibre expr>,      …or anything the translator produced
#               "qgis_expression": "…",             the QGIS source, carried for the round trip
#               "font": "Noto Sans Regular", "size": 12, "color": "#000",
#               "halo_color": "#fff", "halo_width": 1,
#               "offset": [x, y], "rotation": 0, "anchor": "center",
#               "placement": "point" | "line", "max_width": 10,
#               "transform": "none" | "uppercase" | "lowercase",
#               "letter_spacing": 0, "allow_overlap": False, "priority": 0,
#               "minzoom": n, "maxzoom": n}

#: The faces SHIPPED with GeoDeploy. Not a limit: `templates/shared/fonts/` is a drop-in directory
#: and `routers/fonts.py` serves and lists whatever is in it, so an operator can add Noto Serif,
#: Noto Sans Mono or anything else with `scripts/build_glyphs.js`. This tuple is what a UI offers
#: when it has not asked the instance, and what `GET /api/fonts` returns on a default install.
LABEL_FONTS = ("Noto Sans Regular", "Noto Sans Bold", "Noto Sans Italic")
DEFAULT_LABEL_FONT = "Noto Sans Regular"
DEFAULT_LABEL_SIZE = 12
DEFAULT_LABEL_COLOR = "#333333"
DEFAULT_LABEL_HALO = "#ffffff"

_ANCHORS = ("center", "left", "right", "top", "bottom",
            "top-left", "top-right", "bottom-left", "bottom-right")
_TRANSFORMS = ("none", "uppercase", "lowercase")


def labels_of(style: dict) -> dict:
    """The label block when a layer is labelled, else `{}`."""
    labels = (style or {}).get("labels")
    if not isinstance(labels, dict) or not labels.get("enabled"):
        return {}
    # A label with nothing to say draws nothing; `text-field` is the one key with no default.
    return labels if (labels.get("field") or labels.get("expression") is not None) else {}


def label_text(labels: dict):
    """The `text-field` value: a translated expression if there is one, else a plain field read.

    An EXPRESSION wins over a field name because a QGIS label is an expression in the general case —
    `"name" || ' (' || "year" || ')'` is an ordinary thing to label with — and the translator has
    already turned it into something MapLibre can evaluate.
    """
    expression = labels.get("expression")
    if expression is not None:
        return expression
    # `to-string` because MapLibre's text-field wants a string and a numeric column is common.
    return ["to-string", ["get", str(labels.get("field"))]]


def label_layout(labels: dict) -> dict:
    """The `layout` half of a label layer — everything about placement and glyph selection."""
    size = _label_num(labels.get("size"), DEFAULT_LABEL_SIZE)
    out = {
        "text-field": label_text(labels),
        "text-font": font_stack(labels.get("font")),
        "text-size": size,
        # Labels are the one thing on the map that MUST be allowed to move out of the way of each
        # other, so overlap stays off unless the author asked for it.
        "text-allow-overlap": bool(labels.get("allow_overlap")),
    }
    placement = str(labels.get("placement") or "point").lower()
    if placement == "line":
        # QGIS's "curved" and "parallel to line" both become this: MapLibre bends the text along
        # the geometry, which is the same intent and not the same algorithm.
        out["symbol-placement"] = "line"

    anchor = str(labels.get("anchor") or "").lower()
    if anchor in _ANCHORS:
        out["text-anchor"] = anchor

    offset = labels.get("offset")
    if isinstance(offset, (list, tuple)) and len(offset) == 2 and size:
        # `text-offset` is in EMS, and GeoDeploy states every offset in pixels — so it is divided by
        # the text size, which is what an em is.
        try:
            out["text-offset"] = [round(float(offset[0]) / size, 3),
                                  round(float(offset[1]) / size, 3)]
        except (TypeError, ValueError):
            pass

    rotation = _label_num(labels.get("rotation"), None)
    if rotation:
        out["text-rotate"] = round(rotation % 360, 3)

    width = _label_num(labels.get("max_width"), None)
    if width and width > 0:
        out["text-max-width"] = round(width, 2)

    transform = str(labels.get("transform") or "").lower()
    if transform in _TRANSFORMS and transform != "none":
        out["text-transform"] = transform

    spacing = _label_num(labels.get("letter_spacing"), None)
    if spacing:
        out["text-letter-spacing"] = round(spacing, 3)

    priority = _label_num(labels.get("priority"), None)
    if priority is not None:
        # QGIS's priority runs 0-10 with HIGHER meaning more important; MapLibre's sort key places
        # LOWER first, and what is placed first wins the space. So the scale is inverted.
        out["symbol-sort-key"] = round(-priority, 3)
    return out


def font_stack(font) -> list[str]:
    """A `text-font` stack: the face asked for, then the one always shipped.

    A STACK, NOT A NAME, and that is the point. MapLibre reads `text-font` as a PREFERENCE LIST and
    uses the first face the glyph source has — so naming the requested face followed by the fallback
    means an instance that has it draws it, and one that does not still draws the label. The
    alternative, rewriting an unknown face to the fallback at publish time, would silently discard a
    font the operator had actually installed, since only the server knows what is there.

    A face the glyph source lacks and no fallback is the failure this avoids: MapLibre renders such
    a label as nothing at all — no error, no warning, no text.
    """
    name = str(font or "").strip()
    if not name or name == DEFAULT_LABEL_FONT:
        return [DEFAULT_LABEL_FONT]
    return [name, DEFAULT_LABEL_FONT]


def line_marker(style: dict) -> dict:
    """`style.line_marker` when a line carries markers along it, else `{}`.

    QGIS's marker line and hashed line repeat a symbol down a line — arrows on a river, ticks on a
    boundary. MapLibre draws exactly that with `symbol-placement: line`, so it is a real translation
    rather than an approximation, and the plugin ships the repeated symbol as a picture because
    MapLibre needs pixels rather than a description of them.
    """
    block = style.get("line_marker")
    if not isinstance(block, dict):
        return {}
    # The picture lives under `image` here, not `marker_image`: a line's decoration and a point's
    # marker are different things that happen to share a mechanism, and giving them the same key
    # would make a line style look like a point style to everything that inspects one.
    uri = block.get("image")
    return block if isinstance(uri, str) and uri.startswith("data:image/") else {}


DEFAULT_LINE_MARKER_SPACING = 40


def line_marker_layout(block: dict) -> dict:
    """The `layout` for the symbol layer that repeats a marker along a line."""
    spacing = _label_num(block.get("spacing"), DEFAULT_LINE_MARKER_SPACING)
    return {
        "icon-image": picture_id(block["image"]),
        # ALONG the line, and rotated WITH it — `icon-rotation-alignment: map` is what turns an
        # arrow into an arrow pointing downstream rather than one always pointing up the screen.
        "symbol-placement": "line",
        "symbol-spacing": max(1.0, round(spacing, 2)),
        "icon-rotation-alignment": "map",
        # A decoration that is dropped where it collides is a decoration missing from half the line,
        # which reads as a rendering fault rather than a placement rule.
        "icon-allow-overlap": True,
        "icon-ignore-placement": True,
    }


def label_paint(labels: dict, opacity: float = 1.0) -> dict:
    """The `paint` half — colour, halo, and the layer's opacity carried through."""
    out = {
        "text-color": labels.get("color") or DEFAULT_LABEL_COLOR,
        "text-opacity": opacity,
    }
    halo = _label_num(labels.get("halo_width"), 0)
    if halo and halo > 0:
        out["text-halo-color"] = labels.get("halo_color") or DEFAULT_LABEL_HALO
        out["text-halo-width"] = round(halo, 2)
    return out


def label_scope(labels: dict) -> dict:
    """A label's OWN zoom range, which QGIS keeps separately from the layer's."""
    out = {}
    for key in ("minzoom", "maxzoom"):
        value = _label_num(labels.get(key), None)
        if value is None:
            continue
        value = max(0.0, min(24.0, value))
        if (key == "minzoom" and value > 0) or (key == "maxzoom" and value < 24):
            out[key] = round(value, 3)
    return out


def _label_num(value, default):
    """A finite float or `default`. NOT `_num` — that name is taken further down by the legend's
    number FORMATTER, which returns a string, and the collision silently shadowed this one."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if out == out and out not in (float("inf"), float("-inf")) else default


def is_extruded(style: dict) -> bool:
    ex = style.get("extrusion") or {}
    return bool(ex.get("enabled")) and bool(ex.get("field") or ex.get("height"))


#: Metres. A pillar MARKS a location rather than covering ground, so the default footprint is small
#: and the operator sets it against their own extent. Mirrors services/pillars.DEFAULT_RADIUS_M.
DEFAULT_PILLAR_RADIUS_M = 30.0


#: A bar this fraction of the layer's own diagonal reads as a marker at the layer's natural zoom —
#: wide enough to see, narrow enough to still be a symbol rather than a blob.
PILLAR_RADIUS_FRACTION = 400.0


def extent_metres(bbox) -> float | None:
    """Rough diagonal of a lon/lat bbox in metres. None if it is not a usable bbox.

    Deliberately approximate — a degree of longitude scaled by the latitude midpoint. This picks a
    default symbol size; a proper geodesic would not change the answer by anything a viewer notices.
    """
    import math

    try:
        w, s, e, n = (float(v) for v in (bbox if not isinstance(bbox, str) else json.loads(bbox)))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    # Must actually BE lon/lat. Layer bboxes are stored in EPSG:4326 app-wide, but a bbox that
    # somehow arrived in a projected CRS would be read as millions of degrees here and silently
    # clamp the symbol to the maximum size. Out of range → None → the fixed fallback, which is
    # merely small rather than wrong.
    if not (e > w and n > s and -180.0 <= w and e <= 180.0 and -90.0 <= s and n <= 90.0):
        return None
    mid = math.radians((n + s) / 2.0)
    dx = (e - w) * 111320.0 * max(math.cos(mid), 0.05)
    dy = (n - s) * 110540.0
    d = math.hypot(dx, dy)
    return d if d > 0 else None


def pillar_radius(style: dict, bbox=None) -> float:
    """The footprint radius, in metres, of an extruded POINT.

    Points have no area, so extruding one needs a width that polygons get for free. Kept out of
    `extrusion_paint` because it is not paint at all — it decides the GEOMETRY the tile server
    generates (`services/pillars`), not how it is drawn.

    WITHOUT an author-chosen radius this is derived from the LAYER'S OWN EXTENT. The old fixed 30 m
    default was unusable for anything but a street-scale layer: 240 country centroids across the
    whole world got 30 m bars, about three thousandths of a pixel at that zoom — rendered perfectly
    and completely invisible, which is indistinguishable from "3D is broken". A default that depends
    on the data means ticking the box shows something, and the author adjusts from there instead of
    guessing what number makes anything appear at all.
    """
    ex = style.get("extrusion") or {}
    raw = ex.get("radius")
    if raw not in (None, ""):
        try:
            return min(max(float(raw), 0.5), 100000.0)
        except (TypeError, ValueError):
            pass
    d = extent_metres(bbox)
    if d:
        return min(max(d / PILLAR_RADIUS_FRACTION, 5.0), 100000.0)
    return DEFAULT_PILLAR_RADIUS_M


def legend_entries(style: dict) -> list[dict]:
    """What a legend should show for this layer: `[{color, label, …}]`, or [] for single symbols.

    Built here rather than in each renderer for the same reason as the expressions: a legend that
    disagrees with the map is worse than no legend, and the only way to guarantee it agrees is to
    derive both from one description.

    Graduated entries also carry the raw `min`/`max`, and categorized entries the raw `value`,
    alongside the formatted label. A renderer that has to REBUILD symbology — the QGIS plugin, for
    a layer it can only see anonymously — otherwise has to parse those numbers back out of a
    display string containing an EN dash and `≥`, which is precisely the re-derivation this
    function exists to stop. Drawing a legend needs the label; being the map needs the number.
    """
    mode = style.get("color_mode") or "single"
    if mode == "graduated":
        out = []
        for c in style.get("classes") or []:
            lo, hi = c.get("min"), c.get("max")
            if lo is None and hi is None:
                label = "all"
            elif lo is None:
                label = f"< {_num(hi)}"
            elif hi is None:
                label = f"≥ {_num(lo)}"
            else:
                label = f"{_num(lo)} – {_num(hi)}"
            out.append({"color": c.get("color"), "label": label, "min": lo, "max": hi})
        return out
    if mode == "categorized":
        out = [{"color": c.get("color"), "label": str(c.get("value")), "value": c.get("value")}
               for c in style.get("categories") or []]
        if out:
            # "Other" carries no value on purpose: it is the fallback for everything NOT listed,
            # and a renderer needs to be able to tell it apart from a category whose value is null.
            out.append({"color": style.get("other_color") or DEFAULT_OTHER_COLOR, "label": "Other",
                        "value": None, "other": True})
        return out
    return []


def size_legend(style: dict) -> dict | None:
    """What a legend must show about SIZE, or None when size does not vary.

    Colour and size are independent dimensions — colouring by population while sizing by area is a
    normal thing to want, and `color_mode` is deliberately separate from `size_mode`. A legend that
    shows only the colours therefore describes half the map, and the half it omits is the one
    driving how big things are.

    Returned as the two ENDS plus the field, not every stop: the size expression interpolates
    linearly (see `_size_expression`), so the ends define the whole scale, and a legend that listed
    intermediate stops would imply steps that the map does not draw.
    """
    if (style.get("size_mode") or "fixed") != "proportional":
        return None
    field = (style.get("size_field") or "").strip()
    stops = [s for s in (style.get("size_stops") or [])
             if isinstance(s, (list, tuple)) and len(s) == 2]
    if not field or len(stops) < 2:
        return None
    ordered = sorted(stops, key=lambda s: s[0])
    lo, hi = ordered[0], ordered[-1]
    return {
        "field": field,
        "min_value": lo[0], "min_size": lo[1],
        "max_value": hi[0], "max_size": hi[1],
        "min_label": _num(lo[0]), "max_label": _num(hi[0]),
    }


def color_field(style: dict) -> str | None:
    """The column driving colour, or None for a single symbol.

    A legend that shows five colours without naming what they measure is a puzzle: the reader can
    see that something varies but not what.
    """
    mode = style.get("color_mode") or "single"
    if mode in ("graduated", "categorized"):
        return (style.get("color_field") or "").strip() or None
    return None


def _num(v) -> str:
    """Legend numbers: trim a float that is really an integer, and keep the rest short."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if f == int(f) and abs(f) < 1e15:
        return str(int(f))
    return f"{f:,.2f}".rstrip("0").rstrip(".")
