"""Assemble MapLibre GL JS style JSON and write the portal static bundle."""
import json
import os
from pathlib import Path
from ..config import get_settings
from .martin import get_tile_url as vector_tile_url
from .titiler import get_tile_url as raster_tile_url
from . import external_sources as ext_svc


def generate_style(layer_configs: list[dict], vector_layers: list, raster_layers: list,
                   external_sources: list | None = None) -> dict:
    """
    Return user data sources and layers only.
    The basemap is provided by the template's style.json and merged in build_portal_bundle.
    Each layer gets geodeploy:name metadata so the switcher can display it.
    """
    sources = {}
    layers = []
    deck_layers = []  # GeoParquet layers rendered by the deck.gl overlay (not MapLibre layers)
    bounds = [180, 90, -180, -90]  # expanded below

    # layer_configs[0] is the TOP of the layer list and should draw on TOP of the map.
    # MapLibre draws later layers on top, so build them in reverse (config[0] added last).
    for cfg in reversed(layer_configs):
        if cfg["layer_type"] == "vector":
            layer = next((l for l in vector_layers if l.id == cfg["layer_id"]), None)
            if not layer:
                continue
            source_id = f"vector_{layer.id}"
            if getattr(layer, "storage_backend", "postgis") == "geoparquet":
                # File-backed (GeoParquet). PRIMARY display = a deck.gl overlay fed by the public
                # viewport query (rendered outside the MapLibre style by portal.js), so collect a
                # descriptor and emit NO MapLibre layer. FALLBACK: a layer explicitly tiled (ready
                # PMTiles) renders via the pmtiles:// vector source (root-relative; portal.js
                # absolutifies it) and falls through to the normal vector-layer build below.
                if not (layer.tile_status == "ready" and layer.pmtiles_key):
                    dstyle = cfg.get("style") or {}
                    deck_layers.append({
                        "layer_id": layer.id,
                        "name": layer.name,
                        "geometry": _geom_kind(layer.geometry_type),
                        "color": dstyle.get("color", "#3b82f6"),
                        "opacity": cfg.get("opacity", 1.0),
                        "fill_opacity": dstyle.get("fill_opacity", 0.45),
                        "outline_color": dstyle.get("outline_color", "#1d4ed8"),
                        "line_width": dstyle.get("line_width", 2),
                        "radius": dstyle.get("radius", 5),
                        "visible": cfg.get("visible", True),
                        "bbox": json.loads(layer.bbox) if layer.bbox else None,
                    })
                    if layer.bbox:
                        _expand_bounds(bounds, json.loads(layer.bbox))
                    continue
                sources[source_id] = {
                    "type": "vector",
                    "url": f"pmtiles:///api/data/vector/{layer.id}/pmtiles",
                }
            else:
                sources[source_id] = {
                    "type": "vector",
                    "tiles": [vector_tile_url(layer.schema_name, layer.table_name)],
                    "minzoom": 0,
                    "maxzoom": 22,
                }
            ml_layer = _vector_layer(source_id, layer, cfg)
            ml_layer["metadata"] = {
                "geodeploy:name": layer.name,
                "geodeploy:type": "vector",
                "geodeploy:layer_id": layer.id,
                "geodeploy:opacity": cfg.get("opacity", 1.0),
                "geodeploy:bbox": json.loads(layer.bbox) if layer.bbox else None,
                "geodeploy:geometry": _geom_kind(layer.geometry_type),
                "geodeploy:marker": (cfg.get("style") or {}).get("marker", "circle"),
                "geodeploy:markerColor": (cfg.get("style") or {}).get("color", "#3b82f6"),
                "geodeploy:markerSize": (cfg.get("style") or {}).get("radius", 5),
            }
            if not cfg.get("visible", True):
                ml_layer.setdefault("layout", {})["visibility"] = "none"
            layers.append(ml_layer)

            if layer.bbox:
                _expand_bounds(bounds, json.loads(layer.bbox))

        elif cfg["layer_type"] == "raster":
            layer = next((l for l in raster_layers if l.id == cfg["layer_id"]), None)
            if not layer:
                continue
            source_id = f"raster_{layer.id}"
            rstyle = cfg.get("style", {})
            sources[source_id] = {
                "type": "raster",
                "tiles": [raster_tile_url(
                    layer.s3_key,
                    colormap=rstyle.get("colormap"),
                    rescale=rstyle.get("rescale"),
                    algorithm=rstyle.get("algorithm"),
                    zfactor=rstyle.get("zfactor"),
                    bidx=rstyle.get("bidx"),
                )],
                "tileSize": 256,
            }
            raster_layer = {
                "id": f"raster-{layer.id}",
                "type": "raster",
                "source": source_id,
                "paint": {"raster-opacity": cfg.get("opacity", 1.0)},
                "metadata": {
                    "geodeploy:name": layer.name,
                    "geodeploy:type": "raster",
                    "geodeploy:layer_id": layer.id,
                    "geodeploy:opacity": cfg.get("opacity", 1.0),
                    "geodeploy:bbox": json.loads(layer.bbox) if layer.bbox else None,
                    "geodeploy:geometry": "raster",
                    "geodeploy:bands": layer.band_count,
                },
            }
            if not cfg.get("visible", True):
                raster_layer["layout"] = {"visibility": "none"}
            layers.append(raster_layer)

            if layer.bbox:
                _expand_bounds(bounds, json.loads(layer.bbox))

        elif cfg["layer_type"] == "external":
            src = next((s for s in (external_sources or []) if s.id == cfg["layer_id"]), None)
            if not src:
                continue
            estyle = cfg.get("style", {})
            source_id = f"ext_{src.id}"
            src_bbox = json.loads(src.bbox) if src.bbox else None
            base_meta = {
                "geodeploy:name": src.name,
                "geodeploy:type": src.kind,          # raster | vector
                "geodeploy:external": True,
                "geodeploy:layer_id": src.id,
                "geodeploy:opacity": cfg.get("opacity", 1.0),
                "geodeploy:bbox": src_bbox,
                "geodeploy:attribution": src.attribution,
            }
            if src.kind == "raster":
                sources[source_id] = {"type": "raster", "tiles": [ext_svc.tile_url(src)], "tileSize": 256}
                if src.attribution:
                    sources[source_id]["attribution"] = src.attribution
                ext_layer = {
                    "id": f"external-{src.id}",
                    "type": "raster",
                    "source": source_id,
                    "paint": {"raster-opacity": cfg.get("opacity", 1.0)},
                    "metadata": {**base_meta, "geodeploy:geometry": "raster"},
                }
                if not cfg.get("visible", True):
                    ext_layer["layout"] = {"visibility": "none"}
            else:  # vector — WFS through the GeoJSON proxy
                sources[source_id] = {"type": "geojson", "data": ext_svc.features_url(src)}
                if src.attribution:
                    sources[source_id]["attribution"] = src.attribution
                geom = src.geometry_type or "polygon"
                ext_layer = _external_vector_layer(source_id, src, geom, estyle, cfg.get("opacity", 1.0))
                ext_layer["metadata"] = {**base_meta, "geodeploy:geometry": geom}
                if not cfg.get("visible", True):
                    ext_layer.setdefault("layout", {})["visibility"] = "none"
            layers.append(ext_layer)
            if src_bbox:
                _expand_bounds(bounds, src_bbox)

    valid_bounds = bounds if bounds[0] < bounds[2] else None
    return {"sources": sources, "layers": layers, "bounds": valid_bounds, "deck_layers": deck_layers}


def build_portal_bundle(slug: str, title: str, user_data: dict, template_id: str, layer_configs: list[dict],
                        access_type: str = "public", password_sha256: str | None = None,
                        initial_view: dict | None = None) -> str:
    """
    Merge basemap + user data into a complete style, inject into layout.html,
    write to data/portals/{slug}/index.html.
    """
    settings = get_settings()
    template_dir = Path("/templates/official") / template_id
    portals_dir = Path(settings.data_dir) / "portals" / slug
    portals_dir.mkdir(parents=True, exist_ok=True)

    # Shared portal runtime (CSS + JS + skeleton) — edited once, inherited by every template.
    shared_dir = Path("/templates/shared")
    portal_css = _read(shared_dir / "portal.css", "")
    portal_js = _read(shared_dir / "portal.js", "")

    # Load template files. A template only needs theme.css + style.json + template.json;
    # layout.html is optional and falls back to the shared skeleton.
    basemap_style = _load_basemap(template_dir)
    theme_css = _read(template_dir / "theme.css", "")
    layout_html = (_read(template_dir / "layout.html")
                   or _read(shared_dir / "layout.html")
                   or _default_layout())

    # Merge basemap + user layers into a single complete MapLibre style
    full_style = {
        "version": 8,
        "glyphs": "https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf",
        "sprite": basemap_style.get("sprite", ""),
        "sources": {**basemap_style.get("sources", {}), **user_data["sources"]},
        "layers": basemap_style.get("layers", []) + user_data["layers"],
        # Custom key — MapLibre ignores unknown top-level keys
        "geodeploy": {
            "bounds": user_data.get("bounds"),
            "view": initial_view,  # admin-set center/zoom; portal.js prefers this over fitBounds
            "title": title,
            "deckLayers": user_data.get("deck_layers", []),  # GeoParquet layers → deck.gl overlay
        },
    }

    popup_configs = {
        str(cfg["layer_id"]): cfg.get("popup_fields", [])
        for cfg in layer_configs
        if cfg.get("popup_fields")
    }

    # Inject the shared runtime first (it contains no placeholders), then the data.
    html = layout_html.replace("{{PORTAL_CSS}}", portal_css)
    html = html.replace("{{PORTAL_JS}}", portal_js)
    html = html.replace("{{STYLE_JSON}}", json.dumps(full_style))
    html = html.replace("{{THEME_CSS}}", theme_css)
    html = html.replace("{{POPUP_CONFIG}}", json.dumps(popup_configs))
    html = html.replace("{{ACCESS_TYPE}}", access_type)
    html = html.replace("{{PASSWORD_SHA256}}", password_sha256 or "")
    html = html.replace("{{SLUG}}", slug)
    html = html.replace("{{TITLE}}", title)

    (portals_dir / "index.html").write_text(html, encoding="utf-8")
    (portals_dir / "style.json").write_text(json.dumps(full_style, indent=2), encoding="utf-8")

    return f"/portals/{slug}/"


# ── helpers ──────────────────────────────────────────────────────────────────

def _external_vector_layer(source_id: str, src, geom: str, style: dict, opacity: float) -> dict:
    """A MapLibre layer for a WFS GeoJSON source (no source-layer; geom from the probe)."""
    color = style.get("color", "#3b82f6")
    lid = f"external-{src.id}"
    if geom == "polygon":
        return {
            "id": lid, "type": "fill", "source": source_id,
            "paint": {
                "fill-color": color,
                "fill-opacity": opacity * style.get("fill_opacity", 0.45),
                "fill-outline-color": style.get("outline_color", "#1d4ed8"),
            },
        }
    if geom == "line":
        return {
            "id": lid, "type": "line", "source": source_id,
            "paint": {"line-color": color, "line-width": style.get("line_width", 2), "line-opacity": opacity},
        }
    return {
        "id": lid, "type": "circle", "source": source_id,
        "paint": {
            "circle-color": color,
            "circle-radius": style.get("radius", 5),
            "circle-opacity": opacity,
            "circle-stroke-color": "#ffffff",
            "circle-stroke-width": 1,
        },
    }


def _vector_layer(source_id: str, layer, cfg: dict) -> dict:
    geom = (layer.geometry_type or "").lower()
    style = cfg.get("style", {})
    opacity = cfg.get("opacity", 1.0)
    # PostGIS layers tile by Martin under schema.table; GeoParquet PMTiles use the tippecanoe
    # layer name "geodeploy" (see tasks/pmtiles_tile.PMTILES_LAYER).
    source_layer = ("geodeploy" if getattr(layer, "storage_backend", "postgis") == "geoparquet"
                    else f"{layer.schema_name}.{layer.table_name}")

    if "polygon" in geom:
        return {
            "id": f"vector-{layer.id}",
            "type": "fill",
            "source": source_id,
            "source-layer": source_layer,
            "paint": {
                "fill-color": style.get("color", "#3b82f6"),
                "fill-opacity": opacity * style.get("fill_opacity", 0.45),
                "fill-outline-color": style.get("outline_color", "#1d4ed8"),
            },
        }
    if "line" in geom:
        paint = {
            "line-color": style.get("color", "#3b82f6"),
            "line-width": style.get("line_width", 2),
            "line-opacity": opacity,
        }
        line_type = style.get("lineType")
        if line_type == "dashed":
            paint["line-dasharray"] = [2, 1.5]
        elif line_type == "dotted":
            paint["line-dasharray"] = [0.4, 1.8]
        return {
            "id": f"vector-{layer.id}",
            "type": "line",
            "source": source_id,
            "source-layer": source_layer,
            "paint": paint,
        }
    # point / unknown — rendered as a symbol layer with a runtime-generated icon
    # (portal.js / the editor build the image from the marker metadata). This lets
    # points use shapes (circle/square/triangle/diamond/star/cross) on raster basemaps.
    return {
        "id": f"vector-{layer.id}",
        "type": "symbol",
        "source": source_id,
        "source-layer": source_layer,
        "layout": {
            "icon-image": f"gd-pt-{layer.id}",
            "icon-allow-overlap": True,
            "icon-ignore-placement": True,
        },
        "paint": {
            "icon-opacity": opacity,
        },
    }


def _geom_kind(geometry_type: str | None) -> str:
    """Normalize a PostGIS/Fiona geometry type to point|line|polygon."""
    g = (geometry_type or "").lower()
    if "polygon" in g:
        return "polygon"
    if "line" in g:
        return "line"
    if "point" in g:
        return "point"
    return "point"


def _expand_bounds(bounds: list, bbox: list) -> None:
    if len(bbox) < 4:
        return
    bounds[0] = min(bounds[0], bbox[0])
    bounds[1] = min(bounds[1], bbox[1])
    bounds[2] = max(bounds[2], bbox[2])
    bounds[3] = max(bounds[3], bbox[3])


def _load_basemap(template_dir: Path) -> dict:
    path = template_dir / "style.json"
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return _default_basemap()


def _read(path: Path, default: str | None = None) -> str | None:
    if path.exists():
        return path.read_text(encoding="utf-8")
    return default


def _default_basemap() -> dict:
    return {
        "sources": {
            "basemap": {
                "type": "raster",
                "tiles": [
                    "https://a.basemaps.cartocdn.com/light_all/{z}/{x}/{y}@2x.png",
                    "https://b.basemaps.cartocdn.com/light_all/{z}/{x}/{y}@2x.png",
                ],
                "tileSize": 256,
                "attribution": "© <a href='https://www.openstreetmap.org/copyright'>OpenStreetMap</a> contributors © <a href='https://carto.com/attributions'>CARTO</a>",
            }
        },
        "layers": [{"id": "basemap", "type": "raster", "source": "basemap"}],
    }


def _default_layout() -> str:
    """Minimal fallback — the real layout lives in templates/official/minimal/layout.html."""
    return """<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{TITLE}}</title>
<link rel="stylesheet" href="https://unpkg.com/maplibre-gl@4/dist/maplibre-gl.css">
<script src="https://unpkg.com/maplibre-gl@4/dist/maplibre-gl.js"></script>
<style>* {margin:0;padding:0;box-sizing:border-box} body{font-family:system-ui,sans-serif}
#map{width:100vw;height:100vh} {{THEME_CSS}}</style>
</head><body>
<div id="map"></div>
<script>
const STYLE={{STYLE_JSON}};const POPUP_CONFIG={{POPUP_CONFIG}};
const map=new maplibregl.Map({container:'map',style:STYLE,center:[0,20],zoom:2});
map.addControl(new maplibregl.NavigationControl(),'top-right');
if(STYLE.geodeploy?.bounds){const b=STYLE.geodeploy.bounds;map.fitBounds([[b[0],b[1]],[b[2],b[3]]],{padding:40});}
</script></body></html>"""
