# nginx/

## Purpose
The single public entrypoint. Reverse-proxies the SPA, the API, the two tile servers, and serves published portals. This is where several "blank map" bugs originated.

## Contents
- `nginx.conf` — one `server` block on :80 (443 block commented out, ready for certbot). Locations:
  - `/health`, `/api/` → `geodeploy-api:8000` (with stricter rate-limit + longer timeout on the two `/upload` paths).
  - `/tiles/` → Martin and `/raster/` → TiTiler both use `set $var` + `rewrite ^/<prefix>/(.*)$ /$1 break;` to strip the prefix, then **`proxy_pass http://$var$uri$is_args$args;`**. The explicit `$uri$is_args$args` is required: with a *variable* host, plain `proxy_pass http://$var;` does not reliably forward the rewritten path + query args (this is why correct-format tile URLs 404'd through nginx while working directly in the container).
  - `/portals/` → static `alias /var/www/portals/` (the bundles written by the API).
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
2026-06-01
