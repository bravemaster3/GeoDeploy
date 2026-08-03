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
    def rowcount(self):
        """Rows affected by the last statement. psycopg2 has it; the wrapper simply never exposed it,
        so callers reaching for it got AttributeError (or silently `None` via getattr). It is the only
        way to tell "UPDATE matched nothing" from "UPDATE changed nothing", which matters when a
        restore has replaced the row you were updating."""
        return self._cur.rowcount

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


#: Credentials published for the worker. Under `temp/` DELIBERATELY: the API container mounts data
#: SUB-DIRECTORIES (data/sqlite, data/portals, data/temp, ...), not the data root — so a file
#: written to `{data_dir}/x` lands in the API container's own writable layer and is invisible to
#: the host and to celery, which mounts the whole `./data`. `data/temp` is mounted by BOTH and is
#: already the channel for cross-container handoffs (update-status.json, deployed-sha).
RUNTIME_DB_FILE = "temp/runtime-db.json"


def runtime_credentials() -> dict | None:
    """Database credentials from the shared data dir, or None.

    WHY THIS EXISTS. The worker gets its environment from `.env` — but Docker reads `.env` when a
    container is CREATED, not when it restarts. The setup wizard writes the database password
    *after* celery is already running, and `restart()` preserves the original environment, so the
    worker kept the install-time empty password and every task died with
    `fe_sendauth: no password supplied`. (The API escaped it by patching its own os.environ.)

    Recreating the container from the Docker SDK would mean reconstructing its mounts and networks
    by hand. This file is the smaller primitive: the data dir is bind-mounted into both the API and
    the worker, so a value written here is visible immediately, with no restart at all. Read on
    every connect — a short-lived task connection makes that free, and it means a credential change
    never needs a container lifecycle event again.
    """
    import json
    try:
        with open(f"{get_settings().data_dir}/{RUNTIME_DB_FILE}") as fh:
            data = json.load(fh)
        return data if data.get("password") else None
    except (OSError, ValueError):
        return None


def write_runtime_credentials(host, port, dbname, user, password, sslmode="") -> None:
    """Publish credentials for the worker. Written by the setup wizard; 0600 because it holds a
    password (the same one already in `.env` beside it)."""
    import json
    import os
    path = f"{get_settings().data_dir}/{RUNTIME_DB_FILE}"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump({"host": host, "port": int(port or 5432), "dbname": dbname,
                   "user": user, "password": password, "sslmode": sslmode or ""}, fh)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)      # atomic: a worker must never read a half-written file


#: Under `temp/` for the SAME reason as RUNTIME_DB_FILE above — the API container mounts data
#: sub-directories, not the data root, so a file at `{data_dir}/x` lands in the API's own writable
#: layer and the worker never sees it. `data/temp` is mounted by both.
RUNTIME_STORAGE_FILE = "temp/runtime-storage.json"


def runtime_storage() -> dict | None:
    """Object-storage settings published by the setup wizard, or None."""
    import json
    try:
        with open(f"{get_settings().data_dir}/{RUNTIME_STORAGE_FILE}") as fh:
            data = json.load(fh)
        return data if data.get("access_key") else None
    except (OSError, ValueError):
        return None


def write_runtime_storage(endpoint, bucket, access_key, secret_key, region="us-east-1") -> None:
    """Publish storage settings for the WORKER, for the same reason the DB credentials are published:
    a `docker restart` does not re-read `.env` — Docker fixes a container's environment when it is
    CREATED — so celery keeps whatever the installer wrote before the wizard ran.

    That default is `http://minio:9000`, which happens to be CORRECT for a local install (Compose
    gives the MinIO service that network alias), so the bug is invisible there. On external S3 the
    worker kept trying `minio:9000` for every task that touches storage — ingest, tiling, restore —
    while the API, whose own os.environ the wizard updates in-process, worked fine. A restore failed
    with `Could not connect to the endpoint URL: "http://minio:9000/..."` on an instance configured
    for Hetzner.

    0600: it holds a secret key, the same one already in `.env` beside it.
    """
    import json
    import os
    path = f"{get_settings().data_dir}/{RUNTIME_STORAGE_FILE}"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump({"endpoint": endpoint or "", "bucket": bucket or "",
                   "access_key": access_key or "", "secret_key": secret_key or "",
                   "region": region or "us-east-1"}, fh)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)      # atomic: a worker must never read a half-written file


def connect(timeout: int = 30) -> _Connection:
    """A short-lived connection to the state database (the same one PostGIS serves)."""
    settings = get_settings()
    live = runtime_credentials()
    if live:
        kwargs = dict(host=live["host"], port=live["port"], dbname=live["dbname"],
                      user=live["user"], password=live["password"], connect_timeout=timeout)
        if live.get("sslmode"):
            kwargs["sslmode"] = live["sslmode"]
    else:
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
