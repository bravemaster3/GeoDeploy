"""GeoLibre `.geolibre.json` → GeoDeploy portal importer (SPIKE / Front 1).

GeoLibre (opengeos, a browser QGIS) saves a whole map as a native, versioned
`.geolibre.json` project (layers + per-layer `LayerStyle` + view + basemap +
storymap). GeoDeploy publishes MapLibre-styled portals. This module is the
**interop bridge**: it parses a GeoLibre project and produces a GeoDeploy
**import plan** — one entry per layer, already translated to the shapes our
publisher understands (MapLibre paint for vectors, raster style for COGs,
external tile config for XYZ/WMS/PMTiles), plus the portal view/basemap/story.

Design notes (see notes_temp/GEOLIBRE_INTEROP.md for the full plan):

- **Pure & infra-free.** No DB, no network, no Celery — every function here is a
  data transform, unit-testable against a fixture. The *ingestion* of each
  layer's data (geojson → PostGIS/GeoParquet; COG → MinIO/TiTiler) and the
  portal build (`build_portal_bundle`) are the NEXT step; this spike proves the
  hard, uncertain part first: the style/format translation.
- **Style translation mirrors GeoLibre's own** `packages/map/src/style-mapper.ts`
  + `packages/core/src/vector-color.ts` (which we cannot import — those packages
  are `"private"`), so an imported layer renders the same single / graduated /
  categorized / expression / rule-based color it did in GeoLibre.
- **`LayerStyle` lives inline on `layer.style`** (GeoLibre's `normalizeLayer`
  merges it over `DEFAULT_LAYER_STYLE`); we fall back to the top-level
  `project.styles[layer.id]` if a producer put it there instead.
- **Source identity for round-tripping.** Every imported layer carries a
  `source_identity` ({origin, geolibre_layer_id, project}) so a future
  edit-in-GeoLibre → write-back-to-GeoDeploy flow (data cleaning, etc.) can find
  the exact table/object it came from. This spike only *records* it.
- **Rasters are first-class**, not an afterthought: `cog`/`raster` → GeoDeploy
  raster (TiTiler) style; `xyz`/`wms`/`wmts`/`vector-tiles`/`pmtiles`/`arcgis`/
  `mbtiles` → external tile passthrough.
"""
from __future__ import annotations

import json
import re
from typing import Any

SUPPORTED_MAJOR = 0  # `.geolibre.json` format 0.x — bump the accepted range as the format evolves.

_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")

# GeoLibre layer.type → how GeoDeploy will host it.
_RASTER_TYPES = {"cog", "raster"}
_TILE_TYPES = {"xyz", "wms", "wmts", "vector-tiles", "pmtiles", "arcgis", "mbtiles"}
_VECTOR_TYPES = {"geojson", "flatgeobuf", "geoparquet", "duckdb-query"}


# ── parse ─────────────────────────────────────────────────────────────────────

def parse_geolibre_project(source: str | bytes | dict) -> dict:
    """Load + minimally validate a `.geolibre.json`. Accepts JSON text/bytes or a
    parsed dict. Raises ValueError on a non-project or an unsupported format."""
    data = source if isinstance(source, dict) else json.loads(source)
    if not isinstance(data, dict):
        raise ValueError("Not a GeoLibre project (expected a JSON object).")
    version = data.get("version")
    if not version or not data.get("name") or not data.get("mapView"):
        raise ValueError("Invalid GeoLibre project: missing version/name/mapView.")
    try:
        major = int(str(version).split(".", 1)[0])
    except ValueError as exc:
        raise ValueError(f"Unparseable project version {version!r}.") from exc
    if major != SUPPORTED_MAJOR:
        raise ValueError(
            f"Unsupported .geolibre.json format {version} (this importer handles "
            f"{SUPPORTED_MAJOR}.x).")
    return data


# ── geometry inspection ───────────────────────────────────────────────────────

def _geometry_profile(geojson: dict | None) -> dict:
    """Which geometry classes a FeatureCollection contains (like GeoLibre's
    detectGeometryProfile). No data → assume the safe superset."""
    prof = {"point": False, "line": False, "polygon": False}
    if not geojson:
        return {"point": True, "line": True, "polygon": True}
    for feat in geojson.get("features", []):
        _classify_geometry((feat or {}).get("geometry"), prof)
    return prof


def _classify_geometry(geom: dict | None, prof: dict) -> None:
    if not geom:
        return
    t = geom.get("type")
    if t in ("Point", "MultiPoint"):
        prof["point"] = True
    elif t in ("LineString", "MultiLineString"):
        prof["line"] = True
    elif t in ("Polygon", "MultiPolygon"):
        prof["polygon"] = True
    elif t == "GeometryCollection":
        for child in geom.get("geometries", []):
            _classify_geometry(child, prof)


def _has_z(geojson: dict | None) -> bool:
    """True if any coordinate carries a third (Z) ordinate — the trigger for the
    deck.gl 3D-Z render path (GeoLibre's geojsonHasZCoordinates)."""
    if not geojson:
        return False
    for feat in geojson.get("features", []):
        if _coords_have_z(((feat or {}).get("geometry") or {}).get("coordinates")):
            return True
    return False


def _coords_have_z(coords: Any) -> bool:
    if not isinstance(coords, (list, tuple)) or not coords:
        return False
    # A position is [x, y] or [x, y, z] where the members are numbers.
    if isinstance(coords[0], (int, float)):
        return len(coords) >= 3 and isinstance(coords[2], (int, float))
    return any(_coords_have_z(c) for c in coords)


# ── LayerStyle → MapLibre color values (mirror vector-color.ts) ────────────────

def _sv(style: dict, key: str, default: Any) -> Any:
    v = style.get(key)
    return default if v is None else v


def _vector_color_value(style: dict, single_default: str) -> Any:
    """The MapLibre `*-color` value for the style's `vectorStyleMode`: a hex
    string (single) or an expression (graduated `step` / categorized `match` /
    expression passthrough / rule-based `case`)."""
    mode = _sv(style, "vectorStyleMode", "single")
    prop = _sv(style, "vectorStyleProperty", "")
    if mode == "categorized" and prop:
        stops = [s for s in _sv(style, "vectorStyleStops", [])
                 if str(s.get("value", "")).strip() and _HEX.match(str(s.get("color", "")))]
        if stops:
            expr: list = ["match", ["get", prop]]
            for s in stops:
                expr += [str(s["value"]).strip(), s["color"]]
            expr.append(single_default)  # catch-all
            return expr
    elif mode == "graduated" and prop:
        stops = _graduated_stops(style)
        if stops:
            expr = ["step", ["to-number", ["get", prop], stops[0]["value"]], stops[0]["color"]]
            for s in stops[1:]:
                expr += [s["value"], s["color"]]
            return expr
    elif mode == "expression":
        raw = str(_sv(style, "vectorStyleExpression", "")).strip()
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    return parsed
            except json.JSONDecodeError:
                pass  # caller records a warning
    elif mode == "rule-based":
        expr = ["case"]
        for rule in _sv(style, "vectorRules", []):
            if rule.get("enabled") is False or not _HEX.match(str(rule.get("color", ""))):
                continue
            filt = rule.get("filter")
            if isinstance(filt, list):
                expr += [filt, rule["color"]]
        if len(expr) > 1:
            expr.append(single_default)
            return expr
    return single_default


def _graduated_stops(style: dict) -> list[dict]:
    """Numeric, hex-valid, ascending, de-duplicated breaks (GeoLibre graduatedStops)."""
    cleaned = []
    for s in _sv(style, "vectorStyleStops", []):
        val = s.get("value")
        try:
            num = float(val)
        except (TypeError, ValueError):
            continue
        if num == num and _HEX.match(str(s.get("color", ""))):  # num==num rejects NaN
            cleaned.append({"value": num, "color": s["color"]})
    cleaned.sort(key=lambda s: s["value"])
    out: list[dict] = []
    for s in cleaned:
        if not out or s["value"] > out[-1]["value"]:
            out.append(s)
    return out


# ── LayerStyle → MapLibre paint (mirror style-mapper.ts) ───────────────────────

def _fill_paint(style: dict, opacity: float) -> dict:
    return {
        "fill-color": _vector_color_value(style, _sv(style, "fillColor", "#3b82f6")),
        "fill-opacity": _sv(style, "fillOpacity", 0.6) * opacity,
        "fill-outline-color": _sv(style, "strokeColor", "#1e40af"),
    }


def _line_paint(style: dict, opacity: float) -> dict:
    return {
        "line-color": _vector_color_value(style, _sv(style, "strokeColor", "#1e40af")),
        "line-width": _sv(style, "strokeWidth", 2),
        "line-opacity": opacity,
    }


def _circle_paint(style: dict, opacity: float) -> dict:
    return {
        "circle-color": _vector_color_value(style, _sv(style, "fillColor", "#3b82f6")),
        "circle-radius": _sv(style, "circleRadius", 6),
        "circle-opacity": _sv(style, "fillOpacity", 0.6) * opacity,
        "circle-stroke-color": _sv(style, "strokeColor", "#1e40af"),
        "circle-stroke-width": _sv(style, "strokeWidth", 1),
    }


def _fill_extrusion_paint(style: dict, opacity: float) -> dict:
    prop = str(_sv(style, "extrusionHeightProperty", "")).strip()
    scale = _sv(style, "extrusionHeightScale", 1)
    height: Any = (["*", ["to-number", ["get", prop], 0], scale] if prop else scale)
    return {
        "fill-extrusion-color": _vector_color_value(style, _sv(style, "extrusionColor", "#aa8866")),
        "fill-extrusion-opacity": _sv(style, "extrusionOpacity", 1) * opacity,
        "fill-extrusion-height": height,
        "fill-extrusion-base": _sv(style, "extrusionBase", 0),
        "fill-extrusion-vertical-gradient": True,
    }


def _raster_paint(style: dict, opacity: float) -> dict:
    return {
        "raster-opacity": opacity,
        "raster-brightness-min": _sv(style, "rasterBrightnessMin", 0),
        "raster-brightness-max": _sv(style, "rasterBrightnessMax", 1),
        "raster-saturation": _sv(style, "rasterSaturation", 0),
        "raster-contrast": _sv(style, "rasterContrast", 0),
        "raster-hue-rotate": _sv(style, "rasterHueRotate", 0),
    }


# ── unsupported-feature detection (surface, never silently drop) ──────────────

def _style_warnings(style: dict, prof: dict) -> list[str]:
    w = []
    labels = style.get("labels") or {}
    if labels.get("enabled"):
        w.append("Labels are not imported yet (symbol layer TODO).")
    if prof["point"] and _sv(style, "pointRenderer", "single") in ("heatmap", "cluster"):
        w.append(f"Point renderer '{style['pointRenderer']}' is not imported yet; drawn as circles.")
    if style.get("markerEnabled"):
        w.append("Custom marker shape not imported yet; points drawn as circles.")
    if prof["polygon"] and _sv(style, "fillPattern", "none") not in ("none", None):
        w.append("Fill pattern not imported; flat fill used.")
    for key, label in (("diagramType", "per-feature diagrams"),
                       ("lineDecoration", "line decorations"),
                       ("geometryGenerator", "geometry generators")):
        if _sv(style, key, "none") not in ("none", None):
            w.append(f"{label.capitalize()} not imported.")
    if style.get("invertedFillEnabled"):
        w.append("Inverted-polygon fill not imported.")
    return w


# ── per-layer import ──────────────────────────────────────────────────────────

def import_layer(layer: dict, project: dict) -> dict:
    """Translate one GeoLibre layer into a GeoDeploy import-plan entry."""
    style = {**(project.get("styles", {}).get(layer.get("id"), {})), **(layer.get("style") or {})}
    opacity = float(layer.get("opacity", 1) or 1)
    gtype = layer.get("type", "geojson")
    out: dict = {
        "name": layer.get("name") or layer.get("id") or "Layer",
        "geolibre_type": gtype,
        "visible": layer.get("visible", True),
        "opacity": opacity,
        "source_identity": {
            "origin": "geolibre",
            "geolibre_layer_id": layer.get("id"),
            "project": project.get("name"),
        },
        "warnings": [],
    }

    # ── raster (COG / GeoTIFF) → GeoDeploy TiTiler raster ────────────────────
    if gtype in _RASTER_TYPES:
        src = layer.get("source") or {}
        out.update({
            "target": "raster",
            "render_mode": "raster",
            "source": {"url": src.get("url")},
            "raster_style": {
                # GeoDeploy raster style keys (see portal_generator.generate_style).
                "colormap": src.get("colormap"),
                "rescale": ([src["rescaleMin"], src["rescaleMax"]]
                            if src.get("rescaleMin") is not None and src.get("rescaleMax") is not None
                            else None),
                "bidx": src.get("bands"),
                "nodata": src.get("nodata"),
                "paint": _raster_paint(style, opacity),
            },
        })
        return out

    # ── external tiles (XYZ / WMS / PMTiles / ArcGIS / vector tiles) ─────────
    if gtype in _TILE_TYPES:
        src = layer.get("source") or {}
        out.update({
            "target": "external",
            "render_mode": "tiles",
            "source": {
                "kind": gtype,
                "tiles": src.get("tiles"),
                "url": src.get("url"),
                "tile_size": src.get("tileSize", 256),
                "attribution": src.get("attribution"),
                "minzoom": src.get("minzoom"),
                "maxzoom": src.get("maxzoom"),
            },
            "raster_paint": _raster_paint(style, opacity) if gtype != "vector-tiles" else None,
        })
        return out

    # ── vector (geojson & friends) → PostGIS/GeoParquet + MapLibre paint ─────
    geojson = layer.get("geojson")
    prof = _geometry_profile(geojson)
    has_z = _has_z(geojson)
    out["warnings"].extend(_style_warnings(style, prof))
    out.update({
        "target": "vector",
        "geojson": geojson,
        "source_path": layer.get("sourcePath"),
        "has_z": has_z,
        "popup_fields": (layer.get("metadata") or {}).get("popupFields", []),
    })

    if style.get("extrusionEnabled") and prof["polygon"]:
        out["render_mode"] = "extrusion"
        out["maplibre_layers"] = [{
            "suffix": "fill-extrusion", "type": "fill-extrusion",
            "geometry": "polygon", "paint": _fill_extrusion_paint(style, opacity),
        }]
        return out

    # Plain 2D: geometry-filtered fill / line / circle, matching the live map. A 3D-Z layer renders
    # flat THROUGH THESE TOO (so the data is visible now) and additionally carries its elevation params
    # + render_mode="elevation3d" for the deck.gl elevation path (Front 2); the deck path will prefer
    # elevation when it exists and drop these flat layers then.
    layers: list[dict] = []
    if prof["polygon"]:
        layers.append({"suffix": "fill", "type": "fill", "geometry": "polygon",
                       "paint": _fill_paint(style, opacity)})
    if prof["polygon"] or prof["line"]:
        layers.append({"suffix": "line", "type": "line", "geometry": "line",
                       "paint": _line_paint(style, opacity)})
    if prof["point"]:
        layers.append({"suffix": "circle", "type": "circle", "geometry": "point",
                       "paint": _circle_paint(style, opacity)})
    out["maplibre_layers"] = layers

    if style.get("elevation3dEnabled") and has_z:
        out["render_mode"] = "elevation3d"
        out["elevation"] = {
            "vertical_scale": _sv(style, "elevation3dVerticalScale", 1),
            "offset": _sv(style, "elevation3dOffset", 0),
        }
    else:
        out["render_mode"] = "2d"
    return out


# ── project import ────────────────────────────────────────────────────────────

def import_project(source: str | bytes | dict) -> dict:
    """Parse a `.geolibre.json` and return a GeoDeploy import plan:
    `{portal: {...}, layers: [...], warnings: [...]}`.

    NOTE: GeoLibre's `layers[]` order vs GeoDeploy's `layer_configs[0]=top`
    convention still needs confirming against MapController.syncLayers; we
    preserve order here and flag it, so a later step can reverse if needed.
    """
    project = parse_geolibre_project(source)
    layers = [import_layer(lyr, project) for lyr in project.get("layers", [])]

    view = project.get("mapView") or {}
    portal = {
        "title": project.get("name"),
        "view": {
            "center": view.get("center"),
            "zoom": view.get("zoom"),
            "bearing": view.get("bearing", 0),
            "pitch": view.get("pitch", 0),
        },
        "basemap": {
            "style_url": project.get("basemapStyleUrl"),
            "visible": project.get("basemapVisible", True),
            "opacity": project.get("basemapOpacity", 1),
        },
        "story": _import_storymap(project.get("storymap")),
    }

    warnings = ["Layer z-order (GeoLibre layers[] vs GeoDeploy top-first) not yet confirmed."]
    for lyr in layers:
        warnings.extend(f"[{lyr['name']}] {w}" for w in lyr.get("warnings", []))
    return {"portal": portal, "layers": layers, "warnings": warnings}


# ── plan → GeoDeploy portal inputs ────────────────────────────────────────────
# After the ingestion step resolves each GeoLibre layer to a GeoDeploy layer/source id, these turn
# the import plan into the exact shapes `portal_generator.build_portal_bundle` consumes: a
# `layer_configs` list (with the raw MapLibre paint carried in `style.maplibre`) and the portal
# kwargs (title / initial_view / story / basemap). `id_map` maps geolibre_layer_id → resolved id.


def plan_to_layer_configs(plan: dict, id_map: dict) -> tuple[list[dict], list[str]]:
    """Build GeoDeploy `layer_configs` (top-first order) from the plan. Layers with no resolved id
    (ingestion skipped/failed) are dropped with a warning."""
    configs: list[dict] = []
    warnings: list[str] = []
    for lyr in plan.get("layers", []):
        gid = lyr["source_identity"]["geolibre_layer_id"]
        rid = id_map.get(gid)
        if rid is None:
            warnings.append(f"[{lyr['name']}] dropped: no resolved GeoDeploy id (ingestion skipped).")
            continue
        base = {"layer_id": rid, "opacity": lyr["opacity"], "visible": lyr.get("visible", True),
                "popup_fields": lyr.get("popup_fields", [])}
        target = lyr["target"]
        if target == "vector":
            base["layer_type"] = "vector"
            # style.maplibre = the raw-paint passthrough generate_style renders; friendly keys are a
            # fallback for the GeoDeploy editor + the point-marker metadata.
            base["style"] = {"maplibre": {"layers": lyr.get("maplibre_layers", [])},
                             **_friendly_fallback(lyr)}
        elif target == "raster":
            base["layer_type"] = "raster"
            rs = lyr.get("raster_style", {})
            base["style"] = {"colormap": rs.get("colormap"), "rescale": rs.get("rescale"),
                             "bidx": _bidx_list(rs.get("bidx")), "nodata": rs.get("nodata"),
                             "paint": rs.get("paint")}
        else:  # external tiles
            base["layer_type"] = "external"
            base["style"] = {}
        configs.append(base)
    return configs, warnings


def plan_to_portal_kwargs(plan: dict, id_map: dict) -> dict:
    """The portal-level kwargs for build_portal_bundle: title, initial_view, story (its layer refs
    remapped to `type:resolved_id`), and the GeoLibre basemap URL (mapping it to a GeoDeploy basemap
    catalog id is a TODO — for now the template default is used)."""
    portal = plan["portal"]
    target_by_gid = {l["source_identity"]["geolibre_layer_id"]: l["target"] for l in plan["layers"]}
    story = None
    if portal.get("story") and portal["story"].get("sections"):
        sections = []
        for s in portal["story"]["sections"]:
            refs = {}
            for gid, vis in (s.get("layers") or {}).items():
                rid, tgt = id_map.get(gid), target_by_gid.get(gid)
                if rid is not None and tgt:
                    refs[f"{tgt}:{rid}"] = vis
            sections.append({**s, "layers": refs})
        story = {"title": portal["story"].get("title"), "sections": sections}
    view = portal.get("view") or {}
    return {
        "title": portal.get("title"),
        "initial_view": view if view.get("center") else None,
        "story": story,
        # TODO: resolve GeoLibre basemapStyleUrl → a GeoDeploy basemap catalog id; None = template default.
        "basemap": None,
        "geolibre_basemap_url": portal.get("basemap", {}).get("style_url"),
    }


def _friendly_fallback(lyr: dict) -> dict:
    """Best-effort friendly-key style (color/radius/line_width/marker) derived from the raw paint, so
    the GeoDeploy editor and the point-marker metadata have sensible values (expressions → default)."""
    out = {"color": "#3b82f6", "radius": 6, "line_width": 2, "fill_opacity": 0.45,
           "outline_color": "#1d4ed8", "marker": "circle"}
    for ml in lyr.get("maplibre_layers", []):
        p = ml.get("paint", {})
        if ml["type"] == "fill" and isinstance(p.get("fill-color"), str):
            out["color"] = p["fill-color"]
        elif ml["type"] == "circle":
            if isinstance(p.get("circle-color"), str):
                out["color"] = p["circle-color"]
            if isinstance(p.get("circle-radius"), (int, float)):
                out["radius"] = p["circle-radius"]
        elif ml["type"] == "line" and isinstance(p.get("line-width"), (int, float)):
            out["line_width"] = p["line-width"]
    return out


def _bidx_list(bands) -> list[int] | None:
    """GeoLibre COG `bands` ('1' or '1,2,3') → GeoDeploy `bidx` list of 1-based ints (or None)."""
    if not bands:
        return None
    try:
        idx = [int(b) for b in str(bands).split(",") if str(b).strip()]
    except ValueError:
        return None
    return idx or None


def _import_storymap(storymap: dict | None) -> dict | None:
    """Map a GeoLibre storymap → GeoDeploy portal story sections (subset)."""
    if not storymap or not storymap.get("chapters"):
        return None
    sections = []
    for ch in storymap["chapters"]:
        loc = ch.get("location") or {}
        sections.append({
            "id": ch.get("id"),
            "title": ch.get("title", ""),
            "body": ch.get("description", ""),
            "image": ch.get("image"),
            "view": {
                "center": loc.get("center"),
                "zoom": loc.get("zoom"),
                "bearing": loc.get("bearing", 0),
                "pitch": loc.get("pitch", 0),
            },
            # onChapterEnter/Exit opacity ops → per-section layer visibility (subset).
            "layers": {op.get("layerId"): (op.get("opacity", 0) > 0)
                       for op in ch.get("onChapterEnter", []) if op.get("layerId")},
        })
    return {"title": storymap.get("title"), "sections": sections}
