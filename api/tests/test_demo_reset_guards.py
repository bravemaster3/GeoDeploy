"""The demo reset's refusals.

This is the only code in GeoDeploy that deletes user data without anyone asking, so what it REFUSES
to do matters more than what it does. Each guard here corresponds to a way the reset could destroy
something it cannot get back.
"""
import pytest

from geodeploy.config import get_settings
from geodeploy.tasks import demo_reset

NL = chr(10)


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


def test_reset_uses_THE_shared_restore_implementation(demo_on):
    """The demo reset must not carry its own copy of "put this snapshot back".

    Every demo-reset bug so far came from that duplication: it fetched `database.dump` when the file
    is `postgis.dump`; it passed the composed path as the `key` argument, so the request became
    `<key>/database.dump/database.dump` and 404'd at the top of every hour; it never restored portal
    assets; and it restored the database BEFORE the objects, the ordering restore.py's own header
    explains is wrong.

    Asserting on the CALL rather than on behaviour is deliberate — the failure mode is divergence,
    and only one implementation can prevent it.
    """
    import inspect

    from geodeploy.tasks import restore as rt

    # CODE only. The docstring and comments name the old wrong filename on purpose, to explain the
    # bug — and a source grep that counts prose would fail on the very explanation of what it
    # prevents. (This bit an earlier contract test the same way.)
    src = inspect.getsource(demo_reset.reset_now)
    code = NL.join(l.split("#")[0] for l in src.splitlines() if not l.strip().startswith("#"))
    assert "restore_snapshot" in code, "the demo reset must delegate, not re-implement"
    assert "database.dump" not in code, "the dump is postgis.dump; this name never existed"

    # The repairs live INSIDE the shared function, so neither caller can forget them.
    shared = inspect.getsource(rt.restore_snapshot)
    assert "_reapply_schema_migrations" in shared
    assert "_clear_restored_running_rows" in shared
    assert shared.index("restore_database") < shared.index("_reapply_schema_migrations")
    # Objects before the database — a failure between the steps must not leave the catalog
    # advertising files that are not there yet.
    assert shared.index("restore_objects") < shared.index("restore_database")


def test_the_shared_restore_rebuilds_derived_state_too(demo_on):
    """Martin's config and the portal BUNDLES must be rebuilt by the shared function, not by the
    restore task around it.

    They were not, and only the demo felt it. Deleting a portal `rmtree`s `data/portals/<slug>`;
    bundles are derived data and deliberately absent from a backup; so the hourly reset brought the
    portal ROW back with nothing behind it. The portal was listed, said "published", and opened BLANK
    until somebody pressed Publish by hand — on the one instance where nobody can.

    Blank, not 404: nginx serves portals with `try_files $uri $uri/index.html @spa`, so a missing
    bundle falls through to the dashboard SPA (which has no route for a public portal URL). 200, an
    empty page, and no error anywhere — the hardest possible symptom to trace back to a missing file.

    `run_restore` had both steps and looked correct. The demo reset calls `restore_snapshot`
    directly, which is exactly the divergence the test above exists to prevent, one level down.
    """
    import inspect

    from geodeploy.tasks import restore as rt

    shared = inspect.getsource(rt.restore_snapshot)
    assert "_republish_portals" in shared, "a restored portal with no bundle is a 404"
    assert "_regenerate_martin" in shared, "the restored catalog names tables Martin does not know"
    # After the database: both read rows that only exist once the snapshot is back.
    assert shared.index("restore_database") < shared.index("_regenerate_martin")
    assert shared.index("_regenerate_martin") < shared.index("_republish_portals")

    # …and the task must NOT keep its own copy — two callers, one implementation.
    task = inspect.getsource(rt.run_restore)
    code = NL.join(l.split("#")[0] for l in task.splitlines() if not l.strip().startswith("#"))
    assert "_republish_portals" not in code


def test_a_failed_martin_reload_does_not_skip_the_republish(demo_on):
    """They used to share one `try`, so a Martin failure — the recoverable one, fixed from Settings —
    silently skipped the portal republish, which is the one that leaves 404s behind."""
    import inspect

    from geodeploy.tasks import restore as rt

    src = inspect.getsource(rt.restore_snapshot)
    martin_at = src.index("_regenerate_martin()")
    republish_at = src.index("_republish_portals()")
    # An `except` between them is the proof: the Martin call's handler closes before the republish.
    assert "except" in src[martin_at:republish_at]


def test_the_restore_keeps_this_instances_own_storage_credentials(demo_on):
    """setup_config comes back as the SNAPSHOT'S row — including storage keys encrypted under
    another instance's secret. `.env` is what the containers actually run on, so the live values
    win. Without this the Settings panel showed keys matching nothing (GitHub issue #2) and
    `tasks/raster_ingest._get_storage_creds`, which reads these columns FIRST, signed with them."""
    import inspect

    from geodeploy.tasks import restore as rt

    shared = inspect.getsource(rt.restore_snapshot)
    assert "_restore_own_storage_settings" in shared
    assert shared.index("restore_database") < shared.index("_restore_own_storage_settings")
    # Before the repairs that READ setup_config, for the same reason the DB block is.
    assert shared.index("_restore_own_storage_settings") < shared.index("_reapply_schema_migrations")
    assert "storage_secret_key" in rt.OWN_STORAGE_FIELDS


def test_the_shared_restore_downloads_the_file_the_backup_actually_writes(demo_on):
    """tasks/backup.py uploads `<key>/postgis.dump`. A mismatch here is a 404 that only appears in
    production, since no test writes a real snapshot."""
    import inspect

    from geodeploy.tasks import backup as bt, restore as rt

    assert "postgis.dump" in inspect.getsource(bt.run_backup)
    assert "postgis.dump" in inspect.getsource(rt.restore_snapshot)
