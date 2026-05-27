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
docker compose up -d --remove-orphans

echo -e "${GREEN}[geodeploy]${NC} Done. GeoDeploy updated."
