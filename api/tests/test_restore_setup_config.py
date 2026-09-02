"""A restore must leave `setup_config` as one row with its primary key.

THE RACE. `pg_restore --clean` drops `setup_config` and recreates it EMPTY, while the API keeps
serving — that is what an in-place restore means. The first request to read the instance's
configuration finds no `id = 1` and `routers/setup._get_or_create_config` inserts a fresh default
row into the gap. `pg_restore` then COPYs the snapshot's row on top, and its closing

    ALTER TABLE ONLY public.setup_config ADD CONSTRAINT setup_config_pkey PRIMARY KEY (id)

fails with `Key (id)=(1) is duplicated`. The table is then left with two rows and NO primary key,
permanently, and every later restore can add another.

Which row the API reads decides whether it believes it has a database. Observed in production as
"GeoDeploy cannot reach its database" on an instance whose database was answering perfectly: the
blank row won, and a blank row names no host.

These tests drive `_repair_setup_config()` against a real table, because the bug is entirely about
what SQL leaves behind and a mock of the database would have asserted my own assumptions back at me.
"""
import subprocess

import pytest

from geodeploy import state_db
from geodeploy.tasks.restore import _repair_setup_config


def _have(binary: str) -> bool:
    try:
        return subprocess.run([binary, "--version"], capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


pytestmark = pytest.mark.skipif(not _have("psql"), reason="psql not on PATH")


def _rows():
    with state_db.connect() as conn:
        conn.row_factory = state_db.dict_row
        return conn.execute(
            "SELECT id, postgis_host, postgis_db FROM setup_config ORDER BY ctid").fetchall()


def _has_pkey() -> bool:
    with state_db.connect() as conn:
        return bool(conn.execute(
            "SELECT 1 FROM pg_constraint WHERE conname = 'setup_config_pkey'").fetchone())


#: `setup_config` carries NOT NULL columns whose defaults live in the ORM, not the DDL, so a raw
#: INSERT has to supply them. Reading them from the catalog rather than listing them keeps this
#: test working when a release adds another one — which it will.
def _insert_row(**values):
    with state_db.connect() as conn:
        conn.row_factory = state_db.dict_row
        cols = conn.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name = 'setup_config' AND is_nullable = 'NO' "
            "AND column_default IS NULL").fetchall()
        zero = {"boolean": False, "integer": 0, "bigint": 0, "double precision": 0.0}
        row = {c["column_name"]: zero.get(c["data_type"], "") for c in cols}
        row.update(values)
        names = ", ".join(row)
        holes = ", ".join(["?"] * len(row))
        conn.execute(f"INSERT INTO setup_config ({names}) VALUES ({holes})", tuple(row.values()))


def _reset_table():
    with state_db.connect() as conn:
        conn.execute("ALTER TABLE setup_config DROP CONSTRAINT IF EXISTS setup_config_pkey")
        conn.execute("DELETE FROM setup_config")


@pytest.fixture
def wrecked_setup_config():
    """Exactly the state a restore leaves: the snapshot's row, a blank one the live API inserted,
    and no primary key because ADD CONSTRAINT could not run over the duplicate."""
    _reset_table()
    # the blank row the API creates when it finds the table empty mid-restore: every field
    # defaulted, which is what makes it recognisable
    _insert_row(id=1, completed=False, postgis_port=5432)
    # the snapshot's real row, COPYed in afterwards
    _insert_row(id=1, completed=True, postgis_type="local", postgis_host="geodeploy-postgres",
                postgis_port=5432, postgis_db="geodeploy", postgis_user="geodeploy")
    yield
    _reset_table()
    _insert_row(id=1, completed=True, postgis_host="geodeploy-postgres", postgis_db="geodeploy")
    with state_db.connect() as conn:
        conn.execute("ALTER TABLE setup_config ADD CONSTRAINT setup_config_pkey PRIMARY KEY (id)")


def test_the_duplicate_is_collapsed_to_one_row(wrecked_setup_config):
    assert len(_rows()) == 2, "fixture did not reproduce the duplicate"
    _repair_setup_config()
    assert len(_rows()) == 1


def test_the_row_that_survives_is_the_one_naming_a_database(wrecked_setup_config):
    """THE symptom. Keeping the blank row is what made a healthy instance report that it could not
    reach its database — a row with no host names nothing to connect to."""
    _repair_setup_config()
    row = _rows()[0]
    assert row["postgis_host"] == "geodeploy-postgres"
    assert row["postgis_db"] == "geodeploy"


def test_the_primary_key_comes_back(wrecked_setup_config):
    """Without it every later restore can add another duplicate, so the damage compounds."""
    assert not _has_pkey(), "fixture did not reproduce the missing key"
    _repair_setup_config()
    assert _has_pkey()


def test_it_is_a_no_op_on_a_healthy_table(wrecked_setup_config):
    """It runs after EVERY restore, and the overwhelmingly common case is nothing to fix. Repairing
    twice must therefore change nothing the second time."""
    _repair_setup_config()
    before = _rows()
    out = _repair_setup_config()
    assert _rows() == before
    assert _has_pkey()
    assert not out["failed"], out


def test_an_empty_table_does_not_raise(wrecked_setup_config):
    """`--clean` truly emptied it and nothing has written yet — repairing must not be the thing
    that fails the restore."""
    _reset_table()
    out = _repair_setup_config()
    assert not out["failed"], out
    assert _rows() == []


def test_three_rows_are_collapsed_too(wrecked_setup_config):
    """The table has no key while it is broken, so a second restore adds a third row. The repair
    must handle the compounded case, not only the first one."""
    _insert_row(id=1, completed=False, postgis_port=5432)
    assert len(_rows()) == 3
    _repair_setup_config()
    assert len(_rows()) == 1
    assert _rows()[0]["postgis_host"] == "geodeploy-postgres"
