#!/usr/bin/env bash
# Rollback-capable GeoDeploy update (Coolify-style): pull → build → recreate → HEALTH-CHECK, and if
# the new version doesn't come up healthy, ROLL BACK to the previous commit and rebuild. Safe to run
# manually today:  cd ~/geodeploy && sudo bash installer/self-update.sh
#
# It is also the script the (opt-in) one-click updater runs in a detached container. It writes machine
# -readable progress to data/update-status.json so the admin UI can poll it. NOT `set -e`: we handle
# each failure so we can roll back instead of dying half-applied.
set -uo pipefail

cd "$(dirname "$0")/.." || exit 1   # repo root (this script lives in installer/)
STATUS_FILE="data/temp/update-status.json"   # under data/temp (mounted into the API → the UI can poll it)
HEALTH_URL="${GEODEPLOY_HEALTH_URL:-http://localhost/health}"
HEALTH_TRIES="${GEODEPLOY_HEALTH_TRIES:-40}"   # × 3s ≈ 2 min for the stack to come back healthy
# Recreate ONLY the code services. NGINX IS DELIBERATELY EXCLUDED: it's the single ingress, so
# recreating it takes the whole site down for a few seconds (Cloudflare 521). Instead we leave it
# running and RELOAD its config gracefully (nginx -s reload — zero-downtime, and if the new config is
# invalid it keeps the old one). postgres/minio/titiler are wizard-provisioned outside Compose (fixed
# names) and a blanket `up` would collide on them — and a code update doesn't touch any of these.
CORE_SERVICES="geodeploy-api geodeploy-ui celery"

mkdir -p data/temp
_now() { date -u +%FT%TZ; }
write_status() { # phase message
  printf '{"phase":"%s","message":"%s","at":"%s"}\n' "$1" "$(printf '%s' "$2" | sed 's/"/\\"/g')" "$(_now)" > "$STATUS_FILE"
  echo "[self-update] $1: $2"
}
_http_ok() { curl -fsS "$1" >/dev/null 2>&1 || wget -q -O- "$1" >/dev/null 2>&1; }
# Graceful nginx config reload (picks up nginx.conf changes with NO downtime; a bad config is rejected
# and the running config is kept). Never recreates the container.
reload_nginx() { docker compose exec -T nginx nginx -t >/dev/null 2>&1 && docker compose exec -T nginx nginx -s reload >/dev/null 2>&1 || true; }

# Bind-mount safety. The API WRITES portal bundles into data/portals; nginx READS + serves them. If the
# mounted directory's inode ever shifts (a reinstall that recreated the folder, a stray delete), a
# still-running nginx keeps the OLD mount and serves STALE/404 portals while the recreated API writes to
# the fresh one — a `reload` can't re-attach a mount, only a recreate can. This is the class of bug that
# made portals ghost/blank after updates. So: drop a sentinel through the API's mount and check nginx
# sees it; recreate nginx ONLY when they've actually diverged (normal updates skip it → still zero-downtime).
ensure_nginx_mount_synced() {
  local token="mchk-$(date +%s)-$$"
  docker compose exec -T geodeploy-api sh -c "echo $token > /data/portals/.mountcheck" >/dev/null 2>&1 || return 0
  if ! docker compose exec -T nginx sh -c "grep -q $token /var/www/portals/.mountcheck" >/dev/null 2>&1; then
    echo "[self-update] nginx's portals mount is stale (diverged) — recreating nginx to re-attach"
    docker compose up -d --force-recreate nginx >/dev/null 2>&1 || true
  fi
  docker compose exec -T geodeploy-api sh -c "rm -f /data/portals/.mountcheck" >/dev/null 2>&1 || true
}

healthy() {
  local i
  for i in $(seq 1 "$HEALTH_TRIES"); do
    if _http_ok "$HEALTH_URL"; then return 0; fi
    sleep 3
  done
  return 1
}

rollback() { # old_sha reason
  write_status rollingback "$2 — rolling back to ${1:0:7}"
  git reset --hard "$1" >/dev/null 2>&1
  docker compose build && docker compose up -d --force-recreate $CORE_SERVICES && reload_nginx
  # restore the deployed-commit marker so the Updates panel reflects reality
  sed -i "s|^GEODEPLOY_GIT_SHA=.*|GEODEPLOY_GIT_SHA=${1}|" .env 2>/dev/null || true
  if healthy; then
    write_status rolledback "Rolled back to ${1:0:7} ($2). No changes applied."
  else
    write_status error "Rollback attempted but the stack is still unhealthy — check 'docker compose ps' and logs."
  fi
}

OLD_SHA="$(git rev-parse HEAD 2>/dev/null || echo unknown)"
[ "$OLD_SHA" = unknown ] && { write_status error "Not a git checkout — can't self-update."; exit 1; }

write_status running "Fetching the latest version"
if ! git fetch origin main >/dev/null 2>&1; then write_status error "git fetch failed (no network?)."; exit 1; fi
if ! git reset --hard origin/main >/dev/null 2>&1; then write_status error "git update failed."; exit 1; fi
NEW_SHA="$(git rev-parse HEAD)"
if [ "$NEW_SHA" = "$OLD_SHA" ]; then
  # Keep the deployed-commit marker honest even on a no-op — a manual `git pull` moves HEAD but leaves
  # GEODEPLOY_GIT_SHA stale, so the panel wrongly shows "behind". Sync + reload the API to pick it up.
  if ! grep -q "^GEODEPLOY_GIT_SHA=${NEW_SHA}$" .env 2>/dev/null; then
    sed -i "s|^GEODEPLOY_GIT_SHA=.*|GEODEPLOY_GIT_SHA=${NEW_SHA}|" .env 2>/dev/null || true
    docker compose up -d --force-recreate geodeploy-api >/dev/null 2>&1 || true
  fi
  write_status success "Already up to date (${NEW_SHA:0:7})."; exit 0
fi

write_status running "Building the new version"
if ! docker compose build; then rollback "$OLD_SHA" "Build failed"; exit 1; fi

write_status running "Restarting services"
if ! docker compose up -d --force-recreate $CORE_SERVICES; then rollback "$OLD_SHA" "Restart failed"; exit 1; fi
reload_nginx              # apply any nginx.conf change without recreating (and without downtime)
ensure_nginx_mount_synced # …but recreate nginx if its data/portals mount diverged (else portals ghost/404)

write_status running "Checking health"
if ! healthy; then rollback "$OLD_SHA" "Unhealthy after update"; exit 1; fi

# Success: record the now-deployed commit so the Updates panel shows the new version.
sed -i "s|^GEODEPLOY_GIT_SHA=.*|GEODEPLOY_GIT_SHA=${NEW_SHA}|" .env 2>/dev/null || \
  echo "GEODEPLOY_GIT_SHA=${NEW_SHA}" >> .env
write_status success "Updated ${OLD_SHA:0:7} → ${NEW_SHA:0:7} and healthy."
