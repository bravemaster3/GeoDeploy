"""Secrets encrypted at rest (crypto.EncryptedText).

App-managed, DB-only secrets (smtp_password; oidc_client_secret in A-04) are Fernet-encrypted; a
legacy plaintext value reads through unchanged and re-encrypts on the next write. Infra creds
(postgis/storage) are intentionally NOT encrypted (they live plaintext in the container env).
"""
from sqlalchemy import text

from geodeploy.crypto import decrypt_secret, encrypt_secret
from geodeploy.models import SetupConfig


def test_encrypt_roundtrip():
    tok = encrypt_secret("hunter2")
    assert tok != "hunter2"
    assert decrypt_secret(tok) == "hunter2"
    assert encrypt_secret("") == ""
    assert encrypt_secret(None) is None


def test_decrypt_plaintext_fallback():
    # A legacy (pre-encryption) plaintext value returns unchanged, not an error.
    assert decrypt_secret("legacy-plaintext-value") == "legacy-plaintext-value"


async def test_smtp_password_encrypted_at_rest(db):
    db.add(SetupConfig(id=1, smtp_password="s3cret", smtp_host="smtp.x", email_from="a@x"))
    await db.commit()
    # The raw column is ciphertext…
    raw = (await db.execute(text("SELECT smtp_password FROM setup_config WHERE id=1"))).scalar_one()
    assert raw != "s3cret"
    assert decrypt_secret(raw) == "s3cret"
    # …but the ORM decrypts it transparently.
    db.expire_all()
    cfg = await db.get(SetupConfig, 1)
    assert cfg.smtp_password == "s3cret"
