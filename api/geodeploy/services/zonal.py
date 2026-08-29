"""V-16 Dashboard — raster zonal statistics for a drawn or clicked geometry.

The Raster Stats widget answers one question: for the area the visitor just pointed at, what are the
pixel statistics of this raster? The area may be a clicked vector feature's polygon, a hand-drawn
polygon, or a dragged bbox — all three arrive here as the same GeoJSON geometry, because the
dashboard's filter bus normalises them upstream (a bbox IS a rectangular polygon; giving it its own
code path would be two implementations of one computation).

WHY TITILER AND NOT A DEDICATED SERVICE. TiTiler is already deployed, already holds the storage
credentials, and its `POST /cog/statistics` does exactly this: it takes a GeoJSON feature, reads the
COG through a WINDOW around that feature's bounds (rio-tiler's `feature()` → `part()`, a ranged read
of the overview level that matches the requested size), masks to the geometry and reduces. So it is
a windowed read, not a full-raster read, which is the property that decides whether zonal stats are
viable at all — and it returns min/max/mean/sum/std/median AND a histogram, which is the whole v1
statistic list. A rasterio/exactextract service would add a container, a second set of storage
credentials and a second COG reader to maintain, in exchange for a faster inner loop over data that
is dominated by the object-storage read either way.

That said the decision is a MEASUREMENT, not a belief, and this module is written so the measurement
can change it: `compute()` is the only entry point, `_titiler_statistics` is the only thing that
knows how the numbers are produced, and `STATS_KEYS` is the vocabulary both sides speak. Swapping in
exactextract means replacing one function. `notes_temp/DASHBOARD_ARCHETYPE.md` records what still
needs benchmarking on a live instance.

CACHING. Redrawing the same area must not recompute — a visitor comparing two rasters over one
polygon fires N requests for the same geometry, and the filter bus re-publishes the geometry on
every reset/re-select. The key is `(s3_key, band, sha256(canonical geometry))`, so it is
content-addressed: the same shape drawn twice, or the same feature clicked twice, hits. Bounded by
count and by age, in-process — a zonal statistic is cheap to recompute and expensive to get wrong
after the raster is replaced, so an unbounded or persistent cache would be trading the wrong way.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time

logger = logging.getLogger(__name__)

#: What a Raster Stats widget may ask for, and the TiTiler key each maps to. `histogram` is not a
#: number and renders as a distribution plot, but it is requested and cached with the rest.
STATS_KEYS = {
    "min": "min",
    "max": "max",
    "mean": "mean",
    "sum": "sum",
    "std": "std",
    "median": "median",
    "count": "count",          # valid (unmasked) pixels inside the geometry
    "histogram": "histogram",
}

#: Entries and seconds. Small on purpose: the cache exists to absorb the burst of identical requests
#: one interaction produces, not to be a store.
CACHE_MAX = 256
CACHE_TTL = 900

#: The window TiTiler reads at. `max_size` bounds the pixels pulled for a huge polygon: rio-tiler
#: picks the overview level that satisfies it, so a country-sized selection reads a decimated
#: overview rather than every full-resolution pixel. 1024 is ~1M pixels — enough that a mean is a
#: mean and not a sample, cheap enough to answer while a visitor is still looking at the map.
MAX_SIZE = 1024

_cache: dict[str, tuple[float, dict]] = {}


class ZonalError(RuntimeError):
    """The statistics could not be computed — the router turns this into a 502 with the reason."""


def _canonical(geometry: dict) -> str:
    """A geometry's cache identity. `sort_keys` + compact separators make two structurally identical
    geometries hash the same however the client serialised them; coordinates are NOT rounded,
    because two selections that differ in the sixth decimal are two different selections and
    conflating them would show one polygon's statistics under another's outline."""
    return json.dumps(geometry, sort_keys=True, separators=(",", ":"))


def cache_key(s3_key: str, geometry: dict, band: int, categorical: bool) -> str:
    digest = hashlib.sha256(_canonical(geometry).encode("utf-8")).hexdigest()[:32]
    return f"{s3_key}|b{band}|{'c' if categorical else 'n'}|{digest}"


def _cache_get(key: str) -> dict | None:
    hit = _cache.get(key)
    if not hit:
        return None
    stamp, value = hit
    if time.time() - stamp > CACHE_TTL:
        _cache.pop(key, None)
        return None
    return value


def _cache_put(key: str, value: dict) -> None:
    if len(_cache) >= CACHE_MAX:
        # Oldest-first eviction. A plain dict preserves insertion order, so this is the LRU-ish
        # behaviour without carrying an OrderedDict for a cache this size.
        for old in list(_cache)[:max(1, CACHE_MAX // 4)]:
            _cache.pop(old, None)
    _cache[key] = (time.time(), value)


def invalidate(s3_key: str | None = None) -> None:
    """Drop cached statistics — everything, or one raster's. Called when a raster is re-ingested or
    deleted: the object key can be reused, and serving the previous raster's numbers under it would
    be a wrong answer with no symptom."""
    if s3_key is None:
        _cache.clear()
        return
    for key in [k for k in _cache if k.startswith(f"{s3_key}|")]:
        _cache.pop(key, None)


def _feature(geometry: dict) -> dict:
    """TiTiler's statistics endpoint takes a GeoJSON **Feature** (or FeatureCollection), not a bare
    geometry — posting a geometry gets a 422 that reads like a server fault."""
    if geometry.get("type") == "Feature":
        return geometry
    if geometry.get("type") == "FeatureCollection":
        return geometry
    return {"type": "Feature", "properties": {}, "geometry": geometry}


async def _titiler_statistics(s3_key: str, geometry: dict, band: int,
                              categorical: bool, timeout: float) -> dict:
    """The one function that knows HOW the numbers are produced. Everything above it is caching,
    validation and shaping — see the module docstring on replacing this with exactextract."""
    import httpx
    from ..config import get_settings

    settings = get_settings()
    params: dict = {
        "url": f"s3://{settings.storage_bucket}/{s3_key}",
        "bidx": band,
        "max_size": MAX_SIZE,
        # 20 bins is what a small histogram panel can draw legibly; TiTiler's default is 10.
        "histogram_bins": 20,
    }
    if categorical:
        params["categorical"] = "true"
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(f"{settings.titiler_url}/cog/statistics",
                              params=params, json=_feature(geometry))
        r.raise_for_status()
        return r.json()


def _first_band(payload: dict) -> dict | None:
    """The per-band statistics out of TiTiler's response.

    The shape depends on what was posted: a Feature comes back as a Feature whose
    `properties.statistics` is `{b1: {...}}`; a FeatureCollection comes back as a collection. Both
    are handled because both are legal input here, and a widget bound to a multi-part selection must
    not silently return nothing.
    """
    if not isinstance(payload, dict):
        return None
    if payload.get("type") == "FeatureCollection":
        for feature in payload.get("features") or []:
            found = _first_band(feature)
            if found:
                return found
        return None
    stats = (payload.get("properties") or {}).get("statistics") if payload.get("type") == "Feature" \
        else payload
    if not isinstance(stats, dict):
        return None
    for value in stats.values():
        if isinstance(value, dict):
            return value
    return None


def shape_stats(raw: dict, wanted: list[str]) -> dict:
    """TiTiler's band statistics → the widget's payload: only the requested keys, in the order the
    author chose, plus the histogram when asked for.

    `count` is TiTiler's `count` (VALID pixels), not `valid_percent` — the widget label says
    "pixels" and a percentage under that label would be wrong. Missing keys come back as None
    rather than being omitted, so the renderer draws the stat cell with a dash instead of the panel
    changing shape between two rasters.
    """
    out: dict = {}
    for key in wanted:
        source = STATS_KEYS.get(key)
        if not source:
            continue
        if key == "histogram":
            hist = raw.get("histogram")
            if isinstance(hist, list) and len(hist) >= 2:
                counts = [float(c) for c in (hist[0] or [])]
                edges = [float(e) for e in (hist[1] or [])]
                out["histogram"] = {"counts": counts, "edges": edges}
            else:
                out["histogram"] = None
            continue
        value = raw.get(source)
        out[key] = float(value) if isinstance(value, (int, float)) else None
    return out


async def compute(s3_key: str, geometry: dict, *, stats: list[str], band: int = 1,
                  categorical: bool = False, timeout: float = 60.0) -> dict:
    """Zonal statistics for one raster over one geometry. The single entry point.

    Returns `{"stats": {...}, "band": n, "cached": bool}`. Raises `ZonalError` when the statistics
    could not be produced — an empty selection over a raster's nodata is NOT an error and comes back
    as all-None, because "you selected an area with no data" is an answer.
    """
    wanted = [s for s in (stats or []) if s in STATS_KEYS] or ["min", "max", "mean"]
    band = max(1, min(int(band or 1), 64))
    key = cache_key(s3_key, geometry, band, categorical)
    hit = _cache_get(key)
    if hit is not None:
        # A copy, so a caller shaping the result for one widget cannot mutate what the next reads.
        return {"stats": shape_stats(hit, wanted), "band": band, "cached": True}

    try:
        payload = await _titiler_statistics(s3_key, geometry, band, categorical, timeout)
    except Exception as exc:
        raise ZonalError(str(exc)) from exc
    raw = _first_band(payload)
    if raw is None:
        raise ZonalError("The raster server returned no statistics for that area.")
    _cache_put(key, raw)
    return {"stats": shape_stats(raw, wanted), "band": band, "cached": False}
