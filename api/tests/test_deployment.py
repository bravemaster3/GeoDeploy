"""Where GeoDeploy thinks it is published, and the reverse-proxy configuration it hands out.

The properties worth pinning are the ones whose failures are SILENT — where nothing errors, the
dashboard looks fine, and the damage shows up days later as links nobody can open:

* the defaults must reproduce a pre-existing install exactly (no publish keys in `.env` ⇒
  `0.0.0.0:80`, byte-for-byte what the compose file said before the variables existed);
* a broken reverse proxy and an SSH tunnel both present as `Host: 127.0.0.1:8080`, and calling one
  the other is worse than saying nothing;
* every directive in the generated nginx block is there because its absence is a real failure —
  most sharply `client_max_body_size`, whose 1 MB default in the OUTER proxy shadows our 11G and
  413s every upload.
"""
import types

import pytest

from geodeploy.services import deployment


# ── Intent: the back-compatibility contract ───────────────────────────────────────────────────────

def _intent_from(monkeypatch, env):
    monkeypatch.setattr(deployment.envfile, "read_all", lambda path=None: env)
    return deployment.read_intent()


def test_an_env_without_the_keys_is_still_port_80_on_every_interface(monkeypatch):
    """The whole back-compat guarantee in one assertion. These defaults MUST match the `:-` defaults
    in docker-compose.yml and installer/lib-deploy.sh; if they drift, an existing install moves."""
    assert _intent_from(monkeypatch, {"POSTGIS_HOST": "postgres"}) == {
        "mode": "dedicated", "bind": "0.0.0.0", "port": "80"}


def test_the_mode_is_inferred_from_the_bind_when_it_is_missing(monkeypatch):
    # A .env hand-edited to a loopback bind, with no mode line, is behind-proxy whatever it says.
    assert _intent_from(monkeypatch, {"GEODEPLOY_HTTP_BIND": "127.0.0.1",
                                      "GEODEPLOY_HTTP_PORT": "8080"})["mode"] == "behind-proxy"


def test_an_unreadable_env_does_not_explode(monkeypatch):
    def boom(path=None):
        raise OSError("no such file")
    monkeypatch.setattr(deployment.envfile, "read_all", boom)
    assert deployment.read_intent()["port"] == "80"


# ── Observed: telling a broken proxy from an SSH tunnel ───────────────────────────────────────────

def _request(headers):
    return types.SimpleNamespace(
        headers=headers,
        url=types.SimpleNamespace(netloc="127.0.0.1:8080", scheme="http"))


def test_a_tunnel_is_not_mistaken_for_a_proxy():
    """One X-Forwarded-For entry is our OWN nginx adding the client. Nothing forwarded to us."""
    seen = deployment.observe(_request({"host": "127.0.0.1:8080", "x-forwarded-for": "10.0.0.9"}))
    assert seen["behind_outer_proxy"] is False
    assert seen["is_domain"] is False


def test_two_forwarded_for_entries_mean_something_is_in_front():
    """Our nginx APPENDS to X-Forwarded-For, so a second entry can only have arrived from outside."""
    seen = deployment.observe(_request({"host": "127.0.0.1:8080",
                                        "x-forwarded-for": "203.0.113.7, 10.0.0.9"}))
    assert seen["behind_outer_proxy"] is True


def test_https_alone_proves_a_proxy():
    # Our nginx only ever speaks plain HTTP, so a terminated https came from somewhere else.
    assert deployment.observe(_request({"host": "maps.example.org",
                                        "x-forwarded-proto": "https"}))["behind_outer_proxy"] is True


@pytest.mark.parametrize("host,is_domain", [
    ("maps.example.org", True),
    ("example.org", True),
    ("127.0.0.1:8080", False),
    ("localhost:8080", False),
    ("203.0.113.7", False),      # reachable, but no domain and no TLS story
    ("geodeploy-nginx-1", False),
])
def test_what_counts_as_a_domain(host, is_domain):
    assert deployment.observe(_request({"host": host}))["is_domain"] is is_domain


# ── The verdict ───────────────────────────────────────────────────────────────────────────────────

BEHIND = {"mode": "behind-proxy", "bind": "127.0.0.1", "port": "8080"}
DEDICATED = {"mode": "dedicated", "bind": "0.0.0.0", "port": "80"}
UNKNOWN = {"bind": None, "port": None, "running": None, "listening": None, "error": "no socket"}


def _reality(bind="127.0.0.1", port="8080", listening=True):
    return {"bind": bind, "port": port, "running": True, "listening": listening, "error": None}


def test_a_dead_port_outranks_everything_else():
    """Docker reports running with the right mapping and nothing answers — the state a restart does
    NOT repair. It must be reported first, because every other signal says the instance is fine."""
    v = deployment.verdict(BEHIND, _reality(listening=False),
                           deployment.observe(_request({"host": "maps.example.org",
                                                        "x-forwarded-proto": "https"})))
    assert v["level"] == "critical"
    assert "force-recreate" in v["fix"]


def test_env_edited_but_nginx_never_recreated():
    v = deployment.verdict(BEHIND, _reality(port="9090"), deployment.observe(_request({"host": "x"})))
    assert v["level"] == "critical"
    assert "set-port.sh 8080" in v["fix"]


def test_unknown_reality_is_not_reported_as_a_mismatch():
    """No Docker socket means we cannot tell — which is not the same as wrong, and saying so would
    send the operator chasing a problem they do not have."""
    v = deployment.verdict(BEHIND, UNKNOWN, deployment.observe(_request({"host": "127.0.0.1:8080"})))
    assert v["level"] == "warning"
    assert "Only this machine" in v["title"]


def test_a_proxy_that_drops_the_hostname_is_called_out_by_name():
    v = deployment.verdict(BEHIND, _reality(),
                           deployment.observe(_request({"host": "127.0.0.1:8080",
                                                        "x-forwarded-for": "203.0.113.7, 10.0.0.9"})))
    assert v["level"] == "critical"
    assert "proxy_set_header Host" in v["fix"]


def test_a_working_proxy_over_https_is_the_only_ok_state():
    v = deployment.verdict(BEHIND, _reality(),
                           deployment.observe(_request({"host": "maps.example.org",
                                                        "x-forwarded-proto": "https"})))
    assert v["level"] == "ok"
    assert "maps.example.org" in v["title"]


def test_a_domain_without_tls_is_a_warning_not_a_pass():
    v = deployment.verdict(BEHIND, _reality(),
                           deployment.observe(_request({"host": "maps.example.org",
                                                        "x-forwarded-for": "203.0.113.7, 10.0.0.9"})))
    assert v["level"] == "warning"
    assert "HTTPS" in v["title"]


def test_exposed_to_the_network_without_tls():
    v = deployment.verdict(DEDICATED, _reality(bind="0.0.0.0", port="80"),
                           deployment.observe(_request({"host": "203.0.113.7"})))
    assert v["level"] == "warning"
    assert "set-port.sh" in v["fix"]


# ── The generated configuration ───────────────────────────────────────────────────────────────────

def test_the_nginx_block_carries_every_directive_whose_absence_is_a_real_failure():
    cfg = deployment.proxy_config("maps.example.org", "nginx", BEHIND)
    body = cfg["config"]
    # Each of these was MEASURED failing against a naive proxy_pass-only config: the hostname became
    # the upstream address, X-Forwarded-Proto was absent, and a 2 MB upload returned 413.
    assert "proxy_set_header Host              $host;" in body
    assert "X-Forwarded-Proto $scheme" in body
    assert "client_max_body_size 11G;" in body
    assert "proxy_request_buffering off;" in body
    assert "proxy_read_timeout 600s;" in body
    assert "proxy_pass http://127.0.0.1:8080;" in body
    assert cfg["test_command"] == "sudo nginx -t"


def test_the_upstream_follows_the_configured_port():
    cfg = deployment.proxy_config("maps.example.org", "nginx",
                                  {"mode": "behind-proxy", "bind": "127.0.0.1", "port": "9099"})
    assert "http://127.0.0.1:9099" in cfg["config"]
    assert cfg["upstream"] == "http://127.0.0.1:9099"


def test_the_edge_sets_the_scheme_itself_rather_than_echoing_the_client():
    """`$scheme`, never `$http_x_forwarded_proto` — otherwise any client could claim https and get a
    Secure cookie issued over a plaintext connection."""
    body = deployment.proxy_config("maps.example.org", "nginx", BEHIND)["config"]
    assert "$http_x_forwarded_proto" not in body


def test_publishing_on_all_interfaces_is_flagged_when_a_proxy_is_being_set_up():
    cfg = deployment.proxy_config("maps.example.org", "nginx", DEDICATED)
    assert cfg["warnings"], "an 0.0.0.0 publish bypasses ufw and should be called out"
    assert "set-port.sh" in cfg["warnings"][0]
    assert not deployment.proxy_config("maps.example.org", "nginx", BEHIND)["warnings"]


@pytest.mark.parametrize("flavor", deployment.FLAVORS)
def test_every_flavor_renders_and_names_the_domain(flavor):
    cfg = deployment.proxy_config("maps.example.org", flavor, BEHIND)
    assert "maps.example.org" in cfg["config"]
    assert cfg["path"] and cfg["steps"]


def test_traefik_routes_to_the_container_not_the_loopback_port():
    """A container CANNOT reach a 127.0.0.1 host port — that isolation is the point of the mode — so
    the Traefik answer has to be joining the network, not proxying to the published port."""
    body = deployment.proxy_config("maps.example.org", "traefik", BEHIND)["config"]
    assert "docker network connect geodeploy" in body
    assert "loadbalancer.server.port=80" in body


# ── The wedged mapping, detected from Docker's own two views ──────────────────────────────────────
# Measured 2026-09-12 against a real daemon: with the port occupied at start, `HostConfig.PortBindings`
# keeps the request while `NetworkSettings.Ports` goes empty and the container still reports running.

HEALTHY_ATTRS = {"NetworkSettings": {"Ports": {"80/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8080"}]}}}
WEDGED_ATTRS = {"NetworkSettings": {"Ports": {}}}


def test_a_live_mapping_reads_as_live():
    assert deployment._mapping_is_live(HEALTHY_ATTRS) is True


def test_a_bind_that_failed_reads_as_dead():
    assert deployment._mapping_is_live(WEDGED_ATTRS) is False


def test_a_loopback_bind_is_never_mistaken_for_a_dead_one():
    """The regression this guards. Inside the API container the host's 127.0.0.1 is unreachable BY
    DESIGN in behind-proxy mode, so any probe that opened a socket would call every healthy
    loopback install dead — a false alarm on the most common configuration."""
    reality = {"bind": "127.0.0.1", "port": "8080", "running": True,
               "listening": deployment._mapping_is_live(HEALTHY_ATTRS), "error": None}
    v = deployment.verdict(BEHIND, reality,
                           deployment.observe(_request({"host": "maps.example.org",
                                                        "x-forwarded-proto": "https"})))
    assert v["level"] == "ok"


def test_nothing_to_say_is_not_a_failure():
    assert deployment._mapping_is_live({}) is None
    assert deployment._mapping_is_live({"NetworkSettings": {}}) is None
