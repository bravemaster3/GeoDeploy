"""Layout archetypes — a REGRESSION PIN, written before adding `catalog`.

The published portal, `templates/shared/portal.js::resolveLayout` and
`ui/src/views/PortalEditor.vue::resolveLayout` must all agree on the resolved manifest (the
three-surface parity rule in CLAUDE.md). Adding an archetype is exactly the change most likely to
disturb the two that already work, so these tests state the CURRENT webmap and storymap defaults
literally. If one of them fails, an existing portal's layout just changed — that is the point.

Deliberately literal rather than computed: a test that derives its expectation from the same table
it is checking would happily accept a wrong table.
"""
import pytest

from geodeploy.services.portal_generator import resolve_layout

WEBMAP = {
    "archetype": "webmap",
    "regions": {
        "layerList": {"side": "left", "mode": "docked", "collapsed": False,
                      "width": None, "x": None, "y": None},
        "controls": {"position": "top-right"},
        "header": {"style": "bar"},
    },
    "panels": {"layerCatalog": True, "legend": True, "basemap": True, "about": True,
               "story": False},
}

STORYMAP = {
    "archetype": "storymap",
    "regions": {
        "layerList": {"side": "left", "mode": "floating", "collapsed": True,
                      "width": None, "x": None, "y": None},
        "controls": {"position": "top-right"},
        "header": {"style": "minimal"},
    },
    "panels": {"layerCatalog": True, "legend": True, "basemap": True, "about": False,
               "story": True},
}


class TestExistingArchetypesUnchanged:
    def test_webmap_defaults(self):
        assert resolve_layout({"archetype": "webmap"}) == WEBMAP

    def test_storymap_defaults(self):
        assert resolve_layout({"archetype": "storymap"}) == STORYMAP

    def test_no_config_is_webmap(self):
        """Every portal created before layouts existed has layout_config = None and must keep
        rendering exactly as it did."""
        assert resolve_layout(None) == WEBMAP
        assert resolve_layout({}) == WEBMAP

    def test_unknown_archetype_falls_back_to_webmap(self):
        """Includes the case that mattered before `catalog` existed: a portal saved with an
        archetype the runtime does not know must degrade to a working map, not a blank page."""
        assert resolve_layout({"archetype": "nonsense"}) == WEBMAP

    @pytest.mark.parametrize("arch", ["webmap", "storymap"])
    def test_overrides_merge_without_touching_siblings(self, arch):
        """A per-portal override changes only what it names — the merge must stay a deep merge."""
        base = resolve_layout({"archetype": arch})
        out = resolve_layout({"archetype": arch, "regions": {"controls": {"position": "bottom-left"}}})
        assert out["regions"]["controls"]["position"] == "bottom-left"
        assert out["regions"]["layerList"] == base["regions"]["layerList"]
        assert out["panels"] == base["panels"]


class TestCatalogArchetype:
    def test_catalog_exists_and_is_map_secondary(self):
        """The catalog is a BROWSING surface: the dataset list is the page and the map is a panel.
        `layerCatalog` is off because the facet rail replaces the layer switcher."""
        out = resolve_layout({"archetype": "catalog"})
        assert out["archetype"] == "catalog"
        assert out["panels"]["catalog"] is True
        assert out["panels"]["layerCatalog"] is False

    def test_catalog_scope_defaults_to_this_portal(self):
        """Default must be the portal's OWN layers. Defaulting to instance-wide would silently
        widen what a published portal exposes."""
        assert resolve_layout({"archetype": "catalog"})["regions"]["catalog"]["scope"] == "portal"

    def test_catalog_scope_override(self):
        out = resolve_layout({"archetype": "catalog",
                              "regions": {"catalog": {"scope": "public"}}})
        assert out["regions"]["catalog"]["scope"] == "public"
        # merging a scope must not drop the rest of the region
        assert "mapSide" in out["regions"]["catalog"]

    def test_catalog_does_not_leak_into_other_archetypes(self):
        """The new panel key must not appear for webmap/storymap — portal.js branches on it."""
        assert "catalog" not in resolve_layout({"archetype": "webmap"})["panels"]
        assert "catalog" not in resolve_layout({"archetype": "storymap"})["panels"]
