"""Which URL to hand QGIS for a given layer — the plugin's one real piece of judgement.

A GeoDeploy layer is published through several surfaces at once, and they are not interchangeable:

* **PMTiles** is one pre-generalized archive, opened through the ordinary vector-layer path on
  GDAL 3.8+ (with `/vsicurl/` — see below). Geometry is generalized per zoom, features are clipped
  to tile boundaries, and attributes are only what the tiles carry.
* **Vector tiles (MVT)** are the fast way to DRAW a PostGIS layer: generalized per zoom, cut
  server-side, fetched for the viewport. A 350k-feature road layer draws like a small one.
* **OGC API - Features** is the DATA: full attributes, exact geometry. QGIS has read it natively
  since 3.16, and its provider is VIEWPORT-DRIVEN — it asks the server for the extent on screen.
  Correct and complete, and slow enough on a large layer to read as broken — which is why it is no
  longer the default.
* **COG** is a raster's real pixel values, read by range request through `/vsicurl/` — and
  rendered by QGIS's own defaults, NOT by GeoDeploy's styling.
* **Raster tiles** are the same raster as the portal draws it: colormap, stretch and band choice
  baked in by the server. A picture, not measurements.

A correction worth stating plainly, because the first version of this file got it wrong and the
error is easy to repeat: **PMTiles being fast in MapLibre does not make it fast in QGIS.** A web map
fetches only the tiles under the viewport. OGR does not — it opens the archive as an ordinary
dataset and reads every feature at the archive's zoom level, with no notion of a viewport. So
PMTiles is one bounded download with no per-pan server round-trip, while OAPIF asks the server again
on every pan but only ever for what is visible. Which of those is "faster" depends on the layer and
on how the user moves around it, and neither is universally right.

So the rule below is a default, not a verdict, and the caller can override it.
"""
from __future__ import annotations

import json
import re
from urllib.parse import quote

# GDAL gained its PMTiles driver in 3.8. Below that the archive cannot be opened at all, so the
# choice is not a preference — it is availability. Checked at runtime rather than assumed from the
# QGIS version, because the two do not move together.
_PMTILES_MIN_GDAL = (3, 8)


def gdal_supports_pmtiles() -> bool:
    try:
        from osgeo import gdal
    except ImportError:      # pragma: no cover - QGIS always ships GDAL
        return False
    try:
        parts = gdal.__version__.split(".")
        version = (int(parts[0]), int(parts[1]))
    except (AttributeError, IndexError, ValueError):
        return False
    return version >= _PMTILES_MIN_GDAL


def _link(layer: dict, link_id: str) -> str | None:
    """A URL from the layer's own `links` list, which the public index provides.

    Asking the instance beats deriving: it knows which surfaces it actually serves, and the list is
    the same one the share-links dialog shows a human.
    """
    for link in (layer.get("links") or []):
        if isinstance(link, dict) and link.get("id") == link_id and link.get("url"):
            return str(link["url"])
    return None


def describe(layer: dict, prefer_attributes: bool = False) -> dict | None:
    """`{kind, uri, provider, why}` for the best source, or None when there is nothing to add.

    `layer` is a row from the instance's own listing (public index or authenticated list), so this
    works with or without a credential.
    """
    base = (layer.get("_base") or "").rstrip("/")
    ref = layer.get("uid") or layer.get("id")
    if not base or ref is None:
        return None

    if layer.get("layer_type") == "raster" or layer.get("kind") == "raster":
        # THE COG IS THE DATA; THE TILES ARE THE PICTURE.
        #
        # Opening the COG gives real pixel values — what analysis needs — but QGIS then renders it
        # with ITS defaults, so a raster carefully coloured in GeoDeploy arrives as a grey stretch
        # and looks unstyled. The tile URL is the opposite: the server bakes the colormap, rescale,
        # band selection and hillshade into the image, so it looks exactly like the portal, but the
        # pixels are display colours and the values are gone.
        #
        # Neither is "correct", so the same switch that chooses attributes over speed for a vector
        # chooses values over appearance here.
        # THE SERVER PUBLISHES A TILEJSON FOR EXACTLY THIS, and its own hint says why: it "carries
        # the tile template AND the layer bounds, so 'zoom to layer' works (a bare XYZ URL has no
        # bounds)". Both problems this path had, answered by the instance rather than by us.
        #
        # It is also the only styled source an ANONYMOUS caller can use: the public index omits
        # `tile_url`, so without this a public raster fell back to the COG and arrived unstyled.
        # WMTS FIRST, because QGIS speaks it natively and the instance labels the link "for QGIS".
        #
        # An XYZ layer is a bare tile template: QGIS knows no extent and no zoom range, so it asks
        # for z0/z1/z2 tiles that miss the raster entirely, retries each three times, and fills the
        # log with failures while showing nothing. The WMTS capabilities carry the layer's
        # WGS84BoundingBox and its tile matrix set, so QGIS asks only for tiles that exist.
        wmts = _link(layer, "wmts") or f"{base}/api/data/raster/{ref}/wmts"
        if wmts and not prefer_attributes:
            return {
                "kind": "wmts",
                "wmts_url": wmts,
                "provider": "wms",      # WMTS is served through the WMS provider
                "why": "server-rendered tiles with the layer's own bounds, coloured as GeoDeploy draws it",
            }

        tilejson = _link(layer, "tilejson") or f"{base}/api/data/raster/{ref}/tilejson"
        if tilejson and not prefer_attributes:
            return {
                "kind": "tilejson",
                "tilejson_url": tilejson,
                "provider": "wms",
                "why": "server-rendered tiles, coloured exactly as GeoDeploy draws it",
            }

        tiles = (layer.get("tile_url") or "").strip()
        if tiles and not prefer_attributes:
            # Relative to the instance (that is how the API returns it), and QGIS's XYZ provider
            # wants the template URL-encoded inside the connection string.
            url = tiles if tiles.startswith("http") else f"{base}{tiles}"
            return {
                "kind": "xyz",
                # The TEMPLATE, unencoded. The provider URI is assembled by QgsDataSourceUri in
                # `plugin._build_layer`, because hand-rolling the percent-encoding of a URL that
                # itself carries a query string (`?url=s3://…&rescale=…`) is the one part of this
                # path that was never verified — and the server side provably works.
                "tile_url": url,
                "uri": f"type=xyz&url={quote(url, safe='')}&zmin=0&zmax=22",
                "provider": "wms",          # QGIS serves XYZ through the WMS provider
                "why": "server-rendered tiles, coloured exactly as GeoDeploy draws it",
            }
        return {
            "kind": "cog",
            "uri": f"/vsicurl/{base}/api/data/raster/{ref}/cog",
            "provider": "gdal",
            "why": ("the Cloud-Optimized GeoTIFF itself — real pixel values, drawn with QGIS's own "
                    "defaults rather than GeoDeploy's styling"),
        }

    # VECTOR TILES, FOR BOTH BACKENDS, DESCRIBED BY A TILEJSON.
    #
    # One path now covers PostGIS (Martin builds the tiles from the table) and tiled GeoParquet (the
    # server reads them out of the PMTiles archive a tile at a time). The plugin does not need to
    # know which: it reads the TileJSON, which carries the tile template, the bounds, the real zoom
    # range and the name of the layer inside the tiles.
    #
    # Reading it rather than guessing is what fixes three separate complaints at once — "zoom to
    # layer goes somewhere the layer is not" (no bounds), "nothing displays after zooming in"
    # (requests past the deepest real zoom come back empty instead of over-zooming the last good
    # tile), and the retry storms in the log.
    tilejson = _link(layer, "tilejson")
    tiled = (bool(layer.get("pmtiles_key")) or layer.get("tile_status") == "ready"
             or bool(_link(layer, "pmtiles")))
    if not tilejson and (layer.get("storage_backend") == "postgis" or tiled):
        tilejson = f"{base}/api/data/vector/{ref}/tilejson"
    if tilejson and not prefer_attributes:
        return {
            "kind": "vector-tiles",
            "tilejson_url": tilejson,
            # Only used if the TileJSON cannot be read — see `_build_layer`.
            "uri": _link(layer, "xyz-mvt") or "",
            "source_layer": layer.get("source_layer") or _mvt_source_layer(layer),
            "provider": "vectortile",
            "why": "vector tiles, generalized per zoom and fetched for the view",
        }

    # LAST RESORT FOR A TILED LAYER, and deliberately last.
    #
    # Opening the archive with GDAL looks like the obvious fast path and is the opposite. The driver
    # has no viewport: it presents the archive as one dataset, so QGIS's opening questions — feature
    # count, extent — walk every tile at the deepest zoom over HTTP. Measured on this project's own
    # instance, a FIVE-FEATURE layer tiles to 2,171,238 entries across zooms 0–13, which is why the
    # small layers hung worst. Only reached when the instance publishes no TileJSON (an older
    # build), where it still beats paging millions of features through OAPIF.
    pmtiles_link = _link(layer, "pmtiles")
    if tiled and not prefer_attributes and pmtiles_link and gdal_supports_pmtiles():
        return {
            "kind": "pmtiles",
            # `/vsicurl/` is not optional. Handed the bare URL, GDAL looks for a file of that name
            # on disk and fails with "does not exist in the file system".
            "uri": "/vsicurl/" + pmtiles_link,
            "provider": "ogr",
            "why": ("the whole tile archive — this instance publishes no TileJSON for it, so QGIS "
                    "has to read the archive rather than a viewport, and opening it is slow"),
        }

    # OAPIF is addressed by COLLECTION here, not by the service: QGIS's own dialog needs the service
    # and then asks the user to pick, but a URI built in code names the collection directly.
    why = "full attributes and exact geometry, paged by the server"
    if not prefer_attributes and (layer.get("storage_backend") == "geoparquet"
                                  or layer.get("kind") == "vector"):
        # The one remaining slow default, and it is slow because the layer has no fast surface yet
        # — not because a faster one was passed over. Say so, since tiling is one click away.
        why += " — this layer is not tiled, so there is no faster source; tile it in My Data"
    return {
        "kind": "oapif",
        "uri": f"url='{base}/api/ogc' typename='vector-{ref}'",
        "provider": "OAPIF",
        "why": why,
    }


def fallback(layer: dict, failed: dict) -> dict | None:
    """The next source to try when `failed` could not be opened, or None when there is none left.

    THE CASE THAT NEEDS THIS: an instance that has not been updated yet publishes no TileJSON for a
    tiled GeoParquet layer. The plugin now asks for one first — rightly, it is the fast path — and
    without a fallback that layer would simply refuse to open on every older instance, turning a
    speed fix into a compatibility break. So a vector-tile source that fails drops to the archive
    (slow, but it draws) and then to OGC API - Features (slower, but always there).
    """
    kind = (failed or {}).get("kind")
    if kind == "vector-tiles":
        pmtiles_link = _link(layer, "pmtiles")
        tiled = (bool(layer.get("pmtiles_key")) or layer.get("tile_status") == "ready"
                 or bool(pmtiles_link))
        if tiled and pmtiles_link and gdal_supports_pmtiles():
            return {
                "kind": "pmtiles",
                "uri": "/vsicurl/" + pmtiles_link,
                "provider": "ogr",
                "why": ("the whole tile archive — this instance publishes no per-tile URL for it, "
                        "so QGIS has to read the archive, and opening it is slow"),
            }
    if kind in ("vector-tiles", "pmtiles"):
        return describe(layer, prefer_attributes=True)
    if kind in ("wmts", "tilejson", "xyz"):
        return describe(layer, prefer_attributes=True)      # the COG
    return None


def raster_style_from_tile_url(url: str) -> dict:
    """The GeoDeploy raster style baked into a tile template — the inverse of `titiler.get_tile_url`.

    WHERE A PORTAL'S RASTER STYLING ACTUALLY LIVES. A raster is coloured by the server, so a portal
    does not store a colormap beside the layer the way it stores a colour for a vector: it bakes the
    choice into the tile URL, and the same GeoTIFF appears as `&colormap_name=terrain` in one portal,
    a bare `&rescale=264.9,298.33` in another and `&algorithm=hillshade&expression=b1*5.0` in a
    third. Read the layer's own default style instead and you draw the one styling no portal chose.

    So when a portal's raster is reopened as its GeoTIFF to be restyled, this is what makes it open
    looking like THAT portal. Anything unrecognised is skipped rather than guessed at.
    """
    from urllib.parse import parse_qs, unquote, urlparse

    if not url:
        return {}
    try:
        query = parse_qs(urlparse(url).query, keep_blank_values=False)
    except Exception:                   # noqa: BLE001 - a URL we cannot parse simply has no style
        return {}
    style: dict = {}

    bands = [int(b) for b in query.get("bidx", []) if str(b).strip().lstrip("-").isdigit()]
    if bands:
        style["bidx"] = bands
    rescale = (query.get("rescale") or [None])[0]
    if rescale and len(str(rescale).split(",")) == 2:
        style["rescale"] = str(rescale)
    algorithm = (query.get("algorithm") or [None])[0]
    if algorithm:
        style["algorithm"] = str(algorithm)
        # `zfactor` travels as a pre-scale EXPRESSION, `b1*5.0`, because that is the only way to
        # exaggerate a DEM before TiTiler computes relief from it.
        expression = (query.get("expression") or [""])[0]
        match = re.match(r"^\s*b\d+\s*\*\s*([0-9.]+)\s*$", str(expression))
        if match:
            try:
                style["zfactor"] = float(match.group(1))
            except ValueError:
                pass
        # CONTOURS and anything else parameterised the same way. TiTiler takes these as one JSON
        # blob, and GeoDeploy stores them as flat style keys — so a portal drawing contours every
        # 10 m has that number only here, and reopening the GeoTIFF to restyle it would otherwise
        # come back at the algorithm's own 35 m default.
        try:
            extra = json.loads(unquote((query.get("algorithm_params") or ["{}"])[0]))
        except ValueError:
            extra = None
        for key in ("increment", "thickness", "minz", "maxz"):
            value = (extra or {}).get(key) if isinstance(extra, dict) else None
            if isinstance(value, (int, float)):
                style[key] = int(value) if key in ("thickness", "minz", "maxz") else float(value)
    name = (query.get("colormap_name") or [None])[0]
    if name:
        name = str(name)
        if name.endswith("_r"):
            style["colormap"], style["colormap_reverse"] = name[:-2], True
        else:
            style["colormap"] = name
    explicit = (query.get("colormap") or [None])[0]
    if explicit:
        # `{"3": [r,g,b,a]}` — an explicit colour per pixel value, which is how a classified raster
        # travels. TiTiler takes the mapping itself because no named gradient can express one.
        try:
            mapping = json.loads(unquote(str(explicit)))
        except ValueError:
            mapping = None
        classes = []
        for value, rgba in sorted((mapping or {}).items(),
                                  key=lambda kv: int(kv[0]) if str(kv[0]).lstrip("-").isdigit()
                                  else 0):
            if not isinstance(rgba, (list, tuple)) or len(rgba) < 3:
                continue
            try:
                parts = [int(c) for c in rgba[:4]]
            except (TypeError, ValueError):
                continue
            if len(parts) == 3:
                parts.append(255)
            classes.append({"value": int(value),
                            "color": "#{0:02x}{1:02x}{2:02x}{3:02x}".format(*parts)})
        if classes:
            style["color_classes"] = classes
            style.pop("colormap", None)         # the explicit mapping is what gets drawn
    return style


def _mvt_source_layer(layer: dict) -> str | None:
    """The layer name inside the tiles: Martin names it `<schema>.<table>`."""
    schema, table = layer.get("schema_name"), layer.get("table_name")
    return f"{schema}.{table}" if schema and table else None


#: What each source IS, in the words a dropdown has room for. The `why` beside it in every
#: described source is the sentence; this is the name. Both are shown — the name to choose by, the
#: sentence to understand the choice — because "PMTiles" and "OAPIF" are not a choice a user can
#: make without being told what they cost.
_LABELS = {
    "wmts": "Styled tiles — as GeoDeploy draws it",
    "tilejson": "Styled tiles — as GeoDeploy draws it",
    "xyz": "Styled tiles — as GeoDeploy draws it",
    "cog": "The GeoTIFF — real values, restyle and classify",
    "vector-tiles": "Vector tiles — fast, generalized per zoom",
    "pmtiles": "Tile archive — one download, slow to open",
    "oapif": "Features — every attribute, classify by field",
}


def label(source: dict) -> str:
    """A short name for a described source, for a menu."""
    return _LABELS.get((source or {}).get("kind"), (source or {}).get("kind") or "source")


def alternatives(layer: dict) -> list[dict]:
    """Every source this layer offers, best first — for a UI that lets the user choose.

    Each entry carries `label` and `is_data`, so a caller can present the choice without knowing
    what a PMTiles archive is. `is_data` marks the surface holding real VALUES rather than a drawn
    picture: the GeoTIFF for a raster, full features for a vector. That distinction is the entire
    reason this list exists — it is the difference between a layer you can classify and one you can
    only look at — and it was previously buried in a checkbox that applied to every layer at once.
    """
    out = []
    primary = describe(layer)
    if primary:
        out.append(dict(primary, label=label(primary), is_data=False))
    other = describe(layer, prefer_attributes=True)
    if other and (not primary or other["kind"] != primary["kind"]):
        out.append(dict(other, label=label(other), is_data=True))
    elif primary and other and other["kind"] == primary["kind"]:
        # The same surface both ways: a layer with only one source is not offering a choice, and
        # saying it does would promise a restyle that cannot happen.
        out[0]["is_data"] = True
    return out
