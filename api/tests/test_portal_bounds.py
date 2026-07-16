"""Deck-only portals open on the manifest CORE extent (anti-flash bake).

For a portal whose only user layers are deck.gl GeoParquet overlays with no admin-pinned view,
`generate_style` bakes the merged manifest *core* extent (percentile core of the data) into
`geodeploy.bounds` and sets `core_fitted`, so the published map opens there directly instead of
fitting the full extent and snapping to the core once the manifest loads. These pin that decision;
the manifest S3 read itself (`read_deck_core_bbox`) is best-effort and covered by the fallback case.
"""
import json
from types import SimpleNamespace

from geodeploy.services.portal_generator import generate_style


def _gp_layer(id, bbox, *, tiled=False):
    """A GeoParquet vector layer. tiled=True → PMTiles-ready (a MapLibre layer, NOT a deck layer)."""
    return SimpleNamespace(
        id=id, name=f"layer{id}", geometry_type="polygon", bbox=json.dumps(bbox),
        storage_backend="geoparquet", s3_key=f"vectors/1/parts-abc{id}",
        tile_status="ready" if tiled else None, pmtiles_key=f"k{id}" if tiled else None,
    )


def _cfg(id):
    return {"layer_type": "vector", "layer_id": id, "style": {}, "opacity": 1.0, "visible": True}


def test_deck_only_bakes_core_extent():
    layer = _gp_layer(1, [0, 0, 10, 10])
    out = generate_style([_cfg(1)], [layer], [], [], deck_core_bounds={1: [2, 2, 8, 8]})
    assert out["layers"] == []                # deck overlay emits no MapLibre layer
    assert len(out["deck_layers"]) == 1
    assert out["core_fitted"] is True
    assert out["bounds"] == [2, 2, 8, 8]      # opened on the CORE, not the full [0,0,10,10]


def test_deck_only_without_manifest_falls_back_to_full():
    layer = _gp_layer(1, [0, 0, 10, 10])
    out = generate_style([_cfg(1)], [layer], [], [], deck_core_bounds={})  # no core available
    assert out["core_fitted"] is False
    assert out["bounds"] == [0, 0, 10, 10]    # full extent — today's behaviour, no regression


def test_mixed_portal_keeps_full_extent():
    # A MapLibre layer present (PMTiles-tiled) → NOT deck-only, so no core substitution: the map must
    # still frame the tiled layer. Mirrors portal.js, which only refits when there are no MapLibre layers.
    deck = _gp_layer(1, [0, 0, 10, 10])
    tiled = _gp_layer(2, [20, 20, 30, 30], tiled=True)
    out = generate_style([_cfg(1), _cfg(2)], [deck, tiled], [], [], deck_core_bounds={1: [2, 2, 8, 8]})
    assert len(out["layers"]) == 1            # the tiled layer is a MapLibre layer
    assert out["core_fitted"] is False
    assert out["bounds"] == [0, 0, 30, 30]    # full merged extent of both layers
