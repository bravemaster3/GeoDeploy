"""Delete safety (A-05 follow-up): the usage endpoint lists portals that include a layer, and
deleting a layer prunes it from every portal's layer_configs (published ones are re-published)."""
import json

from jose import jwt
from passlib.context import CryptContext
from sqlalchemy import select

from geodeploy.config import get_settings
from geodeploy.models import Portal, User, VectorLayer

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _h(uid):
    return {"Authorization": f"Bearer {jwt.encode({'sub': str(uid)}, get_settings().secret_key, algorithm='HS256')}"}


async def _seed(db):
    db.add(User(id=1, email="e@x", name="E", hashed_password=_pwd.hash("pw"), is_admin=False, role="editor"))
    db.add(VectorLayer(id=4, user_id=1, name="roads", table_name="t", schema_name="s", status="ready",
                       storage_backend="postgis", visibility="organization"))
    db.add(Portal(id=7, user_id=1, title="My Portal", slug="abc", published=False,
                  layer_configs=json.dumps([{"layer_type": "vector", "layer_id": 4, "visible": True},
                                            {"layer_type": "vector", "layer_id": 9, "visible": True}])))
    await db.commit()


async def test_usage_lists_portals(client, db):
    await _seed(db)
    body = (await client.get("/api/data/vector/4/usage", headers=_h(1))).json()
    assert len(body) == 1 and body[0]["title"] == "My Portal" and body[0]["published"] is False
    # A layer no portal uses → empty.
    assert (await client.get("/api/data/vector/999/usage", headers=_h(1))).json() == []


async def test_delete_prunes_layer_from_portals(client, db):
    await _seed(db)
    assert (await client.delete("/api/data/vector/4", headers=_h(1))).status_code == 204
    db.expire_all()
    p = (await db.execute(select(Portal).where(Portal.id == 7))).scalar_one()
    ids = [(c["layer_type"], c["layer_id"]) for c in json.loads(p.layer_configs)]
    assert ("vector", 4) not in ids   # the deleted layer is pruned
    assert ("vector", 9) in ids       # the others are left alone
