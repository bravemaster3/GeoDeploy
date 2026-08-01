from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
    )

    geodeploy_secret_key: str = "insecure-dev-key-change-in-production"
    geodeploy_host: str = "0.0.0.0"
    geodeploy_port: int = 8000
    geodeploy_env: str = "production"
    geodeploy_data_dir: str = "/data"
    # ── DEMO MODE ────────────────────────────────────────────────────────────────────────────
    # OFF unless explicitly set. Everything it enables is additive and guarded by an explicit check
    # on this flag, so a normal install must behave EXACTLY as it did before this existed — that is
    # the contract, and tests/test_demo_mode.py pins it rather than trusting it.
    #
    # It is a public sandbox: anyone may join with a name, gets the EDITOR role (so the existing role
    # system already withholds terminal, environment, services, backups and user management), and the
    # instance is wiped on a schedule. Never switch this on for an instance holding real data.
    geodeploy_demo_mode: bool = False
    # Upload ceiling while in demo mode only. A self-hosted install has no such cap; the message the
    # user sees says so, because "too large" on a demo would otherwise read as a product limit.
    geodeploy_demo_max_upload_mb: int = 500
    # Extra CORS origins allowed to call the API cross-origin (comma-separated), IN ADDITION to
    # localhost + GEODEPLOY_ORIGIN. Needed so a GeoLibre instance (e.g. https://web.geolibre.app, or a
    # desktop origin) can reach /api/interop to publish. Auth there is a Bearer token, not a cookie,
    # so listing an origin here does not expose cookie-authed endpoints to it.
    geodeploy_cors_origins: str = ""
    # The deployed git commit — written into .env by installer/{install,update}.sh so the admin
    # "Updates" panel can tell how far behind GitHub the running code is. "unknown" until an installer
    # writes it (e.g. a dev `docker compose up` without the script).
    geodeploy_git_sha: str = "unknown"
    # DANGER ZONE: the admin in-container command runner (Settings → Danger Zone). OFF by default; an
    # operator must opt in (set true + redeploy). Even on, it's admin-only, whitelisted to leaf
    # containers (never the socket-holding api/celery), time-bounded, and audited. Keep it off unless
    # you need it — it runs shell commands inside a container.
    geodeploy_enable_terminal: bool = False

    postgis_host: str = ""
    postgis_port: int = 5432
    postgis_db: str = "geodeploy"
    postgis_user: str = "geodeploy"
    postgis_password: str = ""
    # Empty for the GeoDeploy-provisioned local DB (no TLS); set to "prefer"/"require" for an
    # external/managed DB that needs SSL. Empty = no sslmode param (current local behaviour).
    postgis_sslmode: str = ""

    storage_type: str = ""
    storage_endpoint: str = ""
    storage_bucket: str = "geodeploy"
    storage_access_key: str = ""
    storage_secret_key: str = ""
    storage_region: str = "us-east-1"

    redis_url: str = "redis://redis:6379/0"
    martin_url: str = "http://martin:3000"
    martin_config_path: str = "/data/martin/martin-config.yaml"
    titiler_url: str = "http://titiler:80"

    @property
    def secret_key(self) -> str:
        return self.geodeploy_secret_key

    @property
    def data_dir(self) -> str:
        return self.geodeploy_data_dir

    @property
    def env(self) -> str:
        return self.geodeploy_env

    @property
    def sqlite_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.data_dir}/sqlite/geodeploy.db"

    @property
    def _pg_sslmode_query(self) -> str:
        """`?sslmode=...` suffix for libpq-style URLs, or empty when unset (local DB)."""
        return f"?sslmode={self.postgis_sslmode}" if self.postgis_sslmode else ""

    @property
    def postgis_dsn(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgis_user}:{self.postgis_password}"
            f"@{self.postgis_host}:{self.postgis_port}/{self.postgis_db}{self._pg_sslmode_query}"
        )

    @property
    def postgis_sync_dsn(self) -> str:
        return (
            f"postgresql://{self.postgis_user}:{self.postgis_password}"
            f"@{self.postgis_host}:{self.postgis_port}/{self.postgis_db}{self._pg_sslmode_query}"
        )

    @property
    def is_dev(self) -> bool:
        return self.env == "development"


@lru_cache
def get_settings() -> Settings:
    return Settings()
