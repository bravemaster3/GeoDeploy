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

!!! tip "Edit these from the dashboard"
    **Settings → Infrastructure → Environment** (owner only) lets you change these and apply them per
    service, with no terminal. Only a curated set is editable — database, storage and encryption
    values are deliberately not exposed there.

    Note that **Save** and **Apply** are separate steps. Docker reads the file when a container is
    **created**, not when it restarts, so a saved value takes effect only once the affected services
    are recreated. Apply does that for you.

If you prefer the file, it is `.env` at the root of your install — the same file the installer
generates for your database and storage credentials. Apply changes with:

```bash
docker compose up -d --force-recreate geodeploy-api celery
```

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

## Running on a small server (2 GB)

GeoDeploy runs on a 2 CPU / 2 GB VPS. Nine containers on that much memory leaves little headroom,
so two things need saying plainly.

### Swap is not optional — and this is not only a 2 GB concern

**Building** the dashboard needs far more memory than running it, and an update builds. With no swap
the kernel kills the build part-way: the update appears to hang and you are left with stopped
containers and no new image.

Most cloud images ship with **no swap at all**, at any size. Check with `free -m` before you believe
you are unaffected — a 4 GB server with no swap and a worker mid-conversion can hit the same wall.
The smaller the machine, the sooner; on 2 GB it is close to certain. Add swap once:

```bash
fallocate -l 2G /swapfile && chmod 600 /swapfile
mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab   # survives a reboot
```

Confirm it took with `swapon --show`, or `free -m` — the Swap row should no longer read zero.

Swap here is insurance rather than a performance trade: the pages it absorbs are mostly idle build
memory, so a build that previously died tends to complete at close to normal speed. On a 2 CPU /
2 GB VPS the difference measured was between *killed* and *finished*, not between fast and slow.

### One worker process, not two

Each background worker holds its own copy of the file it is converting, so concurrency multiplies
**memory** before it multiplies speed. On 2 GB set it to one, in
**Settings → Infrastructure → Environment**:

| Setting | 2 GB | Why |
| --- | --- | --- |
| `CELERY_CONCURRENCY` | `1` | Two simultaneous conversions is the most common way to run out of memory |
| `PMTILES_TILE_MEMORY_LIMIT` | `512MB` | Caps the tiler before the kernel has to |
| `PMTILES_TILE_THREADS` | `1` | Fewer parallel geometry conversions |

Apply, and the worker is recreated with the new values.

### Where the memory actually goes

```bash
docker stats --no-stream --format 'table {{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}'
```

PostgreSQL and MinIO are the resident baseline; the worker is what spikes. If you are only serving
published portals and not ingesting, the worker can be stopped entirely
(`docker compose stop celery`) and started again when you next upload — portals, tiles and the
dashboard do not depend on it.

!!! tip "Object storage takes the pressure off"
    Pointing storage at an external S3 provider removes MinIO from the server entirely — one fewer
    resident process, and disk stops being a server decision. See [Getting started](getting-started.md).
