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
        "listen_addresses": "0.0.0.0:3000",
        "postgres": {
            "connection_string": (
                f"postgresql://{settings.postgis_user}:{settings.postgis_password}"
                f"@{settings.postgis_host}:{settings.postgis_port}/{settings.postgis_db}"
            ),
            "pool_size": 5,
            "tables": tables,
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
        _start_martin_container()
    except Exception:
        pass  # Non-fatal


def _start_martin_container() -> None:
    """Create and start the Martin container when it doesn't exist yet."""
    from .postgis import MARTIN_NAME, MARTIN_IMAGE, NETWORK, _get_host_bind_path
    try:
        client = docker.from_env()
        settings = get_settings()
        martin_host_path = _get_host_bind_path(client, settings.data_dir + "/martin")
        if not martin_host_path:
            martin_host_path = _get_host_bind_path(client, "/data/martin")
        if not martin_host_path:
            return  # Can't determine host path — user must start Martin via docker compose
        network = client.networks.get(NETWORK)
        container = client.containers.run(
            MARTIN_IMAGE,
            name=MARTIN_NAME,
            detach=True,
            restart_policy={"Name": "unless-stopped"},
            command="--config /config/martin-config.yaml",
            volumes={martin_host_path: {"bind": "/config", "mode": "rw"}},
        )
        network.connect(container, aliases=["martin"])
    except Exception:
        pass  # Non-fatal — user can start Martin via docker compose


def get_tile_url(schema: str, table: str, settings=None) -> str:
    """Return browser-accessible tile URL served through nginx's /tiles/ proxy."""
    return f"/tiles/{schema}.{table}/{{z}}/{{x}}/{{y}}"
