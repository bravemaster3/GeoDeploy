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
    "predictor": 2,
}


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

        with rasterio.open(tmp_path) as src:
            rio_copy(src, dst_path, copy_src_overviews=True, **cog_profile)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


def inspect(path: str) -> dict:
    """Return basic metadata from a raster file."""
    with rasterio.open(path) as ds:
        crs = ds.crs
        epsg = crs.to_epsg() if crs else None
        crs_str = f"EPSG:{epsg}" if epsg else (crs.to_string() if crs else None)
        b = ds.bounds
        nodata = ds.nodata
        return {
            "crs": crs_str,
            "bbox": [b.left, b.bottom, b.right, b.top],
            "band_count": ds.count,
            "nodata_value": float(nodata) if nodata is not None else None,
            "width": ds.width,
            "height": ds.height,
        }
