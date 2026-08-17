"""Share links — the per-layer, tool-labelled URLs that make a GeoDeploy layer consumable
elsewhere (QGIS, GeoLibre, MapLibre/deck.gl, GDAL/DuckDB, STAC clients).

Every URL here already exists as a route; this module is the ONE place that knows which artifact
is the *right* one for a given layer (PostGIS vs GeoParquet vs tiled-to-PMTiles vs COG) and how a
human is supposed to paste it into their tool. The `/data/{vector,raster}/{id}/links` endpoints
serve it to the "Share links" panel in My Data; the STAC item (`routers/stac.py`) is the
machine-readable sibling — keep the two in sync when an artifact route changes.

All links are PUBLIC-surface URLs: they only resolve for a layer shared as `public` (the raster
`/cog` + `/tilejson` and the STAC catalog check `is_public`; the vector display routes also accept
a layer that is in a published portal). The endpoint reports `public` so the UI can say so.
"""
from . import martin as martin_svc
from . import titiler as titiler_svc


# Baked into a PUBLISHED portal's About page, whose HTML is written at publish time when the public
# origin isn't known. `portal_generator` passes this as `base`; the page's inline script swaps it for
# `location.origin` on load. Works for every link shape — including `/vsicurl/<origin>/…` and
# `pmtiles://<origin>/…`, where the origin sits mid-string and a root-relative URL would be wrong.
ORIGIN_TOKEN = "__GD_ORIGIN__"


def _link(id_, label, url, *, fmt, tools, hint, primary=False, download=False):
    return {"id": id_, "label": label, "url": url, "format": fmt, "tools": tools,
            "hint": hint, "primary": primary, "download": download}


def public_ref(layer) -> str:
    """The identifier a layer is addressed by in PUBLIC URLs: its stable `uid`, falling back to the
    integer id only for a row predating the uid migration. Never build a shareable URL from `id`
    directly: it is unique only within one layer kind and one database, so a restore or a move to
    another instance renumbers it and every published link points somewhere else (models.new_uid)."""
    return getattr(layer, "uid", None) or str(layer.id)


def stac_item_url(base: str, kind: str, layer) -> str:
    """`kind` is the STAC collection: 'vectors' | 'rasters'."""
    return f"{base}/api/stac/collections/{kind}/items/{kind[:-1]}-{public_ref(layer)}"


def _has_pmtiles(layer) -> bool:
    """A tiled GeoParquet layer — the case where a rendering link beats a feature service."""
    return (getattr(layer, "storage_backend", "postgis") != "postgis"
            and bool(getattr(layer, "pmtiles_key", None))
            and getattr(layer, "tile_status", None) == "ready")


def vector_links(layer, base: str) -> list[dict]:
    # OGC API - Features FIRST: it is the one standard every GIS reads natively (QGIS, ArcGIS Pro,
    # FME, anything on GDAL's OAPIF driver), it carries real attributes, and it works the same for
    # both storage backends. Tiles/PMTiles below are for RENDERING speed, not interchange.
    links: list[dict] = [
        # NOT primary, and NOT labelled for QGIS. A desktop client connects to the SERVICE and picks
        # a layer; pasting a COLLECTION url into QGIS's "Add OGC API - Features Layer" dialog
        # produces an empty list and no error, which is exactly what happened to the first person
        # who tried it — because this link was the promoted one and named QGIS first.
        _link("ogc-features", "OGC API - Features — this layer (for GDAL)",
              f"{base}/api/ogc/collections/vector-{public_ref(layer)}",
              fmt="OGC API - Features (GeoJSON)",
              tools=["GDAL/ogr2ogr", "Python", "R"],
              hint="For GDAL: ogr2ogr out.gpkg \"OAPIF:<this URL>\". QGIS and ArcGIS cannot connect "
                   "to a single collection — use the service URL below and pick this layer."),
        # Named for its SCOPE, not its role. "service endpoint" sat next to "this layer" and read as
        # a synonym, so it was copied expecting one dataset and opened the whole list. The scope is
        # not a leak — /api/ogc lists only layers explicitly shared as public (ogcapi._public_layers)
        # — but it is a surprise, and the label is where that gets settled.
        _link("ogc-service", "OGC API - Features — ALL your public layers", f"{base}/api/ogc",
              fmt="OGC API - Features landing page",
              tools=["QGIS", "ArcGIS Pro", "FME"],
              # Recommended UNLESS this layer has PMTiles — see the reorder at the end of the
              # function. For a multi-million-feature GeoParquet layer, paging OAPIF into QGIS is
              # minutes of waiting where the archive draws immediately.
              primary=not _has_pmtiles(layer),
              hint="Not just this layer: this is the service, and it lists every layer you have "
                   "shared publicly. It is what QGIS needs — Layer ▸ Add Layer ▸ Add OGC API - "
                   "Features Layer ▸ New ▸ paste this, then pick this layer from the list."),
        _link("ogc-items", "Features as GeoJSON (bbox-filtered, paged)",
              f"{base}/api/ogc/collections/vector-{public_ref(layer)}/items?limit=1000",
              fmt="GeoJSON", tools=["any HTTP client", "Python", "R"],
              hint="Add &bbox=minx,miny,maxx,maxy (WGS84) to filter, &offset= to page; follow the "
                   "rel=\"next\" link for the rest."),
    ]
    api = f"{base}/api/data/vector/{public_ref(layer)}"
    if getattr(layer, "storage_backend", "postgis") == "postgis":
        src = f"{layer.schema_name}.{layer.table_name}"
        links.append(_link(
            "tilejson", "TileJSON — vector tiles (fast rendering)", f"{api}/tilejson",
            fmt="TileJSON 3.0", tools=["QGIS", "GeoLibre", "MapLibre", "deck.gl"],
            hint="Pre-generalized tiles — best for drawing a big layer, not for attribute queries. "
                 "QGIS: Add Vector Tile Layer ▸ New. GeoLibre: Add data ▸ OGC API - Tiles (vector) "
                 "▸ paste as the tiles/metadata URL."))
        links.append(_link(
            "xyz-mvt", "XYZ vector tiles (MVT)",
            base + martin_svc.get_tile_url(layer.schema_name, layer.table_name),
            fmt="Mapbox Vector Tile", tools=["MapLibre", "OpenLayers"],
            hint=f"Add as a VECTOR-tile source (not 'XYZ tiles' raster). Source-layer name: '{src}'."))
    else:
        if getattr(layer, "pmtiles_key", None) and getattr(layer, "tile_status", None) == "ready":
            # THE ONE TO REACH FOR IN A DESKTOP GIS, and it goes first for a measured reason. The
            # `pmtiles` link below is the whole archive, which MapLibre reads a tile at a time but
            # GDAL does not: its PMTiles driver has no viewport, so QGIS's first question — how many
            # features? — walks every tile at the deepest zoom. A five-feature layer on this project
            # tiles to 2.17 million entries, so the archive path was slowest exactly where it looked
            # safest. This TileJSON points at a per-tile endpoint that reads one tile per request.
            links.append(_link(
                "tilejson", "TileJSON — vector tiles (fast rendering)", f"{api}/tilejson",
                fmt="TileJSON 3.0", tools=["QGIS", "GeoLibre", "MapLibre", "deck.gl", "OpenLayers"],
                primary=True,
                hint="QGIS: Layer ▸ Add Layer ▸ Add Vector Tile Layer ▸ New ▸ paste this as the "
                     "TileJSON URL. It carries the tile template, the layer's bounds and the real "
                     "zoom range, so only tiles that exist are ever requested. Tiles are "
                     "generalized per zoom — for full attributes use OGC API - Features."))
            links.append(_link(
                "xyz-mvt", "XYZ vector tiles (MVT)", f"{api}/tiles/{{z}}/{{x}}/{{y}}",
                fmt="Mapbox Vector Tile", tools=["MapLibre", "OpenLayers", "deck.gl"],
                hint="The tile template behind the TileJSON above, if your client wants it bare. "
                     "Add as a VECTOR-tile source, not as 'XYZ tiles' (that is for images)."))
            # The PLAIN https URL — no `pmtiles://`. That prefix is a protocol handler the MapLibre
            # GL JS library registers INTERNALLY; it is not part of any address a person pastes.
            # Every consumer-facing field expects the plain URL, GeoLibre's own PMTiles input
            # included (its DEFAULT_ARCHIVE_URL is `https://…/latest.pmtiles`), and QGIS/GDAL have
            # never understood the prefix. GeoDeploy's OWN portals still emit `pmtiles://…` inside
            # their MapLibre style — see portal_generator — which is correct and unrelated to this.
            links.append(_link(
                "pmtiles", "PMTiles archive (the whole file)", f"{api}/pmtiles",
                fmt="PMTiles (vector)", tools=["MapLibre", "GeoLibre", "GDAL", "pmtiles CLI"],
                hint="One file holding every tile — for MapLibre's pmtiles:// protocol, or for "
                     "copying the archive. No pmtiles:// prefix and no /vsicurl/ needed here. "
                     "GDAL 3.8+ CAN open it directly, but do not do that in QGIS: the driver has "
                     "no viewport and reads the entire archive to answer basic questions about it. "
                     "Use the TileJSON above instead."))
        links.append(_link(
            "features-geojson", "Viewport features (GeoDeploy native)",
            f"{api}/features.geojson?bbox=minx,miny,maxx,maxy&limit=50000",
            fmt="GeoJSON", tools=["GeoDeploy portals", "any HTTP client"],
            hint="What the portals themselves call. Prefer the OGC API - Features link above for "
                 "other tools — it is the same data, paged and standards-shaped."))
        links.append(_link(
            "features-arrow", "Features (GeoArrow IPC, by viewport)",
            f"{api}/features.arrow?bbox=minx,miny,maxx,maxy&limit=50000",
            fmt="Arrow IPC stream", tools=["deck.gl", "pyarrow", "DuckDB"],
            hint="Same viewport query, binary GeoArrow — what the portals themselves use."))
        if getattr(layer, "s3_key", None) and not str(layer.s3_key).rstrip("/").endswith(".parquet"):
            links.append(_link(
                "manifest", "GeoParquet dataset manifest", f"{api}/parquet/manifest.json",
                fmt="JSON", tools=["DuckDB", "GDAL/ogr2ogr", "pyarrow"],
                hint=f"Partition grid + file keys; each file is then at {api}/parquet/<key> "
                     "(HTTP Range supported)."))
    # A tiled layer LEADS with its TileJSON. These layers are the big ones — that is why they are
    # GeoParquet — and the honest first answer is the one that draws a screenful at a time. OGC API
    # - Features stays below it for full attributes and queries, which tiles cannot give: they are
    # generalized per zoom and clipped to tile boundaries.
    #
    # This used to promote the PMTiles archive instead, on the reasoning that one bounded download
    # beats paging OAPIF. That is true of MapLibre and false of every GDAL-based client, which reads
    # the archive whole — so the promoted link was the slow one for the tools this list names first.
    #
    # Guarded by `_has_pmtiles`, because a PostGIS layer publishes a `tilejson` link too and it is
    # NOT the recommended one there — `ogc-service` keeps that badge, and promoting the TileJSON to
    # the top would put the first link and the "recommended" mark on two different rows.
    if _has_pmtiles(layer):
        for i, l in enumerate(links):
            if l.get("id") == "tilejson":
                links.insert(0, links.pop(i))
                break

    links.append(_link(
        "stac", "STAC item (metadata + all assets)", stac_item_url(base, "vectors", layer),
        fmt="STAC 1.0 Item", tools=["QGIS 3.40+", "stac-browser", "pystac-client"],
        hint="The machine-readable record of this layer. The whole catalog is at "
             f"{base}/api/stac."))
    return links


def raster_links(layer, base: str, default_style: dict | None = None) -> list[dict]:
    api = f"{base}/api/data/raster/{public_ref(layer)}"
    ds = default_style or {}
    tile_url = base + titiler_svc.get_tile_url(
        layer.s3_key, colormap=ds.get("colormap"), rescale=ds.get("rescale"),
        algorithm=ds.get("algorithm"), zfactor=ds.get("zfactor"), bidx=ds.get("bidx"),
        color_classes=ds.get("color_classes"),
        colormap_reverse=bool(ds.get("colormap_reverse")),
        band_count=getattr(layer, "band_count", None))
    return [
        _link("tilejson", "TileJSON — raster tiles", f"{api}/tilejson",
              fmt="TileJSON 3.0", tools=["QGIS", "GeoLibre", "MapLibre"],
              hint="Carries the tile template AND the layer bounds, so 'zoom to layer' works "
                   "(a bare XYZ URL has no bounds).",
              primary=True),
        _link("wmts", "WMTS — for QGIS", f"{api}/wmts",
              fmt="WMTS 1.0.0 GetCapabilities", tools=["QGIS", "ArcGIS Pro", "OpenLayers"],
              hint="QGIS: Layer ▸ Add Layer ▸ Add WMS/WMTS Layer ▸ New ▸ paste this as the URL. "
                   "Use this rather than XYZ in QGIS — it carries the extent, so 'Zoom to Layer' "
                   "goes to the data instead of the whole world."),
        _link("xyz", "XYZ raster tiles", tile_url,
              fmt="PNG tiles", tools=["Leaflet", "OpenLayers", "QGIS (XYZ connection)"],
              hint="Rendered by TiTiler with this layer's saved styling (band/colormap/stretch). "
                   "A bare XYZ template carries no extent — prefer WMTS in QGIS, TileJSON in "
                   "GeoLibre/MapLibre."),
        _link("vsicurl", "COG for GDAL/QGIS (/vsicurl/)", f"/vsicurl/{api}/cog",
              fmt="Cloud-Optimized GeoTIFF", tools=["QGIS", "GDAL", "rasterio"],
              hint="Full pixel access without downloading — paste as a raster layer source."),
        _link("cog", "COG download", f"{api}/cog",
              fmt="GeoTIFF", tools=["any"], hint="The raw file (HTTP Range supported).",
              download=True),
        _link("stac", "STAC item (metadata + all assets)", stac_item_url(base, "rasters", layer),
              fmt="STAC 1.0 Item", tools=["QGIS 3.40+", "stac-browser", "pystac-client"],
              hint=f"The machine-readable record of this layer. The whole catalog is at "
                   f"{base}/api/stac."),
    ]


def request_base(request) -> str:
    """Absolute origin for the links, https-aware behind nginx (same rule as stac.py::_base)."""
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}"
