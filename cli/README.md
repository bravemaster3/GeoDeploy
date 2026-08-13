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
- `styles.py` — the style vocabulary of `api/geodeploy/services/symbology.py`, assembled from plain
  arguments. **Classification maths is NOT reimplemented**: `classify()` reads
  `GET /data/vector/{ref}/field-stats`, so the CLI cannot disagree with the editor about which class
  a feature is in.
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

`tests/` — 298 tests against a real HTTP server (`conftest.FakeInstance`) that records what arrived
on the wire. `pyproject.toml` — packaging; console script `geodeploy`.

## Dependencies / relationships
- **Zero runtime dependencies, Python 3.9+.** Both constraints exist for the QGIS plugin, which
  vendors this package and cannot pip-install anything (QGIS 3.28 LTR ships Python 3.9). Do not add
  a dependency here without moving it into an extra.
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
first upload — anyone can claim it). To cut a release:

```bash
cd cli
python -m build                     # sdist + wheel into dist/
python -m twine check dist/*        # must pass before anything is uploaded
python -m twine upload --repository testpypi dist/*    # rehearse
python -m twine upload dist/*
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
  `cancel`, typed errors, no printing) are in place and tested.

## Last updated
2026-08-12 (created — packaged CLI + Python client, replacing `examples/geodeploy_cli.py`; then
`browse` + anonymous layer download on top of the new `/api/public` and per-layer export endpoints)
