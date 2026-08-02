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


# ── The reset must actually RUN, not only refuse ────────────────────────────────────────────────
# Every test above stops at a guard, which is why a crash on the FIRST line past the guards shipped
# and survived: reset_now read its config row by column NAME (state_db returns tuples unless
# row_factory is set) and raised TypeError before touching storage. The hourly reset therefore never
# worked once, on any demo, and the only symptom was a countdown reaching zero and nothing happening.
#
# These tests walk past the guards and assert the task REACHES storage — the part no guard covers.

def test_placeholder_snapshot_name_is_reported_clearly(demo_on):
    """The runbook's `<the backup name from step 3>` gets pasted into .env verbatim. Without this it
    fails several steps later as "NoSuchKey", which reads as a storage fault rather than a typo."""
    demo_on.geodeploy_demo_snapshot = "<the backup name from step 3>"
    out = demo_reset.reset_now()
    assert "placeholder" in out["error"]


def test_reset_reaches_the_snapshot_instead_of_crashing_on_its_own_config(demo_on, monkeypatch):
    """THE regression. Past the guards, with a destination configured, the task must get as far as
    reading the manifest. It previously raised TypeError on the config row and never got here.

    Asserting on the RETURNED error rather than mocking a success keeps the test about the one thing
    that broke: config loading. A raised exception fails this test; a handled 'snapshot unreadable'
    passes, because that means the task got all the way to storage.
    """
    demo_on.geodeploy_demo_snapshot = "geodeploy-backups/2026-08-02T00-00-00Z"

    cfg = _Cfg("demo-backup")
    monkeypatch.setattr("geodeploy.tasks.backup._load_cfg", lambda: cfg)

    from geodeploy.services import restore as rs

    def _boom(*a, **k):
        raise RuntimeError("NoSuchKey")

    monkeypatch.setattr(rs, "read_manifest", _boom)

    out = demo_reset.reset_now()          # must NOT raise
    assert "snapshot unreadable" in out["error"]


def test_reset_refuses_when_no_destination_is_configured(demo_on, monkeypatch):
    demo_on.geodeploy_demo_snapshot = "geodeploy-backups/2026-08-02T00-00-00Z"
    monkeypatch.setattr("geodeploy.tasks.backup._load_cfg", lambda: None)
    assert demo_reset.reset_now()["error"] == "no backup destination configured"


def test_reset_repairs_the_schema_the_way_a_restore_does(demo_on):
    """The demo reset calls restore_database DIRECTLY, so it inherits every consequence the restore
    task had to learn — and it runs unattended every hour, where nobody sees the result.

    Without re-applying migrations, an updated demo silently loses any column added after the seed
    was taken, at the top of every hour, with no API restart ever coming to put it back. Without
    clearing `running` rows it permanently answers "a backup is already running", again after every
    reset.
    """
    import inspect

    src = inspect.getsource(demo_reset.reset_now)
    assert "_reapply_schema_migrations" in src
    assert "_clear_restored_running_rows" in src
    assert src.index("restore_database") < src.index("_reapply_schema_migrations")
