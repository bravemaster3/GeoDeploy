"""
Celery task: clip selected portal layers to a bbox and build a ZIP in data/temp/exports.
Runs in the worker so heavy raster/vector clipping never blocks the API process.
Vector via psycopg2 (+ ogr2ogr for GeoPackage); raster via rasterio (windowed, capped).
"""
import io
import json
import logging
import os
import subprocess
import tempfile
import zipfile

from ..celery_app import celery_app
from ..config import get_settings

log = logging.getLogger(__name__)

FEATURE_CAP = 50000          # max features per vector layer
MAX_PIXELS = 16_000_000      # raster output cap (~4000x4000) — bigger selections are downsampled


def _safe(name: str) -> str:
    from slugify import slugify
    return slugify(name or "layer", separator="_") or "layer"


def _env_sql(srid: int) -> str:
    """Clip envelope. The bbox is ALWAYS EPSG:4326 (the map view); transform it INTO the table's SRID so
    the spatial index is used and the &&/ST_Intersects test happens in the geometry's own CRS. Required
    even for a 4326 output, now that geometry may be stored natively."""
    return (f"ST_Transform(ST_MakeEnvelope(%s,%s,%s,%s,4326), {int(srid)})" if int(srid) != 4326
            else "ST_MakeEnvelope(%s,%s,%s,%s,4326)")


def _table_srid(cur, schema: str, table: str) -> int:
    """The stored SRID of the geom column (native since the native-CRS ingest change)."""
    try:
        cur.execute(f'SELECT ST_SRID(geom) FROM "{schema}"."{table}" WHERE geom IS NOT NULL LIMIT 1')
        r = cur.fetchone()
        return int(r[0]) if r and r[0] else 4326
    except Exception:
        return 4326


def _geom_out(srid: int, out_srid: int) -> str:
    return "geom" if int(srid) == int(out_srid) else f"ST_Transform(geom, {int(out_srid)})"


def _vec_geojson(cur, schema: str, table: str, b, srid: int, out_srid: int) -> str:
    env = _env_sql(srid)
    sql = (
        "SELECT jsonb_build_object('type','FeatureCollection','features',"
        "COALESCE(jsonb_agg(f.feat), '[]'::jsonb))::text FROM ("
        "  SELECT jsonb_build_object('type','Feature',"
        f"    'geometry', ST_AsGeoJSON({_geom_out(srid, out_srid)})::jsonb,"
        "    'properties', to_jsonb(t) - 'geom') AS feat"
        f'  FROM "{schema}"."{table}" t'
        f"  WHERE geom && {env} AND ST_Intersects(geom, {env})"
        f"  LIMIT {FEATURE_CAP}"
        ") f"
    )
    cur.execute(sql, (b[0], b[1], b[2], b[3], b[0], b[1], b[2], b[3]))
    row = cur.fetchone()
    return row[0] if row and row[0] else '{"type":"FeatureCollection","features":[]}'


def _vec_csv(cur, schema: str, table: str, b, srid: int, out_srid: int) -> str:
    import csv
    env = _env_sql(srid)
    sql = (
        f"SELECT (to_jsonb(t) - 'geom')::text AS props, ST_AsText({_geom_out(srid, out_srid)}) AS wkt "
        f'FROM "{schema}"."{table}" t '
        f"WHERE geom && {env} AND ST_Intersects(geom, {env}) LIMIT {FEATURE_CAP}"
    )
    cur.execute(sql, (b[0], b[1], b[2], b[3], b[0], b[1], b[2], b[3]))
    cols, recs = [], []
    for props_text, wkt in cur.fetchall():
        props = json.loads(props_text)
        props["geometry_wkt"] = wkt
        recs.append(props)
        for k in props:
            if k not in cols:
                cols.append(k)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols)
    w.writeheader()
    for rec in recs:
        w.writerow(rec)
    return buf.getvalue()


def _gpq_features(s3_key: str, b, settings, keep_native: bool = False) -> list[dict]:
    """Clip a GeoParquet layer to the bbox via DuckDB (covering/partition-pruned viewport query).
    `keep_native=True` → geometries come back in the file's OWN CRS (lossless download); the exact
    4326-intersects refinement is then skipped (the covering prune, done in the file CRS, already
    limits to the region — a few edge near-misses are acceptable for a download). Default (4326) keeps
    the exact shapely intersects test for parity with the PostGIS path."""
    from shapely.geometry import box as shp_box, shape as gj_shape
    from ..services import duckdb_engine
    from .raster_ingest import _get_storage_creds
    # Storage creds from SQLite (§0f) — celery env is unreliable.
    creds = _get_storage_creds(f"{settings.data_dir}/sqlite/geodeploy.db")
    fc = duckdb_engine.query_features_geojson(s3_key, list(b), FEATURE_CAP, creds, keep_native=keep_native)
    feats = fc.get("features", [])
    if keep_native:
        return feats  # already pruned to the bbox in the file CRS; geometries are native
    sel = shp_box(b[0], b[1], b[2], b[3])
    kept = []
    for f in feats:
        try:
            if gj_shape(f["geometry"]).intersects(sel):
                kept.append(f)
        except Exception:  # noqa: BLE001 — a single bad geometry shouldn't kill the export
            continue
    return kept


def _gpq_geojson(feats: list[dict]) -> str:
    return json.dumps({"type": "FeatureCollection", "features": feats}, separators=(",", ":"))


def _gpq_csv(feats: list[dict]) -> str:
    import csv
    from shapely.geometry import shape as gj_shape
    cols, recs = [], []
    for f in feats:
        props = dict(f.get("properties") or {})
        try:
            props["geometry_wkt"] = gj_shape(f["geometry"]).wkt
        except Exception:  # noqa: BLE001
            props["geometry_wkt"] = None
        recs.append(props)
        for k in props:
            if k not in cols:
                cols.append(k)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols)
    w.writeheader()
    for rec in recs:
        w.writerow(rec)
    return buf.getvalue()


def _gj_to_gpkg(geojson_text: str, layer_name: str, srs: str = "EPSG:4326") -> bytes:
    """The `geojson_text` carries coordinates in `srs` (native for a lossless download, else 4326);
    label it with the matching `-a_srs` so the GeoPackage records the correct CRS."""
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "in.geojson")
        out = os.path.join(td, "out.gpkg")
        with open(src, "w", encoding="utf-8") as f:
            f.write(geojson_text)
        r = subprocess.run(
            ["ogr2ogr", "-f", "GPKG", "-a_srs", srs, "-nln", layer_name, out, src],
            capture_output=True,
        )
        if r.returncode != 0:
            raise RuntimeError("ogr2ogr failed: " + r.stderr.decode("utf-8", "ignore")[:300])
        if not os.path.exists(out) or os.path.getsize(out) == 0:
            raise RuntimeError("ogr2ogr produced no output")
        with open(out, "rb") as f:
            return f.read()


def _clip_raster(s3_key: str, b, settings) -> bytes:
    import rasterio
    from rasterio import Affine
    from rasterio.session import AWSSession
    from rasterio.windows import Window, from_bounds
    from rasterio.warp import transform_bounds
    minx, miny, maxx, maxy = b
    # rasterio >=1.4 forbids passing AWS credentials into Env directly — they must go through a
    # boto3 session. Non-credential GDAL/VSI options (endpoint, http, path-style) stay as Env kwargs.
    endpoint = settings.storage_endpoint.replace("https://", "").replace("http://", "")
    use_https = settings.storage_endpoint.lower().startswith("https")
    session = AWSSession(
        aws_access_key_id=settings.storage_access_key,
        aws_secret_access_key=settings.storage_secret_key,
        endpoint_url=endpoint,
    )
    with rasterio.Env(
        session,
        AWS_S3_ENDPOINT=endpoint,
        AWS_HTTPS="YES" if use_https else "NO",
        AWS_VIRTUAL_HOSTING="FALSE",
        GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
    ):
        with rasterio.open(f"s3://{settings.storage_bucket}/{s3_key}") as ds:
            west, south, east, north = transform_bounds("EPSG:4326", ds.crs, minx, miny, maxx, maxy, densify_pts=21)
            win = from_bounds(west, south, east, north, ds.transform).round_offsets().round_lengths()
            win = win.intersection(Window(0, 0, ds.width, ds.height))
            if win.width < 1 or win.height < 1:
                raise ValueError("no-overlap")
            # Cap output size — downsample huge selections (fast via COG overviews).
            scale = 1.0
            total = win.width * win.height
            if total > MAX_PIXELS:
                scale = (MAX_PIXELS / total) ** 0.5
            out_w = max(1, int(win.width * scale))
            out_h = max(1, int(win.height * scale))
            data = ds.read(window=win, out_shape=(ds.count, out_h, out_w))
            transform = ds.window_transform(win) * Affine.scale(win.width / out_w, win.height / out_h)
            profile = ds.profile.copy()
            profile.update(driver="GTiff", height=out_h, width=out_w, transform=transform, compress="lzw")
            # Tiling/block sizes from the source COG can be invalid for a small clip; let GDAL pick.
            for k in ("blockxsize", "blockysize", "tiled", "interleave", "photometric"):
                profile.pop(k, None)
            # GTiff must be written through a real (seekable) GDAL dataset — a plain BytesIO
            # yields a truncated/empty file. MemoryFile is the supported in-memory writer.
            from rasterio.io import MemoryFile
            with MemoryFile() as memfile:
                with memfile.open(**profile) as dst:
                    dst.write(data)
                return memfile.read()


@celery_app.task(bind=True, name="geodeploy.tasks.export.export_bundle")
def export_bundle(self, bbox: str, items: list[dict], target_crs: str = "4326") -> dict:
    """items: [{type:'vector', schema, table, name, format} |
              {type:'geoparquet', s3_key, crs, name, format} | {type:'raster', s3_key, name}]
    target_crs: '4326' (default) or 'native' → GeoPackage/CSV carry the layer's native CRS (lossless);
    GeoJSON is always EPSG:4326 (RFC 7946)."""
    settings = get_settings()
    native = (target_crs == "native")
    b = tuple(float(v) for v in bbox.split(","))
    exports_dir = f"{settings.data_dir}/temp/exports"
    os.makedirs(exports_dir, exist_ok=True)
    out_path = os.path.join(exports_dir, f"{self.request.id}.zip")
    # Build under a temp name and atomically rename when fully written — the status
    # endpoint treats the existence of {id}.zip as "ready", so it must only appear complete.
    tmp_path = out_path + ".part"

    used: set[str] = set()

    def fn(base: str, ext: str) -> str:
        name = f"{base}.{ext}"
        i = 1
        while name in used:
            name = f"{base}_{i}.{ext}"
            i += 1
        used.add(name)
        return name

    import psycopg2
    # Build the DSN from the SQLite setup_config (authoritative) rather than env settings —
    # the celery container's POSTGIS_PASSWORD isn't reliably populated. See csv_import.
    from .vector_ingest import _get_setup
    setup = _get_setup(f"{settings.data_dir}/sqlite/geodeploy.db")
    if not setup:
        raise ValueError("Setup is not complete — no database configured.")
    dsn = (f"host={setup['postgis_host']} port={setup['postgis_port']} dbname={setup['postgis_db']} "
           f"user={setup['postgis_user']} password={setup['postgis_password']}")
    if settings.postgis_sslmode:
        dsn += f" sslmode={settings.postgis_sslmode}"
    conn = psycopg2.connect(dsn)
    try:
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as z:
            cur = conn.cursor()
            for it in items:
                if it.get("type") == "vector":
                    base = _safe(it.get("name"))
                    fmt = it.get("format", "geojson")
                    srid = _table_srid(cur, it["schema"], it["table"])
                    # GeoJSON is ALWAYS 4326 (RFC 7946). CSV/GPKG carry the native SRID when requested.
                    out_srid = srid if (native and fmt in ("csv", "gpkg")) else 4326
                    if fmt == "csv":
                        z.writestr(fn(base, "csv"), _vec_csv(cur, it["schema"], it["table"], b, srid, out_srid))
                    elif fmt == "gpkg":
                        gj = _vec_geojson(cur, it["schema"], it["table"], b, srid, out_srid)
                        try:
                            z.writestr(fn(base, "gpkg"), _gj_to_gpkg(gj, base, f"EPSG:{out_srid}"))
                        except Exception as e:
                            log.warning("GeoPackage export failed for %s, falling back to GeoJSON: %s", base, e)
                            z.writestr(fn(base, "geojson"), gj)
                    else:
                        z.writestr(fn(base, "geojson"), _vec_geojson(cur, it["schema"], it["table"], b, srid, 4326))
                elif it.get("type") == "geoparquet":
                    base = _safe(it.get("name"))
                    fmt = it.get("format", "geojson")
                    # native only for CSV/GPKG; GeoJSON must be 4326.
                    keep_native = native and fmt in ("csv", "gpkg")
                    feats = _gpq_features(it["s3_key"], b, settings, keep_native=keep_native)
                    out_crs = (it.get("crs") or "EPSG:4326") if keep_native else "EPSG:4326"
                    if fmt == "csv":
                        z.writestr(fn(base, "csv"), _gpq_csv(feats))
                    elif fmt == "gpkg":
                        gj = _gpq_geojson(feats)
                        try:
                            z.writestr(fn(base, "gpkg"), _gj_to_gpkg(gj, base, out_crs))
                        except Exception as e:
                            log.warning("GeoPackage export failed for %s, falling back to GeoJSON: %s", base, e)
                            z.writestr(fn(base, "geojson"), _gpq_geojson(_gpq_features(it["s3_key"], b, settings)))
                    else:
                        z.writestr(fn(base, "geojson"), _gpq_geojson(feats))
                else:  # raster
                    try:
                        data = _clip_raster(it["s3_key"], b, settings)
                    except ValueError:
                        log.info("Raster %s does not overlap the selection — skipped.", it.get("name"))
                        continue  # no overlap
                    except Exception:
                        log.exception("Raster clip failed for %s", it.get("name"))
                        raise
                    z.writestr(fn(_safe(it.get("name")) + "_clip", "tif"), data)
            cur.close()
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise
    finally:
        conn.close()

    os.replace(tmp_path, out_path)  # atomic publish — only now does status flip to "ready"
    return {"path": out_path}
