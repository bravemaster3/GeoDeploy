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
