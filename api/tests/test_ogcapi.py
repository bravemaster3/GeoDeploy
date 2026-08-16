"""OGC API - Features (routers/ogcapi.py) — the standards surface other GIS tools read.

What is pinned here is the CONTRACT, not the SQL: the landing page/conformance/collections
documents, the public-only exposure rule (same `visibility='public'` opt-in as STAC), and the
paging/link shape of `/items`. Feature reads themselves hit PostGIS or DuckDB, so those two
backends are stubbed — the suite has neither, and the query code is exercised by the live
instance, not here.

The conformance list is asserted EXACTLY on purpose: over-claiming conformance is the specific bug
this module exists to avoid (spec-driven clients trust it and then break). If you implement a new
class, change the test in the same commit; never widen the list to make a test pass.
"""
import json

import pytest
from jose import jwt
from passlib.context import CryptContext

from geodeploy.config import get_settings
from geodeploy.models import User, VectorLayer
from geodeploy.routers import ogcapi

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
OWNER = 1

PUBLIC_PG, ORG_PG, PUBLIC_GP, PROCESSING = 10, 11, 12, 13
# Explicit uids so assertions are deterministic (the model would otherwise generate random ones).
UID_PG, UID_GP = "aaaa1111bbbb", "cccc2222dddd"


def _auth(uid=OWNER):
    tok = jwt.encode({"sub": str(uid)}, get_settings().secret_key, algorithm="HS256")
    return {"Authorization": f"Bearer {tok}"}


async def _seed(db):
    db.add(User(id=OWNER, email="o@example.com", name="O", hashed_password=_pwd.hash("pw"),
                is_admin=True, role="owner"))
    db.add(VectorLayer(id=PUBLIC_PG, uid=UID_PG, user_id=OWNER, name="Roads", table_name="roads",
                       schema_name="ext_schema", status="ready", storage_backend="postgis",
                       visibility="public", is_public=True, crs="EPSG:4326",
                       bbox=json.dumps([11.0, 57.0, 12.0, 58.0]), feature_count=42,
                       columns=json.dumps([{"name": "id", "type": "int"},
                                           {"name": "label", "type": "string"}]),
                       license="CC-BY-4.0", keywords="roads, sweden"))
    db.add(VectorLayer(id=ORG_PG, user_id=OWNER, name="Internal", table_name="internal",
                       schema_name="ext_schema", status="ready", storage_backend="postgis",
                       visibility="organization", is_public=False))
    db.add(VectorLayer(id=PUBLIC_GP, uid=UID_GP, user_id=OWNER, name="Parcels", table_name="parcels",
                       schema_name="ext_schema", status="ready", storage_backend="geoparquet",
                       s3_key="vectors/1/parcels/", visibility="public", is_public=True,
                       feature_count=7,
                       columns=json.dumps([{"name": "gid", "type": "int"}])))
    db.add(VectorLayer(id=PROCESSING, user_id=OWNER, name="Still ingesting", table_name="wip",
                       schema_name="ext_schema", status="processing", storage_backend="postgis",
                       visibility="public", is_public=True))
    await db.commit()


# ── Landing page, conformance ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_landing_page_links(client):
    r = await client.get("/api/ogc")
    assert r.status_code == 200
    rels = {l["rel"]: l["href"] for l in r.json()["links"]}
    assert rels["self"].endswith("/api/ogc")
    assert rels["conformance"].endswith("/api/ogc/conformance")
    assert rels["data"].endswith("/api/ogc/collections")
    assert "service-desc" in rels


@pytest.mark.asyncio
async def test_conformance_claims_only_what_is_implemented(client):
    r = await client.get("/api/ogc/conformance")
    assert r.status_code == 200
    assert r.json()["conformsTo"] == [
        "http://www.opengis.net/spec/ogcapi-features-1/1.0/conf/core",
        "http://www.opengis.net/spec/ogcapi-features-1/1.0/conf/geojson",
    ]


@pytest.mark.asyncio
async def test_public_surface_is_cors_open(client):
    """Any browser-based client (GeoLibre, a notebook, stac-browser) fetches this cross-origin."""
    r = await client.get("/api/ogc/collections")
    assert r.headers["access-control-allow-origin"] == "*"
    # Exactly ONE ACAO value — a duplicated header makes browsers reject the response outright.
    assert "," not in r.headers["access-control-allow-origin"]
    pre = await client.request("OPTIONS", "/api/ogc/collections")
    assert pre.status_code == 204
    assert pre.headers["access-control-allow-origin"] == "*"


# ── Collections: only public + ready layers ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_collections_lists_only_public_ready_layers(client, db):
    await _seed(db)
    r = await client.get("/api/ogc/collections")
    assert r.status_code == 200
    ids = {c["id"] for c in r.json()["collections"]}
    assert ids == {f"vector-{UID_PG}", f"vector-{UID_GP}"}


@pytest.mark.asyncio
@pytest.mark.parametrize("cid", [f"vector-{ORG_PG}", f"vector-{PROCESSING}", "vector-999", "nope"])
async def test_non_public_collections_404(client, db, cid):
    await _seed(db)
    assert (await client.get(f"/api/ogc/collections/{cid}")).status_code == 404
    assert (await client.get(f"/api/ogc/collections/{cid}/items")).status_code == 404


@pytest.mark.asyncio
async def test_collection_document(client, db):
    await _seed(db)
    # Requested by the LEGACY integer id (a link shared before uids existed) — it still resolves,
    # but the document reports the canonical uid.
    r = await client.get(f"/api/ogc/collections/vector-{PUBLIC_PG}")
    assert r.status_code == 200
    doc = r.json()
    assert doc["id"] == f"vector-{UID_PG}"
    assert doc["title"] == "Roads"
    assert doc["itemType"] == "feature"
    assert doc["extent"]["spatial"]["bbox"] == [[11.0, 57.0, 12.0, 58.0]]
    assert doc["license"] == "CC-BY-4.0"
    assert doc["keywords"] == ["roads", "sweden"]
    rels = {l["rel"] for l in doc["links"]}
    assert {"self", "items", "root", "describedby"} <= rels


# ── Items: paging + link shape (backends stubbed) ─────────────────────────────────────────────

def _feature(i):
    return {"type": "Feature", "id": i, "geometry": {"type": "Point", "coordinates": [11.0, 57.0]},
            "properties": {"id": i, "label": f"f{i}"}}


@pytest.mark.asyncio
async def test_items_paging_links(client, db, monkeypatch):
    await _seed(db)

    async def fake(layer, qb, limit, offset):
        return [_feature(offset + i) for i in range(limit)], 42

    monkeypatch.setattr(ogcapi, "_postgis_items", fake)
    r = await client.get(f"/api/ogc/collections/vector-{UID_PG}/items?limit=10&offset=10")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/geo+json")
    doc = r.json()
    assert doc["type"] == "FeatureCollection"
    assert doc["numberReturned"] == 10 and doc["numberMatched"] == 42
    assert "timeStamp" in doc
    rels = {l["rel"]: l["href"] for l in doc["links"]}
    assert "offset=20" in rels["next"] and "limit=10" in rels["next"]
    assert "offset=0" in rels["prev"]


@pytest.mark.asyncio
async def test_items_last_page_has_no_next(client, db, monkeypatch):
    await _seed(db)

    async def fake(layer, qb, limit, offset):
        return [_feature(0)], 1

    monkeypatch.setattr(ogcapi, "_postgis_items", fake)
    doc = (await client.get(f"/api/ogc/collections/vector-{UID_PG}/items?limit=10")).json()
    assert {l["rel"] for l in doc["links"]}.isdisjoint({"next", "prev"})
    assert doc["numberMatched"] == 1


@pytest.mark.asyncio
async def test_items_unknown_count_is_omitted_not_guessed(client, db, monkeypatch):
    """numberMatched is optional in OGC API - Features; a wrong one is worse than none."""
    await _seed(db)

    async def fake(layer, qb, limit, offset):
        return [_feature(i) for i in range(limit)], None

    monkeypatch.setattr(ogcapi, "_geoparquet_items", fake)
    doc = (await client.get(f"/api/ogc/collections/vector-{UID_GP}/items?limit=5")).json()
    assert "numberMatched" not in doc
    assert {l["rel"] for l in doc["links"]} >= {"next"}   # full page → there may be more


@pytest.mark.asyncio
async def test_bbox_is_parsed_and_passed_through(client, db, monkeypatch):
    await _seed(db)
    seen = {}

    async def fake(layer, qb, limit, offset):
        seen["bbox"], seen["limit"] = qb, limit
        return [], 0

    monkeypatch.setattr(ogcapi, "_postgis_items", fake)
    r = await client.get(
        f"/api/ogc/collections/vector-{UID_PG}/items?bbox=11,57,12,58&limit=99999")
    assert r.status_code == 200
    assert seen["bbox"] == [11.0, 57.0, 12.0, 58.0]
    assert seen["limit"] == ogcapi.MAX_LIMIT          # capped, never unbounded


@pytest.mark.asyncio
async def test_3d_bbox_drops_elevation(client, db, monkeypatch):
    await _seed(db)
    seen = {}

    async def fake(layer, qb, limit, offset):
        seen["bbox"] = qb
        return [], 0

    monkeypatch.setattr(ogcapi, "_postgis_items", fake)
    await client.get(f"/api/ogc/collections/vector-{UID_PG}/items?bbox=11,57,0,12,58,100")
    assert seen["bbox"] == [11.0, 57.0, 12.0, 58.0]


@pytest.mark.asyncio
@pytest.mark.parametrize("bbox", ["1,2,3", "a,b,c,d", "11,57,12"])
async def test_bad_bbox_400s(client, db, bbox):
    await _seed(db)
    r = await client.get(f"/api/ogc/collections/vector-{UID_PG}/items?bbox={bbox}")
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_single_feature(client, db, monkeypatch):
    await _seed(db)

    async def fake(layer, fid):
        return _feature(7) if fid == "7" else None

    monkeypatch.setattr(ogcapi, "_postgis_item", fake)
    r = await client.get(f"/api/ogc/collections/vector-{UID_PG}/items/7")
    assert r.status_code == 200
    assert {l["rel"] for l in r.json()["links"]} == {"self", "collection"}
    assert (await client.get(
        f"/api/ogc/collections/vector-{UID_PG}/items/8")).status_code == 404


# ── Cross-surface wiring: STAC advertises it, share links lead with it ────────────────────────

@pytest.mark.asyncio
async def test_stac_item_advertises_the_ogc_collection(client, db):
    await _seed(db)
    item = (await client.get(
        f"/api/stac/collections/vectors/items/vector-{UID_PG}")).json()
    assert item["assets"]["ogc-features"]["href"].endswith(
        f"/api/ogc/collections/vector-{UID_PG}/items")
    assert any(l["rel"] == "alternate" and "/api/ogc/" in l["href"] for l in item["links"])


@pytest.mark.asyncio
async def test_share_links_lead_with_ogc_features(client, db):
    await _seed(db)
    r = await client.get(f"/api/data/vector/{PUBLIC_PG}/links", headers=_auth())
    assert r.status_code == 200
    body = r.json()
    assert body["public"] is True
    ids = [l["id"] for l in body["links"]]
    assert {"ogc-features", "ogc-service", "ogc-items", "tilejson", "stac"} <= set(ids)

    # The PRIMARY link is the SERVICE, not the collection. It used to be the other way round, and a
    # desktop user followed it into a dead end: QGIS's "Add OGC API - Features Layer" connects to a
    # service and lists its collections, so a collection URL produces an empty list and no error.
    # The collection link stays (GDAL takes `OAPIF:<collection>` happily) but is not the one led with.
    primary = [l for l in body["links"] if l.get("primary")]
    assert [l["id"] for l in primary] == ["ogc-service"]
    service = next(l for l in body["links"] if l["id"] == "ogc-service")
    assert service["url"].endswith("/api/ogc")
    assert "QGIS" in service["tools"]
    collection = next(l for l in body["links"] if l["id"] == "ogc-features")
    assert "QGIS" not in collection["tools"], "labelling a collection for QGIS is what misled"


@pytest.mark.asyncio
async def test_a_tiled_geoparquet_layer_leads_with_pmtiles(client, db):
    """These are the BIG layers — that is why they are GeoParquet — and for them the honest first
    answer is the one that draws. QGIS opens the archive through Add Vector Layer in seconds, where
    OAPIF pages millions of features a screen at a time. Verified against a live instance: GDAL
    reports driver=PMTiles and reads features straight from the plain URL."""
    from geodeploy.services.share_links import vector_links

    class _Tiled:
        id, uid, name = 9, "abc123abc123", "big"
        storage_backend, pmtiles_key, tile_status = "geoparquet", "k.pmtiles", "ready"
        schema_name = table_name = columns = s3_key = None
        is_public, visibility = True, "public"

    links = vector_links(_Tiled(), "https://example.org")
    assert links[0]["id"] == "pmtiles", "the fastest path should be first, not buried"
    assert links[0]["primary"] is True
    assert "QGIS" in links[0]["tools"]
    # Case-insensitive: the hint capitalises the menu name for emphasis, and pinning the shouting
    # would make this a test of formatting rather than of content.
    assert "add vector layer" in links[0]["hint"].lower()   # the dialog that actually works
    assert "add vector tile layer" in links[0]["hint"].lower()  # …and the one that does not
    # Only ONE thing may wear the badge, or "recommended" means nothing.
    assert [l["id"] for l in links if l.get("primary")] == ["pmtiles"]
    # …and the feature service is still right there for attributes, which tiles cannot carry.
    assert "ogc-service" in [l["id"] for l in links]


@pytest.mark.asyncio
async def test_an_untiled_geoparquet_layer_still_leads_with_the_service(client, db):
    """No archive, no shortcut: OAPIF is the answer again."""
    from geodeploy.services.share_links import vector_links

    class _Untiled:
        id, uid, name = 10, "def456def456", "small"
        storage_backend, pmtiles_key, tile_status = "geoparquet", None, None
        schema_name = table_name = columns = s3_key = None
        is_public, visibility = True, "public"

    links = vector_links(_Untiled(), "https://example.org")
    assert [l["id"] for l in links if l.get("primary")] == ["ogc-service"]


@pytest.mark.asyncio
async def test_share_links_flag_a_non_public_layer(client, db):
    await _seed(db)
    body = (await client.get(f"/api/data/vector/{ORG_PG}/links", headers=_auth())).json()
    assert body["public"] is False      # UI turns this into "make it public first"


# ── Stable public ids ────────────────────────────────────────────────────────────────────────
# SQLite reuses an integer PK after the highest row is deleted, so a shared URL built from `id`
# can silently start returning a DIFFERENT dataset. Public identity is therefore the layer's uid;
# these tests are the contract. See models.new_uid + routers/common.by_ref.

@pytest.mark.asyncio
async def test_public_ids_are_uids_not_integer_pks(client, db):
    await _seed(db)
    cols = (await client.get("/api/ogc/collections")).json()["collections"]
    assert all(c["id"].split("-", 1)[1] not in {str(PUBLIC_PG), str(PUBLIC_GP)} for c in cols)
    item = (await client.get(
        f"/api/stac/collections/vectors/items/vector-{UID_PG}")).json()
    assert item["id"] == f"vector-{UID_PG}"
    # …and every advertised URL addresses the uid, never the row id.
    for asset in item["assets"].values():
        assert f"/vector-{PUBLIC_PG}" not in asset["href"]
        assert f"/data/vector/{PUBLIC_PG}/" not in asset["href"]


@pytest.mark.asyncio
@pytest.mark.parametrize("ref", ["{uid}", "vector-{uid}", "{pk}", "vector-{pk}"])
async def test_legacy_integer_links_keep_resolving(client, db, ref):
    """Links shared before the uid migration must not break — by_ref accepts both forms."""
    await _seed(db)
    cid = ref.format(uid=UID_PG, pk=PUBLIC_PG)
    if not cid.startswith("vector-"):
        cid = f"vector-{cid}"
    r = await client.get(f"/api/ogc/collections/{cid}")
    assert r.status_code == 200
    assert r.json()["id"] == f"vector-{UID_PG}"


@pytest.mark.asyncio
async def test_a_recycled_integer_id_cannot_serve_the_old_layers_url(client, db):
    """THE failure this design prevents. Delete a shared layer, let a NEW layer take its recycled
    integer id, and the URL published for the old one must NOT quietly return the new data."""
    await _seed(db)
    from sqlalchemy import select as sel
    from geodeploy.models import VectorLayer as VL

    old = (await db.execute(sel(VL).where(VL.id == PUBLIC_PG))).scalar_one()
    await db.delete(old)
    await db.commit()
    # A different dataset lands on the very same integer id (what SQLite does on the next insert).
    db.add(VL(id=PUBLIC_PG, uid="9999ffff8888", user_id=OWNER, name="Bedrock geology",
              table_name="bedrock", schema_name="ext_schema", status="ready",
              storage_backend="postgis", visibility="public", is_public=True))
    await db.commit()

    # The bookmarked uid URL 404s — honest — instead of serving Bedrock geology as "Roads".
    assert (await client.get(f"/api/ogc/collections/vector-{UID_PG}")).status_code == 404
    assert (await client.get(
        f"/api/stac/collections/vectors/items/vector-{UID_PG}")).status_code == 404
    # The new layer is reachable by its own uid.
    assert (await client.get(
        "/api/ogc/collections/vector-9999ffff8888")).json()["title"] == "Bedrock geology"


@pytest.mark.asyncio
async def test_uid_is_generated_for_new_layers(db):
    """Every creation path gets one from the model default — no route needs to remember."""
    from geodeploy.models import VectorLayer as VL
    from sqlalchemy import select as sel
    await _seed(db)
    db.add(VL(id=99, user_id=OWNER, name="Fresh", table_name="t99", schema_name="s",
              status="ready", storage_backend="postgis"))
    await db.commit()
    layer = (await db.execute(sel(VL).where(VL.id == 99))).scalar_one()
    assert layer.uid and len(layer.uid) == 12 and layer.uid != "99"


# ── The credential decides what exists ────────────────────────────────────────────────────────
# This surface was public-only whatever you sent with it. Someone holding an editor token could
# list a private layer everywhere else in GeoDeploy and be told it did not exist here — and since
# OAPIF is how QGIS adds a layer with full attributes, "add my own layer to QGIS" only worked if
# the layer had been published to the world first. Measured on a live instance before the fix: the
# token could see 14 layers; OAPIF offered 8, with or without it.

@pytest.mark.asyncio
async def test_a_token_widens_the_collection_list(client, db):
    await _seed(db)
    # By TITLE, not id: a layer seeded without an explicit uid gets a generated one, and the
    # document always reports the canonical uid rather than whatever was asked for.
    anon = {c["title"] for c in (await client.get("/api/ogc/collections")).json()["collections"]}
    owner = {c["title"] for c in
             (await client.get("/api/ogc/collections", headers=_auth())).json()["collections"]}
    assert "Internal" not in anon
    assert "Internal" in owner
    assert anon < owner, "a credential must only ever ADD to what is visible"


@pytest.mark.asyncio
async def test_a_private_collection_is_readable_with_a_token(client, db):
    await _seed(db)
    cid = f"vector-{ORG_PG}"
    assert (await client.get(f"/api/ogc/collections/{cid}")).status_code == 404
    r = await client.get(f"/api/ogc/collections/{cid}", headers=_auth())
    assert r.status_code == 200
    # Requested by the legacy integer id; answered with the canonical uid, as elsewhere.
    assert r.json()["title"] == "Internal"
    assert r.json()["id"].startswith("vector-")


@pytest.mark.asyncio
async def test_an_unready_layer_is_404_even_with_a_token(client, db):
    """Authentication widens visibility, not readiness — a half-ingested table has nothing to serve."""
    await _seed(db)
    r = await client.get(f"/api/ogc/collections/vector-{PROCESSING}", headers=_auth())
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_a_private_response_is_never_stored_by_a_shared_cache(client, db):
    """A CDN sits in front of these instances. Without `Vary`, the first authenticated response
    would be cached and then handed to anonymous callers — correct filtering undone by the cache."""
    await _seed(db)
    r = await client.get(f"/api/ogc/collections/vector-{ORG_PG}", headers=_auth())
    assert r.status_code == 200
    assert r.headers.get("vary", "").lower() == "authorization"
    cache = r.headers.get("cache-control", "")
    assert "private" in cache and "no-store" in cache


@pytest.mark.asyncio
async def test_the_collections_list_varies_on_authorization(client, db):
    await _seed(db)
    r = await client.get("/api/ogc/collections")
    assert r.headers.get("vary", "").lower() == "authorization"


def test_public_responses_stay_shared_cacheable():
    """The public path keeps its shared caching — that is what makes the anonymous catalog cheap,
    and nothing about it depends on a credential."""
    headers = ogcapi._cors(type("L", (), {"is_public": True})())
    assert "public" in headers["Cache-Control"] and headers["Vary"] == "Authorization"
