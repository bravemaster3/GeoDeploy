---
hide:
  - navigation
  - toc
---

<div class="gd-hero" markdown>

# Your data. Your server. Your map.

<p class="gd-sub" markdown>
GeoDeploy is an <strong>open-source</strong>, self-hosted spatial data platform and geoportal
builder. Upload your data, style it, and publish a map anyone can use — with your own domain, your
own storage, and no per-seat pricing.
</p>

[Try the demo](https://geodeploy-demo.kndev.org){ .md-button .md-button--primary }
[Get started](getting-started.md){ .md-button }
[See what it publishes](#three-kinds-of-portal){ .md-button }

<p class="gd-demo-note" markdown>
No sign-up — join with a name and you can upload, style and publish. Everything is wiped hourly.
</p>

<div class="gd-install" markdown>
```bash
curl -fsSL https://raw.githubusercontent.com/bravemaster3/geodeploy/main/installer/install.sh | bash
```
</div>

</div>

<figure class="gd-shot gd-shot-lead" markdown>
![A published GeoDeploy web map](assets/portal-webmap.png)
<figcaption>A published portal. Your layers, your branding, its own URL — nothing for visitors to install.</figcaption>
</figure>

One command installs the whole stack on a single Linux server: a spatial database, object storage,
vector and raster tile services, a web dashboard, and the portals you publish from it. A setup wizard
handles the rest.

## Try it without installing anything

[**geodeploy-demo.kndev.org**](https://geodeploy-demo.kndev.org) is a live GeoDeploy. Join with a
name — no email, no password — and you can upload data, style it, and publish a portal.

Two things to know before you do: everyone trying it shares **one workspace**, so treat anything you
put there as public and changeable by others; and the whole instance is **wiped every hour, on the
hour**, so nothing you build survives. It runs the same code this page tells you how to install,
with demo mode switched on.

## Three kinds of portal

The experience you choose changes the shape of the page, not just its colours.

<div class="gd-tiles" markdown>

<figure markdown>
![Web map portal](assets/portal-webmap-2.png)
<figcaption>**Web map** — map-first, with a layer list beside it. For when the map is the point.</figcaption>
</figure>

<figure markdown>
![Story map portal](assets/portal-storymap.png)
<figcaption>**Story map** — scrollytelling. Each section is pinned to a camera and a set of layers.</figcaption>
</figure>

<figure markdown>
![Catalog portal](assets/portal-catalog.png)
<figcaption>**Catalog** — search and facets beside the map, for more datasets than one map should hold.</figcaption>
</figure>

</div>

[How portals work](portals.md){ .md-button }

## Get your data in

Shapefile, GeoPackage, GeoJSON, CSV, GeoParquet and GeoTIFF. Large files upload straight to object
storage, so a multi-gigabyte dataset is not a special case.

<div class="gd-tiles gd-tiles-2" markdown>

<figure markdown>
![Uploading data](assets/upload-browse.png)
<figcaption>Drag a file in. Validation, reprojection and tiling happen in the background.</figcaption>
</figure>

<figure markdown>
![My Data](assets/my-data.png)
<figcaption>Everything you have, with its status, size and storage backend.</figcaption>
</figure>

</div>

[Uploading data](uploading.md){ .md-button }

## Describe it once, publish it everywhere

Fill in an abstract, keywords and a licence, and that metadata drives portal search, the About page,
and the open standards other tools read.

<div class="gd-tiles gd-tiles-2" markdown>

<figure markdown>
![Layer metadata](assets/my-data-2.png)
<figcaption>Catalog metadata per layer, and the access links that come with it.</figcaption>
</figure>

<figure markdown>
![Portal About page](assets/about-page.png)
<figcaption>A documentation page published beside the map.</figcaption>
</figure>

</div>

Shared layers are readable by standard clients over **OGC API - Features**, **STAC**,
**Cloud-Optimized GeoTIFF**, **PMTiles** and **GeoParquet** — so QGIS, Python and R consume them
directly, with no export step.

[Access from other tools](data-access.md){ .md-button }

## Run it without a terminal

Service logs, backups to a separate destination, an in-app restore, and one-button updates.

<div class="gd-tiles" markdown>

<figure markdown>
![Infrastructure panel](assets/settings-infrastructure.png)
<figcaption>Every service, its logs, and its state.</figcaption>
</figure>

<figure markdown>
![Updates](assets/settings-updates.png)
<figcaption>Check for a new version and update in place.</figcaption>
</figure>

<figure markdown>
![Activity log](assets/activity-log.png)
<figcaption>Who changed what, kept even after an account is removed.</figcaption>
</figure>

</div>

[Updating](updating.md){ .md-button } [Backups and restore](backups.md){ .md-button }

## Work as a team

Roles from viewer to owner, per-layer visibility, invitation links and scoped API tokens.

<figure class="gd-shot" markdown>
![User management](assets/users.png)
<figcaption>Invite by link — no mail server required.</figcaption>
</figure>

[Users, roles and sharing](users-and-sharing.md){ .md-button }

## Open source, and yours to run

GeoDeploy is open source. You can read every line, change it, and run it wherever you like — there is
no paid tier that unlocks features, and no arrangement where your data lives somewhere you do not
control.

A hosted **GeoDeploy Cloud** is coming for people who would rather not run a server themselves, or who
only need a handful of layers and would rather share the hosting cost than pay for a whole VPS. It
changes nothing here: the project stays open source, and self-hosting stays the full product.

## Runs on a small server

**4 GB of RAM is a comfortable recommendation**, and two CPU cores are plenty — a real instance runs
happily there, tiling included. **2 GB works too**: the same stack has been tested on a 2 CPU / 2 GB
VPS and runs well. Whatever the size, give the machine some swap — most cloud images have none, and
building the dashboard during an update needs far more memory than running it does. Disk depends on your
data rather than on GeoDeploy, and you can point it at S3-compatible storage so capacity is never a
server decision. Everything is Docker Compose behind nginx: one thing to start, one thing to update.

<div class="gd-next" markdown>

| If you want to… | Read |
| --- | --- |
| Install it and publish something | [Getting started](getting-started.md) |
| Understand the portal types | [Portals and experiences](portals.md) |
| Get your data into QGIS or DuckDB | [Access from other tools](data-access.md) |
| Script against it | [API reference](api-reference.md) |
| Keep it backed up | [Backups and restore](backups.md) |
| Tune it for heavy layers | [Performance tuning](performance-tuning.md) |
| See what is planned | [Roadmap](roadmap.md) |
| Check the licence | [Apache 2.0](licence.md) |

</div>
