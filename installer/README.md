# installer/

## Purpose
Bash scripts that take a bare Linux VPS to a running GeoDeploy (the `curl install.sh | bash` experience) and manage updates/resets.

## Contents
- `install.sh` — installs Docker if missing, clones/updates the repo to `$GEODEPLOY_DIR` (default `~/geodeploy`), generates `.env` from `.env.example` with a random secret key, **records the deployed commit as `GEODEPLOY_GIT_SHA` in `.env`** (read via env_file → the admin **Updates** panel compares it to GitHub `main`), creates the external `geodeploy` Docker network, pulls + starts the **core** services (`geodeploy-api geodeploy-ui nginx redis celery`), waits for `/health`, prints the access URL. Optional services (postgres/martin/minio/titiler) are started later by the setup wizard via the Docker socket.
- `update.sh` — `git pull` → **rewrites `GEODEPLOY_GIT_SHA` in `.env`** → `docker compose build` → `docker compose up -d --remove-orphans`. (A dev `docker compose up` that skips these scripts leaves `GEODEPLOY_GIT_SHA=unknown`; the Updates panel then shows "Running unknown" but still reports the latest available commit.)
- `self-update.sh` — **rollback-capable** update (Coolify-style): records the current commit → `git fetch/reset origin main` → `docker compose build` → `up -d` → **health-checks `/health`**, and if the new version doesn't come up healthy it **reverts to the previous commit and rebuilds**. Writes machine-readable progress to `data/update-status.json` (the admin Updates panel polls it; also the script the opt-in one-click updater runs in a detached container). Prefer this over `update.sh` for a safe update: `cd ~/geodeploy && sudo bash installer/self-update.sh`. Health URL/tries override via `GEODEPLOY_HEALTH_URL`/`GEODEPLOY_HEALTH_TRIES` (the one-click passes the in-network API URL).
- `reset.sh` — destructive: removes all `geodeploy*` containers, the api/ui images, the network, and the install dir (confirmation prompt).

## Dependencies / relationships
- `install.sh` clones from the public GitHub repo and relies on `docker-compose.yml` + `.env.example` at the repo root.
- The setup wizard (`api/.../routers/setup.py`) brings up the profiled services after install — the installer deliberately does **not** start them.
- The `geodeploy` network is created `external` and persists across `compose down/up`.

## Current status & known issues
- **`update.sh` runs `docker compose ... ` without `--profile` flags.** Per `notes_temp/notes_for_future.md` (note #1), this can drop the optional profile services (postgres/martin/titiler/minio) out of Compose management and break their DNS aliases. A real fix needs the active profiles persisted (e.g. `COMPOSE_PROFILES` in `.env`) and passed on every `up`.
- `install.sh` uses `sudo docker` in places and assumes a Debian/Ubuntu-ish host (`get.docker.com`, `apt` hint).
- UI-driven updates (Coolify-style "deploy" button) are planned — see notes_temp note #2.

## Last updated
2026-06-01
