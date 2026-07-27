"""F5 write-back endpoints — the validation/authorization paths (which return before any Celery/PostGIS
work, so they're testable without the worker or a database engine)."""
from jose import jwt
from passlib.context import CryptContext

from geodeploy.config import get_settings
from geodeploy.models import User, VectorLayer

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _auth(uid=1):
    return {"Authorization": f"Bearer {jwt.encode({'sub': str(uid)}, get_settings().secret_key, algorithm='HS256')}"}


async def _user(db, uid=1, role="editor"):
    db.add(User(id=uid, email=f"u{uid}@x", name=f"U{uid}", hashed_password=_pwd.hash("pw"), role=role))


async def _layer(db, lid, uid, backend="postgis", status="ready"):
    db.add(VectorLayer(id=lid, user_id=uid, name=f"L{lid}", table_name=f"t{lid}",
                       schema_name=f"geodeploy_u{uid}", storage_backend=backend, status=status))


async def test_list_editable_layers_only_postgis_ready(client, db):
    await _user(db, 1)
    await _layer(db, 10, 1, backend="postgis", status="ready")
    await _layer(db, 11, 1, backend="geoparquet", status="ready")   # file-backed → excluded
    await _layer(db, 12, 1, backend="postgis", status="processing")  # not ready → excluded
    await db.commit()
    r = await client.get("/api/interop/geodeploy/layers", headers=_auth(1))
    assert r.status_code == 200
    ids = {row["id"] for row in r.json()}
    assert ids == {10}


async def test_read_features_404_for_missing(client, db):
    await _user(db, 1)
    await db.commit()
    r = await client.get("/api/interop/geodeploy/layers/999/features.geojson", headers=_auth(1))
    assert r.status_code == 404


async def test_writeback_404_for_geoparquet(client, db):
    await _user(db, 1)
    await _layer(db, 20, 1, backend="geoparquet")
    await db.commit()
    r = await client.put("/api/interop/geodeploy/layers/20/features", headers=_auth(1),
                         json={"type": "FeatureCollection", "features": [{"x": 1}]})
    assert r.status_code == 404   # not an editable PostGIS layer


async def test_writeback_403_for_non_owner(client, db):
    await _user(db, 1, role="editor")
    await _user(db, 2, role="editor")
    await _layer(db, 30, 2, backend="postgis")   # owned by user 2
    await db.commit()
    r = await client.put("/api/interop/geodeploy/layers/30/features", headers=_auth(1),
                         json={"type": "FeatureCollection", "features": [{"x": 1}]})
    assert r.status_code == 403


async def test_writeback_400_for_non_featurecollection(client, db):
    await _user(db, 1)
    await _layer(db, 40, 1, backend="postgis")
    await db.commit()
    r = await client.put("/api/interop/geodeploy/layers/40/features", headers=_auth(1),
                         json={"type": "Nonsense"})
    assert r.status_code == 400


async def test_writeback_400_for_empty_features(client, db):
    await _user(db, 1)
    await _layer(db, 41, 1, backend="postgis")
    await db.commit()
    r = await client.put("/api/interop/geodeploy/layers/41/features", headers=_auth(1),
                         json={"type": "FeatureCollection", "features": []})
    assert r.status_code == 400
