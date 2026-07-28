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
# Recreate ONLY the Compose-owned code services — postgres/minio/titiler are wizard-provisioned via
# the Docker socket (fixed names outside Compose), so a blanket `up` collides on the name. A code
# update doesn't touch them anyway.
CORE_SERVICES="geodeploy-api geodeploy-ui celery nginx redis"

mkdir -p data/temp
_now() { date -u +%FT%TZ; }
write_status() { # phase message
  printf '{"phase":"%s","message":"%s","at":"%s"}\n' "$1" "$(printf '%s' "$2" | sed 's/"/\\"/g')" "$(_now)" > "$STATUS_FILE"
  echo "[self-update] $1: $2"
}
_http_ok() { curl -fsS "$1" >/dev/null 2>&1 || wget -q -O- "$1" >/dev/null 2>&1; }

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
  docker compose build && docker compose up -d $CORE_SERVICES
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
if [ "$NEW_SHA" = "$OLD_SHA" ]; then write_status success "Already up to date (${OLD_SHA:0:7})."; exit 0; fi

write_status running "Building the new version"
if ! docker compose build; then rollback "$OLD_SHA" "Build failed"; exit 1; fi

write_status running "Restarting services"
if ! docker compose up -d $CORE_SERVICES; then rollback "$OLD_SHA" "Restart failed"; exit 1; fi

write_status running "Checking health"
if ! healthy; then rollback "$OLD_SHA" "Unhealthy after update"; exit 1; fi

# Success: record the now-deployed commit so the Updates panel shows the new version.
sed -i "s|^GEODEPLOY_GIT_SHA=.*|GEODEPLOY_GIT_SHA=${NEW_SHA}|" .env 2>/dev/null || \
  echo "GEODEPLOY_GIT_SHA=${NEW_SHA}" >> .env
write_status success "Updated ${OLD_SHA:0:7} → ${NEW_SHA:0:7} and healthy."
