# Changelog

Notable changes, newest first. Versions are **major.minor** — `v1.0`, `v1.1`, … `v1.9`, then
`v2.0`. The minor number moves for anything shipped, features or fixes; the major changes when an
upgrade needs manual work.

## Unreleased

## v1.5.1 — 2026-08-31

A fortnight of building real dashboards with v1.5. Almost everything here is something that could
not be said, sized or read until someone tried to author a board with it. No migration, no
configuration change: publish a portal again and it picks all of this up.

### Charts you can author

- **Wide data plots.** Let the **columns** be the X axis, for data stored one column per year or
  period (`gdp1960`, `gdp1961`, …) rather than one row per observation. Up to 120 columns, with a
  select-all rather than 120 clicks, and the groups become the series.
- **Tick labels from column names.** Column names rarely read as axis labels, so the shared prefix
  can be trimmed to leave the numbers and an **offset** applied when the numbers are not the values
  meant (`gdp1` + 1959 reads as `1960`).
- **An overall line** across the whole current selection on a multi-column chart, to read the
  individual series against.
- **Axis titles and widget subtitles are yours to write**, and the card's heading follows them. A
  column name is what the data is called, not what it measures — and after a shapefile has cut it
  to ten characters it is often neither.
- **Plot a chosen column per group.** The measure field could not be picked alongside a grouping
  field, so "GDP per country" was unaskable and only counts and averages were offered.
- **Readable axes.** Gridlines and ticks at intervals on line and scatter plots rather than only
  the two extremes; rotated category labels that no longer straddle their ticks; a histogram that
  names the range it covers.
- **The legend gets its room before the plot does** — on the multi-series line, the grouped bars
  and the pie, all of which drew at `height: 100%` and pushed their key into the card's scrollbar.
  Making the widget taller grew the plot by exactly as much, so height never helped. **Plot size**
  now overrules the measured split on any chart with a key, not only a pie.
- **The gauge** no longer clips its own arc: its box is derived from the arc's geometry instead of
  being a constant that stopped matching it.
- Bar-only controls are hidden on line charts, where they changed nothing.

### Reading a dashboard

- **Several table rows at once** — <kbd>Ctrl</kbd>/<kbd>Cmd</kbd> adds and removes, <kbd>Shift</kbd>
  takes the run between. The map fits the **whole** selection rather than the row last clicked, and
  the selection survives a page turn.
- **A scatter can say which feature a dot is**: hover labels from up to three columns you nominate.
  An outlier is the point of a scatter and was the one thing it could not tell you about. Point
  size is adjustable too — 2px read as dust on a small card.
- **A new selection replaces the last** rather than compounding with it. Drawing a box after
  clicking a feature was asking for the features that are both, and getting none.
- **Zoom to what a chart selected**, so the filter and the view agree. On by default for chart
  clicks, off per widget; map clicks never move the camera.
- **Fill the screen**, per dashboard: the grid rows share the height of the window instead of each
  being exactly the row height, so a board laid out on a laptop does not stop a third of the way
  down a large monitor. It stretches but never squeezes — the row height stays the floor — so a
  phone or a portrait display scrolls exactly as before.
- A **filter that matched nothing** because the clicked geometry had zero area now matches: a
  point round-tripped through GeoJSON is precision-fragile against an exact `intersects`.

### On the map

- **Basemaps that are free without an account.** The three CARTO styles asked for an API key;
  they are replaced, and **OpenStreetMap now leads the list** as the default.
- **The selection tools can be dragged** clear of whatever they overlap, by the visitor, on a
  published portal.
- **The coordinate readout names its CRS** (`EPSG:4326`) — two numbers alone are ambiguous — and
  now sits at the **bottom centre**, sharing one line with the scale bar and the attribution. The
  scale bar had floated above the credit on every map in the product since the first shared-runtime
  refactor.
- **Both map readouts are legible in dark mode.** Each took one colour from the theme and one from
  nowhere.
- Drawing on a phone no longer opens the attribute table on every tap.

### Portals, everywhere

- **A dashboard's card thumbnail is the whole dashboard**, not the map cell inside it — two boards
  over the same layer used to get the same picture.
- **A plain map is no longer refused as a blank one.** The capture check was a byte floor, and a map
  zoomed in far enough to show no detail is a real picture that compresses to almost nothing.
- **The legend opens beside the map** rather than at the top of the page on a phone, sizes itself to
  its content, and "start collapsed" now works for a docked list.
- **A big number shrinks to fit its card** instead of overflowing it.
- **A dragged widget no longer lands on top of another.**
- The loading cover is held until the widgets have answered, so a dashboard does not appear empty
  before it appears full.
- **View on GitHub** in the sidebar, on the demo instance only.

## v1.5 — 2026-08-29

### Dashboards — a fourth experience

- **A dashboard is a grid of widgets that cross-filter each other**, with the map placed into a
  cell rather than the page arranged around it. Indicator, gauge, chart, list/table, column
  profile, scatter, selector, search box, details panel, legend, raster statistics and the map.
- **Three filter channels.** An attribute predicate reaches widgets on the same layer; a geometry —
  a click, a drawn polygon, a box, or the map's extent — reaches every widget whatever layer it
  reads; a selection fills the details panel. They combine with AND, and each shows as a chip that
  can clear itself.
- **Linked layers.** Declare that two layers share a column and an attribute filter travels between
  them — clicking a canton in a buildings chart narrows the entrances list. The filter is pushed
  into the engine as a subquery rather than passed around as a list of ids, because narrowing 3.4M
  buildings to one canton yields about 477k keys. A link between differently-stored layers is
  refused with a reason rather than half-applied.
- **The map narrows every layer it draws**, each by its own filters — not only the layer it
  hit-tests clicks against. Those are two different questions about the map widget's bound layer,
  and answering the second with the first left a layer unfiltered on screen while every widget
  reading it had narrowed.
- **A linked filter can narrow the map too, if you ask.** _Follow linked-layer filters_ on the map
  widget, off by default, resolves a relation to the keys it matches so the map can test features
  against them. Capped at 5 000 keys; past the cap the map is left unfiltered and says so on
  screen, so "nothing matched" is never mistaken for "too many matched to draw".
- **The linked-filter key limit is an author's choice** — 1 000, 5 000, 10 000 or 20 000 per map —
  and the request that fetches those keys now asks for keys alone, about a sixth of the bytes the
  grouped answer carried. This also fixes the limit being silently clamped to 200 server-side, which
  made the map stop narrowing at 201 features while reporting that it had passed 5 000.
- **Bar-chart category labels tilt and thin more honestly**, ending at the tick they name rather
  than straddling it, and dropping only as many as they must: 26 categories on a wide card now show
  all 26 labels instead of 13.
- **A pie or donut's plot can be sized independently of its widget**, so a long legend has room to
  be read instead of ending up in a scrollbar.
- **The map now says when a GeoParquet layer is not being narrowed.** Those layers draw through
  deck.gl and have never been filterable on the map, for any kind of filter — but the map drew them
  whole and said nothing, so an unfiltered layer and an empty result looked the same. It now names
  them.
- **Charts that carry their weight.** Several measures against one grouping (multi-line, clustered
  bars), one colour per category or a shaded ramp for an ordered key, printed values with shares,
  and a scatter of one numeric column against another — randomly sampled, and it says so.
- **A column profile** describing what is in the selected data: completeness, distinct counts, and
  either a range or the commonest values.
- **Widgets can be pinned to the map** at any of eight anchors, or docked into the map's own
  control cluster beside zoom and the layer list, and can collapse to a single icon.
- **Filter by map extent**, opt-in, with every widget it narrows saying "· in view" while it is on.
- **Six starting templates**, each a working layout that binds itself to your layers.
- **The dashboard now carries the layer list**, and therefore the legend — it had `legend: true`
  and nowhere to draw it.
- **Point layers can be clustered at low zoom**, applied while the PMTiles archive is built. Points
  only, and it takes effect on a re-tile.

### Also in this release

- **The CLI and the QGIS plugin know the new experience.** `geodeploy portals create --experience
  dashboard` works; it was refused by the client itself before, whatever the server supported. The
  plugin needed no change — it creates portals with no archetype and only reads one for display —
  but it vendors the same client, so its copy is current too.

### Fixes

- **Numbers in a list are no longer abbreviated.** A building id of 1011771 rendered as "1M", and
  so did every other building. Compaction belongs to a measurement in a big figure, not to a value
  in a row; years and postcodes come out right for the same reason.
- **The symbology panel stays inside the window**, and "Save as default" is a pinned footer rather
  than the last thing in a panel several screens long — it could previously be off-screen entirely.
- **The filter bar no longer wraps** into a lozenge covering a third of the map.
- **The demo upload cap exempts the instance owner**, so loading a test layer no longer means taking
  the demo down first. Owner only — not admin — and unauthenticated requests still hit the cap.
- **A linked filter's key limit was really 200, not 5 000.** The server clamped it to the ceiling
  meant for chart groups, so the map stopped narrowing at 201 matching features while reporting that
  it had passed 5 000. Key sets now have their own ceiling, and the offered limits are checked
  against it.
- **`vendor.py` could delete the plugin's vendored client instead of refreshing it.** It removed the
  directory before copying, and removing it fails on Windows whenever anything holds a handle —
  OneDrive does. It now copies over the tree without removing the folder.

## v1.4.1 — 2026-08-28

A patch release, and the second deliberate exception to the major.minor convention above: a
GeoParquet layer in a projected CRS built its tiles at coordinates that were not its own, and that
should not wait for v1.5. Two smaller things already on main ride along with it.

- **A GeoParquet layer in a projected CRS now tiles to the right place.** Tiles were built with
  DuckDB's three-argument `ST_Transform`, whose `always_xy` defaults to false — and EPSG:4326
  declares lat/lon, so every coordinate came out swapped. A Swiss layer (EPSG:2056) tiled to the
  Gulf of Aden while the map still opened on Switzerland: no error, no failed request, just an empty
  map. Layers already in EPSG:4326 were never affected, and neither was anything served from
  PostGIS. **Archives tiled before this fix must be re-tiled** (Tile, in My Data) — the fix only
  changes what new tiles contain.

- **A pasted GeoDeploy link now shows the GeoDeploy card.** The dashboard, every published portal
  (with its own title and description) and the docs emit Open Graph tags against a shipped
  1200x630 image, so a link posted to LinkedIn, Slack or Teams stops rendering as a bare title and
  domain. Existing instances get the dashboard card from a normal update; **portals show theirs
  once re-published**, since the tags are baked at publish time.
- `installer/update.sh` now applies an `nginx.conf` change instead of skipping it. The file is a
  single-file bind mount, so a running container stays on the old inode and `up -d` reports
  "up-to-date" — the in-app updater already handled this, the manual script did not.

## v1.4 — 2026-08-17

### Into the tools you already use

- **A QGIS plugin.** Paste an instance URL and browse what it publishes — **no account needed for
  public data** — then add a layer using the fastest source it offers, or open a whole portal as a
  styled QGIS group. Upload back, including multi-gigabyte files, which go straight to object
  storage. It vendors the same zero-dependency client the CLI is built on, so there is nothing to
  pip-install into QGIS.
- **Styling travels both ways, for every layer type.** Single symbol, graduated and categorized for
  vectors; colormap, stretch, band, hillshade, contours and colour-per-value for rasters;
  size-from-a-field; marker shape, stroke colour and stroke width; a polygon's outline width. A
  layer opens in QGIS looking like the portal, and what you change there goes home.
  **3D extrusion is carried but not yet drawn in QGIS** — see the known limitations below.
- **A raster can actually be classified in QGIS.** Which renderer QGIS offers is decided by the
  SOURCE, not by a setting: server-rendered tiles arrive as one band of RGBA — "Singleband color
  data", nothing to classify — and vector tiles have no categorized or graduated renderer at all.
  Opening the data instead used to cost you GeoDeploy's colours; now the GeoTIFF opens *with* its
  colormap, stretch, band and classification applied. A per-layer **Source** picker says what each
  layer offers, and **Restyle this layer…** reopens one from its data, in place, keeping its styling.
- **A portal opens editable, too.** The Source picker offers a portal two ways: as it draws it —
  the published tiles, fastest and exactly what a visitor sees — or with **every layer opened from
  its own data and then painted with the portal's styling**. The second gives a portal's layers the
  same full symbology a single layer has: classify by a field, build raster classes, edit 3D, then
  push the group back.
- **The default source follows the backend.** PostGIS layers open over OGC API - Features, ready to
  be classified by a column, because that is what those layers are for. Tiled GeoParquet — the
  large-data backend — keeps its tiles. Either is one click from the other.
- **A push cannot delete what QGIS cannot draw.** 3D extrusion, imported MapLibre paint, popup
  fields and server-side algorithms survive a restyle from QGIS instead of being replaced by the
  subset QGIS can express — and opening a portal and pushing it straight back reports nothing
  changed, rather than every layer as restyled.

### Known limitations

- **3D extrusion is not drawn in QGIS's 3D view.** A layer's extrusion is stored, published and
  rendered by GeoDeploy as before, and the plugin carries it safely through a round trip — opening
  an extruded layer and pushing it back does not remove it. But QGIS itself still shows those
  polygons flat, so 3D cannot be *edited* there yet. Tracked as part of "Every symbol QGIS can
  draw" on the roadmap.
- Symbology QGIS has and GeoDeploy does not — inverted polygons, 2.5D, hatch and gradient fills,
  line offsets, markers along a line, multi-layer symbols, rule-based rendering, labels — is
  simplified on the way into QGIS and not carried back. Same roadmap entry.

### Symbology

- **Polygons have an outline WIDTH.** They never did, and the reason was structural: a MapLibre
  `fill` strokes its own boundary and `fill-outline-color` is a colour with no width, so the edge
  was always one pixel and a border could not be set anywhere. It is now drawn as its own line
  layer beside the fill — in the portal, the editor preview, the layer page and the published
  portal, with the legend swatch showing it — and it round-trips through QGIS and the CLI like
  every other property. Existing portals are untouched: the extra layer appears only above the
  hairline they already had.
- **Contour lines**, everywhere raster symbology is edited: My Data, the layer page, the portal
  editor, the published portal with its own legend, the CLI and the plugin. The interval and line
  width are yours; the range the relief behind the lines is coloured over comes from the layer's
  stretch, because the algorithm's own default spans the whole planet and would render a survey DEM
  as one flat colour.
- **Unique values for rasters.** Integer rasters — land cover, soil types, a mask — are
  classifications, and a ramp over them claims the distance between class 3 and class 4 means
  something. **Read values** pulls the distinct pixel values off the raster and gives each a colour
  and a label you can edit; re-reading keeps the colours you already chose. A raster that is
  genuinely continuous says so instead of being carved into classes.
- **The layer page's legend describes what is on the map** — the ramp the algorithm actually draws
  with, the contour interval, and swatches for a classified raster. It previously read the layer's
  colormap and nothing else, so a hillshade showed no gradient at all.

### Fixes found by using it

- **A classified raster drew only one of its classes.** The stretch was still being sent alongside
  the value-lookup palette, and a stretch is what a value lookup cannot survive: it maps the data
  into 0–255 *before* the lookup, so classes 0/1/2 arrived as 0/127/255 and only the one whose
  number still matched a key drew. A stale "reverse the palette" flag was separately re-pairing
  hand-picked class colours end for end.
- **A raster's own pixels were refused to its owner.** The COG required the layer to be public, so
  a private raster could not be opened for restyling by the person who uploaded it.
- **Points drew at a third of their size**, under a dark outline that covered the fill — a unit
  mismatch that made a styled point layer look like tiny black dots next to the same layer in a
  browser.
- **A portal's raster drew in the layer's default colours**, not the portal's. A raster is coloured
  by the server, and a portal bakes its colormap and stretch into its own tile URL.
- **A restyled point layer pushed back as no change**, because QGIS keeps one style per geometry
  type and the reader took whichever came first.
- **A stroke-only edit went unnoticed**, and untouched layers reported as restyled — change
  detection was wrong in both directions.
- **Saving a style from QGIS reset a layer's opacity and deleted its popup fields.**
- **The raster legend omitted `zfactor`**, so a hillshade published with vertical exaggeration
  opened flat in every other tool. That route is the only styling a public raster has.
- **A classified raster or a reversed palette lost exactly that** when added to a portal from the
  catalog, in the editor preview, and when a viewer touched any control in a published portal —
  three separate hand-written key lists, each missing the same keys. There is now one list per
  language.
- **Vector tiles: a layer vanished when you zoomed in.** The plugin told QGIS tiles existed at every
  zoom, so it kept asking past the depth the server has data, retried each three times and drew
  nothing.
- **The public layer page**, the map surviving a refresh, one redraw per layer instead of two, and a
  portal group that opens with the portal's own folders, order and opacity.

### CLI

- `geodeploy layers style --algorithm contours --increment 25`, plus `--thickness`, `--minz`,
  `--maxz` and `--reverse-colormap`. Published to PyPI as **1.4.0**.

## v1.3.1 — 2026-08-14

A hotfix, and the first patch-level release. The major.minor convention above holds for planned
work; a restore that aborts on any instance with a PostGIS layer should not have to wait for v1.4.

- **A restore no longer fails on `DROP EXTENSION postgis`.** `pg_restore --clean` emits a DROP for
  everything the dump creates, and Postgres refuses to drop PostGIS while geometry columns still
  exist — which is true of every instance with a PostGIS vector layer. That refusal is the outcome
  we want and pg_restore continues past it, but we classified it as a fatal error and aborted the
  whole restore. It went unnoticed because the path was proven on a GeoParquet-only instance: no
  geometry columns, nothing depending on the extension, so the DROP succeeded. **Not a v1.3
  regression** — the bug predates it and was found when a demo instance's hourly reset hit it.
- **`/health` reports the real version.** It returned a hardcoded `0.3.0`, which had been wrong
  since v1.0 — on the one endpoint whose job is to say what is running.

## v1.3 — 2026-08-14

### Reach your instance without a browser

- **A packaged command-line client — `pip install geodeploy`.** Uploads of any format and any size
  (multi-gigabyte files go straight to object storage in parallel presigned parts), layers, portals,
  data-driven symbology, publishing, the public catalog, jobs, users and instance administration.
  Every command takes `--json`, and exit codes separate an authentication problem from a network one
  from a server one, so a scheduled job can alert on the right thing.
- **It is also a Python client**, with no dependencies and a Python 3.9 floor, because the QGIS
  plugin will vendor it rather than pip-install into someone's QGIS.
- **Start from a URL alone.** `GET /api/public` is an anonymous index of what an instance
  publishes — public portals, and public layers grouped by how they are stored — so a desktop client
  can browse an instance before anyone signs in. An admin can switch the listing off in
  **Settings → Infrastructure**; it defaults to listed.

### Take a copy with you

- **Download any layer, whole.** A PostGIS table is built into a GeoPackage, CSV or GeoJSON; a
  GeoParquet layer comes straight from its own partition files — complete, lossless and with no
  worker involved; a raster is its COG.
- **A truncated export now says so.** A built export stops at a row cap so the worker cannot run out
  of memory, and until now a capped download looked exactly like a complete one. The row count of
  every file is in `MANIFEST.txt`, in the job status, and in the CLI's non-zero exit — with the
  uncapped alternatives named.

### Symbology

- **Size from a field** — bigger markers for bigger values, thicker lines for busier roads. The
  instance had drawn this since v1.1 with no way to set it outside the API.
- **Invert a colour ramp**, as a checkbox that recolours instantly.
- **Style a layer in My Data**, not only inside a portal — the same panel, reused. What you save
  there is what a portal picks up when the layer is added.
- **Legends collapse** in a published portal's layer list, and the class count stops snapping back
  to whatever the classifier returned.
- **A legend anyone can read**: `GET /api/data/{kind}/{ref}/legend` serves the swatches and labels
  the portal draws, so no other renderer has to re-derive them.

### Fixes found by using it

- **A CSV with an `id` column could not be imported.** The destination table adds its own `id`, so
  the load failed at 45% with "column id specified more than once" — for most CSVs anyone exports.
- **The legend 404'd for the owner of their own layer** when it was not public.
- **Share links sent QGIS to a URL it cannot open** — the OGC *collection* was promoted where QGIS
  needs the *service*. A tiled GeoParquet layer now leads with its PMTiles archive, which QGIS opens
  through *Add Vector Layer*.
- **A raster's zoom floor is measured, not guessed** — read from the file's overview pyramid at
  ingest instead of inferred from its extent, so a small high-resolution layer stops vanishing when
  you zoom out.
- **Story maps work on a phone**: portrait puts the map above a sideways-scrolling narrative, and
  the control cluster stops running off a landscape screen.
- **One failing schema migration no longer disables every migration after it.** They shared a
  transaction, and Postgres aborts the whole thing on the first error — silently, because each
  statement was wrapped in its own `try`.
- **HEAD works on every route.** FastAPI does not add it to a GET route, so every endpoint answered
  405 to a probe.

## v1.2 — 2026-08-07

### From an upload to a map anyone can open

Every raster failure fixed here was **silent**. The layer said `ready`, its TileJSON was valid, and
the tiles did not work — so the only honest place to look was a tile server's log, which is not where
anyone looks first.

- **A 3 GB raster upload no longer dies writing overviews.** A classic TIFF cannot pass 4 GB (its
  header offsets are 32-bit), and GDAL does not refuse the job up front — it fails part way through,
  so the size limit surfaced as an overview error. Large rasters are written as BigTIFF now.
- **Multispectral imagery renders.** A 4-band drone image asked the PNG encoder for five channels
  (four bands plus the mask) and every tile failed, everywhere — the portal, the XYZ link, the STAC
  asset, all of them build the same URL. A raster with more than three bands now gets an explicit
  band selection.
- **Hillshade works from the portal editor**, not only from the published legend. The layer's own
  stretch was being applied *after* the algorithm, flattening a finished relief image to one colour.
- **A world layer no longer breaks every tile.** Web Mercator is undefined at the poles, so a
  countries dataset reaching Antarctica made PostGIS refuse the projection and Martin answer 500 for
  every tile at every zoom. Geographic data is clipped to the Mercator band at import.
- **One oversized tile no longer hangs a portal.** A drone plot 200 m across was still being
  requested at zoom 3 — a single tile spanning 2,000 km — and the timeout left the map waiting, so a
  catalog portal never finished loading and its filters never appeared.
- **Clicking the map** no longer asks every raster on screen for a value it does not have.

### Your data opens somewhere else

- **WMTS for QGIS.** *Layer ▸ Add Layer ▸ Add WMS/WMTS Layer*, paste the link, and **Zoom to Layer**
  goes to the data. An XYZ URL has nowhere to put an extent, which is why it could not.
- **Tiles that miss a raster come back empty, not missing.** A tile server answering 404 for the
  edges of a tile grid is correct for an API and wrong for a map — and a bare XYZ URL gives QGIS,
  GeoLibre or Leaflet no way to avoid asking. They now get a transparent tile instead of a console
  full of errors.
- **Share links say which tool each one is for**, and the service-wide link says so plainly rather
  than looking like the one dataset it was copied from.

### Portals

- **Start tilted** is an authoring choice, beside *Start in 3D globe*. The pinned view always
  carried a pitch; the only way to set one was to right-drag the preview.
- **Catalog portals on a phone**: filters sit beside the results instead of above them. The old
  layout split the one scarce axis three ways and the result list — the point of the page — got the
  smallest share.
- **The "On map" list shows each layer's real symbology.** It is a catalog's only legend, and every
  row used to be a dot coloured by type, so three point layers were three identical dots. Rasters
  show their colour ramp.
- **Custom logos can take the theme colour**, like the built-in ones always could — an SVG exported
  dark on transparent was nearly invisible against a dark header. **SVG uploads** are accepted.
- **A globe portal's thumbnail keeps the space behind the earth.**

### Upgrading

From the dashboard: **Settings ▸ Infrastructure ▸ Updates**, or on the server:

```bash
sudo bash installer/self-update.sh v1.2
```

**Re-publish your portals after updating.** The layer symbology in the catalog list, the logo
tinting, the tilt state and the phone layout are baked into each portal when it is published.

**One behaviour change worth knowing.** Geographic data is now clipped to the Web Mercator band
(±85.05°) at import. Nothing outside that band can be shown on a web map anyway, but it means a
world dataset downloaded back out of GeoDeploy has a truncated Antarctica rather than the polygon you
uploaded. Existing layers are unaffected until re-imported.

No manual migration is needed.

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

Tilt is also an authoring choice: **Start tilted**, beside *Start in 3D globe* in the portal editor's
Layout panel, decides whether visitors open in perspective or looking straight down. The pinned start
view always carried a pitch — until now the only way to set one was to right-drag the preview.

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
