# Performance tuning (heavy layers & tiling)

GeoDeploy runs on a small server with sensible defaults, and **a normal install needs no tuning at
all**. This page exists for operators pushing unusually large layers, and it is the escape hatch —
not a setup step.

## How vector layers are displayed

**Every GeoParquet layer is tiled automatically.** Preparing a layer enqueues PMTiles tiling as part
of ingest — there is no button to press and no decision to make. What changes is which path serves it
while that happens:

| Stage | Served by |
| --- | --- |
| While tiling runs | Read live from the GeoParquet file — DuckDB-WASM in the browser, with a server-side fallback |
| Once tiling finishes | The `.pmtiles` archive, over HTTP range requests |

The layer is usable throughout; the switch happens on its own when the archive is ready. You can
re-tile at any time from **My Data** if the source data changes.

The original data is never modified. The `.pmtiles` archive is display-only, and stays the fast path
for pan and zoom; the GeoParquet remains the source for identify, analysis and download.

!!! info "Why tile at all, if the live path works?"
    Browser WebAssembly has a hard memory ceiling of about 4 GB, so a browser physically cannot hold a
    20-million-feature layer however good the query is. Pre-tiling is the only approach that scales on
    the client. The tiling itself runs server-side in native DuckDB, which has no such ceiling and
    spills to disk, so it completes in bounded memory at any feature count — which is also why it does
    not need a large server.

## Tuning knobs

All of these are optional environment variables, and **you are unlikely to need any of them**.

!!! tip "Coming soon: edit these from the dashboard"
    An **Environment** tab in Settings → Infrastructure will let the owner change these values and
    apply them per service, with no terminal at all. The long-term goal is that a mature GeoDeploy
    never asks you to SSH in. Until then, the file below is the way.

Set them in the **`.env` file** at the root of your GeoDeploy install (the same file the installer
generates for your database and storage credentials), then apply with:

```bash
docker compose up -d --force-recreate geodeploy-api celery
```

A plain `restart` is not enough — the file is read when a container is **created**, not when it
restarts.

| Variable | Default | What it does |
|---|---|---|
| `PMTILES_TILE_MEMORY_LIMIT` | `1GB` | Caps the memory the tiler's DuckDB step may use (it spills to disk beyond this). Lower to `512MB` on a very small VPS; the tiler stays within budget instead of being OOM-killed. Does **not** slow a normal run — the step streams and rarely reaches the cap. |
| `PMTILES_TILE_THREADS` | `2` | Threads for the DuckDB geometry-conversion step only. tippecanoe (the main tiling pass) always uses all cores regardless. |
| `PMTILES_MAXZOOM` | *adaptive* | Maximum zoom baked into the tiles — **the biggest lever on tiling time and output size.** By default it's chosen **automatically from the layer's feature count** (≥10M → z10, ≥2M → z11, ≥500k → z12, else z13), so heavy layers tile fast with no tuning. MapLibre overzooms past the cap, so the map still shows detail beyond it. Set this to force a fixed zoom for the whole deployment. |
| `PMTILES_SIMPLIFICATION` | `0` (off) | tippecanoe geometry simplification factor below the max zoom (higher = more aggressive). **Off by default** — the aggressive factor visibly cut corners on large polygons. Set a value (e.g. `10`) to trade fidelity for smaller/faster tiles on dense data. |
| `PMTILES_KEEP_ALL_FEATURES` | `1` (on) | Guarantee **every feature is visible when zoomed in, even in dense areas.** tippecanoe adds deeper zoom levels only in the tiles that are still dropping features until they all fit, and never merges small polygons away. **Trade-off:** dense layers tile slower and produce a bigger archive (disk/bandwidth only — not RAM; it streams by range request). Set to `0` to let the densest tiles thin at the max zoom for smaller/faster archives. Zoomed-*out* views still thin normally either way (individual features aren't visible there). |
| `PMTILES_DENSEST` | `drop` | How over-budget tiles shed features: `drop` (discard the densest — fast) or `coalesce` (merge them, preserving polygon area coverage at low zoom, but much slower). Only affects zooms where `PMTILES_KEEP_ALL_FEATURES` still allows thinning. |
| `PMTILES_SIMPLIFY` | `0` (off) | Simplify geometry **for the display tiles only** while tiling. **Off by default** — the tolerance (≈9.5 m at z10) cut corners on large parcels and made small polygons (e.g. buildings under the tolerance) collapse and disappear when zoomed in. Set to `1` to re-enable: cuts tiling time ~50–75% on dense polygons at the cost of that fidelity. **Never touches the stored data** — downloads/clip/identify always read the original file at full resolution. |
| `PMTILES_SIMPLIFY_FACTOR` | `1.0` | Scales the simplification tolerance (higher = more aggressive/faster/coarser). Only used when `PMTILES_SIMPLIFY=1`. |
| `PMTILES_INPUT` | `native` | Tiling feed: `native` (DuckDB streams GeoJSON to tippecanoe concurrently, with the simplification above) or `geojsonseq` (force the shapely fallback, no simplify). Debug knob; leave default. |

### When to touch any of this

**Leave everything unset.** A 4 GB server runs the defaults comfortably, tiling included — the tiler
streams and spills to disk rather than holding a layer in memory, which is why it does not need a big
machine.

Reach for these only when you have a specific problem:

- **Tiling is being killed on a very small server** — lower `PMTILES_TILE_MEMORY_LIMIT` (e.g.
  `512MB`). Only worth trying if you actually see a tiling run die.
- **A very dense layer still tiles slowly** — the max zoom is already lowered automatically by feature
  count, but you can force it lower (`PMTILES_MAXZOOM=9`) and/or raise `PMTILES_SIMPLIFICATION`.
  Both trade a little top-zoom detail for a large speedup.

## Monitoring a tiling run

```bash
docker compose logs -f celery | grep -iE "tile_geoparquet|export_geoparquet|tippecanoe"
```

A healthy run logs the FlatGeobuf conversion, then tippecanoe's progress, then `READY`. If the fast
path can't run it logs a warning and continues via the slower fallback (`via geojsonseq`) — tiling
still completes.
