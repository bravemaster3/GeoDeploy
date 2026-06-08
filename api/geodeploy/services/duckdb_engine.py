"""DuckDB in-process engine for file-based (GeoParquet) vector layers and analytics.

GeoParquet layers live on object storage and are read in place by DuckDB — they are NOT
loaded into PostGIS (unlike CSV). `inspect_parquet` reads a file's metadata + geometry on
register (the file equivalent of `cog_converter.inspect_s3` for rasters).
"""
import json
import logging
import time
import duckdb
from ..config import get_settings

logger = logging.getLogger(__name__)

_conn: duckdb.DuckDBPyConnection | None = None

# DuckDB ST_GeometryType → the simple kind GeoDeploy stores (matches PostGIS ingest).
_GEOM_TYPE_MAP = {
    "POINT": "point", "MULTIPOINT": "point",
    "LINESTRING": "line", "MULTILINESTRING": "line",
    "POLYGON": "polygon", "MULTIPOLYGON": "polygon",
}


def get_connection() -> duckdb.DuckDBPyConnection:
    global _conn
    if _conn is None:
        settings = get_settings()
        _conn = duckdb.connect(":memory:")
        _conn.execute("INSTALL spatial; LOAD spatial;")
        _conn.execute("INSTALL httpfs; LOAD httpfs;")
        _configure_s3(_conn, settings)
    return _conn


def _configure_s3(conn: duckdb.DuckDBPyConnection, settings) -> None:
    if not settings.storage_endpoint:
        return
    _apply_s3(conn, settings.storage_endpoint, settings.storage_access_key,
              settings.storage_secret_key, settings.storage_region)


def _apply_s3(conn: duckdb.DuckDBPyConnection, endpoint: str, access_key: str,
              secret_key: str, region: str | None) -> None:
    if not endpoint:
        return
    conn.execute(f"SET s3_endpoint='{endpoint.replace('http://', '').replace('https://', '')}'")
    conn.execute(f"SET s3_access_key_id='{access_key}'")
    conn.execute(f"SET s3_secret_access_key='{secret_key}'")
    conn.execute(f"SET s3_region='{region or 'us-east-1'}'")
    # Path-style addressing matches the rest of the stack (TiTiler AWS_VIRTUAL_HOSTING=FALSE);
    # works for MinIO / R2 / B2 / Hetzner and AWS.
    conn.execute("SET s3_url_style='path'")
    conn.execute("SET s3_use_ssl=" + ("false" if endpoint.startswith("http://") else "true"))


# Inspect does geometry math with shapely, NOT the DuckDB spatial extension: spatial's GeoParquet
# decoder rejects files tagged with spec versions it doesn't know (e.g. "2.0-dev"), and that check
# fires on read_parquet the moment spatial is loaded. Reading WITHOUT spatial returns the geometry
# column as raw WKB bytes, which shapely parses regardless of the declared GeoParquet spec version.
_BBOX_SCAN_CAP = 2_000_000  # only scan geometries for a bbox when the file is smaller than this


def _connect_read(creds: dict | None = None) -> duckdb.DuckDBPyConnection:
    """A fresh DuckDB connection for READING parquet (httpfs + S3, NO spatial extension). Celery
    tasks must pass storage creds from SQLite — the worker's env isn't reliably populated (a
    `docker restart` doesn't re-read .env). See notes_for_future §0f."""
    conn = duckdb.connect(":memory:")
    conn.execute("INSTALL httpfs; LOAD httpfs;")
    if creds:
        _apply_s3(conn, creds.get("endpoint"), creds.get("access_key"),
                  creds.get("secret_key"), creds.get("region"))
    else:
        _configure_s3(conn, get_settings())
    return conn


def _geom_kind_from_wkb(wkb) -> str | None:
    try:
        from shapely import from_wkb
        return _GEOM_TYPE_MAP.get(from_wkb(bytes(wkb)).geom_type.upper())
    except Exception:
        return None


def _bbox_from_wkbs(blobs) -> list | None:
    try:
        from shapely import from_wkb, total_bounds
        geoms = from_wkb([bytes(b) for b in blobs if b is not None])
        b = total_bounds(geoms)
    except Exception:
        return None
    if b is None or any(v != v for v in b):  # NaN guard (empty / unparseable)
        return None
    return [float(b[0]), float(b[1]), float(b[2]), float(b[3])]


def _q(ident: str) -> str:
    return '"' + str(ident).replace('"', '""') + '"'


def _crs_to_epsg(crs) -> str | None:
    """GeoParquet column CRS (PROJJSON, or null = OGC:CRS84) → 'EPSG:n' when resolvable."""
    if crs is None:
        return "EPSG:4326"  # GeoParquet default CRS is OGC:CRS84 (lon/lat WGS84)
    if isinstance(crs, dict):
        ident = crs.get("id") or {}
        auth = str(ident.get("authority", "")).upper()
        code = ident.get("code")
        if auth == "OGC" and str(code).upper() == "CRS84":
            return "EPSG:4326"
        if auth == "EPSG" and code is not None:
            return f"EPSG:{code}"
    return None


def _read_geo_metadata(conn: duckdb.DuckDBPyConnection, loc: str) -> dict:
    """Parse the GeoParquet 'geo' key → {column, epsg, bbox, geometry_types}.

    The metadata bbox + geometry_types (when the writer included them) let us avoid reading any
    geometry at all — the expensive part of inspecting a multi-GB file.
    """
    out = {"column": None, "epsg": None, "bbox": None, "geometry_types": None, "covering": None}
    try:
        rows = conn.execute(
            f"SELECT decode(value) FROM parquet_kv_metadata('{loc}') WHERE decode(key) = 'geo'"
        ).fetchall()
    except Exception:
        return out
    if not rows:
        return out
    try:
        meta = json.loads(rows[0][0])
    except Exception:
        return out
    primary = meta.get("primary_column")
    out["column"] = primary
    col_meta = (meta.get("columns") or {}).get(primary, {}) if primary else {}
    out["epsg"] = _crs_to_epsg(col_meta.get("crs"))
    bbox = col_meta.get("bbox")
    if isinstance(bbox, list) and len(bbox) == 4:
        out["bbox"] = bbox
    gts = col_meta.get("geometry_types")
    if isinstance(gts, list) and gts:
        out["geometry_types"] = gts
    # Covering bbox column (GeoParquet 1.1) → (struct_column, {xmin,ymin,xmax,ymax field names}).
    # Lets us filter by viewport on plain numeric columns (row-group pruning, no geometry read).
    cov = (col_meta.get("covering") or {}).get("bbox")
    if isinstance(cov, dict) and all(k in cov for k in ("xmin", "ymin", "xmax", "ymax")):
        try:
            col = cov["xmin"][0]
            out["covering"] = (col, {k: cov[k][1] for k in ("xmin", "ymin", "xmax", "ymax")})
        except Exception:
            pass
    return out


def _reproject_bbox(bbox: list[float], src_epsg: str, dst_epsg: str = "EPSG:4326") -> list[float]:
    if src_epsg == dst_epsg:
        return bbox
    try:
        from pyproj import Transformer
        t = Transformer.from_crs(src_epsg, dst_epsg, always_xy=True)
        xs, ys = [], []
        for x in (bbox[0], bbox[2]):
            for y in (bbox[1], bbox[3]):
                lx, ly = t.transform(x, y)
                xs.append(lx); ys.append(ly)
        return [min(xs), min(ys), max(xs), max(ys)]
    except Exception:
        return bbox


def inspect_parquet(location: str, creds: dict | None = None) -> dict:
    """Read a GeoParquet file's metadata + geometry without loading it anywhere.

    `location` is a local path or an `s3://bucket/key` URL. Returns geometry_column,
    geometry_type (point|line|polygon), bbox (EPSG:4326), columns, feature_count, crs.
    """
    conn = _connect_read(creds)
    try:
        loc = location.replace("'", "''")
        meta = _read_geo_metadata(conn, loc)
        geom_col, src_epsg = meta["column"], meta["epsg"]

        desc = conn.execute(f"DESCRIBE SELECT * FROM read_parquet('{loc}')").fetchall()
        all_cols = [(r[0], (r[1] or "")) for r in desc]  # (name, duckdb_type)

        if not geom_col:
            for name, typ in all_cols:
                if name.lower() in ("geometry", "geom", "wkb_geometry", "wkb") \
                        or typ.upper() in ("GEOMETRY", "BLOB"):
                    geom_col = name
                    break
        if not geom_col:
            raise ValueError("No geometry column found — is this a GeoParquet file?")

        gq = _q(geom_col)
        fc = conn.execute(f"SELECT COUNT(*) FROM read_parquet('{loc}')").fetchone()[0]
        columns = [{"name": n, "type": t.lower()} for n, t in all_cols if n != geom_col]

        # Geometry type: prefer the metadata's geometry_types (no data read needed); else parse
        # one feature's WKB with shapely. Metadata values may carry a " Z"/" M" suffix → first token.
        geometry_type = None
        if meta["geometry_types"]:
            geometry_type = _GEOM_TYPE_MAP.get(str(meta["geometry_types"][0]).upper().split()[0])
        if geometry_type is None:
            sample = conn.execute(
                f"SELECT {gq} FROM read_parquet('{loc}') WHERE {gq} IS NOT NULL LIMIT 1"
            ).fetchone()
            if sample and sample[0] is not None:
                geometry_type = _geom_kind_from_wkb(sample[0])

        # bbox: prefer the metadata bbox (avoids scanning a multi-GB file); else scan WKB with
        # shapely, but only for files under the cap (a partial scan would give a wrong extent).
        bbox = list(meta["bbox"]) if meta["bbox"] else None
        if bbox is None and fc and fc <= _BBOX_SCAN_CAP:
            rows = conn.execute(
                f"SELECT {gq} FROM read_parquet('{loc}') WHERE {gq} IS NOT NULL"
            ).fetchall()
            bbox = _bbox_from_wkbs([r[0] for r in rows])
        if bbox and src_epsg and src_epsg != "EPSG:4326":
            bbox = _reproject_bbox(bbox, src_epsg)

        return {
            "geometry_column": geom_col,
            "geometry_type": geometry_type,
            "bbox": bbox,
            "columns": columns,
            "feature_count": int(fc) if fc is not None else None,
            "crs": src_epsg or "EPSG:4326",
        }
    finally:
        conn.close()


def _jsonable(v):
    """Coerce a DuckDB cell to something JSON-serialisable (dates/Decimal/bytes → str/float)."""
    import datetime
    import decimal
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (datetime.date, datetime.datetime, datetime.time)):
        return v.isoformat()
    if isinstance(v, (bytes, bytearray, memoryview)):
        return None  # don't ship raw blobs as properties
    return str(v)


def query_features_geojson(s3_key: str, bbox=None, limit: int = 50_000, creds: dict | None = None,
                           bucket: str | None = None) -> dict:
    """Viewport query for a GeoParquet layer → a GeoJSON FeatureCollection (geometries in EPSG:4326).

    `bbox` is `[minx, miny, maxx, maxy]` in EPSG:4326 (the current map view) or None for "first N".
    Spatial filtering uses the GeoParquet **covering bbox** column when present — plain numeric
    comparisons that let DuckDB prune row-groups, so a multi-GB file isn't fully scanned per view.
    Without a covering column we can't bbox-filter cheaply (no spatial extension — see inspect notes),
    so we fall back to the first `limit` rows. Reads WKB (no spatial) and converts with shapely.
    """
    settings = get_settings()
    bkt = bucket or settings.storage_bucket
    loc = f"s3://{bkt}/{s3_key}".replace("'", "''")
    limit = max(1, int(limit))

    conn = _connect_read(creds)
    try:
        meta = _read_geo_metadata(conn, loc)
        all_cols = [r[0] for r in conn.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{loc}')").fetchall()]
        geom_col = meta["column"]
        if not geom_col:
            geom_col = next((c for c in all_cols
                             if c.lower() in ("geometry", "geom", "wkb_geometry", "wkb")), None)
        if not geom_col:
            raise ValueError("No geometry column found.")
        src_epsg = meta["epsg"] or "EPSG:4326"
        prop_cols = [c for c in all_cols if c != geom_col]

        where = ""
        if bbox and len(bbox) == 4 and meta["covering"]:
            qb = _reproject_bbox(list(bbox), "EPSG:4326", src_epsg)  # filter in the file's CRS
            col, fields = meta["covering"]
            def ce(f):
                return f"struct_extract({_q(col)}, '{fields[f]}')"
            where = (f"WHERE {ce('xmin')} <= {qb[2]} AND {ce('xmax')} >= {qb[0]} "
                     f"AND {ce('ymin')} <= {qb[3]} AND {ce('ymax')} >= {qb[1]}")

        sel = ", ".join([f"{_q(geom_col)} AS __wkb"] + [_q(c) for c in prop_cols])
        rows = conn.execute(
            f"SELECT {sel} FROM read_parquet('{loc}') {where} LIMIT {limit}").fetchall()

        from shapely import from_wkb, to_geojson
        transformer = None
        if src_epsg != "EPSG:4326":
            from pyproj import Transformer
            transformer = Transformer.from_crs(src_epsg, "EPSG:4326", always_xy=True)

        features = []
        for r in rows:
            wkb = r[0]
            if wkb is None:
                continue
            try:
                g = from_wkb(bytes(wkb))
                if transformer is not None:
                    from shapely.ops import transform as _shp_transform
                    g = _shp_transform(lambda x, y, z=None: transformer.transform(x, y), g)
                geom = json.loads(to_geojson(g))
            except Exception:
                continue
            props = {prop_cols[i]: _jsonable(r[i + 1]) for i in range(len(prop_cols))}
            features.append({"type": "Feature", "geometry": geom, "properties": props})

        return {"type": "FeatureCollection", "features": features,
                "geodeploy:capped": len(features) >= limit}
    finally:
        conn.close()


def stream_geojsonseq(s3_key: str, out, creds: dict | None = None, bucket: str | None = None,
                      batch_size: int = 20_000, log_every: int = 100_000) -> int:
    """Stream a whole GeoParquet file as newline-delimited GeoJSON (GeoJSONSeq, EPSG:4326) to the
    writable binary stream `out` (e.g. tippecanoe's stdin). Reads WKB without spatial and converts
    with shapely, in batches — never materialising the whole file. Returns the feature count.

    Logs progress every `log_every` features (count, elapsed, feat/s) so a long tiling run is
    observable in the celery log (the shapely conversion loop is usually the slow phase, and
    tippecanoe stays silent while blocked on stdin). Pass `log_every=0` to disable.
    """
    settings = get_settings()
    bkt = bucket or settings.storage_bucket
    loc = f"s3://{bkt}/{s3_key}".replace("'", "''")
    conn = _connect_read(creds)
    try:
        meta = _read_geo_metadata(conn, loc)
        all_cols = [r[0] for r in conn.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{loc}')").fetchall()]
        geom_col = meta["column"] or next(
            (c for c in all_cols if c.lower() in ("geometry", "geom", "wkb_geometry", "wkb")), None)
        if not geom_col:
            raise ValueError("No geometry column found.")
        src_epsg = meta["epsg"] or "EPSG:4326"
        prop_cols = [c for c in all_cols if c != geom_col]
        sel = ", ".join([f"{_q(geom_col)} AS __wkb"] + [_q(c) for c in prop_cols])

        import numpy as np
        from shapely import from_wkb, to_geojson, transform as _shp_transform
        reproject = None
        if src_epsg != "EPSG:4326":
            from pyproj import Transformer
            _tr = Transformer.from_crs(src_epsg, "EPSG:4326", always_xy=True)
            # Vectorised: shapely.transform hands each geometry's whole (N, 2) coordinate array to
            # the func, and pyproj transforms the column arrays in one call — far faster than
            # shapely.ops.transform, which invoked pyproj per coordinate pair.
            def _reproject_coords(coords):
                x, y = _tr.transform(coords[:, 0], coords[:, 1])
                return np.column_stack([x, y])
            reproject = lambda geoms: _shp_transform(geoms, _reproject_coords)

        rel = conn.execute(f"SELECT {sel} FROM read_parquet('{loc}')")
        count = 0
        start = time.monotonic()
        next_log = log_every
        logger.info("stream_geojsonseq: started streaming %s (geom=%s, src=%s)", loc, geom_col, src_epsg)
        while True:
            batch = rel.fetchmany(batch_size)
            if not batch:
                break
            # Vectorise the geometry conversion over the whole fetch batch: from_wkb / reproject /
            # to_geojson each run once per batch in C (releasing the GIL) instead of once per
            # feature in Python — the dominant cost on multi-million-feature files.
            wkbs = [bytes(r[0]) if r[0] is not None else None for r in batch]
            geoms = from_wkb(wkbs, on_invalid="ignore")
            if reproject is not None:
                geoms = reproject(geoms)
            gjs = to_geojson(geoms)  # ndarray of str; None where the geometry was missing/invalid
            parts = []
            for i, gj in enumerate(gjs):
                if not gj:
                    continue
                r = batch[i]
                props = {prop_cols[j]: _jsonable(r[j + 1]) for j in range(len(prop_cols))}
                parts.append(b'{"type":"Feature","geometry":' + gj.encode()
                             + b',"properties":' + json.dumps(props, default=str).encode() + b"}\n")
                count += 1
            if parts:
                out.write(b"".join(parts))  # one write per batch, not four per feature
            if log_every and count >= next_log:
                el = time.monotonic() - start
                logger.info("stream_geojsonseq: %s features streamed in %.0fs (%.0f feat/s)",
                            f"{count:,}", el, count / el if el else 0)
                next_log += log_every
        el = time.monotonic() - start
        logger.info("stream_geojsonseq: DONE — %s features in %.0fs (%.0f feat/s)",
                    f"{count:,}", el, count / el if el else 0)
        return count
    finally:
        conn.close()
