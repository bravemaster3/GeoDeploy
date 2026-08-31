# docs/

## Purpose
End-user / operator documentation (not developer internals — those live in each folder's README and in `notes_temp/`).

## Contents
- `overrides/main.html` — the ONLY theme override: the link-preview (`og:`/`twitter:`) tags, per page, pointing at `assets/og-image.png`. Material's own `social` plugin would render a card per page but needs Cairo + Pango in the build image, and this site is built by a plain `pip install mkdocs-material` in CI — so the card is a shipped image instead. Registered as `theme.custom_dir` and listed in `exclude_docs` so MkDocs does not also copy it into the site.
- `assets/og-image.png` — the 1200x630 card, the same image every instance serves at `/og-image.png` (source of truth: `ui/public/og-image.png`).
- `getting-started.md` — install command, the 3-step setup wizard (Database → Storage → Admin), first upload, first portal.
- `data-access.md` — how third parties consume shared data: the STAC catalog (`/api/stac`), COG via `/vsicurl/`, XYZ tiles into QGIS, GeoParquet via DuckDB/manifest, the honest GeoNode comparison, and what's deliberately not provided (legacy OGC).
- `api-reference.md` — points at the instance's own live OpenAPI docs (`/api/docs`, `/api/redoc`, `/api/openapi.json`) rather than restating endpoints, plus token auth + scopes and the unauthenticated public read surfaces.
- `backups.md` — scheduled backups of PostGIS + objects + instance state to a SEPARATE S3 destination, and the in-app restore (owner-only, typed-name confirm).
- `getting-started.md` — gained (2026-08-03) "Connecting a database you already run" and
  "Reconnecting to an existing GeoDeploy database": the per-DATABASE nature of the PostGIS
  extension, and the wizard's sign-in-or-create-a-new-database choice when the target already
  holds an installation. Also dropped the stale "(recommended)" labels the UI removed long ago.
- `licence.md` (2026-08-02) — Apache 2.0 in plain language: what you may do, what
  redistribution requires, the patent grant (§3) and trademark exclusion (§6), the DCO, and the
  point that the licence covers the SOFTWARE and not the data you put in it.
- `roadmap.md` — THE roadmap, shaped as releases. Previously there were three (this, `roadmap.html`, and the root `ROADMAP.md`) and they drifted; the board is gone and the root file is now a pointer. Releases are `gd-rel` blocks styled as a timeline in `stylesheets/extra.css`.
- `performance-tuning.md` — heavy-layer display (PMTiles tiling, the Data Manager Tile button) and the optional `.env` knobs for tiling (`PMTILES_TILE_MEMORY_LIMIT`, `PMTILES_MAXZOOM`, `PMTILES_DENSEST`, …). Defaults need no tuning; this is the escape hatch for very large layers or unusual hardware.

## Dependencies / relationships
- Describes the flows implemented by `installer/install.sh` and `ui/src/views/SetupWizard.vue`. Keep in sync when those change.
- The root `README.md` links here, including to `api-reference.md` (created 2026-07-30 — that link was dead before).

## Current status & known issues
- **Docs site is live.** `mkdocs.yml` + `.github/workflows/pages.yml` publish this folder to GitHub
  Pages on every push to `main` that touches `docs/`. The workflow runs `mkdocs build --strict`, so a
  broken internal link FAILS the build — links to files OUTSIDE `docs/` must be absolute GitHub URLs.
  `docs/README.md` (this file) is excluded from the site via `exclude_docs`.
- **Remaining gaps:** per-format upload troubleshooting, embedding a portal in another site, and a
  worked tutorial end-to-end. Competitor comparisons are deliberately absent — describe what
  GeoDeploy does, not what other tools do not.
- User-facing docs; for build quirks and debugging history use `notes_temp/notes_for_future.md` instead.

## Last updated
2026-08-31 (**SEO pass + v1.5.1.** Every page now carries its own `description:` front matter —
all eighteen were falling back to `site_description` from mkdocs.yml, so every GeoDeploy search
result shipped an identical snippet. `index.md` also gained a `title:` so its `<title>` carries the
words people search ("self-hosted geoportal builder") while the visible H1 stays the hero line; the
nav label is unaffected because mkdocs.yml names it. Fixed a dead hero link — the button still
pointed at `#three-kinds-of-portal`, which became "Four kinds" when dashboards shipped.
`dashboards.md` documents the v1.5.1 authoring options; `roadmap.md` gained the v1.5.1 stop and its
header now says v1.5.1 rather than v1.4.1.)

2026-08-18 (link previews: `overrides/main.html` + `assets/og-image.png`.)
2026-07-30 (created `api-reference.md` — the root README's link to it had always been dead; indexed
`backups.md` + `roadmap.html`, which this file had never learned about; recorded the docs-site
decision and the coverage gaps)
2026-07-11 (added performance-tuning.md — PMTiles tiling & env knobs for heavy layers)
