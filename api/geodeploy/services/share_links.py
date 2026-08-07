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
    directly — ids get reused after a delete (models.new_uid)."""
    return getattr(layer, "uid", None) or str(layer.id)


def stac_item_url(base: str, kind: str, layer) -> str:
    """`kind` is the STAC collection: 'vectors' | 'rasters'."""
    return f"{base}/api/stac/collections/{kind}/items/{kind[:-1]}-{public_ref(layer)}"


def vector_links(layer, base: str) -> list[dict]:
    # OGC API - Features FIRST: it is the one standard every GIS reads natively (QGIS, ArcGIS Pro,
    # FME, anything on GDAL's OAPIF driver), it carries real attributes, and it works the same for
    # both storage backends. Tiles/PMTiles below are for RENDERING speed, not interchange.
    links: list[dict] = [
        _link("ogc-features", "OGC API - Features — this layer",
              f"{base}/api/ogc/collections/vector-{public_ref(layer)}",
              fmt="OGC API - Features (GeoJSON)",
              tools=["QGIS", "ArcGIS Pro", "FME", "GDAL/ogr2ogr"],
              hint="GDAL/ogr2ogr: use OAPIF:<this URL>. In QGIS you connect to the SERVICE url "
                   "below and pick the layer from the list.",
              primary=True),
        _link("ogc-service", "OGC API - Features — service endpoint", f"{base}/api/ogc",
              fmt="OGC API - Features landing page",
              tools=["QGIS", "ArcGIS Pro", "FME"],
              hint="QGIS: Layer ▸ Add Layer ▸ Add OGC API - Features Layer ▸ New ▸ paste this as "
                   "the URL, then choose this layer from the collection list."),
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
            # The PLAIN https URL — no `pmtiles://`. That prefix is a protocol handler the MapLibre
            # GL JS library registers INTERNALLY; it is not part of any address a person pastes.
            # Every consumer-facing field expects the plain URL, GeoLibre's own PMTiles input
            # included (its DEFAULT_ARCHIVE_URL is `https://…/latest.pmtiles`), and QGIS/GDAL have
            # never understood the prefix. GeoDeploy's OWN portals still emit `pmtiles://…` inside
            # their MapLibre style — see portal_generator — which is correct and unrelated to this.
            links.append(_link(
                "pmtiles", "PMTiles archive (fast rendering)", f"{api}/pmtiles",
                fmt="PMTiles (vector)", tools=["GeoLibre", "MapLibre", "download", "GDAL"],
                hint="Paste as-is. A MapLibre style source needs the pmtiles:// protocol prefix "
                     "added in code, but no UI field wants it. GDAL builds with PMTiles support "
                     "read it via /vsicurl/. To load this layer INTO QGIS, prefer the OGC API - "
                     "Features link above \u2014 PMTiles is a rendering format."))
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
        band_count=getattr(layer, "band_count", None))
    return [
        _link("tilejson", "TileJSON — raster tiles", f"{api}/tilejson",
              fmt="TileJSON 3.0", tools=["QGIS", "GeoLibre", "MapLibre"],
              hint="Carries the tile template AND the layer bounds, so 'zoom to layer' works "
                   "(a bare XYZ URL has no bounds).",
              primary=True),
        _link("xyz", "XYZ raster tiles", tile_url,
              fmt="PNG tiles", tools=["QGIS (XYZ connection)", "Leaflet", "OpenLayers"],
              hint="Rendered by TiTiler with this layer's saved styling (band/colormap/stretch)."),
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
