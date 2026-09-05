"""A DEM can raise the map: 3D terrain for a raster layer.

"Rasters can also be 2.5D, no?" — yes, and this is what that means. A polygon layer extrudes; a DEM
becomes a heightfield the whole map is draped over. Both halves already existed and nothing joined
them: TiTiler serves `terrainrgb` (Mapbox Terrain-RGB) and MapLibre reads exactly that from a
`raster-dem` source.

THE THING TO GET RIGHT is that the DEM is serving two different purposes at once. It stays a
PICTURE — coloured, hillshaded, contoured, whatever its style says — and it becomes a HEIGHTFIELD,
in an encoding where R, G and B are the bytes of a number rather than a colour. So it needs a second
source with no styling at all: a colormap or a stretch applied to Terrain-RGB does not colour the
relief, it corrupts the heights.
"""
import json

import pytest

from geodeploy.services import portal_generator as pg
from geodeploy.services.titiler import (TERRAIN_DEFAULT_EXAGGERATION, terrain_of,
                                        terrain_tile_url, tile_url_from_style)


class _FakeSettings:
    storage_bucket = "gd"


class _Raster:
    def __init__(self, style=None, **kw):
        self.id = 7
        self.name = "dem"
        self.s3_key = "rasters/dem.tif"
        self.band_count = 1
        self.bbox = json.dumps([10.0, 50.0, 11.0, 51.0])
        self.default_style = json.dumps(style or {})
        self.uid = "abc"
        for k, v in kw.items():
            setattr(self, k, v)


class TestTheHeightfieldUrl:

    def test_it_asks_for_terrain_rgb_and_nothing_else(self):
        url = terrain_tile_url("rasters/dem.tif", settings=_FakeSettings())
        assert "algorithm=terrainrgb" in url
        assert "colormap" not in url and "rescale" not in url and "bidx" not in url

    def test_it_carries_no_styling_even_when_the_layer_has_some(self):
        """The decisive property. A stretch on Terrain-RGB does not lighten the relief — it
        rewrites the bytes the heights are encoded in, so the map is deformed by nonsense."""
        url = terrain_tile_url("rasters/dem.tif", settings=_FakeSettings())
        styled = tile_url_from_style("rasters/dem.tif",
                                     {"colormap": "viridis", "rescale": "0,3000"},
                                     settings=_FakeSettings())
        assert "colormap_name" in styled          # the picture is styled…
        assert "colormap_name" not in url         # …and the heightfield is not

    def test_it_points_at_the_same_object(self):
        assert "rasters/dem.tif" in terrain_tile_url("rasters/dem.tif", settings=_FakeSettings())


class TestReadingTheStyle:

    def test_absent_or_disabled_is_no_terrain(self):
        assert terrain_of({}) is None
        assert terrain_of({"terrain": {}}) is None
        assert terrain_of({"terrain": {"enabled": False, "exaggeration": 3}}) is None

    def test_enabled_gets_the_default_exaggeration(self):
        assert terrain_of({"terrain": {"enabled": True}}) == {
            "exaggeration": TERRAIN_DEFAULT_EXAGGERATION}

    @pytest.mark.parametrize("given,expected", [(3, 3.0), (0.5, 0.5), (99, 10.0), (0, 0.1),
                                                (-4, 0.1), ("2.5", 2.5), ("nonsense", 1.5)])
    def test_the_exaggeration_is_clamped_and_survives_a_string(self, given, expected):
        """It comes from a client. Past about 10 a DEM is spikes rather than relief, and 0 is flat
        — which is indistinguishable from the feature being broken."""
        assert terrain_of({"terrain": {"enabled": True, "exaggeration": given}}) == {
            "exaggeration": expected}

    @pytest.mark.parametrize("block", ["yes", 3, [1, 2], None])
    def test_a_block_that_is_not_a_block_is_ignored(self, block):
        assert terrain_of({"terrain": block}) is None


class TestThePublishedStyle:
    """`terrain` is a ROOT property of the MapLibre style spec, so a published portal raises its
    relief with no runtime code at all."""

    def _style(self, raster_style):
        cfg = {"layer_id": 7, "layer_type": "raster", "opacity": 1.0, "visible": True,
               "style": raster_style}
        return pg.generate_style([cfg], [], [_Raster()])

    def test_a_dem_source_is_added_beside_the_picture(self):
        out = self._style({"terrain": {"enabled": True}})
        dem = [k for k, v in out["sources"].items() if v.get("type") == "raster-dem"]
        assert len(dem) == 1, out["sources"].keys()
        assert out["sources"][dem[0]]["encoding"] == "mapbox"
        # …and the ordinary raster source is still there: the DEM does not replace the picture.
        assert any(v.get("type") == "raster" for v in out["sources"].values())

    def test_the_root_terrain_names_that_source(self):
        out = self._style({"terrain": {"enabled": True, "exaggeration": 2}})
        assert out["terrain"]["exaggeration"] == 2.0
        assert out["terrain"]["source"] in out["sources"]
        assert out["sources"][out["terrain"]["source"]]["type"] == "raster-dem"

    def test_no_terrain_key_at_all_without_one(self):
        """The spec has no null form, so `"terrain": null` would be a style error on every portal
        that has no terrain — which is nearly all of them."""
        out = self._style({"colormap": "viridis"})
        assert out.get("terrain") is None
        assert not any(v.get("type") == "raster-dem" for v in out["sources"].values())

    def test_the_dem_source_is_bounded_like_the_picture_is(self):
        """Without `bounds` MapLibre asks for tiles across the whole viewport at every zoom and the
        tile server 404s every one that misses the raster."""
        out = self._style({"terrain": {"enabled": True}})
        dem = next(v for v in out["sources"].values() if v.get("type") == "raster-dem")
        assert dem["bounds"] == [10.0, 50.0, 11.0, 51.0]

    def test_the_topmost_raster_wins(self):
        """MapLibre applies ONE terrain to the whole map, so two rasters asking must not fight over
        it. The one that wins is the TOP of the portal's layer list — the layer a reader would point
        at and call "the terrain". The generator's loop runs in reverse (index 0 is painted last),
        so this is also the case a `setdefault` would have got backwards, handing the map to the
        bottom layer."""
        cfgs = [{"layer_id": 7, "layer_type": "raster", "opacity": 1.0, "visible": True,
                 "style": {"terrain": {"enabled": True, "exaggeration": 2}}},
                {"layer_id": 8, "layer_type": "raster", "opacity": 1.0, "visible": True,
                 "style": {"terrain": {"enabled": True, "exaggeration": 9}}}]
        second = _Raster()
        second.id = 8
        second.s3_key = "rasters/other.tif"
        out = pg.generate_style(cfgs, [], [_Raster(), second])
        assert out["terrain"]["exaggeration"] == 2.0      # layer_configs[0], the top of the list
