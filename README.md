<div align="center">

# GeoDeploy

[![Release](https://img.shields.io/github/v/release/bravemaster3/GeoDeploy?label=release&color=2563eb)](https://github.com/bravemaster3/GeoDeploy/releases/latest)
[![CI](https://img.shields.io/github/actions/workflow/status/bravemaster3/GeoDeploy/ci.yml?branch=main&label=CI)](https://github.com/bravemaster3/GeoDeploy/actions/workflows/ci.yml)
[![Licence](https://img.shields.io/badge/licence-Apache--2.0-blue)](LICENSE)
[![Live demo](https://img.shields.io/badge/live%20demo-open-brightgreen)](https://geodeploy-demo.kndev.org)
[![Docs](https://img.shields.io/badge/docs-read-informational)](https://docs-geodeploy.kndev.org/)
[![Self-hosted](https://img.shields.io/badge/self--hosted-Docker%20Compose-0db7ed)](https://docs-geodeploy.kndev.org/getting-started/)

[![PostGIS](https://img.shields.io/badge/PostGIS-spatial%20database-336791)](https://postgis.net/)
[![MapLibre](https://img.shields.io/badge/MapLibre-GL%20JS-295dfc)](https://maplibre.org/)
[![OGC API](https://img.shields.io/badge/OGC%20API-Features%20%C2%B7%20WMTS-6a1b9a)](https://docs-geodeploy.kndev.org/data-access/)
[![STAC](https://img.shields.io/badge/STAC-catalog-ef6c00)](https://docs-geodeploy.kndev.org/data-access/)
[![GeoParquet](https://img.shields.io/badge/GeoParquet-COG%20%C2%B7%20PMTiles-00897b)](https://docs-geodeploy.kndev.org/data-access/)

**Self-hosted spatial data platform and geoportal builder.**

Upload your data, style it, and publish a map anyone can use — on your own server, with your own
domain, and no per-seat pricing.

**[Try the live demo](https://geodeploy-demo.kndev.org)** — no sign-up, wiped hourly

[Documentation](https://docs-geodeploy.kndev.org/) ·
[Getting started](https://docs-geodeploy.kndev.org/getting-started/) ·
[Access your data](https://docs-geodeploy.kndev.org/data-access/) ·
[Roadmap](https://docs-geodeploy.kndev.org/roadmap/)

</div>

---

## What it is

One command gives you a complete spatial stack on a single Linux server: **PostGIS**, object
storage, **Martin** for vector tiles and **TiTiler** for rasters, a web dashboard, and the portals
you publish from it.

```bash
curl -fsSL https://raw.githubusercontent.com/bravemaster3/geodeploy/main/installer/install.sh | bash
```

A setup wizard takes it from there — database, storage, admin account — and you are uploading data a
couple of minutes later. No terminal, no Docker knowledge, no database configuration.

> **Want to look before installing?** [geodeploy-demo.kndev.org](https://geodeploy-demo.kndev.org)
> is a live instance anyone can use: join with a name, upload something, style it, publish a portal.
> Everyone shares one workspace and it is wiped every hour, so treat it as a sandbox — but it is the
> same code this repository installs, running with demo mode switched on.

## What you get

- **Bring data in.** Shapefile, GeoPackage, GeoJSON, CSV, GeoParquet, GeoTIFF. Large files upload
  straight to object storage, so multi-gigabyte datasets are not a special case.
- **Publish portals.** Choose an experience — a web map, a scrollytelling story, a searchable
  catalog, or a **dashboard** — arrange the layers, set who can see it, publish. Each gets its own
  URL, and any portal embeds in another site with one `<iframe>`.
- **Ask questions of the data.** A dashboard puts charts, numbers, lists and filters around the map
  and cross-filters them: click a bar, draw a box, or type in a search box, and every other widget
  re-answers. Aggregates are computed on the server, in each layer's own storage.
- **Share data properly.** Layers you mark public are readable by standard clients over open
  standards, so QGIS, Python and R consume them directly with no export step.
- **Work without the browser.** `pip install geodeploy` gives you a command-line client covering the
  whole API — upload many files at once, style with data-driven symbology, build and publish a
  portal, administer the instance. It is also the zero-dependency Python client the QGIS plugin is
  built on.
- **Work as a team.** Roles from viewer to owner, per-layer visibility, invitation links, scoped API
  tokens, and an audit log of who changed what.
- **Operate it from the browser.** Service logs, a terminal, scheduled backups to a separate
  destination, an in-app restore, and one-button updates.

## Where it fits

If you have been looking for one of these, it is the same thing under a different name:

- a **self-hosted alternative to a hosted geoportal** — ArcGIS Online, ArcGIS Hub and the like —
  where the data stays on your server and there is no per-seat licence;
- a **geoportal builder** or small **spatial data infrastructure (SDI)** in one package, rather
  than a tile server, a catalog, a database and a front end assembled and kept in step by hand;
- a **GIS data catalog** with OGC API - Features and STAC endpoints, for publishing open data;
- a **geospatial dashboard** tool, for putting charts and figures around a map.

It does not replace a desktop GIS, and does not try to. QGIS stays where the analysis happens — the
[plugin](https://docs-geodeploy.kndev.org/qgis/) and the
[Python client](https://pypi.org/project/geodeploy/) connect the two.

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
included. **2 GB has been tested and runs well** (2 CPU / 2 GB VPS).

**Add swap, whatever the size of your server.** Most cloud images ship with none at all, and
building the dashboard during an update needs far more memory than running it does — with no swap
the kernel kills the build part-way and the update appears to hang. It takes a minute:

```bash
free -m                                          # Swap total 0? then:
fallocate -l 2G /swapfile && chmod 600 /swapfile
mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab  # survives a reboot
```

More in [Performance tuning](https://docs-geodeploy.kndev.org/performance-tuning/). Disk depends on your data, not on GeoDeploy, and you can attach
S3-compatible storage so capacity is never a server decision. Docker is installed for you if missing; Docker Compose must already be
available (it ships with current Debian/Ubuntu). A domain name is optional but recommended.

## Documentation

Full documentation: **<https://docs-geodeploy.kndev.org/>**

| | |
|---|---|
| [Getting started](https://docs-geodeploy.kndev.org/getting-started/) | Install, set up, publish your first portal |
| [Uploading data](https://docs-geodeploy.kndev.org/uploading/) | Formats, the two vector backends, large files |
| [Portals and experiences](https://docs-geodeploy.kndev.org/portals/) | Web map, story map, catalog, dashboard; templates, layout and access |
| [Web maps](https://docs-geodeploy.kndev.org/web-maps/) | The layer list, layout choices, the tools visitors get |
| [Story maps](https://docs-geodeploy.kndev.org/story-maps/) | Sections, capturing a camera, per-section layers |
| [Catalogs](https://docs-geodeploy.kndev.org/catalogs/) | Facets, what a dataset card shows, portal or instance-wide |
| [Dashboards](https://docs-geodeploy.kndev.org/dashboards/) | Widgets, cross-filtering, linked layers, placement |
| [Users, roles and sharing](https://docs-geodeploy.kndev.org/users-and-sharing/) | Roles, visibility, tokens, audit log |
| [Access from other tools](https://docs-geodeploy.kndev.org/data-access/) | QGIS, DuckDB, Python — which standard for which job |
| [The QGIS plugin](https://docs-geodeploy.kndev.org/qgis/) | Browse an instance, restyle its layers and publish back, from inside QGIS |
| [Command line](https://docs-geodeploy.kndev.org/cli/) | `pip install geodeploy` — upload, style, publish and administer from a shell |
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

Issues and pull requests are welcome — see **[CONTRIBUTING.md](CONTRIBUTING.md)**. Contributing a
template is the easiest place to start.

Commits need a `Signed-off-by` line ([DCO](https://developercertificate.org/)) — `git commit -s`. No
copyright assignment: you keep your work, and there is no future relicensing that would need your
permission.

Developer notes live in each folder's `README.md`; `CLAUDE.md` describes how the repository is
organised and kept current.

## Licence and what stays open

[Apache License 2.0](LICENSE). Every feature is in this repository — no paid tier, nothing held back,
nothing that unlocks with a key.

Apache 2.0 rather than MIT for two reasons that matter to the institutions who run this: it grants
patent rights explicitly, and it does not hand over the GeoDeploy name. Fork it, host it, sell it —
just don't call your service GeoDeploy.

If a hosted **GeoDeploy Cloud** appears, it is **hosting only**: this same open-source code, run for
people who would rather not run a server. It will not have features you cannot get by installing it
yourself.
