"""One NaN must not empty a whole list.

A float32 GeoTIFF's nodata is very often NaN. Postgres stores it without complaint, but JSON has
no literal for it and Starlette serialises with `allow_nan=False` — so the ValueError is raised
while the RESPONSE is being built, after the handler has returned successfully. The client does
not get one broken layer among many; it gets HTTP 500 and no layers at all, which is how a single
raster upload made My Data look empty for every kind of layer at once.

These tests assert at the two places it can be stopped: the metadata read at ingest (so nothing
non-finite is ever stored) and the response schema (so rows already in the database, written by
earlier versions, still serialise).
"""
import json
import math

from starlette.responses import JSONResponse
import pytest

from geodeploy.schemas import RasterLayerOut, VectorLayerOut


def _raster(**over):
    base = dict(
        id=1, name="dem", s3_key="rasters/dem.tif", crs="EPSG:4326",
        bbox=[11.0, 55.0, 12.0, 56.0], band_count=1, nodata_value=None,
        file_size=1024, status="ready", error_message=None, default_style=None,
        created_at="2026-08-15T00:00:00",
    )
    base.update(over)
    return base


def test_nan_nodata_does_not_reach_json():
    """The exact shape of the reported failure: nodata=NaN on one raster."""
    obj = RasterLayerOut(**_raster(nodata_value=float("nan")))
    assert obj.nodata_value is None
    # The real assertion is not the None — it is that the response can be BUILT.
    JSONResponse(content=json.loads(obj.model_dump_json()))


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_every_non_finite_float_is_dropped(bad):
    assert RasterLayerOut(**_raster(nodata_value=bad)).nodata_value is None


def test_finite_nodata_is_preserved():
    """The guard must not eat legitimate values — -9999 is the commonest nodata there is."""
    assert RasterLayerOut(**_raster(nodata_value=-9999.0)).nodata_value == -9999.0
    assert RasterLayerOut(**_raster(nodata_value=0.0)).nodata_value == 0.0


def test_bbox_is_all_or_nothing():
    """Three good corners and one NaN cannot be fitted to. Dropping the whole box makes the map
    fall back to a default view; keeping a partial one would silently mis-zoom."""
    assert RasterLayerOut(**_raster(bbox=[11.0, 55.0, float("nan"), 56.0])).bbox is None
    assert RasterLayerOut(**_raster(bbox=[11.0, 55.0, 12.0, 56.0])).bbox == [11.0, 55.0, 12.0, 56.0]


def test_vector_bbox_is_guarded_too():
    """Reprojection can yield inf for any layer kind, not only rasters."""
    v = VectorLayerOut(
        id=1, name="roads", table_name="t", schema_name="s", crs="EPSG:4326",
        feature_count=5, bbox=[float("-inf"), 55.0, 12.0, 56.0], columns=None,
        geometry_type="LineString", file_size=1, storage_backend="postgis",
        status="ready", error_message=None, default_style=None,
        created_at="2026-08-15T00:00:00",
    )
    assert v.bbox is None


def test_list_response_survives_one_bad_row():
    """The regression itself: a good layer must still arrive when a bad one shares the list."""
    rows = [RasterLayerOut(**_raster(id=1, nodata_value=float("nan"))),
            RasterLayerOut(**_raster(id=2, name="ok", nodata_value=-9999.0))]
    body = JSONResponse(content=[json.loads(r.model_dump_json()) for r in rows]).body
    assert len(json.loads(body)) == 2


def test_metadata_never_returns_non_finite():
    """Root cause, at the ingest end: rasterio hands back nan for a float32 nodata."""
    from geodeploy.services.cog_converter import _read_meta

    class _Bounds:
        left, bottom, right, top = 11.0, 55.0, 12.0, 56.0

    class _DS:
        crs = None
        bounds = _Bounds()
        nodata = float("nan")
        count = 1
        width = height = 256
        dtypes = ("float32",)

        def overviews(self, _b):
            return []

    meta = _read_meta(_DS())
    assert meta["nodata_value"] is None
    assert all(math.isfinite(v) for v in meta["bbox"])
