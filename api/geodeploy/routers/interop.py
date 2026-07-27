"""GeoLibre interoperability endpoints.

The front door for "GeoLibre → GeoDeploy portal": accept a `.geolibre.json` project (uploaded by a
user, or POSTed by the GeoLibre publish plugin) and turn it into a portal.

This first endpoint is a **dry-run preview**: it parses + translates the project (via the pure
`services.geolibre_import` translator) and returns what *would* be imported — per-layer target/render
mode + any symbology that won't carry over — WITHOUT ingesting anything. It is the safe half of the
publish flow and the exact validation the plugin/UI shows the user before committing. The actual
ingestion + portal build (async: each layer through the existing vector/raster ingest, then
`build_portal_bundle`) is the next endpoint, `POST /interop/geolibre/publish`.
"""
from fastapi import APIRouter, Depends, HTTPException, Request

from ..deps import require_scope
from ..models import User
from ..services import geolibre_import as gli

router = APIRouter(prefix="/interop", tags=["interop"])


@router.post("/geolibre/preview")
async def preview_geolibre_project(
    request: Request,
    user: User = Depends(require_scope("portal:write")),
):
    """Parse + translate a `.geolibre.json` (the raw project is the JSON body) and return a preview of
    the import plan — no ingestion, no writes. 400 on a non-project / unsupported format."""
    try:
        project = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Request body is not valid JSON.")
    try:
        plan = gli.import_project(project)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return {
        "portal": plan["portal"],
        "layers": [_layer_summary(lyr) for lyr in plan["layers"]],
        "warnings": plan["warnings"],
    }


def _layer_summary(lyr: dict) -> dict:
    """A compact, response-safe view of a planned layer — never echoes the (possibly huge) geojson."""
    gj = lyr.get("geojson") or {}
    return {
        "name": lyr["name"],
        "geolibre_type": lyr.get("geolibre_type"),
        "target": lyr.get("target"),
        "render_mode": lyr.get("render_mode"),
        "has_z": lyr.get("has_z", False),
        "feature_count": len(gj.get("features", [])) if lyr.get("target") == "vector" else None,
        "maplibre_layer_count": len(lyr.get("maplibre_layers", [])),
        "source_identity": lyr.get("source_identity"),
        "warnings": lyr.get("warnings", []),
    }
