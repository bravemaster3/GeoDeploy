""""No basemap" has to be honoured by the BUNDLE, not only by the runtime switcher.

REPORTED AS: "when you choose none as the basemap in the portal editor, it doesn't actually change
to None. it does nothing."

WHY IT LOOKED LIKE AN EDITOR BUG AND WAS NOT. The editor's preview is a REAL published bundle in an
iframe — that is what makes it faithful — so what the preview shows is decided at publish, here.
`__none__` is deliberately not in `BASEMAP_CATALOG` (there is no service behind it; it is the
absence of one), so `_BASEMAP_BY_ID.get(basemap)` returned None, the repoint block did not run, and
the template's own basemap stayed baked in. The runtime switcher could still turn it off
afterwards, which is exactly why it seemed that only the published portal honoured the setting: the
editor was honouring it precisely as much as the portal was, which was not at all.

So the basemap is taken OUT of the style at publish rather than hidden afterwards — no flash of a
map nobody asked for, and a portal that opens on the plain ground it was authored with.

Driven through `apply_basemap_choice` rather than `build_portal_bundle`, which reads the
container's `/templates` mount. The decision is the part with the logic; extracting it is what
makes it testable at all.
"""
import copy

import pytest

from geodeploy.services import portal_generator as pg

TEMPLATE_STYLE = {
    "sprite": "https://tiles.example.org/sprite",
    "sources": {"basemap": {"type": "raster", "tiles": ["https://t.example/{z}/{x}/{y}.png"],
                            "tileSize": 256, "attribution": "© Somebody"}},
    "layers": [{"id": "basemap", "type": "raster", "source": "basemap"}],
}

USER_DATA = {
    "sources": {"vector_4": {"type": "vector", "tiles": ["/tiles/gd.roads/{z}/{x}/{y}"]}},
    "layers": [{"id": "vector-4", "type": "line", "source": "vector_4",
                "source-layer": "gd.roads", "metadata": {"geodeploy:layer_id": 4}}],
}


def build(basemap):
    """The style a publish would write, for one basemap choice."""
    template = copy.deepcopy(TEMPLATE_STYLE)
    user = copy.deepcopy(USER_DATA)
    style = {
        "version": 8,
        "sprite": template.get("sprite", ""),
        "sources": {**template["sources"], **user["sources"]},
        "layers": template["layers"] + user["layers"],
        "geodeploy": {},
    }
    pg.apply_basemap_choice(style, template, user, basemap)
    return style


class TestTheSentinelIsShared:
    def test_the_id_matches_the_editor_and_the_runtime(self):
        # Three surfaces must agree on this string or the choice is dropped between them:
        # `ui/src/lib/basemaps.js`, `templates/shared/portal.js`, and here.
        assert pg.NO_BASEMAP_ID == "__none__"

    def test_it_is_not_a_catalog_entry(self):
        # …and must not become one. The catalog is a list of SERVICES, and everything that consumes
        # it — the switcher, the editor's picker, `_BASEMAP_BY_ID` — expects tiles behind each id.
        assert pg.NO_BASEMAP_ID not in {b["id"] for b in pg.BASEMAP_CATALOG}
        assert pg._BASEMAP_BY_ID.get(pg.NO_BASEMAP_ID) is None

    def test_the_ground_is_a_real_layer(self):
        # With no painted ground, what shows through the map is the canvas — black. That is why
        # this is a `background` layer rather than simply nothing.
        assert pg.GROUND_LAYER["type"] == "background"
        assert pg.GROUND_LAYER["paint"]["background-color"] == "#ffffff"


class TestChoosingNone:
    def test_the_templates_basemap_is_removed(self):
        style = build("__none__")
        assert "basemap" not in style["sources"], "the template's tiles are still in the bundle"
        assert not [lyr for lyr in style["layers"] if lyr.get("type") == "raster"]

    def test_a_white_ground_is_painted_instead(self):
        style = build("__none__")
        ground = style["layers"][0]
        assert ground["id"] == "gd-ground" and ground["type"] == "background"
        assert ground["paint"]["background-color"] == "#ffffff"

    def test_the_runtime_is_told_which_option_is_active(self):
        # Or the switcher opens with the wrong row ticked, telling the reader the portal is showing
        # a basemap that is not there.
        assert build("__none__")["geodeploy"]["defaultBasemap"] == "__none__"

    def test_and_told_not_to_swap_anything_in_on_load(self):
        # `baseRepointed` is what stops `setupBasemaps` calling `selectBasemap` on load. The bundle
        # already IS the chosen state; driving the switcher again would be a visible flash.
        assert build("__none__")["geodeploy"]["baseRepointed"] is True

    def test_the_attribution_goes_with_the_basemap(self):
        # A credit for a service the portal no longer fetches is a false statement about the map.
        import json
        assert "Somebody" not in json.dumps(build("__none__")["sources"])

    def test_a_vector_templates_sprite_goes_too(self):
        # It belongs to layers that are no longer in the style.
        assert build("__none__")["sprite"] == ""


class TestTheDataSurvives:
    """The one thing worse than a basemap that will not go is a portal whose data went with it."""

    def test_the_portals_own_layers_are_still_drawn(self):
        style = build("__none__")
        drawn = [lyr for lyr in style["layers"] if lyr["id"] != "gd-ground"]
        assert [lyr["id"] for lyr in drawn] == ["vector-4"]

    def test_its_source_survives_the_filtering(self):
        assert "vector_4" in build("__none__")["sources"]

    def test_the_ground_is_under_the_data_not_over_it(self):
        assert build("__none__")["layers"][0]["id"] == "gd-ground"

    def test_a_user_source_named_like_the_templates_is_kept(self):
        """The filter removes the TEMPLATE's sources. A data source that happens to be called
        `basemap` is the map itself, and dropping it would empty the portal."""
        template = copy.deepcopy(TEMPLATE_STYLE)
        user = {"sources": {"basemap": {"type": "vector", "tiles": ["/tiles/mine/{z}/{x}/{y}"]}},
                "layers": [{"id": "mine", "type": "line", "source": "basemap"}]}
        style = {"version": 8, "sprite": "", "sources": {**template["sources"], **user["sources"]},
                 "layers": template["layers"] + user["layers"], "geodeploy": {}}
        pg.apply_basemap_choice(style, template, user, "__none__")
        assert style["sources"]["basemap"]["type"] == "vector"
        assert [lyr["id"] for lyr in style["layers"]] == ["gd-ground", "mine"]


class TestEveryOtherChoiceIsUnchanged:
    def test_a_real_basemap_is_repointed(self):
        other = pg.BASEMAP_CATALOG[1]
        style = build(other["id"])
        assert style["sources"]["basemap"]["tiles"] == other["tiles"]
        assert style["sources"]["basemap"]["attribution"] == other["attribution"]
        assert style["geodeploy"]["defaultBasemap"] == other["id"]
        assert style["geodeploy"]["baseRepointed"] is True

    def test_choosing_nothing_leaves_the_template_alone(self):
        # A portal published before basemap selection existed must look exactly as it did.
        style = build(None)
        assert style["sources"]["basemap"]["tiles"] == TEMPLATE_STYLE["sources"]["basemap"]["tiles"]
        assert "defaultBasemap" not in style["geodeploy"]
        assert "baseRepointed" not in style["geodeploy"]

    @pytest.mark.parametrize("unknown", ["not-a-basemap", "", "none", "None", "__None__"])
    def test_an_unknown_id_is_not_mistaken_for_none(self, unknown):
        # A typo, a case difference, or a basemap removed from the catalog must leave the
        # template's own basemap rather than silently publishing a blank map.
        style = build(unknown)
        assert style["sources"]["basemap"]["tiles"] == TEMPLATE_STYLE["sources"]["basemap"]["tiles"]
        assert style["layers"][0]["type"] == "raster"

    def test_the_data_is_untouched_by_a_repoint(self):
        style = build(pg.BASEMAP_CATALOG[1]["id"])
        assert "vector_4" in style["sources"]
        assert any(lyr["id"] == "vector-4" for lyr in style["layers"])
