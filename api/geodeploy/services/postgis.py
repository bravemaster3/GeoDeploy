"""PostGIS provisioning and management."""
import asyncio
import secrets
import string
import docker
import asyncpg
from ..config import get_settings

CONTAINER_NAME = "geodeploy-postgres"
IMAGE = "postgis/postgis:16-3.4"
NETWORK = "geodeploy"


def _random_password(length: int = 32) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


async def provision_local() -> dict:
    """Start the PostGIS container and return connection credentials."""
    password = _random_password()

    client = docker.from_env()

    try:
        container = client.containers.get(CONTAINER_NAME)
        # Read the password the container was originally created with
        for env_var in container.attrs["Config"]["Env"] or []:
            if env_var.startswith("POSTGRES_PASSWORD="):
                password = env_var.split("=", 1)[1]
                break
        if container.status != "running":
            container.start()
        try:
            client.networks.get(NETWORK).connect(container)
        except docker.errors.APIError:
            pass  # already connected
    except docker.errors.NotFound:
        # Remove stale volume so postgres initialises fresh with the new password
        try:
            client.volumes.get("geodeploy_postgres").remove()
        except docker.errors.NotFound:
            pass
        client.containers.run(
            IMAGE,
            name=CONTAINER_NAME,
            detach=True,
            restart_policy={"Name": "unless-stopped"},
            environment={
                "POSTGRES_DB": "geodeploy",
                "POSTGRES_USER": "geodeploy",
                "POSTGRES_PASSWORD": password,
            },
            volumes={"geodeploy_postgres": {"bind": "/var/lib/postgresql/data", "mode": "rw"}},
            network=NETWORK,
        )

    await _wait_healthy(CONTAINER_NAME, 5432, "geodeploy", "geodeploy", password)

    return {
        "host": CONTAINER_NAME,
        "port": 5432,
        "db": "geodeploy",
        "user": "geodeploy",
        "password": password,
    }


async def test_connection(host: str, port: int, db: str, user: str, password: str) -> None:
    """Raise if PostGIS is unreachable or the postgis extension is missing."""
    dsn = f"postgresql://{user}:{password}@{host}:{port}/{db}"
    conn = await asyncpg.connect(dsn, timeout=10)
    try:
        await conn.fetchval("SELECT PostGIS_Version()")
    except asyncpg.exceptions.UndefinedFunctionError:
        raise ValueError("PostGIS extension not installed on this database.")
    finally:
        await conn.close()


async def create_user_schema(user_id: int, host: str, port: int, db: str, user: str, password: str) -> str:
    schema = f"geodeploy_u{user_id}"
    dsn = f"postgresql://{user}:{password}@{host}:{port}/{db}"
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        await conn.execute(f'CREATE EXTENSION IF NOT EXISTS postgis')
    finally:
        await conn.close()
    return schema


async def _wait_healthy(host: str, port: int, db: str, user: str, password: str, retries: int = 30) -> None:
    dsn = f"postgresql://{user}:{password}@{host}:{port}/{db}"
    for attempt in range(retries):
        try:
            conn = await asyncpg.connect(dsn, timeout=5)
            await conn.close()
            return
        except Exception:
            if attempt == retries - 1:
                raise RuntimeError(f"PostGIS did not become healthy after {retries} attempts.")
            await asyncio.sleep(2)
