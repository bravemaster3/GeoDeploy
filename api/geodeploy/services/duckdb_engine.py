"""DuckDB in-process engine for file-based (GeoParquet) vector layers and analytics.

GeoParquet layers live on object storage and are read in place by DuckDB — they are NOT
loaded into PostGIS (unlike CSV). `inspect_parquet` reads a file's metadata + geometry on
register (the file equivalent of `cog_converter.inspect_s3` for rasters).
"""
import json
import duckdb
from ..config import get_settings

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


def _connect(creds: dict | None = None) -> duckdb.DuckDBPyConnection:
    """A fresh DuckDB connection configured for S3. Used by Celery tasks, which must read
    storage creds from SQLite (passed in as `creds`) — the worker's env isn't reliably
    populated (a `docker restart` doesn't re-read .env). See notes_for_future §0f."""
    conn = duckdb.connect(":memory:")
    conn.execute("INSTALL spatial; LOAD spatial;")
    conn.execute("INSTALL httpfs; LOAD httpfs;")
    if creds:
        _apply_s3(conn, creds.get("endpoint"), creds.get("access_key"),
                  creds.get("secret_key"), creds.get("region"))
    else:
        _configure_s3(conn, get_settings())
    return conn


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


def _read_geo_metadata(conn: duckdb.DuckDBPyConnection, loc: str):
    """Parse the GeoParquet 'geo' key → (primary geometry column, source EPSG, metadata bbox).

    The metadata bbox (when the writer included it) lets us skip a full geometry scan — the
    expensive part of inspecting a multi-GB file.
    """
    try:
        rows = conn.execute(
            f"SELECT decode(value) FROM parquet_kv_metadata('{loc}') WHERE decode(key) = 'geo'"
        ).fetchall()
    except Exception:
        return None, None, None
    if not rows:
        return None, None, None
    try:
        meta = json.loads(rows[0][0])
    except Exception:
        return None, None, None
    primary = meta.get("primary_column")
    col_meta = (meta.get("columns") or {}).get(primary, {}) if primary else {}
    bbox = col_meta.get("bbox")
    if not (isinstance(bbox, list) and len(bbox) == 4):
        bbox = None
    return primary, _crs_to_epsg(col_meta.get("crs")), bbox


def _reproject_bbox(bbox: list[float], src_epsg: str) -> list[float]:
    try:
        from pyproj import Transformer
        t = Transformer.from_crs(src_epsg, "EPSG:4326", always_xy=True)
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
    conn = _connect(creds)
    try:
        loc = location.replace("'", "''")
        geom_col, src_epsg, meta_bbox = _read_geo_metadata(conn, loc)

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

        gtype_raw = next((t for n, t in all_cols if n == geom_col), "")
        gq = _q(geom_col)
        gexpr = gq if gtype_raw.upper() == "GEOMETRY" else f"ST_GeomFromWKB({gq})"
        rel = f"(SELECT {gexpr} AS g FROM read_parquet('{loc}'))"

        # COUNT(*) is cheap (parquet footer); geometry-type needs one row.
        fc = conn.execute(f"SELECT COUNT(*) FROM read_parquet('{loc}')").fetchone()[0]

        # Prefer the bbox from the GeoParquet metadata — scanning every geometry is the slow
        # part on a multi-GB file. Fall back to a scan only when the writer omitted it.
        bbox = list(meta_bbox) if meta_bbox else None
        if bbox is None:
            b = conn.execute(
                f"SELECT MIN(ST_XMin(g)), MIN(ST_YMin(g)), MAX(ST_XMax(g)), MAX(ST_YMax(g)) "
                f"FROM {rel} WHERE g IS NOT NULL"
            ).fetchone()
            bbox = [b[0], b[1], b[2], b[3]] if b and b[0] is not None else None
        if bbox and src_epsg and src_epsg != "EPSG:4326":
            bbox = _reproject_bbox(bbox, src_epsg)

        gt = conn.execute(
            f"SELECT UPPER(ST_GeometryType(g)) FROM {rel} WHERE g IS NOT NULL LIMIT 1"
        ).fetchone()
        gt_name = (gt[0] if gt else "").replace("ST_", "").upper()
        geometry_type = _GEOM_TYPE_MAP.get(gt_name)

        columns = [{"name": n, "type": t.lower()} for n, t in all_cols if n != geom_col]

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


def query_geojson(s3_key: str, where: str | None = None, limit: int = 10_000) -> dict:
    """Return a GeoJSON FeatureCollection from a GeoParquet file in S3."""
    settings = get_settings()
    conn = get_connection()
    path = f"s3://{settings.storage_bucket}/{s3_key}"
    sql = f"SELECT * FROM read_parquet('{path}')"
    if where:
        sql += f" WHERE {where}"
    sql += f" LIMIT {limit}"
    rel = conn.execute(sql)
    rows = rel.fetchall()
    cols = [desc[0] for desc in rel.description]

    features = []
    for row in rows:
        props = {cols[i]: row[i] for i in range(len(cols)) if cols[i] != "geometry"}
        geom_idx = cols.index("geometry") if "geometry" in cols else None
        geom = None
        if geom_idx is not None and row[geom_idx]:
            geom = {"type": "Unknown", "coordinates": []}  # WKB parsing handled by frontend via deck.gl
        features.append({"type": "Feature", "geometry": geom, "properties": props})

    return {"type": "FeatureCollection", "features": features}
