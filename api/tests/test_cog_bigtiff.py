"""A big raster must not be written as a classic TIFF.

A classic TIFF cannot exceed 4 GB — its header offsets are 32-bit. GDAL does not refuse the job up
front; it fails PART WAY THROUGH the write with "TIFFAppendToStrip:Maximum TIFF file size exceeded",
which reached us as a rasterio CPLE_AppDefinedError raised from `build_overviews`. That is a
misleading place to land: the overviews are merely what tips a large-but-legal raster over the line
(they add roughly a third), so the size limit reads as an overview bug.

Both writes are pinned here, because they fail differently. The FINAL COG is the obvious one. The
TEMP copy is the one that actually broke: overviews are written into it in `r+` mode, so its format
is decided at creation and no later setting can rescue it — that write is where a 3 GB DEM died.

A 4 GB fixture is not a test, so this asserts the creation options rather than the outcome.
"""
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from geodeploy.services import cog_converter


@pytest.fixture()
def tiny_raster(tmp_path):
    """A real (very small) GeoTIFF — convert_to_cog opens and copies it for real."""
    path = tmp_path / "src.tif"
    with rasterio.open(
        path, "w", driver="GTiff", width=64, height=64, count=1,
        dtype="float32", crs="EPSG:4326", transform=from_origin(19.7, 64.3, 0.001, 0.001),
    ) as ds:
        ds.write(np.arange(64 * 64, dtype="float32").reshape(1, 64, 64))
    return str(path)


def test_both_writes_ask_for_bigtiff(tiny_raster, tmp_path, monkeypatch):
    seen = []
    real_copy = cog_converter.rio_copy

    def spy(src, dst, **kwargs):
        seen.append(kwargs.get("bigtiff"))
        return real_copy(src, dst, **kwargs)

    monkeypatch.setattr(cog_converter, "rio_copy", spy)
    cog_converter.convert_to_cog(tiny_raster, str(tmp_path / "out.tif"))

    # Two copies: the temp working file, then the final COG. Neither may be left as a classic TIFF.
    assert len(seen) == 2, f"expected temp + final copy, got {len(seen)}"
    assert all(v == cog_converter.BIGTIFF for v in seen), seen


def test_it_is_conditional_not_forced():
    """IF_SAFER, not YES. BigTIFF is still refused by some older desktop GIS and these files are
    downloadable, so a raster small enough to be a classic TIFF stays one. Forcing YES would change
    the format of every COG we hand a user."""
    assert cog_converter.BIGTIFF == "IF_SAFER"
    assert cog_converter.COG_PROFILE["bigtiff"] == "IF_SAFER"


def test_the_result_is_still_a_cog(tiny_raster, tmp_path):
    """The option must not cost us the thing the converter exists to produce."""
    out = str(tmp_path / "out.tif")
    cog_converter.convert_to_cog(tiny_raster, out)
    assert cog_converter.is_cog(out)
    # And the georeferencing survives — bbox drives the raster `bounds` that stops the 404s.
    meta = cog_converter.inspect(out)
    assert meta["crs"] == "EPSG:4326"
    assert meta["bbox"][0] == pytest.approx(19.7, abs=1e-6)
