"""TiTiler integration — raster tile URL construction."""
import json
from urllib.parse import quote

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
    band_count: int | None = None,
    color_classes: list | None = None,
    colormap_reverse: bool = False,
    settings=None,
) -> str:
    """
    Return a browser-accessible raster tile URL served through nginx's /raster/ proxy.

    - bidx: list of 1-based band indices for multiband rasters. One band → single-band
      output (a colormap may apply); three bands → an RGB composite (colormap ignored).
      Empty/None lets TiTiler pick its default bands.
    - colormap: a TiTiler colormap name (single-band data only).
    - colormap_reverse: flip the ramp. Low values take the colour high values had — which is what
      you want whenever the convention runs the other way (depth, deprivation, error), and it is
      not a cosmetic preference: a reversed ramp read as forward inverts the map's meaning.
    - color_classes: [{"value": n, "color": "#rrggbb"}] — an EXPLICIT colour per pixel value, for
      data that is classified rather than continuous (land cover, soil types, a QGIS paletted
      raster). Takes precedence over `colormap`, which can only describe a gradient.
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
    # A PNG carries at most four channels, and TiTiler appends the dataset mask as alpha. So a
    # 4-band multispectral raster — a drone Sequoia is GRE/RED/REG/NIR — asks the PNG driver for
    # FIVE, and EVERY tile 500s:
    #   "PNG driver doesn't support 5 bands. Must be 1 (grey), 2 (grey+alpha), 3 (rgb) or 4 (rgba)"
    # With no band selection TiTiler reads them all, so a raster with more than three bands needs an
    # explicit default. The first three are a false-colour composite for a multispectral sensor and
    # plain RGB for ordinary imagery — either way it renders, and the band pickers can change it.
    if not bands and band_count and band_count > 3:
        bands = [1, 2, 3]
    for b in bands:
        url += f"&bidx={b}"
    # `rescale` is a stretch over the DATA range, and TiTiler applies it AFTER the algorithm. Feed it
    # a hillshade — which is already a finished 0–255 relief image — and every pixel saturates to one
    # value: a flat tile that reads as "hillshade is not rendering". Measured on a vegetation index
    # whose range is 0.5563–0.9477: hillshade alone returns a 15 kB tile, hillshade + that rescale
    # returns 623 bytes of uniform colour. Exactly the reasoning that already drops `colormap` below.
    if rescale and algorithm != "hillshade":
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
    elif len(bands) != 3:
        explicit = _explicit_colormap(color_classes, colormap_reverse)
        if explicit:
            # A CLASSIFIED raster — land cover, soil types, a QGIS paletted layer — has a colour
            # per VALUE, which no named gradient can express: interpolating between class 3 and
            # class 4 is meaningless. TiTiler takes the mapping itself as JSON.
            url += f"&colormap={quote(explicit, safe='')}"
        elif colormap:
            # matplotlib — and so rio-tiler, and so TiTiler — spells a reversed ramp with an `_r`
            # suffix. Appended rather than stored that way, so the stored style still names the
            # palette a person chose and "reversed" stays a separate, toggleable fact.
            name = colormap[:-2] if colormap.endswith("_r") else colormap
            url += f"&colormap_name={name}_r" if colormap_reverse else f"&colormap_name={name}"
    return url


#: Every class travels in the URL of EVERY tile request, so the mapping cannot be unbounded — and
#: the limit is set by what a proxy will accept, not by taste. Percent-encoded JSON costs ~36 bytes
#: per class, so 256 classes produced a 9.2 kB request line: past nginx's default 8 kB
#: `large_client_header_buffers`, which would have meant every tile failing and the layer silently
#: never drawing. 128 lands near 4.6 kB, with room for a long object key.
#:
#: It is also far more than real classifications need — CORINE has 44 classes, NLCD about 20,
#: ESA WorldCover 11. Data with more distinct values than this is continuous in all but name, and a
#: named colormap is the right tool for it.
MAX_COLOR_CLASSES = 128


def _explicit_colormap(color_classes, reverse: bool = False) -> str | None:
    """`{"3": [r,g,b,a], …}` as compact JSON, or None when there is nothing usable.

    Silently skips entries that are not a number-plus-colour rather than failing the whole tile
    URL: a single bad class must not take the layer off the map.
    """
    if not color_classes:
        return None
    if reverse:
        # No name to suffix, so the colours themselves are re-paired with the values in the
        # opposite order — the values keep their places, the ramp runs the other way.
        entries = list(color_classes[:MAX_COLOR_CLASSES])
        colours = [e.get("color") if isinstance(e, dict) else None for e in entries][::-1]
        color_classes = [dict(e, color=c) if isinstance(e, dict) else e
                         for e, c in zip(entries, colours)]
    mapping = {}
    for entry in color_classes[:MAX_COLOR_CLASSES]:
        if not isinstance(entry, dict):
            continue
        rgba = _rgba(entry.get("color"))
        value = entry.get("value")
        if rgba is None or value is None:
            continue
        try:
            key = int(value)
        except (TypeError, ValueError):
            continue
        mapping[str(key)] = rgba
    if not mapping:
        return None
    return json.dumps(mapping, separators=(",", ":"))


def _rgba(color) -> list | None:
    """`#rrggbb` (or `#rrggbbaa`) to `[r, g, b, a]`. TiTiler wants 0–255 with alpha."""
    if not isinstance(color, str):
        return None
    text = color.strip().lstrip("#")
    if len(text) not in (6, 8):
        return None
    try:
        parts = [int(text[i:i + 2], 16) for i in range(0, len(text), 2)]
    except ValueError:
        return None
    return parts if len(parts) == 4 else parts + [255]


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
