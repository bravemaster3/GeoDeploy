"""STAC catalog — the discovery half of GeoDeploy's data-access story (notes §0h-addendum).

GeoNode's equivalent is its catalog + GeoServer OGC services; GeoDeploy instead exposes a
lightweight STAC API over the layers the admin has explicitly opted into sharing
(`layer.is_public`). Data access itself is cloud-native: COG + XYZ raster tiles, Martin vector
tiles, and GeoParquet (manifest + partition files + viewport GeoJSON/GeoArrow) — all served by
routes that already exist; STAC just makes them discoverable by QGIS (native STAC since 3.40 /
the STAC plugin), stac-browser, pystac-client, and plain HTTP.

Design deviation from the original note: the catalog is generated DYNAMICALLY from SQLite per
request instead of static JSON files on MinIO — same near-zero weight at this scale (tens to
hundreds of layers), always in sync with the catalog (deletes/renames included), and no public
MinIO plumbing. Only `status='ready' AND is_public` layers are listed; everything here is
UNAUTHENTICATED by design (private layers simply do not appear — the API-token story for private
catalogs is a later increment).
"""
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import RasterLayer, VectorLayer
from ..services import martin as martin_svc
from ..services import titiler as titiler_svc

router = APIRouter(prefix="/stac", tags=["stac"])

STAC_VERSION = "1.0.0"
CONFORMS = [
    "https://api.stacspec.org/v1.0.0/core",
    "https://api.stacspec.org/v1.0.0/collections",
    "https://api.stacspec.org/v1.0.0/item-search",
    "https://api.stacspec.org/v1.0.0/ogcapi-features",
    "http://www.opengis.net/spec/ogcapi-features-1/1.0/conf/core",
    "http://www.opengis.net/spec/ogcapi-features-1/1.0/conf/geojson",
]
COLLECTIONS = {
    "vectors": "Vector layers (PostGIS-served tiles and GeoParquet datasets)",
    "rasters": "Raster layers (Cloud-Optimized GeoTIFFs)",
}


def _base(request: Request) -> str:
    """Absolute origin for hrefs (STAC requires absolute links). Respects the Host header nginx
    forwards; scheme from X-Forwarded-Proto when present (TLS terminates at the proxy)."""
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}"


def _bbox(layer) -> list[float] | None:
    try:
        b = json.loads(layer.bbox) if layer.bbox else None
        return b if isinstance(b, list) and len(b) == 4 else None
    except Exception:
        return None


def _bbox_geometry(b: list[float]) -> dict:
    x0, y0, x1, y1 = b
    return {"type": "Polygon",
            "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}


def _dt(value) -> str:
    if isinstance(value, datetime):
        return value.replace(tzinfo=value.tzinfo or timezone.utc).isoformat()
    return datetime.now(timezone.utc).isoformat()


def _common_properties(layer) -> dict:
    props = {"datetime": _dt(layer.updated_at or layer.created_at), "title": layer.name}
    if layer.abstract:
        props["description"] = layer.abstract
    if layer.keywords:
        props["keywords"] = [k.strip() for k in layer.keywords.split(",") if k.strip()]
    if layer.attribution:
        props["attribution"] = layer.attribution
    return props


def _vector_assets(layer, base: str) -> dict:
    assets = {}
    if layer.storage_backend == "geoparquet" and layer.s3_key:
        prefixed = not layer.s3_key.rstrip("/").endswith(".parquet")
        if prefixed:
            assets["manifest"] = {
                "href": f"{base}/api/data/vector/{layer.id}/parquet/manifest.json",
                "type": "application/json",
                "title": "GeoParquet dataset manifest (partition grid + file list)",
                "description": "Spatially partitioned GeoParquet: fetch this manifest for the "
                               "grid and the per-cell file keys, then read "
                               f"{base}/api/data/vector/{layer.id}/parquet/<key> "
                               "(HTTP Range requests supported — DuckDB/GDAL friendly).",
                "roles": ["metadata"],
            }
        assets["features-geojson"] = {
            "href": f"{base}/api/data/vector/{layer.id}/features.geojson",
            "type": "application/geo+json",
            "title": "Viewport features (GeoJSON; ?bbox=minx,miny,maxx,maxy&limit=N)",
            "roles": ["data"],
        }
        assets["features-arrow"] = {
            "href": f"{base}/api/data/vector/{layer.id}/features.arrow",
            "type": "application/vnd.apache.arrow.stream",
            "title": "Viewport features (GeoArrow IPC; ?bbox=&limit=)",
            "roles": ["data"],
        }
        if layer.pmtiles_key and layer.tile_status == "ready":
            assets["pmtiles"] = {
                "href": f"{base}/api/data/vector/{layer.id}/pmtiles",
                "type": "application/vnd.pmtiles",
                "title": "PMTiles archive (vector tiles, HTTP Range)",
                "roles": ["tiles"],
            }
    else:  # PostGIS-backed → Martin XYZ vector tiles
        assets["vector-tiles"] = {
            "href": base + martin_svc.get_tile_url(layer.schema_name, layer.table_name),
            "type": "application/vnd.mapbox-vector-tile",
            "title": "XYZ vector tiles (paste as a Vector Tiles connection in QGIS)",
            "roles": ["tiles"],
        }
    return assets


def _raster_assets(layer, base: str) -> dict:
    default = json.loads(layer.default_style) if layer.default_style else {}
    tile_url = titiler_svc.get_tile_url(
        layer.s3_key,
        colormap=default.get("colormap"), rescale=default.get("rescale"),
        algorithm=default.get("algorithm"), zfactor=default.get("zfactor"),
        bidx=default.get("bidx"),
    )
    return {
        "cog": {
            "href": f"{base}/api/data/raster/{layer.id}/cog",
            "type": "image/tiff; application=geotiff; profile=cloud-optimized",
            "title": "Cloud-Optimized GeoTIFF (HTTP Range — /vsicurl/ in QGIS/GDAL)",
            "roles": ["data"],
        },
        "tiles": {
            "href": base + tile_url,
            "type": "image/png",
            "title": "XYZ raster tiles (paste as an XYZ connection in QGIS)",
            "roles": ["tiles"],
        },
    }


def _item(layer, kind: str, base: str) -> dict:
    b = _bbox(layer)
    item = {
        "type": "Feature",
        "stac_version": STAC_VERSION,
        "id": f"{kind[:-1]}-{layer.id}",  # vectors → vector-<id>
        "collection": kind,
        "geometry": _bbox_geometry(b) if b else None,
        "bbox": b,
        "properties": _common_properties(layer),
        "license": layer.license or "proprietary",
        "assets": _vector_assets(layer, base) if kind == "vectors" else _raster_assets(layer, base),
        "links": [
            {"rel": "self", "href": f"{base}/api/stac/collections/{kind}/items/{kind[:-1]}-{layer.id}",
             "type": "application/geo+json"},
            {"rel": "collection", "href": f"{base}/api/stac/collections/{kind}", "type": "application/json"},
            {"rel": "root", "href": f"{base}/api/stac", "type": "application/json"},
        ],
    }
    return item


async def _public_layers(db: AsyncSession, kind: str):
    model = VectorLayer if kind == "vectors" else RasterLayer
    result = await db.execute(select(model).where(model.status == "ready",
                                                  model.is_public == True))  # noqa: E712
    return result.scalars().all()


@router.get("")
async def stac_root(request: Request):
    base = _base(request)
    return {
        "type": "Catalog",
        "stac_version": STAC_VERSION,
        "id": "geodeploy",
        "title": "GeoDeploy data catalog",
        "description": "Publicly shared layers of this GeoDeploy instance. Assets are "
                       "cloud-native: COG, XYZ tiles, and GeoParquet over HTTP Range.",
        "conformsTo": CONFORMS,
        "links": [
            {"rel": "self", "href": f"{base}/api/stac", "type": "application/json"},
            {"rel": "root", "href": f"{base}/api/stac", "type": "application/json"},
            {"rel": "conformance", "href": f"{base}/api/stac/conformance", "type": "application/json"},
            {"rel": "data", "href": f"{base}/api/stac/collections", "type": "application/json"},
            {"rel": "search", "href": f"{base}/api/stac/search", "type": "application/geo+json",
             "method": "GET"},
            *[{"rel": "child", "href": f"{base}/api/stac/collections/{cid}", "type": "application/json",
               "title": title} for cid, title in COLLECTIONS.items()],
        ],
    }


@router.get("/conformance")
async def stac_conformance():
    return {"conformsTo": CONFORMS}


async def _collection(cid: str, request: Request, db: AsyncSession) -> dict:
    if cid not in COLLECTIONS:
        raise HTTPException(404, "No such collection.")
    base = _base(request)
    layers = await _public_layers(db, cid)
    boxes = [b for b in (_bbox(l) for l in layers) if b]
    extent = ([min(b[0] for b in boxes), min(b[1] for b in boxes),
               max(b[2] for b in boxes), max(b[3] for b in boxes)]
              if boxes else [-180, -90, 180, 90])
    times = [l.created_at for l in layers if l.created_at]
    return {
        "type": "Collection",
        "stac_version": STAC_VERSION,
        "id": cid,
        "title": COLLECTIONS[cid],
        "description": COLLECTIONS[cid],
        "license": "various",
        "extent": {
            "spatial": {"bbox": [extent]},
            "temporal": {"interval": [[_dt(min(times)) if times else None, None]]},
        },
        "links": [
            {"rel": "self", "href": f"{base}/api/stac/collections/{cid}", "type": "application/json"},
            {"rel": "root", "href": f"{base}/api/stac", "type": "application/json"},
            {"rel": "items", "href": f"{base}/api/stac/collections/{cid}/items",
             "type": "application/geo+json"},
        ],
    }


@router.get("/collections")
async def stac_collections(request: Request, db: AsyncSession = Depends(get_db)):
    base = _base(request)
    return {
        "collections": [await _collection(cid, request, db) for cid in COLLECTIONS],
        "links": [
            {"rel": "self", "href": f"{base}/api/stac/collections", "type": "application/json"},
            {"rel": "root", "href": f"{base}/api/stac", "type": "application/json"},
        ],
    }


@router.get("/collections/{cid}")
async def stac_collection(cid: str, request: Request, db: AsyncSession = Depends(get_db)):
    return await _collection(cid, request, db)


@router.get("/collections/{cid}/items")
async def stac_items(cid: str, request: Request, db: AsyncSession = Depends(get_db)):
    if cid not in COLLECTIONS:
        raise HTTPException(404, "No such collection.")
    base = _base(request)
    layers = await _public_layers(db, cid)
    return {
        "type": "FeatureCollection",
        "features": [_item(l, cid, base) for l in layers],
        "links": [
            {"rel": "self", "href": f"{base}/api/stac/collections/{cid}/items",
             "type": "application/geo+json"},
            {"rel": "root", "href": f"{base}/api/stac", "type": "application/json"},
        ],
    }


@router.get("/collections/{cid}/items/{item_id}")
async def stac_item(cid: str, item_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    if cid not in COLLECTIONS:
        raise HTTPException(404, "No such collection.")
    try:
        layer_id = int(item_id.rsplit("-", 1)[1])
    except (IndexError, ValueError):
        raise HTTPException(404, "No such item.")
    model = VectorLayer if cid == "vectors" else RasterLayer
    result = await db.execute(select(model).where(model.id == layer_id))
    layer = result.scalar_one_or_none()
    if not layer or layer.status != "ready" or not layer.is_public:
        raise HTTPException(404, "No such item.")
    return _item(layer, cid, _base(request))


@router.get("/search")
async def stac_search(request: Request, bbox: str | None = None, collections: str | None = None,
                      limit: int = 100, db: AsyncSession = Depends(get_db)):
    """Minimal GET item search (bbox + collections + limit) — enough for QGIS's STAC client."""
    base = _base(request)
    wanted = [c.strip() for c in collections.split(",")] if collections else list(COLLECTIONS)
    qb = None
    if bbox:
        try:
            qb = [float(v) for v in bbox.split(",")][:4]
        except ValueError:
            raise HTTPException(400, "Invalid bbox.")
    features = []
    for cid in wanted:
        if cid not in COLLECTIONS:
            continue
        for layer in await _public_layers(db, cid):
            b = _bbox(layer)
            if qb and b and not (b[0] <= qb[2] and b[2] >= qb[0] and b[1] <= qb[3] and b[3] >= qb[1]):
                continue
            features.append(_item(layer, cid, base))
            if len(features) >= max(1, min(limit, 1000)):
                break
    return {"type": "FeatureCollection", "features": features,
            "links": [{"rel": "root", "href": f"{base}/api/stac", "type": "application/json"}]}
