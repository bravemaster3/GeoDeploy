"""Import a CSV from object storage as a PostGIS point layer (Celery, background).

Unlike attaching an existing table/COG, a CSV isn't tile-servable, so we build points from its
X/Y columns into PostGIS. Runs as a job (status via UploadJob) with column type inference.
"""
import csv as csvlib
import io
import re
import uuid
from datetime import date, datetime, timezone

from ..celery_app import celery_app
from ..config import get_settings
from ..services import martin as martin_svc
from .vector_ingest import _update_job, _update_layer, _get_all_layers

CSV_MAX_BYTES = 200 * 1024 * 1024   # 200 MB read cap
CSV_MAX_ROWS = 1_000_000            # in-memory load cap (COPY would lift this further)

_INT_RE = re.compile(r"-?\d+")
_LEADING_ZERO_RE = re.compile(r"-?0\d+")
_FLOAT_RE = re.compile(r"-?\d+(\.\d+)?([eE][+-]?\d+)?")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def q(ident: str) -> str:
    return '"' + str(ident).replace('"', '""') + '"'


def safe_name(s: str, fallback: str = "col") -> str:
    s = re.sub(r"[^a-zA-Z0-9_]", "_", (s or "").strip().lower()).strip("_")
    if not s:
        s = fallback
    if s[0].isdigit():
        s = "_" + s
    return s[:60]


def read_csv_text(key: str, settings, max_bytes: int = CSV_MAX_BYTES) -> str:
    from ..services.minio import get_s3_client
    s3 = get_s3_client()
    obj = s3.get_object(Bucket=settings.storage_bucket, Key=key)
    body = obj["Body"].read(max_bytes + 1)
    if len(body) > max_bytes:
        raise ValueError(f"CSV exceeds the {max_bytes // (1024 * 1024)} MB limit.")
    return body.decode("utf-8-sig", errors="replace")


def csv_header(key: str, settings) -> list[str]:
    text = read_csv_text(key, settings, max_bytes=1024 * 1024)  # 1 MB peek
    return next(csvlib.reader(io.StringIO(text)), [])


# ── type inference ─────────────────────────────────────────────────────────────

def _is_int(v: str) -> bool:
    return bool(_INT_RE.fullmatch(v)) and not _LEADING_ZERO_RE.fullmatch(v)  # leading zero → text (zip codes)


def _is_float(v: str) -> bool:
    return bool(_FLOAT_RE.fullmatch(v))


def _is_date(v: str) -> bool:
    if not _DATE_RE.fullmatch(v):
        return False
    try:
        datetime.strptime(v, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def _infer(values) -> str:
    seen = False
    is_int = is_float = is_dt = True
    for v in values:
        if v is None:
            continue
        v = v.strip()
        if v == "":
            continue
        seen = True
        if is_int and not _is_int(v):
            is_int = False
        if is_float and not _is_float(v):
            is_float = False
        if is_dt and not _is_date(v):
            is_dt = False
        if not (is_int or is_float or is_dt):
            break
    if not seen:
        return "text"
    if is_int:
        return "bigint"
    if is_float:
        return "double precision"
    if is_dt:
        return "date"
    return "text"


def _convert(v, pg_type: str):
    if v is None or v.strip() == "":
        return None
    v = v.strip()
    try:
        if pg_type == "bigint":
            return int(v)
        if pg_type == "double precision":
            return float(v)
        if pg_type == "date":
            y, m, d = v.split("-")
            return date(int(y), int(m), int(d))
    except (ValueError, TypeError):
        return None  # tolerate a stray bad cell
    return v


def _load_postgis(settings, schema, table, fields, safe, types, rows, srid):
    """rows: list of (raw_dict, x, y). Returns (bbox_4326, feature_count)."""
    import psycopg2
    srid = int(srid) or 4326
    geom_expr = (f"ST_Transform(ST_SetSRID(ST_MakePoint(%s,%s),{srid}),4326)"
                 if srid != 4326 else "ST_SetSRID(ST_MakePoint(%s,%s),4326)")
    conn = psycopg2.connect(settings.postgis_sync_dsn)
    try:
        cur = conn.cursor()
        cur.execute(f"CREATE SCHEMA IF NOT EXISTS {q(schema)}")
        cur.execute("CREATE EXTENSION IF NOT EXISTS postgis")
        cols_ddl = ", ".join(f"{q(safe[f])} {types[f]}" for f in fields)
        cur.execute(f"CREATE TABLE {q(schema)}.{q(table)} "
                    f"(id serial primary key, {cols_ddl}, geom geometry(Point,4326))")
        insert_cols = ", ".join(q(safe[f]) for f in fields)
        placeholders = ", ".join(["%s"] * len(fields))
        params = [[_convert(row.get(f), types[f]) for f in fields] + [x, y] for (row, x, y) in rows]
        cur.executemany(
            f"INSERT INTO {q(schema)}.{q(table)} ({insert_cols}, geom) "
            f"VALUES ({placeholders}, {geom_expr})", params)
        cur.execute(f"CREATE INDEX ON {q(schema)}.{q(table)} USING GIST (geom)")
        cur.execute(f"SELECT ST_XMin(e), ST_YMin(e), ST_XMax(e), ST_YMax(e) "
                    f"FROM (SELECT ST_Extent(geom) e FROM {q(schema)}.{q(table)}) s")
        b = cur.fetchone()
        bbox = [b[0], b[1], b[2], b[3]] if b and b[0] is not None else None
        conn.commit()
        return bbox, len(params)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@celery_app.task(bind=True, name="geodeploy.tasks.csv_import.import_csv")
def import_csv(self, job_id, layer_id, key, schema, table, x_col, y_col, srid):
    import json
    settings = get_settings()
    db_path = f"{settings.data_dir}/sqlite/geodeploy.db"

    def step(msg, pct):
        _update_job(db_path, job_id, status="processing", current_step=msg, progress=pct,
                    started_at=datetime.now(timezone.utc).isoformat())

    try:
        step("Reading CSV", 10)
        reader = csvlib.DictReader(io.StringIO(read_csv_text(key, settings)))
        fields = reader.fieldnames or []
        if x_col not in fields or y_col not in fields:
            raise ValueError("Selected X/Y columns are not in the CSV header.")

        rows = []
        for row in reader:
            if len(rows) >= CSV_MAX_ROWS:
                break
            try:
                x, y = float(row.get(x_col)), float(row.get(y_col))
            except (TypeError, ValueError):
                continue  # skip rows without usable coordinates
            rows.append((row, x, y))
        if not rows:
            raise ValueError("No rows had valid numeric X/Y values.")

        step("Inferring column types", 30)
        safe, used = {}, set()
        for f in fields:
            s = safe_name(f)
            base, n = s, 1
            while s in used:
                s = f"{base}_{n}"; n += 1
            used.add(s); safe[f] = s
        types = {f: _infer(r[0].get(f) for r in rows) for f in fields}

        step("Loading into PostGIS", 55)
        bbox, fc = _load_postgis(settings, schema, table, fields, safe, types, rows, srid)

        step("Saving metadata", 90)
        columns = [{"name": safe[f], "type": types[f]} for f in fields]
        _update_layer(db_path, layer_id, status="ready", feature_count=fc,
                      bbox=json.dumps(bbox) if bbox else None, columns=json.dumps(columns),
                      crs="EPSG:4326", geometry_type="point",
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
