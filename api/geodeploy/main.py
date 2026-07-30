import os
import re
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import Response

from .config import get_settings
from .database import engine, Base
from .routers import (setup, auth, auth_oidc, portals, stac, templates, admin, basemaps, users,
                      tokens, audit, interop, ogcapi, backups)
from .routers.data import vector, raster, sources, discover


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    for subdir in ("sqlite", "portals", "portal_assets", "temp", "martin"):
        os.makedirs(f"{settings.data_dir}/{subdir}", exist_ok=True)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_apply_schema_migrations)

    # Write a minimal Martin config on first start so Martin can boot without layers
    _ensure_martin_config(settings)

    yield


def _apply_schema_migrations(conn) -> None:
    """Add columns that may be missing on databases created before the current schema."""
    from sqlalchemy import text
    pending = [
        "ALTER TABLE portals ADD COLUMN access_password_sha256 VARCHAR(64)",
        "ALTER TABLE portals ADD COLUMN initial_view TEXT",
        "ALTER TABLE portals ADD COLUMN basemap VARCHAR(64)",
        "ALTER TABLE vector_layers ADD COLUMN default_style TEXT",
        "ALTER TABLE vector_layers ADD COLUMN geometry_column VARCHAR(128)",
        "ALTER TABLE vector_layers ADD COLUMN id_column VARCHAR(128)",
        "ALTER TABLE vector_layers ADD COLUMN storage_backend VARCHAR(16) DEFAULT 'postgis'",
        "ALTER TABLE vector_layers ADD COLUMN s3_key VARCHAR(512)",
        "ALTER TABLE vector_layers ADD COLUMN pmtiles_key VARCHAR(512)",
        "ALTER TABLE vector_layers ADD COLUMN tile_status VARCHAR(16)",
        # Import-existing GeoParquet: the ORIGINAL attached key (prep repoints s3_key at a copy)
        "ALTER TABLE vector_layers ADD COLUMN source_s3_key VARCHAR(512)",
        # Large-upload convert options (CSV X/Y or WKT, srid, delimiter) — persisted for restart
        "ALTER TABLE vector_layers ADD COLUMN convert_opts TEXT",
        "ALTER TABLE raster_layers ADD COLUMN default_style TEXT",
        # Data sharing + STAC catalog metadata (notes §0h-addendum)
        "ALTER TABLE vector_layers ADD COLUMN is_public BOOLEAN DEFAULT 0",
        "ALTER TABLE vector_layers ADD COLUMN abstract TEXT",
        "ALTER TABLE vector_layers ADD COLUMN keywords VARCHAR(512)",
        "ALTER TABLE vector_layers ADD COLUMN license VARCHAR(128)",
        "ALTER TABLE vector_layers ADD COLUMN attribution VARCHAR(256)",
        "ALTER TABLE raster_layers ADD COLUMN is_public BOOLEAN DEFAULT 0",
        "ALTER TABLE raster_layers ADD COLUMN abstract TEXT",
        "ALTER TABLE raster_layers ADD COLUMN keywords VARCHAR(512)",
        "ALTER TABLE raster_layers ADD COLUMN license VARCHAR(128)",
        "ALTER TABLE raster_layers ADD COLUMN attribution VARCHAR(256)",
        # RBAC (A-01): role column + backfill. SQLite ADD COLUMN can't be NOT NULL
        # without a constant default, so the column is nullable on migrated DBs —
        # the guarded UPDATEs below fill it, and every user-creating code path sets
        # role explicitly. Pre-RBAC non-admins had full CRUD on their data → editor;
        # the earliest admin becomes the single workspace owner.
        "ALTER TABLE users ADD COLUMN role VARCHAR(16)",
        "UPDATE users SET role = CASE WHEN is_admin THEN 'admin' ELSE 'editor' END WHERE role IS NULL",
        "UPDATE users SET role = 'owner' WHERE id = (SELECT MIN(id) FROM users WHERE is_admin) "
        "AND NOT EXISTS (SELECT 1 FROM users WHERE role = 'owner')",
        # DB-level single-owner invariant (SQLite partial unique index)
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_single_owner ON users(role) WHERE role = 'owner'",
        # A-04 session revocation: per-user JWT version (default 0 → existing tv-less tokens stay valid).
        "ALTER TABLE users ADD COLUMN token_version INTEGER DEFAULT 0",
        # A-02 per-resource sharing — workspace visibility axis (private | organization | public).
        # Nullable ADD + guarded backfill (SQLite can't ADD NOT NULL without a constant default;
        # every create path sets it, model default is 'organization'). Existing data: public IFF it
        # was already is_public (the pre-A-02 STAC opt-in), else organization (the shared-workspace
        # default). Sources/portals have no is_public → organization. is_public stays as the derived,
        # write-only-synced column (visibility == 'public'); never read visibility from it.
        "ALTER TABLE vector_layers ADD COLUMN visibility VARCHAR(16)",
        "UPDATE vector_layers SET visibility = CASE WHEN is_public THEN 'public' ELSE 'organization' END WHERE visibility IS NULL",
        "ALTER TABLE raster_layers ADD COLUMN visibility VARCHAR(16)",
        "UPDATE raster_layers SET visibility = CASE WHEN is_public THEN 'public' ELSE 'organization' END WHERE visibility IS NULL",
        "ALTER TABLE external_sources ADD COLUMN visibility VARCHAR(16)",
        "UPDATE external_sources SET visibility = 'organization' WHERE visibility IS NULL",
        "ALTER TABLE portals ADD COLUMN visibility VARCHAR(16)",
        "UPDATE portals SET visibility = 'organization' WHERE visibility IS NULL",
        # Portals dropped the separate workspace-visibility control (it duplicated access_type
        # confusingly): reset any card-set 'private' back to organization. Safe to repeat — the API
        # never writes portals.visibility anymore.
        "UPDATE portals SET visibility = 'organization' WHERE visibility = 'private'",
        # Published-access tiers gained 'organization' (members-only) + 'owner' (creator+admins).
        # The legacy 'private' value already meant "any signed-in member" → migrate it to
        # 'organization'. Safe to repeat: the API now only ever writes organization/owner, never
        # 'private', so no genuine 'owner'-tier portal is ever clobbered by this.
        "UPDATE portals SET access_type = 'organization' WHERE access_type = 'private'",
        # Outgoing email via generic SMTP (C-08a)
        "ALTER TABLE setup_config ADD COLUMN smtp_host VARCHAR(256)",
        "ALTER TABLE setup_config ADD COLUMN smtp_port INTEGER DEFAULT 587",
        "ALTER TABLE setup_config ADD COLUMN smtp_security VARCHAR(16) DEFAULT 'starttls'",
        "ALTER TABLE setup_config ADD COLUMN smtp_username VARCHAR(256)",
        "ALTER TABLE setup_config ADD COLUMN smtp_password TEXT",
        "ALTER TABLE setup_config ADD COLUMN email_from VARCHAR(256)",
        # A-04 OIDC SSO config + per-user provider subject
        "ALTER TABLE setup_config ADD COLUMN oidc_enabled BOOLEAN DEFAULT 0",
        "ALTER TABLE setup_config ADD COLUMN oidc_issuer VARCHAR(512)",
        "ALTER TABLE setup_config ADD COLUMN oidc_client_id VARCHAR(512)",
        "ALTER TABLE setup_config ADD COLUMN oidc_client_secret TEXT",
        "ALTER TABLE setup_config ADD COLUMN oidc_label VARCHAR(128)",
        "ALTER TABLE setup_config ADD COLUMN oidc_auto_provision BOOLEAN DEFAULT 0",
        "ALTER TABLE setup_config ADD COLUMN oidc_allowed_domains VARCHAR(512)",
        "ALTER TABLE setup_config ADD COLUMN oidc_default_role VARCHAR(16) DEFAULT 'viewer'",
        "ALTER TABLE users ADD COLUMN oidc_sub VARCHAR(255)",
        # V-13 layer catalog: optional nested folder tree over a portal's layers
        "ALTER TABLE portals ADD COLUMN layer_groups TEXT",
        # V-11 Template Experiences: layout manifest + story-map sections (both nullable → webmap default)
        "ALTER TABLE portals ADD COLUMN layout_config TEXT",
        "ALTER TABLE portals ADD COLUMN story TEXT",
        # V-11 R3: per-portal colour theme (mode/accent/font) baked over the template theme.css
        "ALTER TABLE portals ADD COLUMN theme TEXT",
        # STABLE PUBLIC LAYER IDS (2026-07-29). Integer PKs leak into shareable URLs (STAC items,
        # OGC API - Features collections, /vsicurl/ COGs) and SQLite REUSES them: delete the
        # highest-id layer, create another, and every saved link to the old one silently returns
        # different data. `uid` is the stable identity — see models.new_uid().
        # Backfill uses SQLite's randomblob (this migration only ever runs on the SQLite era; the
        # Postgres move will carry the values across, not regenerate them).
        "ALTER TABLE vector_layers ADD COLUMN uid VARCHAR(32)",
        "UPDATE vector_layers SET uid = lower(hex(randomblob(6))) WHERE uid IS NULL OR uid = ''",
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_vector_layers_uid ON vector_layers (uid)",
        # Deployment history (2026-07-30): `deployment_runs` is a NEW table, created by
        # create_all — no ALTER needed. Listed here only so the migration log tells the story.
        # Backups (2026-07-30). Destination S3 + schedule + retention. `backup_runs` is a NEW
        # table (created by create_all, no entry needed); these are the setup_config additions.
        "ALTER TABLE setup_config ADD COLUMN backup_enabled BOOLEAN DEFAULT 0",
        "ALTER TABLE setup_config ADD COLUMN backup_endpoint VARCHAR(512)",
        "ALTER TABLE setup_config ADD COLUMN backup_bucket VARCHAR(256)",
        "ALTER TABLE setup_config ADD COLUMN backup_prefix VARCHAR(256) DEFAULT 'geodeploy-backups'",
        "ALTER TABLE setup_config ADD COLUMN backup_access_key VARCHAR(256)",
        "ALTER TABLE setup_config ADD COLUMN backup_secret_key TEXT",
        "ALTER TABLE setup_config ADD COLUMN backup_region VARCHAR(64) DEFAULT 'us-east-1'",
        "ALTER TABLE setup_config ADD COLUMN backup_schedule VARCHAR(16) DEFAULT 'off'",
        "ALTER TABLE setup_config ADD COLUMN backup_hour INTEGER DEFAULT 3",
        "ALTER TABLE setup_config ADD COLUMN backup_keep INTEGER DEFAULT 7",
        "ALTER TABLE setup_config ADD COLUMN backup_include_postgis BOOLEAN DEFAULT 1",
        "ALTER TABLE setup_config ADD COLUMN backup_include_objects BOOLEAN DEFAULT 1",
        "ALTER TABLE setup_config ADD COLUMN backup_include_state BOOLEAN DEFAULT 1",
        # Activity log pagination (2026-07-30): every query is `WHERE <filter> ORDER BY created_at
        # DESC LIMIT n`. The per-column indexes on the model satisfy the WHERE but leave a full sort
        # of the matches; SQLite uses ONE index per table per query, so these COMPOSITE indexes let
        # it walk the filter and the order together. Plain created_at covers the unfiltered page.
        "CREATE INDEX IF NOT EXISTS ix_audit_created ON audit_log (created_at DESC, id DESC)",
        "CREATE INDEX IF NOT EXISTS ix_audit_rtype_created ON audit_log (resource_type, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS ix_audit_actor_created ON audit_log (actor_id, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS ix_audit_action_created ON audit_log (action, created_at DESC)",
        "ALTER TABLE raster_layers ADD COLUMN uid VARCHAR(32)",
        "UPDATE raster_layers SET uid = lower(hex(randomblob(6))) WHERE uid IS NULL OR uid = ''",
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_raster_layers_uid ON raster_layers (uid)",
    ]
    for sql in pending:
        try:
            conn.execute(text(sql))
        except Exception:
            pass  # Column already exists


def _ensure_martin_config(settings) -> None:
    """Write a Martin config if none exists so the always-on Martin container can boot.

    Martin is a core service (started by the installer / compose, not profile-gated), so it
    may start before any database is configured. With no PostGIS yet we write a sources-less
    config (just `listen_addresses`) — Martin boots and serves an empty catalog instead of
    crash-looping on an unreachable DB. Once a DB is set up + a layer uploaded,
    `services.martin.regenerate_config` rewrites this with the `postgres` source and restarts.
    """
    import yaml
    config_path = settings.martin_config_path
    if os.path.exists(config_path):
        return
    try:
        config = {"listen_addresses": "0.0.0.0:3000"}
        if settings.postgis_host:
            config["postgres"] = {
                "connection_string": settings.postgis_sync_dsn,
                "pool_size": 5,
                "tables": {},
            }
        with open(config_path, "w") as f:
            yaml.dump(config, f, default_flow_style=False)
    except Exception:
        pass  # Non-fatal — Martin will emit an error on start but won't crash GeoDeploy


app = FastAPI(
    title="GeoDeploy API",
    version="0.3.0",
    description="Self-hosted spatial data management and geoportal builder",
    lifespan=lifespan,
    # nginx proxies ONLY `/api/` to this app; every other path falls through to the SPA. At
    # FastAPI's defaults (`/openapi.json`, `/docs`) the schema was therefore unreachable from
    # outside — the request returned the UI's index.html with content-type text/html. That broke
    # QGIS, which follows the `service-desc` link on the OGC API - Features landing page and
    # reports "Download of API page failed". Serve them under the proxied prefix instead.
    openapi_url="/api/openapi.json",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

settings = get_settings()

_extra_cors = [o.strip() for o in (settings.geodeploy_cors_origins or "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.is_dev else [
        "http://localhost",
        "https://localhost",
        os.getenv("GEODEPLOY_ORIGIN", ""),
        *_extra_cors,   # e.g. a GeoLibre origin, so its publish plugin can reach /api/interop
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# A-04 OIDC: Authlib stores the transient login state/nonce/PKCE in request.session. This signed
# cookie is short-lived and used ONLY during the SSO redirect dance; it doesn't touch the gd_session
# auth cookie. SameSite=Lax so it survives the top-level redirect back from the identity provider.
app.add_middleware(
    SessionMiddleware, secret_key=settings.secret_key, session_cookie="gd_oidc_state",
    max_age=600, same_site="lax", https_only=False,
)

# Public, unauthenticated READ surfaces that ANY tool (QGIS, GeoLibre, MapLibre, deck.gl, browsers) must
# be able to fetch cross-origin: the STAC catalog + the per-layer data artifacts (vector tiles/PMTiles/
# features/TileJSON/identify, raster COG). They're already public-by-id, so we answer them (and their
# OPTIONS preflight) with a single clean `Access-Control-Allow-Origin: *` and NO credentials — overriding
# the credentialed CORSMiddleware (which only allows listed origins) for JUST these paths. Registered
# LAST → it is the OUTERMOST middleware, so it sees the OPTIONS first and rewrites the response last.
_PUBLIC_CORS = re.compile(
    r"^/api/(stac(/.*)?"
    r"|ogc(/.*)?"        # OGC API - Features: the whole tree is public, unauthenticated read

    # `[\w.-]+`, NOT `\d+`. Public URLs address a layer by its stable `uid` (hex, e.g.
    # 488c2c7f55d7) since 2026-07-29 — a digits-only pattern silently stopped matching them, so
    # the header was never added and every browser client (GeoLibre, web maps) rejected a response
    # the server had served perfectly (206 with no ACAO). Pinned by test_cors_public_surface.
    r"|data/vector/[\w.-]+/(pmtiles|features\.geojson|features\.arrow|tilejson|identify)"
    r"|data/vector/[\w.-]+/parquet/.*"     # duckdb-wasm / GDAL read partition files cross-origin
    r"|data/raster/[\w.-]+/(cog|tilejson|statistics))$"
)


@app.middleware("http")
async def _public_data_cors(request, call_next):
    public = bool(_PUBLIC_CORS.match(request.url.path))
    if public and request.method == "OPTIONS":
        return Response(status_code=204, headers={
            "Access-Control-Allow-Origin": "*",
            # POST is for STAC item-search (`POST /api/stac/search`) — pystac-client's default.
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "*",
            "Access-Control-Max-Age": "86400",
        })
    response = await call_next(request)
    if public:
        # Starlette's MutableHeaders has NO .pop()/.setdefault-safe pop — use setitem + guarded del so
        # this never raises (a raise here 500s the STAC/data links, which is exactly what happened).
        response.headers["Access-Control-Allow-Origin"] = "*"
        if "access-control-allow-credentials" in response.headers:
            del response.headers["access-control-allow-credentials"]
        if "access-control-expose-headers" not in response.headers:
            response.headers["Access-Control-Expose-Headers"] = "*"
    return response


# API routes
for router in [setup.router, auth.router, auth_oidc.router, users.router, tokens.router,
               audit.router, portals.router, templates.router, admin.router, basemaps.router,
               vector.router, raster.router, sources.router, discover.router, stac.router,
               ogcapi.router, interop.router, backups.router]:
    app.include_router(router, prefix="/api")

# Serve published portals as static files
portals_dir = f"{settings.data_dir}/portals"
os.makedirs(portals_dir, exist_ok=True)
app.mount("/portals", StaticFiles(directory=portals_dir, html=True), name="portals")

# Serve template preview images
templates_dir = "/templates"
if os.path.exists(templates_dir):
    app.mount("/templates-static", StaticFiles(directory=templates_dir), name="templates-static")


@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.3.0"}
