"""Assemble MapLibre GL JS style JSON and write the portal static bundle."""
import json
import os
import shutil
from pathlib import Path
from ..config import get_settings
from .martin import get_tile_url as vector_tile_url
from .titiler import get_tile_url as raster_tile_url


def generate_style(layer_configs: list[dict], vector_layers: list, raster_layers: list) -> dict:
    """Build a MapLibre GL style document from the portal layer configuration."""
    settings = get_settings()
    sources = {}
    layers = []

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
            layers.append(_vector_layer(source_id, layer, cfg))

        elif cfg["layer_type"] == "raster":
            layer = next((l for l in raster_layers if l.id == cfg["layer_id"]), None)
            if not layer:
                continue
            source_id = f"raster_{layer.id}"
            sources[source_id] = {
                "type": "raster",
                "tiles": [raster_tile_url(layer.s3_key)],
                "tileSize": 256,
            }
            layers.append({
                "id": f"raster-{layer.id}",
                "type": "raster",
                "source": source_id,
                "paint": {
                    "raster-opacity": cfg.get("opacity", 1.0),
                },
            })

    return {
        "version": 8,
        "glyphs": "https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf",
        "sources": sources,
        "layers": layers,
    }


def _vector_layer(source_id: str, layer, cfg: dict) -> dict:
    geom = (layer.geometry_type or "").lower()
    style = cfg.get("style", {})
    opacity = cfg.get("opacity", 1.0)

    if "polygon" in geom or "multi" in geom:
        return {
            "id": f"vector-{layer.id}",
            "type": "fill",
            "source": source_id,
            "source-layer": f"{layer.schema_name}.{layer.table_name}",
            "paint": {
                "fill-color": style.get("color", "#3b82f6"),
                "fill-opacity": opacity * style.get("fill_opacity", 0.4),
                "fill-outline-color": style.get("outline_color", "#1d4ed8"),
            },
        }
    if "line" in geom:
        return {
            "id": f"vector-{layer.id}",
            "type": "line",
            "source": source_id,
            "source-layer": f"{layer.schema_name}.{layer.table_name}",
            "paint": {
                "line-color": style.get("color", "#3b82f6"),
                "line-width": style.get("line_width", 2),
                "line-opacity": opacity,
            },
        }
    return {
        "id": f"vector-{layer.id}",
        "type": "circle",
        "source": source_id,
        "source-layer": f"{layer.schema_name}.{layer.table_name}",
        "paint": {
            "circle-color": style.get("color", "#3b82f6"),
            "circle-radius": style.get("radius", 5),
            "circle-opacity": opacity,
        },
    }


def build_portal_bundle(slug: str, title: str, style: dict, template_id: str, layer_configs: list[dict]) -> str:
    """
    Write a self-contained HTML portal to data/portals/{slug}/index.html.
    Returns the public path.
    """
    settings = get_settings()
    templates_dir = Path("/templates/official") / template_id
    portals_dir = Path(settings.data_dir) / "portals" / slug
    portals_dir.mkdir(parents=True, exist_ok=True)

    theme_css = (templates_dir / "theme.css").read_text() if (templates_dir / "theme.css").exists() else ""
    layout_html = (templates_dir / "layout.html").read_text() if (templates_dir / "layout.html").exists() else _default_layout()

    popup_configs = {
        cfg["layer_id"]: cfg.get("popup_fields", [])
        for cfg in layer_configs
        if cfg.get("popup_fields")
    }

    html = layout_html.replace("{{TITLE}}", title)
    html = html.replace("{{STYLE_JSON}}", json.dumps(style))
    html = html.replace("{{THEME_CSS}}", theme_css)
    html = html.replace("{{POPUP_CONFIG}}", json.dumps(popup_configs))

    (portals_dir / "index.html").write_text(html, encoding="utf-8")
    (portals_dir / "style.json").write_text(json.dumps(style, indent=2), encoding="utf-8")

    return f"/portals/{slug}/"


def _default_layout() -> str:
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{{TITLE}}</title>
  <link rel="stylesheet" href="https://unpkg.com/maplibre-gl@4/dist/maplibre-gl.css">
  <script src="https://unpkg.com/maplibre-gl@4/dist/maplibre-gl.js"></script>
  <script src="https://unpkg.com/deck.gl@9/dist.min.js"></script>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body { font-family: system-ui, sans-serif; }
    #map { width: 100vw; height: 100vh; }
    #title { position: absolute; top: 16px; left: 16px; z-index: 10;
             background: white; padding: 8px 14px; border-radius: 6px;
             box-shadow: 0 2px 8px rgba(0,0,0,0.15); font-weight: 600; }
    {{THEME_CSS}}
  </style>
</head>
<body>
  <div id="title">{{TITLE}}</div>
  <div id="map"></div>
  <script>
    const STYLE = {{STYLE_JSON}};
    const POPUP_CONFIG = {{POPUP_CONFIG}};

    const map = new maplibregl.Map({
      container: 'map',
      style: STYLE,
      center: [0, 20],
      zoom: 2,
    });

    map.addControl(new maplibregl.NavigationControl(), 'top-right');

    map.on('click', (e) => {
      const features = map.queryRenderedFeatures(e.point);
      if (!features.length) return;
      const f = features[0];
      const layerId = parseInt(f.layer.id.split('-')[1]);
      const fields = POPUP_CONFIG[layerId] || Object.keys(f.properties).slice(0, 6);
      if (!fields.length) return;
      const content = fields
        .filter(k => f.properties[k] != null)
        .map(k => `<tr><th>${k}</th><td>${f.properties[k]}</td></tr>`)
        .join('');
      new maplibregl.Popup()
        .setLngLat(e.lngLat)
        .setHTML(`<table style="border-collapse:collapse;font-size:13px">${content}</table>`)
        .addTo(map);
    });

    map.on('mouseenter', () => map.getCanvas().style.cursor = 'pointer');
    map.on('mouseleave', () => map.getCanvas().style.cursor = '');
  </script>
</body>
</html>"""
