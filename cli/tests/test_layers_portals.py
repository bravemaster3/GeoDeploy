"""Layer resolution and portal editing — the library behaviour the QGIS plugin will lean on."""
from __future__ import annotations

import pytest

from geodeploy.errors import NotFoundError, ValidationError
from geodeploy.portals import editable_config


class TestLayerResolution:
    """A layer can be named by id, uid or name. Ambiguity is an error, never a silent pick."""

    def test_by_integer_id(self, client):
        assert client.layers.resolve(2)["name"] == "field sites"

    def test_an_id_that_exists_in_BOTH_kinds_is_refused(self, client):
        """vector 1 and raster 1 are different layers — ids are per-kind sequences. Returning
        whichever the listing happened to put first is how you download a vector while asking for
        a raster, and nothing in the output would tell you."""
        with pytest.raises(ValidationError) as caught:
            client.layers.resolve("1")
        assert "vector-1" in str(caught.value) and "raster-1" in str(caught.value)

    def test_and_the_kind_resolves_it_either_way(self, client):
        assert client.layers.resolve("raster-1")["name"] == "dem"
        assert client.layers.resolve(1, kind="raster")["name"] == "dem"
        assert client.layers.resolve(1, kind="vector")["name"] == "roads"

    def test_a_uid_is_never_ambiguous(self, client):
        assert client.layers.resolve("cccccccccccc")["name"] == "dem"

    def test_by_stable_uid(self, client):
        # uid, not the integer id, is what public URLs use: an integer is unique only within one
        # layer kind and one database, so it renumbers on a restore or a move between instances.
        assert client.layers.resolve("aaaaaaaaaaaa")["name"] == "roads"

    def test_by_prefixed_reference(self, client):
        assert client.layers.resolve("raster-1")["name"] == "dem"
        assert client.layers.resolve("vector-1")["name"] == "roads"

    def test_by_exact_name(self, client):
        assert client.layers.resolve("roads")["id"] == 1

    def test_by_case_insensitive_name(self, client):
        assert client.layers.resolve("ROADS")["id"] == 1

    def test_by_unique_substring(self, client):
        assert client.layers.resolve("field")["id"] == 2

    def test_kind_disambiguates_a_shared_name(self, client, instance):
        instance.raster_layers.append(dict(instance.raster_layers[0], id=2, uid="d" * 12,
                                           name="roads"))
        with pytest.raises(ValidationError) as caught:
            client.layers.resolve("roads")
        assert "several layers" in str(caught.value)
        assert client.layers.resolve("roads", "raster")["layer_type"] == "raster"

    def test_unknown_reference(self, client):
        with pytest.raises(NotFoundError):
            client.layers.resolve("nothing-like-this")

    def test_integer_id_beats_a_name_that_looks_like_one(self, client, instance):
        instance.vector_layers.append(dict(instance.vector_layers[0], id=7, uid="e" * 12, name="1"))
        assert client.layers.resolve(1, "vector")["name"] == "roads"


class TestLayerListing:
    def test_both_kinds_are_tagged_with_their_type(self, client):
        rows = client.layers.list()
        assert {r["layer_type"] for r in rows} == {"vector", "raster"}

    def test_filters(self, client):
        # By name rather than by count: the fixture set grows, and a count assertion turns that
        # into a failure in a test about filtering.
        assert {r["name"] for r in client.layers.list("vector", status="ready")} == {
            "roads", "parcels"}
        assert [r["name"] for r in client.layers.list(query="field")] == ["field sites"]
        assert {r["name"] for r in client.layers.list(visibility="public")} == {"parcels", "dem"}

    def test_sharing_requires_something_to_change(self, client):
        with pytest.raises(ValidationError):
            client.vector.share(1)

    def test_sharing_sends_only_what_was_given(self, client, instance):
        client.vector.share(1, visibility="public", license="CC-BY-4.0")
        body = instance.requests_to("/sharing", "PUT")[0]["body"]
        assert b"license" in body and b"abstract" not in body


class TestPortalLayers:
    """Index 0 is the top of the list and draws on top — the convention the editor uses."""

    def test_add_prepends(self, client, instance):
        updated = client.portals.add_layer(3, 1, "raster", style={"colormap": "viridis"})
        configs = updated["layer_configs"]
        assert configs[0]["layer_id"] == 1 and configs[0]["layer_type"] == "raster"
        assert len(configs) == 2

    def test_add_at_the_bottom(self, client):
        updated = client.portals.add_layer(3, 1, "raster", bottom=True)
        assert updated["layer_configs"][-1]["layer_type"] == "raster"

    def test_adding_twice_is_refused_unless_asked_to_replace(self, client):
        with pytest.raises(ValidationError):
            client.portals.add_layer(3, 1, "vector")
        updated = client.portals.add_layer(3, 1, "vector", style={"color": "#fff"}, replace=True)
        assert len([c for c in updated["layer_configs"] if c["layer_id"] == 1]) == 1
        assert updated["layer_configs"][0]["style"]["color"] == "#fff"

    def test_remove(self, client):
        updated = client.portals.remove_layer(3, 1, "vector")
        assert updated["layer_configs"] == []

    def test_removing_something_absent_is_an_error_not_a_no_op(self, client):
        with pytest.raises(NotFoundError):
            client.portals.remove_layer(3, 99)

    def test_style_merges_by_default(self, client):
        updated = client.portals.set_layer_style(3, 1, {"radius": 6}, layer_type="vector")
        style = updated["layer_configs"][0]["style"]
        assert style == {"color": "#3b82f6", "radius": 6}

    def test_style_can_replace(self, client):
        updated = client.portals.set_layer_style(3, 1, {"radius": 6}, layer_type="vector",
                                                 merge=False)
        assert updated["layer_configs"][0]["style"] == {"radius": 6}

    def test_style_keeps_the_rest_of_the_entry(self, client):
        updated = client.portals.set_layer_style(3, 1, {"radius": 6}, visible=False)
        entry = updated["layer_configs"][0]
        assert entry["visible"] is False and entry["opacity"] == 1.0

    @pytest.mark.parametrize("position,expected_first", [
        ("bottom", 2), ("top", 1), ("down", 2), (1, 2), (0, 1),
    ])
    def test_move(self, client, instance, position, expected_first):
        instance.portals[0]["layer_configs"].append(
            {"layer_id": 2, "layer_type": "vector", "visible": True, "opacity": 1.0, "style": {}})
        updated = client.portals.move_layer(3, 1, position, "vector")
        assert updated["layer_configs"][0]["layer_id"] == expected_first


class TestPortalDocument:
    def test_get_by_slug_and_title(self, client):
        assert client.portals.get("field-sites-2026")["id"] == 3
        assert client.portals.get("Field sites 2026")["id"] == 3
        assert client.portals.get("field sites")["id"] == 3

    def test_unknown_portal(self, client):
        with pytest.raises(NotFoundError):
            client.portals.get("no-such-portal")

    def test_get_by_its_published_url(self, client, server):
        """So `portals publish <url>` works on the link you copied, not on a slug you retyped."""
        assert client.portals.get(server + "/portals/field-sites-2026/")["id"] == 3

    def test_a_url_from_another_instance_is_refused(self, client):
        with pytest.raises(ValidationError) as exc:
            client.portals.get("https://elsewhere.example.org/portals/field-sites-2026/")
        assert "elsewhere.example.org" in str(exc.value)

    def test_round_trip_drops_server_owned_fields(self):
        """`slug` is derived from the title and `published` is not settable; sending them back
        would be at best ignored and at worst a 422."""
        config = {"id": 3, "slug": "x", "published": True, "created_at": "…", "title": "Keep",
                  "layer_configs": [], "theme": {"mode": "dark"}}
        assert editable_config(config) == {"title": "Keep", "layer_configs": [],
                                           "theme": {"mode": "dark"}}

    def test_set_config_filters_before_sending(self, client, instance):
        client.portals.set_config(3, {"id": 999, "slug": "hack", "title": "New"})
        assert instance.last_put == {"title": "New"}

    def test_password_access_needs_a_password(self, client):
        with pytest.raises(ValidationError):
            client.portals.create("Locked", access_type="password")

    def test_unknown_access_tier_is_refused_locally(self, client):
        with pytest.raises(ValidationError):
            client.portals.create("X", access_type="secret")

    def test_unknown_experience_is_refused_locally(self, client):
        with pytest.raises(ValidationError):
            client.portals.create("X", archetype="dashboard")

    def test_create_carries_the_experience_into_layout_config(self, client, instance):
        portal = client.portals.create("Cat", archetype="catalog")
        assert portal["layout_config"]["archetype"] == "catalog"

    def test_publish_and_url(self, client):
        portal = client.portals.create("Fresh")
        assert portal["published"] is False
        published = client.portals.publish(portal["id"])
        assert published["published"] is True
        assert client.portals.url(published).endswith("/portals/fresh/")

    def test_update_with_nothing_is_refused(self, client):
        with pytest.raises(ValidationError):
            client.portals.update(3)


class TestSources:
    def test_wms_needs_a_layer_name(self, client):
        with pytest.raises(ValidationError) as caught:
            client.sources.create("Ortho", "wms", "https://example.org/wms")
        assert "layer-name" in str(caught.value)

    def test_xyz_does_not(self, client):
        source = client.sources.create("OSM", "xyz", "https://tile/{z}/{x}/{y}.png")
        assert source["id"] == 5

    def test_unknown_type(self, client):
        with pytest.raises(ValidationError):
            client.sources.create("X", "wcs", "https://example.org")

    def test_sources_have_no_public_tier(self, client):
        with pytest.raises(ValidationError):
            client.sources.share(5, "public")
