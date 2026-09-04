"""TiTiler integration — raster tile URL construction."""
import json
import math
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
    increment: float | str | None = None,
    thickness: float | str | None = None,
    minz: float | str | None = None,
    maxz: float | str | None = None,
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
    - algorithm: a TiTiler algorithm such as "hillshade" or "contours" (single-band DEM data).
    - zfactor: vertical exaggeration for hillshade — applied as a pre-scale expression
      (b1*z) so the DEM is exaggerated before the hillshade is computed.
    - increment/thickness: CONTOUR spacing in data units, and line width in pixels.
    - minz/maxz: the value range the contour BACKGROUND is coloured over. Defaults to `rescale`,
      which is the only sane default — see below.
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
    # AN EXPLICIT COLOUR-PER-VALUE MAPPING IS KEYED ON THE RAW VALUES, so a stretch destroys it:
    # `rescale` linearly maps the data into 0–255 before the lookup, so a classification of 0/1/2
    # arrives as 0/127/255 and only the class whose number survives still matches a key — every
    # other class falls through to transparent. Measured on a live float32 mask with classes
    # 0/1/2: with the stretch, one class drew and the map was two-thirds empty; without it, all
    # three drew, exactly as QGIS shows them.
    explicit = _explicit_colormap(color_classes, colormap_reverse)
    # …but only when it will actually be APPLIED. An algorithm or a three-band composite ignores the
    # colormap entirely (see the branch below), and there the stretch is the only thing colouring
    # the raster — dropping it for a palette that is not being used would leave neither.
    uses_explicit = bool(explicit) and not algorithm and len(bands) != 3
    # CONTOURS CONSUMES THE STRETCH RATHER THAN BEING SUBJECT TO IT — see `_contour_params`. Sending
    # it as `&rescale=` as well would restretch a finished RGB image, the same mistake hillshade
    # already avoids.
    if rescale and algorithm not in ("hillshade", "contours") and not uses_explicit:
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
        elif algorithm == "contours":
            params = _contour_params(increment, thickness, minz, maxz, rescale)
            if params:
                url += f"&algorithm_params={quote(params, safe='')}"
    # colormap only makes sense for single-band output (one selected band, or a
    # single-band raster). It is ignored when an algorithm or an RGB composite is active.
    elif len(bands) != 3:
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


#: Every key of a raster style that changes the PICTURE. `opacity` is not here: it is applied by the
#: map, not by the tile server, and sending it would be a parameter TiTiler ignores.
STYLE_KEYS = ("colormap", "colormap_reverse", "rescale", "algorithm", "zfactor", "bidx",
              "color_classes", "increment", "thickness", "minz", "maxz")


def tile_url_from_style(s3_key: str, style: dict | None, band_count: int | None = None,
                        settings=None) -> str:
    """`get_tile_url` from a stored raster style dict — the entry point every caller should use.

    THE PROBLEM THIS SOLVES IS A COUNTING ONE. Seven places build a raster tile URL — the layer
    listing, the public index, the TileJSON, STAC assets, share links, the portal generator — and
    each hand-listed the same eight keyword arguments. Adding `colormap_reverse` meant editing seven
    call sites correctly; adding the contour keys would have meant it again, and a call site that is
    merely FORGOTTEN does not fail. It silently serves the layer in a style nobody chose, which is
    exactly the class of bug that made a portal's raster draw in the layer's default colours.

    One unpacking point, driven by `STYLE_KEYS`, so a new raster property reaches every surface the
    moment the builder understands it.
    """
    style = style if isinstance(style, dict) else {}
    kwargs = {k: style.get(k) for k in STYLE_KEYS}
    kwargs["colormap_reverse"] = bool(style.get("colormap_reverse"))
    return get_tile_url(s3_key, band_count=band_count, settings=settings, **kwargs)


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


#: TiTiler's own defaults for the contours algorithm. `minz`/`maxz` are the giveaway: they span the
#: whole earth, from the Mariana Trench to Everest, because the algorithm is written for global DEMs.
CONTOUR_INCREMENT = 35
CONTOUR_THICKNESS = 1
_CONTOUR_MINZ, _CONTOUR_MAXZ = -12000.0, 8000.0

#: THE BOUNDS TITILER ITSELF DECLARES, read from `GET /algorithms/contours` on the running image:
#:
#:     increment  integer  0 – 999
#:     thickness  integer  0 – 10
#:     minz/maxz  integer  ±99999
#:
#: **Every one of them is an `integer`.** `increment` used to be sent as a float, which TiTiler
#: accepted while it was typed loosely; it now rejects the whole tile request with a 422 for a
#: fractional one — and a raster whose tiles all 422 does not draw at all, so a contour layer that
#: had been working simply vanished after an update. TiTiler runs from `:latest` here, so its
#: contract can tighten under us without any change on our side — this is the second parameter to go
#: that way, after `minz`/`maxz` (clamped a few lines below, for the same reason).
CONTOUR_MAX_INCREMENT = 999
CONTOUR_MAX_THICKNESS = 10
CONTOUR_Z_LIMIT = 99999


def _contour_params(increment, thickness, minz, maxz, rescale) -> str | None:
    """`algorithm_params` JSON for the contours algorithm, or None when there is nothing to send.

    WHY `minz`/`maxz` DEFAULT TO THE STRETCH, which is the whole reason this function exists rather
    than three inline lines: TiTiler's contours does not draw lines on a blank page. It colours the
    data across `minz`–`maxz` with a built-in terrain ramp and draws the lines ON that. Its defaults
    span −12000 to 8000 m — the range of the planet — so a survey DEM covering 183–316 m falls
    inside a single band of that ramp and renders as **a flat khaki rectangle with a few stray
    lines**. Measured on this project's own instance: the same raster is one colour with the
    defaults and a legible coloured relief once the range is its own.

    GeoDeploy already knows that range — it is `rescale`, the stretch every raster carries and the
    ⚡ Auto button computes — so contours borrows it instead of asking the user for the same two
    numbers a second time. An explicit `minz`/`maxz` still wins, for the case where the contour
    background should span something other than the data.
    """
    values: dict = {}
    for key, value, default, top in (
            ("increment", increment, CONTOUR_INCREMENT, CONTOUR_MAX_INCREMENT),
            ("thickness", thickness, CONTOUR_THICKNESS, CONTOUR_MAX_THICKNESS)):
        try:
            number = float(value) if value not in (None, "") else float(default)
        except (TypeError, ValueError):
            number = float(default)
        if number > 0:
            # ROUNDED AND CLAMPED, because TiTiler types both as integers with bounds and answers a
            # 422 for anything outside them — which fails every tile and hides the layer entirely.
            # A contour interval of 12.5 becomes 13: a slightly different spacing is a far better
            # outcome than a raster that does not draw, and the alternative is refusing a number the
            # UI happily accepted.
            # `int(x + 0.5)`, NOT `round()`. Python rounds half to EVEN and JavaScript rounds half
            # UP, so an interval of 12.5 would become 12 here and 13 in `mapStyle.js` — the editor
            # preview and the published portal drawing contours at different spacings. Half-up is
            # expressible identically in both, which is the same fix `ramp_colors` needed.
            values[key] = max(1, min(top, int(number + 0.5)))
    lo, hi = _range_of(minz, maxz, rescale)
    if lo is not None:
        # INTEGERS, and not by preference: TiTiler types `minz`/`maxz` as `int` and rejects the whole
        # tile request with a 422 for a fractional one — `input_value=182.789993` — which matters
        # because the stretch these borrow from is very often fractional (a stored DEM range here is
        # `182.789993,315.959992`). Floor and ceil rather than round, so the band always WIDENS to
        # contain the data instead of clipping the extremes to a flat colour.
        values["minz"] = max(-CONTOUR_Z_LIMIT, math.floor(lo))
        values["maxz"] = min(CONTOUR_Z_LIMIT, math.ceil(hi))
    return json.dumps(values, separators=(",", ":")) if values else None


def _range_of(minz, maxz, rescale):
    """`(minz, maxz)` from the explicit pair, else from the `"min,max"` stretch, else `(None, None)`."""
    try:
        if minz not in (None, "") and maxz not in (None, ""):
            lo, hi = float(minz), float(maxz)
            if hi > lo:
                return (lo, hi)
    except (TypeError, ValueError):
        pass
    if isinstance(rescale, str) and "," in rescale:
        try:
            lo, hi = (float(v) for v in rescale.split(",", 1))
            if hi > lo:
                return (lo, hi)
        except (TypeError, ValueError):
            pass
    return (None, None)


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
