# installer/

## Purpose
Bash scripts that take a bare Linux VPS to a running GeoDeploy (the `curl install.sh | bash` experience) and manage updates/resets.

## Contents
- `install.sh` — installs Docker if missing, clones/updates the repo to `$GEODEPLOY_DIR` (default `~/geodeploy`), generates `.env` from `.env.example` with a random secret key, **records the deployed commit as `GEODEPLOY_GIT_SHA` in `.env`** (read via env_file → the admin **Updates** panel compares it to GitHub `main`) **and the deployed REF in `data/temp/deployed-ref`**, creates the external `geodeploy` Docker network, pulls + starts the **core** services (`geodeploy-api geodeploy-ui nginx redis celery`), waits for `/health`, prints the access URL. Optional services (postgres/martin/minio/titiler) are started later by the setup wizard via the Docker socket.
  Takes `--port N`, `--bind ADDR`, `--dedicated`, `--behind-proxy` and `--help` (reachable through a
  pipe as `bash -s -- --port 8081`), each mirrored by an environment variable.
  **Since 2026-09-12 it ASKS where GeoDeploy should be published** and never assumes: preflight →
  prompt → write `GEODEPLOY_DEPLOY_MODE`/`_HTTP_BIND`/`_HTTP_PORT` → start. The prompt is THREE
  options, because "dedicated or not" and "which port" are different questions and bundling them hid
  the second: (1) a dedicated GeoDeploy server, spelled out rather than named — it takes port 80,
  the address has no port in it, anything else wanting 80 later will fail, and nothing running now is
  stopped; (2) the default port, the first free candidate; (3) choose another, from the live free
  list or typed. **Every path checks the port**, including `--port`/the environment (which used to be
  taken on trust — the install then completed with nginx dead), and there is a final re-check
  immediately before `docker compose up`, because the clone, the `.env` write and the image pull all
  happen between choosing and binding. Ports 80/443 are not taken
  because they are free; that is a choice the operator makes, on every route. **Prompts read
  `/dev/tty`, not stdin** — under `curl … | bash` the SCRIPT is stdin, so a bare `read` eats the
  script's own remaining lines and the question is never asked. No TTY and no `GEODEPLOY_DEPLOY_MODE`
  in the environment ⇒ behind-proxy on a free candidate: the only option that claims nothing. An
  EXISTING install never has its port re-picked (an update or re-run that moved it would orphan the
  operator's `proxy_pass`, DNS and bookmarks); one predating the keys is recorded as `0.0.0.0:80`,
  which is what it already was.
  `GEODEPLOY_VERSION` picks the version (branch, **tag** or commit; default `main`). On an EXISTING checkout it now resolves that ref itself — branch, then tag, then commit — and `git reset --hard`s to it; the old `git pull origin "$VERSION"` could not reach a tag at all (a tag is not a branch, so pulling one onto a detached HEAD merges or refuses), which made re-running the installer the wrong way to pin a release.
- `update.sh` — `git pull` → **rewrites `GEODEPLOY_GIT_SHA` in `.env`** → `docker compose build` → `docker compose up -d --remove-orphans`. (A dev `docker compose up` that skips these scripts leaves `GEODEPLOY_GIT_SHA=unknown`; the Updates panel then shows "Running unknown" but still reports the latest available commit.)
  Since 2026-08-18 it also **applies an `nginx.conf` change**: the file is a single-file bind mount and `git pull` gives it a new inode, so a running container stays on the old one and `up -d` reports "up-to-date" while the change silently never lands. It compares the container's config to the host file and force-recreates nginx only when they differ — the same check `self-update.sh::apply_nginx` has always done. **Keep the two in step.**
- `self-update.sh` — **rollback-capable** update (Coolify-style): records the current commit → fetches + resolves the target ref → `git reset --hard` → `docker compose build` → `up -d` → **health-checks `/health`**, and if the new version doesn't come up healthy it **reverts to the previous commit and rebuilds**. Writes machine-readable progress to `data/update-status.json` (the admin Updates panel polls it; also the script the opt-in one-click updater runs in a detached container). Prefer this over `update.sh` for a safe update: `cd ~/geodeploy && sudo bash installer/self-update.sh`. Health URL/tries override via `GEODEPLOY_HEALTH_URL`/`GEODEPLOY_HEALTH_TRIES` (the one-click passes the in-network API URL).
  **The target is the FIRST ARGUMENT** — a tag, a branch or a commit; default `origin/main`, so every existing caller is unchanged: `sudo bash installer/self-update.sh v1.0`. It is validated in the script as well as in the API (the API is the only caller today and will not always be), and RESOLVED before `git reset --hard`, so a typo'd tag fails while the checkout is still intact.
  **2026-08-06 additions** — three things a `git fetch origin main` could not do, each invisible on a normal update and fatal for exactly the targets this feature exists to reach: an `install.sh` clone is **shallow** (so `--unshallow` first: a tag otherwise resolves to a commit with no history, and rollback has nothing to reset into) **and single-branch** (`git remote set-branches origin '*'`, or `origin/<any-other-branch>` never exists and every branch target fails as "No such version"); and `--prune-tags` + `git remote prune`, so a tag moved or a branch deleted upstream stops resolving to a stale local copy. The deployed ref is recorded in `data/temp/deployed-ref` (rollback restores the previous one) and is what `GET /admin/updates` reports as the update **channel** — without it a pinned instance keeps being measured against `main` and called "behind".
- `lib-deploy.sh` — **sourced helpers, no side effects**. Owns the TWO-VARIABLE design that keeps the
  port from drifting: `GEODEPLOY_PORT_CANDIDATES` is a SUGGESTION list, consulted only while a port
  is being chosen; `GEODEPLOY_HTTP_PORT` is the CHOSEN port, authoritative for ever after. Candidates
  resolve environment → `.env` → the built-in ten (reading only the environment made the
  `.env.example` line decorative), are sanitised and deduplicated, and `gd_free_candidates` filters
  them through a live `gd_port_in_use` so nothing is ever offered that is not free at that moment.
  Also: read/write `.env`, test whether a TCP port
  is in use (IPv4 **and** IPv6 — a v6-only listener on `:::80` blocks a v4 bind and a v4-only check
  calls the port free), scan the candidate list, resolve the publish spec, and `gd_ingress_reachable`.
  `gd_env_set` writes **in place** (`> "$file"`, never `sed -i`): GNU `sed -i` renames a temp file
  over the original, giving it a NEW INODE, and `.env` is a single-file bind mount into api+celery —
  a running container would go on reading the old inode. Same trap `services/envfile.py` documents.
  The `:-` defaults here MUST stay in step with `docker-compose.yml` and `self-update.sh`.
- `preflight.sh` — **writes nothing, starts nothing**, safe on a production box; `--json` for
  install.sh and later the dashboard. Reports EVERY conflict at once: ports 80/443 and who holds
  them, an installed web server, a free candidate port, the configured port, whether the ingress is
  actually **answering**, a FOREIGN `geodeploy` Docker network (we would silently join it and share
  the DNS names `postgres`/`redis`/`minio` with a stranger's stack), container-name collisions, and
  swap, **and another GeoDeploy installed anywhere else on the machine** — a blocker, because the two
  would share the `geodeploy` network where the first one's database answers to the alias `postgres`,
  so the second's API could connect to the first's data. Detected precisely from Compose's
  `com.docker.compose.project.working_dir` label on the geodeploy/api and geodeploy/ui containers, so
  an installation never flags itself. Ownership of a container is judged by the Compose project label **or the image**, because
  postgres/martin/titiler/minio are wizard-provisioned outside Compose and carry no label — checking
  only the label flagged a healthy install as a collision.
- `set-port.sh` — **the only supported way to change the host port**, and reversible.
  `set-port.sh 8081` · `--dedicated` · `--port N --bind IP` · `--show`. A target that is taken is
  refused with the holder named AND a live list of free alternatives plus a copy-paste command —
  being refused with nowhere to go is the same dead end as not checking. Preflights the target,
  rewrites the three keys in place, recreates **only nginx** (so a running ingest is untouched),
  health-checks the new address, and RESTORES the old values and recreates again if it does not come
  up — the rollback is itself health-checked before it claims success.
- `reset.sh` — destructive: removes all `geodeploy*` containers, the api/ui images, the network, and the install dir (confirmation prompt).

## Tests
`installer/tests/` — the plan and the suites for everything above; **read its README first**, it is
the test plan. Four suites: `test-unit.sh` (parsing and selection, no Docker, ~1s), `test-proxy.sh`
(the generated config validated by real `nginx -t` / `caddy validate`, then a live proxy),
`test-install.sh` (the question and every path that picks a port), `test-ports.sh` (fallback, re-run
drift, moving it, the wedged bind). `test-port80.sh` is opt-in — it takes port 80 and stops any
GeoDeploy already running. `run-all.sh` runs them in order.

Not in CI: a shared runner cannot be asked for port 80, and these want a real Docker daemon. The
Python half (`services/deployment.py`, the endpoints) is covered by `api/tests/test_deployment*.py`
and does run in CI.

## The compose overrides
Two optional files at the repo root, both opted into the same way — `COMPOSE_FILE` in `.env`, which
the Compose CLI reads exactly as it already reads `COMPOSE_PROFILES`, so install/update/self-update
and the dashboard's apply helper all honour it with no further changes. Chain them with `:`.

- `docker-compose.tls.yml` — publishes host **443**. The base file no longer does: nginx.conf's TLS
  server block is commented out, so it reserved the machine's most contested port and served
  nothing. Needed by anyone who provisioned certificates by hand, and by the future certbot flow.
- `docker-compose.db-port.yml` — publishes the provisioned **PostGIS** on `127.0.0.1:5432` so QGIS,
  psql or DBeaver can reach it over an SSH tunnel. Off by default because a database on the network
  is protected by its password and nothing else; `GEODEPLOY_POSTGIS_BIND=0.0.0.0` is a separate,
  documented decision (docs/data-access.md), and ufw does not cover it.

Compose merges `ports:` ADDITIVELY — an override can add a mapping and never remove one — which is
why the base file carries the one publish everybody needs and these two add the optional extras,
rather than the other way round.

## Dependencies / relationships
- `install.sh` clones from the public GitHub repo and relies on `docker-compose.yml` + `.env.example` at the repo root.
- The setup wizard (`api/.../routers/setup.py`) brings up the profiled services after install — the installer deliberately does **not** start them.
- The `geodeploy` network is created `external` and persists across `compose down/up`.

## Current status & known issues
- **`update.sh` runs `docker compose ... ` without `--profile` flags.** Per `notes_temp/notes_for_future.md` (note #1), this can drop the optional profile services (postgres/martin/titiler/minio) out of Compose management and break their DNS aliases. A real fix needs the active profiles persisted (e.g. `COMPOSE_PROFILES` in `.env`) and passed on every `up`.
- `install.sh` uses `sudo docker` in places and assumes a Debian/Ubuntu-ish host (`get.docker.com`, `apt` hint).
- UI-driven updates (Coolify-style "deploy" button) are planned — see notes_temp note #2.
- `update.sh` (the legacy plain updater) still does a bare `git pull`, takes no target and does
  **not** write `deployed-ref`. It is superseded by `self-update.sh`; the docs point at the latter
  everywhere.

## Last updated
2026-09-12b (**round two, after review**: `GEODEPLOY_PORT_CANDIDATES` is a real, active `.env` key
read environment → `.env` → built-in ten, and the installer's question became THREE options —
dedicated (spelled out), the default port, or choose another from the ports free at that moment.
`--port/--bind/--dedicated/--behind-proxy` arguments. **Every path that selects a port now checks
it**: `--port`/the environment used to be validated as a number and then trusted, so an install onto
a busy port completed with nginx dead. A refusal always names the holder and lists live
alternatives. One shared rule for the mode — port 80 is dedicated, anything else behind-proxy — so
install.sh and set-port.sh cannot disagree. Plus `docker-compose.db-port.yml`, above.)
2026-09-12 (**the port mission — issue #79's first slice.** The host port is a `.env` variable
(`docker-compose.yml` interpolates it), `443:443` is no longer published by the base file — the
container has no 443 listener, so it reserved the most contested port on the machine and served
nothing; `docker-compose.tls.yml` adds it back for the TLS work. New `lib-deploy.sh`, `preflight.sh`
and `set-port.sh`. Both updaters now build the health URL from `.env` — `http://localhost/health`
was hard-coded, and on any install not on port 80 `self-update.sh` would have failed its post-update
check and ROLLED BACK a good update. `self-update.sh` gained `ensure_nginx_ports`, mirroring
`apply_nginx`: nginx is deliberately outside `CORE_SERVICES`, so a port change would otherwise sit
unapplied. Measured and now handled: **a failed port bind is not repaired by `restart` or `up -d`** —
Docker returns the container to `running` with the right PortBindings and NOTHING LISTENING; only
`--force-recreate` fixes it, and preflight, the updater and the dashboard all detect it.)
2026-08-18 (`update.sh` gained the `apply_nginx` checksum check above — the manual path was the only updater that silently dropped an `nginx.conf` change. Both substitutions are `|| true` because this script, unlike the function in self-update.sh, runs them at top level under `set -euo pipefail`.)
2026-08-06b (**`verify_deployed`: "healthy" is not "updated"**. `docker compose build` returning 0
does not mean an image was produced (a cached build is a success and keeps its old timestamp), and
`/health` is answered perfectly by OLD code — so an update that rebuilt nothing reported success
while `record_sha` advertised the new version. Observed: an api image 47 hours old after an update to
a 2-hour-old commit, with the panel reading "Up to date". Now, after the health check: the image must
have been rebuilt IF its build context changed (`git diff -- api/`, so a docs-only update stays
silent), and each service must be RUNNING that image; on failure the sha/ref markers are restored and
the status is an error naming the exact recovery command.)
2026-08-06 (branch + release targets reach a default clone: un-shallow, all-branch refspec, tag/branch pruning; the deployed ref is recorded — issue #4)
