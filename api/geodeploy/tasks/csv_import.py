"""Import a CSV as a PostGIS point layer (Celery, background).

A CSV isn't tile-servable, so we build points from its X/Y columns into PostGIS. The load uses
COPY into a staging table → infer column types in SQL → INSERT…SELECT into the final table, so
it streams from disk (no in-memory row list) and scales to large files. Works for a CSV already
in object storage (discover/import) or one freshly uploaded (saved to a local temp file).
"""
import csv as csvlib
import io
import os
import re
import uuid
from datetime import datetime, timezone

from ..celery_app import celery_app
from ..config import get_settings
from ..services import martin as martin_svc
from .vector_ingest import _update_job, _update_layer, _get_all_layers, _get_setup

# Regex used both to infer types and to guard the casts (so a stray cell becomes NULL, never an
# error that aborts the whole INSERT). 18-digit cap keeps integers inside bigint range.
_INT = r"^-?[0-9]{1,18}$"
_LEADZERO = r"^-?0[0-9]"          # leading-zero ints (zip codes) stay text
_FLOAT = r"^-?[0-9]+(\.[0-9]+)?([eE][+-]?[0-9]+)?$"
_DATE = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"


def q(ident: str) -> str:
    return '"' + str(ident).replace('"', '""') + '"'


def safe_name(s: str, fallback: str = "col") -> str:
    s = re.sub(r"[^a-zA-Z0-9_]", "_", (s or "").strip().lower()).strip("_")
    if not s:
        s = fallback
    if s[0].isdigit():
        s = "_" + s
    return s[:60]


# Delimiter is chosen by the user (comma default); auto-sniffing is unreliable.
_DELIM_CHAR = {"comma": ",", "semicolon": ";", "tab": "\t", "pipe": "|", "space": " "}


def _delim_char(name: str) -> str:
    return _DELIM_CHAR.get(name, ",")


def _copy_delim_sql(name: str) -> str:
    """COPY DELIMITER clause value (whitelisted, so safe to inline)."""
    return "E'\\t'" if name == "tab" else "'" + _DELIM_CHAR.get(name, ",") + "'"


def read_csv_text(key: str, settings, max_bytes: int = 1024 * 1024) -> str:
    """Small header peek from object storage (used by the discover 'csv-columns' endpoint)."""
    from ..services.minio import get_s3_client
    s3 = get_s3_client()
    obj = s3.get_object(Bucket=settings.storage_bucket, Key=key)
    return obj["Body"].read(max_bytes).decode("utf-8-sig", errors="replace")


def csv_header(key: str, settings, delimiter: str = "comma") -> list[str]:
    return next(csvlib.reader(io.StringIO(read_csv_text(key, settings)), delimiter=_delim_char(delimiter)), [])


def _download_s3(key: str, settings) -> str:
    # Storage creds come from the SQLite setup_config (like raster_ingest) — the celery
    # container's env isn't reliably populated (restart doesn't re-read .env).
    import boto3
    from botocore.client import Config
    from .raster_ingest import _get_storage_creds
    creds = _get_storage_creds(f"{settings.data_dir}/sqlite/geodeploy.db")
    s3 = boto3.client(
        "s3", endpoint_url=creds["endpoint"],
        aws_access_key_id=creds["access_key"], aws_secret_access_key=creds["secret_key"],
        region_name=creds["region"], config=Config(signature_version="s3v4"),
    )
    os.makedirs(f"{settings.data_dir}/temp", exist_ok=True)
    path = f"{settings.data_dir}/temp/{uuid.uuid4().hex}.csv"
    s3.download_file(creds["bucket"], key, path)
    return path


def _file_header(path: str, delimiter: str = "comma") -> list[str]:
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        return next(csvlib.reader(fh, delimiter=_delim_char(delimiter)), [])


def _load_copy(path: str, schema: str, table: str, x_col: str, y_col: str, srid, dsn: str,
               delimiter: str = "comma") -> dict:
    """COPY the CSV into a staging table, infer types, then INSERT…SELECT into the point table."""
    import psycopg2

    fields = _file_header(path, delimiter)
    if not fields:
        raise ValueError("CSV has no header row.")
    if x_col not in fields or y_col not in fields:
        raise ValueError("Selected X/Y columns are not in the CSV header.")

    safe, used = {}, set()
    for f in fields:
        s = safe_name(f)
        base, n = s, 1
        while s in used:
            s = f"{base}_{n}"; n += 1
        used.add(s); safe[f] = s

    srid = int(srid) or 4326
    stg = f"{table}_stg"
    xc, yc = q(safe[x_col]), q(safe[y_col])

    conn = psycopg2.connect(dsn)
    try:
        cur = conn.cursor()
        cur.execute(f"CREATE SCHEMA IF NOT EXISTS {q(schema)}")
        cur.execute("CREATE EXTENSION IF NOT EXISTS postgis")
        cur.execute(f"DROP TABLE IF EXISTS {q(schema)}.{q(stg)}")
        cur.execute(f"CREATE UNLOGGED TABLE {q(schema)}.{q(stg)} "
                    f"({', '.join(f'{q(safe[f])} text' for f in fields)})")

        copy_cols = ", ".join(q(safe[f]) for f in fields)
        with open(path, "r", encoding="utf-8-sig", newline="") as fh:
            cur.copy_expert(
                f"COPY {q(schema)}.{q(stg)} ({copy_cols}) FROM STDIN "
                f"WITH (FORMAT csv, HEADER true, DELIMITER {_copy_delim_sql(delimiter)})", fh)

        # Infer each column's type from the staged text values.
        types = {}
        for f in fields:
            t = f"btrim({q(safe[f])})"
            cur.execute(
                f"SELECT bool_and(v ~ '{_INT}' AND v !~ '{_LEADZERO}'), "
                f"       bool_and(v ~ '{_FLOAT}'), bool_and(v ~ '{_DATE}') "
                f"FROM (SELECT NULLIF({t}, '') AS v FROM {q(schema)}.{q(stg)}) s WHERE v IS NOT NULL")
            is_int, is_float, is_date = cur.fetchone()
            types[f] = ("bigint" if is_int else "double precision" if is_float
                        else "date" if is_date else "text")

        typed = ", ".join(f"{q(safe[f])} {types[f]}" for f in fields)
        cur.execute(f"CREATE TABLE {q(schema)}.{q(table)} "
                    f"(id serial primary key, {typed}, geom geometry(Point,4326))")

        def cast_expr(f):
            c = q(safe[f]); t = f"btrim({c})"
            if types[f] == "bigint":
                return f"CASE WHEN {t} ~ '{_INT}' AND {t} !~ '{_LEADZERO}' THEN {t}::bigint END"
            if types[f] == "double precision":
                return f"CASE WHEN {t} ~ '{_FLOAT}' THEN {t}::double precision END"
            if types[f] == "date":
                return f"CASE WHEN {t} ~ '{_DATE}' THEN to_date({t}, 'YYYY-MM-DD') END"
            return f"NULLIF({c}, '')"

        make_point = f"ST_SetSRID(ST_MakePoint(btrim({xc})::double precision, btrim({yc})::double precision), {srid})"
        geom = f"ST_Transform({make_point}, 4326)" if srid != 4326 else make_point
        cur.execute(
            f"INSERT INTO {q(schema)}.{q(table)} ({copy_cols}, geom) "
            f"SELECT {', '.join(cast_expr(f) for f in fields)}, {geom} "
            f"FROM {q(schema)}.{q(stg)} WHERE btrim({xc}) ~ '{_FLOAT}' AND btrim({yc}) ~ '{_FLOAT}'")
        fc = cur.rowcount
        cur.execute(f"DROP TABLE {q(schema)}.{q(stg)}")
        if not fc:
            cur.execute(f"DROP TABLE {q(schema)}.{q(table)}")
            raise ValueError("No rows had valid numeric X/Y values.")
        cur.execute(f"CREATE INDEX ON {q(schema)}.{q(table)} USING GIST (geom)")
        cur.execute(f"SELECT ST_XMin(e), ST_YMin(e), ST_XMax(e), ST_YMax(e) "
                    f"FROM (SELECT ST_Extent(geom) e FROM {q(schema)}.{q(table)}) s")
        b = cur.fetchone()
        bbox = [b[0], b[1], b[2], b[3]] if b and b[0] is not None else None
        conn.commit()
        return {"bbox": bbox, "feature_count": fc,
                "columns": [{"name": safe[f], "type": types[f]} for f in fields]}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@celery_app.task(bind=True, name="geodeploy.tasks.csv_import.import_csv")
def import_csv(self, job_id, layer_id, source, schema, table, x_col, y_col, srid, is_s3=True, delimiter="comma"):
    """source = S3 key (is_s3) or a local temp CSV path (uploaded)."""
    import json
    settings = get_settings()
    db_path = f"{settings.data_dir}/sqlite/geodeploy.db"

    def step(msg, pct):
        _update_job(db_path, job_id, status="processing", current_step=msg, progress=pct,
                    started_at=datetime.now(timezone.utc).isoformat())

    # Build the DB connection from the SQLite setup_config (authoritative) — NOT from env
    # settings, whose POSTGIS_PASSWORD isn't reliably present in the celery container.
    setup = _get_setup(db_path)
    if not setup:
        raise ValueError("Setup is not complete — no database configured.")
    dsn = (f"host={setup['postgis_host']} port={setup['postgis_port']} dbname={setup['postgis_db']} "
           f"user={setup['postgis_user']} password={setup['postgis_password']}")
    if settings.postgis_sslmode:
        dsn += f" sslmode={settings.postgis_sslmode}"

    downloaded = None
    try:
        step("Reading CSV", 15)
        path = _download_s3(source, settings) if is_s3 else source
        downloaded = path if is_s3 else None

        step("Loading into PostGIS", 45)
        res = _load_copy(path, schema, table, x_col, y_col, srid, dsn, delimiter)

        step("Saving metadata", 90)
        _update_layer(db_path, layer_id, status="ready", feature_count=res["feature_count"],
                      bbox=json.dumps(res["bbox"]) if res["bbox"] else None,
                      columns=json.dumps(res["columns"]), crs="EPSG:4326", geometry_type="point",
                      updated_at=datetime.now(timezone.utc).isoformat())
        _update_job(db_path, job_id, status="ready", progress=100,
                    completed_at=datetime.now(timezone.utc).isoformat())

        import asyncio
        asyncio.run(martin_svc.regenerate_config(_get_all_layers(db_path)))
    except Exception as exc:
        _update_job(db_path, job_id, status="error", error_message=str(exc),
                    completed_at=datetime.now(timezone.utc).isoformat())
        _update_layer(db_path, layer_id, status="error", error_message=str(exc))
        raise
    finally:
        # Clean up the temp file (the downloaded S3 copy, or the uploaded local file).
        for p in (downloaded, None if is_s3 else source):
            if p and os.path.exists(p):
                try:
                    os.unlink(p)
                except OSError:
                    pass
