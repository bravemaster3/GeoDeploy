# api/geodeploy/routers/

## Purpose
All HTTP endpoints. Every router is registered in `main.py` under the `/api` prefix.

## Permission model (RBAC A-01; API tokens A-03 — 2026-07-17)
**Shared workspace:** every member SEES all data and portals; the ROLE decides what they may do.
`user_id` on resources is "created by" provenance, not an access boundary. Roles (deps.py
`ROLE_ORDER`): `viewer` (0, read-only) < `editor` (1) < `admin` (2) < `owner` (3, exactly one).

**A-03 scoped API tokens** thread through the SAME dependencies. `get_current_user` now also accepts a
`gdp_…` bearer (→ `deps.authenticate_api_token`, stashed on `request.state.api_token`). Enforcement is a
single factory **`deps.require_scope(scope)`** that checks the scope's ROLE FLOOR (`deps.SCOPES`:
`data:read`/`portal:read`=viewer, `data:write`/`portal:write`/`portal:publish`=editor,
`users:admin`=admin) AND — only when the request is token-authed — that the token carries the scope
(else 403 `Token missing scope: …`). For a browser (JWT/cookie) session the scope check is a **no-op**,
so behaviour is identical to A-01. **Deny-by-default:** `require_role`/`require_editor`/`require_admin`
now REJECT token requests, so any route not explicitly `require_scope`-annotated (e.g. all of
`admin.py`, ownership transfer) is browser-only for tokens.
- **Reads** (list GETs, portal detail, stats, jobs) → `require_scope("data:read"|"portal:read")`. List
  queries AND authenticated by-id lookups use `common.visible_to(user, Model)` — **the A-02 sharing
  seam** (below): admins/owner see all; others see non-private + their own.
- **Mutations** on data → `require_scope("data:write")`; portals → `require_scope("portal:write")`
  (create/edit/delete draft) or `require_scope("portal:publish")` (publish/unpublish/assets). The
  by-id lookups for vector/raster/sources carry the `visible_to` filter, so a private resource the
  caller can't see 404s (the role 403 still fires BEFORE the lookup — pinned in test_rbac.py).
- **Token management** (`tokens.py`, `/tokens`): each user manages their OWN tokens via a browser
  session; a token can't mint/manage tokens (anti-escalation); scope ≤ owner role at mint; mandatory
  expiry 30/90/365d (default 90). Only the sha256 hash is stored; the raw `gdp_…` shown once.
- **A-04 session revocation:** browser JWTs carry `tv` (= `User.token_version`); `get_current_user`
  rejects a stale tv. `auth.py` bumps tv on password change/reset + `POST /auth/logout-all` (each
  re-issues a fresh token for the acting session). Pre-A-04 tv-less tokens read as tv=0 (no forced
  re-login). `GET /auth/session-token` returns the JWT from the `gd_session` cookie (for the SSO handoff).
- **Delete safety (2026-07-20):** `GET /data/{vector,raster,sources}/{id}/usage` → the portals that
  include a layer (`common.portals_using`), shown in the UI's delete-confirmation dialog. On delete,
  `common.prune_layer_from_portals` removes the (now-dangling) layer from every portal's `layer_configs`
  and RE-PUBLISHES the published ones (best-effort, lazy-imports `_rebuild_bundle`) so no "ghost" layer
  lingers in the live map/editor. The delete audit detail records `portals_updated`.
- `backups.py` — **admin-only + browser-only** backup config, history and manual runs
  (`/backups/settings`, `/settings/test`, `/runs`, `/stored`, `/run`, `DELETE /stored/{key}`).
  Browser-only on purpose: these settings hold the credentials to the one copy that survives losing
  this instance, so a scoped API token must not be able to read them or re-point them. The
  destination secret is **write-only** (blank keeps the stored value; `secret_set` tells the UI one
  exists) — same rule as the SMTP/OIDC secrets. `/stored` reads the destination's own manifests,
  which is the only trustworthy inventory: our `backup_runs` table lives in the state DB, and that
  DB is itself part of what gets backed up. Deletion is confined to the configured prefix.
  The work runs in Celery (`tasks/backup.py`, its own `backup` queue so a multi-hour object copy
  can't occupy the ingest slots); scheduling is an every-15-min beat tick that reads the schedule
  from the DB, so changing it in Settings takes effect with no worker restart.
- **`GET /admin/updates?refresh=true` (2026-07-30)** bypasses the 10-minute `_UPDATE_CACHE`. That
  TTL protects GitHub's 60 req/hr unauthenticated budget against repeated PAGE LOADS; it must not
  make a DELIBERATE check return a stale answer, which made a just-pushed commit look like it never
  landed for up to ten minutes. The UI forces it from the Check button (and after an update
  finishes) but not on mount.
- **Deployment history (2026-07-30):** `GET /admin/deployments` + the `deployment_runs` table.
  `POST /admin/update` opens a row; it is CLOSED by whichever `GET /admin/update/status` poll first
  sees a terminal phase (`_reconcile_deployment`) — the API container is recreated by the update
  itself, so the process that started it cannot write the outcome. `GET /admin/services/{name}/logs`
  gained a `timestamps` flag alongside `tail`.
- **Activity log pagination (2026-07-30):** `GET /audit` returns a PAGE — `{items, total, limit,
  offset}` (default limit 20, max 500) — and every filter is applied SERVER-side before the page is
  cut: `q` (LIKE over action/actor_name/resource_id/detail), `resource_type`, `resource_id`,
  `actor_id`, `action` (exact OR `action.` prefix, so "portal" gets the whole family), and
  `since`/`until` ISO instants. Filters AND together. The UI computes date presets (today / this
  week / this month / last 3 months / this year) in the VIEWER's timezone and sends an absolute
  `since` — the server never guesses where a week starts. `GET /audit/actions` lists the distinct
  action values present so the filter offers real options instead of a hardcoded list that drifts.
  Composite indexes (`main.py`) back `WHERE <filter> ORDER BY created_at DESC`: per-column indexes
  satisfied the WHERE but left a full sort. NEVER fetch the log whole and filter client-side — it
  would search only the downloaded slice.
- **A-05 audit log** (`audit.py`, `GET /audit`, admin-only + filterable): reads the append-only
  `AuditLog`. Mutations write via **`common.record_audit(db, actor, action, resource_type, resource_id,
  detail)`** — BEST-EFFORT + self-committing (never fails the real op), called AFTER the mutation
  commits. Instrumented: users (role_change/ownership_transfer/delete/invite), portals
  (create/publish/unpublish/delete), tokens (create/revoke), auth (login/password_change/logout_all +
  oidc login), data (vector/raster upload/share/delete, source create/share/delete). `actor_id` is not
  a FK (log survives user deletion); `actor_name` denormalized. To audit a new mutation: one
  `record_audit(...)` after its commit.
- **A-04 OIDC SSO** (`auth_oidc.py`, `/auth/oidc/*`): public `status`; `login`→ Authlib redirect;
  `callback`→ validate id_token → `services.oidc.resolve_user` (link by sub/verified-email; provision
  only if allow-listed) → mint JWT + `gd_session` cookie → 302 `/sso-callback`. Admin config CRUD is
  `admin.py` `/admin/oidc-settings` (client secret write-only + EncryptedText). Needs Starlette
  `SessionMiddleware` (state/nonce) — added in `main.py`.

**A-02 per-resource sharing (2026-07-16):** each vector/raster layer and external source has a
`visibility` — `private` (creator + admins/owner) ⊂ `organization` (all members; the default) ⊂
`public` (layers only: STAC catalog + raw assets). **Portals do NOT** — a portal's audience is its
published `access_type` (see the portals entry: public / password / organization / owner). `is_public`
is now DERIVED / write-only-synced
(`= visibility == "public"`) via `common.apply_sharing`; STAC / `_publicly_readable` / `/cog` /
portal_generator keep reading it unchanged. Re-sharing is an editor+ power over resources they can SEE
(NOT creator-only — an editor can already delete an org resource). PUBLIC-by-id display endpoints are
deliberately NOT visibility-filtered (published portals depend on them).
- **/admin/*** + setup reconfiguration → `require_admin` (browser-only for tokens). **/users/*** →
  `require_scope("users:admin")`; ownership transfer → `require_owner` (browser-only).
- **PUBLIC surface unchanged**: portal assets/export, vector features.*/identify/pmtiles/parquet
  (`_publicly_readable`), raster /cog + /colormaps, sources features.geojson, stac/templates/basemaps.
- List responses carry `user_id` + `created_by` (one `common.creator_names` query per list call);
  the creator filter in the UI is client-side — do NOT add a `?created_by=` API param.

## Contents
- `users.py` — **user management (admin-gated)**: `GET /users` (members + per-user resource counts),
  invitations (`POST/GET /users/invitations`, `POST .../{id}/regenerate`, `DELETE .../{id}`) with
  **sha256-hashed single-use tokens** (raw token returned ONCE on create/regenerate; "regenerate" is
  the only way to get a link again), `PUT /users/{id}/role` (owner untouchable, no self-change,
  `is_admin` write-synced), `POST /users/{id}/transfer-ownership` (**owner only**; demotes the caller
  to admin FIRST — the partial unique index `uq_users_single_owner` forbids two owners),
  `DELETE /users/{id}` (**reassigns** the member's layers/portals/sources to the owner — nothing is
  destroyed; S3 keys/schema names are stored full-string so nothing physical moves), and
  `POST /users/{id}/reset-password-link` (24 h reset token; owner target requires owner caller).
  Invite/reset links are ALWAYS copy-deliverable; when SMTP is configured (C-08a) they are ALSO
  emailed best-effort (`email_sent` flag in the response; a relay failure never fails the operation).
- `common.py` — `visible_to()` (the A-02 seam), `creator_names()`, and `busy_job_progress()` (2026-07-17:
  `{layer_id: (progress, current_step)}` from each queued/processing layer's latest UploadJob, in ONE
  query) shared by the resource routers. The vector/raster list endpoints attach it to `*LayerOut`
  (`progress`/`current_step`) so My Data shows "Processing NN%" for CLI uploads / after a reload — the
  browser's per-session `pollJob` only covers uploads made in that tab.
- **Outgoing email (C-08a, 2026-07-16)** — generic SMTP via `services/notifications.py` (stdlib,
  any provider incl. Resend/Brevo through their SMTP endpoints), **strictly optional**: admin.py
  `GET/PUT /admin/email-settings` (password write-only, never returned; blank keeps stored) +
  `POST /admin/email-settings/test` (surfaces the relay's real error). PUBLIC
  `POST /auth/forgot-password` always answers 202 identically (anti-enumeration; acts only when the
  user exists AND email is configured; nginx zone `pwreset` 3r/m); `/setup/status` exposes
  `email_enabled` so the login page knows whether to offer "Forgot password?".
- `setup.py` — first-run wizard: `/setup/status`, `/setup/configure-db`, `/setup/configure-storage`, `/setup/create-admin`. Provisions PostGIS/MinIO (via `services.postgis`/`services.minio`), then `_write_env()` persists creds to `.env` and `_apply_to_process()` pushes them into `os.environ`, clears the settings cache, and restarts the celery container. **`_write_env` also writes `TITILER_S3_ENDPOINT`** (scheme-stripped), **`TITILER_AWS_HTTPS`** (YES for an https/external S3, NO for local MinIO), and **`POSTGIS_SSLMODE`** (`prefer` for external DB, empty for local). External storage recreates TiTiler via `minio.restart_titiler`; Martin is a core always-on service so external DB needs nothing special at setup. `_write_env` also persists **`COMPOSE_PROFILES`** (`local-db`/`local-storage`) so `docker compose up` (install/update) keeps the wizard-provisioned local postgres/minio managed instead of orphan-removing them.
- `auth.py` — `/auth/login` (OAuth2 password form → JWT, 7-day expiry) and `/auth/me` (now returns
  `role`). Bcrypt via passlib. **RBAC additions (2026-07-16):** PUBLIC `GET /auth/invitations/{token}`
  (info for the accept/reset pages; 410 when used/expired), PUBLIC `POST .../{token}/accept`
  (redeem invite → create user with the invited role → auto-login TokenResponse; 409 if the email
  registered meanwhile), PUBLIC `POST /auth/password-reset/{token}`, and authed `PUT /auth/password`
  (verify current, set new — does NOT revoke outstanding 7-day JWTs; that's A-04).
- `portals.py` — portal CRUD + `/portals/{id}/publish` and `/unpublish`. Publish loads ready layers, calls `services.portal_generator.generate_style` + `build_portal_bundle` (via the shared `_rebuild_bundle` helper) to write the static site. Slugs are auto-deduped (`_unique_slug`). Passwords stored as both bcrypt (future server-side) and SHA-256 (embedded in the published HTML gate). **Rename (2026-07-11): `PUT /portals/{id}` regenerates the slug when `title` changes** (unique, excluding self); if the slug changes on a **published** portal it re-bakes the bundle under the new slug (the slug is baked as `{{SLUG}}`) and removes the old `data/portals/{old_slug}/` dir so the old URL 404s — a draft just carries the new slug until published. **Anti-flash bake (2026-07-16):** `_rebuild_bundle` reads each deck (GeoParquet, non-PMTiles) layer's manifest core extent via `portal_generator.read_deck_core_bbox` (best-effort, `run_in_threadpool`) and passes `deck_core_bounds` to `generate_style` so a deck-only portal opens on the core extent (no on-load snap — see `services/README.md`).
- `templates.py` — `/templates` lists template folders from `/templates` that have `template.json` + `style.json` (layout.html is optional — shared skeleton fallback).
- `portals.py` **catalog scope "all public" (V-14, 2026-07-30):** `_with_public_catalog_layers` appends
  every `visibility == "public"`, ready layer the portal does not already carry to a LOCAL copy of
  `layer_configs`, marked `visible: False` + `_catalog_extra: True`, for a catalog portal whose scope is
  `public`. That makes every card's "Show on map" / "Zoom to" work through the normal runtime path — the
  layer really is on the map, just hidden — instead of needing layers injected at runtime from share
  links. Hidden layers fetch nothing until a visitor toggles one. NOT persisted to `Portal.layer_configs`
  (the author's saved config is untouched, and the published-portal readability cache keyed off that
  column is unaffected), and appended LAST so they draw beneath the author's own layers.
  **`generate_style` skips every bounds contribution for `_catalog_extra` configs** — otherwise the map
  would open on the union of the whole instance. A short-lived `GET /portals/{slug}/catalog` feed did
  this at runtime instead; it was removed with this change (baked layers make it dead code).
- `portals.py` area-select export (all **public**, queued via Celery so heavy clips never block the API):
  - `POST /portals/{slug}/export-bundle` (body `{bbox, items:[{layer_id, layer_type, format}]}`) — validates the items belong to the portal, resolves them, enqueues `tasks.export.export_bundle`, returns `{job_id}` (202).
  - `GET /portals/{slug}/export-status/{job_id}` — `queued|processing|ready|error` (checks the result file + Celery `AsyncResult`).
  - `GET /portals/{slug}/export-download/{job_id}` — streams `data/temp/exports/{job_id}.zip` (job_id validated against path traversal). Old exports are swept (>1h) on each new request.
  - The clip work + format conversion lives in `tasks/export.py`. Formats: vector geojson/gpkg/csv, raster tif; 50k-feature cap, raster output capped/downsampled.
- `admin.py` — `/admin/health` (HTTP-pings Martin/TiTiler + reports container status for postgres/minio/redis/martin/titiler/nginx/celery/ui/api, each flagged `controllable`), `/admin/services/{name}/{action}` (Coolify-style start/stop/restart via the Docker socket; `api` is non-controllable since it serves the request; resolves both fixed `container_name`s and Compose auto-names), `/admin/reload-martin` (regenerates Martin config from all ready PostGIS layers — the manual recovery hook), `/admin/storage-stats` (**accurate per-store breakdown since 2026-07-16**: PostGIS via `pg_total_relation_size` over catalog tables, raster COGs via S3 head, GeoParquet files/prefixes + PMTiles via S3 list, published bundles via dir walk; a store that can't be measured is `null`, NOT 0 — previously the number was just the portal-bundle dir and wildly understated usage).
- `data/vector.py` — vector layer list/upload/job-status/default-style/delete. Upload streams to `data/temp`, creates the `VectorLayer` + `UploadJob` rows, dispatches `tasks.vector_ingest`. **`POST /upload-csv`** (multipart: file + x/y column + srid + name) saves the CSV to `data/temp` and dispatches `tasks.csv_import` (is_s3=False) to build a point layer — the upload-a-CSV counterpart to "Import existing → CSV". **GeoParquet upload is a 2-step presigned DIRECT-to-storage flow** (no multi-GB passthrough of the API): `POST /geoparquet/presign` (body `{filename, name?, file_size?}`) returns `{upload_url, s3_key}` (key minted server-side under `vectors/{uid}/`, 10 GB cap) → browser PUTs the file straight to MinIO via the same-origin `/s3/` nginx proxy → `POST /geoparquet/complete` (body `{s3_key, name?, file_size?}`, key validated to be in the caller's prefix) registers a `storage_backend='geoparquet'` `VectorLayer` + queues `tasks.geoparquet_import` (DuckDB inspect, no PostGIS). Delete: a **geoparquet** layer deletes its S3 object (no table); a **postgis** layer drops the PostGIS table; either way Martin config is regenerated (postgis layers only). **GeoParquet display is deck.gl-first** (the prepped covering column makes viewport queries cheap; PMTiles is a fallback for layers explicitly tiled). **`GET /{layer_id}/features?bbox=&limit=`** (authed) and **`GET /{layer_id}/features.geojson?bbox=&limit=`** (**PUBLIC**) are the DuckDB viewport feed → GeoJSON (threadpool, covering-column-pruned, capped 200k): the authed one drives the editor preview's deck.gl overlay, the public `.geojson` one drives the deck.gl overlay in published (unauthenticated) portals — public-by-id like `/pmtiles` below (creds stay server-side; multi-tenant scoping is a future concern, notes §0h-addendum). **`POST /large/presign` + `/large/complete`** (2026-07-11) are the **large-vector** direct-to-storage upload: a CSV/GeoJSON/GeoPackage/shapefile-zip too big for the 2 GB API multipart cap gets a presigned PUT URL (up to `MAX_LARGE_UPLOAD`, 10 GB, env-tunable), the browser PUTs it to `/s3/`, and `/large/complete` registers a processing layer + queues `tasks.convert_upload.convert_to_geoparquet` (background convert → GeoParquet → prep → ready). CSV geometry options (x/y or wkt column, srid, delimiter) ride the complete body. **`GET /{layer_id}/identify?lng=&lat=&tol=&limit=`** (**PUBLIC**, 2026-07-11) is identify-on-click for GeoParquet layers: attributes of the features under a clicked point (`duckdb_engine.query_features_at_point`, covering-pruned tiny-box query + exact intersects) — this is what feeds deck-layer popups in portals AND the editor preview, since the viewport transports ship geometry only. `POST /upload-csv` accepts **either** `x_column`+`y_column` (points) **or `wkt_column`** (any WKT geometry, 2026-07-11). **`POST /{layer_id}/tile`** (authed) (re)generates the layer's **PMTiles** archive (`tasks.pmtiles_tile`) — the fallback display path for tiling a pre-existing file or a layer too big for the viewport feed. Delete removes both the `.parquet` and `.pmtiles` objects. **`POST /{layer_id}/reprocess`** (authed, 2026-07-11) **restarts** a stalled/failed GeoParquet layer's background processing without a re-upload — it inspects the layer's current `s3_key`: a RAW ext (`.csv/.gpkg/.geojson/.zip`) means the convert never finished → re-queues `convert_upload.convert_to_geoparquet` with the saved `convert_opts`; a `.parquet`/prepared prefix means re-run the spatial prep. A fresh `UploadJob` is created (returns `JobStatus` to poll). Motivated by a real case: recreating the celery container silently kills any in-flight convert/prep, leaving the layer stuck at its last %. The CSV convert options are persisted on the layer (`vector_layers.convert_opts` JSON, written by `/large/complete`) so a CSV restart doesn't need the user to re-pick columns; a CSV uploaded before this column existed can't be restarted (must re-upload). **`GET /{layer_id}/pmtiles`** is a **PUBLIC** HTTP-Range proxy streaming the layer's PMTiles archive from the (private) bucket — MapLibre's `pmtiles://` protocol reads it; public like `/tiles/` (Martin) since published portals are unauthenticated, same-origin (no CORS), creds stay server-side, and only the layer id is addressable.
- `data/raster.py` — raster equivalent; list endpoint attaches a computed `tile_url` for ready layers; `/colormaps` lists TiTiler colormaps; `/{id}/stats` proxies TiTiler `/cog/statistics` and returns a suggested `rescale` ("min,max", 2–98th percentile) for auto-stretch. Dispatches `tasks.raster_ingest`.
- `data/sources.py` — **external sources** (WMS/XYZ raster, WFS vector) shown in portals without ingesting. Authed CRUD (`GET/POST/DELETE /data/sources`); POST probes a WFS to learn geometry + bbox. **Public** `GET /data/sources/{id}/features.geojson` proxies a WFS to GeoJSON (same-origin → no CORS; published portals are unauthenticated). Rendering helpers live in `services/external_sources.py`; `portal_generator` bakes them into the published style.
- `data/discover.py` — **import existing data** (mostly no copy): `GET /data/discover/database` lists spatial tables from PostGIS `geometry_columns` (any non-system schema, flags already-imported); `POST` registers selected tables as `VectorLayer` rows (introspects bbox→EPSG:4326, columns, PK→`id_column`, geometry column, SRID, est. feature count) then regenerates Martin. `GET /data/discover/storage` lists `.tif/.tiff` (kind `raster`) + **`.parquet`/`.geoparquet` (kind `geoparquet`, 2026-07-11)** + `.csv` (kind `csv`) in the bucket; `POST /storage` registers rasters as `RasterLayer` rows (`cog_converter.inspect_s3`, header-only) and **GeoParquet files as `storage_backend='geoparquet'` `VectorLayer`s via a queued `import_geoparquet` job** (inspect + spatial prep; response carries `jobs` the UI polls). The attached key is kept in `source_s3_key` (de-dup survives the prep repointing `s3_key`); the prep writes its partitioned copy under `vectors/` and never touches the source (attach ≠ copy/destroy). **CSV** is the exception (a CSV isn't tile-servable): `GET /storage/csv-columns` returns the header, `POST /storage/csv` (key, name, x/y columns **or `wkt_column` — WKT geometry of any type, e.g. polygon footprints (2026-07-11)**, srid) **queues a Celery job** (`tasks/csv_import.py`) that loads the geometry into PostGIS with column **type inference**, returning a `JobStatus` the UI polls. All import endpoints accept a per-item `name` override. Identifiers are quote-escaped (`_q`) — no SQL-identifier injection.
- **STABLE PUBLIC LAYER IDS (`uid`, 2026-07-29)** — every shareable URL addresses a layer by its
  `uid` (`models.new_uid`: 12 hex chars), never the integer PK. SQLite assigns PKs as rowid aliases
  WITHOUT `AUTOINCREMENT`, so deleting the highest-id row frees that id for the next insert: a
  bookmarked `vector-3` would silently return a DIFFERENT dataset — no error, wrong data. (Postgres
  sequences would fix the reuse but not the wider problem: integer keys are only meaningful inside
  one database, so a restore or an instance move renumbers everything.) `common.by_ref(model, ref)`
  is THE resolver — it accepts the uid, the legacy integer id, and either with a `vector-`/`raster-`
  prefix, so links shared before the migration keep working. Applied to the PUBLIC routes only
  (vector `tilejson`/`pmtiles`/`features.*`/`identify`/`parquet`, raster `cog`/`tilejson`, STAC item
  ids, OGC collection ids); authenticated/admin routes stay on the integer id. Contract pinned in
  `test_ogcapi.py` ("Stable public ids").

- `ogcapi.py` — **PUBLIC OGC API - Features (Part 1: Core)** at `/api/ogc` (2026-07-29). The
  **widest-reach read surface we have**: QGIS ("Add OGC API - Features Layer"), ArcGIS Pro, FME and
  anything on GDAL's `OAPIF` driver consume it natively — unlike TileJSON/PMTiles, which only the
  MapLibre family reads (in GeoLibre it is buried under "Add data ▸ OGC API - Tiles (vector)"), and
  unlike STAC, which describes layers rather than serving features. Landing page + `/conformance` +
  `/collections` + `/collections/{cid}` + `/collections/{cid}/items` + `/items/{featureId}`.
  - **One collection per PUBLIC ready vector layer**; ids mirror the STAC item ids (`vector-<id>`),
    so `/api/stac` items carry an `ogc-features` asset + `alternate` link and the two cross-reference.
    Same `is_public` opt-in as STAC — nothing is exposed by default; non-public/non-ready → 404.
  - **Both storage backends.** PostGIS: `ST_AsGeoJSON` + an **index-usable** `geom && ST_MakeEnvelope(…)`
    filter transformed into the TABLE's SRID once (a per-row `ST_Transform(geom)` would drop the GIST
    index), ordered by the id column so offset paging is stable. GeoParquet: `duckdb_engine`
    covering-column + partition pruning, `offset` paging, `query_feature_by_id` for single features.
  - `bbox` (4 or 6 numbers) / `limit` (capped `MAX_LIMIT`) / `offset`, plus `numberReturned`,
    `timeStamp`, `next`/`prev` links. `numberMatched` is **best-effort and omitted when unknown** —
    exact for a bbox query, the stored `feature_count` unfiltered — a wrong count is worse than none.
  - **`CONFORMS` claims Core + GeoJSON ONLY.** No CRS negotiation (everything is CRS84), no CQL2, no
    transactions, no OGC API - Tiles/Records. `test_ogcapi.py` asserts the list EXACTLY: over-claiming
    conformance is the bug this module was written to stop — spec-driven clients trust it and break.
- `stac.py` — **PUBLIC STAC 1.0.0 catalog** (`/api/stac`, + `/conformance`, `/collections`,
  `/collections/{vectors|rasters}/items[/{item}]`, GET `/search?bbox=&collections=&limit=`) — the
  discovery half of the data-access story (notes §0h-addendum; GeoNode-catalog equivalent with zero
  extra services). Lists ONLY `status='ready' AND is_public` layers, generated dynamically from SQLite
  per request (deviation from the static-files-on-MinIO idea: same weight, always in sync, no public
  MinIO plumbing). Items carry ready-to-use assets: raster → raw `cog` (`/vsicurl/`-able) + TiTiler
  XYZ `tiles`; postgis vector → Martin XYZ `vector-tiles`; geoparquet → `manifest` + `features-geojson`
  + `features-arrow` (+ `pmtiles` when tiled). Absolute hrefs from the forwarded Host/X-Forwarded-Proto.
  Consumers: QGIS (native STAC 3.40+/plugin), stac-browser, pystac-client — see `docs/data-access.md`.
  **Hardened 2026-07-29 to back the `ogcapi-features` conformance it claims:** `/collections/{cid}/items`
  now honours `bbox` (4 or 6 numbers), `datetime` (instant or `start/end`, `..` open-ended), `limit`
  + `offset`, and returns `numberMatched`/`numberReturned`/`timeStamp` + `next`/`prev`. `/search`
  gained `datetime` + `ids` and a **POST** twin (pystac-client's default verb — hence `POST` in the
  public-CORS preflight's `Allow-Methods` in `main.py`); its old `break`-inside-inner-loop let a
  multi-collection search overrun `limit`. Every vector item also advertises the OGC API - Features
  collection (`ogc-features` asset + `alternate` link).
- **Per-layer share links** (authed, `data:read`): `GET /data/{vector,raster}/{id}/links` → the
  tool-labelled URL list behind My Data's **Share links** panel, built by `services/share_links.py`.
  Returns `{public, name, catalog, links[]}`; `public` is `is_public`, which the UI turns into a
  "make it Public first" notice — the URLs themselves are the public surface and 404 until then.
- **`GET /data/raster/{id}/tilejson`** — **PUBLIC** TileJSON 3.0 for a shared raster (`is_public`,
  like `/cog`). We emit it ourselves rather than proxy TiTiler's `/cog/…/tilejson.json`, whose
  self-URL is built from the container origin (`http://titiler:8000/cog/tiles/…` — wrong host/scheme,
  missing nginx's `/raster` prefix). Bakes the layer's saved styling into the tile template and
  carries **`bounds`** (from the stored EPSG:4326 bbox; TiTiler `/cog/info` only as a fallback for
  legacy rows) — bounds are the whole point: a bare XYZ URL has none, so "zoom to layer" fails.
- **Sharing endpoints** (authed, editor+): `PUT /data/vector/{id}/sharing` + `PUT /data/raster/{id}/sharing`
  (`SharingUpdate`: partial `{visibility, abstract, keywords, license, attribution}` — legacy
  `is_public` bool still accepted, mapped to visibility). `PUT /data/sources/{id}/sharing` takes
  `VisibilityUpdate` (private|organization — no public tier). `visibility=='public'` is the opt-IN to
  the STAC catalog + raw-COG route; nothing is public by default; portal display endpoints stay
  public-by-id regardless (published portals need them).
- **Portal published access** (`PUT /portals/{id}` `access_type`): `public` | `password` |
  `organization` (any signed-in member) | `owner` (creator + admins). Legacy `private`==organization,
  migrated away in main.py. Portals have NO workspace `visibility` (dropped 2026-07-16; `_get_portal`
  is id-only, all portals workspace-visible).
- **Server-side portal gate** (`GET /portals/authz`, declared BEFORE `/{portal_id}`): the nginx
  `auth_request` target for `location /portals/`. Returns 200 (allow) / 401 / 403 from the portal's
  `access_type` + the **session cookie** (`deps.SESSION_COOKIE` = `gd_session`, resolved by
  `resolve_cookie_user`): public/password/SPA-routes/unknown-slugs → 200; organization → any member;
  owner → creator or admin/owner. nginx bounces a deny to `/login?next=…`. The cookie is set by
  `/auth/login` + `/auth/invitations/{token}/accept`, mirrored for existing sessions by
  `POST /auth/session` (the SPA calls it in `fetchMe`), and cleared by `POST /auth/logout`. **Password
  portals are also server-side**: `authz` 401s until the per-portal `gd_pu_{id}` unlock cookie is set
  by `POST /portals/{slug}/unlock` (bcrypt-verify → signed cookie); `GET /portals/{slug}/gate` gives
  the `/portal-gate` SPA page the access_type so it shows a password box (password) or hands off to
  login (org/owner). Every nginx deny redirects to that single `/portal-gate?next=` page (no nginx
  branching). The old client-side sha256 gate in `portal.js` was removed.
- `data/raster.py` also: **`GET /{layer_id}/cog`** — **PUBLIC** HTTP-Range proxy for the layer's COG,
  **only when `is_public`** (404 otherwise). This is the "WCS replacement": full pixel access in
  QGIS/GDAL via `/vsicurl/https://host/api/data/raster/{id}/cog`, and a direct-download URL.
- `data/__init__.py`, `__init__.py` — package markers.

## Dependencies / relationships
- Depends on `..services` (provisioning, tile URLs, portal generation), `..tasks` (Celery dispatch), `..models`, `..schemas`, `..deps` (auth), `..database`.
- All vector tile URLs handed to the frontend are built by `services.martin.get_tile_url`; raster by `services.titiler.get_tile_url`. If a tile path format changes, change it there, not here.

## Current status & known issues
- `reload-martin` exists because Martin can silently end up with an empty/stale config; the Settings page now has a button that calls it.
- Vector ingest reprojects to EPSG:4326; raster ingest currently does **not** reproject (COG keeps source CRS, e.g. UTM) — TiTiler reprojects on the fly via the TileMatrixSet, but the stored bbox is in source CRS and must be handled carefully by callers computing map bounds. See `tasks/README.md` and notes.
- No rate limiting beyond nginx; no pagination on list endpoints (fine at current scale).

## Last updated
2026-07-30 (public `GET /portals/{slug}/catalog` feed for catalog portals scoped to "all public")
2026-07-30 (deployment history + log options for the consolidated Infrastructure panel)
2026-07-30 (new `backups.py` — destination config, history, manual run)
2026-07-30 (activity log paginated + server-side filters/date range + `/audit/actions`)
2026-07-29 (stable public `uid` on layers + `common.by_ref`; SQLite WAL/busy_timeout)
2026-07-29 (INTEROP: new `ogcapi.py` = OGC API - Features Core/GeoJSON at `/api/ogc`; raster
TileJSON; per-layer `/links` (share links) via the new `services/share_links.py`; STAC items +
`/search` brought up to the conformance they claim, + POST search. `main.py`'s `_PUBLIC_CORS` regex
now covers `/api/ogc/*` and its preflight allows POST. Tests: `test_ogcapi.py`.)
2026-07-16 (A-02 per-resource sharing: `visibility` axis private/organization/public on layers +
sources + portals; `common.visible_to(user, Model)` now enforces it in lists + authed by-id lookups;
`is_public` folded in as a derived write-only-synced flag via `common.apply_sharing`; new source +
portal sharing endpoints; `_get_portal` takes `user`. Tests: test_sharing.py + test_migrations
visibility cases. Public display surface untouched.)
2026-07-16 (RBAC A-01: shared-workspace permission model — see the section above. New `users.py` +
`common.py`; auth.py invitation/password flows; all mutating routes editor-gated with id-only
lookups; discover de-dup made instance-wide; vector delete's Martin regen now includes ALL members'
ready postgis layers, not just the deleter's. Tests: test_rbac.py, test_users.py, test_migrations.py.)
2026-07-14 (SECURITY: `setup.configure-db/-storage` now require an admin token once setup is
completed — `_guard_setup_mutation` — closing an unauthenticated config-tampering hole. Vector
display endpoints — `features.arrow/.geojson`, `identify`, `pmtiles`, `parquet/{path}` — now serve a
layer only when `is_public` OR it is in a PUBLISHED portal (`_publicly_readable` + a cache invalidated
on publish/unpublish/share/delete in vector.py and portals.py); previously any layer was readable by
id. Regression tests in `api/tests/test_security.py`.)
2026-07-11 (identify endpoint; CSV WKT geometry; large-vector direct upload + convert; GeoParquet discovery/import; export-bundle resolves geoparquet layers)
