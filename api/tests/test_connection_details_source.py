"""Which copy of the infra credentials the Connection details panel shows (GitHub issue #2).

Three copies exist — `.env`, the process environment, and the `setup_config` row — and a RESTORE
replaces the third with the snapshot's. The panel read that one first, so after a restore it showed
an access key and a secret belonging to another instance (or, when the two instances have different
GEODEPLOY_SECRET_KEYs, the raw Fernet ciphertext, because `decrypt_secret` cannot tell a failed
decrypt from legacy plaintext and returns its input).

The operator's report was exactly that: "wrong MinIO keys shown in Settings — .env didn't change".
`.env` is what every container is CREATED from, so `.env` is what the instance runs on, so `.env` is
what the panel must show.
"""
from geodeploy.crypto import encrypt_secret
from geodeploy.routers.admin import merge_credentials


class _Settings:
    postgis_host = "geodeploy-postgres"
    postgis_port = 5432
    postgis_db = "geodeploy"
    postgis_user = "geodeploy"
    postgis_password = "env-db-password"
    storage_type = "local"
    storage_endpoint = "http://geodeploy-minio:9000"
    storage_bucket = "geodeploy"
    storage_access_key = "ENVACCESSKEY"
    storage_secret_key = "env-secret"
    storage_region = "us-east-1"


class _Cfg:
    """A `setup_config` row as a restore leaves it: the SNAPSHOT'S instance."""
    postgis_type = "local"
    postgis_host = "other-host"
    postgis_port = 5432
    postgis_db = "other"
    postgis_user = "other"
    postgis_password = "snapshot-db-password"
    storage_type = "hetzner"
    storage_endpoint = "https://fsn1.your-objectstorage.com"
    storage_bucket = "someone-elses-bucket"
    storage_access_key = "SNAPSHOTACCESSKEY"
    storage_secret_key = "snapshot-secret"
    storage_region = "eu-central-1"


ENV = {
    "POSTGIS_HOST": "geodeploy-postgres", "POSTGIS_PORT": "5432", "POSTGIS_DB": "geodeploy",
    "POSTGIS_USER": "geodeploy", "POSTGIS_PASSWORD": "file-db-password",
    "STORAGE_TYPE": "local", "STORAGE_ENDPOINT": "http://geodeploy-minio:9000",
    "STORAGE_BUCKET": "geodeploy", "STORAGE_ACCESS_KEY": "FILEACCESSKEY",
    "STORAGE_SECRET_KEY": "file-secret", "STORAGE_REGION": "us-east-1",
}


def test_env_file_wins_over_a_restored_setup_row():
    out = merge_credentials(ENV, _Settings(), _Cfg())
    assert out["storage"]["access_key"] == "FILEACCESSKEY"
    assert out["storage"]["secret_key"] == "file-secret"
    assert out["storage"]["bucket"] == "geodeploy"
    assert out["database"]["password"] == "file-db-password"
    assert out["storage"]["source"] == "env"


def test_the_process_environment_is_the_second_choice():
    """No `.env` — a dev container, or a file that could not be read. The running process still
    knows what it connected with; the snapshot's row still does not."""
    out = merge_credentials({}, _Settings(), _Cfg())
    assert out["storage"]["access_key"] == "ENVACCESSKEY"
    assert out["database"]["password"] == "env-db-password"
    assert out["storage"]["source"] == "environment"


def test_the_stored_row_is_used_only_when_nothing_else_knows():
    """An install configured entirely through the wizard with a container never recreated: the row
    is all there is, and it is better than showing blanks."""
    class _Blank(_Settings):
        postgis_password = ""
        storage_access_key = ""
        storage_secret_key = ""

    out = merge_credentials({}, _Blank(), _Cfg())
    assert out["storage"]["access_key"] == "SNAPSHOTACCESSKEY"
    assert out["storage"]["source"] == "database"


def test_ciphertext_is_never_shown_as_a_credential():
    """THE failure mode of the bug: the restored secret was encrypted under another instance's key,
    so it comes back through the ORM still a Fernet token. Showing that as "your secret key" sends
    an operator to compare a 100-character blob against `.env` and conclude their storage is broken.
    Nothing is better than a wrong thing that looks right.
    """
    class _Blank(_Settings):
        storage_secret_key = ""

    class _Undecryptable(_Cfg):
        # What `decrypt_secret` returns when the key does not match: the input, unchanged.
        storage_secret_key = encrypt_secret("someone-elses-secret")

    out = merge_credentials({}, _Blank(), _Undecryptable())
    assert out["storage"]["secret_key"] is None


def test_a_restored_storage_type_cannot_mislabel_a_managed_minio():
    """`managed` decides whether the panel says "external — you provided this". Read from the
    snapshot it describes somebody else's storage choice."""
    out = merge_credentials(ENV, _Settings(), _Cfg())
    assert out["storage"]["managed"] is True          # .env says local, the restored row says hetzner


def test_the_port_stays_a_number():
    """`.env` values are strings; this field was an int and something may still treat it as one."""
    out = merge_credentials(ENV, _Settings(), _Cfg())
    assert out["database"]["port"] == 5432
