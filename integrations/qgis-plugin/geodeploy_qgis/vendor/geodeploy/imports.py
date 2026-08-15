"""Register data that is ALREADY there — in the database, or in the bucket.

"Import existing" is not an upload: nothing is copied and nothing is destroyed. A PostGIS table is
introspected and registered; a `.parquet`/`.tif`/`.csv` already in object storage is attached where
it lies. For GeoParquet the spatial prep writes its partitioned copy under `vectors/` and leaves the
source key alone — which is why `source_s3_key` exists, so re-scanning still recognises the file as
already imported after the prep repoints the layer.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .errors import ValidationError


class Imports(object):
    def __init__(self, client: Any):
        self._c = client

    # ── PostGIS ─────────────────────────────────────────────────────────────────────────────────

    def database_tables(self) -> List[Dict[str, Any]]:
        """Spatial tables visible to the instance, each flagged if it is already registered."""
        return self._c.get("/data/discover/database") or []

    def database(self, tables: List[Dict[str, Any]]) -> Any:
        """Register tables. Each item needs `schema_name`, `table_name`, `geometry_column`;
        `srid`, `geometry_type` and a `name` override are optional."""
        if not tables:
            raise ValidationError(400, "No tables selected.")
        return self._c.post("/data/discover/database", {"tables": tables})

    # ── Object storage ──────────────────────────────────────────────────────────────────────────

    def storage_objects(self, kind: Optional[str] = None) -> List[Dict[str, Any]]:
        """`.tif` (raster), `.parquet`/`.geoparquet` (geoparquet) and `.csv` files in the bucket."""
        rows = self._c.get("/data/discover/storage") or []
        if kind:
            rows = [r for r in rows if (r.get("kind") or "") == kind]
        return rows

    def storage(self, items: List[Dict[str, Any]]) -> Any:
        """Attach objects by key. GeoParquet items come back with `jobs` to poll (inspect + prep);
        rasters are registered from their header alone and need no job."""
        if not items:
            raise ValidationError(400, "No objects selected.")
        return self._c.post("/data/discover/storage", {"items": items})

    def csv_columns(self, key: str, delimiter: str = "comma") -> Any:
        """The header of a CSV in the bucket, so geometry columns can be chosen before importing."""
        return self._c.get("/data/discover/storage/csv-columns",
                           {"key": key, "delimiter": delimiter})

    def csv(self, key: str, name: Optional[str] = None, x_column: Optional[str] = None,
            y_column: Optional[str] = None, wkt_column: Optional[str] = None, srid: int = 4326,
            delimiter: str = "comma") -> Dict[str, Any]:
        """Build a PostGIS layer from a CSV already in storage (queued; returns a job to poll)."""
        if not wkt_column and not (x_column and y_column):
            raise ValidationError(400, "Pick X and Y columns, or a WKT column.")
        body = {"key": key, "srid": srid, "delimiter": delimiter}
        for field, value in (("name", name), ("x_column", x_column), ("y_column", y_column),
                             ("wkt_column", wkt_column)):
            if value is not None:
                body[field] = value
        return self._c.post("/data/discover/storage/csv", body)
