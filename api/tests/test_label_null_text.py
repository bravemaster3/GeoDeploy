"""A QGIS label that is NULL for some features must not take the whole map down.

`CASE WHEN "pop" > 1000 THEN "name" END` (no ELSE) and `if(cond, "name", NULL)` are ordinary QGIS
labels, and the plugin's translator renders them faithfully with a null fallback. MapLibre
type-checks `text-field` as text and rejects that null — and with it the ENTIRE style, so a portal
published from QGIS showed no map at all: "layers[11].layout.text-field[3]: Expected formatted but
found null instead." (reported 2026-11-19). The expressions below are the translator's real output,
pinned as literals; MapLibre's own validator (`@maplibre/maplibre-gl-style-spec`) rejected each `raw`
and accepted each fixed one when this was written.
"""
import pytest

from geodeploy.services import symbology

_NAME = ["get", "name"]
_BIG = [">", ["get", "pop"], 1000]


@pytest.mark.parametrize("raw, fixed", [
    # CASE WHEN "pop" > 1000 THEN "name" END   — and  if("pop" > 1000, "name", NULL)
    (["case", _BIG, _NAME, None], ["case", _BIG, _NAME, ""]),
    # CASE WHEN "a" = 1 THEN "name" WHEN "a" = 2 THEN NULL ELSE "code" END
    (["case", ["==", ["get", "a"], 1], _NAME, ["==", ["get", "a"], 2], None, ["get", "code"]],
     ["case", ["==", ["get", "a"], 1], _NAME, ["==", ["get", "a"], 2], "", ["get", "code"]]),
    # coalesce("name", NULL)
    (["coalesce", _NAME, None], ["coalesce", _NAME, ""]),
    (["match", ["get", "k"], "a", _NAME, None], ["match", ["get", "k"], "a", _NAME, ""]),
    # nested: the inner case is an output of the outer one
    (["case", _BIG, ["case", ["has", "x"], _NAME, None], None],
     ["case", _BIG, ["case", ["has", "x"], _NAME, ""], ""]),
])
def test_a_null_label_output_becomes_no_label(raw, fixed):
    assert symbology.label_text({"expression": raw}) == fixed
    assert symbology.label_layout({"enabled": True, "expression": raw})["text-field"] == fixed


def test_a_null_compared_against_in_a_condition_is_left_alone():
    """CASE WHEN "name" IS NULL THEN 'unnamed' ELSE "name" END — that null is the question asked."""
    expr = ["case", ["any", ["!", ["has", "name"]], ["==", ["get", "name"], None]], "unnamed", _NAME]
    assert symbology.label_text({"expression": expr}) == expr


def test_a_match_label_that_is_null_is_left_alone():
    # A match LABEL is an input value, not an output; only the outputs may become "".
    expr = ["match", ["get", "k"], "x", _NAME, "fallback"]
    assert symbology.label_text({"expression": expr}) == expr


def test_plain_labels_are_unchanged():
    concat = ["concat", _NAME, " (", ["get", "year"], ")"]
    assert symbology.label_text({"expression": concat}) == concat
    assert symbology.label_text({"field": "name"}) == ["to-string", _NAME]
