"""Martin tile server config generation and lifecycle management."""
import asyncio
import logging
import os
import docker
import yaml
from .. import state_db
from ..config import get_settings

_log = logging.getLogger(__name__)


def _pg_creds(settings) -> dict:
    """Postgres creds from the SQLite setup_config (authoritative).

    The env (`settings.postgis_*`) is empty in the celery container — `docker restart` doesn't
    re-read env_file — and `regenerate_config` runs in celery after every ingest. Reading creds
    from env would write a password-less Martin connection string → Martin can't connect → no
    vector tiles (the table is "ready" but never renders). Falls back to env if SQLite has none."""
    try:
        with state_db.connect() as conn:
            conn.row_factory = state_db.dict_row
            row = conn.execute(
                "SELECT postgis_host, postgis_port, postgis_db, postgis_user, postgis_password "
                "FROM setup_config WHERE id = 1"
            ).fetchone()
        if row and row["postgis_password"]:
            # Raw shim read — decrypt explicitly (EncryptedText only applies to ORM reads).
            from ..crypto import decrypt_secret
            creds = dict(row)
            creds["postgis_password"] = decrypt_secret(creds["postgis_password"])
            return creds
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
    # Percent-encoded: this string goes into martin-config.yaml verbatim, and a password with @ or %
    # in it would point Martin at a different host or fail to parse. See config._pg_userinfo.
    from urllib.parse import quote
    userinfo = f"{quote(c['postgis_user'] or '', safe='')}:{quote(c['postgis_password'] or '', safe='')}"
    return (f"postgresql://{userinfo}"
            f"@{c['postgis_host']}:{c['postgis_port']}/{c['postgis_db']}{ssl}")


async def regenerate_config(layers: list[dict], force: bool = False) -> None:
    """
    Rebuild martin-config.yaml from the current layer list and signal Martin to reload.
    layers: [{"schema": str, "table": str, "id_column": str}]
    force: restart Martin even when nothing changed (the operator's "Reload Martin" button).
    """
    settings = get_settings()
    layers = await _attach_properties(layers, settings)
    installed = await _ensure_pillar_function(settings)
    config = _build_config(layers, settings)
    changed = _write_config(config, settings.martin_config_path)
    # Restarting Martin drops every in-flight tile request, so do it only when it can change what
    # Martin serves: the config differs, or the pillar function did not exist when Martin last
    # resolved its sources, OR its body changed — Martin CACHES TILES in memory, so a corrected
    # function otherwise keeps serving the tiles it already built from the old one.
    #
    # `force` is for the operator's "Reload Martin" button, whose entire purpose is to restart a
    # Martin that is misbehaving for reasons the config cannot show. Skipping the restart there
    # because nothing changed on disk would take away the one manual recovery there is.
    if force or changed or installed:
        await _reload_martin()


def _pillar_body(create_sql: str) -> str:
    """The function body from a `CREATE OR REPLACE FUNCTION … AS $$ … $$` statement.

    Split on the OUTER `$$` only. The body itself contains a `$f$ … $f$` dollar-quoted string (the
    dynamic SQL template), which uses a different tag precisely so it cannot terminate this one.
    """
    parts = create_sql.split("$$")
    return parts[1] if len(parts) > 2 else create_sql


async def _ensure_pillar_function(settings) -> bool:
    """Create/refresh the shared `geodeploy.point_pillars` tile function (3D pillars for points).

    Done HERE rather than in a migration because it must exist wherever Martin's config names it,
    and this is the one place that writes that config — so the two cannot drift apart. `CREATE OR
    REPLACE` makes it idempotent and self-healing: an instance restored from an older snapshot gets
    the current definition on the next config rebuild, with no operator action.

    Non-fatal. A database that refuses the DDL (a read-only replica, a locked-down role) must not
    stop the tile config for every OTHER layer from being written — the pillars source simply
    returns nothing and 3D points do not draw.

    Returns True when Martin has to be RESTARTED: either the function did not exist (so Martin
    resolved its sources without it), or its BODY changed.

    The body case is not obvious and cost a real bug. Martin resolves a function source by name at
    startup and executes the current body per request, so a replaced body takes effect immediately —
    which is what an earlier version of this comment said, and it stopped there. But **Martin caches
    tiles in memory** (its `--cache-size` is on by default). A corrected function therefore keeps
    serving the OLD tiles to anyone who had already requested them, and the fix looks like it did
    not work: the operator sees a deployed instance still drawing the broken geometry, with nothing
    in any log to explain it. Comparing `prosrc` catches exactly that.
    """
    from . import pillars

    import asyncpg
    conn = None
    try:
        conn = await asyncpg.connect(_pg_sync_dsn(settings), timeout=10)
        current = await conn.fetchval(
            """SELECT p.prosrc FROM pg_proc p
               JOIN pg_namespace n ON n.oid = p.pronamespace
               WHERE n.nspname = $1 AND p.proname = $2""",
            pillars.SCHEMA, pillars.FUNCTION,
        )
        await conn.execute(pillars.CREATE_SQL)
        # `prosrc` is the body between the outer $$ delimiters — exactly what CREATE_SQL wraps.
        return (current or "").strip() != _pillar_body(pillars.CREATE_SQL).strip()
    except Exception as exc:      # noqa: BLE001 — see docstring
        _log.warning("could not install the 3D pillar tile function: %s", exc)
        return False
    finally:
        if conn is not None:
            await conn.close()


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

    postgres = {"connection_string": _pg_sync_dsn(settings), "pool_size": 5}
    # OMIT `tables` when there are none. An explicit empty map means "publish nothing", and Martin
    # then exits with "No tile sources found" instead of idling — which crash-looped the container
    # on every fresh install until the first layer was uploaded. With no `tables` key it
    # auto-discovers instead, finds whatever PostGIS itself exposes, and stays up.
    if tables:
        postgres["tables"] = tables
    # The 3D-pillar function source. ONE entry serves every point layer — the layer is named by
    # query parameters on the tile URL (services/pillars) — so this does not grow with the catalog.
    # Listed explicitly because naming `tables` above turns OFF Martin's auto-discovery, which would
    # otherwise have found the function on its own.
    from . import pillars
    postgres["functions"] = {
        pillars.FUNCTION: {"schema": pillars.SCHEMA, "function": pillars.FUNCTION},
    }
    return {"listen_addresses": "0.0.0.0:3000", "postgres": postgres}


def _write_config(config: dict, path: str) -> bool:
    """Write the config, returning whether it actually DIFFERS from what was already there.

    The caller restarts Martin on a change, and a restart drops in-flight tile requests — so
    "nothing changed" is worth knowing. Compared as rendered YAML rather than as dicts, because the
    rendered text is what Martin reads.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    rendered = yaml.dump(config, default_flow_style=False, allow_unicode=True)
    try:
        with open(path) as f:
            if f.read() == rendered:
                return False
    except OSError:
        pass          # no config yet (or unreadable) — write it and treat that as a change
    with open(path, "w") as f:
        f.write(rendered)
    return True


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
