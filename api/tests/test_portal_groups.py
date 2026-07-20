"""V-13 layer catalog: the nested folder tree drives draw order + is baked for the switcher, with a
reconcile step (no layer lost, no dangling node) and full back-compat (no tree → flat, like before)."""
import json
from types import SimpleNamespace

from jose import jwt
from passlib.context import CryptContext

from geodeploy.config import get_settings
from geodeploy.models import Portal, User
from geodeploy.services.portal_generator import (_flatten_layer_tree, _reconcile_layer_tree,
                                                 generate_style)

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _cfg(i):
    return {"layer_type": "vector", "layer_id": i, "style": {}, "opacity": 1.0, "visible": True}


def _gp_layer(i):
    return SimpleNamespace(id=i, name=f"l{i}", geometry_type="polygon", bbox=json.dumps([0, 0, 1, 1]),
                           storage_backend="geoparquet", s3_key=f"vectors/1/parts-{i}",
                           tile_status=None, pmtiles_key=None)


def _order(tree):
    return [(n["layer_type"], n["layer_id"]) for n in _flatten_layer_tree(tree)]


# ── Pure tree helpers ───────────────────────────────────────────────────────────────────────────

def test_flatten_is_depth_first_top_to_bottom():
    tree = [{"layer_type": "vector", "layer_id": 1},
            {"name": "G", "children": [{"layer_type": "vector", "layer_id": 2},
                                       {"name": "Sub", "children": [{"layer_type": "raster", "layer_id": 3}]}]}]
    assert _order(tree) == [("vector", 1), ("vector", 2), ("raster", 3)]


def test_reconcile_drops_dangling_and_appends_missing():
    tree = [{"name": "G", "children": [{"layer_type": "vector", "layer_id": 2},
                                       {"layer_type": "vector", "layer_id": 99}]}]  # 99 has no config
    configs = [_cfg(2), _cfg(5)]  # 5 isn't in the tree
    recon = _reconcile_layer_tree(tree, configs)
    assert _order(recon) == [("vector", 2), ("vector", 5)]  # 99 dropped, 5 appended at root


# ── generate_style ──────────────────────────────────────────────────────────────────────────────

def test_generate_style_uses_tree_order_and_bakes_it():
    out = generate_style([_cfg(1), _cfg(2)], [_gp_layer(1), _gp_layer(2)], [], [],
                         layer_groups=[{"name": "G", "children": [{"layer_type": "vector", "layer_id": 2}]},
                                       {"layer_type": "vector", "layer_id": 1}])
    assert out["layer_tree"] is not None
    assert _order(out["layer_tree"]) == [("vector", 2), ("vector", 1)]
    assert {d["layer_id"] for d in out["deck_layers"]} == {1, 2}  # both layers still rendered


def test_generate_style_without_tree_is_flat():
    out = generate_style([_cfg(1)], [_gp_layer(1)], [], [])
    assert out["layer_tree"] is None


# ── API round-trip ──────────────────────────────────────────────────────────────────────────────

async def test_layer_groups_roundtrips_via_put(client, db):
    db.add(User(id=1, email="e@x", name="E", hashed_password=_pwd.hash("pw"), role="editor"))
    db.add(Portal(id=5, user_id=1, title="P", slug="s", published=False,
                  layer_configs=json.dumps([_cfg(1), _cfg(2)])))
    await db.commit()
    h = {"Authorization": f"Bearer {jwt.encode({'sub': '1'}, get_settings().secret_key, algorithm='HS256')}"}
    tree = [{"id": "g1", "name": "G", "collapsed": True, "exclusive": False, "description": "d",
             "children": [{"layer_type": "vector", "layer_id": 1}]},
            {"layer_type": "vector", "layer_id": 2}]
    assert (await client.put("/api/portals/5", headers=h, json={"layer_groups": tree})).status_code == 200
    assert (await client.get("/api/portals/5", headers=h)).json()["layer_groups"] == tree
