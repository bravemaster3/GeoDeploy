"""
Vector ingest pipeline: uploaded file → PostGIS table → Martin MVT endpoint.

Loads via COPY: features are streamed to a temp CSV (geometry as WKB hex), COPYd into an UNLOGGED
staging table, then a single INSERT…SELECT reprojects to EPSG:4326 IN PostGIS (ST_Transform) into
the final table. Streams from disk (no in-memory feature list) and bulk-loads — fast on large files.
"""
import csv as csvlib
import json
import os
import uuid
import zipfile
from datetime import datetime, timezone

import fiona
import psycopg2
from shapely.geometry import shape as shp_shape

from ..celery_app import celery_app
from ..config import get_settings
from ..services import martin as martin_svc

_PG_TYPE = {
    "int": "BIGINT", "int32": "BIGINT", "int64": "BIGINT",
    "float": "DOUBLE PRECISION", "str": "TEXT",
    "date": "DATE", "datetime": "TIMESTAMP", "time": "TIME",
}


def _q(ident: str) -> str:
    return '"' + str(ident).replace('"', '""') + '"'


def _pg_type(fiona_type) -> str:
    return _PG_TYPE.get(str(fiona_type).lower().split(":")[0], "TEXT")


def _srid_of(crs_wkt) -> int | None:
    if not crs_wkt:
        return None
    try:
        from pyproj import CRS
        return CRS.from_wkt(crs_wkt).to_epsg()
    except Exception:  # noqa: BLE001
        return None


def _update_job(db_path: str, job_id: str, **kwargs) -> None:
    import sqlite3
    with sqlite3.connect(db_path) as conn:
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        values = list(kwargs.values()) + [job_id]
        conn.execute(f"UPDATE upload_jobs SET {sets} WHERE id = ?", values)


def _update_layer(db_path: str, layer_id: int, **kwargs) -> None:
    import sqlite3
    with sqlite3.connect(db_path) as conn:
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        values = list(kwargs.values()) + [layer_id]
        conn.execute(f"UPDATE vector_layers SET {sets} WHERE id = ?", values)


def _get_all_layers(db_path: str) -> list[dict]:
    import sqlite3
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT schema_name, table_name, geometry_column, id_column, crs "
            "FROM vector_layers WHERE status = 'ready' AND storage_backend = 'postgis'"
        ).fetchall()
        return [dict(r) for r in rows]


def _get_setup(db_path: str) -> dict | None:
    import sqlite3
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM setup_config WHERE completed = 1").fetchone()
        return dict(row) if row else None


@celery_app.task(bind=True, name="geodeploy.tasks.vector_ingest.ingest_vector")
def ingest_vector(self, job_id: str, layer_id: int, file_path: str, layer_name: str, schema_name: str, table_name: str):
    settings = get_settings()
    db_path = f"{settings.data_dir}/sqlite/geodeploy.db"

    def step(msg: str, progress: int) -> None:
        _update_job(db_path, job_id, status="processing", current_step=msg, progress=progress,
                    started_at=datetime.now(timezone.utc).isoformat())

    try:
        step("Validating file", 5)
        src_path = _resolve_source(file_path)

        setup = _get_setup(db_path)
        dsn = (f"host={setup['postgis_host']} port={setup['postgis_port']} dbname={setup['postgis_db']} "
               f"user={setup['postgis_user']} password={setup['postgis_password']}")
        # External/managed DBs may require SSL; the local provisioned DB leaves this empty.
        if settings.postgis_sslmode:
            dsn += f" sslmode={settings.postgis_sslmode}"

        step("Loading into PostGIS (COPY)", 30)
        res = _ingest_via_copy(dsn, schema_name, table_name, src_path, settings.data_dir)

        step("Saving metadata", 90)
        _update_layer(db_path, layer_id,
                      status="ready",
                      feature_count=res["count"],
                      bbox=json.dumps(res["bbox"]) if res["bbox"] else None,
                      columns=json.dumps(res["columns"]),
                      geometry_type=res["geom_type"],
                      geometry_column="geom",
                      id_column="id",
                      crs="EPSG:4326",
                      updated_at=datetime.now(timezone.utc).isoformat())

        step("Updating tile server", 95)
        import asyncio
        asyncio.run(martin_svc.regenerate_config(_get_all_layers(db_path)))

        _update_job(db_path, job_id, status="ready", progress=100,
                    completed_at=datetime.now(timezone.utc).isoformat())

    except Exception as exc:
        _update_job(db_path, job_id, status="error", error_message=str(exc),
                    completed_at=datetime.now(timezone.utc).isoformat())
        _update_layer(db_path, layer_id, status="error", error_message=str(exc))
        raise
    finally:
        if os.path.exists(file_path):
            os.unlink(file_path)


def _resolve_source(file_path: str) -> str:
    """Unzip shapefile ZIPs; return a path Fiona can open."""
    if file_path.endswith(".zip"):
        extract_dir = file_path + "_extracted"
        os.makedirs(extract_dir, exist_ok=True)
        with zipfile.ZipFile(file_path) as z:
            z.extractall(extract_dir)
        shps = [os.path.join(extract_dir, f) for f in os.listdir(extract_dir) if f.endswith(".shp")]
        if not shps:
            raise ValueError("ZIP file contains no .shp file.")
        return shps[0]
    return file_path


def _ingest_via_copy(dsn: str, schema: str, table: str, src_path: str, data_dir: str) -> dict:
    """Stream features → temp CSV (geom as WKB hex) → COPY into staging → INSERT…SELECT (reproject)."""
    tmp_csv = os.path.join(data_dir, "temp", f"{uuid.uuid4().hex}.copy.csv")
    os.makedirs(os.path.dirname(tmp_csv), exist_ok=True)

    with fiona.open(src_path) as src:
        crs_wkt = src.crs_wkt
        geom_type = src.schema["geometry"]
        col_schema = src.schema["properties"]
        cols = list(col_schema.keys())
        srid = _srid_of(crs_wkt)

        # Decide where reprojection happens. Known EPSG → reproject set-based in PostGIS (fastest).
        # Unknown EPSG but a WKT is present → transform client-side (rare). No CRS → assume 4326.
        client_tr = None
        db_srid = 4326
        if srid and srid != 4326:
            db_srid = srid
        elif srid is None and crs_wkt:
            from pyproj import CRS, Transformer
            from shapely.ops import transform as shp_transform
            _t = Transformer.from_crs(CRS.from_wkt(crs_wkt), CRS.from_epsg(4326), always_xy=True)
            client_tr = lambda g: shp_transform(_t.transform, g)  # noqa: E731

        count = 0
        try:
            with open(tmp_csv, "w", newline="", encoding="utf-8") as fh:
                w = csvlib.writer(fh)
                for feat in src:
                    g = feat["geometry"]
                    if g is None:
                        continue
                    geom = shp_shape(g)
                    if client_tr:
                        geom = client_tr(geom)
                    props = feat["properties"]
                    row = []
                    for c in cols:
                        v = props.get(c)
                        row.append("" if v is None else (v.isoformat() if hasattr(v, "isoformat") else v))
                    row.append(geom.wkb_hex)
                    w.writerow(row)
                    count += 1
        except Exception:
            if os.path.exists(tmp_csv):
                os.unlink(tmp_csv)
            raise

    if count == 0:
        if os.path.exists(tmp_csv):
            os.unlink(tmp_csv)
        raise ValueError("No features with geometry found in the file.")

    geom_sql = (f"ST_Transform(ST_SetSRID(geom, {db_srid}), 4326)" if db_srid != 4326
                else "ST_SetSRID(geom, 4326)")
    stg = f"{table}_stg"
    coldefs = ", ".join(f"{_q(c)} {_pg_type(col_schema[c])}" for c in cols)
    copycols = ", ".join(_q(c) for c in cols)
    conn = psycopg2.connect(dsn)
    try:
        cur = conn.cursor()
        cur.execute(f"CREATE SCHEMA IF NOT EXISTS {_q(schema)}")
        cur.execute("CREATE EXTENSION IF NOT EXISTS postgis")
        cur.execute(f"DROP TABLE IF EXISTS {_q(schema)}.{_q(table)}")
        cur.execute(f"DROP TABLE IF EXISTS {_q(schema)}.{_q(stg)}")
        cur.execute(f"CREATE UNLOGGED TABLE {_q(schema)}.{_q(stg)} ({coldefs}, geom geometry)")
        with open(tmp_csv, "r", encoding="utf-8", newline="") as fh:
            cur.copy_expert(
                f"COPY {_q(schema)}.{_q(stg)} ({copycols}, geom) FROM STDIN WITH (FORMAT csv)", fh)
        cur.execute(f"CREATE TABLE {_q(schema)}.{_q(table)} "
                    f"(id serial primary key, {coldefs}, geom geometry(Geometry,4326))")
        cur.execute(f"INSERT INTO {_q(schema)}.{_q(table)} ({copycols}, geom) "
                    f"SELECT {copycols}, {geom_sql} FROM {_q(schema)}.{_q(stg)}")
        cur.execute(f"DROP TABLE {_q(schema)}.{_q(stg)}")
        cur.execute(f"CREATE INDEX {_q(table + '_geom_idx')} ON {_q(schema)}.{_q(table)} USING GIST (geom)")
        cur.execute(f"SELECT ST_XMin(e), ST_YMin(e), ST_XMax(e), ST_YMax(e) "
                    f"FROM (SELECT ST_Extent(geom) e FROM {_q(schema)}.{_q(table)}) s")
        b = cur.fetchone()
        bbox = [b[0], b[1], b[2], b[3]] if b and b[0] is not None else None
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
        if os.path.exists(tmp_csv):
            os.unlink(tmp_csv)

    return {
        "bbox": bbox, "count": count, "geom_type": geom_type,
        "columns": [{"name": c, "type": str(col_schema[c])} for c in cols],
    }
