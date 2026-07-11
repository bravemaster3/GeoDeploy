# Performance tuning (heavy layers & tiling)

GeoDeploy is designed to run on a **cheap VPS** with sensible defaults — a normal install needs **no
tuning at all**. This page documents the optional knobs for operators pushing very large layers or
running on unusually small/large hardware.

## How heavy vector layers are displayed

A GeoParquet layer is rendered two ways, and GeoDeploy picks automatically:

- **Small / medium layers** render live from the file via DuckDB (deck.gl viewport queries). No tiling
  needed.
- **Heavy layers** (millions of features) become sluggish to pan/zoom that way, so you **tile them to
  PMTiles once**: open the **Data Manager**, and on a GeoParquet layer click the **Tile** button (grid
  icon). Tiling runs in the background; the layer keeps displaying via the live path until tiling
  finishes, then switches to the fast pre-tiled path automatically. You can re-tile at any time.

The original `.parquet` is never modified — it stays the source for analysis, identify, and download.
The `.pmtiles` archive is display-only, served to browsers via HTTP range requests (no per-pan server
work), which is what makes pan/zoom seamless at 20 M+ features.

> **Why not just render everything live?** Browser WebAssembly (DuckDB-WASM) has a hard ~4 GB memory
> ceiling, so a browser physically cannot hold a 20 M-polygon layer. Pre-tiling to PMTiles is the only
> approach that scales on the client. The tiling itself runs server-side in native DuckDB, which has no
> such limit and spills to disk, so it works in bounded memory at any feature count.

## Tuning knobs

All of these are optional environment variables. Set them in the **`.env` file** at the root of your
GeoDeploy install (the same file the installer generates for your database/storage credentials), then
apply with:

```bash
docker compose up -d --force-recreate geodeploy-api celery
```

| Variable | Default | What it does |
|---|---|---|
| `PMTILES_TILE_MEMORY_LIMIT` | `1GB` | Caps the memory the tiler's DuckDB step may use (it spills to disk beyond this). Lower to `512MB` on a very small VPS; the tiler stays within budget instead of being OOM-killed. Does **not** slow a normal run — the step streams and rarely reaches the cap. |
| `PMTILES_TILE_THREADS` | `2` | Threads for the DuckDB geometry-conversion step only. tippecanoe (the main tiling pass) always uses all cores regardless. |
| `PMTILES_MAXZOOM` | `12` | Maximum zoom baked into the tiles. **The biggest lever on tiling time and output size.** MapLibre overzooms past this, so the map still shows detail beyond it. Drop to `11` for very dense layers to tile ~4× faster. |
| `PMTILES_SIMPLIFICATION` | `10` | Geometry simplification factor below the max zoom (higher = more aggressive). Cuts per-tile vertex work on dense data. Set to `0` to disable. |
| `PMTILES_DENSEST` | `drop` | How over-budget tiles shed features: `drop` (discard the densest — fast) or `coalesce` (merge them, preserving polygon area coverage at low zoom, but much slower). |
| `PMTILES_INPUT` | `fgb` | Tiling feed: `fgb` (fast native FlatGeobuf conversion) with automatic fallback to `geojsonseq` (slower, but works even without the `spatial` extension). Set to `geojsonseq` only to force the fallback for debugging. |

### Guidance by hardware

- **Tiny VPS (≤ 4 GB total RAM):** `PMTILES_TILE_MEMORY_LIMIT=512MB`. Everything else default.
- **Default cheap VPS (~8 GB):** leave everything unset.
- **Very dense layers that still tile slowly:** lower `PMTILES_MAXZOOM` (to 10–11) and/or raise
  `PMTILES_SIMPLIFICATION`. These trade a little top-zoom detail for a large speedup.

## Monitoring a tiling run

```bash
docker compose logs -f celery | grep -iE "tile_geoparquet|export_geoparquet|tippecanoe"
```

A healthy run logs the FlatGeobuf conversion, then tippecanoe's progress, then `READY`. If the fast
path can't run it logs a warning and continues via the slower fallback (`via geojsonseq`) — tiling
still completes.
