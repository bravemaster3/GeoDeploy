"""The QGIS-facing half: a dock that lists an instance's layers, adds them, and uploads back.

Three principles this file exists to keep:

1. **Anonymous works.** A URL with no token lists what the instance publishes. Signing in adds to
   that; it is not the price of entry.
2. **Nothing blocks the UI.** Listing and uploading run in `QgsTask`, because a plugin that freezes
   QGIS while a 2 GB upload runs is a plugin people uninstall.
3. **Nothing here knows HTTP.** Every request goes through the vendored client — the same code the
   CLI runs — so the plugin cannot drift from it.
"""
from __future__ import annotations

import os
import shutil

from qgis.core import (Qgis, QgsApplication, QgsProject, QgsRasterLayer, QgsTask,
                       QgsVectorLayer)
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtWidgets import (QAbstractItemView, QAction, QCheckBox, QComboBox, QDockWidget,
                                 QHBoxLayout, QLabel, QLineEdit, QMessageBox, QProgressBar,
                                 QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from . import export, sources, symbology
from .connection import GeoDeployError, Instance, saved_instances

PLUGIN_NAME = "GeoDeploy"


class _Job(QgsTask):
    """Run one client call off the UI thread. `finished_ok(result)` / `failed(message)`."""

    def __init__(self, description, fn):
        super().__init__(description, QgsTask.CanCancel)
        self._fn = fn
        self.result = None
        self.error = ""

    def run(self):
        try:
            self.result = self._fn()
            return True
        except GeoDeployError as exc:
            self.error = str(exc)
        except Exception as exc:                      # noqa: BLE001 - surface, never crash QGIS
            self.error = f"{type(exc).__name__}: {exc}"
        return False


class GeoDeployDock(QDockWidget):
    # Progress arrives from the QgsTask's worker thread, and Qt widgets may only be touched on the
    # GUI thread. A signal is the crossing: emitting is safe from anywhere, and the connected slot
    # runs where the widgets live.
    _progress = pyqtSignal(str)

    def __init__(self, iface):
        super().__init__(PLUGIN_NAME)
        self._progress.connect(lambda text: self._say(text, bar=False))
        self.iface = iface
        self.instance: Instance | None = None
        self._rows: list[dict] = []

        body = QWidget()
        outer = QVBoxLayout(body)
        outer.setContentsMargins(8, 8, 8, 8)

        # -- connect ------------------------------------------------------------------------------
        row = QHBoxLayout()
        self.url = QComboBox()
        self.url.setEditable(True)
        self.url.setPlaceholderText("https://your-instance.org")
        for url, _token in saved_instances():         # whatever `geodeploy login` already stored
            self.url.addItem(url)
        row.addWidget(self.url, 1)
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self.connect_to_instance)
        row.addWidget(self.connect_btn)
        outer.addLayout(row)

        self.token = QLineEdit()
        self.token.setPlaceholderText("API token (optional — public data needs none)")
        self.token.setEchoMode(QLineEdit.Password)
        outer.addWidget(self.token)

        self.status = QLabel("Not connected.")
        self.status.setWordWrap(True)
        outer.addWidget(self.status)

        # -- layers -------------------------------------------------------------------------------
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Layer", "Kind", "Features"])
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.itemDoubleClicked.connect(lambda *_: self.add_selected())
        outer.addWidget(self.tree, 1)

        self.styled = QCheckBox("Apply the layer's GeoDeploy symbology")
        self.styled.setChecked(True)
        outer.addWidget(self.styled)

        self.attributes = QCheckBox("Prefer full attributes over drawing speed")
        self.attributes.setToolTip(
            "Off: one pre-generalized archive, downloaded once — generalized geometry, and only "
            "the attributes the tiles carry.\n"
            "On: OGC API - Features, which QGIS re-queries for the extent on screen — exact "
            "geometry and every attribute, at a server round-trip per pan.")
        outer.addWidget(self.attributes)

        add = QPushButton("Add to map")
        add.clicked.connect(self.add_selected)
        outer.addWidget(add)

        # -- upload -------------------------------------------------------------------------------
        self.upload_btn = QPushButton("Upload selected layer(s)…")
        self.upload_btn.setToolTip(
            "Uploads every layer selected in the Layers panel, one after another — or the active "
            "layer if nothing is selected. A layer that cannot be sent is named and skipped; the "
            "rest still go.")
        self.upload_btn.clicked.connect(self.upload_active)
        outer.addWidget(self.upload_btn)

        self.push_style = QCheckBox("Send its styling too")
        self.push_style.setChecked(True)
        self.push_style.setToolTip(
            "Save the QGIS renderer as the layer's default style, so a portal built from it looks "
            "like what you see here.")
        outer.addWidget(self.push_style)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        outer.addWidget(self.progress)

        self.setWidget(body)

    # -- helpers -----------------------------------------------------------------------------------

    def _say(self, text, level=Qgis.Info, bar=True):
        """`bar=False` for progress: the dock label updates, the message bar does not.

        QGIS's message bar is a STACK. Pushing "Uploading…" and then pushing a failure on top means
        that when the failure expires the progress message is RESTORED — so an upload that was
        rejected ended by announcing itself as under way. Progress belongs on the dock's own label,
        which is overwritten in place; only outcomes go to the bar, and each one clears what is
        behind it so nothing stale can resurface.
        """
        self.status.setText(text)
        if not bar:
            return
        bar_widget = self.iface.messageBar()
        bar_widget.clearWidgets()
        # A warning or an error waits to be dismissed. Six seconds is fine for "done"; it is not
        # fine for the one message that explains why the thing you asked for did not happen.
        duration = 0 if level in (Qgis.Warning, Qgis.Critical) else 6
        bar_widget.pushMessage(PLUGIN_NAME, text, level=level, duration=duration)

    def _busy(self, on: bool):
        self.progress.setVisible(on)
        self.progress.setRange(0, 0 if on else 1)
        self.connect_btn.setEnabled(not on)

    # -- connect -----------------------------------------------------------------------------------

    def connect_to_instance(self):
        url = (self.url.currentText() or "").strip()
        if not url:
            self._say("Enter the instance URL first.", Qgis.Warning)
            return
        token = (self.token.text() or "").strip() or None
        if not token:
            # A token stored by `geodeploy login` counts as being signed in here.
            for saved_url, saved_token in saved_instances():
                if saved_url.rstrip("/") == url.rstrip("/").replace("http://", "https://"):
                    token = saved_token
                    break
        try:
            self.instance = Instance(url, token)
        except Exception as exc:                      # noqa: BLE001 - a bad URL is a message
            self._say(f"That URL will not do: {exc}", Qgis.Critical)
            return
        self._busy(True)
        self._run(_Job("GeoDeploy: connecting", self.instance.check), self._connected)

    def _connected(self, job):
        self._busy(False)
        if job.error:
            self._say(job.error, Qgis.Critical)
            return
        info = job.result or {}
        who = f"signed in as {info.get('user')}" if info.get("authenticated") else "not signed in"
        extra = ("" if info.get("index_available")
                 else " — this instance does not publish an index, so only what your token can see "
                      "will be listed")
        # Signed in, lead with what the TOKEN can see; the public numbers are a subset of it and
        # saying only those made a full instance look nearly empty.
        if info.get("visible_layers") is None:
            counts = (f"{info.get('public_layers', 0)} public layer(s), "
                      f"{info.get('public_portals', 0)} public portal(s).")
        else:
            counts = (f"{info['visible_layers']} layer(s) and {info['visible_portals']} portal(s) "
                      f"you can see, of which {info.get('public_layers', 0)} and "
                      f"{info.get('public_portals', 0)} are public.")
        self._say(f"{info.get('url')} — {who}. {counts}{extra}")
        self.refresh_layers()

    def refresh_layers(self):
        if not self.instance:
            return
        self._busy(True)
        self._run(_Job("GeoDeploy: listing layers", self.instance.layers), self._listed)

    def _listed(self, job):
        self._busy(False)
        if job.error:
            self._say(job.error, Qgis.Critical)
            return
        self._rows = job.result or []
        self.tree.clear()
        groups: dict[str, QTreeWidgetItem] = {}
        for row in self._rows:
            backend = row.get("storage_backend") or row.get("layer_type") or "other"
            label = {"postgis": "Vector (PostGIS)", "geoparquet": "Vector (GeoParquet)",
                     "raster": "Raster (COG)"}.get(backend, backend)
            parent = groups.get(label)
            if parent is None:
                parent = QTreeWidgetItem(self.tree, [label])
                parent.setExpanded(True)
                groups[label] = parent
            count = row.get("feature_count")
            item = QTreeWidgetItem(parent, [row.get("name") or "?",
                                            row.get("geometry_type") or backend,
                                            f"{count:,}" if isinstance(count, int) else ""])
            item.setData(0, Qt.UserRole, row)
        if not self._rows:
            self._say("Nothing to list. Public data needs no account; a token shows the rest.")

    # -- add ---------------------------------------------------------------------------------------

    def _selected_row(self) -> dict | None:
        items = self.tree.selectedItems()
        return items[0].data(0, Qt.UserRole) if items else None

    def add_selected(self):
        row = self._selected_row()
        if not row:
            self._say("Pick a layer first.", Qgis.Warning)
            return
        source = sources.describe(row, prefer_attributes=self.attributes.isChecked())
        if not source:
            self._say("That layer offers nothing QGIS can read yet.", Qgis.Warning)
            return

        name = row.get("name") or "GeoDeploy layer"
        if source["kind"] == "cog":
            layer = QgsRasterLayer(source["uri"], name, "gdal")
        else:
            layer = QgsVectorLayer(source["uri"], name, source["provider"])
        if not layer.isValid():
            self._say(f"QGIS could not open the {source['kind']} source for {name}.", Qgis.Critical)
            return

        QgsProject.instance().addMapLayer(layer)
        applied = ""
        if self.styled.isChecked() and source["kind"] != "cog":
            style = (row.get("default_style") or {}).get("style") if row.get("default_style") else None
            if style is None and self.instance:
                # A public row carries no style. `layers.resolve` needs a token, so for anonymous
                # browsing — the plugin's headline promise — it can only fail, and every public
                # layer arrived unstyled. `/legend` is PUBLIC and is what the portal draws from.
                try:
                    ref = row.get("uid") or row.get("id")
                    legend = self.instance.client.layers.legend(ref)
                    style = symbology.style_from_legend(legend)
                except GeoDeployError as exc:
                    symbology._log(f"Could not read the legend for {name}: {exc}")
                    style = None
            if style:
                applied = (", styled as the portal draws it"
                           if symbology.apply_to_qgis(layer, style)
                           else " — but its saved style could not be applied; the reason is in "
                                "View > Panels > Log Messages, under GeoDeploy")
            else:
                # Distinguish "has no style" from "has one we failed to use". The first is normal.
                applied = " (no saved style on this layer)"
        self._say(f"Added {name} — {source['why']}{applied}.")

    # -- upload ------------------------------------------------------------------------------------

    def upload_active(self):
        if not self.instance:
            self._say("Connect to an instance first.", Qgis.Warning)
            return
        if not self.instance.token:
            self._say("Uploading needs a token with data:write. Public browsing does not.",
                      Qgis.Warning)
            return
        # Whatever is SELECTED in the Layers panel, falling back to the active layer. Sending five
        # layers is a normal thing to want, and doing it one at a time means five round trips
        # through this dialog.
        layers = list(self.iface.layerTreeView().selectedLayers() or [])
        if not layers:
            active = self.iface.activeLayer()
            if active is None:
                self._say("Select one or more layers in the Layers panel first.", Qgis.Warning)
                return
            layers = [active]

        jobs = []           # (name, path, temporary, style)
        refused = []
        for layer in layers:
            try:
                # Not `layer.source()`: a filtered layer's file holds MORE than the layer does, and
                # a memory or PostGIS layer has no file at all. `prepare` writes those out first.
                path, temporary = export.prepare(
                    layer, on_status=lambda t: self._say(t, bar=False))
            except export.NotUploadable as exc:
                refused.append("{0}: {1}".format(layer.name(), exc))
                continue
            style = symbology.from_qgis(layer) if self.push_style.isChecked() else {}
            jobs.append((layer.name(), path, temporary, style))

        if not jobs:
            # Everything was refused — say why, for each, rather than a generic failure.
            self._say(" | ".join(refused) or "Nothing could be uploaded.", Qgis.Warning)
            return

        client = self.instance.client
        total = len(jobs)

        def work():
            uploaded, failed = [], list(refused)
            for index, (name, path, temporary, style) in enumerate(jobs, start=1):
                # Reported from the worker thread: a queue that looks frozen for four of five files
                # is worse than no progress at all.
                self._progress.emit("Uploading {0} ({1} of {2})…".format(name, index, total))
                try:
                    result = client.uploads.upload(path, wait=True)
                    if style and getattr(result, "layer_id", None):
                        # Styling travels with the upload: the portal then shows what the author
                        # saw, instead of the next default colour in the palette.
                        api = client.layers.api(result.plan.layer_type)
                        api.set_default_style(result.layer_id,
                                              {"opacity": 1.0, "style": style,
                                               "popup_fields": []})
                    uploaded.append(name)
                except Exception as exc:            # noqa: BLE001 - one bad layer, not the batch
                    # Four good layers must still arrive when the third one is broken.
                    failed.append("{0}: {1}".format(name, exc))
                finally:
                    if temporary:
                        # A multi-gigabyte export is not left behind in temp because upload failed.
                        shutil.rmtree(os.path.dirname(path), ignore_errors=True)
            return {"uploaded": uploaded, "failed": failed}

        self._busy(True)
        self._say("Uploading {0} layer(s)… large files go straight to storage.".format(total),
                  bar=False)
        self._run(_Job("GeoDeploy: uploading", work), self._uploaded)

    def _uploaded(self, job):
        self._busy(False)
        if job.error:
            self._say(job.error, Qgis.Critical)
            return
        result = job.result or {}
        uploaded = result.get("uploaded") or []
        failed = result.get("failed") or []
        styling = " Styling sent with them." if (uploaded and self.push_style.isChecked()) else ""

        if uploaded and not failed:
            what = uploaded[0] if len(uploaded) == 1 else f"{len(uploaded)} layers"
            self._say(f"Uploaded {what}.{styling}")
        elif uploaded and failed:
            # Partial success is its own outcome. Reporting it as failure hides work that landed;
            # reporting it as success hides work that did not.
            self._say(f"Uploaded {len(uploaded)}, but {len(failed)} did not: " + " | ".join(failed),
                      Qgis.Warning)
        else:
            self._say(" | ".join(failed) or "Nothing was uploaded.", Qgis.Critical)
        if uploaded:
            self.refresh_layers()

    # -- task plumbing ------------------------------------------------------------------------------

    def _run(self, job, on_done):
        job.taskCompleted.connect(lambda: on_done(job))
        job.taskTerminated.connect(lambda: on_done(job))
        # Keep a reference: QgsTaskManager takes ownership, but Python must not collect the closure.
        self._job = job
        QgsApplication.taskManager().addTask(job)


class GeoDeployPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.dock = None
        self.action = None

    def initGui(self):              # noqa: N802 - the name QGIS calls
        self.action = QAction(PLUGIN_NAME, self.iface.mainWindow())
        self.action.triggered.connect(self.show)
        self.iface.addPluginToWebMenu(PLUGIN_NAME, self.action)
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        if self.dock is not None:
            self.iface.removeDockWidget(self.dock)
            self.dock = None
        if self.action is not None:
            self.iface.removePluginWebMenu(PLUGIN_NAME, self.action)
            self.iface.removeToolBarIcon(self.action)
            self.action = None

    def show(self):
        if self.dock is None:
            self.dock = GeoDeployDock(self.iface)
            self.iface.addDockWidget(Qt.RightDockWidgetArea, self.dock)
        self.dock.show()
        self.dock.raise_()
