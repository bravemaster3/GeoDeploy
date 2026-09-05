"""Data-driven symbology: classification and the MapLibre expressions it produces.

These are the CONTRACT for four renderers — `portal_generator` (published style), `portal.js` (live
runtime), `PortalEditor.vue` (preview) and `LayerPanel.vue` (swatch). Three of them are JavaScript,
so the expressions are pinned here as literals rather than derived from the code that builds them: a
test that recomputes its expectation from the implementation would happily accept a wrong one, and
the JS twin (`ui/src/lib/symbology.js`) is checked against these same literals by eye.

The failure this guards against is specific and quiet: an editor preview and a published portal
disagreeing about which class a feature falls into. Nobody sees that until the map is public.
"""
import pytest

from geodeploy.services import symbology as sym

#: A real 1x1 transparent PNG, as a data URI. The legend only checks the SHAPE of the string, but a
#: valid image keeps the fixture honest if anything downstream ever decodes it.
PNG = ("data:image/png;base64,"
       "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")


# ── Classification ───────────────────────────────────────────────────────────────────────────────

def test_quantile_puts_equal_counts_in_each_class():
    breaks = sym.classify(list(range(1, 101)), "quantile", 4)
    assert len(breaks) == 3
    assert breaks == [26.0, 51.0, 76.0]


def test_equal_interval_splits_the_RANGE_not_the_count():
    breaks = sym.classify([0, 1, 2, 3, 100], "equal", 2)
    assert breaks == [50.0]


def test_a_skewed_distribution_is_why_quantile_is_the_default():
    """Equal-interval on skewed data leaves almost everything in the first class — a map of one
    colour. Quantile spreads the same data across all of them. This is not a preference: it is the
    reason the default is what it is, and a change to that default should argue with this test.

    The data below is the shape most spatial attributes have: many small values, a few large ones.
    """
    skewed = list(range(1, 91)) + [5000, 6000, 7000, 8000, 9000, 10000]

    equal = sym.classify(skewed, "equal", 4)
    quant = sym.classify(skewed, "quantile", 4)

    def counts(breaks):
        edges = [float("-inf"), *breaks, float("inf")]
        return [sum(1 for v in skewed if edges[i] <= v < edges[i + 1]) for i in range(len(edges) - 1)]

    # Equal interval: one class holds nearly everything — a map of essentially one colour.
    assert max(counts(equal)) > 0.8 * len(skewed)
    # Quantile: every class is populated, and none dominates.
    assert all(c > 0 for c in counts(quant))
    assert max(counts(quant)) < 0.5 * len(skewed)


def test_a_column_that_is_mostly_one_value_yields_FEWER_classes_not_empty_ones():
    """The real case behind this: a column that is 90% one value — a default, a flag, an unfilled
    field. Every quantile break then lands ON that value, i.e. on the minimum, and a break equal to
    the minimum leaves the lowest class EMPTY: a legend entry for a colour that appears nowhere,
    which reads to the viewer as missing data.

    Honest degradation — fewer classes — beats a legend the map does not match."""
    breaks = sym.classify([1] * 90 + [1000] * 10, "quantile", 4)
    assert all(b > 1 for b in breaks), "a break at the minimum leaves an empty first class"

    classes = sym.build_classes([1] * 90 + [1000] * 10, "quantile", 4, "viridis")
    assert len(classes) < 4


def test_ties_collapse_classes_rather_than_producing_an_invalid_step():
    """MapLibre requires strictly ascending `step` stops. A column with many repeated values makes
    two quantile breaks land on the same number; emitting both produces a style MapLibre REJECTS —
    i.e. a blank layer. Fewer classes than requested is a correct map."""
    breaks = sym.classify([5] * 50 + [9] * 50, "quantile", 5)
    assert breaks == sorted(set(breaks))
    assert len(breaks) < 4


def test_too_few_distinct_values_for_the_requested_classes():
    assert sym.classify([1, 1, 1], "quantile", 5) == []
    assert sym.classify([], "quantile", 5) == []
    assert sym.classify([7], "equal", 3) == []


def test_jenks_finds_the_gap_in_clustered_data():
    """Natural breaks should land IN the gap, not inside a cluster."""
    breaks = sym.classify([1, 2, 3, 4, 100, 101, 102, 103], "jenks", 2)
    assert len(breaks) == 1
    assert 4 < breaks[0] < 100


# ── Classes and legends ──────────────────────────────────────────────────────────────────────────

def test_the_outer_edges_of_the_class_list_are_OPEN():
    """The lowest class has no minimum and the highest no maximum, so a feature outside the sampled
    range still draws. A layer gains rows after it is styled; closed outer edges would make the
    newest data invisible while the map still looked fine — the worst failure mode available."""
    classes = sym.build_classes(list(range(1, 101)), "quantile", 4, "viridis")
    assert classes[0]["min"] is None
    assert classes[-1]["max"] is None
    assert all(c["color"] for c in classes)


def test_classes_are_contiguous():
    """Each class starts where the previous one ended — no gap a value could fall through."""
    classes = sym.build_classes(list(range(1, 101)), "quantile", 5, "viridis")
    for a, b in zip(classes, classes[1:]):
        assert a["max"] == b["min"]


def test_a_legend_describes_the_open_edges_honestly():
    classes = sym.build_classes(list(range(1, 101)), "quantile", 3, "viridis")
    labels = [e["label"] for e in sym.legend_entries({"color_mode": "graduated", "classes": classes})]
    assert labels[0].startswith("<")
    assert labels[-1].startswith("≥")


def test_a_categorized_legend_ends_with_Other():
    """The `match` expression has a fallback colour, so the legend must show it — otherwise every
    value outside the listed categories is drawn in a colour the legend does not explain."""
    style = {"color_mode": "categorized",
             "categories": [{"value": "a", "color": "#111111"}, {"value": "b", "color": "#222222"}]}
    labels = [e["label"] for e in sym.legend_entries(style)]
    assert labels == ["a", "b", "Other"]


def test_a_single_symbol_layer_has_no_legend():
    assert sym.legend_entries({"color_mode": "single", "color": "#ff0000"}) == []


def test_categories_use_a_QUALITATIVE_palette():
    """A sequential ramp on unordered values implies a ranking that is not in the data — the most
    common misleading map there is. Distinct hues, not shades of one."""
    cats = sym.build_categories(["oak", "pine", "birch"])
    colors = [c["color"] for c in cats]
    assert len(set(colors)) == 3
    assert colors[0] in sym.CATEGORY_COLORS


# ── Expressions ──────────────────────────────────────────────────────────────────────────────────

def test_single_mode_is_a_plain_string():
    """The existing single-symbol path must not become an expression: portals styled before this
    feature existed have to keep rendering byte-identically."""
    assert sym.color_expression({"color": "#ff0000"}) == "#ff0000"
    assert sym.color_expression({}) == sym.DEFAULT_COLOR


def test_graduated_builds_a_step_expression():
    style = {"color": "#000000", "color_mode": "graduated", "color_field": "pop",
             "classes": [{"min": None, "max": 10, "color": "#aaa"},
                         {"min": 10, "max": 20, "color": "#bbb"},
                         {"min": 20, "max": None, "color": "#ccc"}]}
    assert sym.color_expression(style) == [
        "step", ["to-number", ["get", "pop"]], "#aaa", 10, "#bbb", 20, "#ccc"]


def test_categorized_builds_a_match_expression_with_a_fallback():
    style = {"color": "#000000", "color_mode": "categorized", "color_field": "kind",
             "categories": [{"value": "oak", "color": "#0a0"}, {"value": "pine", "color": "#00a"}],
             "other_color": "#999"}
    assert sym.color_expression(style) == [
        "match", ["to-string", ["get", "kind"]], "oak", "#0a0", "pine", "#00a", "#999"]


def test_numeric_categories_are_matched_as_strings():
    """The input is coerced with `to-string`, so a numeric label must be a string too — otherwise
    the category never equals its own value and every feature falls to the fallback colour."""
    style = {"color_mode": "categorized", "color_field": "zone",
             "categories": [{"value": 3, "color": "#0a0"}]}
    expr = sym.color_expression(style)
    assert expr[2] == "3" and isinstance(expr[2], str)


@pytest.mark.parametrize("style", [
    {"color_mode": "graduated", "color_field": "pop"},                       # no classes yet
    {"color_mode": "graduated", "classes": [{"min": None, "color": "#a"}]},  # no field yet
    {"color_mode": "categorized", "color_field": "k", "categories": []},
    {"color_mode": "graduated", "color_field": "pop",
     "classes": [{"min": None, "max": None, "color": "#aaa"}]},              # one class = no stop
])
def test_an_incomplete_configuration_falls_back_to_the_plain_colour(style):
    """Styling is edited LIVE, so every intermediate state is something someone is looking at. A
    half-configured mode must render the layer, never blank it."""
    assert sym.color_expression({**style, "color": "#123456"}) == "#123456"


def test_proportional_size_interpolates():
    style = {"size_mode": "proportional", "size_field": "area", "size_stops": [[0, 2], [100, 20]]}
    assert sym.size_expression(style, 5) == [
        "interpolate", ["linear"], ["to-number", ["get", "area"]], 0, 2, 100, 20]


def test_size_stops_are_sorted_and_deduped():
    """MapLibre requires strictly ascending stop inputs; a user dragging stops around produces
    neither order nor uniqueness."""
    style = {"size_mode": "proportional", "size_field": "a",
             "size_stops": [[100, 20], [0, 2], [100, 9]]}
    expr = sym.size_expression(style, 5)
    assert expr[3:] == [0, 2, 100, 20]


def test_fixed_size_stays_a_number():
    assert sym.size_expression({}, 7) == 7
    assert sym.size_expression({"size_mode": "proportional", "size_field": "a"}, 7) == 7


# ── 3D extrusion ─────────────────────────────────────────────────────────────────────────────────

def test_extrusion_height_comes_from_a_field_and_is_scaled():
    paint = sym.extrusion_paint(
        {"extrusion": {"enabled": True, "field": "levels", "scale": 3}}, 1.0)
    assert paint["fill-extrusion-height"] == ["*", ["to-number", ["get", "levels"], 0], 3]


def test_a_missing_or_bad_height_becomes_zero_not_a_broken_layer():
    """`to-number` with an explicit fallback: one row with a null or a text height must not make
    MapLibre discard the expression and flatten the whole layer."""
    expr = sym.extrusion_paint({"extrusion": {"enabled": True, "field": "h"}}, 1.0)["fill-extrusion-height"]
    assert expr[1] == ["to-number", ["get", "h"], 0]


def test_extrusion_colour_can_still_be_data_driven():
    """Height by one field, colour by another is a normal thing to want — the extrusion must not
    quietly drop the graduated colour."""
    style = {"color_mode": "graduated", "color_field": "pop",
             "classes": [{"min": None, "max": 10, "color": "#aaa"}, {"min": 10, "color": "#bbb"}],
             "extrusion": {"enabled": True, "field": "h"}}
    assert sym.extrusion_paint(style, 1.0)["fill-extrusion-color"][0] == "step"


def test_extrusion_opacity_respects_the_layer_opacity():
    paint = sym.extrusion_paint({"extrusion": {"enabled": True, "field": "h", "opacity": 0.5}}, 0.5)
    assert paint["fill-extrusion-opacity"] == 0.25


def test_extrusion_is_off_unless_it_has_something_to_extrude_by():
    assert not sym.is_extruded({})
    assert not sym.is_extruded({"extrusion": {"enabled": True}})       # enabled but no field
    assert not sym.is_extruded({"extrusion": {"field": "h"}})          # field but not enabled
    assert sym.is_extruded({"extrusion": {"enabled": True, "field": "h"}})


# ── 2.5D: one flat height, no field ──────────────────────────────────────────────────────────────
# QGIS has TWO 3D renderers and they are not variations of one control. `Qgs25DRenderer` gives every
# feature the same height — the height is a PROJECT VARIABLE (`@qgis_25d_height`), not an attribute
# — while attribute-driven 3D reads a column. Supporting only the second meant a 2.5D style survived
# the whole trip from QGIS and then rendered flat, and that the web UI hid its 3D controls entirely
# for any layer with no numeric column, which is most building footprints.

class TestFlatHeightExtrusion:

    def test_a_height_with_no_field_is_a_plain_number(self):
        """Not an expression: there is no attribute to read, so the volume is the same everywhere."""
        paint = sym.extrusion_paint({"extrusion": {"enabled": True, "height": 12}}, 1.0)
        assert paint["fill-extrusion-height"] == 12

    def test_a_field_still_wins_when_both_are_present(self):
        """A style can carry a leftover `height` from before the author switched to a column — the
        UI keeps it deliberately so switching back restores the number. It must not win."""
        paint = sym.extrusion_paint(
            {"extrusion": {"enabled": True, "field": "h", "height": 12, "scale": 2}}, 1.0)
        assert paint["fill-extrusion-height"] == ["*", ["to-number", ["get", "h"], 0], 2]

    def test_a_flat_height_counts_as_extruded(self):
        """`is_extruded` decides whether a `fill-extrusion` layer is emitted at all, and whether a
        GeoParquet layer's deck descriptor carries the block. A 2.5D style has no field, so a
        field-only test made the layer draw flat with no error anywhere."""
        assert sym.is_extruded({"extrusion": {"enabled": True, "height": 10}})
        assert not sym.is_extruded({"extrusion": {"enabled": True, "height": 0}})

    def test_the_roof_colour_overrides_the_fill(self):
        """QGIS's 2.5D renderer names a roof colour and a wall colour; MapLibre paints one volume
        with a vertical gradient, so the roof is the only one it can honour."""
        style = {"color": "#111111", "extrusion": {"enabled": True, "height": 8, "color": "#ff8800"}}
        assert sym.extrusion_paint(style, 1.0)["fill-extrusion-color"] == "#ff8800"

    def test_without_a_roof_colour_it_matches_the_flat_symbology(self):
        """So a layer's 2D and 3D forms agree unless somebody asks otherwise."""
        style = {"color": "#111111", "extrusion": {"enabled": True, "height": 8}}
        assert sym.extrusion_paint(style, 1.0)["fill-extrusion-color"] == "#111111"

    def test_a_base_field_lifts_the_volume_off_the_ground(self):
        """`base` is overloaded exactly as MapLibre overloads `fill-extrusion-base`: a number lifts
        everything equally, a string names a column."""
        paint = sym.extrusion_paint(
            {"extrusion": {"enabled": True, "height": 9, "base": "floor", "scale": 3}}, 1.0)
        assert paint["fill-extrusion-base"] == ["*", ["to-number", ["get", "floor"], 0], 3]

    def test_a_numeric_base_stays_a_number(self):
        paint = sym.extrusion_paint({"extrusion": {"enabled": True, "height": 9, "base": 4}}, 1.0)
        assert paint["fill-extrusion-base"] == 4


# ── Point markers: shapes AND per-feature colour ─────────────────────────────────────────────────
# The first implementation switched a classified point layer to a `circle`, losing the marker shape.
# Rejected outright: "I should be able to choose a different marker and still use the graduated
# colour." `icon-image` is data-driven in MapLibre, so the style carries one image per CLASS and lets
# MapLibre pick. These tests exist to stop anything sliding back to the lossy version.

def test_a_classified_point_layer_KEEPS_its_marker_shape():
    style = {"marker": "star", "radius": 6, "color": "#000000",
             "color_mode": "graduated", "color_field": "pop",
             "classes": [{"min": None, "max": 10, "color": "#aaaaaa"},
                         {"min": 10, "max": None, "color": "#bbbbbb"}]}
    expr = sym.icon_image_expression(style)
    assert expr[0] == "step"
    ids = [x for x in expr if isinstance(x, str) and x.startswith("gd-pt-")]
    assert len(ids) == 2
    # Every image is the CHOSEN shape at the chosen size — only the colour differs per class.
    assert all(i.startswith("gd-pt-star-") and "-6-" in i for i in ids)


def test_the_icon_expression_mirrors_the_colour_expression_stop_for_stop():
    """Same field, same breaks, same order — otherwise a feature could take its colour from one
    classification and its icon from another."""
    style = {"marker": "square", "color_mode": "graduated", "color_field": "pop",
             "classes": [{"min": None, "max": 10, "color": "#aaaaaa"},
                         {"min": 10, "max": 20, "color": "#bbbbbb"},
                         {"min": 20, "max": None, "color": "#cccccc"}]}
    colors = sym.color_expression(style)
    icons = sym.icon_image_expression(style)
    assert colors[1] == icons[1]                    # same input expression
    assert colors[3::2][:2] == icons[3::2][:2]      # same break values


def test_categorized_points_get_an_image_for_the_fallback_too():
    """The `match` has an "other" colour, so there must be an "other" ICON — otherwise every value
    outside the listed categories renders with no marker at all."""
    style = {"marker": "diamond", "color_mode": "categorized", "color_field": "kind",
             "categories": [{"value": "oak", "color": "#0a0a0a"}], "other_color": "#999999"}
    ol, ow = sym.marker_outline(style)
    assert sym.icon_image_expression(style)[-1] == sym.marker_image_id(
        "diamond", "#999999", 5, ol, ow)


def test_marker_images_lists_every_bitmap_the_style_needs():
    """Baked into the published style so the runtime creates them up front — discovering them one
    `styleimagemissing` at a time makes markers pop in as each class scrolls into view."""
    style = {"marker": "circle", "color": "#000000", "color_mode": "categorized",
             "color_field": "k", "other_color": "#999999",
             "categories": [{"value": "a", "color": "#111111"}, {"value": "b", "color": "#222222"}]}
    images = sym.marker_images(style)
    ids = [i["id"] for i in images]
    assert len(ids) == len(set(ids)), "a duplicate would be generated twice for nothing"
    assert sym.marker_image_id("circle", "#111111", 5) in ids
    assert sym.marker_image_id("circle", "#999999", 5) in ids       # the fallback
    assert all(i["shape"] == "circle" for i in images)


# ── Outlines, including "none" ───────────────────────────────────────────────────────────────────

def test_no_outline_is_a_SENTINEL_not_an_empty_string():
    """The style dict is JSON that round-trips through a saved portal and three renderers, and "" is
    what an uninitialised colour input produces — so treating "" as "no outline" would silently strip
    outlines from layers whose author never touched the control. Absent still means the DEFAULT, so
    portals styled before this are unchanged."""
    assert sym.outline_color({}) == "#1d4ed8"
    assert sym.outline_color({"outline_color": None}) == "#1d4ed8"
    assert sym.outline_color({"outline_color": "#ff0000"}) == "#ff0000"
    assert sym.outline_color({"outline_color": sym.NO_OUTLINE}) is None


def test_no_outline_on_a_fill_is_ANTIALIAS_not_an_omission():
    """Reported as "I chose None and it drew a black outline".

    Omitting `fill-outline-color` does NOT remove the outline — the MapLibre spec says an
    unspecified outline MATCHES `fill-color`, and a `fill` layer always strokes its own edge. So the
    first implementation drew an outline in the fill colour, which on a dark fill reads as black.
    `fill-antialias: false` is the actual switch.

    Reasoned from intuition rather than the spec, which is why this test states the mechanism.
    """
    from geodeploy.services.portal_generator import _vector_layer

    class _L:
        id = 3; geometry_type = "MultiPolygon"; schema_name = "s"; table_name = "t"
        storage_backend = "postgis"; geometry_column = "geom"

    def paint(style):
        return _vector_layer("src", _L(), {"opacity": 1.0, "style": style})["paint"]

    off = paint({"color": "#123456", "outline_color": sym.NO_OUTLINE})
    assert off.get("fill-antialias") is False
    assert "fill-outline-color" not in off, "a colour here would be an outline, whatever its value"

    on = paint({"color": "#123456", "outline_color": "#ff0000"})
    assert on["fill-outline-color"] == "#ff0000"
    assert "fill-antialias" not in on, "the default is antialiased; do not restate it"


def test_a_marker_outline_width_is_a_RATIO_of_the_marker():
    """A 3 px ring around a 4 px dot and around a 20 px dot are different symbols. Someone resizing a
    layer expects the outline to keep its proportion, so the width scales with the marker. 0.28 is
    what the old hard-coded stroke was, so an unstyled marker is pixel-identical to before."""
    assert sym.marker_outline({}) == ("#ffffff", 0.28)
    assert sym.marker_outline({"outline_color": sym.NO_OUTLINE})[0] is None
    assert sym.marker_outline({"outline_width": 0.8})[1] == 0.8
    # Clamped: a ratio above 1 would draw outside the marker's own footprint.
    assert sym.marker_outline({"outline_width": 5})[1] == 1.0
    assert sym.marker_outline({"outline_width": "wide"})[1] == 0.28


def test_the_outline_is_part_of_the_marker_ID():
    """It changes the PIXELS, so it must change the id — otherwise a red-ringed marker and a
    white-ringed one collide on one image and whichever was created first wins for both."""
    white = sym.marker_image_id("circle", "#123456", 5, "#ffffff", 0.28)
    red = sym.marker_image_id("circle", "#123456", 5, "#ff0000", 0.28)
    none = sym.marker_image_id("circle", "#123456", 5, None, 0.28)
    thick = sym.marker_image_id("circle", "#123456", 5, "#ffffff", 0.6)
    assert len({white, red, none, thick}) == 4
    assert none.endswith("-none-0.28")


def test_marker_images_carry_the_outline_for_the_renderer():
    """The runtime builds each bitmap from this list, so the outline has to travel with it."""
    images = sym.marker_images({"marker": "circle", "color": "#123456",
                                "outline_color": sym.NO_OUTLINE, "outline_width": 0.5})
    assert images[0]["outline"] is None
    assert images[0]["outline_width"] == 0.5


def test_a_marker_id_round_trips_its_parameters():
    """The id IS the spec: the runtime rebuilds a missing image from it alone, which is what lets
    one layer own many icons. Twin of parseMarkerImageId in lib/symbology.js."""
    assert sym.marker_image_id("star", "#AABBCC", 7) == "gd-pt-star-aabbcc-7-ffffff-0.28"
    assert sym.marker_image_id("circle", "3b82f6", 5.5, None, 0.5) == "gd-pt-circle-3b82f6-5.5-none-0.5"


def test_icon_size_is_a_multiplier_of_the_bitmap():
    """The bitmap is drawn at the layer's base radius, so a fixed-size layer is exactly 1 and
    proportional size divides through — arithmetic MapLibre does inside the expression, which keeps
    ONE classification rather than pre-scaled stops."""
    assert sym.icon_size_expression({"radius": 6}) == 1
    expr = sym.icon_size_expression({"radius": 6, "size_mode": "proportional",
                                     "size_field": "a", "size_stops": [[0, 3], [10, 12]]})
    assert expr[0] == "/" and expr[2] == 6.0


def test_data_driven_detection_still_reports_per_feature_variation():
    """No longer picks a renderer — points keep their symbol layer — but it still answers "does
    anything vary per feature?" for callers that need to know."""
    assert not sym.is_data_driven({"color": "#f00", "marker": "star"})
    assert sym.is_data_driven({"color_mode": "graduated", "color_field": "p",
                               "classes": [{"min": None, "color": "#a"}]})
    assert not sym.is_data_driven({"color_mode": "graduated", "color_field": "p"})


# ── Ramps ────────────────────────────────────────────────────────────────────────────────────────

def test_a_ramp_yields_the_requested_number_of_distinct_colours():
    """EVERY count, not just the small ones — and DISTINCT, which is the part that used to fail.

    The ramps are seven anchor stops. Snapping to the nearest of them meant eight classes came out
    in seven colours and twelve in seven, so a graduated legend could name two classes that were
    drawn identically. That is what capped the class count at 12; interpolating removes both the
    duplicates and the reason for the cap.
    """
    for name in sym.RAMPS:
        for n in (2, 3, 5, 7, 8, 9, 12, 20, 50, 100):
            colors = sym.ramp_colors(name, n)
            assert len(colors) == n, (name, n)
            assert len(set(colors)) == n, (name, n, "repeated colours")
            assert colors[0] != colors[-1]


def test_the_ends_of_a_ramp_are_always_its_own_ends():
    """Interpolation must not drift off the ramp: whatever the count, the first and last colours are
    the ramp's own first and last, or a legend's extremes stop meaning "the extreme"."""
    for name, stops in sym.RAMPS.items():
        for n in (2, 5, 13, 40):
            colors = sym.ramp_colors(name, n)
            assert colors[0] == stops[0] and colors[-1] == stops[-1], (name, n)


# ── Qualitative colours, past the twelve that are hand-picked ────────────────────────────────────

def test_categories_keep_getting_distinct_colours_past_the_palette():
    """Cycling `CATEGORY_COLORS` drew category 13 exactly like category 1. QGIS never does that —
    its random ramp keeps generating — and a categorized layer over a 30-value column is ordinary."""
    colors = [sym.category_color(i) for i in range(120)]
    assert len(set(colors)) == 120


def test_the_hand_picked_twelve_come_first_and_are_unchanged():
    """Existing layers must not be recoloured: the first twelve are the palette they always were."""
    assert [sym.category_color(i) for i in range(12)] == sym.CATEGORY_COLORS


def test_a_categorys_colour_does_not_move_when_the_data_gains_a_value():
    """Deterministic, not random: adding a 20th value must not repaint the other nineteen."""
    assert sym.category_color(18) == sym.category_color(18)
    before = [sym.category_color(i) for i in range(19)]
    after = [sym.category_color(i) for i in range(20)]
    assert after[:19] == before


def test_generated_colours_are_valid_hex():
    for i in range(12, 200):
        c = sym.category_color(i)
        assert len(c) == 7 and c[0] == "#" and int(c[1:], 16) >= 0


def test_build_categories_uses_the_generated_palette_beyond_twelve():
    cats = sym.build_categories(["v{0}".format(i) for i in range(40)])
    assert len({c["color"] for c in cats}) == 40


def test_an_unknown_ramp_falls_back_rather_than_failing():
    assert sym.ramp_colors("no-such-ramp", 3) == sym.ramp_colors("viridis", 3)


def test_the_sampled_colours_are_pinned_against_the_javascript_twin():
    """Literal, because the twin is `ui/src/lib/symbology.js::rampColors` and it cannot be imported
    here — this list IS the contract between them.

    It caught a real divergence: the sampling used `round()`, which in Python rounds half to EVEN
    and in JavaScript rounds half UP, so at every `.5` position the two picked different stops —
    every 5- and 9-class ramp, in all nine ramps. Both now use `x + 0.5` truncated, which is
    expressible identically in either language. If this test fails, the JS file has drifted and the
    editor preview no longer matches what the portal publishes.
    """
    assert sym.ramp_colors("viridis", 5) == [
        "#440154", "#3e4786", "#277f8e", "#35b17a", "#fde725"]
    assert sym.ramp_colors("magma", 5) == [
        "#000004", "#641c79", "#de4968", "#feb780", "#fcfdbf"]
    # NINE CLASSES, NINE COLOURS. This list used to read
    #   "#f7fbff", "#deebf7", "#c6dbef", "#c6dbef", "#9ecae1", "#6baed6", "#3182bd", "#3182bd", …
    # — the duplicates pinned as expected behaviour, because sampling snapped to the nearest of
    # seven anchor stops. Two classes were drawn identically under a legend saying they differed.
    assert sym.ramp_colors("blues", 9) == [
        "#f7fbff", "#e4eff9", "#d2e3f3", "#bcd7ec", "#9ecae1",
        "#78b5d9", "#4e98ca", "#2776b5", "#08519c"]


# ── Reversing a ramp (issue #11) ─────────────────────────────────────────────────────────────────
# Which end means "high" is a cartographic choice, not a property of the ramp. Stored as a FLAG
# rather than as reversed copies in the table: nine ramps would become eighteen and the picker a
# wall. `ui/src/lib/symbology.js::rampColors` is the twin and must reverse identically.

def test_reversing_returns_the_same_colours_in_the_opposite_order():
    """The SAMPLED output is reversed, not the stop list — so a reversed ramp is the same colours
    backwards, not a differently-sampled ramp."""
    for n in (2, 3, 5, 9, 12):
        forward = sym.ramp_colors("viridis", n)
        assert sym.ramp_colors("viridis", n, reverse=True) == forward[::-1]


def test_reversing_twice_is_the_identity():
    assert sym.ramp_colors("magma", 6, reverse=True)[::-1] == sym.ramp_colors("magma", 6)


def test_a_single_class_is_unaffected_by_direction():
    """One colour has no ends to swap; reversing must not pick a different stop."""
    assert sym.ramp_colors("blues", 1, reverse=True) == sym.ramp_colors("blues", 1)


def test_classes_carry_the_reversed_colours_with_the_same_breaks():
    """Reversing changes which class is which COLOUR and nothing else — the breaks are the
    classifier's business and must not move."""
    values = [float(v) for v in range(1, 101)]
    forward = sym.build_classes(values, "quantile", 5, "viridis")
    reversed_ = sym.build_classes(values, "quantile", 5, "viridis", reverse=True)
    assert [c["min"] for c in forward] == [c["min"] for c in reversed_]
    assert [c["max"] for c in forward] == [c["max"] for c in reversed_]
    assert [c["color"] for c in reversed_] == [c["color"] for c in forward][::-1]


def test_categories_can_be_reversed_too():
    values = ["a", "b", "c", "d"]
    forward = sym.build_categories(values, "viridis")
    flipped = sym.build_categories(values, "viridis", reverse=True)
    assert [c["value"] for c in flipped] == values          # order of VALUES is unchanged
    assert [c["color"] for c in flipped] == [c["color"] for c in forward][::-1]


# ── 3D bar defaults come from the DATA ───────────────────────────────────────────────────────────
# A point has no size, so BOTH the width and the height of a bar come from defaults — and a fixed
# default is right at exactly one scale. 240 country centroids with the old fixed 30 m radius drew
# bars roughly three thousandths of a pixel wide: rendered exactly as asked, and indistinguishable
# from "3D does not work".

def test_the_bar_radius_scales_with_the_layers_own_extent():
    import json as _json

    world = _json.dumps([-180, -60, 180, 75])
    city = _json.dumps([11.90, 57.65, 12.05, 57.75])

    # World-scale layer: a bar has to be kilometres across to be a pixel.
    assert sym.pillar_radius({}, world) >= 50_000
    # Street-scale layer: essentially the old default, so nothing local changes.
    assert 20 <= sym.pillar_radius({}, city) <= 60
    assert sym.pillar_radius({}, city) < sym.pillar_radius({}, world)


def test_an_author_chosen_radius_always_wins():
    import json as _json

    world = _json.dumps([-180, -60, 180, 75])
    assert sym.pillar_radius({"extrusion": {"radius": 42}}, world) == 42
    # ...still clamped.
    assert sym.pillar_radius({"extrusion": {"radius": 10 ** 9}}, world) <= 100_000


def test_without_a_usable_bbox_it_falls_back_to_the_fixed_default():
    """No bbox, a malformed one, or a zero-area one must not produce a nonsense size."""
    assert sym.pillar_radius({}, None) == sym.DEFAULT_PILLAR_RADIUS_M
    assert sym.pillar_radius({}, "not json") == sym.DEFAULT_PILLAR_RADIUS_M
    assert sym.pillar_radius({}, "[5, 5, 5, 5]") == sym.DEFAULT_PILLAR_RADIUS_M
    assert sym.extent_metres("[1, 2, 3]") is None


def test_extent_metres_is_sane():
    import json as _json

    # Sweden-ish: roughly 1500 km across the diagonal. Approximate is fine; wrong by an order of
    # magnitude is not, since it sets the symbol size.
    d = sym.extent_metres(_json.dumps([11.0, 55.3, 24.2, 69.1]))
    assert 1_000_000 < d < 2_500_000


def test_a_bbox_that_is_not_lonlat_is_refused():
    """Layer bboxes are EPSG:4326 app-wide, but a projected one would be read as millions of degrees
    and silently clamp every bar to the maximum size. Out of range → the fixed fallback, which is
    merely small rather than wrong."""
    assert sym.extent_metres("[319000, 6390000, 410000, 6480000]") is None
    assert sym.pillar_radius({}, "[319000, 6390000, 410000, 6480000]") == \
        sym.DEFAULT_PILLAR_RADIUS_M


class TestLegendCoversTheNewSymbologies:
    """A legend that disagrees with the map is worse than no legend.

    `legend_entries` only knew about graduated and categorized COLOUR, so every symbology added for
    the QGIS round trip drew a map the legend could not describe: a rule-based layer and a heatmap
    both returned `[]`, and a classified layer of dashed lines or star markers came out as a column
    of plain squares. Reported as "legends don't display the new symbologies yet".
    """

    def test_a_heatmap_gets_a_ramp_not_classes(self):
        """It has no classes to list. Returning the classes of the symbology it REPLACED would
        describe a map nobody is looking at."""
        style = {"color_mode": "graduated",
                 "classes": [{"min": 0, "max": 5, "color": "#111"}],
                 "heatmap": {"enabled": True, "ramp": ["rgba(0,0,255,0)", "#3b82f6", "#ef4444"]}}
        entries = sym.legend_entries(style)
        assert len(entries) == 1
        assert entries[0]["heatmap"] is True
        assert entries[0]["ramp"] == ["rgba(0,0,255,0)", "#3b82f6", "#ef4444"]

    def test_rules_outrank_the_fallback_colour_mode(self):
        """Same precedence the renderers use: a rule-based style's `color_mode` is only the shape
        for viewers that know nothing about rules."""
        style = {"color_mode": "categorized",
                 "categories": [{"value": "a", "color": "#111"}],
                 "rules": [{"label": "Big", "style": {"color": "#ff0000"}},
                           {"label": "Small", "style": {"color": "#00ff00"}}]}
        entries = sym.legend_entries(style)
        assert [e["label"] for e in entries] == ["Big", "Small"]
        assert [e["color"] for e in entries] == ["#ff0000", "#00ff00"]
        assert all(e["rule"] for e in entries)

    def test_a_rule_without_a_label_is_described_by_its_expression(self):
        """Which is at least a statement of what it selects. Matches `cli/geodeploy/styles.py`."""
        entries = sym.legend_entries({"rules": [{"expression": "pop > 1000"}]})
        assert entries[0]["label"] == "pop > 1000"

    def test_each_rule_carries_its_own_symbol(self):
        """A rule-based layer varies by EVERYTHING at once. Drawing every rule with the layer's
        base symbol reports a dashed rule and a solid one as the same thing."""
        style = {"color": "#111", "rules": [
            {"label": "A", "style": {"color": "#f00", "dash": "dashed", "line_width": 4}},
            {"label": "B", "style": {"color": "#0f0"}}]}
        a, b = sym.legend_entries(style)
        assert a["dash"] == "dashed" and a["line_width"] == 4
        assert "dash" not in b

    def test_a_rule_inherits_what_it_does_not_override(self):
        """A rule that only changes colour is still drawn with the layer's marker shape."""
        style = {"color": "#111", "shape": "star",
                 "rules": [{"label": "A", "style": {"color": "#f00"}}]}
        assert sym.legend_entries(style)[0]["shape"] == "star"

    def test_a_classified_layer_carries_its_shape_to_every_swatch(self):
        """A square swatch actively misreports a layer drawn with stars."""
        style = {"color_mode": "categorized", "shape": "star", "dash": "dotted",
                 "categories": [{"value": "a", "color": "#111"}, {"value": "b", "color": "#222"}]}
        entries = sym.legend_entries(style)
        assert len(entries) == 3                          # two categories plus "Other"
        assert all(e["shape"] == "star" for e in entries)

    def test_a_pattern_travels_as_its_TILE_not_as_the_block(self):
        """A swatch needs pixels; the rest of the block describes how MapLibre repeats them."""
        style = {"color_mode": "categorized",
                 "categories": [{"value": "a", "color": "#111"}],
                 "fill_pattern": {"image": PNG, "width": 12, "height": 12, "hatch": "cross"}}
        assert sym.legend_entries(style)[0]["fill_pattern"] == PNG

    @pytest.mark.parametrize("block", [{}, {"image": "http://x/y.png"}, {"image": None}, "nope"])
    def test_a_pattern_that_is_not_a_data_uri_is_not_carried(self, block):
        """The value comes from a client, and it goes straight into an `img src` and a CSS
        `url()` — so anything but the shape a generated tile has is refused, not sanitised."""
        style = {"color_mode": "categorized", "categories": [{"value": "a", "color": "#111"}],
                 "fill_pattern": block}
        assert "fill_pattern" not in sym.legend_entries(style)[0]

    def test_a_single_symbol_still_has_no_legend(self):
        """One swatch repeating the layer's colour says nothing its own swatch does not. The
        `/legend` endpoint synthesises a row for callers that need one; that is presentation."""
        assert sym.legend_entries({"color": "#abcdef", "shape": "star"}) == []
