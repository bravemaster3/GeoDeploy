# .github/

> **Why this is not called `README.md`.** GitHub renders `.github/README.md` as the REPOSITORY's front
> page, in preference to the root `README.md` — so this folder note was what visitors saw instead of
> the project description. It keeps the folder-note role described in `CLAUDE.md`; only the filename
> changed, and it must not go back.

## Purpose
GitHub Actions CI and the community-template validation pipeline.

## Contents
- `workflows/ci.yml` — runs on push/PR to `main`:
  - **api** job: Python 3.12, installs GDAL/PostgreSQL system libs + `requirements.txt` + pytest deps, runs `pytest tests/` with dev env vars.
  - **ui** job: Node 20, `npm install` + `npm run build`.
  - **cli** job: `cli/` on a Python **3.9 and 3.12** matrix — `pip install -e ".[dev]"`, `pytest`,
    then a smoke run of the console script. No services: the suite runs against a fake instance it
    starts itself (`cli/tests/conftest.py`), so it finishes in seconds. **3.9 is in the matrix on
    purpose**: the QGIS plugin vendors that package and QGIS 3.28 LTR ships Python 3.9, so this job
    is what catches syntax or stdlib use that would break there. The install line is `.[dev]` and
    nothing else — the package must stay dependency-free, and this job failing on a missing wheel
    would mean one had crept in.
- `workflows/validate-template.yml` — runs on PRs touching `templates/community/**`; installs `jsonschema`/`Pillow`/`maplibre-style-spec` and runs the validator script.
- `scripts/validate_template.py` — checks each changed community template: required files present, `template.json` schema, `style.json` MapLibre validity, `preview.png` is 800×500, no external CDN URLs in `layout.html`.

## Dependencies / relationships
- `ci.yml` exercises `api/` (pytest), `ui/` (vite build) and `cli/` (pytest + console script) — keep build/test commands here in sync with those folders' tooling.
- `validate-template.yml` + `scripts/validate_template.py` enforce the contract documented in `templates/community/CONTRIBUTING.md`.

## Current status & known issues
- CI runs unit/build checks only — no integration test spins up the full Docker stack, so tile-serving regressions (the kind debugged in `notes_temp/notes_for_future.md`) are **not** caught by CI. Verify those manually.
- The API test suite is minimal (`/health`, initial setup status).

## Last updated
2026-08-12 (added the **cli** job for the packaged `geodeploy` client)
