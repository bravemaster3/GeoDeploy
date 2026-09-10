"""External sources — somebody else's service shown in a portal without ingesting it.

A source is a reference, not a copy: nothing is downloaded, and the published portal fetches it at
view time. Where FROM depends on the kind, and the reason is CORS: raster tiles come straight from
the provider, while anything a browser reads as DATA — GeoJSON, a vector tile, a byte range of a
PMTiles archive — is fetched through GeoDeploy so a provider without an
`Access-Control-Allow-Origin` header cannot break the layer silently.

    xyz         raster tiles from a {z}/{x}/{y} template
    wms         rendered map images, one GetMap per tile
    wmts        tiled map images, one GetTile per tile
    wfs         vector features, proxied as GeoJSON
    ogcapi      an OGC API - Features collection, proxied as GeoJSON
    vectortile  a third-party vector tile set, proxied per tile
    pmtiles     a remote PMTiles archive, read a tile at a time by the instance

WCS is not here because it is not a display service: GetCoverage returns a coverage, not map
images, so there is nothing a web map can draw from it. Most WCS servers publish the same data over
WMS — add it that way — or download the coverage and upload it as a raster layer.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .errors import NotFoundError, ValidationError

SOURCE_TYPES = ("xyz", "wms", "wmts", "wfs", "ogcapi", "vectortile", "pmtiles")

#: Kinds where one URL serves many layers, so the request has to name one. `ogcapi` is not here:
#: its collection is usually already IN the URL people paste.
NEEDS_LAYER_NAME = ("wms", "wmts", "wfs")

#: Kinds whose tiles are a CONTAINER of named layers. A style that names none draws nothing at all,
#: silently — so the instance probes for the name (a TileJSON, a PMTiles header) and refuses when
#: it cannot find one. Passing it here skips that guesswork.
TAKES_SOURCE_LAYER = ("vectortile", "pmtiles")


class Sources(object):
    def __init__(self, client: Any):
        self._c = client

    def list(self, query: Optional[str] = None) -> List[Dict[str, Any]]:
        rows = self._c.get("/data/sources") or []
        if query:
            needle = query.lower()
            rows = [r for r in rows if needle in (r.get("name") or "").lower()
                    or needle in (r.get("url") or "").lower()]
        return rows

    def get(self, ref: Any) -> Dict[str, Any]:
        text = str(ref)
        for row in self.list():
            if str(row.get("id")) == text or (row.get("name") or "") == text:
                return row
        raise NotFoundError(404, "No external source matching {0!r}.".format(ref))

    def create(self, name: str, source_type: str, url: str, layer_name: Optional[str] = None,
               version: Optional[str] = None, image_format: Optional[str] = None,
               attribution: Optional[str] = None, source_layer: Optional[str] = None,
               matrix_set: Optional[str] = None) -> Dict[str, Any]:
        """Register a source.

        EVERYTHING THAT CAN BE VALIDATED IS VALIDATED BY THE INSTANCE, on creation: a WFS and an
        OGC API collection are fetched, a TileJSON is read, a PMTiles header is parsed. That is
        where a wrong `typeName`, an unreachable host or a URL that is not an archive surfaces —
        and it is also where the missing pieces come from (the layer inside the tiles, the zoom
        range, the extent, and whether an archive holds raster or vector tiles).

        The checks HERE are only the ones that need no network, so an obvious mistake is a message
        rather than a round trip.
        """
        if source_type not in SOURCE_TYPES:
            raise ValidationError(400, "Source type must be one of {0}.".format(
                ", ".join(SOURCE_TYPES)))
        if source_type in NEEDS_LAYER_NAME and not layer_name:
            raise ValidationError(
                400, "A {0} source needs --layer-name (the WMS/WMTS `layers`, the WFS `typeName`)."
                     .format(source_type.upper()))
        if source_type == "ogcapi" and not layer_name and "/collections/" not in (url or ""):
            raise ValidationError(
                400, "An OGC API - Features source needs a collection: either in the URL "
                     "(.../collections/<id>) or as --layer-name.")
        body = {"name": name, "source_type": source_type, "url": url}
        for key, value in (("layer_name", layer_name), ("version", version),
                           ("image_format", image_format), ("attribution", attribution),
                           ("source_layer", source_layer), ("matrix_set", matrix_set)):
            if value is not None:
                body[key] = value
        return self._c.post("/data/sources", body)

    def usage(self, source_id: Any) -> List[Dict[str, Any]]:
        return self._c.get("/data/sources/{0}/usage".format(int(source_id))) or []

    def share(self, source_id: Any, visibility: str) -> Dict[str, Any]:
        """private | organization. There is no public tier: a source has no asset of ours to
        expose, so "public" would mean nothing."""
        if visibility not in ("private", "organization"):
            raise ValidationError(400, "External sources are private or organization only.")
        return self._c.put("/data/sources/{0}/sharing".format(int(source_id)),
                           {"visibility": visibility})

    def features(self, source_id: Any) -> Dict[str, Any]:
        """The WFS proxied to GeoJSON — the same feed a published portal reads."""
        return self._c.get("/data/sources/{0}/features.geojson".format(int(source_id)), auth=False)

    def delete(self, source_id: Any) -> Any:
        return self._c.delete("/data/sources/{0}".format(int(source_id)))
