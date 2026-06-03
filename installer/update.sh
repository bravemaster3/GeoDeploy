#!/usr/bin/env bash
set -euo pipefail

GEODEPLOY_DIR="${GEODEPLOY_DIR:-$HOME/geodeploy}"
GREEN='\033[0;32m'; NC='\033[0m'

cd "$GEODEPLOY_DIR"

echo -e "${GREEN}[geodeploy]${NC} Pulling latest changes…"
git pull

echo -e "${GREEN}[geodeploy]${NC} Rebuilding images…"
docker compose build

echo -e "${GREEN}[geodeploy]${NC} Restarting services…"
# Compose reads COMPOSE_PROFILES from .env, so the local postgres/minio (if this install
# provisioned them) are part of the active set and stay managed. NO --remove-orphans: the
# wizard provisions some containers via the Docker socket, and on an install whose .env
# predates COMPOSE_PROFILES that flag would delete them (notes_for_future §1).
docker compose up -d

echo -e "${GREEN}[geodeploy]${NC} Done. GeoDeploy updated."
