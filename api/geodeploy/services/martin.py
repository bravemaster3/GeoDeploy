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
        schema = layer.get("schema_name") or layer.get("schema", "")
        table = layer.get("table_name") or layer.get("table", "")
        key = f"{schema}.{table}"
        tables[key] = {
            "schema": schema,
            "table": table,
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
        if container.status != "running":
            container.start()
        else:
            try:
                container.kill(signal="SIGHUP")
            except Exception:
                container.restart()
    except docker.errors.NotFound:
        pass  # Martin not running yet — config will be picked up on start
    except Exception:
        pass  # Non-fatal


def get_tile_url(schema: str, table: str, settings=None) -> str:
    """Return browser-accessible tile URL served through nginx's /tiles/ proxy."""
    return f"/tiles/{schema}.{table}/{{z}}/{{x}}/{{y}}"
