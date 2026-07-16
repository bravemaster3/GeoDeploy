"""Migration tests for the RBAC role backfill (A-01).

The backfill must be exercised against a PRE-RBAC schema (a users table with no
role column). The ORM-created test DB already has `role` NOT NULL, so these tests
build a scratch sqlite file that mimics the old schema and run
_apply_schema_migrations directly against it.
"""
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from geodeploy.main import _apply_schema_migrations


def _pre_rbac_engine(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path}/pre_rbac.db")
    with eng.begin() as conn:
        conn.execute(text(
            "CREATE TABLE users ("
            "id INTEGER PRIMARY KEY, email VARCHAR(256) UNIQUE, name VARCHAR(256), "
            "hashed_password VARCHAR(256), is_admin BOOLEAN, created_at DATETIME)"
        ))
    return eng


def _seed(conn, uid, is_admin):
    conn.execute(text(
        "INSERT INTO users (id, email, name, hashed_password, is_admin) "
        f"VALUES ({uid}, 'u{uid}@x.com', 'U{uid}', 'h', {int(is_admin)})"
    ))


def _roles(conn):
    rows = conn.execute(text("SELECT id, role FROM users ORDER BY id")).fetchall()
    return {r[0]: r[1] for r in rows}


def test_backfill_owner_admin_editor(tmp_path):
    eng = _pre_rbac_engine(tmp_path)
    with eng.begin() as conn:
        _seed(conn, 1, is_admin=True)   # earliest admin → owner
        _seed(conn, 2, is_admin=False)  # pre-RBAC non-admin had full CRUD → editor
        _seed(conn, 3, is_admin=True)   # later admin stays admin
    with eng.begin() as conn:
        _apply_schema_migrations(conn)
    with eng.connect() as conn:
        assert _roles(conn) == {1: "owner", 2: "editor", 3: "admin"}


def test_backfill_is_idempotent(tmp_path):
    eng = _pre_rbac_engine(tmp_path)
    with eng.begin() as conn:
        _seed(conn, 1, is_admin=True)
        _seed(conn, 2, is_admin=False)
    for _ in range(2):
        with eng.begin() as conn:
            _apply_schema_migrations(conn)
    with eng.connect() as conn:
        assert _roles(conn) == {1: "owner", 2: "editor"}


def test_single_owner_unique_index(tmp_path):
    eng = _pre_rbac_engine(tmp_path)
    with eng.begin() as conn:
        _seed(conn, 1, is_admin=True)
    with eng.begin() as conn:
        _apply_schema_migrations(conn)
    with eng.begin() as conn:
        with pytest.raises(IntegrityError):
            conn.execute(text(
                "INSERT INTO users (id, email, name, hashed_password, is_admin, role) "
                "VALUES (9, 'o2@x.com', 'O2', 'h', 1, 'owner')"
            ))
    with eng.begin() as conn:  # non-owner roles are not constrained by the partial index
        conn.execute(text(
            "INSERT INTO users (id, email, name, hashed_password, is_admin, role) "
            "VALUES (10, 'a2@x.com', 'A2', 'h', 1, 'admin')"
        ))


def test_no_owner_when_no_admin_exists(tmp_path):
    # Degenerate pre-RBAC DB with only a non-admin: nobody is promoted to owner.
    eng = _pre_rbac_engine(tmp_path)
    with eng.begin() as conn:
        _seed(conn, 2, is_admin=False)
    with eng.begin() as conn:
        _apply_schema_migrations(conn)
    with eng.connect() as conn:
        assert _roles(conn) == {2: "editor"}
