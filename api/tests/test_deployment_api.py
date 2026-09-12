"""The deployment endpoints, over HTTP: who may call them, what they refuse, and what they answer.

`test_deployment.py` covers the service's logic in isolation. This covers the parts only reachable
through the app — the permission boundary, the domain validation that guards a string destined for
somebody's web-server config, and the shape the dashboard actually consumes.

The DNS states get their own tests because they are the ones with a WAITING PERIOD in them: telling
"not propagated yet" apart from "pointing at the wrong machine" is the entire reason that endpoint is
separate from Verify, and collapsing them would send an operator to undo a setup that was fine.
"""
from jose import jwt
from passlib.context import CryptContext

from geodeploy.config import get_settings
from geodeploy.models import User
from geodeploy.services import deployment

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
OWNER, ADMIN, EDITOR = 1, 2, 3


def _h(uid):
    return {"Authorization": f"Bearer {jwt.encode({'sub': str(uid)}, get_settings().secret_key, algorithm='HS256')}"}


async def _seed(db):
    for uid, role in ((OWNER, "owner"), (ADMIN, "admin"), (EDITOR, "editor")):
        db.add(User(id=uid, email=f"u{uid}@e.com", name=f"U{uid}", hashed_password=_pwd.hash("pw"),
                    is_admin=role in ("admin", "owner"), role=role))
    await db.commit()


# ── Who may ask ───────────────────────────────────────────────────────────────────────────────────

async def test_deployment_is_owner_only(client, db):
    """Not merely admin-only. It reports the instance's network position and generates the config an
    attacker would want in order to intercept it."""
    await _seed(db)
    assert (await client.get("/api/admin/deployment", headers=_h(EDITOR))).status_code == 403
    assert (await client.get("/api/admin/deployment", headers=_h(ADMIN))).status_code == 403
    assert (await client.get("/api/admin/deployment", headers=_h(OWNER))).status_code == 200


async def test_deployment_needs_authentication_at_all(client, db):
    await _seed(db)
    assert (await client.get("/api/admin/deployment")).status_code in (401, 403)


async def test_the_other_two_are_owner_only_too(client, db):
    await _seed(db)
    for path, body in (("/api/admin/deployment/verify", {"domain": "maps.example.org"}),
                       ("/api/admin/deployment/check-dns", {"domain": "maps.example.org"})):
        assert (await client.post(path, headers=_h(EDITOR), json=body)).status_code == 403
    assert (await client.get("/api/admin/deployment/proxy-config?domain=maps.example.org",
                             headers=_h(EDITOR))).status_code == 403


# ── What it answers ───────────────────────────────────────────────────────────────────────────────

async def test_the_payload_carries_what_the_panel_renders(client, db, monkeypatch):
    await _seed(db)
    monkeypatch.setattr(deployment, "server_ip",
                        lambda: {"outbound": "10.0.0.5", "public": "203.0.113.10", "differs": True})
    body = (await client.get("/api/admin/deployment", headers=_h(OWNER))).json()
    for key in ("intent", "reality", "observed", "verdict", "local_url", "flavors", "server_ip"):
        assert key in body, key
    assert body["intent"]["port"]            # defaults apply even with no publish keys in .env
    assert body["verdict"]["level"] in ("ok", "warning", "critical")
    assert body["server_ip"]["public"] == "203.0.113.10"


async def test_the_observed_origin_is_this_request(client, db):
    """Not a stored setting — the whole point is that it reflects how the caller actually arrived."""
    await _seed(db)
    body = (await client.get("/api/admin/deployment", headers={
        **_h(OWNER), "Host": "maps.example.org", "X-Forwarded-Proto": "https"})).json()
    assert body["observed"]["origin"] == "https://maps.example.org"
    assert body["observed"]["is_domain"] is True


# ── The domain, which ends up inside somebody's web-server config ─────────────────────────────────

BAD_DOMAINS = [
    "",                                   # empty
    "localhost",                          # no dot
    "no spaces.org",                      # space
    "maps.example.org; rm -rf /",         # shell
    "maps.example.org\nserver_name evil",  # newline — an injected nginx directive
    "-leading.example.org",
    "under_score.example.org",
    "a" * 70 + ".example.org",            # label over 63 characters
    "http://maps.example.org",            # a URL, not a hostname
    "maps.example.org/../etc",
]


async def test_every_endpoint_refuses_a_domain_that_is_not_one(client, db):
    await _seed(db)
    for bad in BAD_DOMAINS:
        r = await client.post("/api/admin/deployment/check-dns", headers=_h(OWNER), json={"domain": bad})
        assert r.status_code == 400, f"check-dns accepted {bad!r}"
        r = await client.post("/api/admin/deployment/verify", headers=_h(OWNER), json={"domain": bad})
        assert r.status_code == 400, f"verify accepted {bad!r}"
        if bad:
            r = await client.get("/api/admin/deployment/proxy-config", headers=_h(OWNER),
                                 params={"domain": bad, "flavor": "nginx"})
            assert r.status_code == 400, f"proxy-config accepted {bad!r}"


async def test_a_trailing_dot_and_capitals_are_accepted_and_normalised(client, db, monkeypatch):
    """`MAPS.Example.ORG.` is the same name. Rejecting it would be pedantry the operator has to
    debug, and passing it through unchanged would put a stray dot in their nginx config."""
    await _seed(db)
    monkeypatch.setattr(deployment, "resolve_domain", lambda d: [])
    monkeypatch.setattr(deployment, "server_ip", lambda: {"outbound": None, "public": None, "differs": False})
    r = await client.post("/api/admin/deployment/check-dns", headers=_h(OWNER),
                          json={"domain": "MAPS.Example.ORG."})
    assert r.status_code == 200
    assert "maps.example.org" in r.json()["detail"]


async def test_an_unknown_proxy_flavor_is_refused(client, db):
    await _seed(db)
    r = await client.get("/api/admin/deployment/proxy-config", headers=_h(OWNER),
                         params={"domain": "maps.example.org", "flavor": "../../etc/passwd"})
    assert r.status_code == 400


# ── The DNS states ────────────────────────────────────────────────────────────────────────────────

async def _dns(client, monkeypatch, resolved, ours):
    monkeypatch.setattr(deployment, "resolve_domain", lambda d: resolved)
    monkeypatch.setattr(deployment, "server_ip", lambda: ours)
    r = await client.post("/api/admin/deployment/check-dns", headers=_h(OWNER),
                          json={"domain": "maps.example.org"})
    assert r.status_code == 200
    return r.json()


HERE = {"outbound": "203.0.113.10", "public": "203.0.113.10", "differs": False}


async def test_dns_not_created_yet_says_wait(client, db, monkeypatch):
    await _seed(db)
    body = await _dns(client, monkeypatch, [], HERE)
    assert body["state"] == "unresolved"
    assert "wait" in body["fix"].lower()


async def test_dns_pointing_here_is_ok(client, db, monkeypatch):
    await _seed(db)
    body = await _dns(client, monkeypatch, ["203.0.113.10"], HERE)
    assert body["state"] == "ok" and "fix" not in body


async def test_dns_pointing_somewhere_else_says_so(client, db, monkeypatch):
    await _seed(db)
    body = await _dns(client, monkeypatch, ["198.51.100.7"], HERE)
    assert body["state"] == "elsewhere"
    assert "198.51.100.7" in body["detail"] and "203.0.113.10" in body["detail"]


async def test_cloudflare_is_recognised_rather_than_called_wrong(client, db, monkeypatch):
    """A proxied record resolves to Cloudflare, not to the server, and that is CORRECT. Reporting it
    as a misconfiguration would send the operator to undo a working setup."""
    await _seed(db)
    body = await _dns(client, monkeypatch, ["104.21.5.9", "172.67.1.2"], HERE)
    assert body["state"] == "proxied"
    assert "certbot" in body["fix"].lower() or "challenge" in body["fix"].lower()


async def test_a_server_behind_nat_still_gets_an_answer(client, db, monkeypatch):
    """`public` is unknown; we must not then claim the record is wrong — we simply cannot tell."""
    await _seed(db)
    body = await _dns(client, monkeypatch, ["203.0.113.10"],
                      {"outbound": "10.0.0.5", "public": None, "differs": False})
    assert body["state"] == "elsewhere"          # honest: it does not match what we know of ourselves
    assert body["expected"] == "10.0.0.5"


# ── Whoami: unauthenticated on purpose ────────────────────────────────────────────────────────────

async def test_whoami_needs_no_credentials(client):
    """It has to work unauthenticated: Verify fetches it THROUGH the new public domain, and sending
    credentials to an address that is still under test is exactly what we must not do."""
    r = await client.get("/api/public/whoami", headers={"Host": "maps.example.org",
                                                        "X-Forwarded-Proto": "https"})
    assert r.status_code == 200
    body = r.json()
    assert body["geodeploy"] is True
    assert body["origin"] == "https://maps.example.org"


async def test_whoami_echoes_only_the_fields_it_defines(client):
    """It must not become a reflector. Nothing a caller supplies comes back except the host, which is
    already echoed by every redirect on the internet."""
    r = await client.get("/api/public/whoami", headers={
        "Host": "maps.example.org", "X-Evil": "<script>alert(1)</script>",
        "User-Agent": "reflect-me", "X-Forwarded-For": "1.2.3.4"})
    body = r.json()
    assert set(body) == {"geodeploy", "instance", "scheme", "host", "origin"}
    assert "reflect-me" not in r.text and "script" not in r.text and "1.2.3.4" not in r.text


async def test_the_instance_id_is_stable_and_not_the_secret(client):
    """Verify compares this to know the domain reaches THIS GeoDeploy. It has to be stable across
    calls and must not leak the key it is derived from."""
    a = (await client.get("/api/public/whoami")).json()["instance"]
    b = (await client.get("/api/public/whoami")).json()["instance"]
    assert a == b and len(a) == 16
    assert get_settings().secret_key not in a
