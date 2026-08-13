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
from datetime import datetime, timezone

from ..celery_app import celery_app
from ..config import get_settings

log = logging.getLogger(__name__)

FEATURE_CAP = 50000          # max features per vector layer, for a BBOX clip
# A whole-layer download is a different request: 50k would silently hand back a fraction of a
# large layer and call it the layer. Env-tunable because the honest ceiling depends on the box.
FULL_EXPORT_CAP = int(os.getenv("EXPORT_FEATURE_CAP", "1000000"))
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


def _filter(b, srid: int):
    """`(WHERE clause, params, row cap)` for a clip — or for the whole layer when `b` is None.

    A whole-layer export deliberately emits NO spatial predicate rather than a world envelope: a
    world envelope transformed into a projected CRS is undefined near the poles, so it would drop
    rows from exactly the datasets (national, polar) where nobody would notice.
    """
    if b is None:
        return "", (), FULL_EXPORT_CAP
    env = _env_sql(srid)
    return (f"WHERE geom && {env} AND ST_Intersects(geom, {env})",
            (b[0], b[1], b[2], b[3], b[0], b[1], b[2], b[3]), FEATURE_CAP)


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


def _vec_geojson(cur, schema: str, table: str, b, srid: int, out_srid: int) -> tuple[str, int]:
    """`(GeoJSON text, row count)`. The count comes back from the same query — `count(*)` over the
    already-aggregated subquery is free, and without it a truncated export is indistinguishable
    from a complete one."""
    where, params, cap = _filter(b, srid)
    sql = (
        "SELECT jsonb_build_object('type','FeatureCollection','features',"
        "COALESCE(jsonb_agg(f.feat), '[]'::jsonb))::text, count(*) FROM ("
        "  SELECT jsonb_build_object('type','Feature',"
        f"    'geometry', ST_AsGeoJSON({_geom_out(srid, out_srid)})::jsonb,"
        "    'properties', to_jsonb(t) - 'geom') AS feat"
        f'  FROM "{schema}"."{table}" t'
        f"  {where}"
        f"  LIMIT {cap}"
        ") f"
    )
    cur.execute(sql, params)
    row = cur.fetchone()
    if not (row and row[0]):
        return '{"type":"FeatureCollection","features":[]}', 0
    return row[0], int(row[1] or 0)


def _vec_csv(cur, schema: str, table: str, b, srid: int, out_srid: int) -> tuple[str, int]:
    import csv
    where, params, cap = _filter(b, srid)
    sql = (
        f"SELECT (to_jsonb(t) - 'geom')::text AS props, ST_AsText({_geom_out(srid, out_srid)}) AS wkt "
        f'FROM "{schema}"."{table}" t '
        f"{where} LIMIT {cap}"
    )
    cur.execute(sql, params)
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
    return buf.getvalue(), len(recs)


def _gpq_features(s3_key: str, b, settings, keep_native: bool = False) -> tuple[list[dict], int]:
    """Clip a GeoParquet layer to the bbox via DuckDB (covering/partition-pruned viewport query).
    `keep_native=True` → geometries come back in the file's OWN CRS (lossless download); the exact
    4326-intersects refinement is then skipped (the covering prune, done in the file CRS, already
    limits to the region — a few edge near-misses are acceptable for a download). Default (4326) keeps
    the exact shapely intersects test for parity with the PostGIS path."""
    from shapely.geometry import box as shp_box, shape as gj_shape
    from ..services import duckdb_engine
    from .raster_ingest import _get_storage_creds
    # Storage creds from SQLite (§0f) — celery env is unreliable.
    creds = _get_storage_creds()
    cap = FEATURE_CAP if b is not None else FULL_EXPORT_CAP
    fc = duckdb_engine.query_features_geojson(s3_key, list(b) if b else None, cap, creds,
                                              keep_native=keep_native)
    feats = fc.get("features", [])
    # `read` is what DuckDB returned, BEFORE the refinement below. Truncation is a property of the
    # read, not of what survived the intersects test — a clip that drops rows on purpose is not the
    # same thing as a cap that dropped them silently.
    read = len(feats)
    if keep_native or b is None:
        return feats, read  # already pruned (or unfiltered, for a whole-layer download)
    sel = shp_box(b[0], b[1], b[2], b[3])
    kept = []
    for f in feats:
        try:
            if gj_shape(f["geometry"]).intersects(sel):
                kept.append(f)
        except Exception:  # noqa: BLE001 — a single bad geometry shouldn't kill the export
            continue
    return kept, read


def _gpq_parquet(s3_key: str, b, settings) -> tuple[bytes, int]:
    """Clip a GeoParquet layer to the bbox and return `(GeoParquet bytes, row count)`.

    Written to a temp file because pyarrow writes parquet to a path, then read back — parquet is a
    random-access format with a footer, so it cannot be streamed into the archive as it is produced.
    """
    import tempfile

    from ..services import duckdb_engine
    from .raster_ingest import _get_storage_creds
    creds = _get_storage_creds()
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "clip.parquet")
        rows = duckdb_engine.copy_features_parquet(
            s3_key, list(b) if b else None,
            FEATURE_CAP if b is not None else FULL_EXPORT_CAP, out, creds)
        with open(out, "rb") as f:
            return f.read(), int(rows or 0)


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


def _manifest(items: list[dict], bbox: str | None, target_crs: str, files: list[str],
              rows: dict[str, int] | None = None,
              truncated: list[dict] | None = None) -> str:
    """A plain-text note inside the zip: what was asked for, what came out, and — when the cap bit —
    how to get the data COMPLETE instead.

    An export that hit the row cap looks exactly like a complete one: same formats, same file
    names, fewer rows. Stating the cap was never enough, because the reader still cannot tell
    whether it applied to *them*; the per-file counts below are the part that answers that.
    """
    cap = FEATURE_CAP if bbox else FULL_EXPORT_CAP
    rows = rows or {}
    truncated = truncated or []
    lines = [
        "GeoDeploy export",
        "generated: {0}".format(datetime.now(timezone.utc).isoformat(timespec="seconds")),
        "extent:    {0}".format("bbox " + bbox + " (EPSG:4326)" if bbox else "whole layer, no clip"),
        "CRS:       {0}".format("native where the format allows" if target_crs == "native"
                                else "EPSG:4326"),
        "row cap:   {0} features per vector layer".format(cap),
        "complete:  {0}".format("NO — see the warning below" if truncated else "yes"),
        "",
        "layers requested:",
    ]
    for it in items:
        lines.append("  - {0} ({1}{2})".format(it.get("name"), it.get("type"),
                                               ", " + it["format"] if it.get("format") else ""))
    lines += ["", "files:"]
    for f in files:
        if f in rows:
            mark = "  — TRUNCATED AT THE CAP" if any(t["file"] == f for t in truncated) else ""
            lines.append("  - {0} — {1:,} rows{2}".format(f, rows[f], mark))
        else:
            lines.append("  - " + f)
    if truncated:
        lines += ["", "─" * 78, "WARNING — THIS EXPORT IS INCOMPLETE.", ""]
        for t in truncated:
            lines.append("  {0} stopped at the {1:,}-row cap.".format(t["file"], t["cap"]))
        lines += [
            "",
            "The rows you have are the ones the scan reached first — not a sample, and not the",
            "first N by any meaningful order. Do not treat this file as the dataset.",
            "",
            "Complete alternatives, none of which is capped:",
            "",
            "  1. OGC API - Features — paged, and GDAL follows the paging itself:",
            "       ogr2ogr -f GPKG out.gpkg \"OAPIF:<instance>/api/ogc\" <layer>",
            "     (the layer must have OGC sharing enabled)",
            "",
            "  2. GeoParquet layers — read the source files straight from storage:",
            "       <instance>/api/data/vector/<uid>/parquet/manifest.json",
            "       then each partition it lists, or in DuckDB:",
            "       SELECT * FROM read_parquet('<instance>/api/data/vector/<uid>/parquet/*.parquet')",
            "",
            "  3. Ask for a smaller area: the same export with a bbox returns everything inside it",
            "     (up to {0:,} features per layer).".format(FEATURE_CAP),
            "",
            "An administrator can also raise EXPORT_FEATURE_CAP, but the cap exists because the",
            "archive is assembled in worker memory — raising it far trades a truncated download",
            "for a failed one.",
            "─" * 78,
        ]
    return "\n".join(lines) + "\n"


@celery_app.task(bind=True, name="geodeploy.tasks.export.export_bundle")
def export_bundle(self, bbox: str | None, items: list[dict], target_crs: str = "4326") -> dict:
    """items: [{type:'vector', schema, table, name, format} |
              {type:'geoparquet', s3_key, crs, name, format} | {type:'raster', s3_key, name}]
    target_crs: '4326' (default) or 'native' → GeoPackage/CSV carry the layer's native CRS (lossless);
    GeoJSON is always EPSG:4326 (RFC 7946).

    `bbox` None = the WHOLE layer (the "download this dataset" path, added 2026-08-12) rather than a
    map-view clip. Rasters still require one: a whole raster is already a single file and is served
    as it stands from `GET /api/data/raster/{ref}/cog`, so re-encoding it through here would burn
    worker time to produce a worse copy."""
    settings = get_settings()
    native = (target_crs == "native")
    b = tuple(float(v) for v in bbox.split(",")) if bbox else None
    exports_dir = f"{settings.data_dir}/temp/exports"
    os.makedirs(exports_dir, exist_ok=True)
    out_path = os.path.join(exports_dir, f"{self.request.id}.zip")
    # Build under a temp name and atomically rename when fully written — the status
    # endpoint treats the existence of {id}.zip as "ready", so it must only appear complete.
    tmp_path = out_path + ".part"

    used: set[str] = set()
    row_counts: dict[str, int] = {}
    truncated: list[dict] = []

    def fn(base: str, ext: str) -> str:
        name = f"{base}.{ext}"
        i = 1
        while name in used:
            name = f"{base}_{i}.{ext}"
            i += 1
        used.add(name)
        return name

    def put(z, name: str, payload, rows: int, cap: int) -> None:
        """Write one layer's file and record what it actually contains.

        `rows == cap` is the only signal available that the LIMIT bit: the query cannot report how
        many rows it did NOT return. Equality is therefore treated as truncation, which over-warns
        for a layer of exactly `cap` features — the harmless direction to be wrong in.
        """
        z.writestr(name, payload)
        row_counts[name] = rows
        if rows >= cap:
            truncated.append({"file": name, "rows": rows, "cap": cap})

    import psycopg2
    # Build the DSN from the SQLite setup_config (authoritative) rather than env settings —
    # the celery container's POSTGIS_PASSWORD isn't reliably populated. See csv_import.
    from .vector_ingest import _get_setup
    setup = _get_setup()
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
                    cap = FEATURE_CAP if b is not None else FULL_EXPORT_CAP
                    if fmt == "csv":
                        csv_text, n = _vec_csv(cur, it["schema"], it["table"], b, srid, out_srid)
                        put(z, fn(base, "csv"), csv_text, n, cap)
                    elif fmt == "gpkg":
                        gj, n = _vec_geojson(cur, it["schema"], it["table"], b, srid, out_srid)
                        try:
                            put(z, fn(base, "gpkg"), _gj_to_gpkg(gj, base, f"EPSG:{out_srid}"), n, cap)
                        except Exception as e:
                            log.warning("GeoPackage export failed for %s, falling back to GeoJSON: %s", base, e)
                            put(z, fn(base, "geojson"), gj, n, cap)
                    else:
                        gj, n = _vec_geojson(cur, it["schema"], it["table"], b, srid, 4326)
                        put(z, fn(base, "geojson"), gj, n, cap)
                elif it.get("type") == "geoparquet":
                    base = _safe(it.get("name"))
                    fmt = it.get("format", "geojson")
                    # native only for CSV/GPKG; GeoJSON must be 4326.
                    keep_native = native and fmt in ("csv", "gpkg")
                    cap = FEATURE_CAP if b is not None else FULL_EXPORT_CAP
                    # The parquet path never needs decoded features — computing them would undo the
                    # entire point of it.
                    feats, n = (([], 0) if fmt == "geoparquet"
                                else _gpq_features(it["s3_key"], b, settings,
                                                   keep_native=keep_native))
                    out_crs = (it.get("crs") or "EPSG:4326") if keep_native else "EPSG:4326"
                    if fmt == "geoparquet":
                        # Parquet-to-parquet inside DuckDB: no GeoJSON materialisation, no shapely
                        # round-trip, geometry stays WKB in the file's own CRS. Lossless and the
                        # cheapest of the four, which is why it is offered only where it applies.
                        data, n = _gpq_parquet(it["s3_key"], b, settings)
                        put(z, fn(base, "parquet"), data, n, cap)
                    elif fmt == "csv":
                        put(z, fn(base, "csv"), _gpq_csv(feats), n, cap)
                    elif fmt == "gpkg":
                        gj = _gpq_geojson(feats)
                        try:
                            put(z, fn(base, "gpkg"), _gj_to_gpkg(gj, base, out_crs), n, cap)
                        except Exception as e:
                            log.warning("GeoPackage export failed for %s, falling back to GeoJSON: %s", base, e)
                            again, n = _gpq_features(it["s3_key"], b, settings)
                            put(z, fn(base, "geojson"), _gpq_geojson(again), n, cap)
                    else:
                        put(z, fn(base, "geojson"), _gpq_geojson(feats), n, cap)
                else:  # raster
                    if b is None:
                        log.info("Whole-raster export skipped for %s — /cog serves the file itself.",
                                 it.get("name"))
                        continue
                    try:
                        data = _clip_raster(it["s3_key"], b, settings)
                    except ValueError:
                        log.info("Raster %s does not overlap the selection — skipped.", it.get("name"))
                        continue  # no overlap
                    except Exception:
                        log.exception("Raster clip failed for %s", it.get("name"))
                        raise
                    z.writestr(fn(_safe(it.get("name")) + "_clip", "tif"), data)
            z.writestr("MANIFEST.txt", _manifest(items, bbox, target_crs, sorted(used),
                                                 row_counts, truncated))
            cur.close()
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise
    finally:
        conn.close()

    # The report goes down BEFORE the zip is published: readiness is the existence of {id}.zip, so
    # writing it after would leave a window where a client can download a truncated export and be
    # told nothing is wrong. Best-effort — a missing report must never fail an export that worked.
    try:
        with open(out_path[: -len(".zip")] + ".json", "w", encoding="utf-8") as f:
            json.dump({"rows": row_counts, "truncated": truncated}, f)
    except Exception:  # noqa: BLE001
        log.warning("Could not write the export report for %s", self.request.id, exc_info=True)

    os.replace(tmp_path, out_path)  # atomic publish — only now does status flip to "ready"
    if truncated:
        log.warning("Export %s hit the row cap: %s", self.request.id,
                    ", ".join(t["file"] for t in truncated))
    return {"path": out_path, "truncated": truncated, "rows": row_counts}
