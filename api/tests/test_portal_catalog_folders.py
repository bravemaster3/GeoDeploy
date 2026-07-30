"""The catalog's Folder facet: which folder each dataset reports.

`_folder_by_ref` turns the portal's V-13 layer tree into the "Folder" facet, so a visitor filters by
the same groups the author arranged in the editor rather than a second taxonomy. The rules worth
pinning are the edge cases: a layer at the ROOT must report no folder (so the facet narrows and never
becomes a required choice), and a NESTED layer must report its innermost folder, not the outermost.
"""
from geodeploy.services.portal_generator import _folder_by_ref

TREE = [
    {"name": "Basemaps", "children": [
        {"layer_type": "external", "layer_id": 1},
    ]},
    {"name": "Group 1", "children": [
        {"layer_type": "vector", "layer_id": 10},
        {"layer_type": "raster", "layer_id": 1},
        {"name": "Nested", "children": [
            {"layer_type": "vector", "layer_id": 11},
        ]},
    ]},
    {"layer_type": "vector", "layer_id": 99},   # root-level, deliberately outside any folder
]


def test_layer_reports_its_folder():
    out = _folder_by_ref(TREE)
    assert out[("vector", 10)] == "Group 1"
    assert out[("raster", 1)] == "Group 1"


def test_nested_layer_reports_the_innermost_folder():
    """A path ("Group 1 / Nested") reads badly ellipsised in a 216px rail, and the innermost name is
    what the author sees on the card in the editor."""
    assert _folder_by_ref(TREE)[("vector", 11)] == "Nested"


def test_root_layer_has_no_folder():
    assert ("vector", 99) not in _folder_by_ref(TREE)


def test_external_and_layer_sharing_an_id_are_distinct():
    """Both are id 1 on a fresh install. Keying by (kind, id) is what keeps them apart — the same
    collision that put an XYZ source in a raster's slot in the published layer switcher."""
    out = _folder_by_ref(TREE)
    assert out[("external", 1)] == "Basemaps"
    assert out[("raster", 1)] == "Group 1"


def test_empty_and_missing_trees_are_safe():
    assert _folder_by_ref([]) == {}
    assert _folder_by_ref(None) == {}


def test_unnamed_folder_does_not_become_a_facet_value():
    """A folder the author never named would otherwise contribute an empty-string facet value."""
    out = _folder_by_ref([{"name": "  ", "children": [{"layer_type": "vector", "layer_id": 5}]}])
    assert out == {}
