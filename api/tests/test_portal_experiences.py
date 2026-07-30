"""V-11 Template Experiences: the layout manifest resolves archetype defaults + per-portal overrides
(back-compat: no config → webmap), and layout_config + story round-trip through the portal API."""
import json
from pathlib import Path

from jose import jwt
from passlib.context import CryptContext

from geodeploy.config import get_settings
from geodeploy.models import Portal, User
from geodeploy.services.portal_generator import (
    resolve_layout, resolve_theme, build_theme_css, _vector_layers,
)

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _cfg(i):
    return {"layer_type": "vector", "layer_id": i, "style": {}, "opacity": 1.0, "visible": True}


# ── resolve_layout (the parity contract) ─────────────────────────────────────────────────────────

def test_resolve_layout_none_is_webmap():
    """Back-compat: absent config → the webmap shell (left docked layer list, right controls)."""
    r = resolve_layout(None)
    assert r["archetype"] == "webmap"
    assert r["regions"]["layerList"]["side"] == "left"
    assert r["regions"]["layerList"]["mode"] == "docked"
    assert r["regions"]["controls"]["position"] == "top-right"
    assert r["panels"]["layerCatalog"] is True and r["panels"]["story"] is False


def test_resolve_layout_unknown_archetype_falls_back_to_webmap():
    r = resolve_layout({"archetype": "nonsense"})
    assert r["archetype"] == "webmap"


def test_resolve_layout_unbuilt_archetypes_alias_to_webmap():
    """'webmap+catalog' is still unbuilt and must degrade to a working map, not a blank shell.

    'catalog' USED to be aliased here too — that was the placeholder, and it is why selecting the
    catalog experience appeared to do nothing. It is a real archetype now (see
    test_portal_layouts.py), so it is asserted NOT to alias away."""
    assert resolve_layout({"archetype": "webmap+catalog"})["archetype"] == "webmap"
    assert resolve_layout({"archetype": "catalog"})["archetype"] == "catalog"


def test_resolve_layout_storymap_defaults():
    r = resolve_layout({"archetype": "storymap"})
    assert r["archetype"] == "storymap"
    assert r["panels"]["story"] is True
    # The story map now exposes the layer list too — floating + collapsed, reachable from the toggle.
    assert r["panels"]["layerCatalog"] is True
    assert r["regions"]["layerList"]["mode"] == "floating"
    assert r["regions"]["layerList"]["collapsed"] is True
    assert r["regions"]["header"]["style"] == "minimal"


def test_resolve_layout_merges_overrides_onto_archetype():
    r = resolve_layout({"archetype": "webmap",
                        "regions": {"layerList": {"side": "right", "mode": "floating"},
                                    "controls": {"position": "bottom-left"}},
                        "panels": {"about": False}})
    assert r["regions"]["layerList"]["side"] == "right"      # override applied
    assert r["regions"]["layerList"]["mode"] == "floating"   # override applied
    assert r["regions"]["layerList"]["collapsed"] is False   # untouched default preserved
    assert r["regions"]["controls"]["position"] == "bottom-left"  # override applied
    assert r["panels"]["about"] is False                     # override applied
    assert r["panels"]["basemap"] is True                    # untouched default preserved


# ── R3 colour theme ──────────────────────────────────────────────────────────────────────────────

def test_resolve_theme_defaults_to_auto():
    assert resolve_theme(None)["mode"] == "auto"
    assert resolve_theme({"mode": "nonsense"})["mode"] == "auto"
    assert resolve_theme({"mode": "dark"})["mode"] == "dark"


def test_build_theme_css_valid_accent_and_font():
    css = build_theme_css({"accent": "#ff0000", "font": "serif", "storyBg": "#123456"})
    assert "--accent: #ff0000;" in css
    assert "--story-bg: #123456;" in css
    assert "font-family: Georgia" in css


def test_build_theme_css_rejects_unsafe_values():
    # A non-hex accent (CSS-injection attempt) is dropped, never emitted; unknown font ignored.
    css = build_theme_css({"accent": "red; } body { display:none", "font": "comic"})
    assert "display:none" not in css and "--accent" not in css and "font-family" not in css
    assert build_theme_css(None) == "" and build_theme_css({}) == ""


# ── raw-paint passthrough (GeoLibre import → generate_style) ──────────────────────────────────────

def test_vector_layers_raw_paint_passthrough():
    """style.maplibre.layers → one MapLibre layer per entry, each wired to the layer's Martin source
    + source-layer, ids suffixed so several sub-layers (fill + outline) can coexist."""
    from types import SimpleNamespace
    layer = SimpleNamespace(id=5, schema_name="u1", table_name="roads", geometry_type="polygon")
    cfg = {"opacity": 1.0, "style": {"maplibre": {"layers": [
        {"suffix": "fill", "type": "fill",
         "paint": {"fill-color": ["step", ["to-number", ["get", "x"], 0], "#000", 1, "#fff"]}},
        {"suffix": "line", "type": "line", "paint": {"line-color": "#333", "line-width": 2}},
    ]}}}
    out = _vector_layers("vector_5", layer, cfg)
    assert [l["type"] for l in out] == ["fill", "line"]
    assert out[0]["id"] == "vector-5-fill"
    assert out[0]["source"] == "vector_5" and out[0]["source-layer"] == "u1.roads"
    assert out[0]["paint"]["fill-color"][0] == "step"        # data-driven expression preserved


def test_vector_layers_without_passthrough_is_single():
    from types import SimpleNamespace
    layer = SimpleNamespace(id=6, schema_name="u1", table_name="pts", geometry_type="point")
    out = _vector_layers("vector_6", layer, {"opacity": 1.0, "style": {"color": "#abcdef", "radius": 7}})
    assert len(out) == 1 and out[0]["type"] == "symbol"      # falls back to the friendly-key builder


# ── API round-trip ──────────────────────────────────────────────────────────────────────────────

async def test_layout_and_story_roundtrip_via_put(client, db):
    db.add(User(id=1, email="e@x", name="E", hashed_password=_pwd.hash("pw"), role="editor"))
    db.add(Portal(id=7, user_id=1, title="P", slug="s7", published=False,
                  layer_configs=json.dumps([_cfg(1)])))
    await db.commit()
    h = {"Authorization": f"Bearer {jwt.encode({'sub': '1'}, get_settings().secret_key, algorithm='HS256')}"}

    layout = {"archetype": "storymap", "regions": {"layerList": {"side": "right"}}}
    story = {"sections": [{"id": "a1", "title": "Intro", "body": "Hello", "image": "/portal-assets/1/x.png",
                           "view": {"center": [10, 20], "zoom": 5.0, "bearing": 0, "pitch": 0},
                           "layers": {"vector:1": True}}]}
    theme = {"mode": "dark", "accent": "#059669", "font": "serif"}
    assert (await client.put("/api/portals/7", headers=h,
                             json={"layout_config": layout, "story": story, "theme": theme})).status_code == 200
    got = (await client.get("/api/portals/7", headers=h)).json()
    assert got["layout_config"] == layout
    assert got["story"] == story
    assert got["theme"] == theme


async def test_preview_authz_denies_anonymous(client):
    """R2: the preview bundles (/portals/_preview/{id}/) are logged-in-only — the nginx auth_request
    target must 401 an anonymous (no session cookie) request."""
    r = await client.get("/api/portals/preview-authz")
    assert r.status_code == 401


async def test_geolibre_preview_endpoint(client, db):
    """POST /interop/geolibre/preview parses + translates a `.geolibre.json` and returns the import
    plan preview (no ingestion, geojson never echoed back)."""
    db.add(User(id=1, email="e@x", name="E", hashed_password=_pwd.hash("pw"), role="editor"))
    await db.commit()
    h = {"Authorization": f"Bearer {jwt.encode({'sub': '1'}, get_settings().secret_key, algorithm='HS256')}"}
    project = json.loads((Path(__file__).parent / "fixtures" / "sample.geolibre.json").read_text(encoding="utf-8"))

    r = await client.post("/api/interop/geolibre/preview", headers=h, json=project)
    assert r.status_code == 200
    body = r.json()
    assert body["portal"]["title"] == "Interop Spike Project"
    by_name = {l["name"]: l for l in body["layers"]}
    assert by_name["Elevation (COG)"]["target"] == "raster"
    assert by_name["GPS track (3D-Z)"]["render_mode"] == "elevation3d"
    assert by_name["Districts (single)"]["feature_count"] == 1
    assert "geojson" not in by_name["Districts (single)"]     # never echoed back
    # top-first order: GeoLibre's last layer (XYZ aerial) leads the returned list
    assert body["layers"][0]["name"] == "Aerial (XYZ)"


async def test_geolibre_preview_rejects_non_project(client, db):
    db.add(User(id=1, email="e@x", name="E", hashed_password=_pwd.hash("pw"), role="editor"))
    await db.commit()
    h = {"Authorization": f"Bearer {jwt.encode({'sub': '1'}, get_settings().secret_key, algorithm='HS256')}"}
    r = await client.post("/api/interop/geolibre/preview", headers=h, json={"foo": "bar"})
    assert r.status_code == 400


async def test_geolibre_preview_requires_auth(client):
    r = await client.post("/api/interop/geolibre/preview", json={"version": "0.1.0"})
    assert r.status_code in (401, 403)


async def test_portal_defaults_have_no_layout_or_story(client, db):
    """A portal that never set a layout/story returns null for both (→ webmap at publish)."""
    db.add(User(id=1, email="e@x", name="E", hashed_password=_pwd.hash("pw"), role="editor"))
    db.add(Portal(id=8, user_id=1, title="P", slug="s8", published=False,
                  layer_configs=json.dumps([_cfg(1)])))
    await db.commit()
    h = {"Authorization": f"Bearer {jwt.encode({'sub': '1'}, get_settings().secret_key, algorithm='HS256')}"}
    got = (await client.get("/api/portals/8", headers=h)).json()
    assert got["layout_config"] is None
    assert got["story"] is None
