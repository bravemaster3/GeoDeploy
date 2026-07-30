"""The catalog archetype's live feed: GET /api/portals/{slug}/catalog.

This route is UNAUTHENTICATED, so the tests that matter are the ones about what it refuses. It exists
only so a `catalog` portal scoped to "all public" can list instance-wide datasets without a
re-publish; it must not become a general "enumerate every layer" endpoint. Three gates, all pinned
here: the portal must be published, its archetype must be `catalog`, and its scope must be `public`.

The fourth gate is the one with teeth: only `visibility == "public"` layers may appear. A published
portal is browsed anonymously, so an organization-visible layer leaking into this list would expose
internal data to the internet.
"""
import json

from passlib.context import CryptContext

from geodeploy.models import Portal, RasterLayer, User, VectorLayer

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

CATALOG_PUBLIC = {"archetype": "catalog", "regions": {"catalog": {"scope": "public"}}}
CATALOG_OWN = {"archetype": "catalog"}                       # scope defaults to "portal"


async def _seed(db, *, layout, published=True, slug="cat"):
    db.add(User(id=1, email="o@example.com", name="O", hashed_password=_pwd.hash("pw"),
                is_admin=True, role="owner"))
    db.add(Portal(id=1, user_id=1, title="Catalog", slug=slug, access_type="public",
                  published=published, layer_configs=json.dumps([]),
                  layout_config=json.dumps(layout) if layout is not None else None))
    # One layer per visibility, so a leak is visible by name rather than by count alone.
    for lid, vis in ((1, "public"), (2, "organization"), (3, "private")):
        db.add(VectorLayer(id=lid, user_id=1, name=f"vec-{vis}", visibility=vis, status="ready",
                           schema_name="gd", table_name=f"t{lid}", geometry_type="Point"))
    db.add(RasterLayer(id=1, user_id=1, name="ras-public", visibility="public", status="ready",
                       s3_key="rasters/1/a.tif"))
    db.add(RasterLayer(id=2, user_id=1, name="ras-private", visibility="private", status="ready",
                       s3_key="rasters/1/b.tif"))
    await db.commit()


async def test_lists_only_public_layers(client, db):
    """The gate with teeth. Organization/private layers must never reach an anonymous caller."""
    await _seed(db, layout=CATALOG_PUBLIC)
    r = await client.get("/api/portals/cat/catalog")
    assert r.status_code == 200
    names = {rec["name"] for rec in r.json()["records"]}
    assert names == {"vec-public", "ras-public"}
    assert not any("organization" in n or "private" in n for n in names)


async def test_records_carry_both_kinds(client, db):
    await _seed(db, layout=CATALOG_PUBLIC)
    recs = (await client.get("/api/portals/cat/catalog")).json()["records"]
    assert {r["kind"] for r in recs} == {"vector", "raster"}
    # layer_id is what lets a card join to a map layer — its absence would silently disable the UI.
    assert all(r.get("layer_id") is not None for r in recs)


async def test_portal_scoped_catalog_has_no_feed(client, db):
    """Default scope is "portal": the records are baked into the bundle, so there is nothing to
    serve here and no reason to expose one."""
    await _seed(db, layout=CATALOG_OWN)
    assert (await client.get("/api/portals/cat/catalog")).status_code == 404


async def test_non_catalog_portal_has_no_feed(client, db):
    """A webmap must not become an instance-wide layer listing just by being published."""
    await _seed(db, layout={"archetype": "webmap"})
    assert (await client.get("/api/portals/cat/catalog")).status_code == 404


async def test_no_layout_config_has_no_feed(client, db):
    """Every portal created before layouts existed has layout_config = None → resolves to webmap."""
    await _seed(db, layout=None)
    assert (await client.get("/api/portals/cat/catalog")).status_code == 404


async def test_unpublished_catalog_has_no_feed(client, db):
    await _seed(db, layout=CATALOG_PUBLIC, published=False)
    assert (await client.get("/api/portals/cat/catalog")).status_code == 404


async def test_unknown_slug_404s(client, db):
    await _seed(db, layout=CATALOG_PUBLIC)
    assert (await client.get("/api/portals/nope/catalog")).status_code == 404
