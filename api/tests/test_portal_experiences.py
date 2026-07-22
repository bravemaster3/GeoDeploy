"""V-11 Template Experiences: the layout manifest resolves archetype defaults + per-portal overrides
(back-compat: no config → webmap), and layout_config + story round-trip through the portal API."""
import json

from jose import jwt
from passlib.context import CryptContext

from geodeploy.config import get_settings
from geodeploy.models import Portal, User
from geodeploy.services.portal_generator import resolve_layout, resolve_theme, build_theme_css

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
    assert r["regions"]["controls"]["side"] == "right"
    assert r["panels"]["layerCatalog"] is True and r["panels"]["story"] is False


def test_resolve_layout_unknown_archetype_falls_back_to_webmap():
    r = resolve_layout({"archetype": "nonsense"})
    assert r["archetype"] == "webmap"


def test_resolve_layout_dropped_archetypes_alias_to_webmap():
    """The Phase-1 'catalog'/'webmap+catalog' archetypes were removed → they resolve to webmap."""
    assert resolve_layout({"archetype": "catalog"})["archetype"] == "webmap"
    assert resolve_layout({"archetype": "webmap+catalog"})["archetype"] == "webmap"


def test_resolve_layout_storymap_defaults():
    r = resolve_layout({"archetype": "storymap"})
    assert r["archetype"] == "storymap"
    assert r["panels"]["story"] is True
    assert r["panels"]["layerCatalog"] is False   # story hides the layer-list catalog
    assert r["regions"]["header"]["style"] == "minimal"


def test_resolve_layout_merges_overrides_onto_archetype():
    r = resolve_layout({"archetype": "webmap",
                        "regions": {"layerList": {"side": "right", "mode": "floating"},
                                    "controls": {"side": "left"}},
                        "panels": {"about": False}})
    assert r["regions"]["layerList"]["side"] == "right"      # override applied
    assert r["regions"]["layerList"]["mode"] == "floating"   # override applied
    assert r["regions"]["layerList"]["collapsed"] is False   # untouched default preserved
    assert r["regions"]["controls"]["side"] == "left"        # override applied
    assert r["panels"]["about"] is False                     # override applied
    assert r["panels"]["basemap"] is True                    # untouched default preserved


# ── R3 colour theme ──────────────────────────────────────────────────────────────────────────────

def test_resolve_theme_defaults_to_auto():
    assert resolve_theme(None)["mode"] == "auto"
    assert resolve_theme({"mode": "nonsense"})["mode"] == "auto"
    assert resolve_theme({"mode": "dark"})["mode"] == "dark"


def test_build_theme_css_valid_accent_and_font():
    css = build_theme_css({"accent": "#ff0000", "font": "serif"})
    assert "--accent: #ff0000;" in css
    assert "font-family: Georgia" in css


def test_build_theme_css_rejects_unsafe_values():
    # A non-hex accent (CSS-injection attempt) is dropped, never emitted; unknown font ignored.
    css = build_theme_css({"accent": "red; } body { display:none", "font": "comic"})
    assert "display:none" not in css and "--accent" not in css and "font-family" not in css
    assert build_theme_css(None) == "" and build_theme_css({}) == ""


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
