"""Configure the HOST's reverse proxy for a domain — without disrupting what it already serves.

`deployment.py` answers "where am I and what would a proxy in front need?" and deliberately writes
nothing. This module is the other half: it *applies* that configuration to whatever proxy the machine
already runs — nginx, Caddy or Apache, on the host or in a container — so the operator's whole job is
a DNS record and a domain typed into the dashboard.

WHY THIS IS SAFE TO DO AT ALL, stated plainly because it is the first question anyone should ask.
The API container already mounts `/var/run/docker.sock`, which is root on the host by any measure —
anyone who can reach it can start a privileged container and do anything. This module uses exactly
that, through a one-shot helper container, so it adds NO new capability to the deployment; what it
adds is a small, audited, reversible set of operations instead of an open-ended one. It is still the
most powerful thing GeoDeploy does, which is why every write goes through `apply()` and its checks.

THE SIX RULES. Every one of them exists because breaking it would take down somebody else's website,
and these machines are defined by having somebody else's website on them.

  R1  We only ever CREATE one new file (plus at most one symlink), at a path that is ours by name and
      carries our marker. We never edit, rewrite or reformat a file we did not write — not
      `nginx.conf`, not the Caddyfile, not another vhost. If our own path exists without the marker,
      we stop: it is somebody else's file that happens to share the name.

  R2  We TEST THE EXISTING CONFIGURATION BEFORE WE TOUCH ANYTHING, and refuse if it does not pass.
      This is the single most important check in the module. A machine whose nginx config is already
      broken — edited and never reloaded, which is extremely common — is a machine where OUR reload
      is what finally surfaces the breakage, and from the operator's chair GeoDeploy did it. We are
      not willing to be that, so a config that was broken before we arrived is a blocker, reported
      with the real `nginx -t` output and nothing written.

  R3  We RELOAD, never restart. A reload keeps serving existing connections, and a proxy that
      rejects the new configuration keeps running the old one. A restart drops connections and can
      leave the proxy down.

  R4  We test again AFTER writing, and a failed test means we remove what we wrote, test once more to
      prove the machine is back as we found it, and NEVER reload. A bad config that is never loaded
      has harmed nobody.

  R5  We refuse a domain that the existing configuration already serves. Two server blocks claiming
      one name is not an error in nginx — the first one wins and ours silently does nothing, or
      theirs silently stops working. Either way somebody's site changed behaviour because we wrote a
      file, which is the thing we promised not to do.

  R6  We refuse to write into a directory the main configuration does not actually include. A file
      in `conf.d` on a machine whose `nginx.conf` has no `include conf.d/*.conf` is a silent no-op:
      we would report success, the domain would not work, and the operator would have no way to tell
      that from a DNS problem.

WHAT IS DELIBERATELY NOT AUTOMATED. Traefik: its routing lives in container labels, a file provider,
or a Kubernetes CRD, and there is no single file we can drop that is correct across those — so it
gets precise manual instructions instead of a button, which is `deployment.py`'s Traefik block.
Installing a proxy that is not there: a machine with no reverse proxy is not a machine we should be
installing one on. Obtaining a certificate is a SEPARATE, explicitly consented action
(`issue_certificate`), because it reaches an external CA, publishes the domain to a public log, and
has its own rate limits.
"""
from __future__ import annotations

import io
import json
import os
import re
import tarfile
import time

from ..config import get_settings

MARKER = "managed by GeoDeploy"

# A name we would be willing to put in a shell command. Stricter than the router's validator on
# purpose: this is the last line of defence before a string reaches `sh -c`, and the two checks are
# independent so a bug in one does not disarm the other. No underscore, no uppercase, no trailing
# dot — a hostname, nothing else.
_SAFE_DOMAIN = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+")
_SAFE_EMAIL = re.compile(r"[A-Za-z0-9._%+-]{1,64}@[a-z0-9.-]{1,190}\.[a-z]{2,24}")

# Images we are willing to run the helper from, best first. `nginx:alpine` is OUR OWN ingress image,
# so it is already present on every GeoDeploy host and needs no pull — which matters because this
# runs on machines that may have no outbound internet at all. All three are busybox-based and give
# us the four commands the helper needs: sh, chroot, cp and kill.
_HELPER_IMAGES = ("nginx:alpine", "alpine:latest", "alpine:3", "busybox:latest")

# Where the helper sees the host filesystem. Everything the scripts below touch is under here, which
# makes "did this script stay inside the host tree" a property you can read off the path.
HOST = "/host"


class ProxyError(RuntimeError):
    """Something went wrong that the operator needs to read, not a bug to swallow."""


def _safe_domain(raw: str) -> str:
    domain = (raw or "").strip().lower().rstrip(".")
    if not _SAFE_DOMAIN.fullmatch(domain) or len(domain) > 253:
        raise ProxyError("That does not look like a domain name (for example: maps.example.org).")
    return domain


def _state_path() -> str:
    return os.path.join(get_settings().data_dir, "temp", "hostproxy.json")


def read_state() -> dict:
    """What we last wrote, if anything. The record is what makes removal exact rather than a guess:
    it names the file and the symlink we created, so taking them away restores precisely the machine
    we found. Kept in the bind-mounted `data/temp`, so it survives a container rebuild."""
    try:
        with open(_state_path(), encoding="utf-8") as fh:
            value = json.load(fh)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _write_state(value: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_state_path()), exist_ok=True)
        with open(_state_path(), "w", encoding="utf-8") as fh:
            json.dump(value, fh, indent=2)
    except Exception:
        pass            # the state file is a convenience for removal, never a precondition


# ── The helper container ──────────────────────────────────────────────────────────────────────────

def _client():
    try:
        import docker
    except Exception as exc:                                  # pragma: no cover - import guard
        raise ProxyError(f"The Docker library is unavailable: {exc}") from exc
    try:
        return docker.from_env()
    except Exception as exc:
        raise ProxyError(
            "Docker is not reachable from GeoDeploy, so it cannot configure this machine's web "
            "server. Use the configuration below and apply it yourself."
        ) from exc


def _helper_image(client) -> str:
    """The first candidate image already on this machine; pull only as a last resort.

    Checking first is not an optimisation. These are shared machines, often with egress rules, and a
    silent 30-second pull in the middle of an "Apply" that the operator is watching reads as a hang.
    """
    for name in _HELPER_IMAGES:
        try:
            client.images.get(name)
            return name
        except Exception:
            continue
    try:
        client.images.pull("alpine:latest")
        return "alpine:latest"
    except Exception as exc:
        raise ProxyError(
            "No suitable local image to run the configuration helper, and pulling one failed: "
            f"{exc}"
        ) from exc


def _tar_payload(files: dict[str, bytes]) -> bytes:
    """A tar of `{name: bytes}` for `put_archive`.

    The only route by which operator-supplied text — the domain, inside a generated config — reaches
    the host. It travels as the CONTENT of a file, never as part of a shell command, so no amount of
    quoting creativity in a domain name can become a command. The scripts below interpolate only
    paths from this module's own tables.
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, blob in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(blob)
            info.mode = 0o644
            info.mtime = int(time.time())
            tar.addfile(info, io.BytesIO(blob))
    return buf.getvalue()


def _host_run(script: str, files: dict[str, bytes] | None = None, timeout: int = 180) -> tuple[int, str]:
    """Run one short POSIX script against the host, in a throwaway container, and return (exit, out).

    `privileged`, `pid_mode=host`, `network_mode=host` and `/` bind-mounted at `/host` are each
    load-bearing, and all four together are what make this equivalent to a root shell on the host:

      /:/host        the proxy's configuration, and its own binary — we run the HOST's `nginx -t`,
                     via `chroot`, never this image's. A different nginx build tests a different set
                     of modules and would report problems that are not there (and miss ones that are).
      pid_mode       `nginx -s reload` signals the PID in the host's pidfile. Without the host PID
                     namespace that PID means nothing here and the signal goes nowhere, or worse,
                     somewhere else.
      network_mode   `caddy reload` talks to Caddy's admin socket on localhost, and the post-apply
                     check asks the proxy for our own page over the host's loopback.
      privileged     chroot and signalling processes we do not own.

    The mount is READ-WRITE even for the probe, which looks careless and is not: `nginx -t` opens
    its error log for append, so on a read-only mount a perfectly good configuration fails the test —
    a false blocker on the exact check R2 relies on being trustworthy.

    Auto-remove is NOT used: the whole value is in the output, and a container that removes itself on
    exit frequently takes its logs with it before they can be read.
    """
    client = _client()
    image = _helper_image(client)
    container = None
    try:
        container = client.containers.create(
            image=image,
            entrypoint=["/bin/sh", "-c"],
            command=[script],
            volumes={"/": {"bind": HOST, "mode": "rw"}},
            privileged=True,
            pid_mode="host",
            network_mode="host",
            name=f"geodeploy-hostproxy-{int(time.time() * 1000) % 100000000}",
        )
        if files:
            container.put_archive("/tmp", _tar_payload(files))
        container.start()
        result = container.wait(timeout=timeout)
        out = container.logs(stdout=True, stderr=True).decode("utf-8", "replace")
        return int(result.get("StatusCode", 1)), out
    except ProxyError:
        raise
    except Exception as exc:
        raise ProxyError(f"The configuration helper could not run: {exc}") from exc
    finally:
        if container is not None:
            try:
                container.remove(force=True)
            except Exception:
                pass


# ── Reading the machine ───────────────────────────────────────────────────────────────────────────

# Fixed text, no interpolation, and every command is read-only apart from the log file `nginx -t`
# may append to. Output is sectioned so one missing tool cannot shift the meaning of another
# section's lines — a `grep` that finds nothing simply yields an empty section.
_PROBE = r"""
H=/host
section() { echo "==$1=="; }

section os
sed -n 's/^ID=//p;s/^ID_LIKE=//p' $H/etc/os-release 2>/dev/null | tr -d '"'

section listeners
{ netstat -lntp 2>/dev/null || ss -lntp 2>/dev/null; } \
  | awk '$4 ~ /[:.](80|443)$/ { print $4, $NF }'

section binaries
for b in usr/sbin/nginx usr/bin/nginx usr/bin/caddy usr/local/bin/caddy \
         usr/sbin/apache2 usr/sbin/httpd usr/bin/certbot usr/sbin/certbot snap/bin/certbot ; do
  [ -x "$H/$b" ] && echo "/$b"
done

section units
for u in nginx caddy apache2 httpd ; do
  [ -f "$H/lib/systemd/system/$u.service" ] || [ -f "$H/etc/systemd/system/$u.service" ] \
    || [ -f "$H/usr/lib/systemd/system/$u.service" ] && echo "$u"
done

section dirs
for d in etc/nginx etc/nginx/conf.d etc/nginx/sites-enabled etc/nginx/sites-available \
         etc/caddy etc/caddy/conf.d etc/apache2 etc/apache2/sites-enabled \
         etc/apache2/sites-available etc/apache2/conf-enabled etc/httpd etc/httpd/conf.d ; do
  [ -d "$H/$d" ] && echo "/$d"
done

section includes
grep -rhoE '^[[:space:]]*include[[:space:]]+[^;]+' $H/etc/nginx/nginx.conf 2>/dev/null
grep -rhoE '^[[:space:]]*(import|include)[[:space:]]+[^[:space:]]+' $H/etc/caddy/Caddyfile 2>/dev/null
grep -rhoE '^[[:space:]]*Include(Optional)?[[:space:]]+[^[:space:]]+' \
     $H/etc/apache2/apache2.conf $H/etc/httpd/conf/httpd.conf 2>/dev/null

# The names this machine serves — SKIPPING OUR OWN FILE. R5 refuses a domain that is already
# configured here, and our own file configures exactly that domain: sweeping it in makes GeoDeploy
# collide with itself, so the SECOND visit to the panel refuses to re-apply or change the domain and
# blames "something else". Reported from the field on 2026-10-09, after a successful first apply.
section servernames
for f in $(find $H/etc/nginx $H/etc/apache2 $H/etc/httpd $H/etc/caddy -type f 2>/dev/null | head -400); do
  case "$f" in */geodeploy.conf|*/geodeploy.caddy) continue ;; esac
  grep -hoE '^[[:space:]]*server_name[[:space:]]+[^;]+' "$f" 2>/dev/null
  grep -hioE '^[[:space:]]*Server(Name|Alias)[[:space:]]+[^[:space:]]+' "$f" 2>/dev/null
  grep -hoE '^[a-z0-9][a-z0-9.*-]+[a-z0-9][[:space:]]*\{' "$f" 2>/dev/null
done | head -300

section ourfiles
for f in etc/nginx/conf.d/geodeploy.conf etc/nginx/sites-available/geodeploy.conf \
         etc/nginx/sites-enabled/geodeploy.conf etc/caddy/conf.d/geodeploy.caddy \
         etc/apache2/sites-available/geodeploy.conf etc/apache2/sites-enabled/geodeploy.conf \
         etc/httpd/conf.d/geodeploy.conf ; do
  if [ -L "$H/$f" ]; then
    printf '%s link\n' "/$f"
  elif [ -f "$H/$f" ]; then
    if grep -qF 'managed by GeoDeploy' "$H/$f" ; then printf '%s ours\n' "/$f"
    else printf '%s foreign\n' "/$f" ; fi
  fi
done

# NEVER `cmd | tail; echo rc=$?` — in a pipeline `$?` is the status of the LAST command, so `tail`
# succeeding would report a FAILING `nginx -t` as a pass. That inverts the one check R2 depends on:
# a machine whose config is already broken would be declared healthy and then reloaded. Capture
# first, trim after.
# Which server blocks claim which ports, from the MERGED configuration (`nginx -T`) rather than from
# the files — includes are followed, so a block in a file we never looked at still counts.
#
# This exists because of a failure found in the field (2026-10-09): our block listens on 80 only, the
# machine had a `listen 443 ssl; server_name _;` catch-all for another site, and Cloudflare connects
# to the origin on 443 in Full mode. Result: port 80 served GeoDeploy perfectly, every real visitor
# got the other website, and every check we had said success. Associating listens with server_names
# is what lets us say that BEFORE the operator finds out from a browser.
#
# awk assigns each listen/server_name to the most recent `server {`. Sound for nginx: `location`
# blocks contain neither directive, and `upstream`'s `server 1.2.3.4;` has no brace.
section nginxblocks
NGINXBIN=""
for c in /usr/sbin/nginx /usr/bin/nginx /usr/local/sbin/nginx ; do
  [ -x "$H$c" ] && { NGINXBIN="$c"; break; }
done
if [ -n "$NGINXBIN" ]; then
  chroot $H "$NGINXBIN" -T 2>/dev/null | awk '
    /^# configuration file / { ours = ($0 ~ /geodeploy\.(conf|caddy):/) }
    /server[ \t]*\{/ { b++; cur="b" b; if (ours) print cur, "GEODEPLOY_OWN" }
    /^[ \t]*listen[ \t]/ { if (cur != "") print cur, $0 }
    /^[ \t]*server_name[ \t]/ { if (cur != "") print cur, $0 }
  ' | head -400
fi

section nginxtest
if [ -n "$NGINXBIN" ]; then
  OUT=$(chroot $H "$NGINXBIN" -t 2>&1) ; RC=$? ; echo "$OUT" | tail -8 ; echo "rc=$RC"
fi

section apachetest
if [ -x $H/usr/sbin/apache2 ]; then
  OUT=$(chroot $H /usr/sbin/apache2ctl configtest 2>&1) ; RC=$? ; echo "$OUT" | tail -8 ; echo "rc=$RC"
elif [ -x $H/usr/sbin/httpd ]; then
  OUT=$(chroot $H /usr/sbin/httpd -t 2>&1) ; RC=$? ; echo "$OUT" | tail -8 ; echo "rc=$RC"
fi

section caddytest
for c in usr/bin/caddy usr/local/bin/caddy ; do
  if [ -x "$H/$c" ]; then
    OUT=$(chroot $H "/$c" validate --config /etc/caddy/Caddyfile 2>&1) ; RC=$?
    echo "$OUT" | tail -8 ; echo "rc=$RC" ; break
  fi
done
echo "==end=="
"""


def _sections(text: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    key = None
    for raw in (text or "").splitlines():
        line = raw.rstrip()
        if line.startswith("==") and line.endswith("=="):
            key = line.strip("=")
            out.setdefault(key, [])
            continue
        if key and line.strip():
            out[key].append(line.strip())
    return out


def _test_result(lines: list[str]) -> dict | None:
    """A probe test section into {ok, output}. The trailing `rc=` line is the exit status of the
    pipeline; its absence means the binary was not there and the test did not run, which is NOT a
    failure and must not be reported as one."""
    if not lines:
        return None
    ok = None
    body: list[str] = []
    for line in lines:
        if line.startswith("rc="):
            ok = line[3:].strip() == "0"
        else:
            body.append(line)
    if ok is None:
        return None
    return {"ok": ok, "output": "\n".join(body).strip()}


def _names_from(lines: list[str]) -> set[str]:
    """Every hostname the existing configuration claims, from nginx `server_name`, Apache
    `ServerName`/`ServerAlias` and Caddy site addresses. Over-collecting is the right failure
    direction here: a name we wrongly believe is taken produces a refusal the operator can read and
    override by choosing another subdomain, while one we miss produces R5's silent breakage."""
    names: set[str] = set()
    for line in lines:
        cleaned = re.sub(r"^\s*(server_name|ServerName|ServerAlias)\s+", "", line, flags=re.I)
        cleaned = cleaned.replace("{", " ").strip().strip(";")
        for token in re.split(r"[\s,]+", cleaned):
            token = token.strip().strip(";").lower()
            if not token or token in ("_", "default_server", "localhost"):
                continue
            if token.startswith("http://") or token.startswith("https://"):
                token = token.split("//", 1)[1]
            token = token.split("/")[0].split(":")[0]
            if token:
                names.add(token)
    return names


def _tls_picture(lines: list[str], domain: str) -> dict:
    """Who answers HTTPS on this machine, and would our domain reach us there?

    The question this answers is the one that cost a field install: a port-80 block is perfectly
    correct and completely bypassed when the request arrives on 443. Three outcomes, and they need
    different words:

      no TLS at all            nothing serves 443, so nothing is being shadowed — stay quiet
      TLS, and ours is in it   a certificate for this name already exists; nothing to warn about
      TLS, and ours is NOT     every HTTPS request for this domain lands on somebody else's block.
                               A `server_name _` catch-all makes it certain rather than likely.
    """
    blocks: dict[str, dict] = {}
    for line in lines:
        parts = line.split(None, 1)
        if len(parts) != 2 or not parts[0].startswith("b"):
            continue
        block = blocks.setdefault(parts[0], {"listens": [], "names": set()})
        body = parts[1].strip().rstrip(";")
        if body.startswith("listen"):
            block["listens"].append(body)
        elif body.startswith("server_name"):
            block["names"] |= _names_from([body])
            if re.fullmatch(r"server_name\s+_", body):
                block["catchall"] = True

    tls_blocks = [b for b in blocks.values()
                  if any("443" in item or " ssl" in item for item in b["listens"])]
    if not tls_blocks:
        return {"has_tls": False, "serves_domain": False, "catchall": False, "names": []}

    serves = any(_name_conflict(domain, b["names"]) for b in tls_blocks)
    catchall = any(b.get("catchall") for b in tls_blocks)
    names: set[str] = set()
    for b in tls_blocks:
        names |= b["names"]
    return {"has_tls": True, "serves_domain": serves, "catchall": catchall,
            "names": sorted(names)}


def _name_conflict(domain: str, names: set[str]) -> str | None:
    """Does the existing configuration already claim this name, exactly or by wildcard?"""
    if domain in names:
        return domain
    for name in names:
        if name.startswith("*.") and domain.endswith(name[1:]):
            return name
    return None


def _containerised_proxies(client) -> list[dict]:
    """Reverse proxies running as containers, with the HOST-side path of their config mount.

    Writing to the host side rather than into the container is what makes the change survive: a
    container's own filesystem is discarded by the next `docker compose up -d` or image pull, so a
    config written there works until the most routine maintenance silently undoes it.

    Our OWN nginx is excluded by Compose project label — it is the thing being proxied TO, and
    pointing it at itself would be a loop that looks, from the dashboard, exactly like success.
    """
    out: list[dict] = []
    try:
        me_project = None
        try:
            me = client.containers.get(os.uname().nodename)
            me_project = (me.labels or {}).get("com.docker.compose.project")
        except Exception:
            me_project = None
        for c in client.containers.list():
            labels = c.labels or {}
            if me_project and labels.get("com.docker.compose.project") == me_project:
                continue
            image = ""
            try:
                image = (c.image.tags or [""])[0] or ""
            except Exception:
                pass
            blob = f"{image} {c.name}".lower()
            kind = None
            for candidate in ("traefik", "caddy", "nginx", "apache", "httpd"):
                if candidate in blob:
                    kind = "apache" if candidate == "httpd" else candidate
                    break
            if kind is None:
                continue
            ports = []
            for spec in ((c.attrs.get("NetworkSettings") or {}).get("Ports") or {}).values():
                for binding in spec or []:
                    if binding.get("HostPort") in ("80", "443"):
                        ports.append(binding["HostPort"])
            if not ports:
                continue            # a proxy that publishes neither 80 nor 443 is not the ingress
            mounts = []
            for m in c.attrs.get("Mounts") or []:
                dest = m.get("Destination") or ""
                if m.get("Type") == "bind" and (
                        "/etc/nginx" in dest or "/etc/caddy" in dest or "/etc/apache2" in dest
                        or dest.endswith("/conf.d") or "caddyfile" in dest.lower()):
                    mounts.append({"host": m.get("Source"), "container": dest,
                                   "rw": bool(m.get("RW"))})
            out.append({"kind": kind, "name": c.name, "image": image,
                        "ports": sorted(set(ports)), "mounts": mounts})
    except Exception:
        return out
    return out


# ── Adapters: where the file goes, how it is tested, how it is reloaded ──────────────────────────

def _which(binaries: list[str], *names: str) -> str:
    """The first discovered path whose basename is one of `names`.

    The probe looks in several places for each proxy — nginx lives at `/usr/sbin/nginx` on Debian and
    `/usr/bin/nginx` elsewhere, Caddy at `/usr/bin/caddy` from the package and `/usr/local/bin/caddy`
    from a manual install, which is how most people install Caddy. Hardcoding one path meant Apply
    wrote the file, could not run the test, rolled back and reported failure on a machine that was
    perfectly fine. Found by re-reading the adapters, not by a test — none of these paths has run
    against real hardware.
    """
    for path in binaries or []:
        if path.rsplit("/", 1)[-1] in names:
            return path
    return ""


def _nginx_host_adapter(dirs: list[str], includes: list[str],
                        binaries: list[str] | None = None) -> dict | None:
    """Prefer `conf.d`, fall back to `sites-available` + a symlink.

    `conf.d` is one file and no symlink, and it is the directory both Debian and RHEL layouts
    include, so it is the same operation on both. `sites-enabled` is a Debian habit and needs the
    extra link, which is one more thing to undo on removal.

    Either way the directory must be ACTUALLY INCLUDED by nginx.conf (R6). The include check accepts
    the directory name anywhere in an include line, which is deliberately loose: `include
    /etc/nginx/conf.d/*.conf;` and `include conf.d/*.conf;` are both real and both work.
    """
    joined = " ".join(includes)
    binary = _which(binaries, "nginx")
    if "/conf.d" in dirs and "conf.d" in joined:
        return {"kind": "nginx", "where": "host", "container": None,
                "target": "/etc/nginx/conf.d/geodeploy.conf", "symlink": None,
                "reload": "nginx -s reload", "tls": "certbot", "bin": binary}
    if "/etc/nginx/conf.d" in dirs and "conf.d" in joined:
        return {"kind": "nginx", "where": "host", "container": None,
                "target": "/etc/nginx/conf.d/geodeploy.conf", "symlink": None,
                "reload": "nginx -s reload", "tls": "certbot", "bin": binary}
    if "/etc/nginx/sites-available" in dirs and "/etc/nginx/sites-enabled" in dirs \
            and "sites-enabled" in joined:
        return {"kind": "nginx", "where": "host", "container": None,
                "target": "/etc/nginx/sites-available/geodeploy.conf",
                "symlink": "/etc/nginx/sites-enabled/geodeploy.conf",
                "reload": "nginx -s reload", "tls": "certbot", "bin": binary}
    return None


def _apache_host_adapter(dirs: list[str], includes: list[str],
                         binaries: list[str] | None = None) -> dict | None:
    joined = " ".join(includes)
    binary = _which(binaries, "apache2", "httpd")
    if "/etc/apache2/sites-available" in dirs and "sites-enabled" in joined:
        return {"kind": "apache", "where": "host", "container": None,
                "target": "/etc/apache2/sites-available/geodeploy.conf",
                "symlink": "/etc/apache2/sites-enabled/geodeploy.conf",
                "reload": "apache2ctl graceful", "tls": "certbot", "bin": binary}
    if "/etc/httpd/conf.d" in dirs and "conf.d" in joined:
        return {"kind": "apache", "where": "host", "container": None,
                "target": "/etc/httpd/conf.d/geodeploy.conf", "symlink": None,
                "reload": "httpd -k graceful", "tls": "certbot", "bin": binary}
    return None


def _caddy_host_adapter(dirs: list[str], includes: list[str],
                        binaries: list[str] | None = None) -> dict | None:
    """Caddy only if the Caddyfile already imports a drop-in directory.

    Caddy has no conf.d convention of its own — a site block lives in the single Caddyfile unless
    somebody added an `import`. Adding that import ourselves would mean editing their Caddyfile,
    which R1 forbids, so an un-importing Caddy is a job for the generated snippet and a human. When
    the import IS there, Caddy is the best case in this whole module: it obtains the certificate by
    itself, so one dropped file is the entire setup, HTTPS included.
    """
    if "/etc/caddy/conf.d" in dirs and re.search(r"import\s+\S*conf\.d", " ".join(includes)):
        return {"kind": "caddy", "where": "host", "container": None,
                "target": "/etc/caddy/conf.d/geodeploy.caddy", "symlink": None,
                "reload": "caddy reload --config /etc/caddy/Caddyfile", "tls": "automatic",
                "bin": _which(binaries, "caddy")}
    return None


def _container_adapter(proxy: dict) -> dict | None:
    """A proxy in a container: write to the host side of its config bind mount, test and reload with
    `docker exec`. No privileged helper is needed for the test or the reload here, which makes this
    the gentler of the two paths — the proxy validates its own config with its own binary."""
    if proxy["kind"] == "traefik":
        return None                     # routing is labels/providers, not a file we can drop
    writable = [m for m in proxy["mounts"] if m.get("rw") and m.get("host")]
    if not writable:
        return None
    mount = writable[0]
    host_dir = mount["host"].rstrip("/")
    cdir = mount["container"].rstrip("/")
    if proxy["kind"] == "nginx":
        if not cdir.endswith("conf.d"):
            return None                 # a single-file Caddyfile-style mount is not a drop-in dir
        return {"kind": "nginx", "where": "container", "container": proxy["name"],
                "target": f"{host_dir}/geodeploy.conf", "in_container": f"{cdir}/geodeploy.conf",
                "symlink": None, "reload": "nginx -s reload", "tls": "none"}
    if proxy["kind"] == "caddy":
        if not cdir.endswith("conf.d"):
            return None
        return {"kind": "caddy", "where": "container", "container": proxy["name"],
                "target": f"{host_dir}/geodeploy.caddy", "in_container": f"{cdir}/geodeploy.caddy",
                "symlink": None, "reload": "caddy reload --config /etc/caddy/Caddyfile",
                "tls": "automatic"}
    return None


# ── Detection ─────────────────────────────────────────────────────────────────────────────────────

def detect() -> dict:
    """Everything we can learn without changing anything, plus the adapter we would use.

    Returns `probe_ok: False` with a reason rather than raising, because "we could not look" and
    "we looked and cannot help" need different words in the panel and both are normal.
    """
    out: dict = {
        "probe_ok": False, "error": None, "os": None, "listeners": [], "units": [],
        "binaries": [], "dirs": [], "includes": [], "server_names": [], "our_files": {},
        "tests": {}, "containers": [], "adapter": None, "certbot": None,
    }
    try:
        client = _client()
        out["containers"] = _containerised_proxies(client)
    except ProxyError as exc:
        out["error"] = str(exc)
        return out

    try:
        _, text = _host_run(_PROBE, timeout=120)
    except ProxyError as exc:
        out["error"] = str(exc)
        return out
    if "==end==" not in text:
        out["error"] = ("The probe did not finish, so GeoDeploy cannot tell what this machine runs. "
                        "Use the configuration below and apply it yourself.")
        out["probe_output"] = text[-2000:]
        return out

    sec = _sections(text)
    out["probe_ok"] = True
    out["os"] = " ".join(sec.get("os", [])) or None
    out["units"] = sec.get("units", [])
    out["binaries"] = sec.get("binaries", [])
    out["dirs"] = sec.get("dirs", [])
    out["includes"] = sec.get("includes", [])
    out["server_names"] = sorted(_names_from(sec.get("servernames", [])))
    for line in sec.get("listeners", []):
        parts = line.split()
        out["listeners"].append({"socket": parts[0],
                                 "process": parts[-1] if len(parts) > 1 else "unknown"})
    for line in sec.get("ourfiles", []):
        parts = line.split()
        if len(parts) == 2:
            out["our_files"][parts[0]] = parts[1]
    # The merged config is a better source of claimed names than grepping files — it follows
    # includes — so union the two rather than choosing. Over-collecting is the safe direction: a
    # name we wrongly think is taken is a refusal the operator can read and work around.
    out["blocks"] = sec.get("nginxblocks", [])
    # Our OWN block is marked and excluded here, for the reason in the probe's servernames comment:
    # R5 would otherwise see the domain we configured last time as "already taken by something else"
    # and refuse every re-apply. It is NOT excluded from `_tls_picture`, which asks a different
    # question — once certbot adds a 443 block to our file, that block is exactly what proves HTTPS
    # now reaches us.
    _own = {parts[0] for parts in (ln.split(None, 1) for ln in out["blocks"])
            if len(parts) == 2 and parts[1].strip() == "GEODEPLOY_OWN"}
    _block_names = [parts[1] for parts in (ln.split(None, 1) for ln in out["blocks"])
                    if len(parts) == 2 and parts[1].startswith("server_name") and parts[0] not in _own]
    out["server_names"] = sorted(set(out["server_names"]) | _names_from(_block_names))
    for key, name in (("nginxtest", "nginx"), ("apachetest", "apache"), ("caddytest", "caddy")):
        result = _test_result(sec.get(key, []))
        if result:
            out["tests"][name] = result
    for candidate in out["binaries"]:
        if candidate.endswith("certbot"):
            out["certbot"] = candidate
            break

    # WHICH proxy, when the machine has more than one installed — and these machines do, because an
    # abandoned apache2 package is the most ordinary thing on a Debian box. The one that HOLDS 80 or
    # 443 is the ingress; an installed-but-not-listening proxy is not, and configuring it would
    # produce a file that is correct and has no effect.
    holders = " ".join(item["process"] for item in out["listeners"]).lower()
    order = [k for k in ("nginx", "caddy", "apache", "httpd") if k in holders]
    for proxy in out["containers"]:
        if proxy["kind"] not in order:
            order.append(proxy["kind"])
    if not order:
        order = ["nginx", "caddy", "apache"]

    for kind in order:
        kind = "apache" if kind == "httpd" else kind
        # A containerised proxy of this kind wins over a host one: if both exist, the container is
        # the one with 80/443 published, and we only got here because this kind holds the port.
        for proxy in out["containers"]:
            if proxy["kind"] == kind:
                adapter = _container_adapter(proxy)
                if adapter:
                    out["adapter"] = adapter
                    return out
        if kind == "nginx":
            adapter = _nginx_host_adapter(out["dirs"], out["includes"], out["binaries"])
        elif kind == "apache":
            adapter = _apache_host_adapter(out["dirs"], out["includes"], out["binaries"])
        elif kind == "caddy":
            adapter = _caddy_host_adapter(out["dirs"], out["includes"], out["binaries"])
        else:
            adapter = None
        if adapter:
            out["adapter"] = adapter
            return out
    return out


# ── The configuration we would write ─────────────────────────────────────────────────────────────

def _body(adapter: dict, domain: str, intent: dict) -> str:
    """The file content, from `deployment.py`'s generators plus our marker.

    One generator, two consumers: the text an operator pastes and the text we write are the SAME
    text, so a fix to either reaches both and the panel never shows a config that differs from what
    Apply installs. The marker is how `detect()` later recognises the file as ours (R1) — it is a
    comment in all three syntaxes.
    """
    from . import deployment

    cfg = deployment.proxy_config(domain, adapter["kind"], intent)
    body = cfg["config"]
    # Drop the generator's LEADING comment block — "put this in /etc/nginx/sites-available/… then
    # run nginx -t && systemctl reload". Right for someone copying the text by hand, nonsense in a
    # file we have already placed in conf.d, tested and reloaded; it was in the first version and
    # read as a to-do list for work that was already done. Only the preamble goes: the per-directive
    # comments inside the block are the reason anyone can review what we wrote, and they stay.
    lines = body.splitlines(keepends=True)
    start = 0
    while start < len(lines) and (lines[start].lstrip().startswith("#") or not lines[start].strip()):
        start += 1
    note = (f"# {MARKER} — do not edit by hand.\n"
            f"# Written by the Deployment panel for {domain}, then tested and reloaded.\n"
            f"# Remove it from there rather than with rm, so the proxy is tested before it is\n"
            f"# reloaded — taking this file away can expose an unrelated problem in the rest of\n"
            f"# the configuration, and a tidy-up must not be what takes the other sites down.\n")
    return note + "".join(lines[start:])


def plan(domain: str, intent: dict) -> dict:
    """What Apply would do, and every reason it would refuse. Nothing is written.

    The panel calls this before showing the button, so a machine we cannot help says so BEFORE the
    operator commits to the automatic path — rather than after, which would read as a failure of
    their setup rather than a limit of ours.
    """
    domain = _safe_domain(domain)
    found = detect()
    found["tls"] = _tls_picture(found.get("blocks") or [], domain)
    adapter = found.get("adapter")
    blockers: list[dict] = []
    warnings: list[str] = []

    if not found["probe_ok"]:
        blockers.append({
            "code": "no-probe",
            "title": "GeoDeploy cannot inspect this machine",
            "detail": found.get("error") or "The probe did not run.",
            "fix": "Apply the configuration below yourself — it is the same text GeoDeploy would write.",
        })
    elif adapter is None:
        traefik = any(p["kind"] == "traefik" for p in found["containers"])
        if traefik:
            blockers.append({
                "code": "traefik",
                "title": "This machine is fronted by Traefik",
                "detail": ("Traefik does not take its routing from a configuration file we can add — "
                           "it reads container labels, a file provider, or Kubernetes resources, and "
                           "which of those is in use changes what 'correct' means. GeoDeploy will not "
                           "guess at it."),
                "fix": "Use the Traefik instructions below: one `docker network connect`, and labels "
                       "on GeoDeploy's own nginx service.",
            })
        else:
            blockers.append({
                "code": "no-adapter",
                "title": "No reverse proxy GeoDeploy can configure automatically",
                "detail": _no_adapter_detail(found),
                "fix": "Apply the configuration below yourself, or install Caddy — it is the one that "
                       "needs no further setup and obtains the certificate itself.",
            })

    if adapter:
        kind = adapter["kind"]
        # R2 — the big one. Measured on the real machine, not assumed: a config that was already
        # failing is a config whose breakage our reload would publish.
        test = found["tests"].get(kind)
        if test and not test["ok"]:
            blockers.append({
                "code": "already-broken",
                "title": f"This machine's {kind} configuration does not currently pass its own test",
                "detail": ("GeoDeploy has written nothing. Reloading now would be what finally applies "
                           "whatever is wrong in there, and the sites on this machine would go down "
                           f"with GeoDeploy's name on it. The {kind} test says:\n\n" + test["output"]),
                "fix": f"Fix that first — run the test yourself to see it in full — then come back.",
            })
        if test is None and adapter["where"] == "host":
            warnings.append(
                f"GeoDeploy could not run {kind}'s own configuration test on this machine, so it "
                f"cannot promise the file it writes will load. It will still test before reloading, "
                f"and will remove the file rather than reload a configuration that fails."
            )
        # R5 — the name must not already be served. Belt and braces over the probe's own exclusion:
        # if the clashing name is the one WE recorded applying, and our file is still there carrying
        # our marker, then the thing serving it is us. Without this, changing the domain or simply
        # re-opening the panel after a successful apply refuses with "something already serves that
        # name" — pointing at GeoDeploy's own file.
        clash = _name_conflict(domain, set(found["server_names"]))
        if clash and clash == (read_state().get("domain") or "") \
                and found["our_files"].get(adapter["target"]) == "ours":
            clash = None
        if clash:
            blockers.append({
                "code": "name-taken",
                "title": f"{clash} is already configured on this machine",
                "detail": ("Something already serves that name here. Two blocks claiming one hostname "
                           "do not produce an error — one of them silently wins — so adding ours could "
                           "change or break a site that works today."),
                "fix": f"Choose a different subdomain for GeoDeploy, or remove the existing "
                       f"configuration for {clash} first.",
            })
        # R1 — our path, somebody else's file.
        state = found["our_files"].get(adapter["target"])
        if state == "foreign":
            blockers.append({
                "code": "foreign-file",
                "title": f"{adapter['target']} already exists and GeoDeploy did not write it",
                "detail": ("The name matches what GeoDeploy would use, but the file does not carry its "
                           "marker, so it belongs to something else. GeoDeploy will not overwrite it."),
                "fix": "Move that file aside if it is obsolete, or apply the configuration below by "
                       "hand under a different name.",
            })
        if state == "ours":
            warnings.append(f"{adapter['target']} was written by GeoDeploy earlier and will be "
                            f"replaced — this is how changing the domain works.")
        if adapter["where"] == "container":
            warnings.append(
                f"The proxy is the container '{adapter['container']}'. GeoDeploy writes to the host "
                f"side of its configuration mount, so the change survives that container being "
                f"recreated, and reloads it with `docker exec`."
            )
        if adapter["tls"] == "automatic":
            warnings.append("Caddy will obtain the HTTPS certificate by itself once the domain "
                            "resolves here — there is no separate certificate step.")

        # THE ONE THAT COST A FIELD INSTALL. Our block listens on 80. If this machine already
        # terminates HTTPS for something else and no 443 block claims our name, then every https://
        # request for this domain is answered by somebody else's site — and the operator finds out
        # from a browser, after a panel that said success, because port 80 was perfect all along.
        # Worth saying BEFORE Apply, not in a troubleshooting page.
        tls = found.get("tls") or {}
        if tls.get("has_tls") and not tls.get("serves_domain"):
            warnings.append(
                "This machine already serves HTTPS for other sites, and nothing on port 443 claims "
                f"{domain} yet — so `https://{domain}` will reach "
                + ("the catch-all block that answers for every name here"
                   if tls.get("catchall") else "whichever site answers 443 by default")
                + ", not GeoDeploy, until it has its own certificate. Plain `http://` works as soon "
                  "as you apply. Behind Cloudflare this bites immediately: in Full or Full (strict) "
                  "mode Cloudflare connects to this server on 443, so visitors would get the other "
                  "site. Use SSL/TLS mode Flexible until the certificate exists, then Full (strict)."
            )

    if not intent["bind"].startswith("127."):
        warnings.append(
            f"GeoDeploy is published on {intent['bind']}:{intent['port']}, so it stays reachable "
            f"directly as well as through the proxy. Once this works, "
            f"`sudo bash installer/set-port.sh {intent['port']}` moves it to 127.0.0.1."
        )

    # What we have ALREADY written, from the state file rather than from this session. Without this
    # the Remove button would only exist in the browser tab that pressed Apply: come back tomorrow,
    # or from another machine, and the only way to undo GeoDeploy's own file would be `rm` over SSH —
    # which skips the test-before-reload that makes removal safe.
    state = read_state()
    applied = {
        "domain": state.get("domain"),
        "target": (state.get("adapter") or {}).get("target"),
        "where": (state.get("adapter") or {}).get("where"),
        "at": state.get("at"),
    } if (state.get("adapter") or {}).get("target") else None

    out = {
        "domain": domain,
        "can_apply": bool(adapter) and not blockers,
        "adapter": adapter,
        "applied": applied,
        "blockers": blockers,
        "warnings": warnings,
        "detected": {
            "os": found.get("os"),
            "listeners": found.get("listeners"),
            "proxies": sorted({*(p["kind"] for p in found.get("containers", [])),
                               *(b.rsplit("/", 1)[-1] for b in found.get("binaries", [])
                                 if not b.endswith("certbot"))}),
            "containers": found.get("containers"),
            "tests": found.get("tests"),
            "certbot": found.get("certbot"),
            "probe_ok": found.get("probe_ok"),
            # MUST be carried through: `apply()` reads it from here to decide whether to probe 443
            # after writing. Leaving it out silently skipped the HTTPS check — reintroducing, in the
            # payload, exactly the blind spot §5o was written to close.
            "tls": found.get("tls"),
        },
    }
    if adapter:
        out["target"] = adapter["target"]
        out["config"] = _body(adapter, domain, intent)
    return out


def _no_adapter_detail(found: dict) -> str:
    """Say which of R6's two cases this is, because the fixes are opposite: install/choose a proxy,
    versus a proxy that is there but keeps its configuration somewhere we may not add to."""
    installed = [b.rsplit("/", 1)[-1] for b in found.get("binaries", []) if not b.endswith("certbot")]
    if not installed and not found.get("containers"):
        return ("No nginx, Caddy or Apache was found on this machine, and no proxy container "
                "publishes port 80 or 443. There is nothing in front of GeoDeploy to configure.")
    names = ", ".join(sorted(set(installed))) or "a proxy"
    detail = (f"{names} is installed here, but its configuration is not laid out in a way GeoDeploy "
              f"may add one file to: either the drop-in directory does not exist, or the main "
              f"configuration does not include it. GeoDeploy will not edit the main configuration "
              f"file — a file written where nothing reads it would look like success and do nothing.")
    # Caddy deserves the extra sentence: a stock Caddyfile has no drop-in import at all, so this is
    # the DEFAULT state for a Caddy machine rather than an unusual one — and Caddy is the best case
    # for everything downstream, since it gets the certificate itself. Telling them the exact line to
    # add turns a dead end into one edit they make themselves, which keeps R1 intact.
    if any("caddy" in name for name in installed):
        detail += ("\n\nFor Caddy this is normal: a stock Caddyfile has no drop-in directory. Add one "
                   "line at the end of /etc/caddy/Caddyfile and GeoDeploy can take it from there — "
                   "including the HTTPS certificate, which Caddy obtains by itself:\n\n"
                   "    import /etc/caddy/conf.d/*.caddy\n\n"
                   "Then `sudo mkdir -p /etc/caddy/conf.d && sudo systemctl reload caddy`, and "
                   "re-check here.")
    return detail


# ── Applying ──────────────────────────────────────────────────────────────────────────────────────

# Interpolated ONLY from this module's adapter tables and a domain that has been through
# `_safe_domain`. The config body never appears here — it arrives as /tmp/config via put_archive.
_APPLY_HOST = r"""
set -e
H=/host
TARGET="$H{target}"
LINK="{link}"
say() {{ echo "==$1=="; }}

say write
cp /tmp/config "$TARGET"
chmod 644 "$TARGET"
if [ -n "$LINK" ]; then ln -sfn "{target}" "$H$LINK"; fi
echo ok

say test
set +e
OUT=$({test_cmd} 2>&1)
RC=$?
set -e
echo "$OUT" | tail -10
echo "rc=$RC"

if [ "$RC" != "0" ]; then
  say rollback
  rm -f "$TARGET"
  if [ -n "$LINK" ]; then rm -f "$H$LINK"; fi
  set +e
  {test_cmd} >/dev/null 2>&1
  echo "rc=$?"
  set -e
  echo "==end=="
  exit 3
fi

say reload
set +e
OUT=$({reload_cmd} 2>&1)
RC=$?
set -e
echo "$OUT" | tail -5
echo "rc=$RC"

say route
sleep 1
set +e
if [ "{probe_port}" = "" ] || ! wget --help 2>&1 | grep -q -- '--header' ; then
  echo "skipped"
else
  OUT=$(wget -q -O - --header "Host: {domain}" --timeout 8 \
        "http://127.0.0.1:{probe_port}/api/public/whoami" 2>&1)
  RC2=$?
  echo "$OUT" | head -c 400
  echo ""
  echo "rc=$RC2"
fi
set -e

# And 443, separately, because a correct port-80 block is INVISIBLE to a visitor arriving over
# HTTPS when another block holds 443 — the failure that shipped on 2026-10-09. Asking only the port
# we configured is how a panel reports success while every real request gets somebody else's site.
say route443
set +e
if [ "{probe_tls}" = "" ] || ! wget --help 2>&1 | grep -q -- '--header' ; then
  echo "skipped"
else
  OUT=$(wget -q -O - --no-check-certificate --header "Host: {domain}" --timeout 8 \
        "https://127.0.0.1/api/public/whoami" 2>&1)
  RC3=$?
  echo "$OUT" | head -c 400
  echo ""
  echo "rc=$RC3"
fi
set -e
echo "==end=="
"""


def _host_test_command(adapter: dict) -> str:
    """The bare command, with NO redirection and NO pipe.

    Both are the caller's job, and deliberately so: every caller needs the command's own exit status,
    and `cmd | tail` hands back `tail`'s instead — which would turn a failing configuration test into
    a passing one and silently disable R2 and R4. Keeping the pipe out of this table makes that
    mistake impossible to make in only one of the four call sites.
    """
    binary = adapter.get("bin") or ""
    if adapter["kind"] == "nginx":
        return f"chroot $H {binary or '/usr/sbin/nginx'} -t"
    if adapter["kind"] == "apache":
        if binary.endswith("httpd") or "httpd" in adapter["target"]:
            return f"chroot $H {binary or '/usr/sbin/httpd'} -t"
        return "chroot $H /usr/sbin/apache2ctl configtest"
    return f"chroot $H {binary or '/usr/bin/caddy'} validate --config /etc/caddy/Caddyfile"


def _host_reload_command(adapter: dict) -> str:
    """Reload (R3), and never through systemd.

    `systemctl` from a container is not simply unavailable — it talks to the host's private D-Bus
    socket, and the ways it fails look like a broken proxy rather than a missing tool. Signalling the
    master process is what every init script does underneath anyway, works identically on systemd,
    sysvinit and a hand-started binary, and is the same operation the operator would run.
    """
    binary = adapter.get("bin") or ""
    if adapter["kind"] == "nginx":
        return f"chroot $H {binary or '/usr/sbin/nginx'} -s reload"
    if adapter["kind"] == "apache":
        if binary.endswith("httpd") or "httpd" in adapter["target"]:
            return f"chroot $H {binary or '/usr/sbin/httpd'} -k graceful"
        return "chroot $H /usr/sbin/apache2ctl graceful"
    return f"chroot $H {binary or '/usr/bin/caddy'} reload --config /etc/caddy/Caddyfile"


def _exec(client, name: str, argv: list[str], timeout: int = 60) -> tuple[int, str]:
    container = client.containers.get(name)
    result = container.exec_run(argv, demux=False)
    out = (result.output or b"").decode("utf-8", "replace")
    return int(result.exit_code or 0), out


def apply(domain: str, intent: dict) -> dict:
    """Write, test, reload — or put everything back and reload nothing.

    Every stage is reported as its own step with the proxy's real output, because the useful answer
    to "it did not work" is which stage stopped and what the proxy said, not a boolean.
    """
    domain = _safe_domain(domain)
    checked = plan(domain, intent)
    steps: list[dict] = []

    def step(name, ok, detail, fix=None):
        steps.append({"name": name, "ok": ok, "detail": detail, "fix": fix})

    if not checked["can_apply"]:
        first = (checked["blockers"] or [{}])[0]
        step("Checked this machine", False,
             first.get("detail") or "GeoDeploy cannot configure this machine's web server.",
             first.get("fix"))
        return {"ok": False, "steps": steps, "plan": checked}

    adapter = checked["adapter"]
    body = checked["config"].encode("utf-8")
    kind = adapter["kind"]
    test = checked["detected"]["tests"].get(kind)
    step("Checked this machine", True,
         f"{kind} is the reverse proxy here"
         + (f" (container '{adapter['container']}')" if adapter["where"] == "container" else "")
         + (", and its current configuration passes its own test." if test and test["ok"]
            else ". Its configuration test could not be run — the new file will still be tested "
                 "before anything is reloaded."))

    if adapter["where"] == "container":
        return _apply_in_container(adapter, domain, body, steps, checked)

    # The post-apply routing check only means anything if the proxy actually listens on 80 — on a
    # machine where it serves 443 only, asking the loopback for port 80 proves nothing and reads as
    # a failure of the thing we just did correctly. An empty port skips the check and says so.
    on_80 = any(item["socket"].endswith(":80") or item["socket"].endswith(".80")
                for item in (checked["detected"].get("listeners") or []))
    tls = checked["detected"].get("tls") or {}
    script = _APPLY_HOST.format(
        target=adapter["target"],
        link=adapter["symlink"] or "",
        test_cmd=_host_test_command(adapter),
        reload_cmd=_host_reload_command(adapter),
        domain=domain,
        probe_port="80" if on_80 else "",
        probe_tls="443" if tls.get("has_tls") else "",
    )
    code, text = _host_run(script, files={"config": body}, timeout=240)
    sec = _sections(text)

    if "write" not in sec:
        step("Wrote the configuration", False,
             "The helper did not get as far as writing the file.\n\n" + text[-800:].strip())
        return {"ok": False, "steps": steps, "plan": checked}
    step("Wrote the configuration", True,
         f"{adapter['target']}"
         + (f", linked from {adapter['symlink']}" if adapter["symlink"] else ""))

    nginx_test = _test_result(sec.get("test", []))
    if nginx_test and not nginx_test["ok"]:
        back = _test_result(sec.get("rollback", []))
        step(f"{kind} accepted it", False,
             f"It did not, so nothing was reloaded and the file has been removed. {kind} said:\n\n"
             + (nginx_test["output"] or "(no output)"),
             "This machine is exactly as it was before Apply ran"
             + ("" if not back else (" — its configuration test passes again." if back["ok"] else
                " — but its test is still failing, which means the problem was not ours. Run the "
                "test yourself to see it.")))
        return {"ok": False, "steps": steps, "plan": checked}
    step(f"{kind} accepted it", True, (nginx_test or {}).get("output") or "Configuration test passed.")

    reload_result = _test_result(sec.get("reload", []))
    if reload_result and not reload_result["ok"]:
        step("Reloaded", False,
             (reload_result["output"] or "The reload command failed."),
             "The file is in place and valid, but the proxy did not pick it up. Reload it yourself "
             f"({adapter['reload']}), or remove the file from this panel.")
        _write_state({"domain": domain, "adapter": adapter, "applied": False})
        return {"ok": False, "steps": steps, "plan": checked}
    step("Reloaded", True, "The proxy reloaded without dropping connections.")

    _write_state({"domain": domain, "adapter": adapter, "applied": True, "at": int(time.time())})

    route = sec.get("route", [])
    reached = any("instance" in line for line in route)
    if any(line == "skipped" for line in route):
        step("The proxy reaches GeoDeploy", True,
             "Not checked from here — this machine's proxy does not listen on port 80, or the "
             "helper image's wget cannot set a Host header. The configuration is written, valid "
             "and loaded; Verify below checks it the way a visitor would.")
    elif reached:
        step("The proxy reaches GeoDeploy", True,
             f"Asked the proxy for {domain} over this machine's own loopback and GeoDeploy answered. "
             f"This is independent of DNS: the proxy is correct whether or not the record has "
             f"propagated yet.")
    else:
        step("The proxy reaches GeoDeploy", False,
             "The configuration loaded, but asking the proxy for this domain on the loopback did not "
             "reach GeoDeploy.\n\n" + "\n".join(route[:6]),
             "Most often another block on this machine matches the request first. The file is in "
             "place and valid; Verify below will say what a visitor gets.")

    # HTTPS, as its own step. Reported even though the file is correct, because "correct" and
    # "what a visitor gets" came apart here once already: port 80 answered perfectly while every
    # https:// request landed on another site's catch-all.
    route443 = sec.get("route443", [])
    if route443 and not any(line == "skipped" for line in route443):
        if any("instance" in line for line in route443):
            step("HTTPS reaches GeoDeploy too", True,
                 f"https://{domain} is answered by GeoDeploy on this machine as well.")
        else:
            step("HTTPS does NOT reach GeoDeploy yet", False,
                 "This machine serves HTTPS for something else, and a request for this domain on "
                 "port 443 is answered by that, not by GeoDeploy. The block GeoDeploy just wrote "
                 "listens on port 80 — which is correct, and invisible to anyone arriving over "
                 "HTTPS.",
                 f"Get a certificate for {domain} (the button below, or "
                 f"`sudo certbot --nginx -d {domain}`): it adds a 443 block for this exact name, "
                 f"and an exact match beats a catch-all. Behind Cloudflare, set SSL/TLS to Flexible "
                 f"until then — in Full mode Cloudflare connects to this server on 443 and your "
                 f"visitors get the other site.")
    return {"ok": all(s["ok"] for s in steps), "steps": steps, "plan": checked,
            "domain": domain, "target": adapter["target"]}


def _apply_in_container(adapter: dict, domain: str, body: bytes, steps: list[dict],
                        checked: dict) -> dict:
    """The containerised path: the file goes to the host side of the bind mount, and the proxy tests
    and reloads itself with its own binary through `docker exec` — no privileged helper involved in
    either. Writing the file still needs the host filesystem, so that one step uses the helper."""
    def step(name, ok, detail, fix=None):
        steps.append({"name": name, "ok": ok, "detail": detail, "fix": fix})

    target = adapter["target"]
    code, text = _host_run(
        f'set -e\ncp /tmp/config "/host{target}"\nchmod 644 "/host{target}"\n'
        f'echo "==write=="\necho ok\necho "==end=="\n',
        files={"config": body}, timeout=120)
    if "==write==" not in text:
        step("Wrote the configuration", False, text[-800:].strip() or "The file could not be written.")
        return {"ok": False, "steps": steps, "plan": checked}
    step("Wrote the configuration", True, f"{target} (the host side of the proxy's config mount)")

    client = _client()
    name = adapter["container"]
    kind = adapter["kind"]
    try:
        if kind == "nginx":
            rc, out = _exec(client, name, ["nginx", "-t"])
        else:
            rc, out = _exec(client, name, ["caddy", "validate", "--config", "/etc/caddy/Caddyfile"])
    except Exception as exc:
        step(f"{kind} accepted it", False, f"Could not run the test in '{name}': {exc}",
             "The file is written but has not been tested or loaded. Test and reload the proxy "
             "yourself, or remove the file from this panel.")
        return {"ok": False, "steps": steps, "plan": checked}

    if rc != 0:
        try:
            _host_run(f'rm -f "/host{target}"\necho "==end=="\n', timeout=60)
        except ProxyError:
            pass
        step(f"{kind} accepted it", False,
             f"It did not, so nothing was reloaded and the file has been removed. {kind} said:\n\n{out.strip()}",
             f"The container '{name}' is exactly as it was before Apply ran.")
        return {"ok": False, "steps": steps, "plan": checked}
    step(f"{kind} accepted it", True, out.strip() or "Configuration test passed.")

    try:
        if kind == "nginx":
            rc, out = _exec(client, name, ["nginx", "-s", "reload"])
        else:
            rc, out = _exec(client, name, ["caddy", "reload", "--config", "/etc/caddy/Caddyfile"])
    except Exception as exc:
        rc, out = 1, str(exc)
    if rc != 0:
        step("Reloaded", False, out.strip() or "The reload failed.",
             f"The file is valid and in place; reload '{name}' yourself.")
        _write_state({"domain": domain, "adapter": adapter, "applied": False})
        return {"ok": False, "steps": steps, "plan": checked}
    step("Reloaded", True, f"'{name}' reloaded without dropping connections.")
    _write_state({"domain": domain, "adapter": adapter, "applied": True, "at": int(time.time())})
    return {"ok": True, "steps": steps, "plan": checked, "domain": domain, "target": target}


# ── Undoing it ────────────────────────────────────────────────────────────────────────────────────

def remove() -> dict:
    """Take away exactly what we added, then test and reload the same way Apply did.

    `rm` plus a reload is not a safe sequence on its own: removing OUR file can expose a DIFFERENT
    pre-existing problem in their configuration, and reloading then breaks their sites during what
    the operator thinks is a tidy-up. So the test comes first and a failure leaves the proxy running
    the configuration it already has.
    """
    state = read_state()
    adapter = state.get("adapter") or {}
    steps: list[dict] = []

    def step(name, ok, detail, fix=None):
        steps.append({"name": name, "ok": ok, "detail": detail, "fix": fix})

    if not adapter.get("target"):
        step("Nothing to remove", True,
             "GeoDeploy has no record of writing a configuration on this machine.")
        return {"ok": True, "steps": steps}

    target = adapter["target"]
    link = adapter.get("symlink") or ""
    if adapter.get("where") == "container":
        code, text = _host_run(f'rm -f "/host{target}"\necho "==end=="\n', timeout=60)
        step("Removed the file", True, target)
        try:
            client = _client()
            name = adapter["container"]
            if adapter["kind"] == "nginx":
                rc, out = _exec(client, name, ["nginx", "-t"])
                if rc == 0:
                    rc, out = _exec(client, name, ["nginx", "-s", "reload"])
            else:
                rc, out = _exec(client, name, ["caddy", "reload", "--config", "/etc/caddy/Caddyfile"])
            step("Reloaded", rc == 0, out.strip() or "Done.")
        except Exception as exc:
            step("Reloaded", False, str(exc), f"Reload '{adapter.get('container')}' yourself.")
        _write_state({})
        return {"ok": all(s["ok"] for s in steps), "steps": steps}

    # Capture-then-trim, never `cmd | tail` — see `_host_test_command`. Here the consequence of
    # getting it wrong is reloading a configuration that no longer parses, during a tidy-up.
    script = (
        'set -e\nH=/host\n'
        'say() { echo "==$1=="; }\n'
        f'rm -f "$H{target}"\n'
        + (f'rm -f "$H{link}"\n' if link else "")
        + 'say removed\necho ok\n'
          'say test\nset +e\n'
          'OUT=$(' + _host_test_command(adapter) + ' 2>&1)\nRC=$?\nset -e\n'
          'echo "$OUT" | tail -10\necho "rc=$RC"\n'
          'if [ "$RC" != "0" ]; then echo "==end=="; exit 4; fi\n'
          'say reload\nset +e\n'
          'OUT=$(' + _host_reload_command(adapter) + ' 2>&1)\nRC=$?\nset -e\n'
          'echo "$OUT" | tail -5\necho "rc=$RC"\necho "==end=="\n'
    )
    code, text = _host_run(script, timeout=180)
    sec = _sections(text)
    step("Removed the file", "removed" in sec, target + (f" and {link}" if link else ""))
    test = _test_result(sec.get("test", []))
    if test and not test["ok"]:
        step(f"{adapter['kind']} configuration still valid", False,
             "With GeoDeploy's file gone, the configuration does not pass its own test — so this "
             "machine had another problem independent of GeoDeploy. Nothing was reloaded.\n\n"
             + test["output"],
             "The proxy is still running its previous configuration and is unaffected.")
        _write_state({})
        return {"ok": False, "steps": steps}
    step(f"{adapter['kind']} configuration still valid", True, (test or {}).get("output") or "Yes.")
    reload_result = _test_result(sec.get("reload", []))
    step("Reloaded", bool(reload_result and reload_result["ok"]),
         (reload_result or {}).get("output") or "The proxy reloaded.")
    _write_state({})
    return {"ok": all(s["ok"] for s in steps), "steps": steps}


# ── HTTPS, obtained without installing anything ──────────────────────────────────────────────────
#
# certbot runs in a CONTAINER, not on the host. That is what lets GeoDeploy do this at all: the rule
# against installing packages on somebody else's server stands, and a throwaway container satisfies
# both halves — the operator gets automatic HTTPS, and `apt list --installed` is unchanged tomorrow.
#
# HTTP-01 over the webroot, not the nginx plugin. The plugin would EDIT the host's nginx config, and
# R1 says we never do that; the webroot plugin only writes a challenge file under /var/www/html,
# which the block we already wrote already serves. We then write the HTTPS server block into OUR OWN
# file — the same file, the same marker, the same test-then-reload.
#
# What this does add to the machine, and it is worth being honest that it is more than one config
# file: /etc/letsencrypt (certificates and account key) and /var/lib/letsencrypt (certbot's working
# state), both owned by root, both outliving GeoDeploy unless they are removed by hand.

CERTBOT_IMAGE = "certbot/certbot:latest"
WEBROOT = "/var/www/html"
LETSENCRYPT = "/etc/letsencrypt"


def _certbot(args: list[str], timeout: int = 300) -> tuple[int, str]:
    """Run certbot in a throwaway container against the host's /etc/letsencrypt.

    Not through `_host_run`: this one wants certbot's own image rather than the host filesystem, and
    it needs outbound network to reach Let's Encrypt. Default bridge networking is deliberate — host
    networking would be a larger claim than this needs, and the ACME servers are reached outbound
    either way.
    """
    client = _client()
    try:
        client.images.get(CERTBOT_IMAGE)
    except Exception:
        try:
            client.images.pull(CERTBOT_IMAGE)
        except Exception as exc:
            raise ProxyError(
                "Could not fetch the certbot image, so GeoDeploy cannot request a certificate: "
                f"{exc}. This machine may have no outbound internet access — which would also stop "
                "Let's Encrypt from working."
            ) from exc
    container = None
    try:
        container = client.containers.create(
            image=CERTBOT_IMAGE,
            command=args,
            volumes={
                LETSENCRYPT: {"bind": "/etc/letsencrypt", "mode": "rw"},
                "/var/lib/letsencrypt": {"bind": "/var/lib/letsencrypt", "mode": "rw"},
                WEBROOT: {"bind": WEBROOT, "mode": "rw"},
            },
            name=f"geodeploy-certbot-{int(time.time() * 1000) % 100000000}",
        )
        container.start()
        result = container.wait(timeout=timeout)
        out = container.logs(stdout=True, stderr=True).decode("utf-8", "replace")
        return int(result.get("StatusCode", 1)), out
    except ProxyError:
        raise
    except Exception as exc:
        raise ProxyError(f"certbot could not run: {exc}") from exc
    finally:
        if container is not None:
            try:
                container.remove(force=True)
            except Exception:
                pass


def _cert_mtime(domain: str) -> str:
    """When the live certificate was last written, as a bare epoch string ('' if there is none).

    Comparing this across a renewal run is how we know whether anything actually changed, and it is
    deliberately not a parse of certbot's output: that text is prose, it is localised, and it has
    changed between versions. A file either has a new mtime or it does not.
    """
    _, out = _host_run(
        f'stat -c %Y "/host{LETSENCRYPT}/live/{domain}/fullchain.pem" 2>/dev/null || echo ""\n'
        f'echo "==end=="\n', timeout=60)
    for line in out.splitlines():
        line = line.strip()
        if line.isdigit():
            return line
    return ""


def _is_cloudflare(domain: str) -> bool:
    """Does this name resolve to Cloudflare's proxy? Decides whether the HTTPS block may redirect
    port 80 — see `deployment._nginx_conf_tls`. A lookup failure answers False, which is the
    conservative direction here: a redirect that should not be there is visible immediately, while a
    missing one is a minor imperfection."""
    from . import deployment
    try:
        return deployment.looks_like_cloudflare(deployment.resolve_domain(domain))
    except Exception:
        return False


def issue_certificate(domain: str, email: str) -> dict:
    """Get a Let's Encrypt certificate for this domain and switch our own block to HTTPS.

    Separate from Apply, and never implied by it: it reaches an external CA under terms the operator
    accepts, publishes the hostname to the public Certificate Transparency log, and spends a rate
    limit that is unpleasant to exhaust. Those are decisions, not side effects.
    """
    domain = _safe_domain(domain)
    email = (email or "").strip()
    if not _SAFE_EMAIL.fullmatch(email):
        raise ProxyError("Let's Encrypt needs a contact email address for expiry notices.")

    state = read_state()
    adapter = state.get("adapter") or {}
    steps: list[dict] = []

    def step(name, ok, detail, fix=None):
        steps.append({"name": name, "ok": ok, "detail": detail, "fix": fix})

    if adapter.get("tls") == "automatic":
        step("Nothing to do", True,
             "Caddy obtains and renews the certificate itself as soon as the domain resolves to this "
             "machine. There is no certbot step.")
        return {"ok": True, "steps": steps}
    if adapter.get("where") == "container":
        step("Not available here", False,
             "The proxy is a container, and a certificate obtained out here could not be loaded by it.",
             "Turn on that image's own ACME support — most proxy images have it, and it is the right "
             "thing to use when the proxy is containerised.")
        return {"ok": False, "steps": steps}
    if adapter.get("kind") != "nginx":
        step("Not available yet", False,
             f"Automatic certificates are implemented for nginx; this machine runs "
             f"{adapter.get('kind') or 'something else'}.",
             f"sudo certbot --apache -d {domain}")
        return {"ok": False, "steps": steps}
    if not adapter.get("target"):
        step("No configuration to secure", False,
             "GeoDeploy has not written a proxy configuration on this machine yet.",
             "Apply the domain first — the certificate is added to that block.")
        return {"ok": False, "steps": steps}

    # The challenge is a FILE FETCHED OVER PORT 80 from the public internet. Saying that before
    # spending a rate limit is worth four lines: the two ways it fails are both visible from here.
    behind_cloudflare = _is_cloudflare(domain)
    _host_run(f'mkdir -p "/host{WEBROOT}/.well-known/acme-challenge"\necho "==end=="\n', timeout=60)

    code, out = _certbot([
        "certonly", "--webroot", "-w", WEBROOT, "-d", domain,
        "--non-interactive", "--agree-tos", "-m", email,
        "--keep-until-expiring", "--no-eff-email",
    ])
    tail = "\n".join(out.strip().splitlines()[-18:])
    if code != 0:
        step("Let's Encrypt issued a certificate", False, tail,
             ("Cloudflare's proxy is on for this domain. That usually works — Let's Encrypt reaches "
              "Cloudflare on port 80 and Cloudflare forwards the challenge here — so read certbot's "
              "output above for what actually happened. The three things that do break it: Bot Fight "
              "Mode, a WAF rule or 'Under Attack' mode answering the challenge with a JavaScript "
              "page; a cache or page rule on /.well-known/; and SSL/TLS already set to Full, where "
              "Cloudflare tries HTTPS against an origin that has no certificate yet. Turning the "
              "orange cloud off (DNS only) for two minutes rules out all three."
              if behind_cloudflare else
              "The challenge is a file fetched over port 80 from the public internet. Check that "
              f"http://{domain}/.well-known/acme-challenge/ reaches this server and that no "
              "firewall blocks port 80."))
        return {"ok": False, "steps": steps}
    step("Let's Encrypt issued a certificate", True,
         f"For {domain}, valid for 90 days and renewed automatically from here.")

    # Now OUR file becomes the HTTPS one. Same path, same marker, same test-then-reload — this is an
    # ordinary apply of a different body, not a special case with its own rules.
    from . import deployment
    intent = deployment.read_intent()
    cfg = deployment.proxy_config(domain, "nginx", intent, tls=True, redirect=not behind_cloudflare)
    body = (f"# {MARKER} — do not edit by hand.\n"
            f"# Written by the Deployment panel for {domain}, with HTTPS.\n"
            f"# Remove it from there rather than with rm, so the proxy is tested before it is\n"
            f"# reloaded.\n" + cfg["config"]).encode("utf-8")

    script = _APPLY_HOST.format(
        target=adapter["target"], link=adapter.get("symlink") or "",
        test_cmd=_host_test_command(adapter), reload_cmd=_host_reload_command(adapter),
        domain=domain, probe_port="80", probe_tls="443")
    _, text = _host_run(script, files={"config": body}, timeout=240)
    sec = _sections(text)

    test = _test_result(sec.get("test", []))
    if test and not test["ok"]:
        step("nginx accepted the HTTPS configuration", False,
             "It did not, so nothing was reloaded and the HTTPS block has been removed. nginx said:\n\n"
             + (test["output"] or "(no output)"),
             "The certificate was still issued and is on disk — this is only about the configuration. "
             "The site is back exactly as it was before this ran.")
        return {"ok": False, "steps": steps}
    step("nginx accepted the HTTPS configuration", True, (test or {}).get("output") or "Test passed.")

    reload_result = _test_result(sec.get("reload", []))
    if reload_result and not reload_result["ok"]:
        step("Reloaded", False, reload_result["output"] or "The reload failed.",
             f"The configuration is valid and in place; reload it yourself: {adapter['reload']}")
        return {"ok": False, "steps": steps}
    step("Reloaded", True, "The proxy reloaded without dropping connections.")

    state["certificate"] = {"domain": domain, "email": email, "at": int(time.time()),
                            "mtime": _cert_mtime(domain), "redirect": not behind_cloudflare}
    state["tls"] = True
    _write_state(state)

    route443 = sec.get("route443", [])
    if any("instance" in line for line in route443):
        step("HTTPS reaches GeoDeploy", True,
             f"https://{domain} is answered by GeoDeploy, with its own certificate.")
    elif route443 and not any(line == "skipped" for line in route443):
        step("HTTPS reaches GeoDeploy", False,
             "The certificate is installed and the configuration loaded, but a request for this "
             "domain on port 443 still does not reach GeoDeploy.\n\n" + "\n".join(route443[:6]),
             "Another block on this machine may still be matching first. Press Verify to see what a "
             "visitor gets.")

    if behind_cloudflare:
        step("One thing left, at Cloudflare", True,
             "This server now has its own certificate, so set SSL/TLS to Full (strict) — Cloudflare "
             "will then connect to it over HTTPS and verify it. Turn on 'Always Use HTTPS' there too: "
             "port 80 here deliberately does NOT redirect, because with Cloudflare on Flexible a "
             "redirect loops between Cloudflare and this server forever.")
    return {"ok": all(s["ok"] for s in steps), "steps": steps, "domain": domain}


def renew_certificates() -> dict:
    """Renew anything due, and reload the proxy only if something actually changed.

    Run from a scheduled task, daily. certbot itself decides whether renewal is due — calling this
    every day is correct and cheap, and is what makes a 90-day certificate a non-event.

    The reload is gated on the certificate FILE changing, not on certbot's output: that text is
    prose, localised, and has changed between versions, while an mtime is an mtime. Reloading on
    every tick would be harmless but is still a daily perturbation of somebody else's web server for
    no reason, which is exactly the habit this module exists to avoid.
    """
    state = read_state()
    cert = state.get("certificate") or {}
    adapter = state.get("adapter") or {}
    domain = cert.get("domain")
    if not domain or not adapter.get("target"):
        return {"ok": True, "renewed": False, "detail": "No GeoDeploy-managed certificate."}

    before = _cert_mtime(domain)
    code, out = _certbot(["renew", "--webroot", "-w", WEBROOT, "--no-random-sleep-on-renew"],
                         timeout=600)
    tail = "\n".join(out.strip().splitlines()[-12:])
    if code != 0:
        return {"ok": False, "renewed": False, "detail": tail}

    after = _cert_mtime(domain)
    if before and after and before == after:
        return {"ok": True, "renewed": False, "detail": "Not due for renewal."}

    script = (
        'set -e\nH=/host\nsay() { echo "==$1=="; }\nsay test\nset +e\n'
        'OUT=$(' + _host_test_command(adapter) + ' 2>&1)\nRC=$?\nset -e\n'
        'echo "$OUT" | tail -10\necho "rc=$RC"\n'
        'if [ "$RC" != "0" ]; then echo "==end=="; exit 4; fi\n'
        'say reload\nset +e\n'
        'OUT=$(' + _host_reload_command(adapter) + ' 2>&1)\nRC=$?\nset -e\n'
        'echo "$OUT" | tail -5\necho "rc=$RC"\necho "==end=="\n'
    )
    _, text = _host_run(script, timeout=180)
    sec = _sections(text)
    test = _test_result(sec.get("test", []))
    if test and not test["ok"]:
        return {"ok": False, "renewed": True, "reloaded": False,
                "detail": "The certificate renewed, but the proxy configuration does not pass its "
                          "own test, so it was NOT reloaded. The old certificate stays live until "
                          "that is fixed.\n\n" + test["output"]}
    reload_result = _test_result(sec.get("reload", []))
    state.setdefault("certificate", {})["mtime"] = after
    _write_state(state)
    return {"ok": bool(reload_result and reload_result["ok"]), "renewed": True,
            "reloaded": bool(reload_result and reload_result["ok"]),
            "detail": f"Renewed the certificate for {domain} and reloaded the proxy."}
