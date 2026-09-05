"""TiTiler integration — raster tile URL construction."""
import json
import math
import re
from urllib.parse import quote

from ..config import get_settings
from . import symbology

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
    contour_palette: str | None = None,
    contour_color: str | None = None,
    contour_line_palette: bool | None = None,
    # `None`, NOT `True`: `tile_url_from_style` unpacks every key of a stored style through
    # `STYLE_KEYS`, and an absent one arrives as None. A default of True would still be overridden
    # by that None, so every layer that had never set it would render with its relief switched OFF —
    # the default silently inverted for every existing contour layer. None means "not stated".
    contour_relief: bool | None = None,
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
                url += f"&expression=b{_expression_band(bands)}*{z}"
        elif algorithm == "contours":
            lo, hi = _range_of(minz, maxz, rescale)
            step = increment if increment not in (None, "") else _default_increment(lo, hi)
            scale = _contour_scale(step, lo, hi)
            # A line that follows the palette, or a relief switched off, is something TiTiler's
            # algorithm cannot express either — so both force GeoDeploy's own drawing path.
            relief = contour_relief is not False       # absent means "yes", see the signature
            plain = (not contour_line_palette) and relief
            if plain and _contours_are_default(contour_palette, contour_color):
                # TiTiler's own algorithm draws exactly this, so use it and keep the URL
                # byte-identical to what every existing contour layer already has.
                if scale != 1:
                    # The data is multiplied BEFORE the algorithm runs, which is the only way to
                    # ask TiTiler for an interval finer than 1 — see `_contour_scale`.
                    url += f"&expression=b{_expression_band(bands)}*{scale}"
                params = _contour_params(increment, thickness, minz, maxz, rescale, scale)
                if params:
                    url += f"&algorithm_params={quote(params, safe='')}"
            else:
                # A CHOSEN palette or line colour, which the algorithm cannot express — it hard-codes
                # `terrain` and black. Reproduced as band maths plus an explicit colormap; see
                # `_contour_expression`. No `algorithm=` at all, so this replaces it rather than
                # layering on top: running both would contour an already-coloured RGB image.
                url = url.replace("&algorithm=contours", "")
                values = json.loads(_contour_params(increment, thickness, minz, maxz,
                                                    rescale, scale) or "{}")
                band = _expression_band(bands)
                # `scale` multiplies the data so a sub-unit interval is expressible; here the
                # interval is ours to choose freely, so the data is left alone and the raw interval
                # is used — one fewer transform between the number typed and the lines drawn.
                expr = _contour_expression(
                    band, float(step), values.get("thickness", CONTOUR_THICKNESS),
                    lo if lo is not None else _CONTOUR_MINZ,
                    hi if hi is not None else _CONTOUR_MAXZ,
                    bool(contour_line_palette))
                palette, colour = _contour_colours(contour_palette, contour_color)
                url += f"&expression={quote(expr, safe='')}"
                cmap = _contour_colormap(palette, colour, bool(contour_line_palette), relief)
                url += f"&colormap={quote(cmap, safe='')}"
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



def _expression_band(bands) -> int:
    """Which band an `expression` should read.

    `bN` NAMES A BAND OF THE DATASET, and `&bidx=` is IGNORED whenever an expression is present —
    measured against the running image on a two-band raster whose band 1 is flat and band 2 a ramp:
    `bidx=2&expression=b1*1000` contours the flat band and returns one colour, while
    `bidx=2&expression=b2*1000` returns 77. So the expression has to carry the band selection
    itself; leaving `b1` hard-coded silently renders the wrong band, which looks like plausible
    data rather than a fault. (This is why the hillshade z-factor above uses it too — it had `b1`
    written in, so exaggerating a multiband raster hillshaded band 1 whatever the user picked.)
    """
    return int(bands[0]) if bands else 1


#: MapLibre's own terrain exaggeration bounds. 0 is flat (and so pointless); past about 10 a DEM
#: becomes spikes rather than relief.
TERRAIN_MIN_EXAGGERATION = 0.1
TERRAIN_MAX_EXAGGERATION = 10.0
TERRAIN_DEFAULT_EXAGGERATION = 1.5


def terrain_tile_url(s3_key: str, settings=None) -> str:
    """Tiles of a DEM encoded as Mapbox Terrain-RGB, for a MapLibre `raster-dem` source.

    THIS IS A SECOND URL FOR THE SAME RASTER, not a restyling of the first. A DEM drawn as terrain
    is doing two different jobs at once: it is a picture (coloured, hillshaded, contoured — whatever
    the layer's style says) and it is a HEIGHTFIELD that deforms the map. MapLibre reads the second
    from its own source type, in an encoding that is not a picture at all — R, G and B are the bytes
    of a number — so a colormap or a stretch applied to it would corrupt the heights rather than
    style them. Hence no style keys here, deliberately.

    TiTiler's `terrainrgb` defaults are `interval=0.1` and `baseval=-10000`, which IS the Mapbox
    encoding, so nothing needs stating and MapLibre's `encoding: "mapbox"` reads it directly.
    """
    if settings is None:
        settings = get_settings()
    cog_url = f"s3://{settings.storage_bucket}/{s3_key}"
    return (f"/raster/cog/tiles/WebMercatorQuad/{{z}}/{{x}}/{{y}}.png"
            f"?url={cog_url}&algorithm=terrainrgb")


def terrain_of(style: dict) -> dict | None:
    """`{"exaggeration": n}` when a raster style asks to be the map's terrain, else None.

    MapLibre's terrain is a property of the MAP, not of a layer — one heightfield deforms
    everything — so this is "use this raster as the terrain", and a portal with two such rasters
    uses the first. That is a real limitation of the renderer rather than a choice, and the panel
    says so rather than letting an author wonder why the second did nothing.
    """
    block = (style or {}).get("terrain")
    if not isinstance(block, dict) or not block.get("enabled"):
        return None
    try:
        value = float(block.get("exaggeration", TERRAIN_DEFAULT_EXAGGERATION))
    except (TypeError, ValueError):
        value = TERRAIN_DEFAULT_EXAGGERATION
    return {"exaggeration": min(max(value, TERRAIN_MIN_EXAGGERATION), TERRAIN_MAX_EXAGGERATION)}

#: Every key of a raster style that changes the PICTURE. `opacity` is not here: it is applied by the
#: map, not by the tile server, and sending it would be a parameter TiTiler ignores.
STYLE_KEYS = ("colormap", "colormap_reverse", "rescale", "algorithm", "zfactor", "bidx",
              "color_classes", "increment", "thickness", "minz", "maxz",
              "contour_palette", "contour_color", "contour_line_palette", "contour_relief")


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

#: THE FACTORS THE DATA MAY BE MULTIPLIED BY before contouring. See `_contour_scale`.
CONTOUR_SCALES = (10, 100, 1000, 10000, 100000)


#: A tidy interval is one of these times a power of ten. The set every contour map uses — nobody
#: labels a contour "every 3.7 units" — and the reason the default is snapped rather than left as
#: `range / 10`.
_NICE_STEPS = (1.0, 2.0, 2.5, 5.0, 10.0)

#: Roughly how many lines across the data a default should draw. Ten reads as relief; two reads as
#: a mistake and fifty as hatching.
CONTOUR_TARGET_LINES = 10


def _default_increment(lo, hi) -> float:
    """The interval to use when the author has only ticked the box, derived from the DATA.

    TiTiler's own default is 35, which is a sensible contour interval for a global DEM in metres and
    a catastrophic one for anything else. The vegetation index that prompted this ranges 0.5563 to
    0.9477: at 35 — or at any integer, which is all TiTiler's `increment` could express before
    `_contour_scale` — the whole raster falls inside a single band and the tile is one flat colour.
    Ticking "Contour lines" therefore turned the layer into a dark rectangle, and no value the UI
    would accept could fix it. Reported exactly that way.

    So the default follows the data, snapped to a tidy number: this is the same lesson as
    `symbology.pillar_radius`, where a hard-coded 30 m default made bars about three thousandths of
    a pixel wide on a world map — rendered perfectly, and completely invisible. A default that
    depends on the data means ticking the box shows something, and the author adjusts from there
    rather than guessing which number makes anything appear at all.
    """
    try:
        span = float(hi) - float(lo)
    except (TypeError, ValueError):
        return float(CONTOUR_INCREMENT)
    if not (span > 0):
        return float(CONTOUR_INCREMENT)
    raw = span / CONTOUR_TARGET_LINES
    power = math.floor(math.log10(raw))
    base = 10.0 ** power
    for step in _NICE_STEPS:
        if raw <= step * base * 1.0000001:
            return step * base
    return 10.0 * base


def _contour_scale(increment, lo, hi) -> int:
    """The power of ten to multiply the DATA by so that an integer interval can express `increment`.

    THE PROBLEM: TiTiler's contour interval is an `integer`, minimum 0 — so the finest interval it
    can take is **1**. A vegetation index ranging 0.5563–0.9477 is narrower than that, end to end.
    Every pixel lands in one band and the tile comes back as a single flat colour: not an error, not
    a missing layer, just a dark rectangle. Reported as "the layer displays all dark, and I can't
    change the interval to an appropriate value", which is exactly right — there is no appropriate
    integer.

    THE FIX: contour a scaled copy. `expression=b1*1000` multiplies the data before the algorithm
    sees it, so that same index becomes 556–947 and an interval of 0.05 becomes a perfectly ordinary
    50. Verified against the running image on a 0.556–0.947 float raster: at `increment=1` the tile
    holds **one** distinct colour, and with the expression at `increment=50` it holds **77**.

    The multiplier is chosen so the scaled interval has two significant digits — enough that
    rounding it to an integer costs nothing visible — and then held back to whatever still fits
    TiTiler's own bounds, since `increment` may not exceed 999 and `minz`/`maxz` may not exceed
    ±99999. A DEM in metres never reaches this code at all: an interval of 1 or more is already
    expressible, so the URL is byte-identical to what it was.
    """
    try:
        step = float(increment)
    except (TypeError, ValueError):
        return 1
    if not (0 < step < 1):
        return 1
    extreme = 0.0
    for edge in (lo, hi):
        try:
            extreme = max(extreme, abs(float(edge)))
        except (TypeError, ValueError):
            continue
    best = 1
    for factor in CONTOUR_SCALES:
        if step * factor > CONTOUR_MAX_INCREMENT:
            break
        if extreme and extreme * factor > CONTOUR_Z_LIMIT:
            break
        if step * factor >= 1:
            best = factor
        if step * factor >= 10:
            break
    return best



#: How many colour bands the contour BACKGROUND is drawn in when GeoDeploy colours it itself.
#: Every band is an interval in the colormap and every interval rides in the URL of every tile
#: request, so this is bounded by what a proxy accepts, exactly as `MAX_COLOR_CLASSES` is: ~70 bytes
#: per interval percent-encoded, so 32 lands near 2.4 kB and leaves room for a long object key under
#: nginx's 8 kB `large_client_header_buffers`. It is also enough: the background of a contour map is
#: read as bands between the lines, not as a continuous surface, and hypsometric tints are banded by
#: convention anyway.
#: A `#rgb` or `#rrggbb` colour. Anything else came from a client and is refused
#: rather than interpolated into a URL.
_HEX = re.compile(r"^#(?:[0-9a-f]{3}|[0-9a-f]{6})$")

CONTOUR_BANDS = 32

#: The line colour TiTiler's own algorithm bakes in, and the palette it bakes in. When the style asks
#: for exactly these, the algorithm is used unchanged and the URL is byte-identical to what every
#: existing contour layer already has.
CONTOUR_DEFAULT_COLOR = "#000000"
CONTOUR_DEFAULT_PALETTE = "terrain"


def _contour_colours(style_palette, style_color) -> tuple:
    """`(palette, line_colour)` for a contour layer, defaulted to what TiTiler itself draws."""
    palette = (style_palette or CONTOUR_DEFAULT_PALETTE).strip().lower()
    colour = (style_color or CONTOUR_DEFAULT_COLOR).strip().lower()
    if palette not in symbology.RAMPS:
        palette = CONTOUR_DEFAULT_PALETTE
    if not _HEX.match(colour):
        colour = CONTOUR_DEFAULT_COLOR
    return palette, colour


def _contours_are_default(style_palette, style_color) -> bool:
    """True when TiTiler's own algorithm draws exactly what was asked for.

    Worth its own function because the answer decides between two completely different URLs, and
    "the default still uses the algorithm" is the promise that keeps every published contour layer
    rendering exactly as it did.
    """
    palette, colour = _contour_colours(style_palette, style_color)
    return palette == CONTOUR_DEFAULT_PALETTE and colour == CONTOUR_DEFAULT_COLOR


#: How wide a contour line is, as a FRACTION of the interval, per unit of `thickness`.
#:
#: TiTiler's own test is `data % increment < thickness`, where `thickness` is in DATA UNITS despite
#: being described as a line width — it only looks like a pixel width because the algorithm's
#: default interval is 35 metres, so `< 1` selects about 3% of the gap. GeoDeploy's own expression
#: does NOT scale the data (it can use the real interval directly), so a thickness of 1 against an
#: interval of 0.05 made `b1 % 0.05 < 1` true for EVERY pixel: the whole raster came back as one
#: flat sheet of line colour. Measured — 1 distinct colour across the tile.
#:
#: Expressed as a fraction of the interval instead, a line is the same relative width whatever the
#: data measures. 0.03 is chosen to match what TiTiler draws at its own defaults (1/35 = 0.029), so
#: the default picture is unchanged.
CONTOUR_LINE_FRACTION = 0.03


def _contour_expression(band: int, increment, thickness, lo: float, hi: float,
                        line_palette: bool = False) -> str:
    """The contour picture as band maths, so the COLOURS become ours.

    TiTiler's `Contours` algorithm hard-codes both halves of what it draws:

        arr = linear_rescale(data, (minz, maxz), (1, 255))
        arr, _ = apply_cmap(arr, cmap.get("terrain"))            # the palette
        arr = numpy.where(data % increment < thickness, 0, arr)  # black lines

    Neither is a parameter, so a layer could not be given a different palette or a line colour that
    is not black. This reproduces it with an expression — verified operator by operator against the
    running image: `%`, comparisons and `where()` all survive the parser — and leaves the colouring
    to an explicit colormap, which is where a choice can finally be made.

    A line pixel becomes 0. Everything else becomes a BAND NUMBER, 1..CONTOUR_BANDS, clamped at both
    ends by nested `where`s: an unclamped value outside the stretch lands in no interval at all, and
    a value the colormap does not cover is drawn TRANSPARENT — holes punched in the map exactly
    where the data is highest and lowest.
    """
    span = float(hi) - float(lo)
    if span <= 0:
        span = 1.0
    step = float(increment) or 1.0
    # In DATA UNITS, as a fraction of the interval — see `CONTOUR_LINE_FRACTION`.
    width = max(step * CONTOUR_LINE_FRACTION * max(float(thickness or 1), 1.0), step * 1e-4)
    top = CONTOUR_BANDS
    # The band a pixel falls in, clamped at both ends. Written once and substituted twice below,
    # because a line has to know its own band too when the lines follow the palette.
    band_expr = ("where(b{b}<={lo},1,"
                 "where(b{b}>={hi},{top},"
                 "1+{steps}*(b{b}-{lo})/{span}))").format(
                     b=band, lo=_trim(lo), hi=_trim(hi), top=top, steps=top - 1,
                     span=_trim(span))
    if not line_palette:
        # One colour for every line: band 0, which the colormap paints with it.
        return "where((b{b}%{inc})<{thick},0,{band})".format(
            b=band, inc=_trim(step), thick=_trim(width), band=band_expr)
    # LINES COLOURED BY THEIR OWN VALUE. A line takes its band number SHIFTED past the background's
    # range, so 1..N is the relief and N+1..2N is the lines — one number carrying both facts, which
    # is the only way to say "this pixel is a line, and it is this high" in a single band of output.
    return "where((b{b}%{inc})<{thick},{top}+{band},{band})".format(
        b=band, inc=_trim(step), thick=_trim(width), top=top, band=band_expr)


def _contour_colormap(palette: str, line_colour: str, line_palette: bool = False,
                      relief: bool = True) -> str:
    """The explicit colormap the expression above is coloured through, as JSON.

    THE INTERVAL FORM — `[[[lo, hi], [r, g, b, a]], …]` — not the discrete `{value: colour}` one.
    The expression produces FLOATS, and a discrete map matches exact integers only: measured against
    the running image, the discrete form coloured 156 pixels of a tile where the interval form
    coloured 2704. A contour map with 94% of its pixels missing is not a subtle difference.

    Band 0 is the lines. Bands 1..N are the palette, interpolated by the same `ramp_colors` the
    graduated symbology uses — so a raster's contour background and a vector layer's classes drawn
    from the same named ramp are the same colours.
    """
    ramp = symbology.ramp_colors(palette, CONTOUR_BANDS)
    # TRANSPARENT, not white: with the relief switched off the lines are meant to be read over the
    # basemap, and a white background would hide it just as thoroughly as a coloured one.
    ground = [_rgba(c) for c in ramp] if relief else [[0, 0, 0, 0]] * CONTOUR_BANDS
    intervals = []
    if not line_palette:
        intervals.append([[-0.5, 0.5], _rgba(line_colour)])
    for i, colour in enumerate(ground, start=1):
        intervals.append([[i - 0.5, i + 0.5], colour])
    if line_palette:
        # The second half of the range: the same band, drawn as a LINE. `1..N` is the relief and
        # `N+1..2N` the lines, matching the shift `_contour_expression` applies.
        for i, hexcolour in enumerate(ramp, start=1):
            intervals.append([[CONTOUR_BANDS + i - 0.5, CONTOUR_BANDS + i + 0.5],
                              _rgba(hexcolour)])
    return json.dumps(intervals, separators=(",", ":"))


def _rgba(hexcolour: str) -> list:
    """`#rrggbb` → `[r, g, b, 255]`. Opaque: transparency in a contour background would show the
    basemap through the relief, which reads as a rendering fault rather than a choice."""
    value = (hexcolour or "").lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    try:
        return [int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16), 255]
    except (ValueError, IndexError):
        return [0, 0, 0, 255]


def _trim(value: float) -> str:
    """A number for an expression string: no trailing `.0`, and never scientific notation — the
    parser reads `1e-05` as a name, not a number."""
    out = "{0:.6f}".format(float(value)).rstrip("0").rstrip(".")
    return out or "0"


def _contour_params(increment, thickness, minz, maxz, rescale, scale: int = 1) -> str | None:
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
    lo, hi = _range_of(minz, maxz, rescale)
    for key, value, default, top in (
            ("increment", increment, _default_increment(lo, hi), CONTOUR_MAX_INCREMENT),
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
            # SCALED FIRST for the interval, because the data it measures was scaled too — see
            # `_contour_scale`. `thickness` is a width in PIXELS, not a data value, so it is not.
            if key == "increment":
                number *= scale
            values[key] = max(1, min(top, int(number + 0.5)))
    if lo is not None:
        # INTEGERS, and not by preference: TiTiler types `minz`/`maxz` as `int` and rejects the whole
        # tile request with a 422 for a fractional one — `input_value=182.789993` — which matters
        # because the stretch these borrow from is very often fractional (a stored DEM range here is
        # `182.789993,315.959992`). Floor and ceil rather than round, so the band always WIDENS to
        # contain the data instead of clipping the extremes to a flat colour.
        values["minz"] = max(-CONTOUR_Z_LIMIT, math.floor(lo * scale))
        values["maxz"] = min(CONTOUR_Z_LIMIT, math.ceil(hi * scale))
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
