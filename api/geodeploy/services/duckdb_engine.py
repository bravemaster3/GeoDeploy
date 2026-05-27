"""DuckDB in-process engine for file-based vector layers and analytics."""
import duckdb
from ..config import get_settings

_conn: duckdb.DuckDBPyConnection | None = None


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
    conn.execute(f"SET s3_endpoint='{settings.storage_endpoint.replace('http://', '').replace('https://', '')}'")
    conn.execute(f"SET s3_access_key_id='{settings.storage_access_key}'")
    conn.execute(f"SET s3_secret_access_key='{settings.storage_secret_key}'")
    conn.execute(f"SET s3_region='{settings.storage_region}'")
    if settings.storage_endpoint.startswith("http://"):
        conn.execute("SET s3_use_ssl=false")
        conn.execute("SET s3_url_style='path'")


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
