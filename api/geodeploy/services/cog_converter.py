"""Cloud-Optimised GeoTIFF conversion and inspection using rasterio."""
import os
import tempfile

import rasterio
from rasterio.enums import Resampling
from rasterio.shutil import copy as rio_copy

OVERVIEW_LEVELS = [2, 4, 8, 16, 32, 64]
COG_PROFILE = {
    "driver": "GTiff",
    "tiled": True,
    "blockxsize": 512,
    "blockysize": 512,
    "compress": "lzw",
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
    }


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
