"""Martin tile server config generation and lifecycle management."""
import asyncio
import os
import docker
import yaml
from ..config import get_settings


def _pg_creds(settings) -> dict:
    """Postgres creds from the SQLite setup_config (authoritative).

    The env (`settings.postgis_*`) is empty in the celery container — `docker restart` doesn't
    re-read env_file — and `regenerate_config` runs in celery after every ingest. Reading creds
    from env would write a password-less Martin connection string → Martin can't connect → no
    vector tiles (the table is "ready" but never renders). Falls back to env if SQLite has none."""
    import sqlite3
    try:
        with sqlite3.connect(f"{settings.data_dir}/sqlite/geodeploy.db") as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT postgis_host, postgis_port, postgis_db, postgis_user, postgis_password "
                "FROM setup_config WHERE id = 1"
            ).fetchone()
        if row and row["postgis_password"]:
            return dict(row)
    except Exception:
        pass
    return {
        "postgis_host": settings.postgis_host, "postgis_port": settings.postgis_port,
        "postgis_db": settings.postgis_db, "postgis_user": settings.postgis_user,
        "postgis_password": settings.postgis_password,
    }


def _pg_sync_dsn(settings) -> str:
    c = _pg_creds(settings)
    ssl = f"?sslmode={settings.postgis_sslmode}" if settings.postgis_sslmode else ""
    return (f"postgresql://{c['postgis_user']}:{c['postgis_password']}"
            f"@{c['postgis_host']}:{c['postgis_port']}/{c['postgis_db']}{ssl}")


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
        conn = await asyncpg.connect(_pg_sync_dsn(settings), timeout=10)
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

    return {
        "listen_addresses": "0.0.0.0:3000",
        "postgres": {
            "connection_string": _pg_sync_dsn(settings),
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
