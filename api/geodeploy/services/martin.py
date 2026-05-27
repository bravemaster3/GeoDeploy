"""Martin tile server config generation and lifecycle management."""
import asyncio
import os
import docker
import yaml
from ..config import get_settings


async def regenerate_config(layers: list[dict]) -> None:
    """
    Rebuild martin-config.yaml from the current layer list and signal Martin to reload.
    layers: [{"schema": str, "table": str, "id_column": str}]
    """
    settings = get_settings()
    config = _build_config(layers, settings)
    _write_config(config, settings.martin_config_path)
    await _reload_martin()


def _build_config(layers: list[dict], settings) -> dict:
    tables = {}
    for layer in layers:
        key = f"{layer['schema']}.{layer['table']}"
        tables[key] = {
            "schema": layer["schema"],
            "table": layer["table"],
            "srid": 4326,
            "geometry_column": "geom",
            "id_column": layer.get("id_column", "id"),
        }

    return {
        "postgres": {
            "connection_string": (
                f"postgresql://{settings.postgis_user}:{settings.postgis_password}"
                f"@{settings.postgis_host}:{settings.postgis_port}/{settings.postgis_db}"
            ),
            "pool_size": 5,
            "tables": tables,
        },
        "srv": {
            "listen_addresses": "0.0.0.0:3000",
        },
    }


def _write_config(config: dict, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True)


async def _reload_martin() -> None:
    """Send SIGHUP to Martin container for graceful config reload."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _docker_reload)


def _docker_reload() -> None:
    try:
        client = docker.from_env()
        container = client.containers.get("geodeploy-martin")
        container.kill(signal="SIGHUP")
    except docker.errors.NotFound:
        pass  # Martin not running yet — config will be picked up on start
    except Exception:
        pass  # Non-fatal: tiles will still work after next restart


def get_tile_url(schema: str, table: str, settings=None) -> str:
    if settings is None:
        settings = get_settings()
    return f"{settings.martin_url}/{schema}.{table}/{{z}}/{{x}}/{{y}}"
