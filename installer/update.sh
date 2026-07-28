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

echo -e "${GREEN}[geodeploy]${NC} Done. GeoDeploy updated."
