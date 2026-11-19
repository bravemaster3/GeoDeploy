"""Pushing a group again: what the plugin recognises as already on the instance, and what it does not.

WHAT THIS IS FOR (issue #128). A push remembers what each QGIS layer became on GeoDeploy as a tag
on the layer, and which portal the group became as a tag on the group. Three things broke that
without anybody noticing, all reproduced before this was written:

* the tags are custom properties, which QGIS does not count as a change — so closing without saving
  lost them silently, and the next push uploaded everything again (pinned in `test_real_qgis.py`,
  which needs a real QgsProject; this file covers the logic around it);
* a group whose portal was DELETED could never be pushed again: the instance answers 404, and the
  push stopped at "could not read the portal";
* a tag is only an id. One pointing at a layer deleted on the instance, or written for ANOTHER
  instance, was trusted — and the server silently skips an id it does not have, so the layer the
  dialog called "already on the instance" never appeared on the portal.

Run it:

    python3 scripts/test_repush.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.join(HERE, "..")
sys.path.insert(0, os.path.join(PLUGIN, "geodeploy_qgis", "vendor"))
sys.path.insert(0, os.path.join(PLUGIN, "geodeploy_qgis"))
sys.path.insert(0, PLUGIN)

from geodeploy_qgis import portals                                               # noqa: E402

FAILURES = []
CHECKS = [0]


def check(name, condition, detail=""):
    CHECKS[0] += 1
    if condition:
        print("  ok   {0}".format(name))
    else:
        print("  FAIL {0}{1}".format(name, "  — " + str(detail)[:400] if detail else ""))
        FAILURES.append(name)


class Layer(object):
    """A QGIS layer as `plan_push` sees it: a name, a source on disk, custom properties."""

    def __init__(self, name, props=None):
        self._name = name
        self._props = dict(props or {})

    def name(self):
        return self._name

    def customProperty(self, key, default=None):
        return self._props.get(key, default)

    def setCustomProperty(self, key, value):
        self._props[key] = value

    def providerType(self):
        return "ogr"

    def source(self):
        return "/data/{0}.gpkg".format(self._name)

    def attribution(self):
        return ""

    def opacity(self):
        return 1.0


class Node(object):
    def __init__(self, layer):
        self._layer = layer

    def children(self):
        return []

    def layer(self):
        return self._layer

    def isVisible(self):
        return True

    def customProperty(self, key, default=None):
        return None

    def name(self):
        return "node"


class Group(Node):
    def __init__(self, kids, props=None):
        Node.__init__(self, None)
        self._kids = kids
        self._props = dict(props or {})

    def children(self):
        return self._kids

    def layer(self):
        return None

    def customProperty(self, key, default=None):
        return self._props.get(key, default)

    def setCustomProperty(self, key, value):
        self._props[key] = value

    def name(self):
        return "My portal"


def tagged(name, layer_id, instance_id=None):
    props = {portals.P_LAYER_ID: str(layer_id), portals.P_LAYER_TYPE: "vector",
             portals.P_INSTANCE: "https://old.example.org"}
    if instance_id:
        props[portals.P_INSTANCE_ID] = instance_id
    return Layer(name, props)


class FakePortals(object):
    def __init__(self):
        self.calls = []

    def create(self, title):
        self.calls.append(("create", title))
        return {"id": 99}

    def update(self, portal_id, **kw):
        self.calls.append(("update", portal_id))

    def set_groups(self, portal_id, tree):
        self.calls.append(("groups", portal_id))

    def publish(self, portal_id):
        self.calls.append(("publish", portal_id))

    def get(self, portal_id):
        return {"id": portal_id, "title": "My portal", "slug": "x"}


class FakeClient(object):
    def __init__(self):
        self.portals = FakePortals()


def main():
    print("Re-pushing a group (issue #128)\n")
    here = "a4016b7349a79110"
    existing = {(5, "vector"), (6, "vector")}
    usable = portals.usable_tags(here, existing)

    print("Which tags still point at something")
    check("a tag for a layer that exists, on this instance, is kept",
          usable((5, "vector"), tagged("roads", 5, here)))
    check("a tag for a layer DELETED on the instance is not",
          not usable((7, "vector"), tagged("rivers", 7, here)))
    check("a tag written for ANOTHER instance is not, even when the id exists here",
          not usable((5, "vector"), tagged("roads", 5, "0000other000000")))
    check("the same server under a new address is the same server (no id compared by URL)",
          usable((5, "vector"), Layer("roads", {portals.P_LAYER_ID: "5",
                                                portals.P_LAYER_TYPE: "vector",
                                                portals.P_INSTANCE: "http://151.245.139.229",
                                                portals.P_INSTANCE_ID: here})))
    check("an OLD tag with no instance id is judged on whether its layer exists",
          usable((5, "vector"), tagged("roads", 5)) and not usable((8, "vector"), tagged("x", 8)))
    old_server = portals.usable_tags(None, existing)
    check("a server too old to report an id: existence alone decides",
          old_server((5, "vector"), tagged("roads", 5, "0000other000000"))
          and not old_server((7, "vector"), tagged("rivers", 7)))

    print("\nWhat the plan does with them")
    group = Group([Node(tagged("roads", 5, here)), Node(tagged("rivers", 7, here)),
                   Node(Layer("parks"))])
    plan = portals.plan_push(group, lambda _l, _t: {}, [], usable=usable)
    uploads = [name for name, _l, _n in plan["uploads"]]
    check("a stale tag is planned as an UPLOAD, like a layer that never went",
          uploads == ["rivers", "parks"], uploads)
    check("...and named, so the dialog can say why", plan.get("stale") == ["rivers"],
          plan.get("stale"))
    check("the live tag is still recognised", [c["layer_id"] for c in plan["configs"]] == [5],
          plan["configs"])
    trusting = portals.plan_push(group, lambda _l, _t: {}, [])
    check("without a check (listing failed), tags are trusted exactly as before",
          [c["layer_id"] for c in trusting["configs"]] == [5, 7]
          and [n for n, _l, _x in trusting["uploads"]] == ["parks"] and not trusting["stale"],
          trusting)

    print("\nA group of NEW layers: plan, upload, re-plan (the push's own sequence)")
    # The instance listing is taken ONCE, before anything is uploaded. The re-plan after the
    # uploads used that same snapshot, judged every just-uploaded layer "missing", and sent an
    # empty portal: "This group has no GeoDeploy layers in it" — after uploading all of them.
    usable = portals.usable_tags(here, {(5, "vector")})
    parks, rivers = Layer("parks"), Layer("rivers")
    group = Group([Node(parks), Node(rivers), Node(tagged("roads", 5, here))])
    first = portals.plan_push(group, lambda _l, _t: {}, [], usable=usable)
    check("first plan: the two new layers are uploads",
          [n for n, _l, _x in first["uploads"]] == ["parks", "rivers"], first["uploads"])
    for layer, new_id in ((parks, 9), (rivers, 10)):         # what the worker does per upload
        portals.tag_layer(layer, "https://x", new_id, "vector", instance_id=here)
        usable.remember((new_id, "vector"))
    final = portals.plan_push(group, lambda _l, _t: {}, [], usable=usable)
    check("the re-plan publishes them, in group order",
          [c["layer_id"] for c in final["configs"]] == [9, 10, 5], final["configs"])
    check("...with nothing called stale and nothing left to upload",
          not final["stale"] and not final["uploads"], (final["stale"], final["uploads"]))
    check("remembering one push's layers does not vouch for a deleted one",
          not usable((7, "vector"), tagged("gone", 7, here)))

    print("\nA group whose portal was deleted")
    client = FakeClient()
    group = Group([Node(tagged("roads", 5, here))],
                  {portals.P_PORTAL_ID: "3", portals.P_PORTAL_TITLE: "My portal"})
    portals.push(client, group, [{"layer_id": 5, "layer_type": "vector"}], new_portal=True)
    check("it becomes a NEW portal instead of updating the deleted one",
          client.portals.calls[0] == ("create", "My portal")
          and ("update", 3) not in client.portals.calls, client.portals.calls)
    check("...and the group now points at the new one",
          group.customProperty(portals.P_PORTAL_ID) == "99", group._props)
    client = FakeClient()
    portals.push(client, group, [{"layer_id": 5, "layer_type": "vector"}])
    check("an ordinary re-push still updates the portal it has",
          ("update", 99) in client.portals.calls and not any(c[0] == "create"
                                                             for c in client.portals.calls),
          client.portals.calls)

    print("\nTagging records the instance's stable id")
    layer = Layer("roads")
    portals.tag_layer(layer, "https://geodeploy-lite.kndev.org", 5, "vector", instance_id=here)
    check("the id is written beside the URL", layer.customProperty(portals.P_INSTANCE_ID) == here,
          layer._props)
    layer = Layer("roads")
    portals.tag_layer(layer, "https://x.org", 5, "vector")
    check("...and nothing is written when the server has none",
          layer.customProperty(portals.P_INSTANCE_ID) is None, layer._props)

    print("\n{0} checks, {1} failed".format(CHECKS[0], len(FAILURES)))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
