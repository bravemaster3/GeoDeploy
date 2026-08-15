"""A legend has to be usable as a legend AND as a source of truth for a renderer.

The QGIS plugin can only see a public layer anonymously, and `default_style` is on the
authenticated endpoints — so `/legend` is the only style it can read. Entries used to carry just
`{color, label}`, which meant rebuilding a graduated renderer required parsing numbers back out of
a display string containing an EN dash and `≥`. That re-derivation is exactly what this endpoint
exists to prevent.
"""
from geodeploy.services.symbology import legend_entries


def test_graduated_entries_carry_the_raw_bounds():
    style = {"color_mode": "graduated", "color_field": "pop",
             "classes": [{"min": None, "max": 10, "color": "#111111"},
                         {"min": 10, "max": 20.5, "color": "#222222"},
                         {"min": 20.5, "max": None, "color": "#333333"}]}
    entries = legend_entries(style)
    assert [e["min"] for e in entries] == [None, 10, 20.5]
    assert [e["max"] for e in entries] == [10, 20.5, None]
    # The formatted label is still there and still formatted.
    assert entries[0]["label"] == "< 10"
    assert entries[2]["label"] == "≥ 20.5"


def test_categorized_entries_carry_the_raw_value():
    style = {"color_mode": "categorized", "color_field": "kind",
             "categories": [{"value": "road", "color": "#111111"},
                            {"value": 3, "color": "#222222"}]}
    entries = legend_entries(style)
    assert entries[0]["value"] == "road"
    # An integer category must survive as an integer: str(3) would never match feature value 3.
    assert entries[1]["value"] == 3 and not isinstance(entries[1]["value"], str)


def test_other_is_distinguishable_from_a_null_category():
    """"Other" is the fallback for everything unlisted. A renderer must be able to tell it apart
    from a category whose value happens to be null, which QGIS renders very differently."""
    style = {"color_mode": "categorized", "color_field": "kind",
             "categories": [{"value": None, "color": "#111111"}]}
    entries = legend_entries(style)
    assert entries[-1]["other"] is True
    assert "other" not in entries[0]


def test_single_symbol_still_returns_nothing():
    """The endpoint substitutes a one-entry legend itself; this function must not start guessing."""
    assert legend_entries({"color": "#abcdef"}) == []
