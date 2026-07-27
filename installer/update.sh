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
# Compose reads COMPOSE_PROFILES from .env, so the local postgres/minio (if this install
# provisioned them) are part of the active set and stay managed. NO --remove-orphans: the
# wizard provisions some containers via the Docker socket, and on an install whose .env
# predates COMPOSE_PROFILES that flag would delete them (notes_for_future §1).
docker compose up -d

echo -e "${GREEN}[geodeploy]${NC} Done. GeoDeploy updated."
