"""GDAL-based Cloud-Optimised GeoTIFF conversion."""
import os
import subprocess
import tempfile
from osgeo import gdal

gdal.UseExceptions()

COG_OPTIONS = [
    "-of", "GTiff",
    "-co", "TILED=YES",
    "-co", "BLOCKXSIZE=512",
    "-co", "BLOCKYSIZE=512",
    "-co", "COMPRESS=LZW",
    "-co", "PREDICTOR=2",
    "-co", "COPY_SRC_OVERVIEWS=YES",
]
OVERVIEW_LEVELS = [2, 4, 8, 16, 32, 64]


def is_cog(path: str) -> bool:
    result = gdal.VSIStatL(path)
    if result is None:
        return False
    ds = gdal.Open(path)
    if ds is None:
        return False
    md = ds.GetMetadata("MAIN_FILE")
    return md.get("OVR_RESAMPLING_ALG") is not None


def convert_to_cog(src_path: str, dst_path: str) -> None:
    """Convert any GDAL-readable raster to COG with overviews."""
    with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        ds = gdal.Open(src_path, gdal.GA_ReadOnly)
        if ds is None:
            raise ValueError(f"GDAL cannot open: {src_path}")

        gdal.BuildOverviews(
            ds,
            "NEAREST",
            OVERVIEW_LEVELS,
            callback=gdal.TermProgress_nocb,
        )
        ds.FlushCache()
        ds = None

        cmd = ["gdal_translate", src_path, tmp_path] + COG_OPTIONS
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"gdal_translate failed: {result.stderr}")

        os.replace(tmp_path, dst_path)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def inspect(path: str) -> dict:
    """Return basic metadata from a raster file."""
    ds = gdal.Open(path, gdal.GA_ReadOnly)
    if ds is None:
        raise ValueError(f"Cannot open raster: {path}")

    srs = ds.GetSpatialRef()
    crs = srs.GetAuthorityCode(None) if srs else None
    if crs:
        crs = f"EPSG:{crs}"

    gt = ds.GetGeoTransform()
    cols, rows = ds.RasterXSize, ds.RasterYSize
    minx = gt[0]
    maxy = gt[3]
    maxx = minx + gt[1] * cols
    miny = maxy + gt[5] * rows

    nodata = ds.GetRasterBand(1).GetNoDataValue()

    return {
        "crs": crs,
        "bbox": [minx, miny, maxx, maxy],
        "band_count": ds.RasterCount,
        "nodata_value": nodata,
        "width": cols,
        "height": rows,
    }
