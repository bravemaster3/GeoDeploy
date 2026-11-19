"""Issue #118, the web half: what the portal does with the keys the QGIS plugin now sends.

Reported on the D+S Canal example project. Three things the browser drew differently from QGIS:
* a border at full strength on a translucent fill was faded by the fill (MapLibre applies
  `fill-opacity` to `fill-outline-color`; QGIS does not), so a dark woodland edge drew near-white —
  and an outline-ONLY polygon, sent with `fill_opacity: 0`, drew no border at all;
* a width in MAP UNITS (a 10 m path) was not sent, so it drew at the 2 px default;
* a class without a dash inherited the first class's dash.
Every new behaviour is gated on a key only the plugin sends, so a style authored in the editor —
every portal that exists — renders exactly as before; that is pinned here too.
"""
import math
import pathlib
import re

from geodeploy.services import portal_generator as pg
from geodeploy.services import symbology as sym

ROOT = pathlib.Path(__file__).resolve().parents[2]


class Layer:
    id = 4
    name = "Probable path"
    storage_backend = "postgis"
    schema_name = "s"
    table_name = "t"
    uid = "u4"
    s3_key = None
    pmtiles_key = None
    default_style = {}
    tile_status = "ready"

    def __init__(self, geom, bbox='[13.5, 51.0, 14.0, 51.4]'):
        self.geometry_type = geom
        self.bbox = bbox


def built(style, geom="MultiPolygon", bbox='[13.5, 51.0, 14.0, 51.4]'):
    return pg._vector_layers("src", Layer(geom, bbox), {"style": style, "opacity": 1.0})


# ── a border with its own strength ───────────────────────────────────────────────────────────────

def test_an_editor_style_is_unchanged():
    """The default every editor-made polygon has: a 45% wash with an outline. No new layer."""
    layers = built({"color": "#3b82f6", "fill_opacity": 0.45, "outline_color": "#1d4ed8"})
    assert [x["type"] for x in layers] == ["fill"]
    assert layers[0]["paint"]["fill-outline-color"] == "#1d4ed8"


def test_a_qgis_border_is_drawn_at_its_own_strength_over_a_translucent_fill():
    layers = built({"color": "#d1e7c3", "fill_opacity": 0.502, "outline_color": "#bacdae",
                    "outline_width": 0.98, "outline_opacity": 1.0})
    fill, line = layers
    assert fill["paint"]["fill-antialias"] is False and "fill-outline-color" not in fill["paint"]
    assert line["type"] == "line" and line["paint"]["line-color"] == "#bacdae"
    assert line["paint"]["line-opacity"] == 1.0


def test_an_outline_only_polygon_keeps_its_border():
    """`fill_opacity: 0` faded `fill-outline-color` to nothing — the border was invisible."""
    layers = built({"fill_opacity": 0.0, "outline_color": "#ff0000", "outline_width": 1.0,
                    "outline_opacity": 1.0})
    assert [x["type"] for x in layers] == ["fill", "line"]
    assert layers[1]["paint"]["line-opacity"] == 1.0


def test_a_translucent_border_keeps_its_strength():
    line = built({"outline_color": "#000000", "outline_opacity": 0.4})[1]
    assert line["paint"]["line-opacity"] == 0.4


# ── a width on the ground ────────────────────────────────────────────────────────────────────────

def test_ground_width_doubles_with_every_zoom_and_honours_latitude():
    expr = sym.ground_width(10, 51.2)
    assert expr[:3] == ["interpolate", ["exponential", 2], ["zoom"]]
    z0, w0, z24, w24 = expr[3:]
    assert (z0, z24) == (0, 24)
    expected = 10 / (40075016.68557849 / 512 * math.cos(math.radians(51.2)))
    assert abs(w0 - expected) / expected < 1e-5
    assert abs(w24 / w0 - 2 ** 24) / 2 ** 24 < 1e-5
    # Independently: MapLibre's world at zoom z is 512 x 2^z px around a 6,378,137 m equator, so
    # at zoom 18 and 51.2 deg a pixel is ~0.187 m and a 10 m path is ~53.4 px wide.
    m_per_px = math.cos(math.radians(51.2)) * 2 * math.pi * 6378137 / (512 * 2 ** 18)
    assert abs(w0 * 2 ** 18 - 10 / m_per_px) < 0.01


def test_ground_width_refuses_what_is_not_a_width():
    for value in (None, 0, -1, "x", float("nan"), float("inf")):
        assert sym.ground_width(value) is None


def test_a_line_measured_on_the_ground_uses_the_layers_own_latitude():
    line = built({"color": "#84d9ff", "line_width_m": 9.9842}, geom="MultiLineString")[0]
    assert line["paint"]["line-width"] == sym.ground_width(9.9842, 51.2)


def test_a_line_without_a_ground_width_is_unchanged():
    line = built({"color": "#84d9ff", "line_width": 3}, geom="MultiLineString")[0]
    assert line["paint"]["line-width"] == 3


def test_a_ground_border_gets_its_own_layer_and_width():
    line = built({"outline_color": "#673f20", "outline_width_m": 1.5})[1]
    assert line["paint"]["line-width"] == sym.ground_width(1.5, 51.2)


def test_no_usable_bbox_means_the_equator_rather_than_no_width():
    line = built({"color": "#000", "line_width_m": 5}, geom="MultiLineString", bbox=None)[0]
    assert line["paint"]["line-width"] == sym.ground_width(5, None)


# ── a class that has none of the layer's dash ────────────────────────────────────────────────────

def test_an_explicit_none_dash_means_no_dash_for_that_class():
    style = {"dash_pattern": [0.125, 0.8333], "lineType": "solid"}
    assert sym.dash_array(sym.class_style(style, {})) == [0.125, 0.8333]
    assert sym.dash_array(sym.class_style(style, {"dash_pattern": None})) is None


def test_a_dash_dot_pattern_draws_as_sent():
    assert sym.dash_array({"dash_pattern": [4.0, 2.0, 1.0, 2.0], "lineType": "solid"}) \
        == [4.0, 2.0, 1.0, 2.0]


# ── the twins agree ──────────────────────────────────────────────────────────────────────────────

def _js_list(path, name):
    text = (ROOT / path).read_text(encoding="utf-8")
    body = re.search(rf"export const {name} = \[(.*?)\]", text, re.S).group(1)
    return set(re.findall(r"'([a-z_A-Z]+)'", body))


def test_the_editor_reads_the_same_class_keys_as_the_portal():
    """The editor lacked `line_opacity` while the server had it, so a per-class line opacity the
    portal honoured was ignored in the preview. Pinned so the two lists cannot drift again."""
    assert _js_list("ui/src/lib/symbology.js", "CLASS_SHAPE_KEYS") == set(sym.CLASS_SHAPE_KEYS)


def test_everything_the_plugin_sends_per_class_the_portal_reads():
    text = (ROOT / "integrations/qgis-plugin/geodeploy_qgis/symbology.py").read_text(encoding="utf-8")
    body = re.search(r"^CLASS_SHAPE_KEYS = \((.*?)\)", text, re.S | re.M).group(1)
    plugin = set(re.findall(r'"([a-z_A-Z]+)"', body))
    assert plugin <= set(sym.CLASS_SHAPE_KEYS), plugin - set(sym.CLASS_SHAPE_KEYS)
