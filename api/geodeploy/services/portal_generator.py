"""Assemble MapLibre GL JS style JSON and write the portal static bundle."""
import json
import os
import shutil
from pathlib import Path
from ..config import get_settings
from .martin import get_tile_url as vector_tile_url
from .titiler import tile_url_from_style as raster_tile_url
from . import external_sources as ext_svc
from . import pillars
from . import symbology


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


def _ref(layer) -> str:
    """A layer's STABLE public id for baked URLs (models.new_uid). Published portals outlive the
    integer PK, which SQLite reuses after a delete — a baked `/vector/3/...` could come back
    pointing at someone else's data after an unrelated deletion + upload."""
    return getattr(layer, "uid", None) or str(layer.id)


def generate_style(layer_configs: list[dict], vector_layers: list, raster_layers: list,
                   external_sources: list | None = None,
                   deck_core_bounds: dict[int, list] | None = None,
                   layer_groups: list[dict] | None = None) -> dict:
    """
    Return user data sources and layers only.
    The basemap is provided by the template's style.json and merged in build_portal_bundle.
    Each layer gets geodeploy:name metadata so the switcher can display it.

    `deck_core_bounds` maps a GeoParquet layer id → its manifest grid extent (the percentile CORE
    of the data). For a deck-only portal (no MapLibre layers) with no admin-pinned view, portal.js
    otherwise fits the FULL extent then snaps once to this core extent when the manifest loads — a
    visible flash. Baking the core extent into `bounds` here makes the FIRST fit already correct, and
    the returned `core_fitted` flag tells portal.js to skip its now-redundant refit.

    `layer_groups` (V-13): an optional nested folder TREE (layer + group nodes) over the layers. When
    present, the DRAW ORDER is the depth-first flatten of the reconciled tree (not `layer_configs`
    order), and the reconciled tree is returned as `layer_tree` to bake for portal.js's grouped
    switcher. Absent → flat `layer_configs` order (renders exactly like before).
    """
    sources = {}
    layers = []
    deck_layers = []  # GeoParquet + 3D-Z elevation layers rendered by the deck.gl overlay (not MapLibre)
    elev_seq = 0      # counter for synthetic ids of inline elevation deck layers ("elev-0", …)
    layers_info = []  # per-layer documentation for the portal About panel (name, abstract, links)
    bounds = [180, 90, -180, -90]  # expanded below
    core_bounds = [180, 90, -180, -90]  # deck layers' merged CORE extent (see deck_core_bounds)
    deck_core_seen = False              # ≥1 deck layer contributed a real manifest core extent

    # V-13: order layers by the folder tree (depth-first) when one exists, else by layer_configs order.
    layer_tree = _reconcile_layer_tree(layer_groups, layer_configs) if layer_groups else None
    if layer_tree is not None:
        _cfg_by_ref = {(c["layer_type"], c.get("layer_id")): c for c in layer_configs}
        ordered_configs = [_cfg_by_ref[(n["layer_type"], n["layer_id"])] for n in _flatten_layer_tree(layer_tree)]
    else:
        ordered_configs = layer_configs

    # ordered_configs[0] is the TOP of the layer list and should draw on TOP of the map.
    # MapLibre draws later layers on top, so build them in reverse (config[0] added last).
    for cfg in reversed(ordered_configs):
        # V-14 catalog scope="public": layers baked in ONLY so a visitor can switch them on. They
        # must not drag the opening extent to the union of the whole instance, so every bounds
        # contribution below is skipped for them. Not persisted to Portal.layer_configs — added at
        # bundle-assembly time only.
        _extra = bool(cfg.get("_catalog_extra"))
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
                        # 3D for a deck-rendered POLYGON layer. These emit no MapLibre layer, so
                        # `fill-extrusion` never reaches them — deck's GeoJsonLayer is asked for
                        # `extruded`/`getElevation` instead (portal.js::makeDeckLayer). Points are
                        # excluded: deck extrudes polygons, and a point pillar needs the geometry
                        # buffered first, which only the PostGIS tile path does today.
                        "extrusion": ((cfg.get("style") or {}).get("extrusion")
                                      if (symbology.is_extruded(cfg.get("style") or {})
                                          and _geom_kind(layer.geometry_type) == "polygon")
                                      else None),
                        "bbox": json.loads(layer.bbox) if layer.bbox else None,
                        # Client-side duckdb-wasm read path (root-relative; portal.js
                        # absolutifies). Only prepped (partitioned-prefix) layers carry a
                        # manifest; portal.js falls back to the features.geojson endpoint
                        # when this is null or the manifest fetch fails.
                        "parquet": ({
                            "manifest": f"/api/data/vector/{_ref(layer)}/parquet/manifest.json",
                            "base": f"/api/data/vector/{_ref(layer)}/parquet/",
                        } if (layer.s3_key
                              and not layer.s3_key.rstrip("/").endswith(".parquet")) else None),
                    })
                    lb = json.loads(layer.bbox) if layer.bbox else None
                    if lb and not _extra:
                        _expand_bounds(bounds, lb)
                    core_bbox = None if _extra else (deck_core_bounds or {}).get(layer.id)
                    if core_bbox:
                        _expand_bounds(core_bounds, core_bbox)
                        deck_core_seen = True
                    elif lb and not _extra:  # no manifest core → keep its full extent in the core set
                        _expand_bounds(core_bounds, lb)
                    continue
                sources[source_id] = {
                    "type": "vector",
                    "url": f"pmtiles:///api/data/vector/{_ref(layer)}/pmtiles",
                }
            else:
                sources[source_id] = {
                    "type": "vector",
                    "tiles": [vector_tile_url(layer.schema_name, layer.table_name)],
                    "minzoom": 0,
                    "maxzoom": 22,
                }
            # A point layer in 3D reads from a SECOND source: the same table, buffered into polygons
            # by the shared Martin function (services/pillars). Added beside the normal one rather
            # than replacing it, so toggling 3D off needs no source change.
            _st = cfg.get("style") or {}
            # `_is_point`, not `_geom_kind(...) == "point"`: _geom_kind FALLS BACK to point for a
            # type it does not recognise, and "Unknown" is a real value — Fiona reports it for any
            # shapefile with a generic or mixed header. That fallback sent a polygon layer through
            # here, and the tile function then buffered administrative polygons into
            # self-intersecting rings. Buffering geometry we cannot identify is never right, so this
            # gate demands a positive answer.
            if (symbology.is_extruded(_st)
                    and _is_point(layer.geometry_type)
                    and getattr(layer, "storage_backend", "postgis") != "geoparquet"):
                sources[f"{source_id}-pillars"] = {
                    "type": "vector",
                    "tiles": [pillars.tile_url(layer.schema_name, layer.table_name,
                                               layer.geometry_column or "geom",
                                               # The layer's own extent sets the default bar width:
                                               # a fixed 30 m is invisible on anything wider than a
                                               # town, which is what "3D does nothing" looked like.
                                               symbology.pillar_radius(_st, layer.bbox))],
                    "minzoom": 0,
                    "maxzoom": 22,
                }
            ml_layers = _vector_layers(source_id, layer, cfg)
            meta = {
                "geodeploy:name": layer.name,
                "geodeploy:type": "vector",
                "geodeploy:layer_id": layer.id,
                "geodeploy:opacity": cfg.get("opacity", 1.0),
                "geodeploy:bbox": json.loads(layer.bbox) if layer.bbox else None,
                "geodeploy:geometry": _geom_kind(layer.geometry_type),
                "geodeploy:marker": (cfg.get("style") or {}).get("marker", "circle"),
                # The legend draws a line layer's classes AS LINES, dashed the way the map dashes
                # them — so the dash has to travel with the rest of the symbology.
                "geodeploy:lineType": (cfg.get("style") or {}).get("lineType", "solid"),
                "geodeploy:markerColor": (cfg.get("style") or {}).get("color", "#3b82f6"),
                "geodeploy:markerSize": (cfg.get("style") or {}).get("radius", 5),
                # EVERY marker bitmap this layer needs — one per class for a classified point layer.
                # Baked so the runtime can create them all up front; discovering them one
                # `styleimagemissing` at a time makes markers pop in as each class first scrolls
                # into view.
                "geodeploy:markerImages": symbology.marker_images(cfg.get("style") or {}),
                # The legend for a classified layer, BAKED rather than re-derived at runtime.
                # portal.js renders these entries; it does not read `classes`/`categories` and build
                # its own labels. One description, one legend — a legend that disagrees with the map
                # is worse than none, and the only way to guarantee it agrees is to derive both from
                # the same call (`symbology.legend_entries`, which also feeds the editor).
                # Empty for a single-symbol layer, which is what the swatch already covers.
                "geodeploy:legend": symbology.legend_entries(cfg.get("style") or {}),
                # The COLUMN behind the colours, and the size scale — both baked here for the same
                # reason as the entries: the runtime must not re-derive what the map already knows.
                # Added as separate keys rather than folded into the array above so that a portal
                # published by an older version stays readable (they are simply absent).
                "geodeploy:legendField": symbology.color_field(cfg.get("style") or {}),
                "geodeploy:sizeLegend": symbology.size_legend(cfg.get("style") or {}),
                # 3D is worth announcing: the runtime opens the map tilted when any layer has it.
                "geodeploy:extruded": symbology.is_extruded(cfg.get("style") or {}),
            }
            # A raw-paint passthrough can emit several sub-layers (fill + outline, …). Only the FIRST
            # carries the full geodeploy:* metadata so the switcher lists the layer once; the rest carry
            # just geodeploy:layer_id so the runtime can toggle every sub-layer together (portal.js
            # visibility toggle should target all layers sharing geodeploy:layer_id — parity TODO).
            for i, ml in enumerate(ml_layers):
                ml["metadata"] = meta if i == 0 else {"geodeploy:layer_id": layer.id, "geodeploy:part": True}
                if not cfg.get("visible", True):
                    ml.setdefault("layout", {})["visibility"] = "none"
            layers.extend(ml_layers)

            if layer.bbox and not _extra:
                _expand_bounds(bounds, json.loads(layer.bbox))

        elif cfg["layer_type"] == "raster":
            layer = next((l for l in raster_layers if l.id == cfg["layer_id"]), None)
            if not layer:
                continue
            layers_info.append(_layer_info(layer, "raster"))
            source_id = f"raster_{layer.id}"
            rstyle = cfg.get("style", {})
            # A PORTAL WITH NO STRETCH FALLS BACK TO THE LAYER'S OWN.
            #
            # TiTiler needs a `rescale` for any raster whose values are not already 0-255: without
            # one a float DEM or an index raster comes back essentially transparent. Measured on a
            # live portal — two layers configured `bidx=1` with no rescale returned 206- and
            # 514-byte tiles at the centre of their own bounds, against 27 kB for the hillshade
            # beside them. Blank in the browser and blank in QGIS, while the SAME layer opened on its
            # own looked right, because on its own it is drawn with its default style, which has one.
            #
            # So the portal keeps whatever it states, and states nothing only when the author never
            # touched the stretch — in which case the layer's own is the honest answer, not a blank
            # tile. An 8-bit RGB image has no rescale in either place and is unaffected.
            dstyle = json.loads(layer.default_style) if layer.default_style else {}
            rescale = rstyle.get("rescale") or dstyle.get("rescale")
            sources[source_id] = {
                "type": "raster",
                # The PORTAL's style, with the layer's stretch filled in when the portal names
                # none — a raster with no stretch renders black, and the layer already knows its own.
                "tiles": [raster_tile_url(layer.s3_key, dict(rstyle, rescale=rescale),
                                          band_count=layer.band_count)],
                "tileSize": 256,
            }
            # Where the data actually IS. Without this MapLibre requests tiles across the whole
            # viewport at every zoom, and the tile server answers 404 for every one that misses the
            # raster — a console full of failed requests, and real traffic spent proving that a COG
            # covering one country does not cover the Pacific. `bounds` stops them being asked for.
            _rb = _lonlat_bounds(layer.bbox)
            if _rb:
                sources[source_id]["bounds"] = _rb
                # And how far OUT it is worth asking. `bounds` stops tiles that miss the raster;
                # it does nothing about a tile that hits it and spans a continent. A drone
                # orthomosaic a few hundred metres wide was still requested at z3 — one tile
                # covering most of Europe — and TiTiler took long enough that nginx returned 504.
                # MapLibre then sits waiting on that tile, the portal's load handler never settles,
                # and the whole page hangs on the loading screen until the 15s backstop. A 504 is
                # far worse than a 404: the 404 is instant, this one costs the entire page.
                _mz = raster_minzoom(layer, _rb)
                if _mz:
                    sources[source_id]["minzoom"] = _mz
            # Base opacity + an optional raster-paint passthrough (GeoLibre import carries
            # brightness/contrast/saturation/hue in style.paint; GeoDeploy's own UI sets none of these).
            raster_paint = {"raster-opacity": cfg.get("opacity", 1.0)}
            if isinstance(rstyle.get("paint"), dict):
                raster_paint.update(rstyle["paint"])
            raster_layer = {
                "id": f"raster-{layer.id}",
                "type": "raster",
                "source": source_id,
                "paint": raster_paint,
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

            if layer.bbox and not _extra:
                _expand_bounds(bounds, json.loads(layer.bbox))

        elif cfg["layer_type"] == "external":
            src = next((s for s in (external_sources or []) if s.id == cfg["layer_id"]), None)
            if not src:
                continue
            # layer_id is REQUIRED here, not decorative: the catalog joins a card to its map layer
            # and to its folder by (kind, layer_id). Without it an external source got no
            # "Show on map" button and never appeared under a Folder facet.
            layers_info.append({"name": src.name, "kind": "external", "layer_id": src.id,
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

        elif cfg["layer_type"] == "elevation":
            # 3D-Z: an INLINE deck.gl layer (no DB layer, no MapLibre layer). The geojson carries Z; the
            # portal's deck overlay renders it at altitude. Synthetic string id (no collision with the
            # numeric ids GeoParquet deck layers use).
            estyle = cfg.get("style") or {}
            e_bbox = cfg.get("bbox")
            deck_layers.append({
                "layer_id": f"elev-{elev_seq}",
                "name": cfg.get("name", "3D layer"),
                "geometry": cfg.get("geometry", "line"),
                "elevation": cfg.get("elevation") or {"vertical_scale": 1, "offset": 0},
                "geojson": cfg.get("geojson"),
                "color": estyle.get("color", "#3b82f6"),
                "outline_color": estyle.get("outline_color", "#1d4ed8"),
                "fill_opacity": estyle.get("fill_opacity", 0.45),
                "line_width": estyle.get("line_width", 2),
                "radius": estyle.get("radius", 5),
                "opacity": cfg.get("opacity", 1.0),
                "visible": cfg.get("visible", True),
                "bbox": e_bbox,
            })
            elev_seq += 1
            layers_info.append({"name": cfg.get("name", "3D layer"), "kind": "elevation"})
            if e_bbox and not _extra:
                _expand_bounds(bounds, e_bbox)

    valid_bounds = bounds if bounds[0] < bounds[2] else None
    # Deck-only portal (every user layer is a deck.gl GeoParquet overlay, no MapLibre layers): open on
    # the merged CORE extent instead of the full extent so portal.js needn't snap to it after load.
    # Mirrors portal.js's refit gate (`!userMapLayers.length`); coreFitted then suppresses that refit.
    core_fitted = False
    if deck_layers and not layers and deck_core_seen and core_bounds[0] < core_bounds[2]:
        valid_bounds = core_bounds
        core_fitted = True
    layers_info.reverse()  # the loop runs over reversed configs; the About panel shows list order
    # Folder facet source. Done here rather than inside _layer_info because the reconciled TREE is
    # what tells us where a layer lives, and _layer_info only ever sees one layer.
    if layer_tree is not None:
        _folders = _folder_by_ref(layer_tree)
        for _info in layers_info:
            _f = _folders.get((_info.get("kind"), _info.get("layer_id")))
            if _f:
                _info["folder"] = _f

    # `valid_bounds`, NOT `bounds`: it is the deck-only CORE extent when core_fitted, and None when
    # no layer contributed an extent (raw `bounds` is still the inverted sentinel there, which would
    # be baked as a real extent and open the map on nothing).
    return {"sources": sources, "layers": layers, "bounds": valid_bounds, "core_fitted": core_fitted,
            "deck_layers": deck_layers, "layers_info": layers_info, "layer_tree": layer_tree}


def _layer_info(layer, kind: str) -> dict:
    """Documentation entry for the portal About panel: the layer's catalog metadata plus, when
    the admin shared the layer (`is_public`), its public data-access links (root-relative;
    portal.js absolutifies)."""
    # A PRIVATE layer can still appear on a public portal's map, but its catalog/technical metadata must
    # NOT be exposed on the public About page — list it as "Private" and bake nothing sensitive.
    if getattr(layer, "visibility", "organization") == "private":
        # layer_id is safe to expose and NEEDED: the catalog archetype joins its cards to the map's
        # `metadata["geodeploy:layer_id"]` to toggle/zoom. Without it a restricted layer would draw
        # on the map with no card explaining what it is.
        return {"name": layer.name, "kind": kind, "private": True, "layer_id": layer.id}
    info = {
        "name": layer.name,
        "kind": kind,
        "private": False,
        "layer_id": layer.id,   # joins a catalog card to its map layer / deck layer
        "abstract": getattr(layer, "abstract", None),
        "license": getattr(layer, "license", None),
        "attribution": getattr(layer, "attribution", None),
        "keywords": getattr(layer, "keywords", None),
        "is_public": bool(getattr(layer, "is_public", False)),
        # A3: technical metadata auto-captured at ingest — shown for organization/public layers.
        "crs": getattr(layer, "crs", None),
        "backend": getattr(layer, "storage_backend", None),   # drives the GeoParquet filter chip
        "geometry_type": getattr(layer, "geometry_type", None),
        "feature_count": getattr(layer, "feature_count", None),
    }
    _bbox = getattr(layer, "bbox", None)
    if _bbox:
        try:
            info["bbox"] = json.loads(_bbox) if isinstance(_bbox, str) else _bbox
        except Exception:
            pass
    if info["is_public"]:
        # SINGLE SOURCE OF TRUTH: the same `services/share_links.py` entries the dashboard's
        # "Share links" panel renders (which artifact suits which backend, the tool labels, the
        # per-tool menu-path hints, and which one is Recommended). The About page is plain HTML
        # and the dashboard is Vue, so the RENDERERS differ — the link data must not. Add a new
        # artifact in share_links.py and both surfaces get it.
        from . import share_links
        base = share_links.ORIGIN_TOKEN      # resolved to location.origin by the About page's script
        if kind == "raster":
            try:
                ds = json.loads(layer.default_style) if layer.default_style else {}
            except (ValueError, TypeError):
                ds = {}
            info["share"] = share_links.raster_links(layer, base, ds)
        else:
            info["share"] = share_links.vector_links(layer, base)
    return info


# ── V-11 Template Experiences: layout manifest ────────────────────────────────
# The PARITY CONTRACT. This same archetype→defaults table + override merge is mirrored in
# templates/shared/portal.js (resolveLayout) and ui/src/views/PortalEditor.vue (resolveLayout) so the
# published runtime, the editor, and the server all agree on the resolved manifest. Change all three
# together (CLAUDE.md 3-surface rule). Absent config → 'webmap' → the pre-V-11 fixed shell.
_LAYOUT_ARCHETYPES = {
    # map-first web map — the default experience.
    "webmap": {
        "regions": {
            # layerList: the catalog panel — which side, docked vs floating, its floating box.
            # Opens CLOSED. A docked list costs the map a quarter of its width before the visitor
            # has asked for anything, and on a phone it is an overlay that covers the map entirely —
            # including its own toggle, which left no way to shut it. One tap opens it.
            "layerList": {"side": "left", "mode": "docked", "collapsed": True,
                          "width": None, "x": None, "y": None},
            # controls: the map-control cluster (basemap · globe · zoom · tools · home · zoom-all · draw-zoom).
            # position: any of the 4 corners (top-left | top-right | bottom-left | bottom-right).
            "controls": {"position": "top-right"},
            "header": {"style": "bar"},
        },
        "panels": {"layerCatalog": True, "legend": True, "basemap": True, "about": True, "story": False},
    },
    # CATALOG — a browsing surface, not a map surface. The dataset list IS the page; the map is a
    # panel beside it (default right, full height: vertical space is the scarce axis, so a bottom
    # split would leave both the list and the map too short to use). `layerCatalog` is OFF because
    # the facet rail replaces the layer switcher — the visitor picks datasets from the results.
    #
    # `scope` bounds WHICH datasets are listed and defaults to this portal's own layers. It must
    # never widen past the portal's audience: a published portal is anonymous, so "public" means
    # visibility='public' only — never organization or private. `scope="public"` is read LIVE from
    # /api/stac at runtime rather than baked, so adding a dataset does not require a re-publish.
    "catalog": {
        "regions": {
            # `layerCatalog` is off by default here (the facet rail is the browse surface), but an
            # author CAN turn it on — so these settings have to be the ones that work when they do.
            # FLOATING and on the map's side: the horizontal space of a catalog page belongs to the
            # results, so a docked column would be taken out of the map's half, and the map is the
            # only part of the page a layer list means anything to. Collapsed, like every other
            # archetype — one tap on the on-map toggle opens it.
            "layerList": {"side": "right", "mode": "floating", "collapsed": True,
                          "width": None, "x": None, "y": None},
            "controls": {"position": "top-right"},
            "header": {"style": "bar"},
            "catalog": {
                "scope": "portal",      # portal | public
                "mapSide": "right",     # right | bottom | none
                "mapWidth": 50,         # % of the content area when mapSide=right (half the page)
                "railWidth": 20,        # % for the facet rail
                "perPage": 12,
            },
        },
        "panels": {"catalog": True, "layerCatalog": False, "legend": True, "basemap": True,
                   # No About page: every dataset already carries its abstract, licence and access
                   # links on its own card, so an About page would only restate the catalog.
                   "about": False, "story": False},
    },
    # scrollytelling — a narrative column drives the map camera; the layer list floats (collapsed by
    # default), reachable from the toggle at the top of the control cluster, like a normal web map.
    "storymap": {
        "regions": {
            "layerList": {"side": "left", "mode": "floating", "collapsed": True,
                          "width": None, "x": None, "y": None},
            "controls": {"position": "top-right"},
            "header": {"style": "minimal"},
        },
        "panels": {"layerCatalog": True, "legend": True, "basemap": True, "about": False, "story": True},
    },
}
# Back-compat: the Phase-1 archetypes 'webmap+catalog'/'catalog' were dropped (their only difference was
# a wider list — meaningless); they now resolve to webmap. Catalog prominence is just layer-list placement.
# `catalog` used to be aliased to webmap as a placeholder — which is why choosing it appeared to do
# nothing. It is a real archetype now. `webmap+catalog` (map-first WITH a catalog panel) is still
# unbuilt, so it keeps degrading to a working web map rather than a blank shell.
_ARCHETYPE_ALIASES = {"webmap+catalog": "webmap"}
_DEFAULT_ARCHETYPE = "webmap"


def resolve_layout(config: dict | None) -> dict:
    """Resolve a (possibly partial / None) layout_config into a full manifest: archetype defaults
    deep-merged with per-portal region/panel overrides. None → the webmap default (today's shell)."""
    import copy
    arch = (config or {}).get("archetype") or _DEFAULT_ARCHETYPE
    arch = _ARCHETYPE_ALIASES.get(arch, arch)
    if arch not in _LAYOUT_ARCHETYPES:
        arch = _DEFAULT_ARCHETYPE
    base = copy.deepcopy(_LAYOUT_ARCHETYPES[arch])
    resolved = {"archetype": arch, "regions": base["regions"], "panels": base["panels"]}
    if config:
        for group in ("regions", "panels"):
            for key, val in (config.get(group) or {}).items():
                if isinstance(val, dict) and isinstance(resolved[group].get(key), dict):
                    resolved[group][key].update(val)
                else:
                    resolved[group][key] = val
    return resolved


# ── V-11 R3: per-portal colour theme → CSS-variable overrides ─────────────────
import re as _re
_HEX_COLOR = _re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_FONT_STACKS = {
    "sans": "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
    "serif": "Georgia, 'Iowan Old Style', 'Times New Roman', serif",
    "mono": "'SF Mono', ui-monospace, 'Cascadia Code', Menlo, monospace",
}


def build_theme_css(theme: dict | None) -> str:
    """Render a portal's colour theme as CSS-variable overrides, injected AFTER the template theme.css
    (so it wins). Values are strictly validated (a hex colour / a known font key) since they land inside
    a <style> block — a bad value is dropped, never emitted raw."""
    if not theme:
        return ""
    out = []
    root = []
    accent = theme.get("accent")
    if isinstance(accent, str) and _HEX_COLOR.match(accent.strip()):
        root.append(f"--accent: {accent.strip()};")
        root.append(f"--accent-light: color-mix(in srgb, {accent.strip()} 22%, transparent);")
    # storyBg / storyOpacity / storyFg: the scrollytelling narrative column (storymap archetype only).
    # Opacity is applied as a colour-mix rather than CSS `opacity`, because `opacity` would fade the
    # TEXT with the panel — the point is to see the map through the panel while the words stay solid.
    story_bg = theme.get("storyBg")
    if isinstance(story_bg, str) and _HEX_COLOR.match(story_bg.strip()):
        bg = story_bg.strip()
        try:
            pct = int(theme.get("storyOpacity"))
        except (TypeError, ValueError):
            pct = 100
        pct = max(20, min(100, pct))   # below ~20% the text has nothing to sit on
        root.append(f"--story-bg: {bg};" if pct >= 100
                    else f"--story-bg: color-mix(in srgb, {bg} {pct}%, transparent);")
    # storyFg: text colour. A dark panel colour with the light theme's dark text is unreadable, and
    # the visitor's own light/dark toggle can flip it either way — so the author sets it explicitly.
    story_fg = theme.get("storyFg")
    if isinstance(story_fg, str) and _HEX_COLOR.match(story_fg.strip()):
        root.append(f"--story-fg: {story_fg.strip()};")
    if root:
        out.append(":root { " + " ".join(root) + " }")
    font = _FONT_STACKS.get(theme.get("font"))
    if font:
        out.append(f"body {{ font-family: {font}; }}")
    return ("\n/* V-11 R3 per-portal theme overrides */\n" + "\n".join(out)) if out else ""


def resolve_theme(theme: dict | None) -> dict:
    """The theme metadata baked into style.geodeploy.theme (portal.js reads .mode for the initial
    light/dark state and .logo for the header brand). Colours/fonts are applied via CSS
    (build_theme_css), not here."""
    t = theme or {}
    mode = t.get("mode")
    out = {"mode": mode if mode in ("light", "dark", "auto") else "auto"}
    # Header logo/brand: {kind:'preset'|'custom'|'none', id?, url?}. Custom url is a same-origin asset.
    logo = t.get("logo")
    if isinstance(logo, dict) and logo.get("kind") in ("preset", "custom", "none"):
        # `tint` rides along: a custom logo drawn as a MASK in the accent colour (portal.js
        # buildHeaderLogo). Rebuilt key-by-key rather than passed through, so anything new here has
        # to be added deliberately — which is exactly how `tint` would otherwise have been dropped
        # between the editor and the published page, with no error anywhere.
        out["logo"] = {"kind": logo["kind"], "id": logo.get("id"), "url": logo.get("url"),
                       "tint": bool(logo.get("tint"))}
    return out


def build_portal_bundle(slug: str, title: str, user_data: dict, template_id: str, layer_configs: list[dict],
                        access_type: str = "public", password_sha256: str | None = None,
                        owner_id: int | None = None,
                        initial_view: dict | None = None, description: str | None = None,
                        basemap: str | None = None,
                        layout_config: dict | None = None, story: dict | None = None,
                        theme: dict | None = None) -> str:
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

    # REFUSE to write a portal with no runtime. `_read` returns "" for a missing file, so a caller
    # that cannot see /templates produced a bundle containing a style and nothing else: the portal
    # rendered a basemap and MapLibre's own zoom control, with no sidebar, no layer list and no data,
    # and every caller reported success because files were written.
    #
    # That is exactly what happened when the celery container had no `./templates` mount and a
    # restore's automatic republish ran there. A hollow portal that claims to be published is worse
    # than a failed publish, because nothing points at the cause.
    if not portal_js.strip():
        raise RuntimeError(
            f"The portal runtime is missing: {shared_dir}/portal.js is empty or unreadable. "
            "A bundle written without it renders a basemap and nothing else. If this is the Celery "
            "worker, the container needs the `./templates:/templates:ro` mount.")

    # Load template files. A template only needs theme.css + style.json + template.json;
    # layout.html is optional and falls back to the shared skeleton.
    basemap_style = _load_basemap(template_dir)
    # Template's own theme.css + the per-portal colour overrides (R3), which land AFTER it so they win.
    theme_css = _read(template_dir / "theme.css", "") + build_theme_css(theme)
    layout_html = (_read(template_dir / "layout.html")
                   or _read(shared_dir / "layout.html")
                   or _default_layout())

    # Resolved once and reused: the style needs it, and so does the decision below about whether to
    # bake the catalog payload at all.
    _layout = resolve_layout(layout_config)

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
            # V-13: nested folder tree for the grouped layer switcher (None → portal.js flat list).
            "layerTree": user_data.get("layer_tree"),
            # V-11: resolved layout manifest {archetype, regions, panels}. Always present (webmap
            # default) so portal.js has one source of truth; the editor mirrors resolveLayout().
            "layout": _layout,
            # V-14 catalog archetype: the dataset records the browse surface renders (name, abstract,
            # keywords, license, CRS, geometry, feature count, bbox and the public access links).
            # This is the SAME `layers_info` the About page uses — one metadata shape, so a field added
            # for one surface appears on the other. Baked only for the catalog archetype: it is a few
            # KB per layer and webmap/storymap have no use for it.
            "catalog": (user_data.get("layers_info", [])
                        if _layout.get("panels", {}).get("catalog") else None),
            # V-11 storymap: narrative sections (only rendered when archetype == 'storymap').
            "story": story if (story and story.get("sections")) else None,
            # V-11 R3: colour-theme metadata (portal.js reads .mode for the initial light/dark state).
            "theme": resolve_theme(theme),
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

    # MapLibre v5 reads `projection` from the STYLE. Baking it means a globe portal loads as a globe
    # rather than loading flat and being corrected afterwards — the style's own projection was
    # otherwise applied at style-load and silently reset an imperatively-set globe back to mercator.
    if isinstance(initial_view, dict) and initial_view.get("projection") == "globe":
        full_style["projection"] = {"type": "globe"}

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
    # Images. `![alt](src)` is centred at its natural size (capped to the column width); an alt
    # ending in `|full` — `![Map of the area|full](src)` — stretches it to fill the column. The
    # marker is stripped from the alt so it never reaches a screen reader.
    def _img(m):
        alt, src = m.group(1), m.group(2)
        cls = ""
        if alt.endswith("|full"):
            alt, cls = alt[:-5].rstrip(), ' class="full"'
        return f'<img src="{src}" alt="{alt}"{cls} loading="lazy">'

    s = re.sub(r"!\[([^\]]*)\]\((https?://[^)\s]+|/[^)\s]*)\)", _img, s)
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


def _share_block(links: list[dict]) -> str:
    """The About page's "Use this data" section — the published-portal twin of the dashboard's
    `ShareLinksModal.vue`. Same data (`services/share_links.py`), same reading order (Recommended
    first), same per-row anatomy: label · format · tools · URL · copy · hint. Collapsed into a
    native <details> so a portal with many layers stays readable; no framework, and the only JS is
    the origin swap + clipboard at the bottom of the page."""
    rows = []
    for l in links:
        head = [f'<span class="s-label">{_esc(l["label"])}</span>']
        if l.get("primary"):
            head.append('<span class="s-rec">Recommended</span>')
        head.append(f'<span class="s-fmt">{_esc(l["format"])}</span>')
        head += [f'<span class="s-tool">{_esc(t)}</span>' for t in l.get("tools", [])]
        rows.append(
            '<div class="s-row">'
            f'<div class="s-head">{"".join(head)}</div>'
            '<div class="s-urlrow">'
            f'<code class="s-url" data-url="{_esc(l["url"])}">{_esc(l["url"])}</code>'
            '<button class="s-copy" type="button" title="Copy">Copy</button>'
            "</div>"
            f'<p class="s-hint">{_esc(l.get("hint") or "")}</p>'
            "</div>")
    return ('<details class="share"><summary>Use this data elsewhere '
            f'<span class="s-count">{len(links)} links</span></summary>'
            f'<div class="s-body">{"".join(rows)}</div></details>')


def _layers_section(cards: list[str]) -> str:
    """The "Layers & data" section, with a filter box once there are enough layers that scanning
    the list stops being practical. Filtering is client-side over `data-search` — every card is
    already in the DOM, so there is nothing to fetch and it works on a static page."""
    if not cards:
        return ""
    kinds = {k for k in (_kind_of(c) for c in cards) if k}
    chips = ""
    if len(kinds) > 1:
        labels = [("", "All"), ("vector", "Vector"), ("geoparquet", "GeoParquet"),
                  ("raster", "Raster")]
        chips = '<div class="kind-chips" id="layer-kinds">' + "".join(
            f'<button type="button" class="kind-chip{" on" if not k else ""}" data-kind="{k}">{lbl}</button>'
            for k, lbl in labels if not k or k in kinds) + "</div>"
    # The toolbar only earns its space once the list is long enough to scan.
    bar = ("" if len(cards) < 6 else
           '<div class="layer-tools">'
           '<input type="search" id="layer-search" class="layer-search" '
           'placeholder="Search layers…" aria-label="Search layers">'
           + chips + "</div>")
    return ('<div class="section-title">Layers &amp; data</div>'
            + bar
            + '<div class="grid" id="layer-grid">' + "".join(cards) + "</div>"
            + '<p class="no-match" id="layer-nomatch" hidden>No layer matches that.</p>')


def _kind_of(card_html: str) -> str:
    marker = 'data-kind="'
    i = card_html.find(marker)
    if i < 0:
        return ""
    i += len(marker)
    return card_html[i:card_html.find('"', i)]


def _about_page(slug: str, title: str, description: str | None, layers_info: list[dict]) -> str | None:
    """The standalone documentation page (`about.html`) published next to the map — GeoNode-style
    'full page that links to the map', styled after GeoLibre's dark design tokens. Static HTML,
    rendered server-side at publish (no JS needed)."""
    has_layer_docs = any(i.get("abstract") or i.get("license") or i.get("attribution") or i.get("share")
                         or i.get("crs") or i.get("bbox") or i.get("geometry_type")
                         for i in layers_info)
    if not description and not has_layer_docs:
        return None

    cards = []
    for i in layers_info:
        # Private layer → just the name + a "Private" tag; none of its metadata is exposed publicly.
        if i.get("private"):
            cards.append('<div class="layer" data-kind="' + _esc(i.get("kind") or "")
                         + '" data-search="' + _esc((i.get("name") or "").lower())
                         + '"><div class="layer-name">' + _esc(i.get("name"))
                         + '<span class="badge badge-private">private</span></div></div>')
            continue
        # Searchable haystack for the About page's filter box (name + abstract + keywords +
        # geometry/CRS), lowercased once here so the browser only has to substring-match.
        haystack = " ".join(str(i.get(k) or "") for k in
                            ("name", "abstract", "keywords", "geometry_type", "crs")).lower()
        # GeoParquet is its own filter bucket: a file-backed vector behaves differently enough
        # (deck.gl/DuckDB, different artifacts) that people look for it by name.
        kind = "geoparquet" if (i.get("kind") == "vector" and i.get("backend") == "geoparquet")             else (i.get("kind") or "")
        parts = ['<div class="layer" data-kind="' + _esc(kind) + '" data-search="' + _esc(haystack) + '">'
                 + '<div class="layer-name">' + _esc(i.get("name"))
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
        # A3: auto-captured technical metadata (geometry · features · CRS · extent).
        tech = []
        if i.get("geometry_type"):
            tech.append("Geometry: " + _esc(str(i["geometry_type"])))
        if i.get("feature_count") is not None:
            tech.append(f'{i["feature_count"]:,} features')
        if i.get("crs"):
            tech.append("CRS: " + _esc(str(i["crs"])))
        if isinstance(i.get("bbox"), list) and len(i["bbox"]) == 4:
            try:
                tech.append("Extent: " + ", ".join(f"{float(v):.4f}" for v in i["bbox"]))
            except (TypeError, ValueError):
                pass
        if tech:
            parts.append('<p class="meta tech">' + " · ".join(tech) + "</p>")
        if i.get("share"):
            parts.append(_share_block(i["share"]))
        parts.append("</div>")
        cards.append("".join(parts))

    desc_html = _md_to_html(description) if description else ""
    from .share_links import ORIGIN_TOKEN as _ORIGIN_TOKEN
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
  /* Pasted/linked images: block-level, centred, and never wider than the column. An
     image narrower than the column stays centred rather than hugging the left edge;
     `![alt|full](src)` opts into filling the width. */
  .doc img {{
    display: block; max-width: 100%; height: auto; margin: 16px auto;
    border-radius: var(--radius); border: 1px solid var(--border);
  }}
  .doc img.full {{ width: 100%; }}
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
  /* Single column ON PURPOSE: these cards expand (share links), and in a 2-up grid the
     expanded card stretched its row-mate and left the long URLs cramped. */
  .grid {{ display: grid; grid-template-columns: 1fr; gap: 14px; }}
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
  .badge-private {{ color: var(--muted); background: transparent; border-color: var(--border); }}
  .abstract {{
    font-size: 13.5px; color: var(--abstract-fg); margin-top: 8px;
    width: 100%; max-width: none; text-align: justify; hyphens: auto;
  }}
  .meta {{ font-size: 12px; color: var(--muted); margin-top: 8px; }}
  .tech {{ font-variant-numeric: tabular-nums; margin-top: 4px; opacity: .9; }}
  .links {{ display: flex; flex-wrap: wrap; gap: 7px; margin-top: 12px; }}
  .pill {{
    font-size: 12px; font-weight: 600; text-decoration: none; padding: 5px 13px;
    border-radius: 999px; color: var(--primary); background: var(--card);
    border: 1px solid var(--border); transition: border-color .15s;
  }}
  .pill:hover {{ border-color: var(--primary); }}
  /* Toolbar sits directly above .grid and, like it, is a block child of .wrap — so it spans
     exactly the same width as the cards beneath it. */
  .layer-tools {{ display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin: 0 0 14px; }}
  .layer-search {{
    flex: 1 1 240px; min-width: 0; padding: 9px 13px; font: inherit; font-size: 13.5px;
    color: var(--fg); background: var(--card); border: 1px solid var(--border);
    border-radius: var(--radius);
  }}
  .layer-search:focus {{ outline: none; border-color: var(--primary); }}
  .kind-chips {{ display: flex; gap: 6px; flex-wrap: wrap; }}
  .kind-chip {{
    cursor: pointer; font: inherit; font-size: 12px; font-weight: 600; padding: 7px 13px;
    border-radius: 999px; color: var(--muted); background: var(--card);
    border: 1px solid var(--border); transition: color .15s, border-color .15s;
  }}
  .kind-chip:hover {{ border-color: var(--layer-hover); }}
  .kind-chip.on {{ color: var(--primary); border-color: var(--primary); }}
  .no-match {{ font-size: 13px; color: var(--muted); text-align: center; padding: 18px 0; }}
  /* "Use this data elsewhere" — mirrors ui/src/components/data/ShareLinksModal.vue so the
     published portal and the dashboard read the same. Change both together. */
  .share {{ margin-top: 14px; border-top: 1px solid var(--border); padding-top: 12px; }}
  .share > summary {{
    cursor: pointer; font-size: 12.5px; font-weight: 650; color: var(--primary);
    list-style: none; display: flex; align-items: center; gap: 8px;
  }}
  .share > summary::-webkit-details-marker {{ display: none; }}
  /* The character itself, NOT a CSS F517 escape: this stylesheet is written from a
     Python f-string, and the backslash was being consumed on the way out (it rendered
     as a literal "F517"). No escaping layer left to get wrong. */
  .share > summary::before {{ content: "🔗"; font-size: 12px; }}
  .share[open] > summary {{ margin-bottom: 10px; }}
  .s-count {{ font-size: 11px; font-weight: 500; color: var(--muted); }}
  .s-row {{
    border: 1px solid var(--border); border-radius: var(--radius);
    background: var(--card); padding: 10px 12px; margin-bottom: 8px;
  }}
  .s-head {{ display: flex; flex-wrap: wrap; align-items: center; gap: 7px; }}
  .s-label {{ font-size: 13px; font-weight: 650; }}
  .s-rec {{
    font-size: 9.5px; font-weight: 700; text-transform: uppercase; letter-spacing: .5px;
    color: var(--badge-fg); background: var(--badge-bg); border: 1px solid var(--badge-border);
    border-radius: 999px; padding: 2px 7px;
  }}
  .s-fmt {{
    font-size: 10.5px; color: var(--muted); background: var(--panel);
    border-radius: 4px; padding: 1.5px 6px;
  }}
  .s-tool {{ font-size: 10.5px; color: var(--muted); }}
  .s-urlrow {{ display: flex; align-items: center; gap: 8px; margin-top: 7px; }}
  .s-url {{
    flex: 1; min-width: 0; overflow-x: auto; white-space: nowrap; font-size: 11.5px;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    background: var(--panel); border-radius: 5px; padding: 5px 8px; color: var(--doc-fg);
  }}
  .s-copy {{
    flex: none; cursor: pointer; font: inherit; font-size: 11px; font-weight: 600;
    color: var(--primary); background: var(--card); border: 1px solid var(--border);
    border-radius: 6px; padding: 4px 10px;
  }}
  .s-copy:hover {{ border-color: var(--primary); }}
  .s-hint {{ font-size: 11.5px; color: var(--muted); margin-top: 6px; }}
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
  {_layers_section(cards)}
  <p class="foot">All shared data of this server: <a href="/api/stac">STAC catalog</a></p>
</div>
<script>
  // Share links: the URLs are baked with an ORIGIN placeholder (the public host isn't known at
  // publish time) — swap it for this page's real origin, then wire the copy buttons. Doing it here
  // rather than server-side is what lets ONE published bundle work behind any hostname.
  (function () {{
    var ORIGIN = '{_ORIGIN_TOKEN}';
    document.querySelectorAll('.s-url').forEach(function (el) {{
      var url = (el.getAttribute('data-url') || '').split(ORIGIN).join(location.origin);
      el.setAttribute('data-url', url);
      el.textContent = url;
    }});
    document.querySelectorAll('.s-copy').forEach(function (btn) {{
      btn.addEventListener('click', function () {{
        var row = btn.closest('.s-urlrow');
        var url = row && row.querySelector('.s-url');
        if (!url) return;
        var text = url.getAttribute('data-url') || url.textContent;
        var done = function () {{
          btn.textContent = 'Copied';
          setTimeout(function () {{ btn.textContent = 'Copy'; }}, 1500);
        }};
        if (navigator.clipboard && window.isSecureContext) {{
          navigator.clipboard.writeText(text).then(done, done);
        }} else {{
          // clipboard API needs HTTPS — fall back so a plain-http instance still copies
          var ta = document.createElement('textarea');
          ta.value = text; document.body.appendChild(ta); ta.select();
          try {{ document.execCommand('copy'); }} catch (e) {{}}
          ta.remove(); done();
        }}
      }});
    }});
  }})();
  // Layer filter: substring match over each card's prebuilt data-search haystack, ANDed with the
  // kind chip. Both are attributes already in the DOM, so there is nothing to fetch.
  (function () {{
    var grid = document.getElementById('layer-grid');
    if (!grid) return;
    var box = document.getElementById('layer-search');
    var chipBox = document.getElementById('layer-kinds');
    var none = document.getElementById('layer-nomatch');
    var cards = Array.prototype.slice.call(grid.querySelectorAll('.layer'));
    var kind = '';

    function apply() {{
      var q = box ? box.value.trim().toLowerCase() : '';
      var shown = 0;
      cards.forEach(function (card) {{
        var hit = (!q || (card.getAttribute('data-search') || '').indexOf(q) !== -1) &&
                  (!kind || card.getAttribute('data-kind') === kind);
        card.hidden = !hit;
        if (hit) shown++;
      }});
      if (none) none.hidden = shown !== 0;
    }}

    if (box) box.addEventListener('input', apply);
    if (chipBox) {{
      chipBox.addEventListener('click', function (ev) {{
        var chip = ev.target.closest('.kind-chip');
        if (!chip) return;
        kind = chip.getAttribute('data-kind') || '';
        chipBox.querySelectorAll('.kind-chip').forEach(function (c) {{
          c.classList.toggle('on', c === chip);
        }});
        apply();
      }});
    }}
  }})();

  // Accordion: opening one layer's links closes the others, so the page never becomes a wall of
  // URLs. (<details name> does this natively but only in recent browsers — do it explicitly.)
  (function () {{
    var all = Array.prototype.slice.call(document.querySelectorAll('details.share'));
    all.forEach(function (d) {{
      d.addEventListener('toggle', function () {{
        if (!d.open) return;
        all.forEach(function (other) {{ if (other !== d) other.open = false; }});
      }});
    }});
  }})();
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


def _source_layer_name(layer) -> str:
    """The MapLibre `source-layer` for a vector layer: PostGIS tiles by Martin under schema.table;
    GeoParquet PMTiles use the tippecanoe layer name "geodeploy" (see tasks/pmtiles_tile.PMTILES_LAYER)."""
    return ("geodeploy" if getattr(layer, "storage_backend", "postgis") == "geoparquet"
            else f"{layer.schema_name}.{layer.table_name}")


def _vector_layers(source_id: str, layer, cfg: dict) -> list[dict]:
    """The MapLibre render layers for one vector layer — usually one, but a **raw-paint passthrough**
    (`style.maplibre.layers`, used by the GeoLibre importer to carry data-driven/extrusion symbology
    we can't express with the friendly keys) can emit several (e.g. fill + outline line). Each raw
    entry supplies `type`/`paint`/`layout`/`filter`/`suffix`; we wire the layer id + source-layer."""
    raw = ((cfg.get("style") or {}).get("maplibre") or {}).get("layers")
    if not raw:
        return [_vector_layer(source_id, layer, cfg)]
    source_layer = _source_layer_name(layer)
    out: list[dict] = []
    for i, entry in enumerate(raw):
        suffix = entry.get("suffix") or entry.get("type") or f"l{i}"
        ml = {"id": f"vector-{layer.id}-{suffix}", "type": entry["type"],
              "source": source_id, "source-layer": source_layer}
        if entry.get("filter") is not None:
            ml["filter"] = entry["filter"]
        if entry.get("paint"):
            ml["paint"] = entry["paint"]
        if entry.get("layout"):
            ml["layout"] = dict(entry["layout"])
        out.append(ml)
    return out or [_vector_layer(source_id, layer, cfg)]


def _vector_layer(source_id: str, layer, cfg: dict) -> dict:
    geom = (layer.geometry_type or "").lower()
    style = cfg.get("style", {})
    opacity = cfg.get("opacity", 1.0)
    source_layer = _source_layer_name(layer)
    # Colour may now be a data-driven EXPRESSION rather than a string (graduated / categorized).
    # `services/symbology` computes it, and `ui/src/lib/symbology.js` computes the same thing for
    # the editor preview and the runtime — one description, four renderers (CLAUDE.md parity rule).
    color = symbology.color_expression(style)

    if "polygon" in geom:
        # 3D: polygons extruded by a numeric property. A separate layer TYPE, not a paint variation,
        # so it has to be decided here rather than patched onto the fill below.
        if symbology.is_extruded(style):
            return {
                "id": f"vector-{layer.id}",
                "type": "fill-extrusion",
                "source": source_id,
                "source-layer": source_layer,
                "paint": symbology.extrusion_paint(style, opacity),
            }
        fill_paint = {
            "fill-color": color,
            "fill-opacity": opacity * style.get("fill_opacity", 0.45),
        }
        # Removing a fill's outline is `fill-antialias: false`, NOT omitting fill-outline-color.
        # Omitting it makes the outline MATCH FILL-COLOR (the spec's default), which is why "None"
        # produced a visible dark edge instead of none — reported as "it drew a black outline".
        # A `fill` layer always strokes its own edge; antialias is the switch that stops it.
        _outline = symbology.outline_color(style)
        if _outline:
            fill_paint["fill-outline-color"] = _outline
        else:
            fill_paint["fill-antialias"] = False
        return {
            "id": f"vector-{layer.id}",
            "type": "fill",
            "source": source_id,
            "source-layer": source_layer,
            "paint": fill_paint,
        }
    if "line" in geom:
        paint = {
            "line-color": color,
            "line-width": symbology.size_expression(style, style.get("line_width", 2)),
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
    # POINTS IN 3D: a pillar standing at each location. MapLibre extrudes FILLS only, so there is no
    # point form of fill-extrusion — the geometry has to become a polygon. `services/pillars` serves
    # exactly that: one shared Martin FUNCTION buffers the points by a radius in metres and returns
    # them as polygons, so the layer keeps the renderer it already had (MapLibre, from a tile source)
    # and every cross-cutting behaviour around it — identify, z-order, visibility — is unchanged.
    # Routing these through deck.gl instead would mean a layer changes RENDERER when 3D is ticked.
    #
    # The `_is_point` test MUST match the one guarding the pillar SOURCE in generate_style — this
    # layer reads from that source, so a layer emitted without it points at a source that does not
    # exist and MapLibre drops the layer entirely. Two conditions, one decision: keep them identical.
    if (symbology.is_extruded(style)
            and _is_point(layer.geometry_type)
            and getattr(layer, "storage_backend", "postgis") != "geoparquet"):
        return {
            "id": f"vector-{layer.id}",
            "type": "fill-extrusion",
            "source": f"{source_id}-pillars",
            "source-layer": pillars.SOURCE_LAYER,
            "paint": symbology.extrusion_paint(style, opacity),
        }
    # point / unknown — a symbol layer with runtime-generated canvas icons, which is what lets points
    # use shapes (circle/square/triangle/diamond/star/cross) on any basemap.
    #
    # A classified layer keeps its shape: `icon-image` is DATA-DRIVEN in MapLibre, so the style emits
    # one image per class and selects between them with the same `step`/`match` the colour uses. See
    # the note above `symbology.marker_image_id` for why this beats an SDF icon (mushy edges) and why
    # falling back to a `circle` layer — the first implementation — was wrong: losing the marker
    # shape the moment you classify is not a trade anyone asked for.
    return {
        "id": f"vector-{layer.id}",
        "type": "symbol",
        "source": source_id,
        "source-layer": source_layer,
        "layout": {
            "icon-image": symbology.icon_image_expression(style),
            "icon-size": symbology.icon_size_expression(style),
            "icon-allow-overlap": True,
            "icon-ignore-placement": True,
        },
        "paint": {
            "icon-opacity": opacity,
        },
    }


def _lonlat_bounds(raw) -> list[float] | None:
    """A stored bbox as MapLibre source `bounds` — [w, s, e, n] in lon/lat — or None.

    Raster bboxes are reprojected to EPSG:4326 at ingest (`cog_converter._read_meta`), but that
    reprojection has a fallback that keeps the SOURCE CRS when it fails. A projected bbox here would
    be far outside lon/lat range and would hide the layer completely rather than merely leaving the
    404s in place, so the range check is what makes this safe to apply blindly: anything that is not
    plausibly lon/lat is dropped and the source simply carries no bounds, exactly as before.
    """
    try:
        b = json.loads(raw) if isinstance(raw, str) else raw
        w, s, e, n = (float(v) for v in b)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not (-180.0 <= w < e <= 180.0 and -90.0 <= s < n <= 90.0):
        return None
    return [w, s, e, n]


#: How many zoom levels BELOW "the layer fills one tile" we still bother asking for. Four levels
#: means the layer is about 1/16th of a tile at the lowest zoom requested — a visible speck, not a
#: sub-pixel nothing, and cheap for the tile server to produce from an overview.
_MINZOOM_SLACK = 4


def _min_zoom_for(bounds: list[float]) -> int:
    """The lowest zoom worth requesting tiles at, from the layer's extent.

    `bounds` stops MapLibre asking for tiles that MISS the raster. It does nothing about a tile that
    hits it and spans a continent, and that is the expensive case: a z3 tile of a drone orthomosaic
    is one request covering most of Europe, which TiTiler can take long enough over that nginx
    answers **504**. MapLibre then waits on it, the portal's load handler never completes, and the
    page sits on the loading screen — so one oversized request costs the whole portal, where a 404
    would have cost nothing.

    A tile spans 360/2^z degrees, so the layer first fits inside one tile at z = log2(360 / width).
    Below that it is only getting smaller; `_MINZOOM_SLACK` keeps a few levels of "visible as a
    speck" before we stop asking.

    Returns 0 (falsy — no `minzoom` written) for anything continent-sized, where every zoom is
    legitimate and the old behaviour is correct.
    """
    import math
    width = max(bounds[2] - bounds[0], bounds[3] - bounds[1])
    if width <= 0:
        return 0
    fits_at = math.log2(360.0 / width) if width < 360 else 0
    return max(0, min(int(fits_at) - _MINZOOM_SLACK, 18))


def raster_minzoom(layer, bounds) -> int:
    """The `minzoom` to write for this raster source — 0 meaning "write none" (issue #17).

    The floor above is computed from the layer's EXTENT, which is a proxy for cost. `low_zoom_ok` is
    the measurement: taken at ingest from the file's own overview pyramid
    (`cog_converter.low_zoom_is_cheap`). When the file can answer a zoomed-out tile from a small
    overview, the 504 the floor exists to prevent cannot happen, and keeping the floor only makes a
    small high-resolution layer disappear below a computed zoom with no message.

    `None` — every layer ingested before the measurement existed — keeps the heuristic. So this
    changes nothing for an existing instance until its rasters are re-ingested, which is the
    conservative direction for a guard that was protecting against a page-wide hang.
    """
    if getattr(layer, "low_zoom_ok", None) is True:
        return 0
    return _min_zoom_for(bounds)


def _geom_kind(geometry_type: str | None) -> str:
    """Normalize a PostGIS/Fiona geometry type to point|line|polygon.

    NOTE the fallback: an unrecognised type answers "point". That is a rendering default and it is
    kept — a symbol is the least destructive way to draw something unidentified. It is NOT a
    statement that the layer holds points, so anything that would act on the geometry itself must
    ask `_is_point` instead. See the pillar source in `generate_style`.
    """
    g = (geometry_type or "").lower()
    if "polygon" in g:
        return "polygon"
    if "line" in g:
        return "line"
    if "point" in g:
        return "point"
    return "point"


def _is_point(geometry_type: str | None) -> bool:
    """True only when the type POSITIVELY says point — never for an unknown or missing one."""
    g = (geometry_type or "").lower()
    return "point" in g and "polygon" not in g and "line" not in g


def _expand_bounds(bounds: list, bbox: list) -> None:
    if len(bbox) < 4:
        return
    bounds[0] = min(bounds[0], bbox[0])
    bounds[1] = min(bounds[1], bbox[1])
    bounds[2] = max(bounds[2], bbox[2])
    bounds[3] = max(bounds[3], bbox[3])


def _flatten_layer_tree(tree: list) -> list[dict]:
    """Depth-first list of layer-ref nodes ({layer_type, layer_id}) in top→bottom (= draw) order."""
    out = []
    for node in tree or []:
        if "layer_id" in node:
            out.append({"layer_type": node.get("layer_type"), "layer_id": node.get("layer_id")})
        elif "children" in node:
            out.extend(_flatten_layer_tree(node.get("children") or []))
    return out


def _folder_by_ref(tree: list, path: tuple = ()) -> dict:
    """Map every layer ref in the folder tree to the NAME OF THE FOLDER IT SITS IN.

    Feeds the catalog archetype's "Folder" facet, so a visitor filters by the same groups the portal
    author arranged in the editor — one organisation, not a second taxonomy to maintain.

    Nested folders use the innermost name rather than a full path: it is what the author sees on the
    card in the editor, and a path reads badly ellipsised in a 216px rail. A layer at the ROOT gets
    no entry at all, so it is simply absent from the facet (unticking everything still shows it —
    the facet narrows, it never becomes a required choice).
    """
    out: dict = {}
    for node in tree or []:
        if "layer_id" in node:
            if path:
                out[(node.get("layer_type"), node.get("layer_id"))] = path[-1]
        elif "children" in node:
            name = (node.get("name") or "").strip()
            out.update(_folder_by_ref(node.get("children") or [], path + (name,) if name else path))
    return out


_GROUP_PROPS = ("id", "name", "collapsed", "exclusive", "description")


def _reconcile_layer_tree(tree: list, layer_configs: list[dict]) -> list:
    """Return a copy of the folder tree that references EXACTLY the current layer_configs: drop layer
    nodes whose config is gone (or duplicated), keep the group structure, then append any configs not
    referenced anywhere as root-level layer nodes (at the bottom). Keeps the tree ↔ configs invariant
    so no layer is lost and no node dangles — the same reconciled tree drives draw order + the switcher."""
    cfg_keys = {(c["layer_type"], c["layer_id"]) for c in layer_configs}
    seen: set = set()

    def clean(nodes: list) -> list:
        out = []
        for n in nodes or []:
            if "layer_id" in n:
                key = (n.get("layer_type"), n.get("layer_id"))
                if key in cfg_keys and key not in seen:
                    seen.add(key)
                    out.append({"layer_type": n["layer_type"], "layer_id": n["layer_id"]})
            elif "children" in n:
                group = {k: n.get(k) for k in _GROUP_PROPS if k in n}
                group["children"] = clean(n.get("children") or [])
                out.append(group)
        return out

    cleaned = clean(tree)
    for c in layer_configs:  # configs missing from the tree → appended at root, bottom (layer_configs order)
        key = (c["layer_type"], c["layer_id"])
        if key not in seen:
            seen.add(key)
            cleaned.append({"layer_type": c["layer_type"], "layer_id": c["layer_id"]})
    return cleaned


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
