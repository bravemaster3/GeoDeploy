"""A real dump, restored by the real code path, asserting the thing that actually broke.

`test_restore_keeps_extension.py` covers the TOC filter as a pure function. This is the other half:
take a dump made the way `services/backup.py` makes them — including the `CREATE EXTENSION postgis`
every such dump carries — restore it with `restore_database()`, and check that

  1. the data comes back, and
  2. the `geometry` type is the SAME OBJECT afterwards, and
  3. a spatial query still runs.

(2) is the invariant. Before the fix, `--clean` dropped the extension successfully (tables go first,
so nothing depends on it by then) and `CREATE EXTENSION` rebuilt it with a new OID, stranding every
connection that was open across the restore. This test would have failed on that.

It also answers the question a user asks after a fix like this: do the backups I already have still
restore? They are ordinary `pg_dump -Fc` archives and nothing about the backup side changed, so the
dump built here is the same shape as one taken months ago.
"""
import os
import subprocess

import pytest

from geodeploy.config import get_settings
from geodeploy.services.restore import restore_database

def _have(binary: str) -> bool:
    """`which` is not universally present in a slim image, so ask the binary itself."""
    try:
        return subprocess.run([binary, "--version"], capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


# CI runs on a runner that carries the postgres client tools, so these DO run there. A local image
# built without postgresql-client skips them rather than failing — but a silent skip is how a
# regression test stops testing anything, so the reason names the missing tool.
_MISSING = [b for b in ("pg_dump", "pg_restore", "psql") if not _have(b)]
pytestmark = pytest.mark.skipif(
    bool(_MISSING), reason=f"postgres client tools not on PATH: {', '.join(_MISSING)}")

TABLE = "gd_restore_roundtrip"


def _psql(sql: str) -> str:
    s = get_settings()
    env = dict(os.environ, PGPASSWORD=s.postgis_password or "")
    proc = subprocess.run(
        ["psql", "-h", s.postgis_host or "postgres", "-p", str(s.postgis_port or 5432),
         "-U", s.postgis_user or "geodeploy", "-d", s.postgis_db or "geodeploy",
         "-tAc", sql],
        env=env, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, f"psql failed: {proc.stderr.strip()}\nSQL: {sql}"
    return proc.stdout.strip()


def _dump(path: str) -> None:
    s = get_settings()
    env = dict(os.environ, PGPASSWORD=s.postgis_password or "")
    proc = subprocess.run(
        ["pg_dump", "-h", s.postgis_host or "postgres", "-p", str(s.postgis_port or 5432),
         "-U", s.postgis_user or "geodeploy", "-d", s.postgis_db or "geodeploy",
         "-Fc", "--no-owner", "--no-acl", "-f", path],
        env=env, capture_output=True, text=True, timeout=600)
    assert proc.returncode == 0, f"pg_dump failed: {proc.stderr.strip()}"


@pytest.fixture
def spatial_table():
    _psql("CREATE EXTENSION IF NOT EXISTS postgis")
    _psql(f"DROP TABLE IF EXISTS {TABLE}")
    _psql(f"CREATE TABLE {TABLE} (id serial PRIMARY KEY, name text, geom geometry(Point, 4326))")
    _psql(f"CREATE INDEX {TABLE}_geom_idx ON {TABLE} USING gist (geom)")
    _psql(f"INSERT INTO {TABLE} (name, geom) VALUES "
          "('ankara', ST_SetSRID(ST_MakePoint(32.85, 39.93), 4326)), "
          "('athens', ST_SetSRID(ST_MakePoint(23.73, 37.98), 4326)), "
          "('oslo',   ST_SetSRID(ST_MakePoint(10.75, 59.91), 4326))")
    yield
    _psql(f"DROP TABLE IF EXISTS {TABLE}")


def _geometry_oid() -> str:
    return _psql("SELECT oid FROM pg_type WHERE typname = 'geometry'")


def test_a_restore_keeps_the_geometry_type_the_same_object(tmp_path, spatial_table):
    """THE regression. A new OID here means every connection open across the restore is stranded."""
    dump = str(tmp_path / "roundtrip.dump")
    _dump(dump)
    before = _geometry_oid()

    _psql(f"DELETE FROM {TABLE} WHERE name = 'oslo'")
    restore_database(dump)

    assert _geometry_oid() == before, (
        "the PostGIS extension was rebuilt by the restore; every session open across it now holds "
        "an operator cache pointing at objects that no longer exist"
    )


def test_the_data_comes_back(tmp_path, spatial_table):
    """A restore that protects the extension and loses the rows would be a worse bug."""
    dump = str(tmp_path / "roundtrip.dump")
    _dump(dump)

    _psql(f"DELETE FROM {TABLE}")
    assert _psql(f"SELECT count(*) FROM {TABLE}") == "0"

    restore_database(dump)
    assert _psql(f"SELECT count(*) FROM {TABLE}") == "3"
    assert _psql(f"SELECT name FROM {TABLE} WHERE name = 'oslo'") == "oslo"


def test_spatial_queries_still_run_after_a_restore(tmp_path, spatial_table):
    """The symptom as the user meets it: `COUNT(*)` keeps working while anything touching a spatial
    operator fails. Both are asserted, because only the second one ever broke."""
    dump = str(tmp_path / "roundtrip.dump")
    _dump(dump)
    restore_database(dump)

    assert _psql(f"SELECT count(*) FROM {TABLE}") == "3"

    box = "ST_MakeEnvelope(20, 35, 45, 45, 4326)"
    assert _psql(f"SELECT count(*) FROM {TABLE} WHERE geom && {box}") == "2"
    assert _psql(f"SELECT count(*) FROM {TABLE} "
                 f"WHERE geom && {box} AND ST_Intersects(geom, {box})") == "2"


def test_the_index_is_still_bound_to_a_live_operator_family(tmp_path, spatial_table):
    """The error named an opfamily and a type. After a restore they must still resolve to each
    other, which is what `no spatial operator found` means when they do not."""
    dump = str(tmp_path / "roundtrip.dump")
    _dump(dump)
    restore_database(dump)

    family = _psql(
        "SELECT o.opcfamily FROM pg_index i JOIN pg_opclass o ON o.oid = i.indclass[0] "
        f"WHERE i.indrelid = '{TABLE}'::regclass AND o.opcname LIKE 'gist_geometry%'")
    assert family, "the spatial index did not survive the restore"
    assert _psql(f"SELECT count(*) FROM pg_amop WHERE amopfamily = {family}") != "0", (
        "the operator family survived as an empty shell — the index exists but no operator resolves"
    )
