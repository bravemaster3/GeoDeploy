---
description: >-
  The GeoDeploy QGIS plugin: browse an instance from inside QGIS, load layers, and push styled layers back up to publish them.
---

# The QGIS plugin

Browse a GeoDeploy instance from inside QGIS, add its layers, restyle them, and publish back —
without exporting anything.

The plugin talks to the same public API as everything else, so nothing is special-cased for it: what
it can see is what your account can see, and what it publishes is what a portal serves. It vendors
the same Python client the [CLI](cli.md) is built on — so anything you can do here, you can also
script. ([Which interface should I use?](api-reference.md#which-interface-should-i-use))

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

### Getting a token

The write buttons — **Upload selected layers**, **Save styling to GeoDeploy**, **Push group to
portal** — are greyed out until the plugin has one. That is the answer to the most common question
about this plugin: they are not unfinished, they are unavailable.

1. Sign in to your instance in a browser.
2. Go to **Settings → API tokens** (every account has this tab, not just admins).
3. **Create token**, give it a name, and choose one with **write** access.
4. Copy it once — it is shown only at creation.
5. Paste it into the plugin's **Token** box and press **Connect** again.

The plugin links straight to that page: when the write buttons are greyed out it says so under
them, with a link to the tokens page of the instance you are connected to.

#### Quick start against the demo

    URL:   https://demo.geodeploy.org
    Token: create one under Settings -> API tokens after signing in

Connect, pick any layer in QGIS, tick **Send its styling too**, and press **Upload selected
layers**. It appears in **My Data** on the instance, styled the way QGIS drew it. Note that the demo
resets hourly, which invalidates tokens — if writes suddenly start failing, make a new one.

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

!!! warning "Why can I not change the symbology *type* of this layer?"
    Which renderers QGIS offers is decided by the **source**, not by a setting, and on a tiled
    source the choice is not available at all. This is a QGIS limitation, not a GeoDeploy one, and
    it cannot be worked around from the plugin.

    * **Vector tiles / PMTiles** open as a `QgsVectorTileLayer`, whose only renderer is the
      rule-based tile renderer. Single symbol, categorized, graduated, heatmap and 2.5D do not
      exist for it — the Symbology tab lets you recolour the rules that are already there and
      nothing more. A tile also carries no attribute statistics, so there is nothing to classify
      *from*.
    * **Server-rendered raster tiles** reach QGIS as one band of RGBA — "Singleband color data" —
      with no bands to stretch and no classes to build. The colours are already painted into the
      picture.

    Both are fixed the same way: select the layer and press **Restyle this layer…**, which reopens
    it *in place* from its data, keeping the styling it already has. Every QGIS renderer applies
    from there, and **Save styling to GeoDeploy** sends it back. You can also choose the data
    surface up front in the **Source** picker, or pick *Editable — each layer from its data* before
    opening a portal.

!!! note "Three things a tiled layer cannot draw"
    The plugin says each of these in the log when it happens, rather than leaving you to notice:

    | Styled in GeoDeploy as | On tiles, QGIS draws | Because |
    | --- | --- | --- |
    | **A heatmap** | the points themselves | `QgsHeatmapRenderer` exists only for a feature layer. A *small* heatmap layer is therefore opened from its data automatically; a very large one keeps its tiles, because the download is the worse surprise. |
    | **3D / 2.5D** | flat | A 3D renderer needs a feature layer. |
    | **A raster algorithm** (contours, hillshade) | the values, with their ramp | TiTiler computes these per tile. QGIS builds contours with a *processing* algorithm that outputs a **vector** layer — there is no raster renderer in between. Open it as tiles to see them. |

    In every case the styling is untouched: it is still stored, still drawn by the portal, and
    pushing the layer back from QGIS will not remove it.

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

!!! info "Which button changes what"

    A layer has **one default style** — what its own page shows, and what a portal starts from when
    you add it — and **a style per portal** that has it. The two buttons write different things, and
    that is on purpose: restyling a layer for one portal should not change how it looks on the
    others.

    | | Writes |
    |---|---|
    | **Save styling to GeoDeploy** | the layer's default style |
    | **Push group to portal** | that portal's styling, for layers already on the instance |
    | **Push group to portal**, for a layer that is *new* | both — an uploaded layer has no default style yet, so the one it arrives with becomes it |

    The confirmation dialog says which of these each layer is getting before anything is sent.

What travels:

- **Vectors** — single symbol, graduated, categorized and **rule-based**; colour, marker shape,
  radius, line width and dash, fill opacity, outline colour and width; size from a field.
- **Rasters** — colour ramp and its direction, stretch, band selection, a colour per pixel value,
  hillshade with its Z factor, and contour interval and line width.

Classification is never recomputed inside the plugin: breaks come from the instance, so a QGIS
legend and a published legend cannot disagree about which feature is which colour.

### A class is more than a colour

A category or a range keeps **its own symbol**, not just its colour — its dash, its width, its fill
opacity, its marker shape and size, its outline. Two categories in the same colour that differ only
by dash arrive as two different lines, in the browser and back in QGIS, and so does a class drawn as
a hollow hatch among classes that are filled.

Where a class differs by something a single map layer cannot vary per feature — a dash above all,
which MapLibre cannot data-drive at all — GeoDeploy draws **one map layer per class**, filtered to
it, exactly as it does for a rule-based layer. Classes that differ only in colour are still drawn as
one layer, so nothing about an ordinary classified map changes.

The legend follows the same rule: a swatch shows the symbol *that class* is drawn with.

### Labels by rule

A **rule-based labelling** travels as its rules, the same way rule-based rendering does. A names
layer that draws water in blue at 9pt, woodland in green and towns in brown at 11 arrives with all
of them — GeoDeploy draws one label layer per rule, filtered to it — and comes back to QGIS as a
rule tree with the filter text you typed. Nested rules are flattened, and a rule whose filter falls
outside the translatable set is left behind with a note rather than labelling everything.

### A size of zero is a size

A symbol you have deliberately sized **0** draws as nothing, here and in the browser, exactly as it
does in QGIS. That matters more than it sounds: a point layer whose markers are sized 0 so that only
its labels show is the ordinary way to build a place-names layer, and a renderer that reads 0 as
"unset" turns it into a field of dots.

## Upload

**Upload selected layer(s)…** sends whatever is selected in the Layers panel, with its styling.
Large files go straight to object storage in parallel parts, so a multi-gigabyte GeoPackage does not
pass through the API.

A layer that cannot be sent is named with the reason rather than failing silently — a remote layer,
or one with unsaved edits.

### Why upload from here rather than from the browser

Because the styling travels. A file does not carry yours in any form GeoDeploy reads — a GeoPackage
saved by *Package Layers* has a `layer_styles` table, a shapefile may have a `.qml` beside it, and
uploading either through the web gives you the data with a generated default style.

Sending the layer from QGIS keeps the symbology, and keeps more of it than the file could. A symbol
GeoDeploy has no vocabulary for — an SVG marker, a font marker, a hatch or pattern fill — is
**rendered by QGIS and sent as an image**, so the portal draws the thing you drew. A style embedded
in a file only names a path to an SVG on the machine that wrote it, which is of no use anywhere
else.

### What an instance accepts

`.gpkg`, `.geojson` / `.json`, `.csv`, `.parquet`, `.tif` / `.tiff`, and `.zip`. A **GeoPackage
holding several layers** is ingested as several layers, not just the first.

A `.zip` is searched all the way down, so a shapefile inside a folder — which is what nearly every
"download this dataset" button produces — is found. Any other single-file dataset the server's GDAL
can read (a GeoPackage, a GeoJSON, a FlatGeobuf, a KML, a GML) is also read from inside a zip, which
is the way to upload a format that is not on the list above. If an archive holds more than one
dataset, the shapefile wins and the rest are named in the job log — upload them separately to get
all of them.

### Rule-based layers

A rule-based layer travels as its rules. Each rule keeps its own filter, its own symbol, its own
label and its own scale range, and GeoDeploy draws one map layer per rule — so a layer with three
rules looks the same in the portal as it does in QGIS. Nested rules are flattened (a child's
condition becomes the parent's *and* its own), and an **else** rule becomes "none of the others".

Rule filters are translated between QGIS's expression language and the map's. That covers the
comparisons, `AND`/`OR`/`NOT`, `IN`, `BETWEEN`, `IS NULL`, `LIKE` with a wildcard at either end,
arithmetic, `CASE WHEN`, and the common string and number functions. **A filter outside that set is
reported and its rule is left behind**, rather than published as a rule that draws everything —
which would be a different map, not a simpler one. The Log Messages panel names the rule and the
part it could not read.

A rule's scale range becomes a zoom range on the published map, so a rule that only draws below
1:10 000 in QGIS only draws at those zooms in the portal.

### Markers QGIS draws and a web map cannot describe

An SVG marker, a raster or font marker, an ellipse, a filled marker, or several markers stacked into
one symbol — these used to arrive in the portal as a coloured dot of the right size, because a web
map has no way to *describe* them.

They now arrive as themselves. The plugin asks QGIS to **render the symbol** and sends the picture,
so the portal draws what you drew. One mechanism covers every kind of marker QGIS has, including
ones it adds later.

Two consequences worth knowing:

- **A picture is one image for every feature.** A bitmap cannot be recoloured per class the way a
  generated shape can, so a classified layer with a picture marker draws the same icon for every
  class. The classification still applies to everything else.
- **Very large symbols are refused rather than shipped.** A style travels in every published
  portal's `style.json`, so a marker that renders past about 96 KB is drawn plainly instead, and the
  Log Messages panel says so.

### Heatmaps and centroid fills

A **heatmap** layer travels as a heatmap: the radius, the weighting column and the colour ramp all
arrive, and the portal draws a density surface rather than a pile of dots. The ramp is sampled at
five stops, because QGIS's may be a gradient, a named scheme or a hand-built list and only the
colours are common to all three.

A **centroid fill** — one marker at each polygon's centre — arrives as a marker layer. The portal
places it at the polygon's *label point*, which sits inside the shape even when it is concave, where
a true centroid can fall outside it.

### Patterned fills

Hatches, cross-hatches and dense fills, line and point patterns, and polygons filled with an SVG or
an image all travel. The portal repeats the same tile at the same spacing.

One honest limit. A repeating tile has to *close* — its right edge must line up with its left — and a
square tile only closes at hatch angles of 0°, 45°, 90° and 135°. A hatch at 30° has no tile that
repeats, so it is drawn at the nearest angle that does, and the Log Messages panel says so. A few
degrees out is a slightly wrong hatch; a tile that does not close is a seam every few pixels, which
reads as a fault rather than a pattern.

A **randomly scattered** fill is drawn as an evenly spaced one at the same density, for the same
reason: randomness has no repeating tile. That one is a different picture, and it is reported as
such.

A patterned polygon in QGIS is usually a plain fill with the pattern stacked on top, and both halves
travel — as with lines, the pattern is looked for across every symbol layer rather than just the
first.

### Markers along a line

Ticks on a boundary, arrows on a river, chevrons on a one-way street — QGIS's marker line and hashed
line repeat a symbol down a line, and the portal draws the same thing: the same picture at the same
interval, **rotated to follow the line** rather than always pointing up the screen.

A decorated line is usually two symbol layers in QGIS — a plain stroke with the markers stacked on
top — and both travel. That combination used to arrive as a plain line, because only the first
symbol layer was ever read.

### Labels

Labels travel. The text (an attribute or an expression), the size, the colour, the halo — QGIS calls
it a buffer — the offset, the rotation, the wrapping, the capitalisation, whether labels may overlap,
their priority and their own scale range all arrive in the portal and go back again.

Two things to know:

- **Fonts are mapped, not carried.** A web map draws text from a glyph set, and a font the set does
  not contain renders as *nothing at all* — no error, no fallback, no text. So a label's font is
  matched to one the portal can actually draw, keeping bold and italic. The Log Messages panel names
  the substitution when it happens.
- **Shadows, background shapes and callouts do not travel.** A web map draws a halo and nothing
  else, and the leader line from a displaced label back to its feature is a second geometry with
  nowhere to go.

Rule-based labelling is read as its first rule, and says so.

### 2.5D

**QGIS → GeoDeploy.** A 2.5D layer arrives as a **real 3D extrusion** — which you can orbit, and
QGIS's pseudo-perspective block cannot be. It is not the same picture: the web map has one colour
and a vertical shading gradient where QGIS has a roof, walls and a shadow. The roof colour becomes
the extrusion's colour; the angle, the wall colour and the shadow are stored, so opening the layer
in QGIS again gives you 2.5D back rather than a plain extrusion to rebuild.

**GeoDeploy → QGIS.** Tick *3D — raise these polygons* on a polygon layer in GeoDeploy and it opens
in QGIS as **2.5D**, whether or not it ever came from QGIS. You get two renderers, not one:

* the **2.5D renderer** on the ordinary 2D canvas, so the layer reads as raised straight away;
* a **3D renderer**, so *View → New 3D Map View* shows the true extrusion you can orbit.

A height driven by a **field** travels as an expression (`"Height" * 100`), so the buildings vary
the way they do on the web rather than arriving as one flat slab.

Two layers keep the flat renderer instead, because `Qgs25DRenderer` cannot express them and
replacing what they have would lose more than it gains:

* a **classified** layer — 2.5D is single-symbol, so converting it would throw the classes away;
* a **point or line** layer — 2.5D extrudes polygon rings. GeoDeploy draws extruded points as
  pillars, which is a different shape.

Both still get the 3D renderer, so the height is there in a 3D map view.

The height and viewing angle are **project settings** in QGIS, not layer ones, so changing them
changes every 2.5D layer in the project — GeoDeploy stores them per layer, which is the one place
the two models genuinely differ. The height is in the project's map units and GeoDeploy reads it as
metres; those agree in a projected CRS and not in a geographic one.

## What does not travel

Most of QGIS's symbology reaches GeoDeploy exactly, and a good deal of the rest reaches it as a
stated approximation. This is the list of what does **not**, and why — so that a map that comes out
different is a thing you were told about rather than a thing you have to discover.

**The plugin says these out loud when you push.** None of them makes a push fail; that is exactly
why they are written down. A push that succeeds and quietly changes the map is the failure worth
guarding against.

### Impossible in a web map, and unlikely to change

| QGIS | What happens instead |
|---|---|
| **Blend modes** (Multiply, Screen, Overlay…) on a layer or between its features | Drawn **Normal**. MapLibre has no blend modes at all — every "blend" in its style spec is sky, fog or atmosphere. Lowering the layer's opacity is the nearest thing. |
| **Geometry generators** | Not carried. These are arbitrary expressions that produce *new geometry*; there is nothing to translate them into. The QML is kept so QGIS gets them back. |
| **Shapeburst fill** | A distance transform inside each polygon. Out of reach; the QML is kept. |
| **Mask markers** | A clipping mask, with no MapLibre equivalent. Carried, not drawn. |
| **Embedded (per-feature) symbols** | A vector tile has no way to carry a different symbol per feature. Carried, not drawn. |
| **Draw effects** (blur, drop shadow, glow) | MapLibre exposes none of them. |

### Possible, but not built

| QGIS | Why not yet |
|---|---|
| **Interpolated line** (a colour ramp *along* a line) | Needs MapLibre's `line-gradient`, which the style spec permits **only** on GeoJSON sources with `lineMetrics: true` — a vector tile clips features at tile boundaries, so no tile knows the whole line's length. GeoDeploy's own layers are vector tiles. An external GeoJSON layer could do it. |
| **Linear referencing** (chainage labels along a line) | Labels exist now, and that is not enough: `symbol-placement: line` repeats the *same* text at every placement, and distance-along-the-line is not something MapLibre can compute. The values would have to be generated server-side as point features carrying their measure. |
| **Point clustering** | GeoDeploy clusters, but at **tiling** time rather than in the style — so a QGIS cluster renderer cannot switch it on. Tick *Cluster points* on the layer and re-tile. |
| **Inverted polygons** | Would need a server-derived mask (the layer's union subtracted from the world). Until then the polygons themselves are drawn — the inverse of the picture. |
| **Point displacement** | No web equivalent. Clustering is the nearest honest approximation. |
| **Merged features** | Drawn as the underlying symbol, so the joins QGIS dissolves stay visible. |
| **A layer's own scale range** | Not carried yet, although a *rule's* is. |

### Sizes, and the unit they are stated in

QGIS states every size — a marker's diameter, a line's width, a label's height — in a unit the
symbol itself carries, and its **default is millimetres**. GeoDeploy states them in CSS pixels.

The plugin converts, so a 10 mm marker becomes a radius of 18.9 px and 10 pt becomes 6.67. On the
way back it writes **points** explicitly, so a layer styled in GeoDeploy has an unambiguous size in
QGIS rather than one that depends on a default.

Two units cannot be converted at all, because they depend on the map: **map units** and
**metres in map units** mean a different number of pixels at every zoom. A symbol measured in
either keeps its number and is drawn at that many pixels, which is right at one scale only.

### Approximated, and reported as such

These do travel, but not identically. Each is a deliberate choice with the reasoning recorded in
`scripts/coverage_report.py`, which is checked against QGIS's own registries so the list cannot
drift from what the plugin does:

- **Gradients** — fill, lineburst and shapeburst read as the **middle** of their ramp. MapLibre has
  no gradient of any kind, so one flat colour is all there is; the midpoint is the closest one.
- **Raster lines** — a line stroked with an image becomes the average of that image's opaque pixels.
- **Filled lines** — a buffered-and-filled line becomes an ordinary line in the fill's colour.
- **Line pattern fills** — the angle is snapped to 0/45/90/135°, the only angles at which a square
  tile closes. A seam every tile is worse than a few degrees.
- **Random marker fills** — drawn as a regular grid at the same density; randomness has no
  repeating tile.
- **Arrow lines** — the head is rebuilt and repeated along the line. QGIS draws one arrow per
  feature and MapLibre cannot place an icon at a line's end, and the shaft does not taper.
- **Vector fields** — rendered as a picture, so the layer draws, but the arrows cannot follow the
  data per feature.
- **Simple markers** — six of QGIS's shapes are native; anything else is rendered to an image.
- **2.5D** — becomes a real 3D extrusion. Its viewing angle and shadow have no MapLibre equivalent;
  both are stored so they survive the trip back to QGIS.
- **3D units are not converted.** GeoDeploy's heights and radii are metres; QGIS measures in the
  project's map units. Those agree in a projected CRS and do not in a geographic one.

### Other

- **3D extrusion cannot be edited in QGIS.** It is stored, rendered by GeoDeploy, and carried safely
  — opening an extruded layer and pushing it back does not remove it — but QGIS shows those polygons
  flat.
- A raster must be uploaded from a local file: re-encoding one would mean choosing compression and
  resampling on your behalf, and ingest converts to a COG anyway.

The full table, generated from QGIS's registries rather than written by hand, is
`integrations/qgis-plugin/scripts/coverage_report.py`. It fails CI when this QGIS offers something
the table does not classify, so a new QGIS version forces a decision instead of quietly widening a
gap.

### If a token stops working

An upload that worked five minutes ago and refuses now is almost always one of two things, and
neither is obvious from `HTTP 401`: the token was revoked, or the instance is a **demo** and has
been reset since. The plugin says so rather than showing the status code, on every action that
writes.

On the official demo the reset is hourly, on the hour, and it deletes tokens along with everything
else. The Settings → API tokens page says so too, on a demo instance.

## If something looks wrong

The plugin explains itself in **View ▸ Panels ▸ Log Messages**, under the **GeoDeploy** tab. A style
that could not be applied, a source that fell back to a slower one, a ramp QGIS has no name for —
each says what happened and what to do about it.
