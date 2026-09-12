"""Where is this GeoDeploy published, who can reach it, and what would a reverse proxy need?

Three separate questions, and the panel is only useful because it answers all three side by side:

  INTENT    what `.env` asks for  — GEODEPLOY_DEPLOY_MODE / _HTTP_BIND / _HTTP_PORT
  REALITY   what the nginx container is ACTUALLY publishing, read through the Docker socket
  OBSERVED  the scheme and host of the request being served right now

They come apart in ways that are individually silent and jointly diagnosable. Intent ≠ reality means
`.env` was edited and nginx was never recreated — the class of bug that has bitten this project in
three other places (nginx.conf's single-file mount, .env's inode, the portals mount). Observed ≠
intent usually means a reverse proxy in front is not passing `Host`, which produces shared links and
portal `og:` tags pointing at an address nobody else can open, days later and far from the cause.

NOTHING HERE WRITES ANYTHING, on the host or in the container. Generating a config an operator pastes
is deliberate: the machines this feature exists for are machines with other people's websites on
them, and a bad `nginx -s reload` takes all of them down. Moving GeoDeploy's own port is
`installer/set-port.sh`, which is preflighted and rolls back.
"""
from __future__ import annotations

import ipaddress
import json
import os

from ..config import get_settings
from . import envfile

# Must match the `:-` defaults in docker-compose.yml and installer/lib-deploy.sh. An installation
# whose .env predates these keys is on 0.0.0.0:80 and always has been.
DEFAULT_BIND = "0.0.0.0"
DEFAULT_PORT = "80"


# ── Intent ────────────────────────────────────────────────────────────────────────────────────────

def read_intent() -> dict:
    """What .env asks for. Read per request: .env is bind-mounted, so the file on disk is the file
    the host has, and an installer or set-port.sh run is visible immediately."""
    try:
        env = envfile.read_all()
    except Exception:
        env = {}
    bind = (env.get("GEODEPLOY_HTTP_BIND") or DEFAULT_BIND).strip()
    port = (env.get("GEODEPLOY_HTTP_PORT") or DEFAULT_PORT).strip()
    mode = (env.get("GEODEPLOY_DEPLOY_MODE") or "").strip()
    if not mode:
        mode = "behind-proxy" if bind.startswith("127.") else "dedicated"
    return {"mode": mode, "bind": bind, "port": port}


def domain_hint() -> str | None:
    """A domain the operator typed during install. Only a hint — it saves them retyping it here, and
    it is never treated as configured truth (nothing verifies it until they press Verify)."""
    path = os.path.join(get_settings().data_dir, "temp", "deploy-hints.json")
    try:
        with open(path, encoding="utf-8") as fh:
            value = json.load(fh).get("domain")
        return value.strip() or None if isinstance(value, str) else None
    except Exception:
        return None


# ── Reality ───────────────────────────────────────────────────────────────────────────────────────

def _mapping_is_live(attrs: dict) -> bool | None:
    """Is the host port mapping actually ESTABLISHED, as opposed to merely requested?

    Those are different, and the gap between them is a real state (measured 2026-09-12): if the host
    port is occupied when the container starts, the bind fails — and a later `docker compose up -d`
    or `restart`, once the port is free, returns the container to `running` with the correct
    `HostConfig.PortBindings` and NO HOST MAPPING AT ALL. `docker compose ps` says Up, `ss` shows
    nothing, every request is refused, and only `--force-recreate` repairs it.

    Docker distinguishes the two itself, which is what makes this cheap and reliable:

        HostConfig.PortBindings   what was ASKED for   — still {"80/tcp": [{…8090}]} when wedged
        NetworkSettings.Ports     what is IN EFFECT    — {} when wedged, identical when healthy

    A socket probe would be the obvious alternative and is WRONG here: this code runs inside the API
    container, where the host's 127.0.0.1 is unreachable *by design* in behind-proxy mode. It would
    report every healthy loopback install as dead — a false alarm on the most common configuration,
    which is worse than not checking at all.
    """
    runtime = (attrs.get("NetworkSettings") or {}).get("Ports")
    if runtime is None:
        return None                       # not running, or nothing to say
    return bool(runtime.get("80/tcp"))


def read_reality() -> dict:
    """What the nginx container actually publishes. `None` for `bind`/`port` means we could not tell
    (no Docker socket, nginx not running) — which is NOT the same as "nothing published", and the
    caller must not report a mismatch on the strength of it."""
    out: dict = {"bind": None, "port": None, "running": None, "listening": None, "error": None}
    try:
        import docker
        client = docker.from_env()
    except Exception as exc:
        out["error"] = f"Docker is not reachable: {exc}"
        return out
    # WHICH nginx? On the machines this feature exists for there is very likely more than one — that
    # is the entire premise — so "the first container with nginx in its name" would happily report
    # somebody else's reverse proxy as ours. Ask Compose instead: find our OWN container by hostname
    # (Docker sets it to the short container id), read its project label, and take the `nginx`
    # service from that project. Name matching stays as the fallback for a stack started some other
    # way, and is scoped to our own naming.
    container = None
    try:
        containers = client.containers.list(all=True)
        project = None
        try:
            me = client.containers.get(os.uname().nodename)
            project = (me.labels or {}).get("com.docker.compose.project")
        except Exception:
            project = None
        if project:
            for candidate in containers:
                labels = candidate.labels or {}
                if (labels.get("com.docker.compose.project") == project
                        and labels.get("com.docker.compose.service") == "nginx"):
                    container = candidate
                    break
        if container is None:
            for candidate in containers:
                if "nginx" in candidate.name and "geodeploy" in candidate.name:
                    container = candidate
                    break
    except Exception as exc:
        out["error"] = str(exc)
        return out
    if container is None:
        out["error"] = "No nginx container found."
        return out
    out["running"] = container.status == "running"
    try:
        bindings = (container.attrs.get("HostConfig") or {}).get("PortBindings") or {}
        for spec in bindings.get("80/tcp") or []:
            # An empty HostIp means every interface. A container created from the old literal
            # `80:80` reports exactly that, and calling it a mismatch against `0.0.0.0:80` would
            # make every pre-existing install look misconfigured.
            out["bind"] = spec.get("HostIp") or DEFAULT_BIND
            out["port"] = str(spec.get("HostPort") or "")
            break
    except Exception as exc:
        out["error"] = str(exc)
    if out["running"] and out["port"]:
        out["listening"] = _mapping_is_live(container.attrs)
    return out


# ── Observed ──────────────────────────────────────────────────────────────────────────────────────

def _looks_like_a_domain(host: str) -> bool:
    """A name someone else could type into a browser — as opposed to a loopback address, a bare IP,
    or a container name."""
    name = (host or "").split(":")[0].strip().lower()
    if not name or name in ("localhost", "localhost.localdomain"):
        return False
    try:
        ipaddress.ip_address(name)
        return False           # an IP is reachable but is not a domain; no TLS story either
    except ValueError:
        pass
    return "." in name and not name.endswith(".localhost")


def observe(request) -> dict:
    """The origin of THIS request, plus whether it arrived through a proxy in front of our nginx.

    Detecting the outer proxy matters more than it looks. `Host: 127.0.0.1:8080` has two completely
    different causes — an SSH tunnel (fine, expected, the operator is looking at the dashboard the
    only way they can) and a reverse proxy that forgot `proxy_set_header Host $host` (broken, and
    silently produces unopenable links) — and telling the operator the wrong one is worse than
    saying nothing.

    Our own nginx always sends `X-Forwarded-For $proxy_add_x_forwarded_for` on /api/, which APPENDS
    the client to whatever arrived. So two or more entries means something forwarded to us before
    nginx did. A terminated `https` is the other tell, since our nginx only ever speaks plain HTTP.
    """
    headers = request.headers
    host = headers.get("host") or request.url.netloc
    proto = headers.get("x-forwarded-proto") or request.url.scheme
    xff = [p.strip() for p in (headers.get("x-forwarded-for") or "").split(",") if p.strip()]
    behind_outer_proxy = len(xff) >= 2 or proto == "https"
    return {
        "scheme": proto,
        "host": host,
        "origin": f"{proto}://{host}",
        "is_domain": _looks_like_a_domain(host),
        "behind_outer_proxy": behind_outer_proxy,
        "forwarded_for_depth": len(xff),
    }


# ── The verdict ───────────────────────────────────────────────────────────────────────────────────

def verdict(intent: dict, reality: dict, observed: dict) -> dict:
    """One state, in the operator's terms. Ordered by severity: a genuine misconfiguration outranks
    an incomplete setup, which outranks a working one."""
    bind = intent["bind"]
    port = intent["port"]
    local = f"http://127.0.0.1{'' if port == '80' else ':' + port}"

    # The port is published in Docker's config and never actually bound — see _mapping_is_live.
    # Reported first
    # because every other signal in this panel says the instance is fine, and while this holds they
    # are all describing a container nobody can reach. (You are reading this page, so your own route
    # in still works — an SSH tunnel to a DIFFERENT port, or the proxy hitting the container
    # directly — which is exactly why nothing else notices.)
    if reality.get("running") and reality.get("listening") is False:
        return {
            "level": "critical",
            "title": f"Nothing is listening on port {reality['port']}",
            "detail": (
                f"nginx is running and Docker says it publishes {reality['bind']}:{reality['port']}, "
                "but the port is closed. This happens when the port was occupied at the moment the "
                "container started: the bind failed, and neither a restart nor `up -d` re-establishes "
                "it — only recreating the container does."
            ),
            "fix": "docker compose up -d --force-recreate nginx",
        }

    # Intent vs reality — while this holds, everything else is being read off a stale container.
    if reality.get("port") and (reality["port"] != port or reality["bind"] != bind):
        return {
            "level": "critical",
            "title": "GeoDeploy is not published where .env says it is",
            "detail": (
                f".env asks for {bind}:{port} but the running nginx publishes "
                f"{reality['bind']}:{reality['port']}. Editing .env does not move a running "
                f"container — the settings only apply when it is recreated."
            ),
            "fix": f"sudo bash installer/set-port.sh {port}",
        }

    if observed["behind_outer_proxy"] and not observed["is_domain"]:
        return {
            "level": "critical",
            "title": "Your reverse proxy is not passing the visitor's hostname",
            "detail": (
                f"Requests reach GeoDeploy through a proxy, but arrive as {observed['origin']}. "
                "Every link GeoDeploy generates is built from that — shared links, portal previews, "
                "the STAC and OGC catalogues — so they will all point at an address nobody else can "
                "open."
            ),
            "fix": "Add  proxy_set_header Host $host;  to the proxy's GeoDeploy block.",
        }

    if observed["behind_outer_proxy"] and observed["is_domain"]:
        if observed["scheme"] != "https":
            return {
                "level": "warning",
                "title": f"Reachable at {observed['origin']}, but without HTTPS",
                "detail": (
                    "Sign-in tokens and uploads travel in clear text. Your reverse proxy is already "
                    "in the right place to terminate TLS."
                ),
                "fix": "Give the proxy a certificate — Caddy does it automatically; nginx uses certbot.",
            }
        return {
            "level": "ok",
            "title": f"Requests are arriving as {observed['origin']}",
            "detail": "That is what every link GeoDeploy generates will say. Your reverse proxy is set up correctly.",
            "fix": None,
        }

    if bind.startswith("127."):
        return {
            "level": "warning",
            "title": "Only this machine can reach GeoDeploy",
            "detail": (
                f"GeoDeploy is published on {bind}:{port} and nothing outside this server can open "
                f"it — which is the point of this mode. You are most likely looking at this through "
                f"an SSH tunnel. Give it a domain and a reverse proxy in front."
            ),
            "fix": None,
            "action": "configure-domain",
        }

    return {
        "level": "warning",
        "title": f"GeoDeploy is open to the network at {observed['origin']}, without HTTPS",
        "detail": (
            "Sign-in tokens travel in clear text. Put a reverse proxy with a certificate in front, "
            f"and move GeoDeploy to a local port ({local}) so only that proxy can reach it."
        ),
        "fix": "sudo bash installer/set-port.sh 8080",
        "action": "configure-domain",
    }


# ── The reverse-proxy configuration ───────────────────────────────────────────────────────────────

# Every directive below is here because its absence is a REAL GeoDeploy failure, not because it is
# conventional. They are commented in the generated output for the same reason: an operator pasting
# a block into a server that already hosts other people's sites deserves to know what each line is
# for. The ones that are easy to drop and expensive to debug:
#
#   Host                    — without it every generated URL points at 127.0.0.1
#   client_max_body_size    — the outer nginx's 1 MB default SHADOWS our 11G and 413s every upload
#   proxy_request_buffering — we turn it off to stream 10 GB straight to storage; the outer proxy
#                             turns it back on and spools the whole body to its own disk first
#   X-Forwarded-Proto       — cookies lose Secure and every emitted URL says http:// on an HTTPS site

def _nginx_conf(domain: str, upstream: str) -> str:
    return f"""# GeoDeploy — put this in /etc/nginx/sites-available/geodeploy.conf and symlink it into
# sites-enabled/, or drop it in /etc/nginx/conf.d/. Then:  sudo nginx -t && sudo systemctl reload nginx
#
# This does not replace anything you already serve; it adds one more virtual host.
server {{
    listen 80;
    listen [::]:80;
    server_name {domain};

    # For certbot. Run:  sudo certbot --nginx -d {domain}
    location /.well-known/acme-challenge/ {{ root /var/www/html; }}

    location / {{
        proxy_pass {upstream};

        # REQUIRED. GeoDeploy builds every absolute URL it emits — shared links, portal og: tags,
        # the STAC and OGC catalogues — from this header. Without it they all say 127.0.0.1.
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        # REQUIRED once you have HTTPS: session cookies take their Secure flag from this, and
        # GeoDeploy uses it to decide whether to emit http:// or https:// links.
        proxy_set_header X-Forwarded-Proto $scheme;

        # REQUIRED. nginx defaults to 1 MB, and THIS setting shadows GeoDeploy's own — without it
        # every upload over a megabyte fails with 413.
        client_max_body_size 11G;
        # REQUIRED for large uploads. GeoDeploy streams them straight through to object storage;
        # buffering here would spool the entire file to this server's disk first.
        proxy_request_buffering off;

        # Ingests, tiling and large downloads outlive nginx's 60s default.
        proxy_read_timeout 600s;
        proxy_send_timeout 600s;

        proxy_http_version 1.1;
        proxy_set_header Upgrade    $http_upgrade;
        proxy_set_header Connection "upgrade";
    }}
}}
"""


def _caddy_conf(domain: str, upstream: str) -> str:
    return f"""# GeoDeploy — add this to your Caddyfile, then:  sudo systemctl reload caddy
#
# Caddy needs none of the header and buffering directives nginx does: reverse_proxy sets Host,
# X-Forwarded-For and X-Forwarded-Proto itself, streams request bodies without buffering, and has no
# request-size limit. It also obtains and renews the TLS certificate on its own — so this really is
# the whole configuration, and if you are choosing a reverse proxy for a machine you control, this
# is the easiest correct answer available.
{domain} {{
    reverse_proxy {upstream}
}}
"""


def _apache_conf(domain: str, upstream: str) -> str:
    return f"""# GeoDeploy — /etc/apache2/sites-available/geodeploy.conf
#   sudo a2enmod proxy proxy_http headers
#   sudo a2ensite geodeploy && sudo apache2ctl configtest && sudo systemctl reload apache2
<VirtualHost *:80>
    ServerName {domain}

    # ProxyPreserveHost is Apache's equivalent of nginx's `proxy_set_header Host $host`, and
    # GeoDeploy builds every absolute URL it emits from that header.
    ProxyPreserveHost On
    ProxyRequests Off
    ProxyPass        / {upstream}/
    ProxyPassReverse / {upstream}/

    # Cookies take their Secure flag from this, and it decides http:// vs https:// in emitted URLs.
    RequestHeader set X-Forwarded-Proto "http"

    # Long ingests and large downloads.
    ProxyTimeout 600
    # Apache streams request bodies by default, so large uploads need no extra setting here.
</VirtualHost>
"""


def _traefik_conf(domain: str, port: str) -> str:
    return f"""# GeoDeploy — Traefik in Docker. Traefik must be able to REACH GeoDeploy, and in
# behind-proxy mode the host port is on 127.0.0.1 where a container cannot see it. So join Traefik
# to GeoDeploy's network and route to the container directly, rather than to the host port:
#
#     docker network connect geodeploy <your-traefik-container>
#
# Then add these labels to the `nginx` service in GeoDeploy's docker-compose.yml (a
# docker-compose.override.yml is the tidy place, so updates do not overwrite them):
services:
  nginx:
    labels:
      - "traefik.enable=true"
      - "traefik.docker.network=geodeploy"
      - "traefik.http.routers.geodeploy.rule=Host(`{domain}`)"
      - "traefik.http.routers.geodeploy.entrypoints=websecure"
      - "traefik.http.routers.geodeploy.tls.certresolver=letsencrypt"
      - "traefik.http.services.geodeploy.loadbalancer.server.port=80"

# Traefik forwards Host and sets X-Forwarded-* by default, and does not buffer request bodies or
# cap their size — so, as with Caddy, there is nothing else to set.
# Routing to the container means the host port ({port}) is not involved at all.
"""


FLAVORS = ("nginx", "caddy", "apache", "traefik")


def proxy_config(domain: str, flavor: str, intent: dict) -> dict:
    """The configuration for ONE reverse proxy, plus the steps around it. Never written anywhere by
    us — the operator pastes it, on their own machine, having read it."""
    port = intent["port"]
    upstream = f"http://127.0.0.1:{port}"
    domain = (domain or "geodeploy.example.org").strip()

    if flavor == "caddy":
        body, path = _caddy_conf(domain, upstream), "/etc/caddy/Caddyfile"
        test, reload_cmd = "caddy validate --config /etc/caddy/Caddyfile", "sudo systemctl reload caddy"
    elif flavor == "apache":
        body, path = _apache_conf(domain, upstream), "/etc/apache2/sites-available/geodeploy.conf"
        test, reload_cmd = "sudo apache2ctl configtest", "sudo systemctl reload apache2"
    elif flavor == "traefik":
        body, path = _traefik_conf(domain, port), "docker-compose.override.yml"
        test, reload_cmd = "", "docker compose up -d nginx"
    else:
        body, path = _nginx_conf(domain, upstream), "/etc/nginx/sites-available/geodeploy.conf"
        test, reload_cmd = "sudo nginx -t", "sudo systemctl reload nginx"

    warnings: list[str] = []
    if not intent["bind"].startswith("127."):
        warnings.append(
            f"GeoDeploy is currently published on {intent['bind']}:{port}, so it is reachable "
            f"directly as well as through the proxy — and a Docker publish on 0.0.0.0 is not "
            f"covered by ufw. Once the proxy works, run  sudo bash installer/set-port.sh {port}  "
            f"to move it to 127.0.0.1."
        )
    return {
        "flavor": flavor,
        "domain": domain,
        "upstream": upstream,
        "path": path,
        "config": body,
        "test_command": test,
        "reload_command": reload_cmd,
        "warnings": warnings,
        "steps": [
            f"Point {domain} at this server: an A record for its public IP address.",
            f"Save the configuration below as {path}.",
            *([f"Check it: {test}"] if test else []),
            f"Apply it: {reload_cmd}",
            "Come back here and press Verify.",
        ],
    }
