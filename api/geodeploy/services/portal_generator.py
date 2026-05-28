"""Assemble MapLibre GL JS style JSON and write the portal static bundle."""
import json
import os
from pathlib import Path
from ..config import get_settings
from .martin import get_tile_url as vector_tile_url
from .titiler import get_tile_url as raster_tile_url


def generate_style(layer_configs: list[dict], vector_layers: list, raster_layers: list) -> dict:
    """
    Return user data sources and layers only.
    The basemap is provided by the template's style.json and merged in build_portal_bundle.
    Each layer gets geodeploy:name metadata so the switcher can display it.
    """
    sources = {}
    layers = []
    bounds = [180, 90, -180, -90]  # expanded below

    for cfg in layer_configs:
        if cfg["layer_type"] == "vector":
            layer = next((l for l in vector_layers if l.id == cfg["layer_id"]), None)
            if not layer:
                continue
            source_id = f"vector_{layer.id}"
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
            }
            layers.append(ml_layer)

            if layer.bbox:
                _expand_bounds(bounds, json.loads(layer.bbox))

        elif cfg["layer_type"] == "raster":
            layer = next((l for l in raster_layers if l.id == cfg["layer_id"]), None)
            if not layer:
                continue
            source_id = f"raster_{layer.id}"
            colormap = cfg.get("style", {}).get("colormap")
            sources[source_id] = {
                "type": "raster",
                "tiles": [raster_tile_url(layer.s3_key, colormap=colormap)],
                "tileSize": 256,
            }
            layers.append({
                "id": f"raster-{layer.id}",
                "type": "raster",
                "source": source_id,
                "paint": {"raster-opacity": cfg.get("opacity", 1.0)},
                "metadata": {
                    "geodeploy:name": layer.name,
                    "geodeploy:type": "raster",
                    "geodeploy:layer_id": layer.id,
                    "geodeploy:opacity": cfg.get("opacity", 1.0),
                },
            })

            if layer.bbox:
                _expand_bounds(bounds, json.loads(layer.bbox))

    valid_bounds = bounds if bounds[0] < bounds[2] else None
    return {"sources": sources, "layers": layers, "bounds": valid_bounds}


def build_portal_bundle(slug: str, title: str, user_data: dict, template_id: str, layer_configs: list[dict],
                        access_type: str = "public", password_sha256: str | None = None) -> str:
    """
    Merge basemap + user data into a complete style, inject into layout.html,
    write to data/portals/{slug}/index.html.
    """
    settings = get_settings()
    template_dir = Path("/templates/official") / template_id
    portals_dir = Path(settings.data_dir) / "portals" / slug
    portals_dir.mkdir(parents=True, exist_ok=True)

    # Load template files
    basemap_style = _load_basemap(template_dir)
    theme_css = _read(template_dir / "theme.css", "")
    layout_html = _read(template_dir / "layout.html") or _default_layout()

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
            "title": title,
        },
    }

    popup_configs = {
        str(cfg["layer_id"]): cfg.get("popup_fields", [])
        for cfg in layer_configs
        if cfg.get("popup_fields")
    }

    html = layout_html.replace("{{TITLE}}", title)
    html = html.replace("{{STYLE_JSON}}", json.dumps(full_style))
    html = html.replace("{{THEME_CSS}}", theme_css)
    html = html.replace("{{POPUP_CONFIG}}", json.dumps(popup_configs))
    html = html.replace("{{ACCESS_TYPE}}", access_type)
    html = html.replace("{{PASSWORD_SHA256}}", password_sha256 or "")

    (portals_dir / "index.html").write_text(html, encoding="utf-8")
    (portals_dir / "style.json").write_text(json.dumps(full_style, indent=2), encoding="utf-8")

    return f"/portals/{slug}/"


# ── helpers ──────────────────────────────────────────────────────────────────

def _vector_layer(source_id: str, layer, cfg: dict) -> dict:
    geom = (layer.geometry_type or "").lower()
    style = cfg.get("style", {})
    opacity = cfg.get("opacity", 1.0)
    source_layer = f"{layer.schema_name}.{layer.table_name}"

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
        return {
            "id": f"vector-{layer.id}",
            "type": "line",
            "source": source_id,
            "source-layer": source_layer,
            "paint": {
                "line-color": style.get("color", "#3b82f6"),
                "line-width": style.get("line_width", 2),
                "line-opacity": opacity,
            },
        }
    # point / unknown
    return {
        "id": f"vector-{layer.id}",
        "type": "circle",
        "source": source_id,
        "source-layer": source_layer,
        "paint": {
            "circle-color": style.get("color", "#3b82f6"),
            "circle-radius": style.get("radius", 5),
            "circle-opacity": opacity,
            "circle-stroke-width": 1,
            "circle-stroke-color": "#fff",
        },
    }


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
