"""External data source connections — somebody else's service, displayed without ingesting it.

`xyz`, `wms` and `wmts` are raster tiles the browser fetches from the provider. `wfs` and `ogcapi`
are features fetched through the public same-origin GeoJSON proxy below. `vectortile` and `pmtiles`
are tiles fetched through the tile proxy below — an MVT tile and a PMTiles range read are
cross-origin XHRs, so a provider without a CORS policy would otherwise break them silently, as an
empty layer with a console error the map's reader never sees. `services/external_sources` has the
full table and the reasoning.

The provider's licence applies — `attribution` is surfaced on the map.
"""
import json

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from ...json_safe import SafeJSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...database import get_db
from ...deps import require_scope
from ...models import ExternalSource, User
from ...schemas import ExternalSourceCreate, ExternalSourceOut, PortalRefOut, VisibilityUpdate
from ...services import external_sources as ext
from ..common import (creator_names, portals_using, prune_layer_from_portals, record_audit, visible_to)

router = APIRouter(prefix="/data/sources", tags=["sources"])


def _to_out(src: ExternalSource) -> ExternalSourceOut:
    out = ExternalSourceOut.from_orm_json(src)
    out.tile_url = ext.tile_url(src)
    out.data_url = ext.features_url(src)
    return out


@router.get("", response_model=list[ExternalSourceOut])
async def list_sources(user: User = Depends(require_scope("data:read")), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(ExternalSource).where(visible_to(user, ExternalSource)).order_by(ExternalSource.created_at.desc())
    )
    sources = result.scalars().all()
    names = await creator_names(db, sources)
    out = []
    for s in sources:
        o = _to_out(s)
        o.created_by = names.get(s.user_id)
        out.append(o)
    return out


@router.get("/{source_id}/usage", response_model=list[PortalRefOut])
async def source_usage(source_id: int, user: User = Depends(require_scope("data:read")),
                       db: AsyncSession = Depends(get_db)):
    """Portals that include this external source — shown in the delete-confirmation dialog."""
    return [PortalRefOut.model_validate(p) for p in await portals_using(db, "external", source_id)]


@router.post("", response_model=ExternalSourceOut, status_code=201)
async def create_source(
    req: ExternalSourceCreate,
    request: Request,
    user: User = Depends(require_scope("data:write")),
    db: AsyncSession = Depends(get_db),
):
    url = (req.url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        raise HTTPException(400, "URL must start with http:// or https://")

    # MIXED CONTENT. A browser will not load an `http://` tile into an `https://` page, and it says
    # so only in the console — the source registers, the layer appears in the list, and the map
    # stays empty. So an http address is resolved HERE, while there is somebody to tell: upgraded
    # when the provider answers over https (almost all of them do), refused when it cannot, because
    # storing it would be storing a layer that can never draw.
    #
    # Only when THIS instance is served over https. On a plain-http install — a LAN deployment, a
    # machine behind a VPN — an http tile is exactly right and nothing blocks it.
    if (url.lower().startswith("http://")
            and (request.headers.get("x-forwarded-proto") or request.url.scheme) == "https"):
        if await ext.serves_over_https(url):
            url = "https://" + url[len("http://"):]
        else:
            raise HTTPException(400, ext.MIXED_CONTENT_HINT)

    kind = ext.kind_for(req.source_type)

    # A LAYER NAME IS PART OF THE ADDRESS for the services that have more than one layer behind one
    # URL. `ogcapi` is the exception: its collection is often already IN the URL somebody pasted.
    if req.source_type in ("wms", "wmts", "wfs") and not (req.layer_name or "").strip():
        raise HTTPException(400, f"{req.source_type.upper()} requires a layer name.")
    if req.source_type == "ogcapi" and not ((req.layer_name or "").strip()
                                            or ext.oapif_collection_of(url)):
        raise HTTPException(
            400, "An OGC API - Features source needs a collection: either in the URL "
                 "(…/collections/<id>) or as the layer name.")

    geometry_type = None
    bbox_json = None
    version = req.version
    source_layer = (req.source_layer or "").strip() or None
    layer_name = (req.layer_name or "").strip() or None
    min_zoom = max_zoom = None

    # EVERY KIND THAT CAN BE VALIDATED IS VALIDATED HERE. A typo reported now, with the address in
    # front of you, is a different thing from an empty layer on a published portal next week — and
    # the probe is also where the style's missing pieces come from (which layer inside the tiles,
    # which zooms, which extent, whether the archive is raster or vector).
    if req.source_type == "wfs":
        try:
            info = await ext.probe_wfs(url, layer_name, req.version)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, f"Could not connect to WFS: {exc}") from exc
        geometry_type = info["geometry_type"]
        version = info["version"]
        bbox_json = json.dumps(info["bbox"]) if info.get("bbox") else None
    elif req.source_type == "ogcapi":
        try:
            info = await ext.probe_oapif(url, layer_name)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, f"Could not read that collection: {exc}") from exc
        geometry_type = info["geometry_type"]
        layer_name = info.get("collection") or layer_name
        bbox_json = json.dumps(info["bbox"]) if info.get("bbox") else None
    elif req.source_type == "vectortile":
        try:
            info = await ext.probe_vector_tiles(url, source_layer)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, f"Could not use that tile set: {exc}") from exc
        # The TEMPLATE is stored, not the TileJSON URL: the style needs the thing MapLibre fetches,
        # and resolving it once here means a portal is not at the mercy of a TileJSON that moves.
        url = info["tiles"] or url
        source_layer = info["source_layer"]
        min_zoom, max_zoom = info.get("min_zoom"), info.get("max_zoom")
        bbox_json = json.dumps(info["bbox"]) if info.get("bbox") else None
    elif req.source_type == "pmtiles":
        try:
            info = await ext.probe_pmtiles(url, source_layer)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, f"Could not read that archive: {exc}") from exc
        # An archive SAYS whether it holds raster or vector tiles, so the kind is read, not guessed.
        kind = info["kind"]
        source_layer = info.get("source_layer")
        min_zoom, max_zoom = info.get("min_zoom"), info.get("max_zoom")
        bbox_json = json.dumps(info["bbox"]) if info.get("bbox") else None

    src = ExternalSource(
        user_id=user.id,
        name=req.name.strip() or layer_name or req.source_type.upper(),
        source_type=req.source_type,
        kind=kind,
        url=url,
        layer_name=layer_name,
        source_layer=source_layer,
        matrix_set=(req.matrix_set or "").strip() or None,
        min_zoom=min_zoom,
        max_zoom=max_zoom,
        version=version,
        image_format=req.image_format,
        attribution=(req.attribution or "").strip() or None,
        geometry_type=geometry_type,
        bbox=bbox_json,
    )
    db.add(src)
    await db.commit()
    await db.refresh(src)
    await record_audit(db, user, "source.create", "source", src.id, {"name": src.name, "kind": src.kind})
    return _to_out(src)


@router.put("/{source_id}/sharing", response_model=ExternalSourceOut)
async def save_sharing(
    source_id: int,
    body: VisibilityUpdate,
    user: User = Depends(require_scope("data:write")),
    db: AsyncSession = Depends(get_db),
):
    """Workspace visibility for an external source: private (creator + admins) | organization (all
    members). No public tier — sources reference third-party services and aren't in STAC. Any editor+
    may re-share a source they can SEE (a private source they don't own 404s via the filter)."""
    src = (await db.execute(
        select(ExternalSource).where(ExternalSource.id == source_id, visible_to(user, ExternalSource))
    )).scalar_one_or_none()
    if not src:
        raise HTTPException(404, "Source not found.")
    src.visibility = body.visibility
    await db.commit()
    await db.refresh(src)
    await record_audit(db, user, "source.share", "source", src.id,
                       {"name": src.name, "visibility": src.visibility})
    return _to_out(src)


@router.delete("/{source_id}", status_code=204)
async def delete_source(source_id: int, user: User = Depends(require_scope("data:write")), db: AsyncSession = Depends(get_db)):
    src = (await db.execute(
        select(ExternalSource).where(ExternalSource.id == source_id, visible_to(user, ExternalSource))
    )).scalar_one_or_none()
    if not src:
        raise HTTPException(404, "Source not found.")
    source_name = src.name
    await db.delete(src)
    await db.commit()
    pruned = await prune_layer_from_portals(db, "external", source_id)  # sources are layer_type 'external'
    await record_audit(db, user, "source.delete", "source", source_id,
                       {"name": source_name, "portals_updated": [p.title for p in pruned]})


@router.get("/{source_id}/features.geojson")
async def source_features(source_id: int, db: AsyncSession = Depends(get_db)):
    """PUBLIC GeoJSON proxy for a source drawn from features (published portals are anonymous).

    Only proxies a stored, admin-created source URL — the caller supplies an id, never an address —
    so this is not an open SSRF.
    """
    src = (await db.execute(
        select(ExternalSource).where(ExternalSource.id == source_id))).scalar_one_or_none()
    # By SOURCE TYPE, not by kind: a `vectortile` source is vector too, and it is drawn from tiles.
    # Answering here for one would hand the portal an empty feature collection for a layer that
    # actually has a tile endpoint, which reads as "the source is broken".
    if not src or src.source_type not in ext.PROXIED_FEATURE_TYPES:
        raise HTTPException(404, "Feature source not found.")
    try:
        if src.source_type == "ogcapi":
            gj = await ext.fetch_oapif_geojson(src)
        else:
            gj = await ext.fetch_wfs_geojson(src)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Upstream {src.source_type.upper()} error: {exc}") from exc
    return SafeJSONResponse(gj)


@router.get("/{source_id}/tiles/{z}/{x}/{y}")
async def source_tile(source_id: int, z: int, x: int, y: int,
                      db: AsyncSession = Depends(get_db)):
    """PUBLIC tile proxy for the kinds a browser cannot fetch cross-origin.

    WHY THIS EXISTS. A raster tile is fetched the way the web has always fetched images and every
    tile server is configured for it. An MVT tile is a cross-origin XHR, and a PMTiles archive is
    read with byte RANGES — both need `Access-Control-Allow-Origin` from the provider, and a
    provider who has not set one breaks the layer silently: an empty map and a console error the
    reader will never see. Same-origin through us, it works for every visitor.

    Same public terms as the GeoJSON proxy and Martin's `/tiles/` — a published portal is
    unauthenticated, so its display sources must be too. And the same SSRF answer: the caller
    supplies an id, never an address.

    204 for a tile that genuinely is not there. A sparse archive is normal, and a 404 invites
    clients to retry something that will never appear.
    """
    src = (await db.execute(
        select(ExternalSource).where(ExternalSource.id == source_id))).scalar_one_or_none()
    if not src or src.source_type not in ext.PROXIED_TILE_TYPES:
        raise HTTPException(404, "Tiled source not found.")
    if z < 0 or z > 24 or x < 0 or y < 0 or x >= (1 << z) or y >= (1 << z):
        # Outside the pyramid entirely: not an error, just nothing — and refusing here keeps a
        # crafted URL from becoming an upstream request.
        return Response(status_code=204, headers=_TILE_HEADERS)

    try:
        if src.source_type == "pmtiles":
            got = await ext.fetch_pmtiles_tile(src, z, x, y)
            if got is None:
                return Response(status_code=204, headers=_TILE_HEADERS)
            body, media = got
        else:
            got = await ext.fetch_vector_tile(src, z, x, y)
            if got is None:
                return Response(status_code=204, headers=_TILE_HEADERS)
            body, media = got
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Upstream tile error: {exc}") from exc
    return Response(body, media_type=media, headers=_TILE_HEADERS)


#: A tile is public, immutable for an hour, and read cross-origin by portals on other hosts.
_TILE_HEADERS = {"Access-Control-Allow-Origin": "*", "Cache-Control": "public, max-age=3600"}
