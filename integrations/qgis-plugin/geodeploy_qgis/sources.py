"""Which URL to hand QGIS for a given layer — the plugin's one real piece of judgement.

A GeoDeploy layer is published through several surfaces at once, and they are not interchangeable:

* **PMTiles** is one pre-generalized archive, opened through the ordinary vector-layer path on
  GDAL 3.8+ (with `/vsicurl/` — see below). Geometry is generalized per zoom, features are clipped
  to tile boundaries, and attributes are only what the tiles carry.
* **Vector tiles (MVT)** are the fast way to DRAW a PostGIS layer: generalized per zoom, cut
  server-side, fetched for the viewport. A 350k-feature road layer draws like a small one.
* **OGC API - Features** is the DATA: full attributes, exact geometry. QGIS has read it natively
  since 3.16, and its provider is VIEWPORT-DRIVEN — it asks the server for the extent on screen.
  Correct and complete, and slow enough on a large layer to read as broken — which is why it is no
  longer the default.
* **COG** is a raster's real pixel values, read by range request through `/vsicurl/` — and
  rendered by QGIS's own defaults, NOT by GeoDeploy's styling.
* **Raster tiles** are the same raster as the portal draws it: colormap, stretch and band choice
  baked in by the server. A picture, not measurements.

A correction worth stating plainly, because the first version of this file got it wrong and the
error is easy to repeat: **PMTiles being fast in MapLibre does not make it fast in QGIS.** A web map
fetches only the tiles under the viewport. OGR does not — it opens the archive as an ordinary
dataset and reads every feature at the archive's zoom level, with no notion of a viewport. So
PMTiles is one bounded download with no per-pan server round-trip, while OAPIF asks the server again
on every pan but only ever for what is visible. Which of those is "faster" depends on the layer and
on how the user moves around it, and neither is universally right.

So the rule below is a default, not a verdict, and the caller can override it.
"""
from __future__ import annotations

from urllib.parse import quote

# GDAL gained its PMTiles driver in 3.8. Below that the archive cannot be opened at all, so the
# choice is not a preference — it is availability. Checked at runtime rather than assumed from the
# QGIS version, because the two do not move together.
_PMTILES_MIN_GDAL = (3, 8)


def gdal_supports_pmtiles() -> bool:
    try:
        from osgeo import gdal
    except ImportError:      # pragma: no cover - QGIS always ships GDAL
        return False
    try:
        parts = gdal.__version__.split(".")
        version = (int(parts[0]), int(parts[1]))
    except (AttributeError, IndexError, ValueError):
        return False
    return version >= _PMTILES_MIN_GDAL


def _link(layer: dict, link_id: str) -> str | None:
    """A URL from the layer's own `links` list, which the public index provides.

    Asking the instance beats deriving: it knows which surfaces it actually serves, and the list is
    the same one the share-links dialog shows a human.
    """
    for link in (layer.get("links") or []):
        if isinstance(link, dict) and link.get("id") == link_id and link.get("url"):
            return str(link["url"])
    return None


def describe(layer: dict, prefer_attributes: bool = False) -> dict | None:
    """`{kind, uri, provider, why}` for the best source, or None when there is nothing to add.

    `layer` is a row from the instance's own listing (public index or authenticated list), so this
    works with or without a credential.
    """
    base = (layer.get("_base") or "").rstrip("/")
    ref = layer.get("uid") or layer.get("id")
    if not base or ref is None:
        return None

    if layer.get("layer_type") == "raster" or layer.get("kind") == "raster":
        # THE COG IS THE DATA; THE TILES ARE THE PICTURE.
        #
        # Opening the COG gives real pixel values — what analysis needs — but QGIS then renders it
        # with ITS defaults, so a raster carefully coloured in GeoDeploy arrives as a grey stretch
        # and looks unstyled. The tile URL is the opposite: the server bakes the colormap, rescale,
        # band selection and hillshade into the image, so it looks exactly like the portal, but the
        # pixels are display colours and the values are gone.
        #
        # Neither is "correct", so the same switch that chooses attributes over speed for a vector
        # chooses values over appearance here.
        # THE SERVER PUBLISHES A TILEJSON FOR EXACTLY THIS, and its own hint says why: it "carries
        # the tile template AND the layer bounds, so 'zoom to layer' works (a bare XYZ URL has no
        # bounds)". Both problems this path had, answered by the instance rather than by us.
        #
        # It is also the only styled source an ANONYMOUS caller can use: the public index omits
        # `tile_url`, so without this a public raster fell back to the COG and arrived unstyled.
        # WMTS FIRST, because QGIS speaks it natively and the instance labels the link "for QGIS".
        #
        # An XYZ layer is a bare tile template: QGIS knows no extent and no zoom range, so it asks
        # for z0/z1/z2 tiles that miss the raster entirely, retries each three times, and fills the
        # log with failures while showing nothing. The WMTS capabilities carry the layer's
        # WGS84BoundingBox and its tile matrix set, so QGIS asks only for tiles that exist.
        wmts = _link(layer, "wmts") or f"{base}/api/data/raster/{ref}/wmts"
        if wmts and not prefer_attributes:
            return {
                "kind": "wmts",
                "wmts_url": wmts,
                "provider": "wms",      # WMTS is served through the WMS provider
                "why": "server-rendered tiles with the layer's own bounds, coloured as GeoDeploy draws it",
            }

        tilejson = _link(layer, "tilejson") or f"{base}/api/data/raster/{ref}/tilejson"
        if tilejson and not prefer_attributes:
            return {
                "kind": "tilejson",
                "tilejson_url": tilejson,
                "provider": "wms",
                "why": "server-rendered tiles, coloured exactly as GeoDeploy draws it",
            }

        tiles = (layer.get("tile_url") or "").strip()
        if tiles and not prefer_attributes:
            # Relative to the instance (that is how the API returns it), and QGIS's XYZ provider
            # wants the template URL-encoded inside the connection string.
            url = tiles if tiles.startswith("http") else f"{base}{tiles}"
            return {
                "kind": "xyz",
                # The TEMPLATE, unencoded. The provider URI is assembled by QgsDataSourceUri in
                # `plugin._build_layer`, because hand-rolling the percent-encoding of a URL that
                # itself carries a query string (`?url=s3://…&rescale=…`) is the one part of this
                # path that was never verified — and the server side provably works.
                "tile_url": url,
                "uri": f"type=xyz&url={quote(url, safe='')}&zmin=0&zmax=22",
                "provider": "wms",          # QGIS serves XYZ through the WMS provider
                "why": "server-rendered tiles, coloured exactly as GeoDeploy draws it",
            }
        return {
            "kind": "cog",
            "uri": f"/vsicurl/{base}/api/data/raster/{ref}/cog",
            "provider": "gdal",
            "why": ("the Cloud-Optimized GeoTIFF itself — real pixel values, drawn with QGIS's own "
                    "defaults rather than GeoDeploy's styling"),
        }

    # A tiled layer says so in three different ways depending on who is asking: the authenticated
    # row carries `pmtiles_key`/`tile_status`, and the PUBLIC index carries neither — it advertises
    # the archive as a link instead. Checking only the fields meant an anonymous caller silently
    # got the slow path for a layer that had been tiled precisely to be fast.
    pmtiles_link = _link(layer, "pmtiles")
    tiled = bool(layer.get("pmtiles_key")) or layer.get("tile_status") == "ready" or bool(pmtiles_link)
    if tiled and not prefer_attributes and gdal_supports_pmtiles():
        return {
            "kind": "pmtiles",
            # `/vsicurl/` is not optional. Handed the bare URL, GDAL looks for a file of that name
            # on disk and fails with "does not exist in the file system" — it says so itself, and
            # suggests this prefix. It is also what QGIS's own Add Vector Layer builds when you give
            # it an HTTP(S) source, which is why adding one by hand worked where this did not.
            "uri": "/vsicurl/" + (pmtiles_link or f"{base}/api/data/vector/{ref}/pmtiles"),
            "provider": "ogr",
            "why": "one pre-generalized archive, read by range request instead of re-queried",
        }

    # POSTGIS: MARTIN'S VECTOR TILES, not OGC API - Features.
    #
    # Both are offered; they are not close. Tiles are generalized per zoom, cut server-side and
    # fetched for the viewport, so a 350k-feature road layer draws as fast as a small one. OAPIF
    # asks the server for features and pages through them — exact and complete, and slow enough on
    # a large layer that it reads as broken. Defaulting to the slower one was simply wrong.
    #
    # The trade is real and is what the checkbox is for: tiles carry generalized geometry and only
    # the attributes the tiles hold, so "prefer the real data" still gets OAPIF.
    mvt = _link(layer, "xyz-mvt")
    if mvt and not prefer_attributes:
        return {
            "kind": "vector-tiles",
            "uri": mvt,
            "source_layer": layer.get("source_layer") or _mvt_source_layer(layer),
            "provider": "vectortile",
            "why": "vector tiles, generalized per zoom and fetched for the view",
        }

    # OAPIF is addressed by COLLECTION here, not by the service: QGIS's own dialog needs the service
    # and then asks the user to pick, but a URI built in code names the collection directly.
    why = "full attributes and exact geometry, paged by the server"
    if not prefer_attributes and (layer.get("storage_backend") == "geoparquet"
                                  or layer.get("kind") == "vector"):
        # The one remaining slow default, and it is slow because the layer has no fast surface yet
        # — not because a faster one was passed over. Say so, since tiling is one click away.
        why += " — this layer is not tiled, so there is no faster source; tile it in My Data"
    return {
        "kind": "oapif",
        "uri": f"url='{base}/api/ogc' typename='vector-{ref}'",
        "provider": "OAPIF",
        "why": why,
    }


def _mvt_source_layer(layer: dict) -> str | None:
    """The layer name inside the tiles: Martin names it `<schema>.<table>`."""
    schema, table = layer.get("schema_name"), layer.get("table_name")
    return f"{schema}.{table}" if schema and table else None


def alternatives(layer: dict) -> list[dict]:
    """Every source this layer offers, best first — for a UI that lets the user choose."""
    out = []
    primary = describe(layer)
    if primary:
        out.append(primary)
    other = describe(layer, prefer_attributes=True)
    if other and (not primary or other["kind"] != primary["kind"]):
        out.append(other)
    return out
