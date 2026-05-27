"""External data source connections (future: WMS, WFS, external PostGIS)."""
from fastapi import APIRouter

router = APIRouter(prefix="/data/sources", tags=["sources"])


@router.get("")
async def list_sources():
    return []
