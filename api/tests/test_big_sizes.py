"""Byte counts must be BIGINT.

`backup_runs.size_bytes` was `Integer` — PostgreSQL `int4`, ceiling 2_147_483_647 (2.1 GB). A
whole-instance backup passes that on the first real deployment, and the failure was cruel: the
overflow happened on the LAST write of the task, after the dump, the object copy, the manifest and
retention had all succeeded. A complete, restorable backup was recorded as "integer out of range".

`vector_layers.file_size` and `raster_layers.file_size` had the same ceiling, one 2.1 GB raster away
from the same bug.

These tests write a value above int4 and read it back, which is the only way to prove the column is
actually int8 in the live database rather than merely declared BigInteger in the model — an existing
install only gets there via the migration in main.py::_PG_MIGRATIONS.
"""
import pytest
from sqlalchemy import select

from geodeploy.models import BackupRun, RasterLayer, User, VectorLayer

OVER_INT4 = 3_221_225_472       # 3 GB — comfortably past 2_147_483_647


@pytest.mark.asyncio
async def test_backup_size_bytes_holds_more_than_int4(db):
    run = BackupRun(key="geodeploy-backups/2026-08-02T00-00-00Z", status="success",
                    trigger="manual", size_bytes=OVER_INT4, progress=100)
    db.add(run)
    await db.commit()
    await db.refresh(run)
    assert run.size_bytes == OVER_INT4


@pytest.mark.asyncio
async def test_vector_layer_file_size_holds_more_than_int4(db):
    user = User(email="big@example.com", name="Big", hashed_password="x", role="admin")
    db.add(user)
    await db.commit()
    await db.refresh(user)

    layer = VectorLayer(user_id=user.id, name="huge", table_name="huge_t", schema_name="public",
                        file_size=OVER_INT4, status="ready")
    db.add(layer)
    await db.commit()
    await db.refresh(layer)
    assert layer.file_size == OVER_INT4


@pytest.mark.asyncio
async def test_raster_layer_file_size_holds_more_than_int4(db):
    user = User(email="bigras@example.com", name="Big", hashed_password="x", role="admin")
    db.add(user)
    await db.commit()
    await db.refresh(user)

    layer = RasterLayer(user_id=user.id, name="huge-cog", s3_key="rasters/huge.tif",
                        file_size=OVER_INT4, status="ready")
    db.add(layer)
    await db.commit()
    await db.refresh(layer)

    got = (await db.execute(
        select(RasterLayer).where(RasterLayer.id == layer.id))).scalar_one()
    assert got.file_size == OVER_INT4
