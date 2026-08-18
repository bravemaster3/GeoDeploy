"""A polygon's outline WIDTH, which for a long time could not be drawn at all.

A MapLibre `fill` strokes its own boundary, and `fill-outline-color` is exactly that — a colour,
with no width. The edge is always one pixel. So an outline width had nowhere to go, and the honest
answer to "can my 3 px border travel from QGIS?" was no.

It can, but only by making the outline its own `line` layer beside the fill. Two rules follow, and
both are what these tests are for:

* the extra layer appears ONLY above the hairline, so every polygon already published renders
  byte-identically — nobody's map changes because a feature was added;
* when it does appear the fill must stop drawing its own edge, or the hairline sits underneath the
  wider line and shows as a hard inner rim on a translucent fill.
"""
from geodeploy.services import symbology


# ── the decision ─────────────────────────────────────────────────────────────────────────────────

def test_a_plain_polygon_needs_no_outline_layer():
    """The default IS what a fill already draws, so nothing is added for it."""
    assert symbology.needs_outline_layer({}) is False
    assert symbology.needs_outline_layer({"outline_width": 1}) is False


def test_a_wider_border_needs_one():
    assert symbology.needs_outline_layer({"outline_width": 3}) is True


def test_no_outline_means_no_outline_layer():
    """A width beside `outline_color: none` is a leftover, not a request to draw one."""
    assert symbology.needs_outline_layer({"outline_width": 5, "outline_color": "none"}) is False


def test_a_marker_ratio_left_on_a_polygon_draws_nothing_new():
    """`outline_width` is a RATIO of the radius on a point. A style copied from one — or written by
    an older UI — carries 0.28, and reading that as pixels would give a quarter-pixel border. It is
    below the hairline, so the polygon simply keeps the edge it already had."""
    assert symbology.needs_outline_layer({"outline_width": 0.28}) is False


def test_the_width_is_bounded_and_survives_nonsense():
    assert symbology.outline_width_px({"outline_width": "wide"}) == 1
    assert symbology.outline_width_px({"outline_width": None}) == 1
    assert symbology.outline_width_px({"outline_width": -4}) == 0
    assert symbology.outline_width_px({"outline_width": 5000}) == 40


# ── the layer it produces ────────────────────────────────────────────────────────────────────────

class _Layer:
    id = 7
    geometry_type = "MultiPolygon"
    schema_name = "gd"
    table_name = "parcels"
    storage_backend = "postgis"
    pmtiles_key = None


def _layers(style, opacity=1.0):
    from geodeploy.services import portal_generator
    cfg = {"layer_id": 7, "layer_type": "vector", "style": style, "opacity": opacity}
    return portal_generator._vector_layers("src", _Layer(), cfg)


def test_a_plain_polygon_is_still_exactly_one_layer():
    """The parity guarantee: adding this feature must not change a single existing portal."""
    out = _layers({"color": "#e5b636"})
    assert len(out) == 1 and out[0]["type"] == "fill"
    assert out[0]["paint"]["fill-outline-color"] == "#1d4ed8"
    assert "fill-antialias" not in out[0]["paint"]


def test_a_wide_border_becomes_a_line_layer_and_the_fill_stops_stroking():
    out = _layers({"color": "#e5b636", "outline_color": "#111111", "outline_width": 4})
    assert [l["type"] for l in out] == ["fill", "line"]
    fill, line = out
    # Both halves matter: the line draws the border, and the fill must stop drawing the hairline
    # underneath it or a translucent polygon shows a hard inner rim.
    assert fill["paint"]["fill-antialias"] is False
    assert "fill-outline-color" not in fill["paint"]
    assert line["paint"]["line-color"] == "#111111"
    assert line["paint"]["line-width"] == 4
    assert line["id"] == "vector-7-outline"
    assert line["source"] == "src" and line["source-layer"] == "gd.parcels"


def test_the_border_follows_the_layer_opacity_not_the_fill_opacity():
    """A 45% wash with a solid border is the ordinary way to draw a polygon; tying the border to
    `fill_opacity` would fade it with the wash."""
    out = _layers({"outline_width": 3, "fill_opacity": 0.2}, opacity=0.6)
    assert out[1]["paint"]["line-opacity"] == 0.6


def test_an_extruded_polygon_gets_no_outline_layer():
    """3D is a `fill-extrusion`, which has no fill edge to widen — and a stray line at ground level
    under a block of buildings is not an outline, it is a stripe."""
    out = _layers({"outline_width": 6, "extrusion": {"enabled": True, "field": "h"}})
    assert [l["type"] for l in out] == ["fill-extrusion"]
