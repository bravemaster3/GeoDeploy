"""Portal card thumbnails — the snapshot captured in the editor at publish time.

The behaviours worth pinning are the ones that protect an EXISTING card: the file has a fixed name so
re-publishing overwrites instead of orphaning a file per publish, the stored URL carries a
cache-buster so the unchanged path still refreshes, and the public asset route serves that fixed name
without loosening its traversal allow-list.
"""
import json

import pytest
from jose import jwt
from passlib.context import CryptContext

from geodeploy.config import get_settings
from geodeploy.models import Portal, User

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _auth(uid=1):
    tok = jwt.encode({"sub": str(uid)}, get_settings().secret_key, algorithm="HS256")
    return {"Authorization": f"Bearer {tok}"}
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 4096      # >2 KB so it is not mistaken for a blank capture


async def _seed(db):
    db.add(User(id=1, email="o@example.com", name="O", hashed_password=_pwd.hash("pw"),
                is_admin=True, role="owner"))
    db.add(Portal(id=1, user_id=1, title="P", slug="p", access_type="public",
                  published=True, layer_configs=json.dumps([])))
    await db.commit()


async def test_upload_sets_thumbnail_url(client, db):
    await _seed(db)
    r = await client.post("/api/portals/1/thumbnail",
                          files={"file": ("thumbnail.webp", PNG, "image/webp")},
                          headers=_auth())
    assert r.status_code == 200
    url = r.json()["url"]
    # A unique PATH, not a fixed name with ?v=. The assets route serves `immutable` with a 24h
    # max-age, so a constant path let any cache that ignores query strings serve a stale image for a
    # day — which is exactly what happened in practice.
    import re as _re
    assert _re.fullmatch(r"/api/portals/1/assets/thumbnail-[0-9a-f]{12}\.webp", url), url

    # It must be persisted, not just returned — the card reads it off the portal.
    got = await client.get("/api/portals/1", headers=_auth())
    assert got.json()["thumbnail_url"] == url


async def test_republish_replaces_rather_than_accumulating(client, db):
    """Each capture gets its own path (so caches cannot serve a stale one), but the OLD file must be
    removed — otherwise every re-publish leaves an orphan behind forever."""
    await _seed(db)
    first = (await client.post("/api/portals/1/thumbnail",
                               files={"file": ("t.webp", PNG, "image/webp")},
                               headers=_auth())).json()["url"]
    second = (await client.post("/api/portals/1/thumbnail",
                                files={"file": ("t.webp", PNG + b"x", "image/webp")},
                                headers=_auth())).json()["url"]
    assert first != second                                  # a NEW path, so no cache can serve the old

    from geodeploy.config import get_settings
    import os
    d = f"{get_settings().data_dir}/portal_assets/1"
    files = sorted(os.listdir(d))
    assert len(files) == 1, files                           # the previous capture was removed
    assert files[0] == second.rsplit("/", 1)[-1]            # ...and the survivor is the current one


async def test_asset_route_serves_the_fixed_name(client, db):
    """The public route's filename allow-list is a traversal guard; it must admit this one name."""
    await _seed(db)
    url = (await client.post("/api/portals/1/thumbnail",
                             files={"file": ("t.webp", PNG, "image/webp")},
                             headers=_auth())).json()["url"]
    assert (await client.get(url)).status_code == 200


@pytest.mark.parametrize("name", ["../../etc/passwd", "thumbnail.php", "thumb.webp", "..%2Fx.webp",
                                  "thumbnail-.webp", "thumbnail-zzzzzzzzzzzz.webp"])
async def test_asset_route_still_rejects_everything_else(client, db, name):
    await _seed(db)
    assert (await client.get(f"/api/portals/1/assets/{name}")).status_code in (404, 400)


async def test_empty_upload_rejected(client, db):
    """A blank capture must not replace a good thumbnail with nothing."""
    await _seed(db)
    r = await client.post("/api/portals/1/thumbnail",
                          files={"file": ("t.webp", b"", "image/webp")}, headers=_auth())
    assert r.status_code == 400


async def test_thumbnail_defaults_to_none(client, db):
    """Portals published before this existed have no thumbnail and must still serialise."""
    await _seed(db)
    assert (await client.get("/api/portals/1", headers=_auth())).json()["thumbnail_url"] is None
