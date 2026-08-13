"""`GET /api/data/{vector,raster}/{ref}/legend` — the legend, served rather than re-derived.

`services.symbology.legend_entries` already decides what a legend shows, and the published portal
and the About page both read it. Nothing exposed it, so every OTHER renderer — the QGIS plugin
first — had to reconstruct class labels from `default_style`, and would eventually disagree with
the map about where a break falls or how a number is rounded. The same argument as `/field-stats`:
the client asks, it does not recompute.

What is pinned here is therefore mostly PARITY: the route's answer must equal what the portal draws
from, for the same style.
"""
import json

import pytest

from geodeploy.models import RasterLayer, User, VectorLayer
from geodeploy.services import symbology

GRADUATED = {
    "color_mode": "graduated",
    "color_field": "pop",
    "classes": [{"min": None, "max": 10, "color": "#eff3ff"},
                {"min": 10, "max": 90.5, "color": "#6baed6"},
                {"min": 90.5, "max": None, "color": "#08519c"}],
}


def _stored(style: dict) -> str:
    """A layer's `default_style` column: the style nested under the wrapper the API writes."""
    return json.dumps({"opacity": 1.0, "style": style, "popup_fields": []})


@pytest.fixture
async def layers(db):
    db.add(User(id=1, email="k@e.org", name="K", role="owner", hashed_password="x", is_admin=True))
    db.add_all([
        VectorLayer(id=1, user_id=1, uid="aaaaaaaaaaaa", name="Roads", table_name="t1",
                    schema_name="gd", storage_backend="postgis", status="ready",
                    geometry_type="linestring", is_public=True, visibility="public",
                    default_style=_stored(GRADUATED)),
        VectorLayer(id=2, user_id=1, uid="bbbbbbbbbbbb", name="Plots", table_name="t2",
                    schema_name="gd", storage_backend="postgis", status="ready",
                    geometry_type="polygon", is_public=True, visibility="public",
                    default_style=_stored({"color": "#e11d48", "marker": "star"})),
        VectorLayer(id=3, user_id=1, uid="cccccccccccc", name="Bare", table_name="t3",
                    schema_name="gd", storage_backend="postgis", status="ready",
                    is_public=True, visibility="public"),          # never styled
        VectorLayer(id=4, user_id=1, uid="dddddddddddd", name="Private", table_name="t4",
                    schema_name="gd", storage_backend="postgis", status="ready",
                    is_public=False, visibility="organization",
                    default_style=_stored(GRADUATED)),
        VectorLayer(id=5, user_id=1, uid="eeeeeeeeeeee", name="Cities", table_name="t5",
                    schema_name="gd", storage_backend="postgis", status="ready",
                    geometry_type="point", is_public=True, visibility="public",
                    default_style=_stored({"color": "#111", "size_mode": "proportional",
                                           "size_field": "pop",
                                           "size_stops": [[0, 4], [1000000, 24]]})),
    ])
    db.add_all([
        RasterLayer(id=1, user_id=1, uid="1111aaaa2222", name="DEM", s3_key="r/1/d.tif",
                    status="ready", is_public=True, visibility="public", band_count=1,
                    default_style=_stored({"colormap": "viridis", "rescale": "0,255"})),
        RasterLayer(id=2, user_id=1, uid="3333bbbb4444", name="Secret", s3_key="r/1/s.tif",
                    status="ready", is_public=False, visibility="organization"),
    ])
    await db.commit()
    yield db


class TestVectorLegend:
    async def test_a_graduated_layer_lists_its_classes(self, client, layers):
        r = await client.get("/api/data/vector/aaaaaaaaaaaa/legend")
        assert r.status_code == 200
        body = r.json()
        assert body["color_mode"] == "graduated" and body["field"] == "pop"
        assert [e["label"] for e in body["entries"]] == ["< 10", "10 – 90.5", "≥ 90.5"]
        assert [e["color"] for e in body["entries"]] == ["#eff3ff", "#6baed6", "#08519c"]

    async def test_it_is_exactly_what_the_portal_draws_from(self, client, layers):
        """The whole point. If these ever differ, one of the two renderers is lying."""
        r = await client.get("/api/data/vector/aaaaaaaaaaaa/legend")
        assert r.json()["entries"] == symbology.legend_entries(GRADUATED)

    async def test_a_single_symbol_still_gets_one_swatch(self, client, layers):
        """`legend_entries` returns [] for a single symbol, but a caller drawing a legend needs
        something to draw — and inventing it per renderer is how they drift."""
        r = await client.get("/api/data/vector/bbbbbbbbbbbb/legend")
        body = r.json()
        assert body["color_mode"] == "single" and body["field"] is None
        assert body["entries"] == [{"color": "#e11d48", "label": "Plots"}]

    async def test_an_unstyled_layer_falls_back_to_the_default_colour(self, client, layers):
        r = await client.get("/api/data/vector/cccccccccccc/legend")
        assert r.json()["entries"] == [{"color": symbology.DEFAULT_COLOR, "label": "Bare"}]

    async def test_size_from_a_field_is_reported_separately(self, client, layers):
        """A second visual dimension: a legend explaining only colour is half a legend."""
        body = (await client.get("/api/data/vector/eeeeeeeeeeee/legend")).json()
        assert body["size"] == {"field": "pop", "stops": [[0, 4], [1000000, 24]]}

    async def test_a_layer_without_data_driven_size_reports_none(self, client, layers):
        assert (await client.get("/api/data/vector/aaaaaaaaaaaa/legend")).json()["size"] is None

    async def test_a_private_layer_is_not_readable(self, client, layers):
        """Same terms as every other per-layer artifact — a legend names the classes, which is
        information about the data."""
        assert (await client.get("/api/data/vector/dddddddddddd/legend")).status_code == 404

    async def test_it_answers_by_integer_id_too(self, client, layers):
        assert (await client.get("/api/data/vector/1/legend")).status_code == 200


class TestRasterLegend:
    async def test_a_raster_reports_a_ramp_not_swatches(self, client, layers):
        body = (await client.get("/api/data/raster/1111aaaa2222/legend")).json()
        assert body["ramp"] is True
        assert body["colormap"] == "viridis"
        assert body["band_count"] == 1

    async def test_the_stretch_comes_back_as_numbers(self, client, layers):
        """Stored as TiTiler wants it ("0,255"); a client wants to compute with it."""
        assert (await client.get("/api/data/raster/1111aaaa2222/legend")).json()["rescale"] == [
            0.0, 255.0]

    async def test_a_private_raster_is_not_readable(self, client, layers):
        assert (await client.get("/api/data/raster/3333bbbb4444/legend")).status_code == 404
