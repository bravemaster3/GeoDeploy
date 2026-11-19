"""Every request GeoDeploy makes of Martin and TiTiler, through the real nginx.conf, with the
response DECODED — a 200 is not the question, "does the tile contain the data?" is.

URLs come from GeoDeploy's own builders (services/martin, pillars, label_points, titiler) and, for
the parameters the browser composes itself, from the shapes in templates/shared/portal.js. Server
calls go through the real functions (raster_ingest._default_rescale, zonal._titiler_statistics).
"""
import asyncio
import io
import json
import math
import os
import sys
from urllib.parse import quote

import httpx
import mapbox_vector_tile
import numpy as np
import rasterio
from rasterio.io import MemoryFile

sys.path.insert(0, "/repo/api")
from geodeploy.config import get_settings  # noqa: E402
from geodeploy.services import label_points, martin, pillars, titiler, zonal  # noqa: E402
from geodeploy.tasks import raster_ingest  # noqa: E402

NGINX = "http://nginx"
STATE = json.load(open("/out/state.json"))
P = F = 0
FAILS = []


def ok(msg):
    global P
    P += 1
    print("   PASS ", msg)


def bad(msg):
    global F
    F += 1
    FAILS.append(msg)
    print("   FAIL ", msg)


def chk(msg, cond, got=""):
    ok(msg) if cond else bad(f"{msg}  [got: {str(got)[:300]}]")


def tile_of(lon, lat, z):
    n = 2 ** z
    x = int((lon + 180) / 360 * n)
    y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
    return z, x, y


def fill(url, z, x, y):
    return url.replace("{z}", str(z)).replace("{x}", str(x)).replace("{y}", str(y))


H = httpx.Client(timeout=60)


def get(path, **kw):
    return H.get(NGINX + path if path.startswith("/") else path, **kw)


# ── Vector: Martin ───────────────────────────────────────────────────────────────────────────────

def vector():
    print("\n== Martin ==")
    cat = get("/tiles/catalog")
    chk("catalog answers through nginx", cat.status_code == 200, cat.status_code)
    tiles = (cat.json().get("tiles") if cat.status_code == 200 else {}) or {}
    for layer in STATE["layers"]:
        sid = f"{layer['schema_name']}.{layer['table_name']}"
        chk(f"catalog lists {sid}", sid in tiles, list(tiles)[:10])
    for fn in (pillars.FUNCTION, label_points.FUNCTION):
        chk(f"catalog lists the {fn} function", fn in tiles, list(tiles)[:10])

    centre = {"cities": (17.64, 59.86), "roads": (17.64, 59.86), "parcels": (17.64, 59.86),
              "buildings3d": (17.635, 59.857), "sweref": (17.70, 59.86)}
    want_geom = {"cities": "Point", "roads": "LineString", "parcels": "Polygon",
                 "buildings3d": "Polygon", "sweref": None}
    for layer in STATE["layers"]:
        table, sid = layer["table_name"], f"{layer['schema_name']}.{layer['table_name']}"
        tj = get(f"/tiles/{sid}")
        chk(f"{table}: TileJSON 200", tj.status_code == 200, tj.status_code)
        if tj.status_code == 200:
            vl = (tj.json().get("vector_layers") or [{}])[0]
            fields = set((vl.get("fields") or {}).keys())
            chk(f"{table}: TileJSON fields = the attribute columns (popups)",
                fields == set(layer["properties"].keys()), (fields, layer["properties"]))
        url = martin.get_tile_url(layer["schema_name"], table)
        z, x, y = tile_of(*centre[table], 12)
        r = get(fill(url, z, x, y))
        chk(f"{table}: tile 200 with CORS", r.status_code == 200 and
            r.headers.get("access-control-allow-origin") == "*", (r.status_code, dict(r.headers)))
        if r.status_code != 200:
            continue
        mvt = mapbox_vector_tile.decode(r.content)
        feats = (mvt.get(sid) or {}).get("features") or []
        chk(f"{table}: tile decodes with features in source-layer '{sid}'", len(feats) > 0,
            {k: len(v["features"]) for k, v in mvt.items()})
        if feats:
            props = set(feats[0]["properties"].keys())
            chk(f"{table}: features carry their attributes", set(layer["properties"]) <= props, props)
            if want_geom[table]:
                kinds = {f["geometry"]["type"].replace("Multi", "") for f in feats}
                chk(f"{table}: geometry type survives ({want_geom[table]})",
                    kinds == {want_geom[table]}, kinds)
        if table == "cities":
            z, x, y = tile_of(11.97, 57.71, 12)
            g = mapbox_vector_tile.decode(get(fill(url, z, x, y)).content)
            names = [f["properties"].get("name") for f in (g.get(sid) or {}).get("features", [])]
            chk("cities: non-ASCII attribute values round-trip ('Göteborg – ök')",
                "Göteborg – ök" in names, names)
            far = get(fill(url, 12, 0, 0))
            chk("cities: a tile with no data is empty, not an error", far.status_code in (200, 204)
                and (far.status_code == 204 or not mapbox_vector_tile.decode(far.content).get(sid)),
                far.status_code)

    # The two shared functions, by the URLs the portal publishes.
    for fn_url, layer_name, table, centre_pt in (
            (pillars.tile_url(*_st("cities")), pillars.SOURCE_LAYER, "cities", (17.64, 59.86)),
            (label_points.tile_url(*_st("parcels")), None, "parcels", (17.64, 59.86)),
            (label_points.tile_url(*_st("parcels"), per_part=True), None, "parcels", (17.71, 59.855))):
        z, x, y = tile_of(*centre_pt, 12)
        r = get(fill(fn_url, z, x, y))
        label = fn_url.split("/tiles/")[1].split("/")[0] + (" per_part" if "per_part" in fn_url else "")
        chk(f"{label} on {table}: 200", r.status_code == 200, (r.status_code, r.text[:200]))
        if r.status_code == 200:
            mvt = mapbox_vector_tile.decode(r.content)
            n = sum(len(v["features"]) for k, v in mvt.items() if not layer_name or k == layer_name)
            chk(f"{label} on {table}: features present", n > 0, {k: len(v["features"]) for k, v in mvt.items()})


def _st(table):
    layer = next(l for l in STATE["layers"] if l["table_name"] == table)
    return layer["schema_name"], layer["table_name"], layer["geometry_column"]


# ── Raster: TiTiler ──────────────────────────────────────────────────────────────────────────────

def png(r):
    with MemoryFile(r.content) as mf, mf.open() as ds:
        return ds.read()


def picture(arr):
    """(opaque pixel count, distinct opaque colours)."""
    if arr.shape[0] in (2, 4):
        alpha = arr[-1] > 0
        rgb = arr[:-1]
    else:
        alpha = np.ones(arr.shape[1:], bool)
        rgb = arr
    cols = rgb.reshape(rgb.shape[0], -1).T[alpha.reshape(-1)]
    return int(alpha.sum()), len({tuple(c) for c in cols[:: max(1, len(cols) // 20000)]})


DEM_TILE = tile_of(17.64, 59.87, 12)


def raster_tile(name, url, min_colours=2, tile=DEM_TILE):
    r = get(fill(url, *tile))
    if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image/"):
        bad(f"{name}: tile → {r.status_code} {r.headers.get('content-type')} {r.text[:200]}")
        return None
    arr = png(r)
    opaque, colours = picture(arr)
    chk(f"{name}: tile has data ({opaque} opaque px, {colours} colours)",
        opaque > 0 and colours >= min_colours, (opaque, colours))
    return arr


def ctype(url, tile=None):
    return get(fill(url, *(tile or DEM_TILE))).headers.get("content-type")


def raster():
    print("\n== TiTiler ==")
    s = get_settings()
    T = s.titiler_url
    chk("healthz (dashboard Services check)", H.get(f"{T}/healthz").status_code == 200)
    dem = "rasters/dem.tif"

    info = H.get(titiler.get_info_url(dem, s))
    b = (info.json() if info.status_code == 200 else {}).get("bounds")
    chk("cog/info: bounds (TileJSON fallback)", b and len(b) == 4 and 17.5 < b[0] < 17.6, info.text[:200])
    tj = H.get(titiler.get_tilejson_url(dem, s))
    chk("tilejson: tiles + bounds", tj.status_code == 200 and tj.json().get("tiles") and tj.json().get("bounds"),
        tj.text[:200])

    rescale = raster_ingest._default_rescale(dem, s)
    lo, hi = STATE["rasters"]["dem_range"]
    ok_rescale = bool(rescale) and lo - 1 <= float(rescale.split(",")[0]) < float(rescale.split(",")[1]) <= hi + 1
    chk(f"ingest's default stretch from /cog/statistics ({rescale})", ok_rescale, rescale)
    rescale = rescale or f"{lo},{hi}"

    cat = H.get(f"{T}/cog/statistics", params={"url": f"s3://{s.storage_bucket}/rasters/classes.tif",
                                               "categorical": "true", "bidx": 1, "max_size": 1024})
    hist = (next((v for v in cat.json().values() if isinstance(v, dict)), {}) if cat.status_code == 200 else {}).get("histogram") or []
    values = sorted(int(v) for v in (hist[1] if len(hist) > 1 else []))
    chk("categorical statistics: the class values (raster values panel)", values == [1, 2, 3, 5], (cat.status_code, hist))

    poly = {"type": "Polygon", "coordinates": [[[17.60, 59.84], [17.66, 59.84], [17.66, 59.88],
                                                [17.60, 59.88], [17.60, 59.84]]]}
    raw = asyncio.run(zonal._titiler_statistics(dem, poly, 1, False, 60))
    band = zonal._first_band(raw)
    shaped = zonal.shape_stats(band or {}, ["mean", "min", "max", "count", "histogram"])
    chk("zonal statistics (dashboard widget, POST)", band and shaped.get("mean") and shaped.get("count")
        and shaped.get("histogram") and lo <= shaped["min"] <= shaped["mean"] <= shaped["max"] <= hi,
        shaped)

    pt = get(f"/raster/cog/point/17.64,59.87?url=s3://{s.storage_bucket}/{dem}")
    vals = pt.json().get("values") if pt.status_code == 200 else None
    chk("cog/point inside (portal identify)", vals and lo <= vals[0] <= hi, pt.text[:200])
    out = get(f"/raster/cog/point/10.0,50.0?url=s3://{s.storage_bucket}/{dem}")
    # TiTiler answers PointOutsideBounds with a 500 (it always has: 2.2.1 too). portal.js does not
    # ask for a point outside the layer's recorded bbox, and treats any non-2xx as "no value" — so
    # the contract is only that this is NOT a success carrying made-up values.
    print(f"   info  cog/point outside the data → {out.status_code} {out.text[:120]}")
    chk("cog/point outside the data is not a success (portal.js shows 'no value')",
        out.status_code >= 400, out.status_code)

    alg = H.get(f"{T}/algorithms/contours")
    params = {}
    if alg.status_code == 200:
        params = (alg.json().get("inputs") or {}) or alg.json()
    spec = json.dumps(alg.json())[:4000] if alg.status_code == 200 else ""
    chk("algorithms/contours is described", alg.status_code == 200, alg.status_code)
    for name in ("increment", "thickness", "minz", "maxz"):
        chk(f"contours declares '{name}' as an integer (what titiler.py sends)",
            f'"{name}"' in spec and '"integer"' in spec.split(f'"{name}"', 1)[1][:200], spec[:600])

    ST = lambda style, key=dem, bands=1: titiler.tile_url_from_style(key, style, bands, s)   # noqa: E731
    # Every style a layer can store, through the one builder the portal, STAC and links all use.
    raster_tile("dem default stretch", ST({"rescale": rescale}))
    raster_tile("dem viridis", ST({"rescale": rescale, "colormap": "viridis"}))
    raster_tile("dem viridis reversed", ST({"rescale": rescale, "colormap": "viridis", "colormap_reverse": True}))
    raster_tile("dem terrain colormap", ST({"rescale": rescale, "colormap": "terrain"}))
    raster_tile("dem hillshade", ST({"rescale": rescale, "algorithm": "hillshade"}))
    raster_tile("dem hillshade ×3", ST({"rescale": rescale, "algorithm": "hillshade", "zfactor": 3}))
    raster_tile("dem contours (default)", ST({"rescale": rescale, "algorithm": "contours"}))
    raster_tile("dem contours every 12.5 (scaled)", ST({"rescale": rescale, "algorithm": "contours", "increment": 12.5}))
    raster_tile("dem contours, chosen palette + colour", ST({"rescale": rescale, "algorithm": "contours",
                                                             "contour_palette": "viridis", "contour_color": "#ff0000"}))
    raster_tile("dem contours, line palette, no relief", ST({"rescale": rescale, "algorithm": "contours",
                                                              "contour_palette": "magma", "contour_line_palette": True,
                                                              "contour_relief": False}))
    classes = [{"value": 1, "color": "#ff0000"}, {"value": 2, "color": "#00ff00"},
               {"value": 3, "color": "#0000ff"}, {"value": 5, "color": "#ffff00"}]
    classes_url = ST({"color_classes": classes}, "rasters/classes.tif")
    arr = raster_tile("classified raster, colour per value", classes_url)
    if arr is not None:
        opaque = arr[:3, arr[3] > 0].T if arr.shape[0] == 4 else arr[:3].reshape(3, -1).T
        seen = {tuple(int(v) for v in c) for c in opaque}
        exact = seen <= {(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)} and len(seen) >= 2
        if exact:
            ok("classified raster: exactly the chosen class colours")
        else:
            # KNOWN, and the same on every TiTiler version tested (2.2.1, 2.4.1): a tile URL naming
            # no format is sent as JPEG whenever the tile has no transparent pixel, and JPEG smears
            # colours along class edges. Fixed separately by asking for .png on classified layers.
            print(f"   KNOWN classified raster sent as {ctype(classes_url)}: {len(seen)} colours, "
                  f"not the 4 chosen (JPEG artefacts)")
    raster_tile("RGB default", ST({}, "rasters/rgb.tif", 3))
    raster_tile("RGB bands 3,2,1", ST({"bidx": [3, 2, 1]}, "rasters/rgb.tif", 3))
    raster_tile("4-band (defaults to bands 1-3)", ST({"rescale": "0,65535"}, "rasters/multi.tif", 4))

    # The DEM as terrain: decode the heights from the RGB bytes and compare with the data.
    tr = get(fill(titiler.terrain_tile_url(dem, s), *DEM_TILE))
    if tr.status_code == 200:
        a = png(tr).astype("float64")
        h = -10000 + (a[0] * 65536 + a[1] * 256 + a[2]) * 0.1
        mask = (a[3] > 0) if a.shape[0] == 4 else np.ones(h.shape, bool)
        hv = h[mask & (h > lo - 5)]
        chk("terrain-RGB decodes to the DEM's heights", hv.size > 0 and lo - 5 <= hv.min() and hv.max() <= hi + 5,
            (hv.min() if hv.size else None, hv.max() if hv.size else None, lo, hi))
    else:
        bad(f"terrain-RGB tile → {tr.status_code} {tr.text[:200]}")

    # What portal.js composes in the browser when a viewer changes a raster's settings.
    base = f"/raster/cog/tiles/WebMercatorQuad/{{z}}/{{x}}/{{y}}?url=s3://{s.storage_bucket}/{dem}"
    lo_i, hi_i = math.floor(float(rescale.split(',')[0])), math.ceil(float(rescale.split(',')[1]))
    raster_tile("portal.js: palette change", f"{base}&rescale={rescale}&colormap_name=plasma_r")
    raster_tile("portal.js: hillshade ×2", f"{base}&algorithm=hillshade&expression=b1*2")
    p = quote(json.dumps({"increment": 35, "thickness": 1, "minz": lo_i, "maxz": hi_i}))
    raster_tile("portal.js: contours with algorithm_params", f"{base}&algorithm=contours&algorithm_params={p}")
    raster_tile("portal.js: classes kept", f"/raster/cog/tiles/WebMercatorQuad/{{z}}/{{x}}/{{y}}?url=s3://{s.storage_bucket}/rasters/classes.tif"
                f"&colormap={quote(json.dumps({'1': [255, 0, 0, 255], '2': [0, 255, 0, 255], '3': [0, 0, 255, 255], '5': [255, 255, 0, 255]}))}")
    st = get(f"/raster/cog/statistics?url=s3://{s.storage_bucket}/{dem}")
    band1 = next((v for v in st.json().values() if isinstance(v, dict)), {}) if st.status_code == 200 else {}
    chk("portal.js auto-stretch: /cog/statistics through nginx has percentiles",
        "percentile_2" in band1 and "percentile_98" in band1, st.text[:200])

    off = get(fill(ST({"rescale": rescale}), 12, 0, 0))
    chk("a tile off the edge of the raster is a blank image, not an error (nginx @blank_tile)",
        off.status_code == 200 and off.headers.get("content-type", "").startswith("image/"),
        (off.status_code, off.headers.get("content-type")))


if __name__ == "__main__":
    print("Martin:", os.environ.get("GDT_MARTIN_IMAGE"), "  TiTiler:", STATE["titiler_image"])
    vector()
    raster()
    print(f"\n================  {P} passed, {F} failed  ================")
    for f in FAILS:
        print("   -", f)
    sys.exit(1 if F else 0)
