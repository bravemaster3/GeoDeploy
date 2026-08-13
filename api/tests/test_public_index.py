"""`GET /api/public` — the anonymous index a plugin starts from.

The thing worth pinning is not the shape of the JSON but the EXPOSURE RULE: this endpoint is the
one place that turns "reachable by anyone holding the link" into "findable by anyone at all", so a
portal or a layer appearing here that should not is a disclosure, not a cosmetic bug.
"""
import json

import pytest
from jose import jwt

from geodeploy.config import get_settings
from geodeploy.models import Portal, RasterLayer, SetupConfig, User, VectorLayer
from geodeploy.routers.public import index_enabled


def _as(user_id: int) -> dict:
    """Session headers for a seeded user — admin routes reject API tokens by design."""
    token = jwt.encode({"sub": str(user_id)}, get_settings().secret_key, algorithm="HS256")
    return {"Authorization": "Bearer " + token}


@pytest.fixture
async def seeded(db):
    """One of everything, public and not: what the index must include and what it must not."""
    db.add(User(id=1, email="k@example.org", name="Koffi", role="owner",
                hashed_password="x", is_admin=True))
    db.add_all([
        # Public portal — listed.
        Portal(id=1, user_id=1, title="Open portal", slug="open-portal", published=True,
               access_type="public", template_id="minimal", layer_configs=json.dumps(
                   [{"layer_id": 1, "layer_type": "vector"}]),
               layout_config=json.dumps({"archetype": "catalog"})),
        # Published but NOT public — must never appear.
        Portal(id=2, user_id=1, title="Members only", slug="members-only", published=True,
               access_type="organization", template_id="minimal", layer_configs="[]"),
        Portal(id=3, user_id=1, title="Password gated", slug="pw", published=True,
               access_type="password", template_id="minimal", layer_configs="[]"),
        Portal(id=4, user_id=1, title="Owner only", slug="owner-only", published=True,
               access_type="owner", template_id="minimal", layer_configs="[]"),
        # Public but UNPUBLISHED — a draft is not a portal anyone can open.
        Portal(id=5, user_id=1, title="Draft", slug="draft", published=False,
               access_type="public", template_id="minimal", layer_configs="[]"),
    ])
    db.add_all([
        VectorLayer(id=1, user_id=1, uid="aaaaaaaaaaaa", name="Roads", table_name="t1",
                    schema_name="gd", storage_backend="postgis", status="ready", is_public=True,
                    visibility="public", geometry_type="linestring", feature_count=12,
                    crs="EPSG:4326", bbox=json.dumps([11.0, 55.0, 24.0, 69.0]),
                    keywords="transport, national", license="CC-BY-4.0"),
        VectorLayer(id=2, user_id=1, uid="bbbbbbbbbbbb", name="Parcels", table_name="t2",
                    schema_name="gd", storage_backend="geoparquet", status="ready", is_public=True,
                    visibility="public", s3_key="vectors/1/x/parcels.parquet"),
        # Not public, and not ready: neither may be listed.
        VectorLayer(id=3, user_id=1, uid="cccccccccccc", name="Internal", table_name="t3",
                    schema_name="gd", storage_backend="postgis", status="ready", is_public=False,
                    visibility="organization"),
        VectorLayer(id=4, user_id=1, uid="dddddddddddd", name="Still ingesting", table_name="t4",
                    schema_name="gd", storage_backend="postgis", status="processing",
                    is_public=True, visibility="public"),
    ])
    db.add(RasterLayer(id=1, user_id=1, uid="eeeeeeeeeeee", name="Elevation",
                       s3_key="rasters/1/x/dem.tif", status="ready", is_public=True,
                       visibility="public", band_count=1, crs="EPSG:3006"))
    await db.commit()
    yield db


class TestExposure:
    async def test_only_published_public_portals_are_listed(self, client, seeded):
        body = (await client.get("/api/public")).json()
        assert [p["slug"] for p in body["portals"]] == ["open-portal"]

    async def test_only_public_ready_layers_are_listed(self, client, seeded):
        body = (await client.get("/api/public")).json()
        names = {l["name"] for group in body["layers"].values() for l in group}
        assert names == {"Roads", "Parcels", "Elevation"}
        assert "Internal" not in names          # organization-visibility layer
        assert "Still ingesting" not in names   # not ready

    async def test_no_credentials_are_needed(self, client, seeded):
        response = await client.get("/api/public")
        assert response.status_code == 200
        assert "authorization" not in {k.lower() for k in response.request.headers}


class TestGrouping:
    async def test_layers_are_grouped_by_storage_kind(self, client, seeded):
        body = (await client.get("/api/public")).json()
        assert [l["name"] for l in body["layers"]["postgis"]] == ["Roads"]
        assert [l["name"] for l in body["layers"]["geoparquet"]] == ["Parcels"]
        assert [l["name"] for l in body["layers"]["raster"]] == ["Elevation"]

    async def test_the_three_groups_always_exist_even_when_empty(self, client, db):
        """A client renders three sections; a missing key would make it special-case emptiness."""
        body = (await client.get("/api/public")).json()
        assert set(body["layers"]) == {"raster", "postgis", "geoparquet"}
        assert body["counts"] == {"portals": 0, "raster": 0, "postgis": 0, "geoparquet": 0}

    async def test_layer_entries_carry_what_a_browser_needs(self, client, seeded):
        body = (await client.get("/api/public")).json()
        roads = body["layers"]["postgis"][0]
        assert roads["id"] == "aaaaaaaaaaaa"          # the STABLE uid, never the row id
        assert roads["bbox"] == [11.0, 55.0, 24.0, 69.0]
        assert roads["keywords"] == ["transport", "national"]
        assert roads["license"] == "CC-BY-4.0"
        assert roads["links"], "share links should be reused, not rebuilt here"
        assert roads["download"].endswith("/api/data/vector/aaaaaaaaaaaa/export")


class TestPortalEntries:
    async def test_a_portal_is_addressed_by_slug_not_id(self, client, seeded):
        portal = (await client.get("/api/public")).json()["portals"][0]
        assert "id" not in portal
        assert portal["url"].endswith("/portals/open-portal/")

    async def test_the_style_url_is_offered_so_a_client_can_load_the_whole_portal(self, client,
                                                                                  seeded):
        """`style.json` in the published bundle is the machine-readable portal — sources, layers,
        folder tree, bounds. Without it a plugin can only open a portal in a browser."""
        portal = (await client.get("/api/public")).json()["portals"][0]
        assert portal["style_url"].endswith("/portals/open-portal/style.json")

    async def test_the_experience_and_layer_count_come_through(self, client, seeded):
        portal = (await client.get("/api/public")).json()["portals"][0]
        assert portal["experience"] == "catalog"
        assert portal["layer_count"] == 1

    async def test_portals_only_endpoint_matches(self, client, seeded):
        full = (await client.get("/api/public")).json()["portals"]
        just = (await client.get("/api/public/portals")).json()
        assert just == full


class TestUrls:
    async def test_urls_use_the_host_the_client_reached_not_the_container(self, client, seeded):
        """Every URL here is meant to be pasted elsewhere, so it must carry the public origin."""
        body = (await client.get("/api/public",
                                 headers={"host": "geo.example.org",
                                          "x-forwarded-proto": "https"})).json()
        assert body["geodeploy"]["url"] == "https://geo.example.org"
        assert body["portals"][0]["url"].startswith("https://geo.example.org/")
        assert body["catalogs"]["stac"] == "https://geo.example.org/api/stac"
        assert body["catalogs"]["ogc_features"] == "https://geo.example.org/api/ogc"


class TestTheToggle:
    async def test_it_is_on_by_default(self, client, db):
        db.add(SetupConfig(id=1, completed=True))
        await db.commit()
        assert (await client.get("/api/public")).status_code == 200

    async def test_off_means_404_not_an_empty_list(self, client, db, seeded):
        """A client must be able to tell "this instance publishes no index" from "nothing is
        published" — the first is a decision, the second is a state."""
        db.add(SetupConfig(id=1, completed=True, public_index_enabled=False))
        await db.commit()
        response = await client.get("/api/public")
        assert response.status_code == 404
        assert (await client.get("/api/public/portals")).status_code == 404

    async def test_turning_it_off_does_not_hide_the_portal_itself(self, client, db, seeded):
        """Discoverability, not access: the gate still says a public portal may be served."""
        db.add(SetupConfig(id=1, completed=True, public_index_enabled=False))
        await db.commit()
        gate = await client.get("/api/portals/open-portal/gate")
        assert gate.status_code == 200

    def test_only_an_explicit_false_switches_it_off(self):
        """An UPGRADED instance carries NULL here — the additive ALTER leaves the column nullable —
        and must stay listed. Nothing an operator already published may go dark on an update."""
        class Cfg:
            public_index_enabled = None

        class Older:               # a row from before the column existed at all
            pass

        assert index_enabled(None) is True          # no config row: fresh instance
        assert index_enabled(Cfg()) is True         # NULL from the migration
        assert index_enabled(Older()) is True       # attribute absent
        Cfg.public_index_enabled = True
        assert index_enabled(Cfg()) is True
        Cfg.public_index_enabled = False
        assert index_enabled(Cfg()) is False


class TestAdminToggleEndpoint:
    async def test_it_is_admin_only(self, client, seeded):
        assert (await client.get("/api/admin/public-index")).status_code in (401, 403)
        assert (await client.put("/api/admin/public-index",
                                 json={"enabled": False})).status_code in (401, 403)

    async def test_round_trip(self, client, db, seeded):
        headers = _as(1)
        assert (await client.get("/api/admin/public-index",
                                 headers=headers)).json() == {"enabled": True}

        await client.put("/api/admin/public-index", json={"enabled": False}, headers=headers)
        assert (await client.get("/api/admin/public-index",
                                 headers=headers)).json() == {"enabled": False}
        assert (await client.get("/api/public")).status_code == 404

        await client.put("/api/admin/public-index", json={"enabled": True}, headers=headers)
        assert (await client.get("/api/public")).status_code == 200

    async def test_the_change_is_audited(self, client, db, seeded):
        headers = _as(1)
        await client.put("/api/admin/public-index", json={"enabled": False}, headers=headers)
        rows = (await client.get("/api/audit?action=admin.public_index",
                                 headers=headers)).json()
        assert rows["total"] >= 1
        assert rows["items"][0]["detail"] == {"enabled": False}
