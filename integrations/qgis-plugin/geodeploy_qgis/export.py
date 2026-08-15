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

from qgis.core import (QgsCoordinateTransformContext, QgsVectorFileWriter, QgsVectorLayer,
                       QgsRasterLayer)


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

    plain = _plain_file_source(layer)
    if plain:
        return plain, False               # send the original: no conversion, no fidelity lost

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
    if code != QgsVectorFileWriter.NoError:
        message = error[1] if isinstance(error, (tuple, list)) and len(error) > 1 else code
        raise NotUploadable(f"QGIS could not write this layer out: {message}")
    return tmp, True


def _safe(name: str) -> str:
    keep = "".join(c if c.isalnum() or c in "-_ " else "_" for c in (name or "layer"))
    return keep.strip().replace(" ", "_") or "layer"
