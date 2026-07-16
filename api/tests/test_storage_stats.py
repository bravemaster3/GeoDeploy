"""Storage-stats breakdown tests (2026-07-16 accuracy fix): the endpoint aggregates
per-store measurements; PG/S3 probes are monkeypatched — no infrastructure in tests."""
import json

from jose import jwt
from passlib.context import CryptContext

from geodeploy.config import get_settings
from geodeploy.models import Portal, RasterLayer, User, VectorLayer

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _auth(uid):
    token = jwt.encode({"sub": str(uid)}, get_settings().secret_key, algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


async def _seed(db):
    db.add(User(id=1, email="o@example.com", name="O", hashed_password=_pwd.hash("pw"),
                is_admin=True, role="owner"))
    db.add(VectorLayer(id=1, user_id=1, name="pg", table_name="t1", schema_name="geodeploy_u1",
                       status="ready", storage_backend="postgis"))
    db.add(VectorLayer(id=2, user_id=1, name="gpq", table_name="t2", schema_name="geodeploy_u1",
                       status="ready", storage_backend="geoparquet",
                       s3_key="vectors/1/abc/parts-dead"))
    db.add(RasterLayer(id=1, user_id=1, name="r", s3_key="rasters/1/abc/r.tif", status="ready"))
    db.add(Portal(id=1, user_id=1, title="P", slug="p", layer_configs=json.dumps([])))
    await db.commit()


async def test_storage_breakdown_aggregates(client, db, monkeypatch):
    await _seed(db)
    seen = {}

    async def fake_pg(layers):
        seen["pg_layers"] = [(l.schema_name, l.table_name) for l in layers]
        return 111

    def fake_s3(rasters, gpq):
        seen["raster_keys"] = [l.s3_key for l in rasters]
        seen["gpq_keys"] = [l.s3_key for l in gpq]
        return 222, 333

    monkeypatch.setattr("geodeploy.routers.admin._postgis_bytes", fake_pg)
    monkeypatch.setattr("geodeploy.routers.admin._s3_bytes", fake_s3)

    r = await client.get("/api/admin/storage-stats", headers=_auth(1))
    assert r.status_code == 200
    body = r.json()
    assert body["postgis_bytes"] == 111
    assert body["raster_bytes"] == 222
    assert body["geoparquet_bytes"] == 333
    assert body["portal_bundle_bytes"] >= 0
    assert body["used_bytes"] == 111 + 222 + 333 + body["portal_bundle_bytes"]
    assert body["vector_layers"] == 2 and body["raster_layers"] == 1 and body["portals"] == 1
    # only the postgis-backed layer goes to the PG probe; the geoparquet one goes to S3
    assert seen["pg_layers"] == [("geodeploy_u1", "t1")]
    assert seen["gpq_keys"] == ["vectors/1/abc/parts-dead"]
    assert seen["raster_keys"] == ["rasters/1/abc/r.tif"]


async def test_storage_unmeasurable_stores_are_null_not_zero(client, db, monkeypatch):
    await _seed(db)

    async def pg_down(layers):
        return None

    def s3_down(rasters, gpq):
        return None, None

    monkeypatch.setattr("geodeploy.routers.admin._postgis_bytes", pg_down)
    monkeypatch.setattr("geodeploy.routers.admin._s3_bytes", s3_down)

    r = await client.get("/api/admin/storage-stats", headers=_auth(1))
    body = r.json()
    assert body["postgis_bytes"] is None and body["raster_bytes"] is None
    assert body["geoparquet_bytes"] is None
    # total only sums what was measurable — never pretends unmeasured stores are empty
    assert body["used_bytes"] == body["portal_bundle_bytes"]
