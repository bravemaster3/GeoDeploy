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
  Port 443                  ✗  in use — nginx (systemd: nginx.service)
  A free port for GeoDeploy    8080

  This machine is already serving something on port 80.
  GeoDeploy will not touch it, whichever option you choose.

How should GeoDeploy be published?

  1) Take over port 80 — GeoDeploy becomes this machine's web server.
  2) Behind the web server you already run — 127.0.0.1:8080.

Choose [2]:
```

The recommendation is pre-selected, but pressing Enter is still you choosing. If you pick **1)**
while something else holds port 80, the installer refuses and asks again — it will not stop the
other service for you.

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

**Settings → Deployment** in the dashboard. Type the domain, pick your web server, and it writes the
configuration out for you to paste. Then press **Verify**, which checks DNS, reaches the domain from
the server, and confirms the request lands on this instance with the hostname intact.

GeoDeploy never writes to your web server's configuration itself. On a machine with other people's
sites on it, a bad reload takes all of them down — so the dashboard hands you the text and you
decide.

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
- **Two GeoDeploys on one machine.** Container and network names are fixed, so a second instance
  collides with the first. Preflight reports it rather than letting it half-install.
