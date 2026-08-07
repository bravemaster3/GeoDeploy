# nginx/

## Purpose
The single public entrypoint. Reverse-proxies the SPA, the API, the two tile servers, and serves published portals. This is where several "blank map" bugs originated.

## Contents
- `nginx.conf` — one `server` block on :80 (443 block commented out, ready for certbot). Locations:
  - `/health`, `/api/` → `geodeploy-api:8000` (with stricter rate-limit + longer timeout on the two `/upload` paths).
  - `/tiles/` → Martin and `/raster/` → TiTiler both use `set $var` + `rewrite ^/<prefix>/(.*)$ /$1 break;` to strip the prefix, then **`proxy_pass http://$var$uri$is_args$args;`**. The explicit `$uri$is_args$args` is required: with a *variable* host, plain `proxy_pass http://$var;` does not reliably forward the rewritten path + query args (this is why correct-format tile URLs 404'd through nginx while working directly in the container).
  - `/portals/` → static published bundles under `/var/www/portals/{slug}/index.html`, but **falls back to the SPA** (`@spa` → `geodeploy-ui`) when the path isn't a portal file — because the dashboard's Vue routes also live under `/portals/...` (e.g. `/portals/3/edit`). Without the fallback, refreshing/deep-linking an editor URL 404'd. Uses `root /var/www` + `try_files $uri $uri/index.html @spa`.
  - `/s3/` → local MinIO (`geodeploy-minio:9000`), for **presigned direct uploads** (GeoParquet). The browser can't reach MinIO's internal hostname, so it PUTs here. The presigned URL is signed against `geodeploy-minio:9000`, so this block forwards that **exact** `Host` (hardcoded, not `$host`) for SigV4 to verify, and sets `proxy_request_buffering off` so a 10 GB body streams straight to MinIO instead of spooling to nginx disk. Same `rewrite … break;` + explicit `$uri$is_args$args` pattern as the tile routes (keeps the `?X-Amz-…` signature query intact). Only the local MinIO uses this path; external/public S3 gets a full presigned URL and uploads cross-origin (bucket CORS).
  - `/templates-static/` → API. `/` → `geodeploy-ui:80` (SPA, with websocket upgrade for dev HMR).
  - Uses Docker's internal resolver (`127.0.0.11`) + `set $var` so recreated containers are re-resolved without an nginx restart.
  - **`merge_slashes off;`** at the server level — left in but **was a misdiagnosis**: `merge_slashes` only normalizes the URI *path*, never the query string, so it never affected `?url=s3://...`. Harmless; the real query-forwarding fix is the explicit `$uri$is_args$args` proxy_pass above.

## Dependencies / relationships
- Bind-mounted read-only into the `nginx` container (`docker-compose.yml`), plus `data/portals` as `/var/www/portals` and the certbot dirs.
- Routes to `geodeploy-api`, `geodeploy-ui`, `martin`, `titiler` by their network aliases — those must resolve on the `geodeploy` network.
- The dev equivalent is the Vite proxy in `ui/vite.config.js`; keep prefix-stripping and the titiler:80 port aligned between the two.

## Current status & known issues
- The `rewrite` rules, `merge_slashes off`, and the titiler port were all part of this session's tile-serving fixes. **If you edit any tile route, re-read `notes_temp/notes_for_future.md` first** — there is a documented chain of subtle interactions (prefix stripping, slash merging, TileMatrixSet path, S3 endpoint scheme).
- After editing `nginx.conf` you must `docker compose restart nginx` (or reload) — the file is mounted but nginx loads it at start.
- HTTPS/443 is stubbed but not wired (no automated certbot flow yet).

## Last updated
2026-08-07 (**a tile that misses the raster is EMPTY, not missing.** TiTiler answers 404 for a tile
outside the COG bounds — correct for an API, wrong for a tile pyramid, where the off-the-edge tiles
are a normal part of the grid a client requests. A portal can be told where the data is (MapLibre
source `bounds`, our TileJSON), but a **bare XYZ URL carries no metadata**, so QGIS/GeoLibre/Leaflet
given the XYZ link cannot avoid asking, and every ask became a console error. A new REGEX location
`~ ^/raster/cog/tiles/` (regex beats the `/raster/` prefix location, which stays for everything else)
sets `proxy_intercept_errors on` + `error_page 404 = @blank_tile`, and `@blank_tile` uses nginx's
built-in `empty_gif` — a 1x1 transparent GIF every raster client decodes, no asset to ship. Scoped to
the TILES path deliberately: `/cog/info`, `/cog/statistics` and `/cog/point` keep returning real
errors, since blanking those would hide genuine failures. CORS headers are repeated with `always` on
the named location or they are dropped on the error-derived path and a cross-origin client sees a
CORS failure instead of a blank tile. Verified against a live TiTiler: out-of-bounds → 200 image/gif
43 B with `Access-Control-Allow-Origin: *`; in-bounds → 200 image/png, unchanged.
**Deploying a change to THIS file needs `apply_nginx`, not a bare reload** — the single-file mount
goes stale (see notes_for_future).)
2026-06-04
