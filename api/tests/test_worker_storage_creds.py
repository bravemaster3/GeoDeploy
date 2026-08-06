"""The worker and the API must resolve object storage THE SAME WAY.

This is the second time large uploads broke. The first cause was the CDN request-body cap (fixed by
chunking). The second was this: `tasks/raster_ingest._get_storage_creds` read `setup_config` FIRST,
and `pg_restore` replaces that row with the snapshot's — another instance's MinIO keys, or the same
keys encrypted under another instance's GEODEPLOY_SECRET_KEY.

The failure was shaped to mislead. The browser uploads to a presigned URL the API minted with the
LIVE credentials, so the upload reaches 100% with no error; the background convert then downloads
with the stale ones and the layer goes to `error`. "It uploads, then says error." Every ingest task
took those credentials, so a restore disabled the whole pipeline while the dashboard looked healthy.

These tests pin the ordering rather than the symptom: a divergence between two credential sources is
invisible until something restores a database, and by then the report is about uploads.
"""
import inspect

import pytest

from geodeploy.tasks import raster_ingest


@pytest.fixture
def no_runtime_file(monkeypatch):
    """No `runtime-storage.json` — so `storage_settings()` falls through to the environment."""
    from geodeploy import state_db
    monkeypatch.setattr(state_db, "runtime_storage", lambda: None)


def test_the_worker_uses_the_same_resolver_as_the_api():
    """`services.minio.storage_settings` is THE resolver — the runtime file the API republishes from
    what it just proved works, then the environment. A second implementation is how they drift.

    CODE only, with the docstring stripped: that docstring explains the bug and therefore names
    `setup_config` several paragraphs before the code touches it. A source grep that counts prose
    fails on the very explanation of what it prevents (the same trap caught a contract test in
    test_demo_reset_guards.py).
    """
    src = inspect.getsource(raster_ingest._get_storage_creds)
    code = src.split('"""')[2]          # everything after the docstring
    assert "storage_settings" in code
    # …and it must be consulted BEFORE the database row, which is the whole bug.
    assert code.index("storage_settings") < code.index("setup_config")


def test_live_credentials_win_over_a_restored_setup_row(monkeypatch):
    """THE regression. With a runtime file present, nothing may reach for `setup_config` — if it
    does, a restored instance signs S3 requests with the snapshot's keys."""
    from geodeploy import state_db

    monkeypatch.setattr(state_db, "runtime_storage", lambda: {
        "endpoint": "http://geodeploy-minio:9000", "bucket": "geodeploy",
        "access_key": "LIVEKEY", "secret_key": "live-secret", "region": "us-east-1"})

    def _no_db(*a, **k):        # reaching the database at all is the failure
        raise AssertionError("_get_storage_creds read setup_config despite live credentials")

    monkeypatch.setattr(state_db, "connect", _no_db)

    creds = raster_ingest._get_storage_creds()
    assert creds["access_key"] == "LIVEKEY"
    assert creds["secret_key"] == "live-secret"
    assert creds["bucket"] == "geodeploy"


def test_the_environment_is_used_when_there_is_no_runtime_file(no_runtime_file, monkeypatch):
    """A recreated container has the values from `.env` in its environment; that is still the
    instance's own configuration, and still better than the database copy."""
    from geodeploy import state_db
    from geodeploy.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "storage_access_key", "ENVKEY", raising=False)
    monkeypatch.setattr(settings, "storage_secret_key", "env-secret", raising=False)

    def _no_db(*a, **k):
        raise AssertionError("_get_storage_creds read setup_config despite an environment")

    monkeypatch.setattr(state_db, "connect", _no_db)

    assert raster_ingest._get_storage_creds()["access_key"] == "ENVKEY"


def test_every_ingest_task_goes_through_the_one_resolver():
    """The blast radius is the point: these all took the poisoned credentials together, so a restore
    turned off ingest, tiling, export and conversion at once. Whatever the resolver is, it must be
    the same one for all of them."""
    from geodeploy.tasks import (convert_upload, csv_import, export, geoparquet_import,
                                 geoparquet_prep)

    for mod in (convert_upload, csv_import, export, geoparquet_import, geoparquet_prep):
        assert "_get_storage_creds" in inspect.getsource(mod), (
            f"{mod.__name__} must not roll its own storage credentials")
