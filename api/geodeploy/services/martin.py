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
    layers = await _attach_properties(layers, settings)
    config = _build_config(layers, settings)
    _write_config(config, settings.martin_config_path)
    await _reload_martin()


def _srid_from_crs(crs) -> int:
    """Parse the numeric SRID from an "EPSG:NNNN" crs string (default 4326)."""
    if crs and str(crs).upper().startswith("EPSG:"):
        try:
            return int(str(crs).split(":")[1])
        except (ValueError, IndexError):
            pass
    return 4326


async def _attach_properties(layers: list[dict], settings) -> list[dict]:
    """
    Attach each table's attribute columns (name -> Postgres type) so Martin includes
    them in the MVT tiles. A configured Martin table source with no `properties` map
    serves geometry only — which is why feature popups would show no attributes.
    """
    import asyncpg
    enriched = []
    conn = None
    try:
        conn = await asyncpg.connect(settings.postgis_sync_dsn, timeout=10)
        for layer in layers:
            schema = layer.get("schema_name") or layer.get("schema", "")
            table = layer.get("table_name") or layer.get("table", "")
            geom_col = layer.get("geometry_column") or "geom"
            id_col = layer.get("id_column") or "id"
            rows = await conn.fetch(
                """SELECT column_name, udt_name FROM information_schema.columns
                   WHERE table_schema = $1 AND table_name = $2""",
                schema, table,
            )
            exclude = {geom_col, id_col}
            props = {
                r["column_name"]: r["udt_name"]
                for r in rows
                if r["column_name"] not in exclude
            }
            enriched.append({**layer, "properties": props})
    except Exception:
        return layers  # non-fatal — fall back to no explicit properties
    finally:
        if conn is not None:
            await conn.close()
    return enriched


def _build_config(layers: list[dict], settings) -> dict:
    tables = {}
    for layer in layers:
        schema = layer.get("schema_name") or layer.get("schema", "")
        table = layer.get("table_name") or layer.get("table", "")
        key = f"{schema}.{table}"
        id_col = layer.get("id_column") or "id"
        table_cfg = {
            "schema": schema,
            "table": table,
            # Imported tables may be in any CRS / use any geometry column name.
            "srid": _srid_from_crs(layer.get("crs")),
            "geometry_column": layer.get("geometry_column") or "geom",
        }
        if id_col:
            table_cfg["id_column"] = id_col
        props = layer.get("properties")
        if props:
            table_cfg["properties"] = props
        tables[key] = table_cfg

    sslmode = f"?sslmode={settings.postgis_sslmode}" if settings.postgis_sslmode else ""
    return {
        "listen_addresses": "0.0.0.0:3000",
        "postgres": {
            "connection_string": (
                f"postgresql://{settings.postgis_user}:{settings.postgis_password}"
                f"@{settings.postgis_host}:{settings.postgis_port}/{settings.postgis_db}{sslmode}"
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
    """Reload Martin so it picks up the new config."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _docker_reload)


def _docker_reload() -> None:
    # NOTE: a full restart (not SIGHUP) is required — Martin only rebuilds table
    # source field/property definitions at startup, so SIGHUP leaves feature
    # attributes (vector_layers[].fields) empty after a config change.
    try:
        client = docker.from_env()
        container = client.containers.get("geodeploy-martin")
        if container.status != "running":
            container.start()
        else:
            container.restart()
    except docker.errors.NotFound:
        _start_martin_container()
    except Exception:
        pass  # Non-fatal


def _start_martin_container() -> None:
    """Ensure the Martin container is running (adopt-or-create + network alias)."""
    from .postgis import NETWORK, _start_martin
    try:
        client = docker.from_env()
        network = client.networks.get(NETWORK)
        _start_martin(client, network)  # idempotent + tolerant of an existing container
    except Exception:
        pass  # Non-fatal — user can start Martin via docker compose


def get_tile_url(schema: str, table: str, settings=None) -> str:
    """Return browser-accessible tile URL served through nginx's /tiles/ proxy."""
    return f"/tiles/{schema}.{table}/{{z}}/{{x}}/{{y}}"
