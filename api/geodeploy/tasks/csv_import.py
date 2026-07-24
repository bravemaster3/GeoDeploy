"""Import a CSV as a PostGIS vector layer (Celery, background).

A CSV isn't tile-servable, so we build geometry from it into PostGIS: either points from X/Y
columns, or any geometry from a WKT column (e.g. Google's Open Buildings ships polygons as CSV).
The load uses COPY into a staging table → infer column types in SQL → INSERT…SELECT into the
final table, so it streams from disk (no in-memory row list) and scales to large files. Works
for a CSV already in object storage (discover/import) or one freshly uploaded (local temp file).
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


# GeometryType() output → the catalog's point/line/polygon vocabulary.
_GEOM_KIND = {
    "POINT": "point", "MULTIPOINT": "point",
    "LINESTRING": "line", "MULTILINESTRING": "line",
    "POLYGON": "polygon", "MULTIPOLYGON": "polygon",
}


def _load_copy(path: str, schema: str, table: str, x_col: str | None, y_col: str | None, srid,
               dsn: str, delimiter: str = "comma", wkt_col: str | None = None) -> dict:
    """COPY the CSV into a staging table, infer types, then INSERT…SELECT into the final table.

    Geometry comes from EITHER the X/Y numeric columns (points — the original path) OR a WKT
    column (`wkt_col`, any geometry type — e.g. polygon footprints shipped as CSV)."""
    import psycopg2

    fields = _file_header(path, delimiter)
    if not fields:
        raise ValueError("CSV has no header row.")
    if wkt_col:
        if wkt_col not in fields:
            raise ValueError("Selected WKT geometry column is not in the CSV header.")
    elif x_col not in fields or y_col not in fields:
        raise ValueError("Selected X/Y columns are not in the CSV header.")

    safe, used = {}, set()
    for f in fields:
        s = safe_name(f)
        base, n = s, 1
        while s in used:
            s = f"{base}_{n}"; n += 1
        used.add(s); safe[f] = s

    srid = int(srid) or 4326
    # NATIVE-CRS STORAGE: store in the user-picked SRID. Only for 4326 do we apply the Web-Mercator
    # pole-clamp + (no-op) transform; a projected SRID is stored as-is (Martin reprojects native→3857).
    native = (srid != 4326)
    stg = f"{table}_stg"

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
        # X/Y mode always yields points; WKT can hold any geometry type → generic column. SRID = the
        # user-picked CRS (stored natively).
        geom_ddl = (f"geometry(Geometry,{srid})" if wkt_col else f"geometry(Point,{srid})")
        cur.execute(f"CREATE TABLE {q(schema)}.{q(table)} "
                    f"(id serial primary key, {typed}, geom {geom_ddl})")

        def cast_expr(f):
            c = q(safe[f]); t = f"btrim({c})"
            if types[f] == "bigint":
                return f"CASE WHEN {t} ~ '{_INT}' AND {t} !~ '{_LEADZERO}' THEN {t}::bigint END"
            if types[f] == "double precision":
                return f"CASE WHEN {t} ~ '{_FLOAT}' THEN {t}::double precision END"
            if types[f] == "date":
                return f"CASE WHEN {t} ~ '{_DATE}' THEN to_date({t}, 'YYYY-MM-DD') END"
            return f"NULLIF({c}, '')"

        if wkt_col:
            # A malformed WKT cell would abort the whole INSERT (ST_GeomFromText raises), so parse
            # through a session-local pg_temp function that swallows errors → NULL row, load continues.
            # For 4326 it also clips to the Web Mercator band (±85.0511°): Martin tiles in EPSG:3857,
            # where polar coordinates transform to infinity and 500 every low-zoom tile (§0g). A NATIVE
            # (projected) SRID is stored untransformed — no lat/lon pole concept, no clamp.
            if native:
                cur.execute(f"""
                    CREATE FUNCTION pg_temp.gd_wkt_geom(t text, s int) RETURNS geometry AS $$
                    DECLARE g geometry;
                    BEGIN
                        g := ST_SetSRID(ST_GeomFromText(t), s);
                        RETURN CASE WHEN g IS NULL OR ST_IsEmpty(g) THEN NULL ELSE g END;
                    EXCEPTION WHEN OTHERS THEN RETURN NULL;
                    END $$ LANGUAGE plpgsql IMMUTABLE""")
            else:
                cur.execute(f"""
                    CREATE FUNCTION pg_temp.gd_wkt_geom(t text, s int) RETURNS geometry AS $$
                    DECLARE g geometry;
                    BEGIN
                        g := ST_Transform(ST_SetSRID(ST_GeomFromText(t), s), 4326);
                        IF ST_YMax(g) > 85.05112878 OR ST_YMin(g) < -85.05112878 THEN
                            g := ST_Intersection(g,
                                 ST_MakeEnvelope(-180, -85.05112878, 180, 85.05112878, 4326));
                        END IF;
                        RETURN CASE WHEN g IS NULL OR ST_IsEmpty(g) THEN NULL ELSE g END;
                    EXCEPTION WHEN OTHERS THEN RETURN NULL;
                    END $$ LANGUAGE plpgsql IMMUTABLE""")
            wc = q(safe[wkt_col])
            geom = f"pg_temp.gd_wkt_geom(NULLIF(btrim({wc}), ''), {srid})"
            row_filter = f"NULLIF(btrim({wc}), '') IS NOT NULL"
        else:
            xc, yc = q(safe[x_col]), q(safe[y_col])
            make_point = f"ST_SetSRID(ST_MakePoint(btrim({xc})::double precision, btrim({yc})::double precision), {srid})"
            if native:
                geom = make_point  # store as-is in the projected SRID (no pole clamp)
            else:
                # Clamp latitude to the Web Mercator limit (±85.0511): Martin tiles in EPSG:3857, where a
                # point at/near the poles transforms to infinity and breaks tile generation (HTTP 500). A
                # sentinel/polar row (e.g. a lat=-90 "country") would otherwise blank out low-zoom tiles.
                geom = (f"ST_SetSRID(ST_MakePoint(ST_X({make_point}), "
                        f"GREATEST(-85.05112878, LEAST(85.05112878, ST_Y({make_point})))), 4326)")
            row_filter = f"btrim({xc}) ~ '{_FLOAT}' AND btrim({yc}) ~ '{_FLOAT}'"
        cur.execute(
            f"INSERT INTO {q(schema)}.{q(table)} ({copy_cols}, geom) "
            f"SELECT {', '.join(cast_expr(f) for f in fields)}, {geom} "
            f"FROM {q(schema)}.{q(stg)} WHERE {row_filter}")
        cur.execute(f"DROP TABLE {q(schema)}.{q(stg)}")
        if wkt_col:
            # Rows whose WKT failed to parse (or clipped to empty) carry NULL geometry — drop them
            # so every remaining feature is renderable.
            cur.execute(f"DELETE FROM {q(schema)}.{q(table)} WHERE geom IS NULL")
        cur.execute(f"SELECT count(*) FROM {q(schema)}.{q(table)}")
        fc = cur.fetchone()[0]
        if not fc:
            cur.execute(f"DROP TABLE {q(schema)}.{q(table)}")
            raise ValueError("No rows had valid WKT geometry." if wkt_col
                             else "No rows had valid numeric X/Y values.")
        geom_kind = "point"
        if wkt_col:
            cur.execute(f"SELECT GeometryType(geom) FROM {q(schema)}.{q(table)} "
                        f"WHERE geom IS NOT NULL LIMIT 1")
            row = cur.fetchone()
            geom_kind = _GEOM_KIND.get((row[0] or "").upper(), "polygon") if row else "polygon"
        cur.execute(f"CREATE INDEX ON {q(schema)}.{q(table)} USING GIST (geom)")
        # bbox is stored EPSG:4326 app-wide even for native geometry — transform the extent box only.
        extent = (f"ST_Transform(ST_SetSRID(ST_Extent(geom)::geometry, {srid}), 4326)" if native
                  else "ST_Extent(geom)")
        cur.execute(f"SELECT ST_XMin(e), ST_YMin(e), ST_XMax(e), ST_YMax(e) "
                    f"FROM (SELECT {extent} e FROM {q(schema)}.{q(table)}) s")
        b = cur.fetchone()
        bbox = [b[0], b[1], b[2], b[3]] if b and b[0] is not None else None
        conn.commit()
        return {"bbox": bbox, "feature_count": fc, "geometry_type": geom_kind,
                "crs": f"EPSG:{srid}",
                "columns": [{"name": safe[f], "type": types[f]} for f in fields]}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@celery_app.task(bind=True, name="geodeploy.tasks.csv_import.import_csv")
def import_csv(self, job_id, layer_id, source, schema, table, x_col, y_col, srid, is_s3=True,
               delimiter="comma", wkt_col=None):
    """source = S3 key (is_s3) or a local temp CSV path (uploaded). Geometry from X/Y columns
    (points) or, when `wkt_col` is set, from a WKT column (any geometry type)."""
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
        res = _load_copy(path, schema, table, x_col, y_col, srid, dsn, delimiter, wkt_col)

        step("Saving metadata", 90)
        _update_layer(db_path, layer_id, status="ready", feature_count=res["feature_count"],
                      bbox=json.dumps(res["bbox"]) if res["bbox"] else None,
                      columns=json.dumps(res["columns"]), crs=res.get("crs", "EPSG:4326"),
                      geometry_type=res.get("geometry_type", "point"),
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
