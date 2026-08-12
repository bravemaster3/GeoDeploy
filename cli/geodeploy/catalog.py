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
