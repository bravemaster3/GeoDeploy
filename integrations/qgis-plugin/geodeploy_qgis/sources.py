"""Which URL to hand QGIS for a given layer — the plugin's one real piece of judgement.

A GeoDeploy layer is published through several surfaces at once, and they are not interchangeable:

* **PMTiles** is the fastest way to DRAW a large vector layer — pre-tiled, range-requested, no
  per-pan query — and QGIS opens the archive through the ordinary vector-layer path on GDAL 3.8+.
  It is a *rendering* format: geometry is generalized per zoom, features are clipped to tile
  boundaries, and attributes are only what the tiles carry.
* **OGC API - Features** is the DATA: full attributes, exact geometry, paged. QGIS has read it
  natively since 3.16. For millions of features it is slow, which is exactly when PMTiles wins.
* **COG** is a raster, read by range request through `/vsicurl/`.

So the rule below is "fastest thing that answers the question being asked", and the caller can
override it — a user who wants attributes on a 2-million-feature layer should be able to say so and
wait.
"""
from __future__ import annotations

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
        return {
            "kind": "cog",
            "uri": f"/vsicurl/{base}/api/data/raster/{ref}/cog",
            "provider": "gdal",
            "why": "the Cloud-Optimized GeoTIFF itself, read by range request",
        }

    tiled = bool(layer.get("pmtiles_key")) or layer.get("tile_status") == "ready"
    if tiled and not prefer_attributes and gdal_supports_pmtiles():
        return {
            "kind": "pmtiles",
            "uri": f"{base}/api/data/vector/{ref}/pmtiles",
            "provider": "ogr",
            "why": "pre-tiled, so a large layer draws immediately (generalized per zoom)",
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
