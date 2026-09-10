"""`GET /api/public/portals/{slug}` — a published portal's own styling, for anybody.

WHY THIS ENDPOINT EXISTS. A portal's `style.json` is the drawing instructions: MapLibre paint,
baked expressions, tile URLs. A client that has only that must run the translation BACKWARDS to
recover what the author chose, and the reverse is lossy by construction — a rule tree, a stacked
stroke, a per-class marker, a label's placement and its scale range have no single paint value to
be read back out of. The QGIS plugin did exactly that for anonymous visitors while signed-in ones
got the authored `layer_configs`, so there were two implementations of "what does this portal look
like" and only one of them ever got the new features. This is what lets there be one.

THE EXPOSURE RULE IS THE THING TO PIN, not the JSON's shape. `layer_configs` IS the map — the same
colours, widths and label settings `style.json` already publishes as paint — so serving them is not
a new disclosure. What would be is answering for a portal that is NOT public, and that is what most
of this file checks.
"""
import json

import pytest

from geodeploy.models import Portal, User, VectorLayer

STYLE = {
    "color": "#d40000", "line_width": 3,
    # The keys the reverse translation cannot recover, which are the point of the endpoint.
    "line_stack": [{"color": "#1f4fd8", "line_width": 3, "lineType": "dashed"}],
    "rules": [{"label": "Confident", "filter": ["==", ["get", "c"], 1],
               "style": {"color": "#111111", "lineType": "dotted"}}],
    "labels": {"enabled": True, "field": "name", "placement": "line",
               "line_position": "on", "label_per_part": True,
               "rules": [{"label": "Big", "filter": [">", ["get", "pop"], 10],
                          "labels": {"enabled": True, "field": "name", "size": 14.0}}]},
}


@pytest.fixture
async def seeded(db):
    db.add(User(id=1, email="k@example.org", name="Koffi", role="owner",
                hashed_password="x", is_admin=True))
    db.add_all([
        Portal(id=1, user_id=1, title="Open portal", slug="open-portal", published=True,
               access_type="public", template_id="minimal",
               layer_configs=json.dumps([{"layer_id": 1, "layer_type": "vector", "visible": True,
                                          "opacity": 0.8, "style": STYLE,
                                          "popup_fields": ["name"]}]),
               layer_groups=json.dumps([{"id": "g1", "name": "Roads", "children": []}]),
               basemap="osm",
               initial_view=json.dumps({"center": [11.0, 55.0], "zoom": 8}),
               layout_config=json.dumps({"archetype": "webmap"})),
        Portal(id=2, user_id=1, title="Members only", slug="members-only", published=True,
               access_type="organization", template_id="minimal", layer_configs="[]"),
        Portal(id=3, user_id=1, title="Password gated", slug="pw", published=True,
               access_type="password", template_id="minimal", layer_configs="[]"),
        Portal(id=4, user_id=1, title="Draft", slug="draft", published=False,
               access_type="public", template_id="minimal", layer_configs="[]"),
    ])
    db.add(VectorLayer(id=1, user_id=1, uid="aaaaaaaaaaaa", name="Roads", table_name="t1",
                       schema_name="gd", storage_backend="postgis", status="ready", is_public=True,
                       visibility="public", geometry_type="linestring"))
    await db.commit()
    from geodeploy.routers.data.vector import invalidate_public_layers
    invalidate_public_layers()
    yield db


class TestExposure:
    async def test_a_public_published_portal_answers(self, client, seeded):
        assert (await client.get("/api/public/portals/open-portal")).status_code == 200

    @pytest.mark.parametrize("slug", ["members-only", "pw", "draft", "nope"])
    async def test_nothing_else_does(self, client, seeded, slug):
        # The same filter the listing uses. A portal behind a link, a password or an organization
        # is not public, and a draft is not a portal anyone can open.
        assert (await client.get(f"/api/public/portals/{slug}")).status_code == 404

    async def test_no_credentials_are_needed(self, client, seeded):
        response = await client.get("/api/public/portals/open-portal")
        assert response.status_code == 200
        assert "authorization" not in {k.lower() for k in response.request.headers}

    async def test_the_internal_id_is_not_handed_out(self, client, seeded):
        # `_portal_out` deliberately answers with the slug: the integer id is an internal key that
        # renumbers on a restore, and publishing it invites clients to build URLs that 401.
        body = (await client.get("/api/public/portals/open-portal")).json()
        assert "id" not in body and body["slug"] == "open-portal"


class TestWhatItCarries:
    async def test_the_authored_style_arrives_whole(self, client, seeded):
        """THE POINT OF THE ENDPOINT: the keys a style.json cannot be read backwards into."""
        body = (await client.get("/api/public/portals/open-portal")).json()
        style = body["layer_configs"][0]["style"]
        assert style == STYLE, "the authored style must arrive exactly as it was written"
        assert style["line_stack"][0]["lineType"] == "dashed"
        assert style["rules"][0]["style"]["lineType"] == "dotted"
        assert style["labels"]["placement"] == "line"
        assert style["labels"]["line_position"] == "on"
        assert style["labels"]["label_per_part"] is True
        assert style["labels"]["rules"][0]["labels"]["size"] == 14.0

    async def test_visibility_and_opacity_come_too(self, client, seeded):
        cfg = (await client.get("/api/public/portals/open-portal")).json()["layer_configs"][0]
        assert cfg["visible"] is True and cfg["opacity"] == 0.8
        assert cfg["layer_id"] == 1 and cfg["layer_type"] == "vector"

    async def test_the_folder_tree_comes_too(self, client, seeded):
        # A portal's folders are structure, not decoration: without them a group opens flat.
        body = (await client.get("/api/public/portals/open-portal")).json()
        assert body["layer_groups"] == [{"id": "g1", "name": "Roads", "children": []}]

    async def test_and_where_the_portal_opens(self, client, seeded):
        body = (await client.get("/api/public/portals/open-portal")).json()
        assert body["initial_view"] == {"center": [11.0, 55.0], "zoom": 8}
        assert body["basemap"] == "osm"

    async def test_it_says_it_is_published(self, client, seeded):
        # The plugin's `enrich_from_published` adds the source each layer draws from ONLY for a
        # published portal — without this flag an anonymous reader would get the styling and no
        # way to fetch the data it styles.
        assert (await client.get("/api/public/portals/open-portal")).json()["published"] is True

    async def test_the_summary_fields_are_still_there(self, client, seeded):
        # Same shape as the listing, so a client can use either without special-casing.
        body = (await client.get("/api/public/portals/open-portal")).json()
        for key in ("slug", "title", "url", "style_url", "layer_count", "experience"):
            assert key in body, key
        assert body["layer_count"] == 1

    async def test_a_portal_with_no_layers_is_not_an_error(self, client, seeded, db):
        db.add(Portal(id=9, user_id=1, title="Empty", slug="empty", published=True,
                      access_type="public", template_id="minimal", layer_configs=None))
        await db.commit()
        body = (await client.get("/api/public/portals/empty")).json()
        assert body["layer_configs"] == [] and body["layer_groups"] is None


class TestTheIndexSwitch:
    async def test_it_follows_the_instance_index_setting(self, client, seeded, db):
        """An operator who turns the anonymous index off means it, and this is part of it."""
        from geodeploy.models import SetupConfig
        from geodeploy.routers.public import index_enabled

        db.add(SetupConfig(id=1, public_index_enabled=False))
        await db.commit()
        index_enabled.cache_clear() if hasattr(index_enabled, "cache_clear") else None
        response = await client.get("/api/public/portals/open-portal")
        assert response.status_code in (403, 404), response.status_code
