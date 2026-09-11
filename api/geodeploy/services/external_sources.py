"""External map sources — somebody else's service, displayed without ingesting it.

A source is a REFERENCE: nothing is copied, the provider keeps serving it, and their licence
applies — which is why `attribution` is carried everywhere and shown on the map.

## The kinds, and what each one actually is

| `source_type` | kind   | what the browser ends up fetching                            |
|---------------|--------|--------------------------------------------------------------|
| `xyz`         | raster | the stored z/x/y template, directly from the provider        |
| `wms`         | raster | a GetMap request per tile, built here, directly              |
| `wmts`        | raster | a GetTile request per tile, built here, directly             |
| `wfs`         | vector | GeoJSON through OUR proxy                                    |
| `ogcapi`      | vector | an OGC API - Features collection's items, through OUR proxy  |
| `vectortile`  | vector | Mapbox Vector Tiles through OUR tile proxy                   |
| `pmtiles`     | either | tiles read out of a remote PMTiles archive by US, per tile   |

## Why some go through a proxy and some do not

CORS. A browser fetching a RASTER tile is doing something the whole web has allowed for twenty
years, and tile servers are configured for it. A browser fetching GeoJSON, an MVT tile, or a byte
RANGE of a PMTiles archive is doing a cross-origin XHR, and a provider who has not set
`Access-Control-Allow-Origin` breaks it — silently, as an empty layer with a console error the
map's reader will never see.

So anything read as DATA is proxied same-origin, which is the rule the WFS proxy already
established. It costs bandwidth through the instance; it buys a source that works for every
visitor rather than for the ones whose provider happens to be permissive.

PMTiles is the interesting case: the archive is a single file read with HTTP range requests, and
GeoDeploy already has a v3 reader (`services/pmtiles_reader`). So rather than shipping the browser
a `pmtiles://` URL and hoping the provider allows ranged cross-origin reads, the tile is read HERE
and handed over as an ordinary tile. The portal needs no PMTiles library for it, and the provider
needs no CORS policy.

## What is deliberately NOT a source type

**WCS** is not a display service. GetCoverage returns a coverage — a GeoTIFF, a NetCDF — not a map
image and not a tile, so there is nothing a web map can draw from it without first rendering it,
which means ingesting it. Registering one as a "source" would publish a layer that draws nothing.
Almost every WCS server (MapServer, GeoServer, rasdaman) exposes the same coverage over WMS, and
that is the right way to SHOW it. `IMPORT_ONLY_HINT` is what the API says when somebody offers one.
"""
import json

from urllib.parse import urlsplit, urlunsplit
import httpx

DEFAULT_WMS_VERSION = "1.3.0"
DEFAULT_WFS_VERSION = "2.0.0"
DEFAULT_WMS_FORMAT = "image/png"
DEFAULT_WMTS_FORMAT = "image/png"
#: The tile matrix set nearly every WMTS publishes alongside its own: the Web Mercator one a slippy
#: map already speaks. A server with only its own national grid needs its set named explicitly.
DEFAULT_MATRIX_SET = "GoogleMapsCompatible"
WFS_FEATURE_CAP = 5000  # safety cap for the proxy payload
#: How many features an OGC API - Features collection is asked for. Its own `limit` maximum is
#: usually 1000 or 10000, and asking for more than a server allows is answered with its maximum
#: rather than an error — so this is a ceiling, not a promise.
OAPIF_FEATURE_CAP = 5000

#: Every kind the instance can hold.
SOURCE_TYPES = ("xyz", "wms", "wmts", "wfs", "ogcapi", "vectortile", "pmtiles")

#: Those whose tiles the browser fetches THROUGH US rather than from the provider — see the module
#: docstring: an MVT tile and a PMTiles range read are cross-origin XHRs, a raster tile is not.
PROXIED_TILE_TYPES = ("vectortile", "pmtiles")

#: Those whose features the browser fetches through our GeoJSON proxy.
PROXIED_FEATURE_TYPES = ("wfs", "ogcapi")

#: Those that need a `source_layer` — the name of the layer INSIDE the tile. A vector tile is a
#: container of named layers, and a style that does not name one draws nothing at all.
NEEDS_SOURCE_LAYER = ("vectortile", "pmtiles")

#: Said when somebody offers a service that cannot be DISPLAYED, only imported.
IMPORT_ONLY_HINT = (
    "WCS is not a display service: GetCoverage returns a coverage (a GeoTIFF, a NetCDF), not map "
    "images or tiles, so there is nothing a web map can draw from it. Most WCS servers publish the "
    "same coverage over WMS - add it that way to show it. To analyse it, download the coverage and "
    "upload it as a raster layer."
)

_GEOM_MAP = {
    "point": "point", "multipoint": "point",
    "linestring": "line", "multilinestring": "line",
    "polygon": "polygon", "multipolygon": "polygon",
}


def kind_for(source_type: str, tile_type: str | None = None) -> str:
    """`raster` or `vector` — what this source DRAWS as, which is not what it is fetched as.

    PMTiles is the one that cannot be answered from the type alone: an archive holds either, and
    which one is in its header. `tile_type` is what the probe read; without it the vector case is
    assumed, because that is what almost every published archive is.
    """
    if source_type == "pmtiles":
        return "raster" if (tile_type or "vector") == "raster" else "vector"
    return "vector" if source_type in ("wfs", "ogcapi", "vectortile") else "raster"


def _join(url: str, query: str) -> str:
    return url + ("&" if "?" in url else "?") + query


def tile_url(source) -> str | None:
    """The `tiles[]` template the STYLE should carry, for anything served as tiles.

    Three shapes, and which one a source gets is the whole CORS story from the module docstring:

    * a template the browser fetches from the PROVIDER (`xyz`, and the WMS/WMTS requests built
      below — raster, and therefore not blocked);
    * OUR OWN tile path for the kinds read as data (`vectortile`, `pmtiles`);
    * None for a source that is not tiled at all (`wfs`, `ogcapi`), drawn from GeoJSON instead.
    """
    if source.source_type in PROXIED_TILE_TYPES:
        return proxy_tile_url(source)
    if source.source_type == "xyz":
        return normalise_template(source.url)
    if source.source_type == "wmts":
        # KVP GetTile. The RESTful form of WMTS is a template with {TileMatrix}/{TileRow}/{TileCol}
        # in it, which IS an XYZ template once the tokens are renamed — `xyz` holds those, and
        # `normalise_template` renames them. This branch is the other form: a base URL and a layer,
        # where the request has to be built.
        matrix = getattr(source, "matrix_set", None) or DEFAULT_MATRIX_SET
        fmt = source.image_format or DEFAULT_WMTS_FORMAT
        query = (
            "service=WMTS&version=" + (source.version or "1.0.0") + "&request=GetTile"
            "&layer=" + (source.layer_name or "") + "&style=default&format=" + fmt +
            "&tilematrixset=" + matrix + "&tilematrix={z}&tilerow={y}&tilecol={x}"
        )
        return _join(source.url, query)
    if source.source_type == "wms":
        version = source.version or DEFAULT_WMS_VERSION
        fmt = source.image_format or DEFAULT_WMS_FORMAT
        # EPSG:3857 has easting/northing axis order, so 1.3.0 `crs=` needs no axis swap.
        crs_param = "crs" if version >= "1.3" else "srs"
        query = (
            f"service=WMS&version={version}&request=GetMap"
            f"&layers={source.layer_name or ''}&styles="
            f"&format={fmt}&transparent=true"
            f"&{crs_param}=EPSG:3857&width=256&height=256&bbox={{bbox-epsg-3857}}"
        )
        return _join(source.url, query)
    return None


def features_url(source) -> str | None:
    """Same-origin GeoJSON proxy path for a source drawn from FEATURES, else None.

    Only the two fetched as GeoJSON. A vector source that is TILED (`vectortile`, `pmtiles`) is
    drawn from tiles and has no features endpoint — returning one for it would put a `geojson`
    source in the style beside the vector one and draw the layer twice, once empty.
    """
    if source.source_type in PROXIED_FEATURE_TYPES:
        return f"/api/data/sources/{source.id}/features.geojson"
    return None


def proxy_tile_url(source) -> str:
    """Our own tile path, for a source whose tiles a browser cannot fetch cross-origin."""
    return f"/api/data/sources/{source.id}/tiles/{{z}}/{{x}}/{{y}}"


#: WMTS RESTful templates name their axes differently from every slippy map. Same numbers, same
#: order, different words — so a template that would otherwise be rejected is simply renamed.
_TEMPLATE_TOKENS = (
    ("{TileMatrix}", "{z}"), ("{tilematrix}", "{z}"),
    ("{TileRow}", "{y}"), ("{tilerow}", "{y}"),
    ("{TileCol}", "{x}"), ("{tilecol}", "{x}"),
)


def normalise_template(url: str) -> str:
    """A tile template in MapLibre's tokens, whatever the provider called them."""
    out = url or ""
    for found, replacement in _TEMPLATE_TOKENS:
        out = out.replace(found, replacement)
    return out


def is_tile_template(url: str) -> bool:
    """Whether this URL is a per-tile template rather than a service endpoint."""
    text = normalise_template(url or "")
    return "{z}" in text and "{x}" in text and "{y}" in text


def _wfs_getfeature_url(url: str, layer_name: str, version: str, limit: int, output_format: str) -> str:
    if version >= "2.0":
        query = (
            f"service=WFS&version={version}&request=GetFeature"
            f"&typeNames={layer_name}&count={limit}&outputFormat={output_format}"
        )
    else:
        query = (
            f"service=WFS&version={version}&request=GetFeature"
            f"&typeName={layer_name}&maxFeatures={limit}&outputFormat={output_format}"
        )
    return _join(url, query)


def _bbox_from_geojson(gj: dict) -> list | None:
    if isinstance(gj.get("bbox"), list) and len(gj["bbox"]) >= 4:
        b = gj["bbox"]
        return [b[0], b[1], b[2], b[3]]
    # Fall back to scanning coordinates of the returned features.
    xs, ys = [], []

    def walk(coords):
        if not coords:
            return
        if isinstance(coords[0], (int, float)):
            xs.append(coords[0]); ys.append(coords[1])
        else:
            for c in coords:
                walk(c)

    for f in gj.get("features", []):
        geom = (f or {}).get("geometry") or {}
        walk(geom.get("coordinates"))
    if xs and ys:
        return [min(xs), min(ys), max(xs), max(ys)]
    return None


async def probe_wfs(url: str, layer_name: str, version: str | None) -> dict:
    """Fetch one feature to validate the WFS and learn its geometry type + bbox.

    Tries WFS 2.0.0 then 1.1.0, and json output-format spellings. Raises ValueError
    with a readable message if nothing usable comes back.
    """
    versions = [version] if version else [DEFAULT_WFS_VERSION, "1.1.0"]
    last_err = "no response"
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        for ver in versions:
            for fmt in ("application/json", "json"):
                req_url = _wfs_getfeature_url(url, layer_name, ver, 1, fmt)
                try:
                    r = await client.get(req_url)
                    if r.status_code != 200:
                        last_err = f"HTTP {r.status_code}"
                        continue
                    gj = r.json()
                except Exception as exc:  # noqa: BLE001 — try the next combo
                    last_err = str(exc)
                    continue
                feats = gj.get("features")
                if not isinstance(feats, list):
                    last_err = "response was not GeoJSON (the layer may not support outputFormat=json)"
                    continue
                geom_type = None
                if feats:
                    gt = ((feats[0] or {}).get("geometry") or {}).get("type", "")
                    geom_type = _GEOM_MAP.get(gt.lower())
                return {
                    "version": ver,
                    "geometry_type": geom_type or "polygon",
                    "bbox": _bbox_from_geojson(gj),
                }
    raise ValueError(f"Could not read WFS features: {last_err}")


async def fetch_wfs_geojson(source, limit: int = WFS_FEATURE_CAP) -> dict:
    """Proxy: fetch GetFeature as GeoJSON for the portal/editor to render."""
    version = source.version or DEFAULT_WFS_VERSION
    last_err = "no response"
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        for fmt in ("application/json", "json"):
            req_url = _wfs_getfeature_url(source.url, source.layer_name or "", version, limit, fmt)
            try:
                r = await client.get(req_url)
                if r.status_code != 200:
                    last_err = f"HTTP {r.status_code}"
                    continue
                gj = r.json()
            except Exception as exc:  # noqa: BLE001
                last_err = str(exc)
                continue
            if isinstance(gj.get("features"), list):
                return gj
    raise ValueError(f"Could not fetch WFS features: {last_err}")


# ── OGC API - Features ───────────────────────────────────────────────────────────────────────────
#
# The successor to WFS, and a much easier thing to consume: collections of GeoJSON over plain HTTP,
# no XML, no GetCapabilities, no outputFormat negotiation. What it does have is more than one way to
# be pointed at, which is where the edge cases live — see `oapif_items_url`.


def oapif_items_url(url: str, collection: str | None, limit: int | None = None) -> str:
    """The `items` URL for a collection, from whatever the user pasted.

    People paste three different things, all of them reasonable, and a source that only accepted one
    would be a source that "does not work" for two thirds of the people who try it:

    * the LANDING PAGE (`https://host/ogc`) plus a collection id;
    * the COLLECTION url (`https://host/ogc/collections/roads`), where the id is already in it;
    * the ITEMS url itself (`https://host/ogc/collections/roads/items`), which is what a browser
      shows you when you click through the API.

    All three are recognised, so the collection id becomes optional exactly when the URL already
    carries it.
    """
    base = (url or "").rstrip("/")
    if base.endswith("/items"):
        target = base
    elif "/collections/" in base:
        target = base + "/items"
    else:
        target = base + "/collections/" + (collection or "").strip("/") + "/items"
    query = "f=json"
    if limit:
        query += "&limit=" + str(int(limit))
    return _join(target, query)


def oapif_collection_of(url: str) -> str | None:
    """The collection id already in a URL, if it names one."""
    text = (url or "").rstrip("/")
    if text.endswith("/items"):
        text = text[: -len("/items")]
    if "/collections/" in text:
        found = text.rsplit("/collections/", 1)[1].strip("/")
        return found.split("/")[0] or None
    return None


async def probe_oapif(url: str, collection: str | None) -> dict:
    """Fetch one item to validate the collection and learn its geometry type and extent.

    Validating on ADD is the difference between a typo reported now, with the address in front of
    you, and an empty layer on a published portal a week later.
    """
    last_err = "no response"
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        try:
            r = await client.get(oapif_items_url(url, collection, limit=1),
                                 headers={"Accept": "application/geo+json, application/json"})
            if r.status_code != 200:
                raise ValueError("HTTP " + str(r.status_code))
            gj = r.json()
        except Exception as exc:                        # noqa: BLE001
            raise ValueError("Could not read the collection: " + str(exc)) from exc
        feats = gj.get("features")
        if not isinstance(feats, list):
            raise ValueError(
                "that URL answered with something that is not a GeoJSON feature collection "
                "(last error: " + last_err + ")")
        geom_type = None
        if feats:
            gt = ((feats[0] or {}).get("geometry") or {}).get("type", "")
            geom_type = _GEOM_MAP.get(gt.lower())
        return {
            "collection": collection or oapif_collection_of(url),
            "geometry_type": geom_type or "polygon",
            "bbox": _bbox_from_geojson(gj),
        }


async def fetch_oapif_geojson(source, limit: int = OAPIF_FEATURE_CAP) -> dict:
    """Proxy: the collection's items as GeoJSON, for the portal and the editor to render."""
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        r = await client.get(oapif_items_url(source.url, source.layer_name, limit=limit),
                             headers={"Accept": "application/geo+json, application/json"})
        if r.status_code != 200:
            raise ValueError("Could not fetch the collection: HTTP " + str(r.status_code))
        gj = r.json()
    if not isinstance(gj.get("features"), list):
        raise ValueError("the collection did not answer with GeoJSON features")
    return gj


# ── Vector tiles, and TileJSON ───────────────────────────────────────────────────────────────────


async def probe_vector_tiles(url: str, source_layer: str | None) -> dict:
    """What the style needs about a vector-tile set: which layer, which zooms, which extent.

    A vector tile is a CONTAINER of named layers, and a style that does not name one draws nothing —
    silently, because an unmatched `source-layer` is not an error in MapLibre. So the name is the
    one thing this must come back with.

    Where it comes from depends on what was pasted. TileJSON knows it (`vector_layers`), and is
    worth asking for even when a template was given, because that is how the zoom range and the
    bounds arrive too. A bare template with no TileJSON beside it cannot be introspected without
    decoding an MVT, so the user's own `source_layer` is required — the form asks for it, and this
    refuses without it rather than storing a source that will draw nothing.
    """
    text = (url or "").strip()
    out = {"source_layer": (source_layer or "").strip() or None,
           "tiles": normalise_template(text) if is_tile_template(text) else None,
           "min_zoom": None, "max_zoom": None, "bbox": None}
    if not out["tiles"]:
        doc = await _fetch_tilejson(text)
        tiles = doc.get("tiles")
        if not (isinstance(tiles, list) and tiles):
            raise ValueError(
                "that URL is neither a tile template with {z}/{x}/{y} in it nor a TileJSON "
                "document naming one")
        out["tiles"] = normalise_template(str(tiles[0]))
        _tilejson_fields(doc, out)
        return out
    # A template WAS given. TileJSON often sits beside it; if it does not, the template stands on
    # its own and only the source layer is missing — which the caller must have supplied.
    try:
        doc = await _fetch_tilejson(text.split("/{z}")[0] + ".json")
    except Exception:                                   # noqa: BLE001 - a template needs no TileJSON
        doc = {}
    _tilejson_fields(doc, out)
    if not out["source_layer"]:
        raise ValueError(
            "a vector tile set needs the name of the layer INSIDE the tiles, and this one does not "
            "publish a TileJSON to read it from. Add it as the layer name.")
    return out


async def _fetch_tilejson(url: str) -> dict:
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        r = await client.get(url, headers={"Accept": "application/json"})
        if r.status_code != 200:
            raise ValueError("HTTP " + str(r.status_code))
        doc = r.json()
    if not isinstance(doc, dict):
        raise ValueError("that URL did not answer with a TileJSON document")
    return doc


def _tilejson_fields(doc: dict, out: dict) -> None:
    """Fill in whatever a TileJSON knows, leaving what it does not alone."""
    layers = doc.get("vector_layers")
    if not out.get("source_layer") and isinstance(layers, list) and layers:
        first = layers[0]
        if isinstance(first, dict) and first.get("id"):
            out["source_layer"] = str(first["id"])
    for key, field in (("minzoom", "min_zoom"), ("maxzoom", "max_zoom")):
        value = doc.get(key)
        if isinstance(value, (int, float)):
            out[field] = int(value)
    bounds = doc.get("bounds")
    if isinstance(bounds, list) and len(bounds) >= 4:
        out["bbox"] = [float(b) for b in bounds[:4]]


# ── PMTiles ──────────────────────────────────────────────────────────────────────────────────────
#
# A single file, read with HTTP range requests. GeoDeploy already has a v3 reader for its own
# archives, so a remote one is read the same way — by us, not by the browser (see the module
# docstring on CORS), which also means the portal needs no PMTiles library to draw one.

#: Enough for the header (127 bytes) and, in practice, the root directory and metadata beside it.
PMTILES_HEAD_BYTES = 16384


async def _range(url: str, start: int, length: int) -> bytes:
    """One HTTP range read. The unit of everything PMTiles does."""
    end = start + length - 1
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        r = await client.get(url, headers={"Range": "bytes=" + str(start) + "-" + str(end)})
        if r.status_code not in (200, 206):
            raise ValueError("HTTP " + str(r.status_code))
        data = r.content
    # A server that ignores Range answers 200 with the WHOLE file. Slicing keeps that correct
    # instead of parsing a header out of the middle of an archive.
    if len(data) > length:
        data = data[start:start + length] if len(data) > start + length else data[:length]
    return data


async def probe_pmtiles(url: str, source_layer: str | None) -> dict:
    """Read a remote archive's header and metadata: kind, layer name, zooms, bounds.

    Everything the style needs is IN the file, which is the nice thing about PMTiles — including
    whether it holds vector tiles or raster ones, so `kind` is read rather than guessed.
    """
    from . import pmtiles_reader

    try:
        head = await _range(url, 0, PMTILES_HEAD_BYTES)
    except Exception as exc:                            # noqa: BLE001
        raise ValueError("Could not read the archive: " + str(exc)) from exc
    try:
        header = pmtiles_reader.parse_header(head)
    except Exception as exc:                            # noqa: BLE001
        raise ValueError("That URL is not a PMTiles v3 archive (" + str(exc) + ")") from exc

    # THE TILE TYPE, NOT ITS MEDIA STRING. Sniffing the media type for "mvt" looked reasonable and
    # was wrong: the reader spells an MVT archive `application/vnd.mapbox-vector-tile`, which ends
    # in "tile". Every vector archive was therefore registered as a RASTER one and published as a
    # raster layer — fetching vector tiles into an image source, drawing nothing. Caught by reading
    # a real Protomaps archive rather than by reading this code.
    kind = "vector" if header.tile_type == pmtiles_reader.TILETYPE_MVT else "raster"
    out = {
        "kind": kind,
        "source_layer": (source_layer or "").strip() or None,
        "min_zoom": header.min_zoom,
        "max_zoom": header.max_zoom,
        "bbox": header.bounds,
    }
    # The metadata JSON names the layers inside a vector archive. It usually sits within the head
    # already read; when it does not, one more range request fetches it.
    if kind == "vector" and not out["source_layer"]:
        try:
            raw = head[header.metadata_offset:header.metadata_offset + header.metadata_length]
            if len(raw) < header.metadata_length:
                raw = await _range(url, header.metadata_offset, header.metadata_length)
            meta = json.loads(pmtiles_reader.decompress(raw, header.internal_compression))
            layers = meta.get("vector_layers")
            if isinstance(layers, list) and layers and isinstance(layers[0], dict):
                out["source_layer"] = str(layers[0].get("id") or "") or None
        except Exception:                               # noqa: BLE001 - the user can still name it
            pass
    if kind == "vector" and not out["source_layer"]:
        raise ValueError(
            "this archive does not say what its layers are called, so the name of the layer inside "
            "the tiles has to be given as the layer name")
    return out


def _sync_range(url: str):
    """A SYNCHRONOUS `fetch(offset, length) -> bytes` over a remote archive.

    `pmtiles_reader` is plain synchronous code — deliberately, so one lookup is one hop into a
    thread rather than a bounce between loops for each of its two range reads. This is the same
    shape `routers/data/vector._pmtiles_fetch` builds over object storage; the only difference is
    that the bytes come from somebody else's web server.
    """
    def fetch(offset: int, length: int) -> bytes:
        end = offset + length - 1
        with httpx.Client(timeout=30, follow_redirects=True) as client:
            r = client.get(url, headers={"Range": "bytes=" + str(offset) + "-" + str(end)})
            if r.status_code not in (200, 206):
                raise ValueError("HTTP " + str(r.status_code))
            data = r.content
        # A SERVER THAT IGNORES `Range` answers 200 with the WHOLE file, and parsing a header out
        # of the middle of an archive produces nonsense rather than an error. Slice what was asked
        # for, so an uncooperative host is merely slow instead of silently wrong.
        if len(data) > length:
            data = data[offset:offset + length]
        return data
    return fetch


async def fetch_pmtiles_tile(source, z: int, x: int, y: int) -> tuple[bytes, str] | None:
    """One tile out of a remote archive, decompressed, with the type to serve it as.

    None means the archive genuinely has no tile there — normal for a sparse archive or a zoom
    outside its range, and it must be an empty 204 rather than an error a client would retry.
    """
    import asyncio

    from . import pmtiles_reader

    got = await asyncio.to_thread(
        pmtiles_reader.get_tile, source.url, _sync_range(source.url), z, x, y)
    if got is None:
        return None
    body, header = got
    return body, header.media_type



async def fetch_vector_tile(source, z: int, x: int, y: int) -> tuple[bytes, str] | None:
    """One MVT tile from a third-party tile set, fetched by US so CORS cannot break it.

    None for a tile the provider does not have. A tile server answers a missing tile with 404 or
    204 depending on who wrote it, and both mean the same thing to a map: nothing here.
    """
    template = normalise_template(source.url or "")
    url = (template.replace("{z}", str(z)).replace("{x}", str(x)).replace("{y}", str(y)))
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        r = await client.get(url)
    if r.status_code in (204, 404):
        return None
    if r.status_code != 200:
        raise ValueError("HTTP " + str(r.status_code))
    if not r.content:
        return None
    # The provider's own content type when it gave one, because a vector tile may arrive as
    # `application/x-protobuf` or `application/vnd.mapbox-vector-tile` and MapLibre accepts both.
    media = r.headers.get("content-type") or "application/x-protobuf"
    return r.content, media.split(";")[0].strip()


# ── Mixed content ────────────────────────────────────────────────────────────────────────────────


def _probe_url(template: str) -> str:
    """A template with a real tile in it — `{z}/{x}/{y}` is not an address.

    Fetching the template verbatim asks the provider for a file called `{z}`, which many answer
    with a 404, so the source would be refused for being unreachable when it is merely a template.
    """
    if not is_tile_template(template):
        return template
    return (normalise_template(template)
            .replace("{z}", "0").replace("{x}", "0").replace("{y}", "0"))


def _with_origin(template: str, origin: str) -> str:
    """`template` moved to another scheme and host, keeping its path, query and placeholders."""
    parts = urlsplit(template)
    other = urlsplit(origin)
    return urlunsplit((other.scheme, other.netloc, parts.path, parts.query, parts.fragment))


async def _answers_securely(template: str) -> bool:
    """Whether this address really delivers a tile over https — REDIRECTS INCLUDED.

    THE HOP IS THE WHOLE POINT. `https://www.google.cn/maps/vt?...` answers 302 and sends the
    caller to `http://www.google.com/maps/vt?...`, so the request begins secure and ends insecure
    — which a browser blocks exactly as it blocks an `http` URL typed in directly. An earlier
    version of this check followed redirects and asked only for a 200 with bytes in it, so it
    called that address secure and would have "upgraded" a source to a URL that still cannot draw.
    """
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            response = await client.get(_probe_url(template))
    except Exception:                                       # noqa: BLE001 - it does not answer
        return False
    if response.status_code != 200 or not response.content:
        return False
    hops = [*(h.url for h in response.history), response.url]
    return all(str(getattr(hop, "scheme", "")) == "https" for hop in hops)


async def _redirected_origin(template: str) -> str | None:
    """Where an address sends callers, when it sends them somewhere that is only a different host.

    A provider that has moved says so in a 302 rather than in its documentation — the reported
    `www.google.cn` template redirects every request, http or https, to `www.google.com` — and the
    new host is very often the one that serves https. ONLY A MOVE IS FOLLOWED: if the redirect
    changes the PATH or the QUERY it is answering a different question (a login page, a consent
    wall, a per-tile CDN URL that no template could be built from), and guessing at it would store
    an address that works for tile 0/0/0 and for nothing else.
    """
    probe = _probe_url(template)
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            response = await client.get(probe)
    except Exception:                                       # noqa: BLE001
        return None
    here, there = urlsplit(probe), urlsplit(str(response.url))
    if not there.netloc or (there.path, there.query) != (here.path, here.query):
        return None
    if there.netloc == here.netloc:
        return None
    return urlunsplit((there.scheme, there.netloc, "", "", ""))


async def secure_alternative(url: str) -> str | None:
    """The https address that serves this http one, or None when there is not one.

    WHY THIS QUESTION IS ASKED AT ALL. A browser will not load an `http://` image into an
    `https://` page — that is mixed content, and it is blocked with no visible error on the map.
    The source is registered, the layer is in the list, the tiles never arrive. Reported exactly
    that way: two Google tile URLs added side by side, the `https` one drawing and the `http` one
    not, while both worked in QGIS — which is a desktop application and has no such rule.

    TWO PLACES ARE TRIED, because a provider that still publishes `http` has usually either moved
    to https at the same address or moved house entirely:

    * the same address on https;
    * the address it REDIRECTS to, on https. The reported one is this case and only this case.
      `www.google.cn` answers every request with a 302 to `http://www.google.com`, so the https
      form of the address the user has is NOT secure — it ends on http — while
      `https://www.google.com/maps/vt?...` serves the identical bytes. Nothing but the host
      changes, which is what makes swapping it safe.

    Nothing is assumed: each candidate is fetched, and a candidate whose redirect chain touches
    `http` at any point is not secure however well it answers.
    """
    if not url.lower().startswith("http://"):
        return None
    candidates = ["https://" + url[len("http://"):]]
    origin = await _redirected_origin(url)
    if origin:
        moved = _with_origin(url, "https://" + urlsplit(origin).netloc)
        if moved not in candidates:
            candidates.append(moved)
    for candidate in candidates:
        if await _answers_securely(candidate):
            return candidate
    return None


MIXED_CONTENT_HINT = (
    "This instance is served over https, and a browser refuses to load http:// tiles into an "
    "https:// page — the layer would be registered and then never draw, with nothing on the map "
    "to say why. Neither this address nor the one it redirects to answers over https, so there is "
    "no version of it that would work. Ask the provider for an https endpoint, or use one that "
    "has one."
)
