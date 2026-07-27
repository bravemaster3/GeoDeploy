"""GeoLibre interoperability endpoints.

The front door for "GeoLibre → GeoDeploy portal": accept a `.geolibre.json` project (uploaded by a
user, or POSTed by the GeoLibre publish plugin) and turn it into a portal.

`POST /interop/geolibre/preview` is a **dry-run**: parse + translate, return what *would* be imported,
no writes. `POST /interop/geolibre/publish` commits it: create a layer per vector/tile source, kick the
ingest+build orchestrator (`tasks.geolibre_publish`), and return the new portal. Both take the raw
`.geolibre.json` as the JSON body (a manual upload, or the GeoLibre publish plugin).
"""
import json
import os
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from slugify import slugify
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..database import get_db
from ..deps import require_scope
from ..models import ExternalSource, Portal, RasterLayer, UploadJob, User, VectorLayer
from ..routers.portals import _new_slug
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


@router.post("/geolibre/publish", status_code=202)
async def publish_geolibre_project(
    request: Request,
    user: User = Depends(require_scope("portal:write")),
    db: AsyncSession = Depends(get_db),
):
    """Import a `.geolibre.json` and publish it as a GeoDeploy portal.

    Synchronously creates the layer records + the portal shell (with the translated `layer_configs`),
    then hands off to the `geolibre_publish` worker which ingests each layer and builds the bundle.
    Returns 202 with the new portal id/slug and any warnings; the portal finishes publishing async.
    """
    try:
        project = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Request body is not valid JSON.")
    try:
        plan = gli.import_project(project)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    settings = get_settings()
    os.makedirs(f"{settings.data_dir}/temp", exist_ok=True)
    schema_name = f"geodeploy_u{user.id}"
    id_map: dict = {}
    ingest_jobs: list[list] = []      # vector: [job_id, layer_id, tmp_path, name, schema, table]
    raster_jobs: list[list] = []      # raster: [job_id, layer_id, cog_url, s3_key]
    warnings: list[str] = list(plan["warnings"])

    for lyr in plan["layers"]:
        gid = lyr["source_identity"]["geolibre_layer_id"]
        target = lyr["target"]

        if target == "vector":
            if lyr.get("render_mode") == "elevation3d":
                continue  # 3D-Z: not ingested — rides inline as a deck elevation config (plan_to_layer_configs)
            gj = lyr.get("geojson")
            if not gj or not gj.get("features"):
                warnings.append(f"[{lyr['name']}] skipped: no features to ingest.")
                continue
            layer_name = slugify(lyr["name"], separator="_") or "layer"
            table_name = f"{layer_name}_{uuid.uuid4().hex[:6]}"
            tmp_path = f"{settings.data_dir}/temp/{uuid.uuid4()}.geojson"
            with open(tmp_path, "w", encoding="utf-8") as fh:
                json.dump(gj, fh)
            layer = VectorLayer(user_id=user.id, name=lyr["name"], table_name=table_name,
                                schema_name=schema_name, status="processing")
            db.add(layer)
            await db.flush()
            job_id = str(uuid.uuid4())
            db.add(UploadJob(id=job_id, layer_id=layer.id, layer_type="vector"))
            ingest_jobs.append([job_id, layer.id, tmp_path, layer_name, schema_name, table_name])
            id_map[gid] = layer.id

        elif target == "external":
            spec = gli.external_source_spec(lyr)
            if not spec:
                warnings.append(
                    f"[{lyr['name']}] external type '{lyr['geolibre_type']}' is not supported yet.")
                continue
            src = ExternalSource(user_id=user.id, name=lyr["name"], **spec)
            db.add(src)
            await db.flush()
            id_map[gid] = src.id

        else:  # raster (COG): the worker downloads the URL → the existing GeoTIFF→COG→MinIO ingest.
            url = (lyr.get("source") or {}).get("url")
            if not url or not str(url).lower().startswith("https://"):
                warnings.append(f"[{lyr['name']}] raster skipped: needs an https COG URL.")
                continue
            base = slugify(lyr["name"], separator="_") or "raster"
            s3_key = f"rasters/{user.id}/{uuid.uuid4().hex}/{base}.tif"
            rlayer = RasterLayer(user_id=user.id, name=lyr["name"], s3_key=s3_key, status="processing")
            db.add(rlayer)
            await db.flush()
            job_id = str(uuid.uuid4())
            db.add(UploadJob(id=job_id, layer_id=rlayer.id, layer_type="raster"))
            raster_jobs.append([job_id, rlayer.id, url, s3_key])
            id_map[gid] = rlayer.id

    layer_configs, cfg_warnings = gli.plan_to_layer_configs(plan, id_map)
    warnings.extend(cfg_warnings)
    if not layer_configs:
        raise HTTPException(status_code=400,
                            detail="Nothing importable in this project. " + " ".join(warnings))

    pk = gli.plan_to_portal_kwargs(plan, id_map)
    slug = await _new_slug(db)
    portal = Portal(
        user_id=user.id,
        title=pk["title"] or "Imported GeoLibre project",
        slug=slug,
        template_id="minimal",
        layer_configs=json.dumps(layer_configs),
        initial_view=json.dumps(pk["initial_view"]) if pk["initial_view"] else None,
        story=json.dumps(pk["story"]) if pk["story"] else None,
        access_type="public",
    )
    db.add(portal)
    await db.commit()
    await db.refresh(portal)

    # Deferred import so the router module doesn't pull in Celery at load time.
    from ..tasks.geolibre_publish import publish_geolibre_project as publish_task
    publish_task.delay(portal.id, ingest_jobs, raster_jobs)

    return {
        "portal_id": portal.id,
        "slug": slug,
        "layer_count": len(layer_configs),
        "ingesting": len(ingest_jobs) + len(raster_jobs),
        "warnings": warnings,
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
