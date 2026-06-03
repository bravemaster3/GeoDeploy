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
    bidx: list | None = None,
    settings=None,
) -> str:
    """
    Return a browser-accessible raster tile URL served through nginx's /raster/ proxy.

    - bidx: list of 1-based band indices for multiband rasters. One band → single-band
      output (a colormap may apply); three bands → an RGB composite (colormap ignored).
      Empty/None lets TiTiler pick its default bands.
    - colormap: a TiTiler colormap name (single-band data only).
    - rescale: "min,max" stretch applied before display (needed for non-8-bit data).
    - algorithm: a TiTiler algorithm such as "hillshade" (single-band DEM data).
    - zfactor: vertical exaggeration for hillshade — applied as a pre-scale expression
      (b1*z) so the DEM is exaggerated before the hillshade is computed.
    """
    if settings is None:
        settings = get_settings()
    cog_url = f"s3://{settings.storage_bucket}/{s3_key}"
    url = f"/raster/cog/tiles/WebMercatorQuad/{{z}}/{{x}}/{{y}}?url={cog_url}"
    bands = [b for b in (bidx or []) if b is not None]
    for b in bands:
        url += f"&bidx={b}"
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
    # colormap only makes sense for single-band output (one selected band, or a
    # single-band raster). It is ignored when an algorithm or an RGB composite is active.
    elif colormap and len(bands) != 3:
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
