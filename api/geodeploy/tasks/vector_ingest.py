"""
Vector ingest pipeline: uploaded file → PostGIS table → Martin MVT endpoint.

Loads via COPY: features are streamed to a temp CSV (geometry as WKB hex), COPYd into an UNLOGGED
staging table, then a single INSERT…SELECT reprojects to EPSG:4326 IN PostGIS (ST_Transform) into
the final table. Streams from disk (no in-memory feature list) and bulk-loads — fast on large files.

HEAVY files skip PostGIS entirely: a source above `VECTOR_GEOPARQUET_THRESHOLD_MB` (default 200,
env-tunable on celery; 0 disables) is converted to GeoParquet on object storage instead
(`_ingest_as_geoparquet`) and follows the lakehouse path — spatial prep (partitioning + covering),
deck.gl display, DuckDB analysis — exactly as if the user had uploaded a .parquet directly.
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


def _get_layer_user(db_path: str, layer_id: int) -> int | None:
    import sqlite3
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT user_id FROM vector_layers WHERE id = ?", (layer_id,)).fetchone()
        return row[0] if row else None


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

        # Heavy files go to the GeoParquet lakehouse instead of PostGIS: cheaper to serve
        # (deck.gl viewport reads off object storage, no multi-GB PostGIS table + MVT cost)
        # and directly analysable with DuckDB. Same downstream pipeline as a .parquet upload.
        threshold_mb = float(os.getenv("VECTOR_GEOPARQUET_THRESHOLD_MB", "200"))
        if threshold_mb > 0 and _source_size(src_path) >= threshold_mb * 1024 * 1024:
            _ingest_as_geoparquet(db_path, job_id, layer_id, src_path, layer_name, step, settings)
            return

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


def _source_size(src_path: str) -> int:
    """Uncompressed size of the dataset: for an extracted shapefile that's the whole sidecar set
    (.shp + .dbf + …), so sum the extraction dir; otherwise the single file."""
    d = os.path.dirname(src_path)
    if d.endswith("_extracted"):
        return sum(os.path.getsize(os.path.join(root, f))
                   for root, _, files in os.walk(d) for f in files)
    return os.path.getsize(src_path)


# shapely geom_type → the catalog's point/line/polygon vocabulary (first type wins).
_GEOM_KIND = {
    "Point": "point", "MultiPoint": "point",
    "LineString": "line", "MultiLineString": "line",
    "Polygon": "polygon", "MultiPolygon": "polygon",
}


def _ingest_as_geoparquet(db_path: str, job_id: str, layer_id: int, src_path: str,
                          layer_name: str, step, settings) -> None:
    """Heavy-file path: convert the source to GeoParquet (EPSG:4326, WKB) on object storage and
    chain the spatial prep — the layer becomes a `storage_backend='geoparquet'` layer exactly like
    a direct .parquet upload (geoparquet_import); prep marks the layer + job ready."""
    from .raster_ingest import _get_storage_creds
    from .geoparquet_prep import prepare_geoparquet, _s3_client

    step("Converting to GeoParquet", 20)
    out_path = os.path.join(settings.data_dir, "temp", f"{uuid.uuid4().hex}.parquet")
    try:
        res = _convert_to_geoparquet(src_path, out_path)

        step("Uploading to storage", 60)
        creds = _get_storage_creds(db_path)
        user_id = _get_layer_user(db_path, layer_id) or 0
        safe = layer_name or "layer"
        s3_key = f"vectors/{user_id}/{uuid.uuid4().hex}/{safe}.parquet"
        _s3_client(creds).upload_file(out_path, creds["bucket"], s3_key)
    finally:
        if os.path.exists(out_path):
            os.unlink(out_path)

    step("Queueing spatial prep", 80)
    _update_layer(db_path, layer_id,
                  status="processing",
                  storage_backend="geoparquet", s3_key=s3_key,
                  geometry_type=res["geom_type"], geometry_column="geometry",
                  crs="EPSG:4326", feature_count=res["count"],
                  bbox=json.dumps(res["bbox"]) if res["bbox"] else None,
                  columns=json.dumps(res["columns"]), tile_status="none",
                  updated_at=datetime.now(timezone.utc).isoformat())
    prepare_geoparquet.delay(layer_id, s3_key, job_id)


def _convert_to_geoparquet(src_path: str, out_path: str, batch_size: int = 20_000) -> dict:
    """Stream Fiona features → GeoParquet 1.1 (WKB geometry, EPSG:4326, zstd). Batched: shapely
    WKB/bounds/reprojection run vectorised per batch (C, GIL released), so a multi-GB file never
    materialises in memory. The `geo` footer metadata (geometry types + bbox) is attached at close
    via ParquetWriter.add_key_value_metadata (bbox is only known at the end of the stream)."""
    import numpy as np
    import pyarrow as pa
    import pyarrow.parquet as pq
    import shapely
    from shapely import bounds as shp_bounds, to_wkb

    _PA_TYPE = {"int": pa.int64(), "int32": pa.int64(), "int64": pa.int64(),
                "float": pa.float64(), "bool": pa.bool_()}

    with fiona.open(src_path) as src:
        col_schema = src.schema["properties"]
        cols = list(col_schema.keys())
        crs_wkt = src.crs_wkt
        srid = _srid_of(crs_wkt)

        reproject = None
        if crs_wkt and srid != 4326:
            from pyproj import CRS, Transformer
            _tr = Transformer.from_crs(CRS.from_wkt(crs_wkt), CRS.from_epsg(4326), always_xy=True)

            def _tr_coords(coords):
                x, y = _tr.transform(coords[:, 0], coords[:, 1])
                return np.column_stack([x, y])
            reproject = lambda geoms: shapely.transform(geoms, _tr_coords)  # noqa: E731

        def _base(c):
            return str(col_schema[c]).lower().split(":")[0]
        schema = pa.schema([pa.field(c, _PA_TYPE.get(_base(c), pa.string())) for c in cols]
                           + [pa.field("geometry", pa.binary())])

        def _coerce(v, t):
            if v is None:
                return None
            if pa.types.is_int64(t):
                return int(v)
            if pa.types.is_float64(t):
                return float(v)
            if pa.types.is_boolean(t):
                return bool(v)
            return v.isoformat() if hasattr(v, "isoformat") else str(v)

        writer = pq.ParquetWriter(out_path, schema, compression="zstd")
        count = 0
        bbox = [float("inf"), float("inf"), float("-inf"), float("-inf")]
        geom_types: set[str] = set()
        pend_props: list[tuple] = []
        pend_geoms: list = []

        def _flush():
            nonlocal count
            if not pend_geoms:
                return
            geoms = np.array(pend_geoms, dtype=object)
            if reproject is not None:
                geoms = reproject(geoms)
            b = shp_bounds(geoms)
            bbox[0] = min(bbox[0], float(np.nanmin(b[:, 0])))
            bbox[1] = min(bbox[1], float(np.nanmin(b[:, 1])))
            bbox[2] = max(bbox[2], float(np.nanmax(b[:, 2])))
            bbox[3] = max(bbox[3], float(np.nanmax(b[:, 3])))
            for g in set(shapely.get_type_id(geoms).tolist()):
                name = shapely.GeometryType(g).name  # e.g. POLYGON
                geom_types.add({"POINT": "Point", "LINESTRING": "LineString", "POLYGON": "Polygon",
                                "MULTIPOINT": "MultiPoint", "MULTILINESTRING": "MultiLineString",
                                "MULTIPOLYGON": "MultiPolygon",
                                "GEOMETRYCOLLECTION": "GeometryCollection"}.get(name, name.title()))
            arrays = [pa.array([_coerce(row[j], schema.field(c).type) for row in pend_props],
                               type=schema.field(c).type)
                      for j, c in enumerate(cols)]
            arrays.append(pa.array([bytes(w) for w in to_wkb(geoms)], type=pa.binary()))
            writer.write_table(pa.Table.from_arrays(arrays, schema=schema))
            count += len(pend_geoms)
            pend_props.clear()
            pend_geoms.clear()

        try:
            for feat in src:
                g = feat["geometry"]
                if g is None:
                    continue
                pend_geoms.append(shp_shape(g))
                props = feat["properties"]
                pend_props.append(tuple(props.get(c) for c in cols))
                if len(pend_geoms) >= batch_size:
                    _flush()
            _flush()
            if count == 0:
                raise ValueError("No features with geometry found in the file.")
            # GeoParquet 1.1 footer metadata. CRS is omitted → OGC:CRS84 (lon/lat), which is what
            # we wrote. Everything downstream (inspect_parquet, partition_with_covering, the
            # viewport queries) reads WKB without DuckDB-spatial, so no spec-version wall here.
            # add_key_value_metadata needs pyarrow ≥ 18 (the image pins 18.1); on older pyarrow
            # the file is still fine — every reader falls back to the geometry-column-name
            # heuristic + a WKB scan when the geo footer is absent.
            geo = {"version": "1.1.0", "primary_column": "geometry",
                   "columns": {"geometry": {"encoding": "WKB",
                                            "geometry_types": sorted(geom_types),
                                            "bbox": bbox}}}
            if hasattr(writer, "add_key_value_metadata"):
                writer.add_key_value_metadata({"geo": json.dumps(geo)})
        finally:
            writer.close()

    kind = next((_GEOM_KIND[t] for t in ("Polygon", "MultiPolygon", "LineString",
                                         "MultiLineString", "Point", "MultiPoint")
                 if t in geom_types), "polygon")
    return {"count": count, "bbox": bbox if bbox[0] != float("inf") else None,
            "geom_type": kind,
            "columns": [{"name": c, "type": str(col_schema[c])} for c in cols]}


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
