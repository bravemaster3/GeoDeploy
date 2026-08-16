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

from . import (diffdialog, export, portals as portal_sync, sources, symbology,
               uploadpicker)
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


def _xyz_uri(tile_url: str) -> str | None:
    """An XYZ provider URI built by QGIS itself.

    `QgsDataSourceUri` owns the escaping rules, and ours has to survive a template whose own query
    string contains `?`, `&` and `://`. Letting QGIS encode it removes the only untested step
    between a tile endpoint that provably returns PNGs and a layer that draws nothing.
    """
    try:
        from qgis.core import QgsDataSourceUri
        uri = QgsDataSourceUri()
        uri.setParam("type", "xyz")
        uri.setParam("url", tile_url)
        uri.setParam("zmin", "0")
        uri.setParam("zmax", "22")
        return bytes(uri.encodedUri()).decode("utf-8")
    except Exception:                   # noqa: BLE001 - fall back to the hand-built string
        return None


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

    def _capture_canvas(self):
        """The map canvas as a WebP file, or None. Used as the portal's card image.

        WebP because that is what the endpoint stores and serves it as — handing it PNG bytes under
        a .webp name works by content sniffing, which is not something to rely on. Falls back to
        PNG only if this Qt has no WebP writer.
        """
        try:
            import tempfile
            canvas = self.iface.mapCanvas()
            if canvas is None:
                return None
            image = canvas.grab().toImage()
            # A card, not a poster: the dashboard shows these small, and a 4K screenshot would be
            # megabytes of nothing.
            try:
                from qgis.PyQt.QtCore import Qt as _Qt
                image = image.scaledToWidth(1200, _Qt.SmoothTransformation)
            except Exception:           # noqa: BLE001 - full size is still usable
                pass
            for suffix, fmt in ((".webp", "WEBP"), (".png", "PNG")):
                path = os.path.join(tempfile.mkdtemp(prefix="geodeploy-thumb-"), "thumb" + suffix)
                if image.save(path, fmt, 82 if fmt == "WEBP" else -1):
                    return path
            return None
        except Exception as exc:        # noqa: BLE001 - a picture is never worth a failed publish
            symbology._log("Could not capture the map for the portal thumbnail: {0}".format(exc))
            return None

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
                    # `legend` is defined on the VECTOR and RASTER namespaces, not on the
                    # kind-agnostic `layers` helper — asking the latter is an AttributeError, which
                    # is what every unstyled layer was really hitting.
                    kind = "raster" if (row.get("layer_type") == "raster"
                                        or row.get("storage_backend") == "raster") else "vector"
                    legend = self.instance.client.layers.api(kind).legend(ref)
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
            uri = source["uri"]
            if source["kind"] == "xyz" and source.get("tile_url"):
                uri = _xyz_uri(source["tile_url"]) or uri
            layer = QgsRasterLayer(uri, name, source["provider"])
        else:
            layer = QgsVectorLayer(source["uri"], name, source["provider"])
        if not layer.isValid():
            return None
        if source["kind"] == "xyz":
            # AN XYZ LAYER HAS NO EXTENT OF ITS OWN. It is a global tile pyramid, so QGIS reports
            # the whole world and "Zoom to layer" flies to everything — which is what made zooming
            # to a raster land on some other layer entirely. The instance knows the real bounds, so
            # tell QGIS them.
            self._set_raster_extent(layer, row.get("bbox"))
        if self.instance:
            is_raster = (row.get("layer_type") == "raster"
                         or row.get("storage_backend") == "raster")
            portal_sync.tag_layer(layer, self.instance.url, row.get("id"),
                                  "raster" if is_raster else "vector")
        return layer

    @staticmethod
    def _set_raster_extent(layer, bbox):
        """Give a tile layer the layer's own bounds, in the map's CRS."""
        if not (isinstance(bbox, (list, tuple)) and len(bbox) == 4):
            return
        try:
            from qgis.core import (QgsCoordinateReferenceSystem, QgsCoordinateTransform,
                                   QgsProject, QgsRectangle)
            rect = QgsRectangle(float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
            # The bbox is EPSG:4326 app-wide; an XYZ layer is Web Mercator.
            src = QgsCoordinateReferenceSystem("EPSG:4326")
            if layer.crs().isValid() and layer.crs() != src:
                rect = QgsCoordinateTransform(src, layer.crs(),
                                              QgsProject.instance()).transformBoundingBox(rect)
            layer.setExtent(rect)
        except Exception as exc:        # noqa: BLE001 - a wrong zoom is not worth a failed add
            symbology._log("Could not set the raster's extent: {0}".format(exc))

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
        slug = row.get("slug")
        instance = self.instance

        def work():
            # With a token, ask the API — it is authoritative and covers unpublished portals.
            if instance.token and ref is not None:
                try:
                    return instance.client.portals.get(ref)
                except GeoDeployError:
                    pass                # fall through: a published portal is readable anyway
            # Without one, read what the portal PUBLISHES. Looking at a public portal should never
            # require an account; only changing it should.
            if not slug:
                raise GeoDeployError("This portal has no published address to read.")
            doc = instance.published_style(slug)
            return {"id": row.get("id"), "slug": slug,
                    "title": row.get("title") or row.get("name") or slug,
                    "layer_configs": portal_sync.configs_from_published_style(
                        doc, symbology.style_from_legend),
                    "_anonymous": True}

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
        # Only tag the portal id when we could actually write back to it. Tagging a read-only copy
        # would offer an "update" that is going to be refused, which is worse than not offering it.
        if doc.get("id") is not None and self.instance and self.instance.token:
            group.setCustomProperty(portal_sync.P_PORTAL_ID, str(doc.get("id")))
        group.setCustomProperty(portal_sync.P_PORTAL_TITLE, doc.get("title") or "")

        added, missing = 0, []
        by_key = {(int(c.get("layer_id")), str(c.get("layer_type"))): c for c in configs
                  if c.get("layer_id") is not None}

        def place(node_list, parent):
            """The portal's folder tree, as QGIS sub-groups. Folders are a real structure the
            author built, so they come across as folders rather than being flattened."""
            nonlocal added
            for item in node_list:
                if item.get("children") is not None:
                    sub = parent.addGroup(item.get("name") or "Folder")
                    sub.setCustomProperty(portal_sync.P_FOLDER_ID, str(item.get("id") or ""))
                    sub.setExpanded(not item.get("collapsed"))
                    place(item.get("children") or [], sub)
                    continue
                key = (int(item.get("layer_id")), str(item.get("layer_type") or "vector"))
                cfg = by_key.get(key)
                if cfg is None:
                    continue
                layer_row = self._row_for(cfg.get("layer_id"), cfg.get("layer_type"))
                if layer_row is None:
                    missing.append(str(cfg.get("name") or cfg.get("layer_id")))
                    continue
                source = sources.describe(layer_row,
                                          prefer_attributes=self.attributes.isChecked())
                if not source:
                    missing.append(str(layer_row.get("name") or cfg.get("layer_id")))
                    continue
                layer = self._build_layer(layer_row, source, layer_row.get("name") or "layer")
                if layer is None:
                    missing.append(str(layer_row.get("name") or cfg.get("layer_id")))
                    continue
                project.addMapLayer(layer, False)   # False: placed into the group, not the root
                tree_node = parent.addLayer(layer)
                tree_node.setItemVisibilityChecked(bool(cfg.get("visible", True)))
                style = (cfg.get("style") or {}) if self.styled.isChecked() else {}
                if style and cfg.get("layer_type") != "raster":
                    symbology.apply_to_qgis(layer, style)
                added += 1

        # A portal with no folders is a flat list — the configs themselves, in order.
        # layer_configs[0] is the TOP, and adding in the same order puts it at the top here too.
        tree = doc.get("layer_groups") or [
            {"layer_id": c.get("layer_id"), "layer_type": c.get("layer_type")} for c in configs]
        place(tree, group)

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
        # Grabbed here, on the GUI thread, because a canvas cannot be touched from a worker — and
        # because right now it shows exactly what is being published.
        thumbnail = self._capture_canvas()

        def work():
            sent = []
            for index, (name, qgis_layer, _node) in enumerate(uploads, start=1):
                # Reported from the worker thread: publishing a group of five files is a long
                # operation, and silence through it reads as a hang.
                self._progress.emit("Uploading {0} ({1} of {2})…".format(name, index, len(uploads)))
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
            self._progress.emit("Publishing the portal…")
            final = portal_sync.plan_push(group, style_for, current)
            configs = final["configs"] + keep_removed
            # The rename was listed in the dialog and approved with everything else.
            new_title = (plan.get("rename") or (None, None))[1]
            doc = portal_sync.push(client, group, configs, new_title=new_title,
                                   tree=final.get("tree"))
            # The card image. The dashboard captures its own map at publish time, so a portal
            # published from anywhere else had a blank card — which makes it look unfinished in the
            # one place people browse portals. Never fatal: a missing picture is not a failed
            # publish.
            if thumbnail and doc.get("id"):
                try:
                    client.portals.upload_thumbnail(doc["id"], thumbnail)
                except Exception as exc:        # noqa: BLE001
                    symbology._log("Portal published; its thumbnail did not upload: {0}".format(exc))
                finally:
                    try:
                        os.unlink(thumbnail)
                    except OSError:
                        pass
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

    def _open_portal(self, row):
        """A portal is a published web map — QGIS cannot render one, so open it where it lives."""
        base = (row.get("_base") or "").rstrip("/")
        slug = row.get("slug") or ""
        if not base or not slug:
            self._say("That portal has no address yet — publish it first.", Qgis.Warning)
            return
        url = f"{base}/p/{slug}"
        try:
            from qgis.PyQt.QtCore import QUrl
            from qgis.PyQt.QtGui import QDesktopServices
            QDesktopServices.openUrl(QUrl(url))
            self._say(f"Opened {url} in your browser.")
        except Exception as exc:        # noqa: BLE001 - still give them the address
            self._say(f"Open it at {url} ({exc}).", Qgis.Warning)

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

        # ASK FIRST. Which layers go is the user's call, and a layer that cannot be sent should be
        # visible with its reason rather than turning into an error after the fact.
        candidates = []
        for layer in layers:
            try:
                export.check(layer)
                candidates.append((layer.name(), None))
            except export.NotUploadable as exc:
                candidates.append((layer.name(), str(exc)))
        chosen = uploadpicker.choose(self, candidates)
        if chosen is None:
            self._say("Nothing was uploaded.", bar=False)
            return
        if not chosen:
            self._say("No layers were selected to upload.", Qgis.Warning)
            return
        layers = [l for l in layers if l.name() in set(chosen)]

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
            # A raster's default style is a DIFFERENT shape — {colormap, rescale, bidx}, not
            # classes — so it needs its own translation. Sending the vector shape is what made
            # "Send its styling too" quietly do nothing for a GeoTIFF.
            if not self.push_style.isChecked():
                style = {}
            elif isinstance(layer, QgsRasterLayer):
                style = symbology.raster_from_qgis(layer, self._colormaps())
            else:
                style = symbology.from_qgis(layer)
            jobs.append((layer.name(), path, temporary, style))

        if not jobs:
            # Everything was refused — say why, for each, rather than a generic failure.
            self._say(" | ".join(refused) or "Nothing could be uploaded.", Qgis.Warning)
            return

        client = self.instance.client
        total = len(jobs)

        def work():
            uploaded, styled, failed = [], [], list(refused)
            for index, (name, path, temporary, style) in enumerate(jobs, start=1):
                # Reported from the worker thread: a queue that looks frozen for four of five files
                # is worse than no progress at all.
                self._progress.emit("Uploading {0} ({1} of {2})…".format(name, index, total))
                try:
                    result = client.uploads.upload(path, wait=True)
                    if style and getattr(result, "layer_id", None):
                        # Styling travels with the upload: the portal then shows what the author
                        # saw, instead of the next default colour in the palette.
                        kind = result.plan.layer_type
                        api = client.layers.api(kind)
                        # The two kinds take different bodies. A raster's IS the style; a vector's
                        # wraps it, alongside opacity and popup fields.
                        body = (dict(style, opacity=1.0) if kind == "raster"
                                else {"opacity": 1.0, "style": style, "popup_fields": []})
                        api.set_default_style(result.layer_id, body)
                        styled.append(name)
                    uploaded.append(name)
                except Exception as exc:            # noqa: BLE001 - one bad layer, not the batch
                    # Four good layers must still arrive when the third one is broken.
                    failed.append("{0}: {1}".format(name, exc))
                finally:
                    if temporary:
                        # A multi-gigabyte export is not left behind in temp because upload failed.
                        shutil.rmtree(os.path.dirname(path), ignore_errors=True)
            return {"uploaded": uploaded, "styled": styled, "failed": failed}

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
        styled = result.get("styled") or []
        # Only claim what was actually sent. A renderer we cannot translate produces no style, and
        # saying "styling sent" anyway is how a silent no-op passes for a feature.
        if styled and len(styled) == len(uploaded):
            styling = " Styling sent with " + ("it." if len(styled) == 1 else "them.")
        elif styled:
            styling = f" Styling sent for {len(styled)} of {len(uploaded)}."
        elif uploaded and self.push_style.isChecked():
            styling = " No styling was sent — this renderer has no GeoDeploy equivalent."
        else:
            styling = ""

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
