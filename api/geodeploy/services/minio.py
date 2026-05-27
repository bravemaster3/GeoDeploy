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


def _random_key(length: int = 32) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


async def provision_local() -> dict:
    """Start the MinIO container and return S3 credentials."""
    access_key = _random_key(20)
    secret_key = _random_key(40)

    client = docker.from_env()

    try:
        container = client.containers.get(CONTAINER_NAME)
        if container.status != "running":
            container.start()
    except docker.errors.NotFound:
        client.containers.run(
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
            network=NETWORK,
        )

    await _wait_healthy("http://minio:9000", access_key, secret_key)

    s3 = _make_client("http://minio:9000", access_key, secret_key, "us-east-1")
    _ensure_bucket(s3, "geodeploy")

    return {
        "type": "local",
        "endpoint": "http://minio:9000",
        "bucket": "geodeploy",
        "access_key": access_key,
        "secret_key": secret_key,
        "region": "us-east-1",
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


def get_s3_client():
    settings = get_settings()
    return _make_client(
        settings.storage_endpoint,
        settings.storage_access_key,
        settings.storage_secret_key,
        settings.storage_region,
    )


def presigned_upload_url(key: str, expires: int = 3600) -> str:
    s3 = get_s3_client()
    settings = get_settings()
    return s3.generate_presigned_url(
        "put_object",
        Params={"Bucket": settings.storage_bucket, "Key": key},
        ExpiresIn=expires,
    )


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
