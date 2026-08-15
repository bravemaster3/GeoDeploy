"""External sources — WMS / XYZ rasters and WFS vectors shown in a portal without ingesting them.

A source is a reference, not a copy: nothing is downloaded, and the published portal fetches from
the provider (raster) or through GeoDeploy's same-origin GeoJSON proxy (WFS, so an unauthenticated
portal is not blocked by the provider's CORS policy).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .errors import NotFoundError, ValidationError

SOURCE_TYPES = ("xyz", "wms", "wfs")


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
               attribution: Optional[str] = None) -> Dict[str, Any]:
        """Register a source. A WFS is probed on creation, which is where a wrong `typeName` or an
        unreachable host surfaces — better here than as an empty layer on a published map."""
        if source_type not in SOURCE_TYPES:
            raise ValidationError(400, "Source type must be one of {0}.".format(
                ", ".join(SOURCE_TYPES)))
        if source_type in ("wms", "wfs") and not layer_name:
            raise ValidationError(
                400, "A {0} source needs --layer-name (the WMS `layers` / WFS `typeName`)."
                     .format(source_type.upper()))
        body = {"name": name, "source_type": source_type, "url": url}
        for key, value in (("layer_name", layer_name), ("version", version),
                           ("image_format", image_format), ("attribution", attribution)):
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
