# Dashboards

A **dashboard** is a portal where the map is one widget among many. Charts, numbers, lists and
filters sit beside it, and interacting with any of them narrows all the others.

That last part is the whole point. A page of charts that ignore each other is a report. A dashboard
is what you get when clicking a bar, drawing a box on the map or choosing from a dropdown re-asks
every other question on the page.

---

## Your first dashboard

1. **Portals ▸ New portal**, then choose the **Dashboard** experience.
2. Pick a **template** — Directory, Data explorer, Asset tracker, Monitoring, Regional statistics or
   Zonal analysis. Each one is a working layout, already wired.
3. Add your layers. The template binds its widgets to them automatically: the first suitable layer,
   the first field of the right type. Every guess stays editable.
4. Adjust, then **Publish**.

A template only decides what is on screen the first time. After that a template dashboard and a
hand-built one are the same thing — every widget is removable, re-bindable, re-wirable and
resizable.

!!! tip "Starting from nothing"
    Choose no template and you get an empty grid. **+ Add widget**, pick a type, bind it to a layer.
    New widgets are connected to everything by default — see [Cross-filtering](#cross-filtering).

---

## The widgets

=== "Numbers"

    **Indicator** — one computed number, optionally against a target, with the change shown as an
    arrow. Count, sum, mean, min or max over a field.

    **Gauge** — the same number on a dial, with threshold bands you colour yourself. The dial's
    range can be read from the column's own values rather than guessed.

    **Column profile** — what is actually *in* the selected data, column by column: how complete
    each field is, how many distinct values it has, and either its range and centre or its
    commonest few values. The widget for a layer nobody has read yet.

=== "Charts"

    **Chart** — bar, horizontal bar, line, area, pie or donut, over a grouping field. Time fields
    can be bucketed by hour, day, week, month, quarter or year.

    Bars can take **one colour per category**, or a **shaded ramp** for a grouping key that has an
    order — a construction period, a decade, a size band. A rainbow across an ordered key implies
    the categories are unrelated when they have a direction, so the two modes are separate choices.

    Horizontal bars print their value and its share by default; vertical ones do not, because the
    column is usually narrower than the number.

    **Several measures at once** — a chart can plot more than one aggregate against the same
    grouping ("mean height *and* mean age per district"). Lines are drawn per measure with a
    legend; bars become clustered groups. Colour then identifies the *measure*, not the category,
    because with several lines on one axis colour is the only thing telling them apart.

    **Scatter** — one dot per feature, two numeric columns against each other. This is the only
    chart that plots features rather than a summary of them, so it is **sampled**: a random sample,
    never the first N rows, and it says so under the plot when a sample is what you are seeing.

=== "Lists"

    **List / table** — attribute rows, sortable, paged on the server. Clicking a row zooms the map
    to that feature and fills the details panel.

    The same widget has a **cards** layout: a directory rather than a spreadsheet, one card per
    feature with a heading and a few fields under it. Useful for the datasets people look things
    *up* in — facilities, offices, contacts, stations.

    **Details panel** — the full attributes of whatever is selected anywhere on the dashboard.

=== "Controls"

    **Selector** — a category dropdown, a numeric range slider or a date range. A filter source
    only: other widgets never narrow a control, because a control that moves under the hand using
    it is worse than no control.

    **Search box** — find a feature by name or address across the columns you nominate, fly to it
    and filter to it. Also a source only, for the same reason.

    **Legend** — what the colours on the map mean. Binds to nothing and filters nothing.

=== "Map and raster"

    **Map** — the anchor. Draws every layer the portal publishes, and offers click, polygon-draw,
    box-draw and extent as selection tools.

    **Raster statistics** — min, max, mean, sum, standard deviation, median or a histogram for the
    area currently selected, computed from a raster through a windowed read rather than the whole
    file.

---

## Cross-filtering

Every widget can be a **source** (it publishes a filter), a **target** (it listens), or both. New
widgets are wired to everything by default; disconnecting is the deliberate act.

There are three channels, and they behave differently on purpose.

| Channel | Published by | Reaches |
|---|---|---|
| **Attribute** | a chart segment, a table row, a selector, a search result, an indicator | targets reading the **same layer** — or a [linked](#linking-two-layers) one |
| **Geometry** | a map click, a drawn polygon, a dragged box, the extent tool | **every** target, whatever layer it reads |
| **Selection** | a clicked feature, a chosen row | the details panel |

Filters combine with **AND**. A selector and a map selection both active narrow the result; they do
not replace one another. Everything currently narrowing the page appears as a chip in the filter
bar at the bottom, and each chip can clear itself.

### Why geometry crosses layers and attributes do not

A geometry is universal — an area is an area, whatever table you intersect it against. That is what
lets a polygon drawn over parcels drive elevation statistics from a raster.

A predicate is not. `canton = 'BE'` means nothing against a table with no `canton` column, so it is
dropped rather than silently returning zero rows. Unless you say the two layers are related.

---

## Linking two layers

**Linked layers** let an attribute filter travel from one layer to another. In the dashboard
builder, **Linked layers ▸ + Link**, then name the pair of columns that connect them:

```
buildings.egid  =  entrances.egid
```

After that, clicking a canton in a chart built on `buildings` narrows the entrances pie too. The
link is undirected — declaring it once lets filtering travel either way.

!!! info "How it works, and why it scales"
    The filter is not resolved to a list of matching ids and passed around. Narrowing 3.4 million
    buildings to one canton yields roughly 477 000 keys, which is not a predicate — it is a data
    transfer.

    Instead the filter is pushed into the engine as a subquery:
    `entrances.egid IN (SELECT egid FROM buildings WHERE canton = 'BE')`. One DuckDB query across
    both files for GeoParquet layers, or one SQL subquery for PostGIS ones.

!!! info "A linked filter does not narrow the map by default"
    The widgets re-answer and the chip appears in the filter bar, but the map keeps drawing every
    feature of the *other* layer.

    This is structural rather than an oversight. The map filters itself with a MapLibre expression
    evaluated in the browser, feature by feature, against the vector tiles it has already loaded. A
    linked filter is a *subquery* against another layer, and there is no way to write that as a
    browser-side expression: the widgets narrow because they ask the server, and the map has nothing
    to ask.

    A filter on a layer the map **draws** narrows it regardless, as does any geometry selection —
    those are expressible. It is specifically the cross-layer case that needs help.

    So with both layers on the map, filtering the entrances pie narrows the entrances *directly*
    (same layer, expressible) while the buildings stay whole unless you turn on the option below.

!!! warning "Both layers must use the same storage"
    A link between a GeoParquet layer and a PostGIS one cannot be pushed into a single query, so it
    is **refused** rather than half-applied, and the widget says why. Layers uploaded the same way
    are normally stored the same way; you can see which is which on each layer's page.

### Making the map follow a linked filter

The map widget has a **Follow linked-layer filters** switch, off by default, that appears once the
dashboard declares a relation.

With it on, a filter arriving through a relation is resolved to the **keys it matches** — the actual
`egid` values — and the map tests its features against that list. The subquery becomes a list of
values, which is something a browser-side expression *can* evaluate.

!!! warning "It is bounded, and it tells you when it gives up"
    That list is capped at **5 000 keys**. Filtering entrances to one common category can match
    hundreds of thousands of buildings, and moving half a million values into the browser to draw a
    map is a data transfer wearing a predicate's clothes.

    Past the cap the map is left **unfiltered** and says so, in a line across the bottom:

    > Map not narrowed by a linked filter — over 5 000 matching features

    That notice is the reason the feature is safe to use. Without it, "nothing matched" and "too
    many matched to draw" are the same empty-looking map, and you would have no way to tell which
    one you were looking at. The same notice appears if the key lookup fails outright.

    Leave the switch off and the map simply never claims to follow — which is why that is the
    default. Turn it on when your relations resolve to *narrow* selections: a building and its
    entrances, a station and its readings, a case and its site. Leave it off when a single choice on
    one layer selects a large fraction of the other.

If you would rather not depend on the cap at all, filter on a column the map's own layer has, or
select an area instead — both narrow the map directly, with no lookup and no bound.

!!! note "GeoParquet layers on the map are never narrowed — and the map names them"
    This applies to every kind of filter, not only linked ones. A GeoParquet layer draws through
    deck.gl rather than as a MapLibre style layer, so there is no style layer to filter. The widgets
    reading it still narrow correctly, because that happens on the server — it is only the drawing
    that does not follow.

    Whenever a filter is aimed at such a layer, the map says which one:

    > Parcels is not narrowed on the map (GeoParquet)

    For the same reason the cap notice exists: an unnarrowed layer and an unmatched one look
    identical on a map. The notice names the layers because this is a permanent property of
    particular layers rather than a property of the current selection, and "which ones" is the first
    thing worth knowing. It appears whether or not the linked-filter switch is on, and disappears
    when you switch the layer off in the layer list.

---

## The map as a filter

The map widget offers four tools, and an author chooses which appear:

- **Click a feature** — publishes both its geometry *and*, if you nominate a field, that field's
  value. One click, both channels. This is armed by default, because clicking a feature is the
  first thing anyone tries.
- **Draw a polygon** and **drag a box** — an arbitrary area as the filter.
- **Filter by map extent** — a switch rather than a gesture. While it is on, panning or zooming
  republishes the viewport as the filter, so the numbers describe what is on screen.

!!! note "Extent filtering is off unless you ask for it"
    It changes what your numbers mean: *"Buildings"* quietly becomes *"buildings currently on
    screen"*, and the figure moves when nobody touched the data. So it is never on by default, and
    while it *is* on, every widget it narrows adds **· in view** to its title for as long as the
    tool is active.

---

## Placing widgets

By default a widget takes a cell in a 12-column grid — drag to move, pull the corner to resize,
arrow keys to nudge.

A widget can instead be **pinned to the map**. Choose a placement:

- one of eight compass points (four corners, four edge midpoints), or
- **with the map's buttons**, which docks it into the map's own control cluster alongside zoom,
  home and the layer list.

A pinned widget is sized in pixels rather than grid columns, and can **start collapsed as an icon** —
one button that opens when clicked and closes on <kbd>Esc</kbd>. That is usually what a search box
wants: needed for a few seconds, in the way for the rest of the time. While collapsed the widget is
hidden rather than destroyed, so it keeps its results and any filter it published.

---

## Refreshing

A dashboard can re-ask every question on a timer, for a wall-mounted board. Auto-refresh keeps the
visitor's current selection — it re-asks the question being asked, which is not the same as clearing
the filters — and pauses while the tab is hidden.

---

## Publishing and access

A dashboard publishes exactly like any other portal: one URL, the same four access tiers, embeddable
in an `<iframe>`, and readable on a phone, where the widgets stack in reading order and the map is
capped so it cannot fill the screen.

See [Portals and experiences](portals.md#who-can-see-it) for the access tiers.

---

## Performance notes

Dashboards ask a lot of questions at once, so a few things are worth knowing.

- **Aggregates are computed on the server**, never by shipping features to the browser. A PostGIS
  layer is aggregated in PostGIS; a GeoParquet layer is read in place by DuckDB.
- **Answers are cached** for a few minutes, keyed by the layer, the question and the exact
  selection. Eight widgets over one drawn polygon cost one computation, not eight, and returning to
  a selection you have already made is free.
- **A geometry filter is the expensive one.** An attribute filter is a column scan; an area filter
  has to test each candidate feature against the shape. Very large selections over very large
  layers report themselves as capped rather than answering from a partial sample.
- **The search box does not query on every keystroke.** It waits until you pause, ignores anything
  under two characters, and cancels a search you have already typed past.
