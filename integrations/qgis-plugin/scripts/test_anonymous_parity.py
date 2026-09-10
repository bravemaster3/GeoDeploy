"""Without a token, a portal must open exactly as well as with one.

REPORTED THAT WAY: "unauthenticated paths are lagging behind. all the improvements made are not
working without token."

THE CAUSE WAS TWO IMPLEMENTATIONS. With a token the plugin asked the API for `layer_configs` — the
styling as the author wrote it. Without one it read the published `style.json` and translated the
MapLibre paint BACKWARDS. That reverse translation is lossy by construction: a rule tree, a stacked
stroke, a per-class marker, a label's placement, a scale range — none of them has a single paint
value to be recovered from. So every symbology fix landed on the authenticated path and widened the
gap, and the anonymous one drifted further behind with each release.

There is one path now. `Instance.portal_document` decides how to READ a portal — the API, the
public portal route, or (only for an instance too old for that route) the published style — and all
three answer with the same `layer_configs`, so everything downstream has one shape.

WHAT THIS FILE PINS is that single path and its order, with a fake transport standing in for the
instance. `test_published_style.py` still covers the legacy translation, because an old instance is
a real thing somebody is connected to.

Run it:

    python3 scripts/test_anonymous_parity.py
"""
import json
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.join(HERE, "..")
sys.path.insert(0, os.path.join(PLUGIN, "geodeploy_qgis", "vendor"))
sys.path.insert(0, os.path.join(PLUGIN, "geodeploy_qgis"))
sys.path.insert(0, PLUGIN)

FAILURES = []
CHECKS = [0]


def check(name, condition, detail=""):
    CHECKS[0] += 1
    if condition:
        print("  ok   {0}".format(name))
    else:
        print("  FAIL {0}{1}".format(name, "  — " + str(detail)[:400] if detail else ""))
        FAILURES.append(name)


#: A style with every key the reverse translation cannot recover. If the anonymous path carries
#: these, it is carrying the portal rather than an impression of it.
AUTHORED = {
    "color": "#d40000", "line_width": 3.0, "lineType": "solid",
    "line_stack": [{"color": "#1f4fd8", "line_width": 3.0, "lineType": "dashed"}],
    "rules": [{"label": "Confident", "expression": '"c" = 1',
               "filter": ["==", ["get", "c"], 1], "minzoom": 12.5,
               "style": {"color": "#111111", "lineType": "dotted"}}],
    "labels": {"enabled": True, "field": "name", "placement": "line", "line_position": "on",
               "label_per_part": True, "minzoom": 11.25,
               "rules": [{"label": "Big", "filter": [">", ["get", "pop"], 10],
                          "labels": {"enabled": True, "field": "name", "size": 14.0}}]},
}

PUBLIC_DOC = {
    "slug": "open-portal", "title": "Open portal", "published": True,
    "layer_configs": [{"layer_id": 7, "layer_type": "vector", "visible": True,
                       "opacity": 0.8, "style": AUTHORED, "popup_fields": ["name"]}],
    "layer_groups": [{"id": "g1", "name": "Roads", "children": []}],
}

#: What the same portal looks like as PAINT — all a pre-upgrade instance offers.
STYLE_DOC = {
    "sources": {"vector_7": {"type": "vector",
                             "tiles": ["https://x.org/tiles/gd.roads/{z}/{x}/{y}"]}},
    "layers": [{"id": "vector-7", "type": "line", "source": "vector_7",
                "source-layer": "gd.roads",
                "paint": {"line-color": "#d40000", "line-width": 3.0},
                "metadata": {"geodeploy:layer_id": 7, "geodeploy:type": "vector",
                             "geodeploy:name": "Roads", "geodeploy:legend": []}}],
}


class FakeClient(object):
    def __init__(self, portal=None, fail=False):
        self._portal, self._fail = portal, fail
        self.calls = []

        outer = self

        class Portals(object):
            def get(self, ref):
                outer.calls.append(("api", ref))
                if outer._fail or outer._portal is None:
                    from geodeploy.errors import GeoDeployError
                    raise GeoDeployError("no")
                return outer._portal
        self.portals = Portals()
        self.user_agent = "geodeploy-qgis-test"


def instance(token=None, portal=None, public=None, style=None, api_fails=False):
    """An `Instance` with its two HTTP doors replaced, so the ORDER can be observed."""
    from geodeploy_qgis.connection import Instance

    inst = Instance("https://example.invalid", token)
    inst.client = FakeClient(portal, fail=api_fails)
    inst.fetched = []

    def fetch_json(url, cache=True):
        inst.fetched.append(url)
        if "/api/public/portals/" in url:
            if public is None:
                raise ValueError("404")
            return public
        if "/api/public/layers/" in url:
            return public or {}
        raise ValueError("404")

    def published_style(slug):
        inst.fetched.append("style.json")
        if style is None:
            from geodeploy.errors import GeoDeployError
            raise GeoDeployError("no style")
        return style

    inst.fetch_json = fetch_json
    inst.published_style = published_style
    return inst


def styles_of(doc):
    return [c.get("style") or {} for c in doc.get("layer_configs") or []]


def main():
    print("One path for a portal, with or without a token\n")

    from geodeploy_qgis import symbology                                        # noqa: F401

    row = {"id": 3, "slug": "open-portal", "title": "Open portal"}

    # ── with a token: the API, and nothing else is touched ───────────────────────────────────────
    print("With a token")
    authed = instance(token="tok", portal=dict(PUBLIC_DOC))
    doc_a = authed.portal_document(row)
    check("the API answers", authed.client.calls == [("api", 3)], authed.client.calls)
    check("...and nothing else is fetched", authed.fetched == [], authed.fetched)
    check("the authored style is what comes back", styles_of(doc_a) == [AUTHORED],
          json.dumps(styles_of(doc_a))[:200])

    # ── without one: the public portal route ─────────────────────────────────────────────────────
    print("\nWithout a token")
    anon = instance(token=None, public=dict(PUBLIC_DOC))
    doc_b = anon.portal_document(row)
    check("the API is not called at all", anon.client.calls == [], anon.client.calls)
    check("the public portal route is",
          any("/api/public/portals/open-portal" in u for u in anon.fetched), anon.fetched)
    check("the published style is NOT read", "style.json" not in anon.fetched, anon.fetched)

    # ── and the two are the same portal ──────────────────────────────────────────────────────────
    print("\nThe two agree")
    check("the same layers",
          [(c["layer_id"], c["layer_type"]) for c in doc_a["layer_configs"]]
          == [(c["layer_id"], c["layer_type"]) for c in doc_b["layer_configs"]])
    check("the same styling, key for key", styles_of(doc_a) == styles_of(doc_b),
          json.dumps({"authed": styles_of(doc_a), "anon": styles_of(doc_b)})[:300])
    check("the same visibility and opacity",
          [(c.get("visible"), c.get("opacity")) for c in doc_a["layer_configs"]]
          == [(c.get("visible"), c.get("opacity")) for c in doc_b["layer_configs"]])
    check("the folder tree too", doc_a.get("layer_groups") == doc_b.get("layer_groups"))

    # …and specifically the keys that a paint-only document cannot carry.
    anon_style = styles_of(doc_b)[0]
    for key in ("line_stack", "rules"):
        check("anonymous: `{0}` survives".format(key), anon_style.get(key) == AUTHORED[key],
              json.dumps(anon_style.get(key))[:160])
    labels = anon_style.get("labels") or {}
    for key in ("placement", "line_position", "label_per_part", "rules", "minzoom"):
        check("anonymous: labels.{0} survives".format(key),
              labels.get(key) == AUTHORED["labels"][key], labels.get(key))

    # ── an instance too old for the public route still opens, and says so ────────────────────────
    print("\nAn instance without the public portal route")
    old = instance(token=None, public=None, style=STYLE_DOC)
    doc_c = old.portal_document(row)
    check("it falls back to the published style",
          "style.json" in old.fetched, old.fetched)
    check("...and a portal still opens", len(doc_c.get("layer_configs") or []) == 1,
          json.dumps(doc_c)[:200])
    check("...marked as rebuilt, so nothing downstream mistakes it for the real thing",
          doc_c.get("_rebuilt_from_style") is True, doc_c)
    check("...and the colour it CAN read is right",
          (styles_of(doc_c)[0] or {}).get("color") == "#d40000", styles_of(doc_c))

    # ── the token path falls back too, rather than failing ───────────────────────────────────────
    print("\nA token that cannot read this portal")
    # A published portal is readable by anybody; a token that lacks the portal scope must not make
    # the plugin WORSE than no token at all.
    broken = instance(token="tok", public=dict(PUBLIC_DOC), api_fails=True)
    doc_d = broken.portal_document(row)
    check("the API is tried first", broken.client.calls == [("api", 3)], broken.client.calls)
    check("...then the public route", styles_of(doc_d) == [AUTHORED], styles_of(doc_d))

    # ── a portal with no address anywhere ────────────────────────────────────────────────────────
    print("\nA portal with nothing to read")
    from geodeploy.errors import GeoDeployError
    nothing = instance(token=None)
    try:
        nothing.portal_document({"title": "no slug"})
        raised = None
    except GeoDeployError as exc:
        raised = str(exc)
    except Exception as exc:                                                     # noqa: BLE001
        raised = "{0}: {1}".format(type(exc).__name__, exc)
    check("it says so rather than raising something unreadable",
          raised is not None and "published address" in raised, raised)

    # ── a layer row is completed the same way ────────────────────────────────────────────────────
    print("\nA layer row, with or without a token")
    detail = {"id": "abc", "name": "Roads", "default_style": {"style": {"color": "#00ff00"}},
              "schema_name": "gd", "table_name": "roads", "columns": [{"name": "n"}],
              "storage_backend": "postgis"}
    thin = {"id": "abc", "name": "Roads", "layer_type": "vector", "_public": True,
            "_base": "https://example.invalid"}
    anon_layer = instance(token=None, public=detail)
    full = anon_layer.layer_detail(thin)
    check("the anonymous row gains the styling the index leaves out",
          (full.get("default_style") or {}).get("style", {}).get("color") == "#00ff00", full)
    check("...and the table it is served from", full.get("table_name") == "roads", full)
    check("...while keeping what only the plugin knows",
          full.get("_base") == "https://example.invalid" and full.get("_public") is True, full)

    authed_layer = instance(token="tok")
    already = {"id": 1, "name": "Roads", "default_style": {"style": {}}}
    check("an authenticated row is left exactly as it is",
          authed_layer.layer_detail(already) is already, "it was rebuilt")
    check("...and asks the instance nothing", authed_layer.fetched == [], authed_layer.fetched)

    print("\n{0} checks, {1} failed".format(CHECKS[0], len(FAILURES)))
    for name in FAILURES:
        print("   FAILED: {0}".format(name))
    sys.exit(1 if FAILURES else 0)


if __name__ == "__main__":
    main()
