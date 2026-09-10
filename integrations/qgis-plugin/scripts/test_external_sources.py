"""A QGIS layer served by somebody else, pushed as a GeoDeploy EXTERNAL SOURCE.

WHAT THIS IS FOR. GeoDeploy has held external sources — a WMS, an XYZ tile set, a WFS, referenced
and never ingested — since long before the plugin knew about them. Pushing one from QGIS was
refused with "served from elsewhere, not a local file", and inside a GROUP it was worse: the layer
fell in with the local files and `export.prepare` raised in the push worker, which aborted the whole
publish. One basemap in a group meant nothing reached the portal.

THE URIS BELOW ARE NOT INVENTED. Each was produced by QGIS itself — `QgsProviderRegistry.encodeUri`
and `QgsDataSourceUri` — and pasted here, so this file tests the parser against the grammar QGIS
actually writes rather than against the grammar it was written from memory to expect. The real-QGIS
harness re-derives them (`test_real_qgis.py`), which is what keeps these fixtures honest as QGIS
changes; this file is what makes the parsing testable in a second, anywhere.

Run it:

    python3 scripts/test_external_sources.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.join(HERE, "..")
# BOTH, because these two modules are loaded differently. `external` has no relative imports and is
# read flat, like every other stub test here does it; `portals` says `from .connection import …`
# and so must come through the package — with the vendored client on the path, since that is where
# the package's own imports look.
sys.path.insert(0, os.path.join(PLUGIN, "geodeploy_qgis", "vendor"))
sys.path.insert(0, os.path.join(PLUGIN, "geodeploy_qgis"))
sys.path.insert(0, PLUGIN)

import external                                                                  # noqa: E402
from geodeploy_qgis import portals                                               # noqa: E402


def _server_module():
    """`api/geodeploy/services/external_sources.py`, when this checkout has it.

    The plugin ships on its own — a user installs a zip — so the server is not importable in every
    place this runs. When it IS (this repository, and CI), the two vocabularies are compared,
    because two copies of one list in two languages is exactly the thing that drifts.
    """
    import importlib.util
    path = os.path.join(PLUGIN, "..", "..", "api", "geodeploy", "services", "external_sources.py")
    if not os.path.exists(path):
        return None
    spec = importlib.util.spec_from_file_location("gd_server_external_sources", path)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception:                                                            # noqa: BLE001
        return None                     # httpx missing, say: not a failure of the plugin
    return module

FAILURES = []
CHECKS = [0]


def check(name, condition, detail=""):
    CHECKS[0] += 1
    if condition:
        print("  ok   {0}".format(name))
    else:
        print("  FAIL {0}{1}".format(name, "  — " + str(detail)[:400] if detail else ""))
        FAILURES.append(name)


# ── URIs as QGIS writes them ─────────────────────────────────────────────────────────────────────
XYZ = "type=xyz&url=https://tile.openstreetmap.org/%7Bz%7D/%7Bx%7D/%7By%7D.png&zmax=19&zmin=0"
WMS = ("crs=EPSG:3857&format=image/png&layers=OSM-WMS&styles=&"
       "url=https://ows.terrestris.de/osm/service")
WMTS = ("format=image/png&layers=topo&styles=default&tileMatrixSet=GoogleMapsCompatible&"
        "url=https://example.org/wmts")
WFS = (" pagingEnabled='true' srsname='EPSG:4326' typename='osm:water_areas' "
       "url='https://ahocevar.com/geoserver/wfs' version='auto'")
WFS_PINNED = (" restrictToRequestBBOX=1 typename='ms:roads' "
              "url='https://example.org/wfs' version='2.0.0'")
VECTOR_TILES = "type=xyz&url=https://example.org/tiles/%7Bz%7D/%7Bx%7D/%7By%7D.pbf&zmax=14&zmin=0"
PMTILES = "type=xyz&url=https://files.example.org/basemap.pmtiles"
OAPIF = (" pagingEnabled='true' restrictToRequestBBOX='1' typename='roads' "
         "url='https://example.org/ogc' ")
ARCGIS = "crs=EPSG:3857&url=https://example.org/arcgis/rest/services/Roads/MapServer"
WCS = "url=https://example.org/wcs&identifier=dem&format=image/tiff"


class FakeLayer(object):
    """The four things `external` asks a QGIS layer: its provider, source, name and credit."""

    def __init__(self, provider, source, name="A layer", attribution=""):
        self._provider, self._source = provider, source
        self._name, self._attribution = name, attribution

    def providerType(self):
        return self._provider

    def source(self):
        return self._source

    def name(self):
        return self._name

    def attribution(self):
        return self._attribution


def main():
    print("A service layer becomes an external source, and one we have no kind for says so\n")

    # ── the two grammars ─────────────────────────────────────────────────────────────────────────
    print("Reading QGIS's provider URIs")
    check("an `&`-joined URI is read, and its values un-percent-encoded",
          external.parse_uri(XYZ).get("url") == "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
          external.parse_uri(XYZ))
    quoted = external.parse_uri(WFS)
    check("a quoted URI is read", quoted.get("typename") == "osm:water_areas", quoted)
    check("...including its bare pairs",
          external.parse_uri(WFS_PINNED).get("restrictToRequestBBOX") == "1",
          external.parse_uri(WFS_PINNED))
    check("neither grammar mistakes the other", external.parse_uri("") == {}, "")

    # ── what each service becomes ────────────────────────────────────────────────────────────────
    print("\nWhat GeoDeploy is asked to store")
    xyz = external.spec_from_uri("wms", XYZ)
    check("XYZ tiles become an xyz source",
          (xyz or {}).get("source_type") == "xyz", xyz)
    check("...with the template intact — the braces are the whole point",
          (xyz or {}).get("url") == "https://tile.openstreetmap.org/{z}/{x}/{y}.png", xyz)

    wms = external.spec_from_uri("wms", WMS)
    check("a WMS becomes a wms source", (wms or {}).get("source_type") == "wms", wms)
    check("...naming the layer inside it, which the server requires",
          (wms or {}).get("layer_name") == "OSM-WMS", wms)
    check("...and the image format it was drawn with",
          (wms or {}).get("image_format") == "image/png", wms)

    wfs = external.spec_from_uri("WFS", WFS)
    check("a WFS becomes a wfs source", (wfs or {}).get("source_type") == "wfs", wfs)
    check("...naming its typeName", (wfs or {}).get("layer_name") == "osm:water_areas", wfs)
    # "auto" is QGIS asking the server what it speaks. Storing it would record a version the
    # provider never claimed, and the server has its own default for exactly this case.
    check("...and QGIS's 'auto' version is not stored as a version",
          (wfs or {}).get("version") is None, wfs)
    check("a version somebody pinned IS stored",
          (external.spec_from_uri("WFS", WFS_PINNED) or {}).get("version") == "2.0.0",
          external.spec_from_uri("WFS", WFS_PINNED))

    # ── ONE QGIS PROVIDER, THREE SERVICES ────────────────────────────────────────────────────────
    # `wms` serves XYZ, WMS and WMTS, and only the URI tells them apart. Sending a WMTS as a WMS
    # would publish a layer asking GetMap of a server that only speaks GetTile: nothing drawn, no
    # error anywhere.
    wmts = external.spec_from_uri("wms", WMTS)
    check("a WMTS is recognised as a WMTS", (wmts or {}).get("source_type") == "wmts", wmts)
    check("...carrying its tile matrix set",
          (wmts or {}).get("matrix_set") == "GoogleMapsCompatible", wmts)
    check("...and the layer it draws", (wmts or {}).get("layer_name") == "topo", wmts)

    tiles = external.spec_from_uri("vectortile", VECTOR_TILES)
    check("a vector tile set is its own kind", (tiles or {}).get("source_type") == "vectortile",
          tiles)
    check("...with the template kept whole",
          (tiles or {}).get("url") == "https://example.org/tiles/{z}/{x}/{y}.pbf", tiles)
    pm = external.spec_from_uri("vectortile", PMTILES)
    check("a PMTiles archive is recognised by its own extension",
          (pm or {}).get("source_type") == "pmtiles", pm)
    oapif = external.spec_from_uri("OAPIF", OAPIF)
    check("an OGC API - Features layer travels",
          (oapif or {}).get("source_type") == "ogcapi", oapif)
    check("...naming its collection", (oapif or {}).get("layer_name") == "roads", oapif)

    # ── and what still has no home, by name ──────────────────────────────────────────────────────
    print("\nWhat has no home, and says which")
    check("ArcGIS REST is not registered as something else",
          external.spec_from_uri("arcgismapserver", ARCGIS) is None,
          external.spec_from_uri("arcgismapserver", ARCGIS))
    why = external.refusal_from_uri("arcgismapserver", ARCGIS) or ""
    check("...and is refused BY NAME", "ArcGIS" in why, why)
    # A WCS is not a display service AT ALL, which is a different thing from "not supported yet" —
    # so the message says what it is and where to go instead.
    wcs_why = external.refusal_from_uri("wcs", WCS) or ""
    check("a WCS says why it cannot be drawn", "coverage" in wcs_why.lower(), wcs_why)
    check("...and points at the service that CAN be", "WMS" in wcs_why, wcs_why)
    check("a non-http address is not sent to an endpoint that would reject it",
          external.spec_from_uri("wms", "type=xyz&url=file:///tmp/tiles/{z}/{x}/{y}.png") is None,
          "accepted")
    check("a vector tile layer with no fetchable address is refused with a reason",
          "template" in (external.refusal_from_uri(
              "vectortile", "url=https://example.org/service") or ""),
          external.refusal_from_uri("vectortile", "url=https://example.org/service"))

    # ── describing a whole layer ─────────────────────────────────────────────────────────────────
    print("\nDescribing the layer, not just its URI")
    described = external.describe(FakeLayer("wms", XYZ, "OSM Standard", "© OpenStreetMap"))
    check("the layer's own name travels", (described or {}).get("name") == "OSM Standard", described)
    # A portal SHOWS the credit. Dropping it would republish somebody's service without the notice
    # their licence asks for, which is the one part of this that is not merely cosmetic.
    check("so does the provider's credit",
          (described or {}).get("attribution") == "© OpenStreetMap", described)
    check("a layer with no credit simply has none",
          "attribution" not in (external.describe(FakeLayer("wms", XYZ)) or {}),
          external.describe(FakeLayer("wms", XYZ)))
    check("a local layer is not a service",
          external.describe(FakeLayer("ogr", "/data/roads.gpkg|layername=roads")) is None)
    check("...and is not remote either, so it is an upload",
          not external.is_remote(FakeLayer("ogr", "/data/roads.gpkg")))
    check("a service layer is remote", external.is_remote(FakeLayer("wms", XYZ)))

    # ── the push plan sorts them ─────────────────────────────────────────────────────────────────
    print("\nWhat a group push does with them")

    class Node(object):
        """The layer-tree node `plan_push` walks: a layer, its visibility, no children."""

        def __init__(self, layer):
            self._layer = layer

        def children(self):
            return []

        def layer(self):
            return self._layer

        def isVisible(self):
            return True

        def customProperty(self, _key):
            return None

        def name(self):
            return "group"

    class Group(Node):
        def __init__(self, kids):
            Node.__init__(self, None)
            self._kids = kids

        def children(self):
            return self._kids

        def layer(self):
            return None

    class Tagless(FakeLayer):
        """A layer that has never been to GeoDeploy: no identity, so it is new to the portal."""

        def customProperty(self, _key):
            return None

        def opacity(self):
            return 1.0

    group = Group([Node(Tagless("wms", XYZ, "OSM")),
                   Node(Tagless("WFS", WFS, "Water areas")),
                   Node(Tagless("vectortile", VECTOR_TILES, "Somebody's basemap")),
                   Node(Tagless("arcgismapserver", ARCGIS, "A county service")),
                   Node(Tagless("ogr", "/data/roads.gpkg", "Roads"))])
    plan = portals.plan_push(group, lambda _l, _t: {}, [])

    names = [name for name, _l, _n, _s in plan.get("sources") or []]
    check("every service GeoDeploy can hold is planned as a source",
          names == ["OSM", "Water areas", "Somebody's basemap"], names)
    kinds = [spec["source_type"] for _n, _l, _x, spec in plan.get("sources") or []]
    check("...each as the right kind", kinds == ["xyz", "wfs", "vectortile"], kinds)
    # THE BUG THIS PREVENTS: a remote layer used to land here, and `export.prepare` then raised
    # inside the push worker — which aborted the publish entirely.
    uploads = [name for name, _l, _n in plan.get("uploads") or []]
    check("only the local file is an upload", uploads == ["Roads"], uploads)
    unsupported = [name for name, _why in plan.get("unsupported") or []]
    check("the service we have no kind for is named, not attempted",
          unsupported == ["A county service"], unsupported)
    check("...with a reason a user can act on",
          "ArcGIS" in (plan["unsupported"][0][1] if plan.get("unsupported") else ""),
          plan.get("unsupported"))

    # ── and the dialog says all of it before anything happens ────────────────────────────────────
    print("\nWhat the dialog says first")
    import diffdialog
    text = diffdialog.summarise({
        "sources": ["OSM — XYZ https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
        "unsupported": ["Somebody's basemap — this is vector tiles (including PMTiles)…"],
        "uploads": ["Roads"]})
    check("the services are listed", "OSM — XYZ" in text, text)
    check("...said to be referenced rather than copied",
          "not copied" in text or "not uploaded" in text, text)
    check("what will be left out is listed too", "Somebody's basemap" in text, text)
    check("...and uploads stay their own section", "Roads" in text, text)

    # ── the plugin and the server must agree ─────────────────────────────────────────────────────
    # Two copies of one vocabulary, in two languages, in two deployment units. They drift the way
    # everything in this codebase drifts when nothing compares them.
    print("\nThe plugin and the instance agree about what exists")
    server = _server_module()
    if server is None:
        print("  --   skipped: the API package is not importable from here")
    else:
        check("the same source types",
              tuple(external.SOURCE_TYPES) == tuple(server.SOURCE_TYPES),
              (external.SOURCE_TYPES, server.SOURCE_TYPES))
        check("the same template tokens",
              external._TEMPLATE_TOKENS == server._TEMPLATE_TOKENS,
              (external._TEMPLATE_TOKENS, server._TEMPLATE_TOKENS))
        for uri, provider in ((XYZ, "wms"), (WMS, "wms"), (WMTS, "wms"), (WFS, "WFS"),
                              (VECTOR_TILES, "vectortile"), (PMTILES, "vectortile"),
                              (OAPIF, "OAPIF")):
            spec = external.spec_from_uri(provider, uri) or {}
            if spec:
                check("the instance would accept a {0} source".format(spec["source_type"]),
                      spec["source_type"] in server.SOURCE_TYPES, spec)

    print("\n{0} checks, {1} failed".format(CHECKS[0], len(FAILURES)))
    for name in FAILURES:
        print("   FAILED: {0}".format(name))
    sys.exit(1 if FAILURES else 0)


if __name__ == "__main__":
    main()
