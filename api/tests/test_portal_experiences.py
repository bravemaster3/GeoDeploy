"""V-11 Template Experiences: the layout manifest resolves archetype defaults + per-portal overrides
(back-compat: no config → webmap), and layout_config + story round-trip through the portal API."""
import json

from jose import jwt
from passlib.context import CryptContext

from geodeploy.config import get_settings
from geodeploy.models import Portal, User
from geodeploy.services.portal_generator import resolve_layout

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _cfg(i):
    return {"layer_type": "vector", "layer_id": i, "style": {}, "opacity": 1.0, "visible": True}


# ── resolve_layout (the parity contract) ─────────────────────────────────────────────────────────

def test_resolve_layout_none_is_webmap():
    """Back-compat: absent config → the pre-V-11 webmap shell (left docked sidebar, full panels)."""
    r = resolve_layout(None)
    assert r["archetype"] == "webmap"
    assert r["regions"]["sidebar"]["side"] == "left"
    assert r["regions"]["layerList"]["mode"] == "docked"
    assert r["panels"]["layerCatalog"] is True and r["panels"]["story"] is False


def test_resolve_layout_unknown_archetype_falls_back_to_webmap():
    r = resolve_layout({"archetype": "nonsense"})
    assert r["archetype"] == "webmap"


def test_resolve_layout_storymap_defaults():
    r = resolve_layout({"archetype": "storymap"})
    assert r["archetype"] == "storymap"
    assert r["panels"]["story"] is True
    assert r["panels"]["layerCatalog"] is False   # story hides the sidebar catalog
    assert r["regions"]["header"]["style"] == "minimal"


def test_resolve_layout_merges_overrides_onto_archetype():
    r = resolve_layout({"archetype": "catalog",
                        "regions": {"sidebar": {"side": "right"}},
                        "panels": {"about": False}})
    assert r["archetype"] == "catalog"
    assert r["regions"]["sidebar"]["side"] == "right"        # override applied
    assert r["regions"]["sidebar"]["collapsed"] is False     # untouched default preserved
    assert r["panels"]["about"] is False                     # override applied
    assert r["panels"]["basemap"] is True                    # untouched default preserved


# ── API round-trip ──────────────────────────────────────────────────────────────────────────────

async def test_layout_and_story_roundtrip_via_put(client, db):
    db.add(User(id=1, email="e@x", name="E", hashed_password=_pwd.hash("pw"), role="editor"))
    db.add(Portal(id=7, user_id=1, title="P", slug="s7", published=False,
                  layer_configs=json.dumps([_cfg(1)])))
    await db.commit()
    h = {"Authorization": f"Bearer {jwt.encode({'sub': '1'}, get_settings().secret_key, algorithm='HS256')}"}

    layout = {"archetype": "storymap", "regions": {"sidebar": {"side": "right"}}}
    story = {"sections": [{"id": "a1", "title": "Intro", "body": "Hello",
                           "view": {"center": [10, 20], "zoom": 5.0, "bearing": 0, "pitch": 0},
                           "layers": {"vector:1": True}}]}
    assert (await client.put("/api/portals/7", headers=h,
                             json={"layout_config": layout, "story": story})).status_code == 200
    got = (await client.get("/api/portals/7", headers=h)).json()
    assert got["layout_config"] == layout
    assert got["story"] == story


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
