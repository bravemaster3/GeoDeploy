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

    postgis_host: str = ""
    postgis_port: int = 5432
    postgis_db: str = "geodeploy"
    postgis_user: str = "geodeploy"
    postgis_password: str = ""

    storage_type: str = ""
    storage_endpoint: str = ""
    storage_bucket: str = "geodeploy"
    storage_access_key: str = ""
    storage_secret_key: str = ""
    storage_region: str = "us-east-1"

    redis_url: str = "redis://redis:6379/0"
    martin_url: str = "http://martin:3000"
    martin_config_path: str = "/data/martin/martin-config.yaml"
    titiler_url: str = "http://titiler:8080"

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
    def postgis_dsn(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgis_user}:{self.postgis_password}"
            f"@{self.postgis_host}:{self.postgis_port}/{self.postgis_db}"
        )

    @property
    def postgis_sync_dsn(self) -> str:
        return (
            f"postgresql://{self.postgis_user}:{self.postgis_password}"
            f"@{self.postgis_host}:{self.postgis_port}/{self.postgis_db}"
        )

    @property
    def is_dev(self) -> bool:
        return self.env == "development"


@lru_cache
def get_settings() -> Settings:
    return Settings()
