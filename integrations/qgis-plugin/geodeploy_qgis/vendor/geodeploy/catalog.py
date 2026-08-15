"""The public read surfaces: STAC, OGC API - Features, templates and basemaps.

These need no credentials — they are what an instance offers the rest of the world, and what QGIS,
ArcGIS, FME and GDAL connect to. Only layers explicitly shared as **public** appear here; the CLI
reaching them anonymously is therefore also the honest way to check what you have actually exposed:
if `geodeploy catalog collections` does not list it, nor can anyone else.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


class Catalog(object):
    def __init__(self, client: Any):
        self._c = client

    # ── the instance index ──────────────────────────────────────────────────────────────────────

    def public(self) -> Dict[str, Any]:
        """Everything this instance offers anonymously: public portals, and public layers grouped
        by storage kind (`raster`, `postgis`, `geoparquet`).

        This is the "paste a URL and see what is there" call — the first screen of a plugin. An
        instance may switch its index off, in which case this raises `NotFoundError`: that is a
        decision ("no index here"), distinct from an empty one ("nothing published").
        """
        return self._c.get("/public", auth=False)

    def public_portals(self) -> List[Dict[str, Any]]:
        return self._c.get("/public/portals", auth=False) or []

    def portal_style(self, style_url: str) -> Dict[str, Any]:
        """A published portal's own `style.json` — sources, layers, folder tree, bounds.

        The whole portal in one anonymous fetch, which is what makes "open this portal in QGIS"
        possible without a token. The URL comes from `public()`; it is a static file in the
        published bundle, not an API route.
        """
        response = self._c.send_absolute("GET", style_url)
        if response.status >= 400:
            from .errors import from_status
            raise from_status(response.status, "Could not read the portal style.", response.url)
        return response.json()

    # ── OGC API - Features ──────────────────────────────────────────────────────────────────────

    def collections(self) -> List[Dict[str, Any]]:
        """One collection per public, ready vector layer. Ids mirror the STAC item ids."""
        data = self._c.get("/ogc/collections", auth=False) or {}
        return data.get("collections") or []

    def collection(self, cid: str) -> Dict[str, Any]:
        return self._c.get("/ogc/collections/{0}".format(cid), auth=False)

    def items(self, cid: str, bbox: Optional[str] = None, limit: int = 10,
              offset: int = 0) -> Dict[str, Any]:
        return self._c.get("/ogc/collections/{0}/items".format(cid),
                           {"bbox": bbox, "limit": limit, "offset": offset}, auth=False)

    def item(self, cid: str, fid: str) -> Dict[str, Any]:
        return self._c.get("/ogc/collections/{0}/items/{1}".format(cid, fid), auth=False)

    def conformance(self) -> Dict[str, Any]:
        """What the OGC endpoint claims to support — Core + GeoJSON, and nothing it does not do."""
        return self._c.get("/ogc/conformance", auth=False)

    # ── STAC ────────────────────────────────────────────────────────────────────────────────────

    def stac(self) -> Dict[str, Any]:
        return self._c.get("/stac", auth=False)

    def stac_collections(self) -> List[Dict[str, Any]]:
        data = self._c.get("/stac/collections", auth=False) or {}
        return data.get("collections") or []

    def stac_items(self, cid: str, bbox: Optional[str] = None, datetime: Optional[str] = None,
                   limit: int = 100, offset: int = 0) -> Dict[str, Any]:
        return self._c.get("/stac/collections/{0}/items".format(cid),
                           {"bbox": bbox, "datetime": datetime, "limit": limit, "offset": offset},
                           auth=False)

    def search(self, bbox: Optional[str] = None, collections: Optional[str] = None,
               datetime: Optional[str] = None, ids: Optional[str] = None,
               limit: int = 100) -> Dict[str, Any]:
        return self._c.get("/stac/search", {"bbox": bbox, "collections": collections,
                                            "datetime": datetime, "ids": ids, "limit": limit},
                           auth=False)

    # ── portal building blocks ──────────────────────────────────────────────────────────────────

    def templates(self) -> List[Dict[str, Any]]:
        """Portal templates, each declaring the experiences (`archetypes`) it may be used for."""
        return self._c.get("/templates", auth=False)

    def basemaps(self) -> List[Dict[str, Any]]:
        return self._c.get("/basemaps", auth=False)
