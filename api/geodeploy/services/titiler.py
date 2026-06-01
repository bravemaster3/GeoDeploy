"""TiTiler integration — raster tile URL construction."""
from ..config import get_settings

COLORMAPS = [
    "viridis", "plasma", "inferno", "magma", "cividis",
    "gray", "rdylgn", "rdbu", "spectral", "terrain",
]


def get_tile_url(
    s3_key: str,
    colormap: str | None = None,
    rescale: str | None = None,
    algorithm: str | None = None,
    zfactor: float | str | None = None,
    settings=None,
) -> str:
    """
    Return a browser-accessible raster tile URL served through nginx's /raster/ proxy.

    - colormap: a TiTiler colormap name (single-band data).
    - rescale: "min,max" stretch applied before display (needed for non-8-bit data).
    - algorithm: a TiTiler algorithm such as "hillshade" (single-band DEM data).
    - zfactor: vertical exaggeration for hillshade — applied as a pre-scale expression
      (b1*z) so the DEM is exaggerated before the hillshade is computed.
    """
    if settings is None:
        settings = get_settings()
    cog_url = f"s3://{settings.storage_bucket}/{s3_key}"
    url = f"/raster/cog/tiles/WebMercatorQuad/{{z}}/{{x}}/{{y}}?url={cog_url}"
    if rescale:
        url += f"&rescale={rescale}"
    if algorithm:
        url += f"&algorithm={algorithm}"
        if algorithm == "hillshade":
            try:
                z = float(zfactor) if zfactor is not None else 1.0
            except (TypeError, ValueError):
                z = 1.0
            if z and z != 1.0:
                url += f"&expression=b1*{z}"
    elif colormap:  # colormap is ignored when an algorithm (e.g. hillshade) is active
        url += f"&colormap_name={colormap}"
    return url


def get_tilejson_url(s3_key: str, settings=None) -> str:
    if settings is None:
        settings = get_settings()
    cog_url = f"s3://{settings.storage_bucket}/{s3_key}"
    return f"{settings.titiler_url}/cog/WebMercatorQuad/tilejson.json?url={cog_url}"


def get_info_url(s3_key: str, settings=None) -> str:
    if settings is None:
        settings = get_settings()
    cog_url = f"s3://{settings.storage_bucket}/{s3_key}"
    return f"{settings.titiler_url}/cog/info?url={cog_url}"
