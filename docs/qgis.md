# The QGIS plugin

Browse a GeoDeploy instance from inside QGIS, add its layers, restyle them, and publish back —
without exporting anything.

The plugin talks to the same public API as everything else, so nothing is special-cased for it: what
it can see is what your account can see, and what it publishes is what a portal serves.

## Which QGIS you need

| | |
| --- | --- |
| **Minimum** | QGIS **3.28** |
| **Recommended** | the current **LTR** (Long Term Release) — `Help ▸ About` tells you what you have |
| **GDAL** | 3.8+ *only* for one optional path (see below); QGIS ships its own, so this is rarely something you choose |

3.28 is the floor because that is where the two providers this plugin leans on — **vector tiles**
and **OGC API - Features** — are dependable. QGIS refuses to install a plugin below its declared
minimum, so an older QGIS will simply not offer it.

Anything newer is fine. If you are choosing, take the LTR rather than the latest: the plugin's job
is to hand QGIS ordinary layers, and the LTR is the build least likely to change what "ordinary"
means underneath it.

!!! info "The one version-dependent feature"
    Opening a tiled layer's whole **PMTiles archive** needs **GDAL ≥ 3.8**, which is where the
    PMTiles driver arrived. The plugin checks at runtime and quietly uses another source when it is
    missing, so nothing breaks — that path is a last resort anyway.

## Install

### From the QGIS plugin repository

The plugin is published as **experimental**, which means QGIS hides it by default. Turn that off
once:

1. **Plugins ▸ Manage and Install Plugins ▸ Settings**
2. Tick **Show also experimental plugins**
3. Go back to **All**, search for **GeoDeploy**, and press **Install Plugin**

If you search without that setting the plugin will not appear at all, which looks exactly like it
not being published.

!!! note "Why experimental"
    It marks a plugin as young rather than broken: the interface may still move between versions.
    The flag comes off once it has been used in anger for a while.

### From a ZIP

Download `geodeploy_qgis-<version>.zip` from the
[latest release](https://github.com/bravemaster3/GeoDeploy/releases/latest), then
**Plugins ▸ Manage and Install Plugins ▸ Install from ZIP**. The experimental setting does not
affect this route.

There is nothing to `pip install`: the Python client is vendored inside the plugin, which is why it
has no dependencies and runs on the Python that ships with QGIS.

## Connect

Open **GeoDeploy** from the Plugins menu (or the toolbar) and paste your instance URL —
`https://your-instance.org`. Press **Connect**.

**An account is optional.** With no token the plugin reads the instance's public index, so pasting a
URL shows every public layer and portal. A token adds whatever else you can see, and is required for
anything that writes: uploading, saving a style, pushing a portal.

If you have already run `geodeploy login` at a shell, the plugin finds that token by itself.

## Add a layer

Select a layer and press **Add to map**. It arrives styled as GeoDeploy draws it.

### Choosing a source

A GeoDeploy layer is published through several surfaces at once, and they are not interchangeable.
The **Source** picker shows what the selected layer offers, and opens on the sensible default:

| Layer | Default | The other option |
| --- | --- | --- |
| **PostGIS vector** | OGC API - Features — every attribute, ready to classify by a field | Vector tiles, if a large layer feels slow |
| **Tiled GeoParquet** | Vector tiles — generalized per zoom, fetched for the view | Full features |
| **Raster** | Server-rendered tiles — coloured exactly as GeoDeploy draws it | The GeoTIFF itself, with real pixel values |

The defaults differ because the backends are used for different things: PostGIS holds the layers
people classify, and tiled GeoParquet holds the ones too large to read whole.

!!! tip "Why can I not classify this layer?"
    Which renderer QGIS offers is decided by the **source**, not by a setting. Server-rendered
    raster tiles reach QGIS as one band of RGBA — "Singleband color data", with no bands to stretch
    and no classes to build — and vector tiles have no categorized or graduated renderer at all.

    Switch the **Source** to the data surface, or select the layer and press **Restyle this
    layer…**, which reopens it from its data *in place*, keeping the styling it already has.

## Open a portal

Select a portal and press **Open portal as a group**. Every layer arrives in the portal's own order,
folders and opacity, styled as the portal styles it — which is not always how the layer is stored,
and that difference is the point.

The **Source** picker offers a portal two ways:

- **As the portal draws it** — the published tiles. Fastest, and exactly what a visitor sees.
- **Editable — each layer from its data** — every layer opened from its own data and *then* painted
  with the portal's styling. Slower to draw, and the whole of QGIS's symbology applies.

Restyle the group and press **Push group to portal**. A group opened from a portal updates that
portal; any other group creates a new one. Nothing is published until you have read the summary of
what will change.

## Styling, both ways

A layer opens looking like the portal, and what you change in QGIS goes home — press **Save styling
to GeoDeploy** (the layer's default style) or **Push group to portal** (that portal only).

What travels:

- **Vectors** — single symbol, graduated and categorized; colour, marker shape, radius, line width
  and dash, fill opacity, outline colour and width; size from a field.
- **Rasters** — colour ramp and its direction, stretch, band selection, a colour per pixel value,
  hillshade with its Z factor, and contour interval and line width.

Classification is never recomputed inside the plugin: breaks come from the instance, so a QGIS
legend and a published legend cannot disagree about which feature is which colour.

## Upload

**Upload selected layer(s)…** sends whatever is selected in the Layers panel, with its styling.
Large files go straight to object storage in parallel parts, so a multi-gigabyte GeoPackage does not
pass through the API.

A layer that cannot be sent is named with the reason rather than failing silently — a remote layer,
or one with unsaved edits.

## Known limitations

- **3D extrusion is not drawn.** A layer's extrusion is stored and rendered by GeoDeploy as usual,
  and the plugin carries it safely — opening an extruded layer and pushing it back does not remove
  it — but QGIS shows those polygons flat, so 3D cannot be edited here yet.
- **Symbology QGIS has and GeoDeploy does not** — inverted polygons, 2.5D, hatch and gradient fills,
  line offsets, markers along a line, multi-layer symbols, rule-based rendering, labels — is
  simplified on the way in and not carried back.
- **3D units are not converted.** GeoDeploy's heights and radii are metres; QGIS 3D measures in the
  project's map units. Those agree in a projected CRS and do not in a geographic one.
- A raster must be uploaded from a local file: re-encoding one would mean choosing compression and
  resampling on your behalf, and ingest converts to a COG anyway.

Both symbology gaps are tracked as *Every symbol QGIS can draw* on the [roadmap](roadmap.md).

## If something looks wrong

The plugin explains itself in **View ▸ Panels ▸ Log Messages**, under the **GeoDeploy** tab. A style
that could not be applied, a source that fell back to a slower one, a ramp QGIS has no name for —
each says what happened and what to do about it.
