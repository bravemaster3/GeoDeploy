"""A tiny raster must not be asked for at continent zoom.

`bounds` stops MapLibre requesting tiles that MISS a raster. It does nothing about a tile that hits
it and spans a continent — and that is the expensive one. A drone orthomosaic a few hundred metres
across was still requested at z3, one tile covering most of Europe, and TiTiler took long enough
that nginx answered **504**.

A 504 is far worse than the 404 we fixed earlier. The 404 was instant noise; this one makes MapLibre
sit waiting, so the portal's load handler never completes and the page hangs on its loading screen
until the 15-second backstop — the whole portal held up by one oversized request.
"""
from geodeploy.services.portal_generator import _min_zoom_for


def test_a_drone_orthomosaic_is_not_requested_at_continent_zoom():
    """~500 m across. The z3 request that produced the 504 must not be made."""
    mz = _min_zoom_for([19.720, 64.115, 19.725, 64.120])
    assert mz > 3, f"z3 would still be requested (minzoom={mz})"


def test_a_country_sized_raster_is_left_unrestricted():
    """Every zoom is legitimate for a large layer, so no minzoom is written at all (0 is falsy)."""
    assert _min_zoom_for([-10.0, 35.0, 30.0, 70.0]) == 0
    assert _min_zoom_for([-180.0, -85.0, 180.0, 85.0]) == 0


def test_it_scales_with_extent_not_by_steps():
    """Smaller layer → higher floor. A rule that did not vary with size would either strand small
    rasters or leave large ones paying for continent-sized reads."""
    tiny = _min_zoom_for([19.7200, 64.1150, 19.7205, 64.1155])   # ~50 m
    small = _min_zoom_for([19.72, 64.11, 19.77, 64.16])          # ~5 km
    region = _min_zoom_for([19.0, 64.0, 21.0, 65.0])             # ~200 km
    assert tiny > small > region


def test_degenerate_extents_do_not_explode():
    """A zero-width bbox is corrupt, not infinitely zoomed — it must not produce a huge minzoom or
    raise inside style generation, which runs at publish time."""
    assert _min_zoom_for([5.0, 5.0, 5.0, 5.0]) == 0
    assert _min_zoom_for([5.0, 5.0, 4.0, 4.0]) == 0


def test_the_floor_is_capped():
    """Even a pathologically small extent must stay a usable zoom, not something past MapLibre's
    range where the layer could never draw."""
    assert _min_zoom_for([19.72000, 64.11500, 19.72001, 64.11501]) <= 18
