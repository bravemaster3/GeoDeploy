"""The WHOLE round trip against a REAL instance: QGIS → a file → ingest → the map → QGIS again.

WHY THIS EXISTS AND `test_roundtrip_matrix.py` IS NOT ENOUGH. That file proves the translation is
faithful; it says nothing about what survives an INGEST. A style is read out of QGIS, written into a
file, parsed by GDAL on a server, loaded into PostGIS, tiled, baked into a portal's style.json and
handed back — and every one of those steps has lost something at least once. The column that carried
the classification got renamed by the Shapefile driver. The layer arrived with a truncated table
name. The published style drew every class in the first one's symbol. None of those are visible from
inside QGIS.

So this drives a real instance with a real token, in every file format GeoDeploy ingests, and checks
FOUR surfaces for each one:

  1. **what the plugin sends** — `symbology.from_qgis` on the layer as QGIS holds it;
  2. **what the instance stored** — read straight back off the API;
  3. **what the map would draw** — the published portal's `style.json`, which is the actual answer
     to "does it look right in the browser";
  4. **what comes back to QGIS** — the layer reopened from the instance, through the same code path
     the plugin's "Add layer" button uses, and compared with the renderer we started from.

Run it:

    export GEODEPLOY_URL=https://your-instance
    export GEODEPLOY_TOKEN=gdp_...
    docker run --rm -e GEODEPLOY_URL -e GEODEPLOY_TOKEN \
        -v <repo>/integrations/qgis-plugin:/src -w /src \
        -e QT_QPA_PLATFORM=offscreen qgis/qgis:ltr python3 -u scripts/e2e_live.py

It CREATES layers and a portal, and deletes them again at the end. Set `GEODEPLOY_KEEP=1` to leave
them for inspection. Everything it makes is named with the `E2E` prefix and a run id, so a failed
run leaves nothing ambiguous behind. It is deliberately NOT part of CI: it needs an instance and a
write token, and CI has neither.
"""
import json
import os
import sys
import time
import traceback
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.normpath(os.path.join(HERE, ".."))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from qgis.core import QgsApplication                                            # noqa: E402

QgsApplication.setPrefixPath(os.environ.get("QGIS_PREFIX_PATH", "/usr"), True)
QGS = QgsApplication([], False)
QGS.initQgis()

sys.path.insert(0, os.path.join(PLUGIN_ROOT, "geodeploy_qgis", "vendor"))
sys.path.insert(0, PLUGIN_ROOT)

from qgis.core import (Qgis, QgsCategorizedSymbolRenderer, QgsCoordinateReferenceSystem,  # noqa: E402
                       QgsFeature, QgsFields, QgsField, QgsFillSymbol, QgsGeometry,
                       QgsGraduatedSymbolRenderer, QgsLineSymbol, QgsMarkerSymbol, QgsPointXY,
                       QgsProject, QgsRendererCategory, QgsRendererRange, QgsVectorFileWriter,
                       QgsVectorLayer, QgsWkbTypes)
from qgis.PyQt.QtCore import QVariant, Qt                                       # noqa: E402

from geodeploy import Client                                                     # noqa: E402
from geodeploy_qgis import labels as labels_mod                                  # noqa: E402
from geodeploy_qgis import symbology                                             # noqa: E402
from geodeploy_qgis.compat import enum                                           # noqa: E402

URL = os.environ.get("GEODEPLOY_URL", "").rstrip("/")
TOKEN = os.environ.get("GEODEPLOY_TOKEN", "")
KEEP = os.environ.get("GEODEPLOY_KEEP", "") not in ("", "0", "false")
RUN = uuid.uuid4().hex[:6]
WORK = os.path.join("/tmp", "gd-e2e-" + RUN)

FAILURES = []
CHECKS = [0]
CURRENT = [""]
CREATED = {"vector": [], "raster": [], "portal": []}
DASH = enum(Qt, "PenStyle", "DashLine")
NOBRUSH = enum(Qt, "BrushStyle", "NoBrush")


def section(title):
    print("\n{0}\n== {1}\n{0}".format("-" * 96, title))
    CURRENT[0] = title


def check(name, condition, detail=""):
    CHECKS[0] += 1
    print(("  ok   " if condition else "  FAIL ") + name +
          ("" if condition else "   — " + str(detail)[:500]))
    if not condition:
        FAILURES.append("[{0}] {1}".format(CURRENT[0], name))


# ══ The source data, written out in every format the instance ingests ════════════════════════════

FIELDS = (("kind", QVariant.String), ("pop", QVariant.Int),
          ("height", QVariant.Double), ("name", QVariant.String))

ROWS = (("tithe", 5, 3.5, "Cotton Mill"), ("modern", 40, 12.0, "New Cut"),
        ("tithe", 90, 30.0, "Lock Keeper"), ("legacy", 250, 55.5, "Old Wharf"),
        ("modern", 700, 80.0, "Basin"), ("legacy", 1500, 120.0, "Junction"))


def memory_layer(geometry: str) -> QgsVectorLayer:
    """A small layer with real features — an EMPTY one ingests as an empty layer and proves
    nothing about tiling, statistics or the classification the style depends on."""
    layer = QgsVectorLayer("{0}?crs=EPSG:4326".format(geometry), "src", "memory")
    provider = layer.dataProvider()
    provider.addAttributes([QgsField(n, t) for n, t in FIELDS])
    layer.updateFields()
    feats = []
    for i, row in enumerate(ROWS):
        feature = QgsFeature(layer.fields())
        feature.setAttributes(list(row))
        x, y = -2.0 + i * 0.05, 53.0 + i * 0.03
        if geometry.lower().startswith("point"):
            geom = QgsGeometry.fromPointXY(QgsPointXY(x, y))
        elif geometry.lower().startswith("linestring"):
            geom = QgsGeometry.fromPolylineXY(
                [QgsPointXY(x, y), QgsPointXY(x + 0.04, y + 0.02), QgsPointXY(x + 0.08, y)])
        else:
            geom = QgsGeometry.fromPolygonXY([[QgsPointXY(x, y), QgsPointXY(x + 0.04, y),
                                               QgsPointXY(x + 0.04, y + 0.03),
                                               QgsPointXY(x, y + 0.03), QgsPointXY(x, y)]])
        feature.setGeometry(geom)
        feats.append(feature)
    provider.addFeatures(feats)
    layer.updateExtents()
    QgsProject.instance().addMapLayer(layer)
    return layer


def write(layer, driver: str, filename: str, layer_name=None, append=False) -> str:
    """One layer written out with a real OGR driver — the same file a user would hand the uploader."""
    path = os.path.join(WORK, filename)
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = driver
    options.fileEncoding = "UTF-8"
    if layer_name:
        options.layerName = layer_name
    if append:
        options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteLayer
    error = QgsVectorFileWriter.writeAsVectorFormatV3(
        layer, path, QgsProject.instance().transformContext(), options)
    code = error[0] if isinstance(error, (list, tuple)) else error
    if code != QgsVectorFileWriter.NoError:
        raise RuntimeError("{0} → {1}: {2}".format(driver, filename, error))
    return path


def zip_shapefile(path: str) -> str:
    import zipfile
    base = os.path.splitext(path)[0]
    out = base + ".zip"
    with zipfile.ZipFile(out, "w") as archive:
        for ext in (".shp", ".shx", ".dbf", ".prj", ".cpg"):
            if os.path.exists(base + ext):
                archive.write(base + ext, os.path.basename(base + ext))
    return out


def zip_in_folder(path: str) -> str:
    """A shapefile zipped INSIDE a directory — the shape of nearly every shapefile anyone is sent.

    The archive reader used to list only the top level, so this arrived as "ZIP file contains no
    .shp file": a correct upload refused for a reason that was not true.
    """
    import zipfile
    base = os.path.splitext(path)[0]
    out = base + "_folder.zip"
    with zipfile.ZipFile(out, "w") as archive:
        for ext in (".shp", ".shx", ".dbf", ".prj", ".cpg"):
            if os.path.exists(base + ext):
                archive.write(base + ext, "survey_2024/" + os.path.basename(base + ext))
    return out


def zipped(path: str) -> str:
    """One file in a `.zip`.

    THE UPLOAD ROUTE ACCEPTS `.zip`, `.geojson`, `.json`, `.gpkg`, `.csv` and `.parquet` — nothing
    else — so a FlatGeobuf, a KML or a GML reaches the ingest only inside an archive, where GDAL
    opens it by content. That is a real path a user takes, and whether GDAL on the server can read
    those from a zip is the sort of thing nobody finds out until somebody tries it.
    """
    import zipfile
    out = os.path.splitext(path)[0] + ".zip"
    with zipfile.ZipFile(out, "w") as archive:
        archive.write(path, os.path.basename(path))
        side = os.path.splitext(path)[0] + ".xsd"          # GML writes a schema beside itself
        if os.path.exists(side):
            archive.write(side, os.path.basename(side))
    return out


def csv_with_latlon(name: str) -> str:
    path = os.path.join(WORK, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("kind,pop,height,name,lon,lat\n")
        for i, (kind, pop, height, label) in enumerate(ROWS):
            fh.write("{0},{1},{2},{3},{4},{5}\n".format(
                kind, pop, height, label, -2.0 + i * 0.05, 53.0 + i * 0.03))
    return path


def csv_with_wkt(name: str) -> str:
    path = os.path.join(WORK, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("kind,pop,height,name,geom\n")
        for i, (kind, pop, height, label) in enumerate(ROWS):
            x, y = -2.0 + i * 0.05, 53.0 + i * 0.03
            wkt = "POLYGON(({0} {1},{2} {1},{2} {3},{0} {3},{0} {1}))".format(
                x, y, x + 0.04, y + 0.03)
            fh.write('{0},{1},{2},{3},"{4}"\n'.format(kind, pop, height, label, wkt))
    return path


# ══ The symbologies — each one a thing that used to be lost ══════════════════════════════════════

def style_line_per_class(layer):
    """The reported layer: two categories in the SAME colour, differing only by dash and width."""
    dashed = QgsLineSymbol.createSimple({"color": "#84d9ff"})
    dashed.symbolLayer(0).setWidth(0.6)
    dashed.symbolLayer(0).setPenStyle(DASH)
    solid = QgsLineSymbol.createSimple({"color": "#84d9ff"})
    solid.symbolLayer(0).setWidth(1.4)
    third = QgsLineSymbol.createSimple({"color": "#e24646"})
    third.symbolLayer(0).setWidth(0.9)
    layer.setRenderer(QgsCategorizedSymbolRenderer("kind", [
        QgsRendererCategory("tithe", dashed, "Tithe"),
        QgsRendererCategory("modern", solid, "Modern"),
        QgsRendererCategory("legacy", third, "Legacy")]))
    labels_mod.to_qgis(layer, {"labels": {"enabled": True, "field": "name", "size": 11.0,
                                          "color": "#232323", "halo_color": "#ffffff",
                                          "halo_width": 1.5}})
    return layer


def style_polygon_hollow_class(layer):
    """One class that fills nothing among classes that do, plus a DASHED border — both of which
    used to be taken from the first class and applied to the whole layer."""
    hollow = QgsFillSymbol.createSimple({"color": "#e9c9b0", "outline_color": "#cc22d2"})
    hollow.symbolLayer(0).setBrushStyle(NOBRUSH)
    hollow.symbolLayer(0).setStrokeStyle(DASH)
    hollow.symbolLayer(0).setStrokeWidth(0.66)
    solid = QgsFillSymbol.createSimple({"color": "#d5b43c", "outline_color": "#7c5c00"})
    third = QgsFillSymbol.createSimple({"color": "#3b82f6", "outline_color": "#1d4ed8"})
    layer.setRenderer(QgsCategorizedSymbolRenderer("kind", [
        QgsRendererCategory("tithe", hollow, "Hachure"),
        QgsRendererCategory("modern", solid, "Infrastructure"),
        QgsRendererCategory("legacy", third, "Legacy")]))
    return layer


def style_point_per_class(layer):
    """A marker SHAPE and SIZE per class — and a 10 mm marker, which is the size bug's fixture."""
    small = QgsMarkerSymbol.createSimple({"name": "square", "color": "#ff0000"})
    small.setSize(3.0)
    big = QgsMarkerSymbol.createSimple({"name": "triangle", "color": "#22c55e"})
    big.setSize(10.0)                    # MILLIMETRES, QGIS's default unit — 28.35 pt, radius 18.9
    third = QgsMarkerSymbol.createSimple({"name": "star", "color": "#a855f7"})
    third.setSize(6.0)
    layer.setRenderer(QgsCategorizedSymbolRenderer("kind", [
        QgsRendererCategory("tithe", small, "Small"),
        QgsRendererCategory("modern", big, "Big"),
        QgsRendererCategory("legacy", third, "Star")]))
    labels_mod.to_qgis(layer, {"labels": {"enabled": True, "field": "name", "size": 12.0,
                                          "color": "#111111"}})
    return layer


def style_graduated_widths(layer):
    """A graduated LINE with a width per class — the second half of the per-class fix."""
    ranges = []
    for lo, hi, colour, width in ((0, 100, "#eeeeee", 0.3), (100, 800, "#888888", 1.0),
                                  (800, 5000, "#111111", 3.0)):
        symbol = QgsLineSymbol.createSimple({"color": colour})
        symbol.symbolLayer(0).setWidth(width)
        ranges.append(QgsRendererRange(lo, hi, symbol, "{0}-{1}".format(lo, hi)))
    layer.setRenderer(QgsGraduatedSymbolRenderer("pop", ranges))
    return layer


# ══ The cases: one file format each, carrying one symbology that used to be lost ═════════════════

def build_sources():
    """Every format, written from the SAME six features so a difference is the format's, not the
    data's."""
    os.makedirs(WORK, exist_ok=True)
    cases = []

    def case(label, path, styler, geometry, **extra):
        cases.append(dict(label=label, path=path, styler=styler, geometry=geometry, **extra))

    lines = memory_layer("LineString")
    polys = memory_layer("Polygon")
    points = memory_layer("Point")

    # GeoPackage — the format QGIS itself writes, and the one the reporter's files were in.
    case("GeoPackage / line", write(lines, "GPKG", "lines.gpkg", "lines"),
         style_line_per_class, "LineString")
    case("GeoPackage / polygon", write(polys, "GPKG", "polys.gpkg", "polys"),
         style_polygon_hollow_class, "Polygon")

    # A MULTI-LAYER GeoPackage, which is how a QGIS "package layers" export arrives — and which is
    # what four separate ingest bugs were found in.
    multi = os.path.join(WORK, "multi.gpkg")
    write(lines, "GPKG", "multi.gpkg", "canal_lines")
    write(polys, "GPKG", "multi.gpkg", "canal_polys", append=True)
    write(points, "GPKG", "multi.gpkg", "canal_points", append=True)
    case("GeoPackage / three layers in one file", multi, style_line_per_class, "LineString",
         multilayer=3)

    # Shapefile — zipped, because a bare .shp is not a dataset. Its DBF truncates field names at 10
    # characters, which is exactly the kind of thing that breaks a classification silently.
    shp = write(lines, "ESRI Shapefile", "shp_lines.shp")
    case("Shapefile (zipped) / line", zip_shapefile(shp), style_line_per_class, "LineString")
    # A SHAPEFILE INSIDE A FOLDER inside the zip, which is what QGIS and ArcGIS both hand you and
    # what the recursive search exists for.
    case("Shapefile zipped inside a folder", zip_in_folder(shp), style_line_per_class, "LineString")

    case("GeoJSON / polygon", write(polys, "GeoJSON", "polys.geojson"),
         style_polygon_hollow_class, "Polygon")
    case("FlatGeobuf (zipped) / polygon", zipped(write(polys, "FlatGeobuf", "polys.fgb")),
         style_polygon_hollow_class, "Polygon")
    case("KML (zipped) / point", zipped(write(points, "KML", "points.kml")),
         style_point_per_class, "Point")
    case("GML (zipped) / line", zipped(write(lines, "GML", "gml_lines.gml")),
         style_graduated_widths, "LineString")
    case("GeoPackage (zipped)", zipped(write(polys, "GPKG", "zipped_polys.gpkg", "polys")),
         style_polygon_hollow_class, "Polygon")
    # A CSV HAS NO GEOMETRY UNTIL THE SERVER SNIFFS ONE. OGR opens it as a table, so styling the
    # file itself would classify a layer with no geometry and read back nothing — the symbology
    # under test is the one a user applies AFTER the layer is on the instance.
    case("CSV with lon/lat columns", csv_with_latlon("points.csv"), style_point_per_class, "Point",
         style_from_memory=True)
    case("CSV with a WKT column", csv_with_wkt("wkt.csv"), style_polygon_hollow_class, "Polygon",
         style_from_memory=True)

    for entry in cases:
        entry["size"] = os.path.getsize(entry["path"])
    return cases


# ══ One case, all the way round ══════════════════════════════════════════════════════════════════

def styled_copy(path, styler, geometry, layer_name=None):
    """The uploaded file, reopened and styled — the layer a user would be looking at."""
    uri = path if not layer_name else "{0}|layername={1}".format(path, layer_name)
    layer = QgsVectorLayer(uri, "e2e", "ogr")
    if not layer.isValid():
        return None
    QgsProject.instance().addMapLayer(layer)
    return styler(layer)


def wait_ready(client, layer_id, seconds=180):
    """An ingest is a Celery job; the style and the tiles are not there until it finishes."""
    deadline = time.time() + seconds
    row = {}
    while time.time() < deadline:
        row = client.layers.api("vector").get(layer_id)
        if (row.get("status") or "").lower() in ("ready", "error", "failed"):
            return row
        time.sleep(3)
    return row


def compare(label, sent, stored, geometry):
    """Two styles, folded the way the plugin folds them before deciding a layer was restyled."""
    a = symbology.comparable_style(sent, geometry)
    b = symbology.comparable_style(stored, geometry)
    diff = {k: (a.get(k), b.get(k)) for k in set(a) | set(b) if a.get(k) != b.get(k)}
    check("{0}: the instance stored what QGIS sent".format(label), not diff,
          json.dumps(diff, default=str))
    return diff


def run_case(client, entry):
    label = entry["label"]
    section(label)
    layer = (None if entry.get("style_from_memory")
             else styled_copy(entry["path"], entry["styler"], entry["geometry"],
                              entry.get("layer_name")))
    if layer is None:
        layer = entry["styler"](memory_layer(entry["geometry"]))
    sent = symbology.from_qgis(layer) or {}
    check("{0}: QGIS produced a style at all".format(label), bool(sent), json.dumps(sent)[:200])
    entries = sent.get("categories") or sent.get("classes") or []
    shaped = [c for c in entries
              if any(k in c for k in symbology.CLASS_SHAPE_KEYS)]
    if entry["styler"] is not style_polygon_hollow_class or True:
        check("{0}: at least one class carries a shape of its own".format(label),
              bool(shaped) or not entries, json.dumps(entries)[:300])

    # ── 1. UPLOAD ────────────────────────────────────────────────────────────────────────────────
    name = "E2E {0} {1}".format(RUN, label)
    result, error = None, None
    for attempt in range(3):
        try:
            result = client.uploads.upload(entry["path"], name=name, wait=True)
            error = None
            break
        except Exception as exc:                                                 # noqa: BLE001
            error = "{0}: {1}".format(type(exc).__name__, exc)
            # THE INSTANCE RATE-LIMITS UPLOADS, and ten files back to back is not a normal user.
            # A 429 is the limiter doing its job, not a failure of the ingest, so it is waited out
            # rather than reported as one.
            if "429" not in str(exc):
                break
            print("  .. rate limited; waiting {0}s".format(20 * (attempt + 1)))
            time.sleep(20 * (attempt + 1))
    if result is None:
        check("{0}: uploads".format(label), False, error)
        return None
    layer_id = getattr(result, "layer_id", None)
    check("{0}: uploads and returns a layer".format(label), bool(layer_id), result.as_dict())
    if not layer_id:
        return None
    CREATED["vector"].append(layer_id)

    row = wait_ready(client, layer_id)
    check("{0}: the ingest finished ready".format(label), (row.get("status") or "") == "ready",
          "{0} — {1}".format(row.get("status"), str(row.get("error"))[:200]))
    if (row.get("status") or "") != "ready":
        return None
    geometry = row.get("geometry_type") or entry["geometry"]
    check("{0}: the geometry survived the format".format(label),
          entry["geometry"].lower().replace("multi", "") in (geometry or "").lower(),
          "{0} → {1}".format(entry["geometry"], geometry))

    # THE CLASSIFICATION COLUMN HAS TO STILL EXIST. A Shapefile truncates a field name at ten
    # characters and a CSV may type it differently — either turns a categorized layer into a layer
    # classified by a column that is not there, which draws in the fallback colour and says nothing.
    fields = {f.get("name") if isinstance(f, dict) else str(f) for f in (row.get("fields") or [])}
    if sent.get("color_field") and fields:
        check("{0}: the column the classes are driven by survived".format(label),
              sent["color_field"] in fields, "{0} not in {1}".format(sent["color_field"],
                                                                     sorted(fields)[:12]))

    # ── 2. STORE THE STYLE, AND READ IT BACK ─────────────────────────────────────────────────────
    client.layers.api("vector").set_default_style(
        layer_id, {"opacity": 1.0, "style": sent, "popup_fields": []})
    stored_row = client.layers.api("vector").get(layer_id)
    stored = (stored_row.get("default_style") or {}).get("style") or {}
    compare(label, sent, stored, geometry)

    # THE PER-CLASS SHAPES SPECIFICALLY — the thing this whole round is about.
    for key in ("categories", "classes"):
        if not sent.get(key):
            continue
        want = [{k: v for k, v in c.items() if k in symbology.CLASS_SHAPE_KEYS}
                for c in sent[key]]
        got = [{k: v for k, v in c.items() if k in symbology.CLASS_SHAPE_KEYS}
               for c in (stored.get(key) or [])]
        check("{0}: every class kept its own shape".format(label), want == got,
              "sent {0}  stored {1}".format(json.dumps(want), json.dumps(got)))

    # ── 3. THE LEGEND — what the layer page and the portal's list draw ───────────────────────────
    # READ WITH THE TOKEN. The client's `legend()` asks anonymously, which a private layer rightly
    # refuses — the plugin reads it signed in, and that is the path a user is on.
    try:
        legend = client.get("/data/vector/{0}/legend".format(layer_id))
    except Exception as exc:                                                     # noqa: BLE001
        legend = {"error": "{0}: {1}".format(type(exc).__name__, exc)}
    check("{0}: the legend lists every class".format(label),
          not entries or len(legend.get("entries") or []) >= len(entries),
          "{0} entries for {1} classes  {2}".format(
              len(legend.get("entries") or []), len(entries), legend.get("error") or ""))
    # THE CLASS SHAPE HAS TO REACH THE LEGEND TOO, or the swatch beside a class is drawn in the
    # layer-level dash rather than the one that class actually uses.
    if shaped and legend.get("entries"):
        shapes = {json.dumps({k: v for k, v in e.items()
                              if k in symbology.CLASS_SHAPE_KEYS}, sort_keys=True)
                  for e in legend["entries"]}
        check("{0}: the legend swatches differ where the classes do".format(label),
              len(shapes) > 1, json.dumps(sorted(shapes))[:400])

    return {"id": layer_id, "sent": sent, "stored": stored, "geometry": geometry, "label": label,
            "row": stored_row}


# ══ A RASTER, which is a different shape of style entirely ═══════════════════════════════════════

def write_geotiff(path):
    """A small single-band GeoTIFF with values well outside 0-255.

    THE RANGE IS THE POINT. Non-8-bit data renders BLACK on a tile server that assumes 0-255, so the
    min/max stretch is the difference between a visible layer and a black rectangle — which is why
    `rescale` is the raster key that matters most and the first thing to check survived.
    """
    from osgeo import gdal, osr
    driver = gdal.GetDriverByName("GTiff")
    dataset = driver.Create(path, 64, 64, 1, gdal.GDT_Float32)
    dataset.SetGeoTransform((-2.0, 0.002, 0, 53.2, 0, -0.002))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(4326)
    dataset.SetProjection(srs.ExportToWkt())
    band = dataset.GetRasterBand(1)
    rows = [[120.0 + x * 3.5 + y * 1.25 for x in range(64)] for y in range(64)]
    try:
        import numpy
        band.WriteArray(numpy.array(rows, dtype="float32"))
    except ImportError:                                                          # pragma: no cover
        import struct
        for y, row in enumerate(rows):
            band.WriteRaster(0, y, 64, 1, struct.pack("<64f", *row))
    band.SetNoDataValue(-9999.0)
    dataset.FlushCache()
    dataset = None
    return path


def run_raster(client):
    """A GeoTIFF, all the way round. A raster style is `{colormap, rescale, bidx, algorithm}` — a
    different shape from a vector's classes, and it has its own reader and writer on both sides."""
    section("GeoTIFF — a raster, which has its own style vocabulary")
    from qgis.core import QgsRasterLayer

    path = write_geotiff(os.path.join(WORK, "terrain.tif"))
    check("a GeoTIFF was written", os.path.getsize(path) > 1000, os.path.getsize(path))

    name = "E2E {0} GeoTIFF".format(RUN)
    try:
        result = client.uploads.upload(path, name=name, wait=True)
    except Exception as exc:                                                     # noqa: BLE001
        check("the raster uploads", False, "{0}: {1}".format(type(exc).__name__, exc))
        return
    layer_id = getattr(result, "layer_id", None)
    check("the raster uploads and returns a layer", bool(layer_id), result.as_dict())
    if not layer_id:
        return
    CREATED["raster"].append(layer_id)
    check("...as a RASTER, not a vector", result.plan.layer_type == "raster",
          result.plan.layer_type)

    deadline = time.time() + 240
    row = {}
    while time.time() < deadline:
        row = client.layers.api("raster").get(layer_id)
        if (row.get("status") or "").lower() in ("ready", "error", "failed"):
            break
        time.sleep(3)
    check("the raster ingest finished ready", (row.get("status") or "") == "ready",
          "{0} — {1}".format(row.get("status"), str(row.get("error"))[:200]))
    if (row.get("status") or "") != "ready":
        return

    # THE COLORMAP NAME IS ONLY CLAIMED WHEN THE SERVER HAS ONE BY THAT NAME. QGIS ramps and
    # TiTiler colormaps are different catalogues that happen to share many names, and a wrong one
    # is worse than the default — so the instance is asked.
    try:
        colormaps = client.layers.api("raster").colormaps()
    except Exception:                                                            # noqa: BLE001
        colormaps = []
    check("the instance lists its colormaps", bool(colormaps), len(colormaps or []))
    ramp = "viridis" if "viridis" in (colormaps or []) else (colormaps or ["terrain"])[0]

    # THE STYLE COMES OUT OF A REAL QGIS RENDERER, not written by hand here. That is the whole
    # point: hand-writing it tests the API's schema and nothing about the plugin, and it is exactly
    # how a first attempt at this section sent `rescale` as a LIST and got a 422 — a bug in the
    # harness that the plugin does not have, because `_rescale_text` writes "min,max".
    source = QgsRasterLayer(path, "e2e source", "gdal")
    QgsProject.instance().addMapLayer(source)
    check("QGIS can open the GeoTIFF", source.isValid(),
          source.error().summary() if not source.isValid() else "")
    if not source.isValid():
        return
    symbology.raster_to_qgis(source, {"colormap": ramp, "rescale": "120,420", "bidx": [1]})
    style = symbology.raster_from_qgis(source, colormaps) or {}
    check("QGIS produced a raster style", bool(style), json.dumps(style)[:200])
    check("...with the stretch as the STRING the API takes",
          isinstance(style.get("rescale"), str), repr(style.get("rescale")))

    client.layers.api("raster").set_default_style(layer_id, dict(style, opacity=1.0))
    stored = client.layers.api("raster").get(layer_id).get("default_style") or {}
    diff = {k: (style.get(k), stored.get(k)) for k in style if style.get(k) != stored.get(k)}
    check("the raster style is stored as sent", not diff, json.dumps(diff, default=str))

    # ── back into QGIS, through the same reader and writer the plugin uses ───────────────────────
    fresh = QgsRasterLayer(path, "e2e raster", "gdal")
    QgsProject.instance().addMapLayer(fresh)
    applied = symbology.raster_to_qgis(fresh, dict(stored))
    check("the stored raster style applies in QGIS", applied, repr(applied))
    back = symbology.raster_from_qgis(fresh, colormaps) or {}
    # OPACITY IS THE LAYER'S, not the style's, on both sides of this — a raster's stored document
    # happens to hold it in the same dict as the colouring, but `raster_from_qgis` reads a RENDERER
    # and a renderer has no opacity. Comparing them would report every raster as restyled.
    a = symbology.comparable_style({k: v for k, v in stored.items() if k != "opacity"}, "raster")
    b = symbology.comparable_style(back, "raster")
    diff = {k: (a.get(k), b.get(k)) for k in set(a) | set(b) if a.get(k) != b.get(k)}
    check("the raster style survives the trip back", not diff, json.dumps(diff, default=str))
    check("...including the stretch, which is the difference between a picture and a black square",
          [round(float(v), 1) for v in str(back.get("rescale") or "").split(",") if v]
          == [120.0, 420.0], repr(back.get("rescale")))

    # A HILLSHADE is a real renderer on both sides, so it must read back as one rather than as the
    # colormap it replaces.
    shaded = QgsRasterLayer(path, "e2e hillshade", "gdal")
    QgsProject.instance().addMapLayer(shaded)
    symbology.raster_to_qgis(shaded, {"algorithm": "hillshade", "zfactor": 5.0,
                                      "rescale": "120,420"})
    read = symbology.raster_from_qgis(shaded, colormaps) or {}
    check("a hillshade reads back as a hillshade", read.get("algorithm") == "hillshade",
          json.dumps(read)[:200])
    check("...at the exaggeration it was given", abs((read.get("zfactor") or 0) - 5.0) < 0.01,
          read.get("zfactor"))


# ══ The portal — what the browser actually draws ═════════════════════════════════════════════════

#: The front door refuses a default `Python-urllib/3.x` with a 403, and that 403 reads exactly like
#: "the portal is not published yet" — which cost a whole run before anybody checked with curl.
USER_AGENT = "geodeploy-e2e/1.0"


def fetch(url, timeout=30):
    """A GET that identifies itself, signed in. Returns the decoded body, or raises."""
    import urllib.request
    request = urllib.request.Request(url, headers={"Authorization": "Bearer " + TOKEN,
                                                   "User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:           # nosec B310
        return response.read().decode("utf-8")


def wait_published(slug, seconds=120):
    """Publishing writes a static bundle from a Celery job — the call returning is not the file
    existing, and reading it too early is a 403 from nginx rather than anything about the style."""
    deadline = time.time() + seconds
    url = "{0}/portals/{1}/style.json".format(URL, slug)
    last = ""
    while time.time() < deadline:
        try:
            fetch(url, timeout=20)
            return True
        except Exception as exc:                                                 # noqa: BLE001
            last = "{0}: {1}".format(type(exc).__name__, exc)
        time.sleep(3)
    print("  .. {0} never became readable ({1})".format(url, last))
    return False


def check_published_style(client, portal, results):
    """The published `style.json`, which is the map. Everything before this is a promise about it."""
    section("The published portal — what a browser downloads and draws")
    # THE PUBLISHED BUNDLE IS STATIC, served by nginx from the portal address — not by the API.
    # `/portals/<slug>/style.json` is the document a browser downloads, so it is the one to read:
    # anything derived from the database would be testing the intention rather than the map.
    url = "{0}/portals/{1}/style.json".format(URL, portal.get("slug"))
    try:
        style = json.loads(fetch(url, timeout=60))
    except Exception as exc:                                                     # noqa: BLE001
        check("the published style.json is fetchable", False,
              "{0}  ->  {1}: {2}".format(url, type(exc).__name__, exc))
        return
    check("the published style.json is fetchable", bool(style.get("layers")),
          json.dumps(sorted(style or {}))[:200])
    print("  NOTE: this section reads the PUBLISHED bundle, so it measures the API version the")
    print("        instance is running. A failure here with the plugin sections green means the")
    print("        instance has not been rebuilt, not that the style is wrong.")

    by_layer = {}
    for ml in style.get("layers") or []:
        lid = (ml.get("metadata") or {}).get("geodeploy:layer_id")
        if lid is not None:
            by_layer.setdefault(str(lid), []).append(ml)

    for result in results:
        if result is None:
            continue
        label, lid = result["label"], str(result["id"])
        drawn = by_layer.get(lid) or []
        check("{0}: the portal draws it".format(label), bool(drawn),
              "no render layer carries geodeploy:layer_id {0}".format(lid))
        if not drawn:
            continue
        sent = result["sent"]
        entries = sent.get("categories") or sent.get("classes") or []
        varied = [c for c in entries if any(k in c for k in symbology.CLASS_SHAPE_KEYS)]

        # A CLASSIFICATION THAT VARIES BY MORE THAN COLOUR MUST BE SEVERAL RENDER LAYERS. One layer
        # cannot draw two dashes, whatever expression is written into it.
        #
        # COUNTED BY FILTER, NOT BY LAYER. A polygon already emits a fill plus an outline line and
        # a labelled layer emits a text layer beside them, so "more than one render layer" is true
        # of an unsplit layer too — the thing that makes a layer a CLASS is that it is filtered to
        # one, and an unsplit layer has no filter at all.
        symbol_layers = [ml for ml in drawn if ml.get("type") in ("line", "fill", "symbol",
                                                                  "circle")]
        classed = [ml for ml in symbol_layers if ml.get("filter") is not None]
        if varied:
            check("{0}: it is split into a filtered layer per class".format(label),
                  len(classed) >= len(entries),
                  "{0} filtered of {1} render layers for {2} classes: {3}".format(
                      len(classed), len(symbol_layers), len(entries),
                      [(ml.get("id"), ml.get("type")) for ml in drawn]))
        else:
            check("{0}: an unvaried classification is NOT split".format(label),
                  not classed, [ml.get("id") for ml in classed])

        # THE DASH. The reported layer's whole point: two classes in one colour, one dashed.
        dashes = [bool((ml.get("paint") or {}).get("line-dasharray")) for ml in symbol_layers
                  if ml.get("type") == "line"]
        if any("dashed" == c.get("lineType") for c in entries) or sent.get("lineType") == "dashed":
            check("{0}: something in it is drawn dashed".format(label), any(dashes), dashes)
        if any(c.get("lineType") == "solid" for c in entries) and sent.get("lineType") == "dashed":
            check("{0}: …and something else is drawn solid".format(label),
                  not all(dashes), dashes)

        # THE WIDTHS the classes asked for.
        widths = sorted({(ml.get("paint") or {}).get("line-width") for ml in symbol_layers
                         if ml.get("type") == "line"} - {None})
        wanted = sorted({c.get("line_width") for c in entries if c.get("line_width")}
                        | ({sent["line_width"]} if sent.get("line_width") else set()))
        if len(wanted) > 1:
            check("{0}: the map draws each class at its own width".format(label),
                  len(widths) == len(wanted), "drawn {0}  wanted {1}".format(widths, wanted))

        # LABELS ride along with whatever draws the geometry.
        if (sent.get("labels") or {}).get("enabled"):
            has_labels = [ml for ml in drawn
                          if (ml.get("layout") or {}).get("text-field") is not None]
            check("{0}: the labels are published too".format(label), bool(has_labels),
                  [ml.get("id") for ml in drawn])

        # AN OUTLINE-ONLY CLASS must paint no area, and its border must still be there.
        if any(c.get("fill_opacity") == 0 for c in entries):
            fills = [ml for ml in drawn if ml.get("type") == "fill"]
            opacities = [(ml.get("paint") or {}).get("fill-opacity") for ml in fills]
            check("{0}: the hollow class paints nothing".format(label), 0 in opacities
                  or 0.0 in opacities, opacities)
            check("{0}: …while the others still fill".format(label),
                  any(o for o in opacities), opacities)


# ══ Back to QGIS — the fourth surface ════════════════════════════════════════════════════════════

def reopen_in_qgis(client, results):
    """Every layer opened from the instance again, and compared with the renderer it started as.

    Two surfaces, because the plugin offers both: the FEATURES (OGC API), which QGIS renders with
    an ordinary renderer, and the TILES, which get `QgsVectorTileBasicRenderer` — a different
    renderer with a different vocabulary, and the one a portal opened as a group lands on.
    """
    section("Back in QGIS — the layer reopened from the instance")
    for result in results:
        if result is None:
            continue
        label, stored, geometry = result["label"], result["stored"], result["geometry"]

        # ── the FEATURE path ─────────────────────────────────────────────────────────────────────
        fresh = memory_layer("Point" if "point" in geometry.lower() else
                             "LineString" if "line" in geometry.lower() else "Polygon")
        applied = symbology.apply_to_qgis(fresh, dict(stored))
        check("{0}: the stored style applies to a QGIS layer".format(label), applied, repr(applied))
        if not applied:
            continue
        back = symbology.from_qgis(fresh) or {}
        diff = compare("{0} (reopened)".format(label), stored, back, geometry)
        # AND THE CLASSES STILL DIFFER FROM EACH OTHER — the property that makes the map right.
        renderer = fresh.renderer()
        symbols = []
        for getter in ("categories", "ranges"):
            items = getattr(renderer, getter, lambda: [])()
            symbols = [item.symbol().clone() for item in items if item.symbol() is not None]
            if symbols:
                break
        entries = stored.get("categories") or stored.get("classes") or []
        if [c for c in entries if any(k in c for k in symbology.CLASS_SHAPE_KEYS)]:
            shapes = {json.dumps(symbology._style_from_symbol(s), sort_keys=True) for s in symbols}
            check("{0}: the classes are still DIFFERENT symbols in QGIS".format(label),
                  len(shapes) > 1, "{0} distinct symbols for {1} classes".format(
                      len(shapes), len(symbols)))

        # ── the TILE path ────────────────────────────────────────────────────────────────────────
        try:
            from qgis.core import QgsVectorTileLayer
            tiles = QgsVectorTileLayer(
                "type=xyz&url=https://example.invalid/{z}/{x}/{y}.pbf&zmin=0&zmax=14", label)
            QgsProject.instance().addMapLayer(tiles)
            tiles.setCustomProperty(symbology.P_GEOMETRY,
                                    "point" if "point" in geometry.lower() else
                                    "line" if "line" in geometry.lower() else "polygon")
            ok = symbology.apply_to_vector_tiles(tiles, result["row"], "src", dict(stored))
            check("{0}: it styles as vector TILES too".format(label), ok, repr(ok))
            if ok:
                read = symbology.style_from_vector_tiles(tiles) or {}
                check("{0}: tiles read back the same mode".format(label),
                      (read.get("color_mode") or "single") == (stored.get("color_mode") or "single"),
                      "{0} vs {1}".format(read.get("color_mode"), stored.get("color_mode")))
        except ImportError:                                                      # pragma: no cover
            pass


# ══ Driver ═══════════════════════════════════════════════════════════════════════════════════════

def cleanup(client):
    section("Cleanup")
    if KEEP:
        print("  GEODEPLOY_KEEP is set — leaving {0} layers and {1} portals in place.".format(
            len(CREATED["vector"]), len(CREATED["portal"])))
        return
    for portal_id in CREATED["portal"]:
        try:
            client.portals.delete(portal_id)
            print("  deleted portal {0}".format(portal_id))
        except Exception as exc:                                                 # noqa: BLE001
            print("  !! portal {0} not deleted: {1}".format(portal_id, exc))
    for kind in ("vector", "raster"):
        for layer_id in CREATED[kind]:
            try:
                client.layers.api(kind).delete(layer_id)
                print("  deleted {0} layer {1}".format(kind, layer_id))
            except Exception as exc:                                             # noqa: BLE001
                print("  !! {0} layer {1} not deleted: {2}".format(kind, layer_id, exc))


def main():
    if not URL or not TOKEN:
        print("Set GEODEPLOY_URL and GEODEPLOY_TOKEN. Nothing was done.")
        os._exit(2)
    print("GeoDeploy end-to-end — QGIS {0} → {1}".format(Qgis.QGIS_VERSION, URL))
    print("run {0}; everything created is named 'E2E {0} …'".format(RUN))
    client = Client(URL, token=TOKEN)
    who = client.whoami()
    print("signed in as {0}".format(who.get("email") or who.get("name") or who))

    results = []
    try:
        cases = build_sources()
        print("\n{0} source files written:".format(len(cases)))
        for entry in cases:
            print("  {0:44s} {1:>9,d} bytes  {2}".format(
                entry["label"], entry["size"], os.path.basename(entry["path"])))
        for entry in cases:
            try:
                results.append(run_case(client, entry))
            except Exception:                                                    # noqa: BLE001
                print("!! {0} raised:".format(entry["label"]))
                traceback.print_exc()
                FAILURES.append("[{0}] raised".format(entry["label"]))

        try:
            run_raster(client)
        except Exception:                                                        # noqa: BLE001
            print("!! the raster case raised:")
            traceback.print_exc()
            FAILURES.append("[GeoTIFF] raised")

        ready = [r for r in results if r]
        if ready:
            section("Publishing a portal with all of it")
            portal = client.portals.create("E2E {0}".format(RUN),
                                           description="Round-trip check; safe to delete.")
            CREATED["portal"].append(portal["id"])
            for result in ready:
                client.portals.add_layer(portal["id"], result["id"], "vector",
                                         style=result["sent"])
            client.portals.publish(portal["id"])
            portal = client.portals.get(portal["id"])
            published = wait_published(portal.get("slug"))
            check("the portal published and its bundle is on disk", published,
                  "still not served after 90s")
            check_published_style(client, portal, ready)
            reopen_in_qgis(client, ready)
    finally:
        try:
            cleanup(client)
        except Exception:                                                        # noqa: BLE001
            traceback.print_exc()

    print("\n{0} checks, {1} failed".format(CHECKS[0], len(FAILURES)))
    for name in FAILURES:
        print("   FAILED: {0}".format(name))
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(1 if FAILURES else 0)


if __name__ == "__main__":
    main()
