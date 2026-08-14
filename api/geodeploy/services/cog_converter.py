"""Cloud-Optimised GeoTIFF conversion and inspection using rasterio."""
import os
import tempfile

import rasterio
from rasterio.enums import Resampling
from rasterio.shutil import copy as rio_copy

OVERVIEW_LEVELS = [2, 4, 8, 16, 32, 64]
# A classic TIFF cannot exceed 4 GB — the offsets in its header are 32-bit. Past that, GDAL fails
# mid-write with "TIFFAppendToStrip:Maximum TIFF file size exceeded", which surfaced here as a
# rasterio CPLE_AppDefinedError out of build_overviews rather than as a size error, because the
# overviews are what pushes a large-but-legal raster over the line (they add roughly a third).
#
# IF_SAFER rather than YES: GDAL switches to BigTIFF whenever the UNCOMPRESSED image would exceed
# 2 GB, which leaves comfortable room for the overviews on top, while a raster small enough to be a
# classic TIFF stays one. That matters because BigTIFF is still refused by some older desktop GIS,
# and these files are downloadable — so the format is part of what we hand the user, not just an
# internal detail.
BIGTIFF = "IF_SAFER"
COG_PROFILE = {
    "driver": "GTiff",
    "tiled": True,
    "blockxsize": 512,
    "blockysize": 512,
    "compress": "lzw",
    "bigtiff": BIGTIFF,
}


def _predictor(dtype: str) -> int:
    import numpy as np
    kind = np.dtype(dtype).kind
    if kind == "f":
        return 3  # floating-point predictor
    if np.dtype(dtype).itemsize >= 2:
        return 2  # horizontal differencing for multi-byte integers
    return 1  # no predictor for 8-bit


def is_cog(path: str) -> bool:
    try:
        with rasterio.open(path) as ds:
            return ds.is_tiled and bool(ds.overviews(1))
    except Exception:
        return False


def convert_to_cog(src_path: str, dst_path: str) -> None:
    """Convert any rasterio-readable raster to a COG with overviews."""
    tmp_path = None
    try:
        with rasterio.open(src_path) as src:
            profile = src.profile.copy()

        # Build overviews on a temp copy so the source is not modified
        with tempfile.NamedTemporaryFile(suffix=".tif", delete=False,
                                        dir=os.path.dirname(src_path)) as tmp:
            tmp_path = tmp.name

        # The temp copy needs BigTIFF too, and it is the one that actually failed: overviews are
        # written INTO this file (r+ below), so its format is fixed at creation. A classic TIFF here
        # cannot be rescued later by the final copy's setting. Set in the dict rather than passed as
        # a kwarg — a BigTIFF source could carry `bigtiff` in its own profile and collide.
        profile["bigtiff"] = BIGTIFF
        with rasterio.open(src_path) as src:
            rio_copy(src, tmp_path, **profile)

        with rasterio.open(tmp_path, "r+") as ds:
            ds.build_overviews(OVERVIEW_LEVELS, Resampling.nearest)
            ds.update_tags(ns="rio_overview", resampling="nearest")

        cog_profile = {
            "driver": profile.get("driver", "GTiff"),
            "dtype": profile["dtype"],
            "nodata": profile.get("nodata"),
            "width": profile["width"],
            "height": profile["height"],
            "count": profile["count"],
            "crs": profile.get("crs"),
            "transform": profile.get("transform"),
        }
        cog_profile.update(COG_PROFILE)
        cog_profile["predictor"] = _predictor(profile["dtype"])

        with rasterio.open(tmp_path) as src:
            rio_copy(src, dst_path, copy_src_overviews=True, **cog_profile)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


def _read_meta(ds) -> dict:
    """Metadata from an open rasterio dataset. bbox is always EPSG:4326 (lon/lat) so the
    map/portal code can use it directly for fitBounds (sources are often in a projected CRS)."""
    from rasterio.warp import transform_bounds
    crs = ds.crs
    epsg = crs.to_epsg() if crs else None
    crs_str = f"EPSG:{epsg}" if epsg else (crs.to_string() if crs else None)
    b = ds.bounds
    nodata = ds.nodata
    if crs and epsg != 4326:
        try:
            west, south, east, north = transform_bounds(crs, "EPSG:4326", b.left, b.bottom, b.right, b.top)
            bbox = [west, south, east, north]
        except Exception:
            bbox = [b.left, b.bottom, b.right, b.top]  # fall back to source CRS
    else:
        bbox = [b.left, b.bottom, b.right, b.top]
    return {
        "crs": crs_str,
        "bbox": bbox,
        "band_count": ds.count,
        "nodata_value": float(nodata) if nodata is not None else None,
        "width": ds.width,
        "height": ds.height,
        # Overview decimation factors ([2, 4, 8, …]) of band 1. What they answer is "how cheaply can
        # this be drawn zoomed OUT" — see `low_zoom_is_cheap`.
        "overviews": list(ds.overviews(1) or []),
        # dtype of band 1 — drives whether a default display rescale is needed (non-8-bit data renders
        # black on tile servers that assume 0–255, so ingest computes a stretch for those).
        "dtype": str(ds.dtypes[0]) if ds.dtypes else None,
    }


#: A zoomed-out request is cheap when the pyramid has an overview at or below this many pixels on
#: its longest side — one small read, whatever area the tile covers.
_CHEAP_OVERVIEW_PX = 1024


def low_zoom_is_cheap(width: int | None, height: int | None, overviews: list | None) -> bool:
    """Can this raster be drawn at a low zoom without a big read? (issue #17)

    The minzoom floor exists because a z3 tile of a drone orthomosaic once took long enough that
    nginx returned 504, and a hanging tile costs the whole portal. But the floor is computed from
    the layer's EXTENT, which is only a proxy: what actually decides the cost is whether the file
    has an overview small enough to answer from directly.

    A COG we built always has one — the converter writes a full pyramid — so for those the floor is
    guesswork that hides a small high-resolution layer at zooms where someone might legitimately be
    looking, with no message and nothing in the console. An imported file with no overviews keeps
    the floor, because for that one the original reasoning still holds.
    """
    if not width or not height:
        return False
    longest = max(int(width), int(height))
    if longest <= _CHEAP_OVERVIEW_PX:
        return True                      # small enough that the full read IS the cheap read
    for factor in (overviews or []):
        try:
            if factor and longest / float(factor) <= _CHEAP_OVERVIEW_PX:
                return True
        except (TypeError, ValueError, ZeroDivisionError):
            # A driver can report anything; a display hint must not raise inside ingest.
            continue
    return False


def inspect(path: str) -> dict:
    """Return basic metadata from a local raster file (bbox reprojected to EPSG:4326)."""
    with rasterio.open(path) as ds:
        return _read_meta(ds)


def inspect_s3(s3_key: str, settings) -> dict:
    """Inspect a raster that already lives in S3/MinIO (for 'import existing data') — reads
    only the header via a range request, no download. Mirrors the GDAL S3 env used elsewhere."""
    from rasterio.session import AWSSession
    endpoint = (settings.storage_endpoint or "").replace("https://", "").replace("http://", "")
    use_https = (settings.storage_endpoint or "").lower().startswith("https")
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
            return _read_meta(ds)
