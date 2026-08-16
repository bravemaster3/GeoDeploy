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

from . import diffdialog, export, portals as portal_sync, sources, symbology
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
        self._portals: list[dict] = []

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

        self.attributes = QCheckBox("Prefer the real data over the styled view")
        self.attributes.setToolTip(
            "Vector — off: one pre-generalized archive, downloaded once, carrying only the "
            "attributes the tiles hold. On: OGC API - Features, re-queried for the extent on "
            "screen, with exact geometry and every attribute.\n"
            "Raster — off: server-rendered tiles, coloured exactly as GeoDeploy draws them. "
            "On: the GeoTIFF itself, with real pixel values, drawn using QGIS's own defaults.")
        outer.addWidget(self.attributes)

        self.add_btn = QPushButton("Add to map")
        self.add_btn.clicked.connect(self.add_selected)
        outer.addWidget(self.add_btn)
        self.tree.itemSelectionChanged.connect(self._on_selection_changed)

        # -- portals ----------------------------------------------------------------------------
        self.open_group_btn = QPushButton("Open portal as a group")
        self.open_group_btn.setToolTip(
            "Every layer of the selected portal, in its order and with the portal's own styling, "
            "as one QGIS group. Restyle it and push it back.")
        self.open_group_btn.clicked.connect(self.open_portal_as_group)
        outer.addWidget(self.open_group_btn)

        self.push_group_btn = QPushButton("Push group to portal")
        self.push_group_btn.setToolTip(
            "Turn the selected QGIS group into a portal. A group opened from a portal UPDATES that "
            "portal; any other group creates a new one.")
        self.push_group_btn.clicked.connect(self.push_group)
        outer.addWidget(self.push_group_btn)

        # -- upload -----------------------------------------------------------------------------
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

    def _install_auth(self):
        """Attach the token to QGIS's OWN requests for this instance's host.

        A layer added as OGC API - Features is fetched by QGIS, not by us — so our token never
        reached it, and a private layer could not be opened however valid the credential was.
        QGIS's provider URI has no documented way to carry a header, but the network manager
        accepts a request PREPROCESSOR, which is the supported way for a plugin to add one.

        Scoped to the exact host of the connected instance. A preprocessor sees every request QGIS
        makes — basemaps, other servers, plugin updates — and a bearer token must never leave the
        host it belongs to.
        """
        if not self.instance or not self.instance.token:
            return
        try:
            from qgis.core import QgsNetworkAccessManager
            from qgis.PyQt.QtCore import QUrl
        except ImportError:             # pragma: no cover - QGIS always has these
            return
        if not hasattr(QgsNetworkAccessManager, "setRequestPreprocessor"):
            # Older QGIS: private layers over OAPIF will not open, but everything else works.
            self._say("This QGIS is too old to attach a token to its own requests, so private "
                      "layers cannot be added as OGC API - Features. Public layers are fine.",
                      Qgis.Warning)
            return
        host = (QUrl(self.instance.url).host() or "").lower()
        token = self.instance.token
        if not host:
            return

        def add_token(request):
            try:
                if (request.url().host() or "").lower() == host:
                    request.setRawHeader(b"Authorization",
                                         ("Bearer " + token).encode("utf-8"))
            except Exception:           # noqa: BLE001 - never break QGIS networking
                pass

        try:
            QgsNetworkAccessManager.setRequestPreprocessor(add_token)
        except Exception as exc:        # noqa: BLE001
            symbology._log("Could not install the auth preprocessor: {0}".format(exc))

    def _colormaps(self):
        """The colormap names this instance actually has, fetched once.

        A QGIS ramp name is only sent as a colormap when the server confirms it knows one by that
        name — the two catalogues overlap but are not the same, and a wrong colormap is worse than
        the default. Cached because it cannot change during a session, and returned empty on
        failure so a styling nicety never costs an upload.
        """
        if getattr(self, "_colormap_cache", None) is None:
            try:
                self._colormap_cache = set(self.instance.client.raster.colormaps() or [])
            except Exception:           # noqa: BLE001 - no colormap is not a failed upload
                self._colormap_cache = set()
        return self._colormap_cache

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
        # Before any layer is added: an OAPIF layer is fetched by QGIS itself, so the token has to
        # be on QGIS's requests, not only on ours.
        self._install_auth()
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

        def work():
            layers = self.instance.layers()
            try:
                portals = self.instance.portals()
            except GeoDeployError:
                # A portal listing that fails must not cost you the layer listing.
                portals = []
            return {"layers": layers, "portals": portals}

        self._run(_Job("GeoDeploy: listing", work), self._listed)

    def _listed(self, job):
        self._busy(False)
        if job.error:
            self._say(job.error, Qgis.Critical)
            return
        result = job.result or {}
        self._rows = result.get("layers") or []
        self._portals = result.get("portals") or []
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
        # Portals last: they are things to LOOK at rather than add, so they sit below the data.
        if self._portals:
            parent = QTreeWidgetItem(self.tree, ["Portals"])
            parent.setExpanded(True)
            for portal in self._portals:
                slug = portal.get("slug") or ""
                published = portal.get("is_published", portal.get("published"))
                item = QTreeWidgetItem(parent, [
                    portal.get("title") or portal.get("name") or slug,
                    portal.get("experience") or portal.get("archetype") or "portal",
                    "published" if published else "draft"])
                # Marked so a double-click opens it instead of trying to add it as a layer.
                item.setData(0, Qt.UserRole, {"_portal": True, "slug": slug,
                                              "_base": self.instance.url if self.instance else ""})

        if not self._rows and not self._portals:
            self._say("Nothing to list. Public data needs no account; a token shows the rest.")

    # -- add ---------------------------------------------------------------------------------------

    def _selected_row(self) -> dict | None:
        items = self.tree.selectedItems()
        return items[0].data(0, Qt.UserRole) if items else None

    def _on_selection_changed(self):
        """A portal cannot be added to the map, so the button says what it WILL do instead."""
        row = self._selected_row() or {}
        self.add_btn.setText("Open portal in browser" if row.get("_portal") else "Add to map")

    def add_selected(self):
        row = self._selected_row()
        if not row:
            self._say("Pick a layer first.", Qgis.Warning)
            return
        if row.get("_portal"):
            self._open_portal(row)
            return
        source = sources.describe(row, prefer_attributes=self.attributes.isChecked())
        if not source:
            self._say("That layer offers nothing QGIS can read yet.", Qgis.Warning)
            return

        name = row.get("name") or "GeoDeploy layer"
        layer = self._build_layer(row, source, name)
        if layer is None:
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

    def _build_layer(self, row, source, name):
        """One layer from a described source, tagged with its GeoDeploy identity.

        The tag is what makes the portal round trip safe: a group pushed back has to know WHICH
        layer each entry is, and matching by name would break the first time someone renames one.
        """
        if source["kind"] in ("cog", "xyz"):
            layer = QgsRasterLayer(source["uri"], name, source["provider"])
        else:
            layer = QgsVectorLayer(source["uri"], name, source["provider"])
        if not layer.isValid():
            return None
        if self.instance:
            is_raster = (row.get("layer_type") == "raster"
                         or row.get("storage_backend") == "raster")
            portal_sync.tag_layer(layer, self.instance.url, row.get("id"),
                                  "raster" if is_raster else "vector")
        return layer

    def _row_for(self, layer_id, layer_type):
        """The listing row matching a portal layer_config entry."""
        for row in self._rows:
            kind = "raster" if (row.get("layer_type") == "raster"
                                or row.get("storage_backend") == "raster") else "vector"
            if str(row.get("id")) == str(layer_id) and kind == layer_type:
                return row
        return None

    def open_portal_as_group(self):
        """Every layer of a portal, in its order, styled as the portal styles it, in one group.

        A portal IS an ordered styled list of layers, which is what a group is — so this is a
        direct mapping rather than an interpretation. Styles come from the PORTAL's own
        layer_config, not from the layer's default: a portal may deliberately draw a layer
        differently from how it is stored, and that difference is the thing worth carrying across.
        """
        row = self._selected_row()
        if not row or not row.get("_portal"):
            self._say("Select a portal in the list first.", Qgis.Warning)
            return
        if not self.instance:
            return
        ref = row.get("id") or row.get("slug")

        def work():
            return self.instance.client.portals.get(ref)

        self._busy(True)
        self._say("Opening " + str(row.get("title") or "the portal") + "...", bar=False)
        self._run(_Job("GeoDeploy: opening portal", work), self._portal_opened)

    def _portal_opened(self, job):
        self._busy(False)
        if job.error:
            self._say(job.error, Qgis.Critical)
            return
        doc = job.result or {}
        configs = doc.get("layer_configs") or []
        if not configs:
            self._say("That portal has no layers yet.", Qgis.Warning)
            return

        project = QgsProject.instance()
        group = project.layerTreeRoot().insertGroup(0, doc.get("title") or "GeoDeploy portal")
        group.setCustomProperty(portal_sync.P_PORTAL_ID, str(doc.get("id")))
        group.setCustomProperty(portal_sync.P_PORTAL_TITLE, doc.get("title") or "")

        added, missing = 0, []
        # In order: layer_configs[0] is the TOP of the portal's list, and adding to the group in
        # the same order puts it at the top here too. No reversal anywhere.
        for cfg in configs:
            layer_row = self._row_for(cfg.get("layer_id"), cfg.get("layer_type"))
            if layer_row is None:
                missing.append(str(cfg.get("layer_id")))
                continue
            source = sources.describe(layer_row, prefer_attributes=self.attributes.isChecked())
            if not source:
                missing.append(str(layer_row.get("name") or cfg.get("layer_id")))
                continue
            layer = self._build_layer(layer_row, source, layer_row.get("name") or "layer")
            if layer is None:
                missing.append(str(layer_row.get("name") or cfg.get("layer_id")))
                continue
            project.addMapLayer(layer, False)      # False: placed into the group, not the root
            node = group.addLayer(layer)
            node.setItemVisibilityChecked(bool(cfg.get("visible", True)))
            style = (cfg.get("style") or {}) if self.styled.isChecked() else {}
            if style and cfg.get("layer_type") != "raster":
                symbology.apply_to_qgis(layer, style)
            added += 1

        note = ""
        if missing:
            note = " " + str(len(missing)) + " could not be opened (" + ", ".join(missing[:3]) + ")."
        self._say("Opened " + str(doc.get("title")) + " as a group - " + str(added) +
                  " layer(s)." + note + " Restyle it, then use Push group to portal.",
                  Qgis.Warning if missing else Qgis.Info)

    def push_group(self):
        """Push the selected QGIS group back as a portal — after showing exactly what will change.

        Republishing has several consequences that are not equally reversible: removing a layer
        from a portal is one click to undo, uploading a 2 GB file is not, and publishing a layer
        you were only inspecting is not either. So nothing happens until the plan has been read and
        approved, and the two consequential parts are separate opt-ins rather than one blanket OK.
        """
        if not self.instance or not self.instance.token:
            self._say("Pushing a portal needs a token with write access.", Qgis.Warning)
            return
        view = self.iface.layerTreeView()
        nodes = view.selectedNodes() if view else []
        groups = [n for n in nodes if hasattr(n, "addLayer")]
        if len(groups) != 1:
            self._say("Select exactly one GROUP in the Layers panel - that group becomes the "
                      "portal.", Qgis.Warning)
            return
        group = groups[0]
        portal_id, title = portal_sync.group_portal(group)
        client = self.instance.client

        def style_for(qgis_layer, layer_type):
            if not self.push_style.isChecked():
                return {}
            if layer_type == "raster":
                return symbology.raster_from_qgis(qgis_layer, self._colormaps())
            return symbology.from_qgis(qgis_layer)

        # What the portal looks like NOW, so the plan is a real comparison rather than a guess.
        current = []
        if portal_id is not None:
            try:
                current = (client.portals.get(portal_id) or {}).get("layer_configs") or []
            except GeoDeployError as exc:
                self._say(f"Could not read the portal to compare against: {exc}", Qgis.Critical)
                return

        try:
            plan = portal_sync.plan_push(group, style_for, current)
        except Exception as exc:            # noqa: BLE001 - never crash QGIS over a layer tree
            self._say(f"Could not read that group: {exc}", Qgis.Critical)
            return

        go, upload_new, drop_removed = diffdialog.confirm(
            self, title or "Untitled portal",
            {"unchanged": plan["unchanged"], "restyled": plan["restyled"], "added": plan["added"],
             "uploads": [name for name, _layer, _node in plan["uploads"]],
             "removed": plan["removed"], "rename": plan.get("rename")},
            creating=portal_id is None)
        if not go:
            self._say("Nothing was pushed.", bar=False)
            return

        uploads = list(plan["uploads"]) if upload_new else []
        keep_removed = [] if drop_removed else [
            cfg for cfg in current
            if (int(cfg.get("layer_id")), str(cfg.get("layer_type")))
            not in {(c["layer_id"], c["layer_type"]) for c in plan["configs"]}]
        instance_url = self.instance.url

        def work():
            sent = []
            for name, qgis_layer, _node in uploads:
                # Upload, then TAG the QGIS layer with the id it was given. The tag is what makes
                # the next push see it as an existing layer rather than a new one all over again.
                path, temporary = export.prepare(qgis_layer)
                try:
                    result = client.uploads.upload(path, wait=True)
                finally:
                    if temporary:
                        shutil.rmtree(os.path.dirname(path), ignore_errors=True)
                if not getattr(result, "layer_id", None):
                    continue
                kind = result.plan.layer_type
                portal_sync.tag_layer(qgis_layer, instance_url, result.layer_id, kind)
                style = style_for(qgis_layer, kind)
                if style:
                    body = (dict(style, opacity=1.0) if kind == "raster"
                            else {"opacity": 1.0, "style": style, "popup_fields": []})
                    client.layers.api(kind).set_default_style(result.layer_id, body)
                sent.append(name)

            # Re-plan AFTER the uploads: the new layers are tagged now, so this puts them in their
            # place in the group's order rather than making the caller reconstruct it.
            final = portal_sync.plan_push(group, style_for, current)
            configs = final["configs"] + keep_removed
            # The rename was listed in the dialog and approved with everything else.
            new_title = (plan.get("rename") or (None, None))[1]
            doc = portal_sync.push(client, group, configs, new_title=new_title)
            return {"portal": doc, "uploaded": sent, "kept": len(keep_removed),
                    "skipped": [n for n, _l, _x in final["uploads"]]}

        self._busy(True)
        verb = "Updating" if portal_id else "Creating"
        self._say(verb + " portal " + str(title) + "...", bar=False)
        self._run(_Job("GeoDeploy: pushing portal", work), self._group_pushed)

    def _group_pushed(self, job):
        self._busy(False)
        if job.error:
            self._say(job.error, Qgis.Critical)
            return
        result = job.result or {}
        doc = result.get("portal") or {}
        bits = []
        if result.get("uploaded"):
            bits.append("uploaded " + str(len(result["uploaded"])))
        if result.get("kept"):
            bits.append("kept " + str(result["kept"]) + " you chose not to remove")
        if result.get("skipped"):
            bits.append("left out " + ", ".join(result["skipped"][:3]))
        detail = (" (" + "; ".join(bits) + ")") if bits else ""
        base = self.instance.url.rstrip("/") if self.instance else ""
        self._say("Portal " + str(doc.get("title")) + " published: " +
                  base + "/p/" + str(doc.get("slug")) + detail)
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
