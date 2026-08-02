"""`.env` must never receive the string "None".

`_write_env` formatted every value into an f-string, so a Python `None` became the four characters
`None` in the file. `configure_db` calls it with a BLANK SetupConfig — it has no storage fields at
that point — so every `STORAGE_*` key was written as `None` at the DATABASE step.

Usually the storage step overwrote them a second later and nobody noticed. When it does not — the
wizard is interrupted, or the setup guard refuses the storage step because the database already
holds an installation — the file keeps that value. `settings.storage_endpoint` is then the string
"None", boto3 is handed it as an endpoint URL, and every object-storage operation fails with

    Invalid endpoint: None

which is what a RESTORE reported, several steps and one product area away from the cause.
"""
import pathlib

from geodeploy.models import SetupConfig
from geodeploy.routers import setup as setup_router


def _write(tmp_path, config, monkeypatch, existing: str = "") -> str:
    env = tmp_path / ".env"
    env.write_text(existing, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    # `_write_env` prefers /geodeploy/.env when that directory exists (it is the bind mount inside
    # the container); on a test machine it falls back to ./.env, which chdir puts here.
    monkeypatch.setattr(pathlib.os.path, "exists",
                        lambda p: False if p == "/geodeploy" else pathlib.Path(p).exists())
    setup_router._write_env(config)
    return env.read_text(encoding="utf-8")


def test_the_database_step_does_not_write_none_for_storage(tmp_path, monkeypatch):
    """THE regression. configure_db passes a config whose storage fields are all None."""
    cfg = SetupConfig(id=1, postgis_type="external", postgis_host="db.example.com",
                      postgis_port=5432, postgis_db="geodeploy", postgis_user="u",
                      postgis_password="p")
    body = _write(tmp_path, cfg, monkeypatch)
    assert "=None" not in body, f"the literal string None reached .env:\n{body}"


def test_the_database_step_does_not_clobber_existing_storage(tmp_path, monkeypatch):
    """Re-running configure-db on a WORKING instance must not blank its storage configuration —
    that step knows nothing about storage, so it has nothing to say about it."""
    existing = ("STORAGE_ENDPOINT=https://s3.example.com\n"
                "STORAGE_BUCKET=live-bucket\n"
                "STORAGE_ACCESS_KEY=AKIA\n")
    cfg = SetupConfig(id=1, postgis_type="external", postgis_host="db.example.com",
                      postgis_port=5432, postgis_db="geodeploy", postgis_user="u",
                      postgis_password="p")
    body = _write(tmp_path, cfg, monkeypatch, existing=existing)
    assert "STORAGE_ENDPOINT=https://s3.example.com" in body
    assert "STORAGE_BUCKET=live-bucket" in body
    assert "STORAGE_ACCESS_KEY=AKIA" in body


def test_real_values_are_still_written(tmp_path, monkeypatch):
    cfg = SetupConfig(id=1, postgis_type="local", postgis_host="postgres", postgis_port=5432,
                      postgis_db="geodeploy", postgis_user="geodeploy", postgis_password="secret",
                      storage_type="s3", storage_endpoint="https://s3.example.com",
                      storage_bucket="b", storage_access_key="AK", storage_secret_key="SK")
    body = _write(tmp_path, cfg, monkeypatch)
    assert "STORAGE_ENDPOINT=https://s3.example.com" in body
    assert "POSTGIS_PASSWORD=secret" in body


def test_an_intentional_empty_value_is_still_written(tmp_path, monkeypatch):
    """POSTGIS_SSLMODE is deliberately EMPTY for a local database — empty and None mean different
    things here, and only None means "I have nothing to say about this key"."""
    cfg = SetupConfig(id=1, postgis_type="local", postgis_host="postgres", postgis_port=5432,
                      postgis_db="geodeploy", postgis_user="geodeploy", postgis_password="p")
    body = _write(tmp_path, cfg, monkeypatch)
    assert "POSTGIS_SSLMODE=\n" in body
