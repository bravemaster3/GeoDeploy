# geodeploy

**Command-line client and Python API for [GeoDeploy](https://github.com/bravemaster3/GeoDeploy)** —
the self-hosted spatial data platform and geoportal builder.

Upload data, style it, build a portal and publish it, without opening a browser:

```bash
pip install geodeploy

geodeploy login https://geodeploy.example.org --token gdp_…
geodeploy upload roads.gpkg sites.csv dem.tif --wait
geodeploy portals create "Field sites 2026" --publish
```

**No dependencies. Python 3.9+.** Every request goes through the standard library, so installing
this pulls in nothing else — and a QGIS plugin can vendor the same client without asking anyone to
pip-install into QGIS.

## What it covers

- **Uploads** — shapefile, GeoPackage, GeoJSON, CSV (X/Y or WKT), GeoParquet, GeoTIFF. Many files
  in one command; the route is chosen per file, and anything over 48 MB goes direct to object
  storage in parallel presigned parts, so a multi-gigabyte upload survives a proxy that caps
  request bodies.
- **Layers** — list, inspect, rename, share (STAC / OGC API - Features opt-in), share links per
  tool, download, delete, restart a stalled ingest.
- **Symbology** — colour, opacity, outlines, dashes, markers, raster colormaps and stretches, plus
  data-driven styling: `--color-field pop --classify jenks --classes 6 --ramp magma`, proportional
  size, and attribute-driven 3D. Classification is computed by the instance, with the same code the
  portal editor uses, so the CLI and the editor can never disagree about a class.
- **Portals** — create (web map, story map or catalog), arrange and style layers, folders, About
  page, assets, access tiers, publish, and a whole-configuration JSON round trip for version
  control.
- **Everything else** — external WMS/XYZ/WFS services, registering data already in PostGIS or the
  bucket, the public STAC and OGC API - Features catalog, ingest jobs, users, and instance
  administration (health, services, updates, backups, activity log).

Every command takes `--json`, where stdout is exactly one JSON document, and exit codes distinguish
authentication (3) from network (4) from server (5) so a scheduled job can alert on the right thing.

## As a library

```python
from geodeploy import Client

gd = Client("https://geodeploy.example.org", token="gdp_…")
result = gd.uploads.upload("roads.gpkg", wait=True)
portal = gd.portals.create("Roads")
gd.portals.add_layer(portal["id"], result.layer_id, "vector", {"color": "#e11d48"})
gd.portals.publish(portal["id"])
```

Typed exceptions (`AuthError`, `PermissionError_`, `NotFoundError`, `ValidationError`,
`ServerError`, `TransportError`, `JobFailed`), progress callbacks and cancellation on uploads, and a
swappable transport so a desktop application can route requests through its own network stack.

## Documentation

Full guide: **<https://docs-geodeploy.kndev.org/cli/>**

Source and issues: **<https://github.com/bravemaster3/GeoDeploy>**

Licensed under the Apache License 2.0.
