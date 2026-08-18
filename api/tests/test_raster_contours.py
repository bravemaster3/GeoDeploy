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


def test_thickness_is_an_integer_and_increment_is_not():
    """TiTiler's own types: `increment: float`, `thickness: int`. A float thickness 422s."""
    p = _algo_params(get_tile_url("k.tif", algorithm="contours", increment=12.5, thickness=2.0,
                                  settings=_FakeSettings()))
    assert p["increment"] == 12.5
    assert isinstance(p["thickness"], int) and p["thickness"] == 2


def test_defaults_are_titilers_own():
    """A layer that only ticks the box still gets a working interval rather than nothing."""
    p = _algo_params(get_tile_url("k.tif", algorithm="contours", settings=_FakeSettings()))
    assert p["increment"] == 35.0 and p["thickness"] == 1
    # …and no range at all when there is no stretch to borrow, rather than an invented one.
    assert "minz" not in p


def test_nonsense_values_fall_back_instead_of_breaking_the_tile():
    """A style arrives from JSON, a dialog and a QGIS getter; one bad number must not 422 every
    tile the layer has."""
    p = _algo_params(get_tile_url("k.tif", algorithm="contours", increment="", thickness="two",
                                  minz="x", maxz=None, rescale="1,5", settings=_FakeSettings()))
    assert p["increment"] == 35.0 and p["thickness"] == 1
    assert (p["minz"], p["maxz"]) == (1, 5)


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
