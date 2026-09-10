"""A layer QGIS draws from somebody else's map service, as a GeoDeploy EXTERNAL SOURCE.

A WMS, an XYZ tile set or a WFS opened in QGIS has no file to upload — and uploading one would be
the wrong thing anyway: the provider's copy is the copy, it changes when they change it, and their
licence usually says so. GeoDeploy has a name for exactly this (`external_sources`: a reference,
fetched at view time, never ingested), and until now the plugin did not know it existed. Pushing
such a layer was refused with "served from elsewhere, not a local file", and inside a GROUP it was
worse: the layer fell into the upload list and the first `export.prepare` aborted the whole push,
so one basemap in a group meant nothing was published at all.

WHAT THIS MODULE IS. QGIS describes a provider's connection in a URI, and the URI is the only place
the pieces GeoDeploy needs are written down — the service address, which layer of it, which format,
which version. There are two grammars (`&`-joined for WMS/XYZ, space-joined `key='value'` for WFS),
neither is documented as a contract, and both are read here rather than in three call sites.

WHAT IS DELIBERATELY REFUSED. GeoDeploy holds `xyz | wms | wmts | wfs | ogcapi | vectortile |
pmtiles` — everything a web map can actually draw from somebody else's server. A service outside
that set has no honest home: an ArcGIS REST service, or a WCS (which is not a display service at
all — GetCoverage returns a coverage, not map images, so there is nothing to draw without first
ingesting it). Registering one as the nearest neighbour would publish a portal layer that fetches
from the wrong kind of endpoint and draws nothing, silently. Each is refused BY NAME instead,
because "GeoDeploy has no kind for this yet, here is what to do" is actionable and "cannot be
uploaded" is not.
"""
from __future__ import annotations

import re
from urllib.parse import unquote

#: What `POST /api/data/sources` accepts. Restated from `cli/geodeploy/sources.py` deliberately —
#: this file must not import a client to answer a question about a QGIS layer. An instance too OLD
#: for one of these answers 422, which is a clear message naming the field; the alternative is
#: asking every instance what it supports before every push.
SOURCE_TYPES = ("xyz", "wms", "wmts", "wfs", "ogcapi", "vectortile", "pmtiles")

#: A source that IS an address, rather than one that CONTAINS an address in a `url=` parameter.
REMOTE_PREFIXES = ("http://", "https://", "/vsicurl")

#: Providers with a service GeoDeploy has no `source_type` for, and what to say about each.
UNSUPPORTED_PROVIDERS = {
    "arcgismapserver": "an ArcGIS MapServer service",
    "arcgisfeatureserver": "an ArcGIS FeatureServer service",
    "afs": "an ArcGIS FeatureServer service",
    "wcs": ("a WCS coverage service, which is not a display service at all: GetCoverage returns a "
            "coverage rather than map images, so there is nothing a web map can draw from it. Most "
            "WCS servers publish the same data over WMS"),
}

#: How QGIS spells the providers this DOES support, mapped to GeoDeploy's own names. `wms` is
#: absent because that one provider serves three different things (XYZ, WMS and WMTS) and only the
#: URI can tell them apart — see `spec_from_uri`.
PROVIDER_TYPES = {
    "wfs": "wfs",
    "wfs2": "wfs",
    "oapif": "ogcapi",
    "ogcapif": "ogcapi",
    "vectortile": "vectortile",
}

#: A quoted pair in `QgsDataSourceUri` form: `typename='ms:roads'`. Doubled quotes are escapes.
_QUOTED_PAIR = re.compile(r"([A-Za-z_][\w.]*)='((?:[^']|'')*)'")
#: A bare pair in the same grammar: `restrictToRequestBBOX=1`.
_BARE_PAIR = re.compile(r"(?:^|\s)([A-Za-z_][\w.]*)=([^\s'][^\s]*)")
#: How the two grammars are told apart. The `&`-joined one never quotes a value.
_IS_QUOTED_FORM = re.compile(r"(?:^|\s)[A-Za-z_][\w.]*='")


#: WMTS RESTful templates name their axes differently from every slippy map. Same numbers, same
#: order, different words. The twin of `services/external_sources._TEMPLATE_TOKENS`: the plugin
#: cannot import the server's copy, and the two are asserted to agree in
#: `scripts/test_external_sources.py`.
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


def parse_uri(text: str) -> dict:
    """A QGIS provider URI as a plain dict, in either of the two grammars QGIS writes.

    `&`-joined with percent-encoded values is what the WMS provider writes (XYZ tiles included);
    space-joined `key='value'` is `QgsDataSourceUri`, which the WFS provider writes. Telling them
    apart by looking for a quoted value is reliable because the first grammar never quotes.
    """
    text = (text or "").strip()
    if not text:
        return {}
    if _IS_QUOTED_FORM.search(text):
        out = {}
        # Quoted values first: one may contain spaces and `&`, so the bare pairs are read as
        # whatever the quoted ones did not already claim.
        for key, value in _QUOTED_PAIR.findall(text):
            out[key] = value.replace("''", "'")
        for key, value in _BARE_PAIR.findall(text):
            out.setdefault(key, value)
        return out
    out = {}
    for part in text.split("&"):
        if "=" not in part:
            continue
        key, _, value = part.partition("=")
        out[key.strip()] = unquote(value)
    return out


def spec_from_uri(provider: str, uri: str) -> dict | None:
    """`{source_type, url, …}` for a service GeoDeploy can hold, else None.

    None means "not a service this can register" — which is either "not a service at all" (a
    GeoPackage, a memory layer) or one `refusal_from_uri` explains. The two questions are separate
    calls so a caller can ask the cheap one without composing a sentence it may not need.
    """
    key = (provider or "").strip().lower()
    params = parse_uri(uri)
    url = (params.get("url") or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        # The server refuses anything else, and refusing here means the user hears it before a
        # round trip rather than as a 400 from an endpoint they did not know was involved.
        return None

    if key in ("wfs", "wfs2"):
        # `typename` is the WFS `typeName`; QGIS lower-cases the key, the standard capitalises it.
        name = params.get("typename") or params.get("typeName")
        if not name:
            return None
        version = (params.get("version") or "").strip()
        return {"source_type": "wfs", "url": url, "layer_name": name,
                # "auto" is QGIS asking the server, not a version number: sending it would store a
                # version string the provider never claimed.
                "version": None if version.lower() in ("", "auto") else version}

    if key in ("oapif", "ogcapif"):
        # OGC API - Features. QGIS stores the LANDING PAGE plus a `typename` for the collection,
        # and GeoDeploy accepts either that pair or a URL that already names the collection.
        collection = (params.get("typename") or params.get("collection") or "").strip()
        spec = {"source_type": "ogcapi", "url": url}
        if collection:
            spec["layer_name"] = collection
        elif "/collections/" not in url:
            return None                 # nothing names the collection: see `refusal_from_uri`
        return spec

    if key == "vectortile":
        # A vector tile set, or a PMTiles archive — QGIS uses one provider for both and the URI
        # says which. GeoDeploy stores them as different kinds because it fetches them differently
        # (a template per tile; ranged reads of one file).
        style_url = (params.get("styleUrl") or "").strip()
        if url.lower().endswith(".pmtiles") or (params.get("type") or "").lower() == "pmtiles":
            spec = {"source_type": "pmtiles", "url": url}
        elif not is_tile_template(url) and not url.lower().endswith(".json"):
            return None                 # neither a template nor a TileJSON: nothing to fetch
        else:
            spec = {"source_type": "vectortile", "url": normalise_template(url)}
        if style_url:
            # Carried nowhere yet, but worth NOT silently dropping: a style URL is how a vector
            # tile set says how it wants to be drawn, and GeoDeploy has no field for it.
            _log_dropped("the tile set's own style URL", style_url)
        return spec

    if key == "wms":
        # ONE QGIS PROVIDER, THREE SERVICES. `type=xyz` is a tile template; a `tileMatrixSet` means
        # WMTS; anything else with `layers` is a WMS. Telling them apart is the whole job here,
        # because sending a WMTS as a WMS produces a layer that fetches GetMap from a server that
        # only speaks GetTile — which draws nothing, with no error.
        kind = (params.get("type") or "").strip().lower()
        if kind == "xyz":
            return {"source_type": "xyz", "url": normalise_template(url)}
        matrix = (params.get("tileMatrixSet") or params.get("tilematrixset") or "").strip()
        layers = params.get("layers")
        if kind == "wmts" or matrix:
            if not layers:
                return None
            spec = {"source_type": "wmts", "url": url, "layer_name": layers}
            if matrix:
                spec["matrix_set"] = matrix
            image_format = (params.get("format") or "").strip()
            if image_format:
                spec["image_format"] = image_format
            return spec
        if kind:
            return None                 # a `type=` this does not know — refused by name
        if not layers:
            return None
        spec = {"source_type": "wms", "url": url, "layer_name": layers}
        image_format = (params.get("format") or "").strip()
        if image_format:
            spec["image_format"] = image_format
        version = (params.get("version") or "").strip()
        if version and version.lower() != "auto":
            spec["version"] = version
        return spec
    return None


def _log_dropped(what: str, value: str) -> None:
    """Say what could not travel, rather than dropping it in silence."""
    try:
        try:
            from . import symbology
        except ImportError:             # pragma: no cover - exec'd standalone
            import symbology
        symbology._log("{0} could not travel with this source ({1}); it is registered without "
                       "it.".format(what, str(value)[:120]))
    except Exception:                   # noqa: BLE001  # nosec B110 - intentional: a note is never worth failing a push
        pass


def refusal_from_uri(provider: str, uri: str) -> str | None:
    """Why this remote layer cannot become a source — one sentence — or None if it can.

    Only ever asked about a layer that IS remote and IS NOT registerable, so "this is a local file"
    is not one of the answers.
    """
    key = (provider or "").strip().lower()
    params = parse_uri(uri)
    named = UNSUPPORTED_PROVIDERS.get(key)
    if named:
        return ("this is {0}, and GeoDeploy's external sources are XYZ, WMS and WFS. Save it "
                "locally and upload that instead.".format(named))
    if key == "wms":
        kind = (params.get("type") or "").strip().lower()
        if kind and kind not in ("xyz", "wmts"):
            return ("this is a {0} source, and GeoDeploy's external sources are XYZ, WMS, WMTS, "
                    "WFS, OGC API - Features, vector tiles and PMTiles.".format(kind))
        if not params.get("layers"):
            return ("this layer names no `layers`, so there is nothing to register — a WMS or WMTS "
                    "source has to say which layer of the service it draws.")
    if key == "vectortile":
        if not (is_tile_template(params.get("url") or "")
                or (params.get("url") or "").lower().endswith((".json", ".pmtiles"))):
            return ("this tile set is neither a {z}/{x}/{y} template, a TileJSON nor a .pmtiles "
                    "archive, so there is no address to fetch tiles from.")
    if key in ("oapif", "ogcapif") and not (params.get("typename") or params.get("collection")
                                            or "/collections/" in (params.get("url") or "")):
        return ("this OGC API layer names no collection, so there is nothing to register — the "
                "URL has to contain /collections/<id>, or the collection has to be named.")
    if key in ("wfs", "wfs2") and not (params.get("typename") or params.get("typeName")):
        return "this WFS layer names no `typename`, so there is nothing to register."
    url = (params.get("url") or "").strip()
    if url and not url.lower().startswith(("http://", "https://")):
        return "its address is not an http(s) URL, which is all an external source can hold."
    return None


def is_remote(qgis_layer) -> bool:
    """Whether this layer is served over the network rather than read from a file.

    ASKED OF THE PARSED URI, not of the raw text. A WFS source is
    `pagingEnabled='true' … url='https://host/wfs' version='auto'` — it neither starts with an
    address nor contains the substring `url=http`, because of the quote, so matching on text alone
    called a WFS layer local. The parser already knows both grammars; this asks it.
    """
    source = (getattr(qgis_layer, "source", lambda: "")() or "").strip()
    if source.lower().startswith(REMOTE_PREFIXES):
        return True
    return (parse_uri(source).get("url") or "").strip().lower().startswith(
        ("http://", "https://"))


def describe(qgis_layer) -> dict | None:
    """The arguments for `client.sources.create`, or None when this is not a registerable service.

    The layer's NAME and ATTRIBUTION come from the layer rather than the URI: the name is what the
    user has called it in their own project, and the attribution is a credit the provider requires
    — carried because a portal shows it, and because dropping it would republish somebody's data
    without the notice their licence asks for.
    """
    spec = spec_from_uri(_provider_of(qgis_layer),
                         getattr(qgis_layer, "source", lambda: "")() or "")
    if not spec:
        return None
    name = (getattr(qgis_layer, "name", lambda: "")() or "").strip()
    spec["name"] = name or spec["source_type"].upper()
    credit = _attribution_of(qgis_layer)
    if credit:
        spec["attribution"] = credit
    return spec


def refusal(qgis_layer) -> str | None:
    """Why this layer cannot become a source, for a layer that is remote. None when it can."""
    return refusal_from_uri(_provider_of(qgis_layer),
                            getattr(qgis_layer, "source", lambda: "")() or "")


def _provider_of(qgis_layer) -> str:
    """`providerType()`, or the empty string for anything that does not answer."""
    try:
        return str(qgis_layer.providerType() or "")
    except Exception:                   # noqa: BLE001  # nosec B110 - intentional: a layer that cannot say is simply not a service
        return ""


def _attribution_of(qgis_layer) -> str:
    """The credit the provider asks for, wherever this QGIS keeps it.

    QGIS moved it: `QgsMapLayer.attribution()` through 3.32, `serverProperties().attribution()`
    after, and a layer may instead carry it as metadata rights. Asking all three keeps the credit
    travelling on every build rather than on the one this was written against.
    """
    try:
        value = qgis_layer.attribution()
        if value:
            return str(value).strip()
    except Exception:                   # noqa: BLE001  # nosec B110 - intentional: try the next spelling
        pass
    try:
        value = qgis_layer.serverProperties().attribution()
        if value:
            return str(value).strip()
    except Exception:                   # noqa: BLE001  # nosec B110 - intentional: try the next spelling
        pass
    try:
        rights = [str(r).strip() for r in (qgis_layer.metadata().rights() or []) if str(r).strip()]
        if rights:
            return "; ".join(rights)
    except Exception:                   # noqa: BLE001  # nosec B110 - intentional: a missing credit is not a failure
        pass
    return ""
