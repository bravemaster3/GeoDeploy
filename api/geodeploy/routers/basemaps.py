from fastapi import APIRouter

from ..services.portal_generator import BASEMAP_CATALOG

router = APIRouter(prefix="/basemaps", tags=["basemaps"])


@router.get("")
async def list_basemaps():
    """The shared basemap catalog — the single source of truth (portal_generator.BASEMAP_CATALOG).

    Public (no auth): the editor's basemap picker fetches this, and published portals bake the same
    list in via `geodeploy.basemaps`, so adding a basemap is a ONE-place change (the Python list)."""
    return BASEMAP_CATALOG
