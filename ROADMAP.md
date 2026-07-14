# GeoDeploy Roadmap

GeoDeploy is a self-hosted spatial data platform. The foundation
(ingest anything → render millions of features seamlessly → publish geoportals, all on a cheap VPS)
is shipped. This roadmap tracks the path from that single-tenant platform to a **multi-user product**
and ultimately **GeoDeploy Cloud** (a hosted, multi-tenant service), plus the ecosystem and advanced
features that drive adoption and differentiation.

- **Visual, interactive board:** [`docs/roadmap.html`](docs/roadmap.html) — published as a Claude
  Artifact. Filter by status, search, click a card for detail, watch the cloud-readiness meter.
- **Source of truth:** the JSON embedded in that file (`<script id="roadmap-data">`). This Markdown is
  the narrative + schema + workflow around it.

## ▶ Next logical step: **Multi-user & RBAC** (`A-01`)

Today, onboarding creates exactly **one admin** and stops — there is no way to add teammates or scope
what anyone can do. The next step is **user management with roles** (owner / admin / editor / viewer):
an invite flow, user CRUD screens, and a permission check on every mutating route.

**Why this first:** it's the smallest change that (a) makes GeoDeploy usable by teams *now* and (b) is
the load-bearing prerequisite for **everything in Phase 02 (Cloud)** — workspaces, tenant isolation,
sharing, and billing all assume real users and roles. Ownership/sharing (`A-02`) and API tokens
(`A-03`) follow immediately after.

## Phases

| # | Phase | Theme | State |
|---|-------|-------|-------|
| 00 | **Foundation** | The platform, shipped | 11 items — 10 shipped incl. security hardening; automated tests in progress |
| 01 | **Multi-user & Access** | The bridge to Cloud | `A-01` **next**, then planned |
| 02 | **GeoDeploy Cloud** | Managed, multi-tenant | planned / future |
| 03 | **Ecosystem & Interop** | Adoption engine (GeoLibre, QGIS, standards) | planned / future |
| 04 | **Advanced Capabilities** | Differentiators | future / idea |

Highlights beyond the Cloud spine, captured as tracked items:

- **GeoLibre integration** — "Open in GeoLibre" hand-off (`E-01`) and **"Publish to GeoDeploy" from
  GeoLibre** (`E-02`), turning GeoLibre into GeoDeploy's desktop companion and an adoption funnel.
- **Style interchange** — MapLibre style JSON round-trip (`E-03`) and QGIS QML/SLD + GeoLibre style
  import (`E-04`).
- **Connect from QGIS** (`E-05`), **Catalog/STAC** (`E-06`), optional **OGC API** (`E-07`, kept but
  deprioritized in favor of the lakehouse path).
- **Multi-language (i18n)** (`E-08`, planned, near-term) — run the dashboard and published portals in
  the visitor's language (French first, West-Africa focus). The admin UI already has vue-i18n
  scaffolding; the bigger half is translating the shared portal runtime.
- **Advanced:** in-browser DuckDB-WASM analysis console (`V-01`), fast processing toolkit (`V-02`),
  story-map portals (`V-03`), EXIF photo features (`V-04`), temporal (`V-05`), 3D tiles (`V-06`),
  live connectors (`V-07`).
- **Portal tools framework** (`V-08`) — an admin-selectable toolbox chosen at publish time (measure,
  print, download, swipe, share, filter, geolocate, saved views…), with the build-heavier measure +
  print/PDF tools broken out as `V-09`. The shipped area-clip download (`F-06`) becomes the first
  registered tool.
- **Transactional email & export notifications** (`C-08`) — Resend-powered "your download is ready"
  emails for queued exports once usage grows, reusing the same channel for invites, resets and receipts.

## Data schema (`<script id="roadmap-data">` in `docs/roadmap.html`)

```jsonc
{
  "updated": "YYYY-MM-DD",
  "statusOrder": ["shipped","building","next","planned","future","idea"], // legend + severity order
  "statusMeta":  { "<status>": { "label": "...", "var": "--st-<status>" } },
  "phases": [ { "n": "00", "key": "foundation", "title": "...", "tag": "...", "note": "..." } ],
  "items":  [ {
    "id":     "F-01",                 // stable, unique; prefix by phase (F/A/C/E/V)
    "phase":  "foundation",           // must match a phases[].key
    "title":  "Core platform",
    "status": "shipped",              // one of statusOrder
    "effort": "S | M | L | XL",
    "areas":  ["api","ui"],           // domain tags: api, ui, auth, celery, portal, tiles, duckdb,
                                      //   infra, storage, billing, geolibre, qgis, docs
    "why":    "one-line rationale (shown on the card)",
    "detail": "paragraph shown when the card is expanded",
    "depends":["F-01"],               // ids that must ship first (integrity-checked)
    "frontier": true                  // OPTIONAL — exactly one item; the 'you are here' marker
  } ]
}
```

**Status vocabulary:** `shipped` (done, in production) · `building` (actively in progress) · `next`
(the immediate next thing — usually also the `frontier`) · `planned` (committed, not started) ·
`future` (intended, further out) · `idea` (candidate, not committed).

Everything on the visual board (counts, per-phase progress rails, the cloud-readiness meter, filters)
is **computed from this JSON** — there are no hard-coded numbers. Cloud-readiness is a weighted score
over phases 00–02 (`shipped`=1, `building`=0.5, `next`=0.15).

## Workflow — how to keep this roadmap current

**For Claude Code (and any maintainer):**

1. **Ship / start / add work →** edit `docs/roadmap.html`'s `roadmap-data` JSON:
   - flip an item's `status` (e.g. `next` → `building` → `shipped`);
   - when the `frontier` item ships, move `"frontier": true` to the new `next` item and set that
     item's status to `next`;
   - add new items with a unique `id` and correct `phase`/`depends`.
2. Bump the top-level `"updated"` date.
3. **Re-publish the artifact:** run the **Artifact** tool on `docs/roadmap.html` (same file path →
   same URL; pass the saved `url` to update in place rather than minting a new one).
4. Keep the phase table above in sync if a phase's headline state changes.

**Deriving future roadmaps:** this file is the canonical plan. When scoping a new epic, add its items
here first (with `depends` wired to existing ids), then implement — so the board always leads the code.
When an item is delivered, the corresponding folder `README.md` gets the implementation detail; this
roadmap only tracks *state*, not *how*.

## Last updated
2026-07-14 — added `E-08` Multi-language (i18n) — dashboard + published-portal translation, French
first, planned near-term. 42 items; frontier = `A-01`.
2026-07-14 — added `V-10` Template gallery & branding (planned); shipped a first distinct template
set (minimal, satellite-dark, editorial, humanitarian) and removed the weak research template. 41 items.
2026-07-14 — added `V-08` Portal tools framework (admin-selectable toolbox), `V-09` Measurement &
print tools, and `C-08` Transactional email & export notifications (Resend). 40 items; frontier = `A-01`.
2026-07-14 — added `F-10` Security hardening (shipped: 2026-07 audit — setup lock, private-layer
enforcement, login rate-limit, portal XSS, read-only TiTiler key, security headers; all with
regression tests) and `F-11` Automated tests & CI (building). 37 items; frontier = `A-01`.
2026-07-13 — initial roadmap: 5 phases, 35 tracked items; frontier = `A-01` Multi-user & RBAC.
