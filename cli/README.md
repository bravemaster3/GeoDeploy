# cli/

## Purpose
The packaged **`geodeploy`** command-line client **and** the Python API client it is built on — the
headless half of the product (roadmap v1.3). Two audiences, one package: a person or CI job at a
shell, and the **QGIS plugin** (`E-05`), which imports `geodeploy.client` directly rather than
re-implementing HTTP against the same endpoints.

User documentation is `docs/cli.md`; this file is the technical note.

## Contents

**The library** (`geodeploy/`) — never prints, never exits, raises typed errors:
- `client.py` — `Client`: URL/auth resolution, request plumbing, error mapping, and the namespaces
  (`gd.vector`, `gd.raster`, `gd.layers`, `gd.portals`, `gd.sources`, `gd.imports`, `gd.jobs`,
  `gd.uploads`, `gd.admin`, `gd.users`, `gd.catalog`). `absolute()` resolves the **relative
  `/s3/…` presigned URLs** a managed MinIO returns.
- `transport.py` — stdlib HTTP. `UrllibTransport` (retries only connection failures and 502/503/504,
  and **never** a streamed body — the file object is already partly consumed), `MultipartBody`
  (form-data streamed from disk with a real Content-Length), `ProgressReader`. Anything with
  `send(Request) -> Response` can replace it; that seam is for QGIS's `QgsNetworkAccessManager`.
- `config.py` — profiles + credentials. **Tokens never enter `config.json`**: OS keyring when there
  is one, else `credentials.json` at 0600 in a 0700 directory, written atomically. `normalize_url`
  collapses `…/api`, trailing slashes and a bare host to one origin, so a credential saved under one
  spelling is found under another. `split_portal_url` does the same job for a published portal's
  URL → `(origin, slug)`, so a link copied out of the address bar is a valid portal reference
  anywhere a slug is (`browse --portal`, and `Portals.get`, which every portal command goes
  through). `resolve()` = flag → env → profile, for URL and token separately.
- `uploads.py` — the routing table (below) plus both transports: multipart-through-the-API, and
  presign/chunked direct-to-storage with per-part retry and abort-on-failure.
- `layers.py` — the two layer kinds, and `Layers.resolve` (id | uid | `vector-3` | name; ambiguity
  raises rather than guessing — including a **bare integer that exists in both kinds**, since
  vector and raster ids are separate sequences and "1" is routinely two different layers).
  `download_dataset()` pulls a prepared GeoParquet layer's manifest + every partition straight from
  storage: complete, lossless and **not subject to the export row cap**, which is why
  `layers download` prefers it over queueing an export for those layers.
- `portals.py` — portal CRUD and `layer_configs` surgery. `layer_configs[0]` = top of the list =
  drawn on top. `editable_config()` drops server-owned fields before a round-trip PUT.
- `styles.py` — the style vocabulary of `api/geodeploy/services/symbology.py`, in both directions.
  `build_style()` **assembles** it from plain arguments; `parse()`/`Style` **reads** one back
  (mode, field, classes, categories, size, extrusion, rescale) so a consumer — the QGIS plugin —
  does not re-decide what `color_mode: "graduated"` implies. `Style` is a reader, not a schema: it
  never rejects and keeps `.raw`, and `to_dict()` makes build → parse → build lossless.
  **Classification maths is NOT reimplemented**: `classify()` reads
  `GET /data/vector/{ref}/field-stats`, so the CLI cannot disagree with the editor about which class
  a feature is in. Same rule for legends — `vector.legend()` asks the instance, and
  `Style.legend()` is the local twin for a style that has no URL yet (an unsaved edit), pinned
  against the server's exact labels in `test_styles_jobs`.
- `catalog.py` — the public surfaces, including `public()` (the instance index behind
  `geodeploy browse`) and `portal_style()`, which reads a published portal's own `style.json`.
- `jobs.py`, `sources.py`, `imports.py`, `admin.py`, `errors.py`.

`Layers.resolve_public()` is the anonymous twin of `resolve()`: with no credential a layer is looked
up in the public index, so `geodeploy --url … layers download roads` works by NAME for someone with
no account. `_LayerBase.export_to_file()` drives the queue-poll-download of a built export.

**The CLI** (`geodeploy/cli/`) — the only part allowed to print:
- `main.py` — argparse root, `Context` (formatter + lazy client), and the single place exceptions
  become exit codes.
- `output.py` — `Formatter` (stdout = the answer, stderr = commentary, `--json` = one document),
  tables, progress bar, `EXIT_*` constants.
- `commands/` — one module per group (`auth`, `upload`, `layers`, `portals`, `sources`, `imports`,
  `jobs`, `catalog`, `admin`, `browse`), each with `register(subparsers)`. `_common.py` holds the
  styling flags, shared by the three commands that take them, and `resolve_layer(..., public_ok=)`
  which decides between the authenticated list and the public index.

`tests/` — 314 tests against a real HTTP server (`conftest.FakeInstance`) that records what arrived
on the wire. `pyproject.toml` — packaging; console script `geodeploy`.

## Dependencies / relationships
- **Zero runtime dependencies, Python 3.9+.** Both constraints exist for the QGIS plugin, which
  vendors this package and cannot pip-install anything on a user's machine. The floor is set by the
  **oldest QGIS anyone still runs**, not the current one — institutions pin a release for years —
  so it reaches back through several LTR lines rather than tracking today's. Current QGIS ships a
  much newer Python; that is not the constraint. Do not add a dependency here without moving it
  into an extra.
- Consumes `api/geodeploy/routers/` — see that folder's README for the permission model. Nothing in
  the API imports this.
- **GeoLibre does not use this package**: its plugin is browser TypeScript
  (`integrations/geolibre-plugin/`) hitting `/api/interop/geolibre/*`. What the two share is the
  HTTP contract and the style vocabulary, not code.
- Parity to keep: the upload routing mirrors `ui/src/composables/useUpload.js`, and the style keys
  mirror `services/symbology.py` + `ui/src/lib/symbology.js`.

## The upload routing table (and why 48 MB)
`.gpkg/.geojson/.json/.zip` < 48 MB → `POST /data/vector/upload`; `.csv` < 48 MB → `/upload-csv`
(PostGIS); either at/over 48 MB → presigned direct-to-storage → `/large/complete` (converted to
GeoParquet); `.parquet` at any size → `/geoparquet/{presign,complete}`; `.tif` small/large →
`/data/raster/upload` or chunked → `/data/raster/large/complete`. Over 48 MB the upload is chunked
into 48 MB presigned parts, four in parallel.

**48 MB is not the API's 2 GB cap.** The binding limit is the request-body cap of whatever proxy
sits in front of the instance (Cloudflare's free tier: 100 MB), and exceeding it produces *no server
log at all* — the request never arrives. `LARGE_UPLOAD_THRESHOLD` must stay equal to the UI's.

## Packaging and release

`pyproject.toml` follows PEP 639: `license = "Apache-2.0"` as an SPDX expression plus
`license-files`, and **no `License ::` classifier** — setuptools ≥ 77 refuses a build that declares
both. The version is single-sourced from `geodeploy/__init__.py` (`dynamic = ["version"]`), so
`--version`, the user agent and the package metadata cannot disagree.

**`PYPI.md`, not `README.md`, is the project page.** This file is the folder's technical note (the
repo convention in CLAUDE.md), which is not what someone landing on PyPI wants to read; `PYPI.md` is
the user-facing front page. Keep both current.

The name `geodeploy` was unregistered on PyPI as of 2026-08-13 (re-check immediately before the
first upload — anyone can claim it).

**Releases go through `.github/workflows/publish-cli.yml`, not from a laptop.** It uses PyPI's
Trusted Publishing: no API token exists to leak or rotate, and the workflow gates the upload on the
test suite, on `twine check`, on a clean `--no-deps` install, and on the **tag matching the packaged
version**. Cutting a release is therefore:

```bash
# 1. bump the one line in geodeploy/__init__.py, commit, merge
# 2. tag it — the prefix is `cli-v`, not the platform's `v1.3`
git tag cli-v1.3.0b1 && git push origin cli-v1.3.0b1
```

Use the workflow's manual run (`Actions → Publish CLI → Run workflow → testpypi`) to rehearse
against TestPyPI first. Building by hand is still the way to CHECK a change without releasing it:

```bash
cd cli
python -m build                     # sdist + wheel into dist/
python -m twine check dist/*        # must pass before anything is uploaded
```

`dist/` and `build/` are git-ignored. The sdist carries the tests, so the release can be verified
from source rather than trusted.

**Versioning.** The CLI's number tracks the GeoDeploy release it ships with, so it must not claim a
release that has not happened: the first upload is `1.3.0b1`, and `1.3.0` follows when v1.3 tags. A
version on PyPI can never be re-uploaded — deleting it does not free the number — so a pre-release
is the only way to test the real index without spending the one that matters. `pip install
geodeploy` skips pre-releases unless asked with `--pre`.

## The row cap, and the two ways around it

A **built** export (`gpkg`/`csv`/`geojson`, or anything clipped) stops at `FULL_EXPORT_CAP`
(1,000,000 features, env-tunable) for a whole layer and `FEATURE_CAP` (50,000) for a bbox clip,
because the worker assembles the archive in memory. The failure mode this creates is not the cap —
it is that a truncated export has the same names and formats as a complete one. So:

- the task records the row count of every file it writes, marks the ones that reached the cap in
  `MANIFEST.txt` (with the uncapped alternatives spelled out), and writes `{job_id}.json` beside
  the zip;
- `GET …/export-status/{job_id}` reports `truncated: [{file, rows, cap}]` from that file;
- `layers download` prints `INCOMPLETE`, names the alternative, and **exits non-zero**.

The uncapped paths, which the CLI prefers automatically where it can: a prepared **GeoParquet**
layer's own partition files (`download_dataset`, no worker involved), and **OGC API - Features**
paging for PostGIS (`ogr2ogr -f GPKG out.gpkg "OAPIF:<instance>/api/ogc" <layer>` — not wrapped by
the CLI yet; see below).

## Current status & known issues
- `geodeploy browse` and `layers download` depend on API endpoints added in the same branch
  (`/api/public`, `/data/{kind}/{ref}/export`). An older instance answers 404 for both; the browse
  command says so, but `layers download` for a built format will simply fail against one.
- Built 2026-08-12 on branch `feat/cli`; **not yet exercised against a live instance** — every test
  runs against the in-repo fake. First real run should be `upload --dry-run`, then a small upload,
  then a portal round trip.
- Not published to PyPI yet. `pip install -e cli/` is the current install.
- `geodeploy admin update --watch` polls through the API restart the update itself causes; a failed
  poll is treated as progress, which is right but means a genuinely dead instance looks like a slow
  update until the poll loop is interrupted.
- No declarative `apply` (a manifest describing layers + portal, applied idempotently). The
  `portals export/import` round trip is the seam it would build on. Deliberately deferred.
- No shell completion.
- The QGIS plugin does not exist yet; the seams it needs (pluggable transport, progress callbacks,
  `cancel`, typed errors, no printing, and `styles.Style` for reading a style back) are in place and
  tested.
  **Which URL it should hand QGIS is two questions, not one.** For DISPLAY of a heavy layer,
  **PMTiles** is the fastest thing we serve — pre-tiled, range-requested, no per-pan query — and it
  is what `layers links` should offer first for a big GeoParquet layer. But PMTiles is a *rendering*
  format: generalised geometry, tile-clipped features, attributes trimmed to what the tiles carry.
  For DATA — full attributes, exact geometry, analysis, editing — the answer is OGC API - Features,
  the GeoParquet partitions, or a built export. A plugin that offers only one of the two will be
  wrong half the time, so the layer-add dialog needs both, labelled for what they are.
  Caveat: reading PMTiles needs a GDAL with the support (3.8+), which rules out the oldest QGIS the
  Python floor above deliberately still supports — so the display path must degrade to OAPIF rather
  than assume it.
  **Measured on the maintainer's two installs (2026-08-13)**, which is why this is a rule and not a
  worry: one reports Python **3.9.5** / GDAL **3.7.2** (no PMTiles — and the exact Python the floor
  above exists for), the other Python **3.12.12** / GDAL **3.12.1** (PMTiles fine). Both are in
  daily use. So `metadata.txt` should say `qgisMinimumVersion=3.28` (the oldest QGIS whose Python
  is ≥ 3.9) and the plugin should branch on `gdal.VersionInfo()` at RUNTIME rather than refusing to
  install — blocking the old one would lock out precisely the pinned installs this package's
  constraints were chosen to serve.
  Develop and demo on the NEWEST QGIS; that is a different decision from what the plugin refuses to
  run on, and only the second one is visible to a user with an old install.

## Last updated
2026-08-12 (created — packaged CLI + Python client, replacing `examples/geodeploy_cli.py`; then
`browse` + anonymous layer download on top of the new `/api/public` and per-layer export endpoints)
