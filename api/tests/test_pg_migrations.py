"""One failing migration must not take the others down with it.

This shipped. `_apply_pg_migrations` wrapped each statement in `try/except` and its docstring said
each one "runs independently", but they all shared ONE transaction — and Postgres aborts the whole
transaction on the first failed statement, so every later statement fails with "current transaction
is aborted" and the except swallowed that too, in silence.

The trigger was a single statement missing `IF NOT EXISTS`: on any instance that already had the
column it errored, and the column added AFTER it was therefore never created. The symptom was
`/data/raster` answering **500** on a freshly updated instance — the model selected a column the
database did not have — with nothing in the UI but an empty page.

So two things are pinned here: every statement is written to be re-runnable, and one that is not
still cannot poison its neighbours.
"""
import pytest
from sqlalchemy import text

from geodeploy.main import _apply_pg_migrations
from geodeploy.schema_migrations import PG_MIGRATIONS


class TestTheStatementsThemselves:
    def test_every_add_column_is_idempotent(self):
        """These run on EVERY start. `ADD COLUMN` without `IF NOT EXISTS` fails the second time —
        which, before the savepoint fix, also broke everything after it."""
        offenders = [s for s in PG_MIGRATIONS
                     if "ADD COLUMN" in s.upper() and "IF NOT EXISTS" not in s.upper()]
        assert offenders == [], f"not re-runnable: {offenders}"

    def test_nothing_destructive_slipped_in(self):
        """The file's own rule: additive only. A DROP or a rename running unattended on every boot
        is how databases get lost."""
        for stmt in PG_MIGRATIONS:
            upper = stmt.upper()
            assert "DROP COLUMN" not in upper, stmt
            assert "DROP TABLE" not in upper, stmt
            assert "RENAME" not in upper, stmt


class TestOneFailureCannotPoisonTheRest:
    async def test_a_later_migration_still_applies(self, db):
        """The exact shape of the bug: a broken statement, then a good one."""
        conn = await db.connection()

        def run(sync_conn):
            sync_conn.execute(text("CREATE TABLE IF NOT EXISTS mig_probe (id integer)"))
            broken = "ALTER TABLE mig_probe ADD COLUMN dup integer"
            statements = [broken, broken,                       # the second one fails
                          "ALTER TABLE mig_probe ADD COLUMN IF NOT EXISTS after_the_failure text"]
            import geodeploy.main as main_mod
            original = main_mod._PG_MIGRATIONS
            main_mod._PG_MIGRATIONS = statements
            try:
                _apply_pg_migrations(sync_conn)
            finally:
                main_mod._PG_MIGRATIONS = original
            cols = sync_conn.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'mig_probe'")).scalars().all()
            sync_conn.execute(text("DROP TABLE mig_probe"))
            return cols

        cols = await conn.run_sync(run)
        assert "after_the_failure" in cols, (
            "a migration after a failing one was skipped — the transaction was poisoned")

    async def test_the_connection_is_still_usable_afterwards(self, db):
        """A poisoned transaction takes the rest of startup with it, not just the migrations."""
        conn = await db.connection()

        def run(sync_conn):
            import geodeploy.main as main_mod
            original = main_mod._PG_MIGRATIONS
            main_mod._PG_MIGRATIONS = ["ALTER TABLE no_such_table ADD COLUMN x integer"]
            try:
                _apply_pg_migrations(sync_conn)
            finally:
                main_mod._PG_MIGRATIONS = original
            return sync_conn.execute(text("SELECT 1")).scalar()

        assert await conn.run_sync(run) == 1
