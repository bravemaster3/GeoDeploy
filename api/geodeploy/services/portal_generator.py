"""Assemble MapLibre GL JS style JSON and write the portal static bundle."""
import json
import os
import shutil
from pathlib import Path
from ..config import get_settings
from .martin import get_tile_url as vector_tile_url
from .titiler import get_tile_url as raster_tile_url
from . import external_sources as ext_svc


# ── Basemap catalog — THE single source of truth ─────────────────────────────────────────────────
# All no-API-key raster basemaps. The first entry is the default when a portal has none set. This
# list is the ONLY place to add/edit a basemap: it is served to the editor via GET /api/basemaps and
# baked into every published portal as `geodeploy.basemaps` (so templates/shared/portal.js and
# ui/src/views/PortalEditor.vue both consume it at runtime — neither hard-codes the catalog).
BASEMAP_CATALOG = [
    {"id": "positron", "name": "Positron",
     "tiles": ["https://a.basemaps.cartocdn.com/light_all/{z}/{x}/{y}@2x.png",
               "https://b.basemaps.cartocdn.com/light_all/{z}/{x}/{y}@2x.png",
               "https://c.basemaps.cartocdn.com/light_all/{z}/{x}/{y}@2x.png"],
     "attribution": "© OpenStreetMap © CARTO",
     "thumb": "https://a.basemaps.cartocdn.com/light_all/4/8/5.png"},
    {"id": "voyager", "name": "Voyager",
     "tiles": ["https://a.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}@2x.png",
               "https://b.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}@2x.png",
               "https://c.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}@2x.png"],
     "attribution": "© OpenStreetMap © CARTO",
     "thumb": "https://a.basemaps.cartocdn.com/rastertiles/voyager/4/8/5.png"},
    {"id": "dark", "name": "Dark Matter",
     "tiles": ["https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png",
               "https://b.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png"],
     "attribution": "© OpenStreetMap © CARTO",
     "thumb": "https://a.basemaps.cartocdn.com/dark_all/4/8/5.png"},
    {"id": "osm", "name": "OpenStreetMap",
     "tiles": ["https://a.tile.openstreetmap.org/{z}/{x}/{y}.png",
               "https://b.tile.openstreetmap.org/{z}/{x}/{y}.png"],
     "attribution": "© OpenStreetMap contributors",
     "thumb": "https://a.tile.openstreetmap.org/4/8/5.png"},
    {"id": "topo", "name": "OpenTopoMap",
     "tiles": ["https://a.tile.opentopomap.org/{z}/{x}/{y}.png",
               "https://b.tile.opentopomap.org/{z}/{x}/{y}.png"],
     "attribution": "© OpenStreetMap, SRTM | © OpenTopoMap (CC-BY-SA)",
     "thumb": "https://a.tile.opentopomap.org/4/8/5.png"},
    {"id": "satellite", "name": "Satellite",
     "tiles": ["https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"],
     "attribution": "Imagery © Esri, Maxar, Earthstar Geographics",
     "thumb": "https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/4/5/8"},
    {"id": "esri-topo", "name": "Esri Topographic",
     "tiles": ["https://services.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}"],
     "attribution": "© Esri",
     "thumb": "https://services.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/4/5/8"},
]
_BASEMAP_BY_ID = {b["id"]: b for b in BASEMAP_CATALOG}


def generate_style(layer_configs: list[dict], vector_layers: list, raster_layers: list,
                   external_sources: list | None = None,
                   deck_core_bounds: dict[int, list] | None = None) -> dict:
    """
    Return user data sources and layers only.
    The basemap is provided by the template's style.json and merged in build_portal_bundle.
    Each layer gets geodeploy:name metadata so the switcher can display it.

    `deck_core_bounds` maps a GeoParquet layer id → its manifest grid extent (the percentile CORE
    of the data). For a deck-only portal (no MapLibre layers) with no admin-pinned view, portal.js
    otherwise fits the FULL extent then snaps once to this core extent when the manifest loads — a
    visible flash. Baking the core extent into `bounds` here makes the FIRST fit already correct, and
    the returned `core_fitted` flag tells portal.js to skip its now-redundant refit.
    """
    sources = {}
    layers = []
    deck_layers = []  # GeoParquet layers rendered by the deck.gl overlay (not MapLibre layers)
    layers_info = []  # per-layer documentation for the portal About panel (name, abstract, links)
    bounds = [180, 90, -180, -90]  # expanded below
    core_bounds = [180, 90, -180, -90]  # deck layers' merged CORE extent (see deck_core_bounds)
    deck_core_seen = False              # ≥1 deck layer contributed a real manifest core extent

    # layer_configs[0] is the TOP of the layer list and should draw on TOP of the map.
    # MapLibre draws later layers on top, so build them in reverse (config[0] added last).
    for cfg in reversed(layer_configs):
        if cfg["layer_type"] == "vector":
            layer = next((l for l in vector_layers if l.id == cfg["layer_id"]), None)
            if not layer:
                continue
            layers_info.append(_layer_info(layer, "vector"))
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
                        # Client-side duckdb-wasm read path (root-relative; portal.js
                        # absolutifies). Only prepped (partitioned-prefix) layers carry a
                        # manifest; portal.js falls back to the features.geojson endpoint
                        # when this is null or the manifest fetch fails.
                        "parquet": ({
                            "manifest": f"/api/data/vector/{layer.id}/parquet/manifest.json",
                            "base": f"/api/data/vector/{layer.id}/parquet/",
                        } if (layer.s3_key
                              and not layer.s3_key.rstrip("/").endswith(".parquet")) else None),
                    })
                    lb = json.loads(layer.bbox) if layer.bbox else None
                    if lb:
                        _expand_bounds(bounds, lb)
                    core_bbox = (deck_core_bounds or {}).get(layer.id)
                    if core_bbox:
                        _expand_bounds(core_bounds, core_bbox)
                        deck_core_seen = True
                    elif lb:  # no manifest core for this layer → keep its full extent in the core set
                        _expand_bounds(core_bounds, lb)
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
            layers_info.append(_layer_info(layer, "raster"))
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
            layers_info.append({"name": src.name, "kind": "external",
                                "attribution": src.attribution, "url": src.url})
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
    # Deck-only portal (every user layer is a deck.gl GeoParquet overlay, no MapLibre layers): open on
    # the merged CORE extent instead of the full extent so portal.js needn't snap to it after load.
    # Mirrors portal.js's refit gate (`!userMapLayers.length`); coreFitted then suppresses that refit.
    core_fitted = False
    if deck_layers and not layers and deck_core_seen and core_bounds[0] < core_bounds[2]:
        valid_bounds = core_bounds
        core_fitted = True
    layers_info.reverse()  # the loop runs over reversed configs; the About panel shows list order
    return {"sources": sources, "layers": layers, "bounds": valid_bounds, "core_fitted": core_fitted,
            "deck_layers": deck_layers, "layers_info": layers_info}


def _layer_info(layer, kind: str) -> dict:
    """Documentation entry for the portal About panel: the layer's catalog metadata plus, when
    the admin shared the layer (`is_public`), its public data-access links (root-relative;
    portal.js absolutifies)."""
    info = {
        "name": layer.name,
        "kind": kind,
        "abstract": getattr(layer, "abstract", None),
        "license": getattr(layer, "license", None),
        "attribution": getattr(layer, "attribution", None),
        "keywords": getattr(layer, "keywords", None),
        "is_public": bool(getattr(layer, "is_public", False)),
    }
    if info["is_public"]:
        if kind == "raster":
            info["links"] = {
                "STAC item": f"/api/stac/collections/rasters/items/raster-{layer.id}",
                "Cloud-Optimized GeoTIFF": f"/api/data/raster/{layer.id}/cog",
            }
        else:
            links = {"STAC item": f"/api/stac/collections/vectors/items/vector-{layer.id}"}
            if getattr(layer, "storage_backend", "postgis") == "geoparquet" and layer.s3_key:
                if not layer.s3_key.rstrip("/").endswith(".parquet"):
                    links["GeoParquet manifest"] = f"/api/data/vector/{layer.id}/parquet/manifest.json"
                links["Features (GeoJSON)"] = f"/api/data/vector/{layer.id}/features.geojson"
            info["links"] = links
    return info


def build_portal_bundle(slug: str, title: str, user_data: dict, template_id: str, layer_configs: list[dict],
                        access_type: str = "public", password_sha256: str | None = None,
                        owner_id: int | None = None,
                        initial_view: dict | None = None, description: str | None = None,
                        basemap: str | None = None) -> str:
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
            # bounds already == the deck CORE extent → portal.js skips its post-load refit (no flash).
            "coreFitted": user_data.get("core_fitted", False),
            "view": initial_view,  # admin-set center/zoom; portal.js prefers this over fitBounds
            "title": title,
            "deckLayers": user_data.get("deck_layers", []),  # GeoParquet layers → deck.gl overlay
            # The full basemap catalog, baked in so portal.js builds the switcher from the SAME source
            # as the editor (GET /api/basemaps) — one place to add a basemap.
            "basemaps": BASEMAP_CATALOG,
            # True when about.html was published → portal.js shows the About links
            "aboutPage": False,  # set below once the page is written
        },
    }

    # Repoint the template's base raster source at the admin-chosen basemap (so the published portal
    # OPENS on it, no flash) and record the id so portal.js marks the matching switcher option active.
    bm = _BASEMAP_BY_ID.get(basemap)
    if bm:
        base_src_id = next((lyr.get("source") for lyr in basemap_style.get("layers", [])
                            if lyr.get("type") == "raster"), None)
        if base_src_id is None and "basemap" in full_style["sources"]:
            base_src_id = "basemap"
        if base_src_id and base_src_id in full_style["sources"]:
            full_style["sources"][base_src_id]["tiles"] = bm["tiles"]
            full_style["sources"][base_src_id]["attribution"] = bm["attribution"]
            # The builtin base layer NOW shows the chosen basemap, so portal.js must NOT swap it for the
            # catalog copy on load (that redundant swap is a visible flash). See setupBasemaps.
            full_style["geodeploy"]["baseRepointed"] = True
        full_style["geodeploy"]["defaultBasemap"] = bm["id"]

    # Standalone documentation page (GeoNode-style "full page that links to the map") — written
    # BEFORE the style is baked so the aboutPage flag lands in the HTML.
    about_html = _about_page(slug, title, description, user_data.get("layers_info", []))
    if about_html:
        (portals_dir / "about.html").write_text(about_html, encoding="utf-8")
        full_style["geodeploy"]["aboutPage"] = True

    popup_configs = {
        str(cfg["layer_id"]): cfg.get("popup_fields", [])
        for cfg in layer_configs
        if cfg.get("popup_fields")
    }

    # Inject the shared runtime first (it contains no placeholders), then the data.
    html = layout_html.replace("{{PORTAL_CSS}}", portal_css)
    html = html.replace("{{PORTAL_JS}}", portal_js)
    # STYLE_JSON / POPUP_CONFIG are embedded INSIDE a <script> block, so a user-controlled string
    # (e.g. a layer name containing "</script>") could otherwise break out of the script and inject
    # markup. `_json_for_html` neutralizes the HTML-significant characters as valid JS-string
    # escapes, so the JSON stays valid but can never terminate the tag.
    html = html.replace("{{STYLE_JSON}}", _json_for_html(full_style))
    html = html.replace("{{THEME_CSS}}", theme_css)
    html = html.replace("{{POPUP_CONFIG}}", _json_for_html(popup_configs))
    html = html.replace("{{ACCESS_TYPE}}", access_type)
    html = html.replace("{{PASSWORD_SHA256}}", password_sha256 or "")
    # Owner id for the 'owner' access tier's client gate (JSON literal — 0 is falsy but never a real id).
    html = html.replace("{{OWNER_ID}}", str(owner_id or 0))
    html = html.replace("{{SLUG}}", slug)
    # TITLE lands in both HTML text (<title>, header) and a JS string; escaping it for HTML also
    # makes the JS-string context safe (no raw " or < survives to break out).
    html = html.replace("{{TITLE}}", _esc(title))

    (portals_dir / "index.html").write_text(html, encoding="utf-8")
    (portals_dir / "style.json").write_text(json.dumps(full_style, indent=2), encoding="utf-8")

    # Vendored browser modules (deck.gl + GeoArrow + Arrow as ONE self-contained ESM bundle,
    # templates/shared/vendor/) are copied next to index.html so the portal imports them
    # same-origin — no CDN dependency (offline portals) and no cross-CDN ESM interop failures
    # (the jsDelivr module set failed to load in practice; see notes §0h-addendum-2).
    vendor_dir = shared_dir / "vendor"
    if vendor_dir.is_dir():
        for f in vendor_dir.iterdir():
            if f.is_file():
                shutil.copy2(f, portals_dir / f.name)

    return f"/portals/{slug}/"


# ── About page (portals-as-documentation) ────────────────────────────────────

def _esc(s) -> str:
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _json_for_html(obj) -> str:
    """`json.dumps` for embedding inside an inline <script> element. Escapes the characters that
    could terminate the script tag or confuse the HTML parser as JS unicode escapes (still valid
    JSON/JS, so parsing is unaffected): `<`, `>`, `&`, and the U+2028/U+2029 line separators."""
    return (json.dumps(obj)
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
            .replace(" ", "\\u2028").replace(" ", "\\u2029"))


def _md_inline(s: str) -> str:
    """Inline markdown on an ALREADY-ESCAPED string: images, links, bold, italic, code."""
    import re
    s = re.sub(r"!\[([^\]]*)\]\((https?://[^)\s]+|/[^)\s]*)\)",
               r'<img src="\2" alt="\1" loading="lazy">', s)
    s = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+|/[^)\s]*)\)",
               r'<a href="\2" target="_blank" rel="noopener">\1</a>', s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(^|\W)\*([^*]+)\*(?=\W|$)", r"\1<em>\2</em>", s)
    s = re.sub(r"(^|\W)_([^_]+)_(?=\W|$)", r"\1<em>\2</em>", s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    return s


def _md_to_html(md: str) -> str:
    """Minimal SAFE markdown → HTML for the About page, LINE-based so headings/lists work even in
    single-newline text. Everything is escaped first — no raw HTML passes through. Covers what the
    editor's TipTap→markdown serializer emits (headings, bold/italic, links, images, bullet/numbered
    lists, quotes, code, rules). Keep the vocabulary in sync with the editor's toolbar."""
    import re
    esc = _esc(md)
    out, para, ul, ol, quote = [], [], [], [], []

    def flush_para():
        if para:
            out.append("<p>" + "<br>".join(_md_inline(l) for l in para) + "</p>")
            para.clear()

    def flush_lists():
        if ul:
            out.append("<ul>" + "".join("<li>" + _md_inline(i) + "</li>" for i in ul) + "</ul>")
            ul.clear()
        if ol:
            out.append("<ol>" + "".join("<li>" + _md_inline(i) + "</li>" for i in ol) + "</ol>")
            ol.clear()
        if quote:
            out.append("<blockquote>" + "<br>".join(_md_inline(l) for l in quote) + "</blockquote>")
            quote.clear()

    for raw in esc.split("\n"):
        line = raw.rstrip()
        stripped = line.strip()
        h = re.match(r"^(#{1,6}) (.+)$", stripped)
        if not stripped:
            flush_para(); flush_lists()
        elif h:
            flush_para(); flush_lists()
            level = min(len(h.group(1)) + 1, 4)  # page h1 is the portal title → h2..h4
            out.append(f"<h{level}>" + _md_inline(h.group(2)) + f"</h{level}>")
        elif re.match(r"^(-{3,}|\*{3,})$", stripped):
            flush_para(); flush_lists()
            out.append("<hr>")
        elif re.match(r"^[-*] ", stripped):
            flush_para(); ol and flush_lists(); quote and flush_lists()
            ul.append(stripped[2:])
        elif re.match(r"^\d+[.)] ", stripped):
            flush_para(); ul and flush_lists(); quote and flush_lists()
            ol.append(re.sub(r"^\d+[.)] ", "", stripped))
        elif stripped.startswith("&gt;"):
            flush_para(); ul and flush_lists(); ol and flush_lists()
            quote.append(re.sub(r"^&gt; ?", "", stripped))
        else:
            flush_lists()
            para.append(stripped)
    flush_para(); flush_lists()
    return "".join(out)


def _about_page(slug: str, title: str, description: str | None, layers_info: list[dict]) -> str | None:
    """The standalone documentation page (`about.html`) published next to the map — GeoNode-style
    'full page that links to the map', styled after GeoLibre's dark design tokens. Static HTML,
    rendered server-side at publish (no JS needed)."""
    has_layer_docs = any(i.get("abstract") or i.get("license") or i.get("attribution") or i.get("links")
                         for i in layers_info)
    if not description and not has_layer_docs:
        return None

    cards = []
    for i in layers_info:
        parts = ['<div class="layer"><div class="layer-name">' + _esc(i.get("name"))
                 + ('<span class="badge">public data</span>' if i.get("is_public") else "") + "</div>"]
        if i.get("abstract"):
            parts.append('<p class="abstract">' + _esc(i["abstract"]) + "</p>")
        meta = []
        if i.get("license"):
            meta.append("License: " + _esc(i["license"]))
        if i.get("attribution"):
            meta.append(_esc(i["attribution"]))
        if meta:
            parts.append('<p class="meta">' + " · ".join(meta) + "</p>")
        if i.get("links"):
            links = "".join(f'<a class="pill" href="{_esc(url)}" target="_blank" rel="noopener">'
                            f"{_esc(label)} ↗</a>" for label, url in i["links"].items())
            parts.append(f'<div class="links">{links}</div>')
        parts.append("</div>")
        cards.append("".join(parts))

    desc_html = _md_to_html(description) if description else ""
    # Design tokens borrowed from GeoLibre's dark theme (shadcn scale) — an intentional,
    # self-contained look independent of the map template. Light/dark via html[data-theme],
    # sharing the SAME localStorage key ('gd-portal-theme') + OS-preference default as the
    # portal's toggle (portal.js), so the choice carries between the map and this page. The
    # head script applies the theme BEFORE first paint (no flash).
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_esc(title)} — About</title>
<script>
  (function () {{
    try {{
      var saved = localStorage.getItem('gd-portal-theme');
      var dark = saved ? saved === 'dark'
        : (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches);
      if (dark) document.documentElement.setAttribute('data-theme', 'dark');
    }} catch (e) {{}}
  }})();
</script>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  :root {{
    --bg: hsl(210 40% 99%); --panel: hsl(210 40% 96%); --card: hsl(0 0% 100%);
    --border: hsl(214 32% 88%); --fg: hsl(222 47% 11%); --muted: hsl(215 16% 44%);
    --primary: hsl(217 91% 55%); --radius: 10px;
    --doc-fg: hsl(222 40% 20%); --abstract-fg: hsl(222 35% 26%);
    --layer-hover: hsl(214 32% 75%);
    --badge-fg: hsl(142 71% 30%); --badge-bg: hsl(142 71% 94%); --badge-border: hsl(142 60% 80%);
  }}
  html[data-theme="dark"] {{
    --bg: hsl(222 47% 7%); --panel: hsl(222 44% 9%); --card: hsl(220 40% 12%);
    --border: hsl(217 33% 17%); --fg: hsl(210 40% 98%); --muted: hsl(215 20% 65%);
    --primary: hsl(217 91% 60%);
    --doc-fg: hsl(210 30% 88%); --abstract-fg: hsl(210 30% 85%);
    --layer-hover: hsl(217 33% 28%);
    --badge-fg: hsl(142 71% 55%); --badge-bg: hsl(142 71% 12%); --badge-border: hsl(142 71% 20%);
  }}
  body {{
    background: var(--bg); color: var(--fg); line-height: 1.7; font-size: 16px;
    font-family: 'Inter', ui-sans-serif, system-ui, -apple-system, 'Segoe UI', sans-serif;
  }}
  .wrap {{ max-width: 880px; margin: 0 auto; padding: 0 28px 80px; }}
  .top {{
    display: flex; align-items: center; justify-content: space-between; gap: 16px;
    padding: 22px 0; border-bottom: 1px solid var(--border); margin-bottom: 48px;
  }}
  .brand {{ font-size: 13px; color: var(--muted); letter-spacing: .4px; }}
  .open-map {{
    display: inline-flex; align-items: center; gap: 8px; padding: 9px 20px;
    background: var(--primary); color: #fff; text-decoration: none; font-weight: 600;
    font-size: 14px; border-radius: 999px; transition: filter .15s;
  }}
  .open-map:hover {{ filter: brightness(1.12); }}
  .top-actions {{ display: inline-flex; align-items: center; gap: 10px; }}
  .theme-toggle {{
    display: inline-flex; align-items: center; justify-content: center; width: 36px; height: 36px;
    background: var(--card); color: var(--muted); border: 1px solid var(--border);
    border-radius: 999px; cursor: pointer; transition: border-color .15s, color .15s;
  }}
  .theme-toggle:hover {{ border-color: var(--primary); color: var(--fg); }}
  .theme-toggle svg {{ width: 17px; height: 17px; }}
  .kicker {{
    font-size: 11px; font-weight: 700; letter-spacing: 2.2px; text-transform: uppercase;
    color: var(--primary); margin-bottom: 10px;
  }}
  h1 {{ font-size: 40px; font-weight: 750; letter-spacing: -.02em; margin-bottom: 26px; }}
  .doc {{ color: var(--doc-fg); }}
  .doc h2 {{ font-size: 23px; font-weight: 650; margin: 34px 0 10px; color: var(--fg); }}
  .doc h3, .doc h4 {{ font-size: 18px; font-weight: 600; margin: 24px 0 8px; color: var(--fg); }}
  .doc p {{ margin: 10px 0; text-align: justify; hyphens: auto; }}
  .doc ul, .doc ol {{ margin: 10px 0 10px 26px; }}
  .doc li {{ margin: 4px 0; }}
  .doc a {{ color: var(--primary); text-decoration: none; border-bottom: 1px solid transparent; }}
  .doc a:hover {{ border-bottom-color: var(--primary); }}
  .doc img {{ max-width: 100%; border-radius: var(--radius); border: 1px solid var(--border); margin: 14px 0; }}
  .doc blockquote {{
    border-left: 3px solid var(--primary); background: var(--panel);
    padding: 10px 18px; margin: 14px 0; border-radius: 0 var(--radius) var(--radius) 0;
    color: var(--muted);
  }}
  .doc hr {{ border: none; border-top: 1px solid var(--border); margin: 28px 0; }}
  .doc code {{
    font-size: 13.5px; background: var(--card); border: 1px solid var(--border);
    border-radius: 6px; padding: 1.5px 6px;
  }}
  .section-title {{
    font-size: 13px; font-weight: 700; letter-spacing: 1.8px; text-transform: uppercase;
    color: var(--muted); margin: 56px 0 18px; padding-top: 26px; border-top: 1px solid var(--border);
  }}
  .grid {{ display: grid; grid-template-columns: 1fr; gap: 14px; }}
  @media (min-width: 700px) {{ .grid {{ grid-template-columns: 1fr 1fr; }} }}
  .layer {{
    background: var(--panel); border: 1px solid var(--border); border-radius: var(--radius);
    padding: 18px 20px; transition: border-color .15s;
  }}
  .layer:hover {{ border-color: var(--layer-hover); }}
  .layer-name {{ font-weight: 650; font-size: 15px; }}
  .badge {{
    font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: .6px;
    color: var(--badge-fg); background: var(--badge-bg); border: 1px solid var(--badge-border);
    border-radius: 999px; padding: 2.5px 9px; margin-left: 8px; vertical-align: 2px;
  }}
  .abstract {{ font-size: 13.5px; color: var(--abstract-fg); margin-top: 8px; }}
  .meta {{ font-size: 12px; color: var(--muted); margin-top: 8px; }}
  .links {{ display: flex; flex-wrap: wrap; gap: 7px; margin-top: 12px; }}
  .pill {{
    font-size: 12px; font-weight: 600; text-decoration: none; padding: 5px 13px;
    border-radius: 999px; color: var(--primary); background: var(--card);
    border: 1px solid var(--border); transition: border-color .15s;
  }}
  .pill:hover {{ border-color: var(--primary); }}
  .foot {{ font-size: 12.5px; color: var(--muted); margin-top: 40px; }}
  .foot a {{ color: var(--primary); text-decoration: none; }}
</style>
</head>
<body>
<div class="wrap">
  <div class="top">
    <span class="brand">GeoDeploy portal</span>
    <span class="top-actions">
      <button class="theme-toggle" id="theme-toggle" type="button" aria-label="Toggle theme"></button>
      <a class="open-map" href="./">Open the map →</a>
    </span>
  </div>
  <div class="kicker">Documentation</div>
  <h1>{_esc(title)}</h1>
  <div class="doc">{desc_html}</div>
  {'<div class="section-title">Layers &amp; data</div><div class="grid">' + ''.join(cards) + '</div>' if cards else ''}
  <p class="foot">All shared data of this server: <a href="/api/stac">STAC catalog</a></p>
</div>
<script>
  (function () {{
    var btn = document.getElementById('theme-toggle');
    if (!btn) return;
    var sun = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>';
    var moon = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>';
    function render() {{
      var isDark = document.documentElement.getAttribute('data-theme') === 'dark';
      btn.innerHTML = isDark ? sun : moon;
      btn.title = isDark ? 'Switch to light mode' : 'Switch to dark mode';
      btn.setAttribute('aria-label', btn.title);
    }}
    btn.addEventListener('click', function () {{
      var isDark = document.documentElement.getAttribute('data-theme') === 'dark';
      if (isDark) document.documentElement.removeAttribute('data-theme');
      else document.documentElement.setAttribute('data-theme', 'dark');
      try {{ localStorage.setItem('gd-portal-theme', isDark ? 'light' : 'dark'); }} catch (e) {{}}
      render();
    }});
    render();
  }})();
</script>
</body>
</html>"""


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


def read_deck_core_bbox(s3_key: str | None) -> list | None:
    """Best-effort read of a prepared GeoParquet layer's manifest grid extent — the percentile CORE
    of the data (PREP_EXTENT_QUANTILE), as a lon/lat bbox [minx, miny, maxx, maxy]. This is exactly
    the extent portal.js refits to after the manifest loads; baking it into the portal bounds lets
    `generate_style` open the map there directly (no on-load snap). Returns None on any failure or a
    non-lon/lat grid, in which case the caller falls back to the layer's full bbox (today's behaviour).

    A single small S3 GET per deck layer, run only at publish (a rare admin action)."""
    if not s3_key or s3_key.rstrip("/").endswith(".parquet"):  # unprepped single file: no manifest
        return None
    try:
        from .minio import get_s3_client
        s3 = get_s3_client()
        obj = s3.get_object(Bucket=get_settings().storage_bucket,
                            Key=f"{s3_key.rstrip('/')}/manifest.json")
        grid = (json.loads(obj["Body"].read()) or {}).get("grid")
        if not isinstance(grid, dict):
            return None
        minx, miny = float(grid["minx"]), float(grid["miny"])
        maxx, maxy = minx + float(grid["spanx"]), miny + float(grid["spany"])
        # Mirror portal.js validLonLatBounds — a non-4326 grid is not a lon/lat extent.
        if -180 <= minx < maxx <= 180 and -90 <= miny < maxy <= 90:
            return [minx, miny, maxx, maxy]
    except Exception:
        pass
    return None


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
