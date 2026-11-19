"""EVERY BUTTON OF THE DOCK, pressed against a REAL instance, in the order a person uses them.

WHY THIS EXISTS. `e2e_live.py` proves what survives an ingest; the stub harnesses prove each piece
of logic in isolation. Neither presses the dock's buttons, and the dock is where the pieces are put
together — the one place a bug can live that no piece shows. Plugin 0.7.4–0.7.6 shipped exactly
that: `plan_push` was right, the tag check was right, the upload was right, and "Push group to
portal" on a group of NEW layers uploaded every one of them and then failed with "This group has
no GeoDeploy layers in it", because the re-plan used a listing taken before the uploads. Each part
was tested; the sequence was not.

So this builds the real `GeoDeployDock`, runs its background jobs synchronously, answers its two
dialogs (the push plan and the upload picker) the way a person accepting the defaults would, and
presses:

    Connect · Refresh · Push group (new layers) · Push group again (nothing to do) ·
    Push group after the portal was deleted (#128) · Upload selected · Push group with one more
    layer · Save styling · Add to map · Open portal as group · Restyle · Open in browser

After each press it checks the RESULT on the instance through the API — not the dock's message,
which is what said "done" while nothing had happened — and that no press raised a critical message.

Run it (it creates layers and portals named `E2E-dock-<run>` and deletes them at the end; set
GEODEPLOY_KEEP=1 to leave them for inspection):

    docker run --rm -e GEODEPLOY_URL -e GEODEPLOY_TOKEN -e QT_QPA_PLATFORM=offscreen \\
        -v <repo>/integrations/qgis-plugin:/src -w /src qgis/qgis:ltr python3 -u scripts/e2e_dock.py

Not part of CI: it needs an instance and a write token. Run it before handing over a plugin zip.
"""
import os
import sys
import tempfile
import traceback
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PLUGIN, "geodeploy_qgis", "vendor"))
sys.path.insert(0, PLUGIN)

from qgis.core import (QgsApplication, QgsCategorizedSymbolRenderer, QgsFeature,  # noqa: E402
                       QgsField, QgsGeometry, QgsPalLayerSettings, QgsPointXY, QgsProject,
                       QgsRendererCategory, QgsSymbol, QgsVectorFileWriter, QgsVectorLayer,
                       QgsVectorLayerSimpleLabeling)

APP = QgsApplication([], True)
APP.initQgis()

from qgis.PyQt.QtCore import QVariant                                            # noqa: E402
from qgis.PyQt.QtGui import QColor                                               # noqa: E402

from geodeploy_qgis import diffdialog, plugin, portals, uploadpicker            # noqa: E402

URL = os.environ.get("GEODEPLOY_URL", "").rstrip("/")
TOKEN = os.environ.get("GEODEPLOY_TOKEN", "")
KEEP = os.environ.get("GEODEPLOY_KEEP") == "1"
RUN = uuid.uuid4().hex[:6]
PREFIX = "E2E-dock-{0}".format(RUN)

FAILURES = []
CHECKS = [0]


def section(title):
    print("\n== {0} ==".format(title))


def check(name, condition, detail=""):
    CHECKS[0] += 1
    print("  {0}  {1}".format("ok  " if condition else "FAIL", name))
    if not condition:
        FAILURES.append(name)
        if detail != "":
            print("        -> {0}".format(str(detail)[:600]))


# ── the iface a dock talks to ───────────────────────────────────────────────────────────────

class View(object):
    def __init__(self):
        self.nodes, self.layers = [], []

    def selectedNodes(self):
        return list(self.nodes)

    def selectedLayers(self):
        return list(self.layers)

    def currentLayer(self):
        return self.layers[0] if self.layers else None


class Anything(object):
    """A message bar, main window or canvas that accepts whatever the dock asks of it."""

    def __getattr__(self, name):
        return lambda *a, **k: None


class Iface(object):
    def __init__(self):
        self.view = View()
        self.active = None

    def layerTreeView(self):
        return self.view

    def activeLayer(self):
        return self.active

    def messageBar(self):
        return Anything()

    def mainWindow(self):
        return None

    def mapCanvas(self):
        return None

    def __getattr__(self, name):
        return lambda *a, **k: None


# ── data ────────────────────────────────────────────────────────────────────────────────────

def make_points(name, count=6, kinds=("museum", "castle")):
    """A small point layer in Dresden, categorized by kind, labelled only when zoomed in."""
    mem = QgsVectorLayer("Point?crs=EPSG:4326", name, "memory")
    pr = mem.dataProvider()
    pr.addAttributes([QgsField("name", QVariant.String), QgsField("kind", QVariant.String)])
    mem.updateFields()
    feats = []
    for i in range(count):
        f = QgsFeature(mem.fields())
        f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(13.70 + i * 0.01, 51.04 + (i % 3) * 0.01)))
        f.setAttributes(["Place {0}".format(i), kinds[i % len(kinds)]])
        feats.append(f)
    pr.addFeatures(feats)
    path = os.path.join(tempfile.mkdtemp(prefix="e2edock"), name + ".gpkg")
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    options.layerName = name
    err = QgsVectorFileWriter.writeAsVectorFormatV3(mem, path, QgsProject.instance().transformContext(), options)
    assert err[0] == QgsVectorFileWriter.NoError, err
    layer = QgsVectorLayer(path, name, "ogr")
    assert layer.isValid(), path
    cats = []
    for kind, colour in zip(kinds, ("#b15a4a", "#4a6fb1", "#3a9a5a")):
        sym = QgsSymbol.defaultSymbol(layer.geometryType())
        sym.setColor(QColor(colour))
        cats.append(QgsRendererCategory(kind, sym, kind))
    layer.setRenderer(QgsCategorizedSymbolRenderer("kind", cats))
    settings = QgsPalLayerSettings()
    settings.fieldName = 'CASE WHEN @map_scale < 30000 THEN "name" END'
    settings.isExpression = True
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)
    return layer


# ── the dock, run synchronously ─────────────────────────────────────────────────────────────

MESSAGES = []


def build_dock(iface):
    dock = plugin.GeoDeployDock(iface)
    real_say = dock._say

    def say(text, level=None, bar=True, *a, **k):
        MESSAGES.append((level, str(text)))
        try:
            return real_say(text, level, bar, *a, **k) if level is not None else real_say(text, bar=bar)
        except Exception:               # noqa: BLE001 - the message bar is a stub
            return None
    dock._say = say

    def run_now(job, on_done):
        job.run()
        on_done(job)
    dock._run = run_now
    return dock


def press(label, fn):
    """Press a button; report any critical message or exception it produced."""
    start = len(MESSAGES)
    try:
        fn()
        raised = None
    except Exception:                   # noqa: BLE001
        raised = traceback.format_exc()
    said = MESSAGES[start:]
    critical = [t for lvl, t in said if lvl == plugin.MSG_CRITICAL]
    for lvl, t in said:
        print("        [{0}] {1}".format("CRITICAL" if lvl == plugin.MSG_CRITICAL else
                                         "warning" if lvl == plugin.MSG_WARNING else "info", t[:220]))
    check("{0}: no error".format(label), raised is None and not critical, raised or critical)
    return said


def select_row(dock, predicate):
    """Select the catalog row the predicate picks, as a click would. True if found."""
    from qgis.PyQt.QtCore import Qt
    from geodeploy_qgis.compat import enum
    role = enum(Qt, "ItemDataRole", "UserRole")

    def walk(item):
        yield item
        for i in range(item.childCount()):
            yield from walk(item.child(i))
    for top in range(dock.tree.topLevelItemCount()):
        for item in walk(dock.tree.topLevelItem(top)):
            row = item.data(0, role)
            if isinstance(row, dict) and predicate(row):
                dock.tree.clearSelection()
                item.setSelected(True)
                return True
    return False


def portal_layer_ids(client, group):
    pid, _title = portals.group_portal(group)
    if pid is None:
        return None
    doc = client.portals.get(pid)
    return [(int(c["layer_id"]), c["layer_type"]) for c in (doc.get("layer_configs") or [])]


def instance_layer_names(client):
    return sorted(r.get("name") for r in client.layers.list() if str(r.get("name", "")).startswith(PREFIX))


# ── the run ─────────────────────────────────────────────────────────────────────────────────

def main():
    if not URL or not TOKEN:
        print("Set GEODEPLOY_URL and GEODEPLOY_TOKEN.")
        return 2
    # The dialogs, answered as a person accepting the defaults would.
    diffdialog.confirm = lambda parent, title, plan, creating: (True, True, True)
    uploadpicker.choose = lambda parent, candidates: [c[0] for c in candidates if c[1] is None]
    opened = []
    plugin.GeoDeployDock._open_url = lambda self, url: opened.append(url)

    iface = Iface()
    dock = build_dock(iface)
    sweep = True                        # see below: an earlier run that CRASHED never cleaned up
    project = QgsProject.instance()
    root = project.layerTreeRoot()

    section("Connect with an empty token field and a STALE `geodeploy login` on this machine")
    # A CLI login whose token had since been revoked used to be borrowed when the field was empty,
    # and blocked the connection with "This instance no longer accepts that token" — for someone who
    # never typed one. Only the typed token is used now: empty means anonymous, whatever is stored.
    real_saved = plugin.saved_instances
    plugin.saved_instances = lambda: [(URL, "gdp_stale_token_from_an_old_cli_login")]
    try:
        dock.url.setEditText(URL)
        dock.token.setText("")
        press("Connect (empty field, stale stored login)", dock.connect_to_instance)
        check("connected anonymously; the stored token was not used",
              dock.instance is not None and dock.instance.token is None)

        dock.token.setText("gdp_typed_but_wrong")
        start = len(MESSAGES)
        dock.connect_to_instance()
        typed = MESSAGES[start:]
        check("a TYPED bad token still fails loudly",
              any(lvl == plugin.MSG_CRITICAL for lvl, _t in typed), typed)
    finally:
        plugin.saved_instances = real_saved

    section("Connect")
    dock.url.setEditText(URL)
    dock.token.setText(TOKEN)
    press("Connect", dock.connect_to_instance)
    check("connected with a write token", dock.instance is not None and bool(dock.instance.token))
    client = dock.instance.client
    if sweep:
        # A run killed by a native crash never reaches its `finally`, and left its portals and
        # layers on the instance. Everything this script makes starts with "E2E-dock-", so the
        # next run clears it before starting.
        for p in client.portals.list():
            if str(p.get("title", "")).startswith("E2E-dock-"):
                client.portals.delete(p["id"])
        for r in client.layers.list():
            if str(r.get("name", "")).startswith("E2E-dock-"):
                client.layers.api(r["layer_type"]).delete(r["id"])

    a, b = make_points(PREFIX + "-a"), make_points(PREFIX + "-b", kinds=("castle", "museum", "park"))
    project.addMapLayers([a, b], False)
    group = root.addGroup(PREFIX)
    group.addLayer(a)
    group.addLayer(b)

    try:
        section("Push group — a group of NEW layers (the bug: uploaded them, then 'no layers')")
        iface.view.nodes = [group]
        press("Push group", dock.push_group)
        ids = portal_layer_ids(client, group)
        check("the portal exists and holds BOTH new layers", ids is not None and len(ids) == 2, ids)
        check("both layers are on the instance, once each",
              instance_layer_names(client) == sorted([PREFIX + "-a", PREFIX + "-b"]),
              instance_layer_names(client))
        check("both QGIS layers are tagged with their GeoDeploy id",
              portals.layer_identity(a) is not None and portals.layer_identity(b) is not None)
        first_ids = ids

        section("Push group again — nothing changed")
        press("Push group (unchanged)", dock.push_group)
        check("same portal, same layers", portal_layer_ids(client, group) == first_ids,
              portal_layer_ids(client, group))
        check("nothing uploaded a second time", len(instance_layer_names(client)) == 2,
              instance_layer_names(client))

        section("Push group after the portal was deleted on the instance (#128)")
        old_pid = portals.group_portal(group)[0]
        client.portals.delete(old_pid)
        press("Push group (portal deleted)", dock.push_group)
        new_pid = portals.group_portal(group)[0]
        check("a NEW portal was made", new_pid is not None and str(new_pid) != str(old_pid),
              (old_pid, new_pid))
        check("...with the same layers, none re-uploaded",
              sorted(portal_layer_ids(client, group) or []) == sorted(first_ids)
              and len(instance_layer_names(client)) == 2)

        section("Upload selected")
        c = make_points(PREFIX + "-c")
        project.addMapLayer(c, False)
        iface.view.layers = [c]
        iface.active = c
        press("Upload selected", dock.upload_active)
        check("the layer is on the instance", PREFIX + "-c" in instance_layer_names(client),
              instance_layer_names(client))
        check("...and tagged", portals.layer_identity(c) is not None)

        section("Push group with that uploaded layer added to it")
        group.addLayer(c)
        iface.view.nodes = [group]
        press("Push group (one more)", dock.push_group)
        ids = portal_layer_ids(client, group) or []
        check("the portal now holds all three", len(ids) == 3, ids)
        check("...still without re-uploading anything", len(instance_layer_names(client)) == 3)

        section("Save styling")
        # Hold the list: `categories()` returns a temporary copy, and a symbol taken from a copy
        # that has already been freed is a segfault, not an exception.
        cats = a.renderer().categories()
        sym = cats[0].symbol().clone()
        sym.setColor(QColor("#123456"))
        a.renderer().updateCategorySymbol(0, sym)
        iface.view.layers = [a]
        iface.active = a
        press("Save styling", dock.save_style)
        lid, kind = portals.layer_identity(a)
        stored = client.layers.api(kind).get(lid).get("default_style") or {}
        check("the instance's style carries the new colour", "#123456" in str(stored).lower(),
              str(stored)[:300])

        section("Refresh")
        press("Refresh", dock.refresh_layers)
        check("the catalog lists the run's layers",
              select_row(dock, lambda r: r.get("name") == PREFIX + "-b"))

        section("Add to map")
        before = set(project.mapLayers())
        press("Add to map", dock.add_selected)
        added = [project.mapLayer(i) for i in set(project.mapLayers()) - before]
        check("one layer was added", len(added) == 1, [l.name() for l in added])

        section("Restyle (reopen as an editable layer)")
        if added:
            name, old_id = added[0].name(), added[0].id()   # the swap deletes the old layer
            iface.active = added[0]
            iface.view.layers = [added[0]]
            same_name = lambda: {i for i, l in project.mapLayers().items() if l.name() == name}  # noqa: E731
            before_ids = same_name()            # the group's own source layer shares the name
            press("Restyle", dock.restyle_selected)
            after_ids = same_name()
            check("the layer was REPLACED: the old one is gone, one new one took its place",
                  old_id not in after_ids and len(after_ids) == len(before_ids)
                  and len(after_ids - before_ids) == 1, (before_ids, after_ids))

        section("Open portal as group")
        title = client.portals.get(portals.group_portal(group)[0]).get("title")
        found = select_row(dock, lambda r: bool(r.get("_portal")) and r.get("title") == title)
        check("the portal is listed", found, title)
        groups_before = len(root.findGroups())
        if found:
            press("Open portal as group", dock.open_portal_as_group)
            check("a group was added for it", len(root.findGroups()) == groups_before + 1)

        section("Open in browser")
        press("Open in browser", dock.open_in_browser)
        check("a URL on this instance was opened", any(u.startswith(URL) for u in opened), opened)
    finally:
        section("Cleanup")
        if KEEP:
            print("  GEODEPLOY_KEEP=1 — leaving everything named {0}".format(PREFIX))
        else:
            for p in client.portals.list():
                if str(p.get("title", "")).startswith(PREFIX):
                    client.portals.delete(p["id"])
            for r in client.layers.list():
                if str(r.get("name", "")).startswith(PREFIX):
                    client.layers.api(r["layer_type"]).delete(r["id"])
            left = instance_layer_names(client)
            check("nothing left behind", not left, left)

    print("\n{0} checks, {1} failed".format(CHECKS[0], len(FAILURES)))
    for name in FAILURES:
        print("  FAILED: " + name)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    code = main()
    os._exit(code)
