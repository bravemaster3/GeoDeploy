"""The update preflight lists work that an update would interrupt — and only work that EXISTS.

Reported 2026-11-19: every press of Update warned "vector layer being processed — queued" three
times, for uploads whose layers had been deleted days earlier (they were queued while the worker was
crashing on start, and never ran). An upload job row outlives what it describes in three ways, and
each is pinned here: its layer is deleted, its layer finished without the row being updated, and a
re-run started a newer job beside it.
"""
from datetime import datetime, timedelta

from jose import jwt
from passlib.context import CryptContext
from sqlalchemy import select

from geodeploy.config import get_settings
from geodeploy.models import RasterLayer, UploadJob, User, VectorLayer

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
_T0 = datetime(2026, 11, 19, 8, 0, 0)


def _h(uid):
    return {"Authorization": f"Bearer {jwt.encode({'sub': str(uid)}, get_settings().secret_key, algorithm='HS256')}"}


async def _admin(db):
    db.add(User(id=1, email="a@x", name="A", hashed_password=_pwd.hash("pw"), is_admin=True, role="owner"))
    await db.commit()


def _vector(lid, status, name=None):
    return VectorLayer(id=lid, user_id=1, name=name or f"layer {lid}", table_name=f"t{lid}",
                       schema_name="s", status=status, storage_backend="postgis",
                       visibility="organization")


def _job(jid, lid, status="queued", minutes=0, kind="vector"):
    return UploadJob(id=jid, layer_id=lid, layer_type=kind, status=status,
                     created_at=_T0 + timedelta(minutes=minutes))


async def _busy(client):
    r = await client.get("/api/admin/update/preflight", headers=_h(1))
    assert r.status_code == 200
    return r.json()


async def test_jobs_of_deleted_layers_are_not_work_in_progress(client, db):
    """The reported case: three queued jobs, their layers gone."""
    await _admin(db)
    db.add_all([_job("j1", 101), _job("j2", 102), _job("j3", 103)])
    await db.commit()
    body = await _busy(client)
    assert body["busy"] == [] and body["count"] == 0 and body["blocking"] == 0


async def test_a_job_whose_layer_already_finished_is_not_counted(client, db):
    await _admin(db)
    db.add_all([_vector(5, "ready"), _vector(6, "error"), _job("j5", 5), _job("j6", 6, "processing")])
    await db.commit()
    assert (await _busy(client))["busy"] == []


async def test_only_the_latest_job_of_a_layer_counts(client, db):
    """A re-run starts a NEW job; the old queued row beside it is not a second piece of work."""
    await _admin(db)
    db.add_all([_vector(7, "processing", "roads"),
                _job("old", 7, "queued", minutes=0), _job("new", 7, "processing", minutes=5)])
    await db.commit()
    busy = (await _busy(client))["busy"]
    assert len(busy) == 1
    assert busy[0]["what"] == "“roads” being processed"
    assert busy[0]["recovers"] is False


async def test_a_superseded_job_does_not_count_even_when_it_is_the_only_queued_one(client, db):
    # The newest job is already done; the layer is still marked processing for another reason.
    await _admin(db)
    db.add_all([_vector(8, "processing"), _job("stale", 8, "queued", minutes=0),
                _job("done", 8, "ready", minutes=5)])
    await db.commit()
    assert (await _busy(client))["busy"] == []


async def test_real_work_in_progress_is_still_reported(client, db):
    """The point of the endpoint: a live upload must still be named, vector and raster alike."""
    await _admin(db)
    db.add_all([_vector(9, "processing", "parcels"), _job("v9", 9, "processing"),
                RasterLayer(id=3, user_id=1, name="DEM", s3_key="rasters/dem.tif",
                            status="processing", visibility="organization"),
                _job("r3", 3, "queued", kind="raster"),
                _job("gone", 404)])
    await db.commit()
    body = await _busy(client)
    whats = sorted(b["what"] for b in body["busy"])
    assert whats == ["“DEM” being processed", "“parcels” being processed"]
    assert body["blocking"] == 2


async def test_a_vector_and_a_raster_with_the_same_id_are_not_confused(client, db):
    await _admin(db)
    db.add_all([_vector(11, "ready"),
                RasterLayer(id=11, user_id=1, name="ortho", s3_key="rasters/o.tif",
                            status="processing", visibility="organization"),
                _job("v11", 11, "queued", kind="vector"), _job("r11", 11, "queued", kind="raster")])
    await db.commit()
    assert [b["what"] for b in (await _busy(client))["busy"]] == ["“ortho” being processed"]


async def test_deleting_a_layer_deletes_its_upload_jobs(client, db):
    """So the rows stop accumulating in the first place."""
    await _admin(db)
    db.add_all([_vector(12, "error"), _job("a", 12), _job("b", 12, "error", minutes=1),
                _vector(13, "ready"), _job("keep", 13, "ready")])
    await db.commit()
    assert (await client.delete("/api/data/vector/12", headers=_h(1))).status_code == 204
    db.expire_all()
    left = {j.id for j in (await db.execute(select(UploadJob))).scalars().all()}
    assert left == {"keep"}


async def test_deleting_a_raster_deletes_its_jobs_and_not_a_vectors(client, db):
    await _admin(db)
    db.add_all([RasterLayer(id=14, user_id=1, name="r", s3_key="rasters/r.tif", status="error",
                            visibility="organization"),
                _vector(14, "ready"),
                _job("r14", 14, "queued", kind="raster"), _job("v14", 14, "ready", kind="vector")])
    await db.commit()
    assert (await client.delete("/api/data/raster/14", headers=_h(1))).status_code == 204
    db.expire_all()
    left = {j.id for j in (await db.execute(select(UploadJob))).scalars().all()}
    assert left == {"v14"}
