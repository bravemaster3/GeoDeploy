# docs/

## Purpose
End-user / operator documentation (not developer internals — those live in each folder's README and in `notes_temp/`).

## Contents
- `getting-started.md` — install command, the 3-step setup wizard (Database → Storage → Admin), first upload, first portal.
- `data-access.md` — how third parties consume shared data: the STAC catalog (`/api/stac`), COG via `/vsicurl/`, XYZ tiles into QGIS, GeoParquet via DuckDB/manifest, the honest GeoNode comparison, and what's deliberately not provided (legacy OGC).
- `api-reference.md` — points at the instance's own live OpenAPI docs (`/api/docs`, `/api/redoc`, `/api/openapi.json`) rather than restating endpoints, plus token auth + scopes and the unauthenticated public read surfaces.
- `backups.md` — scheduled backups of PostGIS + objects + instance state to a SEPARATE S3 destination, and the in-app restore (owner-only, typed-name confirm).
- `roadmap.html` — the visual roadmap board; canonical data is its `roadmap-data` JSON block (see `ROADMAP.md` for the update workflow).
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
2026-07-30 (created `api-reference.md` — the root README's link to it had always been dead; indexed
`backups.md` + `roadmap.html`, which this file had never learned about; recorded the docs-site
decision and the coverage gaps)
2026-07-11 (added performance-tuning.md — PMTiles tiling & env knobs for heavy layers)
