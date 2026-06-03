"""
Vector ingest pipeline: uploaded file → PostGIS table → Martin MVT endpoint.
Steps: validate → reproject → load PostGIS → spatial index → metadata → regenerate Martin config
"""
import json
import os
import tempfile
import zipfile
from datetime import datetime, timezone

import fiona
import fiona.transform
import psycopg2
from shapely import wkt
from shapely.geometry import shape, mapping
from shapely.ops import unary_union

from ..celery_app import celery_app
from ..config import get_settings
from ..services import martin as martin_svc


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
            "SELECT schema_name, table_name FROM vector_layers WHERE status = 'ready' AND storage_backend = 'postgis'"
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

        with fiona.open(src_path) as src:
            original_crs = src.crs_wkt
            geom_type = src.schema["geometry"]
            col_schema = src.schema["properties"]
            features = list(src)

        step("Reprojecting to EPSG:4326", 20)
        if original_crs:
            projected = _reproject_features(features, original_crs, src_path)
        else:
            projected = features

        step("Loading into PostGIS", 40)
        setup = _get_setup(db_path)
        dsn = f"host={setup['postgis_host']} port={setup['postgis_port']} dbname={setup['postgis_db']} user={setup['postgis_user']} password={setup['postgis_password']}"
        # External/managed DBs may require SSL; local provisioned DB leaves this empty.
        if settings.postgis_sslmode:
            dsn += f" sslmode={settings.postgis_sslmode}"

        bbox, feature_count = _load_into_postgis(dsn, schema_name, table_name, projected, col_schema, geom_type)

        step("Building spatial index", 80)
        _create_spatial_index(dsn, schema_name, table_name)

        step("Saving metadata", 90)
        columns_json = json.dumps([{"name": k, "type": str(v)} for k, v in col_schema.items()])

        _update_layer(db_path, layer_id,
                      status="ready",
                      feature_count=feature_count,
                      bbox=json.dumps(bbox),
                      columns=columns_json,
                      geometry_type=geom_type,
                      crs="EPSG:4326",
                      updated_at=datetime.now(timezone.utc).isoformat())

        step("Updating tile server", 95)
        all_layers = _get_all_layers(db_path)
        import asyncio
        asyncio.run(martin_svc.regenerate_config(all_layers))

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


def _reproject_features(features, src_crs_wkt: str, src_path: str) -> list:
    from pyproj import CRS, Transformer
    src_crs = CRS.from_wkt(src_crs_wkt)
    dst_crs = CRS.from_epsg(4326)
    if src_crs == dst_crs:
        return features
    transformer = Transformer.from_crs(src_crs, dst_crs, always_xy=True)

    reprojected = []
    for feat in features:
        geom = shape(feat["geometry"])
        coords = list(geom.geoms) if hasattr(geom, "geoms") else [geom]
        # Use fiona's transform for correctness with complex geometries
        try:
            new_geom = fiona.transform.transform_geom(
                f"EPSG:{src_crs.to_epsg()}" if src_crs.to_epsg() else src_crs_wkt,
                "EPSG:4326",
                feat["geometry"],
            )
        except Exception:
            new_geom = feat["geometry"]
        reprojected.append({**feat, "geometry": new_geom})
    return reprojected


def _load_into_postgis(dsn: str, schema: str, table: str, features: list, col_schema: dict, geom_type: str) -> tuple[list, int]:
    type_map = {
        "int": "INTEGER", "float": "DOUBLE PRECISION", "str": "TEXT", "date": "DATE",
    }

    col_defs = ", ".join(
        f'"{name}" {type_map.get(str(ftype).lower().split(":")[0], "TEXT")}'
        for name, ftype in col_schema.items()
    )

    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        cur.execute(f'CREATE EXTENSION IF NOT EXISTS postgis')
        cur.execute(f'DROP TABLE IF EXISTS "{schema}"."{table}"')
        cur.execute(f'''
            CREATE TABLE "{schema}"."{table}" (
                id SERIAL PRIMARY KEY,
                geom geometry(Geometry, 4326),
                {col_defs}
            )
        ''')

        cols_list = list(col_schema.keys())
        placeholders = ", ".join(["%s"] * (len(cols_list) + 1))
        insert_sql = f'''
            INSERT INTO "{schema}"."{table}" (geom, {", ".join(f'"{c}"' for c in cols_list)})
            VALUES (ST_GeomFromGeoJSON(%s), {", ".join(["%s"] * len(cols_list))})
        '''

        minx, miny, maxx, maxy = 180, 90, -180, -90
        count = 0
        batch = []

        for feat in features:
            if feat["geometry"] is None:
                continue
            geom_str = json.dumps(mapping(shape(feat["geometry"])))
            row_vals = [geom_str] + [feat["properties"].get(c) for c in cols_list]
            batch.append(tuple(row_vals))

            # Track bbox
            coords = _extract_coords(feat["geometry"])
            for x, y in coords:
                minx = min(minx, x); miny = min(miny, y)
                maxx = max(maxx, x); maxy = max(maxy, y)
            count += 1

            if len(batch) >= 1000:
                cur.executemany(insert_sql, batch)
                batch = []

        if batch:
            cur.executemany(insert_sql, batch)

        conn.commit()

    return [minx, miny, maxx, maxy], count


def _create_spatial_index(dsn: str, schema: str, table: str) -> None:
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(f'CREATE INDEX IF NOT EXISTS "{table}_geom_idx" ON "{schema}"."{table}" USING GIST (geom)')
        conn.commit()


def _extract_coords(geom: dict) -> list[tuple]:
    coords = []
    gtype = geom.get("type", "")
    raw = geom.get("coordinates", [])
    if gtype == "Point":
        coords.append((raw[0], raw[1]))
    elif gtype in ("MultiPoint", "LineString"):
        coords.extend((c[0], c[1]) for c in raw)
    elif gtype in ("MultiLineString", "Polygon"):
        for ring in raw:
            coords.extend((c[0], c[1]) for c in ring)
    elif gtype == "MultiPolygon":
        for poly in raw:
            for ring in poly:
                coords.extend((c[0], c[1]) for c in ring)
    return coords
