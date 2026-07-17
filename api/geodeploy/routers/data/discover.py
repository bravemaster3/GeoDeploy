"""Discover + import data that ALREADY exists in the connected PostGIS / object store.

Importing registers a layer in GeoDeploy's catalog (a vector_layers / raster_layers row) that
points at the existing table/object — it does NOT copy or re-upload the data. Use this when you
connect GeoDeploy to a database/bucket that already has data (yours or someone else's).
"""
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import get_settings
from ...database import get_db
# Discover/import mutates the catalog (and can LOAD data via the CSV path) and exposes raw
# DB/bucket listings — editor-gated across the board, not viewer material.
from ...deps import require_scope
from ...models import RasterLayer, UploadJob, User, VectorLayer
from ...schemas import JobStatus
from ...services import martin as martin_svc
from ...services import cog_converter

router = APIRouter(prefix="/data/discover", tags=["discover"])

# Schemas PostGIS ships with — never offered for import.
_SYS_SCHEMAS = ["information_schema", "pg_catalog", "topology", "tiger", "tiger_data"]

_GEOM_MAP = {
    "POINT": "point", "MULTIPOINT": "point",
    "LINESTRING": "line", "MULTILINESTRING": "line",
    "POLYGON": "polygon", "MULTIPOLYGON": "polygon",
}


def _q(ident: str) -> str:
    """Safely quote a SQL identifier (asyncpg can't parameterise identifiers)."""
    return '"' + str(ident).replace('"', '""') + '"'


# ── PostGIS ──────────────────────────────────────────────────────────────────

class DbTable(BaseModel):
    schema_name: str
    table_name: str
    geometry_column: str
    srid: int = 0
    geometry_type: str | None = None
    name: str | None = None


class ImportDbRequest(BaseModel):
    tables: list[DbTable]


@router.get("/database")
async def discover_database(user: User = Depends(require_scope("data:write")), db: AsyncSession = Depends(get_db)):
    """List spatial tables in the connected PostGIS (any non-system schema).

    GeoDeploy's OWN per-user schemas (`geodeploy_u{id}`) are excluded: tables there are created
    by the upload/CSV pipelines and already live in My Data — listing them here made the user's
    own uploads sit in 'Import existing' permanently flagged 'already imported' (confusing, and
    importing them again would double-register)."""
    import asyncpg
    settings = get_settings()
    if not settings.postgis_host:
        raise HTTPException(409, "No PostGIS database is configured.")
    try:
        conn = await asyncpg.connect(settings.postgis_sync_dsn, timeout=15)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Could not connect to PostGIS: {exc}") from exc
    try:
        rows = await conn.fetch(
            """SELECT f_table_schema AS schema, f_table_name AS tbl,
                      f_geometry_column AS gcol, srid, type
               FROM geometry_columns
               WHERE f_table_schema <> ALL($1::text[])
                 AND f_table_schema NOT LIKE 'geodeploy\\_u%'
               ORDER BY f_table_schema, f_table_name""",
            _SYS_SCHEMAS,
        )
        out_types = {}
        for r in rows:
            declared = (r["type"] or "").upper()
            gt = _GEOM_MAP.get(declared)
            if gt is None:
                # Generic `geometry(Geometry,...)` columns (e.g. GeoDeploy's own ingested tables)
                # report type "GEOMETRY" — sample one row to learn the real geometry type.
                gt = await _sample_geom_type(conn, r["schema"], r["tbl"], r["gcol"])
            out_types[(r["schema"], r["tbl"])] = gt
    finally:
        await conn.close()

    # "Already imported" is instance-wide (shared workspace): if ANY member registered the
    # table, it's in the catalog — don't offer it again per-user.
    existing = await db.execute(
        select(VectorLayer.schema_name, VectorLayer.table_name)
    )
    have = {(s, t) for s, t in existing.all()}
    return [
        {
            "schema_name": r["schema"], "table_name": r["tbl"],
            "geometry_column": r["gcol"], "srid": r["srid"] or 0,
            "type": r["type"],
            "geometry_type": out_types.get((r["schema"], r["tbl"]), "polygon"),
            "already_imported": (r["schema"], r["tbl"]) in have,
        }
        for r in rows
    ]


async def _sample_geom_type(conn, schema, table, gcol) -> str:
    """Read one row's GeometryType so generic `geometry` columns map to point/line/polygon."""
    try:
        t = await conn.fetchval(
            f"SELECT GeometryType({_q(gcol)}) FROM {_q(schema)}.{_q(table)} "
            f"WHERE {_q(gcol)} IS NOT NULL LIMIT 1")
        return _GEOM_MAP.get((t or "").upper(), "polygon")
    except Exception:  # noqa: BLE001
        return "polygon"


async def _table_bbox_4326(conn, schema, table, gcol, srid) -> list | None:
    geom = f'ST_Extent({_q(gcol)})::geometry'
    if srid and srid not in (0, 4326):
        geom = f'ST_Transform(ST_SetSRID({geom}, {int(srid)}), 4326)'
    sql = (f'SELECT ST_XMin(b), ST_YMin(b), ST_XMax(b), ST_YMax(b) '
           f'FROM (SELECT {geom} AS b FROM {_q(schema)}.{_q(table)}) s')
    try:
        row = await conn.fetchrow(sql)
        if row and row[0] is not None:
            return [row[0], row[1], row[2], row[3]]
    except Exception:  # noqa: BLE001 — a bad/empty geometry shouldn't block the import
        pass
    return None


async def _primary_key(conn, schema, table) -> str | None:
    try:
        rows = await conn.fetch(
            """SELECT a.attname FROM pg_index i
               JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
               WHERE i.indrelid = ($1)::regclass AND i.indisprimary""",
            f"{_q(schema)}.{_q(table)}",
        )
        if len(rows) == 1:
            return rows[0]["attname"]
    except Exception:  # noqa: BLE001
        pass
    return None


@router.post("/database", status_code=201)
async def import_database(
    req: ImportDbRequest,
    user: User = Depends(require_scope("data:write")),
    db: AsyncSession = Depends(get_db),
):
    """Register selected existing PostGIS tables as vector layers (no data copy)."""
    import asyncpg
    settings = get_settings()
    if not req.tables:
        raise HTTPException(400, "No tables selected.")
    conn = await asyncpg.connect(settings.postgis_sync_dsn, timeout=30)
    created: list[str] = []
    try:
        for t in req.tables:
            dup = (await db.execute(select(VectorLayer).where(
                VectorLayer.schema_name == t.schema_name,
                VectorLayer.table_name == t.table_name,
            ))).scalar_one_or_none()
            if dup:
                continue

            bbox = await _table_bbox_4326(conn, t.schema_name, t.table_name, t.geometry_column, t.srid)
            col_rows = await conn.fetch(
                """SELECT column_name, udt_name FROM information_schema.columns
                   WHERE table_schema = $1 AND table_name = $2 ORDER BY ordinal_position""",
                t.schema_name, t.table_name,
            )
            columns = [{"name": r["column_name"], "type": r["udt_name"]}
                       for r in col_rows if r["column_name"] != t.geometry_column]
            id_col = await _primary_key(conn, t.schema_name, t.table_name)
            try:
                fc = await conn.fetchval("SELECT reltuples::bigint FROM pg_class WHERE oid = ($1)::regclass",
                                         f"{_q(t.schema_name)}.{_q(t.table_name)}")
                fc = int(fc) if fc and fc > 0 else None
            except Exception:  # noqa: BLE001
                fc = None

            db.add(VectorLayer(
                user_id=user.id,
                name=(t.name or t.table_name),
                schema_name=t.schema_name,
                table_name=t.table_name,
                crs=f"EPSG:{t.srid}" if t.srid else None,
                feature_count=fc,
                bbox=json.dumps(bbox) if bbox else None,
                columns=json.dumps(columns),
                geometry_type=t.geometry_type or "polygon",
                geometry_column=t.geometry_column,
                id_column=id_col,
                storage_backend="postgis",
                status="ready",
            ))
            created.append(f"{t.schema_name}.{t.table_name}")
        await db.commit()
    finally:
        await conn.close()

    if created:
        result = await db.execute(select(VectorLayer).where(
            VectorLayer.status == "ready", VectorLayer.storage_backend == "postgis"))
        layers = [{"schema_name": l.schema_name, "table_name": l.table_name,
                   "geometry_column": l.geometry_column, "id_column": l.id_column, "crs": l.crs}
                  for l in result.scalars().all()]
        try:
            await martin_svc.regenerate_config(layers)
        except Exception:  # noqa: BLE001 — tiles refresh on the next successful regeneration
            pass
    return {"imported": created}


# ── Object storage (S3 / MinIO) ───────────────────────────────────────────────

class StorageItem(BaseModel):
    key: str
    name: str | None = None


class ImportStorageRequest(BaseModel):
    items: list[StorageItem]


@router.get("/storage")
async def discover_storage(user: User = Depends(require_scope("data:write")), db: AsyncSession = Depends(get_db)):
    """List spatial objects already in the configured bucket (GeoTIFF, GeoParquet, CSV)."""
    from ...services.minio import get_s3_client
    settings = get_settings()
    if not settings.storage_endpoint:
        raise HTTPException(409, "No object storage is configured.")

    def _list():
        s3 = get_s3_client()
        found = []
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=settings.storage_bucket):
            for obj in page.get("Contents", []):
                # Skip GeoDeploy's OWN upload areas — those objects were created by the upload
                # pipelines and already live in My Data (same reasoning as the geodeploy_u%
                # schema exclusion in discover/database).
                if obj["Key"].startswith(("rasters/", "vectors/")):
                    continue
                low = obj["Key"].lower()
                if low.endswith((".tif", ".tiff")):
                    found.append({"key": obj["Key"], "size": obj["Size"], "kind": "raster"})
                elif low.endswith((".parquet", ".geoparquet")):
                    found.append({"key": obj["Key"], "size": obj["Size"], "kind": "geoparquet"})
                elif low.endswith(".csv"):
                    found.append({"key": obj["Key"], "size": obj["Size"], "kind": "csv"})
        return found

    try:
        keys = await run_in_threadpool(_list)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Could not list storage: {exc}") from exc

    # Instance-wide "already imported" check (shared workspace) — see the database twin above.
    existing = await db.execute(select(RasterLayer.s3_key))
    have = {row[0] for row in existing.all()}
    vec = await db.execute(select(VectorLayer.s3_key, VectorLayer.source_s3_key))
    # source_s3_key is the attached original — s3_key alone stops matching once the spatial prep
    # repoints the layer at its prepped copy under vectors/.
    have_vec = {k for row in vec.all() for k in row if k}
    for k in keys:
        # CSV import creates a fresh PostGIS table (no stored source key), so it can't be
        # de-duplicated here.
        k["already_imported"] = ((k["kind"] == "raster" and k["key"] in have)
                                 or (k["kind"] == "geoparquet" and k["key"] in have_vec))
    return keys


@router.post("/storage", status_code=201)
async def import_storage(
    req: ImportStorageRequest,
    user: User = Depends(require_scope("data:write")),
    db: AsyncSession = Depends(get_db),
):
    """Register selected existing storage objects (no data copy): GeoTIFFs become raster layers
    immediately; GeoParquet files become file-backed vector layers via a queued inspect+prep job
    (the prep writes its partitioned copy under vectors/ and NEVER touches the attached source —
    import = listing, delete = unlist)."""
    from slugify import slugify
    from ...tasks.geoparquet_import import import_geoparquet
    settings = get_settings()
    if not req.items:
        raise HTTPException(400, "No files selected.")
    created: list[str] = []
    jobs: list[JobStatus] = []
    for item in req.items:
        key = item.key
        low = key.lower()
        name = (item.name or "").strip() or key.rsplit("/", 1)[-1].rsplit(".", 1)[0]

        if low.endswith((".parquet", ".geoparquet")):
            dup = (await db.execute(select(VectorLayer).where(
                (VectorLayer.s3_key == key) | (VectorLayer.source_s3_key == key),
            ))).scalars().first()
            if dup:
                continue
            layer = VectorLayer(
                user_id=user.id, name=name,
                table_name=f"gpq_{slugify(name, separator='_') or 'layer'}_{uuid.uuid4().hex[:6]}",
                schema_name=f"geodeploy_u{user.id}",
                storage_backend="geoparquet", s3_key=key, source_s3_key=key,
                status="processing",
            )
            db.add(layer)
            await db.flush()
            job_id = str(uuid.uuid4())
            db.add(UploadJob(id=job_id, layer_id=layer.id, layer_type="vector"))
            await db.commit()
            import_geoparquet.delay(job_id, layer.id, key)
            created.append(key)
            jobs.append(JobStatus(id=job_id, layer_id=layer.id, layer_type="vector",
                                  status="queued", progress=0, current_step="Queued",
                                  error_message=None))
            continue

        dup = (await db.execute(select(RasterLayer).where(
            RasterLayer.s3_key == key))).scalar_one_or_none()
        if dup:
            continue
        try:
            meta = await run_in_threadpool(cog_converter.inspect_s3, key, settings)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, f"Could not read '{key}': {exc}") from exc
        db.add(RasterLayer(
            user_id=user.id, name=name, s3_key=key,
            crs=meta["crs"], bbox=json.dumps(meta["bbox"]),
            band_count=meta["band_count"], nodata_value=meta["nodata_value"],
            status="ready",
        ))
        created.append(key)
    await db.commit()
    return {"imported": created, "jobs": [j.model_dump() for j in jobs]}


# ── CSV → points (loads into PostGIS; a CSV isn't tile-servable as-is) ─────────
# The heavy load runs in a Celery job (geodeploy.tasks.csv_import) with type inference.

class ImportCsvRequest(BaseModel):
    key: str
    name: str | None = None
    x_column: str | None = None
    y_column: str | None = None
    wkt_column: str | None = None   # WKT geometry column (any type) — alternative to X/Y points
    srid: int = 4326
    delimiter: str = "comma"   # comma | semicolon | tab | pipe | space


@router.get("/storage/csv-columns")
async def csv_columns(key: str, delimiter: str = "comma", _: User = Depends(require_scope("data:write"))):
    """Read a CSV's header so the UI can offer X/Y column pickers."""
    from ...tasks import csv_import
    settings = get_settings()
    try:
        cols = await run_in_threadpool(csv_import.csv_header, key, settings, delimiter)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"Could not read CSV header: {exc}") from exc
    return {"columns": cols}


@router.post("/storage/csv", response_model=JobStatus, status_code=202)
async def import_csv(
    req: ImportCsvRequest,
    user: User = Depends(require_scope("data:write")),
    db: AsyncSession = Depends(get_db),
):
    """Queue a CSV → PostGIS layer import: points from X/Y columns, or any geometry (e.g.
    polygons) from a WKT column. Reprojected to 4326."""
    from ...tasks import csv_import
    settings = get_settings()
    if not settings.postgis_host:
        raise HTTPException(409, "No PostGIS database is configured.")
    if not req.wkt_column and not (req.x_column and req.y_column):
        raise HTTPException(400, "Pick X/Y columns or a WKT geometry column.")
    name = (req.name or "").strip() or req.key.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    schema = f"geodeploy_u{user.id}"
    table = f"csv_{csv_import.safe_name(name, 'layer')}_{uuid.uuid4().hex[:6]}"

    layer = VectorLayer(
        user_id=user.id, name=name, schema_name=schema, table_name=table,
        # WKT can hold any geometry type — the task fills in the real one after the load.
        geometry_type=None if req.wkt_column else "point",
        geometry_column="geom", id_column="id",
        storage_backend="postgis", status="processing",
    )
    db.add(layer)
    await db.flush()
    job_id = str(uuid.uuid4())
    db.add(UploadJob(id=job_id, layer_id=layer.id, layer_type="vector"))
    await db.commit()
    await db.refresh(layer)

    csv_import.import_csv.delay(job_id, layer.id, req.key, schema, table,
                               req.x_column, req.y_column, req.srid, True, req.delimiter,
                               req.wkt_column)
    return JobStatus(id=job_id, layer_id=layer.id, layer_type="vector",
                     status="queued", progress=0, current_step="Queued", error_message=None)
