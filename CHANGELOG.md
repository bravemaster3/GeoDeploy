# Changelog

Notable changes, newest first. Versions are **major.minor** — `v1.0`, `v1.1`, … `v1.9`, then
`v2.0`. The minor number moves for anything shipped, features or fixes; the major changes when an
upgrade needs manual work.

## v1.1 — 2026-08-07

### Data-driven symbology

Style a layer **by its data**, not just with one colour. In the portal editor's symbology popover:

- **Graduated** — classify a numeric field into colour classes, with quantile, equal-interval or
  natural-breaks classing, 2–9 classes, and a choice of sequential or diverging colour ramps.
- **Categories** — a colour per distinct value, with a qualitative palette (a sequential ramp on
  unordered values implies a ranking the data does not have).
- Every class colour is editable, and the class list *is* the legend — published portals show it
  beside the layer name.
- **Point markers keep their shape.** A classified point layer still draws stars, squares or
  diamonds; only the colour varies.

**3D.** Polygons can be extruded by a numeric field — building heights, floors × a multiplier,
anything. Points get **bars**: a column standing at each location, raised by a field. Portals
containing an extruded layer open tilted, because straight down a 3D block and a flat polygon are the
same shape — and there is now a **tilt button** beside the zoom controls, so you can look at 3D from
the side without knowing that right-dragging the map does it.

The bar footprint is sized from the layer's own extent rather than a fixed number of metres. A fixed
default is right at exactly one scale: a few hundred points spread across the world would otherwise
draw bars a few thousandths of a pixel wide — rendered perfectly, and invisible.

**The globe has a sky.** In 3D globe view the planet sits against space with an atmospheric limb,
instead of a flat dark panel — now with a brighter, deeper starfield behind it.

### Portals load as one piece

Every portal used to assemble itself in front of the visitor: the map appeared as soon as its tiles
arrived, and the catalog rail, story column and layer list turned up afterwards in whatever order
they finished. Portals now show a loading screen until the pieces are actually ready — not for a
fixed time, but until each part reports in.

- **Pointing at a feature shows a pointer cursor** — including GeoParquet layers, which are drawn by
  a different renderer and never had one.
- On a **catalog**, the "On map" list folds away and opens closed, so switching several datasets on
  no longer eats the map it is describing.
- A long **layer list** scrolls on its own, leaving the search box and buttons in place.

### Outlines, and rings

- **Outlines can be turned off.** Polygons and points both take "None" — previously a polygon always
  had a blue outline and a point always had a white one, with no way to remove either.
- **Points get a real outline control**: a colour and a thickness. Thickness is a proportion of the
  marker, so resizing a layer keeps it looking right — and a thick one hides the fill, which is how
  you draw a **ring**.

### Easier to live with

- **My Data** collapses per section (Vectors / Rasters / External, remembered between visits) and
  paginates at 10 — a few hundred layers no longer make the page unusable. **External sources** got
  the search box the other two sections already had, matching on the service type and endpoint as
  well as the name, and searching a collapsed section now opens it rather than reporting a count for
  rows you cannot reach.
- **Infrastructure ▸ Deployments and Environment** scroll inside a fixed height instead of growing
  the Settings page without limit.
- **`CELERY_CONCURRENCY`** is now an editable setting. Each background worker holds its own copy of
  the file it is converting, so on a small server this multiplies memory before it multiplies speed —
  set it to 1 on 2 GB.

### Choose which version to install

The updater could only ever move you to the tip of the development branch — even on an instance
deliberately installed at a release, which meant "Update now" silently undid that choice. Settings ▸
Infrastructure ▸ Updates now offers **main**, the **latest release**, or **a specific release** (to
hold back, or to step back down after a bad update) — and remembers which one this instance follows,
so an instance on a release is measured against releases instead of being told it is many commits
behind development.

Any **branch** can be installed too, listed in the same picker under "Another branch (advanced)" —
that is how an unreleased feature branch gets tried on a real instance instead of by hand over SSH.

The same choice works from the server: `sudo bash installer/self-update.sh v1.0`,
matching `GEODEPLOY_VERSION` in the installer. Documented in [Updating](docs/updating.md).

### Backup history can be tidied

Failed backup runs stayed red in **Settings ▸ Backups ▸ History** forever, on the page whose job is
to tell you at a glance whether your backups are healthy. Entries can now be removed individually,
or all failed ones at once. History is a log of attempts — removing an entry never touches a backup
at the destination, and the app says so at the point of the click.

### Fixed

- **Download by area works in 3D.** On the globe, "no layers intersect the selected area" came back
  for an area plainly full of features: the check ran against a flat rectangle on screen, which on a
  curved globe is not the region you drew. It now compares real coordinates, which also fixes a
  quieter 2D case — a layer whose tiles had not finished loading was left out of the download.

- **3D bars near the antimeridian no longer stripe the planet.** A bar is generated by buffering the
  point, and buffering in lon/lat across the ±180° line produces a ring that wraps the whole world —
  so one country near the dateline drew a band right around the globe, shredded into bow-tie shapes
  where each map tile cut it. Bars are now built in projected coordinates, where there is no wrap.

- **The globe keeps its sky whichever way you reach it.** Switching to the globe with the map's own
  globe button left the planet on a flat black panel: the atmosphere and starfield are GeoDeploy's,
  and only its own view-restoring code was telling them the projection had changed.

- **A shapefile's geometry type is read from the data, not its header.** Shapefiles whose header
  declares a generic or mixed type were recorded as "Unknown", and the rest of the app then guessed
  — differently in different places. A polygon layer could be treated as points, which with 3D
  enabled drew a mess of shards across the map. Imports now ask the database what actually arrived.
  Layers imported before this keep their recorded type until re-uploaded.

- **Rasters stop asking for tiles that do not exist.** A raster layer did not tell the map where its
  data was, so the map requested tiles across the whole world at every zoom and the tile server
  answered "not found" to nearly all of them.

- **The tile server learns about new capabilities on restart.** Its configuration was only rebuilt
  when the layer list changed, so an instance that updated without uploading anything could be
  running a version whose new tile features were never switched on. It is now rebuilt whenever
  GeoDeploy starts, and the tile server is only restarted when something actually changed — which
  now includes a corrected tile definition, because the tile server caches tiles in memory and would
  otherwise keep serving the ones it built before the fix.

- **Large uploads work again after a restore.** A GeoParquet, GeoPackage or big CSV would upload to
  100% and then fail. Background jobs took their storage credentials from a copy kept in the
  database — the copy a restore replaces with the backup's — while the upload itself used the live
  ones, so the file arrived and nothing could read it back. There is now one source for both, and a
  restore puts the instance's own credentials back. Rasters, tiling and exports were affected the
  same way.

- **Restoring a backup no longer breaks the connection details.** The Settings panel read the
  credentials stored *in the database* — the one copy a restore replaces with the backup's — so after
  a restore it showed keys belonging to another instance, or unreadable text, while `.env` (what the
  instance actually runs on) was right all along. It now reads `.env` first and says where each value
  came from. The restore also puts this instance's own storage credentials back where the rest of the
  app looks for them, which raster ingest was quietly using.
- **Demo instances republish their portals after the hourly reset.** A portal deleted by a visitor
  came back in the dashboard, but its page opened blank until someone pressed Publish. Published
  portal pages are now rebuilt as part of restoring any snapshot, not just an operator-run restore.

## v1.0 — 2026-08-03

**The first tagged release.** GeoDeploy has been running in production for months; this is the point
at which it becomes something you can install by version number, upgrade, and recover.

Nothing below is new in the sense of "added this week" — v1.0 is the line drawn around what already
works.

### What you get

**One command on a bare Linux server** brings up a complete spatial stack: PostGIS, object storage,
vector and raster tile services, a web dashboard, and the portals you publish from it. A setup
wizard handles the rest — no Docker knowledge, no database configuration.

- **Bring your own, or let GeoDeploy run it.** PostGIS and object storage can each be managed for
  you on the same server, or pointed at a database and an S3-compatible provider you already have.

**Data**

- Shapefile, GeoPackage, GeoJSON, KML, CSV (X/Y or WKT), GeoParquet, GeoTIFF.
- Two vector backends: **PostGIS** served as vector tiles, and **GeoParquet** with no database table
  at all — read by the browser through DuckDB, tiled to **PMTiles** when large, and streamed by HTTP
  range request. Twenty million features pan and zoom on a small VPS.
- Rasters convert to **Cloud-Optimized GeoTIFF** and are served by TiTiler.
- Files above the request cap upload **direct to storage**, never through the API.
- Click-to-identify and **draw-a-box export** happen in the browser, at near-zero server cost.
- Data already in PostGIS or S3 can be **registered without re-uploading it**.
- The native CRS is preserved; reprojection happens for display, not on ingest.

**Portals**

- Three experiences: **web map**, **story map**, **catalog** — each a different shape, not a map
  with extra panels.
- An editor whose preview *is* the published runtime, so what you see is what visitors get.
- One style per layer — colour, opacity, dashes, marker shapes and sizes — plus layer icons,
  legends and nestable folders. (Styling by a data field is the next feature, not this one.)
- **About pages** with a WYSIWYG editor, per-layer metadata and direct data links.
- Templates, per-portal branding, a 3D globe start view, and responsive layouts.

**People and access**

- Roles from viewer to owner, with a single transferable owner.
- Invitations by email or copy-able link; optional **single sign-on (OIDC)**.
- Per-resource visibility (private ⊂ organization ⊂ public) and four access tiers on published
  portals, enforced server-side.
- **Scoped API tokens**, secrets encrypted at rest, and an audit log.

**Open formats — nothing here needs GeoDeploy to read it**

- **STAC 1.0.0** and **OGC API - Features**.
- TileJSON for vector and raster layers; COG, PMTiles and GeoParquet read directly by QGIS, DuckDB,
  Python and R.

**Operating it**

- Per-service logs, terminal and deployments from inside the app.
- **Scheduled backups** of database, files and state to a *separate* destination, with in-app
  restore, history, and a guarded delete.
- One-button updates, with a preflight that refuses to run over work in progress.
- Owner-editable environment variables, allow-listed and applied per service.
- **Demo mode** — a public sandbox wiped hourly, behind a single flag.

### See it running

[geodeploy-demo.kndev.org](https://geodeploy-demo.kndev.org) is this release with demo mode on —
join with a name, publish something, and watch it disappear on the hour.

### Verified for this release

The pieces were always tested; these are the things that can only be proven on a running instance,
and each of them turned up real bugs that unit tests could not have caught:

- **A clean install from scratch**, in all four combinations: PostGIS managed by GeoDeploy or
  external, object storage on this server or an external S3 provider.
- A **backup → restore round trip** on a live instance, including the object copy.
- **Restoring onto a rebuilt instance**, against a database that already held an installation.
- A **scheduled wipe and restore**, hourly and unattended (demo mode uses the same restore path).
- A backup **larger than 2 GB**, which had been failing on an `int4` column.

That exercise found seven bugs no test suite could have caught, because each needed real
infrastructure and several were invisible on a local install — the defaults happen to be correct
there. Among them: external PostGIS could never complete setup, the worker never received the
storage or database credentials the wizard wrote, and a restore silently rewrote the instance's own
database settings. All are fixed, and all are covered by tests now.

### Known issues

Stated plainly, because finding these yourself is worse:

- **No upgrade has been exercised between two tagged versions**, for the obvious reason that there
  are not two yet. Updates between commits on `main` have been running throughout.
- **Single sign-on (OIDC)** is built and unit-tested but has not been verified against a live
  identity provider.
- **SMTP and OIDC secrets do not survive a restore taken under a different `GEODEPLOY_SECRET_KEY`**
  and must be re-entered. That is the design — the key lives in `.env` and deliberately not in the
  database or any backup, so a stolen backup cannot hand over your credentials. Your data, and the
  backup destination, are unaffected.
- **Object storage credentials can only be set during setup.** There is no screen for them
  afterwards, so rotating an S3 key means editing `.env`. Same for `GEODEPLOY_SECRET_KEY` itself.
- **Restoring a backup rolls the schema back** to the snapshot's, then re-applies the additive
  migrations. Restoring a backup from a *much* older version is untested territory.
- Restore history begins at the restore that creates it: a restore replaces the database its own
  record lives in, so the record is re-inserted afterwards rather than surviving.

### Upgrading

There is nothing to upgrade *from* yet. Existing installs tracking `main` are already on this code;
the tag simply names it.

---

## Before v1.0

Development began 27 May 2026 and ran on `main` without tagged releases. The full history is in
`git log`; the [roadmap](https://docs-geodeploy.kndev.org/roadmap/) groups what landed by area.
