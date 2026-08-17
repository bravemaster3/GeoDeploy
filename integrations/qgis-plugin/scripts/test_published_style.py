"""Rebuilding a portal's layer list from its PUBLIC style.json.

This is the anonymous path — no token, no API — and it was quietly wrong in two ways that both look
like "the plugin ignores my portal":

* **The style came out empty for most layers.** `geodeploy:legend` lists the CLASSES of a graduated
  or categorized layer; for a single-symbol layer, which most are, it is `[]`. Reading only that
  produced `{}`, and the plugin fell back to the layer's own default style — a different colour
  from the one the portal draws. Nothing to do with permissions: measured on a public layer.
* **A layer went missing.** De-duplication was keyed on the layer id alone, and vectors and rasters
  are numbered separately, so a portal holding vector 1 and raster 1 lost one of them silently.

The fixture below is trimmed from a real `style.json` — same keys, same nesting, same metadata
names — so it tests the document the server actually publishes rather than an idea of it.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(HERE, "..", "geodeploy_qgis")
sys.path.insert(0, PKG)
sys.path.insert(0, os.path.join(PKG, "vendor"))


def _load(name):
    """Import a plugin module without its QGIS-only imports."""
    lines = open(os.path.join(PKG, name + ".py"), encoding="utf-8").read().splitlines()
    src = "\n".join(l for l in lines
                    if not (l.startswith("from .connection") or l.startswith("from .symbology")))
    ns = {}
    exec(compile(src, name, "exec"), ns)                                        # noqa: S102
    return ns


symbology = _load("symbology")
portals = _load("portals")
configs_from_published_style = portals["configs_from_published_style"]
style_from_legend = symbology["style_from_legend"]

STYLE_DOC = {
    "sources": {
        "vector_3": {"type": "vector", "url": "pmtiles://https://x.org/api/data/vector/3/pmtiles"},
        "vector_4": {"type": "vector",
                     "tiles": ["https://x.org/tiles/geodeploy_u1.dresden/{z}/{x}/{y}"]},
        "vector_1": {"type": "vector", "tiles": ["https://x.org/tiles/geodeploy_u1.countries/{z}/{x}/{y}"]},
        "raster_1": {"type": "raster", "tiles": ["/raster/cog/tiles/WebMercatorQuad/{z}/{x}/{y}?url=s3://b/a.tif&rescale=0,1"]},
    },
    # MapLibre draws later layers on top, so this list is BOTTOM-to-top.
    "layers": [
        {"id": "vector-3", "type": "fill", "source": "vector_3", "source-layer": "geodeploy",
         "paint": {"fill-color": "#10b981", "fill-opacity": 0.45,
                   "fill-outline-color": "#1d4ed8"},
         "metadata": {"geodeploy:layer_id": 3, "geodeploy:type": "vector",
                      "geodeploy:name": "example", "geodeploy:opacity": 1.0,
                      "geodeploy:marker": "circle", "geodeploy:lineType": "solid",
                      "geodeploy:legend": []}},
        # The SAME layer baked twice — a fill plus its outline. Only one config may come out.
        {"id": "vector-3-outline", "type": "line", "source": "vector_3", "source-layer": "geodeploy",
         "paint": {"line-color": "#1d4ed8", "line-width": 1},
         "metadata": {"geodeploy:layer_id": 3, "geodeploy:type": "vector"}},
        {"id": "raster-1", "type": "raster", "source": "raster_1", "paint": {"raster-opacity": 0.6},
         "metadata": {"geodeploy:layer_id": 1, "geodeploy:type": "raster",
                      "geodeploy:name": "RVI_2023_2024", "geodeploy:opacity": 0.6}},
        {"id": "vector-1", "type": "symbol", "source": "vector_1", "source-layer": "geodeploy",
         "paint": {"icon-opacity": 1.0},
         "metadata": {"geodeploy:layer_id": 1, "geodeploy:type": "vector",
                      "geodeploy:name": "countries", "geodeploy:opacity": 1.0,
                      "geodeploy:marker": "circle", "geodeploy:markerColor": "#ef4444",
                      "geodeploy:markerSize": 1.0, "geodeploy:legend": []}},
        {"id": "vector-4", "type": "line", "source": "vector_4", "source-layer": "geodeploy_u1.dresden",
         "layout": {"visibility": "none"},
         "paint": {"line-color": "#3b82f6", "line-width": 1.04, "line-opacity": 1.0},
         "metadata": {"geodeploy:layer_id": 4, "geodeploy:type": "vector",
                      "geodeploy:name": "shapefiles_dresden", "geodeploy:opacity": 1.0,
                      "geodeploy:lineType": "dashed", "geodeploy:legend": []}},
    ],
}

configs = configs_from_published_style(STYLE_DOC, style_from_legend)
by_name = {c["name"]: c for c in configs}

# ── nothing may go missing ────────────────────────────────────────────────────────────────────
assert len(configs) == 4, [c["name"] for c in configs]
assert "countries" in by_name and "RVI_2023_2024" in by_name, (
    "vector 1 and raster 1 are DIFFERENT layers; keying de-duplication on the id alone drops one")
print("de-duplication   -> 4 layers from 5 baked, and vector 1 / raster 1 both survive")

# ── order is reversed: MapLibre draws bottom-to-top, a portal lists top-first ─────────────────
assert [c["name"] for c in configs] == ["shapefiles_dresden", "countries", "RVI_2023_2024",
                                        "example"], [c["name"] for c in configs]
print("order            -> reversed, so the group opens the right way up")

# ── a POLYGON keeps the portal's colour, outline and translucency ─────────────────────────────
s = by_name["example"]["style"]
assert s["color"] == "#10b981", s          # NOT the layer's default
assert s["outline_color"] == "#1d4ed8", s
assert s["fill_opacity"] == 0.45, s
print("polygon          ->", json.dumps(s))

# ── a LINE keeps the portal's colour and width, and its dash comes from the metadata ──────────
s = by_name["shapefiles_dresden"]["style"]
assert s["color"] == "#3b82f6" and s["line_width"] == 1.04 and s["lineType"] == "dashed", s
assert by_name["shapefiles_dresden"]["visible"] is False, "layout.visibility none means hidden"
print("line             ->", json.dumps(s))

# ── POINTS are baked as icons, so colour and radius live in the metadata, not the paint ───────
s = by_name["countries"]["style"]
assert s["color"] == "#ef4444" and s["radius"] == 1.0, s
print("point (icon)     ->", json.dumps(s))

# ── opacity travels ───────────────────────────────────────────────────────────────────────────
assert by_name["RVI_2023_2024"]["opacity"] == 0.6
print("opacity          -> carried from the baked layer")

# ── the layer-level opacity must not be counted twice ─────────────────────────────────────────
# The portal bakes `opacity * fill_opacity` into one number and QGIS applies layer opacity itself,
# so a half-opacity layer with a 0.45 fill bakes 0.225 and must come back out as 0.45.
half = json.loads(json.dumps(STYLE_DOC))
ml = half["layers"][0]
ml["paint"]["fill-opacity"] = 0.225
ml["metadata"]["geodeploy:opacity"] = 0.5
out = {c["name"]: c for c in configs_from_published_style(half, style_from_legend)}["example"]
assert out["opacity"] == 0.5 and abs(out["style"]["fill_opacity"] - 0.45) < 1e-9, out
print("opacity, twice   -> divided back out, not applied twice")

# ── a CATEGORIZED layer, the shape the live instance bakes ────────────────────────────────────
cat = json.loads(json.dumps(STYLE_DOC))
ml = cat["layers"][0]
ml["paint"]["fill-color"] = ["match", ["get", "Type"], "Autochamber", "#8c4ee3", "#eea26b"]
ml["metadata"]["geodeploy:legendField"] = "Type"
ml["metadata"]["geodeploy:legend"] = [
    {"color": "#8c4ee3", "label": "Autochamber", "value": "Autochamber"},
    {"color": "#6cefa2", "label": "Manual chamber", "value": "Manual chamber"},
    {"color": "#eea26b", "label": "Other", "value": None, "other": True}]
s = {c["name"]: c for c in configs_from_published_style(cat, style_from_legend)}["example"]["style"]
assert s.get("color_mode") == "categorized" and s.get("color_field") == "Type", s
assert not isinstance(s.get("color"), list)
print("categorized      ->", len(s.get("categories") or []), "categories, field", s.get("color_field"))


# ── a CLASSIFIED layer bakes an expression; its classes come from the legend, not the paint ───
classified = json.loads(json.dumps(STYLE_DOC))
ml = classified["layers"][0]
ml["paint"]["fill-color"] = ["step", ["get", "pop"], "#fee5d9", 100, "#a50f15"]
ml["metadata"]["geodeploy:legendField"] = "pop"
# The mode is not stated anywhere — it is read from the ENTRIES' shape (`_mode_of`): `min`/`max`
# means graduated, `value` means categorized. Written the way the server bakes it.
ml["metadata"]["geodeploy:legend"] = [{"color": "#fee5d9", "label": "< 100", "min": None, "max": 100},
                                      {"color": "#a50f15", "label": "100 - 900", "min": 100, "max": 900}]
s = {c["name"]: c for c in
     configs_from_published_style(classified, style_from_legend)}["example"]["style"]
assert s.get("color_mode") == "graduated" and len(s.get("classes") or []) == 2, s
assert not isinstance(s.get("color"), list), "a MapLibre expression must never be used as a colour"
print("classified       -> classes from the legend, expression never mistaken for a colour")

print("\nALL PUBLISHED-STYLE CASES PASS")

# ── the AUTHENTICATED document, and the asymmetry that bit it ─────────────────────────────────
# `PortalOut.LayerConfig` is {layer_id, layer_type, visible, opacity, style, popup_fields}: no
# `source`, no `geometry_type`, no `name`. Every fix that read those from the published style
# therefore covered the anonymous path only — a portal opened WITH a token still drew its rasters in
# the layer's default style, and still had to guess geometry from a listing lookup. `enrich_from_
# published` merges the missing keys in, and must never touch the API's own values.
enrich_from_published = portals["enrich_from_published"]


class FakeInstance:
    def __init__(self, doc, fail=False):
        self._doc, self.fail, self.calls = doc, fail, 0

    def published_style(self, slug):
        self.calls += 1
        if self.fail:
            raise RuntimeError("style.json is unreachable")
        return self._doc


API_DOC = {
    "id": 13, "slug": "abc", "title": "Web map", "published": True,
    "layer_configs": [
        # A raster whose PORTAL styling is a terrain colormap; the API config states the style but
        # gives no source, which is what left the plugin building it from the layer's default.
        {"layer_id": 1, "layer_type": "raster", "visible": True, "opacity": 0.6,
         "style": {"colormap": "terrain", "rescale": "264.9,298.33"}, "popup_fields": []},
        # A vector the portal draws differently from its default.
        {"layer_id": 3, "layer_type": "vector", "visible": True, "opacity": 1.0,
         "style": {"color": "#10b981", "fill_opacity": 0.45}, "popup_fields": []},
    ],
}

inst = FakeInstance(STYLE_DOC)
out = enrich_from_published(json.loads(json.dumps(API_DOC)), inst, style_from_legend)
by_id = {(c["layer_id"], c["layer_type"]): c for c in out["layer_configs"]}

raster = by_id[(1, "raster")]
assert raster["source"]["kind"] == "raster-xyz", raster
assert "rescale=0,1" in raster["source"]["url"], "the PORTAL's tile template, styling and all"
assert raster["opacity"] == 0.6, "the API's own opacity must survive"
assert raster["style"] == {"colormap": "terrain", "rescale": "264.9,298.33"}, "style untouched"
print("authed raster    -> gains the portal's source, keeps the API's style and opacity")

vector = by_id[(3, "vector")]
assert vector["geometry_type"] == "polygon", vector
assert vector["source"]["kind"] == "pmtiles", vector
assert vector["name"] == "example", "a failed layer is named, not numbered"
# The API's style is authoritative for vectors and must NOT be replaced by the baked one.
assert vector["style"] == {"color": "#10b981", "fill_opacity": 0.45}, vector
print("authed vector    -> gains geometry/source/name, style left alone")

# An UNPUBLISHED portal has no style.json to read, and must not be asked for one.
unpub = enrich_from_published(dict(json.loads(json.dumps(API_DOC)), published=False),
                              FakeInstance(STYLE_DOC), style_from_legend)
assert unpub["layer_configs"][0].get("source") is None
print("unpublished      -> nothing merged, nothing fetched")

# An unreachable style.json must degrade, not raise — the API document is perfectly usable.
degraded = enrich_from_published(json.loads(json.dumps(API_DOC)),
                                 FakeInstance(STYLE_DOC, fail=True), style_from_legend)
assert len(degraded["layer_configs"]) == 2
print("unreachable      -> degrades to the API document")

# A layer the published style does not mention (added since publishing) is left as it is.
extra = json.loads(json.dumps(API_DOC))
extra["layer_configs"].append({"layer_id": 99, "layer_type": "vector", "style": {}})
out = enrich_from_published(extra, FakeInstance(STYLE_DOC), style_from_legend)
assert out["layer_configs"][-1].get("geometry_type") is None
print("unknown layer    -> untouched")

print("\nALL AUTHENTICATED-PARITY CASES PASS")
