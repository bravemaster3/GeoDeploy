"""External map sources (WMS / XYZ raster, WFS vector) — display without ingesting.

These are third-party services the admin connects to; tiles/features are fetched from
the provider (raster tiles directly by the browser; WFS features through our same-origin
GeoJSON proxy to dodge CORS). The provider's own licence/attribution applies — always
surface the attribution string.
"""
import json

import httpx

DEFAULT_WMS_VERSION = "1.3.0"
DEFAULT_WFS_VERSION = "2.0.0"
DEFAULT_WMS_FORMAT = "image/png"
WFS_FEATURE_CAP = 5000  # safety cap for the proxy payload

_GEOM_MAP = {
    "point": "point", "multipoint": "point",
    "linestring": "line", "multilinestring": "line",
    "polygon": "polygon", "multipolygon": "polygon",
}


def kind_for(source_type: str) -> str:
    """xyz/wms render as raster tiles; wfs as vector features."""
    return "vector" if source_type == "wfs" else "raster"


def _join(url: str, query: str) -> str:
    return url + ("&" if "?" in url else "?") + query


def tile_url(source) -> str | None:
    """MapLibre raster `tiles[]` template for a raster source (None for vector).

    XYZ: the stored template as-is. WMS: a GetMap KVP request with the MapLibre
    `{bbox-epsg-3857}` token (MapLibre substitutes the tile bbox per request).
    """
    if source.source_type == "xyz":
        return source.url
    if source.source_type == "wms":
        version = source.version or DEFAULT_WMS_VERSION
        fmt = source.image_format or DEFAULT_WMS_FORMAT
        # EPSG:3857 has easting/northing axis order, so 1.3.0 `crs=` needs no axis swap.
        crs_param = "crs" if version >= "1.3" else "srs"
        query = (
            f"service=WMS&version={version}&request=GetMap"
            f"&layers={source.layer_name or ''}&styles="
            f"&format={fmt}&transparent=true"
            f"&{crs_param}=EPSG:3857&width=256&height=256&bbox={{bbox-epsg-3857}}"
        )
        return _join(source.url, query)
    return None


def features_url(source) -> str | None:
    """Same-origin GeoJSON proxy path for a vector (WFS) source (None for raster)."""
    if source.kind == "vector":
        return f"/api/data/sources/{source.id}/features.geojson"
    return None


def _wfs_getfeature_url(url: str, layer_name: str, version: str, limit: int, output_format: str) -> str:
    if version >= "2.0":
        query = (
            f"service=WFS&version={version}&request=GetFeature"
            f"&typeNames={layer_name}&count={limit}&outputFormat={output_format}"
        )
    else:
        query = (
            f"service=WFS&version={version}&request=GetFeature"
            f"&typeName={layer_name}&maxFeatures={limit}&outputFormat={output_format}"
        )
    return _join(url, query)


def _bbox_from_geojson(gj: dict) -> list | None:
    if isinstance(gj.get("bbox"), list) and len(gj["bbox"]) >= 4:
        b = gj["bbox"]
        return [b[0], b[1], b[2], b[3]]
    # Fall back to scanning coordinates of the returned features.
    xs, ys = [], []

    def walk(coords):
        if not coords:
            return
        if isinstance(coords[0], (int, float)):
            xs.append(coords[0]); ys.append(coords[1])
        else:
            for c in coords:
                walk(c)

    for f in gj.get("features", []):
        geom = (f or {}).get("geometry") or {}
        walk(geom.get("coordinates"))
    if xs and ys:
        return [min(xs), min(ys), max(xs), max(ys)]
    return None


async def probe_wfs(url: str, layer_name: str, version: str | None) -> dict:
    """Fetch one feature to validate the WFS and learn its geometry type + bbox.

    Tries WFS 2.0.0 then 1.1.0, and json output-format spellings. Raises ValueError
    with a readable message if nothing usable comes back.
    """
    versions = [version] if version else [DEFAULT_WFS_VERSION, "1.1.0"]
    last_err = "no response"
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        for ver in versions:
            for fmt in ("application/json", "json"):
                req_url = _wfs_getfeature_url(url, layer_name, ver, 1, fmt)
                try:
                    r = await client.get(req_url)
                    if r.status_code != 200:
                        last_err = f"HTTP {r.status_code}"
                        continue
                    gj = r.json()
                except Exception as exc:  # noqa: BLE001 — try the next combo
                    last_err = str(exc)
                    continue
                feats = gj.get("features")
                if not isinstance(feats, list):
                    last_err = "response was not GeoJSON (the layer may not support outputFormat=json)"
                    continue
                geom_type = None
                if feats:
                    gt = ((feats[0] or {}).get("geometry") or {}).get("type", "")
                    geom_type = _GEOM_MAP.get(gt.lower())
                return {
                    "version": ver,
                    "geometry_type": geom_type or "polygon",
                    "bbox": _bbox_from_geojson(gj),
                }
    raise ValueError(f"Could not read WFS features: {last_err}")


async def fetch_wfs_geojson(source, limit: int = WFS_FEATURE_CAP) -> dict:
    """Proxy: fetch GetFeature as GeoJSON for the portal/editor to render."""
    version = source.version or DEFAULT_WFS_VERSION
    last_err = "no response"
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        for fmt in ("application/json", "json"):
            req_url = _wfs_getfeature_url(source.url, source.layer_name or "", version, limit, fmt)
            try:
                r = await client.get(req_url)
                if r.status_code != 200:
                    last_err = f"HTTP {r.status_code}"
                    continue
                gj = r.json()
            except Exception as exc:  # noqa: BLE001
                last_err = str(exc)
                continue
            if isinstance(gj.get("features"), list):
                return gj
    raise ValueError(f"Could not fetch WFS features: {last_err}")
