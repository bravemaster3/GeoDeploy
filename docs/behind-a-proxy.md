---
description: >-
  Install GeoDeploy on a machine that already runs other software: a local port instead of ports 80
  and 443, an existing nginx, Caddy or Apache in front, and a domain configured from the dashboard.
---

# Installing alongside other software

GeoDeploy does not have to own the server. It can publish itself on a local port and sit behind an
nginx, Caddy, Apache or Traefik you already run — a lab server, a shared research machine, a VM with
someone else's website on it.

The installer **asks** which of these you want, every time. It never takes ports 80 and 443 because
they happen to be free, and it never stops, edits or reconfigures anything already running.

## The two shapes

| | **Dedicated** | **Behind a proxy** |
| --- | --- | --- |
| Publishes on | `0.0.0.0:80` | `127.0.0.1:<port>` |
| Reachable from | anywhere | this machine only |
| HTTPS and the domain | not yet configured | your existing reverse proxy |
| Right when | the server was bought for GeoDeploy | anything else on the box serves the web |

```
DEDICATED                          BEHIND A PROXY

internet                           internet
   │                                  │
   ▼                                  ▼
0.0.0.0:80                       your nginx :443  ──► other sites
   │                                  │
   ▼                                  ▼
GeoDeploy nginx                  127.0.0.1:8080
                                      │
                                      ▼
                                 GeoDeploy nginx
```

## Installing

```bash
curl -fsSL https://raw.githubusercontent.com/bravemaster3/geodeploy/main/installer/install.sh | bash
```

The installer checks the machine first and shows you what it found, then asks:

```
  Docker                    ✓  usable with sudo
  Port 80                   ✗  in use — nginx (systemd: nginx.service)
  A free port for GeoDeploy    8080

  This machine is already serving something on port 80 or 443.
  GeoDeploy will not touch it, whichever option you choose.

How should GeoDeploy be published?

  1) Make this machine a dedicated GeoDeploy server.
     GeoDeploy takes port 80 and answers at http://203.0.113.10 — no port in
     the address. It becomes the machine's web server: anything else that wants
     port 80 afterwards will fail to start. Nothing running now is stopped by
     this installer. Choose this on a server you bought for GeoDeploy.
     Not available right now — port 80 is in use by nginx (pid 812).

  2) Use the default port — 127.0.0.1:8080
     GeoDeploy shares the machine. It listens on port 8080, reachable only from
     this server, and a reverse proxy you already run publishes it on a domain.

  3) Choose a different port.
     Same as 2, on a port you pick.
     Free right now: 8080 8082 8090 8880 9080 9090

Choose [2]:
```

Option 1 is the recommendation on an empty machine, option 2 when something else is already serving.
Either way the recommendation is only pre-selected — pressing Enter is you choosing it. Picking 1
while something holds port 80 is refused and re-asked; the installer will not stop the other service
for you.

**Option 3** asks which port, offering the ones that are free *at that moment* and accepting any
other you type. A port that is taken is refused, with the process that holds it named, and you are
asked again — GeoDeploy never quietly substitutes a working port for the one you asked for.

### Choosing the port up front

If you already know, skip the question:

```bash
curl -fsSL …/install.sh | bash -s -- --port 8081
curl -fsSL …/install.sh | bash -s -- --dedicated
```

`bash -s --` is how arguments reach a piped script. The port is still checked: if 8081 is taken you
are told what holds it and offered what is free, rather than getting an install that completes and
leaves nginx dead.

### Which ports get offered

`GEODEPLOY_PORT_CANDIDATES` in `.env` (or in the environment, which wins) — ten by default:

```
GEODEPLOY_PORT_CANDIDATES=8080,8081,8082,8090,8880,9080,9090,8008,7080,8888
```

It is a **suggestion list**, read only while a port is being chosen, and always filtered to the ones
actually free at that moment. It is not the same as `GEODEPLOY_HTTP_PORT`, which records the port
you *chose* and is authoritative from then on. Keeping them apart is what stops the port drifting:
if the installer re-scanned the list on every run, an install that landed on 8081 because 8080 was
busy would move back to 8080 the day that service was retired — while your `proxy_pass`, your DNS
record and everyone's bookmarks still pointed at 8081.

### Checking before you install

`preflight.sh` runs every check and changes nothing. Safe on a production box at any time:

```bash
bash installer/preflight.sh
```

### Installing without a terminal

Cloud-init, Ansible, CI — anywhere the installer cannot ask a question. Set the answer in the
environment and it will not prompt:

```bash
GEODEPLOY_DEPLOY_MODE=behind-proxy GEODEPLOY_HTTP_PORT=8080 bash install.sh
GEODEPLOY_DEPLOY_MODE=dedicated bash install.sh
```

With no terminal **and** no setting, GeoDeploy installs behind-proxy on the first free port. That is
the only choice that can neither break anything nor claim anything, and taking port 80 is never
inferred from silence.

## Reaching the dashboard before you have a domain

In behind-proxy mode nothing outside the server can reach GeoDeploy — that is the point. Open an SSH
tunnel from your own computer:

```bash
ssh -L 8080:127.0.0.1:8080 you@your-server
```

then open `http://localhost:8080`. The installer prints this line with your port already in it.

## Giving it a domain

**Settings → Deployment** in the dashboard. Add a DNS record, type the domain, and choose one of two
routes — both are offered every time, and neither is a fallback for the other:

- **Let GeoDeploy configure it.** It looks at what is actually in front of it, adds one new file to
  your web server, tests it, and reloads. Described below.
- **I'll configure it myself.** The same configuration as text, for you to place. If you would rather
  own every change to your own web server, this is the right choice and always available —
  [the settings are explained here](#if-you-would-rather-do-it-by-hand).

Either way, finish with **Verify**: it checks DNS, reaches the domain from the server, and confirms
the request lands on *this* instance with the hostname intact.

### Letting GeoDeploy configure it

It works with **nginx, Caddy and Apache**, whether they run on the host or in a container. What it
does is deliberately small: it adds **one new file** — `geodeploy.conf` in your web server's drop-in
directory, carrying a `managed by GeoDeploy` marker — and reloads. It never edits `nginx.conf`, your
Caddyfile, or any other site's configuration, and it never restarts anything.

Before writing anything it checks six things, and **refuses rather than guesses**:

| It stops if | Because |
| --- | --- |
| Your configuration does not currently pass its own test | Reloading would be what finally applies whatever is already wrong in there, and your sites would go down with GeoDeploy's name on it. It tells you what `nginx -t` said and writes nothing. |
| The domain is already served on this machine | Two blocks claiming one hostname is not an error — one silently wins. Adding ours could change a site that works today. |
| `geodeploy.conf` exists without our marker | The name matches but the file is somebody else's. |
| The drop-in directory is not actually included by the main configuration | The file would be written where nothing reads it: success that does nothing, which you would then debug as a DNS problem. |
| Traefik is the proxy | Its routing comes from container labels, a file provider or Kubernetes resources — there is no single file to drop that is correct across those. You get [the exact manual steps](#traefik-or-any-proxy-in-docker) instead. |
| There is no reverse proxy at all | A machine with no proxy is not one GeoDeploy should be installing one on. |

After writing, it tests again. **A failed test removes the file and never reaches the reload** — a
configuration that is never loaded has harmed nobody — and it tells you whether the machine's own
test passes again, so you know whether the problem was ours. Only a passing test gets a reload, and
it is always a reload, never a restart: existing connections keep being served, and a proxy that
rejects the new configuration keeps running the old one.

When it succeeds it asks the proxy for your domain over the machine's own loopback and checks that
GeoDeploy answers. That is worth more than it sounds: it proves the proxy is correct **whether or not
DNS has propagated yet**, which is the one thing you otherwise cannot tell apart from a broken
configuration.

**Remove it** undoes exactly what was added — the file, the symlink if there was one — then tests
*before* reloading, for the same reason: taking our file away can expose an unrelated problem in your
configuration, and a tidy-up must not be what takes your sites down.

!!! note "What this needs, and why it is not a new risk"

    The dashboard reaches your web server through a one-shot privileged container, using the Docker
    socket GeoDeploy already has. That socket is root on the host by any measure — anything that can
    reach it can already do this — so the capability is not new. What is new is that it is now used
    for a small, audited, reversible set of operations instead of being available for anything. Every
    apply and removal is recorded in the audit log with the file it wrote.

    If you would rather GeoDeploy never did this, use the manual route. It is the same text.

### HTTPS

!!! warning "A port-80 block is invisible to anyone arriving over HTTPS"

    The block GeoDeploy writes listens on **port 80**. If your machine already terminates HTTPS for
    another site — and especially if that site's block is `listen 443 ssl; server_name _;`, a
    catch-all that answers for every hostname — then `https://your-domain` is answered by **that
    site**, not GeoDeploy, no matter how correct our block is. `http://` works immediately;
    `https://` does not, until the domain has its own certificate.

    **Behind Cloudflare this bites straight away**, because the SSL/TLS mode decides which origin
    port Cloudflare connects to:

    | Cloudflare SSL/TLS mode | Connects to your origin on | Result before you have a certificate |
    | --- | --- | --- |
    | **Flexible** | port 80 | works |
    | Full / Full (strict) | port 443 | you get the other site, or a 525/526 |

    So: **Flexible** until the certificate exists, then **Full (strict)**. The panel warns about this
    before you apply, and checks port 443 as well as port 80 afterwards, so it says plainly when the
    configuration is right and HTTPS still lands elsewhere.

Getting the certificate depends on which proxy you run:

- **Caddy** obtains and renews it itself. There is nothing to do, and nothing below applies.
- **nginx** — press **Get a certificate** in the panel. GeoDeploy runs certbot **in a throwaway
  container**, so nothing is installed on your machine, gets a Let's Encrypt certificate over the
  HTTP-01 challenge, rewrites its own block to serve HTTPS on 443, tests, and reloads. It then
  renews itself daily.
- **Apache** is not automated yet; the panel gives you `sudo certbot --apache -d your-domain`.

#### What automatic HTTPS does to your machine

More than one config file, so it is worth knowing before you press it:

| | |
| --- | --- |
| Installed on the host | **nothing** — certbot runs in a container that is removed afterwards |
| Created on the host | `/etc/letsencrypt` (certificates, account key) and `/var/lib/letsencrypt` |
| Changed | GeoDeploy's own `geodeploy.conf`, nothing else |
| Renewal | a daily task in GeoDeploy's worker; certbot no-ops until renewal is due |

The HTTP-01 challenge is a file fetched over **port 80** from the public internet, so port 80 must
reach this server for that name.

**Behind Cloudflare it usually works with the proxy on**: Let's Encrypt connects to Cloudflare on
port 80 and Cloudflare forwards the challenge to you, and a redirect to HTTPS is followed and
accepted. Three things genuinely break it — Bot Fight Mode, a WAF rule or "Under Attack" mode
answering with a JavaScript page; a cache or page rule on `/.well-known/`; and SSL/TLS already set to
**Full**, where Cloudflare tries HTTPS against an origin that has no certificate yet. Try it with the
orange cloud on; if it fails, grey-cloud for two minutes and retry, which rules out all three at
once. Let's Encrypt allows 5 failed validations per hostname per hour, so failures are cheap but not
free.

!!! note "Two deliberate details, both of which exist because of how this breaks"

    **Port 80 keeps serving the ACME path, never redirected.** Renewal uses the same challenge, and
    redirecting it to a port whose certificate has just expired is exactly how an auto-renewing
    certificate quietly stops renewing.

    **Behind Cloudflare, port 80 serves the application rather than redirecting to HTTPS.** With
    SSL/TLS on Flexible, Cloudflare connects to port 80; a redirect there sends the browser back to
    Cloudflare, which connects to port 80 again — an infinite loop on a site that worked a minute
    earlier. GeoDeploy detects the Cloudflare case and omits the redirect. Turn on **Always Use
    HTTPS** in Cloudflare instead, and switch SSL/TLS to **Full (strict)** now that your server has
    its own certificate.

Renewal reloads your proxy **only when the certificate file actually changed** — not on a schedule,
and not by parsing certbot's output, which is prose and changes between versions. If the
configuration fails its test at renewal time, nothing is reloaded and the existing certificate stays
live, which buys you the remaining weeks of its validity to fix the real problem.

### If you would rather do it by hand

The four settings below are not optional. Each one is a real GeoDeploy failure if it is missing, and
the first two are the ones people leave out:

```nginx
server {
    listen 80;
    server_name maps.example.org;

    location / {
        proxy_pass http://127.0.0.1:8080;

        # Without this every link GeoDeploy generates — shared links, portal previews, the STAC and
        # OGC catalogues — points at 127.0.0.1 instead of your domain.
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        # Session cookies take their Secure flag from this, and it decides http:// vs https://
        # in every URL GeoDeploy emits.
        proxy_set_header X-Forwarded-Proto $scheme;

        # nginx defaults to 1 MB and THIS setting shadows GeoDeploy's own: without it every
        # upload over a megabyte fails with 413.
        client_max_body_size 11G;
        # Large uploads stream straight through to object storage; buffering here would spool the
        # whole file onto this server's disk first.
        proxy_request_buffering off;

        proxy_read_timeout 600s;
        proxy_send_timeout 600s;

        proxy_http_version 1.1;
        proxy_set_header Upgrade    $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

Then `sudo nginx -t && sudo systemctl reload nginx`, and add a certificate with
`sudo certbot --nginx -d maps.example.org`.

!!! tip "Caddy needs three lines"
    ```
    maps.example.org {
        reverse_proxy 127.0.0.1:8080
    }
    ```
    `reverse_proxy` sets `Host` and the `X-Forwarded-*` headers itself, streams request bodies
    without buffering, has no size limit, and obtains and renews the TLS certificate on its own. If
    you are choosing a reverse proxy for a machine you control, this is the shortest correct answer.

### Traefik, or any proxy in Docker

A container cannot reach a `127.0.0.1` host port — that isolation is the whole point of the mode. So
put the proxy on GeoDeploy's network and route to the container instead of the host port:

```bash
docker network connect geodeploy <your-traefik-container>
```

Settings → Deployment's **Traefik** tab generates the labels.

## Changing the port later

Never by hand. Editing `.env` changes nothing until nginx is recreated, so the file says one thing
and the running container does another:

```bash
cd ~/geodeploy
sudo bash installer/set-port.sh 8081        # move it
sudo bash installer/set-port.sh --dedicated # take port 80
sudo bash installer/set-port.sh --show      # where is it now?
```

Each one checks the target port is free first, recreates only nginx — leaving the API, the worker and
any running ingest untouched — and **puts the old settings back if the new ones do not come up**.

## The port never moves on its own

Chosen once, at install, and then left alone. Updates do not re-pick it, and re-running the installer
does not either. This matters more than it sounds: your reverse proxy's `proxy_pass`, a DNS record
and everyone's bookmarks all point at that number, and a port that moved by itself would be
indistinguishable from a broken install.

If the configured port is taken when GeoDeploy starts, nginx fails and says so. It does not go
looking for another one.

## When something is wrong

**"Nothing is listening on port 8080" although nginx is running.** If the port was occupied at the
moment the container started, the bind failed — and neither `restart` nor `up -d` re-establishes it.
Docker will report the container as running with the correct port mapping while nothing answers.
Only a recreate repairs it:

```bash
docker compose up -d --force-recreate nginx
```

`installer/preflight.sh` detects this state, and so does Settings → Deployment.

**Shared links and portal previews point at `127.0.0.1`.** Your proxy is not passing the hostname.
Add `proxy_set_header Host $host;`. Settings → Deployment says so explicitly when it sees it.

**Uploads over 1 MB fail with 413.** Your proxy's `client_max_body_size`, not GeoDeploy's.

**GeoDeploy is on 0.0.0.0 and you expected the firewall to cover it.** It does not. A Docker publish
on `0.0.0.0` inserts its forwarding rule ahead of ufw, so `ufw deny 8080` will not keep that port off
the internet. Bind the loopback instead: `sudo bash installer/set-port.sh 8080`.

## What is not here yet

- **HTTPS in dedicated mode.** GeoDeploy does not yet obtain its own certificate; in that mode it
  serves plain HTTP. Until it does, a reverse proxy in front is how you get HTTPS — which is another
  reason behind-proxy is worth choosing even on a machine you own.
- **A base path.** GeoDeploy has to live at the root of its domain or subdomain; it cannot yet be
  served under `https://example.org/geodeploy/`.
- **Two GeoDeploys on one machine.** Five containers still have fixed names and every instance joins
  the same Docker network, where the first one's database answers to the alias `postgres` — so a
  second instance could connect to the first one's data. Preflight refuses rather than letting it
  half-install, and names the directory it found. Running GeoDeploy alongside *other software* is
  what this page is about and is fully supported; it is two copies of GeoDeploy that cannot coexist.
