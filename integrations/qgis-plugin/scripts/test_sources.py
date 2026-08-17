"""Which source the plugin picks, and what it falls back to.

This is the file where a mistake is invisible until someone's QGIS hangs, so the choices are pinned
here rather than left to be re-derived. Two things it exists to protect:

* **Every vector layer takes the viewport-driven path.** Tiles that arrive four-per-screen, not an
  archive read whole and not features paged one extent at a time.
* **An older instance still works.** The per-tile URL is newer than some instances this plugin talks
  to; asking for it first is right, and failing to open when it is absent would not be.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "geodeploy_qgis"))

import sources                                                                  # noqa: E402

sources.gdal_supports_pmtiles = lambda: True
BASE = "https://example.org"


def row(**kw):
    return dict({"_base": BASE, "uid": "abc123", "name": "layer"}, **kw)


def links(*ids):
    return [{"id": i, "url": f"{BASE}/{i}"} for i in ids]


# ── PostGIS: the FEATURES by default ──────────────────────────────────────────────────────────
# The default is a property of the BACKEND, not of the plugin. PostGIS holds the layers people
# classify — the attribute table is the point, and Martin's tiles carry only what was baked into
# them — so one opens ready to be styled by a column. The tiles are still one click away.
pg = row(storage_backend="postgis", links=links("tilejson", "xyz-mvt"))
assert sources.describe(pg)["kind"] == "oapif", sources.describe(pg)
assert sources.prefers_attributes(pg) is True
print("postgis            -> features, so it opens ready to classify by a field")

# …and the tiles are still reachable, with the bounds and zoom range only a TileJSON carries.
tiles = sources.describe(pg, prefer_attributes=False)
assert tiles["kind"] == "vector-tiles" and tiles["tilejson_url"] == f"{BASE}/tilejson", tiles
print("postgis, tiles     -> vector-tiles via its published TileJSON")

# No links at all (an authenticated row, which carries fields instead) — derive the URL.
got = sources.describe(row(storage_backend="postgis"), prefer_attributes=False)
assert got["tilejson_url"] == f"{BASE}/api/data/vector/abc123/tilejson", got
print("postgis, no links  -> derived TileJSON URL")

# BOTH surfaces are offered, with the backend's default first — the picker opens on entry 0.
alts = sources.alternatives(pg)
assert [(a["kind"], a["is_data"]) for a in alts] == [("oapif", True), ("vector-tiles", False)], alts
print("postgis picker     -> features first, tiles second")

# ── Tiled GeoParquet: tiles, NOT the archive ──────────────────────────────────────────────────
# Tiled GeoParquet is the LARGE-data backend: those layers are tiled precisely because reading them
# whole is not practical, so unlike PostGIS their default stays the tiles.
tiled = row(storage_backend="geoparquet", tile_status="ready", links=links("pmtiles", "tilejson"))
got = sources.describe(tiled)
assert got["kind"] == "vector-tiles", got
assert sources.prefers_attributes(tiled) is False
alts = sources.alternatives(tiled)
assert [(a["kind"], a["is_data"]) for a in alts] == [("vector-tiles", False), ("oapif", True)], alts
print("tiled geoparquet   -> vector-tiles, not the archive, and tiles stay the default")

# The measured reason, restated as a test: the archive must never be the FIRST choice, because
# GDAL reads it whole — a five-feature layer on the project's own instance tiles to 2.17M entries.
assert sources.describe(tiled)["kind"] != "pmtiles"

# ── An instance too old to publish a per-tile URL ─────────────────────────────────────────────
# `describe` still asks for the TileJSON (it cannot know), so what matters is where a failure goes.
old = row(storage_backend="geoparquet", tile_status="ready", links=links("pmtiles"))
first = sources.describe(old)
assert first["kind"] == "vector-tiles", first
second = sources.fallback(old, first)
assert second["kind"] == "pmtiles" and second["uri"].startswith("/vsicurl/"), second
third = sources.fallback(old, second)
assert third["kind"] == "oapif", third
assert sources.fallback(old, third) is None
print("old instance       -> vector-tiles, then pmtiles, then oapif, then stop")

# On GDAL older than 3.8 the archive cannot be opened at all, so it must be skipped, not offered.
sources.gdal_supports_pmtiles = lambda: False
assert sources.fallback(old, first)["kind"] == "oapif"
sources.gdal_supports_pmtiles = lambda: True
print("gdal < 3.8         -> skips the archive entirely")

# ── The checkbox still means what it says ─────────────────────────────────────────────────────
got = sources.describe(tiled, prefer_attributes=True)
assert got["kind"] == "oapif", got
print("prefer attributes  -> OGC API - Features")

# An untiled GeoParquet layer has no fast surface, and the reason is said out loud.
got = sources.describe(row(storage_backend="geoparquet"))
assert got["kind"] == "oapif" and "not tiled" in got["why"], got
print("untiled geoparquet -> oapif, and says why")

# ── Raster: WMTS first (bounds + matrix set), COG when the real values are wanted ─────────────
r = row(layer_type="raster", links=links("wmts", "tilejson", "cog"))
assert sources.describe(r)["kind"] == "wmts"
assert sources.describe(r, prefer_attributes=True)["kind"] == "cog"
assert sources.fallback(r, sources.describe(r))["kind"] == "cog"
print("raster             -> wmts, falling back to the COG")

# ── Nothing to go on ──────────────────────────────────────────────────────────────────────────
assert sources.describe({"name": "no base"}) is None
print("no base/ref        -> None")

print("\nALL SOURCE-SELECTION CASES PASS")
