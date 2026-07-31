<div align="center">

# GeoDeploy

**Self-hosted spatial data platform and geoportal builder.**

Upload your data, style it, and publish a map anyone can use — on your own server, with your own
domain, and no per-seat pricing.

[Documentation](https://docs-geodeploy.kndev.org/) ·
[Getting started](https://docs-geodeploy.kndev.org/getting-started/) ·
[Access your data](https://docs-geodeploy.kndev.org/data-access/) ·
[Roadmap](https://docs-geodeploy.kndev.org/roadmap/)

</div>

---

## What it is

One command gives you a complete spatial stack on a single Linux server: a spatial database, object
storage, vector and raster tile services, a web dashboard, and the portals you publish from it.

```bash
curl -fsSL https://raw.githubusercontent.com/bravemaster3/geodeploy/main/installer/install.sh | bash
```

A setup wizard takes it from there — database, storage, admin account — and you are uploading data a
couple of minutes later. No terminal, no Docker knowledge, no database configuration.

## What you get

- **Bring data in.** Shapefile, GeoPackage, GeoJSON, CSV, GeoParquet, GeoTIFF. Large files upload
  straight to object storage, so multi-gigabyte datasets are not a special case.
- **Publish portals.** Choose an experience — a web map, a scrollytelling story, or a searchable
  catalog — arrange the layers, set who can see it, publish. Each gets its own URL, and any portal
  embeds in another site with one `<iframe>`.
- **Share data properly.** Layers you mark public are readable by standard clients over open
  standards, so QGIS, Python and R consume them directly with no export step.
- **Work as a team.** Roles from viewer to owner, per-layer visibility, invitation links, scoped API
  tokens, and an audit log of who changed what.
- **Operate it from the browser.** Service logs, a terminal, scheduled backups to a separate
  destination, an in-app restore, and one-button updates.

## How data flows

```
upload ──▶ PostGIS or GeoParquet ──▶ tiles ──▶ portal editor ──▶ published portal
                    │
                    └──▶ OGC API - Features · STAC · COG · PMTiles ──▶ QGIS · Python · R
```

Vector data lands in PostGIS or as GeoParquet depending on its size and how you will use it; rasters
become Cloud-Optimized GeoTIFFs. Everything is then reachable two ways: through the portals you
publish, and through open standards other tools already speak.

## Requirements

A Linux server. **4 GB of RAM is a comfortable recommendation** and two cores are plenty — tiling
included. Disk depends on your data, not on GeoDeploy, and you can attach S3-compatible storage so
capacity is never a server decision. Docker and Docker Compose are installed for you if missing. A
domain name is optional but recommended.

## Documentation

Full documentation: **<https://docs-geodeploy.kndev.org/>**

| | |
|---|---|
| [Getting started](https://docs-geodeploy.kndev.org/getting-started/) | Install, set up, publish your first portal |
| [Uploading data](https://docs-geodeploy.kndev.org/uploading/) | Formats, the two vector backends, large files |
| [Portals and experiences](https://docs-geodeploy.kndev.org/portals/) | Web map, story map, catalog; layout and access |
| [Users, roles and sharing](https://docs-geodeploy.kndev.org/users-and-sharing/) | Roles, visibility, tokens, audit log |
| [Access from other tools](https://docs-geodeploy.kndev.org/data-access/) | QGIS, DuckDB, Python — which standard for which job |
| [API reference](https://docs-geodeploy.kndev.org/api-reference/) | Tokens, scopes, and the live OpenAPI docs |
| [Updating](https://docs-geodeploy.kndev.org/updating/) | Self-update, services, the Infrastructure panel |
| [Backups and restore](https://docs-geodeploy.kndev.org/backups/) | Scheduled backups and in-app restore |

Every instance also serves its own interactive API documentation at `/api/docs`.

## Stack

| Layer | Technology |
|-------|-----------|
| API | FastAPI + Celery + Redis |
| State + spatial database | PostgreSQL / PostGIS (provisioned automatically) |
| Vector tiles | Martin |
| Raster tiles | TiTiler |
| Object storage | MinIO, S3-compatible (provisioned automatically) |
| Columnar analytics | DuckDB (embedded) |
| Frontend | Vue 3 + MapLibre GL JS + deck.gl |
| Infrastructure | Docker Compose, nginx |

## Contributing

Issues and pull requests are welcome. Templates are the easiest place to start —
see [templates/community/CONTRIBUTING.md](templates/community/CONTRIBUTING.md).

Developer notes live in each folder's `README.md`; `CLAUDE.md` describes how the repository is
organised and kept current.

## Licence

MIT — free forever, with no feature restrictions on the self-hosted version.
