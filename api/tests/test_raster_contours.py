"""Contour lines: the parameters, and the two traps that make them render as a flat rectangle.

TiTiler's `contours` algorithm does NOT draw lines on a blank page. It colours the data across
`minz`–`maxz` with a built-in terrain ramp and draws the contour lines on top of that. Its defaults
span **−12000 to 8000 m**, the range of the planet, because the algorithm is written for global
DEMs — so a survey raster covering 183–316 m falls inside a single band of that ramp.

Measured against the live TiTiler on this project's own `Degfert_DEM` (183–316 m):

    algorithm=contours                                          14751 bytes  (flat khaki, few lines)
    algorithm=contours&algorithm_params={"increment":10}         32341 bytes  (still flat, more lines)
    …with minz/maxz set to the layer's own stretch              208485 bytes  (coloured relief)

GeoDeploy already knows that range — it is `rescale` — so contours borrows it rather than asking for
the same two numbers again.

The second trap is typing: TiTiler declares `minz`/`maxz` as **int**, and rejects the whole request
with a 422 for a fractional one. Stretches are routinely fractional (`182.789993,315.959992` is a
real stored value here), so the borrowed range is floored and ceiled — widening the band rather than
clipping the extremes to a flat colour.
"""
import json

import pytest

from urllib.parse import quote

from geodeploy.services import titiler
from geodeploy.services.titiler import get_tile_url, tile_url_from_style


class _FakeSettings:
    storage_bucket = "geodeploy"


def _params(url: str) -> dict[str, list[str]]:
    from urllib.parse import parse_qs, urlsplit
    return parse_qs(urlsplit(url).query)


def _algo_params(url: str) -> dict:
    return json.loads(_params(url)["algorithm_params"][0])


def test_contours_borrows_the_stretch_as_its_colour_range():
    """The whole reason the algorithm's own defaults cannot be used."""
    url = get_tile_url("k.tif", algorithm="contours", rescale="182.789993,315.959992",
                       settings=_FakeSettings())
    p = _algo_params(url)
    assert (p["minz"], p["maxz"]) == (182, 316)


def test_the_borrowed_range_is_integral():
    """TiTiler types minz/maxz as int and 422s a float — verified against the live server:

        2 validation errors for Contours … input_value=182.789993 … type=int_from_float
    """
    p = _algo_params(get_tile_url("k.tif", algorithm="contours", rescale="0.4,9.6",
                                  settings=_FakeSettings()))
    assert isinstance(p["minz"], int) and isinstance(p["maxz"], int)
    # WIDER, not nearer: rounding 9.6 down to 10 would be fine, but rounding 0.4 up to 0 would
    # clip the lowest values into the ramp's first colour. Floor and ceil always contain the data.
    assert (p["minz"], p["maxz"]) == (0, 10)


def test_contours_is_not_also_rescaled():
    """`rescale` is applied AFTER the algorithm, and contours returns a finished RGB image — the
    same reasoning that already excludes hillshade."""
    p = _params(get_tile_url("k.tif", algorithm="contours", rescale="0,100",
                             settings=_FakeSettings()))
    assert "rescale" not in p


def test_an_explicit_range_beats_the_stretch():
    """For the case where the contour background should span something other than the data."""
    p = _algo_params(get_tile_url("k.tif", algorithm="contours", rescale="182,316",
                                  minz=0, maxz=1000, settings=_FakeSettings()))
    assert (p["minz"], p["maxz"]) == (0, 1000)


def test_increment_and_thickness_travel():
    p = _algo_params(get_tile_url("k.tif", algorithm="contours", increment=10, thickness=2,
                                  settings=_FakeSettings()))
    assert p["increment"] == 10 and p["thickness"] == 2


def test_every_contour_parameter_is_an_integer():
    """TiTiler USED to type `increment` as a float, and this test asserted exactly that. It does not
    any more — `GET /algorithms/contours` on the current `:latest` image declares increment,
    thickness, minz and maxz all as `integer` — so a fractional interval now 422s every tile and the
    layer does not draw at all. The old expectation was what let that reach an instance.

    TiTiler runs from `:latest` here, so this is a contract that can tighten without any change on
    our side. If it loosens again, the rounding is still correct; if it tightens further, this test
    is where it will show."""
    p = _algo_params(get_tile_url("k.tif", algorithm="contours", increment=12.5, thickness=2.0,
                                  settings=_FakeSettings()))
    assert p["increment"] == 13, "half-UP, so the JS twin in mapStyle.js agrees"
    assert isinstance(p["increment"], int)
    assert isinstance(p["thickness"], int) and p["thickness"] == 2


def test_defaults_are_titilers_own_when_there_is_nothing_to_derive_from():
    """A layer that only ticks the box still gets a working interval rather than nothing.

    With NO stretch there is nothing to derive an interval from, so TiTiler's own 35 stands. When
    there is one, the default follows the data instead — see `TestDefaultInterval`, and the raster
    whose whole range is 0.4 units wide that 35 rendered as a flat rectangle."""
    p = _algo_params(get_tile_url("k.tif", algorithm="contours", settings=_FakeSettings()))
    assert p["increment"] == 35 and p["thickness"] == 1
    # …and no range at all when there is no stretch to borrow, rather than an invented one.
    assert "minz" not in p


def test_nonsense_values_fall_back_instead_of_breaking_the_tile():
    """A style arrives from JSON, a dialog and a QGIS getter; one bad number must not 422 every
    tile the layer has.

    The fallback is now the interval DERIVED from the stretch rather than TiTiler's 35: a range of
    1-5 gets lines every 0.5, which is eight of them, where 35 would have drawn none at all. The
    range is scaled by the same factor that makes 0.5 expressible as an integer — 100 — so the lines
    and the relief behind them still describe the same numbers."""
    p = _algo_params(get_tile_url("k.tif", algorithm="contours", increment="", thickness="two",
                                  minz="x", maxz=None, rescale="1,5", settings=_FakeSettings()))
    assert p["increment"] == 50 and p["thickness"] == 1
    assert (p["minz"], p["maxz"]) == (100, 500)


def test_a_colormap_is_still_ignored_under_contours():
    """Contours colours the background itself; a named ramp beside it is not drawn."""
    p = _params(get_tile_url("k.tif", algorithm="contours", colormap="viridis",
                             settings=_FakeSettings()))
    assert "colormap_name" not in p


# ── the one unpacking point ──────────────────────────────────────────────────────────────────────

def test_tile_url_from_style_carries_every_style_key():
    """Seven call sites used to hand-list these arguments, and two of them had already fallen behind
    — `portals.py` dropped `color_classes` and `colormap_reverse`, so a classified raster added to a
    portal from the catalog lost exactly that. A forgotten key does not fail; it silently serves the
    layer in a style nobody chose."""
    style = {"algorithm": "contours", "increment": 10, "thickness": 2,
             "rescale": "182.789993,315.959992", "opacity": 0.5}
    url = tile_url_from_style("k.tif", style, settings=_FakeSettings())
    p = _algo_params(url)
    assert (p["increment"], p["thickness"], p["minz"], p["maxz"]) == (10.0, 2, 182, 316)
    # `opacity` is the map's business, not the tile server's, and must not be sent as a parameter.
    assert "opacity" not in _params(url)


def test_tile_url_from_style_matches_the_keyword_form():
    """The helper must be exactly the old call, not a re-interpretation of it."""
    style = {"colormap": "viridis", "colormap_reverse": True, "rescale": "0,1", "bidx": [2]}
    assert tile_url_from_style("k.tif", style, settings=_FakeSettings()) == get_tile_url(
        "k.tif", colormap="viridis", colormap_reverse=True, rescale="0,1", bidx=[2],
        settings=_FakeSettings())


def test_an_empty_or_missing_style_is_a_plain_tile_url():
    for style in ({}, None):
        assert tile_url_from_style("k.tif", style, settings=_FakeSettings()) == get_tile_url(
            "k.tif", settings=_FakeSettings())


# ── TiTiler's integer contract (2026-09-04) ──────────────────────────────────────────────────────
# Read from `GET /algorithms/contours` on the running `:latest` image:
#
#     increment  integer  0 – 999
#     thickness  integer  0 – 10
#     minz/maxz  integer  ±99999
#
# EVERY ONE IS AN INTEGER. `increment` used to be sent as a float, which TiTiler accepted while it
# was typed loosely; it now 422s the whole tile request for a fractional one — and a raster whose
# tiles all 422 does not draw AT ALL, so a working contour layer simply disappeared after an update.
# TiTiler runs from `:latest` here, so its contract can tighten without any change on our side.

class TestContourIntegerContract:
    def test_a_fractional_interval_is_rounded_rather_than_sent(self):
        """A 422 on every tile hides the layer; a slightly different spacing does not."""
        params = json.loads(titiler._contour_params(12.5, 1, 0, 100, None))
        assert params["increment"] == 13
        assert isinstance(params["increment"], int)

    def test_rounding_is_half_UP_so_the_two_renderers_agree(self):
        """Python rounds half to EVEN and JavaScript half UP, so `round()` here would put the
        editor preview and the published portal on different contour spacings. `int(x + 0.5)` is
        expressible identically in both — the same fix `ramp_colors` needed."""
        assert json.loads(titiler._contour_params(12.5, 1, 0, 100, None))["increment"] == 13
        assert json.loads(titiler._contour_params(1.5, 1, 0, 100, None))["increment"] == 2

    def test_every_value_sent_is_an_int(self):
        params = json.loads(titiler._contour_params(35, 2, 10.4, 99.6, None))
        assert all(isinstance(v, int) for v in params.values()), params

    @pytest.mark.parametrize("increment, expected", [(9999, 999), (0.4, 1), (1000, 999)])
    def test_the_interval_is_clamped_to_titilers_range(self, increment, expected):
        assert json.loads(titiler._contour_params(
            increment, 1, 0, 100, None))["increment"] == expected

    @pytest.mark.parametrize("thickness, expected", [(99, 10), (0.4, 1), (11, 10)])
    def test_the_thickness_is_clamped_too(self, thickness, expected):
        assert json.loads(titiler._contour_params(
            35, thickness, 0, 100, None))["thickness"] == expected

    def test_the_z_range_is_clamped_to_titilers_range(self):
        params = json.loads(titiler._contour_params(35, 1, -500000, 500000, None))
        assert params["minz"] == -99999 and params["maxz"] == 99999

    def test_a_fractional_stretch_still_widens_to_contain_the_data(self):
        """Floor and ceil, not round: the band must never clip the extremes to a flat colour."""
        params = json.loads(titiler._contour_params(35, 1, None, None, "182.789993,315.959992"))
        assert params["minz"] == 182 and params["maxz"] == 316


class TestSubUnitRasters:
    """A raster whose whole range is narrower than 1.

    TiTiler's contour interval is an `integer` with a minimum of 0, so **1 is the finest interval it
    can express** — and a vegetation index running 0.5563-0.9477 is narrower than that end to end.
    Every pixel fell in one band and the tile came back a single flat colour: not an error, not a
    missing tile, a dark rectangle. Reported as "the layer displays all dark, and I can't change the
    interval to an appropriate value", which was exactly true — no integer was appropriate.

    Measured against the running image on a 0.556-0.947 float raster: at `increment=1` the tile
    holds ONE distinct colour; with `expression=b1*1000` at `increment=50` it holds 77.
    """

    STRETCH = "0.5563,0.9477"

    def test_the_data_is_scaled_so_a_fine_interval_becomes_expressible(self):
        url = get_tile_url("k.tif", algorithm="contours", increment=0.05, rescale=self.STRETCH,
                           settings=_FakeSettings())
        assert "expression=b1*1000" in url
        assert _algo_params(url)["increment"] == 50

    def test_the_colour_range_is_scaled_by_the_same_factor(self):
        """Or the relief would be coloured over 0-1 while the lines were drawn across 556-947 —
        which is the flat rectangle again, with contours faintly on top of it."""
        p = _algo_params(get_tile_url("k.tif", algorithm="contours", increment=0.05,
                                      rescale=self.STRETCH, settings=_FakeSettings()))
        assert (p["minz"], p["maxz"]) == (556, 948)

    def test_the_thickness_is_not_scaled(self):
        """It is a width in PIXELS, not a value in the data — scaling it would draw a 1 px line
        1000 px wide, which is the whole tile."""
        p = _algo_params(get_tile_url("k.tif", algorithm="contours", increment=0.05, thickness=2,
                                      rescale=self.STRETCH, settings=_FakeSettings()))
        assert p["thickness"] == 2

    def test_a_dem_in_metres_is_untouched(self):
        """The URL for ordinary elevation data must be byte-identical to what it was — an interval
        of 1 or more is already expressible, so there is nothing to scale."""
        url = get_tile_url("k.tif", algorithm="contours", increment=25,
                           rescale="182.79,315.96", settings=_FakeSettings())
        assert "expression" not in url
        assert _algo_params(url)["increment"] == 25

    @pytest.mark.parametrize("increment,factor,scaled", [
        (0.5, 100, 50), (0.1, 100, 10), (0.05, 1000, 50), (0.01, 1000, 10), (0.002, 10000, 20)])
    def test_the_factor_gives_the_interval_two_significant_digits(self, increment, factor, scaled):
        """Enough that rounding the scaled interval to an integer costs nothing visible."""
        url = get_tile_url("k.tif", algorithm="contours", increment=increment, rescale="0,1",
                           settings=_FakeSettings())
        assert "expression=b1*{0}".format(factor) in url
        assert _algo_params(url)["increment"] == scaled

    def test_a_range_too_wide_to_scale_falls_back_rather_than_overflowing(self):
        """`minz`/`maxz` cap at +/-99999. A raster spanning -50000..50000 cannot be multiplied at
        all, so the interval clamps to 1 — the honest limit — instead of sending a value TiTiler
        would 422."""
        url = get_tile_url("k.tif", algorithm="contours", increment=0.05,
                           rescale="-50000,50000", settings=_FakeSettings())
        assert "expression" not in url
        p = _algo_params(url)
        assert p["increment"] == 1
        assert abs(p["minz"]) <= 99999 and abs(p["maxz"]) <= 99999

    def test_the_scaling_expression_names_the_selected_band(self):
        """`bN` names a band of the DATASET and `&bidx=` is IGNORED once an expression is present —
        measured on a two-band raster whose band 1 is flat and band 2 a ramp: `bidx=2` with
        `expression=b1*1000` returns one colour, with `b2*1000` it returns 77. A hard-coded `b1`
        would contour the wrong band and look like plausible data rather than a fault."""
        url = get_tile_url("k.tif", algorithm="contours", increment=0.05, bidx=[2],
                           rescale=self.STRETCH, settings=_FakeSettings())
        assert "expression=b2*1000" in url

    def test_hillshade_exaggeration_names_the_selected_band_too(self):
        """The same bug, already shipped: `expression=b1*{z}` was hard-coded, so exaggerating a
        multiband raster hillshaded band 1 whatever the author had picked."""
        url = get_tile_url("k.tif", algorithm="hillshade", zfactor=3, bidx=[2],
                           settings=_FakeSettings())
        assert "expression=b2*3" in url


class TestDefaultInterval:
    """What "just tick the box" draws.

    TiTiler's own default is 35 — a sensible contour interval for a global DEM in metres, and a
    catastrophic one for anything else. On the 0.556-0.947 raster it is far wider than the data, so
    ticking "Contour lines" produced the flat dark rectangle before the author had typed anything at
    all. Same lesson as `symbology.pillar_radius`: a default that does not depend on the data means
    ticking a box can show nothing.
    """

    def test_the_default_follows_the_data(self):
        p = _algo_params(get_tile_url("k.tif", algorithm="contours", rescale="0.5563,0.9477",
                                      settings=_FakeSettings()))
        # ~0.039 of range per line, snapped up to a tidy 0.05, then scaled x1000.
        assert p["increment"] == 50

    def test_it_lands_on_a_tidy_number(self):
        """Nobody labels a contour "every 3.7 units"."""
        from geodeploy.services.titiler import _default_increment
        assert _default_increment(182.79, 315.96) == 20
        assert _default_increment(0, 1) == 0.1
        assert _default_increment(0, 1000) == 100

    def test_roughly_ten_lines_across_the_data(self):
        """Two reads as a mistake and fifty as hatching."""
        from geodeploy.services.titiler import _default_increment
        for lo, hi in ((0, 1), (182.79, 315.96), (0.5563, 0.9477), (-20, 60)):
            lines = (hi - lo) / _default_increment(lo, hi)
            assert 3 <= lines <= 20, (lo, hi, lines)

    def test_titilers_own_default_survives_when_there_is_no_stretch(self):
        """Nothing to derive from, so nothing invented."""
        p = _algo_params(get_tile_url("k.tif", algorithm="contours", settings=_FakeSettings()))
        assert p["increment"] == 35
        assert "minz" not in p


class TestContourColours:
    """TiTiler's contour algorithm hard-codes `cmap.get("terrain")` and black lines, and returns a
    finished RGB image — so a downstream colormap is ignored and ticking "Contour lines" took the
    layer's colours away with no way to get them back. The picture is reproduced as band maths plus
    an explicit colormap whenever the style asks for anything the algorithm cannot express.
    """

    STRETCH = "0.5563,0.9477"

    def _url(self, **style):
        return get_tile_url("k.tif", algorithm="contours", increment=0.05,
                            rescale=self.STRETCH, settings=_FakeSettings(), **style)

    def test_the_defaults_still_use_titilers_own_algorithm(self):
        """The promise that keeps every published contour layer rendering exactly as it did."""
        url = self._url()
        assert "algorithm=contours" in url
        assert "colormap=" not in url

    def test_writing_the_defaults_out_is_the_same_as_leaving_them(self):
        assert self._url(contour_palette="terrain", contour_color="#000000") == self._url()

    def test_a_chosen_palette_replaces_the_algorithm_rather_than_layering_on_it(self):
        """Running both would contour an already-coloured RGB image."""
        url = self._url(contour_palette="viridis")
        assert "algorithm=contours" not in url
        assert "expression=" in url and "colormap=" in url

    def test_an_unknown_palette_falls_back_instead_of_reaching_the_url(self):
        """The value comes from a client, and it selects a table of colours here."""
        assert self._url(contour_palette="../../etc/passwd") == self._url()

    def test_a_line_colour_that_is_not_a_colour_falls_back_too(self):
        assert self._url(contour_color="javascript:alert(1)") == self._url()

    def test_lines_can_take_the_palette_instead_of_one_colour(self):
        """So a reader can tell which line is which height rather than counting from the edge. The
        band is SHIFTED past the relief's range — 1..N is the ground, N+1..2N the lines — because
        one number has to carry both "this is a line" and "it is this high"."""
        from geodeploy.services import titiler as t
        url = self._url(contour_palette="viridis", contour_line_palette=True)
        assert "algorithm=contours" not in url
        assert quote(str(t.CONTOUR_BANDS) + "+where", safe="") in url

    def test_palette_lines_force_our_own_drawing_even_on_the_default_palette(self):
        """`terrain` + black is normally the algorithm's job, but the algorithm cannot colour a
        line by its value at all."""
        url = self._url(contour_line_palette=True)
        assert "algorithm=contours" not in url

    def test_the_relief_can_be_switched_off(self):
        """Coloured lines over a coloured ground read as neither, so the ground goes transparent —
        not white, which would hide the basemap just as thoroughly."""
        url = self._url(contour_palette="viridis", contour_line_palette=True,
                        contour_relief=False)
        assert "algorithm=contours" not in url
        assert quote('[0,0,0,0]', safe="") in url

    def test_an_absent_relief_key_means_it_is_ON(self):
        """`tile_url_from_style` unpacks every key of a stored style, and an absent one arrives as
        None. A plain `bool(None)` would have switched the relief off for every layer that had
        never set it — the default silently inverted for all of them."""
        assert self._url(contour_relief=None) == self._url()
        assert "algorithm=contours" in tile_url_from_style(
            "k.tif", {"algorithm": "contours", "rescale": self.STRETCH, "increment": 0.05},
            settings=_FakeSettings())

    def test_every_contour_key_reaches_the_url_through_the_style(self):
        """`STYLE_KEYS` is what carries a raster property to all seven surfaces; a key missing from
        it silently serves the layer in a style nobody chose."""
        from geodeploy.services import titiler as t
        for key in ("contour_palette", "contour_color", "contour_line_palette", "contour_relief"):
            assert key in t.STYLE_KEYS, key
        url = tile_url_from_style("k.tif", {
            "algorithm": "contours", "rescale": self.STRETCH, "increment": 0.05,
            "contour_palette": "viridis", "contour_line_palette": True}, settings=_FakeSettings())
        assert "colormap=" in url and "algorithm=contours" not in url


# The published-style half of the contour story needs a layer and the generator. Kept beside the
# tests that use it rather than in a shared fixture: it exists to answer one question, which is
# whether a coloured contour layer still ANNOUNCES itself as contours.
from geodeploy.services import portal_generator as pg           # noqa: E402


class _RasterLayer:
    id = 7
    name = "dem"
    s3_key = "rasters/dem.tif"
    band_count = 1
    bbox = json.dumps([10.0, 50.0, 11.0, 51.0])
    default_style = "{}"
    uid = "abc"

class TestTheLayerStillSaysItIsContours:
    """A coloured contour layer carries no `algorithm=` at all, and everything downstream that asked
    the URL "what is this?" got the wrong answer.

    The published legend reads the algorithm to decide what kind of legend to draw. With none, it
    took the CLASSIFIED-raster branch, found the explicit colormap, and rendered it as a class list —
    sixty-odd near-black swatches labelled 0, 1, 2, 3…, which are the interval array's own INDICES.
    The style popover had the same problem in a quieter way: it offered "None" for a layer plainly
    drawing contours.

    So the fact has to be recorded where it is true rather than inferred from a URL that no longer
    spells it. `geodeploy:contour` is baked at publish for exactly that, and `portal.js`'s
    `effectiveAlgorithm` falls back to it.
    """

    def _meta(self, style):
        cfg = {"layer_id": 7, "layer_type": "raster", "opacity": 1.0, "visible": True,
               "style": style}
        out = pg.generate_style([cfg], [], [_RasterLayer()])
        layer = next(ml for ml in out["layers"]
                     if (ml.get("metadata") or {}).get("geodeploy:type") == "raster")
        return layer["metadata"]

    def test_a_coloured_contour_layer_is_still_marked_as_contours(self):
        meta = self._meta({"algorithm": "contours", "increment": 0.3,
                           "rescale": "0.5563,0.9477", "contour_palette": "viridis",
                           "contour_line_palette": True, "contour_relief": False})
        assert meta["geodeploy:contour"] is not None
        assert meta["geodeploy:contour"]["increment"] == 0.3

    def test_its_tile_url_really_does_drop_the_algorithm(self):
        """The premise of the bug, pinned — so if this ever stops being true, the reason for the
        metadata fallback is visible rather than mysterious."""
        url = get_tile_url("k.tif", algorithm="contours", increment=0.3, rescale="0.5563,0.9477",
                           contour_palette="viridis", settings=_FakeSettings())
        assert "algorithm=contours" not in url
        assert "colormap=" in url

    def test_the_colormap_it_sends_is_a_LIST_not_a_value_map(self):
        """Which is why rendering it as a class list produced indices. TiTiler takes both shapes;
        only `{value: colour}` is a classification."""
        p = _params(get_tile_url("k.tif", algorithm="contours", increment=0.3,
                                 rescale="0.5563,0.9477", contour_palette="viridis",
                                 settings=_FakeSettings()))
        assert isinstance(json.loads(p["colormap"][0]), list)   # `_params` gives lists

    def test_an_ordinary_contour_layer_is_marked_too(self):
        """The default palette still routes through TiTiler's algorithm, so the URL DOES say
        contours — but the metadata must be there either way, or the legend would depend on which
        colours the author happened to pick."""
        meta = self._meta({"algorithm": "contours", "increment": 10, "rescale": "0,100"})
        assert meta["geodeploy:contour"]["increment"] == 10

    def test_a_raster_that_is_not_contours_carries_none(self):
        assert self._meta({"colormap": "viridis"})["geodeploy:contour"] is None
        assert self._meta({"algorithm": "hillshade"})["geodeploy:contour"] is None
