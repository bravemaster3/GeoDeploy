"""A legend that shows only colour describes half of a map.

Colour and size are independent dimensions on purpose — `color_mode` is separate from `size_mode`
so that colouring by population while sizing by area is possible — which means a legend has to be
able to show size ALONE, colour alone, or both. And a row of swatches that never names the column
it measures shows THAT something varies without saying what.
"""
from geodeploy.services.symbology import color_field, size_legend


def _sized(**over):
    style = {"size_mode": "proportional", "size_field": "pop",
             "size_stops": [[100, 4], [5000, 20]]}
    style.update(over)
    return style


def test_size_legend_reports_both_ends():
    out = size_legend(_sized())
    assert out["field"] == "pop"
    assert (out["min_value"], out["min_size"]) == (100, 4)
    assert (out["max_value"], out["max_size"]) == (5000, 20)
    assert (out["min_label"], out["max_label"]) == ("100", "5000")


def test_stops_out_of_order_are_sorted_not_trusted():
    """A legend showing 5000 at the small end would be worse than no legend."""
    out = size_legend(_sized(size_stops=[[5000, 20], [100, 4]]))
    assert out["min_value"] == 100 and out["max_value"] == 5000


def test_no_size_legend_when_size_is_fixed():
    assert size_legend({"size_mode": "fixed", "size_field": "pop"}) is None
    assert size_legend({}) is None


def test_no_size_legend_without_enough_to_interpolate():
    """One stop is a fixed size wearing a costume — there is no scale to draw."""
    assert size_legend(_sized(size_stops=[[100, 4]])) is None
    assert size_legend(_sized(size_field="")) is None


def test_size_and_graduated_colour_coexist():
    """The combination the report was about: both dimensions on one layer."""
    style = _sized(color_mode="graduated", color_field="area",
                   classes=[{"min": 0, "max": 5, "color": "#111111"}])
    assert size_legend(style)["field"] == "pop"
    assert color_field(style) == "area"


def test_colour_field_is_none_for_a_single_symbol():
    """Naming a field on a layer whose colour does not vary would be a lie."""
    assert color_field({"color": "#abcdef"}) is None
    assert color_field({"color_mode": "single", "color_field": "leftover"}) is None


def test_colour_field_is_reported_for_both_data_driven_modes():
    assert color_field({"color_mode": "graduated", "color_field": "pop"}) == "pop"
    assert color_field({"color_mode": "categorized", "color_field": "kind"}) == "kind"
    assert color_field({"color_mode": "graduated", "color_field": "  "}) is None
