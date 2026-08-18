#!/usr/bin/env bash
set -euo pipefail

GEODEPLOY_DIR="${GEODEPLOY_DIR:-$HOME/geodeploy}"
GREEN='\033[0;32m'; NC='\033[0m'

cd "$GEODEPLOY_DIR"

echo -e "${GREEN}[geodeploy]${NC} Pulling latest changes…"
git pull

# Record the now-deployed commit in .env so the admin "Updates" panel reflects the new version
# (the API reads GEODEPLOY_GIT_SHA via env_file after the recreate below).
GD_SHA=$(git rev-parse HEAD 2>/dev/null || echo unknown)
if grep -q '^GEODEPLOY_GIT_SHA=' .env 2>/dev/null; then
  sed -i "s|^GEODEPLOY_GIT_SHA=.*|GEODEPLOY_GIT_SHA=${GD_SHA}|" .env
else
  echo "GEODEPLOY_GIT_SHA=${GD_SHA}" >> .env
fi

echo -e "${GREEN}[geodeploy]${NC} Rebuilding images…"
docker compose build

echo -e "${GREEN}[geodeploy]${NC} Restarting services…"
# Recreate ONLY the Compose-owned code services. postgres/minio/titiler (and sometimes martin) are
# provisioned by the setup wizard via the Docker socket — OUTSIDE Compose — with fixed container
# names, so a blanket `up -d` collides ("container name /geodeploy-postgres already in use"). Those
# services aren't affected by a code update anyway. NO --remove-orphans (would delete wizard containers).
docker compose up -d geodeploy-api geodeploy-ui celery nginx redis

# nginx.conf is a SINGLE-FILE bind mount and `git pull` rewrites it with a NEW inode, but a running
# container stays bound to the OLD one — so the `up -d` above reports "up-to-date" and the change
# silently never lands (this is the bug class that made CORS/route fixes look undeployed). Compare
# what the container is actually serving to the host file and recreate only when they differ; an
# unchanged config costs one `sha1sum` and nothing else. Mirrors `apply_nginx` in self-update.sh,
# which is what the in-app "Update now" runs — keep the two in step.
# `|| true` on both: this script runs under `set -euo pipefail`, so a failing substitution (nginx
# not running) would abort the update at the very end instead of skipping a comparison it cannot
# make. self-update.sh gets this for free — `local x=$(…)` masks the status inside a function; at
# top level it does not.
host_sum=$(sha1sum nginx/nginx.conf 2>/dev/null | cut -d' ' -f1 || true)
cont_sum=$(docker compose exec -T nginx sha1sum /etc/nginx/nginx.conf 2>/dev/null | tr -d '\r' | cut -d' ' -f1 || true)
if [ -n "$host_sum" ] && [ "$host_sum" != "$cont_sum" ]; then
  echo -e "${GREEN}[geodeploy]${NC} nginx.conf changed — recreating nginx to re-mount it…"
  docker compose up -d --force-recreate nginx
fi

echo -e "${GREEN}[geodeploy]${NC} Done. GeoDeploy updated."
