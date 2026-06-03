"""
Celery task: clip selected portal layers to a bbox and build a ZIP in data/temp/exports.
Runs in the worker so heavy raster/vector clipping never blocks the API process.
Vector via psycopg2 (+ ogr2ogr for GeoPackage); raster via rasterio (windowed, capped).
"""
import io
import json
import os
import subprocess
import tempfile
import zipfile

from ..celery_app import celery_app
from ..config import get_settings

FEATURE_CAP = 50000          # max features per vector layer
MAX_PIXELS = 16_000_000      # raster output cap (~4000x4000) — bigger selections are downsampled
_ENV = "ST_MakeEnvelope(%s,%s,%s,%s,4326)"


def _safe(name: str) -> str:
    from slugify import slugify
    return slugify(name or "layer", separator="_") or "layer"


def _vec_geojson(cur, schema: str, table: str, b) -> str:
    sql = (
        "SELECT jsonb_build_object('type','FeatureCollection','features',"
        "COALESCE(jsonb_agg(f.feat), '[]'::jsonb))::text FROM ("
        "  SELECT jsonb_build_object('type','Feature',"
        "    'geometry', ST_AsGeoJSON(geom)::jsonb,"
        "    'properties', to_jsonb(t) - 'geom') AS feat"
        f'  FROM "{schema}"."{table}" t'
        f"  WHERE geom && {_ENV} AND ST_Intersects(geom, {_ENV})"
        f"  LIMIT {FEATURE_CAP}"
        ") f"
    )
    cur.execute(sql, (b[0], b[1], b[2], b[3], b[0], b[1], b[2], b[3]))
    row = cur.fetchone()
    return row[0] if row and row[0] else '{"type":"FeatureCollection","features":[]}'


def _vec_csv(cur, schema: str, table: str, b) -> str:
    import csv
    sql = (
        "SELECT (to_jsonb(t) - 'geom')::text AS props, ST_AsText(geom) AS wkt "
        f'FROM "{schema}"."{table}" t '
        f"WHERE geom && {_ENV} AND ST_Intersects(geom, {_ENV}) LIMIT {FEATURE_CAP}"
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


def _gj_to_gpkg(geojson_text: str, layer_name: str) -> bytes:
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "in.geojson")
        out = os.path.join(td, "out.gpkg")
        with open(src, "w", encoding="utf-8") as f:
            f.write(geojson_text)
        r = subprocess.run(
            ["ogr2ogr", "-f", "GPKG", "-nln", layer_name, out, src],
            capture_output=True,
        )
        if r.returncode != 0:
            raise RuntimeError(r.stderr.decode("utf-8", "ignore")[:300])
        with open(out, "rb") as f:
            return f.read()


def _clip_raster(s3_key: str, b, settings) -> bytes:
    import rasterio
    from rasterio import Affine
    from rasterio.windows import Window, from_bounds
    from rasterio.warp import transform_bounds
    minx, miny, maxx, maxy = b
    env = {
        "AWS_ACCESS_KEY_ID": settings.storage_access_key,
        "AWS_SECRET_ACCESS_KEY": settings.storage_secret_key,
        "AWS_S3_ENDPOINT": settings.storage_endpoint.replace("https://", "").replace("http://", ""),
        "AWS_HTTPS": "NO", "AWS_VIRTUAL_HOSTING": "FALSE",
        "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    }
    with rasterio.Env(**env):
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
            buf = io.BytesIO()
            with rasterio.open(buf, "w", **profile) as out:
                out.write(data)
            return buf.getvalue()


@celery_app.task(bind=True, name="geodeploy.tasks.export.export_bundle")
def export_bundle(self, bbox: str, items: list[dict]) -> dict:
    """items: [{type:'vector', schema, table, name, format} | {type:'raster', s3_key, name}]"""
    settings = get_settings()
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
    conn = psycopg2.connect(settings.postgis_sync_dsn)
    try:
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as z:
            cur = conn.cursor()
            for it in items:
                if it.get("type") == "vector":
                    base = _safe(it.get("name"))
                    fmt = it.get("format", "geojson")
                    if fmt == "csv":
                        z.writestr(fn(base, "csv"), _vec_csv(cur, it["schema"], it["table"], b))
                    elif fmt == "gpkg":
                        gj = _vec_geojson(cur, it["schema"], it["table"], b)
                        try:
                            z.writestr(fn(base, "gpkg"), _gj_to_gpkg(gj, base))
                        except Exception:
                            z.writestr(fn(base, "geojson"), gj)
                    else:
                        z.writestr(fn(base, "geojson"), _vec_geojson(cur, it["schema"], it["table"], b))
                else:  # raster
                    try:
                        data = _clip_raster(it["s3_key"], b, settings)
                    except ValueError:
                        continue  # no overlap
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
