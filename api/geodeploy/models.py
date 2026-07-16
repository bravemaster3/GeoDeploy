from datetime import datetime
from sqlalchemy import (
    Boolean, DateTime, Float, ForeignKey,
    Integer, String, Text, func
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .database import Base


class SetupConfig(Base):
    __tablename__ = "setup_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    completed: Mapped[bool] = mapped_column(Boolean, default=False)

    postgis_type: Mapped[str | None] = mapped_column(String(16))   # local | external
    postgis_host: Mapped[str | None] = mapped_column(String(256))
    postgis_port: Mapped[int | None] = mapped_column(Integer, default=5432)
    postgis_db: Mapped[str | None] = mapped_column(String(128))
    postgis_user: Mapped[str | None] = mapped_column(String(128))
    postgis_password: Mapped[str | None] = mapped_column(Text)  # encrypted at rest

    storage_type: Mapped[str | None] = mapped_column(String(16))   # local | s3 | hetzner | r2 | backblaze
    storage_endpoint: Mapped[str | None] = mapped_column(String(512))
    storage_bucket: Mapped[str | None] = mapped_column(String(256))
    storage_access_key: Mapped[str | None] = mapped_column(String(256))
    storage_secret_key: Mapped[str | None] = mapped_column(Text)   # encrypted at rest
    storage_region: Mapped[str | None] = mapped_column(String(64), default="us-east-1")

    # Outgoing email (C-08a): generic SMTP so ANY provider works (Resend/Brevo/institutional
    # relay — they all expose SMTP). Unconfigured (no host/from) = invite & reset links are
    # copy-delivered only. Admin-editable in Settings → Email; never required.
    smtp_host: Mapped[str | None] = mapped_column(String(256))
    smtp_port: Mapped[int | None] = mapped_column(Integer, default=587)
    smtp_security: Mapped[str | None] = mapped_column(String(16), default="starttls")  # tls | starttls | none
    smtp_username: Mapped[str | None] = mapped_column(String(256))
    smtp_password: Mapped[str | None] = mapped_column(Text)
    email_from: Mapped[str | None] = mapped_column(String(256))

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(256), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(256), nullable=False)
    # DEPRECATED: superseded by `role`. Never read; kept in sync on write
    # (is_admin = role in ("admin", "owner")) so a rollback stays safe.
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    # owner | admin | editor | viewer — exactly one owner per install
    # (enforced by the uq_users_single_owner partial index + the transfer endpoint).
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="viewer")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    vector_layers: Mapped[list["VectorLayer"]] = relationship(back_populates="user")
    raster_layers: Mapped[list["RasterLayer"]] = relationship(back_populates="user")
    portals: Mapped[list["Portal"]] = relationship(back_populates="user")


class Invitation(Base):
    """Single-use signup invitation or password-reset link.

    Only the sha256 hash of the token is stored — the raw token is returned once at
    creation/regeneration and cannot be recovered (regenerate mints a fresh one).
    """
    __tablename__ = "invitations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    purpose: Mapped[str] = mapped_column(String(16), nullable=False, default="invite")  # invite | password_reset
    email: Mapped[str] = mapped_column(String(256), nullable=False)
    role: Mapped[str | None] = mapped_column(String(16))          # invite only: role granted on accept
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))  # password_reset only: target user
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    invited_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class VectorLayer(Base):
    __tablename__ = "vector_layers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    table_name: Mapped[str] = mapped_column(String(256), nullable=False)
    schema_name: Mapped[str] = mapped_column(String(128), nullable=False)
    crs: Mapped[str | None] = mapped_column(String(64))
    feature_count: Mapped[int | None] = mapped_column(Integer)
    bbox: Mapped[str | None] = mapped_column(Text)      # JSON [minx, miny, maxx, maxy]
    columns: Mapped[str | None] = mapped_column(Text)   # JSON [{name, type}]
    geometry_type: Mapped[str | None] = mapped_column(String(64))
    # Geometry / id column names — GeoDeploy-ingested tables use geom/id, but layers IMPORTED
    # from an existing PostGIS may use any names (NULL → fall back to geom/id).
    geometry_column: Mapped[str | None] = mapped_column(String(128))
    id_column: Mapped[str | None] = mapped_column(String(128))
    file_size: Mapped[int | None] = mapped_column(Integer)
    storage_backend: Mapped[str] = mapped_column(String(16), default="postgis")  # postgis | geoparquet
    s3_key: Mapped[str | None] = mapped_column(String(512))
    # For a GeoParquet layer ATTACHED via import-existing: the ORIGINAL object key it was imported
    # from. The spatial prep repoints s3_key at a prepped copy under vectors/, so this is what lets
    # discover/storage keep flagging the source file as already imported (and it is never deleted —
    # attach, don't copy/destroy).
    source_s3_key: Mapped[str | None] = mapped_column(String(512))
    # For a large-upload GeoParquet layer whose raw file is converted in the background: the CSV/
    # conversion options the user chose (X/Y or WKT column, srid, delimiter), as JSON. Persisted so a
    # "restart processing" can re-run the convert stage without the user re-picking columns / re-uploading.
    convert_opts: Mapped[str | None] = mapped_column(Text)
    # GeoParquet display path: a PMTiles archive tiled from the file (key on storage). tile_status:
    # NULL/none (n/a or not started) | tiling | ready | error. Until ready, the layer isn't displayable.
    pmtiles_key: Mapped[str | None] = mapped_column(String(512))
    tile_status: Mapped[str | None] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="processing")  # processing | ready | error
    error_message: Mapped[str | None] = mapped_column(Text)
    default_style: Mapped[str | None] = mapped_column(Text)  # JSON {opacity, style, popup_fields}
    # Data sharing (STAC catalog + raw-asset access): the admin opts a layer INTO the public
    # catalog. Display endpoints that published portals need (tiles, viewport features) stay
    # public-by-id regardless — this flag governs DISCOVERY + raw data assets.
    is_public: Mapped[bool] = mapped_column(Boolean, default=False)
    # Catalog metadata (STAC common metadata / the GeoNode-parity fields — see notes §0h-addendum).
    abstract: Mapped[str | None] = mapped_column(Text)
    keywords: Mapped[str | None] = mapped_column(String(512))   # comma-separated
    license: Mapped[str | None] = mapped_column(String(128))
    attribution: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship(back_populates="vector_layers")


class RasterLayer(Base):
    __tablename__ = "raster_layers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    s3_key: Mapped[str] = mapped_column(String(512), nullable=False)
    crs: Mapped[str | None] = mapped_column(String(64))
    bbox: Mapped[str | None] = mapped_column(Text)
    band_count: Mapped[int | None] = mapped_column(Integer)
    nodata_value: Mapped[float | None] = mapped_column(Float)
    file_size: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="processing")
    error_message: Mapped[str | None] = mapped_column(Text)
    default_style: Mapped[str | None] = mapped_column(Text)  # JSON {opacity}
    # Data sharing + catalog metadata — see the VectorLayer twin fields.
    is_public: Mapped[bool] = mapped_column(Boolean, default=False)
    abstract: Mapped[str | None] = mapped_column(Text)
    keywords: Mapped[str | None] = mapped_column(String(512))
    license: Mapped[str | None] = mapped_column(String(128))
    attribution: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship(back_populates="raster_layers")


class ExternalSource(Base):
    """A third-party map service (WMS/WMTS/XYZ raster or WFS vector) displayed in a
    portal WITHOUT ingesting — tiles/features are fetched from the provider at view time."""
    __tablename__ = "external_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    source_type: Mapped[str] = mapped_column(String(16), nullable=False)  # xyz | wms | wfs
    kind: Mapped[str] = mapped_column(String(8), nullable=False)          # raster | vector
    url: Mapped[str] = mapped_column(Text, nullable=False)               # XYZ template or WMS/WFS base URL
    layer_name: Mapped[str | None] = mapped_column(Text)                 # WMS layers= / WFS typeName
    version: Mapped[str | None] = mapped_column(String(16))              # WMS/WFS version
    image_format: Mapped[str | None] = mapped_column(String(32))         # WMS format (default image/png)
    attribution: Mapped[str | None] = mapped_column(Text)               # required credit string
    geometry_type: Mapped[str | None] = mapped_column(String(32))        # WFS: point|line|polygon (probed)
    bbox: Mapped[str | None] = mapped_column(Text)                       # JSON [minx,miny,maxx,maxy] EPSG:4326
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship()


class UploadJob(Base):
    __tablename__ = "upload_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # UUID
    layer_id: Mapped[int] = mapped_column(Integer, nullable=False)
    layer_type: Mapped[str] = mapped_column(String(8), nullable=False)  # vector | raster
    status: Mapped[str] = mapped_column(String(16), default="queued")   # queued | processing | ready | error
    progress: Mapped[int] = mapped_column(Integer, default=0)
    current_step: Mapped[str | None] = mapped_column(String(128))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Portal(Base):
    __tablename__ = "portals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    slug: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    template_id: Mapped[str] = mapped_column(String(128), default="minimal")
    basemap: Mapped[str | None] = mapped_column(String(64))  # basemap catalog id (BASEMAP_CATALOG)
    layer_configs: Mapped[str] = mapped_column(Text, default="[]")  # JSON
    initial_view: Mapped[str | None] = mapped_column(Text)  # JSON {center:[lng,lat], zoom, bearing, pitch} — published portal's start view
    access_type: Mapped[str] = mapped_column(String(16), default="public")  # public | password | private
    access_password_hash: Mapped[str | None] = mapped_column(String(256))    # bcrypt — for future server-side auth
    access_password_sha256: Mapped[str | None] = mapped_column(String(64))   # SHA-256 hex — embedded in published portal
    published: Mapped[bool] = mapped_column(Boolean, default=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship(back_populates="portals")
