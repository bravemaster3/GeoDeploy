"""Configuring somebody else's web server — the refusals, and the one invariant that hides a reload.

This is the only code in GeoDeploy that changes software GeoDeploy did not install, on machines that
by definition host other people's sites. So the tests worth having are not "does it write the file";
they are the ones that pin the REFUSALS and the shell-level details whose failure is silent:

* `cmd | tail; rc=$?` reports the status of `tail`. If that pattern ever comes back, a FAILING
  `nginx -t` reads as a pass, and both the before-check (R2) and the rollback (R4) quietly stop
  working while every test about them still passes. Hence a test on the command table itself.
* A drop-in directory that exists but is not INCLUDED by the main config takes our file and ignores
  it — success that does nothing (R6).
* A domain already served here must be refused, because two blocks claiming one name is not an
  error: one silently wins (R5).
* A config that was already failing before we arrived must stop us, or our reload becomes the thing
  that takes their sites down (R2).

No Docker is touched anywhere in this file: every test either exercises pure parsing/selection logic or
monkeypatches `detect`.
"""
import pytest

from geodeploy.services import hostproxy


# ── The shell invariant ───────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("kind,target", [
    ("nginx", "/etc/nginx/conf.d/geodeploy.conf"),
    ("apache", "/etc/apache2/sites-available/geodeploy.conf"),
    ("apache", "/etc/httpd/conf.d/geodeploy.conf"),
    ("caddy", "/etc/caddy/conf.d/geodeploy.caddy"),
])
def test_test_and_reload_commands_never_contain_a_pipe(kind, target):
    """The caller needs the command's OWN exit status. A pipe hands back the last stage's instead,
    which turns a failing configuration test into a passing one — see this module's docstring."""
    adapter = {"kind": kind, "target": target}
    for command in (hostproxy._host_test_command(adapter), hostproxy._host_reload_command(adapter)):
        assert "|" not in command
        assert "2>&1" not in command        # redirection is the caller's job too, for the same reason


def test_the_apply_script_captures_before_it_trims():
    """The rendered script must read the status from the command, not from `tail`."""
    script = hostproxy._APPLY_HOST.format(
        target="/etc/nginx/conf.d/geodeploy.conf", link="",
        test_cmd="chroot $H /usr/sbin/nginx -t",
        reload_cmd="chroot $H /usr/sbin/nginx -s reload",
        domain="maps.example.org", probe_port="80")
    assert "OUT=$(chroot $H /usr/sbin/nginx -t 2>&1)\nRC=$?" in script
    assert 'echo "rc=$RC"' in script
    # and the rollback must remove the file BEFORE it re-tests, with no reload after a failure
    rollback = script.split("say rollback", 1)[1].split("say reload", 1)[0]
    assert 'rm -f "$TARGET"' in rollback
    assert "exit 3" in rollback


def test_a_symlink_points_at_the_host_path_not_the_helper_path():
    """The link is created under /host but must RESOLVE on the host, so its target is host-absolute.
    A link into /host/... would dangle the moment the helper container exits."""
    script = hostproxy._APPLY_HOST.format(
        target="/etc/nginx/sites-available/geodeploy.conf",
        link="/etc/nginx/sites-enabled/geodeploy.conf",
        test_cmd="true", reload_cmd="true", domain="x.example.org", probe_port="80")
    assert 'ln -sfn "/etc/nginx/sites-available/geodeploy.conf" "$H$LINK"' in script


# ── Domain validation: the last gate before a string reaches `sh -c` ──────────────────────────────

@pytest.mark.parametrize("bad", [
    "", "example", "maps.example.org; rm -rf /", "maps example.org", "$(id).example.org",
    "`id`.example.org", "maps.example.org\nServer", "-maps.example.org", "maps..example.org",
    "a" * 250 + ".example.org",
])
def test_a_domain_that_could_carry_a_command_is_refused(bad):
    with pytest.raises(hostproxy.ProxyError):
        hostproxy._safe_domain(bad)


@pytest.mark.parametrize("good,expected", [
    ("maps.example.org", "maps.example.org"),
    ("  MAPS.Example.ORG. ", "maps.example.org"),
    ("geodeploy-lite.kndev.org", "geodeploy-lite.kndev.org"),
])
def test_an_ordinary_hostname_is_accepted_and_normalised(good, expected):
    assert hostproxy._safe_domain(good) == expected


# ── Parsing the probe ─────────────────────────────────────────────────────────────────────────────

def test_sections_are_keyed_and_blank_lines_dropped():
    parsed = hostproxy._sections("==os==\ndebian\n\n==dirs==\n/etc/nginx\n==end==\n")
    assert parsed["os"] == ["debian"]
    assert parsed["dirs"] == ["/etc/nginx"]
    assert parsed["end"] == []


def test_a_test_section_without_an_rc_line_is_not_a_failure():
    """The binary was not there, so the test never ran. Reporting that as a failing configuration
    would block Apply on every machine where the proxy is a different one."""
    assert hostproxy._test_result([]) is None
    assert hostproxy._test_result(["nginx: configuration file ok"]) is None


def test_the_rc_line_decides_and_is_not_reported_as_output():
    result = hostproxy._test_result(["nginx: [emerg] unknown directive", "rc=1"])
    assert result == {"ok": False, "output": "nginx: [emerg] unknown directive"}
    assert hostproxy._test_result(["syntax is ok", "rc=0"])["ok"] is True


# ── Which names this machine already serves ───────────────────────────────────────────────────────

def test_server_names_are_collected_from_all_three_syntaxes():
    names = hostproxy._names_from([
        "server_name  shop.example.org www.shop.example.org;",
        "ServerName blog.example.org",
        "ServerAlias www.blog.example.org",
        "metrics.example.org {",
    ])
    assert {"shop.example.org", "www.shop.example.org", "blog.example.org",
            "www.blog.example.org", "metrics.example.org"} <= names


def test_placeholders_are_not_mistaken_for_hostnames():
    """`server_name _;` is nginx's catch-all. Treating it as a claimed name would make every machine
    with a default block refuse every domain."""
    names = hostproxy._names_from(["server_name _;", "server_name localhost;"])
    assert names == set()


def test_a_wildcard_block_counts_as_a_conflict():
    names = {"*.example.org"}
    assert hostproxy._name_conflict("maps.example.org", names) == "*.example.org"
    assert hostproxy._name_conflict("maps.other.org", names) is None


def test_an_exact_match_is_a_conflict():
    assert hostproxy._name_conflict("maps.example.org", {"maps.example.org"}) == "maps.example.org"


# ── Adapter selection ─────────────────────────────────────────────────────────────────────────────

def test_confd_is_preferred_and_needs_no_symlink():
    adapter = hostproxy._nginx_host_adapter(
        ["/etc/nginx", "/etc/nginx/conf.d", "/etc/nginx/sites-enabled", "/etc/nginx/sites-available"],
        ["include /etc/nginx/conf.d/*.conf", "include /etc/nginx/sites-enabled/*"])
    assert adapter["target"] == "/etc/nginx/conf.d/geodeploy.conf"
    assert adapter["symlink"] is None


def test_sites_available_is_used_with_a_link_when_confd_is_not_included():
    adapter = hostproxy._nginx_host_adapter(
        ["/etc/nginx", "/etc/nginx/sites-enabled", "/etc/nginx/sites-available"],
        ["include /etc/nginx/sites-enabled/*"])
    assert adapter["target"] == "/etc/nginx/sites-available/geodeploy.conf"
    assert adapter["symlink"] == "/etc/nginx/sites-enabled/geodeploy.conf"


def test_a_dropin_directory_that_nothing_includes_is_refused(monkeypatch):
    """R6. The directory exists, so a naive check would write there — and nginx would never read it.
    Success that does nothing is worse than a refusal, because the operator then debugs DNS."""
    assert hostproxy._nginx_host_adapter(["/etc/nginx", "/etc/nginx/conf.d"], ["include mime.types"]) is None


def test_caddy_is_only_automatic_when_the_caddyfile_already_imports_a_dropin():
    assert hostproxy._caddy_host_adapter(["/etc/caddy", "/etc/caddy/conf.d"], []) is None
    adapter = hostproxy._caddy_host_adapter(
        ["/etc/caddy", "/etc/caddy/conf.d"], ["import /etc/caddy/conf.d/*.caddy"])
    assert adapter["tls"] == "automatic"      # Caddy needs no certbot step at all


def test_traefik_gets_no_adapter():
    assert hostproxy._container_adapter(
        {"kind": "traefik", "name": "traefik", "mounts": [
            {"host": "/srv/traefik", "container": "/etc/traefik/conf.d", "rw": True}]}) is None


def test_a_containerised_proxy_writes_to_the_host_side_of_its_mount():
    adapter = hostproxy._container_adapter({
        "kind": "nginx", "name": "web",
        "mounts": [{"host": "/srv/web/conf.d", "container": "/etc/nginx/conf.d", "rw": True}]})
    assert adapter["target"] == "/srv/web/conf.d/geodeploy.conf"
    assert adapter["container"] == "web"


def test_a_read_only_config_mount_is_not_usable():
    assert hostproxy._container_adapter({
        "kind": "nginx", "name": "web",
        "mounts": [{"host": "/srv/web/conf.d", "container": "/etc/nginx/conf.d", "rw": False}]}) is None


# ── plan(): the refusals, end to end ──────────────────────────────────────────────────────────────

_INTENT = {"mode": "behind-proxy", "bind": "127.0.0.1", "port": "8080"}

_NGINX_ADAPTER = {"kind": "nginx", "where": "host", "container": None,
                  "target": "/etc/nginx/conf.d/geodeploy.conf", "symlink": None,
                  "reload": "nginx -s reload", "tls": "certbot"}


def _detected(monkeypatch, **overrides):
    found = {
        "probe_ok": True, "error": None, "os": "debian", "listeners": [
            {"socket": "0.0.0.0:80", "process": "1974595/nginx"}],
        "units": ["nginx"], "binaries": ["/usr/sbin/nginx"], "dirs": ["/etc/nginx/conf.d"],
        "includes": ["include /etc/nginx/conf.d/*.conf"], "server_names": [],
        "our_files": {}, "tests": {"nginx": {"ok": True, "output": "syntax is ok"}},
        "containers": [], "adapter": dict(_NGINX_ADAPTER), "certbot": "/usr/bin/certbot",
    }
    found.update(overrides)
    monkeypatch.setattr(hostproxy, "detect", lambda: found)
    return found


def _codes(plan):
    return {b["code"] for b in plan["blockers"]}


def test_a_healthy_machine_can_be_applied_to(monkeypatch):
    _detected(monkeypatch)
    plan = hostproxy.plan("maps.example.org", _INTENT)
    assert plan["can_apply"] is True
    assert plan["blockers"] == []
    assert plan["target"] == "/etc/nginx/conf.d/geodeploy.conf"
    assert "maps.example.org" in plan["config"]
    assert hostproxy.MARKER in plan["config"]     # so detect() recognises the file later as ours


def test_a_configuration_that_already_fails_blocks_everything(monkeypatch):
    """R2, the most important refusal in the module: our reload would be what publishes THEIR
    breakage, and from the operator's chair GeoDeploy did it."""
    _detected(monkeypatch, tests={"nginx": {"ok": False, "output": "nginx: [emerg] unknown directive"}})
    plan = hostproxy.plan("maps.example.org", _INTENT)
    assert plan["can_apply"] is False
    assert "already-broken" in _codes(plan)
    assert "unknown directive" in plan["blockers"][0]["detail"]


def test_a_domain_this_machine_already_serves_is_refused(monkeypatch):
    _detected(monkeypatch, server_names=["maps.example.org", "shop.example.org"])
    plan = hostproxy.plan("maps.example.org", _INTENT)
    assert plan["can_apply"] is False
    assert "name-taken" in _codes(plan)


def test_our_filename_holding_someone_elses_file_is_refused(monkeypatch):
    _detected(monkeypatch, our_files={"/etc/nginx/conf.d/geodeploy.conf": "foreign"})
    plan = hostproxy.plan("maps.example.org", _INTENT)
    assert plan["can_apply"] is False
    assert "foreign-file" in _codes(plan)


def test_our_own_earlier_file_is_replaced_not_refused(monkeypatch):
    """Changing the domain has to work, and it works by rewriting the file we wrote."""
    _detected(monkeypatch, our_files={"/etc/nginx/conf.d/geodeploy.conf": "ours"})
    plan = hostproxy.plan("maps.example.org", _INTENT)
    assert plan["can_apply"] is True
    assert any("written by GeoDeploy earlier" in w for w in plan["warnings"])


def test_traefik_is_a_named_refusal_not_a_shrug(monkeypatch):
    _detected(monkeypatch, adapter=None,
              containers=[{"kind": "traefik", "name": "traefik", "image": "traefik:v3",
                           "ports": ["80", "443"], "mounts": []}])
    plan = hostproxy.plan("maps.example.org", _INTENT)
    assert "traefik" in _codes(plan)
    assert "labels" in plan["blockers"][0]["fix"]


def test_no_proxy_at_all_says_so_plainly(monkeypatch):
    _detected(monkeypatch, adapter=None, binaries=[], containers=[], dirs=[], includes=[])
    plan = hostproxy.plan("maps.example.org", _INTENT)
    assert "no-adapter" in _codes(plan)
    assert "nothing in front of GeoDeploy" in plan["blockers"][0]["detail"]


def test_a_probe_that_could_not_run_is_distinguished_from_one_that_found_nothing(monkeypatch):
    _detected(monkeypatch, probe_ok=False, adapter=None, error="Docker is not reachable")
    plan = hostproxy.plan("maps.example.org", _INTENT)
    assert "no-probe" in _codes(plan)
    assert "Docker is not reachable" in plan["blockers"][0]["detail"]


def test_a_publicly_bound_geodeploy_is_warned_about_but_not_blocked(monkeypatch):
    _detected(monkeypatch)
    plan = hostproxy.plan("maps.example.org", {"mode": "dedicated", "bind": "0.0.0.0", "port": "80"})
    assert plan["can_apply"] is True
    assert any("set-port.sh" in w for w in plan["warnings"])


def test_apply_refuses_without_writing_when_the_plan_says_no(monkeypatch):
    """The guard is inside apply(), not only in the panel: an API client that skips the plan call
    must hit the same refusal."""
    _detected(monkeypatch, tests={"nginx": {"ok": False, "output": "broken"}})
    called = []
    monkeypatch.setattr(hostproxy, "_host_run", lambda *a, **k: called.append(a) or (0, ""))
    result = hostproxy.apply("maps.example.org", _INTENT)
    assert result["ok"] is False
    assert called == []                   # nothing was run against the host


def test_an_earlier_apply_is_reported_so_removal_stays_reachable(monkeypatch):
    """The Remove button must exist in a browser that never pressed Apply — otherwise undoing last
    week's file means `rm` over SSH, which skips the test-before-reload that makes removal safe."""
    _detected(monkeypatch)
    monkeypatch.setattr(hostproxy, "read_state", lambda: {
        "domain": "old.example.org", "at": 1760000000,
        "adapter": {"target": "/etc/nginx/conf.d/geodeploy.conf", "where": "host"}})
    plan = hostproxy.plan("maps.example.org", _INTENT)
    assert plan["applied"]["domain"] == "old.example.org"
    assert plan["applied"]["target"] == "/etc/nginx/conf.d/geodeploy.conf"


def test_nothing_applied_yet_reports_none_not_an_empty_shell(monkeypatch):
    _detected(monkeypatch)
    monkeypatch.setattr(hostproxy, "read_state", lambda: {})
    assert hostproxy.plan("maps.example.org", _INTENT)["applied"] is None
