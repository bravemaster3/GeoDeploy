"""TiTiler integration — raster tile URL construction."""
from ..config import get_settings


def get_tile_url(s3_key: str, settings=None) -> str:
    """Return the TiTiler XYZ tile URL for a COG stored in MinIO/S3."""
    if settings is None:
        settings = get_settings()
    cog_url = f"s3://{settings.storage_bucket}/{s3_key}"
    base = f"{settings.titiler_url}/cog/tiles/{{z}}/{{x}}/{{y}}"
    return f"{base}?url={cog_url}"


def get_tilejson_url(s3_key: str, settings=None) -> str:
    if settings is None:
        settings = get_settings()
    cog_url = f"s3://{settings.storage_bucket}/{s3_key}"
    return f"{settings.titiler_url}/cog/tilejson.json?url={cog_url}"


def get_info_url(s3_key: str, settings=None) -> str:
    if settings is None:
        settings = get_settings()
    cog_url = f"s3://{settings.storage_bucket}/{s3_key}"
    return f"{settings.titiler_url}/cog/info?url={cog_url}"
