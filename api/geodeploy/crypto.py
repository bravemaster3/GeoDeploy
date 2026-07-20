"""Application-level encryption for secrets stored in the database (Fernet, symmetric).

Scope is deliberate: only secrets that live EXCLUSIVELY in the DB and are read through the ORM are
encrypted here — currently `smtp_password`, and `oidc_client_secret` (A-04). The infra credentials
(`postgis_password`, `storage_secret_key`) are NOT encrypted on purpose: the containers consume them
as plaintext env vars (setup.py provisions them into the compose env), so the DB copy can't be the
source of truth, and they're read via RAW SQL in services/martin.py + tasks/raster_ingest.py, which a
TypeDecorator wouldn't cover. Encrypting only the DB copy there would be inconsistent theatre; the
real protection for those is host/filesystem security + keeping env files out of git.

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


class EncryptedText(TypeDecorator):
    """A Text column Fernet-encrypted at rest and transparently decrypted on read. Legacy plaintext
    reads through unchanged. Use ONLY for ORM-read, DB-only secrets (see the module docstring)."""
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return encrypt_secret(value)

    def process_result_value(self, value, dialect):
        return decrypt_secret(value)
