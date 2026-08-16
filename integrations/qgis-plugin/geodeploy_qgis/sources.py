"""Which URL to hand QGIS for a given layer — the plugin's one real piece of judgement.

A GeoDeploy layer is published through several surfaces at once, and they are not interchangeable:

* **PMTiles** is one pre-generalized archive, opened through the ordinary vector-layer path on
  GDAL 3.8+ (with `/vsicurl/` — see below). Geometry is generalized per zoom, features are clipped
  to tile boundaries, and attributes are only what the tiles carry.
* **OGC API - Features** is the DATA: full attributes, exact geometry. QGIS has read it natively
  since 3.16, and its provider is VIEWPORT-DRIVEN — it asks the server for the extent on screen.
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
        tiles = (layer.get("tile_url") or "").strip()
        if tiles and not prefer_attributes:
            # Relative to the instance (that is how the API returns it), and QGIS's XYZ provider
            # wants the template URL-encoded inside the connection string.
            url = tiles if tiles.startswith("http") else f"{base}{tiles}"
            return {
                "kind": "xyz",
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

    tiled = bool(layer.get("pmtiles_key")) or layer.get("tile_status") == "ready"
    if tiled and not prefer_attributes and gdal_supports_pmtiles():
        return {
            "kind": "pmtiles",
            # `/vsicurl/` is not optional. Handed the bare URL, GDAL looks for a file of that name
            # on disk and fails with "does not exist in the file system" — it says so itself, and
            # suggests this prefix. It is also what QGIS's own Add Vector Layer builds when you give
            # it an HTTP(S) source, which is why adding one by hand worked where this did not.
            "uri": f"/vsicurl/{base}/api/data/vector/{ref}/pmtiles",
            "provider": "ogr",
            "why": "one pre-generalized archive, read by range request instead of re-queried",
        }

    # OAPIF is addressed by COLLECTION here, not by the service: QGIS's own dialog needs the service
    # and then asks the user to pick, but a URI built in code names the collection directly.
    return {
        "kind": "oapif",
        "uri": f"url='{base}/api/ogc' typename='vector-{ref}'",
        "provider": "OAPIF",
        "why": "full attributes and exact geometry, paged by the server",
    }


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
