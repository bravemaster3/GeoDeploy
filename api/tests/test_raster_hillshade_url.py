"""Hillshade must not be re-stretched by the layer's own data range.

TiTiler applies `rescale` AFTER the algorithm, and a hillshade is already a finished 0-255 relief
image. Stretching that with the SOURCE data's range saturates every pixel to one value, so the tile
arrives as a flat block of colour — which reads as "hillshade is not rendering at all".

Measured against the live TiTiler on a vegetation index whose range is 0.5563-0.9477:

    algorithm=hillshade                          15505 bytes   (real relief)
    algorithm=hillshade&rescale=0.5563,0.9477      623 bytes   (uniform)

Why it looked like a portal-editor bug: `portal.js::applyRaster` rebuilds the tile URL from scratch
and drops the layer's baked rescale, so ticking Hillshade in the published legend worked. The editor
sidebar bakes the style server-side through this function, which kept the rescale. Same option, two
paths, one of them accidentally right.
"""
from geodeploy.services.titiler import get_tile_url


def _params(url: str) -> dict[str, list[str]]:
    from urllib.parse import parse_qs, urlsplit
    return parse_qs(urlsplit(url).query)


def test_hillshade_drops_the_rescale():
    url = get_tile_url("k.tif", rescale="0.5563,0.9477", algorithm="hillshade",
                       settings=_FakeSettings())
    p = _params(url)
    assert p["algorithm"] == ["hillshade"]
    assert "rescale" not in p, f"rescale flattens the hillshade: {url}"


def test_rescale_survives_without_an_algorithm():
    """The stretch is the whole point for non-8-bit imagery — it must not be lost generally."""
    p = _params(get_tile_url("k.tif", rescale="0,4095", settings=_FakeSettings()))
    assert p["rescale"] == ["0,4095"]


def test_rescale_survives_for_other_algorithms():
    """Scoped to hillshade deliberately. An index-style algorithm outputs a data range of its own
    (e.g. -1..1) and still wants stretching, so this must not become 'any algorithm'."""
    p = _params(get_tile_url("k.tif", rescale="-1,1", algorithm="normalizedIndex",
                             settings=_FakeSettings()))
    assert p["rescale"] == ["-1,1"]


def test_zfactor_still_rides_along():
    """The exaggeration is applied BEFORE the algorithm (b1*z), so it is unaffected by the above."""
    p = _params(get_tile_url("k.tif", rescale="0.5,0.9", algorithm="hillshade", zfactor=2.5,
                             settings=_FakeSettings()))
    assert p["expression"] == ["b1*2.5"]
    assert "rescale" not in p


def test_colormap_is_still_dropped_under_an_algorithm():
    p = _params(get_tile_url("k.tif", colormap="viridis", algorithm="hillshade",
                             settings=_FakeSettings()))
    assert "colormap_name" not in p


class _FakeSettings:
    storage_bucket = "geodeploy"


def test_a_4_band_raster_gets_an_explicit_band_default():
    """A PNG holds four channels and TiTiler appends the mask as alpha, so reading all four bands of
    a multispectral raster asks the driver for FIVE and every tile 500s:

        PNG driver doesn't support 5 bands. Must be 1 (grey), 2 (grey+alpha), 3 (rgb) or 4 (rgba)

    This hit a drone Sequoia (GRE/RED/REG/NIR) on every surface at once — the portal, the XYZ share
    link, the STAC tiles asset — because they all build the URL here.
    """
    p = _params(get_tile_url("k.tif", band_count=4, settings=_FakeSettings()))
    assert p["bidx"] == ["1", "2", "3"]


def test_an_explicit_band_choice_always_wins():
    p = _params(get_tile_url("k.tif", bidx=[4], band_count=4, settings=_FakeSettings()))
    assert p["bidx"] == ["4"]


def test_rgb_and_single_band_rasters_are_left_alone():
    """Only >3 bands is ambiguous. 1 and 3 already encode as PNG, so adding bidx would be noise —
    and forcing [1,2,3] on a single-band raster would ask for bands that do not exist."""
    for n in (None, 1, 3):
        assert "bidx" not in _params(get_tile_url("k.tif", band_count=n, settings=_FakeSettings()))
