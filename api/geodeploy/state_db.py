"""Synchronous state-DB access for the Celery worker.

The worker has no async session, so every task module talked to the state database through raw
`sqlite3` — ten modules, dozens of `?`-placeholder statements, all writing job progress and layer
status. Moving state to PostgreSQL could have meant rewriting every one of those queries.

Instead this is a **thin psycopg2 wrapper with the same surface those call sites already use**:
`with connect() as conn:` … `conn.execute("UPDATE … WHERE id = ?", (v,))`, `.fetchone()`,
`.fetchall()`, commit-on-exit. It translates `?` to `%s` and nothing else. That keeps the migration
mechanical and reviewable — a behavioural change hiding inside a hand-rewritten SQL statement is
exactly what you cannot afford in ingest code.

Deliberately NOT an ORM and not a connection pool: a Celery task is a short process doing a handful
of statements, and a pool would just hold connections open across long file conversions.
"""
import logging
import re

import psycopg2

from .config import get_settings

logger = logging.getLogger(__name__)

# `?` outside of quoted strings → `%s`. Also escapes literal `%` (a LIKE pattern such as `%foo%`
# would otherwise be read by psycopg2 as a placeholder and raise IndexError).
_PLACEHOLDER = re.compile(r"\?(?=(?:[^']*'[^']*')*[^']*$)")


def translate(sql: str) -> str:
    return _PLACEHOLDER.sub("%s", sql.replace("%", "%%"))


#: Assign to `conn.row_factory` for dict rows, mirroring how these call sites used `sqlite3.Row`.
#: NOTE: unlike sqlite3.Row these support key access and `dict(row)` but NOT positional `row[0]` —
#: every converted call site uses keys.
dict_row = "dict"


class _Cursor:
    """Wraps a psycopg2 cursor so `?` keeps working and results stay tuple-shaped."""

    def __init__(self, cur, row_factory=None):
        self._cur = cur
        self._row_factory = row_factory

    def _wrap(self, row):
        if row is None or self._row_factory != dict_row:
            return row
        cols = [d[0] for d in self._cur.description]
        return dict(zip(cols, row))

    def execute(self, sql, params=()):
        self._cur.execute(translate(sql), tuple(params))
        return self

    def fetchone(self):
        return self._wrap(self._cur.fetchone())

    def fetchall(self):
        return [self._wrap(r) for r in self._cur.fetchall()]

    @property
    def lastrowid(self):
        """sqlite3's lastrowid has no psycopg2 equivalent — a statement that needs the new id must
        say `RETURNING id` and read it back. Raising here (rather than returning None) makes the
        omission loud instead of silently inserting NULL foreign keys later."""
        raise NotImplementedError(
            "Postgres has no lastrowid: add `RETURNING id` to the INSERT and use fetchone()[0].")

    def __iter__(self):
        return iter(self._cur)


class _Connection:
    """Context manager mirroring `sqlite3.connect(...)` usage: commits on clean exit, rolls back on
    exception, and always closes — sqlite3's context manager does NOT close, which is why the old
    call sites could get away with `with sqlite3.connect(path) as conn:` and no cleanup."""

    def __init__(self, conn):
        self._conn = conn
        self.row_factory = None      # set to state_db.dict_row for dict rows

    def execute(self, sql, params=()):
        cur = _Cursor(self._conn.cursor(), self.row_factory)
        return cur.execute(sql, params)

    def cursor(self):
        return _Cursor(self._conn.cursor(), self.row_factory)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self._conn.commit()
            else:
                self._conn.rollback()
        finally:
            self.close()
        return False


def connect(timeout: int = 30) -> _Connection:
    """A short-lived connection to the state database (the same one PostGIS serves)."""
    settings = get_settings()
    kwargs = dict(
        host=settings.postgis_host or "postgres",
        port=int(settings.postgis_port or 5432),
        dbname=settings.postgis_db or "geodeploy",
        user=settings.postgis_user or "geodeploy",
        password=settings.postgis_password or "",
        connect_timeout=timeout,
    )
    if settings.postgis_sslmode:
        kwargs["sslmode"] = settings.postgis_sslmode
    return _Connection(psycopg2.connect(**kwargs))
