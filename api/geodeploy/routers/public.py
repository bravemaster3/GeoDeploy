"""`GET /api/public` — what this instance offers to someone with no account.

**The gap this closes.** Public layers were already discoverable (STAC, OGC API - Features) and
public portals were already *reachable* — but only if you knew the slug. `GET /portals` requires the
`portal:read` scope, so nothing let a client type an instance URL and see what is there. That is
precisely the first screen of a QGIS plugin, and of any "browse this instance" surface.

So this is one unauthenticated call that answers "what can I have?":

* **portals** — published, `access_type == "public"`. Not password/organization/owner ones: those
  are not public, and listing their titles would leak what an instance holds.
* **layers**, grouped by the three kinds a GIS user actually distinguishes — `raster` (COG),
  `postgis` (served as vector tiles) and `geoparquet` (files) — each with the URLs that suit it.
  Only `is_public`, ready layers, exactly like STAC.

**Discoverability is not the same as access**, which is why there is a switch. Every portal listed
here is already reachable by anyone holding its link; an index makes it *findable*. For a geoportal
that is the whole point, so `public_index_enabled` defaults to **on** — but an instance running one
deliberately-unlisted public portal can turn it off, and then this endpoint 404s rather than
returning an empty list (a client can tell "no index here" from "nothing published").
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import Portal, RasterLayer, SetupConfig, VectorLayer
from ..services import share_links
from .common import by_ref

router = APIRouter(prefix="/public", tags=["public"])

#: The layer kinds this endpoint groups by — storage, which is what decides how a client reads it.
KINDS = ("raster", "postgis", "geoparquet")


@router.get("")
async def public_index(request: Request, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Everything anonymous: the portals, the layers by kind, and where the catalogs live."""
    await _require_enabled(db)
    base = _base_url(request)

    portals = (await db.execute(
        select(Portal).where(Portal.published.is_(True), Portal.access_type == "public")
        .order_by(Portal.published_at.desc().nullslast(), Portal.created_at.desc())
    )).scalars().all()

    vectors = (await db.execute(
        select(VectorLayer).where(VectorLayer.is_public.is_(True), VectorLayer.status == "ready")
        .order_by(VectorLayer.name)
    )).scalars().all()
    rasters = (await db.execute(
        select(RasterLayer).where(RasterLayer.is_public.is_(True), RasterLayer.status == "ready")
        .order_by(RasterLayer.name)
    )).scalars().all()

    grouped: dict[str, list] = {kind: [] for kind in KINDS}
    for layer in vectors:
        kind = "geoparquet" if layer.storage_backend == "geoparquet" else "postgis"
        grouped[kind].append(_vector_out(layer, base))
    for layer in rasters:
        grouped["raster"].append(_raster_out(layer, base))

    return {
        "geodeploy": {"api": base + "/api", "url": base},
        "counts": {"portals": len(portals),
                   **{kind: len(rows) for kind, rows in grouped.items()}},
        "portals": [_portal_out(p, base) for p in portals],
        "layers": grouped,
        "catalogs": {
            # The machine-readable surfaces, so a client can stop reading this endpoint and start
            # reading a standard one as soon as it knows they exist.
            "stac": base + "/api/stac",
            "ogc_features": base + "/api/ogc",
        },
    }


@router.get("/layers/{kind}/{layer_ref}")
async def public_layer(kind: str, layer_ref: str, request: Request,
                       db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """ONE public layer, in the shape the layer page reads — so a public layer has a shareable page.

    The index above lists layers; nothing let an anonymous visitor look at one. That is what the
    plugin's "Open in GeoDeploy" needs: a public layer should open for anybody, and a private one
    should ask them to sign in — the same rule the rest of the anonymous surface follows.

    Deliberately the SAME readability rule as the display endpoints (`_publicly_readable`): a layer
    that is shared, or drawn by a published portal. It adds `default_style`, `columns` and
    `tile_status` to what the index carries, because the page draws the layer and lists its fields;
    it adds nothing about who uploaded it or where it is stored.
    """
    await _require_enabled(db)
    base = _base_url(request)
    if kind == "raster":
        from .data.raster import _describable
        row = (await db.execute(select(RasterLayer).where(by_ref(RasterLayer, layer_ref)))
               ).scalar_one_or_none()
        if not await _describable(row, db):
            raise HTTPException(404, "No public layer at this address.")
        out = _raster_out(row, base)
        out.update({"uid": row.uid, "status": row.status, "band_count": row.band_count,
                    "default_style": _style(row), "file_size": row.file_size,
                    "width": getattr(row, "width", None), "height": getattr(row, "height", None),
                    "nodata_value": getattr(row, "nodata_value", None)})
        return out
    if kind == "vector":
        from .data.vector import _publicly_readable
        row = (await db.execute(select(VectorLayer).where(by_ref(VectorLayer, layer_ref)))
               ).scalar_one_or_none()
        if not row or row.status != "ready" or not await _publicly_readable(row, db):
            raise HTTPException(404, "No public layer at this address.")
        out = _vector_out(row, base)
        columns = []
        if row.columns:
            try:
                columns = json.loads(row.columns)
            except (ValueError, TypeError):
                columns = []
        out.update({"uid": row.uid, "status": row.status, "columns": columns,
                    "storage_backend": getattr(row, "storage_backend", "postgis"),
                    "tile_status": getattr(row, "tile_status", None),
                    "schema_name": row.schema_name, "table_name": row.table_name,
                    "file_size": row.file_size,
                    "default_style": json.loads(row.default_style) if row.default_style else None})
        return out
    raise HTTPException(404, "Unknown layer kind.")


@router.get("/portals")
async def public_portals(request: Request, db: AsyncSession = Depends(get_db)) -> list[dict]:
    """Just the portals — the cheap call for a plugin's browse tree."""
    await _require_enabled(db)
    base = _base_url(request)
    rows = (await db.execute(
        select(Portal).where(Portal.published.is_(True), Portal.access_type == "public")
        .order_by(Portal.published_at.desc().nullslast(), Portal.created_at.desc())
    )).scalars().all()
    return [_portal_out(p, base) for p in rows]


# ── shaping ──────────────────────────────────────────────────────────────────────────────────────

def _portal_out(portal: Portal, base: str) -> dict[str, Any]:
    """A portal as the outside sees it: the slug, never the integer id.

    The id is an internal key that the authenticated API addresses; handing it out here would
    invite clients to build `/api/portals/{id}` URLs that 401, and it renumbers on a restore.
    """
    layout = json.loads(portal.layout_config) if portal.layout_config else {}
    configs = json.loads(portal.layer_configs) if portal.layer_configs else []
    return {
        "slug": portal.slug,
        "title": portal.title,
        "description": (portal.description or "")[:500] or None,
        "experience": (layout or {}).get("archetype") or "webmap",
        "layer_count": len(configs),
        "url": "{0}/portals/{1}/".format(base, portal.slug),
        # The published bundle's own style — sources, layers, folder tree, bounds. A client can
        # load the whole portal from this one file without a token, which is what makes "open this
        # portal in QGIS" possible at all.
        "style_url": "{0}/portals/{1}/style.json".format(base, portal.slug),
        "thumbnail_url": portal.thumbnail_url,
        "published_at": portal.published_at,
    }


def _vector_out(layer: VectorLayer, base: str) -> dict[str, Any]:
    return {
        "id": layer.uid or str(layer.id),
        "name": layer.name,
        "kind": "geoparquet" if layer.storage_backend == "geoparquet" else "postgis",
        "geometry_type": layer.geometry_type,
        "feature_count": layer.feature_count,
        "crs": layer.crs,
        "bbox": json.loads(layer.bbox) if layer.bbox else None,
        "abstract": layer.abstract,
        "keywords": _keywords(layer.keywords),
        "license": layer.license,
        "attribution": layer.attribution,
        "links": _links(share_links.vector_links, layer, base),
        "download": "{0}/api/data/vector/{1}/export".format(base, layer.uid or layer.id),
    }


def _raster_out(layer: RasterLayer, base: str) -> dict[str, Any]:
    return {
        "id": layer.uid or str(layer.id),
        "name": layer.name,
        "kind": "raster",
        "band_count": layer.band_count,
        "crs": layer.crs,
        "bbox": json.loads(layer.bbox) if layer.bbox else None,
        "abstract": layer.abstract,
        "keywords": _keywords(layer.keywords),
        "license": layer.license,
        "attribution": layer.attribution,
        "links": _links(share_links.raster_links, layer, base, _style(layer)),
        "download": "{0}/api/data/raster/{1}/cog".format(base, layer.uid or layer.id),
    }


def _links(builder, layer, base: str, *args) -> list[dict[str, Any]]:
    """Reuse `services/share_links.py` so this endpoint cannot drift from the Share links panel —
    one place decides which artifact suits which tool.

    Best-effort: a link builder that trips over one odd layer must not take the whole index down.
    """
    try:
        return builder(layer, base, *args) or []
    except Exception:  # noqa: BLE001 - the index is more useful incomplete than absent
        return []


def _keywords(raw: str | None) -> list[str]:
    return [k.strip() for k in (raw or "").split(",") if k.strip()]


def _style(layer: RasterLayer) -> dict:
    """The raster's saved styling, so its tile URLs come back rendered the way the layer is meant
    to look — an unstretched 16-bit raster tiles out black, which reads as broken."""
    try:
        return json.loads(layer.default_style) if layer.default_style else {}
    except ValueError:
        return {}


# The origin as the CLIENT reached it (https-aware behind nginx). `share_links.request_base` is the
# one implementation: every URL in this response is meant to be pasted somewhere else, and the
# container's own address (`http://geodeploy-api:8000/…`) is useless to a QGIS user.
_base_url = share_links.request_base


def index_enabled(cfg) -> bool:
    """Only an explicit False switches the index off.

    Three ways this reads as ON, and all three are deliberate: no config row at all (a fresh
    instance), the column NULL (an instance UPGRADED into this feature — the additive
    `ALTER TABLE … DEFAULT TRUE` leaves the column nullable, so an older row can carry NULL), and
    True. An upgrade must never silently unlist what an operator already published.
    """
    if cfg is None:
        return True
    return getattr(cfg, "public_index_enabled", True) is not False


async def _require_enabled(db: AsyncSession) -> None:
    cfg = (await db.execute(select(SetupConfig).limit(1))).scalar_one_or_none()
    if not index_enabled(cfg):
        raise HTTPException(404, "This instance does not publish a public index.")
