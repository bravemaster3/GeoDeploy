"""MinIO / S3-compatible storage provisioning and management."""
import asyncio
import secrets
import string
import docker
import boto3
from botocore.client import Config
from botocore.exceptions import ClientError, EndpointResolutionError
from ..config import get_settings

CONTAINER_NAME = "geodeploy-minio"
IMAGE = "minio/minio:latest"
NETWORK = "geodeploy"
TITILER_NAME = "geodeploy-titiler"
TITILER_IMAGE = "ghcr.io/developmentseed/titiler:latest"


def _random_key(length: int = 32) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


# The user TiTiler runs as. TiTiler only needs to READ objects (GetObject) — it must never hold the
# MinIO root/write key, so if it is ever compromised the blast radius is read-only, not full storage.
_TITILER_RO_USER = "gd-titiler-ro"


def _ensure_readonly_user(endpoint: str, root_access: str, root_secret: str, bucket: str) -> tuple[str, str] | None:
    """Create/refresh a READ-ONLY MinIO user scoped to `bucket` (GetObject + ListBucket only) and
    return its (access_key, secret_key). Uses a short-lived `minio/mc` container against the local
    MinIO admin API. Best-effort: returns None on ANY failure so the caller falls back to the root
    credentials and raster serving is never broken by this hardening step."""
    ro_secret = _random_key(40)
    policy = ('{"Version":"2012-10-17","Statement":[{"Effect":"Allow",'
              '"Action":["s3:GetObject","s3:GetBucketLocation","s3:ListBucket"],'
              '"Resource":["arn:aws:s3:::%s","arn:aws:s3:::%s/*"]}]}' % (bucket, bucket))
    # Remove-then-add makes the secret deterministic across re-provisioning (a plain re-add can
    # error on an existing user, leaving MinIO on the OLD secret while TiTiler gets the NEW one).
    # `policy create` (new mc) falls back to `policy add`, and `policy attach` to `policy set`, so
    # this works across mc versions. Everything but the final add is tolerant; the add is guarded.
    script = (
        'set -e; '
        'mc alias set gd "{ep}" "{ra}" "{rs}" >/dev/null; '
        "printf '%s' '{pol}' > /tmp/p.json; "
        '(mc admin policy create gd {user}-pol /tmp/p.json >/dev/null 2>&1 || '
        ' mc admin policy add gd {user}-pol /tmp/p.json >/dev/null 2>&1 || true); '
        'mc admin user remove gd "{ro}" >/dev/null 2>&1 || true; '
        'mc admin user add gd "{ro}" "{rosec}" >/dev/null; '
        '(mc admin policy attach gd {user}-pol --user "{ro}" >/dev/null 2>&1 || '
        ' mc admin policy set gd {user}-pol user="{ro}" >/dev/null 2>&1 || true); '
        'echo GEODEPLOY_RO_OK'
    ).format(ep=endpoint, ra=root_access, rs=root_secret, pol=policy,
             user=_TITILER_RO_USER, ro=_TITILER_RO_USER, rosec=ro_secret)
    try:
        client = docker.from_env()
        out = client.containers.run(
            "minio/mc", entrypoint="/bin/sh", command=["-c", script],
            network=NETWORK, remove=True, stderr=True)
        if b"GEODEPLOY_RO_OK" in (out or b""):
            return _TITILER_RO_USER, ro_secret
    except Exception:
        pass
    return None


async def provision_local() -> dict:
    """Start the MinIO container and return S3 credentials."""
    access_key = _random_key(20)
    secret_key = _random_key(40)

    client = docker.from_env()

    network = client.networks.get(NETWORK)

    try:
        container = client.containers.get(CONTAINER_NAME)
        # Read the credentials the container was originally created with
        for env_var in container.attrs["Config"]["Env"] or []:
            if env_var.startswith("MINIO_ROOT_USER="):
                access_key = env_var.split("=", 1)[1]
            elif env_var.startswith("MINIO_ROOT_PASSWORD="):
                secret_key = env_var.split("=", 1)[1]
        if container.status != "running":
            container.start()
        # Reconnect with the service-name alias so Docker DNS resolves "minio"
        try:
            network.disconnect(container)
        except docker.errors.APIError:
            pass
        network.connect(container, aliases=["minio"])
    except docker.errors.NotFound:
        # Remove stale volume so MinIO initialises fresh with the new credentials
        try:
            client.volumes.get("geodeploy_minio").remove()
        except docker.errors.NotFound:
            pass
        container = client.containers.run(
            IMAGE,
            name=CONTAINER_NAME,
            detach=True,
            restart_policy={"Name": "unless-stopped"},
            command="server /data --console-address ':9001'",
            environment={
                "MINIO_ROOT_USER": access_key,
                "MINIO_ROOT_PASSWORD": secret_key,
            },
            volumes={"geodeploy_minio": {"bind": "/data", "mode": "rw"}},
        )
        network.connect(container, aliases=["minio"])

    await _wait_healthy(f"http://{CONTAINER_NAME}:9000", access_key, secret_key)

    endpoint = f"http://{CONTAINER_NAME}:9000"
    s3 = _make_client(endpoint, access_key, secret_key, "us-east-1")
    _ensure_bucket(s3, "geodeploy")

    # TiTiler gets a READ-ONLY key (never the root/write key). Falls back to root if the read-only
    # user can't be provisioned, so raster serving always works.
    ro = _ensure_readonly_user(endpoint, access_key, secret_key, "geodeploy")
    ti_access, ti_secret = ro if ro else (access_key, secret_key)
    _start_titiler(client, network, ti_access, ti_secret, endpoint)

    return {
        "type": "local",
        "endpoint": endpoint,
        "bucket": "geodeploy",
        "access_key": access_key,
        "secret_key": secret_key,
        "region": "us-east-1",
        # Creds for TiTiler (read-only if provisioned, else the root key). The setup wizard persists
        # these as TITILER_ACCESS_KEY/SECRET so compose recreates keep TiTiler on the scoped key.
        "titiler_access_key": ti_access,
        "titiler_secret_key": ti_secret,
    }


async def test_connection(endpoint: str, bucket: str, access_key: str, secret_key: str, region: str = "us-east-1") -> None:
    s3 = _make_client(endpoint, access_key, secret_key, region)
    try:
        s3.head_bucket(Bucket=bucket)
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code == "404":
            _ensure_bucket(s3, bucket)
        else:
            raise ValueError(f"Storage connection failed: {e}") from e


def _make_client(endpoint: str, access_key: str, secret_key: str, region: str):
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
        config=Config(signature_version="s3v4"),
    )


def _ensure_bucket(s3, bucket: str) -> None:
    try:
        s3.create_bucket(Bucket=bucket)
    except ClientError as e:
        if e.response["Error"]["Code"] not in ("BucketAlreadyExists", "BucketAlreadyOwnedByYou"):
            raise


# boto3 client construction costs ~100ms of CPU; the parquet range proxy serves dozens of tiny
# range requests per map pan, so a client-per-call burned the api at 100%+ CPU and pushed
# individual range requests to seconds. Clients are thread-safe for concurrent operations
# (boto3 docs) — cache one per credential set (keyed so a setup-wizard change takes effect).
_client_cache: dict[tuple, object] = {}


def get_s3_client():
    settings = get_settings()
    key = (settings.storage_endpoint, settings.storage_access_key,
           settings.storage_secret_key, settings.storage_region)
    client = _client_cache.get(key)
    if client is None:
        client = _make_client(*key)
        _client_cache.clear()  # one live credential set at a time
        _client_cache[key] = client
    return client


def presigned_upload_url(key: str, expires: int = 3600) -> str:
    s3 = get_s3_client()
    settings = get_settings()
    return s3.generate_presigned_url(
        "put_object",
        Params={"Bucket": settings.storage_bucket, "Key": key},
        ExpiresIn=expires,
    )


def browser_upload_url(key: str, expires: int = 3600) -> str:
    """A presigned PUT URL the BROWSER can reach.

    The local MinIO has an internal Docker hostname (geodeploy-minio:9000) that the browser
    can't resolve, so we strip the scheme+host and return a same-origin `/s3/...` path that
    nginx proxies to MinIO **with the signed Host preserved** — the SigV4 signature still
    verifies and, being same-origin, there's no CORS. For an external (public) S3 endpoint we
    return the full presigned URL (that bucket must allow cross-origin PUT from the dashboard).
    """
    settings = get_settings()
    url = presigned_upload_url(key, expires)
    if "geodeploy-minio" in (settings.storage_endpoint or ""):
        from urllib.parse import urlsplit
        parts = urlsplit(url)
        return f"/s3{parts.path}?{parts.query}" if parts.query else f"/s3{parts.path}"
    return url


def restart_titiler(endpoint: str, access_key: str, secret_key: str, region: str = "us-east-1") -> None:
    """(Re)create the TiTiler container for an arbitrary S3 endpoint (local MinIO or external).

    Used by the setup wizard's *existing storage* branch so an external HTTPS provider
    (AWS S3 / R2 / Backblaze / Hetzner) gets the right GDAL flags — the docker-compose
    defaults are tuned for the local MinIO (HTTP, path-style)."""
    client = docker.from_env()
    network = client.networks.get(NETWORK)
    _start_titiler(client, network, access_key, secret_key, endpoint, region)


def _start_titiler(client: docker.DockerClient, network, access_key: str, secret_key: str,
                   endpoint: str, region: str = "us-east-1") -> None:
    """Start the TiTiler raster tile server with storage credentials."""
    # GDAL VSI S3 expects host:port only — no http:// scheme. HTTPS is derived from the
    # endpoint so a real (HTTPS) S3 works; the local MinIO endpoint is http → "NO".
    use_https = endpoint.lower().startswith("https://")
    endpoint_for_gdal = endpoint.removeprefix("https://").removeprefix("http://")

    # Always recreate so updated credentials/endpoint are picked up
    try:
        client.containers.get(TITILER_NAME).remove(force=True)
    except docker.errors.NotFound:
        pass

    container = client.containers.run(
        TITILER_IMAGE,
        name=TITILER_NAME,
        detach=True,
        restart_policy={"Name": "unless-stopped"},
        environment={
            "AWS_ACCESS_KEY_ID": access_key,
            "AWS_SECRET_ACCESS_KEY": secret_key,
            "AWS_REGION": region or "us-east-1",
            "AWS_DEFAULT_REGION": region or "us-east-1",
            "AWS_S3_ENDPOINT": endpoint_for_gdal,
            "AWS_HTTPS": "YES" if use_https else "NO",
            "AWS_VIRTUAL_HOSTING": "FALSE",  # path-style works for MinIO/R2/B2/Hetzner and AWS
            "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
            "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.tiff",
            "WORKERS_PER_CORE": "1",
        },
    )
    network.connect(container, aliases=["titiler"])


async def _wait_healthy(endpoint: str, access_key: str, secret_key: str, retries: int = 30) -> None:
    import httpx
    for attempt in range(retries):
        try:
            async with httpx.AsyncClient() as http:
                r = await http.get(f"{endpoint}/minio/health/live", timeout=5)
                if r.status_code == 200:
                    return
        except Exception:
            pass
        if attempt == retries - 1:
            raise RuntimeError("MinIO did not become healthy.")
        await asyncio.sleep(2)
