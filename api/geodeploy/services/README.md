# api/geodeploy/services/

## Purpose
The "hard parts" GeoDeploy hides from users: provisioning Docker containers, generating tile-server config, building tile URLs, COG conversion, and assembling published portals. Most tile-serving bugs live or die here.

## Contents
- `postgis.py` — provisions the PostGIS container (random password, named volume `geodeploy_postgres`, `postgres` network alias), waits healthy, writes the initial Martin config, and starts the Martin container. Also `create_user_schema`, `test_connection`. Exposes constants reused elsewhere: `MARTIN_NAME`, `MARTIN_IMAGE`, `NETWORK`, `_get_host_bind_path`.
- `minio.py` — **`browser_upload_url(key)`** returns a presigned PUT URL the *browser* can reach: for the local MinIO (internal hostname) it strips scheme+host and returns a same-origin `/s3/...` path that nginx proxies with the **signed Host preserved** (SigV4 still verifies, no CORS); for an external/public endpoint it returns the full presigned URL (bucket must allow cross-origin PUT). Used by the GeoParquet direct-upload flow. Also provisions the MinIO container (named volume `geodeploy_minio`, `minio` alias), ensures the bucket, and **starts the TiTiler container** via `_start_titiler()`. `_start_titiler` strips the `http://` scheme from the endpoint for GDAL's VSI S3 (`AWS_S3_ENDPOINT` must be `host:port`), **derives `AWS_HTTPS` from the endpoint scheme** (so a real HTTPS S3 works, not just the local HTTP MinIO) + sets `AWS_REGION`, and always recreates the container so credential changes take effect. **`restart_titiler(endpoint, key, secret, region)`** is the public entry the *existing-storage* setup branch calls (`routers/setup.py`) — the local branch goes through `provision_local()` instead. `AWS_VIRTUAL_HOSTING` stays `FALSE` (path-style works for MinIO/R2/B2/Hetzner and AWS).
- `martin.py` — `regenerate_config(layers)` rebuilds `martin-config.yaml` and reloads Martin (restart, else create the container if missing). Per-layer `geometry_column`/`id_column`/`srid` (`_srid_from_crs` parses `EPSG:N` from the layer's `crs`) so **imported** tables that don't use GeoDeploy's `geom`/`id`/4326 conventions still serve; ingested layers default to geom/id/4326. **Martin is now a core always-on service** (no compose profile; started by `install.sh`; boots from a sources-less config written by `main.py::_ensure_martin_config` until a DB exists) — so both local and external PostGIS serve tiles without manual intervention. `get_tile_url(schema, table)` → `/tiles/{schema}.{table}/{z}/{x}/{y}`. The connection string + `_attach_properties`'s asyncpg connect use `settings.postgis_*` and append `?sslmode=` when `postgis_sslmode` is set (external/managed DBs; empty for the local DB). **Config notes:** (1) `listen_addresses` is top-level (Martin v1.x); the old `srv:` key is ignored. (2) `_attach_properties()` queries `information_schema.columns` and writes a `properties` map per table — **required**, because a configured Martin table source with no `properties` serves geometry only (feature popups would show no attributes). (3) Reload does a full **container restart**, not SIGHUP — Martin only builds table field/property definitions at startup, so SIGHUP leaves `vector_layers[].fields` empty after a config change.
- `titiler.py` — `get_tile_url(s3_key, colormap, rescale, algorithm, zfactor, bidx)` → `/raster/cog/tiles/WebMercatorQuad/{z}/{x}/{y}?url=s3://...` (the `WebMercatorQuad` TileMatrixSet segment is **required** by the current TiTiler API). Supports `rescale` ("min,max" stretch); `algorithm` (e.g. `hillshade`, single-band; hillshade adds `expression=b1*{zfactor}` for vertical exaggeration); `bidx` (list of 1-based band indices → `&bidx=` per band: one band = single-band output, three = RGB composite); and `colormap_name`. Colormap is dropped for an RGB composite (`len(bidx)==3`) or when an algorithm is active; algorithm and colormap are mutually exclusive. `get_tilejson_url` uses `/cog/WebMercatorQuad/tilejson.json`. `COLORMAPS` list.
- `share_links.py` (2026-07-29) — the ONE place that knows **which artifact a given layer should be
  consumed through, and how a human pastes it into their tool**. `vector_links(layer, base)` /
  `raster_links(layer, base, default_style)` return tool-labelled entries
  (`{id, label, url, format, tools[], hint, primary, download}`) for the `GET
  /data/{vector,raster}/{id}/links` endpoints behind the My Data **Share links** panel.
  **Order is the recommendation**, and it leads with **OGC API - Features** (`/api/ogc`) for vectors
  — the only read standard QGIS/ArcGIS/FME/GDAL all speak natively; TileJSON/PMTiles follow, labelled
  as *rendering* paths (they only reach MapLibre-family clients — in GeoLibre, TileJSON hides under
  "Add data ▸ OGC API - Tiles (vector)", which is why the panel spells the menu path out). Rasters
  lead with the raster TileJSON (it carries `bounds`, so "zoom to layer" works) then `/vsicurl/` COG.
  `request_base(request)` is the shared https-aware origin helper. **Keep in sync with
  `routers/stac.py`'s asset list** — same artifacts, machine-readable shape.
  **THREE consumers, one source (2026-07-29):** the dashboard panel (`ShareLinksModal.vue`), the
  STAC item, and a PUBLISHED portal's About page (`portal_generator._layer_info` → `_share_block`).
  The renderers differ (Vue vs static HTML) but the link data must not — add an artifact here and
  all of them get it. For the About page the links are baked with **`ORIGIN_TOKEN`** as the base,
  because the public host isn't known at publish time; the page's inline script swaps it for
  `location.origin` on load. That token approach (rather than root-relative URLs) is what keeps
  `/vsicurl/<origin>/…` and `pmtiles://<origin>/…` correct, where the origin sits mid-string.
  The About page also gets a **toolbar** (`_layers_section`, from 6 layers up): a search box +
  kind chips (Vector / GeoParquet / Raster, only the kinds actually present), ANDed together and
  matched client-side against `data-search` (name + abstract + keywords + geometry + CRS) and
  `data-kind` baked onto each card — no request, works on the static page. Both are block children
  of `.wrap`, so the toolbar spans exactly the card column's width.
  **Layout decisions (2026-07-30, from user feedback on the live page):** the card grid is
  deliberately **one column** — at 2-up an expanded card stretched its row-mate and cramped the long
  URLs; the share `<details>` blocks are an **accordion** (opening one closes the rest) so the page
  never becomes a wall of URLs; `.abstract` is full-width + justified (it was not filling the card);
  and the link glyph is the literal 🔗 **character, not a CSS `F517` escape** — the backslash was
  being eaten on the way out of the Python f-string and rendered as a literal "F517".
  `.doc img` is block/centred/`max-width:100%`, and `![alt|full](src)` opts an image into filling
  the column (`_md_inline` strips the marker from the alt text).
- `setup_errors.py` (2026-08-02) — turns a driver exception into a sentence naming the CAUSE and
  the next command. The wizard used to surface the raw error, so a firewalled port, a closed one,
  wrong credentials and a missing PostGIS extension all read as "Cannot connect" — and only two
  of those relate to what was typed. Timeout vs REFUSED is the key split: refused means
  something answered. Unclassified errors keep their original text.
- `backup.py` (2026-07-30) — copies everything non-regenerable to a **separate** object store.
  `verify_destination` REFUSES a destination whose endpoint+bucket match the live data bucket (a
  copy that dies with the original is not a backup). `copy_objects` uses **server-side
  `copy_object`** when destination and source are the same provider, so a multi-hundred-GB instance
  never streams its bytes through this container; cross-provider falls back to streaming.
  `snapshot_state_db` uses SQLite **`VACUUM INTO`**, never a file copy — the DB is written during
  the backup and in WAL mode the newest commits sit in the `-wal` sidecar, so a plain copy can
  restore corrupt or stale. `dump_postgis` shells to `pg_dump -Fc`; the client is pinned to the
  server major in `api/Dockerfile` (**bump both together with `postgis.IMAGE`** — pg_dump refuses a
  newer server). `list_runs` reads the destination's own `manifest.json` files rather than our DB,
  because the state DB is itself one of the things being backed up. `prune` only ever counts
  COMPLETE backups toward retention, so a run of failures can't age out the last good copy.
  Restore is deliberately NOT automated — procedure in `docs/backups.md`.
  `verify_destination` raises **`BucketMissing`** (not a plain `ValueError`) when the provider
  answers `NoSuchBucket` — authoritative good news, since answering at all means the signature was
  accepted. `create_destination_bucket` acts on it: same live-bucket refusal, `LocationConstraint`
  sent only where it is legal (never us-east-1, never R2's `auto`), idempotent on
  `BucketAlreadyOwnedByYou`, and it re-verifies afterwards because creating a bucket says nothing
  about being allowed to write into it.
- `external_sources.py` — third-party services shown in portals **without ingesting**. `kind_for` (xyz/wms→raster, wfs→vector), `tile_url` (xyz template as-is; wms → GetMap KVP with MapLibre's `{bbox-epsg-3857}` token), `features_url` (the public GeoJSON proxy path), `probe_wfs` (fetch 1 feature on add → geometry type + bbox; validates), `fetch_wfs_geojson` (proxy fetch, 5k-feature cap). Consumed by `routers/data/sources.py` + `portal_generator`.
- `cog_converter.py` — rasterio-based: `is_cog`, `convert_to_cog` (512×512 tiles, overviews 2–64, LZW + dtype-aware predictor, **`BIGTIFF=IF_SAFER` on BOTH writes** — see below), `inspect` (local file → CRS/bbox/bands/nodata; bbox reprojected to 4326 via shared `_read_meta`), `inspect_s3` (same but reads an existing object's header over S3 for the "import existing data" flow).
- `oidc.py` (A-04 SSO) — generic OpenID Connect via **Authlib**. `get_oidc_config(db)` reads the admin-set `SetupConfig.oidc_*` (client secret decrypted by EncryptedText; None when not enabled/complete). `build_oauth(cfg)` registers a fresh Authlib client per-request (config is dynamic). **`resolve_user(claims, cfg, db)` is THE account-linking/provisioning policy (unit-tested):** link by `oidc_sub` then VERIFIED email; no account → create only if `auto_provision` + domain allow-listed, with `default_role`; else raise `OidcError` (user-facing). SSO-created users get a RANDOM bcrypt hash (a non-bcrypt placeholder would make passlib.verify RAISE in the login path). Consumed by `routers/auth_oidc.py`.
- `portal_generator.py` — `generate_style()` returns user sources+layers (with `geodeploy:*` metadata for the layer switcher) and a merged bbox; `build_portal_bundle(..., initial_view=)` bakes the admin's pinned center/zoom into `style.geodeploy.view` (the runtime `jumpTo`s it instead of `fitBounds` when present) and injects the **shared runtime** (`{{PORTAL_CSS}}`/`{{PORTAL_JS}}` from `templates/shared/`, falling back to `shared/layout.html`) + the template basemap + user style into a full MapLibre style, writing `data/portals/{slug}/index.html` + `style.json` by substituting `{{TITLE}}`, `{{STYLE_JSON}}`, `{{THEME_CSS}}`, `{{POPUP_CONFIG}}`, `{{ACCESS_TYPE}}`, `{{PASSWORD_SHA256}}`. **Key behaviours (mirror these in `PortalEditor.vue::buildPreviewStyle`):** layers are built in **reverse** so `layer_configs[0]` = top of list = drawn on top; `cfg.visible === false` bakes `layout.visibility:none` (via `setdefault` so it doesn't clobber a symbol layer's `layout`); **point layers are emitted as `symbol` layers** with `icon-image: gd-pt-{id}` + `geodeploy:marker/markerColor/markerSize` metadata (the runtime/editor draw the canvas icon — circle/square/triangle/diamond/star/cross); line `lineType` → `line-dasharray` (dashed `[2,1.5]`, dotted `[0.4,1.8]`). **GeoParquet layers are NOT emitted as MapLibre layers** — they're collected into `user_data["deck_layers"]` → baked into `style.geodeploy.deckLayers` (id/name/geometry/style/opacity/bbox/visible) for the portal.js deck.gl overlay; a layer explicitly tiled (ready PMTiles) instead emits a `pmtiles://` vector source and follows the normal vector path. Either way the layer's bbox still feeds the fit. **Deck-only anti-flash bake (2026-07-16):** a portal whose only user layers are deck.gl GeoParquet overlays (no MapLibre layers, no pinned view) used to fit the FULL extent then snap once to the manifest **core** extent when the manifest loaded (a visible flash). `read_deck_core_bbox(s3_key)` now reads each such layer's `manifest.json` `grid` (best-effort, one small S3 GET at publish; `_rebuild_bundle` in `routers/portals.py` collects them into `deck_core_bounds` and passes them in); `generate_style` bakes the merged core extent into `geodeploy.bounds` and sets `core_fitted` → `geodeploy.coreFitted`, so portal.js opens there directly and **skips its refit**. A miss (manifest unreadable / non-4326 grid) falls back to the full bbox (today's behaviour) and portal.js's refit — now a gentle glide, not a snap. **Basemap no-swap (2026-07-17):** when the builtin base source is successfully repointed to the chosen basemap, `build_portal_bundle` also sets `geodeploy.baseRepointed=True` so portal.js skips its redundant on-load `selectBasemap` swap (another flash). **Layer catalog (V-13, 2026-07-20):** `generate_style(…, layer_groups=)` — when a portal has a nested folder TREE, layers draw in the depth-first flatten of the reconciled tree (`_reconcile_layer_tree` drops dangling nodes + appends missing configs; `_flatten_layer_tree` gives draw order), and the tree is baked to `style.geodeploy.layerTree` for portal.js's grouped switcher. No tree → flat `layer_configs` order (back-compat). Structure lives in `Portal.layer_groups`; per-layer STYLE stays in `layer_configs`. **Template Experiences (V-11, 2026-07-21):** `resolve_layout(config)` turns `Portal.layout_config` (`{archetype, regions, panels}`, nullable) into a full manifest — archetype defaults (`webmap`/`storymap`/`catalog`; `webmap+catalog` is unbuilt and aliases to `webmap`) deep-merged with per-portal overrides — baked into `style.geodeploy.layout` (always present; None → `webmap` = pre-V-11 shell). `build_portal_bundle(…, layout_config=, story=)` also bakes `Portal.story` (`{sections:[{title,body,view,layers}]}`) into `style.geodeploy.story` when it has sections. **PARITY: the archetype-defaults table + merge in `resolve_layout` is mirrored in `portal.js::resolveLayout` and `PortalEditor.vue::resolveLayout`** — change all three together. **Pinned by `tests/test_portal_layouts.py`, which states the webmap + storymap manifests LITERALLY** (not derived from the table — a test computing its expectation from the table it checks would accept a wrong table); a failure there means an existing portal's layout changed. **Catalog archetype (V-14, 2026-07-30):** `catalog` resolves to a browse layout (`panels.catalog=True`, `panels.layerCatalog=False` — the facet rail replaces the switcher) with `regions.catalog = {scope, mapSide, mapWidth, railWidth, perPage}`. When `panels.catalog` is set, `build_portal_bundle` also bakes `layers_info` into `style.geodeploy.catalog` (only for this archetype — it is a few KB per layer and webmap/storymap never read it). `_layer_info` now carries `layer_id` so a catalog card can join to the map layer's `metadata['geodeploy:layer_id']`.
- `duckdb_engine.py` — in-process DuckDB (httpfs + S3, path-style). **`inspect_parquet(location, creds)`** is the GeoParquet equivalent of `cog_converter.inspect_s3`: reads a local-or-`s3://` file's `geo` metadata (primary geometry column, CRS, bbox, geometry_types), columns, and feature count, returning bbox reprojected to EPSG:4326 (`pyproj`). **GOTCHA — does NOT load the DuckDB spatial extension:** spatial's GeoParquet decoder rejects files tagged with spec versions it doesn't know (e.g. `2.0-dev`), and that check fires on `read_parquet` the moment spatial is loaded (`InvalidInputException: Geoparquet version 2.0-dev is not supported`). So `_connect_read()` loads httpfs only → geometry comes back as raw **WKB bytes**, and geometry type / fallback bbox are computed with **shapely** (`from_wkb`/`total_bounds`), independent of the declared spec version. Prefers metadata `geometry_types`/`bbox` so a multi-GB file needs no geometry scan; only scans (shapely) when metadata is absent and the file is under `_BBOX_SCAN_CAP`. Creds are **SQLite-sourced** (Celery env unreliable — §0f). Used by `tasks/geoparquet_import.py`. **`query_features_geojson(s3_key, bbox, limit, creds)`** is the deck.gl viewport feed: filters by the EPSG:4326 `bbox` using the GeoParquet **covering bbox** column (`struct_extract` on plain numerics → DuckDB row-group pruning, so a multi-GB file isn't fully scanned per view; no covering column → falls back to first-N), reads WKB without spatial, and returns a GeoJSON FeatureCollection in 4326 (reprojecting via pyproj when the file CRS differs; `_jsonable` coerces dates/Decimal, drops blob props). `get_connection`/the old spatial-loading path are gone from these flows. **`stream_geojsonseq(s3_key, out, creds)`** streams the whole file as newline-delimited GeoJSON (batched, 4326) into a writable (tippecanoe's stdin) for the PMTiles tiling job — no giant temp file. **The shapely conversion is vectorised per fetch-batch** (`from_wkb`/`to_geojson`/`shapely.transform` reproject run once per ~20k-row batch in C, releasing the GIL; one `out.write` per batch) — this is the tiling bottleneck on multi-million-feature files, so don't regress it to a per-feature Python loop. Logs progress every `log_every` features (count/elapsed/feat-per-sec) so a long run is observable in the celery log. **This is now the tiling FALLBACK, not the primary feed** — the primary is `export_geoparquet_to_fgb` (below). **Memory-bounded (2026-07-11):** both feeds set `memory_limit` (default 1 GB, was DuckDB's default 80 % of RAM → OOM-killed the 20 M-polygon tiling on a 7.7 GB box beside tippecanoe) + a per-run on-disk spill dir, so tiling scales to any feature count in bounded RAM on a cheap VPS (cost is time, not RAM). Also fixed a `loc` NameError in the start-of-stream log line (undefined var → crash before the loop). **`export_geoparquet_to_fgb(s3_key, out_path, creds, memory_limit, threads)` (2026-07-11)** is the FAST primary tiling feed: a single streaming DuckDB `COPY (… ST_GeomFromWKB/ST_Transform …) TO out.fgb (FORMAT GDAL, DRIVER 'FlatGeobuf', SRS 'EPSG:4326')` using the baked **`spatial`** extension, so geometry is converted natively (no per-feature shapely/pyproj funnel — the old bottleneck) and tippecanoe reads binary. Drops the GeoParquet 1.1 covering STRUCT column (GDAL can't write a struct); reprojects only when the source CRS ≠ EPSG:4326; memory-bounded like the stream. Raises when `spatial` isn't loadable so `pmtiles_tile` falls back to the GeoJSONSeq stream. **SUPERSEDED as the tiling feed (2026-07-12)** by **`stream_tiling_geojsonseq(s3_key, out, creds, simplify_tol=, …)`** — the current PRIMARY feed: DuckDB (spatial) converts geometry to GeoJSON and applies **display-only** `ST_Simplify` (tolerance sized to ~1 tile-unit at the max zoom), streamed to tippecanoe's stdin so the feed **overlaps** tiling (the FGB write serialized it — a net loss on heavy geometry). Simplification touches ONLY the tiles; `query_features_geojson`/download/identify still read the source `.parquet` at full resolution. Memory-bounded; raises → shapely `stream_geojsonseq` fallback. **`partition_with_covering(s3_key, creds, out_prefix=None, …, partition_grid=16, extent_quantile=0.005)`** (requires **pyarrow**; REPLACED `sort_with_covering` 2026-06-12 — the out-of-core total Z-order sort hung for hours on 9.5 M large polygons) rewrites a GeoParquet as a **spatially-partitioned dataset**: a single-pass scatter into a `partition_grid`² grid, written as a PREFIX of **`__cell=N/*.parquet`** hive files, each carrying GeoParquet 1.1 `bbox` covering metadata + a custom **`geodeploy:partition`** key (grid minx/miny/spanx/spany/size — read back by `query_features_geojson` for partition pruning). **WKB is parsed at most ONCE** (vectorised Arrow UDF `gd_bbox`, sub-chunked by `bbox_chunk`; an existing covering column skips the parse entirely). **Grid extent is percentile-based** (`approx_quantile`, `PREP_EXTENT_QUANTILE` default 0.005) so outliers (e.g. overseas territories) don't collapse the dense bulk into 1–2 huge cells. **Partitions are written to a LOCAL dir then uploaded** (DuckDB's `COPY … PARTITION_BY` direct to `s3://` buffers per open partition file → OOM); per-run spill dir + `max_temp_directory_size` cap (DuckDB misreads free space on overlay/WSL). Output goes to a NEW `parts-<hex>/` prefix (read old → write new → delete old; the prep task repoints `layer.s3_key`). `_parquet_paths()` resolves single-file vs prefix for `inspect_parquet`/`query_features_geojson`/`stream_geojsonseq` (`**/*.parquet` glob, `hive_partitioning=false` so `__cell` doesn't leak into data); all three **exclude the covering column** from catalog columns/properties. `query_features_geojson` prunes twice: covering-column bbox predicate (row groups) AND `__cell IN (…)` over the grid metadata (+1-cell pad, `hive_partitioning=true`) so a small-bbox query opens only overlapping partition files (was: all 368 files over S3, 16 s for 797 features); WKB→GeoJSON + reprojection are vectorised over the whole result; viewport cap 50k. **`query_features_at_point(s3_key, lng, lat, tol, limit, creds)` (2026-07-11)** is the identify-on-click feed: same covering + partition pruning over a tiny `tol`-degree box around the clicked point, then an exact shapely `intersects` test in the file's CRS, returning attribute dicts only (no geometry) — this is what gives deck.gl-rendered GeoParquet layers popups (the GeoArrow viewport transport is geometry-only by design). Served by the PUBLIC `GET /data/vector/{id}/identify`. **`build_manifest(s3_key, creds)`** describes a partitioned dataset for the browser-side duckdb-wasm reader (§0h-addendum-2 phase 1): grid, CRS, covering column, columns, and cell→object-key map with per-file row counts from the footers (no data scan) — uploaded as `manifest.json` under the prefix by `tasks/geoparquet_prep._write_manifest` and served through the public `GET /data/vector/{id}/parquet/{path}` range proxy (`routers/data/vector.py::vector_parquet_object`, pmtiles-style boto3 Range → 206; partition files get `immutable` cache headers since a re-prep mints a new prefix). `portal_generator` bakes `deckLayers[].parquet = {manifest, base}` (root-relative) for prepped layers. **`_read_geo_metadata` resolves a glob to ONE concrete file first** (`_meta_probe_path`; `parquet_kv_metadata` over the glob opened all 370 footers over S3 = 15.3 s per query, which was eclipsing the pruning) **and caches the parsed metadata per path** (`_GEO_META_CACHE` — safe because a re-prep writes a NEW `parts-<hex>` prefix; only successful parses are cached). Verified 2026-07-09: small-bbox endpoint 1.5 s warm (was 16 s), low-zoom 504s gone. **httpfs + spatial are BAKED into the api image** (build-time download to a fixed `DUCKDB_EXTENSION_DIR`, LOAD-verified; runtime INSTALL only a fallback — the extension CDN is too flaky for first-use download, and the per-HOME cache dies on container recreate). `spatial` is what lets `export_geoparquet_to_fgb` convert geometry natively; a fresh `docker compose build` is required to pick it up. NOTE: a file prepped before 2d77499 lacks the grid metadata → re-prep (`POST /data/vector/{id}/prepare`).
- `geolibre_import.py` (GeoLibre interop — Front 1 SPIKE, 2026-07-27) — parses a GeoLibre
  `.geolibre.json` project and produces a GeoDeploy **import plan** (`import_project` →
  `{portal, layers[], warnings[]}`). Pure/infra-free: translates each `LayerStyle` → MapLibre paint
  (single/graduated `step`/categorized `match`/expression/rule-based `case`, `fill-extrusion`),
  detects 3D-Z → `elevation3d` (deck), maps COG→raster style + XYZ/WMS/PMTiles→external tiles, and
  view/basemap/storymap. Each layer carries a `source_identity` for the future write-back round-trip.
  **Mirrors GeoLibre's own `style-mapper.ts`/`vector-color.ts`** (their `@geolibre/*` packages are
  private, so we re-implement, not import). Also `plan_to_layer_configs(plan, id_map)` +
  `plan_to_portal_kwargs(plan, id_map)` (pure): once ingestion resolves each GeoLibre layer to a
  GeoDeploy id, these emit the exact `layer_configs` (raw paint carried in `style.maplibre`) + portal
  kwargs (title/initial_view/story with remapped `type:id` refs) that `build_portal_bundle` consumes.
  3D-Z layers render **flat** now (data visible) and carry `render_mode:elevation3d` + params for
  Front 2. `external_source_spec(plan_layer)` maps a tile layer → `ExternalSource` kwargs (xyz/wms;
  others warn). Full plan: `notes_temp/GEOLIBRE_INTEROP.md`. Tested by `api/tests/test_geolibre_import.py`.
  **Publish path (2026-07-27, needs stack validation):** `routers/interop.py` — `POST /interop/geolibre/
  preview` (dry-run) + `POST /interop/geolibre/publish` (creates a VectorLayer/UploadJob per vector
  layer with its geojson to a temp file, ExternalSource per xyz/wms tile layer, the Portal shell with
  translated `layer_configs`, then hands off to `tasks/geolibre_publish.publish_geolibre_project`, which
  runs each `ingest_vector.apply()` synchronously and finalizes via the router's async `_rebuild_bundle`
  + `published=True`). **COG rasters** are wired too: the endpoint creates a RasterLayer + job, the
  worker downloads the https COG URL (size-capped; SSRF: https-only + private/loopback/link-local
  block via `_assert_public_https`) → the existing GeoTIFF→COG→MinIO `ingest_raster`. Layer z-order is
  REVERSED on import (GeoLibre layers[0]=bottom → GeoDeploy top-first).
- **3D-Z rendering (Front 2, 2026-07-27):** a GeoLibre `elevation3d` layer is NOT ingested to PostGIS
  (Martin MVT flattens Z); `geolibre_import._elevation_config` emits a `layer_type:"elevation"` config
  carrying the geojson INLINE + `{vertical_scale, offset}`. `generate_style` turns it into a deck.gl
  descriptor (`deckLayers[]` with `elevation`+`geojson`, synthetic `elev-N` id); `portal.js` preloads
  it (Z transformed by scale·z+offset) and renders via the existing GeoJsonLayer, which draws 3D
  coordinates at altitude — no viewport fetch. Mirrored in `PortalEditor.makeElevationDeckLayer`; the
  portal opens tilted (initial_view pitch). Publish skips ingest for these. Inline geojson suits small
  GeoLibre tracks (large = follow-up).
- **`portal_generator.generate_style` raw-paint passthrough (GeoLibre interop, 2026-07-27):**
  `_vector_layers(source_id, layer, cfg)` emits N MapLibre layers when `cfg.style.maplibre.layers` is
  present (fill + outline, extrusion, …) wired to the layer's Martin source/source-layer, else the
  single friendly-key `_vector_layer`. The first sub-layer carries the `geodeploy:*` switcher metadata;
  the rest carry only `geodeploy:layer_id` (+`geodeploy:part`). Raster block merges an optional
  `style.paint` (GeoLibre brightness/contrast/etc.). **PARITY TODO:** mirror the passthrough in
  `PortalEditor.vue::buildPreviewStyle` and make portal.js's visibility toggle target ALL layers
  sharing a `geodeploy:layer_id`, not just the first. Tested in `api/tests/test_portal_experiences.py`.

## Dependencies / relationships
- `postgis.py`/`minio.py` talk to the Docker daemon (`docker.from_env()`) and reuse each other's constants. They are called from `routers/setup.py`.
- `martin.py` is called from `routers/data/vector.py` (on upload/delete), `routers/admin.py` (manual reload), and `tasks/vector_ingest.py` (after ingest).
- `titiler.py` is called from `routers/data/raster.py` and `portal_generator.py`.
- `portal_generator.py` reads `templates/` (mounted at `/templates`) and writes `data/portals/`.
- `cog_converter.py` is called from `tasks/raster_ingest.py`.

## Current status & known issues
- **Tile URL formats are version-coupled to Martin and TiTiler `:latest` images.** The TiTiler `WebMercatorQuad` path segment and the Martin top-level `listen_addresses` were both breaking changes discovered this session. If raster/vector tiles 404 after an image bump, re-verify these paths first (see `notes_temp/notes_for_future.md`).
- GDAL needs `AWS_S3_ENDPOINT` **without** scheme; both `minio.py::_start_titiler` and `routers/setup.py` strip it. The compose file reads `${TITILER_S3_ENDPOINT}` for the same reason — keep all three in sync.
- **Existing/external storage + DB (2026-06-04, implemented but UNTESTED on real providers):** the "connect to existing" wizard branches now configure TiTiler HTTPS (`TITILER_AWS_HTTPS`/`_start_titiler` derive it from the endpoint) and PostGIS `sslmode` (`postgis_sslmode`, wizard sets `prefer` for external). Verify on a real AWS/R2/B2 + managed Postgres before trusting. The local provisioned path is unchanged (http → `AWS_HTTPS=NO`, local DB → no sslmode). See `notes_temp/notes_for_future.md` (DONE 2026-06-04 external).
- `martin.py` and `minio.py` start containers programmatically (Docker SDK) AND those services exist in `docker-compose.yml` under profiles. Mixing the two can cause name conflicts / lost network aliases — see the "profile management" note in notes_temp.
- All tile URLs returned are **root-relative** (`/tiles/...`, `/raster/...`); callers that feed MapLibre must make them absolute (MapLibre's web worker can't resolve relative URLs). Done in `portal_generator` output consumer (`layout.html`) and `PortalEditor.vue`.

- `duckdb_engine.py` additions (2026-07-29, for OGC API - Features): `query_features_geojson(…,
  offset=)` pages the same pruned scan (`LIMIT n OFFSET m`) — deterministic for a given filter, not a
  snapshot, so a re-prep mid-crawl can shift rows; and **`query_feature_by_id(s3_key, id_col, value)`**
  returns ONE feature by an id-like column (`CAST(col AS VARCHAR) = ?`, so int/text/uuid ids all work
  through one path). Parquet has no index — it is a scan with `LIMIT 1`, fine for the single-feature
  permalink it serves, never a substitute for the bbox queries.

## Last updated
2026-08-17 (**new `pmtiles_reader.py` — one tile out of an archive, so tiled layers can be served as
ordinary XYZ.** PMTiles v3 read-only, written by hand rather than pulled in as a dependency (fixed
127-byte header, varint directories, a Hilbert curve, all frozen by the spec). Every read is an HTTP
Range through a caller-supplied `fetch(offset, length)`; the header + root directory are cached per
object key, so a tile costs ONE range read in the steady state. `forget(key)` on a re-tile.
**Why it exists, measured:** GDAL's PMTiles driver is not viewport-driven — it presents the archive
as one dataset, so a client's opening questions (feature count, extent) walk every tile at the
deepest zoom. On geodeploy-lite a FIVE-FEATURE layer's archive holds 2,171,238 tile entries across
z0–13 (tippecanoe's `--extend-zooms-if-still-dropping`), which is why QGIS hung worst on small
layers. Consumed by `routers/data/vector.py::vector_pmtiles_tile` + `vector_tilejson`.
`share_links.py`: a tiled GeoParquet layer now leads with **TileJSON**, not the archive — the archive
link stays, relabelled for MapLibre/GDAL-copy use with a warning against opening it in QGIS.)
2026-08-07c (**a 504 on one tile hangs the whole portal.** `bounds` stops MapLibre
asking for tiles that MISS a raster; it does nothing about a tile that HITS it and spans a continent.
A drone orthomosaic a few hundred metres across was still requested at z3 — one tile covering most of
Europe — and TiTiler took long enough that nginx answered 504. MapLibre waits on it, the portal's load
handler never completes, and the page sits on the loading screen until the 15s backstop: the whole
portal held up by one request. New `portal_generator._min_zoom_for(bounds)` writes a source `minzoom`
from the extent (a tile spans 360/2^z degrees, so the layer fits one tile at log2(360/width);
`_MINZOOM_SLACK` = 4 levels of "visible speck" before we stop asking). Continent-sized layers get 0 =
unrestricted. Mirrored in `PortalEditor.minZoomFor`. Pinned by `api/tests/test_raster_minzoom.py`.
NOTE this is a heuristic on EXTENT, not the COG's real overview range — TiTiler `/cog/info` knows the
true min/max zoom and storing it at ingest would be the exact fix.)
2026-08-07b (**hillshade rendered nothing from the portal editor, because the layer's own stretch was
applied to it.** TiTiler applies `rescale` AFTER the algorithm, and a hillshade is already a finished
0–255 relief image — so stretching it with the SOURCE data's range saturates every pixel to one
value. Measured on a vegetation index with range 0.5563–0.9477: `algorithm=hillshade` returns a
15505-byte tile, `algorithm=hillshade&rescale=0.5563,0.9477` returns 623 bytes of uniform colour.
`titiler.get_tile_url` now skips `rescale` when the algorithm is `hillshade` — the same reasoning
that already dropped `colormap`, which had simply been missed. **Scoped to hillshade on purpose:** an
index-style algorithm outputs a range of its own and still wants stretching, so this must not become
"any algorithm". Why it looked like an EDITOR bug: `portal.js::applyRaster` rebuilds the tile URL
from scratch and drops the baked rescale, so the published legend's checkbox worked — the same
option, two paths, one accidentally right. Mirrored in `PortalEditor.rasterTilesUrl` + `portal.js`,
and `LayerPanel` now disables the stretch inputs under hillshade. Pinned by
`api/tests/test_raster_hillshade_url.py`.)
2026-08-07 (**a 3 GB raster died in `build_overviews`, and the real limit was the FILE FORMAT.**
A classic TIFF cannot pass 4 GB — 32-bit header offsets — and GDAL does not refuse the job up front,
it fails part-way through with `TIFFAppendToStrip:Maximum TIFF file size exceeded`, which reached us
as a rasterio `CPLE_AppDefinedError` out of `build_overviews`. Misleading place to land: the
overviews only ADD about a third, so they tip a large-but-legal raster over rather than being the
fault. `convert_to_cog` now sets `BIGTIFF=IF_SAFER` on **both** writes — and the TEMP copy is the one
that mattered, since overviews are written into it in `r+` mode so its format is fixed at creation
and the final copy cannot rescue it. `IF_SAFER` not `YES`: GDAL switches when the UNCOMPRESSED image
would pass ~2 GB, leaving room for overviews, while smaller rasters stay classic TIFFs — these files
are downloadable and some older desktop GIS still refuses BigTIFF. Set via `profile["bigtiff"]`
rather than a kwarg: a BigTIFF SOURCE can carry `bigtiff` in its own profile and collide. Pinned by
`api/tests/test_cog_bigtiff.py` (spies on both `rio_copy` calls; a 4 GB fixture is not a test).)
2026-08-06e (**the 3D-bar defaults come from the DATA, and "Unknown" no longer reaches the buffer**.
After the SQL and Martin-config fixes, bars STILL showed nothing: 240 country centroids with
latitude as the height field means the tallest bar is 90 m and the default footprint was 30 m — about
three thousandths of a pixel at world zoom. Correct in every layer of the stack, invisible on screen.
`symbology.pillar_radius(style, bbox)` now derives the footprint from the layer's own extent
(`extent_metres`/400 — world → 100 km, a city → 36 m, i.e. street scale unchanged) and an
author-chosen radius still wins. The editor mirrors it (`lib/symbology.pillarRadius`) so the number
shown is the number rendered. The height MULTIPLIER is deliberately left alone at ×1: an earlier
pass auto-derived it from the field's max, which was tuned on one throwaway test field — deriving a
default from the data is right, inferring INTENT from it is not.
Also `portal_generator._is_point`: `_geom_kind` FALLS BACK to "point" for an unrecognised type, and
`"Unknown"` is a real stored value (Fiona reports it for any generic/mixed shapefile header) — that
fallback sent a polygon layer to the pillar function, which buffered administrative polygons into
self-intersecting rings. The fallback stays as a RENDERING default; anything acting on the geometry
must ask `_is_point`. Both the pillar SOURCE and the pillar LAYER use it — emitting one without the
other points a layer at a missing source and MapLibre drops it. Plus `_lonlat_bounds`: raster sources
now declare `bounds`, so MapLibre stops requesting whole-world tiles that the tile server 404s;
range-checked because the ingest bbox reprojection falls back to the SOURCE CRS, and a projected
bbox there would hide the layer entirely.)
2026-08-06d (**`pillars.py`: the tile function never worked** — it failed on EVERY request with
`syntax error at or near "%"`, so 3D point bars drew nothing from the day they shipped. The body used
`%%1$I` inside a `format()`, which renders a literal `%1$I` awaiting a SECOND format pass that did
not exist; the `USING` clause could not substitute for it (it binds `$1` parameters and cannot quote
an identifier at all). Rewritten as ONE positional pass. While in there: attributes are now listed
EXPLICITLY instead of `t.*` — which put the source point geometry in the tile beside the buffered
polygon, giving `ST_AsMVT` two geometry columns — the tile envelope is computed once in plpgsql and
passed as `%L::geometry`, the bbox test compares `t.geom && ST_Transform(env, srid)` so the spatial
INDEX can be used (it was transforming every row instead), the guard requires a real
`geometry`/`geography` column (`pg_authid.rolname` passed the old existence-only check and produced a
Postgres error rather than an empty tile), and the function is `STABLE`, not `IMMUTABLE` — it reads
tables. **The tests are the point:** all 12 asserted on `CREATE_SQL` as TEXT and all 12 passed against
SQL that could not run. CI has PostGIS, so it now installs the function and calls it.
`martin.py`: `_ensure_pillar_function` reports whether it CREATED the function, `_write_config`
reports whether the config CHANGED, and Martin is restarted only when one of those is true — a
restart drops in-flight tiles. `main.py::lifespan` rebuilds the config at startup
(`_refresh_martin_sources`), because it was previously written only when the layer list changed, so
an updated instance never learned about a new tile source. See notes_for_future.md.)
2026-08-06c (`symbology.py`: `outline_color()` and `marker_outline()` — `NO_OUTLINE` ("none") means
draw none, absent means the default, so existing portals are untouched. MapLibre has no transparent
-outline keyword, so a polygon expresses "none" by OMITTING `fill-outline-color`; a marker expresses
it by not stroking. Marker outline width is a RATIO of the radius and is part of `marker_image_id`,
since it changes the pixels — two differently-ringed markers must not share one image.)
2026-08-06b (**new `pillars.py` — 3D for POINT layers**. MapLibre extrudes FILLS, so there is no
point form of `fill-extrusion`: the geometry has to become a polygon. ONE shared Martin FUNCTION
source (`geodeploy.point_pillars`) buffers a layer's points by a radius in METRES (via `geography` —
a degree buffer is a different real size at every latitude) and returns them as MVT polygons, which
`fill-extrusion` then raises. The layer is named by QUERY PARAMETERS on the tile URL, so one function
serves every layer instead of DDL per upload. That URL is PUBLIC on a published portal, so the
function validates schema/table/geom against `information_schema` before interpolating them and
`%I`-quotes them as well. `martin._ensure_pillar_function` installs it (CREATE OR REPLACE,
non-fatal) from the same place that writes the config naming it, so the two cannot drift.
Deliberately NOT deck.gl's ColumnLayer for these: PostGIS layers render through Martin into MapLibre,
and switching renderer when 3D is ticked would need a second implementation of identify, visibility
and z-order. GeoParquet points are deck-rendered and their half is NOT built — the editor hides the
control for them rather than showing one that does nothing.)
2026-08-13b (`cog_converter.low_zoom_is_cheap` + `portal_generator.raster_minzoom` — issue #17. The
raster minzoom floor was computed from the layer's EXTENT, a proxy; what decides whether a
zoomed-out tile is expensive is the file's OVERVIEW PYRAMID. Measured at ingest on the CONVERTED
COG (the pyramid is what conversion adds, so reading `meta` from the original would answer the
wrong question) and stored as `raster_layers.low_zoom_ok`. True → no floor; NULL/False → the
heuristic. NULL is deliberately not False: existing layers keep today's behaviour until they are
re-ingested, which is the conservative direction for a guard against a page-wide hang.)
2026-08-13 (`ramp_colors(name, count, reverse=False)` — reversing a ramp is a FLAG
(`color_ramp_reverse` in the style, `?reverse=` on `field-stats`), never a second table of reversed
ramps: which end means "high" is a cartographic choice, and nine ramps would become eighteen. The
sampled OUTPUT is reversed, not the stop list, so a reversed ramp is the same colours backwards.
**While adding it, the twins were found to already disagree**: sampling used `round()`, which is
half-to-EVEN in Python and half-UP in JavaScript, so every 5- and 9-class ramp picked a different
stop in `ui/src/lib/symbology.js` than here. Latent only because nothing in the UI called
`rampColors` yet. Both now use `x + 0.5` truncated — one formula, expressible identically in either
language — and `test_symbology` pins three literal colour lists as the contract between them.)
2026-08-06 (**new `symbology.py` — data-driven styling**. THE source of truth for turning a layer's
friendly style keys into MapLibre expressions: `classify` (quantile/equal/jenks-by-k-means),
`build_classes`/`build_categories`, `color_expression` (`step`/`match`), `size_expression`
(`interpolate`), `extrusion_paint` (`fill-extrusion`), `legend_entries`, plus the point-marker set
(`marker_image_id`/`icon_image_expression`/`marker_images`/`icon_size_expression`). Points keep their
SHAPE under a classification: `icon-image` is data-driven in MapLibre, so the style emits one image
per class and selects between them with the same step/match the colour uses. (An earlier version
switched to a `circle` layer and lost the shape — rejected; see notes_for_future.) Pure and DB-free because
`ui/src/lib/symbology.js` is its line-by-line twin and three of the four renderers are JavaScript;
`tests/test_symbology.py` pins the expressions as literals for both. Two decisions worth keeping:
class breaks have OPEN outer edges (data added after styling still draws), and a break at the column
minimum is dropped rather than shipped as an empty class. `portal_generator` bakes
`geodeploy:legend` from `legend_entries` so portal.js renders a legend it never re-derives.
`duckdb_engine.field_stats` + `routers/data/vector.field-stats` supply the distribution; the
classifier stays server-side so the editor and the portal cannot disagree.)
2026-07-30 (V-14 `catalog` archetype in `resolve_layout` — it previously ALIASED to webmap, which is
why selecting it did nothing; `layers_info` gains `layer_id` and is baked to `style.geodeploy.catalog`)
2026-08-01 (`backup.py`: `BucketMissing` + `create_destination_bucket`)

2026-07-30 (new `backup.py` — separate-destination backups of PostGIS/objects/state)
2026-07-29 (About page renders share_links via `portal_generator._share_block`; `ORIGIN_TOKEN`)
2026-07-29 (new `share_links.py`; `duckdb_engine` gained `offset` paging + `query_feature_by_id` for
`routers/ogcapi.py`)
2026-07-27 (GeoLibre interop Front-1: `geolibre_import.py` — `.geolibre.json` → import plan
[LayerStyle→MapLibre paint incl. extrusion/3D-Z/raster/tiles] + `plan_to_layer_configs`/
`plan_to_portal_kwargs`; `generate_style` gains a raw-paint passthrough via `_vector_layers`. Pure
translation fully tested; ingestion orchestration + endpoints next. Parity TODO: PortalEditor + portal.js)
2026-07-21 (V-11 Template Experiences: `portal_generator.resolve_layout` bakes the layout manifest +
story into `style.geodeploy.layout`/`.story`; mirrored in portal.js + PortalEditor.vue — see the bullet)
2026-07-14 (SECURITY: `portal_generator._json_for_html` HTML-escapes JSON embedded in the portal
`<script>` (blocks a layer-name `</`+`script>` breakout); title is `_esc`'d. `minio._ensure_readonly_user`
mints a read-only, bucket-scoped MinIO user (via a short-lived `minio/mc` container) that TiTiler runs
as instead of the root key — falls back to root if mc is unavailable so raster never breaks.)
2026-07-11 (duckdb_engine: native FlatGeobuf tiling feed `export_geoparquet_to_fgb` via baked `spatial`; memory-bounded tiling feeds; `loc` NameError fix)
