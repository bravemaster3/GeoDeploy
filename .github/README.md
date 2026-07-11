# .github/

## Purpose
GitHub Actions CI and the community-template validation pipeline.

## Contents
- `workflows/ci.yml` — runs on push/PR to `main`:
  - **api** job: Python 3.12, installs GDAL/PostgreSQL system libs + `requirements.txt` + pytest deps, runs `pytest tests/` with dev env vars.
  - **ui** job: Node 20, `npm install` + `npm run build`.
- `workflows/validate-template.yml` — runs on PRs touching `templates/community/**`; installs `jsonschema`/`Pillow`/`maplibre-style-spec` and runs the validator script.
- `scripts/validate_template.py` — checks each changed community template: required files present, `template.json` schema, `style.json` MapLibre validity, `preview.png` is 800×500, no external CDN URLs in `layout.html`.

## Dependencies / relationships
- `ci.yml` exercises `api/` (pytest) and `ui/` (vite build) — keep build/test commands here in sync with those folders' tooling.
- `validate-template.yml` + `scripts/validate_template.py` enforce the contract documented in `templates/community/CONTRIBUTING.md`.

## Current status & known issues
- CI runs unit/build checks only — no integration test spins up the full Docker stack, so tile-serving regressions (the kind debugged in `notes_temp/notes_for_future.md`) are **not** caught by CI. Verify those manually.
- The API test suite is minimal (`/health`, initial setup status).

## Last updated
2026-06-01
