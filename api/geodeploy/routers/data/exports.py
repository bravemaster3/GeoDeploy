"""Per-layer downloads — "give me this dataset as a file".

Until now the only server-side export was **draw a box on a portal**
(`POST /portals/{slug}/export-bundle`): it needs a bbox, and it needs the layer to be on a portal.
That left a real gap for anyone approaching an instance from outside — QGIS, a plugin, a script:
a raster could be fetched whole (`/cog`), a GeoParquet layer could be fetched whole (`/parquet/…`),
but a **PostGIS vector layer had no file**. `features.geojson` stops at 50k features and OGC API -
Features pages, which GDAL handles but a person clicking "download" does not.

So these three routes wrap the SAME Celery machinery the portal export uses, with the bbox made
optional:

    POST /api/data/{vector|raster}/{ref}/export            → {job_id}
    GET  /api/data/{vector|raster}/{ref}/export-status/{job_id}
    GET  /api/data/{vector|raster}/{ref}/export-download/{job_id}

**Public, on the same terms as every other per-layer artifact**: a vector layer must be
`_publicly_readable` (shared publicly, or shown by a published portal) and a raster must be
`is_public`. Nothing new is exposed — this is a second shape of data that was already readable.

A raster export REQUIRES a bbox. The whole raster is already one file and `/cog` streams it by
range request; re-encoding it through a worker would spend minutes to produce a worse copy.
"""
from __future__ import annotations

import os
import re

from fastapi import HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ...config import get_settings

#: What each backend can be asked for. GeoParquet is offered only where it costs nothing (the
#: source is already parquet, so the clip stays inside DuckDB and never materialises GeoJSON).
VECTOR_FORMATS = ("geojson", "gpkg", "csv", "geoparquet")
RASTER_FORMATS = ("tif",)

#: A celery task id. Validated before it is used as a filename — this is a path built from user
#: input, and the portal export route learned the same lesson.
_JOB_ID = re.compile(r"^[0-9a-fA-F-]{8,64}$")


class LayerExportRequest(BaseModel):
    """`bbox` omitted = the whole layer. `target_crs` 'native' keeps the layer's own CRS where the
    format can carry it (GeoPackage, CSV, GeoParquet); GeoJSON is always EPSG:4326 (RFC 7946)."""

    # No default here: the sensible one differs by layer kind (geojson for vector, tif for
    # raster), and a shared default of "geojson" made every raster export fail the format check.
    format: str | None = None
    bbox: str | None = None            # "minx,miny,maxx,maxy" in EPSG:4326
    target_crs: str = "4326"           # '4326' | 'native'


def validate_bbox(bbox: str | None) -> str | None:
    if bbox is None or bbox == "":
        return None
    try:
        parts = [float(v) for v in bbox.split(",")]
    except ValueError:
        raise HTTPException(400, "bbox must be 'minx,miny,maxx,maxy' in EPSG:4326.")
    if len(parts) != 4:
        raise HTTPException(400, "bbox must be 'minx,miny,maxx,maxy' in EPSG:4326.")
    if parts[0] >= parts[2] or parts[1] >= parts[3]:
        raise HTTPException(400, "bbox is empty: min must be less than max on both axes.")
    return bbox


def vector_item(layer, fmt: str | None) -> dict:
    """The export-task item for a vector layer, whichever backend it lives on."""
    fmt = fmt or "geojson"
    if fmt not in VECTOR_FORMATS:
        raise HTTPException(400, "format must be one of {0}.".format(", ".join(VECTOR_FORMATS)))
    if layer.storage_backend == "geoparquet":
        if not layer.s3_key:
            raise HTTPException(409, "This layer has no data file yet.")
        return {"type": "geoparquet", "s3_key": layer.s3_key, "crs": layer.crs or "EPSG:4326",
                "name": layer.name, "format": fmt}
    if fmt == "geoparquet":
        raise HTTPException(
            400, "GeoParquet export is only available for file-backed layers; this one is in "
                 "PostGIS. Ask for gpkg, geojson or csv.")
    return {"type": "vector", "schema": layer.schema_name, "table": layer.table_name,
            "name": layer.name, "format": fmt}


def raster_item(layer, fmt: str | None, bbox: str | None) -> dict:
    fmt = fmt or "tif"
    if fmt not in RASTER_FORMATS:
        raise HTTPException(400, "Raster export format must be tif.")
    if bbox is None:
        raise HTTPException(
            400, "A raster export needs a bbox. The whole file is already one download: "
                 "GET /api/data/raster/{0}/cog".format(layer.uid or layer.id))
    return {"type": "raster", "s3_key": layer.s3_key, "name": layer.name}


def start(item: dict, bbox: str | None, target_crs: str) -> dict:
    """Queue the export and return the job id to poll."""
    from ...routers.portals import _sweep_old_exports
    from ...tasks.export import export_bundle

    if target_crs not in ("4326", "native"):
        raise HTTPException(400, "target_crs must be '4326' or 'native'.")
    _sweep_old_exports(get_settings())
    task = export_bundle.delay(bbox, [item], target_crs=target_crs)
    return {"job_id": task.id}


def status(job_id: str) -> dict:
    """queued | processing | ready | error — the portal export's contract, unchanged.

    Readiness is the EXISTENCE of the finished file, not the task state: the task writes to a
    `.part` and renames atomically, so a file that is there is a file that is complete, and the
    answer survives a worker restart that loses the result backend entry.
    """
    from ...celery_app import celery_app
    _check_job_id(job_id)
    settings = get_settings()
    if os.path.exists("{0}/temp/exports/{1}.zip".format(settings.data_dir, job_id)):
        return {"status": "ready"}
    state = celery_app.AsyncResult(job_id).state
    if state == "FAILURE":
        return {"status": "error"}
    if state == "STARTED":
        return {"status": "processing"}
    return {"status": "queued"}


def download(job_id: str, filename: str) -> FileResponse:
    _check_job_id(job_id)
    settings = get_settings()
    path = "{0}/temp/exports/{1}.zip".format(settings.data_dir, job_id)
    if not os.path.exists(path):
        raise HTTPException(404, "That export is not ready (or has been swept).")
    return FileResponse(path, media_type="application/zip", filename=filename)


def _check_job_id(job_id: str) -> None:
    # The id becomes a path segment. A traversal here would serve any file the API can read.
    if not _JOB_ID.match(job_id or ""):
        raise HTTPException(400, "Invalid job id.")
