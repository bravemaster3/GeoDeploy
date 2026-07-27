"""GeoLibre `.geolibre.json` → GeoDeploy import plan (Front 1 spike).

Pure translation tests — no DB, no services, no network (safe to run anywhere).
Proves each render path the importer must get right: single / graduated /
categorized vector color, attribute extrusion, 3D-Z detection, COG raster, and
external XYZ tiles, plus view/story/source-identity mapping.
"""
import json
from pathlib import Path

import pytest

from geodeploy.services.geolibre_import import (
    import_project,
    parse_geolibre_project,
)

FIXTURE = Path(__file__).parent / "fixtures" / "sample.geolibre.json"


@pytest.fixture(scope="module")
def plan():
    return import_project(FIXTURE.read_text(encoding="utf-8"))


def _layer(plan, name):
    return next(l for l in plan["layers"] if l["name"] == name)


# ── parse / validation ────────────────────────────────────────────────────────

def test_parse_rejects_non_project():
    with pytest.raises(ValueError):
        parse_geolibre_project(json.dumps({"foo": "bar"}))


def test_parse_rejects_unsupported_major_version():
    with pytest.raises(ValueError):
        parse_geolibre_project(json.dumps({"version": "9.0.0", "name": "x", "mapView": {}}))


def test_parse_accepts_dict_or_text():
    d = {"version": "0.1.0", "name": "x", "mapView": {"center": [0, 0], "zoom": 1}}
    assert parse_geolibre_project(d)["name"] == "x"
    assert parse_geolibre_project(json.dumps(d))["name"] == "x"


# ── portal-level mapping ──────────────────────────────────────────────────────

def test_portal_view_basemap_and_story(plan):
    portal = plan["portal"]
    assert portal["title"] == "Interop Spike Project"
    assert portal["view"]["center"] == [12.57, 55.68]
    assert portal["view"]["pitch"] == 45  # 3D tilt carried through
    assert portal["basemap"]["style_url"].startswith("https://tiles.openfreemap.org")
    assert portal["story"]["sections"][0]["title"] == "Copenhagen"
    # onChapterEnter opacity:1 → layer visible in that section
    assert portal["story"]["sections"][0]["layers"]["lyr-graduated-poly"] is True


def test_every_layer_carries_source_identity_for_writeback(plan):
    for lyr in plan["layers"]:
        si = lyr["source_identity"]
        assert si["origin"] == "geolibre" and si["geolibre_layer_id"] and si["project"]


# ── vector: single symbology ──────────────────────────────────────────────────

def test_single_polygon_fill_paint(plan):
    lyr = _layer(plan, "Districts (single)")
    assert lyr["target"] == "vector" and lyr["render_mode"] == "2d"
    fill = next(m for m in lyr["maplibre_layers"] if m["type"] == "fill")
    assert fill["paint"]["fill-color"] == "#3b82f6"
    assert fill["paint"]["fill-opacity"] == pytest.approx(0.6)  # fillOpacity * opacity(1)


# ── vector: graduated (numeric → step expression) ─────────────────────────────

def test_graduated_polygon_step_expression(plan):
    lyr = _layer(plan, "Population (graduated)")
    fill = next(m for m in lyr["maplibre_layers"] if m["type"] == "fill")
    expr = fill["paint"]["fill-color"]
    assert expr[0] == "step"
    assert expr[1] == ["to-number", ["get", "pop"], 0]  # input, first break value
    assert expr[2] == "#f7fbff"                          # color for < first break
    assert expr[3:] == [100, "#6baed6", 1000, "#08306b"]
    # opacity folds the layer's 0.9 into fillOpacity 0.7
    assert fill["paint"]["fill-opacity"] == pytest.approx(0.63)


# ── vector: categorized points (match expression) ─────────────────────────────

def test_categorized_points_match_expression(plan):
    lyr = _layer(plan, "Stations (categorized)")
    circle = next(m for m in lyr["maplibre_layers"] if m["type"] == "circle")
    expr = circle["paint"]["circle-color"]
    assert expr[:2] == ["match", ["get", "kind"]]
    assert expr[2:6] == ["bus", "#e6194b", "rail", "#3cb44b"]
    assert expr[-1] == "#888888"  # catch-all = fillColor


# ── vector: attribute extrusion (native fill-extrusion) ───────────────────────

def test_extruded_polygon_fill_extrusion(plan):
    lyr = _layer(plan, "Buildings (extruded)")
    assert lyr["render_mode"] == "extrusion"
    ext = lyr["maplibre_layers"][0]
    assert ext["type"] == "fill-extrusion"
    assert ext["paint"]["fill-extrusion-height"] == ["*", ["to-number", ["get", "height_m"], 0], 1]
    assert ext["paint"]["fill-extrusion-base"] == 0


# ── vector: 3D-Z elevation (deck path) ────────────────────────────────────────

def test_z_track_is_elevation3d(plan):
    lyr = _layer(plan, "GPS track (3D-Z)")
    assert lyr["render_mode"] == "elevation3d"
    assert lyr["has_z"] is True
    assert lyr["maplibre_layers"] == []          # rendered by deck, not MapLibre
    assert lyr["elevation"]["vertical_scale"] == 2


# ── raster: COG → TiTiler style ───────────────────────────────────────────────

def test_cog_raster_style(plan):
    lyr = _layer(plan, "Elevation (COG)")
    assert lyr["target"] == "raster" and lyr["render_mode"] == "raster"
    assert lyr["source"]["url"].endswith("dem.tif")
    rs = lyr["raster_style"]
    assert rs["colormap"] == "terrain"
    assert rs["rescale"] == [0, 400]
    assert rs["nodata"] == -9999
    assert rs["paint"]["raster-opacity"] == pytest.approx(0.8)
    assert rs["paint"]["raster-contrast"] == pytest.approx(0.1)


# ── external: XYZ tiles ───────────────────────────────────────────────────────

def test_xyz_tiles_external(plan):
    lyr = _layer(plan, "Aerial (XYZ)")
    assert lyr["target"] == "external" and lyr["render_mode"] == "tiles"
    assert lyr["source"]["tiles"][0].startswith("https://tiles.example.com/aerial/")
    assert lyr["source"]["tile_size"] == 256


# ── warnings surface, never silently drop ─────────────────────────────────────

def test_zorder_caveat_is_warned(plan):
    assert any("z-order" in w.lower() for w in plan["warnings"])
