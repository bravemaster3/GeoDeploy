"""`GET /api/data/{vector,raster}/{ref}/legend` — the legend, served rather than re-derived.

`services.symbology.legend_entries` already decides what a legend shows, and the published portal
and the About page both read it. Nothing exposed it, so every OTHER renderer — the QGIS plugin
first — had to reconstruct class labels from `default_style`, and would eventually disagree with
the map about where a break falls or how a number is rounded. The same argument as `/field-stats`:
the client asks, it does not recompute.

What is pinned here is therefore mostly PARITY: the route's answer must equal what the portal draws
from, for the same style.
"""
import hashlib
import json
from datetime import timedelta

import pytest
from jose import jwt

from geodeploy.config import get_settings
from geodeploy.models import ApiToken, RasterLayer, User, VectorLayer
from geodeploy.services import symbology
from geodeploy.timeutil import naive_utcnow

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


def _session(uid: int = 1) -> dict:
    """A browser session for `uid` — the credential the dashboard sends."""
    return {"Authorization": "Bearer " + jwt.encode({"sub": str(uid)},
                                                    get_settings().secret_key, algorithm="HS256")}


async def _token(db, scopes: str, uid: int = 1, raw: str = "gdp_testtoken") -> dict:
    """A live API token carrying `scopes` — the credential a plugin or the CLI sends."""
    db.add(ApiToken(user_id=uid, name="test", scopes=scopes, prefix=raw[:12],
                    token_hash=hashlib.sha256(raw.encode()).hexdigest(),
                    expires_at=naive_utcnow() + timedelta(days=1)))
    await db.commit()
    return {"Authorization": "Bearer " + raw}


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

    async def test_a_private_layer_is_not_readable_anonymously(self, client, layers):
        """A legend names the classes, which is information about the data."""
        assert (await client.get("/api/data/vector/dddddddddddd/legend")).status_code == 404

    async def test_it_answers_by_integer_id_too(self, client, layers):
        assert (await client.get("/api/data/vector/1/legend")).status_code == 200


class TestASignedInCallerSeesTheirOwnLayers:
    """Found by running the CLI against a live instance: the OWNER of an organization layer, holding
    a valid token, got 404 for the legend of their own data. `_publicly_readable` is right for a
    rendering an anonymous visitor can already see; a legend is metadata the dashboard shows, and
    the plugin asks for it with the same credential the dashboard uses."""

    async def test_a_session_reads_its_own_organization_layer(self, client, layers):
        r = await client.get("/api/data/vector/dddddddddddd/legend", headers=_session())
        assert r.status_code == 200
        assert r.json()["entries"][0]["label"] == "< 10"

    async def test_a_token_with_data_read_does_too(self, client, layers, db):
        headers = await _token(db, "data:read portal:read")
        assert (await client.get("/api/data/vector/dddddddddddd/legend",
                                 headers=headers)).status_code == 200

    async def test_a_token_WITHOUT_data_read_sees_only_the_public_answer(self, client, layers, db):
        """Deny-by-default: a token never exceeds the scopes it was granted, so an unscoped one is
        treated as anonymous rather than as its owner."""
        headers = await _token(db, "portal:write", raw="gdp_noread")
        assert (await client.get("/api/data/vector/dddddddddddd/legend",
                                 headers=headers)).status_code == 404
        assert (await client.get("/api/data/vector/aaaaaaaaaaaa/legend",
                                 headers=headers)).status_code == 200      # public, still fine

    async def test_a_garbage_credential_reads_as_anonymous_not_as_an_error(self, client, layers):
        """The route is public. Failing a request that would have succeeded with no header at all
        is the worse answer."""
        bad = {"Authorization": "Bearer gdp_not_a_real_token"}
        assert (await client.get("/api/data/vector/aaaaaaaaaaaa/legend",
                                 headers=bad)).status_code == 200
        assert (await client.get("/api/data/vector/dddddddddddd/legend",
                                 headers=bad)).status_code == 404

    async def test_a_raster_behaves_the_same_way(self, client, layers):
        assert (await client.get("/api/data/raster/3333bbbb4444/legend")).status_code == 404
        assert (await client.get("/api/data/raster/3333bbbb4444/legend",
                                 headers=_session())).status_code == 200


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
