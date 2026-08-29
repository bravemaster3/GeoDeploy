"""V-16 Dashboard archetype — the config schema, its registry, and its normaliser.

A dashboard is a single-screen grid of widgets built over layers the portal already publishes. Its
defining behaviour is CROSS-FILTERING: interacting with one widget (selecting on the map, clicking a
chart segment, moving a slider) re-queries the others. That wiring is declared per widget, in the
ArcGIS-Dashboards "actions" shape — a SOURCE widget names the TARGET widgets it filters.

This module is the schema authority. It is deliberately the only place that knows what a widget type
is, because three surfaces have to agree on that answer:

  * `services/portal_generator.build_portal_bundle` bakes the resolved config into
    `style.geodeploy.dashboard`,
  * `templates/shared/dashboard.js` renders it in the published portal,
  * `ui/src/components/portal/DashboardBuilder.vue` authors it.

REGISTERING A NEW WIDGET TYPE (the extensibility contract):
  1. Add an entry to `WIDGET_TYPES` here — it declares what data the widget binds to (`needs`),
     whether it can be a filter SOURCE, whether it can be a filter TARGET, and which filter channel
     it listens on (`attr` for field/value filters, `geom` for a spatial selection).
  2. Add a renderer to `WIDGET_RENDERERS` in `templates/shared/dashboard.js` (one function, keyed by
     the same type string).
  3. Add an editor case to `WIDGET_TYPES` in `DashboardBuilder.vue`.
Nothing else in the stack has a per-type branch, so those three are the whole cost.

NORMALISATION, not rejection: an unknown widget type or a dangling action target is DROPPED, never
raised. A dashboard published from a newer builder must still render what this server understands
rather than failing the whole publish — the same posture `resolve_layout` takes.
"""
from __future__ import annotations

import copy
import re

#: Every widget type v1 ships. `needs` is what the builder must bind before the widget can render:
#:   vector  — a vector layer (+ usually a field)
#:   raster  — a raster layer
#:   none    — nothing (the details panel reads whatever is selected; the map reads the portal's layers)
#: `source` = can publish a filter. `target` = can be filtered. `channel` = which filter it listens on.
#:
#: Selector is source-only BY DEFINITION — it is an input, and letting other widgets narrow its
#: options would make the control jump under the hand that is using it. Raster Stats is target-only
#: for the mirror-image reason: it has no attribute table of its own to filter anything else with.
WIDGET_TYPES: dict[str, dict] = {
    # The map's dataSource is OPTIONAL and names the SELECTION layer — the layer a click hit-tests
    # against. Without one the map still draws every portal layer and still offers polygon/bbox
    # draw; it just cannot turn a click into a feature.
    "map":         {"needs": "map",    "source": True,  "target": True,  "channel": "attr"},
    "indicator":   {"needs": "vector", "source": True,  "target": True,  "channel": "attr"},
    "gauge":       {"needs": "vector", "source": True,  "target": True,  "channel": "attr"},
    "chart":       {"needs": "vector", "source": True,  "target": True,  "channel": "attr"},
    "table":       {"needs": "vector", "source": True,  "target": True,  "channel": "attr"},
    # PROFILE is a TARGET only, like rasterstats and for the same reason: it describes the shape of
    # whatever is currently selected — how many, what range, which values are common — and has no
    # single value of its own to publish. Clicking one of its top-N values COULD filter, and that is
    # the obvious v2 seam, but "the panel that tells you what is in the data" should not also be a
    # control that changes what the data is until that is asked for.
    "profile":     {"needs": "vector", "source": False, "target": True,  "channel": "attr"},
    # SCATTER is a target only for now, like profile: it plots features rather than choosing them.
    # Making a dot publish its own feature is the obvious v2 seam and is deliberately not taken here,
    # because a scatter is SAMPLED — clicking a dot would filter to a feature that happens to be in
    # the sample, and the same click on a redrawn sample would select nothing.
    "scatter":     {"needs": "vector", "source": False, "target": True,  "channel": "attr"},
    # SEARCH is a SOURCE only, for the same reason `selector` is: it is an input. Letting other
    # widgets narrow the set it searches would move the control under the hand using it — you would
    # type a name, another widget would narrow, and the name would stop matching.
    "search":      {"needs": "vector", "source": True,  "target": False, "channel": "attr"},
    "selector":    {"needs": "vector", "source": True,  "target": False, "channel": None},
    # LEGEND binds to NOTHING and neither filters nor is filtered: it describes the map's symbology,
    # which no filter changes. `needs: none` for the same reason the details panel has it — there is
    # no layer to pick, because it describes all of them.
    "legend":      {"needs": "none",   "source": False, "target": False, "channel": None},
    "details":     {"needs": "none",   "source": False, "target": True,  "channel": "select"},
    "rasterstats": {"needs": "raster", "source": False, "target": True,  "channel": "geom"},
}

#: Aggregations an indicator / gauge / chart may ask the server for. `count` needs no field; the
#: rest do. Kept as a set here AND validated again in `services/aggregate.py`, because this module
#: normalises a stored config while that one guards a live request — they are different trust
#: boundaries and only one of them is reachable by an anonymous caller.
AGG_OPS = {"count", "sum", "avg", "min", "max"}

#: Time buckets a chart may group a date/timestamp field by.
TIME_BUCKETS = {"hour", "day", "week", "month", "quarter", "year"}

#: Chart shapes. `pie` and `donut` differ only in the hole, but they are separate types because the
#: builder shows them as separate choices and the renderer needs to know which one was chosen.
CHART_KINDS = {"bar", "hbar", "line", "area", "pie", "donut"}

#: How a bar chart assigns colour. See `_normalize_style` for why `single` remains the default.
CHART_COLOR_MODES = {"single", "category", "sequential"}

#: How a table widget arranges its rows: a spreadsheet, or a directory of per-feature cards.
TABLE_LAYOUTS = {"table", "cards"}

#: How a search term is matched. Mirrors `services/aggregate.SEARCH_MODES`; `prefix` scans less.
SEARCH_MODES = {"contains", "prefix"}

#: Where an overlay widget pins itself on the map: the eight compass points — four corners and four
#: edge midpoints. What is excluded is FREE x/y placement, which reads as a design tool and then has
#: to survive every viewport; an anchor is stable at any size because it is defined against an edge
#: rather than against a coordinate. A centred anchor is exactly as stable as a corner, which is why
#: `top-center` (where a search box usually wants to be) belongs here too.
#:
#: What this is NOT is a full-height side RAIL. That is a different shape, and the grid already
#: builds it better: a two-column dashboard puts a real rail beside the map, sized in columns, with
#: the map keeping the rest. An overlay rail would cover half the map permanently and still have to
#: fight the map's own controls for the edge.
OVERLAY_ANCHORS = {
    # The eight compass points…
    "top-left", "top-center", "top-right",
    "left-center", "right-center",
    "bottom-left", "bottom-center", "bottom-right",
    # …and `controls`, which is not a position at all but a HOME: the widget's collapsed icon joins
    # the map's own control cluster, wherever the author put that, and its panel opens beside it. A
    # legend belongs with the zoom and basemap buttons, not floating in a corner over the scale bar.
    # Only meaningful together with `overlayCollapsed` — an always-open panel has no icon to dock.
    "controls",
}

#: The selection modes a map widget can offer. All of them normalise to ONE geometry filter
#: downstream (a bbox IS a rectangular polygon), which is why they are a list of switches here
#: rather than several widget types.
#:
#: `extent` is the odd one and is deliberately NOT in the default set: it is a switch rather than a
#: gesture (the map's own viewport becomes the filter, republished on every pan), and turning it on
#: for every dashboard that never named its tools would silently narrow widgets nobody asked to
#: narrow. It is offered, not assumed.
MAP_TOOLS = ("click", "polygon", "bbox", "extent")
DEFAULT_MAP_TOOLS = ("click", "polygon", "bbox")


def _tools(value) -> list[str]:
    picked = [t for t in (value if value is not None else DEFAULT_MAP_TOOLS) if t in MAP_TOOLS]
    return picked or list(DEFAULT_MAP_TOOLS)


#: Selector input kinds. `category` = a dropdown of distinct values, `range` = a two-handle numeric
#: slider, `date` = a from/to date pair.
SELECTOR_KINDS = {"category", "range", "date"}

#: Zonal statistics a Raster Stats widget can display. `histogram` is not a number — it renders as a
#: small distribution plot rather than a stat cell — but it travels in the same list because it comes
#: from the same TiTiler response and the author picks it the same way.
RASTER_STATS = {"min", "max", "mean", "sum", "std", "median", "count", "histogram"}

#: Grid geometry. 12 columns is the web's default mental model for a dashboard and divides by 2, 3,
#: 4 and 6, so a two-, three- or four-across row of indicators lands on whole columns.
GRID_COLS = 12
GRID_MIN_W, GRID_MIN_H = 2, 2
GRID_MAX_H = 24

#: A widget id is generated by the builder and lands in DOM ids and CSS selectors in the published
#: page, so it is constrained to a safe alphabet rather than trusted.
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")

#: The map widget's click hit radius, in SCREEN PIXELS. The runtime converts it to degrees at the
#: click's zoom and latitude, so the same setting means the same aim at every scale. 6 px is about a
#: fingertip's worth of slack on a desktop and is what a point layer needs to be clickable at all;
#: 0 is legal and exact, which is all a polygon layer ever needs.
DEFAULT_TOL_PX, MAX_TOL_PX = 6, 24

#: Auto-refresh, for near-real-time layers. 0 = off. The floor is 5 s because anything faster is a
#: request storm rather than a refresh, and the ceiling is an hour because past that a visitor has
#: reloaded the page anyway.
REFRESH_MIN, REFRESH_MAX = 5, 3600


def _int(value, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


def _str(value, allowed: set[str] | None = None, default: str | None = None) -> str | None:
    if not isinstance(value, str):
        return default
    text = value.strip()
    if not text:
        return default
    if allowed is not None and text not in allowed:
        return default
    return text


def _layer_ref(source: dict | None) -> dict | None:
    """`{layerType, layerId}` (+ optional field/band) from an arbitrary dataSource, or None.

    `layerId` stays an INT because that is what `layer_configs` uses to name a layer everywhere else
    in the portal — the dashboard joins to the same map layers the layer list toggles, and a string
    id would silently fail that join.
    """
    if not isinstance(source, dict):
        return None
    kind = _str(source.get("layerType"), {"vector", "raster"})
    if not kind:
        return None
    try:
        layer_id = int(source.get("layerId"))
    except (TypeError, ValueError):
        return None
    out: dict = {"layerType": kind, "layerId": layer_id}
    field = _str(source.get("field"))
    if field:
        out["field"] = field
    return out


def _normalize_source(widget_type: str, source: dict | None) -> dict | None:
    """Per-type dataSource normalisation. Returns None when the widget is not yet bound — an
    UNBOUND widget is legal and is exactly what a preset template ships (a preset is a starting
    layout, and the layer ids it would need do not exist until an author picks them). The renderer
    draws such a widget as a labelled placeholder instead of an error."""
    spec = WIDGET_TYPES[widget_type]
    if spec["needs"] == "none":
        return None
    src = dict(source) if isinstance(source, dict) else {}

    if spec["needs"] == "map":
        # The map is the one widget that is useful entirely unbound, so its dataSource is built even
        # when no layer is named — `tools` is a map property, not a data property.
        out: dict = {"tools": _tools(src.get("tools"))}
        # HIT RADIUS IN SCREEN PIXELS, converted to degrees by the runtime at the click's own zoom
        # and latitude. It sits OUTSIDE the layer-bound branch because a click now falls through to
        # the portal's other vector layers when the named one misses, so the radius applies even
        # with no selection layer named at all.
        #
        # Pixels, not degrees, and this is the correction that matters: `tol` defaulted to 0, which
        # makes the pick an exact `ST_Intersects` against a zero-area point. That can only ever hit
        # a polygon — clicking a POINT layer resolved to nothing at every zoom, with no error to
        # show for it. Degrees were also the wrong thing to ask an author for: the same 0.0005° is
        # half a screen at z18 and invisible at z6.
        out["tolPx"] = _int(src.get("tolPx"), DEFAULT_TOL_PX, 0, MAX_TOL_PX)
        ref = _layer_ref(source)
        if ref and ref["layerType"] == "vector":
            out["layerType"] = "vector"
            out["layerId"] = ref["layerId"]
            # `field`: the attribute a CLICK publishes alongside the geometry. Clicking a region
            # both selects its polygon (which drives raster zonal stats) and filters the
            # attribute-backed widgets to that region's name — one click, both channels, which is
            # what makes a choropleth dashboard feel wired rather than merely linked.
            if ref.get("field"):
                out["field"] = ref["field"]
            # A degree floor, kept for configs authored against the original schema and for the rare
            # author who wants a fixed ground distance rather than a fixed screen distance. The
            # runtime takes the LARGER of this and the pixel radius.
            out["tol"] = max(0.0, min(_num(src.get("tol"), 0.0) or 0.0, 1.0))
        return out

    ref = _layer_ref(source)
    if ref is None:
        return None

    if spec["needs"] == "raster":
        if ref["layerType"] != "raster":
            return None
        stats = [s for s in (src.get("stats") or []) if s in RASTER_STATS]
        ref["stats"] = stats or ["min", "max", "mean"]
        ref["band"] = _int(src.get("band"), 1, 1, 64)
        return ref

    # vector-backed
    if ref["layerType"] != "vector":
        return None
    if widget_type in ("indicator", "gauge"):
        # CLICK-TO-FILTER. An indicator or gauge has no categories of its own to publish, so it is a
        # filter source only when the author says WHAT it stands for: "Open incidents" filtering the
        # rest to status='open'. Without the pair it is simply not clickable — which is the honest
        # outcome, rather than a wired action that silently does nothing.
        ffield = _str(src.get("filterField"))
        fvalue = src.get("filterValue")
        if ffield and fvalue not in (None, ""):
            ref["filterField"] = ffield
            ref["filterValue"] = fvalue if isinstance(fvalue, (str, int, float, bool)) else str(fvalue)
    if widget_type in ("indicator", "gauge", "chart"):
        ref["op"] = _str(src.get("op"), AGG_OPS, "count")
        if ref["op"] != "count" and not ref.get("field"):
            # An aggregate with no column to aggregate cannot be answered. Falling back to `count`
            # keeps the widget alive and showing a true number, which is better than a red box: the
            # author sees a count where they expected a sum and fixes the binding.
            ref["op"] = "count"
    if widget_type == "chart":
        ref["groupBy"] = _str(src.get("groupBy"))
        ref["timeBucket"] = _str(src.get("timeBucket"), TIME_BUCKETS)
        ref["limit"] = _int(src.get("limit"), 12, 2, 100)
        ref["sort"] = _str(src.get("sort"), {"value_desc", "value_asc", "key_asc"}, "value_desc")
    if widget_type == "table":
        fields = [f for f in (src.get("fields") or []) if isinstance(f, str) and f.strip()]
        ref["fields"] = fields[:24]     # a scrollable row, not a spreadsheet
        # The column a ROW CLICK publishes as a filter. Defaults to the first shown column, so a
        # table wired to filter something works the moment it is wired.
        ref["keyField"] = _str(src.get("keyField")) or (ref["fields"][0] if ref["fields"] else None)
        # CARD layout only: the field used as each card's heading. A directory reads heading-first
        # ("5th Ave Parking Deck" over the name and the phone number), and that heading is rarely the
        # same column as `keyField`, which exists to say what a click FILTERS on. Falls back to
        # keyField so a table switched to cards is readable before anyone configures it.
        ref["titleField"] = _str(src.get("titleField")) or ref["keyField"]
        ref["pageSize"] = _int(src.get("pageSize"), 50, 5, 500)
        ref["sort"] = _str(src.get("sort"))
        ref["dir"] = _str(src.get("dir"), {"asc", "desc"}, "asc")
    if widget_type == "search":
        fields = [f for f in (src.get("fields") or []) if isinstance(f, str) and f.strip()]
        # Capped low: every named column is another column the scan has to read, and a search that
        # looks in eight columns is usually a sign the author has not decided what it searches FOR.
        ref["fields"] = fields[:8]
        # The column a chosen result publishes as a filter, and the one shown as its heading. They
        # differ for the same reason they do on a card list: what you READ is the name, what you
        # FILTER on is the identity.
        ref["keyField"] = _str(src.get("keyField")) or (ref["fields"][0] if ref["fields"] else None)
        ref["titleField"] = _str(src.get("titleField")) or ref["keyField"]
        ref["searchMode"] = _str(src.get("searchMode"), SEARCH_MODES, "contains")
        ref["limit"] = _int(src.get("limit"), 8, 3, 25)
        placeholder = _str(src.get("placeholder"))
        if placeholder:
            ref["placeholder"] = placeholder[:60]
    if widget_type == "scatter":
        ref["xField"] = _str(src.get("xField"))
        ref["yField"] = _str(src.get("yField"))
        # Points drawn. The cap is the renderer's limit as much as the query's: past a few thousand
        # dots an SVG scatter is a solid shape and every one of them is a DOM node.
        ref["limit"] = _int(src.get("limit"), 1500, 50, 3000)
    if widget_type == "profile":
        fields = [f for f in (src.get("fields") or []) if isinstance(f, str) and f.strip()]
        # Capped low on purpose: this widget is read, not scanned. Every field costs a pass, and a
        # panel describing forty columns is a data dictionary, not a dashboard card.
        ref["fields"] = fields[:12]
        # How many values a categorical field shows. 5 is the default because a top list is there to
        # say "mostly these" — past about seven it stops being a summary and becomes the column.
        ref["topN"] = _int(src.get("topN"), 5, 3, 20)
    if widget_type == "selector":
        ref["kind"] = _str(src.get("kind"), SELECTOR_KINDS, "category")
        ref["multi"] = bool(src.get("multi", True))
    return ref


def _normalize_style(widget_type: str, style: dict | None) -> dict:
    """The per-widget presentation options. Everything here is cosmetic and everything has a working
    default, so a widget with no style block renders identically to one with the defaults spelled
    out — which is what lets a preset template stay readable."""
    s = style if isinstance(style, dict) else {}
    out: dict = {}
    fmt = _str(s.get("format"), {"auto", "integer", "decimal", "percent", "compact"}, "auto")
    out["format"] = fmt
    out["decimals"] = _int(s.get("decimals"), 1, 0, 6)
    unit = _str(s.get("unit"))
    if unit:
        out["unit"] = unit[:16]
    if widget_type == "chart":
        out["chart"] = _str(s.get("chart"), CHART_KINDS, "bar")
        out["legend"] = bool(s.get("legend", True))
        # How the bars are coloured. `single` is the default and stays the default: an axis that
        # already names the category does not need the bars to repeat it, and every dashboard
        # published so far keeps the look it had. `category` is for a NOMINAL key, `sequential` for
        # an ORDERED one (a construction period, a decade) — a rainbow across an ordered key implies
        # the categories are unrelated when they have a direction.
        out["colorMode"] = _str(s.get("colorMode"), CHART_COLOR_MODES, "single")
        # Printed values on the bars. None = the renderer's own default, which is ON for a
        # horizontal bar (the row has space for it) and OFF for a vertical one (the column does not).
        if s.get("valueLabels") is not None:
            out["valueLabels"] = bool(s.get("valueLabels"))
        out["valueShare"] = bool(s.get("valueShare", True))
    if widget_type == "table":
        # `table` is the spreadsheet; `cards` is a directory — one card per feature with a heading
        # and a few fields under it. Same query, same paging, same click behaviour: only the shape on
        # screen differs, which is why this is a style option and not a second widget type.
        out["layout"] = _str(s.get("layout"), TABLE_LAYOUTS, "table")
    if widget_type == "gauge":
        out["min"] = _num(s.get("min"), 0.0)
        out["max"] = _num(s.get("max"), 100.0)
        if out["max"] <= out["min"]:
            out["max"] = out["min"] + 1.0
        # Threshold bands: [{from, color, label}]. Sorted and clamped into the dial's own range, so a
        # band left over from a previous binding cannot draw outside the arc.
        bands = []
        for band in (s.get("bands") or [])[:6]:
            if not isinstance(band, dict):
                continue
            start = _num(band.get("from"), None)
            if start is None:
                continue
            bands.append({"from": max(out["min"], min(out["max"], start)),
                          "color": _hex(band.get("color")) or "#3b82f6",
                          "label": (_str(band.get("label")) or "")[:24]})
        bands.sort(key=lambda b: b["from"])
        out["bands"] = bands
    if widget_type == "indicator":
        # Comparison / target value — "vs. last month". `compareMode` says how to render the delta.
        target = _num(s.get("target"), None)
        if target is not None:
            out["target"] = target
        out["compareMode"] = _str(s.get("compareMode"), {"delta", "percent", "none"}, "delta")
        out["goodDirection"] = _str(s.get("goodDirection"), {"up", "down", "none"}, "up")
    color = _hex(s.get("color"))
    if color:
        out["color"] = color
    return out


def _normalize_actions(actions: dict | None) -> dict:
    """`{filters: [widgetId, …], listens: bool}` — the source→target wiring.

    `filters` is the list of widgets this one FILTERS when it is interacted with. `listens` is
    whether this widget is itself re-queried when someone else publishes a filter. Both are stored
    per widget so the two directions can be set independently, which is what the ArcGIS actions
    model gives you: a map can drive four widgets without being driven by any of them.

    Dangling ids are pruned by `resolve_dashboard` once every widget id is known — not here, because
    a single widget cannot see its siblings.
    """
    a = actions if isinstance(actions, dict) else {}
    targets = [t for t in (a.get("filters") or []) if isinstance(t, str) and _ID_RE.match(t)]
    # De-duplicated, order preserved: the builder appends and a double-click would otherwise wire
    # the same target twice and re-render it twice per interaction.
    seen, filters = set(), []
    for t in targets:
        if t not in seen:
            seen.add(t)
            filters.append(t)
    return {"filters": filters[:32], "listens": bool(a.get("listens", True))}


def _normalize_layout(layout: dict | None, index: int) -> dict:
    """Grid placement. A widget with no layout is stacked in a default full-width row rather than
    dropped — a config hand-written without coordinates still produces a usable page."""
    lay = layout if isinstance(layout, dict) else {}
    w = _int(lay.get("w"), 6, GRID_MIN_W, GRID_COLS)
    h = _int(lay.get("h"), 4, GRID_MIN_H, GRID_MAX_H)
    x = _int(lay.get("x"), 0, 0, GRID_COLS - GRID_MIN_W)
    if x + w > GRID_COLS:
        w = GRID_COLS - x
    y = _int(lay.get("y"), index * 4, 0, 400)
    out = {"x": x, "y": y, "w": w, "h": h}
    # OVERLAY: the widget floats over the map, pinned to one of its corners, instead of taking a
    # grid cell. This is what a search box on a map wants — it belongs ON the map, not beside it —
    # and it is the only placement available in an archetype whose map is full-bleed (a webmap or a
    # storymap has no widget grid to sit in). Absent = a normal grid cell, which stays the default:
    # a widget that silently left the grid would be a surprise, and the grid is what a dashboard is.
    anchor = _str(lay.get("overlay"), OVERLAY_ANCHORS)
    if anchor:
        out["overlay"] = anchor
        # Overlay size in PIXELS, not grid columns: it is measured against the map, which has no
        # columns. Bounded so an overlay can never swallow the map it sits on.
        out["overlayW"] = _int(lay.get("overlayW"), 260, 140, 520)
        # Height is OPTIONAL and 0 means "as tall as its content" — the right default for the small
        # panels this placement is for (a search box should not reserve 300px of map to show one
        # input). A list that wants to scroll inside a fixed box sets a number instead.
        out["overlayH"] = _int(lay.get("overlayH"), 0, 0, 800)
        # COLLAPSED: the overlay ships as a single icon button and opens when clicked. For a search
        # box on a map this is usually what is wanted — the box is needed for a few seconds and the
        # map is needed the rest of the time — and it is the difference between a widget that sits
        # ON the map and one that sits IN THE WAY of it. Absent = always open, which is right for a
        # legend or a readout that is there to be read.
        out["overlayCollapsed"] = bool(lay.get("overlayCollapsed"))
    return out


def _num(value, default):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if out == out else default   # NaN guard


_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def _hex(value) -> str | None:
    text = value.strip() if isinstance(value, str) else ""
    return text if _HEX.match(text) else None


def _normalize_widget(raw: dict, index: int, used_ids: set[str]) -> dict | None:
    if not isinstance(raw, dict):
        return None
    wtype = _str(raw.get("type"), set(WIDGET_TYPES))
    if not wtype:
        return None       # unknown type from a newer builder — drop the widget, keep the dashboard
    wid = _str(raw.get("id")) or ""
    if not _ID_RE.match(wid) or wid in used_ids:
        wid = f"w{index + 1}"
        n = index + 1
        while wid in used_ids:
            n += 1
            wid = f"w{n}"
    used_ids.add(wid)
    return {
        "id": wid,
        "type": wtype,
        "title": (_str(raw.get("title")) or "")[:80],
        "layout": _normalize_layout(raw.get("layout"), index),
        "dataSource": _normalize_source(wtype, raw.get("dataSource")),
        "style": _normalize_style(wtype, raw.get("style")),
        "actions": _normalize_actions(raw.get("actions")),
    }


def resolve_dashboard(config: dict | None) -> dict | None:
    """A stored/authored dashboard config → the normalised manifest baked into the published page.

    Returns None when there is nothing to render (no config, or every widget was dropped), so the
    caller can bake `null` and the runtime can treat "no dashboard" as one condition rather than
    "an object with an empty list".

    THE INVARIANTS THE RUNTIME IS ALLOWED TO ASSUME, and therefore the ones this function owns:
      * every widget has a unique, DOM-safe `id`;
      * every widget's `type` is in `WIDGET_TYPES`;
      * every id in `actions.filters` names a widget that exists AND can be a target;
      * a widget that cannot be a source has an empty `actions.filters`;
      * `layout` is inside the grid.
    dashboard.js does no validation of its own precisely because of this — one validator, run at
    publish, rather than a second half-implementation in the browser.
    """
    if not isinstance(config, dict):
        return None
    raw_widgets = config.get("widgets")
    if not isinstance(raw_widgets, list):
        return None

    used_ids: set[str] = set()
    widgets: list[dict] = []
    for i, raw in enumerate(raw_widgets[:40]):     # a screen holds nowhere near this many
        w = _normalize_widget(raw, i, used_ids)
        if w:
            widgets.append(w)
    if not widgets:
        return None

    # Second pass: the wiring, now that every id is known. A target must EXIST, must be able to
    # listen, and must not be the source itself (a widget filtering itself is a re-render loop).
    by_id = {w["id"]: w for w in widgets}
    for w in widgets:
        spec = WIDGET_TYPES[w["type"]]
        if not spec["source"]:
            w["actions"]["filters"] = []
            continue
        w["actions"]["filters"] = [
            t for t in w["actions"]["filters"]
            if t != w["id"] and t in by_id
            and WIDGET_TYPES[by_id[t]["type"]]["target"]
            and by_id[t]["actions"]["listens"]
        ]
    for w in widgets:
        if not WIDGET_TYPES[w["type"]]["target"]:
            w["actions"]["listens"] = False

    grid = config.get("grid") if isinstance(config.get("grid"), dict) else {}
    return {
        "version": 1,
        "grid": {
            "cols": GRID_COLS,                                  # fixed: the builder's own geometry
            "rowHeight": _int(grid.get("rowHeight"), 90, 40, 240),
            "gap": _int(grid.get("gap"), 10, 0, 32),
        },
        "refresh": _int(config.get("refresh"), 0, 0, REFRESH_MAX) if config.get("refresh") else 0,
        "widgets": widgets,
        "relations": _normalize_relations(config.get("relations"), widgets),
    }


#: Declared joins between two vector layers, so an attribute filter can travel from one to the
#: other. Four is plenty for a single screen and keeps the resolver's per-filter lookup trivial.
MAX_RELATIONS = 8


def _normalize_relations(raw, widgets: list[dict]) -> list[dict]:
    """`[{left:{layerId, field}, right:{layerId, field}}]` — an equi-join between two vector layers.

    WHY THIS EXISTS. Attribute filters are layer-scoped: a predicate on `canton` is meaningless
    against a table that has no such column, so `filtersFor` drops it rather than silently returning
    nothing. That is right in the absence of any stated connection between two layers — and wrong the
    moment the author knows one. A relation is the author stating it: *these two layers describe the
    same things, and this pair of columns is how you tell*.

    UNDIRECTED, deliberately. An author declaring `buildings.egid = entrances.egid` means the two are
    related, not that filtering may only travel one way; requiring them to declare it twice would be
    a way to get it half-declared. The resolver reads it from either side.

    Only layers a widget actually binds to are kept — a relation naming a layer nobody displays is a
    join that can never fire, and keeping it would put a row in the editor that does nothing.
    """
    if not isinstance(raw, list):
        return []
    bound: set[int] = set()
    for w in widgets:
        ds = w.get("dataSource") or {}
        if ds.get("layerType") == "vector" and isinstance(ds.get("layerId"), int):
            bound.add(ds["layerId"])

    out: list[dict] = []
    seen: set[tuple] = set()
    for rel in raw[:MAX_RELATIONS * 2]:
        if not isinstance(rel, dict):
            continue
        left, right = rel.get("left"), rel.get("right")
        if not isinstance(left, dict) or not isinstance(right, dict):
            continue
        try:
            lid, rid = int(left.get("layerId")), int(right.get("layerId"))
        except (TypeError, ValueError):
            continue
        lf, rf = _str(left.get("field")), _str(right.get("field"))
        if not lf or not rf:
            continue
        # A layer joined to itself is not a relation, it is a filter — and it would make the
        # resolver translate a predicate into the same predicate for ever.
        if lid == rid:
            continue
        if lid not in bound or rid not in bound:
            continue
        key = tuple(sorted([(lid, lf), (rid, rf)]))
        if key in seen:
            continue
        seen.add(key)
        out.append({"left": {"layerId": lid, "field": lf},
                    "right": {"layerId": rid, "field": rf}})
        if len(out) >= MAX_RELATIONS:
            break
    return out


def dashboard_layer_refs(dashboard: dict | None) -> tuple[set[int], set[int]]:
    """`(vector_ids, raster_ids)` a resolved dashboard binds to.

    `build_portal_bundle` uses this to bake field metadata for exactly those layers, and
    `routers/portals` uses it to make a dashboard-only raster publicly readable: a Raster Stats
    widget queries a COG the map may never draw, so the layer would otherwise be private to a
    portal that is publishing statistics about it.
    """
    vectors: set[int] = set()
    rasters: set[int] = set()
    for w in (dashboard or {}).get("widgets", []):
        src = w.get("dataSource")
        if not isinstance(src, dict):
            continue
        if not src.get("layerId"):
            continue          # an unbound widget, or a map widget with tools but no selection layer
        if src.get("layerType") == "raster":
            rasters.add(int(src["layerId"]))
        elif src.get("layerType") == "vector":
            vectors.add(int(src["layerId"]))
    return vectors, rasters


def clone(config: dict | None) -> dict | None:
    """A deep copy, for callers that normalise then mutate (the preset seeder)."""
    return copy.deepcopy(config) if isinstance(config, dict) else None
