# Changelog

Notable changes, newest first. Versions follow [semantic versioning](https://semver.org/):
the major version changes when an upgrade needs manual work, the minor when features land, the
patch for fixes alone.

## v1.0.0 — unreleased

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
- Symbology (single, graduated, categorized), layer icons and markers, legends, nestable folders.
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

- A **backup → restore round trip** on a live instance, including the object copy.
- A **scheduled wipe and restore**, hourly and unattended (demo mode uses the same restore path).
- A backup **larger than 2 GB**, which had been failing on an `int4` column.

### Known issues

Stated plainly, because finding these yourself is worse:

- **A clean install has not yet been verified end to end on fresh hardware** following only the
  documentation. It is the last open item before the tag.
- **No upgrade has been exercised between two tagged versions**, for the obvious reason that there
  are not two yet. Updates between commits on `main` have been running throughout.
- **Single sign-on (OIDC)** is built and unit-tested but has not been verified against a live
  identity provider.
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
