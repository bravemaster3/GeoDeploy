"""Turn whatever QGIS has open into a file GeoDeploy can ingest.

`layer.source()` is not a path. It is a provider URI, and reading it as one silently does the wrong
thing:

* a **filtered** layer is `…/roads.gpkg|layername=roads|subset="type"='primary'` — trimming at the
  first `|` uploads the whole dataset, quietly sending far more than the user is looking at;
* a **memory** layer (digitised on the spot) has no file at all;
* a **PostGIS** layer's source is a connection string;
* a layer added **from GeoDeploy** is a remote URL, and re-uploading it would be a copy.

So the rule is: write the layer out, and only skip that when the file on disk is provably what the
user means — no filter, no sub-layer selection, no unsaved edits.
"""
from __future__ import annotations

import os
import tempfile
import zipfile

from qgis.core import (QgsCoordinateTransformContext, QgsVectorFileWriter, QgsVectorLayer,
                       QgsRasterLayer)

from .vendor.geodeploy.uploads import GEOPARQUET_EXTENSIONS, LARGE_VECTOR_EXTENSIONS
try:                                    # a package, inside QGIS
    from .compat import enum
except ImportError:                     # pragma: no cover - exec'd standalone by the test harness
    from compat import enum

# What the server will actually take. Imported rather than restated: a list copied into the plugin
# would drift from the client's, and the user would learn about it as an HTTP 400 mid-upload.
UPLOADABLE = frozenset(LARGE_VECTOR_EXTENSIONS | GEOPARQUET_EXTENSIONS)

# A shapefile is not a file. Without .shx/.dbf it cannot be read, and .prj is the difference between
# a layer that lands in the right place and one that lands off the coast of Africa. QGIS reports the
# .shp alone as the source, so sending "the file" sends a fragment. GeoDeploy accepts the SET as a
# .zip, which is lossless and avoids rewriting the geometry of a large layer.
_SHAPEFILE_PARTS = (".shp", ".shx", ".dbf", ".prj", ".cpg", ".qpj", ".sbn", ".sbx", ".qix", ".fix",
                    ".idm", ".ind", ".ain", ".aih", ".atx", ".shp.xml")
_SHAPEFILE_REQUIRED = (".shx", ".dbf")


class NotUploadable(Exception):
    """Explains, in words a user can act on, why this layer cannot be sent."""


def _plain_file_source(layer) -> str | None:
    """The layer's own file, when nothing about the layer modifies it."""
    source = layer.source() or ""
    if "|" in source:
        return None                       # a filter or a sub-layer: what is on disk is not the layer
    if source.startswith(("http://", "https://", "/vsicurl", "url=", "dbname=", "memory")):
        return None
    return source if os.path.isfile(source) else None


def check(layer) -> None:
    """Raise `NotUploadable` if this layer cannot be sent — WITHOUT exporting anything.

    The picker needs to know before the user chooses, and writing a two-gigabyte GeoPackage to find
    out would be a strange way to populate a list.
    """
    if isinstance(layer, QgsRasterLayer):
        if not _plain_file_source(layer):
            raise NotUploadable("served from elsewhere, not a local file")
        return
    if not isinstance(layer, QgsVectorLayer):
        raise NotUploadable("not a vector or raster layer")
    if layer.isEditable() and layer.isModified():
        raise NotUploadable("has unsaved edits")
    source = layer.source() or ""
    if source.startswith(("http://", "https://", "/vsicurl", "url=")):
        raise NotUploadable("served from elsewhere, not a local file")


def prepare(layer, on_status=None) -> tuple[str, bool]:
    """`(path, is_temporary)` for a layer ready to upload.

    Raises `NotUploadable` when there is nothing sensible to send, rather than uploading something
    that is not what is on screen.
    """
    def say(text):
        if on_status:
            on_status(text)

    if isinstance(layer, QgsRasterLayer):
        # A raster is only uploadable as its file: re-encoding one here would mean choosing a
        # compression and a resampling on the user's behalf, and GeoDeploy converts to COG anyway.
        path = _plain_file_source(layer)
        if not path:
            raise NotUploadable(
                "This raster is not a local file — it is served from elsewhere. Save it locally "
                "first (right-click ▸ Export ▸ Save As), then upload that.")
        return path, False

    if not isinstance(layer, QgsVectorLayer):
        raise NotUploadable("Only vector and raster layers can be uploaded.")

    if layer.isEditable() and layer.isModified():
        raise NotUploadable("This layer has unsaved edits. Save or discard them first — otherwise "
                            "what arrives would not be what you are looking at.")

    # A STYLE THAT MEASURES GEOMETRY ($area, $length, $perimeter — issue #133) needs those numbers
    # IN THE DATA, because a web map cannot measure a clipped tile. Written into the upload as
    # GeoDeploy-internal columns, computed by QGIS itself, so the threshold means what it means here.
    measures = measures_used(layer)
    if measures:
        say("Measuring {0}…".format(", ".join(m.lstrip("$") for m in measures)))
        return _write_with_measures(layer, measures), True

    plain = _plain_file_source(layer)
    if plain:
        ext = os.path.splitext(plain)[1].lower()
        if ext == ".shp":
            say("Collecting the shapefile's sidecar files…")
            return _zip_shapefile(plain, say), True
        if ext in UPLOADABLE:
            return plain, False           # send the original: no conversion, no fidelity lost
        # A local file in a format the server does not take (.tab, .kml, .gml, …). Converting is
        # better than a 400 that names extensions instead of a way forward.
        say("Converting {0} to GeoPackage…".format(ext or "this layer"))

    # Everything else — filtered, memory, PostGIS, or a format QGIS opened read-only — is written
    # out as GeoPackage. GPKG because it is what GeoDeploy ingests most faithfully: one file, any
    # geometry type, real field types, and a CRS that travels with it.
    say("Writing the layer out…")
    tmp = os.path.join(tempfile.mkdtemp(prefix="geodeploy-"), f"{_safe(layer.name())}.gpkg")

    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    options.layerName = _safe(layer.name())
    options.fileEncoding = "UTF-8"
    # The FILTERED features, not the file behind them. This is the whole reason the export exists.
    options.onlySelectedFeatures = False

    error = QgsVectorFileWriter.writeAsVectorFormatV3(
        layer, tmp, QgsCoordinateTransformContext(), options)
    # V3 returns (errorCode, errorMessage) on modern QGIS and a 3-tuple on some builds.
    code = error[0] if isinstance(error, (tuple, list)) else error
    if code != enum(QgsVectorFileWriter, "WriterError", "NoError"):
        message = error[1] if isinstance(error, (tuple, list)) and len(error) > 1 else code
        raise NotUploadable(f"QGIS could not write this layer out: {message}")
    return tmp, True


#: QGIS measure → the internal column it travels in. Must match `expressions._MEASURES` and the
#: server's `services/internal_columns.MEASURE_COLUMNS`.
MEASURE_COLUMNS = {"$area": "__gd_area", "$length": "__gd_length", "$perimeter": "__gd_perimeter"}
_MEASURE_IN_TEXT = {
    "$area": ("$area", "area($geometry)", "area( $geometry )"),
    "$length": ("$length", "length($geometry)", "length( $geometry )"),
    "$perimeter": ("$perimeter", "perimeter($geometry)", "perimeter( $geometry )"),
}


def measures_used(layer) -> list:
    """The geometry measures this layer's style uses anywhere — rules, labels, data-defined
    properties, the subset filter — read off the style document QGIS itself saves, so no place a
    measure can hide is missed. `[]` when none (the upload is then exactly what it always was)."""
    if not isinstance(layer, QgsVectorLayer):
        return []
    try:
        from qgis.PyQt.QtXml import QDomDocument
        doc = QDomDocument()
        layer.exportNamedStyle(doc)
        text = doc.toString().lower().replace("&quot;", '"')
        text += " " + (layer.subsetString() or "").lower()
    except Exception:                   # noqa: BLE001 - no style document: nothing to measure
        return []
    compact = text.replace(" ", "")
    return [m for m, spellings in _MEASURE_IN_TEXT.items()
            if any(sp.replace(" ", "") in compact for sp in spellings)]


def _write_with_measures(layer, measures) -> str:
    """The layer as a GeoPackage, plus one internal column per measure, evaluated per feature by
    QGIS's own expression engine in the PROJECT's context — the context that carries the project's
    ellipsoid and distance/area units, which is exactly what `$area` means on the QGIS map."""
    from qgis.core import (QgsExpression, QgsExpressionContext, QgsExpressionContextUtils,
                           QgsFeature, QgsField, QgsFields)
    try:
        from qgis.PyQt.QtCore import QMetaType
        double = QMetaType.Type.Double
    except Exception:                   # noqa: BLE001 - Qt5 spells it QVariant
        from qgis.PyQt.QtCore import QVariant
        double = QVariant.Double

    tmp = os.path.join(tempfile.mkdtemp(prefix="geodeploy-"), f"{_safe(layer.name())}.gpkg")
    fields = QgsFields(layer.fields())
    taken = {f.name().lower() for f in layer.fields()}
    columns = []
    for m in measures:
        name = MEASURE_COLUMNS[m]
        if name.lower() in taken:       # the source already has it: overwrite nothing of theirs
            continue
        fields.append(QgsField(name, double))
        columns.append((name, QgsExpression(m)))

    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    options.layerName = _safe(layer.name())
    options.fileEncoding = "UTF-8"
    from qgis.core import QgsProject
    writer = QgsVectorFileWriter.create(tmp, fields, layer.wkbType(), layer.crs(),
                                        QgsProject.instance().transformContext(), options)
    if writer is None or writer.hasError() != enum(QgsVectorFileWriter, "WriterError", "NoError"):
        message = writer.errorMessage() if writer is not None else "no writer"
        raise NotUploadable(f"QGIS could not write this layer out: {message}")

    context = QgsExpressionContext(QgsExpressionContextUtils.globalProjectLayerScopes(layer))
    for _name, expression in columns:
        expression.prepare(context)
    try:
        for feature in layer.getFeatures():           # the FILTERED features, as everywhere else
            context.setFeature(feature)
            values = []
            for _name, expression in columns:
                value = expression.evaluate(context)
                try:
                    values.append(float(value) if value is not None else None)
                except (TypeError, ValueError):
                    values.append(None)
            out = QgsFeature(fields)
            out.setGeometry(feature.geometry())
            out.setAttributes(list(feature.attributes()) + values)
            if not writer.addFeature(out):
                raise NotUploadable("QGIS could not write a feature out: "
                                    + (writer.errorMessage() or "unknown error"))
    finally:
        del writer                      # flushes and closes the GeoPackage
    return tmp


def _zip_shapefile(shp_path: str, say=None) -> str:
    """Zip a .shp with every sidecar that shares its stem. Returns the path to the .zip.

    Stored uncompressed for the geometry-bearing parts? No — deflate: a .dbf of repeated attribute
    values compresses hard, and the upload is over a network, not a bus.
    """
    stem, _ = os.path.splitext(shp_path)
    base = os.path.basename(stem)

    present = {}
    for part in _SHAPEFILE_PARTS:
        candidate = stem + part
        if os.path.isfile(candidate):
            present[part] = candidate
        else:  # shapefile extensions are case-blind on disk; QGIS opens ROADS.SHP happily
            upper = stem + part.upper()
            if os.path.isfile(upper):
                present[part] = upper

    missing = [p for p in _SHAPEFILE_REQUIRED if p not in present]
    if missing:
        raise NotUploadable(
            "This shapefile is incomplete: {0} is missing next to {1}. A shapefile needs its "
            "sidecar files — copy the whole set into one folder, or use Export ▸ Save As to write "
            "a GeoPackage instead.".format(" and ".join(missing), os.path.basename(shp_path)))

    if ".prj" not in present and say:
        # Not fatal — the CRS can be set on the server — but silence here becomes a mystery when
        # the layer draws in the wrong hemisphere.
        say("No .prj alongside this shapefile: its CRS will have to be set after upload.")

    out = os.path.join(tempfile.mkdtemp(prefix="geodeploy-"), _safe(base) + ".zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for part, path in present.items():
            archive.write(path, arcname=base + part)
    return out


def _safe(name: str) -> str:
    keep = "".join(c if c.isalnum() or c in "-_ " else "_" for c in (name or "layer"))
    return keep.strip().replace(" ", "_") or "layer"
