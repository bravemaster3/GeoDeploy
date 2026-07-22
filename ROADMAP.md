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

## ▶ Frontier: **Auth hardening** (`A-04`) — **building**

`A-01`, `A-02`, `A-03` are **shipped** (RBAC + shared workspace; per-resource sharing + server-side
portal access; scoped API tokens driving the headless CLI). `A-04` rounds out identity so the platform
is safe to open beyond a trusted operator: **(0)** app-managed DB secrets (SMTP, OIDC) encrypted at
rest (Fernet); **(1)** session/token revocation — browser JWTs carry a `token_version`, so a password
change/reset and a "log out other sessions" action revoke outstanding tokens while the acting tab
stays signed in; **(2)** **OIDC single sign-on** via Authlib — an admin-configured generic provider
(Google / Microsoft / Keycloak / institutional), account linking by verified email, and opt-in
domain-allow-listed auto-provisioning with a default role. Password reset already shipped (A-01 + C-08).
Next: **A-05** activity & audit log.

<details><summary>Previous frontier — API tokens (A-03), shipped</summary>

## ▶ Frontier: **API tokens** (`A-03`) — **building**

`A-01` **Multi-user & RBAC** and `A-02` **Resource ownership & sharing** are **shipped**: a **shared
workspace** with a role ladder (owner / admin / editor / viewer), a per-resource **visibility axis**
(`private` ⊂ `organization` ⊂ `public`) on layers + sources enforced by the `visible_to()` seam, and a
**server-side-enforced 4-tier published-access** model for portals (public / password / organization /
private, via nginx `auth_request` + a session cookie, password portals via a per-portal unlock cookie).

`A-03` adds **scoped personal access tokens** so an editor can drive the SAME API headlessly — from a
GeoLibre or QGIS plugin — to upload data, prepare/tile, and **publish a portal**, or **open a portal in
edit mode** (GET its config → edit → PUT). Tokens are opaque, shown once, stored hashed; each
authenticates as its owner through the existing Bearer path, **capped at a chosen role tier** (never
above the owner's live role) so enforcement reuses the RBAC ladder with no new per-route checks.
Managed per-user in Settings; revocable; optionally auto-expiring; dies with the owner.

**Why this matters:** API tokens are the foundation under the desktop plugins (`E-02` Push from
GeoLibre, `E-05` Connect from QGIS) — the adoption funnel — and under CI/scripting. Auth hardening
(`A-04`) follows.

</details>

## Phases

| # | Phase | Theme | State |
|---|-------|-------|-------|
| 00 | **Foundation** | The platform, shipped | 12 items — 10 shipped incl. security hardening; tests building; service logs/console planned |
| 01 | **Multi-user & Access** | The bridge to Cloud | `A-01`, `A-02`, `A-03` **shipped**; `A-04` (frontier) + `A-05` **building** |
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
- **Transactional email & export notifications** (`C-08`) — **building**: the invite/password-reset
  half shipped 2026-07-16 as **optional generic SMTP** (any provider — Resend, Brevo, an institutional
  relay; copy-link delivery always remains the fallback), including login-page "Forgot password?".
  Remaining: "your download is ready" emails for queued exports and receipts.
- **Template gallery & branding** (`V-10`) — visual template gallery + per-portal brand colours/logo
  (the look-and-feel half), with **structural layout templates** (`V-11`) as the separate,
  build-heavier half: floating right-hand layer list, basemap-picker placement, sidebar side,
  docked/overlay panels — layout variety like ArcGIS Experience Builder, by making the shared portal
  runtime's regions configurable rather than fixed.

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
2026-07-22 — `V-11` **redesign** after user testing (stays **building**/frontier). New model: portal =
Template (default minimal) + Theme (colors) + Experience (**webmap · storymap** only — the confusing
catalog/webmap+catalog archetypes were dropped and alias→webmap) + Layout (placement). **R1 shipped**
(runtime substrate): widened manifest (`layerList{side,mode,collapsed,width,x,y}` + `controls{side}`), new
**Home / Zoom-to-all / draw-box-zoom** controls, an **on-map side-aware layer-list toggle**, floating list
now collapses + moves + resizes, **Reset/About moved into the layer actions row**, transparent card border.
**R2 shipped** (faithful editor preview): the editor preview is now a same-origin **iframe of the real
portal** (`POST /portals/{id}/preview` → unlisted, logged-in-only bundle + nginx gate), with a portal.js
**edit shim** for **click-to-place** (pick an element, click a spot on the live map) — the schematic is
gone and the preview can't drift from the published portal. **R3 shipped** (colour themes): a per-portal
`{mode, accent, font}` baked as validated CSS-var overrides over the template — so one base template yields
many looks; editor gains a Theme section (light/dark · accent presets/custom · font). **R4 shipped** (story
pictures): per-section images via the existing asset upload. V-11's redesign (R1–R4) is now feature-complete
pending deploy+verify; follow-ups (remove the hidden editor map, persist the floating box, rich-text story
editor = V-15) are logged in notes.
2026-07-21 — `V-11` **Template experiences** flipped `next` → **building** (frontier, code-complete /
unverified). Phase 1 landed: a `Portal.layout_config` manifest `{archetype, regions, panels}` resolved
by `portal_generator.resolve_layout` and baked into `style.geodeploy.layout`, a **region-driven shell**
(`portal.js` sets `data-*` on `<body>`; `portal.css` grid/overlay rules) with 3 reuse-only archetypes
(webmap · webmap+catalog · catalog), a **PortalEditor "Experience" panel** (archetype cards + placement
toggles + a schematic wireframe). Phase 2 START: a **storymap** archetype MVP — a `Portal.story`
`{sections:[{title,body,view,layers}]}`, a scroll-driven `StoryPanel` (IntersectionObserver → `flyTo` +
per-section layer visibility), a story-section editor (capture map view), and a dedicated **Story**
template (`official/story`, `archetype:storymap`); `humanitarian` presets `webmap+catalog`. Parity held
across all three surfaces (resolveLayout mirrored). Back-compat: no manifest ⇒ webmap ⇒ unchanged. New
`test_portal_experiences.py`. Full rich-text/media story editor + scroll polish stays as `V-15`.
2026-07-16 — **portal access enforced SERVER-SIDE** (was client-side): nginx `auth_request` →
`GET /api/portals/authz` validates a `gd_session` HttpOnly cookie against the portal's `access_type`
before the static bundle is served (organization = any member, owner = creator + admins; a deny
bounces to `/login?next=`). Login/accept set the cookie; the SPA mirrors existing sessions via
`POST /auth/session`. Password stays a client-side gate. Also added `V-12` **Responsive layouts
(mobile/tablet)** (planned). 80 backend tests pass.
2026-07-20 — `V-13` **Layer groups & catalog panel** flipped `planned` → **building** (now the active
frontier): a portal's flat layer list becomes a NESTED FOLDER TREE (collapse, toggle-all, exclusive/
radio groups, per-group description) built in the editor (`LayerTree.vue`) and browsed in the published
portal (`portal.js applyLayerGroups`). New `Portal.layer_groups` tree alongside flat `layer_configs`;
no tree → flat list (back-compat). A-04/A-05 stay `building` (code-complete, awaiting deploy+verify).
2026-07-20 — Roadmap gained an **`"unverified": true`** flag → a "⚠ needs testing" pill + amber
accent on code-complete-but-unverified items (search "untested"). Tagged `A-04` (SSO/OIDC live flow)
and `C-08` (email SMTP). Added **`V-13` Layer groups & catalog panel** (planned — folder/group catalog
tree as a per-template portal element) and bumped **`V-11` Structural layout templates** `future` →
`planned` (configurable regions so templates differ in LAYOUT, not just colour). These two are the
near-term portal focus after A-04/A-05 verify.
2026-07-20 — `A-05` **Activity & audit log** flipped `future` → **building**: append-only `AuditLog` +
`record_audit()` wired into the key mutations (user/portal/token/auth/data), admin-only filterable
`GET /audit` + an **Activity** view. Entries survive user deletion. 123 backend tests pass.
2026-07-20 — `A-03` API tokens flipped → **shipped** (scoped tokens + CLI in use). **Frontier → `A-04`
Auth hardening**, now **building**: secrets encrypted at rest (Fernet); session/token revocation
(JWT `token_version` + "log out other sessions"); **OIDC SSO** via Authlib (admin-configured provider,
verified-email linking, opt-in domain-allow-listed auto-provisioning). 118 backend tests pass.
2026-07-17 — Added `F-12` **Service logs & console** (Foundation, planned): per-service live log
streaming in Settings → Infrastructure, plus a security-gated (owner-only, opt-in) in-browser
container console — extends the existing service start/stop/restart controls.
2026-07-17 — `A-02` Resource ownership & sharing flipped `building` → **shipped** (server-side portal
access + password unlock landed and verified; user sign-off). **Frontier moves to `A-03` API
tokens.** Also fixed the portal/preview on-load map flash (three causes: deck two-stage fit, preview
multi-`applyStyle`, redundant basemap swap) and made the portal-editor access picker an icon dropdown.
2026-07-16 — `A-01` Multi-user & RBAC flipped `building` → **shipped** (user sign-off); **frontier
moves to `A-02`**. Portals dropped the confusing workspace-visibility control in favor of a 4-tier
published-access model (public / password / organization / private = creator + admins); the legacy
`private` access value maps to `organization`. Portal access gates are client-side today —
**server-side enforcement is the next task**.
2026-07-16 — `A-02` Resource ownership & sharing flipped `planned` → `building` (code complete, in
verification): a per-resource visibility axis — private (creator + admins) / organization (all
members) / public (STAC data catalog + raw assets) — on vector + raster layers, external sources, and
portals. Folds the earlier `is_public` STAC flag into the axis (kept write-only-synced); the
`visible_to()` seam enforces it across every list + authenticated by-id lookup while public-by-id
portal display endpoints stay untouched (published portals unaffected). 72 backend tests pass.
2026-07-16 — `C-08` flipped `future` → `building`: invite + password-reset email delivery shipped
as optional generic SMTP (self-service "Forgot password?" included); export notifications remain.
2026-07-16 — `A-01` Multi-user & RBAC flipped `next` → `building`: all 7 implementation phases
committed (role ladder + single transferable owner, shared workspace, copy-link invitations, user
CRUD with delete-reassign, password flows, role-aware UI; 45 backend tests pass). Frontier stays on
`A-01` until verified on the live instance and shipped.
2026-07-14 — added `V-11` Structural layout templates (configurable portal regions — floating layer
list, panel placement, sidebar side; Experience-Builder-style layout variety) and refocused `V-10` on
the gallery + brand look-and-feel. 43 items; frontier = `A-01`.
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
