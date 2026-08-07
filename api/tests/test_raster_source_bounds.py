"""A raster source declares WHERE ITS DATA IS.

Without `bounds`, MapLibre asks for tiles across the whole viewport at every zoom and the tile
server answers 404 for each one that misses the raster. Zoomed out over a COG covering one country
that is most of the requests on the page — a console full of failures, and real traffic spent
proving that the Pacific is not in the file.

The reason this needs a range check rather than being applied blindly: raster bboxes are reprojected
to EPSG:4326 at ingest (`cog_converter._read_meta`), but that reprojection FALLS BACK to the source
CRS when it fails. Putting a projected bbox into `bounds` would not merely leave the 404s in place —
it would hide the layer entirely, which is far worse than the problem being fixed.
"""
import json

from geodeploy.services.portal_generator import _lonlat_bounds


def test_a_lonlat_bbox_becomes_bounds():
    assert _lonlat_bounds(json.dumps([11.2, 57.1, 12.9, 58.3])) == [11.2, 57.1, 12.9, 58.3]
    # Already-parsed lists are accepted too — callers hold the bbox both ways.
    assert _lonlat_bounds([1.0, 2.0, 3.0, 4.0]) == [1.0, 2.0, 3.0, 4.0]


def test_a_PROJECTED_bbox_is_dropped_rather_than_used():
    """The ingest fallback case. UTM metres in `bounds` would hide the layer completely, so anything
    outside lon/lat range yields None and the source carries no bounds — the previous behaviour."""
    assert _lonlat_bounds(json.dumps([319000, 6390000, 410000, 6480000])) is None


def test_nonsense_never_reaches_the_style():
    assert _lonlat_bounds(None) is None
    assert _lonlat_bounds("not json") is None
    assert _lonlat_bounds(json.dumps([1, 2, 3])) is None          # too few
    assert _lonlat_bounds(json.dumps(["a", "b", "c", "d"])) is None
    # Degenerate (zero-area) bounds would make MapLibre request nothing at all.
    assert _lonlat_bounds(json.dumps([5, 5, 5, 5])) is None
    # Inverted axes — south above north — is a corrupt bbox, not a wrap-around.
    assert _lonlat_bounds(json.dumps([10, 60, 12, 55])) is None
