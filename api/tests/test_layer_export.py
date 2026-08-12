"""Per-layer download — `POST /api/data/{kind}/{ref}/export`.

Two things are being pinned. First the ACCESS rule: this serves the same data the other per-layer
artifacts serve, so it must be readable on exactly the same terms and no wider — a private layer
that becomes downloadable because a new route forgot the check is a data leak, not a bug report.

Second the bbox rule: **omitting it means the whole layer**, and the task must then emit no spatial
predicate at all. A world envelope would look equivalent and quietly drop rows, because
transforming -180…180 into a projected CRS is undefined near the poles — precisely the national and
polar datasets where nobody would notice the loss.
"""
import json
from unittest.mock import patch

import pytest

from geodeploy.models import Portal, RasterLayer, User, VectorLayer
from geodeploy.tasks import export as export_task


class _Task:
    id = "11111111-2222-3333-4444-555555555555"


@pytest.fixture
async def layers(db):
    db.add(User(id=1, email="k@e.org", name="K", role="owner", hashed_password="x", is_admin=True))
    db.add_all([
        VectorLayer(id=1, user_id=1, uid="aaaaaaaaaaaa", name="Roads", table_name="t1",
                    schema_name="gd", storage_backend="postgis", status="ready",
                    is_public=True, visibility="public"),
        VectorLayer(id=2, user_id=1, uid="bbbbbbbbbbbb", name="Parcels", table_name="t2",
                    schema_name="gd", storage_backend="geoparquet", status="ready",
                    is_public=True, visibility="public", s3_key="vectors/1/x/p.parquet"),
        VectorLayer(id=3, user_id=1, uid="cccccccccccc", name="Private", table_name="t3",
                    schema_name="gd", storage_backend="postgis", status="ready",
                    is_public=False, visibility="organization"),
        VectorLayer(id=4, user_id=1, uid="dddddddddddd", name="Busy", table_name="t4",
                    schema_name="gd", storage_backend="postgis", status="processing",
                    is_public=True, visibility="public"),
    ])
    db.add_all([
        RasterLayer(id=1, user_id=1, uid="eeeeeeeeeeee", name="DEM", s3_key="rasters/1/x/d.tif",
                    status="ready", is_public=True, visibility="public"),
        RasterLayer(id=2, user_id=1, uid="ffffffffffff", name="Secret", s3_key="rasters/1/y/s.tif",
                    status="ready", is_public=False, visibility="organization"),
    ])
    await db.commit()
    yield db


def _queued():
    """Patch the Celery dispatch: these tests are about the ROUTE, not the worker."""
    return patch.object(export_task.export_bundle, "delay", return_value=_Task())


class TestAccess:
    async def test_a_public_layer_can_be_exported_without_a_token(self, client, layers):
        with _queued():
            r = await client.post("/api/data/vector/aaaaaaaaaaaa/export", json={"format": "gpkg"})
        assert r.status_code == 202
        assert r.json()["job_id"] == _Task.id

    async def test_a_private_layer_is_not_found(self, client, layers):
        r = await client.post("/api/data/vector/cccccccccccc/export", json={})
        assert r.status_code == 404

    async def test_a_layer_still_ingesting_is_not_exportable(self, client, layers):
        r = await client.post("/api/data/vector/dddddddddddd/export", json={})
        assert r.status_code == 404

    async def test_a_layer_shown_by_a_published_portal_is_exportable(self, client, db, layers):
        """Same rule as pmtiles/features: a published portal makes its layers readable."""
        db.add(Portal(id=1, user_id=1, title="P", slug="p", published=True, access_type="public",
                      template_id="minimal",
                      layer_configs=json.dumps([{"layer_id": 3, "layer_type": "vector"}])))
        await db.commit()
        from geodeploy.routers.data import vector as vector_router
        vector_router.invalidate_public_layers()
        with _queued():
            r = await client.post("/api/data/vector/cccccccccccc/export", json={})
        assert r.status_code == 202

    async def test_private_raster(self, client, layers):
        assert (await client.post("/api/data/raster/ffffffffffff/export",
                                  json={"bbox": "1,1,2,2"})).status_code == 404


class TestWhatIsQueued:
    async def test_no_bbox_means_the_whole_layer(self, client, layers):
        with _queued() as delay:
            await client.post("/api/data/vector/aaaaaaaaaaaa/export", json={"format": "gpkg"})
        bbox, items = delay.call_args.args[0], delay.call_args.args[1]
        assert bbox is None                       # NOT a world envelope — see the module docstring
        assert items == [{"type": "vector", "schema": "gd", "table": "t1", "name": "Roads",
                          "format": "gpkg"}]

    async def test_a_bbox_is_passed_through(self, client, layers):
        with _queued() as delay:
            await client.post("/api/data/vector/aaaaaaaaaaaa/export",
                              json={"format": "csv", "bbox": "11,55,12,56"})
        assert delay.call_args.args[0] == "11,55,12,56"

    async def test_a_geoparquet_layer_exports_from_its_file(self, client, layers):
        with _queued() as delay:
            await client.post("/api/data/vector/bbbbbbbbbbbb/export",
                              json={"format": "geoparquet"})
        item = delay.call_args.args[1][0]
        assert item["type"] == "geoparquet" and item["s3_key"] == "vectors/1/x/p.parquet"

    async def test_native_crs_is_offered(self, client, layers):
        with _queued() as delay:
            await client.post("/api/data/vector/bbbbbbbbbbbb/export",
                              json={"format": "gpkg", "target_crs": "native"})
        assert delay.call_args.kwargs["target_crs"] == "native"


class TestArgumentChecking:
    @pytest.mark.parametrize("bbox", ["1,2,3", "a,b,c,d", "5,5,1,1", "1,1,1,1"])
    async def test_a_bad_bbox_is_refused_before_the_worker(self, client, layers, bbox):
        r = await client.post("/api/data/vector/aaaaaaaaaaaa/export", json={"bbox": bbox})
        assert r.status_code == 400

    async def test_an_unknown_format(self, client, layers):
        r = await client.post("/api/data/vector/aaaaaaaaaaaa/export", json={"format": "shp"})
        assert r.status_code == 400
        assert "gpkg" in r.json()["detail"]

    async def test_geoparquet_is_refused_for_a_postgis_layer_with_a_reason(self, client, layers):
        r = await client.post("/api/data/vector/aaaaaaaaaaaa/export",
                              json={"format": "geoparquet"})
        assert r.status_code == 400
        assert "PostGIS" in r.json()["detail"]

    async def test_a_raster_export_requires_a_bbox_and_names_the_alternative(self, client, layers):
        """The whole raster is already one file; /cog streams it. Say so rather than burn a worker."""
        r = await client.post("/api/data/raster/eeeeeeeeeeee/export", json={})
        assert r.status_code == 400
        assert "/cog" in r.json()["detail"]

    async def test_a_raster_clip_is_accepted(self, client, layers):
        with _queued() as delay:
            r = await client.post("/api/data/raster/eeeeeeeeeeee/export",
                                  json={"bbox": "11,55,12,56"})
        assert r.status_code == 202
        assert delay.call_args.args[1][0]["type"] == "raster"

    async def test_an_invalid_job_id_cannot_traverse_the_filesystem(self, client, layers):
        """The id becomes a path segment; a traversal here would serve any file the API can read."""
        r = await client.get("/api/data/vector/aaaaaaaaaaaa/export-status/..%2F..%2Fetc%2Fpasswd")
        assert r.status_code in (400, 404)

    async def test_downloading_an_export_that_does_not_exist(self, client, layers):
        r = await client.get(
            "/api/data/vector/aaaaaaaaaaaa/export-download/" + _Task.id)
        assert r.status_code == 404


class TestTheTaskItself:
    """The SQL the whole-layer path builds. Pinned here because it is the difference between
    "downloaded the dataset" and "downloaded the first 50,000 rows of it"."""

    def test_no_bbox_emits_no_spatial_filter_and_the_larger_cap(self):
        where, params, cap = export_task._filter(None, 3006)
        assert where == "" and params == ()
        assert cap == export_task.FULL_EXPORT_CAP > export_task.FEATURE_CAP

    def test_a_bbox_still_filters_in_the_table_srid_with_the_clip_cap(self):
        where, params, cap = export_task._filter((11, 55, 12, 56), 3006)
        assert "ST_Transform" in where and "geom &&" in where
        assert len(params) == 8 and cap == export_task.FEATURE_CAP

    def test_the_manifest_says_which_cap_applied(self):
        text = export_task._manifest([{"name": "Roads", "type": "vector", "format": "gpkg"}],
                                     None, "4326", ["roads.gpkg"])
        assert "whole layer, no clip" in text
        assert str(export_task.FULL_EXPORT_CAP) in text
        assert "TRUNCATED" in text          # an export that hit the cap must say so in the zip
        assert "roads.gpkg" in text

    def test_the_manifest_records_a_clip(self):
        text = export_task._manifest([{"name": "Roads", "type": "vector", "format": "csv"}],
                                     "11,55,12,56", "native", ["roads.csv"])
        assert "bbox 11,55,12,56" in text
        assert "native" in text
