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
  - **qgis-plugin** job: the stubbed plugin tests, the dock smoke test, flake8 and the bandit scan
    plugins.qgis.org runs. No QGIS — the `qgis` module is not pip-installable.
  - **qgis-real** job (added 2026-09-03): a matrix of `qgis/qgis:ltr` (3.44) and `qgis/qgis:4.2`,
    run as **container jobs** with `QT_QPA_PLATFORM=offscreen`, executing
    `integrations/qgis-plugin/scripts/test_real_qgis.py`. **Why a second QGIS job at all:** every
    other plugin test stubs the QGIS classes, and the stubs shared a base that gave each symbol
    layer a `setWidth`/`width` pair only `QgsSimpleLineSymbolLayer` really has. The plugin called
    `setWidth` on a FILL, this workflow went green, and in real QGIS every polygon layer raised
    `AttributeError` — it shipped, and a user reported it. Offscreen rather than xvfb because
    nothing opens a window; the images are ~3.5 GB, so this job is the slow one.
- `workflows/publish-cli.yml` — publishes `cli/` to PyPI on a **`cli-v*`** tag, via **Trusted
  Publishing**: PyPI verifies a short-lived OIDC token minted by GitHub for this repo, workflow
  filename and environment, so no API token is stored anywhere. `workflow_dispatch` runs the same
  pipeline against TestPyPI.
  - The **tag prefix is deliberate**: `cli-v1.3.0b1`, not the platform's `v1.3` release tags. The
    two do not always move together — the CLI's first upload is a pre-release that exists before
    v1.3 does — and a CLI-only fix must be releasable without re-tagging the whole platform.
  - The `build` job is the gate, because **a version on PyPI can never be re-uploaded**: it runs the
    suite on 3.9 (the floor), asserts the **tag matches the packaged version** (the failure it
    prevents is tagging `cli-v1.3.0` while `__init__.py` still says `1.3.0b1`), runs `twine check`,
    and installs the wheel with `--no-deps` to prove the zero-dependency promise.
  - **One-time setup on PyPI** (Account → Publishing → pending publisher, before the first upload):
    owner `bravemaster3`, repository `GeoDeploy`, workflow **`publish-cli.yml`**, environment
    **`pypi`** (and `testpypi` for the rehearsal). All four must match exactly or the upload is
    rejected as `invalid-publisher`. Adding a required reviewer to the `pypi` environment makes
    every upload wait for approval.
- `workflows/validate-template.yml` — runs on PRs touching `templates/community/**`; installs `jsonschema`/`Pillow`/`maplibre-style-spec` and runs the validator script.
- `scripts/validate_template.py` — checks each changed community template: required files present, `template.json` schema, `style.json` MapLibre validity, `preview.png` is 800×500, no external CDN URLs in `layout.html`.

## Dependencies / relationships
- `ci.yml` exercises `api/` (pytest), `ui/` (vite build) and `cli/` (pytest + console script) — keep build/test commands here in sync with those folders' tooling.
- `validate-template.yml` + `scripts/validate_template.py` enforce the contract documented in `templates/community/CONTRIBUTING.md`.

## Action versions
Every `actions/*` step is pinned to its latest MAJOR and they are moved together, because a repo
that bumps one at a time ends up spread across four generations of runner requirements. What each
crossing actually required, checked rather than assumed (2026-08-30):

- **Node 24** is the substance of most of the majors (`checkout` v5, `setup-python` v6,
  `upload-artifact` v5, `download-artifact` v6). They need runner **v2.327.1+**, which every
  GitHub-hosted runner already exceeds.
- **`download-artifact` v5** changed the output path for downloads **by ID**. Downloads **by name**
  are explicitly unchanged, and by name is what `publish-cli.yml` does.
- **`download-artifact` v8** now errors on a hash mismatch instead of warning. That is the behaviour
  worth having on a release pipeline.
- **`setup-node` v5** caches automatically when `package.json` has a `packageManager` field.
  `ui/package.json` has none, so nothing changed here.
- **`upload-pages-artifact` v4** stopped including dotfiles. `docs/` has none, and `docs/CNAME` —
  which is what keeps the custom domain — is not a dotfile.
- **`checkout` v7** blocks checking out a fork PR under `pull_request_target` / `workflow_run`.
  No workflow here uses either trigger.

## Current status & known issues
- CI runs unit/build checks only — no integration test spins up the full Docker stack, so tile-serving regressions (the kind debugged in `notes_temp/notes_for_future.md`) are **not** caught by CI. Verify those manually.
- The API test suite is minimal (`/health`, initial setup status).

## Last updated
2026-09-03 (added the `qgis-real` matrix job — the plugin's symbology round trip against a real PyQGIS on 3.44 and 4.2)
