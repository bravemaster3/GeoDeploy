# docs/

## Purpose
End-user / operator documentation (not developer internals — those live in each folder's README and in `notes_temp/`).

## Contents
- `getting-started.md` — install command, the 3-step setup wizard (Database → Storage → Admin), first upload, first portal.
- `data-access.md` — how third parties consume shared data: the STAC catalog (`/api/stac`), COG via `/vsicurl/`, XYZ tiles into QGIS, GeoParquet via DuckDB/manifest, the honest GeoNode comparison, and what's deliberately not provided (legacy OGC).
- `performance-tuning.md` — heavy-layer display (PMTiles tiling, the Data Manager Tile button) and the optional `.env` knobs for tiling (`PMTILES_TILE_MEMORY_LIMIT`, `PMTILES_MAXZOOM`, `PMTILES_DENSEST`, …). Defaults need no tuning; this is the escape hatch for very large layers or unusual hardware.

## Dependencies / relationships
- Describes the flows implemented by `installer/install.sh` and `ui/src/views/SetupWizard.vue`. Keep in sync when those change.
- The root `README.md` links here and references `docs/api-reference.md`, which **does not exist yet** (broken link — create it or remove the link).

## Current status & known issues
- `api-reference.md` is referenced from the root README but missing — create it or remove the link.
- User-facing docs; for build quirks and debugging history use `notes_temp/notes_for_future.md` instead.

## Last updated
2026-07-11 (added performance-tuning.md — PMTiles tiling & env knobs for heavy layers)
