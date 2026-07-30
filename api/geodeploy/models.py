import secrets
from datetime import datetime
from sqlalchemy import (
    Boolean, DateTime, Float, ForeignKey,
    Integer, String, Text, func
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .crypto import EncryptedText
from .database import Base


def new_uid() -> str:
    """A layer's STABLE PUBLIC identifier.

    Integer primary keys must never appear in shareable URLs. SQLite assigns them as rowid
    aliases WITHOUT the AUTOINCREMENT keyword, so deleting the highest-id row frees that id for
    the next insert: delete a shared layer, create another, and every saved link to
    `.../vector-3` — a STAC item, an OGC API - Features collection, a `/vsicurl/` COG pasted into
    someone's QGIS project — silently resolves to a DIFFERENT dataset. No error, wrong data.
    Postgres sequences would fix the reuse, but not the wider problem: integer keys are only
    meaningful within one database, so a restore or an instance-to-instance move renumbers
    everything and invalidates every published URL.

    12 hex chars from `secrets`: collision-free in practice, unguessable, and URL-safe.
    """
    return secrets.token_hex(6)


class SetupConfig(Base):
    __tablename__ = "setup_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    completed: Mapped[bool] = mapped_column(Boolean, default=False)

    postgis_type: Mapped[str | None] = mapped_column(String(16))   # local | external
    postgis_host: Mapped[str | None] = mapped_column(String(256))
    postgis_port: Mapped[int | None] = mapped_column(Integer, default=5432)
    postgis_db: Mapped[str | None] = mapped_column(String(128))
    postgis_user: Mapped[str | None] = mapped_column(String(128))
    # Fernet-encrypted at rest (crypto.EncryptedText). It was plain Text until 2026-07-30 despite
    # the comment claiming otherwise — which mattered once backups shipped: pg_dump carries
    # setup_config, so this and storage_secret_key were leaving the box in plaintext.
    # Raw readers (the Celery shim bypasses SQLAlchemy types) MUST call crypto.decrypt_secret.
    postgis_password: Mapped[str | None] = mapped_column(EncryptedText)

    storage_type: Mapped[str | None] = mapped_column(String(16))   # local | s3 | hetzner | r2 | backblaze
    storage_endpoint: Mapped[str | None] = mapped_column(String(512))
    storage_bucket: Mapped[str | None] = mapped_column(String(256))
    storage_access_key: Mapped[str | None] = mapped_column(String(256))
    storage_secret_key: Mapped[str | None] = mapped_column(EncryptedText)  # see postgis_password
    storage_region: Mapped[str | None] = mapped_column(String(64), default="us-east-1")

    # Outgoing email (C-08a): generic SMTP so ANY provider works (Resend/Brevo/institutional
    # relay — they all expose SMTP). Unconfigured (no host/from) = invite & reset links are
    # copy-delivered only. Admin-editable in Settings → Email; never required.
    smtp_host: Mapped[str | None] = mapped_column(String(256))
    smtp_port: Mapped[int | None] = mapped_column(Integer, default=587)
    smtp_security: Mapped[str | None] = mapped_column(String(16), default="starttls")  # tls | starttls | none
    smtp_username: Mapped[str | None] = mapped_column(String(256))
    smtp_password: Mapped[str | None] = mapped_column(EncryptedText)  # Fernet-encrypted at rest (crypto.py)
    email_from: Mapped[str | None] = mapped_column(String(256))

    # A-04 OIDC SSO (generic OpenID Connect provider; admin-configured). client_secret is
    # Fernet-encrypted at rest + write-only via the API (never returned).
    oidc_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    oidc_issuer: Mapped[str | None] = mapped_column(String(512))          # discovery base URL
    oidc_client_id: Mapped[str | None] = mapped_column(String(512))
    oidc_client_secret: Mapped[str | None] = mapped_column(EncryptedText)
    oidc_label: Mapped[str | None] = mapped_column(String(128))           # sign-in button text
    oidc_auto_provision: Mapped[bool] = mapped_column(Boolean, default=False)
    oidc_allowed_domains: Mapped[str | None] = mapped_column(String(512))  # comma-separated
    oidc_default_role: Mapped[str] = mapped_column(String(16), default="viewer")

    # Backups (2026-07-30). A SEPARATE S3 destination on purpose: a backup that lives in the same
    # bucket (or on the same box) as the data is not a backup. Credentials are Fernet-encrypted at
    # rest and the secret is write-only over the API, like the SMTP/OIDC secrets.
    backup_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    backup_endpoint: Mapped[str | None] = mapped_column(String(512))
    backup_bucket: Mapped[str | None] = mapped_column(String(256))
    backup_prefix: Mapped[str | None] = mapped_column(String(256), default="geodeploy-backups")
    backup_access_key: Mapped[str | None] = mapped_column(String(256))
    backup_secret_key: Mapped[str | None] = mapped_column(EncryptedText)
    backup_region: Mapped[str | None] = mapped_column(String(64), default="us-east-1")
    # Schedule: 'off' | 'daily' | 'weekly', fired at backup_hour UTC (weekly → Mondays). Checked by a
    # periodic task rather than reconfiguring celery beat when the setting changes.
    backup_schedule: Mapped[str] = mapped_column(String(16), default="off")
    backup_hour: Mapped[int] = mapped_column(Integer, default=3)
    backup_keep: Mapped[int] = mapped_column(Integer, default=7)      # retention, newest N kept
    # What to include. PostGIS + object storage are the irreplaceable ones; the state DB is small
    # and essential; portal bundles are regenerable by re-publishing but portal_assets are NOT.
    backup_include_postgis: Mapped[bool] = mapped_column(Boolean, default=True)
    backup_include_objects: Mapped[bool] = mapped_column(Boolean, default=True)
    backup_include_state: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class BackupRun(Base):
    """One backup attempt — the history behind Settings → Backups.

    A backup you cannot see the result of is not a backup, so every run is recorded whether it
    succeeded or not, with the failure text. `manifest` is the JSON inventory written alongside the
    artifacts (what was included, sizes, the PostGIS/state/object counts) — it is what a restore
    reads to know what it is looking at.
    """
    __tablename__ = "backup_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(256), nullable=False, index=True)   # destination prefix
    status: Mapped[str] = mapped_column(String(16), default="running")  # running|success|error
    trigger: Mapped[str] = mapped_column(String(16), default="manual")  # manual|scheduled
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(Text)
    manifest: Mapped[str | None] = mapped_column(Text)     # JSON inventory
    current_step: Mapped[str | None] = mapped_column(String(128))
    progress: Mapped[int] = mapped_column(Integer, default=0)


class DeploymentRun(Base):
    """One update attempt — the Deployments history in Settings -> Infrastructure.

    The updater already writes live progress to `data/temp/update-status.json`, but that file is
    overwritten by the next run, so history was lost. This table keeps it. The API container is
    RECREATED mid-update, so a row can outlive the process that made it: `finished_at` is set by
    whoever next reads the status file and finds a terminal phase (see admin.update_status).
    """
    __tablename__ = "deployment_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    status: Mapped[str] = mapped_column(String(16), default="running")   # running|success|error|rolledback
    trigger: Mapped[str] = mapped_column(String(16), default="manual")   # manual|scheduled
    actor_name: Mapped[str | None] = mapped_column(String(256))
    from_sha: Mapped[str | None] = mapped_column(String(64))
    to_sha: Mapped[str | None] = mapped_column(String(64))
    message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)


class RestoreRun(Base):
    """One restore attempt.

    Separate from BackupRun on purpose: a restore is a different, destructive act and its history
    should not be mixed in with routine backups. `confirmed_by` records WHO typed the confirmation,
    because this is the one operation that can destroy an instance.
    """
    __tablename__ = "restore_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), default="running")  # running|success|error
    confirmed_by: Mapped[str | None] = mapped_column(String(256))
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    error_message: Mapped[str | None] = mapped_column(Text)
    current_step: Mapped[str | None] = mapped_column(String(128))
    progress: Mapped[int] = mapped_column(Integer, default=0)
    detail: Mapped[str | None] = mapped_column(Text)      # JSON: what each part restored


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
    # A-04 session revocation: embedded in the browser JWT as `tv`; get_current_user rejects a token
    # whose tv != this. Bumped on password change/reset + "log out everywhere" to kill outstanding
    # JWTs. (API tokens are revoked individually — this is only the stateless browser JWT.)
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # A-04 OIDC: provider subject, pinned on first SSO link so future logins match by sub not just email.
    oidc_sub: Mapped[str | None] = mapped_column(String(255), index=True)
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


class ApiToken(Base):
    """Scoped personal access token (A-03) — headless API access for scripts and the
    GeoLibre/QGIS plugins.

    Only the sha256 hash of the token is stored; the raw `gdp_…` string is returned once at
    creation and cannot be recovered. The token authenticates as `user_id` (via the Bearer path
    in deps.get_current_user), capped by `scopes` (space-separated; see deps.SCOPES) and never
    above the owner's LIVE role — the role is re-read per request, so demotion/deletion applies
    immediately. `prefix` (first 12 chars, e.g. `gdp_a1b2c3d4`) is shown in the UI for
    identification. This is a NEW table → created by Base.metadata.create_all, no migration entry.
    """
    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    prefix: Mapped[str] = mapped_column(String(16), nullable=False)  # gdp_ + 8 chars, display only
    scopes: Mapped[str] = mapped_column(Text, nullable=False, default="")  # space-separated
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)  # mandatory, ≤365d
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class VectorLayer(Base):
    __tablename__ = "vector_layers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Stable public identity — see `new_uid()`. The integer `id` stays the internal key (foreign
    # keys, portal layer_configs); `uid` is what STAC / OGC API - Features / share links expose.
    uid: Mapped[str | None] = mapped_column(String(32), unique=True, index=True, default=new_uid)
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
    # A-02 per-resource sharing — the workspace visibility axis (private ⊂ organization ⊂ public):
    #   private      = only the creator + admins/owner see it (WRITES too); hidden from peers.
    #   organization = every workspace member sees it (the default; pre-A-02 behavior).
    #   public       = organization + exposed to the internet (STAC catalog + raw asset download).
    # THE seam is routers/common.py::visible_to. Display endpoints published portals need (tiles,
    # viewport features) stay public-by-id regardless — visibility governs the workspace + DISCOVERY.
    visibility: Mapped[str] = mapped_column(String(16), nullable=False, default="organization")
    # DEPRECATED/derived: kept write-only-synced (is_public = visibility == "public") so STAC /
    # _publicly_readable / portal_generator keep reading it unchanged. Never set it directly.
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
    uid: Mapped[str | None] = mapped_column(String(32), unique=True, index=True, default=new_uid)
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
    visibility: Mapped[str] = mapped_column(String(16), nullable=False, default="organization")
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
    # A-02 visibility (see VectorLayer). External sources reference third-party services and are not
    # in STAC / raw-asset endpoints, so only private | organization are meaningful (no public tier).
    visibility: Mapped[str] = mapped_column(String(16), nullable=False, default="organization")
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


class AuditLog(Base):
    """Append-only activity log (A-05) — who did what, when, for operator trust + support.

    New table → created by `Base.metadata.create_all`, no migration entry. `actor_id` is NOT a hard FK
    (the log must survive a user deletion), and `actor_name` is denormalized so an entry stays readable
    afterwards. `detail` is small JSON context. Written best-effort via `routers/common.record_audit`.
    """
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_id: Mapped[int | None] = mapped_column(Integer, index=True)   # not a FK — survives user delete
    actor_name: Mapped[str | None] = mapped_column(String(256))
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)   # e.g. portal.publish
    resource_type: Mapped[str | None] = mapped_column(String(32), index=True)     # vector|raster|portal|user|…
    resource_id: Mapped[str | None] = mapped_column(String(64), index=True)
    detail: Mapped[str | None] = mapped_column(Text)   # JSON context (small)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


class Portal(Base):
    __tablename__ = "portals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    slug: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    template_id: Mapped[str] = mapped_column(String(128), default="minimal")
    basemap: Mapped[str | None] = mapped_column(String(64))  # basemap catalog id (BASEMAP_CATALOG)
    layer_configs: Mapped[str] = mapped_column(Text, default="[]")  # JSON — flat per-layer STYLE
    # V-13 catalog: optional nested folder TREE over the layers (JSON list of layer/group nodes).
    # NULL = flat list (renders like before). Structure lives here; per-layer style stays in layer_configs.
    layer_groups: Mapped[str | None] = mapped_column(Text)
    initial_view: Mapped[str | None] = mapped_column(Text)  # JSON {center:[lng,lat], zoom, bearing, pitch, projection} — published portal's start view
    # V-11 Template Experiences: optional layout manifest {archetype, regions, panels}. NULL = webmap
    # (identical to the pre-V-11 fixed shell). Resolved at publish via portal_generator.resolve_layout.
    layout_config: Mapped[str | None] = mapped_column(Text)
    # V-11 story-map archetype: {sections:[{id, html, view, layers}]}. NULL = no story. Only consumed
    # when the resolved layout archetype is 'storymap'.
    story: Mapped[str | None] = mapped_column(Text)
    # V-11 R3 colour theme: {mode:auto|light|dark, accent:#hex, font:sans|serif}. NULL = the template's
    # own theme.css unchanged. Baked as CSS-variable overrides AFTER theme.css (so it wins).
    theme: Mapped[str | None] = mapped_column(Text)
    # Card thumbnail: a snapshot of the PUBLISHED map, captured in the browser at publish time (the
    # editor already renders the real portal in an iframe) and written to a FIXED filename, so
    # re-publishing overwrites instead of orphaning a file. Carries a ?v= cache-buster. NULL = the
    # card falls back to its gradient — a portal published before this existed, or one whose capture
    # failed, must still render.
    thumbnail_url: Mapped[str | None] = mapped_column(String(512))
    # DORMANT: portals dropped the separate workspace-visibility control (it duplicated access_type
    # confusingly — a portal's audience is its published access_type, below). Kept at 'organization'
    # for every portal (reset by a migration); never written by the API. Data layers/sources still use
    # visibility. Column retained to avoid a destructive drop on SQLite.
    visibility: Mapped[str] = mapped_column(String(16), nullable=False, default="organization")
    # Who can VIEW the published portal: public (anyone) | password | organization (any signed-in
    # workspace member) | owner (only the creator + admins). Client-side gate in templates/shared/
    # portal.js. Legacy 'private' == organization (members-only), migrated away in main.py.
    access_type: Mapped[str] = mapped_column(String(16), default="public")
    access_password_hash: Mapped[str | None] = mapped_column(String(256))    # bcrypt — for future server-side auth
    access_password_sha256: Mapped[str | None] = mapped_column(String(64))   # SHA-256 hex — embedded in published portal
    published: Mapped[bool] = mapped_column(Boolean, default=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship(back_populates="portals")
