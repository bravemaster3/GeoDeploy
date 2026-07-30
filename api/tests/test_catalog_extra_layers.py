"""Catalog scope "all public": public layers are baked in HIDDEN so every card can be shown on the map.

The critical invariant is the one that is easy to get wrong and invisible until someone publishes: a
baked-in layer must contribute NOTHING to the portal's opening extent. Without that, a catalog portal
opens on the union of every public layer on the instance instead of on the layers its author chose.

`generate_style` is a pure function, so these run without a database.
"""
import json
from types import SimpleNamespace

from geodeploy.services.portal_generator import generate_style


def _pg_layer(id, bbox):
    return SimpleNamespace(
        id=id, name=f"layer{id}", geometry_type="polygon", bbox=json.dumps(bbox),
        storage_backend="postgis", schema_name="gd", table_name=f"t{id}",
        s3_key=None, tile_status=None, pmtiles_key=None, visibility="public",
        abstract=None, license=None, attribution=None, keywords=None, is_public=True,
        crs="EPSG:4326", feature_count=1, default_style=None, uid=f"u{id}",
    )


def _cfg(id, *, extra=False, visible=True):
    c = {"layer_type": "vector", "layer_id": id, "style": {}, "opacity": 1.0, "visible": visible}
    if extra:
        c["_catalog_extra"] = True
    return c


def test_baked_layer_does_not_move_the_opening_extent():
    """THE regression this file exists for. The author's layer covers [0,0,10,10]; a public layer
    baked in for the catalog sits far away. The portal must still open on [0,0,10,10]."""
    mine = _pg_layer(1, [0, 0, 10, 10])
    theirs = _pg_layer(2, [100, 60, 120, 80])
    out = generate_style([_cfg(1), _cfg(2, extra=True, visible=False)], [mine, theirs], [], [])
    assert out["bounds"] == [0, 0, 10, 10]


def test_baked_layer_is_still_on_the_map():
    """It must be present — that is the whole point: the card's "Show on map" toggles a real layer."""
    mine = _pg_layer(1, [0, 0, 10, 10])
    theirs = _pg_layer(2, [100, 60, 120, 80])
    out = generate_style([_cfg(1), _cfg(2, extra=True, visible=False)], [mine, theirs], [], [])
    ids = {l["metadata"]["geodeploy:layer_id"] for l in out["layers"] if l.get("metadata", {}).get("geodeploy:name")}
    assert ids == {1, 2}


def test_baked_layer_starts_hidden():
    """Nothing is fetched until a visitor asks for it."""
    mine = _pg_layer(1, [0, 0, 10, 10])
    theirs = _pg_layer(2, [100, 60, 120, 80])
    out = generate_style([_cfg(1), _cfg(2, extra=True, visible=False)], [mine, theirs], [], [])
    by_id = {l["metadata"]["geodeploy:layer_id"]: l for l in out["layers"]
             if l.get("metadata", {}).get("geodeploy:name")}
    assert by_id[2].get("layout", {}).get("visibility") == "none"
    assert by_id[1].get("layout", {}).get("visibility") != "none"


def test_baked_layer_appears_in_the_catalog_records():
    """It must be listed, or the portal shows a layer with no card explaining it."""
    mine = _pg_layer(1, [0, 0, 10, 10])
    theirs = _pg_layer(2, [100, 60, 120, 80])
    out = generate_style([_cfg(1), _cfg(2, extra=True, visible=False)], [mine, theirs], [], [])
    assert {i["name"] for i in out["layers_info"]} == {"layer1", "layer2"}


def test_a_portal_without_extras_is_unchanged():
    """Ordinary portals must not notice this feature at all."""
    mine = _pg_layer(1, [0, 0, 10, 10])
    out = generate_style([_cfg(1)], [mine], [], [])
    assert out["bounds"] == [0, 0, 10, 10]
    assert len(out["layers_info"]) == 1


def test_extras_only_still_yields_no_extent():
    """Every layer baked in and none chosen → no extent to open on, rather than a wrong one.
    `valid_bounds` returns None there and the runtime falls back to its default view."""
    theirs = _pg_layer(2, [100, 60, 120, 80])
    out = generate_style([_cfg(2, extra=True, visible=False)], [theirs], [], [])
    assert out["bounds"] is None
