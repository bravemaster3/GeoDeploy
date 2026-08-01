"""The demo reset's refusals.

This is the only code in GeoDeploy that deletes user data without anyone asking, so what it REFUSES
to do matters more than what it does. Each guard here corresponds to a way the reset could destroy
something it cannot get back.
"""
import pytest

from geodeploy.config import get_settings
from geodeploy.tasks import demo_reset


@pytest.fixture
def demo_on():
    s = get_settings()
    before = s.geodeploy_demo_mode
    s.geodeploy_demo_mode = True
    yield s
    s.geodeploy_demo_mode = before


class _Cfg:
    def __init__(self, bucket, endpoint="http://minio:9000"):
        self.backup_bucket = bucket
        self.backup_endpoint = endpoint
        self.backup_access_key = "k"
        self.backup_secret_key = "s"
        self.backup_region = "us-east-1"


def test_nothing_runs_on_a_normal_instance():
    """Every entry point checks the flag. A stray call on a real install must be inert."""
    assert demo_reset.tick()["skipped"] == "not a demo"
    assert demo_reset.reset_now()["skipped"] == "not a demo"
    assert demo_reset._sweep_orphans(_Cfg("anything"), "k")["skipped"] == "not a demo"


def test_reset_refuses_without_a_named_snapshot(demo_on):
    """Restoring 'the latest backup' on a demo would restore whatever visitors last did to it."""
    demo_on.geodeploy_demo_snapshot = ""
    assert "error" in demo_reset.reset_now()


def test_sweep_refuses_when_backup_shares_the_data_bucket(demo_on):
    """THE one that would eat its own seed: the snapshot's keys are not under its own objects/
    prefix, so a shared bucket means the sweep deletes the backup the next reset needs."""
    # No S3 stub needed: the guard is a CONFIGURATION check and fires before any client is built,
    # which is the point — refusing a dangerous setup must not depend on storage being reachable.
    same = get_settings().storage_bucket or "geodeploy"
    out = demo_reset._sweep_orphans(_Cfg(same), "snap")
    assert "error" in out
    assert "bucket" in out["error"]


class _EmptyStore:
    """Storage that lists nothing, and would RAISE if asked to delete — so the test proves the guard
    stops before deletion rather than merely that it returns the right string."""
    def get_paginator(self, _):
        class P:
            def paginate(self, **kw):
                return [{"Contents": []}]
        return P()

    def delete_objects(self, **kw):  # pragma: no cover — reaching this is the failure
        raise AssertionError("sweep attempted a delete despite an empty snapshot")


def test_sweep_refuses_an_empty_snapshot(demo_on, monkeypatch):
    """An empty snapshot would mean 'delete everything you have'. This check necessarily runs after
    the clients exist — it has to read the snapshot to know it is empty — so storage is stubbed."""
    import geodeploy.services.backup as bk
    import geodeploy.services.minio as mn
    monkeypatch.setattr(bk, "make_client", lambda *a, **k: _EmptyStore())
    monkeypatch.setattr(mn, "get_s3_client", lambda *a, **k: _EmptyStore())

    out = demo_reset._sweep_orphans(_Cfg("separate-backup-bucket"), "snap")
    assert out["skipped"] == "snapshot had no objects"
