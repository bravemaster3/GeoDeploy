"""DuckDB in-process engine for file-based (GeoParquet) vector layers and analytics.

GeoParquet layers live on object storage and are read in place by DuckDB — they are NOT
loaded into PostGIS (unlike CSV). `inspect_parquet` reads a file's metadata + geometry on
register (the file equivalent of `cog_converter.inspect_s3` for rasters).
"""
import json
import logging
import os
import shutil
import time
from uuid import uuid4
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


# DuckDB extensions are baked into the image here at build time (see api/Dockerfile) so S3 reads
# need NO runtime download from the often-slow/unreachable extensions.duckdb.org. Pointing
# extension_directory here makes LOAD use the baked copy; INSTALL is only a network fallback.
_DUCKDB_EXT_DIR = os.getenv("DUCKDB_EXTENSION_DIR")


def _load_extension(conn: duckdb.DuckDBPyConnection, name: str) -> None:
    """LOAD a DuckDB extension from the image-baked directory (no network); fall back to INSTALL
    (which downloads) only if the baked copy is missing."""
    if _DUCKDB_EXT_DIR:
        conn.execute(f"SET extension_directory='{_DUCKDB_EXT_DIR}'")
    try:
        conn.execute(f"LOAD {name}")
    except Exception:
        conn.execute(f"INSTALL {name}; LOAD {name}")


def get_connection() -> duckdb.DuckDBPyConnection:
    global _conn
    if _conn is None:
        settings = get_settings()
        _conn = duckdb.connect(":memory:")
        _load_extension(_conn, "spatial")
        _load_extension(_conn, "httpfs")
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
    _load_extension(conn, "httpfs")
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


# Parsed 'geo' metadata per metadata_path. Safe to cache in-process: a partitioned prefix is
# immutable (a re-prep writes a NEW parts-<hex> prefix and the layer's s3_key is repointed), and
# single-file uploads land under a fresh uuid dir — so a given path's metadata never changes.
# Only successful parses are cached, so a transient S3 failure is retried next call.
_GEO_META_CACHE: dict[str, dict] = {}
_GEO_META_CACHE_MAX = 128


def _meta_probe_path(conn: duckdb.DuckDBPyConnection, loc: str) -> str:
    """Resolve a glob metadata path to ONE concrete file. Every partition file carries the same
    'geo' blob by construction (partition_with_covering attaches it to each file), but
    parquet_kv_metadata over the glob opens EVERY footer over S3 — ~15 s on a 370-file prefix,
    which dominated small viewport queries. glob() is a cheap LIST instead."""
    if "*" not in loc:
        return loc
    row = conn.execute(f"SELECT file FROM glob('{loc}') LIMIT 1").fetchone()
    return row[0].replace("'", "''") if row else loc


def _read_geo_metadata(conn: duckdb.DuckDBPyConnection, loc: str) -> dict:
    """Parse the GeoParquet 'geo' key → {column, epsg, bbox, geometry_types}.

    The metadata bbox + geometry_types (when the writer included them) let us avoid reading any
    geometry at all — the expensive part of inspecting a multi-GB file.
    """
    cached = _GEO_META_CACHE.get(loc)
    if cached is not None:
        return dict(cached)
    out = {"column": None, "epsg": None, "bbox": None, "geometry_types": None,
           "covering": None, "grid": None}
    try:
        rows = conn.execute(
            f"SELECT decode(value) FROM parquet_kv_metadata('{_meta_probe_path(conn, loc)}') "
            "WHERE decode(key) = 'geo'"
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
    # GeoDeploy partition grid (written by partition_with_covering): lets a viewport query open only
    # the __cell partitions overlapping the bbox instead of every file. {minx,miny,spanx,spany,grid}.
    g = meta.get("geodeploy:partition")
    if isinstance(g, dict) and all(k in g for k in ("minx", "miny", "spanx", "spany", "grid")):
        out["grid"] = g
    if len(_GEO_META_CACHE) >= _GEO_META_CACHE_MAX:
        _GEO_META_CACHE.clear()  # a handful of tiny dicts; a reset beats LRU bookkeeping
    _GEO_META_CACHE[loc] = dict(out)
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


def _parquet_paths(location: str) -> tuple[str, str]:
    """Resolve a layer storage `location` to (metadata_path, from_expr) for DuckDB.

    A single-file layer is `…/file.parquet`; a spatially-partitioned layer (see
    `partition_with_covering`) is a PREFIX directory holding `__cell=N/*.parquet` files. The
    partitioned read globs the prefix with `hive_partitioning=false` so the synthetic `__cell`
    partition column is NOT surfaced as data. `metadata_path` is what `parquet_kv_metadata` reads
    (a glob is fine — every partition file carries the same `geo` covering metadata)."""
    loc = location.replace("'", "''")
    if location.rstrip("/").endswith(".parquet"):
        return loc, f"read_parquet('{loc}')"
    glob = f"{loc}/**/*.parquet"
    return glob, f"read_parquet('{glob}', hive_partitioning=false)"


def inspect_parquet(location: str, creds: dict | None = None) -> dict:
    """Read a GeoParquet file's metadata + geometry without loading it anywhere.

    `location` is a local path, an `s3://bucket/key` single file, or an `s3://bucket/prefix`
    partitioned dataset. Returns geometry_column, geometry_type (point|line|polygon),
    bbox (EPSG:4326), columns, feature_count, crs.
    """
    conn = _connect_read(creds)
    try:
        meta_path, src = _parquet_paths(location)
        meta = _read_geo_metadata(conn, meta_path)
        geom_col, src_epsg = meta["column"], meta["epsg"]

        desc = conn.execute(f"DESCRIBE SELECT * FROM {src}").fetchall()
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
        fc = conn.execute(f"SELECT COUNT(*) FROM {src}").fetchone()[0]
        # The synthetic bbox covering column (added by partition_with_covering) is not user data.
        cov_col = meta["covering"][0] if meta.get("covering") else None
        columns = [{"name": n, "type": t.lower()} for n, t in all_cols
                   if n != geom_col and n != cov_col]

        # Geometry type: prefer the metadata's geometry_types (no data read needed); else parse
        # one feature's WKB with shapely. Metadata values may carry a " Z"/" M" suffix → first token.
        geometry_type = None
        if meta["geometry_types"]:
            geometry_type = _GEOM_TYPE_MAP.get(str(meta["geometry_types"][0]).upper().split()[0])
        if geometry_type is None:
            sample = conn.execute(
                f"SELECT {gq} FROM {src} WHERE {gq} IS NOT NULL LIMIT 1"
            ).fetchone()
            if sample and sample[0] is not None:
                geometry_type = _geom_kind_from_wkb(sample[0])

        # bbox: prefer the metadata bbox (avoids scanning a multi-GB file); else scan WKB with
        # shapely, but only for files under the cap (a partial scan would give a wrong extent).
        bbox = list(meta["bbox"]) if meta["bbox"] else None
        if bbox is None and fc and fc <= _BBOX_SCAN_CAP:
            rows = conn.execute(
                f"SELECT {gq} FROM {src} WHERE {gq} IS NOT NULL"
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
    meta_path, src = _parquet_paths(f"s3://{bkt}/{s3_key}")
    limit = max(1, int(limit))

    conn = _connect_read(creds)
    try:
        meta = _read_geo_metadata(conn, meta_path)
        all_cols = [r[0] for r in conn.execute(
            f"DESCRIBE SELECT * FROM {src}").fetchall()]
        geom_col = meta["column"]
        if not geom_col:
            geom_col = next((c for c in all_cols
                             if c.lower() in ("geometry", "geom", "wkb_geometry", "wkb")), None)
        if not geom_col:
            raise ValueError("No geometry column found.")
        src_epsg = meta["epsg"] or "EPSG:4326"
        cov_col = meta["covering"][0] if meta.get("covering") else None
        prop_cols = [c for c in all_cols if c != geom_col and c != cov_col]

        where_parts, qb = [], None
        if bbox and len(bbox) == 4 and meta["covering"]:
            qb = _reproject_bbox(list(bbox), "EPSG:4326", src_epsg)  # filter in the file's CRS
            col, fields = meta["covering"]
            def ce(f):
                return f"struct_extract({_q(col)}, '{fields[f]}')"
            where_parts.append(f"{ce('xmin')} <= {qb[2]} AND {ce('xmax')} >= {qb[0]} "
                               f"AND {ce('ymin')} <= {qb[3]} AND {ce('ymax')} >= {qb[1]}")

        # Partition pruning: open only the __cell partitions whose grid cells overlap the bbox
        # (+1-cell pad for features straddling a boundary) instead of every partition file — this is
        # what makes a small-bbox query fast when there are hundreds of partitions. Needs the hive
        # partition column, so read with hive_partitioning=true. Cells mirror the prep grid layout
        # (cell = ix*grid + iy; see partition_with_covering).
        read_src = src
        gm = meta.get("grid")
        if qb is not None and gm:
            gsz = int(gm["grid"]); pad = 1
            def _ci(v, lo, span):
                return int((v - lo) / (span or 1.0) * gsz)
            ix0 = max(0, _ci(qb[0], gm["minx"], gm["spanx"]) - pad)
            ix1 = min(gsz - 1, _ci(qb[2], gm["minx"], gm["spanx"]) + pad)
            iy0 = max(0, _ci(qb[1], gm["miny"], gm["spany"]) - pad)
            iy1 = min(gsz - 1, _ci(qb[3], gm["miny"], gm["spany"]) + pad)
            if ix0 <= ix1 and iy0 <= iy1:
                cells = [ix * gsz + iy for ix in range(ix0, ix1 + 1) for iy in range(iy0, iy1 + 1)]
                if len(cells) < gsz * gsz:  # only worth it if a real subset of the grid
                    read_src = f"read_parquet('{meta_path}', hive_partitioning=true)"
                    where_parts.append(f"__cell IN ({','.join(str(c) for c in cells)})")

        where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
        sel = ", ".join([f"{_q(geom_col)} AS __wkb"] + [_q(c) for c in prop_cols])
        rows = conn.execute(
            f"SELECT {sel} FROM {read_src} {where} LIMIT {limit}").fetchall()

        # Vectorised WKB→GeoJSON: from_wkb / reproject / to_geojson run ONCE over the whole result in
        # C (GIL released), not per feature. The old per-feature pyproj reprojection made even a few
        # thousand features on a projected-CRS layer take many seconds. (This bulk reproject also
        # carries over to the planned GeoArrow transport; only the final to_geojson swaps out.)
        import numpy as np
        from shapely import from_wkb, to_geojson, transform as _shp_transform
        reproject = None
        if src_epsg != "EPSG:4326":
            from pyproj import Transformer
            _tr = Transformer.from_crs(src_epsg, "EPSG:4326", always_xy=True)
            def _reproject_coords(coords):
                x, y = _tr.transform(coords[:, 0], coords[:, 1])
                return np.column_stack([x, y])
            reproject = lambda geoms: _shp_transform(geoms, _reproject_coords)

        wkbs = [bytes(r[0]) if r[0] is not None else None for r in rows]
        geoms = from_wkb(wkbs, on_invalid="ignore")
        if reproject is not None:
            geoms = reproject(geoms)
        gjs = to_geojson(geoms)  # ndarray of GeoJSON strings (None where geometry missing/invalid)
        features = []
        for i, gj in enumerate(gjs):
            if not gj:
                continue
            r = rows[i]
            props = {prop_cols[j]: _jsonable(r[j + 1]) for j in range(len(prop_cols))}
            features.append({"type": "Feature", "geometry": json.loads(gj), "properties": props})

        return {"type": "FeatureCollection", "features": features,
                "geodeploy:capped": len(rows) >= limit}
    finally:
        conn.close()


def query_features_at_point(s3_key: str, lng: float, lat: float, tol: float = 1e-4,
                            limit: int = 10, creds: dict | None = None,
                            bucket: str | None = None) -> list[dict]:
    """Identify-on-click for a GeoParquet layer: attribute dicts of the features intersecting a
    small box (`tol` degrees half-width) around the clicked EPSG:4326 point. This is what gives
    deck.gl-rendered layers popups — the viewport transports ship geometry only (GeoArrow) or are
    capped, so attributes are fetched on demand per click instead of riding every pan.

    Same covering-column + partition pruning as the viewport queries (the tiny bbox makes this
    cheap on a prepped layer), then an exact shapely intersects test in the file's CRS. On an
    unprepped file (no covering) only the first candidates are scanned — best-effort."""
    settings = get_settings()
    bkt = bucket or settings.storage_bucket
    meta_path, src = _parquet_paths(f"s3://{bkt}/{s3_key}")
    limit = max(1, min(int(limit), 50))
    bbox = [lng - tol, lat - tol, lng + tol, lat + tol]

    conn = _connect_read(creds)
    try:
        meta = _read_geo_metadata(conn, meta_path)
        all_cols = [r[0] for r in conn.execute(
            f"DESCRIBE SELECT * FROM {src}").fetchall()]
        geom_col = meta["column"] or next(
            (c for c in all_cols if c.lower() in ("geometry", "geom", "wkb_geometry", "wkb")), None)
        if not geom_col:
            raise ValueError("No geometry column found.")
        src_epsg = meta["epsg"] or "EPSG:4326"
        cov_col = meta["covering"][0] if meta.get("covering") else None
        prop_cols = [c for c in all_cols if c != geom_col and c != cov_col]

        qb = _reproject_bbox(bbox, "EPSG:4326", src_epsg)
        where_parts = []
        if meta["covering"]:
            col, fields = meta["covering"]
            def ce(f):
                return f"struct_extract({_q(col)}, '{fields[f]}')"
            where_parts.append(f"{ce('xmin')} <= {qb[2]} AND {ce('xmax')} >= {qb[0]} "
                               f"AND {ce('ymin')} <= {qb[3]} AND {ce('ymax')} >= {qb[1]}")
        read_src = src
        gm = meta.get("grid")
        if gm and meta["covering"]:
            gsz = int(gm["grid"]); pad = 1
            def _ci(v, lo, span):
                return int((v - lo) / (span or 1.0) * gsz)
            ix0 = max(0, _ci(qb[0], gm["minx"], gm["spanx"]) - pad)
            ix1 = min(gsz - 1, _ci(qb[2], gm["minx"], gm["spanx"]) + pad)
            iy0 = max(0, _ci(qb[1], gm["miny"], gm["spany"]) - pad)
            iy1 = min(gsz - 1, _ci(qb[3], gm["miny"], gm["spany"]) + pad)
            if ix0 <= ix1 and iy0 <= iy1:
                cells = [ix * gsz + iy for ix in range(ix0, ix1 + 1) for iy in range(iy0, iy1 + 1)]
                if len(cells) < gsz * gsz:
                    read_src = f"read_parquet('{meta_path}', hive_partitioning=true)"
                    where_parts.append(f"__cell IN ({','.join(str(c) for c in cells)})")
        where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
        sel = ", ".join([f"{_q(geom_col)} AS __wkb"] + [_q(c) for c in prop_cols])
        rows = conn.execute(f"SELECT {sel} FROM {read_src} {where} LIMIT 512").fetchall()
        if not rows:
            return []

        from shapely import box as shp_box, from_wkb, intersects
        test = shp_box(qb[0], qb[1], qb[2], qb[3])
        geoms = from_wkb([bytes(r[0]) if r[0] is not None else None for r in rows],
                         on_invalid="ignore")
        out = []
        for i, hit in enumerate(intersects(geoms, test)):
            if not hit:
                continue
            r = rows[i]
            out.append({prop_cols[j]: _jsonable(r[j + 1]) for j in range(len(prop_cols))})
            if len(out) >= limit:
                break
        return out
    finally:
        conn.close()


# shapely.GeometryType → GeoArrow extension name (geometry collections unsupported → caller
# falls back to the GeoJSON transport).
_GEOARROW_EXT = {0: "geoarrow.point", 1: "geoarrow.linestring", 3: "geoarrow.polygon",
                 4: "geoarrow.multipoint", 5: "geoarrow.multilinestring", 6: "geoarrow.multipolygon"}


def query_features_arrow(s3_key: str, bbox=None, limit: int = 50_000, creds: dict | None = None,
                         bucket: str | None = None) -> bytes | None:
    """Viewport query → a GeoArrow-encoded Arrow IPC stream (geometry only, EPSG:4326).

    The binary twin of `query_features_geojson` for the portal deck.gl overlay: WKB →
    `shapely.to_ragged_array` (flat coordinate ndarray + offsets, C speed) → zero-copy pyarrow
    nested lists tagged `ARROW:extension:name = geoarrow.*` → IPC bytes. **No GeoJSON text is
    produced anywhere on this path** — the browser hands the buffer to @geoarrow/deck.gl-layers
    as-is. Only the geometry column is shipped (deck layers carry no popups yet). Returns None
    for an empty viewport; raises for geometry collections (caller falls back to GeoJSON).
    """
    settings = get_settings()
    bkt = bucket or settings.storage_bucket
    meta_path, src = _parquet_paths(f"s3://{bkt}/{s3_key}")
    limit = max(1, int(limit))

    conn = _connect_read(creds)
    try:
        meta = _read_geo_metadata(conn, meta_path)
        all_cols = [r[0] for r in conn.execute(
            f"DESCRIBE SELECT * FROM {src}").fetchall()]
        geom_col = meta["column"] or next(
            (c for c in all_cols if c.lower() in ("geometry", "geom", "wkb_geometry", "wkb")), None)
        if not geom_col:
            raise ValueError("No geometry column found.")
        src_epsg = meta["epsg"] or "EPSG:4326"

        # Same covering + partition pruning as query_features_geojson.
        where_parts, qb = [], None
        if bbox and len(bbox) == 4 and meta["covering"]:
            qb = _reproject_bbox(list(bbox), "EPSG:4326", src_epsg)
            col, fields = meta["covering"]
            def ce(f):
                return f"struct_extract({_q(col)}, '{fields[f]}')"
            where_parts.append(f"{ce('xmin')} <= {qb[2]} AND {ce('xmax')} >= {qb[0]} "
                               f"AND {ce('ymin')} <= {qb[3]} AND {ce('ymax')} >= {qb[1]}")
        read_src = src
        gm = meta.get("grid")
        if qb is not None and gm:
            gsz = int(gm["grid"]); pad = 1
            def _ci(v, lo, span):
                return int((v - lo) / (span or 1.0) * gsz)
            ix0 = max(0, _ci(qb[0], gm["minx"], gm["spanx"]) - pad)
            ix1 = min(gsz - 1, _ci(qb[2], gm["minx"], gm["spanx"]) + pad)
            iy0 = max(0, _ci(qb[1], gm["miny"], gm["spany"]) - pad)
            iy1 = min(gsz - 1, _ci(qb[3], gm["miny"], gm["spany"]) + pad)
            if ix0 <= ix1 and iy0 <= iy1:
                cells = [ix * gsz + iy for ix in range(ix0, ix1 + 1) for iy in range(iy0, iy1 + 1)]
                if len(cells) < gsz * gsz:
                    read_src = f"read_parquet('{meta_path}', hive_partitioning=true)"
                    where_parts.append(f"__cell IN ({','.join(str(c) for c in cells)})")
        where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

        rows = conn.execute(
            f"SELECT {_q(geom_col)} AS __wkb FROM {read_src} {where} LIMIT {limit}").fetchall()
        wkbs = [bytes(r[0]) for r in rows if r[0] is not None]
        if not wkbs:
            return None

        import numpy as np
        import pyarrow as pa
        from shapely import from_wkb, to_ragged_array
        geoms = from_wkb(wkbs, on_invalid="ignore")
        geoms = geoms[geoms != None]  # noqa: E711 — elementwise ndarray comparison
        if geoms.size == 0:
            return None
        gt, coords, offsets = to_ragged_array(geoms, include_z=False)
        ext = _GEOARROW_EXT.get(int(gt))
        if ext is None:
            raise ValueError(f"Unsupported geometry type for GeoArrow transport: {gt}")

        if src_epsg != "EPSG:4326":
            from pyproj import Transformer
            _tr = Transformer.from_crs(src_epsg, "EPSG:4326", always_xy=True)
            x, y = _tr.transform(coords[:, 0], coords[:, 1])
            coords = np.column_stack([x, y])

        # Zero-copy assembly: flat coords → FixedSizeList<double,2> ("xy"), then one ListArray
        # per offsets level (innermost first, as shapely emits them).
        flat = pa.array(np.ascontiguousarray(coords, dtype=np.float64).ravel(), type=pa.float64())
        arr = pa.FixedSizeListArray.from_arrays(flat, 2)
        for offs in offsets:
            arr = pa.ListArray.from_arrays(
                pa.array(np.asarray(offs, dtype=np.int32), type=pa.int32()), arr)
        field = pa.field("geometry", arr.type, nullable=False,
                         metadata={b"ARROW:extension:name": ext.encode()})
        table = pa.Table.from_arrays([arr], schema=pa.schema([field]))
        sink = pa.BufferOutputStream()
        with pa.ipc.new_stream(sink, table.schema) as writer:
            writer.write_table(table)
        return sink.getvalue().to_pybytes()
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
    meta_path, src = _parquet_paths(f"s3://{bkt}/{s3_key}")
    conn = _connect_read(creds)
    try:
        meta = _read_geo_metadata(conn, meta_path)
        all_cols = [r[0] for r in conn.execute(
            f"DESCRIBE SELECT * FROM {src}").fetchall()]
        geom_col = meta["column"] or next(
            (c for c in all_cols if c.lower() in ("geometry", "geom", "wkb_geometry", "wkb")), None)
        if not geom_col:
            raise ValueError("No geometry column found.")
        src_epsg = meta["epsg"] or "EPSG:4326"
        cov_col = meta["covering"][0] if meta.get("covering") else None
        prop_cols = [c for c in all_cols if c != geom_col and c != cov_col]
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

        rel = conn.execute(f"SELECT {sel} FROM {src}")
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


# --- GeoParquet preparation: spatial PARTITIONING + bbox covering column ----------------------
# Rewrites a GeoParquet so spatial reads are fast WITHOUT a total-order sort (which was impractical
# on millions of large polygons — out-of-core sorting GBs of geometry payload hung for hours). Two
# moves give the same payoff far more cheaply:
#   1. a GeoParquet 1.1 `bbox` covering struct column → DuckDB prunes row-groups on a bbox filter
#      (fast analysis AND the deck.gl viewport feed; query_features_geojson uses meta["covering"]);
#   2. **partition the rows into a coarse spatial grid** (`PARTITION_BY __cell`) — a single-pass
#      scatter (NO sort/merge) that co-locates nearby features into the same partition file, so the
#      covering's row-group stats are tight and pruning actually skips data.
# Per-feature bbox is computed once with shapely (vectorised, NO DuckDB spatial → dodges the
# GeoParquet 2.0-dev decoder wall). The output is a PREFIX of `__cell=N/*.parquet` files (each
# carrying the `geo` covering metadata), read back via `_parquet_paths` with hive_partitioning off.
# When the source already has a covering column (GDAL/GeoPandas, or a re-run) the parse is skipped.

# Logical bbox subfields, in the order the extent query / grid cell expect them.
_BBOX_FIELDS = ("xmin", "ymin", "xmax", "ymax")


def _build_geo_with_covering(conn: duckdb.DuckDBPyConnection, loc: str, geom_col: str,
                             cov_col: str = "bbox",
                             fields: dict[str, str] | None = None,
                             partition: dict | None = None) -> dict:
    """Original `geo` metadata + a covering entry pointing at the `cov_col` struct column,
    relabelled to GeoParquet 1.1.0 WKB (covering is a 1.1 feature; 1.1.0/WKB is also spec-clean for
    DuckDB spatial, unlike the original `2.0-dev` tag). `fields` maps each logical bbox corner to the
    struct subfield name in `cov_col` (defaults to identity: xmin→xmin …)."""
    fields = fields or {f: f for f in _BBOX_FIELDS}
    geo: dict = {}
    try:
        rows = conn.execute(
            f"SELECT decode(value) FROM parquet_kv_metadata('{loc}') WHERE decode(key) = 'geo'"
        ).fetchall()
        if rows:
            geo = json.loads(rows[0][0])
    except Exception:
        geo = {}
    primary = geo.get("primary_column") or geom_col
    geo["version"] = "1.1.0"
    geo["primary_column"] = primary
    cols = geo.setdefault("columns", {})
    col = cols.setdefault(primary, {})
    col.setdefault("encoding", "WKB")
    col.setdefault("geometry_types", col.get("geometry_types") or [])
    col["covering"] = {"bbox": {f: [cov_col, fields[f]] for f in _BBOX_FIELDS}}
    if partition:  # custom GeoDeploy key (GeoParquet readers ignore unknown top-level keys)
        geo["geodeploy:partition"] = partition
    return geo


def partition_with_covering(s3_key: str, creds: dict | None = None, out_prefix: str | None = None,
                            bucket: str | None = None, row_group_size: int = 50_000,
                            memory_limit: str = "4GB", bbox_chunk: int = 50_000,
                            max_temp_dir_size: str = "100GiB", partition_grid: int = 16,
                            extent_quantile: float = 0.005, rows_per_cell: int = 100_000) -> dict:
    """Rewrite the GeoParquet at `s3_key` into a spatially-partitioned dataset with a `bbox` covering
    column, written DIRECTLY to S3 as a PREFIX of `__cell=N/*.parquet` files. Returns
    {"out_key" (the prefix), "feature_count", "geometry_column", "partitioned": True}. Requires
    pyarrow. Creds must come from SQLite in Celery (env unreliable — §0f).

    No total-order sort (that hung for hours on millions of large polygons): rows are scattered into
    a `partition_grid`×`partition_grid` spatial grid in a single pass. WKB is parsed at most once —
    the per-feature bbox is materialised once to a local intermediate, then the extent + grid cell
    read that NUMERIC column. If the source already has a covering bbox column, no geometry is parsed.
    `bbox_chunk` caps shapely geometries parsed per UDF slice (peak-memory guard)."""
    import numpy as np
    import pyarrow as pa
    from shapely import bounds as _bounds, from_wkb

    settings = get_settings()
    bkt = bucket or (creds.get("bucket") if creds else None) or settings.storage_bucket
    src_meta_path, src_expr = _parquet_paths(f"s3://{bkt}/{s3_key}")
    if out_prefix is None:
        # Partition files live under the layer's own dir, in a fresh `parts-<hex>` prefix so a
        # re-prep writes a NEW dataset (read old → write new → delete old) rather than overwriting
        # the file it is reading from.
        layer_dir = s3_key.rstrip("/").rsplit("/", 1)[0] if "/" in s3_key.rstrip("/") else ""
        out_prefix = (layer_dir + "/" if layer_dir else "") + f"parts-{uuid4().hex[:8]}"
    tmpdir = f"{settings.data_dir}/temp"
    os.makedirs(tmpdir, exist_ok=True)
    local_bbox = os.path.join(tmpdir, f"{uuid4().hex}.parquet")  # intermediate: bbox materialised
    local_parts = os.path.join(tmpdir, f"parts-{uuid4().hex}")   # partitioned output (local, then uploaded)
    # Per-run spill dir + explicit cap: DuckDB's auto-detection of free temp space misreads
    # overlay/WSL filesystems (bogus "16383 PiB"), and concurrent runs sharing one temp dir miscount.
    spill_dir = os.path.join(tmpdir, f"duckdb-{uuid4().hex}")
    os.makedirs(spill_dir, exist_ok=True)

    conn = _connect_read(creds)  # httpfs + S3, NO spatial
    try:
        conn.execute(f"SET temp_directory='{spill_dir}'")
        conn.execute(f"SET memory_limit='{memory_limit}'")
        conn.execute(f"SET max_temp_directory_size='{max_temp_dir_size}'")

        meta = _read_geo_metadata(conn, src_meta_path)
        all_cols = [r[0] for r in conn.execute(
            f"DESCRIBE SELECT * FROM {src_expr}").fetchall()]
        geom_col = meta["column"] or next(
            (c for c in all_cols if c.lower() in ("geometry", "geom", "wkb_geometry", "wkb")), None)
        if not geom_col:
            raise ValueError("No geometry column found.")
        gq = _q(geom_col)
        started = time.monotonic()

        # Where the per-feature bbox comes from: an existing covering column (zero WKB parse — fast
        # re-runs / GDAL files), else parse the geometry ONCE into a local intermediate's bbox struct.
        existing = meta.get("covering")
        if existing:
            part_src = src_expr
            cov_col, cov_fields = existing[0], existing[1]
            logger.info("partition_with_covering: %s — reusing existing covering column %r (no parse)",
                        s3_key, cov_col)
        else:
            cov_col = "bbox" if "bbox" not in all_cols else "gd_bbox_cov"
            cov_fields = {f: f for f in _BBOX_FIELDS}

            def _udf_bbox(arr):
                wkbs = arr.to_pylist()
                n = len(wkbs)
                out = np.full((n, 4), np.nan)
                for i in range(0, n, bbox_chunk):  # cap peak shapely geometry count per slice
                    chunk = wkbs[i:i + bbox_chunk]
                    out[i:i + len(chunk)] = _bounds(from_wkb(chunk))
                return pa.StructArray.from_arrays(
                    [pa.array(out[:, 0]), pa.array(out[:, 1]), pa.array(out[:, 2]), pa.array(out[:, 3])],
                    names=list(_BBOX_FIELDS))
            conn.create_function(
                "gd_bbox", _udf_bbox, [duckdb.typing.BLOB],
                duckdb.struct_type({f: "DOUBLE" for f in _BBOX_FIELDS}), type="arrow")

            # Parse pass (the only WKB read): append the bbox struct, streams to a local file.
            conn.execute(
                f"COPY (SELECT *, gd_bbox({gq}) AS {_q(cov_col)} FROM {src_expr}) "
                f"TO '{local_bbox}' (FORMAT PARQUET, COMPRESSION ZSTD)")
            part_src = f"read_parquet('{local_bbox.replace(chr(39), chr(39) * 2)}')"
            logger.info("partition_with_covering: %s — bbox materialised in %.0fs", s3_key,
                        time.monotonic() - started)

        def ce(f):  # numeric corner expression on whichever covering column we settled on
            return f"struct_extract({_q(cov_col)}, '{cov_fields[f]}')"

        # Grid extent (numeric, no geometry parse). Use ROBUST percentiles, not min/max: a few
        # far-flung features (e.g. overseas territories) otherwise stretch the extent so the dense
        # mainland collapses into 1-2 giant cells (observed: 4 of 256 cells held ~8.3M of 9.5M rows
        # → a small-bbox query scanned a 2.5M-feature partition in ~25s). Clamping the grid to the
        # `extent_quantile`..`1-extent_quantile` range spreads the bulk across the whole grid (outliers
        # land in edge cells via the LEAST/GREATEST clamp below). min/max is the fallback.
        q = max(0.0, min(0.05, float(extent_quantile)))
        minx, miny, maxx, maxy = conn.execute(
            f"SELECT approx_quantile({ce('xmin')}, {q}), approx_quantile({ce('ymin')}, {q}), "
            f"approx_quantile({ce('xmax')}, {1 - q}), approx_quantile({ce('ymax')}, {1 - q}) "
            f"FROM {part_src}").fetchone()
        if minx is None:
            raise ValueError("Could not compute extent (no valid geometries).")
        spanx = (maxx - minx) or 1.0
        spany = (maxy - miny) or 1.0

        # Coarse spatial grid cell (native SQL, row-major over a grid×grid grid). PARTITION_BY this
        # scatters nearby features into the same file in ONE pass — no sort, no payload merge.
        # The grid ADAPTS to the dataset size (`partition_grid` is the ceiling): a light layer
        # (e.g. world countries) must not be scattered into hundreds of near-empty files — that
        # makes every viewport read open many objects for no pruning benefit. Target roughly
        # `rows_per_cell` features per OCCUPIED cell; assume ~half the grid cells hold data.
        n_rows = conn.execute(f"SELECT count(*) FROM {part_src}").fetchone()[0] or 0
        target = max(1, int(rows_per_cell))
        grid = max(1, min(max(2, int(partition_grid)),
                          int((n_rows / (0.5 * target)) ** 0.5) + 1))

        def _cell(center_expr, lo, span):
            return (f"LEAST({grid - 1}, GREATEST(0, "
                    f"CAST((({center_expr}) - ({lo!r})) / ({span!r}) * {grid} AS INTEGER)))")
        cx = f"(({ce('xmin')}) + ({ce('xmax')})) * 0.5"
        cy = f"(({ce('ymin')}) + ({ce('ymax')})) * 0.5"
        cell_sql = f"({_cell(cx, minx, spanx)} * {grid} + {_cell(cy, miny, spany)})"

        # Preserve the original `geo` metadata (geometry_types, CRS), point covering at cov_col, and
        # record the grid so viewport queries can prune to the overlapping __cell partitions.
        grid_meta = {"minx": float(minx), "miny": float(miny),
                     "spanx": float(spanx), "spany": float(spany), "grid": int(grid)}
        geo_str = json.dumps(
            _build_geo_with_covering(conn, src_meta_path, geom_col, cov_col, cov_fields, grid_meta)
        ).replace("'", "''")

        # Partitioned write to a LOCAL dir (each file gets the geo covering metadata). `__cell` is a
        # Hive partition column → encoded in the path, NOT stored as data. We write locally then
        # upload because DuckDB's partitioned write straight to S3 buffers a large block per open
        # partition file (~tens of MB × grid² partitions) and OOMs under a modest memory_limit; a
        # local write streams each partition to disk with bounded memory.
        local_glob = os.path.join(local_parts, "**", "*.parquet").replace("\\", "/").replace("'", "''")
        conn.execute(
            f"COPY (SELECT *, {cell_sql} AS __cell FROM {part_src}) "
            f"TO '{local_parts.replace(chr(39), chr(39) * 2)}' (FORMAT PARQUET, PARTITION_BY (__cell), "
            f"COMPRESSION ZSTD, ROW_GROUP_SIZE {int(row_group_size)}, KV_METADATA {{geo: '{geo_str}'}}, "
            f"OVERWRITE_OR_IGNORE)")
        fc = conn.execute(
            f"SELECT COUNT(*) FROM read_parquet('{local_glob}', hive_partitioning=false)").fetchone()[0]
        logger.info("partition_with_covering: %s — partitioned %s features into a %dx%d grid in %.0fs → uploading %s",
                    s3_key, f"{fc:,}", grid, grid, time.monotonic() - started, out_prefix)

        # Upload every partition file to the S3 prefix, preserving the __cell=N/ layout.
        import boto3
        from botocore.client import Config
        s3 = boto3.client(
            "s3", endpoint_url=creds["endpoint"],
            aws_access_key_id=creds["access_key"], aws_secret_access_key=creds["secret_key"],
            region_name=creds["region"], config=Config(signature_version="s3v4"))
        n_files = 0
        for root, _dirs, fnames in os.walk(local_parts):
            for fn in fnames:
                full = os.path.join(root, fn)
                rel = os.path.relpath(full, local_parts).replace(os.sep, "/")
                s3.upload_file(full, bkt, f"{out_prefix}/{rel}",
                               ExtraArgs={"ContentType": "application/octet-stream"})
                n_files += 1
        logger.info("partition_with_covering: %s — uploaded %d partition files in %.0fs total",
                    s3_key, n_files, time.monotonic() - started)

        return {"out_key": out_prefix, "feature_count": int(fc),
                "geometry_column": geom_col, "partitioned": True, "partition_files": n_files}
    finally:
        conn.close()
        if os.path.exists(local_bbox):
            try:
                os.unlink(local_bbox)
            except OSError:
                pass
        shutil.rmtree(local_parts, ignore_errors=True)  # local partition output
        shutil.rmtree(spill_dir, ignore_errors=True)    # this run's DuckDB spill files


def build_manifest(s3_key: str, creds: dict | None = None, bucket: str | None = None) -> dict:
    """Describe a partitioned GeoParquet dataset for clients that cannot list S3 — the portal.js
    duckdb-wasm viewport reader: the partition grid, CRS, covering column, and each grid cell's
    object keys (relative to the prefix, served via the public `/parquet/{path}` range proxy).
    Row counts come from parquet footers (no data scan). Uploaded as `manifest.json` under the
    prefix by `tasks/geoparquet_prep`; a re-prep writes a new prefix, so a manifest never goes
    stale for the prefix it lives in."""
    import re
    settings = get_settings()
    bkt = bucket or (creds.get("bucket") if creds else None) or settings.storage_bucket
    base = s3_key.rstrip("/")
    if base.endswith(".parquet"):
        raise ValueError("Manifests are only built for partitioned prefixes.")
    loc = f"s3://{bkt}/{base}"
    meta_path, src = _parquet_paths(loc)
    conn = _connect_read(creds)
    try:
        meta = _read_geo_metadata(conn, meta_path)
        glob_sql = f"{loc}/**/*.parquet".replace("'", "''")
        files = [r[0] for r in conn.execute(f"SELECT file FROM glob('{glob_sql}')").fetchall()]
        if not files:
            raise ValueError(f"No parquet files under {loc}")
        rows_by_file: dict = {}
        try:  # per-file row counts from the footers; optional (clients tolerate absence)
            rows_by_file = dict(conn.execute(
                f"SELECT file_name, max(num_rows) FROM parquet_file_metadata('{glob_sql}') "
                "GROUP BY file_name").fetchall())
        except Exception:
            logger.warning("build_manifest: %s — row counts unavailable", base, exc_info=True)

        cov = meta.get("covering")
        cov_col = cov[0] if cov else None
        desc = conn.execute(f"DESCRIBE SELECT * FROM {src}").fetchall()
        columns = [{"name": r[0], "type": (r[1] or "").lower()} for r in desc
                   if r[0] != meta.get("column") and r[0] != cov_col]

        full_prefix = f"s3://{bkt}/{base}/"
        cells: dict[str, list] = {}
        total = 0
        for f in files:
            rel = f[len(full_prefix):] if f.startswith(full_prefix) else f
            m = re.search(r"__cell=(\d+)/", rel)
            cell = m.group(1) if m else "0"
            entry: dict = {"key": rel}
            n = rows_by_file.get(f)
            if n is not None:
                entry["rows"] = int(n)
                total += int(n)
            cells.setdefault(cell, []).append(entry)

        return {
            "version": 1,
            "crs": meta.get("epsg") or "EPSG:4326",
            "geometry_column": meta.get("column"),
            "geometry_types": meta.get("geometry_types"),
            "bbox": meta.get("bbox"),  # in the dataset's own CRS (from the geo metadata)
            "covering": {"column": cov[0], "fields": cov[1]} if cov else None,
            "grid": meta.get("grid"),  # {minx,miny,spanx,spany,grid} — mirrors query_features_geojson
            "feature_count": total or None,
            "columns": columns,
            "cells": cells,  # cell number → [{key, rows}] relative to the prefix
        }
    finally:
        conn.close()
