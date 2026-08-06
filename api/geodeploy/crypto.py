"""Application-level encryption for secrets stored in the database (Fernet, symmetric).

Scope: `smtp_password`, `oidc_client_secret` (A-04), and — since 2026-07-30 — the infra credentials
`postgis_password` and `storage_secret_key`. Those two were deliberately left in plaintext while the
DB copy was only a convenience, on the argument that the containers consume them as env vars anyway.
Backups changed that argument: `pg_dump` carries `setup_config`, so plaintext there meant every
infra secret left the box inside every backup. They are encrypted now.

The consequence for readers: `.env` remains the SOURCE OF TRUTH for those two (the containers are
created from it), and the DB copy is a derived record. Anything reading them via RAW SQL — the
Celery shim bypasses SQLAlchemy types, so `services/martin.py`, `tasks/raster_ingest.py` and
`tasks/restore.py` — MUST call `decrypt_secret` itself, and must expect `looks_encrypted` to be true
when the row came from another instance's snapshot.

This defends the realistic threats for the app-managed secrets — a leaked DB file, a stolen backup, a
SQL read that dumps setup_config — but is defense-in-depth, not absolute (the key lives on the host).

Key: `GEODEPLOY_ENCRYPTION_KEY` if set, else derived from `GEODEPLOY_SECRET_KEY`, so existing installs
need no new config. Any non-empty string works as the source (hashed to a valid Fernet key). A stored
value that isn't valid ciphertext (a legacy plaintext secret from before this landed) is read back
unchanged and re-encrypted on the next write — no data migration needed.
"""
import base64
import hashlib
import os
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator


@lru_cache(maxsize=1)
def _cipher() -> Fernet:
    from .config import get_settings
    source = os.getenv("GEODEPLOY_ENCRYPTION_KEY") or get_settings().secret_key or "geodeploy"
    key = base64.urlsafe_b64encode(hashlib.sha256(source.encode()).digest())
    return Fernet(key)


def encrypt_secret(value: str | None) -> str | None:
    if not value:
        return value
    return _cipher().encrypt(value.encode()).decode()


def decrypt_secret(value: str | None) -> str | None:
    if not value:
        return value
    try:
        return _cipher().decrypt(value.encode()).decode()
    except (InvalidToken, ValueError):
        return value  # legacy plaintext (pre-encryption) — read as-is; re-encrypted on next write


def looks_encrypted(value: str | None) -> bool:
    """True when a value that has been through `decrypt_secret` is STILL a Fernet token.

    Decryption failure is indistinguishable from a legacy plaintext value, so `decrypt_secret`
    returns the input unchanged rather than raising. That is the right default for reading, and a
    trap for anything that then USES the result: signing an S3 request with a Fernet blob fails as
    `SignatureDoesNotMatch`, which blames the credentials rather than the key.

    Fernet tokens are version byte 0x80, base64url-encoded — always the `gAAAAA` prefix.
    """
    return bool(value) and value.startswith("gAAAAA")


class EncryptedText(TypeDecorator):
    """A Text column Fernet-encrypted at rest and transparently decrypted on read. Legacy plaintext
    reads through unchanged. Use ONLY for ORM-read, DB-only secrets (see the module docstring)."""
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return encrypt_secret(value)

    def process_result_value(self, value, dialect):
        return decrypt_secret(value)
