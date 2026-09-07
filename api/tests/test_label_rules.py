"""A layer that labels by RULE draws one label layer per rule.

QGIS labels a place-names layer with a rule tree: water blue at 9pt, woodland green, a town brown at
11. The plugin used to read the FIRST rule and send it as the whole labelling, so every name on the
map came out water-blue at one size — reported exactly that way ("this layer has different colours
from what it had in QGIS").

`labels.rules` is the same shape `style.rules` uses, and for the same reason: MapLibre cannot vary
text colour or size per feature from one layer, so a rule has to be a layer. The top-level block
stays as the fallback, which is what a renderer knowing nothing about label rules still draws.
"""
import pytest

from geodeploy.services import portal_generator as pg
from geodeploy.services import symbology as sym


class FakeLayer:
    id = 7
    name = "OS Open Zoomstack - names"
    geometry_type = "MultiPoint"
    storage_backend = "postgis"
    schema_name = "public"
    table_name = "names"
    uid = "u7"
    s3_key = None
    pmtiles_key = None
    bbox = None
    default_style = {}
    tile_status = "ready"


#: The reported layer, trimmed to three of its eight rules.
RULED = {
    "color": "#e5b636", "color_mode": "single", "marker": "circle", "radius": 0.0,
    "marker_opacity": 0.0,
    "labels": {
        "enabled": True, "field": "name1", "color": "#318fae", "size": 12.0,
        "rules": [
            {"label": "Water", "expression": "\"type\" = 'Water'",
             "filter": ["==", ["get", "type"], "Water"],
             "labels": {"enabled": True, "field": "name1", "color": "#318fae", "size": 12.0}},
            {"label": "Woodland", "expression": "\"type\" = 'Woodland'",
             "filter": ["==", ["get", "type"], "Woodland"],
             "labels": {"enabled": True, "field": "name1", "color": "#599c30", "size": 12.0}},
            {"label": "Town", "expression": "\"type\" = 'Town'",
             "filter": ["==", ["get", "type"], "Town"],
             "labels": {"enabled": True, "field": "name1", "color": "#372d0b", "size": 14.67}},
        ],
    },
}

PLAIN = {"color": "#3b82f6", "labels": {"enabled": True, "field": "name1", "color": "#111111"}}


def build(style):
    return pg._vector_layers("src", FakeLayer(), {"style": style, "opacity": 1.0})


def label_layers(built):
    return [ml for ml in built if (ml.get("layout") or {}).get("text-field") is not None]


# ── The accessor ─────────────────────────────────────────────────────────────────────────────────

def test_the_rules_are_read():
    assert len(sym.label_rules(RULED["labels"])) == 3


def test_an_ordinary_labelling_has_none():
    assert sym.label_rules(PLAIN["labels"]) == []


def test_an_entry_without_a_labels_block_is_not_a_rule():
    """Half a rule would produce a label layer with no text, which MapLibre drops silently."""
    assert sym.label_rules({"rules": [{"label": "x", "filter": ["==", 1, 1]}]}) == []


# ── The layers ───────────────────────────────────────────────────────────────────────────────────

def test_one_label_layer_per_rule():
    labels = label_layers(build(RULED))
    assert len(labels) == 3
    assert [ml["id"] for ml in labels] == ["vector-7-labels-r0", "vector-7-labels-r1",
                                           "vector-7-labels-r2"]


def test_each_label_layer_is_painted_in_its_own_colour():
    colours = [ml["paint"]["text-color"] for ml in label_layers(build(RULED))]
    assert colours == ["#318fae", "#599c30", "#372d0b"]


def test_each_label_layer_has_its_own_size():
    sizes = [ml["layout"]["text-size"] for ml in label_layers(build(RULED))]
    assert sizes[2] != sizes[0], "a town is labelled larger than a lake"


def test_each_label_layer_is_filtered_to_its_rule():
    filters = [ml.get("filter") for ml in label_layers(build(RULED))]
    assert filters == [["==", ["get", "type"], "Water"],
                       ["==", ["get", "type"], "Woodland"],
                       ["==", ["get", "type"], "Town"]]


def test_a_rule_inherits_what_it_does_not_override():
    """A rule that names only a colour still labels the right field — QGIS's rules start as a copy
    of the layer's settings, so anything a rule is silent about comes from the layer."""
    style = {"labels": {"enabled": True, "field": "name1", "color": "#111111", "size": 12.0,
                        "rules": [{"label": "A", "filter": ["==", ["get", "t"], "a"],
                                   "labels": {"enabled": True, "color": "#ff0000"}}]}}
    built = label_layers(build(style))
    assert len(built) == 1
    assert built[0]["paint"]["text-color"] == "#ff0000"
    assert built[0]["layout"]["text-field"] == ["to-string", ["get", "name1"]]


def test_an_ordinary_labelling_is_still_one_layer():
    labels = label_layers(build(PLAIN))
    assert len(labels) == 1
    assert labels[0]["id"] == "vector-7-labels"
    assert labels[0].get("filter") is None


def test_a_rule_list_that_draws_nothing_still_labels_the_layer():
    """Switching to rules must never silently remove every label a layer had."""
    style = {"labels": {"enabled": True, "field": "name1", "color": "#111111",
                        "rules": [{"label": "A", "filter": ["==", ["get", "t"], "a"],
                                   "labels": {"enabled": True}}]}}
    # The rule inherits `field` from the layer, so it DOES draw — the fallback is for the case
    # where no rule produces text at all.
    assert len(label_layers(build(style))) == 1
    empty = {"labels": {"enabled": True, "rules": [{"label": "A", "labels": {"enabled": True}}]}}
    built = build(empty)
    assert len(label_layers(built)) == 0, "no field anywhere means no text, which is honest"


def test_a_rules_key_never_leaks_into_a_label_layer():
    """`rules` is not a label property; a renderer reading it as one would be a loop."""
    for ml in label_layers(build(RULED)):
        assert "rules" not in str(ml.get("layout")) and "rules" not in str(ml.get("paint"))


# ── The zoom range a rule carries ────────────────────────────────────────────────────────────────

def test_a_rules_zoom_range_reaches_its_layer():
    style = {"labels": {"enabled": True, "field": "n", "color": "#111",
                        "rules": [{"label": "A", "filter": ["==", ["get", "t"], "a"],
                                   "minzoom": 8, "maxzoom": 14,
                                   "labels": {"enabled": True, "color": "#f00"}}]}}
    built = label_layers(build(style))
    assert (built[0].get("minzoom"), built[0].get("maxzoom")) == (8, 14)


def test_a_rule_zoom_outside_maplibres_range_is_clamped():
    """QGIS stores scale thresholds far outside 0-24, and ONE out-of-range number makes MapLibre
    reject the whole style rather than ignore it."""
    style = {"labels": {"enabled": True, "field": "n", "color": "#111",
                        "rules": [{"label": "A", "filter": ["==", ["get", "t"], "a"],
                                   "minzoom": -3, "maxzoom": 29.058,
                                   "labels": {"enabled": True}}]}}
    built = label_layers(build(style))
    assert built[0].get("minzoom") is None, "a minzoom of 0 is the default and is not written"
    assert built[0].get("maxzoom") is None, "24 is the default and is not written"


# ── Labels still ride along with everything else ─────────────────────────────────────────────────

@pytest.mark.parametrize("extra", [
    {},
    {"color_mode": "categorized", "color_field": "type", "line_width": 2, "lineType": "dashed",
     "categories": [{"value": "a", "color": "#111"},
                    {"value": "b", "color": "#111", "lineType": "solid"}]},
    {"heatmap": {"enabled": True}},
])
def test_label_rules_survive_whatever_draws_the_geometry(extra):
    """Labels are their own layers, so a heatmap, a rule split and a plain symbol must all keep
    them — the heatmap case especially, since it REPLACES the features."""
    built = build(dict(RULED, **extra))
    assert len(label_layers(built)) == 3
