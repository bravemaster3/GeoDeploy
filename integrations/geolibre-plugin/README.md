# Publish to GeoDeploy — a GeoLibre plugin

A GeoLibre plugin that sends the **current project** to a [GeoDeploy](../../) instance and publishes
it as a hosted portal. Layers, symbology, attribute 3D (extrusion), COG rasters, XYZ/WMS tiles, and
the story map are all translated **on the GeoDeploy side** (see `api/geodeploy/services/geolibre_import.py`),
so this plugin stays thin: it grabs the project and POSTs it.

## How it works

```
GeoLibre  ──(getProjectSnapshot → .geolibre.json)──▶  POST /api/interop/geolibre/{preview,publish}
 (plugin)      Authorization: Bearer <GeoDeploy API token>          (GeoDeploy)
```

- **Preview** → `POST /api/interop/geolibre/preview` — a dry run; shows what would import (per-layer
  target/render mode + any symbology that won't carry over). No writes.
- **Publish** → `POST /api/interop/geolibre/publish` — ingests each layer and builds the portal;
  returns the public portal URL.

### Round-trip a layer (edit a GeoDeploy layer, save it back)

The panel's **Round-trip a layer** section closes the loop for data cleaning etc.:

- **List layers / Load selected** → `GET /api/interop/geodeploy/layers` then
  `GET /api/interop/geodeploy/layers/{id}/features.geojson` — loads a GeoDeploy PostGIS layer into
  GeoLibre as an editable GeoJSON layer (the plugin remembers GeoLibre-layer ↔ GeoDeploy-layer).
- Edit it in GeoLibre (GeoEditor, attribute edits, processing tools).
- **Save edits back** → `PUT /api/interop/geodeploy/layers/{id}/features` — writes the edited GeoJSON
  back; GeoDeploy re-ingests it into the **same** table, so published portals using it update too.

Loading needs the host's `addGeoJsonLayer` (present in the live app); saving needs `getProjectSnapshot`
(the same upstream API as Publish). The links persist with the project.

## Prerequisites

1. **A GeoDeploy instance** you can reach over HTTPS, and an **API token** with the `portal:write`
   scope (GeoDeploy → Settings → API tokens). Paste the instance URL + token into the plugin panel.

2. **The GeoLibre project-snapshot host API.** For full fidelity (all styling + 3D-Z + the story map)
   the plugin needs `app.getProjectSnapshot()` — the host handing the whole project to the plugin.
   GeoLibre already computes this internally (`serializeProject(buildProjectSnapshot(...))`); exposing
   it to plugins is a small upstream addition (tracked as GeoDeploy interop **Front 4**). Until a
   GeoLibre build ships it, the plugin disables Publish and says so (it deliberately does **not** fall
   back to a styleless 2D export).

3. **CORS.** The plugin runs in GeoLibre's browser and calls GeoDeploy cross-origin. The GeoDeploy
   instance must allow the GeoLibre origin on `/api/interop/*`. Auth is a Bearer token (not cookies),
   so this is safe to enable. (Deployment-specific — configure it on the GeoDeploy side.)

## Build

```bash
npm install
npm run build        # → geolibre-plugin/dist/{index.js,style.css}
npm run typecheck    # optional: tsc --noEmit
```

## Install into GeoLibre

The build produces the loadable bundle under `geolibre-plugin/` (`plugin.json` + `dist/`). GeoLibre
loads it any of the documented ways (see the GeoLibre plugin docs):

- **Unpacked directory** — point GeoLibre Desktop's Settings → Plugins at this `geolibre-plugin/` folder.
- **Manifest URL** — host `geolibre-plugin/` and give GeoLibre the `plugin.json` URL.
- **Zip** — zip the `geolibre-plugin/` folder (root `plugin.json`) and install from file.

Then open **GeoDeploy → Publish to GeoDeploy…** from the toolbar (or the right-panel rail).

## Layout

| File | Purpose |
| --- | --- |
| `src/host-api.ts` | The slice of the GeoLibre host contract used here (+ the optional `getProjectSnapshot`). |
| `src/publish.ts` | Transport: `collectProject` (snapshot), `previewProject`, `publishProject`. |
| `src/panel.ts` | The right-panel DOM UI (URL/token, Preview, Publish, results). |
| `src/geolibre.ts` | The `GeoLibrePlugin` entry — registers the panel + toolbar menu, persists settings. |
| `geolibre-plugin/plugin.json` | The loadable manifest (points at `dist/`). |

Scaffolded from `opengeos/geolibre-plugin-template`.
