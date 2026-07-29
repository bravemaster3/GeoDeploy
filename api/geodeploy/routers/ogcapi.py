"""OGC API - Features (Part 1: Core) — the universal, standards-based way to read GeoDeploy's
vector data from any tool.

Why this exists alongside STAC and TileJSON:
  * **STAC** (`/api/stac`) is DISCOVERY — one item per LAYER, describing where its assets live.
  * **TileJSON / PMTiles** are RENDERING — pre-generalized tiles, no attribute queries, and only
    the MapLibre-family of clients speaks them (in GeoLibre it hides under
    "Add data ▸ OGC API - Tiles (vector)").
  * **This** is DATA ACCESS — every collection is one layer, every item one real feature with its
    attributes, filterable by bbox and paged. QGIS ("Layer ▸ Add Layer ▸ Add OGC API - Features"),
    ArcGIS Pro, FME, and anything on GDAL (the `OAPIF` driver) consume it natively, which makes it
    the widest-reach surface we have — and the standards gap versus GeoLens (notes: GeoLens leads on
    OGC/metadata standards).

Scope of the conformance we CLAIM (`CONFORMS` below): Core + GeoJSON only. No CRS negotiation
(everything is EPSG:4326 / OGC:CRS84), no CQL2, no transactions. Do not add a class here without
implementing it — over-claiming is exactly the bug this module was written to stop repeating.

Collections are the PUBLIC (`visibility='public'`), ready vector layers — the same opt-in that
governs the STAC catalog; nothing is exposed by default. Collection ids mirror the STAC item ids
(`vector-<layer id>`) so the two surfaces cross-reference cleanly.
"""
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from ..config import get_settings
from ..database import get_db
from ..models import VectorLayer

router = APIRouter(prefix="/ogc", tags=["ogc-api-features"])

CONFORMS = [
    "http://www.opengis.net/spec/ogcapi-features-1/1.0/conf/core",
    "http://www.opengis.net/spec/ogcapi-features-1/1.0/conf/geojson",
]
DEFAULT_LIMIT = 1000
MAX_LIMIT = 10000
GEOJSON = "application/geo+json"

# One clean ACAO on every response: these are public read surfaces that browsers, QGIS-in-WASM and
# GeoLibre must reach cross-origin. main.py's `_public_data_cors` also matches /api/ogc/* and simply
# re-sets the SAME header value (setitem, never append) — so there is exactly one ACAO, which is the
# duplicate-header trap the /tiles/ + /raster/ proxies hit. Don't "helpfully" add another here.
CORS = {"Access-Control-Allow-Origin": "*", "Cache-Control": "public, max-age=60"}


def _base(request: Request) -> str:
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}"


def _root(base: str) -> str:
    return f"{base}/api/ogc"


def _cid(layer) -> str:
    return f"vector-{layer.id}"


def _layer_id(cid: str) -> int | None:
    try:
        return int(cid.rsplit("-", 1)[1])
    except (IndexError, ValueError):
        return None


def _bbox(layer) -> list[float] | None:
    try:
        b = json.loads(layer.bbox) if layer.bbox else None
        return b if isinstance(b, list) and len(b) == 4 else None
    except ValueError:
        return None


def _srid(layer) -> int:
    """The table's SRID, from the stored CRS string. GeoDeploy-ingested tables are 4326; a table
    IMPORTED from an existing PostGIS keeps its own (e.g. EPSG:3006)."""
    raw = (layer.crs or "").upper().replace("EPSG:", "").strip()
    try:
        return int(raw)
    except ValueError:
        return 4326


def _q(ident: str) -> str:
    """Quote a SQL identifier (asyncpg cannot parameterise identifiers). Same rule as discover.py."""
    return '"' + str(ident).replace('"', '""') + '"'


def _parse_bbox(raw: str | None) -> list[float] | None:
    if not raw:
        return None
    try:
        parts = [float(v) for v in raw.split(",")]
    except ValueError:
        raise HTTPException(400, "Invalid bbox — expected minx,miny,maxx,maxy in WGS84.")
    if len(parts) == 6:                       # 3D bbox → drop the elevation ordinates
        parts = [parts[0], parts[1], parts[3], parts[4]]
    if len(parts) != 4:
        raise HTTPException(400, "Invalid bbox — expected 4 (or 6) numbers.")
    return parts


async def _public_layers(db: AsyncSession) -> list[VectorLayer]:
    result = await db.execute(select(VectorLayer).where(
        VectorLayer.status == "ready", VectorLayer.is_public == True))  # noqa: E712
    return list(result.scalars().all())


async def _get_layer(cid: str, db: AsyncSession) -> VectorLayer:
    lid = _layer_id(cid)
    if lid is None:
        raise HTTPException(404, "No such collection.")
    result = await db.execute(select(VectorLayer).where(VectorLayer.id == lid))
    layer = result.scalar_one_or_none()
    if not layer or layer.status != "ready" or not layer.is_public:
        raise HTTPException(404, "No such collection.")
    return layer


def _collection(layer, base: str) -> dict:
    b = _bbox(layer) or [-180, -90, 180, 90]
    cid = _cid(layer)
    doc = {
        "id": cid,
        "title": layer.name,
        "description": layer.abstract or f"{layer.name} — served by GeoDeploy.",
        "extent": {"spatial": {"bbox": [b], "crs": "http://www.opengis.net/def/crs/OGC/1.3/CRS84"}},
        "itemType": "feature",
        "crs": ["http://www.opengis.net/def/crs/OGC/1.3/CRS84"],
        "links": [
            {"rel": "self", "href": f"{_root(base)}/collections/{cid}", "type": "application/json"},
            {"rel": "items", "href": f"{_root(base)}/collections/{cid}/items", "type": GEOJSON,
             "title": f"{layer.name} — features"},
            {"rel": "root", "href": _root(base), "type": "application/json"},
            # Cross-reference to the discovery surface: same layer, richer asset list.
            {"rel": "describedby", "type": "application/json",
             "href": f"{base}/api/stac/collections/vectors/items/{cid}",
             "title": "STAC item (all assets for this layer)"},
        ],
    }
    if layer.license:
        doc["license"] = layer.license
    if layer.attribution:
        doc["attribution"] = layer.attribution
    if layer.keywords:
        doc["keywords"] = [k.strip() for k in layer.keywords.split(",") if k.strip()]
    return doc


# ── Landing page / conformance / collections ─────────────────────────────────────────────────

@router.get("")
async def landing(request: Request):
    base = _base(request)
    return JSONResponse({
        "title": "GeoDeploy — OGC API - Features",
        "description": "Publicly shared vector layers of this GeoDeploy instance, served as OGC "
                       "API - Features collections (GeoJSON, EPSG:4326).",
        "links": [
            {"rel": "self", "href": _root(base), "type": "application/json",
             "title": "This document"},
            {"rel": "service-desc", "href": f"{base}/api/openapi.json",
             "type": "application/vnd.oai.openapi+json;version=3.0", "title": "API definition"},
            {"rel": "conformance", "href": f"{_root(base)}/conformance", "type": "application/json",
             "title": "Conformance classes"},
            {"rel": "data", "href": f"{_root(base)}/collections", "type": "application/json",
             "title": "Collections"},
        ],
    }, headers=CORS)


@router.get("/conformance")
async def conformance():
    return JSONResponse({"conformsTo": CONFORMS}, headers=CORS)


@router.get("/collections")
async def collections(request: Request, db: AsyncSession = Depends(get_db)):
    base = _base(request)
    return JSONResponse({
        "collections": [_collection(l, base) for l in await _public_layers(db)],
        "links": [
            {"rel": "self", "href": f"{_root(base)}/collections", "type": "application/json"},
            {"rel": "root", "href": _root(base), "type": "application/json"},
        ],
    }, headers=CORS)


@router.get("/collections/{cid}")
async def collection(cid: str, request: Request, db: AsyncSession = Depends(get_db)):
    layer = await _get_layer(cid, db)
    return JSONResponse(_collection(layer, _base(request)), headers=CORS)


# ── Features ─────────────────────────────────────────────────────────────────────────────────

@router.get("/collections/{cid}/items")
async def items(cid: str, request: Request, bbox: str | None = None, limit: int = DEFAULT_LIMIT,
                offset: int = 0, db: AsyncSession = Depends(get_db)):
    """Features of a collection: GeoJSON, EPSG:4326, `bbox`-filtered and paged.

    `limit` is capped at MAX_LIMIT — a client that wants everything follows `rel="next"` (which is
    what QGIS/ogr2ogr do). `numberMatched` is best-effort: exact for a bbox query, the stored
    feature count for an unfiltered one, and omitted when counting would be too expensive."""
    layer = await _get_layer(cid, db)
    qb = _parse_bbox(bbox)
    limit = max(1, min(int(limit), MAX_LIMIT))
    offset = max(0, int(offset))

    if layer.storage_backend == "geoparquet":
        features, matched = await _geoparquet_items(layer, qb, limit, offset)
    else:
        features, matched = await _postgis_items(layer, qb, limit, offset)

    base = _base(request)
    items_url = f"{_root(base)}/collections/{cid}/items"
    q = f"limit={limit}" + (f"&bbox={bbox}" if bbox else "")
    links = [
        {"rel": "self", "href": f"{items_url}?{q}&offset={offset}", "type": GEOJSON},
        {"rel": "collection", "href": f"{_root(base)}/collections/{cid}", "type": "application/json"},
        {"rel": "root", "href": _root(base), "type": "application/json"},
    ]
    # A full page means "there may be more" — emit `next` even when numberMatched is unknown, so a
    # paging client (ogr2ogr, QGIS) can walk the whole collection.
    if len(features) == limit and (matched is None or offset + limit < matched):
        links.append({"rel": "next", "href": f"{items_url}?{q}&offset={offset + limit}",
                      "type": GEOJSON})
    if offset:
        links.append({"rel": "prev", "href": f"{items_url}?{q}&offset={max(0, offset - limit)}",
                      "type": GEOJSON})

    doc = {
        "type": "FeatureCollection",
        "features": features,
        "numberReturned": len(features),
        "timeStamp": datetime.now(timezone.utc).isoformat(),
        "links": links,
    }
    if matched is not None:
        doc["numberMatched"] = matched
    return JSONResponse(doc, media_type=GEOJSON, headers=CORS)


@router.get("/collections/{cid}/items/{fid}")
async def item(cid: str, fid: str, request: Request, db: AsyncSession = Depends(get_db)):
    """A single feature by its id. The id is the table's primary key for a PostGIS layer; for a
    file-backed (GeoParquet) layer it is an `id`-like column when the dataset has one — otherwise
    single-feature access isn't addressable and this 404s (the collection still pages fine)."""
    layer = await _get_layer(cid, db)
    if layer.storage_backend == "geoparquet":
        feature = await _geoparquet_item(layer, fid)
    else:
        feature = await _postgis_item(layer, fid)
    if feature is None:
        raise HTTPException(404, "No such feature.")
    base = _base(request)
    feature["links"] = [
        {"rel": "self", "href": f"{_root(base)}/collections/{cid}/items/{fid}", "type": GEOJSON},
        {"rel": "collection", "href": f"{_root(base)}/collections/{cid}", "type": "application/json"},
    ]
    return JSONResponse(feature, media_type=GEOJSON, headers=CORS)


# ── PostGIS backend ──────────────────────────────────────────────────────────────────────────

async def _pg_connect():
    import asyncpg
    settings = get_settings()
    # asyncpg wants the plain postgresql:// DSN, not SQLAlchemy's +asyncpg form.
    return await asyncpg.connect(settings.postgis_sync_dsn, timeout=15)


async def _pg_columns(conn, schema: str, table: str, geom_col: str) -> list[str]:
    rows = await conn.fetch(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = $1 AND table_name = $2 ORDER BY ordinal_position", schema, table)
    return [r["column_name"] for r in rows if r["column_name"] != geom_col]


def _pg_geom_col(layer) -> str:
    return layer.geometry_column or "geom"


def _pg_id_col(layer, cols: list[str]) -> str | None:
    idc = layer.id_column or ("id" if "id" in cols else None)
    return idc if idc in cols else None


def _pg_bbox_where(qb, srid: int, geom: str, params: list) -> str:
    """`&&` against a constant envelope transformed into the TABLE's SRID — index-usable (a
    per-row ST_Transform of the geometry would not be)."""
    params.extend(qb)
    n = len(params)
    env = (f"ST_MakeEnvelope(${n-3}::float8, ${n-2}::float8, ${n-1}::float8, ${n}::float8, 4326)")
    if srid != 4326:
        env = f"ST_Transform({env}, {srid})"
    return f"{_q(geom)} && {env}"


async def _postgis_items(layer, qb, limit: int, offset: int):
    geom = _pg_geom_col(layer)
    srid = _srid(layer)
    conn = await _pg_connect()
    try:
        cols = await _pg_columns(conn, layer.schema_name, layer.table_name, geom)
        idc = _pg_id_col(layer, cols)
        params: list = []
        where = ""
        if qb:
            where = "WHERE " + _pg_bbox_where(qb, srid, geom, params)
        tbl = f"{_q(layer.schema_name)}.{_q(layer.table_name)}"
        geom_out = _q(geom) if srid == 4326 else f"ST_Transform({_q(geom)}, 4326)"
        select_cols = ", ".join([f"ST_AsGeoJSON({geom_out}) AS __geom"] +
                                [_q(c) for c in cols]) if cols else \
            f"ST_AsGeoJSON({geom_out}) AS __geom"
        # A stable ORDER BY is what makes offset paging return each feature exactly once.
        order = f"ORDER BY {_q(idc)}" if idc else ""
        rows = await conn.fetch(
            f"SELECT {select_cols} FROM {tbl} {where} {order} LIMIT {limit} OFFSET {offset}",
            *params)

        matched = None
        if qb:
            try:
                matched = await conn.fetchval(f"SELECT count(*) FROM {tbl} {where}", *params)
            except Exception:
                matched = None
        elif layer.feature_count is not None:
            matched = layer.feature_count

        features = []
        for i, r in enumerate(rows):
            props = {c: _jsonable(r[c]) for c in cols}
            fid = props.get(idc) if idc else offset + i
            features.append({"type": "Feature", "id": fid,
                             "geometry": json.loads(r["__geom"]) if r["__geom"] else None,
                             "properties": props})
        return features, matched
    finally:
        await conn.close()


async def _postgis_item(layer, fid: str):
    geom = _pg_geom_col(layer)
    srid = _srid(layer)
    conn = await _pg_connect()
    try:
        cols = await _pg_columns(conn, layer.schema_name, layer.table_name, geom)
        idc = _pg_id_col(layer, cols)
        if not idc:
            return None
        tbl = f"{_q(layer.schema_name)}.{_q(layer.table_name)}"
        geom_out = _q(geom) if srid == 4326 else f"ST_Transform({_q(geom)}, 4326)"
        select_cols = ", ".join([f"ST_AsGeoJSON({geom_out}) AS __geom"] + [_q(c) for c in cols])
        # Compare as text so a uuid/text/int primary key all work with one query.
        row = await conn.fetchrow(
            f"SELECT {select_cols} FROM {tbl} WHERE {_q(idc)}::text = $1 LIMIT 1", str(fid))
        if not row:
            return None
        props = {c: _jsonable(row[c]) for c in cols}
        return {"type": "Feature", "id": props.get(idc),
                "geometry": json.loads(row["__geom"]) if row["__geom"] else None,
                "properties": props}
    finally:
        await conn.close()


def _jsonable(v):
    """asyncpg hands back native Python types; JSON-encode the ones json.dumps can't."""
    import decimal
    import uuid as _uuid
    if isinstance(v, (datetime,)):
        return v.isoformat()
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (_uuid.UUID,)):
        return str(v)
    if isinstance(v, (bytes, bytearray, memoryview)):
        return None            # blobs (incl. any stray geometry column) are not GeoJSON properties
    if hasattr(v, "isoformat"):   # date / time
        return v.isoformat()
    return v


# ── GeoParquet backend ───────────────────────────────────────────────────────────────────────

_ID_CANDIDATES = ("id", "fid", "gid", "objectid", "ogc_fid", "feature_id")


def _gp_id_key(names) -> str | None:
    """The column a file-backed layer's feature ids come from. GeoParquet has no primary key, so we
    look for the conventional id-ish name; without one, features are only addressable by page
    position (and `/items/{fid}` 404s — documented on the endpoint)."""
    for k in names or ():
        if str(k).lower() in _ID_CANDIDATES:
            return k
    return None


def _gp_columns(layer) -> list[str]:
    try:
        return [c["name"] for c in json.loads(layer.columns)] if layer.columns else []
    except (ValueError, KeyError, TypeError):
        return []


async def _geoparquet_items(layer, qb, limit: int, offset: int):
    """DuckDB over the prepared GeoParquet. The bbox filter uses the covering column (row-group +
    partition pruning), so this stays cheap on multi-million-feature layers. Paging is by
    LIMIT/OFFSET over that scan — deterministic for a given filter, not a snapshot isolation
    guarantee; a layer being re-prepared mid-crawl can shift rows."""
    from ..services import duckdb_engine
    fc = await run_in_threadpool(
        duckdb_engine.query_features_geojson, layer.s3_key, qb, limit, None, None, False, offset)
    feats = fc.get("features", [])
    for i, f in enumerate(feats):
        props = f.get("properties") or {}
        key = _gp_id_key(props.keys())
        f["id"] = props[key] if key else offset + i
    # Only claim a count we actually know: the stored total for an unfiltered read. Counting a
    # bbox query would mean a second full scan for no client benefit (`next` links suffice).
    matched = layer.feature_count if (qb is None and layer.feature_count is not None) else None
    return feats, matched


async def _geoparquet_item(layer, fid: str):
    from ..services import duckdb_engine
    key = _gp_id_key(_gp_columns(layer))
    if not key:
        return None
    feature = await run_in_threadpool(
        duckdb_engine.query_feature_by_id, layer.s3_key, key, str(fid))
    if feature:
        feature["id"] = (feature.get("properties") or {}).get(key, fid)
    return feature
