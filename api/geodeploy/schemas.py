from datetime import datetime
from typing import Any
from pydantic import BaseModel, EmailStr, Field, field_validator


# ── Setup ────────────────────────────────────────────────────────────────────

class SetupStatus(BaseModel):
    completed: bool
    postgis_configured: bool
    storage_configured: bool
    admin_created: bool
    # Outgoing email configured (C-08a) — the login page uses this to decide whether to
    # offer "Forgot password?" (without email, self-service reset can't deliver anything).
    email_enabled: bool = False


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
    is_admin: bool  # deprecated — read `role` instead
    role: str
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Users & invitations (RBAC, A-01) ─────────────────────────────────────────

class UserAdminOut(UserOut):
    """User row for the admin Users screen — adds creation counts so an admin can
    review what a member owns before changing their role / deleting them."""
    vector_count: int = 0
    raster_count: int = 0
    portal_count: int = 0
    source_count: int = 0


class InviteCreate(BaseModel):
    email: EmailStr
    role: str = Field(pattern="^(viewer|editor|admin)$")  # owner only via ownership transfer


class InvitationOut(BaseModel):
    id: int
    purpose: str
    email: str
    role: str | None
    expires_at: datetime
    created_at: datetime
    # The RAW token — present ONLY in the response that creates/regenerates it (never stored,
    # never listed). The UI turns it into the copyable accept/reset link.
    token: str | None = None
    # True when the link was ALSO delivered by email (SMTP configured + relay accepted it).
    email_sent: bool = False

    model_config = {"from_attributes": True}


# ── API tokens (A-03) ────────────────────────────────────────────────────────

class ApiTokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    scopes: list[str] = Field(min_length=1)   # validated against deps.SCOPES + role in the router
    expires_in_days: int = 90                  # clamped to {30, 90, 365} in the router


class ApiTokenOut(BaseModel):
    id: int
    name: str
    prefix: str                # gdp_ + 8 chars — identifies the token; the secret is never returned
    scopes: list[str]
    expires_at: datetime
    last_used_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("scopes", mode="before")
    @classmethod
    def _split_scopes(cls, v):
        # Stored space-separated on the model; expose as a list.
        return v.split() if isinstance(v, str) else v


class ApiTokenCreated(ApiTokenOut):
    """Returned ONCE at creation — carries the raw `gdp_…` secret, which is never stored or listed."""
    token: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class EmailSettings(BaseModel):
    """Admin Settings → Email (partial update; smtp_password only written when provided)."""
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_security: str | None = Field(default=None, pattern="^(tls|starttls|none)$")
    smtp_username: str | None = None
    smtp_password: str | None = None
    email_from: str | None = None


class EmailSettingsOut(BaseModel):
    smtp_host: str | None
    smtp_port: int | None
    smtp_security: str | None
    smtp_username: str | None
    email_from: str | None
    has_password: bool = False  # the password itself is never returned
    configured: bool = False


class InvitePublicOut(BaseModel):
    """What the public accept/reset page may learn from a valid token."""
    email: str
    role: str | None
    purpose: str


class AcceptInviteRequest(BaseModel):
    name: str
    password: str = Field(min_length=8)


class RoleUpdate(BaseModel):
    role: str = Field(pattern="^(viewer|editor|admin)$")  # owner only via ownership transfer


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


class PasswordResetRequest(BaseModel):
    password: str = Field(min_length=8)


# ── Vector Layers ─────────────────────────────────────────────────────────────

class DefaultStyle(BaseModel):
    opacity: float = 1.0
    style: dict[str, Any] = Field(default_factory=dict)
    popup_fields: list[str] = Field(default_factory=list)


class SharingUpdate(BaseModel):
    """Data-sharing settings (A-02 workspace visibility + STAC catalog metadata). Partial update:
    only the fields present in the request body are applied.

    `visibility` is the axis (private | organization | public); `public` opts the layer into the
    STAC catalog + raw-asset access. `is_public` is accepted for backward compatibility and mapped
    to visibility (True → public, False → organization) when `visibility` is not given."""
    visibility: str | None = Field(default=None, pattern="^(private|organization|public)$")
    is_public: bool | None = None
    abstract: str | None = None
    keywords: str | None = None    # comma-separated
    license: str | None = None
    attribution: str | None = None


class VisibilityUpdate(BaseModel):
    """Workspace visibility for resources with NO public/internet tier (external sources, portals):
    private | organization only. (Layers use SharingUpdate, which adds the `public` STAC tier.)"""
    visibility: str = Field(pattern="^(private|organization)$")


class VectorLayerOut(BaseModel):
    id: int
    user_id: int | None = None          # creator ("created by" provenance — shared workspace)
    created_by: str | None = None       # creator display name, populated by the list endpoints
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
    # Live ingest progress for a queued/processing layer (from its latest UploadJob; None when ready)
    # — populated by the list endpoint so My Data shows "Processing NN%" for CLI uploads / after reload.
    progress: int | None = None
    current_step: str | None = None
    default_style: DefaultStyle | None
    visibility: str = "organization"
    is_public: bool = False
    abstract: str | None = None
    keywords: str | None = None
    license: str | None = None
    attribution: str | None = None
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
    user_id: int | None = None
    created_by: str | None = None
    name: str
    s3_key: str
    crs: str | None
    bbox: list[float] | None
    band_count: int | None
    nodata_value: float | None
    file_size: int | None
    status: str
    error_message: str | None
    progress: int | None = None       # live ingest progress (see VectorLayerOut)
    current_step: str | None = None
    default_style: RasterDefaultStyle | None
    visibility: str = "organization"
    is_public: bool = False
    abstract: str | None = None
    keywords: str | None = None
    license: str | None = None
    attribution: str | None = None
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
    user_id: int | None = None
    created_by: str | None = None
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
    visibility: str = "organization"
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


# Published-portal access tiers (who can VIEW the live portal): public (anyone) | password |
# organization (any signed-in workspace member) | owner (only the creator + admins). The legacy value
# 'private' == organization (members-only); it is migrated to 'organization' and never written again.
_ACCESS_TYPE = "^(public|password|organization|owner)$"


class PortalCreate(BaseModel):
    title: str
    description: str | None = None
    template_id: str = "minimal"
    layer_configs: list[LayerConfig] = Field(default_factory=list)
    access_type: str = Field(default="public", pattern=_ACCESS_TYPE)
    access_password: str | None = None


class PortalUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    template_id: str | None = None
    layer_configs: list[LayerConfig] | None = None
    initial_view: dict[str, Any] | None = None  # {center:[lng,lat], zoom, bearing, pitch}
    access_type: str | None = Field(default=None, pattern=_ACCESS_TYPE)
    access_password: str | None = None
    basemap: str | None = None  # basemap catalog id (see BASEMAP_CATALOG); default = first entry


class PortalOut(BaseModel):
    id: int
    user_id: int | None = None
    created_by: str | None = None
    title: str
    slug: str
    description: str | None
    template_id: str
    layer_configs: list[LayerConfig]
    initial_view: dict[str, Any] | None = None
    access_type: str
    basemap: str | None = None
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
    used_bytes: int                     # total across all measurable stores below
    total_bytes: int | None             # capacity — unknown for external PG/S3, kept for the bar
    vector_layers: int
    raster_layers: int
    portals: int
    # Per-store breakdown (None = that store could not be measured, e.g. DB unreachable —
    # distinct from 0, which means "measured, empty"). used_bytes sums the measurable ones.
    postgis_bytes: int | None = None      # pg_total_relation_size over catalog postgis tables
    raster_bytes: int | None = None       # COG objects on S3 (per-layer)
    geoparquet_bytes: int | None = None   # .parquet files/prefixes + .pmtiles on S3 (per-layer)
    portal_bundle_bytes: int | None = None  # published static bundles under data/portals/
