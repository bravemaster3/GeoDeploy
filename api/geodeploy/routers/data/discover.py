"""Discover + import data that ALREADY exists in the connected PostGIS / object store.

Importing registers a layer in GeoDeploy's catalog (a vector_layers / raster_layers row) that
points at the existing table/object — it does NOT copy or re-upload the data. Use this when you
connect GeoDeploy to a database/bucket that already has data (yours or someone else's).
"""
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import get_settings
from ...database import get_db
from ...deps import get_current_user
from ...models import RasterLayer, User, VectorLayer
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
async def discover_database(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """List spatial tables in the connected PostGIS (any non-system schema)."""
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
               ORDER BY f_table_schema, f_table_name""",
            _SYS_SCHEMAS,
        )
    finally:
        await conn.close()

    existing = await db.execute(
        select(VectorLayer.schema_name, VectorLayer.table_name).where(VectorLayer.user_id == user.id)
    )
    have = {(s, t) for s, t in existing.all()}
    return [
        {
            "schema_name": r["schema"], "table_name": r["tbl"],
            "geometry_column": r["gcol"], "srid": r["srid"] or 0,
            "type": r["type"],
            "geometry_type": _GEOM_MAP.get((r["type"] or "").upper(), "polygon"),
            "already_imported": (r["schema"], r["tbl"]) in have,
        }
        for r in rows
    ]


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
    user: User = Depends(get_current_user),
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
                VectorLayer.user_id == user.id,
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

class ImportStorageRequest(BaseModel):
    keys: list[str]


@router.get("/storage")
async def discover_storage(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """List GeoTIFF objects already in the configured bucket."""
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
                if obj["Key"].lower().endswith((".tif", ".tiff")):
                    found.append({"key": obj["Key"], "size": obj["Size"]})
        return found

    try:
        keys = await run_in_threadpool(_list)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Could not list storage: {exc}") from exc

    existing = await db.execute(select(RasterLayer.s3_key).where(RasterLayer.user_id == user.id))
    have = {row[0] for row in existing.all()}
    for k in keys:
        k["already_imported"] = k["key"] in have
    return keys


@router.post("/storage", status_code=201)
async def import_storage(
    req: ImportStorageRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Register selected existing GeoTIFFs as raster layers (no data copy)."""
    settings = get_settings()
    if not req.keys:
        raise HTTPException(400, "No files selected.")
    created: list[str] = []
    for key in req.keys:
        dup = (await db.execute(select(RasterLayer).where(
            RasterLayer.user_id == user.id, RasterLayer.s3_key == key))).scalar_one_or_none()
        if dup:
            continue
        try:
            meta = await run_in_threadpool(cog_converter.inspect_s3, key, settings)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, f"Could not read '{key}': {exc}") from exc
        name = key.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        db.add(RasterLayer(
            user_id=user.id, name=name, s3_key=key,
            crs=meta["crs"], bbox=json.dumps(meta["bbox"]),
            band_count=meta["band_count"], nodata_value=meta["nodata_value"],
            status="ready",
        ))
        created.append(key)
    await db.commit()
    return {"imported": created}
