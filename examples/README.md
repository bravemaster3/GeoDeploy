# examples/

## Purpose
Reference clients for the GeoDeploy API using **scoped API tokens** (A-03). These are the crib sheet
the GeoLibre (`E-02`) and QGIS (`E-05`) plugins build on: authenticate headlessly, push data, and
create / edit / publish portals — the same API the dashboard uses, no browser session.

## Contents
- `geodeploy_cli.py` — a thin `requests`-based CLI. Commands: `whoami`, `layers [--raster]`,
  `upload <file> [--poll]`, `portals`, `portal-get <id>`, `portal-set <id> <config.json>`,
  `publish <id>`, `unpublish <id>`. Reads `GEODEPLOY_URL` + `GEODEPLOY_TOKEN` from the environment.

## Quick start
1. In the dashboard: **Settings → API tokens → Create token** (pick scopes + expiry). Copy the
   `gdp_…` secret — it's shown once.
2. Configure the client:
   ```bash
   pip install requests
   export GEODEPLOY_URL=http://127.0.0.1            # your instance origin
   export GEODEPLOY_TOKEN=gdp_xxxxxxxxxxxxxxxxxxxx
   ```
   > **Tip — use `127.0.0.1`, not `localhost`.** On Windows + WSL2 Docker, `localhost` resolves to
   > IPv6 (`::1`) first, but the published port usually binds only IPv4, so each request stalls on the
   > `::1` attempt before falling back — requests "work but feel slow". `http://127.0.0.1` skips it.
   > (PowerShell sets env vars with `$env:GEODEPLOY_URL = "http://127.0.0.1"`.)
3. End-to-end flow (prepare a dataset → build/edit → publish):
   ```bash
   python geodeploy_cli.py whoami
   python geodeploy_cli.py upload roads.gpkg --poll     # needs data:write
   python geodeploy_cli.py portals
   python geodeploy_cli.py portal-get 3 portal3.json    # editable config incl. layer_configs styles
   #   …edit portal3.json (layers, symbology, template, access_type)…
   python geodeploy_cli.py portal-set 3 portal3.json    # needs portal:write
   python geodeploy_cli.py publish 3                    # needs portal:publish
   ```

## Scopes & auth
The token authenticates as its owner, limited to its scopes (never above the owner's role). A call
outside the token's scopes returns `403 Token missing scope: …` — e.g. a `portal:publish`-only token
can `publish` but not `upload`. Send the token as `Authorization: Bearer <token>`. Tokens are managed
(and revoked) per-user in Settings → API tokens; they expire (30/90/365 days).

## Dependencies / relationships
- Server side: `api/geodeploy/routers/tokens.py` (mint/revoke), `deps.require_scope` + `deps.SCOPES`
  (enforcement), and the data/portal routers.
- The portal edit model is just `GET /api/portals/{id}` → mutate `layer_configs` (symbology lives in
  each layer's `style`) → `PUT /api/portals/{id}`. The plugins translate QGIS/GeoLibre styling into
  that `layer_configs` shape (the deep fidelity work is `E-02`/`E-05`, not here).

## Current status & known issues
- `upload` uses the API-passthrough multipart route (good to ~2 GB). Very large files use the
  presign/direct-to-storage flow — not wrapped by this reference CLI yet.
- No packaging (single script by design). A published `geodeploy` PyPI client can come with the plugins.

## Last updated
2026-07-17 — initial CLI (A-03 API tokens).
