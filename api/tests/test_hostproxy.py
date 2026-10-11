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
        domain="maps.example.org", probe_port="80", probe_tls="443")
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
        test_cmd="true", reload_cmd="true", domain="x.example.org", probe_port="80",
        probe_tls="")
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
    assert any("will be replaced" in w for w in plan["warnings"])


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


# ── HTTPS shadowing: the failure that reached a real browser (2026-10-09) ────────────────────────
#
# Found in the field. Our block listens on 80 and was perfect; the machine had
# `listen 443 ssl; server_name _;` for another site; Cloudflare in Full mode connects to the origin
# on 443. Port 80 served GeoDeploy, every real visitor got the other website, and every check we had
# reported success. These pin the three outcomes apart.

_BLOCKS_CATCHALL_TLS = [
    "b1 listen 80;",
    "b1 server_name shop.example.org;",
    "b2 listen 443 ssl;",
    "b2 server_name _;",
]


def test_a_443_catchall_means_https_does_not_reach_us():
    tls = hostproxy._tls_picture(_BLOCKS_CATCHALL_TLS, "maps.example.org")
    assert tls == {"has_tls": True, "serves_domain": False, "catchall": True, "names": []}


def test_a_443_block_for_our_own_name_is_not_a_problem():
    tls = hostproxy._tls_picture(
        ["b1 listen 443 ssl;", "b1 server_name maps.example.org;"], "maps.example.org")
    assert tls["has_tls"] is True and tls["serves_domain"] is True


def test_a_machine_with_no_tls_at_all_says_nothing():
    """Nothing serves 443, so nothing is being shadowed. Warning here would be noise on exactly the
    machines where the port-80 block is the whole answer."""
    tls = hostproxy._tls_picture(["b1 listen 80;", "b1 server_name shop.example.org;"], "maps.example.org")
    assert tls["has_tls"] is False


def test_the_plan_warns_before_apply_when_https_would_land_elsewhere(monkeypatch):
    """Said BEFORE Apply, not in a troubleshooting page: the operator otherwise learns it from a
    browser, after a panel that said success."""
    _detected(monkeypatch, blocks=_BLOCKS_CATCHALL_TLS)
    plan = hostproxy.plan("maps.example.org", _INTENT)
    assert plan["can_apply"] is True           # the http:// configuration is still correct
    # Assert the CLAIM, not the phrasing: https goes somewhere else, http does not, and a
    # certificate is the fix. The wording is UI copy and is allowed to be rewritten.
    warning = next((w for w in plan["warnings"] if "https://" in w), None)
    assert warning is not None
    assert "certificate" in warning
    assert "http://" in warning


def test_no_https_warning_when_this_machine_serves_no_tls(monkeypatch):
    _detected(monkeypatch, blocks=["b1 listen 80;", "b1 server_name shop.example.org;"])
    plan = hostproxy.plan("maps.example.org", _INTENT)
    assert not any("HTTPS" in w for w in plan["warnings"])


def test_the_written_file_does_not_carry_paste_instructions(monkeypatch):
    """The generator's preamble tells the reader to place the file and reload — a to-do list for work
    that already happened by the time it is on disk. The marker and the per-directive comments stay."""
    _detected(monkeypatch)
    body = hostproxy.plan("maps.example.org", _INTENT)["config"]
    assert hostproxy.MARKER in body
    assert "put this in" not in body
    assert "systemctl reload" not in body.split("server {")[0]
    assert "client_max_body_size" in body      # the comments that matter are still there
    assert "proxy_set_header Host" in body


def test_the_apply_script_probes_443_as_well_as_80():
    script = hostproxy._APPLY_HOST.format(
        target="/etc/nginx/conf.d/geodeploy.conf", link="", test_cmd="true", reload_cmd="true",
        domain="maps.example.org", probe_port="80", probe_tls="443")
    assert "say route443" in script
    assert "--no-check-certificate" in script
    assert "https://127.0.0.1/api/public/whoami" in script


# ── Self-collision: GeoDeploy must not refuse because of its own file ────────────────────────────
#
# Reported from the field the day Apply first worked: the FIRST apply succeeded, and every visit to
# the panel afterwards refused with "something already serves that name here" — pointing, invisibly,
# at the file GeoDeploy had just written. R5 is right; sweeping our own configuration into the list
# of what "somebody else" serves is not.

def test_our_own_block_is_not_counted_as_somebody_elses_name(monkeypatch):
    _detected(monkeypatch,
              server_names=["shop.example.org"],      # the probe now excludes our file
              our_files={"/etc/nginx/conf.d/geodeploy.conf": "ours"})
    monkeypatch.setattr(hostproxy, "read_state", lambda: {
        "domain": "maps.example.org",
        "adapter": {"target": "/etc/nginx/conf.d/geodeploy.conf", "where": "host"}})
    plan = hostproxy.plan("maps.example.org", _INTENT)
    assert plan["can_apply"] is True
    assert "name-taken" not in _codes(plan)


def test_the_recorded_domain_is_forgiven_even_if_the_probe_still_reports_it(monkeypatch):
    """Belt and braces over the probe's own exclusion — an operator who renamed the file, or a probe
    that could not run `find`, must still be able to re-apply."""
    _detected(monkeypatch,
              server_names=["maps.example.org"],
              our_files={"/etc/nginx/conf.d/geodeploy.conf": "ours"})
    monkeypatch.setattr(hostproxy, "read_state", lambda: {
        "domain": "maps.example.org",
        "adapter": {"target": "/etc/nginx/conf.d/geodeploy.conf", "where": "host"}})
    assert hostproxy.plan("maps.example.org", _INTENT)["can_apply"] is True


def test_a_name_served_by_somebody_else_is_still_refused(monkeypatch):
    """The forgiveness above must not become a hole: a DIFFERENT name, or no file of ours, still
    blocks."""
    _detected(monkeypatch, server_names=["maps.example.org"], our_files={})
    monkeypatch.setattr(hostproxy, "read_state", lambda: {})
    assert "name-taken" in _codes(hostproxy.plan("maps.example.org", _INTENT))


def test_our_marked_block_still_counts_for_the_https_question(monkeypatch):
    """Excluded from R5, INCLUDED in the TLS picture: once certbot adds a 443 block to our file, that
    block is exactly what proves HTTPS now reaches GeoDeploy."""
    tls = hostproxy._tls_picture(
        ["b1 GEODEPLOY_OWN", "b1 listen 443 ssl;", "b1 server_name maps.example.org;"],
        "maps.example.org")
    assert tls["has_tls"] is True and tls["serves_domain"] is True


def test_the_tls_picture_reaches_the_payload_apply_reads_it_from(monkeypatch):
    """apply() decides whether to probe 443 from plan()["detected"]["tls"]. Computing it and not
    carrying it through skipped the HTTPS check silently — the blind spot §5o exists to close, put
    back by omission in a dict literal. Caught on a real machine by a missing step in the results."""
    _detected(monkeypatch, blocks=_BLOCKS_CATCHALL_TLS)
    plan = hostproxy.plan("maps.example.org", _INTENT)
    assert plan["detected"]["tls"]["has_tls"] is True
    assert plan["detected"]["tls"]["catchall"] is True


# ── The binary is where the probe found it, not where Debian puts it ─────────────────────────────
#
# Caddy from the package is /usr/bin/caddy; Caddy installed by hand — how most people install it —
# is /usr/local/bin/caddy. nginx is /usr/sbin/nginx on Debian and /usr/bin/nginx elsewhere. The
# command table hardcoded one of each, so on the other half of the world Apply wrote the file, could
# not run the test, rolled back and reported failure on a machine that was perfectly fine.

def test_caddy_installed_by_hand_is_tested_with_the_binary_that_exists():
    adapter = hostproxy._caddy_host_adapter(
        ["/etc/caddy", "/etc/caddy/conf.d"], ["import /etc/caddy/conf.d/*.caddy"],
        ["/usr/local/bin/caddy"])
    assert adapter["bin"] == "/usr/local/bin/caddy"
    assert "/usr/local/bin/caddy validate" in hostproxy._host_test_command(adapter)
    assert "/usr/local/bin/caddy reload" in hostproxy._host_reload_command(adapter)


def test_nginx_outside_usr_sbin_is_tested_with_the_binary_that_exists():
    adapter = hostproxy._nginx_host_adapter(
        ["/etc/nginx/conf.d"], ["include /etc/nginx/conf.d/*.conf"], ["/usr/bin/nginx"])
    assert hostproxy._host_test_command(adapter) == "chroot $H /usr/bin/nginx -t"
    assert hostproxy._host_reload_command(adapter) == "chroot $H /usr/bin/nginx -s reload"


def test_httpd_is_recognised_by_its_binary_not_only_by_its_path():
    adapter = hostproxy._apache_host_adapter(
        ["/etc/httpd/conf.d"], ["IncludeOptional conf.d/*.conf"], ["/usr/sbin/httpd"])
    assert adapter["bin"] == "/usr/sbin/httpd"
    assert "httpd -t" in hostproxy._host_test_command(adapter)


def test_an_unknown_layout_still_falls_back_to_the_usual_path():
    """No binary discovered is not a reason to emit an empty command — the fallback is the path the
    overwhelming majority of machines use, and a wrong guess fails safely in the test step."""
    adapter = {"kind": "nginx", "target": "/etc/nginx/conf.d/geodeploy.conf"}
    assert hostproxy._host_test_command(adapter) == "chroot $H /usr/sbin/nginx -t"


def test_a_stock_caddy_is_told_the_one_line_that_unlocks_it(monkeypatch):
    """A Caddyfile with no drop-in import is the DEFAULT state of a Caddy machine, not an odd one —
    and Caddy is the best case downstream, since it gets the certificate itself. A bare "cannot help"
    there wastes the easiest win available."""
    _detected(monkeypatch, adapter=None, binaries=["/usr/bin/caddy"],
              dirs=["/etc/caddy"], includes=[])
    plan = hostproxy.plan("maps.example.org", _INTENT)
    detail = plan["blockers"][0]["detail"]
    assert "import /etc/caddy/conf.d/*.caddy" in detail
    assert "Caddy obtains by itself" in detail


# ── Automatic HTTPS: certbot in a container, and the renewal that makes it mean anything ─────────

def test_the_https_block_keeps_the_acme_path_unredirected():
    """Renewal uses the same HTTP-01 challenge. Redirecting it to a port whose certificate has just
    expired is how an auto-renewing certificate quietly stops auto-renewing."""
    from geodeploy.services import deployment
    body = deployment.proxy_config(
        "maps.example.org", "nginx", _INTENT, tls=True, redirect=True)["config"]
    port80 = body.split("server {", 1)[1].split("server {", 1)[0]
    assert ".well-known/acme-challenge" in port80
    assert port80.index(".well-known") < port80.index("return 301")


def test_behind_cloudflare_port_80_serves_instead_of_redirecting():
    """Cloudflare on Flexible connects to port 80; a 301 to https sends the browser back to
    Cloudflare, which connects to port 80 again. That is an infinite loop, and it appears on a site
    that worked a minute earlier."""
    from geodeploy.services import deployment
    body = deployment.proxy_config(
        "maps.example.org", "nginx", _INTENT, tls=True, redirect=False)["config"]
    port80 = body.split("server {", 1)[1].split("server {", 1)[0]
    assert "return 301" not in port80
    assert "proxy_pass" in port80
    assert "Always Use HTTPS" in body          # says what to turn on at Cloudflare instead


def test_the_two_blocks_carry_the_same_proxy_settings():
    """One generator, two layouts. A setting present on 443 and missing on 80 produces a site that
    works until somebody follows an http:// link, then 413s on upload."""
    from geodeploy.services import deployment
    body = deployment.proxy_config(
        "maps.example.org", "nginx", _INTENT, tls=True, redirect=False)["config"]
    for required in ("client_max_body_size 11G", "proxy_request_buffering off",
                     "proxy_set_header Host              $host"):
        assert body.count(required) == 2, f"{required} must appear in both blocks"


def test_the_certificate_paths_are_certbots_own():
    from geodeploy.services import deployment
    body = deployment.proxy_config("maps.example.org", "nginx", _INTENT, tls=True)["config"]
    assert "/etc/letsencrypt/live/maps.example.org/fullchain.pem" in body
    assert "/etc/letsencrypt/live/maps.example.org/privkey.pem" in body


def test_renewal_is_a_no_op_without_a_certificate(monkeypatch):
    """Almost every install never asks for one. The daily tick must cost a state-file read, not a
    container start."""
    monkeypatch.setattr(hostproxy, "read_state", lambda: {})
    started = []
    monkeypatch.setattr(hostproxy, "_certbot", lambda *a, **k: started.append(a) or (0, ""))
    result = hostproxy.renew_certificates()
    assert result["renewed"] is False
    assert started == []


def test_renewal_does_not_reload_when_nothing_changed(monkeypatch):
    """Reloading somebody's web server daily for no reason is the habit this module exists to avoid.
    The gate is the certificate file's mtime, not certbot's prose."""
    monkeypatch.setattr(hostproxy, "read_state", lambda: {
        "certificate": {"domain": "maps.example.org"},
        "adapter": {"kind": "nginx", "target": "/etc/nginx/conf.d/geodeploy.conf"}})
    monkeypatch.setattr(hostproxy, "_cert_mtime", lambda d: "1760000000")
    monkeypatch.setattr(hostproxy, "_certbot", lambda *a, **k: (0, "Cert not yet due for renewal"))
    reloaded = []
    monkeypatch.setattr(hostproxy, "_host_run", lambda *a, **k: reloaded.append(a) or (0, ""))
    result = hostproxy.renew_certificates()
    assert result["renewed"] is False
    assert reloaded == []


def test_a_failed_test_after_renewal_does_not_reload(monkeypatch):
    """The certificate renewed but the configuration is broken for some unrelated reason. Reloading
    would publish that breakage; the old certificate stays live instead, which is the safe side."""
    monkeypatch.setattr(hostproxy, "read_state", lambda: {
        "certificate": {"domain": "maps.example.org"},
        "adapter": {"kind": "nginx", "target": "/etc/nginx/conf.d/geodeploy.conf"}})
    mtimes = iter(["1760000000", "1760999999"])
    monkeypatch.setattr(hostproxy, "_cert_mtime", lambda d: next(mtimes))
    monkeypatch.setattr(hostproxy, "_certbot", lambda *a, **k: (0, "renewed"))
    monkeypatch.setattr(hostproxy, "_host_run",
                        lambda *a, **k: (0, "==test==\nnginx: [emerg] bad\nrc=1\n==end==\n"))
    result = hostproxy.renew_certificates()
    assert result["ok"] is False
    assert result["reloaded"] is False
    assert "NOT reloaded" in result["detail"]


# ── Advice has a blast radius too ────────────────────────────────────────────────────────────────
#
# GeoDeploy told an operator to switch Cloudflare to Full (strict). That setting is per-ZONE, and
# every other site in the zone started failing with 526. We did not touch those sites — we told
# someone to, which is the same thing from where they were sitting. "Do not break what you did not
# install" has to cover the advice, not only the files.

def test_neighbours_that_would_break_are_named(monkeypatch):
    monkeypatch.setattr(hostproxy, "_host_run", lambda *a, **k: (0,
        "==strict==\nshop.example.org untrusted\nblog.example.org ok\n"
        "old.example.org unreachable\n==end==\n"))
    failing = hostproxy.neighbours_failing_strict(
        "maps.example.org", ["shop.example.org", "blog.example.org", "old.example.org"])
    assert failing == ["shop.example.org"]


def test_a_name_that_merely_fails_to_answer_is_not_called_broken(monkeypatch):
    """`unreachable` is not `untrusted`. Naming a site as about-to-break when it is simply down
    sends the operator to fix the wrong thing, and makes the warning untrustworthy next time."""
    monkeypatch.setattr(hostproxy, "_host_run", lambda *a, **k: (0,
        "==strict==\ndown.example.org unreachable\n==end==\n"))
    assert hostproxy.neighbours_failing_strict("maps.example.org", ["down.example.org"]) == []


def test_our_own_domain_is_never_listed_as_a_neighbour(monkeypatch):
    called = []
    monkeypatch.setattr(hostproxy, "_host_run", lambda *a, **k: called.append(a) or (0, ""))
    assert hostproxy.neighbours_failing_strict("maps.example.org", ["maps.example.org"]) == []
    assert called == []                      # nothing to test means no container at all


def test_a_failed_measurement_returns_nothing_rather_than_a_guess(monkeypatch):
    """The caller says 'these will break'. It must never say 'nothing else will' on the strength of
    a probe that did not run."""
    def boom(*a, **k):
        raise hostproxy.ProxyError("no docker")
    monkeypatch.setattr(hostproxy, "_host_run", boom)
    assert hostproxy.neighbours_failing_strict("maps.example.org", ["shop.example.org"]) == []


def test_neighbour_names_go_through_the_hostname_validator(monkeypatch):
    """They come from the machine's own config rather than a request, but they still reach a shell
    loop — so the same gate applies, and anything that fails it is simply not tested."""
    seen = {}
    monkeypatch.setattr(hostproxy, "_host_run",
                        lambda script, **k: seen.update(script=script) or (0, "==strict==\n==end==\n"))
    hostproxy.neighbours_failing_strict("maps.example.org", ["ok.example.org", "bad;rm -rf /"])
    assert "ok.example.org" in seen["script"]
    assert "rm -rf" not in seen["script"]


def test_cloudflare_menu_paths_are_only_emitted_for_cloudflare(monkeypatch):
    """"Rules → Configuration Rules" is Cloudflare's vocabulary. Showing it to a Fastly or
    CloudFront user sends them hunting for a screen that does not exist, which is worse than
    generic advice."""
    from geodeploy.services import deployment
    monkeypatch.setattr(deployment, "resolve_domain", lambda d: ["203.0.113.9"])
    monkeypatch.setattr(deployment, "server_ip", lambda: {"public": "198.51.100.1", "outbound": None})
    assert hostproxy._is_cloudflare("maps.example.org") is False
    assert hostproxy._fronted_by_something("maps.example.org") is True


def test_a_domain_pointing_straight_at_us_has_nothing_in_front(monkeypatch):
    from geodeploy.services import deployment
    monkeypatch.setattr(deployment, "resolve_domain", lambda d: ["198.51.100.1"])
    monkeypatch.setattr(deployment, "server_ip", lambda: {"public": "198.51.100.1", "outbound": None})
    assert hostproxy._fronted_by_something("maps.example.org") is False


def test_an_unknowable_answer_stays_quiet(monkeypatch):
    """No DNS, or no idea what our own address is: say nothing rather than guess at somebody's CDN."""
    from geodeploy.services import deployment
    monkeypatch.setattr(deployment, "resolve_domain", lambda d: [])
    monkeypatch.setattr(deployment, "server_ip", lambda: {"public": None, "outbound": None})
    assert hostproxy._fronted_by_something("maps.example.org") is False


def test_the_scoped_rule_is_the_recommendation_even_when_no_neighbour_is_at_risk(monkeypatch):
    """Finding nothing at risk ON THIS MACHINE is not evidence the zone is safe — GeoDeploy cannot
    see records pointing at other servers. The scoped rule is correct either way, so it is the
    recommendation either way; the zone switch is never the default."""
    import geodeploy.services.hostproxy as hp
    _detected(monkeypatch)
    monkeypatch.setattr(hp, "read_state", lambda: {
        "adapter": {"kind": "nginx", "where": "host", "tls": "certbot",
                    "target": "/etc/nginx/conf.d/geodeploy.conf", "symlink": None,
                    "reload": "nginx -s reload"}})
    monkeypatch.setattr(hp, "_is_cloudflare", lambda d: True)
    monkeypatch.setattr(hp, "neighbours_failing_strict", lambda d, n: [])
    monkeypatch.setattr(hp, "_certbot", lambda *a, **k: (0, "Congratulations"))
    monkeypatch.setattr(hp, "_cert_mtime", lambda d: "1")
    monkeypatch.setattr(hp, "_host_run", lambda *a, **k: (0,
        "==write==\nok\n==test==\nok\nrc=0\n==reload==\ndone\nrc=0\n==route==\nskipped\n==end==\n"))
    result = hp.issue_certificate("maps.example.org", "me@example.org")
    cf = [s for s in result["steps"] if "Cloudflare" in s["name"]]
    assert cf, "there must be a Cloudflare step"
    assert "Configuration Rules" in cf[0]["detail"]
    # The zone-wide switch may be mentioned as an alternative, never as the instruction.
    assert "SSL/TLS → Overview" not in cf[0]["detail"]


# ── The three that a critical re-read found, after the feature was already "working" ────────────
#
# None of these needed a new machine to find — only asking "what does the NEXT press of this button
# do?". All three are one click away on an instance that is serving happily.

def test_apply_after_a_certificate_writes_the_HTTPS_shape(monkeypatch):
    """Severity 1. The panel offers "Apply again", and remove-then-reapply is the natural recovery.
    Rendering the plain port-80 block would overwrite a working HTTPS vhost — and behind Cloudflare
    on Full (strict), where Cloudflare connects to 443, nothing would claim the name there, the
    catch-all would answer, and every visitor would get 526. A downgrade on paper, an outage in
    fact."""
    _detected(monkeypatch, certs=["maps.example.org"])
    monkeypatch.setattr(hostproxy, "read_state", lambda: {
        "certificate": {"domain": "maps.example.org", "redirect": False}})
    plan = hostproxy.plan("maps.example.org", _INTENT)
    assert plan["has_certificate"] is True
    assert "listen 443 ssl" in plan["config"]
    assert "/etc/letsencrypt/live/maps.example.org/fullchain.pem" in plan["config"]


def test_apply_without_a_certificate_still_writes_plain_http(monkeypatch):
    """The other direction must not regress: a first apply has no certificate, and a 443 block
    naming files that do not exist fails nginx's test and takes the whole config with it."""
    _detected(monkeypatch, certs=[])
    monkeypatch.setattr(hostproxy, "read_state", lambda: {})
    plan = hostproxy.plan("maps.example.org", _INTENT)
    assert plan["has_certificate"] is False
    assert "listen 443 ssl" not in plan["config"]


def test_the_recorded_redirect_choice_survives_a_reapply(monkeypatch):
    """The no-redirect decision was made when the certificate was issued, from the Cloudflare check.
    Re-deciding it on every apply means a DNS blip can flip a Cloudflare-fronted site into the
    redirect loop `_nginx_conf_tls` exists to avoid."""
    _detected(monkeypatch, certs=["maps.example.org"])
    monkeypatch.setattr(hostproxy, "read_state", lambda: {
        "certificate": {"domain": "maps.example.org", "redirect": False}})
    monkeypatch.setattr(hostproxy, "_is_cloudflare", lambda d: False)   # would say "redirect" today
    body = hostproxy.plan("maps.example.org", _INTENT)["config"]
    port80 = body.split("server {", 1)[1].split("server {", 1)[0]
    assert "return 301" not in port80


def test_a_failed_test_restores_the_previous_file_rather_than_deleting_it():
    """`rm` is right only when the file is NEW. Replacing a working vhost and then deleting it on a
    failed test turns "the new config was rejected" into "the site has no configuration at all"."""
    script = hostproxy._APPLY_HOST.format(
        target="/etc/nginx/conf.d/geodeploy.conf", link="", test_cmd="true", reload_cmd="true",
        domain="maps.example.org", probe_port="80", probe_tls="")
    assert 'cp "$TARGET" "$TARGET.gd-prev"' in script
    rollback = script.split("say rollback", 1)[1].split("say reload", 1)[0]
    assert 'mv -f "$TARGET.gd-prev" "$TARGET"' in rollback
    assert "echo restored" in rollback
    # and the backup must not survive a success, where a stale copy would confuse the next rollback
    assert 'rm -f "$TARGET.gd-prev"' in script


def test_the_backup_name_is_not_picked_up_by_a_dropin_glob():
    """conf.d/*.conf and conf.d/*.caddy must not match the backup, or nginx loads two copies of the
    same server block while it sits there."""
    for suffix in (".conf", ".caddy"):
        assert not f"geodeploy{suffix}.gd-prev".endswith(suffix)


def test_reapplying_the_same_domain_keeps_the_certificate_record(monkeypatch):
    """Dropping it meant renewal silently stopped: the site still served HTTPS, the daily task found
    no certificate in state, and it failed for every visitor 90 days later."""
    monkeypatch.setattr(hostproxy, "read_state", lambda: {
        "domain": "maps.example.org", "tls": True,
        "certificate": {"domain": "maps.example.org", "at": 1}})
    state = hostproxy._next_state("maps.example.org", dict(_NGINX_ADAPTER), applied=True)
    assert state["certificate"]["domain"] == "maps.example.org"
    assert state["tls"] is True


def test_changing_the_domain_clears_the_certificate_record(monkeypatch):
    """That certificate is no longer the one this installation serves; renewing it would keep a name
    alive that nothing here uses."""
    monkeypatch.setattr(hostproxy, "read_state", lambda: {
        "domain": "old.example.org", "tls": True,
        "certificate": {"domain": "old.example.org", "at": 1}})
    state = hostproxy._next_state("new.example.org", dict(_NGINX_ADAPTER), applied=True)
    assert "certificate" not in state
    assert not state.get("tls")
