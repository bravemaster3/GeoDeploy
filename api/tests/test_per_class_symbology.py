"""A class of a classified layer is more than a colour.

GeoDeploy carried a colour per class and ONE shape for the whole layer, taken from the first class.
Reported on a layer with two categories in the SAME colour that differed only by dash: the two
arrived identical, and the map lost the distinction it was drawn to make.

WHY A CLASS BECOMES A RENDER LAYER instead of a data-driven expression. MapLibre can vary a colour,
an opacity and a width per feature, but **`line-dasharray` is not data-driven at all** — there is no
expression that chooses a dash from an attribute. So a layer whose classes differ by dash cannot be
one render layer however it is written. One layer per class draws every difference, is what a
rule-based renderer already flattens to, and goes through the ordinary single-symbol builder, so
markers, outlines and patterns need no second implementation.

The cost is N render layers instead of one, and the whole of what keeps that cost off every existing
portal is that an ORDINARY classified layer — every class the same shape in a different colour — is
not split at all. Half the tests here are about that.
"""
import pytest

from geodeploy.services import portal_generator as pg
from geodeploy.services import symbology as sym


# ── Fixtures ─────────────────────────────────────────────────────────────────────────────────────

class FakeLayer:
    """The handful of attributes `_vector_layers` reads off a vector layer."""

    def __init__(self, geometry_type="LineString", layer_id=42):
        self.id = layer_id
        self.name = "Tithe boundaries"
        self.geometry_type = geometry_type
        self.storage_backend = "postgis"
        self.schema_name = "public"
        self.table_name = "tithe"
        self.uid = "abc123"
        self.s3_key = None
        self.pmtiles_key = None
        self.bbox = None
        self.default_style = {}
        self.tile_status = "ready"


def build(style, geometry="LineString"):
    return pg._vector_layers("src", FakeLayer(geometry), {"style": style, "opacity": 1.0})


PLAIN_CATEGORIES = {
    "color_mode": "categorized", "color_field": "kind", "line_width": 2,
    "categories": [{"value": "a", "color": "#84d9ff"}, {"value": "b", "color": "#e24646"}],
}

#: The reported layer: one colour, two dashes. Only the SECOND category carries anything of its
#: own — the first matches the layer and inherits, which is what keeps the style small.
DASH_PER_CLASS = {
    "color_mode": "categorized", "color_field": "kind", "line_width": 2.27, "lineType": "dashed",
    "other_color": "#9ca3af",
    "categories": [{"value": "tithe", "color": "#84d9ff"},
                   {"value": "modern", "color": "#84d9ff",
                    "lineType": "solid", "line_width": 5.29}],
}

WIDTH_PER_CLASS = {
    "color_mode": "graduated", "color_field": "pop", "line_width": 1,
    "classes": [{"min": None, "max": 10, "color": "#eeeeee"},
                {"min": 10, "max": 50, "color": "#888888"},
                {"min": 50, "max": None, "color": "#111111", "line_width": 6}],
}


# ── An ordinary classified layer must not change at all ──────────────────────────────────────────

def test_an_ordinary_categorized_layer_is_not_split():
    assert sym.expand_classes(PLAIN_CATEGORIES) is None


def test_an_ordinary_graduated_layer_is_not_split():
    style = {"color_mode": "graduated", "color_field": "pop",
             "classes": [{"min": None, "max": 10, "color": "#eee"},
                         {"min": 10, "max": None, "color": "#111"}]}
    assert sym.expand_classes(style) is None


def test_a_single_symbol_layer_is_not_split():
    assert sym.expand_classes({"color": "#f00", "line_width": 3}) is None


def test_an_unsplit_layer_still_draws_as_one_layer_with_a_match_expression():
    built = build(PLAIN_CATEGORIES)
    assert len(built) == 1
    assert built[0]["id"] == "vector-42"
    assert built[0]["paint"]["line-color"][0] == "match"


def test_a_class_carrying_only_a_colour_is_not_a_shape():
    """`color` is not in `CLASS_SHAPE_KEYS`, or every classified layer on every instance would
    split the moment it was read back out of QGIS."""
    assert "color" not in sym.CLASS_SHAPE_KEYS
    assert "value" not in sym.CLASS_SHAPE_KEYS
    assert "min" not in sym.CLASS_SHAPE_KEYS and "max" not in sym.CLASS_SHAPE_KEYS


# ── …and one that really does differ must be split ───────────────────────────────────────────────

def test_two_categories_in_one_colour_still_differ():
    rules = sym.expand_classes(DASH_PER_CLASS)
    assert len(rules) == 3                        # the catch-all, plus one per category
    assert [r["style"].get("lineType") for r in rules] == ["dashed", "dashed", "solid"]
    assert [r["style"].get("line_width") for r in rules] == [2.27, 2.27, 5.29]


def test_the_catch_all_draws_underneath():
    """`match`'s fallback colours everything unlisted, and it must keep doing so — drawn FIRST,
    which is where the tile renderer and QGIS both put it."""
    rules = sym.expand_classes(DASH_PER_CLASS)
    assert rules[0]["label"] == "Other"
    assert rules[0]["style"]["color"] == "#9ca3af"
    assert rules[0]["filter"] == ["match", ["to-string", ["get", "kind"]],
                                  ["tithe", "modern"], False, True]


def test_the_split_becomes_one_render_layer_per_class():
    built = build(DASH_PER_CLASS)
    assert [ml["id"] for ml in built] == ["vector-42-r0", "vector-42-r1", "vector-42-r2"]
    assert built[1]["paint"]["line-dasharray"], "the dashed class draws dashed"
    assert "line-dasharray" not in built[2]["paint"], "the solid class draws solid"
    assert built[1]["paint"]["line-color"] == built[2]["paint"]["line-color"] == "#84d9ff"
    assert (built[1]["paint"]["line-width"], built[2]["paint"]["line-width"]) == (2.27, 5.29)


def test_every_split_layer_carries_its_own_filter():
    built = build(DASH_PER_CLASS)
    assert all(ml.get("filter") is not None for ml in built)
    assert built[2]["filter"] == ["==", ["to-string", ["get", "kind"]], "modern"]


# ── The graduated split has to mirror `step`, not read min/max ───────────────────────────────────

def test_a_graduated_split_mirrors_the_step_stops():
    """`step` gives everything BELOW the first boundary the first class's colour and everything
    above the last one the last class's, so filters read off `min`/`max` literally would stop
    drawing the features outside the sampled range — a silent change to which rows appear."""
    rules = sym.expand_classes(WIDTH_PER_CLASS)
    num = ["to-number", ["get", "pop"]]
    assert rules[0]["filter"] == ["<", num, 10]
    assert rules[1]["filter"] == ["all", [">=", num, 10], ["<", num, 50]]
    assert rules[2]["filter"] == [">=", num, 50]


def test_the_graduated_boundaries_are_the_same_numbers_step_uses():
    step = sym.color_expression(WIDTH_PER_CLASS)
    boundaries = [v for v in step[3:] if isinstance(v, (int, float))]
    rules = sym.expand_classes(WIDTH_PER_CLASS)
    from_filters = []
    for rule in rules[1:]:
        flat = str(rule["filter"])
        from_filters.append(next(b for b in boundaries if ">=', {0}".format(b) in flat
                                 or ">=', {0}]".format(b) in flat or str(b) in flat))
    assert from_filters == boundaries


def test_a_graduated_class_keeps_its_own_width():
    built = build(WIDTH_PER_CLASS)
    assert [ml["paint"]["line-width"] for ml in built] == [1, 1, 6]


# ── Polygons, and the class that fills nothing ───────────────────────────────────────────────────

def test_one_hollow_class_among_filled_ones():
    style = {"color_mode": "categorized", "color_field": "kind", "fill_opacity": 1.0,
             "categories": [{"value": "hachure", "color": "#e9c9b0", "fill_opacity": 0.0},
                            {"value": "infra", "color": "#d5b43c"}]}
    built = build(style, "Polygon")
    fills = [ml for ml in built if ml["type"] == "fill"]
    assert len(fills) == 3
    assert fills[1]["paint"]["fill-opacity"] == 0.0, "the hachure class paints no area"
    assert fills[2]["paint"]["fill-opacity"] == 1.0, "the others still do"


def test_a_marker_per_class_becomes_a_symbol_layer_per_class():
    style = {"color_mode": "categorized", "color_field": "kind", "marker": "circle", "radius": 5,
             "categories": [{"value": "s", "color": "#ff0000", "marker": "square", "radius": 3},
                            {"value": "b", "color": "#00ff00", "marker": "triangle",
                             "radius": 8}]}
    built = build(style, "Point")
    icons = [ml["layout"]["icon-image"] for ml in built if ml["type"] == "symbol"]
    assert len(icons) == 3
    assert "square" in icons[1] and "triangle" in icons[2]


# ── Rules still outrank classes ──────────────────────────────────────────────────────────────────

def test_a_rule_based_layer_is_drawn_by_its_rules_not_by_its_classes():
    """A rule-based style also carries the first rule's shape at the top level so a viewer that
    knows nothing about rules draws something — reading that first would flatten the layer."""
    style = dict(DASH_PER_CLASS, rules=[
        {"filter": ["==", ["get", "kind"], "x"], "style": {"color": "#000", "line_width": 9}}])
    built = build(style)
    assert len(built) == 1
    assert built[0]["paint"]["line-width"] == 9


def test_expand_classes_ignores_a_style_that_is_already_rule_based():
    style = dict(DASH_PER_CLASS, rules=[{"filter": None, "style": {}}])
    rules = sym.expand_classes(style)
    assert all("rules" not in r["style"] for r in rules), "a rule inside a rule is a loop"


# ── The layer's own scope reaches every split layer ──────────────────────────────────────────────

def test_the_layers_zoom_range_applies_to_every_split_layer():
    style = dict(DASH_PER_CLASS, minzoom=6, maxzoom=14)
    built = build(style)
    assert len(built) == 3
    assert all(ml.get("minzoom") == 6 and ml.get("maxzoom") == 14 for ml in built)


def test_the_layers_own_filter_is_anded_with_each_class_filter():
    """Both are true at once in QGIS, so a subset string must narrow the classes rather than
    replace them."""
    style = dict(DASH_PER_CLASS, filter=["==", ["get", "county"], "Kent"])
    built = build(style)
    for ml in built:
        assert ml["filter"][0] == "all", ml["filter"]
        assert ["==", ["get", "county"], "Kent"] in ml["filter"]


# ── A class with no colour is not a class ────────────────────────────────────────────────────────

def test_a_colourless_class_is_skipped_rather_than_drawn_in_black():
    style = {"color_mode": "categorized", "color_field": "kind", "lineType": "dashed",
             "categories": [{"value": "a", "color": "#84d9ff", "lineType": "solid"},
                            {"value": "b"}]}
    rules = sym.expand_classes(style)
    assert [r["label"] for r in rules] == ["Other", "a"]


def test_a_classification_with_no_field_cannot_be_split():
    style = dict(DASH_PER_CLASS)
    style.pop("color_field")
    assert sym.expand_classes(style) is None


# ── class_style is the inverse of what the plugin writes ─────────────────────────────────────────

@pytest.mark.parametrize("key,value", [
    ("line_width", 5.0), ("lineType", "dotted"), ("fill_opacity", 0.2),
    ("outline_color", "#123456"), ("outline_width", 3.0), ("radius", 9.0),
    ("marker", "star"), ("dash_pattern", [4, 2]),
])
def test_every_class_shape_key_overrides_the_layers(key, value):
    merged = sym.class_style({"color": "#fff", key: "LAYER"}, {"color": "#000", key: value})
    assert merged[key] == value
    assert merged["color"] == "#fff", "a class's colour is applied by the caller, not by class_style"


def test_class_style_leaves_a_style_alone_when_the_class_carries_nothing():
    base = {"color": "#fff", "line_width": 2}
    assert sym.class_style(base, {"value": "a", "color": "#000"}) == base


# ── A dashed hairline border needs its own line layer ────────────────────────────────────────────

def test_a_dashed_hairline_polygon_border_gets_a_line_layer():
    """`fill-outline-color` is a colour with no width and no pattern, so a fill's own edge cannot
    draw a dash. Without this the border simply rendered solid, at every width up to 1 px."""
    style = {"color": "#3b82f6", "fill_opacity": 0.4, "outline_color": "#cc22d2",
             "outline_width": 1.0, "lineType": "dashed"}
    assert sym.needs_outline_layer(style)
    built = build(style, "Polygon")
    outline = [ml for ml in built if ml["type"] == "line"]
    assert len(outline) == 1
    assert outline[0]["paint"]["line-dasharray"]


def test_a_plain_hairline_border_still_draws_as_the_fills_own_edge():
    """Emitting the extra layer unconditionally would change how every existing portal renders."""
    style = {"color": "#3b82f6", "fill_opacity": 0.4, "outline_color": "#1d4ed8"}
    assert not sym.needs_outline_layer(style)
    assert len(build(style, "Polygon")) == 1
