"""`state_db` — the psycopg2 shim the Celery worker reaches the state database through.

Worth real tests despite being ~100 lines: it rewrites EVERY SQL statement the worker runs, so a
bug here is silent data corruption in ingest paths rather than an obvious crash. These cases are
pure string/logic — no database needed — which is exactly why they should exist.
"""
import pytest

from geodeploy.state_db import dict_row, translate


class TestPlaceholderTranslation:
    def test_positional_placeholders_become_pyformat(self):
        assert translate("UPDATE upload_jobs SET status = ? WHERE id = ?") == \
            "UPDATE upload_jobs SET status = %s WHERE id = %s"

    def test_question_mark_inside_a_string_literal_is_left_alone(self):
        """`?` inside quotes is DATA. Rewriting it would both corrupt the value and shift every
        later parameter by one — the worst kind of failure, because it still executes."""
        out = translate("SELECT * FROM t WHERE label = 'why?' AND id = ?")
        assert "'why?'" in out
        assert out.count("%s") == 1

    def test_literal_percent_is_escaped(self):
        """psycopg2 reads `%` as the start of a placeholder, so an unescaped LIKE pattern raises
        IndexError at execute time. `%%` is how you pass a literal one."""
        assert translate("SELECT * FROM t WHERE name LIKE '%geo%' AND id = ?") == \
            "SELECT * FROM t WHERE name LIKE '%%geo%%' AND id = %s"

    def test_statement_without_placeholders_is_unchanged(self):
        assert translate("SELECT 1") == "SELECT 1"

    @pytest.mark.parametrize("sql,expected_params", [
        ("INSERT INTO t (a) VALUES (?)", 1),
        ("INSERT INTO t (a, b, c) VALUES (?, ?, ?)", 3),
        ("UPDATE t SET a = ?, b = ? WHERE id = ?", 3),
    ])
    def test_placeholder_count_is_preserved(self, sql, expected_params):
        """The count must match the tuple the call site passes, or psycopg2 raises at execute."""
        assert translate(sql).count("%s") == expected_params


class TestLastRowIdIsRefused:
    def test_lastrowid_raises_rather_than_returning_none(self):
        """sqlite3's lastrowid has no psycopg2 equivalent. Returning None would let an INSERT that
        needs its new id write NULL foreign keys and fail much later, somewhere unrelated; raising
        forces the call site to add `RETURNING id`."""
        from geodeploy.state_db import _Cursor

        cur = _Cursor(cursor_stub := object.__new__(object))   # never touched before the raise
        with pytest.raises(NotImplementedError, match="RETURNING id"):
            _ = cur.lastrowid
        assert cursor_stub is not None


class TestDictRows:
    def test_dict_row_marker_is_stable(self):
        """Call sites assign this to `conn.row_factory`; it only has to be a recognisable sentinel,
        but changing its value would silently turn dict rows back into tuples."""
        assert dict_row == "dict"

    def test_rows_are_mapped_to_dicts_when_requested(self):
        from geodeploy.state_db import _Cursor

        class FakeCursor:
            description = [("schema_name",), ("table_name",)]

            def fetchone(self):
                return ("geodeploy_u1", "roads")

            def fetchall(self):
                return [("geodeploy_u1", "roads"), ("geodeploy_u2", "parcels")]

        cur = _Cursor(FakeCursor(), dict_row)
        assert cur.fetchone() == {"schema_name": "geodeploy_u1", "table_name": "roads"}
        assert cur.fetchall()[1]["table_name"] == "parcels"

    def test_rows_stay_tuples_without_a_row_factory(self):
        from geodeploy.state_db import _Cursor

        class FakeCursor:
            description = [("id",)]

            def fetchone(self):
                return (7,)

            def fetchall(self):
                return [(7,)]

        cur = _Cursor(FakeCursor())
        assert cur.fetchone() == (7,)          # positional access is what most call sites use
        assert cur.fetchall() == [(7,)]
