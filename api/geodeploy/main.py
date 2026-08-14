import logging
import os
import re
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import Response

from .config import get_settings
from . import database
from .database import Base
from .routers import (public, setup, auth, auth_oidc, portals, stac, templates, admin, basemaps, users,
                      tokens, audit, interop, ogcapi, backups)
from .routers.data import vector, raster, sources, discover
# The migration list lives in its own module because the Celery worker re-applies it after a
# RESTORE (pg_restore --clean installs the SNAPSHOT's schema, losing every column added since);
# importing main.py from a task would drag in the whole FastAPI app.
from .schema_migrations import PG_MIGRATIONS as _PG_MIGRATIONS

# Module-level, because `_apply_pg_migrations` logs from it. `lifespan` binds its own local `log`,
# so this was previously a NameError waiting inside an `except` — a failing migration would have
# raised from its own error handler and surfaced as "state database not reachable yet", hiding the
# real cause behind an unrelated message.
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    for subdir in ("portals", "portal_assets", "temp", "martin"):
        os.makedirs(f"{settings.data_dir}/{subdir}", exist_ok=True)

    # State lives in PostgreSQL, which the SETUP WIZARD configures — so the app must boot with no
    # database at all and still serve /api/setup/*. `configure()` returns None until credentials
    # exist in the environment; `routers/setup.configure_db` calls it again and creates the schema
    # the moment it has them.
    log = logging.getLogger(__name__)
    eng = database.configure(force=True)
    if eng is None:
        log.info("no state database configured yet — serving the setup wizard only")
    else:
        # NEVER fatal. Credentials existing does not mean the server is REACHABLE: on a fresh
        # install postgres is behind the `local-db` compose profile and is not started until the
        # wizard runs, and on a running instance the database can simply be slow to come up or
        # briefly down. Dying here puts the API in a restart loop and takes the whole site to 502
        # — including the setup wizard that would have fixed it. Log and serve; `configure_db`
        # creates the schema itself, and `get_db` answers 503 until then.
        try:
            async with eng.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
                await conn.run_sync(_apply_pg_migrations)
            log.info("state database ready")
            # Publish the credentials that JUST WORKED to the shared volume, so the Celery worker
            # can reach the database no matter what its own (container-creation-time) environment
            # says. Doing it here rather than only in the setup wizard makes it SELF-HEALING: any
            # instance configured before this existed repairs itself on the next API start, and
            # the value can never drift from what the API is actually using. Connecting
            # successfully is the proof that these credentials are the right ones.
            try:
                from . import state_db
                state_db.write_runtime_credentials(
                    settings.postgis_host, settings.postgis_port, settings.postgis_db,
                    settings.postgis_user, settings.postgis_password, settings.postgis_sslmode)
                # STORAGE, for the same reason and with the same self-healing property. The worker's
                # environment was fixed when its container was created — before the wizard ran — so
                # it kept the installer's `http://minio:9000` and `geodeploy` bucket. Publishing here
                # repairs any instance configured before this existed, on its next API start, with no
                # action from the operator. The API's own values come from `.env`, which the wizard
                # does write correctly, so they are the right source.
                if settings.storage_access_key:
                    state_db.write_runtime_storage(
                        settings.storage_endpoint, settings.storage_bucket,
                        settings.storage_access_key, settings.storage_secret_key,
                        settings.storage_region)
            except Exception:
                log.exception("could not publish runtime credentials for the worker")
            # Martin's config is only rebuilt when the LAYER LIST changes, so an instance that
            # updates without uploading anything keeps whatever config was written under the
            # PREVIOUS version — including one written before a tile source existed. That is how
            # 3D point bars could be fully implemented and still serve nothing: the
            # `geodeploy.point_pillars` function source was never added to the running config.
            # Rebuilding here makes the tile config self-healing on restart, like the credentials
            # above, and it is cheap — Martin is only restarted if the config actually changed.
            try:
                await _refresh_martin_sources()
            except Exception:
                log.exception("could not refresh the Martin tile configuration")
        except Exception as exc:
            log.warning("state database not reachable yet (%s) — serving the setup wizard "
                        "until it is", exc)

    # Write a minimal Martin config on first start so Martin can boot without layers
    _ensure_martin_config(settings)

    yield


def _apply_pg_migrations(conn) -> None:
    """Apply the additive column migrations. Each runs independently so one failure cannot block the
    rest, and a failure is logged rather than fatal — the API must keep serving (and the wizard must
    stay reachable) even if a migration cannot be applied.

    "Independently" needs a SAVEPOINT, which is what `begin_nested` opens. The try/except alone did
    NOT deliver it: Postgres aborts the entire transaction on the first failed statement, so every
    later migration then fails with "current transaction is aborted" — and the except swallowed
    that too, silently. It shipped exactly that way: one statement missing `IF NOT EXISTS` failed on
    every instance that already had the column, and the column added AFTER it was never created, so
    `/data/raster` answered 500 on a real deploy. One statement, one savepoint, one rollback.
    """
    from sqlalchemy import text
    for stmt in _PG_MIGRATIONS:
        try:
            with conn.begin_nested():
                conn.execute(text(stmt))
        except Exception:
            log.exception("schema migration failed: %s", stmt)


def _apply_schema_migrations(conn) -> None:
    """LEGACY — SQLite-era upgrades, kept for reference only.

    State moved to PostgreSQL on 2026-07-30 and every install starts from `Base.metadata.create_all`,
    which builds the current schema outright. These statements are SQLite dialect (`DEFAULT 0` for a
    boolean, `lower(hex(randomblob(6)))`) and would error on Postgres, so nothing calls this anymore.
    A future Postgres schema change belongs in a real migration, not here — see notes_for_future.
    """
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


async def _refresh_martin_sources() -> None:
    """Rebuild martin-config.yaml from the current layer list at startup.

    Same list the `/admin/reload-martin` endpoint builds — this just stops it being something an
    operator has to know to click. Non-fatal by construction: the caller wraps it, and
    `regenerate_config` already swallows a failure to reach the database or Docker.
    """
    from sqlalchemy import select
    from . import database
    from .models import VectorLayer
    from .services.martin import regenerate_config

    if database.AsyncSessionLocal is None:
        return
    async with database.AsyncSessionLocal() as session:
        result = await session.execute(
            select(VectorLayer).where(VectorLayer.status == "ready",
                                      VectorLayer.storage_backend == "postgis")
        )
        layers = [{"schema_name": l.schema_name, "table_name": l.table_name,
                   "geometry_column": l.geometry_column, "id_column": l.id_column, "crs": l.crs}
                  for l in result.scalars().all()]
    await regenerate_config(layers)


def _ensure_martin_config(settings) -> None:
    """Write a Martin config if none exists so the always-on Martin container can boot.

    Martin is a core service (started by the installer / compose, not profile-gated), so it may
    start before any database is configured.

    IMPORTANT, learned the hard way: Martin EXITS with "No tile sources found" when it resolves to
    an empty catalog — it does not idle. So never write `tables: {}`; omit the key and let it
    auto-discover. Before the wizard runs there is no reachable database at all, so Martin will
    restart-loop until `routers/setup.configure_db` rewrites this with real credentials and
    restarts it. That is expected during setup and only during setup.
    """
    import yaml
    config_path = settings.martin_config_path
    if os.path.exists(config_path):
        return
    try:
        config = {"listen_addresses": "0.0.0.0:3000"}
        if settings.postgis_host and settings.postgis_password:
            # No `tables` key: an empty one makes Martin exit (see the docstring).
            config["postgres"] = {
                "connection_string": settings.postgis_sync_dsn,
                "pool_size": 5,
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
    r"|public(/.*)?"     # the anonymous instance index: portals + public layers by kind

    # `[\w.-]+`, NOT `\d+`. Public URLs address a layer by its stable `uid` (hex, e.g.
    # 488c2c7f55d7) since 2026-07-29 — a digits-only pattern silently stopped matching them, so
    # the header was never added and every browser client (GeoLibre, web maps) rejected a response
    # the server had served perfectly (206 with no ACAO). Pinned by test_cors_public_surface.
    r"|data/vector/[\w.-]+/(pmtiles|features\.geojson|features\.arrow|tilejson|identify|legend)"
    r"|data/vector/[\w.-]+/parquet/.*"     # duckdb-wasm / GDAL read partition files cross-origin
    # Whole-layer / clipped downloads: same public terms as the artifacts above, and a browser
    # client polls the status endpoint cross-origin while the export runs.
    r"|data/(vector|raster)/[\w.-]+/export"
    r"|data/(vector|raster)/[\w.-]+/export-(status|download)/[\w-]+"
    r"|data/raster/[\w.-]+/(cog|tilejson|statistics|legend))$"
)


class _DemoUploadCap:
    """DEMO ONLY: refuse request bodies over the demo ceiling.

    PURE ASGI, deliberately — NOT `@app.middleware("http")`. That decorator is Starlette's
    BaseHTTPMiddleware, which runs the rest of the app in a separate anyio task; stacking a second one
    broke the `get_db` dependency teardown so that COMMITS WERE SILENTLY ROLLED BACK. Every write in
    the app, not just demo uploads. A pure-ASGI wrapper adds no task and no context switch, so it
    cannot do that.

    Content-Length is enough here: every browser upload sets it, and the check exists as a courtesy —
    the real enforcement for direct-to-storage uploads is `demo_upload_cap()` at the multipart
    initiate, which is where a size that never passes through the API is declared.

    Inert unless demo mode is on: a normal install keeps its full 2 GB API cap.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and scope.get("method") in ("POST", "PUT", "PATCH"):
            settings = get_settings()
            if settings.geodeploy_demo_mode:
                limit = settings.geodeploy_demo_max_upload_mb * 1024 * 1024
                raw = dict(scope.get("headers") or {}).get(b"content-length")
                try:
                    declared = int(raw) if raw else 0
                except ValueError:
                    declared = 0
                if declared > limit:
                    from fastapi.responses import JSONResponse
                    await JSONResponse(status_code=413, content={"detail": (
                        f"This demo caps uploads at {settings.geodeploy_demo_max_upload_mb} MB "
                        f"(yours is {declared / 1024 / 1024:.0f} MB). The limit exists only here — a "
                        f"GeoDeploy you install yourself has no such cap.")})(scope, receive, send)
                    return
        await self.app(scope, receive, send)


class _HeadAsGet:
    """Answer HEAD on every GET route, by running the GET and dropping the body.

    FastAPI's `APIRoute` does NOT add HEAD to a GET route (plain Starlette's `Route` does), so every
    endpoint here answered **405** to HEAD. That is not a formality: **`/vsicurl/` opens a URL with a
    HEAD request**, so GDAL — and therefore QGIS, ogr2ogr and anything else built on it — could not
    open a single GeoDeploy artifact. `ogrinfo /vsicurl/…/pmtiles` failed with "not recognized as
    being in a supported file format", which reads like a broken file and is really a 405 on a probe.
    The COG path our own documentation recommends for QGIS was affected too.

    PURE ASGI for the reason `_DemoUploadCap` documents above: a second `BaseHTTPMiddleware` broke
    dependency teardown once and silently rolled back commits.

    Headers — Content-Length and Content-Range included — are passed through untouched, which is
    what a HEAD is FOR; only the body bytes are dropped. A streamed response is still produced
    server-side, but the probes that matter here are small metadata reads.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("method") != "HEAD":
            await self.app(scope, receive, send)
            return

        scope = dict(scope, method="GET")

        async def send_without_body(message):
            if message.get("type") == "http.response.body":
                # Keep the message (the protocol needs it) but empty, and never ask for more.
                message = {"type": "http.response.body", "body": b"", "more_body": False}
            await send(message)

        await self.app(scope, receive, send_without_body)


app.add_middleware(_HeadAsGet)
app.add_middleware(_DemoUploadCap)


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
               ogcapi.router, interop.router, backups.router, public.router]:
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
