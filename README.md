# GeoDeploy

**Self-hosted spatial data management and geoportal builder.**

Install on your VPS. Upload your data. Publish a live geoportal. Own everything.

> GeoDeploy is to spatial data what Coolify is to app deployment — a control panel that makes complex infrastructure simple, running entirely on your own server.

---

## The problem

A GIS coordinator at a development project in Cotonou has PostGIS data and GeoTIFFs. They need a live public portal as a project deliverable. They have a modest VPS budget. They cannot hire a developer.

Existing tools require deep technical knowledge (GeoNode, GeoServer) or charge enterprise prices and take data custody (ArcGIS Online, CARTO).

GeoDeploy should take this person from a blank VPS to a published portal in under 30 minutes.

## What it does

- **One-command install** — `curl install.sh | bash` and a browser opens
- **Browser-only management** — no terminal, no Docker knowledge, no database configuration
- **Vector upload** — Shapefile, GeoJSON, GeoPackage → PostGIS → live MVT tiles via Martin
- **Raster upload** — GeoTIFF → auto Cloud-Optimised GeoTIFF → MinIO → live XYZ tiles via TiTiler
- **Portal builder** — drag layers, pick a template, set access control, click Publish
- **Template system** — official + community templates, all MIT-licensed
- **Embeddable** — one `<iframe>` line to embed any portal in any website

## Quick install

```bash
curl -fsSL https://raw.githubusercontent.com/bravemaster3/geodeploy/main/installer/install.sh | bash
```

Requires: Linux VPS, Docker + Docker Compose. Tested on Hetzner CX31 (€11/month).

## Stack

| Layer | Technology |
|-------|-----------|
| API | FastAPI + Celery + Redis |
| Database | PostGIS 16 (provisioned automatically) |
| Vector tiles | Martin (Rust) |
| Raster tiles | TiTiler |
| Object storage | MinIO (S3-compatible, provisioned automatically) |
| Analytics | DuckDB (embedded) |
| Frontend | Vue 3 + MapLibre GL JS + deck.gl |
| Infrastructure | Docker Compose, Nginx |

## License

MIT — free forever, no feature restrictions on self-hosted version.

Optional [GeoDeploy Cloud](https://geodeploy.io) hosting for teams who want managed infrastructure.

---

[Getting started](docs/getting-started.md) · [API reference](docs/api-reference.md) · [Template contributing](templates/community/CONTRIBUTING.md)
