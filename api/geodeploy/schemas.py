from datetime import datetime
from typing import Any
from pydantic import BaseModel, EmailStr, Field


# ── Setup ────────────────────────────────────────────────────────────────────

class SetupStatus(BaseModel):
    completed: bool
    postgis_configured: bool
    storage_configured: bool
    admin_created: bool


class ConfigureDBRequest(BaseModel):
    type: str = Field(pattern="^(local|external)$")
    host: str | None = None
    port: int = 5432
    db: str = "geodeploy"
    user: str = "geodeploy"
    password: str | None = None


class ConfigureStorageRequest(BaseModel):
    type: str = Field(pattern="^(local|s3|hetzner|r2|backblaze)$")
    endpoint: str | None = None
    bucket: str = "geodeploy"
    access_key: str | None = None
    secret_key: str | None = None
    region: str = "us-east-1"


class CreateAdminRequest(BaseModel):
    name: str
    email: EmailStr
    password: str = Field(min_length=8)


# ── Auth ─────────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    id: int
    email: str
    name: str
    is_admin: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Vector Layers ─────────────────────────────────────────────────────────────

class DefaultStyle(BaseModel):
    opacity: float = 1.0
    style: dict[str, Any] = Field(default_factory=dict)
    popup_fields: list[str] = Field(default_factory=list)


class VectorLayerOut(BaseModel):
    id: int
    name: str
    table_name: str
    schema_name: str
    crs: str | None
    feature_count: int | None
    bbox: list[float] | None
    columns: list[dict[str, str]] | None
    geometry_type: str | None
    file_size: int | None
    storage_backend: str
    s3_key: str | None = None
    pmtiles_key: str | None = None
    tile_status: str | None = None
    status: str
    error_message: str | None
    default_style: DefaultStyle | None
    created_at: datetime

    model_config = {"from_attributes": True}

    @classmethod
    def from_orm_json(cls, obj: Any) -> "VectorLayerOut":
        import json
        data = {
            c.name: getattr(obj, c.name)
            for c in obj.__table__.columns
        }
        data["bbox"] = json.loads(obj.bbox) if obj.bbox else None
        data["columns"] = json.loads(obj.columns) if obj.columns else None
        data["default_style"] = json.loads(obj.default_style) if obj.default_style else None
        return cls(**data)


# ── Raster Layers ─────────────────────────────────────────────────────────────

class RasterDefaultStyle(BaseModel):
    opacity: float = 1.0
    colormap: str | None = None
    rescale: str | None = None       # "min,max" stretch
    algorithm: str | None = None     # e.g. "hillshade" (single-band)
    zfactor: float | None = None     # hillshade vertical exaggeration
    bidx: list[int] | None = None    # band selection: [n] single-band, [r,g,b] RGB composite


class RasterLayerOut(BaseModel):
    id: int
    name: str
    s3_key: str
    crs: str | None
    bbox: list[float] | None
    band_count: int | None
    nodata_value: float | None
    file_size: int | None
    status: str
    error_message: str | None
    default_style: RasterDefaultStyle | None
    created_at: datetime
    tile_url: str | None = None  # populated by router for ready layers

    model_config = {"from_attributes": True}

    @classmethod
    def from_orm_json(cls, obj: Any) -> "RasterLayerOut":
        import json
        data = {c.name: getattr(obj, c.name) for c in obj.__table__.columns}
        data["bbox"] = json.loads(obj.bbox) if obj.bbox else None
        data["default_style"] = json.loads(obj.default_style) if obj.default_style else None
        data["tile_url"] = None
        return cls(**data)


# ── External sources (WMS / XYZ / WFS) ────────────────────────────────────────

class ExternalSourceCreate(BaseModel):
    name: str
    source_type: str = Field(pattern="^(xyz|wms|wfs)$")
    url: str
    layer_name: str | None = None     # WMS layers= / WFS typeName (required for wms/wfs)
    version: str | None = None        # WMS (default 1.3.0) / WFS (default 2.0.0)
    image_format: str | None = None   # WMS image format (default image/png)
    attribution: str | None = None


class ExternalSourceOut(BaseModel):
    id: int
    name: str
    source_type: str
    kind: str                         # raster | vector
    url: str
    layer_name: str | None
    version: str | None
    image_format: str | None
    attribution: str | None
    geometry_type: str | None
    bbox: list[float] | None
    created_at: datetime
    tile_url: str | None = None       # raster sources: MapLibre tiles[] template
    data_url: str | None = None       # vector sources: GeoJSON proxy path

    model_config = {"from_attributes": True}

    @classmethod
    def from_orm_json(cls, obj: Any) -> "ExternalSourceOut":
        import json
        data = {c.name: getattr(obj, c.name) for c in obj.__table__.columns}
        data["bbox"] = json.loads(obj.bbox) if obj.bbox else None
        data["tile_url"] = None
        data["data_url"] = None
        return cls(**data)


# ── Upload Jobs ───────────────────────────────────────────────────────────────

class JobStatus(BaseModel):
    id: str
    layer_id: int
    layer_type: str
    status: str
    progress: int
    current_step: str | None
    error_message: str | None

    model_config = {"from_attributes": True}


# ── Portals ───────────────────────────────────────────────────────────────────

class LayerConfig(BaseModel):
    layer_id: int
    layer_type: str   # vector | raster
    visible: bool = True
    opacity: float = 1.0
    style: dict[str, Any] = Field(default_factory=dict)  # for rasters: may contain colormap key
    popup_fields: list[str] = Field(default_factory=list)


class PortalCreate(BaseModel):
    title: str
    description: str | None = None
    template_id: str = "minimal"
    layer_configs: list[LayerConfig] = Field(default_factory=list)
    access_type: str = Field(default="public", pattern="^(public|password|private)$")
    access_password: str | None = None


class PortalUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    template_id: str | None = None
    layer_configs: list[LayerConfig] | None = None
    initial_view: dict[str, Any] | None = None  # {center:[lng,lat], zoom, bearing, pitch}
    access_type: str | None = None
    access_password: str | None = None


class PortalOut(BaseModel):
    id: int
    title: str
    slug: str
    description: str | None
    template_id: str
    layer_configs: list[LayerConfig]
    initial_view: dict[str, Any] | None = None
    access_type: str
    published: bool
    published_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}

    @classmethod
    def from_orm_json(cls, obj: Any) -> "PortalOut":
        import json
        data = {c.name: getattr(obj, c.name) for c in obj.__table__.columns}
        data["layer_configs"] = json.loads(obj.layer_configs) if obj.layer_configs else []
        data["initial_view"] = json.loads(obj.initial_view) if obj.initial_view else None
        data.pop("access_password_hash", None)
        data.pop("user_id", None)
        return cls(**data)


# ── Templates ─────────────────────────────────────────────────────────────────

class TemplateOut(BaseModel):
    id: str
    name: str
    author: str
    description: str
    tags: list[str]
    language: str
    basemap: str
    preview_url: str
    version: str
    license: str
    is_official: bool


# ── Admin ─────────────────────────────────────────────────────────────────────

class ServiceHealth(BaseModel):
    name: str
    status: str       # healthy | unhealthy | running | stopped | exited | unknown
    message: str | None = None
    controllable: bool = False   # whether start/stop/restart is offered for this service


class StorageStats(BaseModel):
    used_bytes: int
    total_bytes: int | None
    vector_layers: int
    raster_layers: int
    portals: int
