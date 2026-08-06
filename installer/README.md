# installer/

## Purpose
Bash scripts that take a bare Linux VPS to a running GeoDeploy (the `curl install.sh | bash` experience) and manage updates/resets.

## Contents
- `install.sh` — installs Docker if missing, clones/updates the repo to `$GEODEPLOY_DIR` (default `~/geodeploy`), generates `.env` from `.env.example` with a random secret key, **records the deployed commit as `GEODEPLOY_GIT_SHA` in `.env`** (read via env_file → the admin **Updates** panel compares it to GitHub `main`) **and the deployed REF in `data/temp/deployed-ref`**, creates the external `geodeploy` Docker network, pulls + starts the **core** services (`geodeploy-api geodeploy-ui nginx redis celery`), waits for `/health`, prints the access URL. Optional services (postgres/martin/minio/titiler) are started later by the setup wizard via the Docker socket.
  `GEODEPLOY_VERSION` picks the version (branch, **tag** or commit; default `main`). On an EXISTING checkout it now resolves that ref itself — branch, then tag, then commit — and `git reset --hard`s to it; the old `git pull origin "$VERSION"` could not reach a tag at all (a tag is not a branch, so pulling one onto a detached HEAD merges or refuses), which made re-running the installer the wrong way to pin a release.
- `update.sh` — `git pull` → **rewrites `GEODEPLOY_GIT_SHA` in `.env`** → `docker compose build` → `docker compose up -d --remove-orphans`. (A dev `docker compose up` that skips these scripts leaves `GEODEPLOY_GIT_SHA=unknown`; the Updates panel then shows "Running unknown" but still reports the latest available commit.)
- `self-update.sh` — **rollback-capable** update (Coolify-style): records the current commit → fetches + resolves the target ref → `git reset --hard` → `docker compose build` → `up -d` → **health-checks `/health`**, and if the new version doesn't come up healthy it **reverts to the previous commit and rebuilds**. Writes machine-readable progress to `data/update-status.json` (the admin Updates panel polls it; also the script the opt-in one-click updater runs in a detached container). Prefer this over `update.sh` for a safe update: `cd ~/geodeploy && sudo bash installer/self-update.sh`. Health URL/tries override via `GEODEPLOY_HEALTH_URL`/`GEODEPLOY_HEALTH_TRIES` (the one-click passes the in-network API URL).
  **The target is the FIRST ARGUMENT** — a tag, a branch or a commit; default `origin/main`, so every existing caller is unchanged: `sudo bash installer/self-update.sh v1.0`. It is validated in the script as well as in the API (the API is the only caller today and will not always be), and RESOLVED before `git reset --hard`, so a typo'd tag fails while the checkout is still intact.
  **2026-08-06 additions** — three things a `git fetch origin main` could not do, each invisible on a normal update and fatal for exactly the targets this feature exists to reach: an `install.sh` clone is **shallow** (so `--unshallow` first: a tag otherwise resolves to a commit with no history, and rollback has nothing to reset into) **and single-branch** (`git remote set-branches origin '*'`, or `origin/<any-other-branch>` never exists and every branch target fails as "No such version"); and `--prune-tags` + `git remote prune`, so a tag moved or a branch deleted upstream stops resolving to a stale local copy. The deployed ref is recorded in `data/temp/deployed-ref` (rollback restores the previous one) and is what `GET /admin/updates` reports as the update **channel** — without it a pinned instance keeps being measured against `main` and called "behind".
- `reset.sh` — destructive: removes all `geodeploy*` containers, the api/ui images, the network, and the install dir (confirmation prompt).

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
2026-08-06b (**`verify_deployed`: "healthy" is not "updated"**. `docker compose build` returning 0
does not mean an image was produced (a cached build is a success and keeps its old timestamp), and
`/health` is answered perfectly by OLD code — so an update that rebuilt nothing reported success
while `record_sha` advertised the new version. Observed: an api image 47 hours old after an update to
a 2-hour-old commit, with the panel reading "Up to date". Now, after the health check: the image must
have been rebuilt IF its build context changed (`git diff -- api/`, so a docs-only update stays
silent), and each service must be RUNNING that image; on failure the sha/ref markers are restored and
the status is an error naming the exact recovery command.)
2026-08-06 (branch + release targets reach a default clone: un-shallow, all-branch refspec, tag/branch pruning; the deployed ref is recorded — issue #4)
