"""GeoLibre interoperability endpoints.

The front door for "GeoLibre → GeoDeploy portal": accept a `.geolibre.json` project (uploaded by a
user, or POSTed by the GeoLibre publish plugin) and turn it into a portal.

`POST /interop/geolibre/preview` is a **dry-run**: parse + translate, return what *would* be imported,
no writes. `POST /interop/geolibre/publish` commits it: create a layer per vector/tile source, kick the
ingest+build orchestrator (`tasks.geolibre_publish`), and return the new portal. Both take the raw
`.geolibre.json` as the JSON body (a manual upload, or the GeoLibre publish plugin).

**Write-back round-trip (F5):** `GET /interop/geodeploy/layers` lists editable PostGIS vector layers;
`GET /interop/geodeploy/layers/{id}/features.geojson` returns a layer as editable GeoJSON (load it into
GeoLibre, clean it); `PUT /interop/geodeploy/layers/{id}/features` writes the edited GeoJSON back — a
full REPLACE that re-ingests into the SAME table via `ingest_vector` (updates bbox/count, reloads
Martin), so published portals using the layer reflect the change.
"""
import json
import os
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from slugify import slugify
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from ..config import get_settings
from ..database import get_db
from ..deps import require_scope
from ..models import ExternalSource, Portal, RasterLayer, UploadJob, User, VectorLayer
from ..routers.portals import _new_slug
from ..services import geolibre_import as gli

router = APIRouter(prefix="/interop", tags=["interop"])

# Editable-layer feature cap for the write-back round-trip (GeoLibre editing is for reasonable sizes).
WRITEBACK_FEATURE_CAP = 100000


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


# ── Write-back round-trip (F5): GeoDeploy layer ⇄ GeoLibre edit ────────────────

def _pg_dsn() -> str:
    """The PostGIS DSN, from the SQLite-stored setup (same source the ingest/export tasks use)."""
    from ..tasks.vector_ingest import _get_setup
    s = get_settings()
    setup = _get_setup(f"{s.data_dir}/sqlite/geodeploy.db")
    dsn = (f"host={setup['postgis_host']} port={setup['postgis_port']} dbname={setup['postgis_db']} "
           f"user={setup['postgis_user']} password={setup['postgis_password']}")
    if s.postgis_sslmode:
        dsn += f" sslmode={s.postgis_sslmode}"
    return dsn


def _read_layer_geojson(schema: str, table: str, cap: int) -> str:
    """The whole PostGIS layer as a GeoJSON FeatureCollection string, reprojected to EPSG:4326 (RFC
    7946 / what GeoLibre expects for GeoJSON). Z ordinates are preserved by ST_AsGeoJSON. Runs sync
    (psycopg2) — call via run_in_threadpool."""
    import psycopg2

    from ..tasks.export import _geom_out, _table_srid
    conn = psycopg2.connect(_pg_dsn())
    try:
        cur = conn.cursor()
        srid = _table_srid(cur, schema, table)
        sql = (
            "SELECT jsonb_build_object('type','FeatureCollection','features',"
            "COALESCE(jsonb_agg(f.feat), '[]'::jsonb))::text FROM ("
            "  SELECT jsonb_build_object('type','Feature',"
            f"    'geometry', ST_AsGeoJSON({_geom_out(srid, 4326)})::jsonb,"
            "    'properties', to_jsonb(t) - 'geom' - 'id') AS feat"
            f'  FROM "{schema}"."{table}" t'
            f"  LIMIT {int(cap)}"
            ") f"
        )
        cur.execute(sql)
        row = cur.fetchone()
        return row[0] if row and row[0] else '{"type":"FeatureCollection","features":[]}'
    finally:
        conn.close()


async def _get_writable_layer(layer_id: int, db: AsyncSession) -> VectorLayer:
    """Load a PostGIS vector layer or 404 (GeoParquet/file-backed layers aren't edit-in-place)."""
    layer = await db.get(VectorLayer, layer_id)
    if layer is None or getattr(layer, "storage_backend", "postgis") != "postgis":
        raise HTTPException(status_code=404, detail="No editable PostGIS layer with that id.")
    return layer


@router.get("/geodeploy/layers")
async def list_editable_layers(user: User = Depends(require_scope("data:read")),
                               db: AsyncSession = Depends(get_db)):
    """PostGIS vector layers that can be round-tripped (loaded into GeoLibre, edited, written back)."""
    r = await db.execute(select(VectorLayer).where(
        VectorLayer.storage_backend == "postgis", VectorLayer.status == "ready"))
    return [{"id": l.id, "name": l.name, "geometry_type": l.geometry_type,
             "feature_count": l.feature_count, "crs": l.crs} for l in r.scalars().all()]


@router.get("/geodeploy/layers/{layer_id}/features.geojson")
async def read_layer_features(layer_id: int, user: User = Depends(require_scope("data:read")),
                              db: AsyncSession = Depends(get_db)):
    """The layer as editable GeoJSON (EPSG:4326) — load this into GeoLibre, clean it, write it back."""
    layer = await _get_writable_layer(layer_id, db)
    if layer.status != "ready":
        raise HTTPException(status_code=409, detail="Layer is not ready.")
    gj = await run_in_threadpool(_read_layer_geojson, layer.schema_name, layer.table_name,
                                 WRITEBACK_FEATURE_CAP)
    return Response(content=gj, media_type="application/geo+json")


@router.put("/geodeploy/layers/{layer_id}/features", status_code=202)
async def writeback_layer_features(layer_id: int, request: Request,
                                   user: User = Depends(require_scope("data:write")),
                                   db: AsyncSession = Depends(get_db)):
    """Replace a layer's features with edited GeoJSON (from GeoLibre). Re-ingests into the SAME table
    via the normal vector ingest (DROP+CREATE), so bbox/count/geometry refresh, Martin reloads, and any
    published portal using the layer reflects the edit. Only the owner (or an admin) may overwrite."""
    layer = await _get_writable_layer(layer_id, db)
    if layer.user_id != user.id and user.role not in ("admin", "owner"):
        raise HTTPException(status_code=403, detail="Only the layer's owner can overwrite it.")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Body is not valid JSON.")
    if not isinstance(body, dict) or body.get("type") != "FeatureCollection":
        raise HTTPException(status_code=400, detail="Expected a GeoJSON FeatureCollection.")
    features = body.get("features") or []
    if not isinstance(features, list) or not features:
        raise HTTPException(status_code=400, detail="FeatureCollection has no features.")
    if len(features) > WRITEBACK_FEATURE_CAP:
        raise HTTPException(status_code=413,
                            detail=f"Too many features (> {WRITEBACK_FEATURE_CAP}) for write-back.")

    settings = get_settings()
    os.makedirs(f"{settings.data_dir}/temp", exist_ok=True)
    tmp_path = f"{settings.data_dir}/temp/{uuid.uuid4()}.geojson"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(body, fh)

    layer.status = "processing"
    job_id = str(uuid.uuid4())
    db.add(UploadJob(id=job_id, layer_id=layer.id, layer_type="vector"))
    await db.commit()

    # Re-ingest into the SAME schema/table (the ingest DROPs + CREATEs → a full replace). GeoLibre
    # GeoJSON is 4326, so the refreshed layer is stored 4326 even if it was native before (noted).
    from ..tasks.vector_ingest import ingest_vector
    ingest_vector.delay(job_id, layer.id, tmp_path, slugify(layer.name, separator="_") or "layer",
                        layer.schema_name, layer.table_name)

    return {"job_id": job_id, "layer_id": layer.id, "status": "processing", "features": len(features)}
