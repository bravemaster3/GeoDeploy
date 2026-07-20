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
- `stac.py` — **PUBLIC STAC 1.0.0 catalog** (`/api/stac`, + `/conformance`, `/collections`,
  `/collections/{vectors|rasters}/items[/{item}]`, GET `/search?bbox=&collections=&limit=`) — the
  discovery half of the data-access story (notes §0h-addendum; GeoNode-catalog equivalent with zero
  extra services). Lists ONLY `status='ready' AND is_public` layers, generated dynamically from SQLite
  per request (deviation from the static-files-on-MinIO idea: same weight, always in sync, no public
  MinIO plumbing). Items carry ready-to-use assets: raster → raw `cog` (`/vsicurl/`-able) + TiTiler
  XYZ `tiles`; postgis vector → Martin XYZ `vector-tiles`; geoparquet → `manifest` + `features-geojson`
  + `features-arrow` (+ `pmtiles` when tiled). Absolute hrefs from the forwarded Host/X-Forwarded-Proto.
  Consumers: QGIS (native STAC 3.40+/plugin), stac-browser, pystac-client — see `docs/data-access.md`.
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
